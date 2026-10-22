"""Runtime status: per-process event log + aggregated marker.

Why not a single JSON file: the engine runs the per-page IL stages in a *process
pool* (see ``close_process_pool()`` in ``babeldoc/format/pdf/high_level.py:897``), and
every worker applies the patches again in its own interpreter -- confirmed by four
``[PDF2REA]`` banners in one job.  A plain status file written at parent exit would
report the parent's own zero counters and claim the fixes did nothing.

So each process appends one JSON line to ``pdf2rea.events.jsonl`` tagged with
``run_id`` (the plugin writes a fresh run id into ``pdf2rea.json`` per job, and every
worker reads it from there), and the aggregate for that run is rewritten into
``pdf2rea.status.json`` -- the single file the Zotero plugin reads.

The plugin cannot see the engine subprocess at all; this is what turns
"patch installed" into "patch loaded, N corner marks rescued, M table cells created".
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import time

STATE_FILE = "pdf2rea.status.json"
EVENTS_FILE = "pdf2rea.events.jsonl"
_EVENTS_LIMIT_BYTES = 512_000

_STATE: dict = {
    "loaded_at": None,
    "options_path": None,
    "options": {},
    "patches": [],
    "errors": [],
    "versions": {},
    "finished_at": None,
    "stats": {},
    "argv": [],
    "run_id": None,
    "pid": os.getpid(),
}


def begin(options: dict, options_path) -> None:
    _STATE["loaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _STATE["options_path"] = options_path
    _STATE["options"] = options
    # The plugin writes a fresh run_id per job.  If it is absent, the options file's
    # mtime is still identical in every pool worker, so it groups them correctly --
    # unlike a per-process timestamp, which would split one run into N "runs".
    _STATE["run_id"] = options.get("run_id") or _fallback_run_id(options_path)
    _STATE["pid"] = os.getpid()
    _STATE["argv"] = [os.path.basename(sys_argv()[0] if sys_argv() else "")] + sys_argv()[1:6]
    _STATE["versions"] = _detect_versions()
    _STATE["versions"]["pdf2rea_plugin"] = options.get("plugin_version")


def _fallback_run_id(options_path) -> str:
    if options_path:
        try:
            return f"mtime-{int(os.path.getmtime(options_path))}"
        except OSError:
            pass
    return time.strftime("%Y%m%d-%H%M%S")


def sys_argv() -> list:
    import sys

    return list(sys.argv)


def _detect_versions() -> dict:
    from importlib import metadata

    versions = {"python": platform.python_version(), "platform": platform.system().lower()}
    for package in ("babeldoc", "pdf2zh_next"):
        try:
            versions[package] = metadata.version(package)
        except Exception:
            versions[package] = None
    try:
        from . import __version__ as engine_version
    except ImportError:
        engine_version = "unknown"
    versions["pdf2rea_engine"] = engine_version
    return versions


def snapshot() -> dict:
    return dict(_STATE)


def record(patches: list[str], errors: list[str]) -> None:
    """Update the in-memory view (called right after apply() arms the hooks)."""
    _STATE["patches"] = list(patches)
    _STATE["errors"] = list(errors)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
def _line(directory: str) -> str:
    path = os.path.join(directory, EVENTS_FILE)
    payload = {
        "run_id": _STATE["run_id"],
        "pid": os.getpid(),
        "loaded_at": _STATE["loaded_at"],
        "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "patches": _STATE["patches"],
        "errors": _STATE["errors"],
        "stats": _STATE["stats"],
        "versions": _STATE["versions"],
        "argv": _STATE["argv"],
    }
    try:
        if os.path.exists(path) and os.path.getsize(path) > _EVENTS_LIMIT_BYTES:
            _prune(path)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return path


def _prune(path: str) -> None:
    """Keep the tail of the log; a 500 KB cap is generous for a few hundred runs."""
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        with open(path, "w", encoding="utf-8") as handle:
            handle.writelines(lines[-200:])
    except OSError:
        pass


def aggregate(directory: str, run_id: str | None = None) -> dict:
    """Merge every process line belonging to one run."""
    path = os.path.join(directory, EVENTS_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [json.loads(raw) for raw in handle if raw.strip()]
    except (OSError, ValueError):
        return {}
    if run_id is None:
        run_id = lines[-1].get("run_id") if lines else None
    selected = [entry for entry in lines if entry.get("run_id") == run_id]
    if not selected:
        return {}

    merged = {
        "run_id": run_id,
        "processes": sorted({entry.get("pid") for entry in selected}),
        "loaded_at": selected[0].get("loaded_at"),
        "finished_at": max((entry.get("finished_at") or "") for entry in selected),
        "versions": selected[-1].get("versions", {}),
        "patches": sorted({p for entry in selected for p in entry.get("patches", [])}),
        "errors": [e for entry in selected for e in entry.get("errors", [])],
        "argv": selected[0].get("argv", []),
        "stats": {"formula_guard": {}, "table_text": {}, "terminology": {}, "processes": len(selected)},
    }
    for feature in ("formula_guard", "table_text", "cross_page", "layout_repair", "structured_text", "response_guard"):
        totals: dict = {}
        for entry in selected:
            for key, value in (entry.get("stats", {}).get(feature) or {}).items():
                if isinstance(value, int):
                    totals[key] = totals.get(key, 0) + value
        merged["stats"][feature] = totals

    terms: dict = {"glossaries": set(), "terms": 0, "hard_terms": 0,
                   "stats": {"enforced": 0, "normalized": 0, "violations": 0}}
    for entry in selected:
        terminology = entry.get("stats", {}).get("terminology") or {}
        terms["glossaries"].update(terminology.get("glossaries") or [])
        terms["terms"] = max(terms["terms"], terminology.get("terms", 0) or 0)
        terms["hard_terms"] = max(terms["hard_terms"], terminology.get("hard_terms", 0) or 0)
        for key, value in (terminology.get("stats") or {}).items():
            if isinstance(value, int):
                terms["stats"][key] = terms["stats"].get(key, 0) + value
    terms["glossaries"] = sorted(terms["glossaries"])
    merged["stats"]["terminology"] = terms
    return merged


def write(directory: str | None) -> str | None:
    """Append this process's line and refresh the aggregate marker."""
    if not directory:
        return None
    try:
        _line(directory)
        merged = aggregate(directory, _STATE["run_id"])
        path = os.path.join(directory, STATE_FILE)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(merged or snapshot(), handle, ensure_ascii=False, indent=1,
                      sort_keys=True)
        return path
    except OSError:
        return None


_finalized_pid = None


def register_exit_hook() -> None:
    atexit.register(_finalize)
    # multiprocessing exits through os._exit(), bypassing Python atexit.
    # Its own finalizer registry runs before that exit, including spawned workers.
    from multiprocessing.util import Finalize
    Finalize(None, _finalize, exitpriority=10)


def _finalize() -> None:
    global _finalized_pid
    if _finalized_pid == os.getpid():
        return
    _finalized_pid = os.getpid()
    from . import cross_page, formula_guard, layout_repair, registry, table_text, terminology, structured_text, response_guard
    from . import options as options_module

    _STATE["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _STATE["stats"] = {
        "cross_page": cross_page.stats(),
        "layout_repair": layout_repair.stats(),
        "structured_text": structured_text.stats(),
        "response_guard": response_guard.stats(),
        "formula_guard": formula_guard.stats(),
        "table_text": table_text.stats(),
        "terminology": terminology.state(),
    }
    summary = registry.summary()
    # Refresh from the registry: in a pool worker this is where the real counts live.
    _STATE["patches"] = summary["applied"]
    _STATE["errors"] = summary["errors"]
    _, options_path = options_module.load()
    write(options_module.state_dir(options_path))
