"""主張自帶證據，避免模型外鍵漏填；仍核主體、原文數字與匿名輸入。"""
import json,unittest
from unittest.mock import patch
from test_source_fact_ledger import p

class QueryEvidence(unittest.TestCase):
 def test_direct_mode_does_not_spend_calls_on_a_blind_summary(self):
  sig={'watch_watch':[{'code':'1111','name':'甲公司','reason':'甲公司回測季線。'}]}
  with patch.object(p,'_CODE_MAP',{'1111':'甲公司'}),patch.object(p,'source_inventory',return_value=[]),patch.object(p,'call_gemini') as model:
   ledger=p.source_claim_ledger(sig,'甲公司回測季線。',direct_evidence=True)
  model.assert_not_called();self.assertFalse(ledger['facts'])
  self.assertEqual(ledger['evidence_mode'],'query_owned_source_evidence')

 def test_embedded_evidence_binds_without_a_model_generated_foreign_key(self):
  raw='乙公司上半年EPS21.83元，甲公司量縮整理。'
  for number,valid in [('21.83',True),('83',False),('29',False)]:
   ledger=p._validate_source_facts({},raw,{'1111','2222'})
   ledger['company_candidates']={'1111':['甲公司'],'2222':['乙公司']}
   row={'code':'1111','name':'甲公司','reason':'甲公司上半年EPS21.83元。'}
   def answer(system,payload,**kw):
    d=json.loads(payload);q=d['queries'][0]
    self.assertNotIn('甲公司',q['text']);self.assertNotIn('watch_watch',payload)
    return json.dumps({'matches':[dict(id=q['id'],query_text=q['text'],entails_all_details=True,
      relation='entailed',coverage='complete',evidence=[dict(subject_code='2222',source_ids=['S0001'],statement='乙公司EPS'+number+'元')])]})
   with patch.object(p,'call_gemini',side_effect=answer),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_CODE_MAP',{'1111':'甲公司','2222':'乙公司'}):
    p.align_anonymous_claims({'watch_watch':[row]},raw,ledger)
   self.assertEqual(bool(row['_source_claim_bindings']['claims']),valid)
   if valid:
    ids=row['_source_claim_bindings']['claims']['c0']['fact_ids']
    self.assertEqual(ledger['facts'][ids[0]]['subject_codes'],['2222'])
   else:self.assertEqual(ledger['alignment_checks'][0]['evidence_rejected_count'],1)

 def test_unknown_source_or_subject_cannot_become_verified_evidence(self):
  raw='甲公司量縮整理。'
  for ids,code in [(['bogus'],'1111'),(['S0001'],'9999')]:
   facts=p._validate_source_facts({'facts':[dict(id='E1',subject_codes=[code],source_ids=ids,statement='量縮整理')]},raw,{'1111'})
   self.assertFalse(facts['facts']);self.assertEqual(facts['rejected_count'],1)

if __name__=='__main__':unittest.main()
