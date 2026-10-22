"""PDF2REA engine patch layer.

Loaded automatically by ``pdf2rea.pth`` inside the ``zotero-pdf2zh-next-venv``
interpreter, i.e. inside the same process that runs ``pdf2zh_next``/``babeldoc``.

Nothing here edits ``server.py`` or ``babeldoc``: every change is an in-memory
monkey-patch registered as a post-import hook.  Deleting the ``.pth`` file restores
stock behaviour completely, and a package upgrade cannot leave a half-applied patch
behind because the patched files were never touched on disk.

Entry point: :func:`apply`.
"""

from __future__ import annotations

__version__ = "1.0.5"
__all__ = ["apply", "options", "registry", "status", "is_installed"]

_INSTALLED = False


def is_installed() -> bool:
    return _INSTALLED


def apply() -> dict:
    """Register every enabled PDF2REA patch. Never raises.

    Returns a small report so the CLI/``--version`` path can print what happened.
    """
    global _INSTALLED
    if _INSTALLED:
        return {"enabled": False, "reason": "already applied"}

    from . import (
        cross_page,
        layout_repair,
        structured_text,
        response_guard,
        formula_guard,
        llm_hook,
        options,
        registry,
        status,
        table_text,
        terminology,
    )

    loaded, path = options.load()
    status.begin(loaded, path)
    status.register_exit_hook()

    report = {"enabled": bool(loaded.get("enabled")), "options_path": path,
              "features": {}}
    if not loaded.get("enabled", True):
        status.record(["disabled by options"], [])
        return report

    # Terminology + numeric rules reshape every prompt, so it goes first.
    if options.feature_enabled(loaded, "terminology"):
        term_options = dict(options.section(loaded, "terminology"))
        term_options.setdefault("reuse_ledger", True)
        terminology.configure(term_options)
        terminology.install(term_options)
        report["features"]["terminology"] = term_options.get("numeric_policy", "auto")

    # Prompt hooks must exist before the cross-page annotator relies on them.
    llm_hook.install()

    if options.feature_enabled(loaded, "cross_page"):
        cross_page.install(options.section(loaded, "cross_page"))
        report["features"]["cross_page"] = True

    if options.feature_enabled(loaded, "formula_guard"):
        formula_guard.install(options.section(loaded, "formula_guard"))
        report["features"]["formula_guard"] = True

    if options.feature_enabled(loaded, "table_text"):
        table_options = dict(options.section(loaded, "table_text"))
        table_options.setdefault("cell_segmentation", True)
        table_text.install(table_options)
        report["features"]["table_text"] = table_options.get("cell_segmentation", True)

    if options.feature_enabled(loaded, "layout_repair"):
        layout_repair.install(options.section(loaded, "layout_repair"))
        report["features"]["layout_repair"] = True

    if options.feature_enabled(loaded, "structured_text"):
        structured_text.install(options.section(loaded, "structured_text"))
        report["features"]["structured_text"] = True
    response_guard.install({})

    _INSTALLED = True
    summary = registry.summary()
    status.record(summary["applied"], summary["errors"])
    report["patches"] = summary["applied"]
    report["errors"] = summary["errors"]

    if loaded.get("log_level", "info") != "quiet":
        # stderr only: server/execute.py parses stdout for "translate n/m" progress.
        # At this point the hooks are *armed* -- patches attach when pdf2zh_next
        # imports babeldoc itself, so the real count lands in pdf2rea.status.json.
        print(f"[PDF2REA {__version__}] {len(summary['applied'])} import hooks armed"
              + (f", {len(summary['errors'])} warnings" if summary["errors"] else ""),
              file=__import__("sys").stderr)
    return report


def rollback() -> int:
    """Undo every registered patch (used by tests and the uninstall path)."""
    from . import llm_hook, registry

    global _INSTALLED
    llm_hook.reset_hooks()
    _INSTALLED = False
    return registry.rollback()
