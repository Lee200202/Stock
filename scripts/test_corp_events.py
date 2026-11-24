"""corp_events.py 的離線契約：民國日期、欄位依名稱、權息種類、還原倍數與異常值排除。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import corp_events as ce  # noqa: E402


class CorpEventsTest(unittest.TestCase):
    def test_roc_dates(self):
        self.assertEqual(ce.roc_date('114年06月13日'), '2025-06-13')
        self.assertEqual(ce.roc_date('114/01/02'), '2025-01-02')
        self.assertEqual(ce.roc_date('2025/06/13'), '2025-06-13')
        self.assertIsNone(ce.roc_date('—'))

    def test_twse_and_tpex(self):
        tw = {'stat': 'OK', 'fields': ['資料日期', '股票代號', '股票名稱', '除權息前收盤價', '除權息參考價', '權值+息值', '權/息'],
              'data': [['114年06月13日', '2327', '國巨*', '487.00', '467.00', '19.995014', '息'], ['114年06月13日', 'ABCD', 'x', '1', '1', '0', '息']]}
        rows = ce.parse('twse', tw)
        self.assertEqual(rows, [{'code': '2327', 'date': '2025-06-13', 'kind': '除息', 'prev': 487.0, 'ref': 467.0, 'value': 19.995014}])
        tp = {'tables': [{'fields': ['除權息日期', '代號', '名稱', '除權息前收盤價', '除權息參考價', '權值', '息值', '權值+息值', '權/息'],
                          'data': [['113/08/15', '6488', '環球晶', '500.00', '480.00', '10', '10', '20', '除權息']]}]}
        self.assertEqual(ce.parse('tpex', tp)[0]['kind'], '權息')

    def test_build_factor_and_outlier(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'twse' / '2025.json'
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'rows': [
                {'code': '2327', 'date': '2025-06-13', 'kind': '除息', 'prev': 487.0, 'ref': 467.0, 'value': 20},
                {'code': '2327', 'date': '2025-09-01', 'kind': '除權', 'prev': 100.0, 'ref': 10.0, 'value': 90}]}), encoding='utf-8')
            out = ce.build(tmp)['2327']
            self.assertEqual(len(out), 1)                         # 倍數 10 視為異常不採用
            self.assertEqual(out[0]['factor'], round(487 / 467, 6))


if __name__ == '__main__':
    unittest.main()
