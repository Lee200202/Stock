"""矛盾／錯引可同票補核一次；不提供期待分類、不增加投票票數。"""
import copy,json,unittest
from unittest.mock import patch
from test_source_fact_ledger import p

class QuestionnaireRepair(unittest.TestCase):
 def run_case(self,correct):
  raw='甲公司往季線走，後續可以留意。';item=('1111',{'name':'甲公司','heard':['甲公司'],'known':[],'passages':[]})
  wrong={'id':'1111','about_itself':False,'content_role':'comparison_only','independent_analysis':True,
   'entry_for_new':'wait_condition','now':'conditional','tone':'bullish','quotes':{'analysis':[raw],'stance':[raw]},'quote_source_ids':{'analysis':['S0001'],'stance':['S0001']}}
  calls=[]
  def model(system,payload,**kw):
   d=json.loads(payload);calls.append(d)
   self.assertNotIn('expected_categories',payload);self.assertNotIn('watch_watch',payload)
   if len(calls)==2:
    self.assertTrue(d['stocks'][0]['answer_validation']['issues'])
   answer=copy.deepcopy(wrong)
   if correct and len(calls)==2:answer.update(about_itself=True,content_role='independent_analysis')
   return json.dumps({'verdicts':[answer]})
  with patch.object(p,'call_gemini',side_effect=model),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_DAY_SOURCE_CONTEXT',{}),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}):
   result=p._ask_verdicts([item],'2026/10/27',0,raw)
  return result['1111'],calls,raw

 def test_repair_replaces_one_vote_and_preserves_the_original(self):
  answer,calls,raw=self.run_case(True)
  self.assertEqual(len(calls),2);self.assertTrue(answer['_answer_repair']['replaces_same_vote'])
  self.assertFalse(answer['_answer_repair']['previous_answer']['about_itself'])
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'watch_watch')

 def test_still_inconsistent_answer_is_invalid_and_never_retries_forever(self):
  answer,calls,raw=self.run_case(False)
  self.assertEqual(len(calls),2);self.assertTrue(answer['_validation_issues'])
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'')

 def test_transport_failure_is_not_retried_as_a_content_repair(self):
  item=('1111',{'name':'甲公司','heard':[],'known':[],'passages':[]})
  with patch.object(p,'call_gemini',side_effect=RuntimeError('request configuration rejected')) as model,patch.object(p,'budget_left',return_value=9999),patch.object(p,'_DAY_SOURCE_CONTEXT',{}),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}):
   self.assertEqual(p._ask_verdicts([item],'2026/10/27',0,'甲公司有提及。'),{})
  self.assertEqual(model.call_count,1)

if __name__=='__main__':unittest.main()
