"""離線核對證據、來源範圍與僅更新說明的操作，不呼叫API或模型。"""
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
 def test_returned_buy_zone_is_current_advice(self):
  self.assertTrue(pl._returned_buy_zone({'甲股'},'甲股又回來前面的買點了。你們要買，可以買。'))
  for raw in ['以前甲股回到買點。你們要買，可以買。','甲股回來前面的買點了。你們可不可以買？','甲股回來前面的買點了。接著看乙股，你們要買，可以買。','甲股尚未回到买點。你們要買，可以買。']:
   self.assertFalse(pl._returned_buy_zone({'甲股'},raw),raw)
  source=json.loads((ROOT/'scratch/audit1014-current/original/source.json').read_text(encoding='utf8'))['raw']
  self.assertTrue(pl._returned_buy_zone({'鴻海','宏達電'},source))
 def test_sold_record_does_not_hide_new_buy_zone(self):
  # 2026/10/27 管理者：方向不由字詞決定。已出場的部位持有不成立（帳本事實），「回到買點、可以買」交問卷判讀；
  # 問卷引句核對通過才列觀望注意，問不到答案時退回回顧，不由程式憑「可以買」三個字定案。
  text='鴻海又回來前面的買點了。你們要買，可以買。'
  def run(answer):
   signals={k:[] for k in ('buy','sell','holdings','watch_watch','watch_avoid','history','uncertain','ignored')}
   signals['holdings']=[{'name':'鴻海','code':'2317','note':'仍持有','evidence':[]}]
   signals['watch_watch']=[{'name':'鴻海','code':'2317','reason':'回到買點','evidence':[]}]
   class Book:
    def worksheet(self,n):raise KeyError(n)
   seen=pl._class_snapshot(signals)
   asked=[]
   def fake(system,payload,**kw):
    if system is not pl.ADJUDICATE_SYSTEM:return '{}'
    asked.append(payload)
    if answer is None:raise RuntimeError('offline')
    return json.dumps({'verdicts':[answer]},ensure_ascii=False)
   with patch.object(pl,'_verdict_known',return_value=({'2317':['系統紀錄：10/06 賣出這一檔']},set(),{})),patch.object(pl,'_verdict_cache',return_value={}),patch.object(pl,'call_gemini',side_effect=fake),patch.dict(os.environ,{'CLASS_ADJUDICATION':'on'}):
    out=pl.adjudicate_classes(Book(),signals,text,'2026/10/14',seen,seen)
   self.assertTrue(asked,'方向要交問卷判讀')
   return out
  buy_ok={'id':'2317','buy_today':False,'sell_today':False,'holding_now':False,'past_trade':False,'about_itself':True,'now':'buy_ok','tone':'bullish',
          'quotes':{'buy_today':[],'sell_today':[],'holding_now':[],'past_trade':[],'stance':['鴻海又回來前面的買點了']},'unsure':''}
  out=run(buy_ok)
  self.assertEqual(out['_verdicts']['2317']['cls'],'watch_watch');self.assertFalse(out['holdings']);self.assertEqual(out['watch_watch'][0]['code'],'2317')
  out=run(None)
  self.assertFalse(out['holdings']);self.assertEqual(out['_verdicts']['2317']['cls'],'history')
if __name__=='__main__':unittest.main()
