"""Write the translation into the PDF.

Source text is removed glyph-precisely with redactions (images and vector
graphics stay), then translated text is drawn and inline formulas are placed
as vector clips of a formula-only copy of the source page.
"""

from __future__ import annotations

import logging

import pymupdf

from . import fonts as fontlib
from .model import Inline, Line, PageInfo, Unit
from .typeset import Op, Placement

log = logging.getLogger(__name__)


Box = tuple[float, float, float, float]


def _hits(rect: Box, box: Box) -> bool:
    return rect[0] < box[2] and rect[2] > box[0] and rect[1] < box[3] and rect[3] > box[1]


def _subtract(rect: Box, blockers: list[Box]) -> list[Box]:
    """Split a horizontal strip so that it touches none of the blockers."""
    pieces = [rect]
    for box in blockers:
        if not (rect[1] < box[3] and rect[3] > box[1]):
            continue
        cut = []
        for x0, y0, x1, y1 in pieces:
            if x1 <= box[0] or x0 >= box[2]:
                cut.append((x0, y0, x1, y1))
                continue
            if box[0] - x0 > 0.06:
                cut.append((x0, y0, box[0] - 0.03, y1))
            if x1 - box[2] > 0.06:
                cut.append((box[2] + 0.03, y0, x1, y1))
        pieces = cut
    return pieces


def _glyph_rect(glyph, blockers: list[Box]) -> Box | None:
    """A tiny rectangle inside one glyph that touches no blocker, if one exists."""
    width = glyph.x1 - glyph.x0
    for fy in (0.3, 0.12, 0.5, 0.05, 0.62):
        y = glyph.oy - fy * glyph.size
        for fx0, fx1 in ((0.3, 0.7), (0.05, 0.3), (0.7, 0.95)):
            rect = (glyph.x0 + fx0 * width, y - 0.08, glyph.x0 + fx1 * width, y + 0.08)
            if not any(_hits(rect, box) for box in blockers):
                return rect
    return None


def _strips(glyphs, protected: list[Box] = ()) -> tuple[list[Box], int]:
    """Rectangles that remove exactly the given glyphs when redacted.

    MuPDF removes every glyph whose box intersects a redaction, so rectangles
    are thin, sit at mid x-height, and are cut around any glyph that must stay
    (a tall bracket of a display equation can reach into the next line).
    Returns (rectangles, number of glyphs that could not be isolated).
    """
    protected = [(b[0] - 0.05, b[1] - 0.05, b[2] + 0.05, b[3] + 0.05) for b in protected]
    rects: list[Box] = []
    stuck = 0
    run: list = []

    def flush() -> None:
        nonlocal stuck
        if not run:
            return
        first, last = run[0], run[-1]
        y = first.oy - 0.3 * first.size
        inset0 = min(0.35, 0.25 * (first.x1 - first.x0))
        inset1 = min(0.35, 0.25 * (last.x1 - last.x0))
        strip = (first.x0 + inset0, y - 0.12, max(first.x0 + inset0 + 0.05, last.x1 - inset1), y + 0.12)
        nearby = [b for b in protected if _hits(strip, b)]
        if not nearby:
            rects.append(strip)
        else:
            pieces = _subtract(strip, nearby)
            rects.extend(pieces)
            for glyph in run:
                if any(px0 < glyph.x1 and px1 > glyph.x0 for px0, _, px1, _ in pieces):
                    continue
                single = _glyph_rect(glyph, nearby)
                if single is None:
                    stuck += 1
                else:
                    rects.append(single)
        run.clear()

    for glyph in glyphs:
        if glyph is None:
            flush()
            continue
        if glyph.c == " ":
            continue
        if run and (abs(glyph.oy - run[0].oy) > 0.08 * run[0].size or abs(glyph.size - run[0].size) > 0.05 * run[0].size
                    or glyph.x0 < run[-1].x0 - 0.5):
            flush()
        run.append(glyph)
    flush()
    return rects, stuck


def _redact(page: pymupdf.Page, rects) -> None:
    if not rects:
        return
    for rect in rects:
        page.add_redact_annot(pymupdf.Rect(rect), fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


def _inline_glyph_ids(units: list[Unit]) -> dict[int, list[Inline]]:
    by_page: dict[int, list[Inline]] = {}
    for unit in units:
        for inline in unit.inlines.values():
            by_page.setdefault(inline.page, []).append(inline)
    return by_page


def _in_inline(glyph, inlines: list[Inline]) -> bool:
    cx, cy = (glyph.x0 + glyph.x1) / 2, glyph.oy - 0.3 * glyph.size
    return any(i.box[0] <= cx <= i.box[2] and i.box[1] <= cy <= i.box[3] for i in inlines)


def formula_layer(source_path: str, pages: list[PageInfo], units: list[Unit]) -> pymupdf.Document | None:
    """A copy of the document in which only inline-formula glyphs remain."""
    by_page = _inline_glyph_ids(units)
    if not by_page:
        return None
    layer = pymupdf.open(source_path)
    for info in pages:
        inlines = by_page.get(info.number)
        if not inlines:
            continue
        sequence, keep = [], []
        for line in list(info.lines) + [l for l in info.kept if l.label == "rotated"]:
            for glyph in line.glyphs:
                if _in_inline(glyph, inlines):
                    sequence.append(None)
                    if glyph.c != " ":
                        keep.append((glyph.x0, glyph.y0, glyph.x1, glyph.y1))
                else:
                    sequence.append(glyph)
            sequence.append(None)
        rects, _ = _strips(sequence, keep)
        _redact(layer[info.number], rects)
    return layer


def _rgb(color: int) -> tuple[float, float, float]:
    return ((color >> 16) & 255) / 255, ((color >> 8) & 255) / 255, (color & 255) / 255


def _draw(page: pymupdf.Page, ops: list[Op], layer: pymupdf.Document | None) -> None:
    writers: dict[int, pymupdf.TextWriter] = {}
    chunk: dict | None = None                        # consecutive characters drawn in one call

    def flush() -> None:
        nonlocal chunk
        if chunk is None:
            return
        writer = writers.get(chunk["color"])
        if writer is None:
            writer = writers[chunk["color"]] = pymupdf.TextWriter(page.rect)
        writer.append((chunk["x"], chunk["y"]), chunk["text"], font=fontlib.font_by_key(chunk["font"]), fontsize=chunk["size"])
        chunk = None

    for op in ops:
        mark = op.mark
        if mark.inline is not None:
            flush()
            inline = mark.inline
            x0, y0, x1, y1 = inline.box
            k = mark.scale
            target = pymupdf.Rect(op.x, op.y - (inline.baseline - y0) * k, op.x + (x1 - x0) * k,
                                  op.y + (y1 - inline.baseline) * k)
            if layer is not None and not target.is_empty:
                page.show_pdf_page(target, layer, inline.page, clip=pymupdf.Rect(inline.box), keep_proportion=False)
            continue
        if (chunk is not None and chunk["font"] == mark.font and chunk["size"] == mark.size
                and chunk["color"] == mark.color and abs(chunk["y"] - op.y) < 0.01 and abs(chunk["end"] - op.x) < 0.02):
            chunk["text"] += mark.text
            chunk["end"] = op.x + mark.width
        else:
            flush()
            chunk = {"x": op.x, "y": op.y, "text": mark.text, "font": mark.font, "size": mark.size,
                     "color": mark.color, "end": op.x + mark.width}
    flush()
    for color, writer in writers.items():
        writer.write_text(page, color=_rgb(color))


def render(source_path: str, pages: list[PageInfo], units: list[Unit], placements: dict[int, Placement],
           progress=None) -> pymupdf.Document:
    """Return the translated document (not yet saved)."""
    done_units = [u for u in units if u.id in placements]
    layer = formula_layer(source_path, pages, done_units)
    doc = pymupdf.open(source_path)

    lines_by_page: dict[int, list[Line]] = {}
    boxes_by_page: dict[int, list] = {}
    for unit in done_units:
        for block in unit.blocks:
            lines_by_page.setdefault(block.page, []).extend(block.lines)
            boxes_by_page.setdefault(block.page, []).append(pymupdf.Rect(block.box))
    ops_by_page: dict[int, list[Op]] = {}
    for unit in done_units:
        for number, ops in placements[unit.id].ops.items():
            ops_by_page.setdefault(number, []).extend(ops)

    stuck = 0
    for index, info in enumerate(pages, 1):
        page = doc[info.number]
        lines = lines_by_page.get(info.number, [])
        if lines:
            sequence = []
            for line in lines:
                sequence.extend(line.glyphs)
                sequence.append(None)
            moving = {id(line) for line in lines}
            keep = [(g.x0, g.y0, g.x1, g.y1)
                    for line in list(info.lines) + [l for l in info.kept if l.label == "rotated"]
                    if id(line) not in moving for g in line.glyphs if g.c != " "]
            # Links pointing at text that is about to move would mislead.
            for link in page.get_links():
                rect = pymupdf.Rect(link["from"])
                if any(rect.intersects(box) for box in boxes_by_page[info.number]):
                    page.delete_link(link)
            rects, missed = _strips(sequence, keep)
            stuck += missed
            _redact(page, rects)
        _draw(page, ops_by_page.get(info.number, []), layer)
        if progress:
            progress("render", index, len(pages))
    if layer is not None:
        doc._zhiyi_layer = layer                      # keep alive until the document is saved
    if stuck:
        log.warning("%d source glyphs overlap text that must stay and were left in place", stuck)
    doc._zhiyi_stuck = stuck
    return doc


def save(doc: pymupdf.Document, path: str) -> None:
    try:
        doc.subset_fonts()
    except Exception as error:                        # an unsubsetted file is large but valid
        log.warning("font subsetting skipped: %s", error)
    doc.save(path, garbage=4, deflate=True, use_objstms=1)


def make_dual(source_path: str, translated: pymupdf.Document, path: str, mode: str = "LR") -> None:
    """Bilingual PDF: side by side (LR) or alternating pages (TB)."""
    source = pymupdf.open(source_path)
    out = pymupdf.open()
    for number in range(len(source)):
        rect = source[number].rect
        if mode == "TB":
            for part in (source, translated):
                page = out.new_page(width=rect.width, height=rect.height)
                page.show_pdf_page(page.rect, part, number)
        else:
            page = out.new_page(width=rect.width * 2, height=rect.height)
            page.show_pdf_page(pymupdf.Rect(0, 0, rect.width, rect.height), source, number)
            page.show_pdf_page(pymupdf.Rect(rect.width, 0, rect.width * 2, rect.height), translated, number)
    out.save(path, garbage=4, deflate=True, use_objstms=1)
    out.close()
    source.close()
