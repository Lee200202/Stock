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
 def test_notice_facts_are_original_and_expire_when_notice_changes(self):
  raw='甲公司今日名稱被聽錯。';notice='甲公司目前會員持股續抱。'
  context={'transcript_sha256':hashlib.sha256(raw.encode()).hexdigest(),'notices':[{'original':notice}]}
  with patch.object(p,'_DAY_SOURCE_CONTEXT',context):
   facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['1111'],source_ids=['N0001'],statement='甲公司續抱')]},raw,{'1111'})
   self.assertEqual(facts['facts']['F1']['source_quote'],notice)
   row={'code':'1111','name':'甲公司','reason':'甲公司會員持股續抱。','_source_fact_ledger':facts}
   reply={'source_review':{'claims':[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_ids=['N0001'],source_fact_ids=['F1'],explanation='當日原通知本人續抱')]}}
   review=p._validated_source_review(reply,row,row['reason'],raw)
   self.assertEqual(review['status'],'supported');row['_source_attribution_review']=review
   self.assertTrue(p._current_source_review(row,raw,'reason'))
   context['notices'][0]['original']='甲公司已全數賣出。'
   self.assertFalse(p._current_source_review(row,raw,'reason'))

 def test_wrong_query_echo_cannot_bind_a_claim(self):
  facts=ledger();row={'code':'1111','name':'甲公司','reason':'甲公司股本比較小。'}
  reply={'additional_facts':[],'matches':[dict(id='Q0001',query_text='另一項主張',entails_all_details=True,relation='entailed',coverage='complete',fact_ids=['F1'])]}
  with patch.object(p,'call_gemini',return_value=json.dumps(reply)),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_CODE_MAP',{'1111':'甲公司','2222':'乙公司'}):
   p.align_anonymous_claims({'watch_watch':[row]},RAW,facts)
  self.assertFalse(row['_source_claim_bindings']['claims'])

 def test_different_price_fact_cannot_force_a_cross_stock_deletion(self):
  raw='乙公司從4400跌至3500。甲公司的走勢另待確認。'
  facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['2222'],source_quote=raw,subject_quote=raw,statement='乙公司4400跌至3500')]},raw,{'1111','2222'})
  text='甲公司從2685跌至1750。';claim=p._note_claims(text)[0]
  row={'code':'1111','name':'甲公司','reason':text,'_source_fact_ledger':facts,
    '_source_claim_bindings':{'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),'claims':{'c0':dict(relation='entailed',coverage='complete',text_sha256=hashlib.sha256(claim['text'].encode()).hexdigest(),fact_ids=['F1'])}}}
  review=p._validated_source_review({},row,text,raw)
  self.assertEqual(review['status'],'insufficient')
  self.assertFalse(p._apply_source_claim_review({},row,'reason',review));self.assertEqual(row['reason'],text)

 def test_incomplete_index_can_be_extended_but_partial_search_cannot_exclude_target(self):
  for coverage in ('partial','complete'):
   facts=ledger();row={'code':'1111','name':'甲公司','reason':'甲公司股本比較小。','_source_fact_ledger':facts}
   def answer(system,payload,**kwargs):
    q=json.loads(payload)['queries'][0]
    return json.dumps({'additional_facts':[], 'matches':[dict(q,query_text=q['text'],entails_all_details=True,relation='entailed',coverage=coverage,fact_ids=['F1'])]})
   with patch.object(p,'call_gemini',side_effect=answer),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_CODE_MAP',{'1111':'甲公司','2222':'乙公司'}):
    p.align_anonymous_claims({'watch_watch':[row]},RAW,facts)
   r=p._validated_source_review({'source_review':{'claims':[]}},row,row['reason'],RAW)
   self.assertEqual(r['status'],'cross_entity' if coverage=='complete' else 'insufficient')

 def test_draft_subject_copy_cannot_override_independent_source_subject(self):
  row={'name':'甲公司','code':'1111','reason':'甲公司股本比較小，目前尚未見低檔成交量。','_source_fact_ledger':ledger()}
  replies=[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='錯把草稿當主體'),
   dict(id='c1',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F2'],explanation='本股自己的量能')]
  review=p._validated_source_review({'source_review':{'claims':replies}},row,row['reason'],RAW)
  self.assertEqual([c['status'] for c in review['claims']],['insufficient','supported'])
  # 編輯與匿名主體互相矛盾，不能直接當跨股證實；短文投影也須再審。
  signals={};self.assertTrue(p._apply_source_claim_review(signals,row,'reason',review))
  self.assertNotIn('股本比較小',row['reason']);self.assertIn('低檔成交量',row['reason'])
  self.assertIn('股本比較小',row['_source_projection']['original_text'])
  self.assertTrue(row['_source_projection_pending'])
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
   d=json.loads(payload);self.assertEqual(set(d),{'sources','company_candidates','focus_source_ids'})
   self.assertNotIn('刻意放入',payload);self.assertNotIn('watch_watch',payload)
   return json.dumps({'facts':[]})
  with patch.object(p,'_CODE_MAP',{'1111':'甲公司','2222':'乙公司'}),patch.object(p,'source_inventory',return_value=[]),patch.object(p,'call_gemini',side_effect=fake) as call,patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']):
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
 def test_style_only_rejection_retains_a_source_verified_row(self):
  raw='甲公司目前量縮整理並等待季線止穩。'
  facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['1111'],source_quote=raw,
    subject_quote=raw,statement='甲公司等待季線止穩')]},raw,{'1111'})
  row={'code':'1111','name':'甲公司','reason':raw,'_source_fact_ledger':facts}
  claim=dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=raw,
    source_fact_ids=['F1'],explanation='這項內容確實是甲公司自己的原句')
  reply={'texts':[dict(id='n0',text=raw,meaning_preserved=True,category_consistent=True,
    source_supported=True,written_style_ok=False,source_review={'claims':[claim]})]}
  sig={'watch_watch':[row],'_source_fact_ledger':facts}
  with patch.object(p,'call_gemini',return_value=json.dumps(reply)),patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']):
   p._polish_public_texts(sig,raw,'2026/10/27')
  self.assertEqual(row['reason'],raw)
  self.assertEqual(p._current_source_review(row,raw,'reason')['status'],'supported')
  _,hard,_=p.quality_overview(sig,raw,check_chapters=False)
  self.assertFalse(any('來源' in x or '審閱' in x for x in hard),hard)
 def test_anonymous_alignment_cannot_copy_draft_subject_or_borrow_unrelated_fact(self):
  row={'name':'甲公司','code':'1111','reason':'甲公司股本比較小，目前尚未見低檔成交量。','_source_fact_ledger':ledger()}
  def align(system,payload,**kw):
   data=json.loads(payload)
   self.assertNotIn('甲公司',json.dumps(data['queries'],ensure_ascii=False))
   self.assertNotIn('watch_watch',payload)
   return json.dumps({'additional_facts':[],'matches':[dict(id=q['id'],query_text=q['text'],entails_all_details=True,relation='entailed',coverage='complete',fact_ids=['F1' if i==0 else 'F2']) for i,q in enumerate(data['queries'])]})
  with patch.object(p,'call_gemini',side_effect=align),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_CODE_MAP',{'1111':'甲公司','2222':'乙公司'}):
   p.align_anonymous_claims({'watch_watch':[row]},RAW,ledger())
  replies=[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='編輯抄錯股名'),
   dict(id='c1',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='編輯抄錯事實ID')]
  review=p._validated_source_review({'source_review':{'claims':replies}},row,row['reason'],RAW)
  self.assertEqual([c['status'] for c in review['claims']],['insufficient','supported'])
  self.assertEqual(review['claims'][1]['source_fact_ids'],['F2'])
  row['_source_claim_bindings']['source_sha256']='changed'
  review=p._validated_source_review({'source_review':{'claims':replies}},row,row['reason'],RAW)
  self.assertTrue(all(c['status']=='insufficient' for c in review['claims']))
 def test_style_rewrite_carries_the_verified_source_fingerprint(self):
  raw='甲公司現在等季線止穩。'
  text='甲公司目前等待季線止穩。'
  facts=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['1111'],source_quote=raw,
    subject_quote=raw,statement='甲公司等待季線止穩')]},raw,{'1111'})
  row={'code':'1111','name':'甲公司','reason':raw,'_source_fact_ledger':facts}
  claim=dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=raw,
    source_fact_ids=['F1'],explanation='本股等待季線止穩')
  reply={'texts':[dict(id='n0',text=text,meaning_preserved=True,category_consistent=True,
    source_supported=True,written_style_ok=True,source_review={'claims':[claim]})]}
  with patch.object(p,'call_gemini',return_value=json.dumps(reply)),patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']):
   p._polish_public_texts({'watch_watch':[row],'_source_fact_ledger':facts},raw,'2026/10/27')
  self.assertEqual(row['reason'],text)
  self.assertEqual(p._current_source_review(row,raw,'reason')['status'],'supported')
  row['reason']='甲公司明天大漲。'
  self.assertFalse(p._current_source_review(row,raw,'reason'))
if __name__=='__main__':unittest.main()
