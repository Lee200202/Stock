import unittest
from unittest.mock import patch
from datetime import date
import market_data as m

class MarketV47Tests(unittest.TestCase):
    def test_holiday_does_not_open_sheets_for_scheduled_hours(self):
        with patch.dict(m.os.environ, {'GITHUB_EVENT_NAME':'schedule'}), patch.object(m,'is_trading_day',return_value=False), patch.object(m,'open_sheets') as opened, patch('sys.argv',['market_data.py','--mode','hours']):
            m.main();opened.assert_not_called()

    def test_taiwan_holiday_keeps_international_dashboard(self):
        with patch.dict(m.os.environ, {'GITHUB_EVENT_NAME':'schedule'}), patch.object(m,'is_trading_day',return_value=False), patch.object(m,'open_sheets'), patch.object(m,'update_dashboard') as run, patch('sys.argv',['market_data.py']):
            m.main();self.assertFalse(run.call_args.args[3])

    def test_real_holidays_and_monday(self):
        self.assertFalse(m.is_trading_day(date(2026,9,25)))
        self.assertFalse(m.is_trading_day(date(2026,9,28)))
        self.assertTrue(m.is_trading_day(date(2026,9,21)))

    def test_futures_near_contract_not_night_or_spread(self):
        def row(month,session='一般',day='20260918'):
            return {'Date':day,'Contract':'TX','TradingSession':session,'ContractMonth(Week)':month,'Open':'100','High':'110','Low':'90','Last':'105','Volume':'12','Change':'5','%':'5%'}
        card=m.futures_card([row('202611'),row('202610'),row('202609','盤後'),row('202609/202610'),row('202609',day='20260917')])
        self.assertEqual(card['contract'],'202610');self.assertEqual(card['value'],105)
        old=dict(contract='202609',candles=[['2026/09/17',99,100,98,99,1]])
        self.assertEqual(len(m.futures_card([row('202610')],old)['candles']),1)
        old['contract']='202610';self.assertEqual(len(m.futures_card([row('202610')],old)['candles']),2)

    def test_candle_validation(self):
        try:import pandas as pd
        except ImportError:self.skipTest('pandas unavailable')
        df=pd.DataFrame({'Open':[100,100],'High':[90,110],'Low':[80,90],'Close':[85,105]},index=pd.date_range('2026-09-17',periods=2))
        self.assertEqual(len(m.daily_bars(df)),1)
        self.assertEqual(m.daily_bars(df)[0][5],0)
