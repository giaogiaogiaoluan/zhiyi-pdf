"""PDF2REA engine option resolution.

The plugin never modifies ``server.py`` or ``babeldoc``.  It only drops a JSON
file next to the ``config.toml`` that ``zotero-pdf2zh`` already rewrites on
every request; this module finds and validates that file.

Lookup order (first hit wins):

1. ``$PDF2REA_CONFIG`` (a file path).
2. ``pdf2rea.json`` in the directory of ``--config-file`` / ``--config`` found
   in ``sys.argv`` -- this is exactly the ``server/config`` directory that
   ``zotero-pdf2zh/server/server.py`` writes ``config.toml`` into.
3. ``~/zotero-pdf2zh/server/config/pdf2rea.json``
4. ``~/.pdf2rea/config.json``
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    # Written by the plugin before each job so every worker process of one
    # translation can be grouped in the event log (see status.py).
    "run_id": None,
    "plugin_version": None,
    # --- Fix 1: cross-page paragraph continuity -----------------------------
    "cross_page": {
        "enabled": True,
        "mode": "joint",
        # Require the page-N fragment to end mid-sentence.  Set to False to
        # pair every page boundary (aggressive, can glue unrelated blocks).
        "require_open_ending": True,
        # A fragment shorter than this is treated as an orphan tail/head.
        "min_fragment_chars": 12,
        # Also apply the directive to babeldoc's cross-column pairs.
        "also_cross_column": True,
    },
    # --- Fix 2: terminology + numeric consistency ---------------------------
    "terminology": {
        "enabled": True,
        "glossary_files": [],
        # Never let the auto-extracted glossary hide the user glossaries
        # (upstream bug: translation_config.get_glossaries_for_translation).
        "keep_user_glossaries": True,
        "numeric_policy": "arabic",  # arabic | keep | chinese | auto
        "enforce_units": True,
        "custom_rules": [],
        # ship + enforce engine/pdf2rea_engine/glossary/academic_base.csv
        "use_builtin_glossary": True,
        # replay ~/.pdf2rea/ledger.json so terminology is stable across runs
        "reuse_ledger": True,
        # append the consistency rules to every LLM prompt
        "prompt_rules": True,
    },
    # --- Fix 3: table text --------------------------------------------------
    "table_text": {
        "enabled": True,
        # Add table labels to the text-layout whitelist so cell characters are
        # grouped into paragraphs and translated through the normal path.
        "treat_table_layout_as_text": True,
        "table_labels": ["table", "wired_table", "blocked_table"],
        # Split each table block into one paragraph per detected cell.
        "cell_segmentation": True,
        # Only accept whole-table layouts whose height/width stay sane.
        "max_table_box_area_ratio": 0.55,
    },
    # --- Fix 4: superscript / symbol induced blank gaps ---------------------
    "formula_guard": {
        "enabled": True,
        "rescue_corner_marks": True,
        "max_rescue_chars": 8,
        "operators": ["=", "+", "-", "*", "/", "^", "_", "\\sum", "\\int", "\\alpha"],
        "never_rescue_labels": ["formula", "display_formula"],
    },
    "layout_repair": {"enabled": True},
    "structured_text": {"enabled": True},
    # --- diagnostics --------------------------------------------------------
    "report": True,
    "log_level": "info",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _config_file_from_argv(argv: list[str]) -> str | None:
    for index, token in enumerate(argv):
        for flag in ("--config-file", "--config"):
            if token == flag and index + 1 < len(argv):
                return argv[index + 1]
            if token.startswith(f"{flag}="):
                return token.split("=", 1)[1]
    return None


def candidate_paths() -> list[str]:
    paths: list[str] = []
    env = os.environ.get("PDF2REA_CONFIG")
    if env:
        paths.append(os.path.expanduser(env))

    toml_path = _config_file_from_argv(sys.argv[1:])
    if toml_path:
        paths.append(os.path.join(os.path.dirname(os.path.abspath(toml_path)), "pdf2rea.json"))

    home = os.path.expanduser("~")
    paths.append(os.path.join(home, ".pdf2rea", "server", "config", "pdf2rea.json"))
    paths.append(os.path.join(home, ".pdf2rea", "config.json"))
    return paths


def state_dir(options_path: str | None) -> str:
    """Where the runtime status marker is written (plugin reads this back)."""
    if options_path:
        directory = os.path.dirname(os.path.abspath(options_path))
    else:
        directory = os.path.join(os.path.expanduser("~"), ".pdf2rea", "server", "config")
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".pdf2rea-write-test")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
    except OSError:
        directory = os.path.join(tempfile.gettempdir(), "pdf2rea")
        os.makedirs(directory, exist_ok=True)
    return directory


def load() -> tuple[dict[str, Any], str | None]:
    """Return (options, path_that_provided_them)."""
    for path in candidate_paths():
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as error:  # keep translating on bad config
            print(f"[PDF2REA] ignoring {path}: {error}", file=sys.stderr)
            continue
        if not isinstance(raw, dict):
            continue
        loaded = _deep_merge(DEFAULTS, raw)
        if os.environ.get("PDF2REA_DISABLE") == "1": loaded["enabled"] = False
        return loaded, path

    # An env kill-switch works even with no file present.
    options = json.loads(json.dumps(DEFAULTS))
    if os.environ.get("PDF2REA_DISABLE") == "1":
        options["enabled"] = False
    return options, None


def section(options: dict[str, Any], name: str) -> dict[str, Any]:
    return options.get(name, {}) or {}


def feature_enabled(options: dict[str, Any], name: str) -> bool:
    if not options.get("enabled", True):
        return False
    return bool(section(options, name).get("enabled", True))
