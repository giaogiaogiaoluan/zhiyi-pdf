"""Link paragraph fragments into logical paragraphs.

Two kinds of link:

* ``flow``    – the paragraph runs on in the next column or page;
* ``formula`` – a display equation sits inside the sentence.

A linked chain is translated as one piece of text and typeset back through
its boxes in order, so the sentence is never translated in halves.
"""

from __future__ import annotations

import re

from .model import Block
from .order import Item

_ABBREV = re.compile(r"(?:\b(?:et al|e\.g|i\.e|vs|viz|cf|Fig|Figs|Eq|Eqs|Ref|Refs|No|Sec|Tab|approx|resp)\.|\b[A-Z]\.)\s*$")
_TRAILING_NOISE = re.compile(r"(?:\s*\[[\d,\s–-]+\]|[\"'”’)\]])+\s*$")
_LIST_START = re.compile(r"^\s*(?:[•●▪■◦‣·∙*–—]\s|\(?\d{1,2}[.)]\s|\([a-zA-Z]\)\s)")
_REFERENCES = re.compile(r"^\W*(?:\d+\.?\s*|[IVX]+\.?\s*)?(references?|bibliography|literature cited|works cited|参考文献)\W*$", re.I)


def ends_open(text: str) -> bool:
    """True when the text stops mid-sentence."""
    text = text.rstrip()
    if not text:
        return False
    if text.endswith(("-", "‐", "­")):
        return True
    if _ABBREV.search(text):
        return True
    core = _TRAILING_NOISE.sub("", text).rstrip()
    if not core:
        return False
    return core[-1] not in ".!?:;。！？：；"


def is_references_heading(text: str) -> bool:
    return bool(_REFERENCES.match(text.strip()))


def _last_line_full(block: Block) -> bool:
    last = block.lines[-1]
    if last.text.rstrip().endswith(("-", "‐")):
        return True
    width = block.frame_x1 - block.frame_x0
    return width > 0 and (last.x1 - block.frame_x0) >= 0.72 * width


def _compatible(a: Block, b: Block) -> bool:
    if a.role != b.role or a.role not in ("body", "list"):
        return False
    if abs(a.size - b.size) > 0.04 * a.size or a.bold != b.bold:
        return False
    wa, wb = a.frame_x1 - a.frame_x0, b.frame_x1 - b.frame_x0
    return min(wa, wb) >= 0.8 * max(wa, wb)


def _same_column(a: Block, b: Block) -> bool:
    overlap = min(a.frame_x1, b.frame_x1) - max(a.frame_x0, b.frame_x0)
    return overlap >= 0.7 * min(a.frame_x1 - a.frame_x0, b.frame_x1 - b.frame_x0)


def link(pages_items: list[list[Item]]) -> None:
    """Set ``block.next`` / ``block.prev`` and ``block.link`` kinds in place."""
    sequence: list[tuple[int, Item]] = [(p, item) for p, items in enumerate(pages_items) for item in items]
    previous: tuple[int, int, Block] | None = None      # (page, sequence index, block)
    for index, (page, item) in enumerate(sequence):
        block = item.block
        if block is None:
            continue
        if block.role in ("heading", "title"):
            previous = None
            continue
        if block.role not in ("body", "list"):
            continue                                     # floats do not break a paragraph
        if previous is not None:
            prev_page, prev_index, a = previous
            between = [it for _, it in sequence[prev_index + 1:index]]
            kind = _decide(a, block, prev_page, page, between)
            if kind:
                a.next, block.prev = block, a
                block.link_kind = kind
        previous = (page, index, block)


def _decide(a: Block, b: Block, page_a: int, page_b: int, between: list[Item]) -> str | None:
    if not _compatible(a, b) or a.next is not None:
        return None
    a_text, b_text = a.text, b.text.lstrip()
    if not ends_open(a_text) or _LIST_START.match(b_text):
        return None
    lower_start = b_text[:1].islower()
    formula_between = [it for it in between if it.kind == "isolate_formula"]
    other_between = [it for it in between if it.kind in ("figure", "table")]

    if page_a == page_b and _same_column(a, b) and b.y0 >= a.y1 - 1:
        gap_has_formula = any(a.y1 - 2 <= (it.box[1] + it.box[3]) / 2 <= b.y0 + 2 for it in formula_between)
        if gap_has_formula and not other_between:
            # "… is defined by [equation] where β controls …"
            return "formula" if (lower_start or not a_text.rstrip().endswith(":")) and b.indent == 0 else None
        if other_between and (lower_start or b.indent == 0) and _last_line_full(a):
            return "flow"                                # a float splits the paragraph
        return None
    if page_b - page_a > 3:
        return None
    if page_b > page_a or b.x0 > a.x1 or b.y1 < a.y0:
        if not _last_line_full(a):
            return None
        if lower_start or b.indent == 0:
            return "flow"
    return None
