"""部位否認、現況看法與晚補投票的正反例；不呼叫模型。"""
import os
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline-placeholder')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as p


class Boundaries(unittest.TestCase):
    def test_source_id_keeps_misheard_name_verbatim(self):
        text = '你今天不應該再買【連電】了，這是缺口壓力區。'
        cites = p.verdict_citations([{'text': text, 'before': '不相干的上文。'}])
        a = {'about_itself': True, 'now': 'prohibit', 'tone': 'bearish',
             'quote_ids': {k: [cites[0]['id']] if k == 'stance' else []
                           for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}}
        bound = p.bind_verdict_citations(a, cites)
        self.assertIn('連電', bound['quotes']['stance'][0])
        self.assertNotIn('聯電', bound['quotes']['stance'][0])
        self.assertEqual(p.verdict_class(bound, p._ev_norm(text.replace('【', '').replace('】', '')), ['連電'], [text])[0], 'watch_avoid')

    def test_fabricated_id_never_falls_back_to_generated_quote(self):
        a = {'about_itself': True, 'quotes': {'stance': ['甲公司可以買']},
             'quote_ids': {k: ['other-stock-id'] if k == 'stance' else []
                           for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}}
        bound = p.bind_verdict_citations(a, [{'id': 'p0s0c0', 'text': '甲公司不宜追高。'}])
        self.assertEqual(p.verdict_class(bound, p._ev_norm('甲公司可以買'))[0], '')

    def test_batch_resolves_ids_without_an_additional_model_request(self):
        item = {'name': '甲公司', 'heard': [], 'known': [], 'passages': [{'text': '甲公司不要追高。', 'before': ''}]}
        calls = []
        def fake(system, payload, **kwargs):
            data = json.loads(payload); calls.append(data)
            cite = data['stocks'][0]['citations'][0]['id']
            refs = {k: [cite] if k == 'stance' else [] for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}
            return json.dumps({'verdicts': [{'id': '1111', 'quote_ids': refs}]})
        with patch.object(p, 'call_gemini', side_effect=fake):
            out = p._ask_verdicts([('1111', item)], '2026/10/20', 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out['1111']['quotes']['stance'], ['甲公司不要追高。'])

    def test_position_denial_does_not_erase_a_current_view(self):
        for cls in ('watch_watch', 'watch_avoid'):
            entry = {'current': cls, 'not_held': '原文明講沒有持有', 'not_held_fallback': 'ignored'}
            base = p.verdict_comparison_base(entry)
            self.assertEqual(base, cls)
            self.assertEqual(p.verdict_settle(base, [cls])[:2], ('定案', cls))

    def test_unsupported_holding_does_not_get_same_relaxation(self):
        entry = {'current': 'holdings', 'not_held': '原文明講沒有持有', 'not_held_fallback': 'ignored'}
        base = p.verdict_comparison_base(entry)
        self.assertEqual(base, 'ignored')
        self.assertNotEqual(p.verdict_settle(base, ['holdings'], last=True)[0], '定案')

    def test_late_candidate_remains_hidden_until_supported(self):
        entry = {'current': 'watch_watch', 'late': 'watch_watch'}
        self.assertEqual(p.verdict_comparison_base(entry), 'ignored')
        self.assertEqual(p.verdict_settle('ignored', ['history'], late=True)[0], '再問')
        self.assertEqual(p.verdict_settle('ignored', ['history', 'history'], late=True)[:2], ('定案', 'ignored'))
        self.assertNotEqual(p.verdict_settle('ignored', ['watch_watch'], last=True, late=True)[0], '定案')

    def test_invalid_or_conflicting_votes_do_not_create_a_public_view(self):
        for votes in (['', '', ''], ['history', 'watch_watch', ''], ['holdings', '', 'watch_avoid']):
            self.assertNotEqual(p.verdict_settle('ignored', votes, last=True, late=True)[0], '定案')

    def test_transaction_direction_still_needs_original_execution(self):
        text = '今天賣鴻海跟今天賣聯電一樣蠢，不准殺低。'
        answer = {'sell_today': True, 'about_itself': True, 'now': 'none', 'tone': 'bullish',
                  'quotes': {'sell_today': [text], 'stance': [text]}}
        self.assertNotEqual(p.verdict_class(answer, p._ev_norm(text), ['鴻海'], [text])[0], 'sell')


if __name__ == '__main__':
    unittest.main()
