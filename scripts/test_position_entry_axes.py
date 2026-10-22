"""剩餘部位、暫不進場與分類快取的契約；不連線、不呼叫模型。"""
import copy
import json
import unittest
from test_coverage_contracts import pl


def restriction():
    return {'class': 'watch_avoid', 'answer': {'about_itself': True, 'semantic': {
        'entity': 'company', 'role': 'current_view', 'entry_restricted_now': True,
        'anchor': '甲公司已賣部分', 'quote': '甲公司已賣部分目前不急於買回，條件尚未出現。'}}}


class PositionEntryAxes(unittest.TestCase):
    def entry(self):
        return {'name': '甲公司', 'cls': 'watch_avoid', 'tier': '裁決', 'open': True,
                'new': True, 'enforce': True, 'fp': 'source-position-version',
                'attempts': [restriction(), restriction(), restriction()]}

    def test_open_position_and_verified_entry_restriction_keep_both_rows(self):
        entry = self.entry()
        signals = {'watch_avoid': [{'code': '1111', 'name': '甲公司', '_date': '2026/10/15',
                                    'reason': '暫不買回'}], '_verdicts': {'1111': entry}}
        pl.finalize_position_identity(signals['_verdicts'], signals)
        self.assertEqual(entry['cls'], 'holdings')
        self.assertEqual(entry['also'][0], 'watch_avoid')
        pl.apply_verdicts(signals, '2026/10/15')
        self.assertEqual([r['code'] for r in signals['holdings']], ['1111'])
        self.assertEqual([r['code'] for r in signals['watch_avoid']], ['1111'])
        self.assertFalse(signals.get('buy') or signals.get('sell'))

    def test_no_position_pending_denial_and_insufficient_support_do_not_add_holdings(self):
        changes = [{'open': False}, {'not_held': '原文明講沒有持有'}, {'tier': '未定'},
                   {'tier': '人工裁決'}, {'attempts': [restriction()]},
                   {'attempts': [restriction(), {'class': 'ignored'}, {'class': 'ignored'}]},
                   {'trade_check': 'sell'}]
        for update in changes:
            with self.subTest(update=update):
                entry = dict(self.entry(), **update)
                pl.finalize_position_identity({'1111': entry}, {})
                self.assertEqual(entry['cls'], 'watch_avoid')
                self.assertNotIn('also', entry)

    def test_holding_votes_do_not_erase_the_same_answers_entry_restriction(self):
        entry = self.entry()
        entry['cls'] = 'holdings'
        for attempt in entry['attempts']:
            attempt['class'] = 'holdings'
        pl.finalize_position_identity({'1111': entry}, {})
        self.assertEqual(entry['cls'], 'holdings')
        self.assertEqual(entry['also'][0], 'watch_avoid')

    def test_current_risk_alone_is_not_a_verified_wait_to_reenter(self):
        entry = self.entry()
        for attempt in entry['attempts']:
            attempt['answer']['semantic']['role'] = 'current_risk'
        pl.finalize_position_identity({'1111': entry}, {})
        self.assertNotIn('also', entry)

    def test_compound_cache_requires_batched_revalidation(self):
        stored = []
        class Sheet:
            def append_rows(self, rows, **kwargs): stored.extend(copy.deepcopy(rows))
            def get_all_values(self): return [pl.VERDICT_HEADERS] + stored
        class Book:
            def worksheet(self, name): return Sheet()
        entry = self.entry()
        pl.finalize_position_identity({'1111': entry}, {})
        entry['answer'] = {'holdings': {'answer': {'long': '原文' * 2000}}}
        pl.save_verdicts(Book(), '2026/10/15', {'_verdicts': {'1111': entry}})
        self.assertTrue(json.loads(stored[0][7])['_requires_multi_axis_review'])
        self.assertEqual(json.loads(stored[0][7])['separate_stance'], 'watch_avoid')
        self.assertLess(len(stored[0][7]), 1800)
        self.assertEqual(pl._verdict_cache(Book(), '2026/10/15')['1111']['by_fp'], {})

    def test_normal_single_axis_cache_is_still_reused(self):
        row = ['2026/10/15', '', '1111', '甲公司', '觀望注意', '裁決', '', '{}', 'fp', 'v', '', '']
        class Sheet:
            def get_all_values(self): return [pl.VERDICT_HEADERS, row]
        class Book:
            def worksheet(self, name): return Sheet()
        self.assertEqual(pl._verdict_cache(Book(), '2026/10/15')['1111']['by_fp'], {('fp', 'v'): 'watch_watch'})


if __name__ == '__main__':
    unittest.main()
