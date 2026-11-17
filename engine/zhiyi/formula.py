"""Inline formula detection.

Deliberately conservative: a run becomes a formula only when it contains
something text cannot represent (math-italic variables, stacked scripts,
glyphs without a usable Unicode value). Plain digits, percent signs and
lone operators stay in the sentence, where the translator can see them.
"""

from __future__ import annotations

import re
import unicodedata

from .model import Glyph, Line

TEXT, SOFT, HARD = 0, 1, 2

_MATH_ITALIC = re.compile(
    r"^(CMMIB?\d|MTMI|RMTMI|LMMathItalic|NewTXMI|NewPXMI|ntxmia?|txmia?|pxmia?|rtxmi|zplmi|"
    r"EUFM|EURM|EUSM|RSFS|MSBM|BBM|BBOLD|DSROM|PazoMath|.*MathIt)", re.I)
_MATH_SYMBOL = re.compile(
    r"^(CMSY|CMBSY|MTSY|MSAM|LMMathSymbols|txsy|pxsy|ntxsy|STMARY|WASY|LASY|Symbol|MTExtra|"
    r"MathematicalPi|Euclid(Symbol|Extra|Math)|CM_Maths|AdvMathSymb|AdvPSSym|AdvP4C4E74|"
    r".*Math|.*Symbol)", re.I)
_MATH_EXT = re.compile(r"^(CMEX|LMMathExtension|MTEX|txex|pxex|ntxexa?|esint)", re.I)
_ROMAN_MATH = re.compile(r"^(CM(R|BX|SS|TI|SL|TT)|LMRoman|SFRM)\d", re.I)

_OPERATORS = set("=+-−–*/<>≤≥≈≠∼~≃≅≡∈∉⊂⊆⊃⊇∩∪×·∘→←↔⇒⇔≪≫∝±∓:|\\∖")
_MATHY_PUNCT = set("()[]{}|/,.:;'′″^_!%")
_FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "exp", "log", "ln", "lg", "max", "min", "arg",
              "argmax", "argmin", "lim", "sup", "inf", "det", "dim", "ker", "deg", "gcd", "mod", "tr",
              "diag", "rank", "sgn", "sign", "Pr", "Var", "Cov", "softmax", "sigmoid", "tanh", "sinh", "cosh"}
_CITATION = re.compile(r"^[\d,\-–—\s\[\]()*†‡§¶#]+$|^[a-h]$")
_OPEN, _CLOSE = "([{", ")]}"


def font_roles(lines: list[Line]) -> set[str]:
    """Roman fonts that carry running text (so they are *not* math fonts here)."""
    counts: dict[str, int] = {}
    for line in lines:
        for glyph in line.glyphs:
            counts[glyph.font] = counts.get(glyph.font, 0) + 1
    total = sum(counts.values()) or 1
    return {font for font, count in counts.items() if count >= 0.03 * total}


def _classify(glyph: Glyph, text_fonts: set[str], can_render) -> str:
    char = glyph.c
    if char == " ":
        return " "
    code = ord(char)
    if char == "�" or 0xE000 <= code <= 0xF8FF or 0x1D400 <= code <= 0x1D7FF or 0x2100 <= code <= 0x214F:
        return "H"
    font = glyph.font
    if _MATH_EXT.match(font):
        return "H"
    letter = char.isalpha()
    if _MATH_ITALIC.match(font):
        return "H" if letter else ("D" if char.isdigit() else "S")
    if _MATH_SYMBOL.match(font):
        if letter and code < 0x250:
            return "H"                       # calligraphic capitals and friends
        return "S" if can_render(char) else "H"
    if _ROMAN_MATH.match(font) and font not in text_fonts:
        return "M" if letter else ("D" if char.isdigit() else "S")
    if 0x370 <= code <= 0x3FF or 0x1F00 <= code <= 0x1FFF:
        return "S" if can_render(char) else "H"
    if unicodedata.category(char) == "Sm" or char in _OPERATORS:
        return "S" if can_render(char) else "H"
    if char.isdigit():
        return "D"
    if char in _MATHY_PUNCT:
        return "P"
    if not can_render(char):
        return "H"
    return "L" if letter else "O"


class _Token:
    __slots__ = ("start", "end", "cls", "glyphs")

    def __init__(self, start: int, end: int, cls: list[str], glyphs: list[Glyph]):
        self.start, self.end, self.cls, self.glyphs = start, end, cls, glyphs

    @property
    def text(self) -> str:
        return "".join(g.c for g in self.glyphs)


def _tokens(line: Line, cls: list[str]) -> list[_Token]:
    tokens, start = [], None
    for index, kind in enumerate(cls + [" "]):
        if kind == " ":
            if start is not None:
                tokens.append(_Token(start, index, cls[start:index], line.glyphs[start:index]))
                start = None
        elif start is None:
            start = index
    return tokens


def _raw_span(token: _Token) -> tuple[int, int] | None:
    """Glyph span (relative to the token) that is formula material, untrimmed."""
    cls, glyphs = token.cls, token.glyphs
    n = len(cls)
    scripts = [i for i, g in enumerate(glyphs) if g.script]
    if "H" not in cls:
        if not scripts:
            return None
        script_text = "".join(glyphs[i].c for i in scripts)
        base = "".join(g.c for g in glyphs if not g.script)
        letters = sum(c.isalpha() for c in base)
        superscript = all(glyphs[i].script > 0 for i in scripts)
        if superscript and _CITATION.match(script_text) and (letters >= 4 or not base or not base[-1].isalnum()):
            return None                       # a citation / footnote mark
        if letters > 6:
            # A script on a long word ("kg/m2-adjusted"): keep only its neighbourhood.
            return (max(0, scripts[0] - 1), scripts[-1] + 1)
        return (0, n)
    mathy = [k in "HSDPM" or bool(g.script) for k, g in zip(cls, glyphs)]
    # Absorb upright function names glued to math: "exp(", "max_i".
    i = 0
    while i < n:
        if mathy[i]:
            i += 1
            continue
        j = i
        while j < n and not mathy[j]:
            j += 1
        word = "".join(g.c for g in glyphs[i:j])
        if word in _FUNCTIONS and ((i > 0 and mathy[i - 1]) or (j < n and mathy[j])):
            for k in range(i, j):
                mathy[k] = True
        i = j
    hard = [i for i, k in enumerate(cls) if k == "H"]
    start, end = hard[0], hard[-1] + 1
    while start > 0 and mathy[start - 1]:
        start -= 1
    while end < n and mathy[end]:
        end += 1
    if not all(mathy[start:end]):
        # Text letters wedged between math ("k-means"): keep the first run.
        end = start + mathy[start:].index(False)
        if "H" not in cls[start:end]:
            return None
    return (start, end)


def _trim(glyphs: list[Glyph], start: int, end: int) -> tuple[int, int] | None:
    """Give sentence punctuation and unbalanced outer brackets back to the text."""
    changed = True
    while changed and end > start:
        changed = False
        while end > start and glyphs[end - 1].c == " ":
            end -= 1
        while end > start and glyphs[start].c == " ":
            start += 1
        if end > start and glyphs[end - 1].c in ",.;:!?" and not glyphs[end - 1].script:
            end -= 1
            changed = True
            continue
        opened, stray_close = [], []
        for i in range(start, end):
            char = glyphs[i].c
            if char in _OPEN:
                opened.append(i)
            elif char in _CLOSE:
                if opened:
                    opened.pop()
                else:
                    stray_close.append(i)
        if opened and opened[0] == start:
            start += 1
            changed = True
        elif stray_close and stray_close[-1] == end - 1:
            end -= 1
            changed = True
    return (start, end) if end > start else None


def _depth_change(glyphs) -> int:
    return sum((g.c in _OPEN) - (g.c in _CLOSE) for g in glyphs)


def mark_line(line: Line, text_fonts: set[str], can_render) -> list[tuple[int, int]]:
    """Return formula spans as glyph index ranges and set ``glyph.math``."""
    glyphs = line.glyphs
    cls = [_classify(g, text_fonts, can_render) for g in glyphs]
    for glyph, kind in zip(glyphs, cls):
        glyph.math = HARD if kind == "H" else SOFT if kind in "SM" else TEXT
    tokens = _tokens(line, cls)
    raw = [_raw_span(token) for token in tokens]
    n = len(tokens)

    def mathy_all(k: int) -> bool:
        return all(c in "HSDPM" or g.script for c, g in zip(tokens[k].cls, tokens[k].glyphs))

    def operator(k: int) -> bool:
        return raw[k] is None and all(c in _OPERATORS for c in tokens[k].text)

    def operand(k: int) -> bool:
        if raw[k] is not None:
            return raw[k][0] == 0
        if not mathy_all(k) or operator(k):
            return False
        return any(c in "DHM" or (c == "S" and g.c not in _OPERATORS)
                   for c, g in zip(tokens[k].cls, tokens[k].glyphs))

    def starts_with_operator(k: int) -> bool:
        return len(tokens[k].cls) > 1 and tokens[k].text[0] in _OPERATORS and mathy_all(k)

    def reaches_end(k: int) -> bool:
        return raw[k] is None or raw[k][1] == len(tokens[k].cls)

    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if raw[i] is None:
            i += 1
            continue
        start = tokens[i].start + raw[i][0]
        end = tokens[i].start + raw[i][1]
        depth = _depth_change(glyphs[start:end])
        j = i
        while j + 1 < n and reaches_end(j):
            tail = tokens[j].text[-1:]
            if depth > 0:
                ok = operand(j + 1) or operator(j + 1) or mathy_all(j + 1)
            elif tail in ",;.":
                ok = False                    # sentence punctuation ends the formula
            elif operator(j + 1):
                ok = j + 2 < n and operand(j + 2)
            elif starts_with_operator(j + 1):
                ok = True                     # "∈V", "≥2": TeX sets no space after the relation
            else:
                ok = operand(j + 1) and (operator(j) or tail in _OPERATORS)
            if not ok:
                break
            j += 1
            end = tokens[j].start + (raw[j][1] if raw[j] is not None else len(tokens[j].cls))
            depth += _depth_change(tokens[j].glyphs)
        # "0 < x": a number and an operator in front of the seed belong to it.
        if raw[i][0] == 0 and i >= 2 and operator(i - 1) and raw[i - 2] is None and operand(i - 2):
            start = tokens[i - 2].start
        if spans:
            start = max(start, spans[-1][1])
        trimmed = _trim(glyphs, start, end) if end > start else None
        if trimmed:
            spans.append(trimmed)
        i = j + 1
    for start, end in spans:
        for glyph in glyphs[start:end]:
            if glyph.math == TEXT:
                glyph.math = SOFT
    return spans


def citation_marks(line: Line, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Runs of script glyphs outside formulas (citation marks, footnote signs)."""
    covered = set()
    for start, end in spans:
        covered.update(range(start, end))
    runs, start = [], None
    for index, glyph in enumerate(line.glyphs + [None]):
        scripted = glyph is not None and glyph.script != 0 and index not in covered
        if scripted and start is None:
            start = index
        elif not scripted and start is not None:
            if glyph is not None and glyph.c == " " and index + 1 < len(line.glyphs) \
                    and line.glyphs[index + 1].script and index + 1 not in covered:
                continue                      # a space inside "1, 2"
            runs.append((start, index))
            start = None
    return runs
