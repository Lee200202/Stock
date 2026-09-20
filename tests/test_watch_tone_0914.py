# -*- coding: utf-8 -*-
"""觀望方向（2026/09/14）：語氣偏多但沒講買點的，不能因為「沒有買點」被歸成觀望不碰。

實際判錯：裕隆「資產價值非常高、具備長線價值、可持續注意」與鴻準「外資持續買進、
相對穩健、適合資金較少者切入」出現在「觀望不碰｜語氣偏空，暫不進場」那張表。
"""
import re
import unittest
from pathlib import Path

from test_quality import p, empty

ROOT = Path(__file__).resolve().parents[1]

YULON = '美股淨值高達60.65，資產價值非常高，且將Luxgen賣給鴻海後具備長線價值，若拉回回補缺口可持續注意。'
FOXCONN_TECH = '會員買在第一根長虹棒那天，外資持續買進，是相對穩健且適合資金較少者切入的標的。'


class SoftPositiveToneTests(unittest.TestCase):
    def test_reported_rows_are_watch(self):
        self.assertEqual(p.watch_tone(YULON), 'watch_watch')
        self.assertEqual(p.watch_tone(FOXCONN_TECH), 'watch_watch')

    def test_other_positive_evaluations_without_buy_point(self):
        for text in ('外資連續買超，基本面不錯', '營收創新高，體質佳，可以持續留意', '投信持續加碼，具有轉機價值'):
            self.assertEqual(p.watch_tone(text), 'watch_watch', text)

    def test_any_negative_cue_keeps_avoid(self):
        for text in ('資產價值高，但追高容易套牢', '外資持續賣超，走勢轉弱', '相對穩健但要注意跌破季線的風險',
                     '昨天長紅今天大跌', '利多出盡', '具備長線價值，不過還沒跌完'):
            self.assertEqual(p.watch_tone(text), 'watch_avoid', text)

    def test_negated_positive_is_not_positive(self):
        for text in ('不適合資金較少的人切入', '沒有長線價值，營收衰退', '不穩健，也不值得追蹤', '不可以買，還沒跌完'):
            self.assertEqual(p.watch_tone(text), 'watch_avoid', text)

    def test_neutral_and_churn_rules_unchanged(self):
        for text in ('持續觀察', '原地橫盤整理', '外資買一天賣一天，散戶跟著追會被套', '外資操作呈現先買再賣或先賣再買'):
            self.assertEqual(p.watch_tone(text), 'watch_avoid', text)
        for text in ('不要追高，900以下是買點', '是好股票，等補完缺口再站回去'):
            self.assertEqual(p.watch_tone(text), 'watch_watch', text)

    def test_final_normalization_moves_reported_rows_to_watch(self):
        s = empty()
        s['watch_avoid'] = [{'name': '裕隆', 'code': '2201', 'reason': YULON},
                            {'name': '鴻準', 'code': '2354', 'reason': FOXCONN_TECH},
                            {'name': '華邦電', 'code': '2344', 'reason': '外資先買再賣，追高容易套牢'}]
        p.normalize_watch_tones(s)
        self.assertEqual(sorted(r['name'] for r in s['watch_watch']), ['裕隆', '鴻準'])
        self.assertEqual([r['name'] for r in s['watch_avoid']], ['華邦電'])

    def test_prompts_state_the_rule_in_pipeline_and_gas(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn('可續留意且全段無負面，屬正面看法', src)
        gas = (ROOT / 'apps-script/Adminpipeline.gs').read_text(encoding='utf-8')
        self.assertIn('可續留意且全段無負面，屬正面看法', gas, '要執行 scripts/sync_quality.py 同步 GAS 的 prompt')
        self.assertIn('正面評價即使沒講買點也歸 watch', (ROOT / 'apps-script/Adminservice.gs').read_text(encoding='utf-8'))
        self.assertEqual((ROOT / 'pipeline' / 'pipeline.py').read_bytes(), (ROOT / 'pipeline/pipeline.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
