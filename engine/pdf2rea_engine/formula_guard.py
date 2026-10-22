"""Fix 4 -- superscripts, citation marks and symbols no longer punch holes in text.

Root cause chain (verified in the installed engine)::

    styles_and_formulas.py:471-503   is_corner_mark = a char whose font size is
        < 0.79 x the previous char's (or > 1.1 x while already in corner-mark state)
    styles_and_formulas.py:505       is_formula = is_formula or is_corner_mark
    styles_and_formulas.py:509-516   the flag is *sticky* along the rest of the line
    styles_and_formulas.py:933-944   create_composition() -> PdfParagraphComposition(
                                         pdf_formula=PdfFormula(..., is_corner_mark=True))
    styles_and_formulas.py:621-648   process_translatable_formulas() converts "numeric"
        formulas back into text lines, but explicitly skips them when
        ``composition.pdf_formula.is_corner_mark`` -- so corner marks never come back.
    formular_helper.py:33-45         is_formulas_start_char() also fires for unicode
        categories Mn/Sk/Sm/Zl/Zp/Zs/Co (angle brackets, diacritics, trade marks)
    formular_helper.py:49            and for the regex [0-9\\[\\]•] -- citation "[12]"
    il_translator.py:622-626         pre_translate_paragraph():
        "elif composition.pdf_formula:  # 不需要翻译纯公式  ->  return None"

Net effect: one citation superscript or one `Sm` symbol can flip the remainder of a
line into formula state.  The paragraph is then handed back as ``pdf_formula``, the
translator returns ``None`` for it, nothing gets translated and the re-typeset region
shows up as a blank gap in the middle of a paragraph -- the reported symptom.

Repair: intercept the single choke point ``create_composition`` (every formula
composition in babeldoc is born there) and demote runs that are prose artefacts --
citation digits, footnote daggers, unit marks, short symbol clusters -- back to
ordinary ``PdfLine`` compositions.  Then sweep the corner-mark formulas that survived,
which upstream deliberately ignores.  Genuine math (anything containing operators or
LaTeX-ish tokens) is never touched.
"""

from __future__ import annotations

import re

from . import registry

# Runs that are obviously not mathematics.
CITATION_RE = re.compile(r"^[\s()\[\].,;:'\"A-Za-z0-9\-–—/°*#⁰-⁹]+$")
SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹¹⁰"
MATH_TOKENS = ("=", "≈", "≠", "<", ">", "≤", "≥", "+", "−", "^", "_", "∑", "∫", "√",
               "×", "÷", "→", "⇒", "α", "β", "γ", "δ", "λ", "μ", "σ", "ω", "Δ", "Ω", "\\frac",
               "\\sum", "\\int", "\\alpha")

# Symbols that legitimately appear in scholarly prose and must not seed a formula run.
# Note: × and ÷ are deliberately absent -- they are arithmetic in most papers.
PROSE_SYMBOLS = set("*†‡§¶°±‰™®©·•∙′″")

_stats = {"rescued": 0, "kept_as_formula": 0, "recovered_corner_marks": 0, "reasons": {}}


def _run_text(chars) -> str:
    return "".join(getattr(c, "char_unicode", "") or "" for c in chars)


def has_math_token(text: str, extra_operators=()) -> bool:
    tokens = list(MATH_TOKENS) + [t for t in (extra_operators or []) if t]
    lowered = text.lower()
    return any(token.lower() in lowered for token in tokens)


def is_prose_artefact(chars, options: dict) -> tuple[bool, str]:
    """Can this formula run safely be treated as ordinary text again?"""
    # Layout-model formula membership takes precedence over text heuristics.
    if any(getattr(c, "formula_layout_id", None) is not None for c in chars):
        return False, "explicit formula layout"
    text = _run_text(chars)
    stripped = text.strip()
    if not stripped:
        return False, "empty"

    if re.search(r"[A-Za-z].*\d|\d.*[A-Za-z]|\d\s*/\s*\d", stripped):
        return False, "ambiguous variable, exponent, or fraction"
    limit = int(options.get("max_rescue_chars", 8))
    operators = options.get("operators", [])
    # An operator always wins -- never demote real math.
    if has_math_token(stripped, operators):
        return False, "contains math operators"

    body = [c for c in stripped if not c.isspace()]

    # 1. Pure prose symbols: dagger, section mark, degree, prime, bullet.
    if body and all(c in PROSE_SYMBOLS for c in body):
        return True, "prose symbol"

    # 2. Citation / footnote marks: "[12]", "3,4", "¹²", "1", "*".
    if len(stripped) <= limit and CITATION_RE.match(stripped):
        if any(ch.isdigit() or ch in SUPERSCRIPT_DIGITS for ch in stripped):
            return True, "citation or footnote mark"
        if any(ch in PROSE_SYMBOLS for ch in stripped):
            return True, "symbol with marker"

    # 3. Ambiguous alphabetic markers stay as formulas unless explicitly requested.
    # Short alphabetic footnote markers: "a", "b1", "fn2".
    if options.get("rescue_letter_marks", False) and len(stripped) <= 3 and re.match(r"^[A-Za-z]{1,2}\d?$", stripped):
        return True, "footnote letter"

    return False, "not a recognisable mark"


def install(options: dict) -> None:
    target = "babeldoc.format.pdf.document_il.midend.styles_and_formulas"

    def _patch(module):
        cls = module.StylesAndFormulas
        PdfLine = module.PdfLine
        PdfParagraphComposition = module.PdfParagraphComposition

        # ---- (A) demote prose artefacts at composition time -----------------
        if options.get("rescue_corner_marks", True):

            def _composition_factory(original):
                def create_composition(self, chars, is_formula, line_index,
                                       is_corner_mark=False):
                    if is_formula:
                        rescued, reason = is_prose_artefact(chars, options)
                        if rescued:
                            _stats["rescued"] += 1
                            _stats["reasons"][reason] = _stats["reasons"].get(reason, 0) + 1
                            # Delegate to the caller's own "not a formula" branch
                            # instead of duplicating PdfLine construction.
                            return original(self, chars, False, line_index, is_corner_mark)
                        _stats["kept_as_formula"] += 1
                    return original(self, chars, is_formula, line_index, is_corner_mark)

                return create_composition

            if registry.wrap_method(cls, "create_composition", _composition_factory,
                                    "formula_guard"):
                registry.note_applied("StylesAndFormulas.create_composition prose-run demotion")

        # ---- (B) widen the "this formula is really text" predicate ----------
        def _translatable_factory(original):
            def is_translatable_formula(self, formula):
                try:
                    if original(self, formula):
                        return True
                except Exception as error:
                    # Upstream compares formula.y_offset unconditionally
                    # (styles_and_formulas.py:956), which raises when offset
                    # calculation was skipped.  PDF2REA must not inherit that crash.
                    registry._errors.append(f"is_translatable_formula: {error!r}")
                chars = list(getattr(formula, "pdf_character", []) or [])
                rescued, _reason = is_prose_artefact(chars, options)
                return bool(rescued)

            return is_translatable_formula

        if registry.wrap_method(cls, "is_translatable_formula", _translatable_factory,
                                "formula_guard_translatable"):
            registry.note_applied("StylesAndFormulas.is_translatable_formula widened")

        # ---- (C) sweep the corner marks upstream refuses to convert ---------
        def _pass_factory(original):
            def process_translatable_formulas(self, page):
                original(self, page)
                for paragraph in getattr(page, "pdf_paragraph", []) or []:
                    compositions = getattr(paragraph, "pdf_paragraph_composition", None)
                    if not compositions:
                        continue
                    rebuilt = []
                    changed = False
                    for composition in compositions:
                        formula = getattr(composition, "pdf_formula", None)
                        if (
                            formula is not None
                            and getattr(formula, "is_corner_mark", False)
                            and self.is_translatable_formula(formula)
                        ):
                            line = PdfLine(pdf_character=list(formula.pdf_character or []))
                            self.update_line_data(line)
                            rebuilt.append(PdfParagraphComposition(pdf_line=line))
                            _stats["recovered_corner_marks"] += 1
                            changed = True
                        else:
                            rebuilt.append(composition)
                    if changed:
                        paragraph.pdf_paragraph_composition = rebuilt
                return None

            return process_translatable_formulas

        if registry.wrap_method(cls, "process_translatable_formulas", _pass_factory,
                                "formula_guard_pass"):
            registry.note_applied("StylesAndFormulas.process_translatable_formulas corner-mark sweep")

        # ---- (D) stop prose symbols from seeding formula runs ---------------
        helper = registry.require("babeldoc.format.pdf.document_il.utils.formular_helper")
        if helper is not None and hasattr(helper, "is_formulas_start_char"):

            def _start_factory(original):
                def is_formulas_start_char(char, *rest, **kwargs):
                    if char in PROSE_SYMBOLS:
                        return False
                    return original(char, *rest, **kwargs)

                return is_formulas_start_char

            if registry.patch_module_function(
                helper, "is_formulas_start_char", _start_factory, "formula_guard_startchar",
                "formular_helper.is_formulas_start_char prose symbols",
            ):
                # paragraph_finder imported the name by value; rebind that copy too.
                registry.rebind_dependents(
                    "is_formulas_start_char", helper.is_formulas_start_char,
                    ("babeldoc.format.pdf.document_il.midend.paragraph_finder",
                     "babeldoc.format.pdf.document_il.midend.styles_and_formulas"),
                )

    registry.on_import(target, _patch)


def stats() -> dict:
    return {
        "rescued": _stats["rescued"],
        "kept_as_formula": _stats["kept_as_formula"],
        "recovered_corner_marks": _stats["recovered_corner_marks"],
        "reasons": dict(_stats["reasons"]),
    }
