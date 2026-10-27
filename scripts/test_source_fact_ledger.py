"""先讀原稿主體再審草稿；不靠最近股名、價位或方向詞判斷。"""
import copy,hashlib,importlib.util,json,os,unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('SPREADSHEET_ID','offline');os.environ.setdefault('GEMINI_API_KEY','offline-test')
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('source_fact_contract',ROOT/'pipeline/pipeline.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
RAW='乙公司股本比較小，拉起來速度比較快。甲公司尚未見低檔成交量，因此目前沒有買進。'
def ledger():
 return p._validate_source_facts({'facts':[
  {'id':'F1','subject_codes':['2222'],'source_quote':'乙公司股本比較小，拉起來速度比較快。','subject_quote':'乙公司股本比較小','statement':'乙公司股本較小且拉升較快'},
  {'id':'F2','subject_codes':['1111'],'source_quote':'甲公司尚未見低檔成交量，因此目前沒有買進。','subject_quote':'甲公司尚未見低檔成交量','statement':'甲公司尚待量能條件'}]},RAW,{'1111','2222'})
class SourceFacts(unittest.TestCase):
 def test_draft_subject_copy_cannot_override_independent_source_subject(self):
  row={'name':'甲公司','code':'1111','reason':'甲公司股本比較小，目前尚未見低檔成交量。','_source_fact_ledger':ledger()}
  replies=[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='錯把草稿當主體'),
   dict(id='c1',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F2'],explanation='本股自己的量能')]
  review=p._validated_source_review({'source_review':{'claims':replies}},row,row['reason'],RAW)
  self.assertEqual([c['status'] for c in review['claims']],['cross_entity','supported'])
  signals={};self.assertTrue(p._apply_source_claim_review(signals,row,'reason',review))
  self.assertNotIn('股本比較小',row['reason']);self.assertIn('低檔成交量',row['reason'])
  self.assertIn('股本比較小',row['_claim_repair']['original'])
 def test_missing_or_stale_fact_id_preserves_input_without_approval(self):
  for facts in [ledger(),dict(ledger(),source_sha256='stale')]:
   row={'name':'甲公司','code':'1111','reason':'甲公司回測季線。','_source_fact_ledger':facts}
   answer={'source_review':{'claims':[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['missing'],explanation='不實編號')]}}
   review=p._validated_source_review(answer,row,row['reason'],RAW)
   self.assertEqual(review['status'],'insufficient');self.assertFalse(p._apply_source_claim_review({},row,'reason',review))
   self.assertEqual(row['reason'],'甲公司回測季線。')
   row['_source_attribution_review']=review
   _,hard,_=p.quality_overview({'watch_watch':[row]},RAW,check_chapters=False)
   self.assertTrue(any('尚未核實' in h for h in hard))
 def test_source_stage_never_receives_draft_class_or_expected_answer(self):
  sig={'watch_watch':[{'code':'1111','name':'甲公司','reason':'這是刻意放入的待審草稿'}]}
  def fake(system,payload,**kwargs):
   d=json.loads(payload);self.assertEqual(set(d),{'sources','company_candidates'})
   self.assertNotIn('刻意放入',payload);self.assertNotIn('watch_watch',payload)
   return json.dumps({'facts':[]})
  with patch.object(p,'source_inventory',return_value=[]),patch.object(p,'call_gemini',side_effect=fake) as call,patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']):
   p.source_claim_ledger(sig,RAW)
  self.assertEqual(call.call_count,1)
 def test_joint_subjects_are_valid_for_both_members(self):
  raw='甲公司和乙公司都回測年線，需要等待止穩。'
  facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['1111','2222'],source_quote=raw,subject_quote=raw,statement='兩檔共同等待止穩')]},raw,{'1111','2222'})
  for code in ['1111','2222']:
   row={'code':code,'name':'甲公司' if code=='1111' else '乙公司','_source_fact_ledger':facts}
   reply=dict(id='c0',status='supported',subject=row['name'],subject_code=code,source_quote=raw,source_fact_ids=['F1'],explanation='共同結論')
   r=p._validated_source_review({'source_review':{'claims':[reply]}},row,row['name']+'等待止穩。',raw)
   self.assertEqual(r['status'],'supported')
 def test_source_ids_restore_original_without_accepting_retyped_quotes(self):
  facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['2222'],source_ids=['S0001'],
    source_quote='模型重寫的不存在文字',subject_quote='',statement='乙公司的股本特徵')]},RAW,{'1111','2222'})
  self.assertEqual(facts['facts']['F1']['source_quote'],RAW)
  self.assertIn(facts['facts']['F1']['subject_quote'],RAW)
  self.assertEqual(facts['rejected_count'],0)
  invalid=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['2222'],source_ids=['不存在'],
    source_quote='模型重寫的不存在文字',subject_quote='',statement='無效事實')]},RAW,{'1111','2222'})
  self.assertFalse(invalid['facts'])
 def test_missing_about_itself_does_not_become_a_negative_vote(self):
  for value in [None,0,'false']:
   answer={'about_itself':value,'quotes':{}}
   self.assertEqual(p.verdict_class(answer,p._ev_norm(RAW))[0],'')
  self.assertEqual(p.verdict_class({'about_itself':False,'quotes':{}},p._ev_norm(RAW))[0],'ignored')
if __name__=='__main__':unittest.main()
