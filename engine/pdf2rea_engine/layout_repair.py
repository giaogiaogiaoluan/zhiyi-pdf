"""Reconnect text cut through words by a layout box boundary, before translation.

Only single-line fragments with a fallback label can seed a repair. Never join
columns across a gutter, tables, equations, or unrelated consecutive paragraphs.
Raw character baselines avoid mistaking the short visual box of 'or' for a
superscript. Original glyph objects (including styles and citations) survive.
"""
from __future__ import annotations
import re
import statistics
from . import registry

_stats = {'inline_fragments': 0, 'continued_lines': 0}
BODY = {'plain text', 'text', 'paragraph_hybrid', 'fallback_line'}
CAPTIONS = {'figure_caption', 'table_caption'}


def chars(paragraph):
    result = []
    for comp in paragraph.pdf_paragraph_composition or []:
        if comp.pdf_character is not None:
            result.append(comp.pdf_character)
        for key in ('pdf_line', 'pdf_formula', 'pdf_same_style_characters'):
            group = getattr(comp, key, None)
            if group is not None:
                result.extend(group.pdf_character or [])
    return result


def metrics(paragraph):
    cs = chars(paragraph)
    if not cs or not paragraph.debug_id or paragraph.layout_label not in BODY | CAPTIONS:
        return None
    if any(getattr(c, "formula_layout_id", None) is not None for c in cs):
        return None
    sizes = [c.pdf_style.font_size for c in cs if c.pdf_style and c.pdf_style.font_size]
    if not sizes or any(c.box is None for c in cs):
        return None
    size = statistics.median(sizes)
    ys = [c.box.y for c in cs]
    # Exclude multi-line paragraphs; don't absorb a neighboring paragraph's end.
    if size <= 0 or max(ys)-min(ys) > .65*size:
        return None
    return (size, statistics.median(ys), min(c.box.x for c in cs), max(c.box.x2 for c in cs))


def repair(finder, document, module):
    for page in document.page:
        removed = set()
        repaired = set()
        values = [(p, metrics(p)) for p in page.pdf_paragraph]
        values = [(p,m) for p,m in values if m]
        values.sort(key=lambda v:(-v[1][1],v[1][2]))
        for a, _ in values:
            if id(a) in removed:
                continue
            while True:
                am = metrics(a)
                if not am: break
                candidates = []
                for b, _ in values:
                    if a is b or id(b) in removed: continue
                    bm = metrics(b)
                    if not bm or a.xobj_id != b.xobj_id: continue
                    if not .9 <= am[0]/bm[0] <= 1.1 or abs(am[1]-bm[1]) > .3*am[0]: continue
                    if 'fallback_line' not in (a.layout_label,b.layout_label) and id(a) not in repaired: continue
                    # Caption/body combinations require the fallback to carry the caption prefix.
                    if a.layout_label in CAPTIONS or b.layout_label in CAPTIONS:
                        fallback = a if a.layout_label == 'fallback_line' else b
                        if not re.match(r'^(?:Figure|Fig\.|Table)\s*\d',fallback.unicode or '',re.I): continue
                    gap = bm[2]-am[3]
                    limit = 2.2*am[0] if (a.unicode or '').rstrip().endswith(':') else am[0]
                    if -.35*am[0] <= gap <= limit and bm[2] > am[2]: candidates.append((gap,b))
                if not candidates: break
                b = min(candidates,key=lambda v:v[0])[1]
                merged_chars = sorted(chars(a)+chars(b),key=lambda c:c.box.x)
                line = module.PdfLine(pdf_character=merged_chars)
                finder.update_line_data(line)
                a.pdf_paragraph_composition = [module.PdfParagraphComposition(pdf_line=line)]
                if b.layout_label in CAPTIONS: a.layout_label=b.layout_label
                elif a.layout_label not in CAPTIONS and b.layout_label != 'fallback_line': a.layout_label=b.layout_label
                a.render_order=min((v for v in (a.render_order,b.render_order) if v is not None),default=None)
                finder.update_paragraph_data(a,update_unicode=True)
                removed.add(id(b));repaired.add(id(a));_stats['inline_fragments']+=1
        # Reattach a wrapped line only after a verified inline repair and an open sentence.
        for a,_ in values:
            if id(a) in removed or id(a) not in repaired or a.layout_label not in BODY: continue
            from .cross_page import ends_open,starts_continued
            if not ends_open(a.unicode): continue
            ac=chars(a);size=statistics.median(c.pdf_style.font_size for c in ac)
            baseline=min(c.box.y for c in ac)
            choices=[]
            for b,_ in values:
                if a is b or id(b) in removed or b.layout_label not in BODY or b.xobj_id!=a.xobj_id:continue
                bm=metrics(b)
                if not bm or not starts_continued(b.unicode or ''):continue
                if not .9<=size/bm[0]<=1.1 or abs(a.box.x-b.box.x)>size*.5:continue
                if .8*size<=baseline-bm[1]<=1.5*size:choices.append((baseline-bm[1],b))
            if not choices:continue
            b=min(choices,key=lambda v:v[0])[1]
            a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
            finder.update_paragraph_data(a,update_unicode=True)
            if a.layout_label=='fallback_line':a.layout_label='plain text'
            removed.add(id(b));_stats['continued_lines']+=1
        page.pdf_paragraph=[p for p in page.pdf_paragraph if id(p) not in removed]


def install(options):
    def patch(module):
        def factory(original):
            def process(self, document):
                result=original(self,document)
                repair(self,document,module)
                return result
            return process
        registry.wrap_method(module.ParagraphFinder,'process',factory,'layout_repair')
    registry.on_import('babeldoc.format.pdf.document_il.midend.paragraph_finder',patch)

    def patch_translator(module):
        def factory(original):
            def pre_translate(self, paragraph, *args, **kwargs):
                label = (getattr(paragraph, "layout_label", "") or "").lower()
                text = getattr(paragraph, "unicode", "") or ""
                # process_page bypasses _should_translate_paragraph. Guard the
                # shared pre-translation entry used by both LLM and plain engines.
                if label in {"header", "footer", "page_header", "page_footer"}:
                    return None, None
                if label == "abandon" and (len(text.strip()) < 48 or re.search(r"https?://|www\.|copyright|©|\bdoi\b", text, re.I)):
                    return None, None
                return original(self, paragraph, *args, **kwargs)
            return pre_translate
        registry.wrap_method(module.ILTranslator, "pre_translate_paragraph", factory, "page_furniture")

    registry.on_import('babeldoc.format.pdf.document_il.midend.il_translator',patch_translator)



def stats():
    return dict(_stats)
