"""Document analysis: pages → blocks → logical paragraphs (units)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pymupdf

from . import chain, extract, fonts, formula, layout, markup, order, regions
from .model import Block, PageInfo, Unit

log = logging.getLogger(__name__)


@dataclass
class Analysis:
    pages: list[PageInfo]
    units: list[Unit]
    body_size: float
    serif: bool
    stats: dict = field(default_factory=dict)


def _mark_references(pages_items) -> int:
    """Flag bibliography entries so they are left in the original language."""
    inside, count = False, 0
    for items in pages_items:
        for item in items:
            block = item.block
            if block is None:
                continue
            if block.role in ("heading", "title"):
                inside = chain.is_references_heading(block.text)
            elif inside and block.role in ("body", "list", "footnote"):
                block.reference = True
                count += 1
    return count


def analyze(doc: pymupdf.Document, pages: list[int] | None = None, *, translate_references: bool = False,
            progress=None) -> Analysis:
    numbers = list(range(len(doc))) if pages is None else [p for p in pages if 0 <= p < len(doc)]
    infos: list[PageInfo] = []
    rotated: dict[int, list] = {}
    for done, number in enumerate(numbers, 1):
        page = doc[number]
        found = regions.detect(page)
        lines, other = extract.extract(page, number, found)
        info = PageInfo(number, page.rect.width, page.rect.height, lines, found)
        layout.label_lines(info)
        rotated[number] = other
        infos.append(info)
        if progress:
            progress("analyze", done, len(numbers))
    layout.fix_running_heads(infos)
    for info in infos:
        layout.build_blocks(info)
        info.kept.extend(rotated[info.number])
    body = layout.assign_roles(infos)

    pages_items = [order.reading_order(info) for info in infos]
    skipped = 0 if translate_references else _mark_references(pages_items)
    for info in infos:
        for block in [b for b in info.blocks if b.reference]:
            info.kept.extend(block.lines)
        info.blocks = [b for b in info.blocks if not b.reference]
    if skipped:
        pages_items = [order.reading_order(info) for info in infos]
    for info in infos:
        order.assign_frames(info)
    chain.link(pages_items)

    all_lines = [line for info in infos for line in info.lines]
    text_fonts = formula.font_roles(all_lines)
    body_blocks = [b for info in infos for b in info.blocks if b.role == "body"]
    serif = sum(b.serif for b in body_blocks) >= 0.5 * len(body_blocks) if body_blocks else True
    can_render = fonts.fontset(serif).can_render

    units: list[Unit] = []
    for items in pages_items:
        for item in items:
            block = item.block
            if block is None or block.prev is not None:
                continue
            blocks: list[Block] = [block]
            while blocks[-1].next is not None:
                blocks.append(blocks[-1].next)
            unit = Unit(id=len(units) + 1, blocks=blocks, role=block.role)
            markup.build_source(unit, text_fonts, can_render)
            if not any(c.isalpha() for c in markup.plain_text(unit.source)):
                unit.role = "skip"
            units.append(unit)
    # Units with nothing translatable stay untouched on the page.
    by_number = {info.number: info for info in infos}
    for unit in [u for u in units if u.role == "skip"]:
        for b in unit.blocks:
            by_number[b.page].kept.extend(b.lines)
            by_number[b.page].blocks = [x for x in by_number[b.page].blocks if x is not b]
    units = [u for u in units if u.role != "skip"]
    for index, unit in enumerate(units, 1):
        unit.id = index
    stats = {"pages": len(infos), "units": len(units), "chained": sum(len(u.blocks) > 1 for u in units),
             "inline_formulas": sum(len(u.inlines) for u in units), "references_kept": skipped}
    return Analysis(infos, units, body, serif, stats)
