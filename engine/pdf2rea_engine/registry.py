"""Patch plumbing: post-import hooks and idempotent method wrappers.

``pdf2rea.pth`` is executed by CPython during interpreter start-up, i.e. long
before ``babeldoc`` is imported.  Eagerly importing ``babeldoc`` there would
pull numpy / onnxruntime / pymupdf into *every* ``python`` invocation of that
venv, so instead we register post-import hooks and only touch a module once the
real entry point (``pdf2zh_next``) imports it itself.

Every patch is:
  * idempotent -- applying twice is a no-op (guarded by a marker attribute),
  * fail-soft  -- an exception disables that one feature; translation continues,
  * reversible -- the original callable is retained and restorable.
"""

from __future__ import annotations

import importlib
import sys
import threading
from typing import Callable

_lock = threading.RLock()
_applied: set[str] = set()
_errors: list[str] = []
_restorers: list[Callable[[], None]] = []


def _safe_call(label: str, callback: Callable, *args):
    try:
        return callback(*args)
    except Exception as error:
        with _lock:
            _errors.append(f"{label}: {error!r}")
        return None


# --------------------------------------------------------------------------- #
# post-import hook
# --------------------------------------------------------------------------- #
class _ExecutingLoader:
    """Delegate loader that fires callbacks once the module body has run."""

    def __init__(self, loader, callbacks):
        self._loader = loader
        self._callbacks = callbacks

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        for callback in list(self._callbacks):
            _safe_call(f"post-import callback for {module.__name__}", callback, module)

    def __getattr__(self, name):
        return getattr(self._loader, name)

    def __repr__(self) -> str:
        return f"<PDF2REA executing loader for {self._loader!r}>"


class _PostImportFinder:
    def __init__(self):
        self.hooks: dict[str, list[Callable]] = {}

    def find_spec(self, fullname, path=None, target=None):
        callbacks = self.hooks.get(fullname)
        if not callbacks:
            return None
        spec = None
        for finder in sys.meta_path:
            if finder is self or not hasattr(finder, "find_spec"):
                continue
            try:
                found = finder.find_spec(fullname, path, target)
            except Exception:
                found = None
            if found is not None:
                spec = found
                break
        if spec is None or isinstance(spec.loader, _ExecutingLoader):
            return spec
        spec.loader = _ExecutingLoader(spec.loader, callbacks)
        return spec

    def __repr__(self) -> str:
        return f"<PDF2REA post-import finder {sorted(self.hooks)}>"


_finder: _PostImportFinder | None = None


def on_import(module_name: str, callback: Callable) -> None:
    """Run ``callback(module)`` as soon as ``module_name`` finishes importing."""
    global _finder
    with _lock:
        module = sys.modules.get(module_name)
        if module is not None:
            _safe_call(f"already-imported {module_name}", callback, module)
            return
        if _finder is None:
            _finder = _PostImportFinder()
            sys.meta_path.insert(0, _finder)
        bucket = _finder.hooks.setdefault(module_name, [])
        if callback not in bucket:
            bucket.append(callback)


# --------------------------------------------------------------------------- #
# method wrapping
# --------------------------------------------------------------------------- #
def wrap_method(target_class, method_name: str, factory: Callable[[Callable], Callable],
                token: str) -> bool:
    """Replace ``target_class.method_name`` with ``factory(original)``.

    ``token`` marks the class so a re-apply (module reload, retry) cannot
    double-wrap.  Returns True when the wrap actually happened.
    """
    with _lock:
        marker = f"_pdf2rea_wrapped_{token}"
        if getattr(target_class, marker, False):
            return False
        try:
            original = getattr(target_class, method_name)
        except AttributeError:
            _errors.append(f"{target_class.__name__} has no {method_name}")
            return False
        setattr(target_class, method_name, factory(original))
        setattr(target_class, marker, True)
        _restorers.append(_make_restorer(target_class, method_name, original, marker))
        _applied.add(f"{target_class.__module__}.{target_class.__name__}.{method_name}")
        return True


def _make_restorer(target_class, method_name, original, marker):
    def _do():
        setattr(target_class, method_name, original)
        try:
            delattr(target_class, marker)
        except AttributeError:
            pass

    return _do


def require(module_name: str):
    """Import a module, tolerating an absent optional dependency."""
    try:
        return importlib.import_module(module_name)
    except Exception as error:
        with _lock:
            _errors.append(f"import {module_name}: {error!r}")
        return None


def patch_module_function(module, name: str, factory: Callable[[Callable], Callable],
                          token: str, label: str | None = None) -> bool:
    """Replace a module-level function, registering a real undo step.

    ``wrap_method`` only understands classes.  Helper functions such as
    ``layout_helper.is_text_layout`` are patched with plain ``setattr``, and if the
    original is not saved here, ``rollback()`` silently leaves the patch in place --
    i.e. uninstalling the plugin would keep mutating babeldoc behaviour.
    """
    with _lock:
        marker = f"_pdf2rea_wrapped_{token}"
        original = getattr(module, name, None)
        if original is None:
            _errors.append(f"{module.__name__} has no {name}")
            return False
        if getattr(original, marker, False):
            return False
        replacement = factory(original)
        setattr(replacement, marker, True)
        replacement.__name__ = getattr(original, "__name__", name)
        setattr(module, name, replacement)
        _restorers.append(_make_module_restorer(module, name, original))
        _applied.add(label or f"{module.__name__}.{name}")
        return True


def _make_module_restorer(module, name, original):
    def _do():
        setattr(module, name, original)

    return _do


def rebind_dependents(name: str, value, modules: tuple[str, ...], note=True) -> list[str]:
    """Re-point ``from module import name`` copies at the patched callable.

    BabelDOC imports helpers by value (``paragraph_finder.py:34``), so patching only
    the defining module leaves the actual call sites using the original function.
    """
    rebound = []
    for module_name in modules:
        module = require(module_name)
        if module is None or not hasattr(module, name):
            continue
        previous = getattr(module, name)
        if previous is value:
            continue
        setattr(module, name, value)
        rebound.append(module_name)
        with _lock:
            # capture the value as it was *before* the rebind so undo is exact
            _restorers.append(_make_dependent_restorer(module, name, previous))
    if note and rebound:
        with _lock:
            _applied.add(f"rebound {name} in {', '.join(m.rsplit('.', 1)[-1] for m in rebound)}")
    return rebound


def _make_dependent_restorer(module, name, original):
    def _do():
        setattr(module, name, original)

    return _do


def note_applied(label: str) -> None:
    with _lock:
        _applied.add(label)


def summary() -> dict:
    with _lock:
        return {
            "applied": sorted(_applied),
            "errors": list(_errors),
            "restorable": len(_restorers),
        }


def rollback() -> int:
    with _lock:
        count = len(_restorers)
        for restorer in reversed(_restorers):
            _safe_call("rollback", restorer)
        _restorers.clear()
        _applied.clear()
    return count
