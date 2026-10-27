"""價位只提供查核線索；以來源、主體與上下文處理說明，無模型／網路呼叫。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('price_context_pipeline', ROOT / 'pipeline/pipeline.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PriceContextContract(unittest.TestCase):
    def test_historical_or_split_price_does_not_delete_technical_context(self):
        text = '甲公司先前成本1080元，目前回測年線，等待量縮止穩。'
        row = {'code': '1111', 'name': '甲公司', 'reason': text}
        signals = {'watch_watch': [row]}
        with patch.object(p, '_daily_k_cached', return_value={'1111': {'2026/10/21': [60, 50]}}):
            p.strip_implausible_price_claims(None, signals, '2026/10/21')
            p.price_reality_check(None, signals, '2026/10/21')
        self.assertEqual(row['reason'], text)

    def test_price_match_cannot_rename_company(self):
        raw = '甲公司今天來到237元，先前曾在238元買進。乙公司今天來到237元。'
        row = {'name': '甲公司', 'code': '1111', 'reason': '甲公司回測季線。'}
        signals = {'watch_watch': [row, {'name': '乙公司', 'code': '2222'}]}
        with patch.object(p, '_daily_k_cached', return_value={}), \
             patch.object(p, '_recent_band', side_effect=lambda _k, c, _d: (250, 230) if c == '2222' else (45, 40)):
            p.recheck_names_by_price(None, signals, raw, '2026/10/21')
        self.assertEqual((row['code'], row['name']), ('1111', '甲公司'))
        self.assertNotIn('aliases', row)

    def review(self, status, source_quote, subject='', note_quote='成本1080元', **extra):
        raw = '甲公司目前回測年線，等待量縮止穩。乙公司先前成本1080元，現在跌破季線。'
        text = '甲公司成本1080元，目前回測年線，等待量縮止穩。'
        row = {'name': '甲公司', 'code': '1111', 'reason': text, 'evidence': [raw[:21]]}
        signals = {'watch_watch': [row]}
        answer = dict(id='n0', text=text, meaning_preserved=True, category_consistent=True,
                      source_supported=status == 'supported', written_style_ok=True, issues_found=[],
                      source_review=dict(status=status, note_quote=note_quote, source_quote=source_quote,
                                         subject=subject, explanation='先核主體與時間，再看價位是否可能為誤聽。'))
        answer.update(extra)
        with patch.object(p, 'call_gemini', return_value=json.dumps({'texts': [answer]}, ensure_ascii=False)) as call, \
             patch.object(p, 'budget_left', return_value=1000), patch.object(p, 'GEMINI_KEYS', ['offline']):
            p._polish_public_texts(signals, raw, '2026/10/21')
        return signals, row, raw, call

    def test_confirmed_cross_entity_is_isolated_with_source_and_original_preserved(self):
        signals, row, raw, call = self.review('cross_entity', '乙公司先前成本1080元，現在跌破季線。', '乙公司')
        before = copy.deepcopy(row)
        p.quarantine_public_rows(signals, raw)
        self.assertEqual(signals['watch_watch'], [])
        self.assertEqual(signals['uncertain'][0]['reason'], before['reason'])
        self.assertEqual(signals['uncertain'][0]['code'], '1111')
        payload = json.loads(call.call_args.args[1])
        self.assertEqual(payload['source'], raw)
        self.assertTrue(payload['texts'][0]['context'])

    def test_fabricated_or_incomplete_cross_entity_evidence_cannot_delete(self):
        for quote, subject, note in [('乙公司從未說過的1080元成本', '乙公司', '成本1080元'),
                                     ('乙公司先前成本1080元，現在跌破季線。', '丙公司', '成本1080元'),
                                     ('乙公司先前成本1080元，現在跌破季線。', '乙公司', '不存在的說明')]:
            with self.subTest(quote=quote, subject=subject):
                signals, row, raw, _ = self.review('cross_entity', quote, subject, note)
                _, hard, _ = p.quality_overview(signals, raw, check_chapters=False)
                self.assertFalse(any('上下文已確認跨股' in e for e in hard))

    def test_asr_uncertainty_is_not_a_company_rejection_or_digit_repair(self):
        signals, row, raw, _ = self.review('asr_uncertain', '乙公司先前成本1080元，現在跌破季線。')
        self.assertEqual(row['code'], '1111')
        self.assertIn('1080', row['reason'])
        _, hard, soft = p.quality_overview(signals, raw, check_chapters=False)
        self.assertFalse(any('上下文已確認跨股' in e for e in hard))
        self.assertTrue(any('聽寫' in e for e in soft))

    def test_changed_text_or_source_invalidates_attribution_decision(self):
        for changed_source in (False, True):
            signals, row, raw, _ = self.review('cross_entity', '乙公司先前成本1080元，現在跌破季線。', '乙公司')
            if changed_source:
                raw += '後來更正了主體。'
            else:
                row['reason'] = '甲公司目前回測年線，等待量縮止穩。'
            _, hard, _ = p.quality_overview(signals, raw, check_chapters=False)
            self.assertFalse(any('上下文已確認跨股' in e for e in hard))


if __name__ == '__main__':
    unittest.main()
