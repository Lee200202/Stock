"""來源修復的長段落時間證據須綁定主體、稿件及文字；不擴大一般模型引用窗口。"""
import hashlib,json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'))
os.environ.setdefault('SPREADSHEET_ID','offline');os.environ.setdefault('GEMINI_API_KEY','offline')
import pipeline as pl
def sha(t):return hashlib.sha256(t.encode()).hexdigest()
class ReviewedContext(unittest.TestCase):
 def test_bound_context_and_stale_or_foreign_binding(self):
  raw='甲股已有部位。'+'補充說明。'*180+'去年全年EPS29，今年上半年EPS21.83。'
  text='甲股去年EPS29，上半年EPS21.83，續抱。'
  row={'name':'甲股','code':'1111'}
  with patch.object(pl,'_name_adjacent_text',return_value='甲股已有部位。'),patch.object(pl,'_name_forward_text',return_value='甲股已有部位。'):
   self.assertEqual(set(pl._unsupported_note_time(text,row,raw)),{'去年','上半年'})
   bound={'code':'1111','subject_reviewed':True,'source_sha256':sha(raw),'text_sha256':sha(text),'quotes':[raw]}
   row['_reviewed_note_context']=bound
   self.assertEqual(pl._unsupported_note_time(text,row,raw),[])
   for key,value in [('code','2222'),('source_sha256',sha(raw+'改稿')),('text_sha256',sha(text+'改文')),('subject_reviewed',False),('quotes',['原稿沒有的去年與上半年'])]:
    row['_reviewed_note_context']=dict(bound,**{key:value})
    self.assertEqual(set(pl._unsupported_note_time(text,row,raw)),{'去年','上半年'})
 def test_october27_reviewed_note_plan_keeps_actual_technical_facts(self):
  plan=json.loads((ROOT/'scripts/golden/2026-10-27.json').read_text(encoding='utf-8'))
  for item in plan['reviewed_note_variants']:
   raw='\n'.join(item['quotes']);text=item['text'];row={'code':item['code'],'name':plan['names'][item['code']],
     '_reviewed_note_context':{'code':item['code'],'subject_reviewed':True,'source_sha256':sha(raw),'text_sha256':sha(text),'quotes':item['quotes']}}
   self.assertEqual(pl._unsupported_note_time(text,row,raw),[],item['code'])
  notes=[i['text'] for i in plan['reviewed_note_variants'] if i['code']=='8210']
  for note in notes:
   for fact in ['1,590','假破底','兩天','21.83','40','上半年']:self.assertIn(fact,note)
   self.assertNotIn('785',note)
if __name__=='__main__':unittest.main()
