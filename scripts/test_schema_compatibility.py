"""HTTP400 schema相容性不能毒化三把金鑰；所有實際POST仍節流及計數。"""
import copy,json,unittest
from contextlib import ExitStack
from unittest.mock import patch
from test_source_fact_ledger import p

class Response:
 def __init__(self,status):
  self.status_code=status;self.headers={}
  self.text=json.dumps({'error':{'status':'INVALID_ARGUMENT','message':'Request contains an invalid argument.'}})
 def json(self):return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'{"verdicts":[]}'}]}}]}

class SchemaCompatibility(unittest.TestCase):
 def context(self,stack,enabled=True):
  state={'idx':0,'dead':set()};calls={'n':0,'by_tag':{},'http':0}
  for key,value in {'GEMINI_KEYS':['offline'],'_KEY_STATE':state,'_SCHEMA_STATE':{'on':enabled,'why':''},
   '_QUOTA_STOP':{'daily':False},'_GEMINI_CALLS':calls,'require_gemini_key':lambda:None,
   'current_gemini_model':lambda:'gemini-3-test','current_gemini_key':lambda:'offline',
   'key_label':lambda *a:'offline-key'}.items():stack.enter_context(patch.object(p,key,value))
  pace=stack.enter_context(patch.object(p,'throttle_gemini'))
  rotate=stack.enter_context(patch.object(p,'rotate_gemini_key',side_effect=AssertionError('參數錯不能換鑰')))
  return state,calls,pace,rotate

 def test_dynamic_then_static_schema_probe_stays_on_same_key_and_counts_http(self):
  requests=[]
  def post(*a,**kw):
   requests.append(copy.deepcopy(kw['json']))
   return Response(400 if len(requests)<3 else 200)
  with ExitStack() as stack:
   state,calls,pace,rotate=self.context(stack)
   stack.enter_context(patch.object(p.requests,'post',side_effect=post))
   self.assertEqual(p._call_gemini_only('system','user',want_json=True,tag='adjudicate-r1',
     response_schema={'type':'object','properties':{'dynamic':{'type':'string'}}}),'{"verdicts":[]}')
   self.assertIn('dynamic',requests[0]['generationConfig']['responseJsonSchema']['properties'])
   self.assertIn('verdicts',requests[1]['generationConfig']['responseJsonSchema']['properties'])
   self.assertNotIn('responseJsonSchema',requests[2]['generationConfig'])
   self.assertEqual(calls['n'],1);self.assertEqual(calls['http'],3);self.assertEqual(pace.call_count,3)
   self.assertFalse(state['dead']);rotate.assert_not_called()

 def test_parameter_error_without_schema_does_not_mark_key_dead(self):
  with ExitStack() as stack:
   state,calls,pace,rotate=self.context(stack,False)
   stack.enter_context(patch.object(p.requests,'post',return_value=Response(400)))
   with self.assertRaisesRegex(RuntimeError,'請求參數不相容'):
    p._call_gemini_only('system','user',want_json=True,tag='adjudicate-r1')
   self.assertEqual(calls['http'],1);self.assertFalse(state['dead']);rotate.assert_not_called()

 def test_invalid_key_is_not_a_schema_fallback(self):
  self.assertFalse(p._schema_rejected(400,'API_KEY_INVALID api key not valid',{'responseJsonSchema':{}}))

if __name__=='__main__':unittest.main()
