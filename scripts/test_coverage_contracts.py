"""Candidate/quote/provenance/position contracts. No models, Sheets or market calls."""
import copy
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
spec = importlib.util.spec_from_file_location('coverage_contract_pipeline', ROOT / 'pipeline/pipeline.py')
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


class SourceView(unittest.TestCase):
    def setUp(self):
        self.scope = dict(pl._TX_SCOPED)
    def tearDown(self):
        pl._TX_SCOPED.clear(); pl._TX_SCOPED.update(self.scope)

    def test_polished_lexical_changes_never_enter_the_view(self):
        raw = '甲 公 司 價 格 13 元 我 沒 有 買'
        view = pl.punctuate_from_polished(raw, '甲公司價格130元，我已經買。')
        self.assertEqual(pl._ev_norm(view), pl._ev_norm(raw))
        self.assertNotIn('130', view)
        self.assertNotIn('已經', view)

    def test_polished_punctuation_cannot_split_a_numeric_value(self):
        view = pl.punctuate_from_polished('價 格 130 元', '價格13.0元。')
        self.assertIn('130', view)
        self.assertNotIn('13.0', view)

    def test_view_and_evidence_spans_keep_the_original_source(self):
        raw = '甲 公 司 股 價 130 元 現 在 不 要 賣 乙 公 司 現 在 不 能 買 ' * 24
        polished = '甲公司股價130元，現在不要賣。乙公司現在不能買。' * 24
        with patch.object(pl, 'apply_spoken_name_scopes', side_effect=lambda text: (text, [])):
            view = pl.transcript_sources(raw, polished)['audit']
        self.assertEqual(pl._TX_SCOPED['raw'], raw)
        self.assertTrue(pl._TX_SCOPED['punctuation_only'])
        self.assertEqual(pl._ev_norm(view), pl._ev_norm(raw))
        signals = {'watch_watch': [{'name': '甲公司', 'evidence_refs': ['S0001', 'S0002']}]}
        row = pl.materialize_evidence(signals, view)['watch_watch'][0]
        self.assertEqual(row['evidence'], [raw[a:b] for a, b in row['_source_spans']])
        self.assertNotEqual(row['_source_spans'], row['_source_view_spans'])

    def test_well_punctuated_raw_is_not_changed(self):
        raw = '甲公司不要殺低。乙公司目前不進場。' * 20
        with patch.object(pl, 'apply_spoken_name_scopes', side_effect=lambda text: (text, [])):
            self.assertEqual(pl.transcript_sources(raw, raw)['audit'], raw)


class Coverage(unittest.TestCase):
    def test_weak_names_are_asked_together_not_published_automatically(self):
        inventory = [dict(name='甲股', official_name='甲股', code='1111', weak=True, refs=['S0001']),
                     dict(name='大量', official_name='大量', code='3167', weak=True, refs=['S0001'])]
        signals = {'ignored': []}
        before = copy.deepcopy(signals)
        with patch.object(pl, 'source_inventory', return_value=inventory):
            gaps = pl.inventory_gaps(signals, '一些原文')
        self.assertEqual(len(gaps), 2)
        self.assertTrue(all('先判公司身分' in gap for gap in gaps))
        self.assertEqual(signals, before)

    def test_explicit_exclusion_satisfies_inventory_coverage(self):
        item = dict(name='大量', official_name='大量', code='3167', weak=True, refs=['S0001'])
        with patch.object(pl, 'source_inventory', return_value=[item]):
            self.assertEqual(pl.inventory_gaps({'ignored': [{'code': '3167', 'name': '大量'}]}, '原文'), [])

    def test_group_pointer_is_quoteable_but_unrelated_market_context_is_not(self):
        passage = '大盤不要緊張。不要殺低。我講的是這兩支股票。都回年線了。甲公司，還有一支乙公司。'
        marked, _ = pl._verdict_mark([passage], {'甲公司'})
        self.assertIn('不要殺低', marked[0]['text'])
        plain, _ = pl._verdict_mark(['大盤不要緊張。甲公司今天只是碰季線。'], {'甲公司'})
        self.assertNotIn('大盤不要緊張', plain[0]['text'])

    def test_repeated_sound_alike_is_not_a_global_alias(self):
        before = dict(pl.CONFIRMED_NAMES)
        with patch.object(pl, '_CODE_MAP', {'3592': '瑞鼎', '1504': '東元', '5289': '宜鼎'}):
            self.assertEqual(pl.heard_form('瑞鼎', '瑞頂下跌之後融資大減瑞頂現在會往上'), '瑞頂')
            self.assertEqual(pl.heard_form('東元', '被動元件漲被動元件跌被動元件不要買'), '')
            self.assertEqual(pl.heard_form('宜鼎', '一定會漲一定要買一定會突破'), '')
        self.assertEqual(pl.CONFIRMED_NAMES, before)


class PositionEvidence(unittest.TestCase):
    def test_only_prior_unclosed_positions_override_watch_labels(self):
        trades = [['日期', '代號', '方向', '來源影片ID', '股票名稱'],
                  ['2026/10/19', '1111', '買入', 'v', '甲公司'],
                  ['2026/10/20', '2222', '賣出', 'v', '乙公司'],
                  ['2026/10/22', '3333', '買入', 'v', '未來公司']]
        holdings = [['日期', '代號', '股票名稱'], ['2026/10/19', '2222', '乙公司'],
                    ['2026/10/20', '4444', '丙公司'], ['2026/10/21', '5555', '今日錯列'],
                    ['2026/10/22', '6666', '未來錯列']]
        class Sheet:
            def __init__(self, values): self.values = values
            def get_all_values(self): return copy.deepcopy(self.values)
        class Book:
            def worksheet(self, name): return Sheet(trades if name == '操作紀錄' else holdings)
        with patch.object(pl, '_source_sale_scopes', return_value={}):
            _, _, positions = pl._verdict_known(Book(), '2026/10/21', with_open=True)
        self.assertEqual(set(positions), {'1111', '4444'})


if __name__ == '__main__': unittest.main()
