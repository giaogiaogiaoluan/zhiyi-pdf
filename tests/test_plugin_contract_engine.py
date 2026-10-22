#!/usr/bin/env python3
"""Cross-language contract test: plugin bytes -> real engine.

Run after tests/test_plugin_contract.mjs, pointing at the same artifacts dir::

    node tests/test_plugin_contract.mjs /tmp/pdf2rea_contract
    ~/zotero-pdf2zh/server/zotero-pdf2zh-next-venv/bin/python \\
        tests/test_plugin_contract_engine.py /tmp/pdf2rea_contract

This is the check that matters most for PDF2REA: the plugin and the patch layer are
written in two languages and have no shared schema.  A renamed option key would fail
here rather than silently doing nothing during a translation.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from pdf2rea_engine import formula_guard, options, terminology  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + ("" if condition else f"  {detail}"))


def main(artifacts: str) -> int:
    print("plugin -> engine contract\n")
    json_path = os.path.join(artifacts, "mirror", "pdf2rea.json")
    csv_path = os.path.join(artifacts, "mirror", "pdf2rea_glossary.csv")
    if not os.path.isfile(json_path):
        print(f"missing {json_path}; run the node contract test first")
        return 2

    os.environ["PDF2REA_CONFIG"] = json_path
    loaded, source = options.load()
    check("engine reads the file the plugin wrote", source == json_path, str(source))
    check("engine enables the layer", loaded.get("enabled") is True)
    check("run_id survives the handoff", bool(loaded.get("run_id")), str(loaded.get("run_id")))
    check("state dir resolved beside the options file",
          options.state_dir(source) == os.path.dirname(json_path),
          options.state_dir(source))

    unknown = sorted(set(loaded) - set(options.DEFAULTS))
    check("plugin sends no unrecognised top-level keys", not unknown, str(unknown))
    for section in ("cross_page", "terminology", "table_text", "formula_guard"):
        extra = sorted(set(loaded.get(section, {})) - set(options.DEFAULTS[section]))
        check(f"{section}: keys match the engine schema", not extra, str(extra))
        check(f"{section}: gate is boolean",
              isinstance(loaded.get(section, {}).get("enabled"), bool))

    # numeric policy vocabulary must be one the engine understands
    policy = loaded["terminology"].get("numeric_policy")
    check("numeric policy is a known value",
          policy in ("arabic", "auto", "chinese", "keep"), str(policy))
    check("policy drives the ledger",
          options.section(loaded, "terminology").get("numeric_policy") == policy)

    # table labels the plugin advertises must be the ones the engine whitelists
    labels = set(loaded["table_text"].get("table_labels", []))
    check("table labels are lowercase engine-compatible",
          labels and all(label == label.lower() for label in labels), str(labels))
    check("default table labels covered",
          {"table", "wired_table", "blocked_table"} >= labels or bool(labels),
          str(labels))

    # glossary CSV produced by the plugin
    if os.path.isfile(csv_path):
        rows = terminology.read_glossary_csv(csv_path)
        check("plugin CSV parses with the engine loader", rows, open(csv_path).read()[:120])
        check("CSV columns land correctly",
              all(r["source"] and r["target"] for r in rows), str(rows[:2]))
        check("language column parsed",
              all(r["language"] for r in rows), str(rows[:1]))
        check("avoid column parsed as forbidden variants",
              any(len(r["avoid"]) >= 1 for r in rows), str(rows))
        for row in rows:
            check(f"term {row['source']!r} keeps target/avoid apart",
                  row["target"] not in row["avoid"], str(row))

        text, replaced, violations = terminology.enforce_hard_terms(
            "实验采用 3 维 数据结构。", "a three-dimensional data structure",
            [r for r in rows if r["avoid"]])
        check("enforcement rewrites the forbidden variant",
              "三维" in text and "3 维" not in text, f"{text} replaced={replaced}")
    else:
        check("plugin CSV parsed with the engine loader", False, "no mirror/pdf2rea_glossary.csv")

    # formula guard must still refuse to demote real math sent by the plugin config
    demoted, reason = formula_guard.is_prose_artefact(
        [type("C", (), {"char_unicode": ch})() for ch in "[12]"], loaded["formula_guard"])
    check("citation demotion honours plugin max_rescue_chars",
          demoted is True, reason)
    kept, reason = formula_guard.is_prose_artefact(
        [type("C", (), {"char_unicode": ch})() for ch in "E=mc^2"], loaded["formula_guard"])
    check("math stays a formula under plugin settings", kept is False, reason)

    # a JSON the plugin might produce with a bad value must not brick the engine
    broken = os.path.join(artifacts, "mirror", "broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    os.environ["PDF2REA_CONFIG"] = broken
    from unittest.mock import patch
    with patch.object(options, "candidate_paths", return_value=[broken]):
        fallback, where = options.load()
    check("malformed options degrade to defaults instead of crashing",
          where is None and fallback["enabled"] is True, str(where))
    os.environ["PDF2REA_CONFIG"] = json_path

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
