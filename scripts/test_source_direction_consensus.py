"""已核對來源的中性收錄票不是反向立場；不呼叫模型。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from semantic_context import source_direction_consensus


def normalized(text):
    return ''.join(c for c in str(text) if c.isalnum())


def attempt(category, role, quote, **changes):
    result = {'class': category, 'answer': {'about_itself': True, 'semantic': {
        'entity': 'company', 'role': role, 'quote': quote,
    }}}
    result.update(changes)
    return result


class SourceConsensus(unittest.TestCase):
    def setUp(self):
        self.entry = {
            'tier': '未定', 'current': 'ignored', 'source_names': ['甲玻', '甲播', '1111'],
            'attempts': [
                attempt('ignored', 'neutral_description', '甲播昨天最強，今天下跌。'),
                attempt('watch_avoid', 'current_risk', '甲播今天追強後走弱，注意追價風險。'),
                attempt('', 'uncertain', ''),
                attempt('watch_avoid', 'current_risk', '甲播昨天強勢，今天轉弱，不宜追高。'),
                attempt('', 'uncertain', ''),
            ],
        }

    def test_two_named_risk_votes_and_one_company_neutral_can_set_direction(self):
        before = copy.deepcopy(self.entry)
        self.assertEqual(source_direction_consensus(self.entry, normalized)[0], 'watch_avoid')
        self.assertEqual(self.entry, before)

    def test_a_real_opposite_view_is_not_an_abstention(self):
        self.entry['attempts'][0] = attempt('watch_watch', 'current_view', '甲播現在可低檔布局。')
        self.assertIsNone(source_direction_consensus(self.entry, normalized))

    def test_company_use_objection_and_unbound_bullish_advice_do_not_recover(self):
        for mode in ('ordinary', 'anonymous', 'false_about', 'prior_identity', 'history'):
            with self.subTest(mode=mode):
                item = copy.deepcopy(self.entry)
                if mode == 'ordinary':
                    item['attempts'][0]['answer']['semantic']['entity'] = 'ordinary'
                elif mode == 'anonymous':
                    item['attempts'][1]['answer']['semantic']['quote'] = '應該在低檔布局其他股票，不要追當天最強勢。'
                elif mode == 'false_about':
                    item['attempts'][1]['answer']['about_itself'] = False
                elif mode == 'prior_identity':
                    item['identity_prior'] = 'ordinary'
                else:
                    item['attempts'][0] = attempt('history', 'historical_only', '甲播上週已經賣掉。')
                self.assertIsNone(source_direction_consensus(item, normalized))

    def test_no_trade_or_holding_created_and_no_manual_or_pending_identity_override(self):
        for category in ('buy', 'sell', 'holdings'):
            item = copy.deepcopy(self.entry)
            for a in item['attempts']:
                if a['class'] == 'watch_avoid':
                    a['class'] = category
            self.assertIsNone(source_direction_consensus(item, normalized))
        for updates in ({'tier': '人工裁決'}, {'current': 'holdings'}, {'trade_check': True}, {'source_names': []}):
            item = copy.deepcopy(self.entry)
            item.update(updates)
            self.assertIsNone(source_direction_consensus(item, normalized))

    def test_missing_votes_are_not_evidence_and_two_neutrals_reduce_fraction(self):
        self.entry['attempts'][0]['class'] = ''
        self.assertIsNone(source_direction_consensus(self.entry, normalized))
        self.entry['attempts'][0]['class'] = 'ignored'
        self.entry['attempts'].append(attempt('ignored', 'general_example', '甲播作為追高例子。'))
        self.assertIsNone(source_direction_consensus(self.entry, normalized))


if __name__ == '__main__':
    unittest.main()
