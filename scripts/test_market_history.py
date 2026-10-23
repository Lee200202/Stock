import importlib.util, tempfile, unittest, json, gzip, os, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
s=importlib.util.spec_from_file_location('hist',Path(__file__).with_name('market_history.py'));h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
class HistoryContract(unittest.TestCase):
 def test_human_exclusion_applies_to_all_input_routes_without_source_guesses(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'codes.txt';p.write_text('3182 8299 9999',encoding='utf-8')
   for args in [SimpleNamespace(codes='3182,8299,9999'),SimpleNamespace(codes_file=str(p)),SimpleNamespace(sheets=True)]:
    with patch.object(h,'sheet_codes',return_value={'3182','8299','9999'}):
     selected,excluded,raw=h._select_history_codes(args)
    self.assertEqual(selected,{'8299','9999'});self.assertEqual(set(excluded),{'3182'});self.assertIn('3182',raw)

 def test_exclusion_preserves_raw_archive_and_is_not_missing_history(self):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'archive';out=Path(d)/'history'
   p=a/'tpex/3182/202610.json';h._write(p,{'rows':[]});before=p.read_bytes()
   h._write(a/'tpex/8299/200412.json',{'rows':[['2004-12-06',77,81,77,81,0,1165000]]})
   with patch.object(h,'tpex_calendar',return_value=(set(),set())):
    h.cmd_build(SimpleNamespace(archive=str(a),out=str(out),codes='3182,8299'))
   m=json.loads((out/'_meta.json').read_text(encoding='utf-8'))
   self.assertEqual(m['requestedCodes'],['8299']);self.assertIn('3182',m['inputCodes']);self.assertIn('3182',m['excludedCodes']);self.assertNotIn('3182',m['missingCodes']);self.assertEqual(p.read_bytes(),before)

 def test_excluded_code_never_calls_otc_source(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(h,'twse_first_dates',return_value=({},'')),patch.object(h,'listing_dates',return_value={}),patch.object(h,'fetch_tpex_month',side_effect=AssertionError('不可抓取3182')):
    h.cmd_fetch_tpex(SimpleNamespace(archive=d,codes='3182',years=0))

 def test_saturday_is_requested_sunday_is_not(self):
  with tempfile.TemporaryDirectory() as d:
   a=SimpleNamespace(start='2017-02-18',end='2017-02-19',years=0,deadline_min=1,archive=d)
   with patch.object(h,'fetch_twse_day',return_value={'stat':'OK','rows':[['2330','台積電',190,191,189,189.5,0,1000]]}) as f:
    h.cmd_fetch_twse(a)
   self.assertEqual([c.args[0].isoformat() for c in f.call_args_list],['2017-02-18'])
   self.assertTrue((Path(d)/'twse/2017/20170218.json.gz').exists())
 def test_otc_missing_first_before_transfer_probe(self):
  with tempfile.TemporaryDirectory() as d:
   a=SimpleNamespace(start='2026-10-01',years=0,deadline_min=1,archive=d,codes='1590,8299',codes_file=None,sheets=False)
   with patch.object(h,'twse_first_dates',return_value=({'1590':'2026-10-01'},'2020-01-01')),patch.object(h,'fetch_tpex_month',return_value={'stat':'ok','rows':[]}) as f,patch.object(h.time,'sleep'):
    h.cmd_fetch_tpex(a)
    self.assertEqual(f.call_args_list[0].args[0],'8299')
    self.assertEqual(f.call_args_list[-1].args[0],'1590')
 def test_coverage_distinguishes_empty_and_missing(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);archive=root/'archive';out=root/'history'
   h._write(archive/'twse/2004/20040211.json.gz',{'date':'2004-02-11','stat':'no data','rows':[]},True)
   h._write(archive/'twse/2004/20040212.json.gz',{'date':'2004-02-12','stat':'OK','rows':[['2330','台積電',50,52,49,51,1,1000]]},True)
   h.cmd_build(SimpleNamespace(archive=str(archive),out=str(out),codes='2330',codes_file=None,sheets=False))
   meta=json.loads((out/'_meta.json').read_text(encoding='utf-8'));c=meta['archiveCoverage']['twse']
   self.assertIn('2004-02-11',c['emptyDaysUnverified']);self.assertNotIn('2004-02-11',c['missingWeekdays'])
   self.assertIn('2004-02-13',c['missingWeekdays']);self.assertFalse(meta['coverageVerified'])
 def test_all_not_ten_years(self):
  a=SimpleNamespace(years=0,start=None)
  self.assertEqual(h._start(a,h.date(2026,10,22),h.TWSE_START),h.date(2004,2,11))
 def test_no_price_based_adjustment(self):
  rows,adj=h.build_series([('2025-06-11',200,204,198,200,0,1000),('2025-06-18',50,51,49,50,0,4000)])
  self.assertEqual(rows[0][4],200);self.assertEqual(adj,[])
 def test_atomic_unchanged_and_corrupt(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.gz';h._write(p,{'rows':[1]},True);stamp=p.stat().st_mtime_ns;h._write(p,{'rows':[1]},True)
   self.assertEqual(p.stat().st_mtime_ns,stamp);p.write_bytes(b'bad');self.assertIsNone(h._read(p,True))
 def test_existing_twse_no_network(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'twse/2020/20200106.json.gz';h._write(p,{'date':'2020-01-06','stat':'OK','rows':[[1]]},True)
   a=SimpleNamespace(start='2020-01-06',end='2020-01-06',years=0,deadline_min=1,archive=d)
   with patch.object(h,'fetch_twse_day',side_effect=AssertionError('不應重抓')):h.cmd_fetch_twse(a)
 def test_three_empty_months_do_not_end_probe(self):
  with tempfile.TemporaryDirectory() as d:
   a=SimpleNamespace(start='2026-06-01',years=0,deadline_min=1,archive=d,codes='8299',codes_file=None,sheets=False)
   with patch.object(h,'fetch_tpex_month',return_value={'stat':'ok','rows':[]}) as f,patch.object(h.time,'sleep'):
    h.cmd_fetch_tpex(a);self.assertGreaterEqual(f.call_count,5)
 def test_failed_twse_not_cached(self):
  with tempfile.TemporaryDirectory() as d:
   a=SimpleNamespace(start='2020-01-06',end='2020-01-06',years=0,deadline_min=1,archive=d)
   with patch.object(h,'fetch_twse_day',side_effect=TimeoutError):h.cmd_fetch_twse(a)
   self.assertFalse(list(Path(d).rglob('*.gz')))
 def test_official_events_parse_both_markets(self):
  tw=h.event_rows(['恢復買賣日期','股票代號','名稱','停止買賣前收盤價格','恢復買賣參考價','漲停價格'],[['114/08/25','2327','國巨','546.00','136.50','150.00']],'par')
  self.assertEqual(tw,[{'date':'2025-08-25','code':'2327','prev':546.0,'ref':136.5,'kind':'par'}])
  tp=h.event_rows(['恢復買賣日期','股票代號','名稱','最後交易日之收盤價格','減資恢復買賣開始日參考價格','漲停價格','跌停價格','開始交易基準價','除權參考價'],[['1140113','2740','天蔥','21.55','39.18','43.05','35.30','39.20','0.00']],'reduction')
  self.assertEqual(tp[0]['date'],'2025-01-13');self.assertEqual(tp[0]['ref'],39.18)
  etf=h.event_rows(['恢復買賣日期','ETF代號','名稱','分割(反分割)','停止買賣前收盤價格','恢復買賣參考價'],[['114/02/19','00676R','反1','反分割','2.04','20.40']],'etf-split')
  self.assertEqual(etf[0]['kind'],'etf-reverse')
  with self.assertRaises(ValueError):h.event_rows(['日期','代號'],[['114/01/01','2330']],'par')
 def test_official_event_adjusts_only_before_and_checks_archive(self):
  e=[('2025-08-13',540,550,530,546,1,1000),('2025-08-25',137,140,135,138,1.5,4000)]
  rows,adj=h.build_series(e,[{'date':'2025-08-25','code':'2327','prev':546.0,'ref':136.5,'kind':'par'}])
  self.assertEqual(adj[0]['factor'],4.0);self.assertTrue(adj[0]['matchesArchive']);self.assertEqual(adj[0]['kind'],'面額變更')
  self.assertEqual(rows[0][4],136.5);self.assertEqual(rows[1][4],138);self.assertEqual(rows[0][5],1000)
  rows,adj=h.build_series(e,[{'date':'2026-01-05','code':'2327','prev':1.0,'ref':2.0,'kind':'reduction'}])
  self.assertEqual(adj,[]);self.assertEqual(rows[0][4],546)
 def test_saturday_skipped_only_when_tpex_calendar_says_closed(self):
  with tempfile.TemporaryDirectory() as d:
   for c in ('8299','5289'):h._write(Path(d)/f'tpex/{c}/201702.json',{'rows':[['2017-02-17',1,1,1,1,0,1],['2017-02-18',1,1,1,1,0,1],['2017-02-24',1,1,1,1,0,1]]})
   a=SimpleNamespace(start='2017-02-18',end='2017-02-25',years=0,deadline_min=1,archive=d,order='newest')
   with patch.object(h,'fetch_twse_day',return_value={'stat':'OK','rows':[['2330','台積電',190,191,189,189.5,0,1000]]}) as f:h.cmd_fetch_twse(a)
   asked=sorted(c.args[0].isoformat() for c in f.call_args_list)
   self.assertIn('2017-02-18',asked);self.assertNotIn('2017-02-25',asked);self.assertNotIn('2017-02-19',asked);self.assertIn('2017-02-20',asked)
 def test_empty_answers_settle_by_recorded_checks_not_file_time(self):
  day=h.date(2020,1,1)
  self.assertEqual(h._empty_settled({'checks':['2020-03-01T10:00:00+08:00','2020-03-05T10:00:00+08:00']},day),'settled')
  self.assertEqual(h._empty_settled({'checks':['2020-03-01T10:00:00+08:00']},day),'ask')
  self.assertEqual(h._empty_settled({'checks':[h._iso_now()]},day),'wait')
  self.assertEqual(h._empty_settled({},day,calendar_closed=True),'settled')
  self.assertEqual(h._empty_settled({'checkedAt':'2020-01-02T10:00:00+08:00'},day),'ask')
 def test_otc_probe_starts_at_listing_month_and_refreshes_recent_month(self):
  with tempfile.TemporaryDirectory() as d:
   now=h.datetime.now(h.TZ);cur=f'{now.year}{now.month:02d}'
   h._write(Path(d)/f'tpex/8299/{cur}.json',{'stat':'ok','rows':[[f'{now.year}-{now.month:02d}-01',1,1,1,1,0,1000]]})
   lm=(now.year*12+now.month-1)-3;listed=f'{lm//12}-{lm%12+1:02d}-15'
   a=SimpleNamespace(start=None,years=0,deadline_min=1,archive=d,codes='8299',codes_file=None,sheets=False,order='newest')
   with patch.object(h,'listing_dates',return_value={'8299':{'otc':listed}}),patch.object(h,'twse_first_dates',return_value=({},'')),\
        patch.object(h,'fetch_tpex_month',return_value={'stat':'ok','rows':[['2020-01-02',1,1,1,1,0,1000]]}) as f:h.cmd_fetch_tpex(a)
   asked=[(c.args[1],c.args[2]) for c in f.call_args_list]
   self.assertEqual(len(asked),4);self.assertEqual(asked[0],(now.year,now.month))
   self.assertEqual(asked[-1],(lm//12,lm%12+1))
 def test_transfer_probe_keeps_earlier_month_after_two_empty_months(self):
  with tempfile.TemporaryDirectory() as d:
   a=SimpleNamespace(start=None,years=0,deadline_min=1,archive=d,codes='6770',codes_file=None,sheets=False,order='newest')
   def response(code,y,m):
    return {'stat':'ok','rows':[[f'{y}-{m:02d}-01',1,1,1,1,0,1000]] if m==10 else []}
   with patch.object(h,'listing_dates',return_value={'6770':{'listed':'2021-12-06','founded':'2021-10-01'}}),        patch.object(h,'twse_first_dates',return_value=({'6770':'2021-12-06'},'2004-02-11')),        patch.object(h,'fetch_tpex_month',side_effect=response) as f,patch.object(h.time,'sleep'):h.cmd_fetch_tpex(a)
   self.assertIn((2021,10),[(c.args[1],c.args[2]) for c in f.call_args_list])
   saved=json.loads((Path(d)/'tpex/6770/202110.json').read_text(encoding='utf-8'))
   self.assertTrue(saved['rows'])
if __name__=='__main__':unittest.main()
