#!/usr/bin/env python3
"""Execute BabelDOC's real two-box translation/validation with one joined call."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
import pdf2rea_engine
assert Path(pdf2rea_engine.__file__).resolve().is_relative_to(Path(__file__).resolve().parents[1]/'engine')
from pdf2rea_engine import cross_page, llm_hook
from babeldoc.format.pdf.document_il import PdfParagraph,Page
from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly,BatchParagraph
from babeldoc.format.pdf.document_il.midend.il_translator import PageTranslateTracker,ILTranslator
import tiktoken
llm_hook.reset_hooks();cross_page.install({'require_open_ending':True})
seen=[];applied=[];fallbacks=[]
translated='在我们的所有实验中，当学习率设置为一以下时，模型都能够稳定收敛。'
class Engine:
    def llm_translate(self,prompt,**kwargs):
        data=llm_hook.parse_items(prompt);seen.append(data)
        return json.dumps([{'id':0,'output':translated}],ensure_ascii=False)
class LocalIL:
    def pre_translate_paragraph(self,p,*args):return p.unicode,ILTranslator.TranslateInput(p.unicode,[])
    def post_translate_paragraph(self,p,tracker,ti,text):p.unicode=text;applied.append(text)
    def translate_paragraph(self,*args,**kwargs):raise AssertionError('unexpected per-page fallback')
obj=object.__new__(ILTranslatorLLMOnly)
obj.translation_config=NS(lang_out='zh-CN',raise_if_cancelled=lambda:None,add_formula_placehold_hint=False,disable_same_text_fallback=False)
obj.translate_engine=llm_hook._EngineProxy(Engine(),obj.translation_config)
obj.il_translator=LocalIL();obj._cached_glossaries=[];obj.tokenizer=tiktoken.encoding_for_model('gpt-4o')
obj.ok_count=obj.fallback_count=obj.total_count=0
cases=[
    (['The model converges when the learning rate is','set below one in all of our experiments.'],'在我们的所有实验中，当学习率设置为一以下时，模型都能够稳定收敛。'),
    (['which align recommendations','with individual taste profiles while together delivering nutritionally sound and appealing food choices.'],'这些方法将推荐内容与个人口味偏好相匹配，同时提供营养合理且有吸引力的食物选择。'),
]
for sources,translated in cases:
    seen.clear();applied.clear();fallbacks.clear();obj.ok_count=obj.fallback_count=obj.total_count=0
    paragraphs=[PdfParagraph(unicode=s,layout_label='text',debug_id=str(i)) for i,s in enumerate(sources)]
    batch=BatchParagraph(paragraphs,[Page(page_number=0),Page(page_number=1)],PageTranslateTracker())
    obj.translate_paragraph(batch,pbar=NS(advance=lambda n:None),executor=NS(submit=lambda *a,**k:fallbacks.append((a,k))))
    assert len(seen)==1 and len(seen[0])==1,seen
    assert seen[0][0]['input']==' '.join(sources)
    assert obj.ok_count==2 and obj.fallback_count==0 and not fallbacks,(obj.ok_count,obj.fallback_count)
    assert ''.join(applied)==translated,applied
assert cross_page.stats()['joined']==2,cross_page.stats()
print('Real BabelDOC: two cross-page cases including reported recommendations/with; one input each, intact output, zero page fallbacks; passed')
