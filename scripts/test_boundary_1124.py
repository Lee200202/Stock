"""2026/11/24 管理者邊界（雲端工作階段與 LINE 裁示）的離線契約。

當天網站錯在這幾處，改成通用規則後用當天原稿原句當反例：
  1 價位寫成國字數字（國巨「三百二十八以下」）→ 寫入前換成阿拉伯數字，換不到的記疑點（另見 test_number_digits_1112.py）
  2 公開文字用「講者」當主詞 → 推送前檢查與章節核對都擋
  3 追高教材的引句要看得出先漲後跌、別追；ETF 在跌停第 6 天出清、隨後漲停不是追高教材（創意）
  4 紀錄上未出場的部位今天被點名，問卷只答「講本股但只報漲跌」→ 會員持股（力積電，管理者 LINE 裁示）
  5 問卷與排除覆核的提示詞寫明：持有者反問勿賣＝勿殺低（AES-KY）、ADR 推論明天有高點＝向前偏多（聯電、日月光）
規則只看句型與結構化答案，不看股名；另用「甲公司」驗反例。模型會不會照提示詞填，由真實模型重播驗收。
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as p  # noqa: E402
from preflight import public_text_problems  # noqa: E402

# 11/24 原稿原句（sha256 a78c1cec…）
CHUANGYI = ['我一直跟你們講， 這一波的性化。建策。窗湖。創意。全部都是ETF所單拱上去的。啊鎖單拱上去沒人要買，自己殺最低點。',
            '我們來看今天創意，跌停6天，你賣在第六天。然後今天創意漲停板。']
LIJI = '否則，我昨天怎麼會講在戶記憶體，還有所謂的成熟製程的聯電立積電。這些都漲停啊，漲停就漲停啊。'


def answer(raw, **fields):
    a = dict(about_itself=True, independent_analysis=False, content_role='independent_analysis',
             buy_today=False, sell_today=False, holding_now=False, past_trade=False,
             entry_for_new='none', now='none', tone='neutral', holder_advice='none', selection_intent='neutral',
             forward_view='none', view_basis='speaker_judgment', quotes={'stance': [raw], 'analysis': []})
    a.update(fields)
    return a


def klass(name, raw, **fields):
    return p.verdict_class(answer(raw, **fields), p._ev_norm(raw), [name])[0]


class NumbersAndSpeaker(unittest.TestCase):
    def test_price_context_numbers_become_digits(self):
        self.assertEqual(p.digits_for_reading('國巨在三百二十八以下時，持有現股者無需賣出。'), '國巨在328以下時，持有現股者無需賣出。')
        self.assertEqual(p.digits_for_reading('當時指出在二千二百八十以下皆可買進台積電'), '當時指出在2280以下皆可買進台積電')
        self.assertEqual(p.digits_for_reading('做了三十年'), '做了三十年')

    def test_public_text_rejects_speaker_subject_and_chinese_prices(self):
        self.assertTrue(public_text_problems('講者說這一檔今天沒有漲停', 'x'))
        self.assertTrue(public_text_problems('張震指出328以下不用賣', 'x'))
        self.assertTrue(public_text_problems('國巨在三百二十八以下時不用賣', 'x'))
        self.assertEqual(public_text_problems('國巨328以下融資全部斷頭，持有現股的人要忍耐。做了三十年。', 'x'), [])
        self.assertEqual(public_text_problems('提醒手中有甲公司的人，回到起漲點不要在這裡賣。', 'x'), [])


class ChaseLesson(unittest.TestCase):
    def test_chase_quote_needs_rise_then_fall_or_chase_words(self):
        self.assertFalse(p.chase_quote_ok(CHUANGYI[0]))
        self.assertFalse(p.chase_quote_ok(CHUANGYI[1]))           # 先跌後漲
        self.assertTrue(p.chase_quote_ok('甲公司昨天漲停，今天跌停，你昨天買什麼意思'))
        self.assertTrue(p.chase_quote_ok('甲公司不要追利多'))
        self.assertTrue(p.chase_quote_ok('昨天玉晶光漲停，打開報紙全部玉晶光。來，今天玉晶光了，跌'))   # 模型截短的引句

    def test_etf_sold_at_the_low_is_not_a_chase_lesson(self):
        raw = CHUANGYI[1]
        self.assertNotEqual(klass('創意', raw, view_basis='chase_lesson', about_itself=False), 'watch_avoid')
        self.assertEqual(klass('創意', raw, view_basis='price_move_only'), 'ignored')

    def test_real_chase_lesson_still_avoid(self):
        raw = '甲公司昨天漲停，今天跌停，你昨天買什麼意思？不要追利多。'
        self.assertEqual(klass('甲公司', raw, view_basis='chase_lesson'), 'watch_avoid')


class OpenPositionNamed(unittest.TestCase):
    def entry(self, classes, **answers):
        rounds = [{'class': c, 'answer': answers.get(str(i), {'about_itself': True, 'content_role': 'comparison_only'})}
                  for i, c in enumerate(classes)]
        return {'open': True, 'round_answers': rounds, 'votes': classes}

    def test_record_held_and_named_is_holdings(self):
        e = self.entry(['ignored', 'holdings', 'ignored'])
        self.assertTrue(p.open_position_named_holding(e, LIJI, ['力積電', '立積電']))

    def test_not_open_or_denied_or_ordinary_word_is_not(self):
        e = self.entry(['ignored', 'ignored'])
        e['open'] = False
        self.assertEqual(p.open_position_named_holding(e, LIJI, ['力積電']), '')
        e = self.entry(['ignored', 'ignored'])
        e['not_held'] = True
        self.assertEqual(p.open_position_named_holding(e, LIJI, ['力積電']), '')
        e = self.entry(['ignored', 'ignored'], **{'1': {'about_itself': False}})
        self.assertEqual(p.open_position_named_holding(e, LIJI, ['力積電']), '')
        e = self.entry(['ignored', 'ignored'], **{'0': {'about_itself': True, 'content_role': 'ordinary_word'}})
        self.assertEqual(p.open_position_named_holding(e, LIJI, ['力積電']), '')

    def test_avoid_or_history_votes_block_it(self):
        self.assertEqual(p.open_position_named_holding(self.entry(['ignored', 'watch_avoid']), LIJI, ['力積電']), '')
        self.assertEqual(p.open_position_named_holding(self.entry(['ignored', 'history']), LIJI, ['力積電']), '')
        self.assertEqual(p.open_position_named_holding(self.entry(['ignored']), LIJI, ['力積電']), '')

    def test_sold_today_blocks_it(self):
        e = self.entry(['ignored', 'ignored'])
        self.assertEqual(p.open_position_named_holding(e, '我今天賣力積電，賣在40塊。', ['力積電']), '')


class OpenPositionWatchVotes(unittest.TestCase):
    def test_open_position_with_watch_votes_is_holdings(self):
        self.assertEqual(p.verdict_settle('watch_watch', ['watch_watch', 'watch_watch'], open_position=True)[:2], ('定案', 'holdings'))

    def test_without_record_stays_watch(self):
        self.assertEqual(p.verdict_settle('watch_watch', ['watch_watch', 'watch_watch'], open_position=False)[1], 'watch_watch')

    def test_avoid_votes_are_not_turned_into_holdings(self):
        self.assertNotEqual(p.verdict_settle('watch_avoid', ['watch_avoid', 'watch_avoid'], open_position=True)[1], 'holdings')


class OrdinaryWords(unittest.TestCase):
    def test_reduplication_and_volume_talk_are_ordinary(self):
        self.assertTrue(p._only_ordinary_word('安心', '1259', '沒有最壞的情況出現，安安心心，坐在那邊，抱著你的股票。'))
        self.assertTrue(p._only_ordinary_word('大量', '3167', '他又把這一根大量站回去啊，底部暴大量，他把大量都站回去啦'))
        self.assertFalse(p._only_ordinary_word('大量', '3167', '大量這一檔，季線翻揚'))
        self.assertFalse(p._only_ordinary_word('國巨', '2327', '328以下你有國巨的就不用賣了'))

    def test_prompt_marks_ordinary_words(self):
        self.assertIn('安安心心', p.ADJUDICATE_SYSTEM)


class ChasePublicationGate(unittest.TestCase):
    def test_already_classified_own_reversal_survives_final_gate(self):
        for text in ('甲公司昨日漲335元後今日跌300元。', '甲公司昨天漲停，今天跌停。'):
            row = {'name':'甲公司','code':'1111','reason':text}
            out = p.gate_watch_conclusions({'watch_avoid':[row]})
            self.assertEqual(out['watch_avoid'], [row])

    def test_price_only_or_rebound_does_not_become_a_chase_conclusion(self):
        for text in ('甲公司今天跌300元。', '甲公司昨天跌停，今天漲停。', '乙公司昨天漲停，今天跌停。'):
            out = p.gate_watch_conclusions({'watch_avoid':[{'name':'甲公司','code':'1111','reason':text}]})
            self.assertEqual(out['watch_avoid'], [])
        out = p.gate_watch_conclusions({'watch_watch':[{'name':'甲公司','code':'1111','reason':'甲公司昨天漲停，今天跌停。'}]})
        self.assertEqual(out['watch_watch'], [])


class Prompts(unittest.TestCase):
    def test_adjudicate_prompt_states_the_1124_boundaries(self):
        s = p.ADJUDICATE_SYSTEM
        for key in ('你在賣什麼意思', 'holder_advice=hold_rebound', 'ADR', 'forward_view=rise', 'ETF', '先跌後漲', 'open_position'):
            self.assertIn(key, s)
        self.assertTrue(p.VERDICT_VERSION.startswith('v49'))

    def test_excluded_review_and_writing_prompts(self):
        self.assertIn('ADR', p.EXCLUDED_REVIEW_SYSTEM)
        self.assertIn('不用賣', p.EXCLUDED_REVIEW_SYSTEM)
        self.assertIn('數字一律寫阿拉伯數字', p.POLICY)
        self.assertIn('三百二十八', p.STOCK_CONTEXT_SYSTEM)


if __name__ == '__main__':
    unittest.main()
