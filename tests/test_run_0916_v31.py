"""0916 執行紀錄回饋（2026/09/16）：同音名稱要進盤點、標題只留一句、排版分批要小。"""
import unittest

from test_quality import p

# 原文片段（語音轉文字，鴻海寫成紅海、鴻準寫成紅準、勤誠寫成秦城）
SRC = ('台 積 電 今 天 沒 有 填 不 用 緊 張 後 面 一 定 填 '
       '紅 海 其 實 最 近 沒 有 事 我 叫 你 們 238 買 嘛 紅 海 最 近 就 在 這 邊 盤 沿 著 薊 限 走 '
       '那 最 近 比 較 比 紅 海 強 的 是 這 一 隻 嘛 叫 做 紅 準 嘛 我 買 在 這 一 天 我 沒 有 賣 我 就 抱 著 '
       '真 正 最 近 有 開 始 準 備 要 轉 強 的 是 這 一 隻 可 是 他 還 沒 有 過 薊 限 '
       '就 它 的 基 本 面 來 講 你 就 在 這 邊 有 拉 回 再 佈 局 等 他 這 個 叫 台 達 電 ')


class Run0916Tests(unittest.TestCase):
    def setUp(self):
        p._CODE_MAP = {'2317': '鴻海', '2354': '鴻準', '2330': '台積電', '2308': '台達電'}
        p._CODE_MAP_FULL = True
        p._SOUND_MEMO.clear()
        p._INVENTORY_CACHE['key'] = None

    def test_sound_only_names_reach_the_prompt_and_the_roster(self):
        # 9/16 他整段講「紅海」，盤點卻一個字都沒提到，於是鴻海對整條流程是隱形的。
        self.assertEqual(p._sound_hits(SRC.replace(' ', '')), {'紅海': ('2317', '鴻海')})
        self.assertEqual(p._confirmed_names_for(SRC.replace(' ', '')).get('紅海'), ('2317', '鴻海'))
        roster = p.source_inventory({'S0001': {'text': SRC}})
        self.assertIn('紅海', [r['name'] for r in roster])
        self.assertEqual([r['code'] for r in roster if r['name'] == '紅海'], ['2317'])

    def test_sound_table_does_not_replace_the_words_in_public_text(self):
        # 紅海是常用詞，只在比對代號與盤點時對應到鴻海，不做全文替換。
        self.assertNotIn('紅海', p.CONFIRMED_NAMES)
        self.assertEqual(p.public_narrative('紅海市場競爭激烈。'), '紅海市場競爭激烈。')

    def test_title_keeps_one_sentence(self):
        # 9/16 模型回了三個問句串在一起，整行都是標題。v44 起標題至少 15 字：第一句只有 10 字，接上第二句就停。
        run_on = '大家有沒有看到成交量？你現在的手機裡面預估今天成交量多少？5000億昨天成交量多少？6000億'
        self.assertEqual(p._title_one_sentence(run_on), '大家有沒有看到成交量？你現在的手機裡面預估今天成交量多少')
        for good in ('量縮震盪，靜待CPI', '你買在高檔 神仙都難救'):
            self.assertEqual(p._title_one_sentence(good), good)
        # 一句話裡有幾個逗號不動它——v19 拿掉字數上限就是為了不要把好句子砍成半句。
        long_one = '敵不動我不動，敵動我才動，這才是這個禮拜該有的節奏'
        self.assertEqual(p._title_one_sentence(long_one), long_one)

    def test_title_pipeline_uses_the_rule(self):
        signals = {k: [] for k in p.SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')}
        signals['market'] = [{'kind': 'event', 'text': '大家有沒有看到成交量？預估今天成交量5000億。',
                              'headline': '大家有沒有看到成交量？預估今天成交量多少？5000億',
                              '_evidence_verified': True}]
        title = p.article_title(signals)
        self.assertEqual(title, '大家有沒有看到成交量？預估今天成交量多少！', '接到滿 15 字就停，不是三個問句串起來')

    def test_transcript_layout_batches_are_small_enough(self):
        # 9/16 第一批 finish=MAX_TOKENS：7000 字一批時輸出額度被思考吃光。
        self.assertLessEqual(p.TX_LAYOUT_CHUNK, 4000)
        src = open('pipeline/pipeline.py', encoding='utf-8').read()
        self.assertIn('max_out=MAX_OUT, tag=f\'tx-layout-{i}\'', src)

    def test_sound_lookup_is_incremental(self):
        """分批估算會拿越來越長的前綴呼叫；每次重算注音會讓整輪多花好幾分鐘。"""
        import time
        text = SRC.replace(' ', '') * 40
        p._SOUND_MEMO.clear()
        start = time.time()
        p._sound_hits(text)
        first = time.time() - start
        start = time.time()
        for cut in range(1, 40):
            p._sound_hits(text[:len(text) * cut // 40])
        rest = time.time() - start
        self.assertLess(rest, max(first, 0.05) * 3, '越來越長的前綴不可以每次從頭算注音')

    def test_prompt_learned_from_this_run(self):
        for rule in ('這個叫台達電', '台積電今天沒有填不用緊張', '幫他們解套'):
            self.assertIn(rule, p.POLICY)


if __name__ == '__main__':
    unittest.main()
