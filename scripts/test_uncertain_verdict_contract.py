"""Publication isolation and runtime completeness; no models or network."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('uncertain_contract_pipeline', ROOT/'pipeline/pipeline.py')
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


class PublicationContracts(unittest.TestCase):
    def test_unverified_buy_or_sell_is_quarantined_and_other_rows_continue(self):
        for category in ('buy', 'sell'):
            with self.subTest(category=category):
                suspect = {'name': '甲公司', 'code': '1111', 'reason': '待核', '_date': '2026/10/21'}
                good = {'name': '乙公司', 'code': '2222', 'reason': '已核對說明', '_date': '2026/10/21'}
                signals = {category: [suspect], 'watch_watch': [good], '_verdicts': {
                    '1111': {'cls': 'uncertain', 'enforce': True, 'name': '甲公司', 'basis': '今日成交未通過核對'}}}
                pl.apply_verdicts(signals, '2026/10/21')
                self.assertEqual(signals[category], [])
                self.assertEqual(signals['uncertain'][0]['code'], '1111')
                self.assertEqual(signals['watch_watch'], [good])
                # A later stage must not silently make it public again.
                signals[category].append(dict(suspect))
                pl.apply_verdicts(signals, '2026/10/21', final=True)
                self.assertEqual(signals[category], [])
                self.assertEqual(len(signals['uncertain']), 1)

    def test_shadow_mode_does_not_change_published_class(self):
        row = {'name': '甲公司', 'code': '1111'}
        signals = {'buy': [row], '_verdicts': {'1111': {'cls': 'uncertain', 'enforce': False}}}
        pl.apply_verdicts(signals, '2026/10/21')
        self.assertEqual(signals['buy'], [row])
        self.assertNotIn('uncertain', signals)

    def test_an_unexecuted_adjudication_is_a_quality_error_not_success(self):
        with patch.object(pl, '_adjudicate_classes', side_effect=RuntimeError('offline simulated failure')), \
             patch.dict(os.environ, {'CLASS_ADJUDICATION': 'on'}):
            signals = pl.adjudicate_classes(None, {}, '原文', '2026/10/21')
        self.assertTrue(signals['_classification_runtime_error'])
        self.assertTrue(signals['_quality_requires_review'])
        _, hard, _ = pl.quality_overview(signals, check_chapters=False)
        self.assertTrue(any('分類裁決未完成' in item for item in hard))


if __name__ == '__main__':
    unittest.main()
