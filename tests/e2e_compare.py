#!/usr/bin/env python3
"""End-to-end harness: run the real engine twice and compare the produced PDFs.

    VENV_PY=/path/to/separate/venv/bin/python scripts/run_tests.sh --e2e

Use a separate engine environment (for example the plugin's private ~/.pdf2rea
runtime), not the original PDF2zh installation. The test runner gives cache,
configuration and ledger files private temporary paths. To invoke this harness
directly, set PDF2REA_CACHE_DIR, PDF2REA_TRANSLATOR_CACHE, PDF2REA_CONFIG_DIR and
PDF2REA_LEDGER_PATH to a disposable test directory first.

Why a fake translator: the goal is to prove that the patch layer does not break a
real pipeline, and that table / superscript text now *reaches* the translator.  An
offline ``--clitranslator`` command gives a deterministic, network-free engine that
still exercises the whole IL path (parser -> layouts -> paragraphs -> styles/formulas
-> translation -> typesetting -> renderer).  Anything this harness cannot exercise is
covered by tests/test_patch_wiring.py against babeldoc's real objects.

Measurable signal: every ASCII word becomes 词, so any English word still visible in
the output text was NOT translated.  We count leftover English per region.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.abspath(os.path.join(HERE, "..", "engine"))
FAKE_TRANSLATOR = os.path.join(HERE, "fake_translator.py")
FIXTURE_BUILDER = os.path.join(HERE, "make_fixture.py")
ENGINE_RUNNER = os.path.join(HERE, "engine_runner.py")

TABLE_WORDS = ("Method", "Recall", "Latency", "Baseline", "Ours")
CITATION_WORDS = ("transformer", "architectures", "regularisation", "dagger")

PYTHON = sys.executable


def run(command, env=None, label="", log_path=None):
    result = subprocess.run(command, capture_output=True, text=True, env=env)
    if log_path:
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("=== stdout ===\n" + result.stdout)
            handle.write("\n=== stderr ===\n" + result.stderr)
    if result.returncode != 0:
        tail = (result.stdout + "\n" + result.stderr)[-2500:]
        print(f"--- {label} FAILED ---\n{tail}")
    return result


def build_fixture(workdir: str) -> str:
    path = os.path.join(workdir, "fixture.pdf")
    result = run([PYTHON, FIXTURE_BUILDER, path], label="fixture")
    if result.returncode != 0 or not os.path.isfile(path):
        raise SystemExit("fixture build failed")
    return path


def options_path_for(workdir: str) -> str:
    return os.path.join(workdir, "pdf2rea.json")


def write_options(workdir: str, *, enabled: bool, run_id: str) -> str:
    """Same file the Zotero plugin writes before submitting a job."""
    path = options_path_for(workdir)
    payload = {"enabled": enabled, "run_id": run_id}
    if enabled:
        payload["table_text"] = {"cell_segmentation": True}
        payload["formula_guard"] = {"rescue_corner_marks": True}
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    return path


def counters(workdir: str, run_id: str) -> dict:
    sys.path.insert(0, ENGINE)
    from pdf2rea_engine import status as status_module

    return status_module.aggregate(workdir, run_id) or {}


def translate(fixture: str, outdir: str, *, pdf2rea: bool, env_extra=None,
              run_id: str = "e2e") -> int:
    """Run the real engine. Both arms use the same runner, so the patch layer is the
    only difference between them."""
    os.makedirs(outdir, exist_ok=True)
    env = os.environ.copy()
    # Keep every artefact of the test run inside workdir: the plugin writes this
    # same file in production, and state_dir() is derived from its location.
    workdir = os.path.dirname(outdir)
    write_options(workdir, enabled=pdf2rea, run_id=run_id)
    env["PDF2REA_CONFIG"] = options_path_for(workdir)
    env.pop("PDF2REA_DISABLE", None)
    if not pdf2rea: env["PDF2REA_DISABLE"] = "1"
    env["PYTHONPATH"] = ENGINE + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)
    command = [PYTHON, ENGINE_RUNNER] + ([] if pdf2rea else ["--no-pdf2rea"]) + [
        fixture,
        "--clitranslator",
        "--clitranslator-command", f"{PYTHON} {FAKE_TRANSLATOR}",
        "--lang-in", "en",
        "--lang-out", "zh-CN",
        "--output", outdir,
        "--watermark-output-mode", "no_watermark",
        "--no-dual",
        "--qps", "50",
    ] + getattr(translate, "extra_args", [])
    label = "pdf2zh_next+PDF2REA" if pdf2rea else "pdf2zh_next (stock)"
    result = run(command, env=env, label=label,
                 log_path=os.path.join(outdir, "run.log"))
    # Guard against a silently inactive patch layer -- an identical run proves nothing.
    log = open(os.path.join(outdir, "run.log"), encoding="utf-8").read()
    if pdf2rea:
        if "[PDF2REA" not in log:
            print("WARN patched run did not load the patch layer:", log[-400:])
        else:
            for line in log.splitlines():
                if line.startswith("[PDF2REA") or "PDF2REA]" in line:
                    print("   patch banner:", line.strip())
    else:
        if "[PDF2REA" in log:
            print("WARN stock run unexpectedly loaded patches")
    return result.returncode


def analyse(pdf_path: str) -> dict:
    import pymupdf

    doc = pymupdf.open(pdf_path)
    pages = [page.get_text() for page in doc]
    doc.close()
    blob = "\n".join(pages)
    english = re.findall(r"[A-Za-z]{4,}", blob)
    return {
        "path": pdf_path,
        "pages": len(pages),
        "chinese_chars": len(re.findall(r"[一-鿿]", blob)),
        "english_words": len(english),
        "table_leftovers": sorted({w for w in english if w in TABLE_WORDS}),
        "citation_leftovers": sorted({w for w in english if w in CITATION_WORDS}),
        "page1_english": len(re.findall(r"[A-Za-z]{4,}", pages[0] if pages else "")),
        "page2_english": len(re.findall(r"[A-Za-z]{4,}", pages[1] if len(pages) > 1 else "")),
    }


def main() -> int:
    args = [a for a in sys.argv[1:]]
    pdf_override = None
    pages = None
    if "--pdf" in args:
        index = args.index("--pdf")
        pdf_override = args[index + 1]
        del args[index:index + 2]
    if "--pages" in args:
        index = args.index("--pages")
        pages = args[index + 1]
        del args[index:index + 2]
    workdir = args[0] if args else tempfile.mkdtemp(prefix="pdf2rea-e2e-")
    os.makedirs(workdir, exist_ok=True)
    if pdf_override:
        fixture = os.path.abspath(os.path.expanduser(pdf_override))
        print(f"using real document: {os.path.basename(fixture)}"
              + (f" (pages {pages})" if pages else ""))
    else:
        fixture = build_fixture(workdir)
    translate.extra_args = ["--pages", pages] if pages else []

    stock_dir = os.path.join(workdir, "stock")
    rea_dir = os.path.join(workdir, "pdf2rea")
    stock_rc = translate(fixture, stock_dir, pdf2rea=False, run_id="e2e-stock")
    rea_rc = translate(fixture, rea_dir, pdf2rea=True, run_id="e2e-rea")

    def find(dirpath):
        for name in sorted(os.listdir(dirpath)) if os.path.isdir(dirpath) else []:
            if name.endswith(".mono.pdf"):
                return os.path.join(dirpath, name)
        return None

    stock_pdf, rea_pdf = find(stock_dir), find(rea_dir)
    print(f"stock rc={stock_rc} out={stock_pdf}")
    print(f"pdf2rea rc={rea_rc} out={rea_pdf}")
    if stock_pdf is None or rea_pdf is None:
        print("E2E INCONCLUSIVE: one of the runs produced no PDF")
        return 2

    stock, rea = analyse(stock_pdf), analyse(rea_pdf)
    print("\n=== extracted text metrics ===")
    for key in ("chinese_chars", "english_words", "table_leftovers",
                "citation_leftovers", "page1_english", "page2_english"):
        print(f"{key:20s} stock={stock[key]!r:28} pdf2rea={rea[key]!r}")

    print("\n=== patch-layer counters (aggregated over all worker processes) ===")
    rea_counters = counters(workdir, "e2e-rea")
    stock_counters = counters(workdir, "e2e-stock")
    print(f"pdf2rea processes={len(rea_counters.get('processes', []))} "
          f"patches={len(rea_counters.get('patches', []))} "
          f"stats={json.dumps(rea_counters.get('stats', {}), ensure_ascii=False)[:600]}")
    print(f"stock   patches={len(stock_counters.get('patches', []))} "
          f"(expected 0: the control arm disables the layer)")

    failures = []
    if not rea_counters.get("patches"):
        failures.append("no status events written -- cannot prove the patch layer ran")
    if len(rea_counters.get("processes", [])) < 2:
        print("note  only one process reported status for the patched arm")
    if stock_rc != 0:
        failures.append("stock run failed (harness problem)")
    if rea_rc != 0:
        failures.append("PDF2REA run failed -- the patch layer breaks the pipeline")
    if rea["chinese_chars"] == 0:
        failures.append("PDF2REA run produced no translated text at all")
    # Evidence-based expectations: a fix only "counts" as engaged when the document
    # actually contains the structure it targets. Absence is reported as inconclusive
    # for this corpus, never silently as a pass or a failure.
    table_blocks = rea_counters.get("stats", {}).get("table_text", {}).get("table_blocks", 0)
    rescued = rea_counters.get("stats", {}).get("formula_guard", {}).get("rescued", 0)
    if table_blocks:
        print(f"PASS  Fix 3 engaged: {table_blocks} table blocks re-segmented")
        if stock["table_leftovers"] and not rea["table_leftovers"]:
            print("PASS  table headers untranslated in stock, translated with PDF2REA")
    elif stock["table_leftovers"] == rea["table_leftovers"]:
        print("n/a   no whole-table layout detected in this document -> Fix 3 had "
              "nothing to act on here (see docs/问题根因: the upstream flag is dead "
              "code regardless)")
    if rescued:
        print(f"PASS  Fix 4 engaged: {rescued} superscript/citation runs demoted "
              "from formula to translatable text")
    else:
        print("n/a   no formula runs classified in this document")
    if stock["english_words"] < rea["english_words"]:
        failures.append("PDF2REA left MORE untranslated text than stock")
    print()
    for failure in failures:
        print("FAIL " + failure)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
