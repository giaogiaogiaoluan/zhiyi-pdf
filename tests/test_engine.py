#!/usr/bin/env python3
"""PDF2REA engine logic tests -- stdlib only, no babeldoc required.

Run with any python3::

    python3 tests/test_engine.py

These cover the *pure* decision functions (the ones that decide whether a run is a
prose artefact, whether a page boundary is a broken sentence, how a table splits into
cells, how numeric style is unified).  The monkey-patch wiring itself is exercised by
``tests/test_patch_wiring.py`` inside the engine venv.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from pdf2rea_engine import (  # noqa: E402
    cross_page,
    formula_guard,
    llm_hook,
    numeric_policy,
    options,
    registry,
    run_ledger,
    table_text,
    terminology,
)

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    if not condition:
        print(f"  FAIL {name} {detail}")


# --------------------------------------------------------------------------- #
def fake_char(text, x, y, advance=None, font_size=10.0):
    box = types.SimpleNamespace(x=x, y=y, x2=x + 6, y2=y + 8)
    return types.SimpleNamespace(
        char_unicode=text,
        # upstream shape: visual_bbox is a Bbox wrapper holding .box
        visual_bbox=types.SimpleNamespace(box=box),
        box=box,
        advance=advance if advance is not None else 6.0,
        pdf_style=types.SimpleNamespace(font_size=font_size),
        vertical=False, xobj_id=0, formula_layout_id=None,
    )


def fake_composition(char):
    return types.SimpleNamespace(pdf_character=char, pdf_line=None, pdf_formula=None)


def fake_paragraph(text, label="text"):
    return types.SimpleNamespace(
        unicode=text, layout_label=label, layout_id=1, xobj_id=0, debug_id="x",
        pdf_paragraph_composition=[], box=None, pdf_style=None, vertical=False,
    )


# --------------------------------------------------------------------------- #
def test_cross_page():
    print("cross_page")
    check("open ending on comma", cross_page.ends_open("the model is robust,") is True)
    check("open ending on bare word", cross_page.ends_open("we further evalu") is True)
    check("closed ending on period", cross_page.ends_open("in this section.") is False)
    check("abbreviation is open", cross_page.ends_open("as shown in Fig.") is True,
          "-> should be open")
    check("et al. is open", cross_page.ends_open("reported by Smith et al.") is True)
    check("question mark closes", cross_page.ends_open("is it larger?") is False)

    check("lowercase head continues", cross_page.starts_continued("and achieves 3.1 mAP.") is True)
    check("bracket head continues", cross_page.starts_continued("[12] confirm this.") is True)
    check("capital head is new sentence", cross_page.starts_continued("We further show") is False)

    opts = {"require_open_ending": True, "min_fragment_chars": 12}
    ok, reason = cross_page.is_continuation(
        "the proposed framework achieves state-of-the-art accuracy on",
        "the three benchmarks we evaluated, surpassing prior work.",
        opts, fake_paragraph("the proposed framework achieves state-of-the-art accuracy on"),
        fake_paragraph("the three benchmarks we evaluated, surpassing prior work."),
    )
    check("detects real cross-page sentence", ok, reason)

    ok2, reason2 = cross_page.is_continuation(
        "This concludes section two.",
        "We now turn to ablations.",
        opts, fake_paragraph("This concludes section two."),
        fake_paragraph("We now turn to ablations."),
    )
    check("rejects two complete sentences", not ok2, reason2)

    ok3, _ = cross_page.is_continuation(
        "the loss decreases monotonically when",
        "the learning rate is reduced.",
        opts, fake_paragraph("the loss decreases", label="table_caption"),
        fake_paragraph("when the rate drops"),
    )
    check("rejects caption boundary", not ok3)

    ok4, _ = cross_page.is_continuation(
        "(1) the first item", "(2) the second item", opts,
        fake_paragraph("(1) the first item"), fake_paragraph("(2) the second item"),
    )
    check("rejects list boundary", not ok4)

    # Batch classification: pages differ => cross-page
    a, b = fake_paragraph("alpha" * 10), fake_paragraph("beta" * 10)
    page1, page2 = object(), object()
    batch = types.SimpleNamespace(paragraphs=[a, b], pages=[page1, page2])
    info = cross_page._describe_batch(
        batch, {"require_open_ending": False, "min_fragment_chars": 1,
                "also_cross_column": True}, "zh-CN")
    check("cross-page batch annotated", bool(info and info.get("directive")),
          str(info and info.get("reason")))
    same_page = types.SimpleNamespace(paragraphs=[a, b], pages=[page1, page1])
    info2 = cross_page._describe_batch(
        same_page, {"require_open_ending": False, "min_fragment_chars": 1,
                    "also_cross_column": True}, "zh-CN")
    check("ordinary same-page batch is not a cross-column pair",
          info2 is None,
          str(info2))
    single = types.SimpleNamespace(paragraphs=[a], pages=[page1])
    check("single-paragraph batch ignored",
          cross_page._describe_batch(single, {}, "zh-CN") is None)


# --------------------------------------------------------------------------- #
def test_numeric_policy():
    print("numeric_policy")
    check("cn 二十三 -> 23", numeric_policy.chinese_to_number("二十三") == 23)
    check("cn 三百五十 -> 350", numeric_policy.chinese_to_number("三百五十") == 350)
    check("cn 两万 -> 20000", numeric_policy.chinese_to_number("两万") == 20000)
    check("rejects mixed junk", numeric_policy.chinese_to_number("二十三日") is None)
    check("2 -> 二", numeric_policy.number_to_chinese(2) == "二")
    check("13 -> 十三", numeric_policy.number_to_chinese(13) == "十三")
    check("100 out of safe range", numeric_policy.number_to_chinese(100) is None)

    # policy: arabic -- rewrite 五 mm -> 5 mm, but never touch 第二章 / decimals
    ledger = numeric_policy.NumericLedger()
    ledger.policy = "arabic"
    text = "实验在 第二章 讨论了三种情况，尺寸为 五 mm，另有 2.5 mm 与 十 次重复。"
    out = numeric_policy.normalize(text, ledger)
    check("arabic policy converts unit numeral", "5 mm" in out, out)
    check("spacing preserved after numeral", "5 mm" in out and "5mm" not in out, out)
    check("arabic policy keeps 第二章", "第二章" in out, out)
    check("arabic policy keeps decimals", "2.5 mm" in out, out)

    ledger2 = numeric_policy.NumericLedger()
    ledger2.policy = "keep"
    same = "温度为 三十 ℃"
    check("keep policy is identity", numeric_policy.normalize(same, ledger2) == same)

    # auto policy needs evidence before switching
    ledger3 = numeric_policy.NumericLedger()
    ledger3.policy = "auto"
    for _ in range(4):
        ledger3.observe("长度为 3 mm 的样品")
    text3 = "长度为 五 mm 的样品"
    out3 = numeric_policy.normalize(text3, ledger3)
    check("auto policy follows document majority", "5 mm" in out3, out3)

    ledger4 = numeric_policy.NumericLedger()
    ledger4.policy = "auto"
    out4 = numeric_policy.normalize("长度为 五 mm", ledger4)
    check("auto defers without evidence", out4 == "长度为 五 mm", out4)

    # placeholders must survive
    ledger5 = numeric_policy.NumericLedger()
    ledger5.policy = "arabic"
    guarded = "<style id='2'>第三章</style> 与 {v1} 及 三十 mm"
    out5 = numeric_policy.normalize(guarded, ledger5)
    check("style tag untouched", "<style id='2'>第三章</style>" in out5, out5)
    check("placeholder untouched", "{v1}" in out5, out5)
    check("outside tag still normalized", "30 mm" in out5, out5)


# --------------------------------------------------------------------------- #
def test_table_segmentation():
    print("table_text")
    # a 2-row x 3-cell grid: "ab cd ef" / "gh ij kl"
    cols = [0.0, 40.0, 80.0]
    rows = [100.0, 60.0]
    comps = []
    for row_y in rows:
        for col_x in cols:
            for offset, letter in enumerate("xy"):
                comps.append(fake_composition(fake_char(letter, col_x + offset * 6, row_y)))
    cells = table_text.segment_into_cells(comps)
    check("splits into 6 cells", len(cells) == 6, f"got {len(cells)}")
    check("each cell holds 2 chars", all(len(c) == 2 for c in cells),
          str([len(c) for c in cells]))
    flat = [c for cell in cells for c in cell]
    check("no character lost", len(flat) == len(comps))
    # reading order: first cell of top row
    first_x = flat[0].pdf_character.visual_bbox.box.x
    check("reading order starts top-left", first_x == 0.0, str(first_x))
    check("single composition passthrough",
          len(table_text.segment_into_cells([comps[0]])) == 1)


# --------------------------------------------------------------------------- #
def test_terminology_enforcement():
    print("terminology")
    hard = [{"source": "three-dimensional", "target": "三维",
             "avoid": ["3 维", "三 维"]}]
    src = "We use a three-dimensional model."
    text = "我们采用 3 维 模型，见 <style id='2'>three-dimensional</style> 说明。"
    out, replaced, violations = terminology.enforce_hard_terms(text, src, hard)
    check("forbidden variant replaced", "三维" in out and "3 维" not in out, out)
    check("replacement counted", replaced == 1, str(replaced))

    # term present but no declared variant -> counted as violation, not rewritten
    text2 = "我们采用立体模型。"
    out2, r2, v2 = terminology.enforce_hard_terms(text2, src, hard)
    check("undeclared variant not rewritten", out2 == text2 and r2 == 0)
    check("violation detected", v2 == 1, str(v2))

    # when the correct term is present, nothing happens
    out3, r3, v3 = terminology.enforce_hard_terms("我们采用三维模型。", src, hard)
    check("correct term is no-op", (out3, r3, v3) == ("我们采用三维模型。", 0, 0))

    numeric_ledger = numeric_policy.NumericLedger()
    numeric_ledger.policy = "arabic"
    combined = terminology.normalize_preserving_terms("三维模型重复 三 次", src, hard, numeric_ledger)
    check("numeric normalization preserves canonical glossary spelling", "三维" in combined and "3维" not in combined and "3 次" in combined, combined)

    # glossary csv parsing incl. header, avoid column, language filter
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8") as handle:
        handle.write("source,target,tgt_lng,avoid\n"
                     "recall,召回率,zh-CN,查全率|查准率\n"
                     "\n"
                     "precision,精确率,zh-CN,\n"
                     "comment-line-should-be-dropped\n"
                     "nocn,美式拼写,en-US,\n"
                     "garbage,,\n")
        path = handle.name
    rows = terminology.read_glossary_csv(path)
    os.unlink(path)
    check("csv rows parsed", len(rows) == 3, str(rows))
    check("target column is target", rows[0]["target"] == "召回率", str(rows[0]))
    check("avoid column parsed", rows[0]["avoid"] == ["查全率", "查准率"], str(rows[0]))
    check("language parsed", rows[0]["language"] == "zh-CN")
    check("header consumed not a term", rows[0]["source"] == "recall")
    check("malformed rows dropped",
          all(r["source"] != "garbage" for r in rows), str(rows))
    check("single-field row dropped",
          all("comment" not in r["source"] for r in rows), str(rows))
    check("foreign language row retained for filtering",
          rows[1]["language"] == "zh-CN" and rows[2]["language"] == "en-US", str(rows))
    check("built-in glossary ships", terminology.built_in_glossary_path() is not None)
    builtin = terminology.read_glossary_csv(terminology.built_in_glossary_path())
    check("built-in glossary non-empty", len(builtin) > 20, str(len(builtin)))
    check("built-in rows carry language", all(r["language"] for r in builtin))


# --------------------------------------------------------------------------- #
def test_formula_guard():
    print("formula_guard")
    opts = {"max_rescue_chars": 8, "operators": []}

    def run(text):
        return [fake_char(ch, index * 6, 100) for index, ch in enumerate(text)]

    rescued, reason = formula_guard.is_prose_artefact(run("[12]"), opts)
    check("citation [12] rescued", rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("3,4"), opts)
    check("numeric citation rescued", rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("†"), opts)
    check("dagger rescued", rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("a"), opts)
    check("ambiguous letter retained as formula", not rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("x^2+y^2"), opts)
    check("real math kept as formula", not rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("α+β=γ"), opts)
    check("greek equation kept", not rescued, reason)
    rescued, reason = formula_guard.is_prose_artefact(run("Eq. 12 shows that"), opts)
    check("long run kept", not rescued, reason)
    for text in ("x2", "1/2", "x", "3×4"):
        check(f"ambiguous math {text} preserved", not formula_guard.is_prose_artefact(run(text), opts)[0])
    formula_chars = run("[12]")
    for ch in formula_chars: ch.formula_layout_id = 0
    check("explicit formula layout preserved even with citation-like text", not formula_guard.is_prose_artefact(formula_chars, opts)[0])
    original_table = fake_paragraph("abcd", label="table")
    original_table.pdf_paragraph_composition = [fake_composition(fake_char(ch, i*50, 100)) for i,ch in enumerate("abcd")]
    doc = types.SimpleNamespace(page=[types.SimpleNamespace(pdf_paragraph=[original_table])])
    class BadFinder:
        def update_paragraph_data(self, *args, **kwargs): raise ValueError("bad cell")
    table_text.split_table_blocks(BadFinder(), doc, {"table"}, lambda **kw:types.SimpleNamespace(**kw))
    check("failed table rebuild preserves original text", doc.page[0].pdf_paragraph == [original_table])
    check("PROSE_SYMBOLS covers common marks",
          {"*", "†", "‡", "°", "±", "‰"} <= formula_guard.PROSE_SYMBOLS)


# --------------------------------------------------------------------------- #
def test_llm_hook():
    print("llm_hook")
    prompt = ("You are a translator.\n\n## Here is the input:\n\n"
              + json.dumps([{"id": 0, "input": "hello world", "layout_label": "text"},
                            {"id": 1, "input": "second part", "layout_label": "text"}],
                           ensure_ascii=False, indent=2))
    items = llm_hook.parse_items(prompt)
    check("items parsed", len(items) == 2 and items[0]["input"] == "hello world", str(items))
    ctx = llm_hook.PromptContext(prompt=prompt, items=items, config=None)
    mutated = ctx.replace_items([{"id": 0, "input": "你好世界", "layout_label": "text"}])
    check("items re-serialized", "你好世界" in mutated and "hello world" not in mutated)

    calls = []
    llm_hook.register_hook(llm_hook.PRIORITY_TERMS, lambda c: c.prompt + "\nTERMS")
    llm_hook.register_hook(llm_hook.PRIORITY_CROSS_PAGE, lambda c: c.prompt + "\nPAGES")
    out = llm_hook.apply_hooks(llm_hook.PromptContext(prompt="P", items=[], config=None))
    check("hooks run in priority order", out.endswith("TERMS\nPAGES"), out)
    llm_hook.reset_hooks()

    # proxy passes non-string bodies straight through
    class FakeEngine:
        def __init__(self):
            self.seen = None

        def llm_translate(self, body, **kwargs):
            self.seen = body
            return "ok"

        def do_llm_translate(self, _):
            return None

    engine = FakeEngine()
    proxy = llm_hook._EngineProxy(engine, config=None)
    llm_hook.register_hook(llm_hook.PRIORITY_TERMS, lambda c: c.prompt + "\nRULES")
    proxy.llm_translate(prompt)
    check("proxy rewrote prompt", engine.seen.endswith("RULES"), engine.seen[-20:])
    check("proxy delegates attributes", proxy.do_llm_translate(None) is None)
    llm_hook.reset_hooks()


# --------------------------------------------------------------------------- #
def test_options_and_ledger():
    print("options + run_ledger")
    directory = tempfile.mkdtemp()
    payload = {"cross_page": {"require_open_ending": False},
               "terminology": {"numeric_policy": "arabic"}}
    path = os.path.join(directory, "pdf2rea.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    loaded, found = options._merge_from(path) if hasattr(options, "_merge_from") else (None, None)

    os.environ["PDF2REA_CONFIG"] = path
    loaded, found = options.load()
    check("env config discovered", found == path, str(found))
    check("deep merge keeps defaults",
          loaded["cross_page"]["min_fragment_chars"] == 12 and
          loaded["cross_page"]["require_open_ending"] is False,
          str(loaded["cross_page"]))
    check("nested override applied", loaded["terminology"]["numeric_policy"] == "arabic")
    check("feature gate on", options.feature_enabled(loaded, "cross_page"))
    del os.environ["PDF2REA_CONFIG"]

    argv_backup = sys.argv
    sys.argv = ["pdf2zh_next", "a.pdf", "--config-file",
                os.path.join(directory, "config.toml")]
    try:
        found_from_argv = options._config_file_from_argv(sys.argv[1:])
        check("--config-file parsed", found_from_argv.endswith("config.toml"))
    finally:
        sys.argv = argv_backup

    os.environ["PDF2REA_DISABLE"] = "1"
    disabled, _ = options.load()
    check("PDF2REA_DISABLE kills patches", disabled["enabled"] is False)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"enabled": True}, f)
        explicit = f.name
    os.environ["PDF2REA_CONFIG"] = explicit
    check("kill switch overrides enabled config", options.load()[0]["enabled"] is False)
    os.unlink(explicit)
    os.environ.pop("PDF2REA_CONFIG", None)
    del os.environ["PDF2REA_DISABLE"]

    # ledger: majority target survives, file round-trips
    temp_home = os.path.join(directory, "home")
    os.makedirs(temp_home, exist_ok=True)
    ledger_file = os.path.join(temp_home, "ledger.json")
    original_path = run_ledger._PATH
    run_ledger._PATH = ledger_file
    run_ledger._loaded = False
    run_ledger._memory.clear()
    run_ledger.record([("recall", "召回率"), ("recall", "召回率"),
                       ("recall", "查全率"), ("precision", "精确率")], "zh-CN")
    pairs = dict(run_ledger.load_pairs("zh-CN"))
    check("ledger stored terms", pairs.get("recall") == "召回率", str(pairs))
    check("ledger second term", pairs.get("precision") == "精确率", str(pairs))
    run_ledger._loaded = False
    run_ledger._memory.clear()
    reloaded = dict(run_ledger.load_pairs("zh-CN"))
    check("ledger persists to disk", reloaded.get("recall") == "召回率", str(reloaded))
    check("other language isolated", run_ledger.load_pairs("en") == [])
    run_ledger._PATH = original_path


# --------------------------------------------------------------------------- #
def test_registry():
    print("registry")

    class Target:
        def method(self, value):
            return value * 2

    def factory(original):
        def method(self, value):
            return original(self, value) + 1

        return method

    check("first wrap applies", registry.wrap_method(Target, "method", factory, "t1"))
    check("second wrap is no-op",
          not registry.wrap_method(Target, "method", factory, "t1"))
    check("wrapped behaviour", Target().method(3) == 7)
    before = len(registry.summary()["applied"])
    registry.wrap_method(Target, "missing", factory, "t2")
    errors = registry.summary()["errors"]
    check("missing attribute reported",
          any("has no missing" in e for e in errors)
          and len(registry.summary()["applied"]) == before,
          str(registry.summary()))
    removed = registry.rollback()
    check("rollback restores original", Target().method(3) == 6 and removed >= 1,
          str(removed))
    registry._errors.clear()

    # post-import hook fires for a module imported later
    fired = []
    module_name = "pdf2rea_tmp_probe_module"
    registry.on_import(module_name, lambda m: fired.append(m.__name__))
    with open(os.path.join(directory := tempfile.mkdtemp(), f"{module_name}.py"),
              "w", encoding="utf-8") as handle:
        handle.write("VALUE = 41\n")
    sys.path.insert(0, directory)
    try:
        import importlib

        module = importlib.import_module(module_name)
        check("post-import hook fired", fired == [module_name], str(fired))
        check("module usable after hook", module.VALUE == 41)
    finally:
        sys.path.remove(directory)
        if module_name in sys.modules:
            del sys.modules[module_name]


# --------------------------------------------------------------------------- #
def main() -> int:
    for test in (test_cross_page, test_numeric_policy, test_table_segmentation,
                 test_terminology_enforcement, test_formula_guard, test_llm_hook,
                 test_options_and_ledger, test_registry):
        test()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed checks: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
