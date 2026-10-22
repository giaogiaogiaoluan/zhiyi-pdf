"""Production regression: actual .pth bootstrap survives multiprocessing spawn.

Run with the private interpreter and --input so the parent is recognized as an
engine command. The child deliberately never calls apply() in its target body.
"""
import json
import multiprocessing
import os
from pathlib import Path
import sys


def worker(queue):
    from pdf2rea_engine import is_installed, __version__
    from pdf2rea_engine import formula_guard
    from babeldoc.format.pdf.document_il import PdfCharacter, PdfStyle
    from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
    from types import SimpleNamespace as NS
    # Exercise a real wrapped method, not just the presence of imported hooks.
    formula = NS(pdf_character=[PdfCharacter(char_unicode='12',pdf_style=PdfStyle(font_size=10))],y_offset=0)
    StylesAndFormulas.is_translatable_formula(NS(),formula)
    queue.put({'pid':os.getpid(),'installed':is_installed(),'version':__version__})


if __name__=='__main__':
    assert os.environ.get('PDF2REA_APPLY_EAGERLY') is None
    from pdf2rea_engine import is_installed
    assert is_installed(), 'parent .pth was not activated'
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue();p=ctx.Process(target=worker,args=(queue,));p.start();result=queue.get(timeout=60);p.join(60)
    assert p.exitcode==0 and result['installed'],result
    state=json.loads((Path(os.environ['PDF2REA_CONFIG']).parent/'pdf2rea.status.json').read_text())
    assert result['pid'] in state['processes'],state
    assert any('is_translatable_formula' in v for v in state['patches']),state
    print('Production .pth + spawn: child patches active and child exit status persisted; passed')
