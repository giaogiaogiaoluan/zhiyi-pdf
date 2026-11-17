"""Source markup for the translator, and parsing of what comes back.

Markup is plain text with two kinds of markers:

* ``{v1}``  an inline formula, re-used verbatim from the source page;
* ``{eq1}`` the position of a display equation inside a sentence;
* ``<b>…</b>``, ``<i>…</i>``, ``<sup>…</sup>``, ``<sub>…</sub>``, ``<a>…</a>``
  spans whose style differs from the paragraph's base style.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from . import formula
from .extract import ASCENT, DESCENT
from .model import Block, Glyph, Inline, Style, Unit

MARKER = re.compile(r"</?(?:b|i|sup|sub|a\d?)>|\{v\d+\}|\{eq\d+\}")
_TAG = re.compile(r"<(/?)(b|i|sup|sub|a\d?)>")
_HYPHENS = ("-", "‐", "­")


@dataclass(slots=True)
class Run:
    """A piece of translated text with resolved style, or an inline object."""

    text: str = ""
    bold: bool = False
    italic: bool = False
    script: int = 0
    color: int = 0
    inline: Inline | None = None
    brk: bool = False            # hard break between chained boxes ({eqN})


def _dark(color: int) -> bool:
    return ((color >> 16) & 255) < 0x48 and ((color >> 8) & 255) < 0x48 and (color & 255) < 0x48


def _inline_box(glyphs: list[Glyph]):
    x0 = min(g.x0 for g in glyphs) - 0.4
    x1 = max(g.x1 for g in glyphs) + 0.4
    tops, bottoms = [], []
    for g in glyphs:
        if g.c == " ":
            continue
        if formula._MATH_EXT.match(g.font):
            tops.append(g.y0)
            bottoms.append(g.y1)
        else:
            tops.append(max(g.y0, g.oy - 1.05 * g.size))
            bottoms.append(min(g.y1, g.oy + 0.42 * g.size))
            tops.append(g.oy - ASCENT * g.size)
            bottoms.append(g.oy + DESCENT * g.size)
    return (x0, min(tops) - 0.3, x1, max(bottoms) + 0.3)


class _Builder:
    def __init__(self, unit: Unit, base: Block, text_fonts: set[str], can_render):
        self.unit, self.base = unit, base
        self.text_fonts, self.can_render = text_fonts, can_render
        self.parts: list[str] = []
        self.open: str | None = None
        self.colors: dict[int, str] = {}
        self.pending_join = ""       # "", " " or "glue"

    # -- style tags -------------------------------------------------------
    def _tag_for(self, glyph: Glyph, script: int) -> str | None:
        if script:
            return "sup" if script > 0 else "sub"
        if glyph.c.isspace() or not (glyph.c.isalnum() or glyph.c in "[]()"):
            return self.open                      # punctuation never opens/closes a span
        base = self.base
        if glyph.color != base.color and not (_dark(glyph.color) and _dark(base.color)):
            if glyph.color not in self.colors:
                name = "a" if not self.colors else f"a{len(self.colors) + 1}"
                self.colors[glyph.color] = name
                self.unit.styles[name] = Style(color=glyph.color)
            return self.colors[glyph.color]
        if glyph.bold and not base.bold:
            return "b"
        if glyph.italic and not base.italic and glyph.c.isalpha():
            return "i"
        return None

    def _switch(self, tag: str | None) -> None:
        if tag == self.open:
            return
        if self.open:
            # keep trailing spaces outside the closing tag
            trail = ""
            while self.parts and self.parts[-1] == " ":
                trail += self.parts.pop()
            self.parts.append(f"</{self.open}>" + trail)
        if tag:
            self.parts.append(f"<{tag}>")
            self.unit.styles.setdefault(tag, {"b": Style(bold=True), "i": Style(italic=True),
                                              "sup": Style(script=1), "sub": Style(script=-1)}.get(tag, Style()))
        self.open = tag

    # -- content ----------------------------------------------------------
    def _emit_join(self) -> None:
        if self.pending_join == " " and self.parts and self.parts[-1] != " ":
            self.parts.append(" ")
        self.pending_join = ""

    def add_line(self, line) -> None:
        glyphs = line.glyphs
        spans = formula.mark_line(line, self.text_fonts, self.can_render)
        marks = formula.citation_marks(line, spans)
        span_at = {start: end for start, end in spans}
        mark_of = {}
        for start, end in marks:
            for k in range(start, end):
                mark_of[k] = glyphs[start].script
        self._emit_join()
        index, count = 0, len(glyphs)
        while index < count:
            if index in span_at:
                end = span_at[index]
                self._switch(None)
                key = f"{{v{len(self.unit.inlines) + 1}}}"
                chunk = glyphs[index:end]
                self.unit.inlines[key] = Inline(key, line.page, _inline_box(chunk), line.baseline, line.size,
                                                "".join(g.c for g in chunk))
                self.parts.append(key)
                index = end
                continue
            glyph = glyphs[index]
            char = unicodedata.normalize("NFKC", glyph.c) if 0xFB00 <= ord(glyph.c) <= 0xFB06 else glyph.c
            if char == " ":
                if self.parts and self.parts[-1] != " ":
                    self.parts.append(" ")
            else:
                self._switch(self._tag_for(glyph, mark_of.get(index, 0)))
                self.parts.append(char)
            index += 1
        # decide how this line joins the next one
        tail = line.text.rstrip()
        if tail.endswith(_HYPHENS) and len(tail) >= 2 and tail[-2].isalpha() and tail[-2].islower():
            self.pending_join = "hyphen"
        elif tail and ord(tail[-1]) >= 0x2E80:
            self.pending_join = ""               # CJK lines join without a space
        else:
            self.pending_join = " "

    def join_hyphen(self, next_text: str) -> None:
        """Resolve a line-end hyphen once the next line's first character is known."""
        if self.pending_join != "hyphen":
            return
        self.pending_join = ""
        if next_text[:1].islower():
            # drop the hyphen (searching back past a closing tag)
            for k in range(len(self.parts) - 1, -1, -1):
                if self.parts[k] in _HYPHENS:
                    del self.parts[k]
                    break
                if not self.parts[k].startswith("</"):
                    break

    def add_break(self, key: str) -> None:
        self._switch(None)
        self.pending_join = ""
        if self.parts and self.parts[-1] != " ":
            self.parts.append(" ")
        self.parts += [key, " "]

    def finish(self) -> str:
        self._switch(None)
        text = "".join(self.parts)
        text = re.sub(r"<(b|i|a\d?)>\s*</\1>", "", text)
        return re.sub(r"[ \t]+", " ", text).strip()


def build_source(unit: Unit, text_fonts: set[str], can_render) -> None:
    """Fill ``unit.source`` / ``unit.inlines`` / ``unit.styles`` from its blocks."""
    builder = _Builder(unit, unit.blocks[0], text_fonts, can_render)
    breaks = 0
    for position, block in enumerate(unit.blocks):
        if position and block.link_kind == "formula":
            breaks += 1
            builder.add_break(f"{{eq{breaks}}}")
        for line in block.lines:
            builder.join_hyphen(line.text.lstrip())
            builder.add_line(line)
    unit.source = builder.finish()


def plain_text(markup: str) -> str:
    return MARKER.sub(" ", markup)


# --------------------------------------------------------------------------- #
# translated markup → runs
# --------------------------------------------------------------------------- #
def repair(unit: Unit, translated: str) -> tuple[str, list[str]]:
    """Make a translation safe to typeset; returns (markup, notes)."""
    notes: list[str] = []
    text = translated.strip()
    # tolerate full-width or spaced variants of our markers
    text = re.sub(r"[{｛]\s*(v|eq)\s*(\d+)\s*[}｝]", r"{\1\2}", text)
    text = re.sub(r"<\s*(/?)\s*(b|i|sup|sub|a\d?)\s*>", r"<\1\2>", text)

    wanted = list(unit.inlines) + [f"{{eq{k}}}" for k in range(1, sum(b.link_kind == "formula" for b in unit.blocks) + 1)]
    seen: set[str] = set()

    def keep_first(match: re.Match) -> str:
        key = match.group(0)
        if key not in wanted or key in seen:
            return ""
        seen.add(key)
        return key

    text = re.sub(r"\{(?:v|eq)\d+\}", keep_first, text)
    missing = [key for key in wanted if key not in seen]
    if missing:
        notes.append(f"译文丢失占位符 {' '.join(missing)}，已补在段末")
        text = text.rstrip() + " " + " ".join(k for k in missing if k.startswith("{v"))
        if any(k.startswith("{eq") for k in missing):
            notes.append("公式分隔位置丢失，改为按容量续排")
            text = re.sub(r"\{eq\d+\}", " ", text)

    # tags must be known and properly nested, otherwise drop them all
    stack, ok = [], True
    for match in _TAG.finditer(text):
        closing, name = match.group(1), match.group(2)
        if name not in unit.styles:
            ok = False
            break
        if not closing:
            stack.append(name)
        elif not stack or stack.pop() != name:
            ok = False
            break
    if not ok or stack:
        notes.append("样式标签不配对，已按纯文本排版")
        text = _TAG.sub("", text)
    return text, notes


def parse(unit: Unit, markup: str, base: Block) -> list[Run]:
    runs: list[Run] = []
    stack: list[str] = []

    def styled(text: str) -> Run:
        run = Run(text, bold=base.bold, italic=False, color=base.color)
        for name in stack:
            style = unit.styles.get(name)
            if style is None:
                continue
            run.bold = run.bold or style.bold
            run.italic = run.italic or style.italic
            run.script = style.script or run.script
            if style.color is not None:
                run.color = style.color
        return run

    position = 0
    for match in MARKER.finditer(markup):
        if match.start() > position:
            runs.append(styled(markup[position:match.start()]))
        token = match.group(0)
        position = match.end()
        if token.startswith("{v"):
            inline = unit.inlines.get(token)
            if inline is not None:
                runs.append(Run(inline=inline))
        elif token.startswith("{eq"):
            runs.append(Run(brk=True))
        elif token.startswith("</"):
            if stack and stack[-1] == token[2:-1]:
                stack.pop()
        else:
            stack.append(token[1:-1])
    if position < len(markup):
        runs.append(styled(markup[position:]))
    return runs
