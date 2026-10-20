"""部位否認、現況看法與晚補投票的正反例；不呼叫模型。"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault('SPREADSHEET_ID', 'offline-placeholder')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as p


class Boundaries(unittest.TestCase):
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
