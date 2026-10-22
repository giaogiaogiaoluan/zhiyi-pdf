"""Persistent term memory: ``~/.pdf2rea/ledger.json``.

BabelDoc's automatic term extractor already resolves (source -> target) votes by
majority (``translation_config.py:99-121``) but discards them when the process exits,
which is why the same paper translated twice can come out with two different term
choices.  PDF2REA writes those resolved pairs to disk and replays them as a glossary
on the next run, so terminology is stable across documents *and* across days.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections import Counter

_PATH = os.environ.get("PDF2REA_LEDGER_PATH") or os.path.join(os.path.expanduser("~"), ".pdf2rea", "ledger.json")
_LOCK = threading.Lock()
_ENABLED = True
_MAX_PER_LANGUAGE = 4000
_SCHEMA = 1

_memory: dict[str, dict[str, dict]] = {}
_loaded = False


def set_enabled(enabled: bool) -> None:
    global _ENABLED
    _ENABLED = bool(enabled)


def path() -> str:
    return _PATH


def _load() -> dict:
    global _loaded
    if _loaded:
        return _memory
    _loaded = True
    try:
        with open(_PATH, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return _memory
    if not isinstance(raw, dict) or raw.get("schema") != _SCHEMA:
        return _memory
    for language, pairs in (raw.get("languages") or {}).items():
        if not isinstance(pairs, dict):
            continue
        bucket = _memory.setdefault(language, {})
        for source, value in pairs.items():
            if isinstance(value, dict) and value.get("target"):
                bucket[source] = {"target": str(value["target"]),
                                  "count": int(value.get("count", 1)),
                                  "updated": value.get("updated", "")}
    return _memory


def load_pairs(language: str) -> list[tuple[str, str]]:
    """Most-used target per source term, for glossary replay."""
    with _LOCK:
        bucket = _load().get(language) or {}
        return [(source, value["target"]) for source, value in
                sorted(bucket.items(), key=lambda item: item[0])]


def record(pairs, language: str) -> int:
    """Merge (source, target) pairs; a term keeps its most frequent rendering."""
    if not _ENABLED or not pairs:
        return 0
    with _LOCK:
        memory = _load()
        bucket = memory.setdefault(language, {})
        incoming: Counter = Counter()
        for source, target in pairs:
            source = (source or "").strip()
            target = (target or "").strip()
            if not source or not target or source == target:
                continue
            incoming[source] += 1
            entry = bucket.get(source)
            if entry is None:
                bucket[source] = {"target": target, "count": 1}
            elif entry["target"] == target:
                entry["count"] = entry.get("count", 1) + 1
            elif entry.get("count", 1) < 2:
                # A weakly-supported earlier choice yields to the newer evidence.
                bucket[source] = {"target": target, "count": 1}
        if len(bucket) > _MAX_PER_LANGUAGE:
            keep = sorted(bucket.items(), key=lambda item: -item[1].get("count", 1))
            memory[language] = dict(keep[:_MAX_PER_LANGUAGE])
        _write(memory)
        return len(incoming)


def _write(memory: dict) -> None:
    directory = os.path.dirname(_PATH)
    try:
        os.makedirs(directory, exist_ok=True)
        payload = {"schema": _SCHEMA, "languages": memory}
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=directory, prefix=".ledger-", suffix=".json",
            delete=False,
        )
        try:
            json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        finally:
            handle.close()
        os.replace(handle.name, _PATH)
    except OSError:
        pass  # read-only home, disk full: keep translating, lose the memory


def stats() -> dict:
    with _LOCK:
        memory = _load()
        return {
            "path": _PATH,
            "enabled": _ENABLED,
            "languages": {language: len(bucket) for language, bucket in memory.items()},
        }


def clear(language: str | None = None) -> int:
    with _LOCK:
        memory = _load()
        removed = 0
        if language:
            bucket = memory.pop(language, {})
            removed = len(bucket)
        else:
            removed = sum(len(bucket) for bucket in memory.values())
            memory.clear()
        _write(memory)
        return removed
