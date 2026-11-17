"""Lines → paragraph fragments (blocks).

The layout model only says *what kind* of area a line sits in. Paragraph
boundaries come from typography: line pitch, first-line indents, hanging
indents and whether the previous line had room for the next word.
"""

from __future__ import annotations

import re
import statistics

from .model import Block, Line, PageInfo, Region

KEEP_LABELS = {"figure", "isolate_formula", "formula_caption", "abandon", "rotated"}
FAMILY = {"plain text": "text", "": "text", "title": "title", "figure_caption": "figcap",
          "table_caption": "tabcap", "table_footnote": "tabnote", "table": "table"}
MARGIN = 0.085                     # header/footer zone as a fraction of page height

_LIST_MARK = re.compile(r"^\s*(?:[•●▪■◦‣·∙*–—-]\s|\(?\d{1,2}[.)]\s|\(?[a-zA-Z][.)]\s|\([ivxIVX]{1,5}\)\s|\[\d{1,3}\]\s)")
_TERMINAL = re.compile(r"[.!?:;。！？：；][\"'”’)\]]*(?:\s*\[[\d,\s–-]+\])?\s*$")
_WORD = re.compile(r"[A-Za-z]{2,}")


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _inter(a, b) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _bold_share(line: Line) -> float:
    inked = [g for g in line.glyphs if g.c != " "]
    return sum(g.bold for g in inked) / max(1, len(inked))


def _mono_share(line: Line) -> float:
    inked = [g for g in line.glyphs if g.c != " "]
    return sum(bool(g.flags & 8) for g in inked) / max(1, len(inked))


def prose_like(text: str) -> bool:
    words = _WORD.findall(text)
    letters = sum(c.isalpha() for c in text)
    return len(words) >= 2 and letters >= 0.45 * len(text.replace(" ", ""))


# --------------------------------------------------------------------------- #
# labelling
# --------------------------------------------------------------------------- #
def label_lines(page: PageInfo) -> None:
    height = page.height
    # Headers/footers live outside the vertical span of the running text.
    text_boxes = [r.box for r in page.regions if r.label == "plain text"]
    text_top = min((b[1] for b in text_boxes), default=MARGIN * height)
    text_bottom = max((b[3] for b in text_boxes), default=(1 - MARGIN) * height)
    for line in page.lines:
        box = (line.x0, line.y0, line.x1, line.y1)
        area = _area(box) or 1.0
        best, best_key = -1, None
        for index, region in enumerate(page.regions):
            cover = _inter(box, region.box) / area
            if cover < 0.5:
                continue
            key = (-_area(region.box),)          # the smallest covering region wins
            if best_key is None or key > best_key:
                best, best_key = index, key
        line.region = best
        line.label = page.regions[best].label if best >= 0 else ""

        outside = line.y1 < text_top + 0.3 * line.size or line.y0 > text_bottom - 0.3 * line.size
        in_margin = outside and (line.y1 < 1.6 * MARGIN * height or line.y0 > (1 - 1.6 * MARGIN) * height)
        if line.label == "abandon" and not in_margin:
            # Mid-page "abandon" is nearly always a heading or stray prose.
            line.label = "title" if _bold_share(line) > 0.6 and len(line.text) < 90 else "plain text"
        elif line.label == "" and in_margin and len(line.text.strip()) < 60:
            line.label = "abandon"
        if _mono_share(line) > 0.8 and line.label in ("plain text", ""):
            line.label = "code"


_SECTION = re.compile(r"^\s*(?:[IVXLC]+\.|[A-Z]\.|\d+(?:\.\d+)*\.?)\s+[A-Za-z]")


def fix_running_heads(pages: list[PageInfo]) -> None:
    """Rescue headings the model mistook for page headers.

    A real running head repeats from page to page; a section heading that
    happens to be the first thing on a page does not.
    """
    seen: dict[str, set[int]] = {}
    for page in pages:
        for line in page.lines:
            if line.label == "abandon":
                seen.setdefault(re.sub(r"\d+", "#", line.text.strip().lower()), set()).add(page.number)
    for page in pages:
        tops = [r.box[1] for r in page.regions if r.label == "plain text"]
        if not tops:
            continue
        text_top = min(tops)
        for line in page.lines:
            if line.label != "abandon" or line.y1 > text_top + 0.3 * line.size:
                continue
            text = line.text.strip()
            key = re.sub(r"\d+", "#", text.lower())
            if len(seen.get(key, ())) > 1 or sum(c.isalpha() for c in text) < 4:
                continue
            near = line.y0 > text_top - 4.5 * line.size
            if near and (_bold_share(line) > 0.6 or _SECTION.match(text)):
                line.label = "title"


# --------------------------------------------------------------------------- #
# stacking: lines that sit under each other in one column
# --------------------------------------------------------------------------- #
class _Stack:
    __slots__ = ("lines", "x0", "x1", "family", "region", "measure")

    def __init__(self, line: Line, family: str):
        self.lines = [line]
        self.x0, self.x1 = line.x0, line.x1
        self.family = family
        self.region = line.region
        self.measure = 0.0             # right edge lines are set against; 0 = own x1

    def ragged(self) -> bool:
        """True when hardly any line reaches the stack's right edge."""
        if len(self.lines) < 2:
            return False
        size = self.lines[0].size
        reaching = sum(l.x1 >= self.x1 - 0.6 * size for l in self.lines)
        return reaching < 0.4 * len(self.lines) or reaching < 2

    def pitch(self) -> float:
        if len(self.lines) < 3:
            return 0.0
        gaps = [b.baseline - a.baseline for a, b in zip(self.lines, self.lines[1:])]
        return statistics.median(gaps)

    def accepts(self, line: Line, family: str) -> float | None:
        if family != self.family:
            return None
        if family != "text" and line.region != self.region:
            return None
        last = self.lines[-1]
        size = max(last.size, line.size)
        if abs(last.size - line.size) > 0.14 * size:
            return None
        drop = line.baseline - last.baseline
        pitch = self.pitch()
        limit = 1.45 * pitch if pitch else 2.0 * size
        if family == "table":
            limit = min(limit, 1.6 * size)
        if not 0.55 * size < drop <= limit:
            return None
        overlap = min(self.lines[-1].x1, line.x1) - max(self.lines[-1].x0, line.x0)
        if overlap < 0.5 * min(last.width, line.width):
            return None
        slack = 1.6 * size
        inside = line.x0 >= self.x0 - slack and line.x1 <= self.x1 + slack
        encloses = len(self.lines) == 1 and self.x0 >= line.x0 - slack and self.x1 <= line.x1 + slack
        if not (inside or encloses):
            return None
        return drop

    def add(self, line: Line) -> None:
        self.lines.append(line)
        self.x0, self.x1 = min(self.x0, line.x0), max(self.x1, line.x1)


def _stacks(lines: list[Line]) -> list[_Stack]:
    stacks: list[_Stack] = []
    for line in sorted(lines, key=lambda l: (l.baseline, l.x0)):
        family = FAMILY.get(line.label)
        if family is None:
            continue
        best, best_drop = None, None
        for stack in stacks:
            drop = stack.accepts(line, family)
            if drop is not None and (best_drop is None or drop < best_drop):
                best, best_drop = stack, drop
        if best is None:
            stacks.append(_Stack(line, family))
        else:
            best.add(line)
    return stacks


# --------------------------------------------------------------------------- #
# paragraph breaks inside a stack
# --------------------------------------------------------------------------- #
def _first_word_width(line: Line) -> float:
    for glyph in line.glyphs:
        if glyph.c == " ":
            return glyph.x0 - line.x0
    return line.width


def _centered(lines: list[Line]) -> bool:
    if len(lines) < 2:
        return False
    centers = [(l.x0 + l.x1) / 2 for l in lines]
    lefts = [l.x0 for l in lines]
    rights = [l.x1 for l in lines]
    size = lines[0].size
    # Both edges must be ragged: a first-line indent in justified text is not centring.
    return (max(centers) - min(centers) < 0.8 * size and max(lefts) - min(lefts) > 1.2 * size
            and max(rights) - min(rights) > 1.2 * size)


def _split(stack: _Stack) -> list[list[Line]]:
    lines = stack.lines
    if len(lines) == 1:
        return [lines]
    pitch = stack.pitch() or min(b.baseline - a.baseline for a, b in zip(lines, lines[1:]))
    size = statistics.median(l.size for l in lines)
    text_rules = stack.family == "text" and not _centered(lines)

    # Is the stack set with first-line indents, or with hanging indents?
    left = min(l.x0 for l in lines)
    offsets = [l.x0 - left for l in lines]
    at_base = sum(o < 0.45 * size for o in offsets)
    indented = sum(0.75 * size <= o <= 4.5 * size for o in offsets)
    hanging_mode = indented > at_base

    right_edge = stack.measure or stack.x1
    paragraphs, current = [], [lines[0]]
    for prev, line, offset, prev_offset in zip(lines, lines[1:], offsets[1:], offsets):
        drop = line.baseline - prev.baseline
        brk = drop > 1.28 * pitch + 0.5
        if not brk and abs(line.size - prev.size) > 0.07 * size:
            brk = True
        if not brk and text_rules and line.glyphs[0].script > 0:
            brk = True                                       # a footnote/affiliation mark
        if not brk and text_rules:
            room = right_edge - prev.x1
            is_indented = 0.75 * size <= offset <= 4.5 * size
            if room > _first_word_width(line) + 0.35 * size:
                brk = True                                   # previous line ended early
            elif hanging_mode:
                brk = offset < 0.45 * size and len(current) >= 1 and prev_offset >= 0.45 * size
                if not brk and offset < 0.45 * size and prev_offset < 0.45 * size:
                    brk = bool(_LIST_MARK.match(line.text))
            elif is_indented and prev_offset < 0.45 * size:
                brk = True                                   # first-line indent
            elif is_indented and len(current) == 1:
                brk = True                                   # two indented one-liners
            elif _LIST_MARK.match(line.text) and _TERMINAL.search(prev.text):
                brk = True
            elif (prev.region != line.region and prev.region >= 0 and line.region >= 0
                  and _TERMINAL.search(prev.text)):
                brk = True                                   # the model's split, corroborated
        if brk:
            paragraphs.append(current)
            current = [line]
        else:
            current.append(line)
    paragraphs.append(current)
    return paragraphs


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
def _table_cells(stack: _Stack) -> list[list[Line]]:
    """Group a table column stack into cells without gluing separate rows."""
    lines = stack.lines
    if len(lines) == 1:
        return [lines]
    drops = [b.baseline - a.baseline for a, b in zip(lines, lines[1:])]
    tight = min(drops)
    bimodal = max(drops) >= 1.3 * tight
    cells, current = [], [lines[0]]
    for prev, line, drop in zip(lines, lines[1:], drops):
        prev_text, text = prev.text.rstrip(), line.text.lstrip()
        continues = prev_text.endswith(("-", ",", "‐")) or (text[:1].islower() and not _LIST_MARK.match(text))
        same = (bimodal and drop <= 1.15 * tight) or (not bimodal and continues)
        if same:
            current.append(line)
        else:
            cells.append(current)
            current = [line]
    cells.append(current)
    return cells


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #
def _dominant(values):
    counts: dict = {}
    for value, weight in values:
        counts[value] = counts.get(value, 0) + weight
    return max(counts, key=counts.get)


def _make_block(page: PageInfo, lines: list[Line], stack: _Stack) -> Block:
    size = _dominant((round(l.size, 1), l.width) for l in lines)
    inked = [g for l in lines for g in l.glyphs if g.c != " " and not g.script]
    bold = sum(g.bold for g in inked) > 0.6 * len(inked)
    italic = sum(g.italic for g in inked) > 0.6 * len(inked)
    serif = sum(g.serif for g in inked) >= 0.5 * len(inked)
    color = _dominant((g.color, 1) for g in inked) if inked else 0
    block = Block(page=page.number, lines=lines, label=lines[0].label, size=size,
                  bold=bold, italic=italic, serif=serif, color=color)
    block.x0, block.x1 = min(l.x0 for l in lines), max(l.x1 for l in lines)
    block.y0, block.y1 = lines[0].y0, lines[-1].y1
    if len(lines) > 1:
        block.pitch = statistics.median(b.baseline - a.baseline for a, b in zip(lines, lines[1:]))

    wide_stack = len(stack.lines) >= 3 and stack.x1 - stack.x0 >= block.x1 - block.x0
    block.frame_x0, block.frame_x1 = (stack.x0, stack.x1) if wide_stack else (block.x0, block.x1)

    # alignment and indents
    first, rest = lines[0], lines[1:]
    body_left = min(l.x0 for l in rest) if rest else block.frame_x0
    if _centered(lines):
        block.align = "center"
    elif rest:
        rights = [l.x1 for l in lines[:-1]]
        ragged = max(rights) - min(rights) if len(rights) > 1 else block.frame_x1 - rights[0]
        block.align = "justify" if ragged < 0.8 * size else "left"
        delta = first.x0 - body_left
        if 0.6 * size <= delta <= 4.5 * size:
            block.indent = delta
        elif -8 * size <= delta <= -0.6 * size:
            block.hanging = -delta
    else:
        delta = first.x0 - block.frame_x0
        if wide_stack and 0.6 * size <= delta <= 4.5 * size:
            block.indent = delta
        frame_center = (block.frame_x0 + block.frame_x1) / 2
        if abs((first.x0 + first.x1) / 2 - frame_center) < 0.8 * size and first.x0 - block.frame_x0 > 2 * size:
            block.align = "center"
    return block


def build_blocks(page: PageInfo) -> None:
    """Fill ``page.blocks`` (translatable) and ``page.kept`` (left untouched).

    ``label_lines`` (and ``fix_running_heads``) must have run first.
    """
    page.kept = [l for l in page.lines if FAMILY.get(l.label) is None]
    blocks: list[Block] = []
    stacks = _stacks(page.lines)
    # A ragged stack of short lines (an address, an author list) is measured
    # against the column it sits in, so each short line ends its own paragraph.
    columns = [s for s in stacks if s.family == "text" and len(s.lines) >= 3 and not s.ragged()]
    for stack in stacks:
        if stack.family != "text" or not stack.ragged():
            continue
        size = stack.lines[0].size
        wider = [c.x1 for c in columns if c.x0 - 2 <= stack.x0 and c.x1 >= stack.x1 + 2 * size]
        if wider:
            stack.measure = min(wider)
    for stack in stacks:
        groups = _table_cells(stack) if stack.family == "table" else _split(stack)
        for lines in groups:
            block = _make_block(page, lines, stack)
            text = block.text
            if stack.family == "table":
                block.role, block.table = "table", stack.region
                block.frame_x0, block.frame_x1 = block.x0, block.x1
                block.align, block.indent, block.hanging = "left", 0.0, 0.0
                # Numbers and abbreviations ("iAUC", "ReAct", "T2DM") stay as they are;
                # a cell is translated only if it holds an ordinary word.
                keep = (not re.search(r"\b[A-Z]?[a-z]{3,}\b", text)
                        or sum(c.isalpha() for c in text) < 0.5 * len(text.replace(" ", "")))
            elif stack.family == "title":
                block.role, keep = "heading", len(text.strip()) < 2
            elif stack.family in ("figcap", "tabcap", "tabnote"):
                block.role, keep = "caption", not _WORD.search(text)
            else:
                block.role = "body"
                unowned = all(l.region < 0 for l in lines)
                if unowned:
                    keep = not prose_like(text) or len(_WORD.findall(text)) < 4
                else:   # inside a text area even "and" / "where" between equations is prose
                    letters = sum(c.isalpha() for c in text)
                    keep = not _WORD.search(text) or letters < 0.45 * len(text.replace(" ", ""))
            if keep:
                page.kept.extend(lines)
            else:
                blocks.append(block)
    page.blocks = blocks


def body_size(pages: list[PageInfo]) -> float:
    """The document's body font size: the size carrying the most body text."""
    weight: dict[float, float] = {}
    for page in pages:
        for block in page.blocks:
            if block.role == "body" and block.label == "plain text":
                chars = sum(len(l.glyphs) for l in block.lines)
                weight[block.size] = weight.get(block.size, 0) + chars
    return max(weight, key=weight.get) if weight else 10.0


def assign_roles(pages: list[PageInfo]) -> float:
    base = body_size(pages)
    titles = [b for p in pages[:2] for b in p.blocks if b.role == "heading"]
    top = max((b.size for b in titles), default=0)
    for page in pages:
        for block in page.blocks:
            text = block.text.strip()
            if block.role == "heading":
                if page.number <= 1 and block.size == top and top >= 1.25 * base:
                    block.role = "title"
            elif block.role == "body":
                one_line = len(block.lines) == 1
                if (one_line and block.bold and block.size >= 0.98 * base and len(text) < 90
                        and not _TERMINAL.search(text)):
                    block.role = "heading"
                elif block.size <= 0.9 * base and block.y0 > 0.6 * page.height:
                    block.role = "footnote"
                elif _LIST_MARK.match(text) and block.hanging:
                    block.role = "list"
    return base
