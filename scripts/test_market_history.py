import importlib.util, tempfile, unittest, json, gzip, os, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
s=importlib.util.spec_from_file_location('hist',Path(__file__).with_name('market_history.py'));h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
class HistoryContract(unittest.TestCase):
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
if __name__=='__main__':unittest.main()
