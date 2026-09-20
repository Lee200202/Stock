import unittest
from unittest.mock import Mock
from datetime import datetime
from zoneinfo import ZoneInfo
import market_data as m


class IncrementalTests(unittest.TestCase):
    def test_hours_complete_skip_and_only_missing_day(self):
        now=datetime(2026,9,21,tzinfo=ZoneInfo('Asia/Taipei'))
        bars=[[f'2026/09/18 {h:02}:00',10,12,9,11,100] for h in range(9,14)]
        self.assertEqual(m.hourly_missing_ranges(bars,now),[])
        ranges=m.hourly_missing_ranges(bars[:-1],now)
        self.assertEqual(str(ranges[0][0]),'2026-09-18')
        self.assertEqual(str(ranges[0][1]),'2026-09-19')

    def test_existing_day_history_kept_last_unclosed_refreshes(self):
        import pandas as pd
        f=Mock()
        f.history.return_value=pd.DataFrame({'Open':[10,11],'High':[12,13],'Low':[9,10],'Close':[12,12],'Volume':[2,3]},index=pd.to_datetime(['2026-09-18','2026-09-21']))
        prior=[['2026/09/17',9,10,8,9,1],['2026/09/18',10,12,9,11,2]]
        result=m.incremental_daily(f,'x',prior)
        self.assertEqual(result[0],prior[0]);self.assertEqual(result[-1][0],'2026/09/21')
        self.assertEqual(f.history.call_args.kwargs['start'],'2026-09-18')

    def test_invalid_ohlcv_never_counts_as_complete(self):
        self.assertFalse(m.valid_bar(['2026/09/18',10,9,8,11,100]))
        self.assertFalse(m.valid_bar(['2026/09/18',10,12,8,11,None]))
        self.assertTrue(m.valid_bar(['2026/09/18',10,12,8,11,0]))
