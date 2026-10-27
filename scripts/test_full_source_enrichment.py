"""說明切窗只供定位，完整來源及同義技術描述不可被局部詞表刪除。"""
import json,unittest
from unittest.mock import patch
from test_source_fact_ledger import p

class FullSourceEnrichment(unittest.TestCase):
 def test_global_quote_can_support_a_financial_clause_outside_the_short_hint(self):
  raw='甲公司目前續抱。'+('中間說明很長。'*50)+'甲公司半年報21.83，外資連買兩天。'
  row={'name':'甲公司','code':'1111','note':'甲公司續抱。'}
  def model(system,payload,**kw):
   data=json.loads(payload);parts=data['full_source']
   ids=[k for k,v in parts.items() if '21.83' in v]
   self.assertTrue(ids)
   return json.dumps({'notes':[dict(id='holdings:0',sentences=[dict(text='甲公司上半年EPS為21.83，外資連續兩天買超。',source_ids=ids)],limitation='')]})
  with patch.object(p,'_own_segments',return_value=['甲公司目前續抱。']),patch.object(p,'_named_current_prohibition',return_value=''),patch.object(p,'stock_themes',return_value=[]),patch.object(p,'unnamed_cost_passages',return_value={}),patch.object(p,'call_gemini',side_effect=model),patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}):
   p.enrich_stock_context({'holdings':[row]},raw,'2026/10/27')
  self.assertIn('21.83',row['note']);self.assertIn('買超',row['note'])

if __name__=='__main__':unittest.main()
