"""Offline evidence, source scope and note-only updates. No API or model calls."""
import os,sys,json,hashlib,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));sys.path.insert(0,str(ROOT/'pipeline'))
os.environ.setdefault('GEMINI_API_KEY','AIzaSyDUMMY_local_import_only_0000000000');os.environ.setdefault('SPREADSHEET_ID','test')
import pipeline as pl
from apply_reviewed_notes import plan_notes
from apply_reviewed_points import source_quote
class Reviewed(unittest.TestCase):
 def test_joint_refusal(self):
  for name in ['甲股','乙股']:
   self.assertTrue(pl.shared_list_refusal({'name':name},'你們在講甲股，乙股，什麼？我才不買那一種漲那麼多的股票。'))
   self.assertTrue(pl.shared_list_refusal({'name':name},'甲股、乙股。這類股票我不買。'))
 def test_no_cross_scope(self):
  for text in ['推薦甲股，不推薦乙股。這類股票不買。','甲股、乙股。不是說這類股票不能買。','甲股、乙股。昨天不買那一種股票。','甲股、乙股。有人說不買那一種股票。','甲股、乙股。接著，這類股票不買。','甲股、乙股。如果過季線就不買那一種股票。','甲股、乙股。這一支我不買。']:
   self.assertFalse(pl.shared_list_refusal({'name':'甲股'},text),text)
 def test_only_current_source_note(self):
  head=['日期','股票名稱','代號','方向','理由摘錄','來源影片ID']
  rows=[head,['2026/10/14','甲股','1111','觀望注意','舊文','video'],['2026/10/13','甲股','1111','觀望注意','昨日','video'],['2026/10/14','甲股','1111','觀望不碰','另一類','video'],['2026/10/14','甲股','1111','觀望注意','人工','manual']]
  patches,matched,_,_=plan_notes({'操作紀錄':rows},{'1111':{'category':'watch_watch','text':'新文','quotes':['證據原句']}},'2026/10/14','video')
  self.assertEqual(patches,{'操作紀錄':[{'range':'E2','values':[['新文']]}]});self.assertEqual(matched,{'1111'})
 def test_original_evidence_and_quality(self):
  p=ROOT/'scratch/audit1014-current/original/source.json'
  if not p.exists():self.skipTest('private local audit artifact not present')
  src=json.loads(p.read_text(encoding='utf-8'));raw=src.get('raw') or src.get('transcript');gold=json.loads((ROOT/'scripts/golden/2026-10-14.json').read_text(encoding='utf-8'))
  self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(),gold['reviewed_notes_sha256'])
  signals={}
  for code,item in gold['reviewed_notes'].items():
   self.assertTrue(item['quotes']);quotes=[source_quote(raw,q) for q in item['quotes']]
   signals.setdefault(item['category'],[]).append({'name':gold['names'][code],'code':code,'reason':item['text'],'note':item['text'],'evidence':quotes,'_evidence_verified':True})
  self.assertTrue(pl.shared_list_refusal({'name':'波若威'},raw))
  overview,hard,_=pl.quality_overview(signals,raw,check_chapters=False)
  self.assertFalse(hard,hard);print('\n'.join(overview))
 def test_holder_advice_is_not_owned_position(self):
  self.assertFalse(pl._claims_owned_holding('台化尚未爆量，對已持有部位的建議是續抱。'))
  self.assertTrue(pl._claims_owned_holding('台化會員目前持有，持續續抱。'))
  self.assertTrue(pl._claims_owned_holding('若持有者可以續抱，但會員仍持有這一檔。'))
 def test_future_evidence_cannot_cross_to_other_stock(self):
  raw='甲股回測季線。接著看乙股，乙股下週突破。'
  self.assertEqual(pl._unsupported_note_time('甲股下週突破',{'name':'甲股','evidence':['乙股下週突破'],'_evidence_verified':True},raw),['下週'])
if __name__=='__main__':unittest.main()
