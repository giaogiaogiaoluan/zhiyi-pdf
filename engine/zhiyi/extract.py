"""Glyph extraction: PyMuPDF rawdict → visual lines.

MuPDF frequently reports one visual line as several pieces (a superscript, a
font change, a wide justified gap). Pieces are stitched back together here so
later stages reason about real lines.
"""

from __future__ import annotations

import statistics
import unicodedata

import pymupdf

from .model import Glyph, Line

_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP
_SPACE_LIKE = {" ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
               " ": " ", " ": " ", " ": " ", "\t": " "}
ASCENT, DESCENT = 0.76, 0.22      # nominal ink extent relative to font size


def _clean(char: str) -> str:
    char = _SPACE_LIKE.get(char, char)
    if char in ("​", "‌", "‍", "﻿"):
        return ""
    if char != " " and unicodedata.category(char) == "Cc":
        return ""
    return char


def _fragment(raw_line) -> list[Glyph]:
    glyphs: list[Glyph] = []
    for span in raw_line["spans"]:
        size = float(span["size"])
        if size <= 0:
            continue
        font, flags, color = span["font"], int(span["flags"]), int(span.get("color", 0))
        if "+" in font[:8]:
            font = font.split("+", 1)[1]
        for char in span["chars"]:
            text = _clean(char["c"])
            if not text:
                continue
            x0, y0, x1, y1 = char["bbox"]
            if x1 - x0 <= 0 and text != " ":
                x1 = x0 + 0.01
            glyphs.append(Glyph(text, x0, y0, x1, y1, float(char["origin"][1]), size, font, flags, color))
    return glyphs


def _metrics(glyphs: list[Glyph]) -> tuple[float, float]:
    """Dominant font size and the baseline of the glyphs set in it."""
    inked = [g for g in glyphs if g.c != " "] or glyphs
    weight: dict[float, float] = {}
    for g in inked:
        key = round(g.size, 1)
        weight[key] = weight.get(key, 0.0) + (g.x1 - g.x0)
    size = max(weight, key=weight.get)
    baseline = statistics.median(g.oy for g in inked if abs(g.size - size) <= 0.06 * size + 0.06)
    return size, baseline


def _stitch(fragments: list[list[Glyph]]) -> list[Glyph]:
    """Concatenate fragments left to right, keeping each one's glyph order.

    Glyph order inside a fragment is MuPDF's logical order and must not be
    re-sorted: the pieces of an expanded ligature share one x position.
    """
    fragments = sorted(fragments, key=lambda f: min(g.x0 for g in f))
    glyphs: list[Glyph] = []
    for fragment in fragments:
        first = fragment[0]
        if glyphs and first.x0 < glyphs[-1].x1 - 0.5 * first.size:
            # A script reported separately but sitting inside the line.
            at = next((k for k, g in enumerate(glyphs) if g.x0 > first.x0), len(glyphs))
            glyphs[at:at] = fragment
            continue
        if glyphs and glyphs[-1].c != " " and first.c != " ":
            size = max(glyphs[-1].size, first.size)
            if first.x0 - glyphs[-1].x1 > 0.18 * size:
                last = glyphs[-1]
                glyphs.append(Glyph(" ", last.x1, last.y0, first.x0, last.y1, last.oy, last.size,
                                    last.font, last.flags, last.color))
        glyphs.extend(fragment)
    return glyphs


def _build(page: int, fragments: list[list[Glyph]]) -> Line | None:
    glyphs = _stitch(fragments)
    while glyphs and glyphs[0].c == " ":
        glyphs.pop(0)
    while glyphs and glyphs[-1].c == " ":
        glyphs.pop()
    if not glyphs:
        return None
    # Drop duplicated spaces produced when fragments were stitched.
    tidy: list[Glyph] = []
    for g in glyphs:
        if g.c == " " and tidy and tidy[-1].c == " ":
            continue
        tidy.append(g)
    size, baseline = _metrics(tidy)
    for g in tidy:
        g.script = 0
        if g.c != " " and g.size < 0.86 * size:
            if g.oy < baseline - 0.12 * size:
                g.script = 1
            elif g.oy > baseline + 0.05 * size:
                g.script = -1
    y0 = min(g.oy - ASCENT * g.size for g in tidy)
    y1 = max(g.oy + DESCENT * g.size for g in tidy)
    x0 = min(g.x0 for g in tidy)
    x1 = max(g.x1 for g in tidy)
    return Line(page, tidy, x0, y0, x1, y1, baseline, size)


WIDE_GAP = 3.2                    # em; only bridged with corroboration


def _same_visual_line(a, b, wide_ok) -> bool:
    """a is left of b; both are [glyphs, size, baseline, x0, x1]."""
    size = max(a[1], b[1])
    gap = b[3] - a[4]
    if gap < -0.35 * size or gap > WIDE_GAP * size:
        return False
    ratio = min(a[1], b[1]) / size
    shift = abs(a[2] - b[2])
    if gap > 0.75 * size:
        # A wide gap is a justified sentence space only if something says the
        # two pieces share a text area; otherwise it is a gutter or a table.
        return ratio > 0.9 and shift <= 0.2 * size and wide_ok(a, b)
    if ratio > 0.9:
        return shift <= 0.25 * size
    # A smaller piece is a script: it must sit within the big line's extent.
    return shift <= 0.6 * size


def _wide_gap_test(pieces, regions):
    """Build the predicate deciding whether a wide same-baseline gap is bridged."""
    text_like = {"title", "plain text", "figure_caption", "table_caption", "table_footnote", "abandon"}

    def owner(piece) -> int:
        cx, cy = (piece[3] + piece[4]) / 2, piece[2] - 0.3 * piece[1]
        best, best_area = -1, None
        for index, region in enumerate(regions):
            x0, y0, x1, y1 = region.box
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                area = (x1 - x0) * (y1 - y0)
                if best_area is None or area < best_area:
                    best, best_area = index, area
        return best

    owners: dict[int, int] = {}

    def wide_ok(a, b) -> bool:
        ia = owners.setdefault(id(a), owner(a))
        ib = owners.setdefault(id(b), owner(b))
        if ia >= 0 and ia == ib:
            return regions[ia].label in text_like
        if ia >= 0 and ib >= 0:
            return False                      # two different areas: a gutter
        if any(i >= 0 and regions[i].label not in text_like for i in (ia, ib)):
            return False
        # A piece the model missed (a run-in label, say): bridge only when a
        # neighbouring line spans the gap.
        size, gap0, gap1 = max(a[1], b[1]), a[4], b[3]
        for other in pieces:
            if other is a or other is b:
                continue
            if 0.5 * size < abs(other[2] - a[2]) < 2.2 * size and other[3] < gap0 - 1 and other[4] > gap1 + 1:
                return True
        return False

    return wide_ok


def extract(page: pymupdf.Page, number: int, regions=()) -> tuple[list[Line], list[Line]]:
    """Return (horizontal lines, other text that must stay untouched)."""
    raw = page.get_text("rawdict", flags=_FLAGS)
    pieces = []
    rotated: list[Line] = []
    for block in raw["blocks"]:
        if block.get("type", 0) != 0:
            continue
        for raw_line in block["lines"]:
            glyphs = _fragment(raw_line)
            if not any(g.c != " " for g in glyphs):
                continue
            direction = raw_line.get("dir", (1.0, 0.0))
            if abs(direction[0] - 1.0) > 0.02 or abs(direction[1]) > 0.02 or raw_line.get("wmode", 0):
                x0, y0, x1, y1 = raw_line["bbox"]
                rotated.append(Line(number, glyphs, x0, y0, x1, y1, y1, glyphs[0].size, label="rotated"))
                continue
            size, baseline = _metrics(glyphs)
            inked = [g for g in glyphs if g.c != " "]
            pieces.append([glyphs, size, baseline, min(g.x0 for g in inked), max(g.x1 for g in inked)])

    # Union pieces that belong to one visual line.
    pieces.sort(key=lambda p: p[3])
    parent = list(range(len(pieces)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    wide_ok = _wide_gap_test(pieces, regions)
    for i, a in enumerate(pieces):
        for j in range(i + 1, len(pieces)):
            b = pieces[j]
            if b[3] - a[4] > WIDE_GAP * 40:      # sorted by x0: nothing further can touch a
                break
            if _same_visual_line(a, b, wide_ok):
                parent[find(j)] = find(i)

    groups: dict[int, list[list[Glyph]]] = {}
    for i, piece in enumerate(pieces):
        groups.setdefault(find(i), []).append(piece[0])
    lines = [line for fragments in groups.values() if (line := _build(number, fragments))]
    lines.sort(key=lambda l: (l.baseline, l.x0))
    return lines, rotated
