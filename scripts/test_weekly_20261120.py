"""2026/11/20 週檢討（週五正式排程）：兩條通用規則。

規則一（問卷裁決的程式對應）：同一份問卷答案同時答「持有者勿殺低」（holder_advice=hold_rebound）與
「本股被明講排除」（entry_for_new=prohibit、selection_intent=explicit_exclusion）時，新進場以明講不買為準，列觀望不碰。
- 11/20 國巨：問卷答 prohibit＋hold_rebound＋explicit_exclusion＋bearish，程式卻把 hold_rebound 一律改成 conditional，
  發布成觀望注意。原句「可是我還是不會買國巨，我帶會員不會買」「為什麼我堅持不碰國巨」。
- 依據：裁示紀錄 一-3 F2／B2（明講不碰、不買＝不碰）；11/02「給持有者別殺低的建議不等於新買進」；
  問卷提示詞本身「持有者建議…不能覆蓋明確的新進場等待」。
- 反例：D1／D2（只叫持有者別殺低、講者說自己沒有）仍是觀望注意；prohibit 而沒有 explicit_exclusion 的舊契約不動。

規則二（上下文範圍）：追高名單「昨天X漲停、今天X跌停」的共同教訓落在下一段時，也帶給名單裡的本股。
- 11/19 景碩：名單在 S0006，教訓「我昨天如果叫你買，你只有爽一天。」在 S0007；問卷只看到名單，答成只報漲跌。
- 依據：裁示紀錄 11/18 晚間取消 E1（追高反面教材列觀望不碰）。
- 反例：只念今天跌停的名單（沒有昨天漲停的對照）、教訓前面有換題句或本股不在列舉範圍。
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
with contextlib.redirect_stdout(io.StringIO()):
    import pipeline as p  # noqa: E402

FILLER_A = '好，我們先看一下今天的天氣，台北有一點下雨，大家出門記得帶傘。'
FILLER_B = '來，喝口水，我們等一下再講下一段，今天的節目比較長一點。'

# 11/20 原稿原句（國巨）
YAGEO_1120_HOLD = '原因是，手中如果持有現股的人，328以下，你也不用賣了，對不對？'
YAGEO_1120_NO_BUY = '可是我還是不會買國巨，我帶會員不會買。'
YAGEO_1120_RAW = FILLER_A + YAGEO_1120_HOLD + '昨天跌破328以後，我說，你也不用賣了，後面他也會彈回比328更高。' \
    + YAGEO_1120_NO_BUY + FILLER_B
# 11/19 原稿原句（國巨：只叫現股持有者別賣，沒有明講不買）
YAGEO_1119 = '只要你是現股的，今天跌破328，我可以跟你們講，不用賣了。他一定要跌破328。不用賣了，就抱著。可是我沒有叫你們加碼。'
YAGEO_1119_RAW = FILLER_A + YAGEO_1119 + FILLER_B

# 11/19 原稿原句（S0006 名單、S0007 教訓）
CHASE_1119_LIST = ('\n\n你買不到最低點的， 你一定要買在股票穩的時候。\n\n所以你急什麼？\n\n'
                   '很多投資人說，對啊，可是我看到漲就受不了啊，你看到漲就受不了， 就會 出現一個情況，'
                   '昨天 金源電漲停板，今天金源電跌停板，昨天四星科漲停板，今天四星科跌停板，昨天景碩漲停板，今天 景碩跌停板，'
                   '昨天你看到四星KY漲停板，搞不好今天就殺跌停。\n\n你有沒有發現昨天漲停的股票今天全部殺 全都再殺。\n\n昨天漲停了。')
CHASE_1119_NEXT = ('\n\n昨天漲停了。\n\n昨天還有可能罵我說，張總，你怎麼不叫我買金源電昨天漲停板，你看哦昨天是不是一根紅K棒？'
                   '張總，你怎麼不叫我買金源電？啊今天跌停板。\n\n我昨天如果叫你買， 你只有爽一天。\n\n'
                   '啊，隔天你的股票又全部崩下去。\n\n' + FILLER_B)
# 11/19 原稿另一段：只念今天跌停的名單（沒有昨天漲停的對照）
LIMIT_DOWN_LIST_1119 = ('有什麼股票跌停？你們都沒注意啊。\n\n來，給你看，哪些股票跌停？8046\n\n南電\n\n3189\n\n'
                        '3037這個叫做ABF載板，對不對？')


def klass(name, raw, quote, **fields):
    a = dict(about_itself=True, independent_analysis=True, content_role='independent_analysis',
             buy_today=False, sell_today=False, holding_now=False, past_trade=False,
             entry_for_new='none', now='none', tone='neutral', holder_advice='none', selection_intent='neutral',
             view_basis='speaker_judgment', quotes={'stance': [quote], 'analysis': [quote]})
    a.update(fields)
    return p.verdict_class(a, p._ev_norm(raw), [name])[0]


class HolderAdviceVsExplicitExclusion(unittest.TestCase):
    def test_1120_explicit_no_buy_beats_holder_advice(self):
        got = klass('國巨', YAGEO_1120_RAW, YAGEO_1120_NO_BUY,
                    entry_for_new='prohibit', now='prohibit', tone='bearish',
                    holder_advice='hold_rebound', selection_intent='explicit_exclusion')
        self.assertEqual(got, 'watch_avoid')

    def test_1119_holder_advice_alone_stays_watch(self):
        # 裁示 一-3 D1／D2：叫持有者別殺低、講者自己沒有也一樣是觀望注意；「沒有叫你們加碼」不是明講不買
        got = klass('國巨', YAGEO_1119_RAW, YAGEO_1119, holder_advice='hold_rebound')
        self.assertEqual(got, 'watch_watch')

    def test_prohibit_without_explicit_exclusion_keeps_the_old_contract(self):
        # 既有契約（test_model_stance_contract）：沒有答 explicit_exclusion 時，勿殺低照舊列注意
        got = klass('國巨', YAGEO_1119_RAW, YAGEO_1119, entry_for_new='prohibit', holder_advice='hold_rebound')
        self.assertEqual(got, 'watch_watch')

    def test_explicit_exclusion_without_prohibit_entry_keeps_holder_advice(self):
        # 只有偏空的排斥判斷、沒有明講不買（entry 不是 prohibit）：持有者建議照舊
        got = klass('國巨', YAGEO_1119_RAW, YAGEO_1119, entry_for_new='none', tone='bearish',
                    holder_advice='hold_rebound', selection_intent='explicit_exclusion')
        self.assertEqual(got, 'watch_watch')

    def test_prompt_states_the_two_axes_and_version_is_bumped(self):
        self.assertIn('新進場以明講不買為準（2026/11/20 週檢討）', p.ADJUDICATE_SYSTEM)
        self.assertIn('「沒有叫你們加碼」', p.ADJUDICATE_SYSTEM)
        self.assertTrue(p.VERDICT_VERSION.startswith('v48-20261120'))


class ChaseLessonInNextSegment(unittest.TestCase):
    def test_1119_lesson_in_next_segment_reaches_every_listed_stock(self):
        for name in ('景碩', '四星KY'):
            got = p._chase_conclusion_next(FILLER_A + CHASE_1119_LIST, CHASE_1119_NEXT, [name])
            self.assertEqual(got, '我昨天如果叫你買， 你只有爽一天。', name)

    def test_stock_outside_the_list_does_not_get_the_lesson(self):
        self.assertEqual(p._chase_conclusion_next(FILLER_A + CHASE_1119_LIST, CHASE_1119_NEXT, ['台積電']), '')

    def test_limit_down_list_without_yesterday_rally_is_not_a_chase_lesson(self):
        # 11/06 Q5：只念今天跌停的名單仍是只報漲跌
        self.assertEqual(p._chase_conclusion_next(FILLER_A + LIMIT_DOWN_LIST_1119, CHASE_1119_NEXT, ['南電']), '')

    def test_topic_change_before_the_lesson_cuts_the_scope(self):
        nxt = '\n\n我的會員今天都抱著，不用擔心。\n\n我昨天如果叫你買， 你只有爽一天。' + FILLER_B
        self.assertEqual(p._chase_conclusion_next(FILLER_A + CHASE_1119_LIST, nxt, ['景碩']), '')

    def test_no_lesson_in_next_segment(self):
        self.assertEqual(p._chase_conclusion_next(FILLER_A + CHASE_1119_LIST, FILLER_B + FILLER_A, ['景碩']), '')

    def test_1029_same_segment_conclusion_still_matches(self):
        text = (FILLER_A + '昨天玉晶光漲停，打開報紙全部玉晶光。來，今天玉晶光了，跌停。'
                '我問你，為什麼我昨天跟你們講，不要買？' + FILLER_B)
        m = p._CHASE_CONCLUSION.search(text)
        self.assertTrue(m)
        self.assertIn('玉晶光', p._shared_conclusion_scope(text, m.start(), m.end()))

    def test_verdict_passage_builder_uses_the_next_segment_helper(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("_chase_conclusion_next(_st, _seg_texts[_si + 1]", src)
        self.assertIn('content_role不是comparison_only（2026/11/20 週檢討）', p.ADJUDICATE_SYSTEM)


if __name__ == '__main__':
    unittest.main()
