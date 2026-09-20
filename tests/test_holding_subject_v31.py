"""持股主詞（2026/09/16）：「連電集團裡面這一支股票」買的是聯陽，不是聯電。

原文：「我會員的一支股票，我買130出頭的……哪一支股票？連陽，我也沒有跟你們講，
        連電集團裡面這一支股票獲利很好，上半年賺5塊8，全年可以賺10塊」
聯電（2303）在整份原文裡只是「昨天跌4塊、今天漲三塊半」的追高反例，
卻被記成會員持股、進場價 142.50（當天收盤），等於開了一個不存在的持有回合。
"""
import unittest

from test_quality import p

SRC = ('來 再 給 大 家 看 今 天 連 電 有 沒 有 漲 最 高 漲 三 塊 半 138.5 漲 到 142 '
       '昨 天 連 電 跌 4 塊 今 天 漲 三 塊 半 你 一 直 在 這 邊 玩 這 個 東 西 沒 有 用 啊 '
       '我 舉 個 例 子 我 會 員 的 一 支 股 票 我 買 130 出 頭 的 有 一 些 會 員 就 每 天 罵 '
       '你 告 訴 我 哪 一 支 股 票 ？ 連 陽 我 也 沒 有 跟 你 們 講 連 電 集 團 裡 面 這 一 支 股 票 '
       '獲 利 很 好 上 半 年 賺 5 塊 8 全 年 可 以 賺 10 塊 給 你 看 他 的 K 線 他 最 近 在 幹 嘛 '
       '敵 不 動 我 不 動 敵 動 我 才 動 那 最 近 比 較 強 的 是 這 一 隻 叫 做 紅 準 嘛 '
       '我 買 在 這 一 天 啊 你 說 我 賺 多 少 我 沒 有 賣 我 就 抱 著 啊 籌 碼 有 夠 安 定 ')


class HoldingSubjectTests(unittest.TestCase):
    def setUp(self):
        p._CODE_MAP = {'2303': '聯電', '3014': '聯陽', '2354': '鴻準', '5269': '祥碩'}
        p._CODE_MAP_FULL = True
        p._ATTR_MEMO.clear()

    def rows(self, holdings, **extra):
        sig = {k: [] for k in p.SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')}
        sig['holdings'] = holdings
        sig.update(extra)
        return sig

    def test_group_parent_is_not_the_holder(self):
        sig = self.rows([{'name': '聯電', 'code': '2303', 'stance': '續抱',
                          'note': '上半年的獲利良好，雖然K線盤整仍需抱牢。'},
                         {'name': '鴻準', 'code': '2354', 'stance': '續抱', 'note': '買在這一天都沒有賣。'}])
        out = p.verify_holding_subject(sig, SRC)
        self.assertEqual([r['name'] for r in out['holdings']], ['鴻準'], '離「我買的」最近的那一檔才留著')
        self.assertEqual([r['name'] for r in out['watch_watch']], ['聯電'])
        self.assertTrue(any('聯電' in g and '集團' in g for g in out['_repair_gaps']))

    def test_demoted_row_keeps_its_words_and_loses_the_round(self):
        sig = self.rows([{'name': '聯電', 'code': '2303', 'stance': '續抱', 'note': '上半年的獲利良好。'}])
        out = p.verify_holding_subject(sig, SRC)
        moved = out['watch_watch'][0]
        self.assertEqual(moved['reason'], '上半年的獲利良好。')
        self.assertNotIn('stance', moved, '不再是持股，不留續抱')

    def test_the_real_holder_survives(self):
        sig = self.rows([{'name': '聯陽', 'code': '3014', 'stance': '續抱', 'note': '買130出頭，上半年賺5塊8。'}])
        out = p.verify_holding_subject(sig, SRC)
        self.assertEqual([r['name'] for r in out['holdings']], ['聯陽'])
        self.assertEqual(out['watch_watch'], [])

    def test_single_stock_day_is_left_alone(self):
        # 只有一檔時沒有「隔壁那一檔」可以比，不要亂降。
        sig = self.rows([{'name': '鴻準', 'code': '2354', 'stance': '續抱', 'note': '買在這一天都沒有賣。'}])
        out = p.verify_holding_subject(sig, SRC)
        self.assertEqual([r['name'] for r in out['holdings']], ['鴻準'])

    def test_heard_spelling_of_the_group_member(self):
        self.assertEqual(p.resolve_code('連陽', '')[:2], ('3014', '聯陽'))

    def test_prompt_carries_the_case(self):
        self.assertIn('集團裡面這一支股票', p.POLICY)
        self.assertIn('買在130出頭的是聯陽', p.POLICY)


class AuditLengthTests(unittest.TestCase):
    def test_short_description_hint_matches_the_forty_to_one_twenty_rule(self):
        # v25 把說明收短到 40～120 字，稽核卻還在用 70 字當門檻，兩邊互相拉扯。
        src = open('pipeline/pipeline.py', encoding='utf-8').read()
        self.assertIn("if len(note) < 40 and", src)
        self.assertIn('一到三句、40～120 字', src)

    def test_layout_check_ignores_width_and_spacing_only(self):
        self.assertTrue(p._tx_same_text('他說：「買在１３０？」', '他說:「買在130?」'))
        self.assertFalse(p._tx_same_text('他說買在130出頭', '他說買在130'))


if __name__ == '__main__':
    unittest.main()
