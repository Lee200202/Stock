"""valuation_history.py 的離線契約：欄位解析、日期核對、虧損不計、百分位與區間。"""
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import valuation_history as vh  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_twse_fields_by_name(self):
        p = {'stat': 'OK', 'date': '20170104', 'fields': ['證券代號', '證券名稱', '本益比', '殖利率(%)', '股價淨值比'],
             'data': [['1101', '台泥', '20.61', '3.77', '1.24'], ['2888', '新光金', '-', '0.00', '0.62'], ['TOTAL', 'x', '1', '1', '1']]}
        rows = vh.parse('twse', date(2017, 1, 4), p)
        self.assertEqual(rows['1101'], [20.61, 3.77, 1.24])
        self.assertEqual(rows['2888'], [None, 0.0, 0.62])   # 虧損：本益比不計，殖利率 0 照收
        self.assertNotIn('TOTAL', rows)

    def test_twse_newer_layout(self):
        p = {'stat': 'OK', 'date': '20240104', 'fields': ['證券代號', '證券名稱', '收盤價', '殖利率(%)', '股利年度', '本益比', '股價淨值比', '財報年/季'],
             'data': [['2330', '台積電', '593.00', '1.85', 112, '16.29', '4.41', '112/3']]}
        self.assertEqual(vh.parse('twse', date(2024, 1, 4), p)['2330'], [16.29, 1.85, 4.41])

    def test_date_mismatch_rejected(self):
        p = {'date': '20261123', 'tables': [{'fields': ['股票代號', '公司名稱', '本益比', '每股股利', '股利年度', '殖利率(%)', '股價淨值比'],
                                             'data': [['3529', '力旺', '59.52', '22', 114, '1.08', '34.29']]}]}
        self.assertIsNone(vh.parse('tpex', date(2007, 11, 23), p))
        self.assertEqual(vh.parse('tpex', date(2026, 11, 23), p)['3529'], [59.52, 1.08, 34.29])

    def test_tpex_na(self):
        p = {'date': '20081124', 'tables': [{'fields': ['股票代號', '公司名稱', '本益比', '每股股利', '股利年度', '殖利率(%)', '股價淨值比'],
                                             'data': [['1107', '建台', 'N/A', '0.00000000', 96, '0.00', 'N/A']]}]}
        self.assertEqual(vh.parse('tpex', date(2008, 11, 24), p)['1107'], [None, 0.0, None])

    def test_empty_day(self):
        self.assertIsNone(vh.parse('twse', date(2026, 11, 24), {'stat': '很抱歉，沒有符合條件的資料!'}))


class BuildTest(unittest.TestCase):
    def test_quantiles_and_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(30):
                d = date(2026, 1, 1 + i) if i < 28 else date(2026, 2, i - 27)
                vh._write(vh._path(tmp, 'twse', d), {'date': d.isoformat(), 'market': 'twse', 'stat': 'ok',
                                                     'rows': {'1234': [10.0 + i, None, 1.0]}})
            old = date(2019, 1, 2)
            vh._write(vh._path(tmp, 'twse', old), {'date': old.isoformat(), 'market': 'twse', 'stat': 'ok', 'rows': {'1234': [999.0, 1.0, 1.0]}})
            out = vh.build(tmp, 5)['1234']
            q = out['per']['q']
            self.assertEqual(len(q), 101)
            self.assertEqual((q[0], q[100]), (10.0, 39.0))     # 五年前的 999 倍不在區間
            self.assertEqual(out['per']['n'], 30)
            self.assertNotIn('yld', out)                       # 不足 20 筆不出這個指標
            self.assertEqual(out['market'], '上市')


if __name__ == '__main__':
    unittest.main()
