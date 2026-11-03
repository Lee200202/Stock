"""ph9引用與背景角色的通用正反例，不使用股票答案或方向字詞表。"""
import copy,hashlib,unittest
from test_source_fact_ledger import p

class EvidenceContract(unittest.TestCase):
 def answer(self,ids,parts,quote):
  return {'about_itself':True,'independent_analysis':True,'content_role':'independent_analysis',
   'entry_for_new':'wait_condition','now':'conditional','tone':'neutral','quotes':{'stance':[quote]},
   'quote_source_ids':{'stance':ids},'_verified_quote_sources':{'stance':parts}}

 def test_adjacent_source_quote_keeps_the_model_excerpt_not_whole_paragraph(self):
  a=self.answer(['S0031','S0032'],['乙公司今日已買。甲公司等待','止穩再評估，尚未出手。'],'甲公司等待止穩再評估')
  self.assertEqual(p._questionnaire_verified_quotes(a,'stance'),['甲公司等待止穩再評估'])
  self.assertEqual(p.verdict_class(a,'甲公司等待止穩再評估。',['甲公司'])[0],'watch_watch')

 def test_nonadjacent_or_reversed_or_different_notices_do_not_join(self):
  for ids in (['S0031','S0033'],['S0032','S0031'],['N0001','N0002'],['S0031','N0001']):
   a=self.answer(ids,['甲公司等待','止穩再評估。'],'甲公司等待止穩再評估')
   self.assertEqual(p._questionnaire_verified_quotes(a,'stance'),[])

 def test_invalid_excerpt_never_falls_back_to_a_real_but_different_source(self):
  a=self.answer(['S0031'],['乙公司創新高；甲公司只作比較。'],'甲公司創歷史新高並突破')
  self.assertEqual(p._questionnaire_verified_quotes(a,'stance'),[])
  self.assertTrue(p._questionnaire_issues(a,'乙公司創新高；甲公司只作比較。',['甲公司']))

 def test_background_with_explicit_no_analysis_can_vote_ignored_without_a_stance(self):
  a=dict(about_itself=True,independent_analysis=False,content_role='selection_scope',now='none',tone='neutral',
   entry_for_new='none',selection_intent='explicit_exclusion',buy_today=False,sell_today=False,holding_now=False,past_trade=False,quotes={})
  self.assertEqual(p.verdict_class(a,p._ev_norm('甲公司只是名單對照。'),['甲公司'])[0],'ignored')
  missing=copy.deepcopy(a);missing.pop('independent_analysis')
  self.assertEqual(p.verdict_class(missing,p._ev_norm('甲公司只是名單對照。'),['甲公司'])[0],'')

 def test_background_rise_is_not_promoted_only_by_a_bullish_tone_boolean(self):
  raw='甲公司今天漲停，接著討論乙公司。'
  a=dict(about_itself=True,independent_analysis=True,content_role='comparison_only',now='none',tone='bullish',
   entry_for_new='none',holder_advice='none',selection_intent='neutral',quotes={'analysis':[raw],'stance':[raw]})
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'ignored')
  a['content_role']='independent_analysis'
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_watch')

 def test_no_technical_analysis_does_not_erase_a_real_standalone_prohibition(self):
  raw='甲公司現階段明確不參與。'
  a=dict(about_itself=True,independent_analysis=False,content_role='independent_analysis',now='prohibit',tone='bearish',
   entry_for_new='prohibit',selection_intent='explicit_exclusion',quotes={'stance':[raw]})
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')

 def foreign_case(self):
  raw='乙公司股本較小。甲公司只有量能不足，目前未買。';text='甲公司股本較小。'
  ledger=p._validate_source_facts({'facts':[dict(id='F1',subject_codes=['2222'],source_ids=['S0001'],statement='乙公司股本較小')]},raw,{'1111','2222'})
  row=dict(code='1111',name='甲公司',reason=text,_source_fact_ledger=ledger,_source_claim_bindings={
   'source_sha256':hashlib.sha256(raw.encode()).hexdigest(),'claims':{'c0':{
    'text_sha256':hashlib.sha256(p._note_claims(text)[0]['text'].encode()).hexdigest(),
    'relation':'entailed','coverage':'partial','fact_ids':['F1'],'subject_checks':{
     '1111':{'supports_all_details':False,'source_backed':True,'source_ids':['S0001'],'statement':'甲公司只有量能不足'},
     '2222':{'supports_all_details':True,'source_backed':True,'source_ids':['S0001'],'statement':'乙公司股本較小'}}}}})
  item={'source_review':{'claims':[dict(id='c0',status='cross_entity',subject_code='2222',subject='乙公司',source_ids=['S0001'],source_fact_ids=['F1'],explanation='該特徵真正主體是乙公司')]}}
  return raw,text,row,item

 def test_partial_inventory_can_prove_foreign_only_with_explicit_two_subject_contrast(self):
  raw,text,row,item=self.foreign_case()
  self.assertEqual(p._validated_source_review(item,row,text,raw)['status'],'cross_entity')
  row['_source_claim_bindings']['claims']['c0']['subject_checks'].pop('1111')
  self.assertEqual(p._validated_source_review(item,row,text,raw)['status'],'insufficient')

 def test_recheck_completed_flag_does_not_override_a_real_editor_subject_disagreement(self):
  raw,text,row,item=self.foreign_case();row['_source_evidence_recheck']={'status':'completed'}
  item['source_review']['claims'][0].update(status='supported',subject_code='1111',subject='甲公司')
  self.assertEqual(p._validated_source_review(item,row,text,raw)['status'],'insufficient')

 def test_editor_alone_cannot_approve_cross_entity_when_anonymous_binding_is_missing(self):
  raw,text,row,item=self.foreign_case();row['_source_claim_bindings']['claims']={}
  self.assertEqual(p._validated_source_review(item,row,text,raw)['status'],'insufficient')

 def test_negative_subject_check_needs_literal_source_and_identity_evidence(self):
  raw='乙公司股本較小。甲公司沒有買。'
  bad=[dict(subject_code='1111',supports_all_details=False,source_ids=[],statement='甲公司不適用'),
       dict(subject_code='2222',supports_all_details=True,source_ids=['S0001'],statement='乙公司股本較小')]
  checks=p._source_subject_checks(bad,['1111','2222'],raw,{'1111':['甲公司'],'2222':['乙公司']})
  self.assertNotIn('1111',checks);self.assertIn('2222',checks)

if __name__=='__main__':unittest.main()
