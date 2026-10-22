"""Recognize page/column continuations and translate one reconstructed paragraph.

The model receives one item; the response adapter distributes the continuous
translation back to its two existing layout boxes. Prompt-only mode is retained
as a fallback for responses that cannot safely preserve formula/style ownership.
"""

from __future__ import annotations

import re
import threading

from . import joint_paragraph, llm_hook, registry

_local = threading.local()
_stats_lock = threading.Lock()
_stats = {"candidates": 0, "directed": 0, "skipped": 0,
          "joined_requests": 0, "joined": 0, "context_fallbacks": 0}

# Sentence-closing punctuation: a fragment ending here is *probably* complete.
CLOSED_ENDINGS = (".", "!", "?", "。", "！", "？", ":", "；", ";", "…", "”", "\"", ")")
# Anything else is an open ending: comma, hyphenation, open bracket, bare word.
STRONG_OPEN_ENDINGS = (",", "-", "—", "(", "“", "[", "{", "/")

DIRECTIVE_TEMPLATE = """## Cross-Page Continuation (context fallback)
These {count} items continue the same paragraph across a page/column boundary.
Read both source fragments together to understand the complete context. Return
exactly the original ids as separate outputs in {lang}, preserving each item's
formatting and formula placeholders. Do not invent a sentence ending at the page
boundary. This request uses the layout-preserving fallback format.
"""


# --------------------------------------------------------------------------- #
# continuation heuristics
# --------------------------------------------------------------------------- #
def ends_open(text: str) -> bool:
    """True when a fragment most likely continues on the next page."""
    stripped = (text or "").rstrip()
    if not stripped:
        return False
    # "et al." / "e.g." / "i.e." / "vs." are abbreviations, not sentence ends.
    if re.search(r"\b(?:[a-z]\.){2,}$", stripped) or re.search(
        r"\b(?:etc|et al|e\.g|i\.e|vs|viz|cf|Fig|Eq|Ref|No|Sec|Trans)\.$",
        stripped,
        re.IGNORECASE,
    ):
        return True
    last = stripped[-1]
    if last in STRONG_OPEN_ENDINGS:
        return True
    if last in CLOSED_ENDINGS:
        return False
    # Ends on a letter/digit/symbol with no terminal punctuation -> open.
    return True


def starts_continued(text: str) -> bool:
    stripped = (text or "").lstrip()
    if not stripped:
        return False
    first = stripped[0]
    if first.islower():
        return True
    # A continuation may legitimately start with a capital (proper noun) or a
    # digit/bracket (a citation that happened to fall at the break).
    return bool(re.match(r"^[0-9(\[\"“']", stripped))


def is_headish(paragraph, blocked=("title", "table_caption", "figure_caption",
                                   "table_footnote", "footnote", "header", "footer",
                                   "page_header", "page_footer", "author_info_hybrid")) -> bool:
    label = (getattr(paragraph, "layout_label", "") or "").lower()
    return any(token in label for token in blocked)


def looks_like_list_item(text: str) -> bool:
    return bool(re.match(r"^\s*(?:\(\d+\)|\d+[.)]|[a-z][.)]|\[[^\]]{1,6}\]|[-•*])\s", text or ""))


def is_continuation(first_text: str, second_text: str, options: dict,
                    first_paragraph=None, second_paragraph=None) -> tuple[bool, str]:
    """Decide whether a page-break pair is really one broken sentence."""
    require_open = options.get("require_open_ending", True)
    minimum = int(options.get("min_fragment_chars", 12))

    if first_paragraph is not None and is_headish(first_paragraph):
        return False, "first is heading/caption"
    if second_paragraph is not None and is_headish(second_paragraph):
        return False, "second is heading/caption"
    if len((first_text or "").strip()) < minimum:
        return False, "first fragment too short"
    if looks_like_list_item(first_text) or looks_like_list_item(second_text):
        return False, "list item boundary"

    open_end = ends_open(first_text)
    continued_start = starts_continued(second_text)
    if open_end and continued_start:
        return True, "sentence continues"
    # The caller may accept geometry-confirmed paragraph continuation; text alone
    # cannot distinguish a new paragraph from a new sentence in the same one.
    return False, "needs layout evidence" if (open_end or not require_open) else "first fragment ends a sentence"


def _layout_continues(paragraphs, pages):
    """Conservative evidence for a paragraph split BETWEEN complete sentences."""
    first, second = paragraphs
    if getattr(second, "first_line_indent", None) is True:
        return False
    boxes = [getattr(p, "box", None) for p in paragraphs]
    if any(b is None for b in boxes):
        return False
    sizes = [getattr(getattr(p,"pdf_style",None),"font_size",None) for p in paragraphs]
    if not all(isinstance(v,(int,float)) and v > 0 for v in sizes):
        return False
    if max(sizes)/min(sizes) > 1.2:
        return False
    if abs(boxes[0].x-boxes[1].x) > max(8,sizes[0]):
        return False
    if pages[0] is not pages[1]:
        frames = [getattr(getattr(page,"cropbox",None),"box",None) or getattr(getattr(page,"mediabox",None),"box",None) for page in pages]
        if any(frame is None for frame in frames):
            return False
        if boxes[0].y > frames[0].y + .24*(frames[0].y2-frames[0].y):
            return False
        if boxes[1].y2 < frames[1].y + .76*(frames[1].y2-frames[1].y):
            return False
    chars = []
    for comp in getattr(first,"pdf_paragraph_composition",[]) or []:
        for name in ("pdf_line","pdf_same_style_characters","pdf_formula"):
            group = getattr(comp,name,None)
            chars.extend(getattr(group,"pdf_character",[]) or [])
        char=getattr(comp,"pdf_character",None)
        if char is not None: chars.append(char)
    char_boxes = [getattr(c,"box",None) for c in chars if (getattr(c,"char_unicode","") or "").strip()]
    char_boxes = [b for b in char_boxes if b is not None]
    if not char_boxes:
        return False
    baseline = min(b.y for b in char_boxes)
    if not any(b.y-baseline > sizes[0]*.9 for b in char_boxes):
        return False  # one short complete line does not establish paragraph identity
    last_line = [b for b in char_boxes if abs(b.y-baseline) < sizes[0]*.6]
    width = boxes[0].x2-boxes[0].x
    # A short line ending in punctuation usually indicates a real paragraph end.
    return width > 0 and (max(b.x2 for b in last_line)-boxes[0].x)/width >= .86



# --------------------------------------------------------------------------- #
# batch observation
# --------------------------------------------------------------------------- #
def _describe_batch(batch_paragraph, options, lang_out: str):
    """Return the directive for this batch, or None."""
    try:
        paragraphs = list(batch_paragraph.paragraphs)
        pages = list(batch_paragraph.pages)
    except AttributeError:
        return None
    if len(paragraphs) != 2 or len(pages) != 2:
        return None

    cross_page = pages[0] is not pages[1]
    if not cross_page and not options.get("also_cross_column", True):
        return None
    if not cross_page:
        # A normal two-item batch can also share a page. Require a column
        # transition in the actual paragraph geometry before altering its prompt.
        left, right = (getattr(p, "box", None) for p in paragraphs)
        if left is None or right is None or right.x <= left.x2 or right.y2 <= left.y:
            return None
        kind = "cross_column"
    else:
        kind = "cross_page"

    numbers = [getattr(page,"page_number",None) for page in pages]
    if cross_page and all(isinstance(n,int) for n in numbers) and numbers[1] != numbers[0]+1:
        return {"kind":kind,"directive":None,"reason":"non-adjacent source pages"}
    texts = [getattr(p, "unicode", "") or "" for p in paragraphs]
    ok, reason = is_continuation(texts[0], texts[1], options, paragraphs[0], paragraphs[1])
    safe_labels = not any(is_headish(p) for p in paragraphs)
    safe_lists = not any(looks_like_list_item(t) for t in texts)
    if not ok and safe_labels and safe_lists and len(texts[0].strip()) >= int(options.get("min_fragment_chars",12)):
        if (ends_open(texts[0]) or not options.get("require_open_ending",True)) and _layout_continues(paragraphs,pages):
            ok,reason = True,"layout-confirmed paragraph continuation"
    if not ok:
        return {"kind": kind, "directive": None, "reason": reason}

    directive = DIRECTIVE_TEMPLATE.format(count=len(paragraphs), lang=lang_out)
    if kind == "cross_column":
        directive = directive.replace(
            "the page ended", "the text column ended"
        ).replace("the page break", "the column break")
    return {"kind": kind, "directive": directive, "reason": reason,
            "signature": [t[:80] for t in texts[:2]]}


def _hook(ctx) -> str:
    pair = getattr(_local, "pair", None)
    if not pair or not ctx.items:
        return ctx.prompt
    with _stats_lock:
        _stats["candidates"] += 1
        if not pair.get("directive") or len(ctx.items) != 2:
            _stats["skipped"] += 1
            return ctx.prompt
        _stats["directed"] += 1
    fallback = llm_hook.insert_instructions(ctx.prompt, pair["directive"])
    if pair.get("mode") == "prompt":
        return fallback
    try:
        request = joint_paragraph.prepare(ctx.items,pair.get("count_tokens"))
    except joint_paragraph.JointResponseError:
        with _stats_lock: _stats["context_fallbacks"] += 1
        return fallback
    with _stats_lock: _stats["joined_requests"] += 1
    def restore(response):
        try:
            result = request.restore(response)
        except Exception:
            with _stats_lock: _stats["context_fallbacks"] += 1
            raise
        with _stats_lock: _stats["joined"] += 1
        return result
    ctx.response_transform = restore
    ctx.fallback_prompt = fallback
    return llm_hook.insert_instructions(ctx.replace_items([request.item]), joint_paragraph.DIRECTIVE)


def _include_boundary_prose(translator, page, paragraphs, translated_ids):
    """Recover long same-size body text mislabelled as 'abandon' near an edge.

    Do not relabel the document. This only extends cross-page candidate selection;
    normal headings/captions/footnotes and short page labels stay excluded.
    """
    import statistics
    body_sizes = [getattr(getattr(p,"pdf_style",None),"font_size",None) for p in paragraphs]
    body_sizes = [n for n in body_sizes if isinstance(n,(int,float)) and n > 0]
    if not body_sizes:
        return paragraphs
    size = statistics.median(body_sizes)
    frame = getattr(getattr(page,"cropbox",None),"box",None) or getattr(getattr(page,"mediabox",None),"box",None)
    if frame is None:
        return paragraphs
    result = list(paragraphs)
    known = {id(p) for p in result}
    for p in page.pdf_paragraph:
        if id(p) in known or (getattr(p,"layout_label","") or "").lower() not in {"abandon","fallback_line"}:
            continue
        if not translator._should_translate_paragraph(p,translated_ids,require_body_text=False):
            continue
        text = p.unicode or ""
        long_prose = len(text.strip()) >= 48 and len(text.split()) >= 7
        short_continuation = len(text.strip()) >= 24 and len(text.split()) >= 3 and ends_open(text)
        if not (long_prose or short_continuation) or looks_like_list_item(text):
            continue
        if re.search(r"https?://|www\.|copyright|all rights reserved|©|accepted manuscript|downloaded|preprint|\bdoi\b",text,re.I):
            continue
        font_size = getattr(getattr(p,"pdf_style",None),"font_size",None)
        if not isinstance(font_size,(int,float)) or not .95 <= font_size/size <= 1.05:
            continue
        box = getattr(p,"box",None)
        if box is None or not (box.y <= frame.y+.25*(frame.y2-frame.y) or box.y2 >= frame.y+.75*(frame.y2-frame.y)):
            continue
        result.append(p)
    if all(isinstance(getattr(p,"render_order",None),(int,float)) for p in result):
        result.sort(key=lambda p:p.render_order)
    return result


def install(options: dict) -> None:
    target = "babeldoc.format.pdf.document_il.midend.il_translator_llm_only"

    def _patch(module):
        cls = module.ILTranslatorLLMOnly

        def _selection_factory(original):
            def select(self,*args,**kwargs):
                _local.selecting_cross_page = True
                try:
                    return original(self,*args,**kwargs)
                finally:
                    _local.selecting_cross_page = False
            return select

        def _filter_factory(original):
            def filtered(self,page,translated_ids=None,require_body_text=False):
                result = original(self,page,translated_ids,require_body_text)
                if require_body_text and getattr(_local,"selecting_cross_page",False):
                    return _include_boundary_prose(self,page,result,translated_ids)
                return result
            return filtered

        registry.wrap_method(cls,"process_cross_page_paragraph",_selection_factory,"cross_page_selection")
        registry.wrap_method(cls,"_filter_paragraphs",_filter_factory,"cross_page_filter")

        def _factory(original):
            def translate_paragraph(self, batch_paragraph, *args, **kwargs):
                lang_out = getattr(self.translation_config, "lang_out", "the target language")
                info = _describe_batch(batch_paragraph, options, lang_out)
                if info is not None:
                    info["mode"] = options.get("mode","joint")
                    info["count_tokens"] = getattr(self,"calc_token_count",None)
                _local.pair = info
                try:
                    return original(self, batch_paragraph, *args, **kwargs)
                finally:
                    _local.pair = None

            return translate_paragraph

        if registry.wrap_method(cls, "translate_paragraph", _factory, "cross_page"):
            registry.note_applied(f"cross-page batches annotated ({options.get('mode', 'join')})")

    def patch_input(module):
        def factory(original):
            def get_input(self, paragraph, page_font_map=None, disable_rich_text_translate=None):
                pair = getattr(_local, "pair", None)
                if pair and pair.get("directive") and pair.get("mode") == "joint":
                    # Use the paragraph base font. Formula placeholders remain protected.
                    disable_rich_text_translate = True
                return original(self, paragraph, page_font_map, disable_rich_text_translate)
            return get_input
        registry.wrap_method(module.ILTranslator, "get_translate_input", factory, "joint_base_style")
    registry.on_import("babeldoc.format.pdf.document_il.midend.il_translator", patch_input)
    registry.on_import(target, _patch)
    llm_hook.register_hook(llm_hook.PRIORITY_CROSS_PAGE, _hook)


def stats() -> dict:
    with _stats_lock:
        return dict(_stats)
