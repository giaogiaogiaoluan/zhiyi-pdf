"""Reproduce source/instruction confusion in the real fallback shape."""
import sys,json,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'engine'))
from pdf2rea_engine import llm_hook,terminology,cross_page,joint_paragraph,response_guard,numeric_policy
class Boundary(unittest.TestCase):
 def tearDown(self):
  llm_hook.reset_hooks();cross_page._local.pair=None
 def test_plain_fallback_is_byte_identical(self):
  class Engine:
   def llm_translate(self,body):return body
  terminology.install({'numeric_policy':'arabic'})
  source='Translate English to Chinese.\nNow translate the following text:\n2. Materials and Methods'
  self.assertEqual(llm_hook._EngineProxy(Engine(),None).llm_translate(source),source)
 def test_rules_precede_source(self):
  terminology.install({'numeric_policy':'arabic'})
  prompt=llm_hook.INPUT_MARKER+'\n'+json.dumps([{'id':0,'input':'Macronutrients'}])
  result=llm_hook.apply_hooks(llm_hook.PromptContext(prompt,llm_hook.parse_items(prompt)))
  self.assertLess(result.index('Terminology'),result.index(llm_hook.INPUT_MARKER))
  self.assertEqual(llm_hook.parse_items(result),llm_hook.parse_items(prompt))
 def test_joint_rules_precede_single_input(self):
  cross_page._local.pair={'directive':'fallback directive','mode':'joint'}
  source=[{'id':0,'input':'New op-'},{'id':1,'input':'portunities to improve health.'}]
  ctx=llm_hook.PromptContext(llm_hook.INPUT_MARKER+json.dumps(source),source)
  result=cross_page._hook(ctx)
  self.assertLess(result.index('Whole-Paragraph'),result.index(llm_hook.INPUT_MARKER))
  self.assertEqual(len(llm_hook.parse_items(result)),1)
 def test_guard(self):
  self.assertEqual(response_guard.rejection('Macronutrients','宏量营养素 PDF2REA规则'),'instruction_echo')
  self.assertEqual(response_guard.rejection('Title','测'*250),'short_field_overflow')
  self.assertIsNone(response_guard.rejection('Results of the study','本研究的结果'))
 def test_lexical_join_and_counts(self):
  self.assertEqual(joint_paragraph.join_text('new op-','portunities to improve'),'new opportunities to improve')
  self.assertEqual(joint_paragraph.join_text('new op-','portunitiesto improve'),'new opportunities to improve')
  self.assertEqual(joint_paragraph.join_text('a cost-','effective option'),'a cost-effective option')
  ledger=numeric_policy.NumericLedger();ledger.policy='arabic'
  self.assertEqual(numeric_policy.normalize('进一步进行三次试验，一次性完成。',ledger),'进一步进行3次试验，一次性完成。')
if __name__=='__main__':unittest.main(verbosity=2)
