"""裁決的方向、主體與共享條件合約；不呼叫模型或寫入正式資料。"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline-placeholder')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as p


def answer(quote, **fields):
    row = dict(fact_schema=2, holding_actor='none', stance_action='outlook',
               scope_relation='own', context_quote='', buy_today=False,
               sell_today=False, holding_now=False, past_trade=False,
               about_itself=True, now='none', tone='bullish',
               quotes={'stance': [quote]})
    row.update(fields)
    return row


def classify(row, text, name, backgrounds=None):
    return p.verdict_class(row, p._ev_norm(text), [name], [text], backgrounds)[0]


class FactScope(unittest.TestCase):
    def test_exit_warning_is_not_entry_prohibition(self):
        text = '聯電130以下不准賣，成熟製程明天會止穩。'
        row = answer(text, stance_action='exit')
        self.assertEqual(classify(row, text, '聯電'), 'watch_watch')
        row['now'] = 'prohibit'
        self.assertEqual(classify(row, text, '聯電'), '')

    def test_institutional_position_never_becomes_our_holding(self):
        text = '外資手上有很多台積電，季線以下是機會。'
        row = answer(text, holding_actor='institution', holding_now=True,
                     quotes={'holding_now': [text], 'stance': [text]}, now='conditional', stance_action='entry')
        self.assertEqual(classify(row, text, '台積電'), 'watch_watch')

    def test_viewer_holding_not_member_position(self):
        text = '台化你手中有的繼續抱著，不用出。'
        row = answer(text, holding_actor='viewer', holding_now=True,
                     quotes={'holding_now': [text], 'stance': [text]}, stance_action='exit')
        self.assertEqual(classify(row, text, '台化'), 'watch_watch')

    def test_actual_holding_remains_supported(self):
        text = '我有瑞鼎，我有瑞鼎的會員繼續抱著。'
        row = answer(text, holding_actor='speaker', holding_now=True,
                     quotes={'holding_now': [text], 'stance': [text]})
        self.assertEqual(classify(row, text, '瑞鼎'), 'holdings')

    def test_shared_rule_needs_own_application_and_background_quote(self):
        text = '宜鼎是不是來到年線了？宜鼎來到年線。'
        background = '如果你的股票來到年線，你就好好的佈局，等它。'
        row = answer(text, now='conditional', stance_action='entry', scope_relation='shared_rule', context_quote=background)
        self.assertEqual(classify(row, text, '宜鼎', [background]), 'watch_watch')
        self.assertEqual(classify(row, text, '宜鼎', ['群聯上半年EPS很好。']), '')
        row['quotes']['stance'] = [background]
        self.assertEqual(classify(row, text, '宜鼎', [background]), '')

    def test_location_without_outlook_stays_unpublished(self):
        text = '創意是不是回來碰季線，在季線附近整理。'
        self.assertEqual(classify(answer(text, tone='neutral'), text, '創意'), 'ignored')

    def test_past_sale_and_current_condition_are_separate(self):
        text = '鴻海之前已經賣出。鴻海240元以下是買點。'
        row = answer('鴻海240元以下是買點。', past_trade=True,
                     now='conditional', stance_action='entry',
                     quotes={'past_trade': ['鴻海之前已經賣出。'], 'stance': ['鴻海240元以下是買點。']})
        self.assertEqual(classify(row, text, '鴻海'), 'watch_watch')
        row['now'], row['tone'] = 'none', 'neutral'
        self.assertEqual(classify(row, text, '鴻海'), 'history')

    def test_direction_cannot_invent_today_trade(self):
        text = '今天賣鴻海跟今天賣聯電一樣蠢，不准殺低。'
        row = answer(text, sell_today=True, stance_action='exit',
                     quotes={'sell_today': [text], 'stance': [text]})
        self.assertNotEqual(classify(row, text, '鴻海'), 'sell')

    def test_wrong_owner_and_fabricated_quote_are_unusable(self):
        text = '祥碩的MACD收斂，譜瑞今天有漲。'
        row = answer('祥碩的MACD收斂', scope_relation='other')
        self.assertEqual(classify(row, text, '譜瑞'), '')
        row = answer('譜瑞季線向上即將突破。')
        self.assertEqual(classify(row, text, '譜瑞'), '')

    def test_batch_rejects_missing_fact_protocol_without_extra_call(self):
        item = {'name': '宜鼎', 'heard': [], 'known': [], 'passages': [{'text': '宜鼎來到年線。', 'before': ''}]}
        old = {'id': '5289', 'about_itself': True, 'now': 'conditional'}
        with patch.object(p, 'call_gemini', return_value=json.dumps({'verdicts': [old]})) as call:
            self.assertEqual(p._ask_verdicts([('5289', item)], '2026/10/20', 0), {})
            self.assertEqual(call.call_count, 1)

    def test_existing_vote_threshold_not_relaxed(self):
        self.assertNotEqual(p.verdict_settle('ignored', ['watch_watch'], last=True)[0], '定案')
        self.assertEqual(p.verdict_settle('ignored', ['watch_watch'] * 3)[1], 'watch_watch')


if __name__ == '__main__':
    unittest.main()
