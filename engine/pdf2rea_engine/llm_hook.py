"""A single interception point for every LLM prompt PDF2REA wants to shape.

``babeldoc`` builds its prompt inside ``ILTranslatorLLMOnly.translate_paragraph``
(~200 lines) and immediately hands it to
``translate_engine.llm_translate(...)``.  Rather than copy that method -- which
would silently fork all of its validation, fallback and placeholder logic -- we
wrap the *engine object* held by the translator instance.  One wrapper, per
request, sees the final prompt string and may rewrite it.

Registered hooks run in priority order and each receives::

    ctx = PromptContext(prompt, items, config, batch_kind)

where ``items`` is the parsed JSON array of ``{"id", "input", "layout_label"}``
objects that the model is being asked to translate.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from . import registry

INPUT_MARKER = "## Here is the input:"

# Ordered by execution: consistency rules first, cross-page instruction last so
# it is the final thing the model reads.
PRIORITY_TERMS = 10
PRIORITY_CROSS_PAGE = 20


@dataclass
class PromptContext:
    prompt: str
    items: list[dict] = field(default_factory=list)
    config: object = None
    batch_kind: str = "normal"  # normal | cross_page | cross_column
    notes: list[str] = field(default_factory=list)
    response_transform: Callable[[str], str] | None = None
    fallback_prompt: str | None = None

    def replace_items(self, items: list[dict]) -> str:
        """Re-serialize the input array back into the prompt body."""
        payload = json.dumps(items, ensure_ascii=False, indent=2)
        head, sep, tail = self.prompt.partition(INPUT_MARKER)
        if not sep:
            self.notes.append("input marker missing; items not replaced")
            return self.prompt
        start = tail.find("[")
        try:
            _, length = json.JSONDecoder().raw_decode(tail[start:])
        except (ValueError, TypeError):
            self.notes.append("invalid input JSON; items not replaced")
            return self.prompt
        return f"{head}{INPUT_MARKER}{tail[:start]}{payload}{tail[start+length:]}"



_hooks: list[tuple[int, Callable[[PromptContext], str]]] = []
_hooks_lock = threading.Lock()


def register_hook(priority: int, hook: Callable[[PromptContext], str]) -> None:
    with _hooks_lock:
        _hooks.append((priority, hook))
        _hooks.sort(key=lambda pair: pair[0])


def reset_hooks() -> None:
    with _hooks_lock:
        _hooks.clear()


def insert_instructions(prompt: str, instructions: str) -> str:
    """Keep instructions out of the source text, including single-item fallback."""
    head, marker, tail = prompt.partition(INPUT_MARKER)
    if not marker:
        return prompt
    return head.rstrip() + "\n\n" + instructions.rstrip() + "\n\n" + marker + tail


def parse_items(prompt: str) -> list[dict]:
    """Extract the JSON input array from a rendered prompt (best effort)."""
    _, _, tail = prompt.partition(INPUT_MARKER)
    if not tail:
        return []
    start = tail.find("[")
    if start == -1:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(tail[start:])
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [item for item in data if isinstance(item, dict)]


def apply_hooks(ctx: PromptContext) -> str:
    prompt = ctx.prompt
    with _hooks_lock:
        hooks = list(_hooks)
    for _, hook in hooks:
        try:
            candidate = hook(ctx)
        except Exception as error:  # a bad hook must never kill a request
            ctx.notes.append(f"hook error: {error!r}")
            continue
        if isinstance(candidate, str) and candidate.strip():
            prompt = candidate
            ctx.prompt = prompt
            ctx.items = parse_items(prompt)
    return prompt


class _EngineProxy:
    """Transparent proxy that only rewrites ``llm_translate`` input."""

    __slots__ = ("_pdf2rea_engine", "_pdf2rea_config")

    def __init__(self, engine, config):
        object.__setattr__(self, "_pdf2rea_engine", engine)
        object.__setattr__(self, "_pdf2rea_config", config)

    # -- attribute delegation ------------------------------------------------
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_pdf2rea_engine"), name)

    def __setattr__(self, name, value):
        if name in _EngineProxy.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_pdf2rea_engine"), name, value)

    def __repr__(self) -> str:
        return f"<PDF2REA engine proxy {self._pdf2rea_engine!r}>"

    # -- the one intercepted call -------------------------------------------
    def llm_translate(self, body, *args, **kwargs):
        ctx = None
        if isinstance(body, str):
            try:
                items = parse_items(body)
                if not items:
                    return self._pdf2rea_engine.llm_translate(body, *args, **kwargs)
                ctx = PromptContext(
                    prompt=body,
                    items=items,
                    config=self._pdf2rea_config,
                    batch_kind=classify_batch(body),
                )
                body = apply_hooks(ctx)
            except Exception as error:
                registry._errors.append(f"llm_translate hook: {error!r}")
        result = self._pdf2rea_engine.llm_translate(body, *args, **kwargs)
        if ctx is not None and ctx.response_transform is not None:
            try:
                return ctx.response_transform(result)
            except Exception:
                # A rejected joint result must never leak partial text or wrong
                # formula ownership. Retry the original two-item context format.
                if ctx.fallback_prompt is None:
                    raise
                return self._pdf2rea_engine.llm_translate(ctx.fallback_prompt, *args, **kwargs)
        return result


def classify_batch(body: str) -> str:
    """Cheap marker detection for the batch kind (see cross_page.py)."""
    if "PDF2REA-CROSS-PAGE" in body:
        return "cross_page"
    if "PDF2REA-CROSS-COLUMN" in body:
        return "cross_column"
    return "normal"


_TAG_RE = re.compile(r"<style id='(\d+)'>")


def install() -> None:
    """Wrap the translator engine so prompt hooks run on every LLM request."""

    def _patch(module):
        cls = module.ILTranslatorLLMOnly

        def _factory(original_init):
            def __init__(self, translate_engine, translation_config, tokenizer=None):
                proxy = _EngineProxy(translate_engine, translation_config)
                original_init(self, proxy, translation_config, tokenizer)
                # Keep a handle for diagnostics and for the pair registry.
                self._pdf2rea_proxy = proxy

            return __init__

        if registry.wrap_method(cls, "__init__", _factory, "llm_hook"):
            registry.note_applied("ILTranslatorLLMOnly.llm_translate prompt hooks")

    registry.on_import(
        "babeldoc.format.pdf.document_il.midend.il_translator_llm_only", _patch
    )
