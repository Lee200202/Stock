"""已證實子句可重組，但不因刪掉未核資料就自動宣告語意／分類通過。"""
import hashlib,json,unittest
from unittest.mock import patch
from test_source_fact_ledger import p

RAW='甲公司量縮整理。甲公司目前未放量。'
OLD='甲公司量縮整理。還有尚未核實的獨立消息。'

def facts():
 return p._validate_source_facts({'facts':[
  dict(id='F1',subject_codes=['1111'],source_quote=RAW,subject_quote=RAW,statement='甲公司量縮整理'),
  dict(id='F2',subject_codes=['1111'],source_quote=RAW,subject_quote=RAW,statement='甲公司目前未放量')]},RAW,{'1111'})

class Ph6Projection(unittest.TestCase):
 def test_missing_initial_reply_does_not_consume_projection_confirmation(self):
  ledger=facts();row={'code':'1111','name':'甲公司','reason':OLD,'_source_fact_ledger':ledger,
   '_source_claim_bindings':{'source_sha256':hashlib.sha256(RAW.encode()).hexdigest(),'claims':{}}}
  sig={'watch_watch':[row],'_source_fact_ledger':ledger};calls=[]
  def model(system,payload,**kw):
   entries=json.loads(payload)['texts'];calls.append(entries)
   schema=kw['response_schema']['properties']['texts'];self.assertEqual(schema['minItems'],len(entries))
   self.assertEqual(schema['items']['properties']['id']['enum'],[e['id'] for e in entries])
   if len(calls)==1:return '{"texts":[]}'
   e=entries[0];claims=[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='本股量縮整理')]
   if len(calls)==2:claims.append(dict(id='c1',status='insufficient',explanation='消息無據'))
   return json.dumps({'texts':[dict(id=e['id'],text=e['text'],meaning_preserved=True,category_consistent=True,
     source_supported=len(calls)==3,written_style_ok=True,source_review={'claims':claims})]})
  with patch.object(p,'call_gemini',side_effect=model),patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']),patch.object(p,'_DAY_SOURCE_CONTEXT',{}):
   p._polish_public_texts(sig,RAW,'2026/10/27')
  self.assertEqual(len(calls),3);self.assertFalse(row['_source_projection_pending'])
  self.assertEqual(row['reason'],'甲公司量縮整理。')

 def test_duplicate_reply_ids_cannot_approve_a_row_or_retry_forever(self):
  ledger=facts();row={'code':'1111','name':'甲公司','reason':'甲公司量縮整理。','_source_fact_ledger':ledger}
  reply=dict(id='n0',text=row['reason'],meaning_preserved=True,category_consistent=True,source_supported=True,written_style_ok=True,
    source_review={'claims':[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='本股') ]})
  with patch.object(p,'call_gemini',return_value=json.dumps({'texts':[reply,reply]})) as model,patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']):
   p._polish_public_texts({'watch_watch':[row],'_source_fact_ledger':ledger},RAW,'2026/10/27')
  self.assertEqual(model.call_count,2);self.assertNotIn('_source_attribution_review',row)

 def test_alignment_diagnostics_distinguish_echo_format_from_incomplete_support(self):
  for complete in (True,False):
   ledger=facts();row={'code':'1111','name':'甲公司','reason':'甲公司量縮整理。'}
   def model(system,payload,**kw):
    query=json.loads(payload)['queries'][0]
    return json.dumps({'additional_facts':[],'matches':[dict(id=query['id'],query_text=' '+query['text']+'\n',
      entails_all_details=complete,relation='entailed',coverage='complete',fact_ids=['F1'])]})
   with patch.object(p,'call_gemini',side_effect=model),patch.object(p,'budget_left',return_value=9999),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}):
    p.align_anonymous_claims({'watch_watch':[row]},RAW,ledger)
   self.assertTrue(ledger['alignment_checks'][0]['query_echo_match'])
   self.assertEqual(bool(row['_source_claim_bindings']['claims']),complete)

 def run_review(self,confirmed=True):
  ledger=facts();row={'code':'1111','name':'甲公司','reason':OLD,'_source_fact_ledger':ledger,
    '_source_claim_bindings':{'source_sha256':hashlib.sha256(RAW.encode()).hexdigest(),'claims':{}}}
  sig={'watch_watch':[row],'_source_fact_ledger':ledger};calls=[]
  def answer(system,payload,**kwargs):
   entries=json.loads(payload)['texts'];calls.append([e['text'] for e in entries]);out=[]
   for e in entries:
    claims=[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F1'],explanation='本股獨立事實')]
    if len(calls)==1:claims.append(dict(id='c1',status='insufficient',source_fact_ids=[],explanation='此消息未核實'))
    out.append(dict(id=e['id'],text=e['text'],meaning_preserved=True,category_consistent=confirmed,
      written_style_ok=True,source_supported=len(calls)>1,source_review={'claims':claims}))
   return json.dumps({'texts':out})
  with patch.object(p,'call_gemini',side_effect=answer),patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']),patch.object(p,'_DAY_SOURCE_CONTEXT',{}):
   p._polish_public_texts(sig,RAW,'2026/10/27')
  return row,sig,calls

 def test_supported_projection_is_independently_reviewed_and_original_is_preserved(self):
  row,sig,calls=self.run_review()
  self.assertEqual(calls,[[OLD],['甲公司量縮整理。']])
  self.assertEqual(row['reason'],'甲公司量縮整理。')
  self.assertEqual(row['_source_projection']['original_text'],OLD)
  self.assertEqual(row['_source_projection']['input_review']['status'],'insufficient')
  self.assertEqual(row['_source_projection']['withheld_claims'][0]['status'],'insufficient')
  self.assertFalse(row['_source_projection_pending'])
  self.assertEqual(p._current_source_review(row,RAW,'reason')['status'],'supported')
  self.assertFalse(p.quality_overview(sig,RAW,check_chapters=False)[1])

 def test_projection_without_category_confirmation_stays_blocked(self):
  row,sig,calls=self.run_review(False)
  self.assertTrue(row['_source_projection_pending'])
  self.assertTrue(any('投影' in x for x in p.quality_overview(sig,RAW,check_chapters=False)[1]))

 def test_missing_alignment_preserves_ids_without_accepting_unrelated_own_fact(self):
  row={'code':'1111','name':'甲公司','_source_fact_ledger':facts(),'_source_claim_bindings':{'claims':{}}}
  reply={'source_review':{'claims':[dict(id='c0',status='supported',subject='甲公司',subject_code='1111',source_quote=RAW,source_fact_ids=['F2'],explanation='錯用本股的另一項事實')]}}
  r=p._validated_source_review(reply,row,'甲公司量縮整理。',RAW)
  self.assertEqual(r['status'],'insufficient');self.assertEqual(r['claims'][0]['proposed_fact_ids'],['F2'])
  reply['source_review']['claims'][0]['source_fact_ids']=['F1']
  self.assertEqual(p._validated_source_review(reply,row,'甲公司量縮整理。',RAW)['status'],'supported')

 def test_all_unknown_claims_do_not_create_a_verified_projection(self):
  row={'reason':'甲公司有未知消息。'};review={'status':'insufficient','claims':[{'id':'c0','text':row['reason'],'status':'insufficient'}],
    'text_sha256':hashlib.sha256(row['reason'].encode()).hexdigest()}
  self.assertFalse(p._apply_source_claim_review({},row,'reason',review));self.assertNotIn('_source_projection',row)

if __name__=='__main__':unittest.main()
