"""Offline, shared Python/GAS cases for notice speech acts and trade evidence."""
import importlib.util, json, os, subprocess, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('GEMINI_API_KEY','offline-dummy');os.environ.setdefault('SPREADSHEET_ID','offline')
spec=importlib.util.spec_from_file_location('pl_notice_test',ROOT/'pipeline/pipeline.py')
pl=importlib.util.module_from_spec(spec);spec.loader.exec_module(pl)
NOTICE='今日大盤已到60分k線200均低點，目前只等MACD收斂。如果想買股票者，1590亞德客、8210勤誠此兩檔可以買！世芯、嘉澤高點賣出者，不急於買回，條件尚未出現，也不用一個個私下問我。今日持股一張都不准殺低，續抱即可！'
CASES=[
 [NOTICE,'勤誠','8210','買入','觀望注意'],
 [NOTICE,'亞德客','1590','買入','觀望注意'],
 [NOTICE,'嘉澤','3533','觀望注意','觀望不碰'],
 [NOTICE,'嘉澤','3533','賣出','觀望不碰'],
 [NOTICE,'世芯','3661','觀望不碰','觀望不碰'],
 [NOTICE,'台積電','2330','會員持股',''],
 ['手中持有嘉澤者，請於2010元之上，賣出2000元買的部位張數，在1580元買的部位續抱長線。','嘉澤','3533','賣出','賣出'],
 ['會員手中未持有美律者，請於94元以下買進，已持有者續抱即可。','美律','2439','買入','買入'],
 ['早上賣出的世芯，請於265元以下買回來。','世芯','3661','賣出',''],
 ['早上賣出的世芯，請於265元以下買回來。','世芯','3661','買入','買入'],
 ['會員手中鴻海、緯創，今天應站賣方。','鴻海','2317','賣出','賣出'],
 ['會員手中嘉澤，不准殺低，續抱。','嘉澤','3533','會員持股','會員持股'],
]
class Cases(unittest.TestCase):
 def test_python_gas_parity(self):
  for body,name,code,action,expected in CASES:
   with self.subTest(name=name,action=action):self.assertEqual(pl.notice_editor().classify(body,name,code,action)[0],expected)
  code="const fs=require('fs'),vm=require('vm');const s=fs.readFileSync('public-site/gas-source/Cmoney.gs','utf8');const part=s.slice(s.indexOf('function cmNoticeAction_'),s.indexOf('function cmVerifyItems_'));const c={};vm.createContext(c);vm.runInContext(part,c);const rows=JSON.parse(fs.readFileSync(0,'utf8'));for(const r of rows){if(c.cmNoticeAction_(...r.slice(0,4)).action!==r[4])throw Error(JSON.stringify(r));}console.log(rows.length+' GAS cases passed');"
  result=subprocess.run(['node','-e',code],input=json.dumps(CASES,ensure_ascii=False),encoding='utf8',cwd=ROOT,capture_output=True)
  self.assertEqual(result.returncode,0,result.stderr)
 def test_reparse_does_not_resurrect_bad_trades(self):
  prior=[{'name':'世芯-KY','code':'3661','action':'賣出'},{'name':'嘉澤','code':'3533','action':'買入'}]
  self.assertEqual(pl.sms_keep_prior_trades([],prior,NOTICE,{'3661':'世芯-KY','3533':'嘉澤'}),[])
  self.assertEqual(pl.sms_uncovered_trades(NOTICE,[],{'3661':'世芯-KY','3533':'嘉澤','8210':'勤誠','1590':'亞德客-KY'}),{})
 def test_duplicates_removed_with_same_entry_stance(self):
  rows=[{'name':'嘉澤','code':'3533','action':a,'note':'等待買回條件。'} for a in ('觀望注意','觀望不碰')]
  out=pl._sms_dedupe_and_condition(rows,{'text':NOTICE,'time':'2026/10/15 11:34:51'}, {})
  self.assertEqual([(r['code'],r['action']) for r in out],[('3533','觀望不碰')])
 def test_no_actual_sale_in_criticism(self):
  for phrase in ('今天賣鴻海跟今天賣聯電一樣蠢。','今天不能賣鴻海。','如果今天賣出鴻海就是阿呆。','昨天賣出鴻海。'):
   self.assertEqual(pl.explicit_today_trades(phrase,{'鴻海','2317'},'sell'),[])
  self.assertTrue(pl.explicit_today_trades('我今天賣出鴻海了。',{'鴻海','2317'},'sell'))
 def test_one_batch_full_notice_and_no_extra_retry(self):
  data={'items':[{'name':n,'code':c,'action':a,'note':'等待買回條件。'} for n,c,a in [('勤誠','8210','買入'),('亞德客','1590','買入'),('嘉澤','3533','觀望注意'),('嘉澤','3533','觀望不碰'),('世芯-KY','3661','觀望不碰')]]}
  mapping={'8210':'勤誠','1590':'亞德客-KY','3533':'嘉澤','3661':'世芯-KY'}
  def resolve(n,h):return h,mapping[h],'official'
  with patch.object(pl,'call_gemini',return_value=data) as model,patch.object(pl,'resolve_code',side_effect=resolve),patch.object(pl.time,'sleep'):
   items,failed,calls=pl._sms_extract_items({'id':'case','text':NOTICE},__import__('re').compile(r'(?!)'),mapping)
  self.assertFalse(failed);self.assertEqual(calls,1);self.assertEqual(model.call_count,1)
  self.assertIn(NOTICE,model.call_args.args[1]);self.assertFalse(any(x['action'] in ('買入','賣出') for x in items))
  self.assertIn('3661',[i['code'] for i in items])
 def test_both_trade_candidates_share_one_questionnaire(self):
  text='我今天買進甲股了。我今天賣出乙股了。'
  signals={cat:[] for cat in pl.SIGNAL_CATEGORIES+('history','ignored','uncertain')}
  signals['buy']=[{'name':'甲股','code':'1234','when':'today','reason':'今日完成買進。'}]
  signals['sell']=[{'name':'乙股','code':'5678','when':'today','reason':'今日完成賣出。'}]
  def answer(code,side,quote):return {'id':code,side:True,'quotes':{side:[quote]}}
  answers={'1234':answer('1234','buy_today','我今天買進甲股了。'),'5678':answer('5678','sell_today','我今天賣出乙股了。')}
  with patch.object(pl,'_verdict_known',return_value=({},set(),{})),patch.object(pl,'_verdict_cache',return_value={}),patch.object(pl,'_own_segments',return_value=[text]),patch.object(pl,'preserve_explicit_holdings',side_effect=lambda s,t:s),patch.object(pl,'_ask_verdicts',return_value=answers) as ask,patch.object(pl,'budget_left',return_value=900),patch.object(pl,'GEMINI_KEYS',['dummy']),patch.dict(pl._QUOTA_STOP,{'daily':False}):
   out=pl._adjudicate_classes(None,signals,text,'2026/10/15',signals,signals,'on')
  self.assertEqual(ask.call_count,1)
  self.assertEqual({c for c,_ in ask.call_args.args[0]},{'1234','5678'})
  self.assertTrue(all(out['_verdicts'][c]['execution_verified'] for c in ('1234','5678')))
  with patch.object(pl,'day_notice_trades',return_value=set()):
   kept=pl.guard_today_trade_publication(None,out,text,'2026/10/15')
  self.assertEqual(len(kept['buy'])+len(kept['sell']),2)
 def test_unverified_trade_isolated_without_stopping_other_stocks(self):
  signals={'buy':[{'code':'1234','name':'甲股'}],'sell':[],'watch_watch':[{'code':'9876','name':'丙股'}]}
  with patch.object(pl,'day_notice_trades',return_value=set()):out=pl.guard_today_trade_publication(None,signals,'我今天買進甲股了。','2026/10/15')
  self.assertEqual(out['buy'],[]);self.assertEqual(len(out['watch_watch']),1);self.assertEqual(len(out['_publication_quarantine']),1)
 def test_named_condition_survives_short_sentence_breaks_but_not_another_stock(self):
  with patch.object(pl,'_CODE_MAP',{'1234':'甲股','5678':'乙股'}):
   self.assertEqual(pl.bounded_current_conditions({'甲股'},'甲股。跌了很多。兩千到一千。它的年線在這裡，如果跌到年線就變安全。')[0],'watch_watch')
   self.assertEqual(pl.bounded_current_conditions({'甲股'},'甲股。跌了很多。乙股如果跌到年線就變安全。')[0],'')
if __name__=='__main__':unittest.main()
