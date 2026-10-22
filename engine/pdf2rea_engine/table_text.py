"""Fix 3 -- text inside tables is recognised and translated.

Root cause (verified in the installed engine)::

    1. The advertised switch is dead code.
       pdf2zh_next/high_level.py:576-580
           if settings.pdf.translate_table_text:
               from babeldoc.docvision.table_detection.rapidocr import RapidOCRModel
               table_model = RapidOCRModel()
       babeldoc/docvision/table_detection/rapidocr.py:8-38
           class RapidOCRModel:
               "Compatibility no-op for the retired RapidOCR table text detector."
               def predict(...):        return YoloResult(names=self.names, boxes=[])
               def handle_document(...): yield page, YoloResult(names=..., boxes=[])
       --> ``--translate-table-text`` does reach babeldoc, TableParser runs, receives
           zero boxes for every page and contributes nothing.  Ticking the Zotero
           preference cannot help; that is why the toggle appeared inert.

    2. Table glyphs are dropped before translation can even start.
       babeldoc/format/pdf/document_il/utils/layout_helper.py:801-849
           is_text_layout() whitelists "table_text", "table_cell",
           "wired_table_cell", "wireless_table_cell", "table_caption", ...
           but NOT the whole-table labels "table" / "wired_table" / "blocked_table"
           that the doclayout model emits for a bordered table block.
       paragraph_finder.py:454
           if not is_text_layout(char_layout) or self.is_isolated_formula(char):
               skip_chars.append(char); continue
       --> every glyph inside a block labelled "table" leaves paragraph grouping and
           is never translated.  paragraph_finder binds the helper by value
           (paragraph_finder.py:34 ``from ... import is_text_layout``), so patching
           only the helper module would silently miss the real call site.

Repair, in order of safety:
  (A) whitelist the whole-table labels so their characters enter paragraph grouping;
  (B) split each table block into *cell-sized* paragraphs (2-D gap clustering) so a
      cell is translated and re-typeset inside its own box, instead of one giant
      paragraph spanning twelve columns;
  (C) ``cell_segmentation = false`` keeps a table block as one paragraph -- better
      reading order for narrow two-column tables, but it can overflow wide cells.

(A)+(B) are pure geometry over data babeldoc already has: no model download and no
OCR, so this keeps working fully offline.
"""

from __future__ import annotations

from . import registry

DEFAULT_TABLE_LABELS = ("table", "wired_table", "blocked_table")

_stats = {"cell_paragraphs": 0, "table_blocks": 0}


def _layout_name(layout) -> str:
    return (getattr(layout, "name", "")
            or getattr(layout, "class_name", "")
            or "").lower()


def _paragraph_label(paragraph) -> str:
    return (getattr(paragraph, "layout_label", "") or "").lower()


def _box_of(char):
    """Upstream reads ``char.visual_bbox.box`` (paragraph_finder.py:458,
    styles_and_formulas.py:384); ``char.box`` is the raw PDF box and can differ
    for transformed text, so visual_bbox is preferred whenever present."""
    visual = getattr(char, "visual_bbox", None)
    box = getattr(visual, "box", None) if visual is not None else None
    if box is None:
        box = getattr(char, "box", None)
    return box


def _centre_y(char) -> float:
    box = _box_of(char)
    return (box.y + box.y2) / 2.0 if box is not None else 0.0


def _left_x(char) -> float:
    box = _box_of(char)
    return box.x if box is not None else 0.0


def _right_x(char) -> float:
    box = _box_of(char)
    return box.x2 if box is not None else 0.0


def _height(char) -> float:
    box = _box_of(char)
    return max((box.y2 - box.y), 0.5) if box is not None else 0.5


def _advance(char) -> float:
    box = _box_of(char)
    width = (_right_x(char) - _left_x(char)) if box is not None else 0.0
    value = float(getattr(char, "advance", 0.0) or 0.0)
    return max(value, width * 0.6, 0.5)


def segment_into_cells(compositions, gap_factor: float = 1.6, row_factor: float = 0.6):
    """Group single-character compositions into cell-sized clusters.

    At ``ParagraphFinder`` stage every composition holds exactly one
    ``pdf_character``.  Rows split when vertical centres differ by more than
    ``row_factor`` x median glyph height; within a row, cells split on a horizontal
    gap wider than ``gap_factor`` x median advance.  Output is in reading order
    (top row first, left cell first).
    """
    pairs = []
    for composition in compositions:
        char = getattr(composition, "pdf_character", None)
        if char is not None:
            pairs.append((char, composition))
    if len(pairs) < 2:
        return [list(compositions)]

    heights = sorted(_height(char) for char, _c in pairs)
    advances = sorted(_advance(char) for char, _c in pairs)
    median_height = heights[len(heights) // 2]
    median_advance = advances[len(advances) // 2]

    rows: list[list] = []  # [row_centre_y, [(left_x, right_x, composition), ...]]
    for char, composition in sorted(pairs, key=lambda item: (-_centre_y(item[0]),
                                                             _left_x(item[0]))):
        centre = _centre_y(char)
        entry = (_left_x(char), _right_x(char), composition)
        target = None
        for row in rows:
            if abs(centre - row[0]) <= median_height * row_factor:
                target = row
                break
        if target is None:
            rows.append([centre, [entry]])
        else:
            target[0] = (target[0] + centre) / 2.0
            target[1].append(entry)

    cells: list[list] = []
    for _row_centre, items in rows:
        items.sort(key=lambda item: item[0])
        current = [items[0][2]]
        previous_right = items[0][1]
        for left_x, right_x, composition in items[1:]:
            if left_x - previous_right > median_advance * gap_factor:
                cells.append(current)
                current = [composition]
            else:
                current.append(composition)
            previous_right = max(previous_right, right_x)
        cells.append(current)
    return cells


def install(options: dict) -> None:
    extra_labels = {str(label).lower() for label in
                    options.get("table_labels", DEFAULT_TABLE_LABELS)}
    do_segment = bool(options.get("cell_segmentation", True))

    # ------------------------------------------------------------------ (A)
    def _patch_layout_helper(module):

        def _factory(original):
            def is_text_layout(layout):
                if original(layout):
                    return True
                return _layout_name(layout) in extra_labels

            return is_text_layout

        if registry.patch_module_function(
            module, "is_text_layout", _factory, "table_text_whitelist",
            "layout_helper.is_text_layout accepts table blocks",
        ):
            # Rebind every by-value import site; missing one silently keeps the bug.
            registry.rebind_dependents("is_text_layout", module.is_text_layout, (
                "babeldoc.format.pdf.document_il.midend.paragraph_finder",
                "babeldoc.format.pdf.document_il.midend.styles_and_formulas",
                "babeldoc.format.pdf.document_il.utils.spatial_analyzer",
            ))

    registry.on_import("babeldoc.format.pdf.document_il.utils.layout_helper",
                       _patch_layout_helper)

    if not do_segment:
        return

    # ------------------------------------------------------------------ (B)
    def _patch_paragraph_finder(module):
        cls = module.ParagraphFinder

        def _factory(original):
            def process(self, document):
                original(self, document)
                try:
                    split_table_blocks(self, document, extra_labels, module.PdfParagraph)
                except Exception as error:  # upstream behaviour must survive us failing
                    registry._errors.append(f"table cell split: {error!r}")
                return None

            return process

        if registry.wrap_method(cls, "process", _factory, "table_text"):
            registry.note_applied("ParagraphFinder splits table blocks into cells")

    registry.on_import("babeldoc.format.pdf.document_il.midend.paragraph_finder",
                       _patch_paragraph_finder)


def split_table_blocks(instance, document, extra_labels, pdf_paragraph_cls) -> int:
    """Turn every whole-table paragraph into one paragraph per detected cell.

    Called right after ``ParagraphFinder.process`` so the paragraphs still hold
    single-character compositions (nothing has been merged into lines yet).
    ``instance`` is the live ParagraphFinder, reused for its own
    ``update_paragraph_data`` so boxes/unicode/indent are computed exactly the way
    upstream computes them for every other paragraph.
    """
    created = 0
    for page in getattr(document, "page", []) or []:
        paragraphs = getattr(page, "pdf_paragraph", None)
        if not paragraphs:
            continue
        rebuilt = []
        changed = False
        for paragraph in paragraphs:
            if _paragraph_label(paragraph) not in extra_labels:
                rebuilt.append(paragraph)
                continue
            compositions = list(paragraph.pdf_paragraph_composition or [])
            if len(compositions) < 4:
                rebuilt.append(paragraph)
                continue
            _stats["table_blocks"] += 1
            cells = segment_into_cells(compositions)
            if len(cells) < 2:
                rebuilt.append(paragraph)
                continue
            # Commit only if every nonempty source composition survives. A failed
            # update must keep the original table instead of silently dropping cells.
            staged = []
            for index, cell in enumerate(cells):
                child = pdf_paragraph_cls(
                    pdf_paragraph_composition=cell,
                    layout_id=paragraph.layout_id,
                    layout_label=paragraph.layout_label,
                    xobj_id=paragraph.xobj_id,
                    debug_id=f"{paragraph.debug_id}-cell-{index}",
                )
                try:
                    instance.update_paragraph_data(child, update_unicode=True)
                except Exception:
                    staged = []
                    break
                if not (child.unicode or "").strip():
                    if any((getattr(c.pdf_character, "char_unicode", "") or "").strip() for c in cell):
                        staged = []
                        break
                    continue
                staged.append(child)
            kept = sum(len(p.pdf_paragraph_composition) for p in staged)
            if not staged or kept != len(compositions):
                rebuilt.append(paragraph)
                continue
            rebuilt.extend(staged)
            _stats["cell_paragraphs"] += len(staged)
            created += len(staged)
            changed = True
        if changed:
            page.pdf_paragraph = rebuilt
    return created


def stats() -> dict:
    return dict(_stats)
