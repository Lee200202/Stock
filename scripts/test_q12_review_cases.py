"""q12 審閱反例（2026/10/29 另一位審閱者提出的驗收矩陣）：不呼叫模型或網路。

預期在寫測試時先固定；這些是合成的事故壓力案例，不是未見交易日。
T：今日成交證據；I：公司身分；S：共同結論範圍；C：時態核對；G：思考檔位。
"""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import unittest

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('q12_review_pipeline', ROOT/'pipeline/pipeline.py')
pl = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(pl)

QIHONG = ['奇鋐', '3017']
# 2026/10/29 原稿：三檔分句列舉後才接共同結論。
PASSAGE_1029 = ('所以你一定要按部就班一步一步來。好來，昨天玉晶光漲停，打開報紙全部玉晶光。來，今天玉晶光了，跌停。'
                '昨天金策漲停，打開報紙全部是金策的利多。金策今天跌停。對不對？昨天聯電漲5塊，一堆利多，今天聯電跌停。')
CONCLUSION_1029 = '我問你，張震是多來M哦？為什麼我昨天跟你們講，不要買，不要買。'


def buys(text, names=QIHONG):
    return pl.explicit_today_trades(text, names, 'buy')


def sells(text, names=QIHONG):
    return pl.explicit_today_trades(text, names, 'sell')


def in_scope(text, conclusion, name):
    i = text.index(conclusion)
    return name in pl._shared_conclusion_scope(text, i, i + len(conclusion))


class TodayTradeEvidence(unittest.TestCase):
    def test_t01_t02_reveal_waiting_for_a_price_is_not_a_trade(self):
        self.assertFalse(buys('我們今天買哪一檔股票？就是3017奇鋐，等回測到720才下單。'))
        self.assertFalse(buys('今天買哪檔：3017奇鋐，等回測到720才下單。'))

    def test_t03_an_order_is_not_a_fill(self):
        self.assertFalse(buys('今天買哪檔？3017奇鋐，已下單但尚未成交。'))

    def test_t04_a_partial_fill_of_this_stock_counts(self):
        self.assertTrue(buys('我們今天買哪一檔股票？就是3017奇鋐，已成交一半，剩下等跌到720才下單。'))

    def test_t05_another_stocks_fill_does_not_count(self):
        self.assertFalse(buys('我們今天買哪一檔股票？就是3017奇鋐，尚未下單，2330台積電已成交一半。'))
        self.assertFalse(buys('今天買哪檔？3017奇鋐，尚未下單，但台積電已成交。'))

    def test_t06_an_earlier_fill_does_not_count_today(self):
        self.assertFalse(buys('今天買哪檔？3017奇鋐，上週已成交一半，今天尚未下單。'))

    def test_t07_a_viewers_purchase_is_not_ours(self):
        self.assertFalse(buys('有觀眾說他今天買了3017奇鋐，問我怎麼辦？'))

    def test_t08_yesterdays_sale_is_not_todays(self):
        self.assertFalse(sells('我昨天已經賣掉奇鋐了，今天它又漲。'))

    def test_t09_telling_viewers_not_to_sell_is_neither(self):
        self.assertFalse(sells('不要殺低奇鋐，抱著就好。'))
        self.assertFalse(buys('不要殺低奇鋐，抱著就好。'))

    def test_t10_a_clear_notice_to_members_still_counts(self):
        self.assertTrue(buys('今天早上我通知會員買進3017奇鋐。'))

    def test_q12_positive_reveal_still_counts(self):
        self.assertTrue(buys('今天早盤大盤拉回跌了150點，我就帶會員逢低進場布局一檔散熱模組、日K線剛突破季線的股票。'
                             '我們今天新買進哪一檔股票？就是3017奇鋐！買在平盤附近720塊，60分K線剛剛打出漂亮的W底突破。'))

    def test_tomorrow_does_not_borrow_today(self):
        self.assertFalse(buys('今天大盤跌了很多。明天我再買奇鋐。'))


class CompanyIdentity(unittest.TestCase):
    """方向裁決不能把一般詞或跨詞拼接改列公開；真公司有本股語境時照常。"""

    def promote(self, name, code, transcript):
        with contextlib.redirect_stdout(io.StringIO()):
            signals = {'ignored': [{'name': name, 'code': code, '_date': '2026/10/29'}], '_verdicts': {
                code: {'cls': 'watch_avoid', 'enforce': True, 'name': name, 'current': 'ignored', 'basis': '2/2',
                       'identity_confirmed': pl._weak_name_explicitly_stock(name, code, transcript)}}}
            pl.apply_verdicts(signals, '2026/10/29')
        return [r.get('code') for r in signals.get('watch_avoid') or []]

    def test_i01_world_number_one_is_not_vis(self):
        self.assertEqual(self.promote('世界', '5347', '昨天的最低點532，你世界第一名，對不對？'), [])

    def test_i02_guoju_rally_is_not_giant(self):
        self.assertEqual(self.promote('巨大', '9921', '昨天國巨大漲，所有人又講國巨活起來了。'), [])

    def test_i03_real_companies_still_promote(self):
        self.assertEqual(self.promote('世界', '5347', '5347世界先進今天跌破季線，不要碰。'), ['5347'])
        self.assertEqual(self.promote('巨大', '9921', '9921巨大今天跌破月線，不要碰。'), ['9921'])


class SharedConclusionScope(unittest.TestCase):
    def test_s01_listed_sentences_share_the_conclusion(self):
        for name in ('玉晶光', '金策', '聯電'):
            self.assertTrue(in_scope(PASSAGE_1029 + CONCLUSION_1029, CONCLUSION_1029, name), name)
        text = ('你看今天早盤，2383台光電開高走低跌破月線，3037欣興爆量長黑殺破季線，8046南電外資連賣五天破底。'
                '這種籌碼亂掉、均線蓋頭反壓的股票，我告訴你們，千萬不要去接刀、不要買！')
        for name in ('台光電', '欣興', '南電'):
            self.assertTrue(in_scope(text, '這種籌碼亂掉、均線蓋頭反壓的股票，我告訴你們，千萬不要去接刀、不要買！', name), name)

    def test_s02_zailai_continues_the_list(self):
        self.assertTrue(in_scope('玉晶光昨天漲停。再來精測昨天也漲停。聯電昨天大漲。這幾檔不要買。', '這幾檔不要買。', '玉晶光'))

    def test_s03_a_stock_with_its_own_call_or_before_a_topic_switch_is_out(self):
        text = '台積電守穩季線，會員續抱。奇鋐跟雙鴻，這幾檔股票千萬不要買。'
        self.assertFalse(in_scope(text, '奇鋐跟雙鴻，這幾檔股票千萬不要買。', '台積電'))
        self.assertTrue(in_scope(text, '奇鋐跟雙鴻，這幾檔股票千萬不要買。', '奇鋐'))
        text = '台積電平盤。接下來只談伺服器散熱股。奇鋐、雙鴻。這幾檔不要買。'
        self.assertFalse(in_scope(text, '這幾檔不要買。', '台積電'))
        self.assertTrue(in_scope(text, '這幾檔不要買。', '奇鋐'))


class ClaimTense(unittest.TestCase):
    SHENZHUN = ('我有神準。張總，今天神準又平新高耶。我有沒有跟你們講神準這兩個高點會突破，根本不用擔心，等它過了再說，'
                '我要每天進進出出嗎？我就抱著抱著抱著，創新高，抱著抱著抱著創新高')

    def test_c03_expected_breakout_written_as_done_is_withheld(self):
        self.assertTrue(pl._claim_overreach('高點壓力順利突破', self.SHENZHUN, {'神準'}))

    def test_c04_a_breakout_the_source_reports_is_kept(self):
        self.assertFalse(pl._claim_overreach('60分K線已突破', '60分K線剛剛全部突破，沒有背離', {'力智'}))
        self.assertFalse(pl._claim_overreach('神準股價逆勢創新高', self.SHENZHUN, {'神準'}))

    def test_x_mian_verdicts_are_not_decided_by_wording(self):
        # 「X面」評語交對齊提示逐項核對，程式不用字面比對（會誤刪「MACD翻紅…的強勢技術面」這種有據句）。
        self.assertFalse(pl._claim_overreach('瑞鼎具備月週K線MACD翻紅與日K線突破所有均線的強勢技術面',
                                             '月K線MACD翻紅。週K線MACD翻紅。日K線突破所有均線。', {'瑞鼎'}))


class ThinkingLevel(unittest.TestCase):
    def level(self, tag, thinking, env):
        old = os.environ.get('GEMINI_THINKING_LEVEL')
        try:
            if env is None:
                os.environ.pop('GEMINI_THINKING_LEVEL', None)
            else:
                os.environ['GEMINI_THINKING_LEVEL'] = env
            return pl.gemini_generation_config('gemini-3.5-flash-lite', 3000, thinking, True, tag)['thinkingConfig']['thinkingLevel']
        finally:
            if old is None:
                os.environ.pop('GEMINI_THINKING_LEVEL', None)
            else:
                os.environ['GEMINI_THINKING_LEVEL'] = old

    def test_auto_raises_only_later_verdict_rounds(self):
        self.assertEqual(self.level('adjudicate-r1', 1024, 'auto'), 'medium')
        self.assertEqual(self.level('adjudicate-r2', 2048, 'auto'), 'high')
        self.assertEqual(self.level('adjudicate-r1-repair', 3072, None), 'high')
        self.assertEqual(self.level('source-align', 2048, 'auto'), 'medium')

    def test_an_explicit_level_is_kept(self):
        self.assertEqual(self.level('adjudicate-r3', 3072, 'medium'), 'medium')
        self.assertEqual(self.level('adjudicate-r2', 2048, 'low'), 'low')


if __name__ == '__main__':
    unittest.main()
