"""價位歸屬（2026/09/16）：講完一檔接下一檔時，上一檔的成本不可以接到下一檔的說明裡。

原文（2026/09/15，語音轉文字把鴻海寫成紅海、鴻準寫成紅準）：
  「紅海沒事啦，紅海就在這邊做一個收斂壓縮，張正當時叫你們買的是238嘛……賺10塊……
    好，來，紅海講完講紅準，跟你講我買這邊啊……我沒有賣我就抱著啊……因為他的籌碼有夠安定的」
238 是鴻海的成本，信上卻寫成鴻準「成本在238附近」。
"""
import unittest

from test_quality import p, empty

SOURCE = (
    '好 啊 紅 海 呢 ？ 紅 海 沒 事 啦 紅 海 就 在 這 邊 做 一 個 收 斂 壓 縮 '
    '張 正 當 時 叫 你 們 買 的 是 238 嘛 你 們 怎 麼 樣 用 聽 張 正 講 的 買 點 不 至 於 會 虧 錢 '
    '對 不 對 你 賺 目 前 還 沒 有 賺 多 少 了 賺 10 塊 對 不 對 '
    '好 來 紅 海 講 完 講 紅 準 跟 你 講 我 買 這 邊 啊 我 買 這 邊 了 啊 你 說 我 要 不 要 賣 ？ '
    '他 這 邊 長 上 去 又 跌 下 來 我 要 不 要 賣 ？ 我 沒 有 賣 我 就 抱 著 啊 我 抱 著 等 你 啊 '
    '為 什 麼 ？ 因 為 他 的 籌 碼 有 夠 安 定 的 來 啦 看 一 支 股 票 先 把 最 大 量 的 地 方 找 出 來 '
    '現 在 的 股 價 有 這 兩 根 大 量 在 支 撐 你 在 怕 屁 啊 '
    '來 我 現 在 講 我 會 員 手 中 的 股 票 他 有 去 跌 破 昨 天 低 點 嗎 ？ 沒 有 欸 '
    '他 如 果 在 這 邊 撐 三 天 不 破 低 現 在 低 一 點 是 不 是 3410 你 是 不 是 再 看 三 天 不 破 低 ？ '
    '這 個 就 是 我 看 四 星 KY 的 重 點 公 司 本 身 營 收 年 成 長 181% 創 歷 史 新 高 沒 問 題 '
    '來 這 邊 我 有 幾 個 好 股 票 的 重 點 要 告 訴 你 們 '
    '第 一 個 如 果 你 有 這 一 支 股 票 的 人 900 以 下 不 要 賣 一 般 來 講 以 下 是 買 點 '
    '因 為 00981A 還 有 幾 百 張 還 沒 有 殺 低 殺 完 等 他 那 這 間 公 司 是 好 公 司 '
    '張 正 有 沒 有 一 兩 個 月 前 就 跟 你 預 告 秦 城 一 定 破 900 漢 糖 一 定 破 1000 ')

CODES = {'2317': '鴻海', '2354': '鴻準', '3661': '世芯-KY', '8210': '勤誠', '3062': '漢唐'}


class PriceAttributionTests(unittest.TestCase):
    def setUp(self):
        p._CODE_MAP, p._CODE_MAP_FULL = dict(CODES), True
        p._ATTR_MEMO.clear()

    def rows(self, **kw):
        sig = empty()
        for key, value in kw.items():
            sig[key] = value
        return sig

    def test_neighbour_cost_is_removed_from_this_stock(self):
        sig = self.rows(holdings=[{'name': '鴻準', 'code': '2354',
                                   'note': '成本在238附近，籌碼安定且下方有大量支撐，抱著等待後續表現。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertEqual(out['holdings'][0]['note'], '籌碼安定且下方有大量支撐，抱著等待後續表現。')
        self.assertTrue(any('238' in g and '鴻準' in g for g in out['_repair_gaps']), '要留下可稽核的紀錄')

    def test_price_field_copied_from_the_neighbour_is_cleared(self):
        sig = self.rows(buy=[{'name': '鴻準', 'code': '2354', 'price': '238',
                              'reason': '買在238，之後沒有賣掉。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertEqual(out['buy'][0]['price'], '未說明')
        self.assertNotIn('238', out['buy'][0]['reason'])

    def test_the_stock_that_owns_the_number_keeps_it(self):
        sig = self.rows(holdings=[{'name': '鴻海', 'code': '2317', 'note': '當時叫你們買的是238，目前賺10塊。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertIn('238', out['holdings'][0]['note'])
        self.assertEqual(out.get('_repair_gaps', []), [])

    def test_unnamed_stock_keeps_its_price(self):
        # 他整段講「這一支股票900以下不要賣」沒有講名字：兩邊都不近，沒有證據說它錯，就不要動。
        sig = self.rows(watch_watch=[{'name': '勤誠', 'code': '8210', 'price': '900',
                                      'reason': '手中有持股的人900以下不要急著賣出，沒有持股的可等900以下再注意買點。'}],
                        watch_avoid=[{'name': '漢唐', 'code': '3062', 'price': '1000',
                                      'reason': '先前就預告一定會跌破1000，跌破1000屬於超跌。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertIn('900', out['watch_watch'][0]['reason'])
        self.assertEqual(out['watch_watch'][0]['price'], '900')
        self.assertIn('1000', out['watch_avoid'][0]['reason'])
        self.assertEqual(out.get('_repair_gaps', []), [])

    def test_distant_number_about_this_stock_is_kept(self):
        sig = self.rows(holdings=[{'name': '世芯-KY', 'code': '3661',
                                   'note': '觀察低點3410連續三天不破低，底部出量且營收創新高，續抱看好後市。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertIn('3410', out['holdings'][0]['note'])

    def test_number_that_never_appears_is_removed(self):
        sig = self.rows(holdings=[{'name': '鴻準', 'code': '2354', 'note': '成本在512附近，籌碼安定。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertEqual(out['holdings'][0]['note'], '籌碼安定。')
        self.assertTrue(any('原文沒有這個數字' in g for g in out['_repair_gaps']))

    def test_non_price_numbers_are_not_touched(self):
        sig = self.rows(holdings=[{'name': '世芯-KY', 'code': '3661',
                                   'note': '營收年成長181%創歷史新高，看三天不破低。'}])
        out = p.strip_foreign_price_claims(sig, SOURCE)
        self.assertIn('181%', out['holdings'][0]['note'])

    def test_heard_spelling_of_hon_hai_resolves(self):
        # 紅海是常用詞（紅海市場），所以放讀音表而不是別名表：只在比對代號時對到 2317。
        self.assertEqual(p.resolve_code('紅海', '')[:2], ('2317', '鴻海'))
        self.assertNotIn('紅海', p.CONFIRMED_NAMES)

    def test_prompt_carries_the_case(self):
        self.assertIn('紅海講完講紅準', p.POLICY)
        self.assertIn('238 不是鴻準的成本', p.POLICY)


if __name__ == '__main__':
    unittest.main()
