import unittest
from datetime import datetime
from unittest.mock import patch
import market_data as m

class MarketDataTests(unittest.TestCase):
    def test_industry_totals_use_amount_and_no_double_count(self):
        report={'stat':'OK','date':'20260918','tables':[
            {'fields':['指數','收盤指數','漲跌(+/-)','漲跌點數','漲跌百分比(%)'],
             'data':[['發行量加權股價指數','20,000','-','100','-0.5']]},
            {'fields':['證券代號','成交金額','成交股數'],
             'data':[['2330','800','99999'],['2201','100','1'],['9999','100','5'],['00981A','9000','9000']]}]}
        companies=[{'公司代號':'2330','產業別':'24'},{'公司代號':'2201','產業別':'12'},{'公司代號':'9999','產業別':'99'}]
        index,sector=m.parse_twse(report,companies,{'13':'電子工業','12':'汽車工業'})
        self.assertEqual(index['change'],-100)
        self.assertEqual(sector['total'],1000)
        self.assertAlmostEqual(sum(x['percent'] for x in sector['rows']),100)
        self.assertEqual(sector['rows'][0]['percent'],80)
        self.assertIn('未分類',[r['name'] for r in sector['rows']])

    def test_empty_report_rejects_instead_of_publishing_zero(self):
        with self.assertRaises(ValueError):m.parse_twse({'stat':'很抱歉'},[],{})

    def test_industry_names_from_official_options(self):
        self.assertEqual(m.industry_labels('<option value="24">半導體業</option><option value="ALL">全部</option>'),{'24':'半導體業'})

    def test_nonfinite_values_not_written_as_json_nan(self):
        for value in ['NaN','inf','--',None]:self.assertIsNone(m.number(value))
        self.assertEqual(m.number('1,234'),1234)

    def test_hourly_timezone_volume_and_incomplete_day(self):
        try:import pandas as pd
        except ImportError:self.skipTest('pandas 安裝於 requirements-market.txt')
        frame=pd.DataFrame({'Open':[100,101,102],'High':[110,110,110],'Low':[90,90,90],
            'Close':[105,106,107],'Volume':[123456,2000,3000]},
            index=pd.DatetimeIndex(['2026-09-17T01:00Z','2026-09-17T05:00Z','2026-09-18T01:00Z']))
        bars=m.hourly_rows(frame,datetime(2026,9,18,18,tzinfo=m.TZ))
        self.assertEqual([r[0] for r in bars],['2026/09/17 09:00','2026/09/17 13:00'])
        self.assertEqual(bars[0][5],123.456)
        self.assertEqual(bars[0][4],105)
        frame.index=frame.index.tz_localize(None)
        with self.assertRaises(ValueError):m.hourly_rows(frame,datetime.now(m.TZ))

    def test_macro_delta_compares_actual_previous_observation(self):
        try:import pandas as pd
        except ImportError:self.skipTest('pandas 安裝於 requirements-market.txt')
        frame=pd.DataFrame({'Close':[30,31]},index=pd.date_range('2026-09-17',periods=2))
        card=m.macro_card(frame,'usdtwd','美元／台幣','台幣／美元')
        self.assertEqual(card['value'],31)
        self.assertIn('非即時',card['source'])
        self.assertAlmostEqual(card['percent'],100/30)

if __name__=='__main__':unittest.main()
