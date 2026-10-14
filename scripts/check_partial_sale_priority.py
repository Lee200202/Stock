"""Cross-source holding regression: partial sale cannot close the remaining position. No models/network."""
import importlib.util,os,pathlib,unittest
from unittest.mock import patch
ROOT=pathlib.Path(__file__).resolve().parents[1]
os.environ.setdefault('SPREADSHEET_ID','test');os.environ.setdefault('GEMINI_API_KEY','AIzaSyDUMMY_local_import_only_0000000000')
spec=importlib.util.spec_from_file_location('partial_priority',ROOT/'pipeline/pipeline.py');pl=importlib.util.module_from_spec(spec);spec.loader.exec_module(pl)
HEAD=['日期','股票名稱','代號','方向','來源影片ID','理由摘錄']
class Sheet:
 def __init__(self,rows):self.rows=rows
 def get_all_values(self):return self.rows
class Book:
 def __init__(self,trades,holds=()):self.tabs={'操作紀錄':Sheet([HEAD]+list(trades)),'會員持股':Sheet([['日期','股票名稱','代號']]+list(holds))}
 def worksheet(self,n):return self.tabs[n]
def trade(day,kind,reason):return [day,'測試股','9999',kind,'CMONEY-123',reason]
class TestPartialPriority(unittest.TestCase):
 def test_predicate(self):
  for reason in ['部分部位賣出','賣出部分持股','減碼','減持']:
   self.assertTrue(pl.is_partial_sale_record({'direction':'賣出','reason':reason}))
  for reason in ['全數賣出','全部賣出','部分賣出後剩餘全數賣出','獲利全出','清倉','出清']:
   self.assertFalse(pl.is_partial_sale_record({'direction':'賣出','reason':reason}))
  self.assertFalse(pl.is_partial_sale_record({'direction':'買入','reason':'等待部分賣出'}))
 def test_old_partial_retains_buy(self):
  book=Book([trade('2026/10/01','買入','買入'),trade('2026/10/05','賣出','部分部位賣出')])
  known,_,opened=pl._verdict_known(book,'2026/10/14',True);self.assertIn('9999',opened);self.assertIn('買入',known['9999'][0])
 def test_partial_retains_declared_holding(self):
  book=Book([trade('2026/10/12','賣出','減碼')],[['2026/10/10','測試股','9999']]);self.assertIn('9999',pl._verdict_known(book,'2026/10/14',True)[2])
 def test_partial_alone_does_not_create_position(self):
  self.assertNotIn('9999',pl._verdict_known(Book([trade('2026/10/12','賣出','減碼')]),'2026/10/14',True)[2])
 def test_later_full_sale_closes(self):
  book=Book([trade('2026/10/01','買入','買入'),trade('2026/10/05','賣出','減碼'),trade('2026/10/13','賣出','全數賣出')]);self.assertNotIn('9999',pl._verdict_known(book,'2026/10/14',True)[2])
 def test_current_notice_partial_keeps_video_hold(self):
  signals={'sell':[{'code':'9999','reason':'部分賣出'}],'holdings':[{'code':'9999','name':'測試股','note':'仍持有'}]}
  pl.drop_sms_sold_holdings(Book([trade('2026/10/14','賣出','部分部位賣出')]),signals,'2026/10/14');self.assertEqual(len(signals['holdings']),1)
 def test_current_notice_full_sale_overrides_partial(self):
  signals={'sell':[{'code':'9999'}],'holdings':[{'code':'9999','name':'測試股'}]}
  book=Book([trade('2026/10/14','賣出','部分部位賣出'),trade('2026/10/14','賣出','獲利全出')]);pl.drop_sms_sold_holdings(book,signals,'2026/10/14');self.assertFalse(signals['holdings'])
if __name__=='__main__':
 with patch.object(pl,'sheets_retry',side_effect=lambda f,*a,**k:f(*a,**k)):unittest.main()
