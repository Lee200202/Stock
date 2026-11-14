"""2026/11/13 週檢討（週五正式排程）：個股說明借用講者明講引出的下一檔（裁示三：說明對回本股；分類裁決基準 決策順序 7 不借鄰股）。

本週案例（原句取自 11/09、11/10 原稿，前後加無關的填充句）：
- 11/09 祥碩：「這個是我的股票嘛。5269祥碩嘛。好，現在離我的成本近。…我覺得它會漲回來。來，再告訴你們一檔，大模連續再吃貨的股票…
  再告訴你們一檔，我說籌碼最安定的」——後面講的是亞德客-KY，網站說明卻寫成祥碩「主力吃貨、籌碼安定」。
- 11/10 漢唐：「來我給你看漢糖的基本面…上半年EPS36塊…有哈我給你看行程的基本面前三GEPS30塊EPS全年大概有40塊的實力股票500多塊」
  ——「行程」是勤誠（聽錯，名稱表不認得），網站說明卻把全年 EPS 40、股價 500 多寫給漢唐。
規則：本股最後一次點名之後，講者明講引出另一檔（「再告訴你們一檔」「再來一檔」「我給你看<別的名稱>的基本面」）時，
本股的可引用範圍（_own_name_spans，個股說明補充與引用歸屬核對共用）停在那裡。

反例：「我給你看它的基本面」（代名詞，仍在講本股）、「我給你看<本股>的基本面」、引出語之後才點名本股、沒有引出語的一般段落。
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

# 11/09 原稿原句
ASMEDIA_1109 = (FILLER_A + '這個是我的股票嘛。5269祥碩嘛。好，現在離我的成本近。啊我這一波也有被它壓下來，可是我沒有賣。我覺得它會漲回來。'
                '來，再告訴你們一檔，大模連續再吃貨的股票，它都不會跟你講了。再告訴你們一檔，我說籌碼最安定的，還記不記得這一檔？'
                '上半年EPS29，這是Q2齁，Q2。' + FILLER_B)
# 11/10 原稿原句（手動稿，標點很少）
HANTANG_1110 = (FILLER_A + '你有沒有去看他的基本面了來我給你看漢糖的基本面我給你看漢糖的基本面上半年EPS36塊有沒有看到36塊？'
                '有哈我給你看行程的基本面前三GEPS30塊EPS全年大概有40塊的實力股票500多塊如果他今年EPS40塊股票500多塊你不會覺得太便宜嗎？' + FILLER_B)

ASMEDIA = {'name': '祥碩', 'code': '5269'}
HANTANG = {'name': '漢唐', 'code': '2404', '原始語音名稱': '漢糖'}


def own_text(row, snippet):
    return ''.join(norm[low:high] for norm, low, high in p._own_name_spans(row, snippet))


class NextStockIntroCut(unittest.TestCase):
    def test_asmedia_stops_before_next_stock(self):
        text = own_text(ASMEDIA, ASMEDIA_1109)
        self.assertIn('現在離我的成本近', text)
        self.assertIn('我覺得它會漲回來', text)
        self.assertNotIn('吃貨', text)
        self.assertNotIn('籌碼最安定', text)

    def test_hantang_keeps_own_eps_drops_next_stock(self):
        text = own_text(HANTANG, HANTANG_1110)
        self.assertIn('上半年EPS36塊', text)
        self.assertNotIn('全年大概有40塊', text)
        self.assertNotIn('500多塊', text)

    def test_pronoun_is_still_own_stock(self):
        snippet = (FILLER_A + '這個是我的股票嘛。5269祥碩嘛。來我給你看它的基本面，上半年EPS29，今年可以來到58塊。' + FILLER_B)
        self.assertIn('今年可以來到58塊', own_text(ASMEDIA, snippet))

    def test_look_at_its_chart_is_still_own_stock(self):
        # 11/13 原稿：「來，給你看了順準。昨天跌37，今天漲42…來，我給你看看它的K線，精彩到爆。」（命中回測時第一版誤中，已收窄）
        row = {'name': '神準', 'code': '3558', '原始語音名稱': '順準'}
        snippet = (FILLER_A + '來，給你看了順準。昨天跌37，今天漲42，你以為這個最精彩。來，我給你看看它的K線，精彩到爆。一天漲42塊，一天跌42塊。' + FILLER_B)
        self.assertIn('精彩到爆', own_text(row, snippet))
        self.assertIsNone(p._next_stock_intro_at(snippet, snippet.find('順準') + 2, ['順準']))

    def test_own_name_in_intro_is_not_a_switch(self):
        snippet = (FILLER_A + '5269祥碩嘛，我有沒有跟你們講？來我給你看祥碩的營收，今年可以來到58塊。' + FILLER_B)
        self.assertIn('今年可以來到58塊', own_text(ASMEDIA, snippet))

    def test_intro_before_own_name_keeps_own_paragraph(self):
        snippet = (FILLER_A + '來，再告訴你們一檔，5269祥碩，現在離我的成本近，我覺得它會漲回來，籌碼很安定。' + FILLER_B)
        self.assertIn('籌碼很安定', own_text(ASMEDIA, snippet))

    def test_plain_paragraph_unchanged(self):
        snippet = (FILLER_A + '5269祥碩嘛。好，現在離我的成本近。我覺得它會漲回來，季線在下面有支撐。' + FILLER_B)
        self.assertIn('季線在下面有支撐', own_text(ASMEDIA, snippet))

    def test_helper_skips_self_and_pronoun(self):
        text = '祥碩嘛我給你看它的基本面我給你看祥碩的營收我給你看行程的基本面'
        at = p._next_stock_intro_at(text, 3, ['祥碩'])
        self.assertEqual(text[at:at + 7], '我給你看行程的')


class ContextSourcesIntegration(unittest.TestCase):
    """個股說明補充只拿得到可引用範圍：下一檔的條件不會交給模型。"""

    def test_context_sources_exclude_next_stock(self):
        joined = ''.join(s['text'] for s in p._context_sources(ASMEDIA, [ASMEDIA_1109]))
        self.assertIn('現在離我的成本近', joined)
        self.assertNotIn('吃貨', joined)
        joined = ''.join(s['text'] for s in p._context_sources(HANTANG, [HANTANG_1110]))
        self.assertIn('EPS36', joined)
        self.assertNotIn('500多', joined)


if __name__ == '__main__':
    unittest.main()
