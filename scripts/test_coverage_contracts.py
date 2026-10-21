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


class SemanticContracts(unittest.TestCase):
    def answer(self, entity='company', role='current_view', direction='positive', quote='甲公司雖然較弱但股本小拉升較快'):
        return {'semantic': dict(entity=entity, role=role, direction=direction, scope='self',
                                 members=['甲公司'], anchor='甲公司雖然較弱', quote=quote)}

    def test_concession_and_non_dictionary_outlook_can_support_watch(self):
        a = self.answer()
        result = pl.semantic_stance(a, ['甲公司'], ['甲公司雖然較弱但股本小拉升較快'])
        self.assertEqual(result[0], 'watch_watch')

    def test_risk_example_does_not_require_a_ban_keyword(self):
        a = self.answer(role='current_risk', direction='negative')
        self.assertEqual(pl.semantic_stance(a, ['甲公司'], [a['semantic']['quote']])[0], 'watch_avoid')

    def test_general_mechanism_is_not_a_current_buy_view(self):
        a = self.answer(role='general_example')
        self.assertEqual(pl.semantic_stance(a, ['甲公司'], [a['semantic']['quote']])[0], 'ignored')

    def test_ordinary_word_cannot_borrow_a_positive_paragraph(self):
        a = self.answer(entity='ordinary')
        self.assertEqual(pl.semantic_stance(a, ['甲公司'], [a['semantic']['quote']])[0], 'ignored')

    def test_anchor_member_and_exact_source_are_mandatory(self):
        a = self.answer()
        for field, value in [('anchor', '另一家公司'), ('quote', '甲公司明天一定漲停不存在'),
                             ('members', ['乙公司']), ('role', 'buy_today'), ('scope', 'guessed')]:
            with self.subTest(field=field):
                b = copy.deepcopy(a); b['semantic'][field] = value
                self.assertIsNone(pl.semantic_stance(b, ['甲公司'], [a['semantic']['quote']]))

    def test_distant_quotation_does_not_bind_to_target(self):
        a = self.answer(quote='這裡是別的股票之後會上漲')
        self.assertIsNone(pl.semantic_stance(a, ['甲公司'], ['甲公司雖然較弱'+'甲'*700+a['semantic']['quote']]))

    def test_missing_or_invalid_semantics_is_not_a_negative_vote(self):
        self.assertIsNone(pl.semantic_stance({}, ['甲公司'], ['甲公司']))
        self.assertIsNone(pl.semantic_stance(self.answer(), ['甲公司'], []))

    def test_shared_prior_conclusion_has_explicit_member_anchor(self):
        a = self.answer(quote='這兩支股票年線附近不應該恐慌殺低')
        a['semantic'].update(scope='shared', members=['甲公司', '乙公司'])
        self.assertEqual(pl.semantic_stance(a, ['甲公司'], [a['semantic']['quote']+'。甲公司雖然較弱，還有乙公司'])[0], 'watch_watch')

    def test_discovery_is_a_hidden_candidate_not_a_position_or_price(self):
        text = '富橋昨天最強今天追高的人有風險'
        response = {'candidates': [{'code': '1815', 'heard': '富橋', 'quote': text},
                                   {'code': '9999', 'heard': '假公司', 'quote': '假公司不存在'}]}
        signals = {}
        with patch.object(pl, '_CODE_MAP', {'1815': '富喬'}), patch.object(pl, 'GEMINI_KEYS', ['offline']), \
             patch.object(pl, 'budget_left', return_value=1000), patch.object(pl, 'call_gemini', return_value=__import__('json').dumps(response,ensure_ascii=False)):
            pl.discover_source_candidates(signals, text, '2026/10/21')
        self.assertEqual(list(signals), ['ignored'])
        self.assertEqual(signals['ignored'][0]['code'], '1815')
        self.assertNotIn('price', signals['ignored'][0])
        self.assertNotIn('富橋', pl.CONFIRMED_NAMES)


if __name__ == '__main__': unittest.main()
