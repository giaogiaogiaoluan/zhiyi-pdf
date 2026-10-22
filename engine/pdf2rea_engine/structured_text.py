"""Keep numbered affiliations and aligned acronym definitions as independent rows.

Runs before styles/formulas, conserves source glyphs and recomputes paint order.
Only positive list/column evidence enables splitting; normal prose is untouched.
"""
from __future__ import annotations
from collections import Counter
import copy
import re
import statistics
from . import registry
from .layout_repair import chars
from .table_text import _box_of

_stats = {"list_rows": 0, "definition_rows": 0}
LABELS = {"plain text", "text", "paragraph_hybrid", "author_info_hybrid", "fallback_line"}


def rows_of(paragraph):
    cs = chars(paragraph)
    if not cs or any(_box_of(c) is None for c in cs):
        return []
    sizes = [c.pdf_style.font_size for c in cs if c.pdf_style and c.pdf_style.font_size]
    if not sizes:
        return []
    tolerance = .6 * statistics.median(sizes)
    rows = []
    # Raw font boxes share a baseline; visual descenders and superscripts
    # otherwise split one physical row into multiple false rows.
    row_y = lambda c: ((c.box or _box_of(c)).y + (c.box or _box_of(c)).y2)/2
    for c in sorted(cs, key=lambda c: (-row_y(c), _box_of(c).x)):
        y = row_y(c)
        row = next((r for r in rows if abs(y-r[0]) <= tolerance), None)
        if row is None:
            rows.append([y, [c]])
        else:
            row[1].append(c)
    return [sorted(r[1], key=lambda c: _box_of(c).x) for r in rows]


def text(cs):
    return ''.join(c.char_unicode or '' for c in cs).strip()


def numbered_groups(rows):
    starts = []
    for i, row in enumerate(rows):
        m = re.match(r'^(\d{1,2})\s+[A-Za-z]', text(row))
        if m:
            starts.append((i, int(m[1])))
        elif re.match(r'^\*\s*(?:Correspondence|Corresponding)', text(row), re.I):
            starts.append((i, None))
    nums = [n for _, n in starts if n is not None]
    if len(nums) < 3 or nums != list(range(nums[0], nums[0] + len(nums))) or starts[0][0] != 0:
        return None
    bounds = [i for i, _ in starts] + [len(rows)]
    return [([c for row in rows[a:b] for c in row], 'author_info_hybrid') for a,b in zip(bounds,bounds[1:])]


def definition_groups(rows):
    if len(rows) < 3:
        return None
    columns = []
    for row in rows:
        visible = [c for c in row if (c.char_unicode or '').strip()]
        for a,b in zip(visible, visible[1:]):
            size = b.pdf_style.font_size if b.pdf_style else 9
            if _box_of(b).x - _box_of(a).x2 > 1.5 * (size or 9):
                prefix = text([c for c in row if _box_of(c).x < _box_of(b).x])
                if re.fullmatch(r'[A-Z][A-Za-z0-9 +/().−-]{0,21}', prefix):
                    columns.append(_box_of(b).x)
                    break
    if len(columns) < .6 * len(rows):
        return None
    column = statistics.median(columns)
    if sum(abs(x-column) <= 5 for x in columns) < .7 * len(rows):
        return None
    groups = []
    for row in rows:
        index = next((i for i,c in enumerate(row) if _box_of(c).x >= column-2 and (c.char_unicode or '').strip()), None)
        if index is None:
            return None
        key, definition = row[:index], row[index:]
        if not re.fullmatch(r'[A-Z][A-Za-z0-9 +/().−-]{0,21}', text(key)) or len(text(definition)) < 6 or not re.search('[a-z]', text(definition)):
            return None
        groups.extend([(key, 'abbreviation_key'), (definition, 'table_cell')])
    return groups


def repair(finder, document, module):
    for page in document.page:
        rebuilt = []
        changed = False
        for paragraph in page.pdf_paragraph:
            if paragraph.layout_label not in LABELS:
                rebuilt.append(paragraph)
                continue
            rows = rows_of(paragraph)
            groups = numbered_groups(rows)
            kind = 'list_rows'
            if not groups:
                groups = definition_groups(rows)
                kind = 'definition_rows'
            if not groups:
                rebuilt.append(paragraph)
                continue
            if Counter(id(c) for c in chars(paragraph)) != Counter(id(c) for cs,_ in groups for c in cs):
                rebuilt.append(paragraph)
                continue
            staged = []
            try:
                for i, (cs,label) in enumerate(groups):
                    child = copy.copy(paragraph)
                    # Rebuild lines instead of one giant character run.
                    line_groups = rows_of(type('P', (), {'pdf_paragraph_composition': [module.PdfParagraphComposition(pdf_character=c) for c in cs]})())
                    compositions = []
                    for row in line_groups:
                        line = module.PdfLine(pdf_character=row)
                        finder.update_line_data(line)
                        compositions.append(module.PdfParagraphComposition(pdf_line=line))
                    child.pdf_paragraph_composition = compositions
                    child.layout_label = label
                    child.debug_id = f'{paragraph.debug_id}-{kind}-{i}'
                    finder.update_paragraph_data(child, update_unicode=True)
                    staged.append(child)
            except Exception as error:
                registry._errors.append(f'structured text split: {error!r}')
                rebuilt.append(paragraph)
                continue
            rebuilt.extend(staged)
            _stats[kind] += len(groups) if kind == 'list_rows' else len(groups)//2
            changed = True
        if changed:
            page.pdf_paragraph = rebuilt
            finder._set_paragraph_render_order(page)


def install(options):
    def patch(module):
        def factory(original):
            def process(self, document):
                result = original(self, document)
                repair(self, document, module)
                return result
            return process
        registry.wrap_method(module.ParagraphFinder, 'process', factory, 'structured_text')
    registry.on_import('babeldoc.format.pdf.document_il.midend.paragraph_finder', patch)
    def patch_translator(module):
        def factory(original):
            def pre(self, paragraph, *args, **kwargs):
                if paragraph.layout_label == 'abbreviation_key':
                    return None, None
                return original(self, paragraph, *args, **kwargs)
            return pre
        registry.wrap_method(module.ILTranslator, 'pre_translate_paragraph', factory, 'abbreviation_keys')
    registry.on_import('babeldoc.format.pdf.document_il.midend.il_translator', patch_translator)


def stats():
    return dict(_stats)
