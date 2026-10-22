import importlib.util,json,tempfile,unittest,subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

def load(name):
 spec=importlib.util.spec_from_file_location(name,Path(__file__).with_name(name+'.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
h=load('market_history');pub=load('publish_market_history');report=load('history_progress_report')
class PublicationTests(unittest.TestCase):
 def test_bad_ohlc_is_quarantined_not_repaired_or_blocking_other_codes(self):
  with tempfile.TemporaryDirectory() as d:
   a=Path(d)/'archive';out=Path(d)/'history';raw={'name':'精成科','rows':[['1995-01-18',22.7,22.5,22.5,22.5,-.2,3000],['1995-01-19',22.5,23,22,22.5,0,5000]]}
   p=a/'tpex/6191/199501.json';h._write(p,raw);before=p.read_bytes()
   h._write(a/'tpex/8299/200412.json',{'rows':[['2004-12-06',77,81,77,81,0,1165000]]})
   with patch.object(h,'tpex_calendar',return_value=(set(),set())):h.cmd_build(SimpleNamespace(archive=str(a),out=str(out),codes='6191,8299',codes_file=None,sheets=False))
   meta=json.loads((out/'_meta.json').read_text(encoding='utf-8'));doc=json.loads((out/'6191.json').read_text(encoding='utf-8'))
   self.assertEqual(len(meta['quarantinedRows']),1);self.assertEqual(doc['rows'][0][0],'1995-01-19');self.assertEqual(p.read_bytes(),before)
   self.assertEqual(doc['excludedRows'][0]['raw'][1:5],[22.7,22.5,22.5,22.5]);self.assertEqual(h.cmd_check(SimpleNamespace(out=str(out),archive=None)),0);self.assertFalse(meta['coverageVerified']);self.assertEqual(len(meta['codes']),2)
 def test_nonfinite_negative_and_date_values_are_invalid(self):
  for r in [('bad',1,2,1,1,0,100),('2024-01-01',1,2,1,float('nan'),0,100),('2024-01-01',1,2,1,1,0,-1)]:self.assertTrue(h.history_row_issue(r))
 def test_progress_counts_kept_new_and_failed_without_claiming_complete(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'progress.json';obj=h.HistoryProgress('上櫃',4,SimpleNamespace(progress_file=str(p)))
   obj.begin('8299／2004-12');obj.tick('kept');obj.tick('new',True);obj.tick('refreshed');obj.tick('failed');obj.emit(True,'scanned')
   j=json.loads(p.read_text(encoding='utf-8'));self.assertEqual(j['percent'],100);self.assertEqual(j['new'],1);self.assertEqual(j['kept'],1);self.assertEqual(j['failed'],1);self.assertEqual(j['emptyUnverified'],1);self.assertNotIn('coverageVerified',j)
 def test_today_existing_daily_data_is_not_fetched_again(self):
  with tempfile.TemporaryDirectory() as d:
   today=h.datetime.now(h.TZ).date();iso=today.isoformat();p=Path(d)/f'twse/{today:%Y}/{today:%Y%m%d}.json.gz';h._write(p,{'date':iso,'rows':[['2330','台積電',1,2,1,2,1,1000]]},True)
   # 明示以盤後時間測當天已有有效封存，不能重打來源。
   with patch.object(h,'datetime') as clock,patch.object(h,'fetch_twse_day',side_effect=AssertionError('不能重抓')):
    clock.now.return_value=__import__('datetime').datetime.combine(today,__import__('datetime').time(16),h.TZ)
    h.cmd_fetch_twse(SimpleNamespace(start=iso,end=iso,years=0,deadline_min=1,archive=d))
 def test_archive_is_saved_before_build_failure(self):
  with tempfile.TemporaryDirectory() as d:
   events=[]
   with patch.object(pub,'git',return_value='abc'),patch.object(pub,'save_archive',side_effect=lambda *a:events.append('saved')),patch.object(pub,'pull'),patch.object(pub,'build_history',side_effect=RuntimeError('bad quality')) as build,patch('builtins.print'):
    p=Path(d)/'publication.json'
    with self.assertRaises(RuntimeError):pub.publish(Path(d),'test',p)
    j=json.loads(p.read_text(encoding='utf-8'));self.assertTrue(j['archiveSaved']);self.assertEqual(j['status'],'failed');self.assertEqual(build.call_count,1);self.assertEqual(events,['saved']);self.assertFalse(j['pagesDispatched'])
 def test_summary_distinguishes_probe_and_publish(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);data=root/'data';logs=root/'logs';logs.mkdir();h._write(data/'history/_meta.json',{'codes':{'8299':{'bars':5}},'requestedCodes':['8299','6191'],'missingCodes':['6191'],'quarantinedRows':[{'date':'1995-01-18'}]})
   h._write(logs/'1-tpex-progress.json',{'percent':50,'processed':5,'total':10,'market':'上櫃','current':'8299／2004-12','kept':4,'new':1})
   h._write(logs/'publication-test.json',{'status':'failed','archiveSaved':True,'pagesDispatched':False})
   text=report.render(data,logs);self.assertIn('50%',text);self.assertIn('6191',text);self.assertIn('不代表全歷史已核實',text);self.assertIn('failed',text);self.assertIn('矛盾來源隔離：1筆',text)
 def test_real_local_git_save_build_and_publication_boundary(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);remote=root/'remote.git';data=root/'data';data.mkdir()
   def cmd(*args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   cmd('git','init','--bare',str(remote));cmd('git','init',str(data));cmd('git','-C',str(data),'checkout','-b','market-history')
   for key,val in [('user.name','測試'),('user.email','test@example.invalid')]:cmd('git','-C',str(data),'config',key,val)
   h._write(data/'archive/twse/2004/20040211.json.gz',{'date':'2004-02-11','rows':[['2330','台積電',10,12,9,11,1,1000]]},True)
   cmd('git','-C',str(data),'add','.');cmd('git','-C',str(data),'commit','-m','initial');cmd('git','-C',str(data),'remote','add','origin',str(remote));cmd('git','-C',str(data),'push','-u','origin','market-history')
   codes=root/'codes.txt';codes.write_text('2330',encoding='utf-8');calls=[];real=pub.run
   def execute(command,cwd=None):
    if command[0]=='gh':calls.append(command);return
    return real(command,cwd)
   with patch.object(pub,'run',side_effect=execute):state=pub.publish(data,'contract',root/'report.json',codes)
   self.assertTrue(state['archiveSaved']);self.assertTrue(state['pagesDispatched']);self.assertEqual(len(calls),1)
   saved=json.loads(subprocess.check_output(['git','--git-dir',str(remote),'show','market-history:history/2330.json']).decode('utf-8'));self.assertEqual(saved['rows'][0][4],11)
 def test_live_logs_and_summary_are_in_workflow(self):
  s=Path('.github/workflows/market-history.yml').read_text(encoding='utf-8-sig');self.assertIn('| tee progress/',s);self.assertIn('--progress-file',s);self.assertIn('history_progress_report.py',s);self.assertIn('upload-artifact',s);self.assertNotIn('save checkpoint &&',s)
if __name__=='__main__':unittest.main()
