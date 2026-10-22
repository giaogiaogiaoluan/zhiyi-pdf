#!/usr/bin/env python3
"""PDF2REA wiring tests -- run inside the engine venv against the real babeldoc.

    ~/zotero-pdf2zh/server/zotero-pdf2zh-next-venv/bin/python \\
        tests/test_patch_wiring.py

Everything here uses babeldoc's own dataclasses, so a renamed attribute or a changed
call signature fails loudly instead of silently no-oping in production.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + ("" if condition else f"  {detail}"))


# --------------------------------------------------------------------------- #
def main() -> int:
    import pdf2rea_engine
    check("wiring tests load the working checkout",os.path.realpath(pdf2rea_engine.__file__).startswith(os.path.realpath(os.path.join(os.path.dirname(__file__),"../engine"))))
    from pdf2rea_engine import apply

    report = apply()
    check("apply() returns without error", isinstance(report, dict), str(report))

    import importlib

    layout_helper = importlib.import_module(
        "babeldoc.format.pdf.document_il.utils.layout_helper")
    paragraph_finder = importlib.import_module(
        "babeldoc.format.pdf.document_il.midend.paragraph_finder")
    styles = importlib.import_module(
        "babeldoc.format.pdf.document_il.midend.styles_and_formulas")
    il_translator = importlib.import_module(
        "babeldoc.format.pdf.document_il.midend.il_translator")
    il_translator_llm = importlib.import_module(
        "babeldoc.format.pdf.document_il.midend.il_translator_llm_only")
    translation_config = importlib.import_module("babeldoc.format.pdf.translation_config")
    glossary_module = importlib.import_module("babeldoc.glossary")
    from pdf2rea_engine import formula_guard, table_text

    il = importlib.import_module("babeldoc.format.pdf.document_il.il_version_1")
    Box, PdfStyle, PdfCharacter, VisualBbox = il.Box, il.PdfStyle, il.PdfCharacter, il.VisualBbox
    PdfLine, PdfFormula = il.PdfLine, il.PdfFormula
    PdfParagraph, PdfParagraphComposition = il.PdfParagraph, il.PdfParagraphComposition
    Page, Document = il.Page, il.Document

    # ---------------- Fix 3a: table labels enter text grouping --------------
    check("is_text_layout still rejects images",
          not layout_helper.is_text_layout(layout_helper.Layout(1, "figure")))
    check("is_text_layout accepts body text",
          layout_helper.is_text_layout(layout_helper.Layout(2, "plain text")))
    check("is_text_layout NOW accepts table blocks",
          layout_helper.is_text_layout(layout_helper.Layout(3, "table")),
          "the whole-table label is still whitelisted out -> tables stay untranslated")
    check("is_text_layout NOW accepts wired tables",
          layout_helper.is_text_layout(layout_helper.Layout(4, "wired_table")))
    check("paragraph_finder's bound copy is patched too",
          paragraph_finder.is_text_layout(layout_helper.Layout(5, "table")),
          "paragraph_finder.py:34 imports by value; unpatched copy = fix does nothing")

    # ---------------- Fix 4: corner-mark demotion ---------------------------
    instance = object.__new__(styles.StylesAndFormulas)

    def chars(text, size=10.0):
        out = []
        for index, ch in enumerate(text):
            box = Box(x=index * 6.0, y=100.0, x2=index * 6.0 + 5.0, y2=108.0)
            out.append(PdfCharacter(
                pdf_style=PdfStyle(graphic_state=None, font_id="F1", font_size=size),
                box=box, visual_bbox=VisualBbox(box=box), vertical=False, scale=None,
                pdf_character_id=None, char_unicode=ch, advance=6.0, xobj_id=None,
                debug_info=None, formula_layout_id=None, render_order=None,
                sub_render_order=None,
            ))
        return out

    citation = instance.create_composition(chars("[12]"), True, 0, True)
    check("citation corner mark demoted to text line",
          citation.pdf_line is not None and citation.pdf_formula is None,
          f"got formula={citation.pdf_formula is not None}")

    real_math = instance.create_composition(chars("x^2+y^2=1"), True, 1, False)
    check("real formula stays a formula",
          real_math.pdf_formula is not None,
          "over-demoting math would corrupt equations")

    greek = instance.create_composition(chars("α+β"), True, 2, False)
    check("greek expression stays a formula", greek.pdf_formula is not None)

    dagger = instance.create_composition(chars("†"), True, 3, True)
    check("dagger footnote demoted", dagger.pdf_line is not None)

    rescued = formula_guard.stats()
    check("rescue counter advanced", rescued["rescued"] >= 2, str(rescued))

    # Upstream compares y_offset unconditionally; a None offset must not crash us.
    crash_formula = PdfFormula(pdf_character=chars("12"), is_corner_mark=True,
                               x_offset=None, y_offset=None, line_id=None)
    try:
        verdict = instance.is_translatable_formula(crash_formula)
        check("None offsets handled without crash", verdict is True, str(verdict))
    except Exception as error:
        check("None offsets handled without crash", False, repr(error))

    # ---------------- Fix 4b: corner-mark sweep in process_translatable ----
    formula = PdfFormula(pdf_character=chars("12"), is_corner_mark=True,
                         x_offset=0.0, y_offset=0.0, line_id=0)
    paragraph = PdfParagraph(
        box=Box(0, 0, 100, 20), pdf_style=None,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=formula)],
        xobj_id=None, unicode="12", vertical=False, debug_id="p1",
        layout_label="text", layout_id=1,
    )
    page = Page(mediabox=None, cropbox=None, pdf_xobject=[], page_layout=[],
                pdf_rectangle=[], pdf_font=[], pdf_paragraph=[paragraph],
                pdf_figure=[], pdf_character=[], pdf_curve=[], pdf_form=[],
                base_operations=None, page_number=0, unit=None)
    before = paragraph.pdf_paragraph_composition[0].pdf_formula is not None
    instance.process_translatable_formulas(page)
    after = paragraph.pdf_paragraph_composition[0]
    check("corner-mark formula existed before sweep", before)
    check("sweep converted it into a text line",
          after.pdf_line is not None and after.pdf_formula is None,
          "upstream skips is_corner_mark at styles_and_formulas.py:634")

    # ---------------- Fix 3b: table block -> cell paragraphs ----------------
    finder = object.__new__(paragraph_finder.ParagraphFinder)
    comps = []
    for row_y in (200.0, 150.0):
        for col_x in (10.0, 90.0, 170.0):
            box = Box(x=col_x, y=row_y, x2=col_x + 6, y2=row_y + 8)
            char = PdfCharacter(
                pdf_style=PdfStyle(graphic_state=None, font_id="F1", font_size=9.0),
                box=box, visual_bbox=VisualBbox(box=box), vertical=False, scale=None,
                pdf_character_id=None, char_unicode="x", advance=6.0, xobj_id=None,
                debug_info=None, formula_layout_id=None, render_order=None,
                sub_render_order=None,
            )
            comps.append(PdfParagraphComposition(pdf_character=char))
    table_paragraph = PdfParagraph(
        box=Box(0, 0, 300, 300), pdf_style=None,
        pdf_paragraph_composition=comps, xobj_id=None, unicode="xxxxxx",
        vertical=False, debug_id="t1", layout_label="table", layout_id=7,
    )
    table_page = Page(mediabox=None, cropbox=None, pdf_xobject=[], page_layout=[],
                      pdf_rectangle=[], pdf_font=[], pdf_paragraph=[table_paragraph],
                      pdf_figure=[], pdf_character=[], pdf_curve=[], pdf_form=[],
                      base_operations=None, page_number=0, unit=None)
    document = Document(page=[table_page], total_pages=None)
    created = table_text.split_table_blocks(
        finder, document, {"table", "wired_table", "blocked_table"}, PdfParagraph)
    labels = [p.layout_label for p in table_page.pdf_paragraph]
    check("table block split into cells", created == 6, f"created={created} labels={labels}")
    check("cell paragraphs keep the table label",
          all(label == "table" for label in labels), str(labels))
    check("each cell got its own unicode",
          all((p.unicode or "").strip() == "x" for p in table_page.pdf_paragraph),
          str([p.unicode for p in table_page.pdf_paragraph]))
    check("each cell got a box",
          all(p.box is not None for p in table_page.pdf_paragraph))

    # ---------------- Fix 1 + 2: prompt rewriting ---------------------------
    from pdf2rea_engine import cross_page, llm_hook

    tail = PdfParagraph(box=Box(0, 0, 300, 10), pdf_paragraph_composition=[],
                        unicode="The model converges when the learning rate is",
                        layout_label="text", layout_id=1, debug_id="a")
    head = PdfParagraph(box=Box(0, 0, 300, 10), pdf_paragraph_composition=[],
                        unicode="set below 1e-4 in all of our experiments.",
                        layout_label="text", layout_id=2, debug_id="b")
    page_a, page_b = object(), object()
    batch = types_BatchParagraph([tail, head], [page_a, page_b])
    info = cross_page._describe_batch(
        batch, {"require_open_ending": True, "min_fragment_chars": 12,
                "also_cross_column": True}, "zh-CN")
    check("cross-page pair recognised as continuation",
          info is not None and info["kind"] == "cross_page", str(info))
    check("continuation carries a directive", bool(info and info.get("directive")))

    closed_tail = PdfParagraph(box=Box(0, 0, 300, 10), pdf_paragraph_composition=[],
                               unicode="This proves the lemma. ", layout_label="text",
                               layout_id=1, debug_id="c")
    info2 = cross_page._describe_batch(
        batch.__class__([closed_tail, head], [page_a, page_b]),
        {"require_open_ending": True, "min_fragment_chars": 12, "also_cross_column": True},
        "zh-CN")
    check("two complete sentences are NOT merged",
          info2 is not None and info2.get("directive") is None, str(info2))

    prompt = il_translator_llm.PROMPT_TEMPLATE.substitute(
        role_block="You are a translator. Follow all rules strictly.",
        glossary_usage_rules_block="", contextual_hints_block="",
        glossary_tables_block="", lang_out="zh-CN",
        json_input_str=json.dumps(
            [{"id": 0, "input": tail.unicode, "layout_label": "text"},
             {"id": 1, "input": head.unicode, "layout_label": "text"}],
            ensure_ascii=False),
    )
    seen = {}

    class FakeEngine:
        def do_llm_translate(self, _):
            return None

        def llm_translate(self, body, **kwargs):
            seen["body"] = body
            seen["calls"] = seen.get("calls",0)+1
            return json.dumps([{"id":0,"output":"在我们的所有实验中，当学习率设置为一万分之一以下时，模型都能够稳定收敛。"}],ensure_ascii=False)

    from pdf2rea_engine import terminology

    terminology.configure({"numeric_policy": "arabic", "prompt_rules": True,
                           "use_builtin_glossary": True, "reuse_ledger": False,
                           "glossary_files": [], "custom_rules": ["保留变量名 X"]})
    terminology.install({"numeric_policy": "arabic", "prompt_rules": True,
                         "use_builtin_glossary": True, "reuse_ledger": False,
                         "glossary_files": [], "custom_rules": ["保留变量名 X"]})
    cross_page._local.pair = info
    proxy = llm_hook._EngineProxy(FakeEngine(), config=None)
    mapped_response = proxy.llm_translate(prompt)
    cross_page._local.pair = None
    rewritten = seen.get("body", "")
    check("model receives one whole paragraph",
          len(llm_hook.parse_items(rewritten)) == 1 and "Whole-Paragraph Translation" in rewritten)
    check("one successful joint translation uses one API call",seen.get("calls")==1)
    check("continuous translation maps back to two original ids",
          [x["id"] for x in json.loads(mapped_response)]==[0,1])
    check("prompt gained consistency rules",
          "Terminology and Style Consistency" in rewritten)
    check("custom rule forwarded", "保留变量名 X" in rewritten)
    check("original prompt untouched", "The model converges" in rewritten)
    check("rewrite happens before the engine sees it", rewritten != prompt)

    # glossary merge (upstream bug at translation_config.py:130-140)
    shared = object.__new__(translation_config.SharedContextCrossSplitPart)
    shared._lock = __import__("threading").RLock()
    user = glossary_module.Glossary("user", [glossary_module.GlossaryEntry("recall", "召回率", "zh-CN")])
    auto = glossary_module.Glossary("auto", [glossary_module.GlossaryEntry("precision", "精确率", "zh-CN")])
    shared.user_glossaries = [user]
    shared.auto_extracted_glossary = auto
    merged = shared.get_glossaries_for_translation(True)
    check("user glossary survives auto extraction",
          any(g.name == "user" for g in merged),
          f"upstream returns only {[g.name for g in merged]}")

    # glossary injection through the LLM translator constructor path
    options = {"use_builtin_glossary": True, "glossary_files": [], "reuse_ledger": False}
    built = [
        glossary_module.Glossary(
            name,
            [glossary_module.GlossaryEntry(row["source"], row["target"], row["language"])
             for row in rows],
        )
        for name, rows in terminology.collect_glossary_rows(options, "zh-CN")
    ]
    entries = {e.source: e.target for g in built for e in g.entries}
    check("built-in glossary compiles in babeldoc", len(built) >= 1 and len(entries) > 20,
          f"{len(built)} glossaries / {len(entries)} terms")
    check("glossary target is the Chinese term",
          entries.get("three-dimensional") == "三维", str(entries.get("three-dimensional")))
    check("hyperscan matcher finds the term",
          any(g.get_active_entries_for_text("a three-dimensional model") for g in built))

    # ---------------- rollback restores stock behaviour ---------------------
    from pdf2rea_engine import registry

    removed = registry.rollback()
    check("rollback reports restorations", removed >= 1, str(removed))
    check("is_text_layout restored for tables",
          not layout_helper.is_text_layout(layout_helper.Layout(9, "table")),
          "if rollback cannot undo it, uninstalling the plugin leaves behaviour changed")

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


class BatchLike:
    """Stand-in for babeldoc's BatchParagraph (only .paragraphs/.pages are read)."""

    def __init__(self, paragraphs, pages):
        self.paragraphs = paragraphs
        self.pages = pages


types_BatchParagraph = BatchLike


if __name__ == "__main__":
    raise SystemExit(main())
