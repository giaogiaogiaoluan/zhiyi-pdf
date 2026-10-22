"""Reject leaked engine instructions before they are squeezed into a PDF box."""
from __future__ import annotations
import re
import threading
from . import registry
_lock = threading.Lock()
_stats = {'rejected': 0, 'instruction_echo': 0, 'short_field_overflow': 0}


def rejection(source, output):
    if not isinstance(output, str):
        return None
    if 'pdf2rea' in output.lower() and 'pdf2rea' not in source.lower():
        return 'instruction_echo'
    visible = lambda s: re.sub(r'<[^>]+>|\{\{[^}]+\}\}', '', s).strip()
    n, m = len(visible(source)), len(visible(output))
    if n <= 80 and m > max(200, 6*n):
        return 'short_field_overflow'
    return None


def install(options):
    def patch(module):
        def factory(original):
            def post(self, paragraph, tracker, translate_input, translated_text):
                reason = rejection(paragraph.unicode or '', translated_text)
                if reason:
                    with _lock:
                        _stats['rejected'] += 1
                        _stats[reason] += 1
                    # Preserve the original glyphs. Raising here can trigger a
                    # single-fragment retranslation and undo joint translation.
                    return False
                return original(self, paragraph, tracker, translate_input, translated_text)
            return post
        registry.wrap_method(module.ILTranslator, 'post_translate_paragraph', factory, 'response_guard')
    registry.on_import('babeldoc.format.pdf.document_il.midend.il_translator', patch)


def stats():
    with _lock:
        return dict(_stats)
