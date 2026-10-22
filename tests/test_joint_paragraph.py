#!/usr/bin/env python3
"""Regressions for complete-paragraph requests, safe layout and honest fallback."""
from __future__ import annotations
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from pdf2rea_engine import cross_page, joint_paragraph as joint, llm_hook


def items(a='The model converges when the learning rate is',b='set below one in all of our experiments.'):
    return [{'id':0,'input':a,'layout_label':'text'},{'id':1,'input':b,'layout_label':'text'}]


class JointTests(unittest.TestCase):
    def test_one_input_without_page_boundary(self):
        request=joint.prepare(items())
        self.assertEqual(request.item['input'],'The model converges when the learning rate is set below one in all of our experiments.')
        result=json.loads(request.restore(json.dumps([{'id':0,'output':'在我们的所有实验中，当学习率设置为一以下时，模型都能够稳定收敛。'}],ensure_ascii=False)))
        self.assertEqual([x['id'] for x in result],[0,1])
        self.assertEqual(''.join(x['output'] for x in result),'在我们的所有实验中，当学习率设置为一以下时，模型都能够稳定收敛。')

    def test_formula_ids_and_style_tokens_do_not_collide(self):
        source=items("<style id='1'>Alpha</style> {v1} remains stable while", "<style id='1'>Beta</style> {v1} changes gradually.")
        request=joint.prepare(source)
        self.assertEqual(len(set(t for m in request.token_maps for t in m)),6)
        result=json.loads(request.restore(json.dumps([{'id':0,'output':request.item['input'].replace('Alpha','阿尔法').replace('Beta','贝塔')}],ensure_ascii=False)))
        for output in result:
            self.assertEqual(output['output'].count('{v1}'),1)
            self.assertEqual(output['output'].count("<style id='1'>"),1)
            self.assertEqual(output['output'].count('</style>'),1)
        self.assertNotIn('PDF2REA_P',json.dumps(result))

    def test_missing_duplicate_reordered_tokens_rejected(self):
        request=joint.prepare(items('Alpha {v1} remains stable while','Beta {v1} changes gradually.'))
        tokens=[t for m in request.token_maps for t in m]
        text=request.item['input']
        for invalid in [text.replace(tokens[0],''),text+tokens[0],text.replace(tokens[0],'TEMP').replace(tokens[1],tokens[0]).replace('TEMP',tokens[1])]:
            with self.subTest(invalid=invalid),self.assertRaises(joint.JointResponseError):
                request.restore(json.dumps([{'id':0,'output':invalid}]))

    def test_bad_shapes_and_empty_outputs_rejected(self):
        request=joint.prepare(items())
        for output in ['[]','not json', '[{"id":0,"output":""}]','[{"id":9,"output":"text"}]','[{"id":0,"output":[]}]']:
            with self.subTest(output=output),self.assertRaises(joint.JointResponseError):request.restore(output)

    def test_hyphenated_word_and_soft_hyphen(self):
        self.assertEqual(joint.join_text('cost-','effective'),'cost-effective')
        self.assertEqual(joint.join_text('trans\u00ad','lation'),'translation')

    def test_json_span_does_not_swallow_trailing_rules(self):
        prompt='head\n'+llm_hook.INPUT_MARKER+'\n'+json.dumps(items())+'\nRule: preserve [x] and ] symbols.'
        self.assertEqual(len(llm_hook.parse_items(prompt)),2)
        context=llm_hook.PromptContext(prompt,items())
        replaced=context.replace_items([joint.prepare(items()).item])
        self.assertEqual(len(llm_hook.parse_items(replaced)),1)
        self.assertTrue(replaced.endswith('Rule: preserve [x] and ] symbols.'))

    def test_joint_response_used_or_explicit_context_fallback(self):
        llm_hook.reset_hooks();llm_hook.register_hook(20,cross_page._hook)
        source=items('Alpha {v1} remains stable while','Beta {v1} changes gradually.')
        prompt=llm_hook.INPUT_MARKER+'\n'+json.dumps(source)
        class Engine:
            def __init__(self,invalid):self.calls=[];self.invalid=invalid
            def llm_translate(self,prompt,**kwargs):
                self.calls.append(prompt);data=llm_hook.parse_items(prompt)
                if len(data)==1 and self.invalid:return '[{"id":0,"output":"lost tokens"}]'
                return json.dumps([{'id':x['id'],'output':x['input']} for x in data])
        try:
            for invalid in [False,True]:
                cross_page._local.pair={'directive':'Use context, preserve original ids','count_tokens':len}
                engine=Engine(invalid);response=llm_hook._EngineProxy(engine,None).llm_translate(prompt)
                self.assertEqual(len(llm_hook.parse_items(engine.calls[0])),1)
                self.assertEqual(len(engine.calls),2 if invalid else 1)
                self.assertEqual([x['id'] for x in json.loads(response)],[0,1])
                if invalid:self.assertEqual(len(llm_hook.parse_items(engine.calls[1])),2)
        finally:cross_page._local.pair=None;llm_hook.reset_hooks()

    def test_layout_supports_complete_sentences_but_rejects_new_paragraphs(self):
        box=lambda x,y,x2,y2:NS(x=x,y=y,x2=x2,y2=y2)
        chars=[NS(box=box(40,y,500,y+10),char_unicode='body') for y in [50,65]]
        first=NS(unicode='This is a full sentence at the page edge.',layout_label='text',box=box(40,50,500,130),pdf_style=NS(font_size=10),pdf_paragraph_composition=[NS(pdf_line=NS(pdf_character=chars))])
        second=NS(unicode='The next sentence still belongs to this paragraph.',layout_label='text',box=box(40,710,500,750),pdf_style=NS(font_size=10),first_line_indent=False)
        pages=[NS(page_number=i,cropbox=NS(box=box(0,0,600,800))) for i in [0,1]]
        batch=NS(paragraphs=[first,second],pages=pages)
        self.assertTrue(cross_page._describe_batch(batch,{'require_open_ending':False},'zh-CN')['directive'])
        self.assertIsNone(cross_page._describe_batch(batch,{'require_open_ending':True},'zh-CN')['directive'])
        second.first_line_indent=True
        self.assertIsNone(cross_page._describe_batch(batch,{'require_open_ending':False},'zh-CN')['directive'])
        second.first_line_indent=False;chars[0].box.x2=180
        self.assertIsNone(cross_page._describe_batch(batch,{'require_open_ending':False},'zh-CN')['directive'])

    def test_reported_recommendations_fragment_is_one_input(self):
        source=items('which align recommendations','with individual taste profiles while together delivering nutritionally sound and appealing food choices.')
        request=joint.prepare(source)
        self.assertEqual(request.item['input'],'which align recommendations with individual taste profiles while together delivering nutritionally sound and appealing food choices.')
        self.assertNotIn('page',request.item['input'])

    def test_short_body_tail_can_be_recovered_without_accepting_footers(self):
        box=lambda x,y,x2,y2:NS(x=x,y=y,x2=x2,y2=y2)
        body=NS(pdf_style=NS(font_size=11),render_order=10)
        tail=NS(unicode='which align recommendations',layout_label='abandon',box=box(60,60,300,80),pdf_style=NS(font_size=11),render_order=20)
        page=NS(cropbox=NS(box=box(0,0,600,800)),pdf_paragraph=[body,tail])
        translator=NS(_should_translate_paragraph=lambda *a,**k:True)
        self.assertEqual(cross_page._include_boundary_prose(translator,page,[body],set()),[body,tail])
        tail.unicode='Downloaded by University of Testing'
        self.assertEqual(cross_page._include_boundary_prose(translator,page,[body],set()),[body])
        tail.unicode='which align recommendations'
        for smaller_font in [8,10]:
            tail.pdf_style.font_size=smaller_font
            self.assertEqual(cross_page._include_boundary_prose(translator,page,[body],set()),[body])

    def test_non_adjacent_source_pages_never_join(self):
        a,b=(NS(unicode=x['input'],layout_label='text') for x in items())
        batch=NS(paragraphs=[a,b],pages=[NS(page_number=1),NS(page_number=3)])
        self.assertIsNone(cross_page._describe_batch(batch,{},'zh-CN')['directive'])

class LayoutSplitTests(unittest.TestCase):
    def test_sentence_and_decimal_survive(self):
        text = '前面的观察结果表明这一方法值得继续研究。人工智能辅助监测可为营养干预提供新的机遇，准确率为12.34%。'
        req = joint.prepare(items('a'*50, 'b'*70))
        result = json.loads(req.restore(json.dumps([{'id':0,'output':text}], ensure_ascii=False)))
        self.assertEqual(''.join(x['output'] for x in result), text)
        self.assertTrue(result[0]['output'].endswith('。'))
        self.assertTrue(any('12.34%' in x['output'] for x in result))

if __name__=='__main__':unittest.main(verbosity=2)
