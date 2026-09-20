"""2026/09/13 v7：公開說明去人名、觀望不碰從寬、⑤ 教學增量加長。

這幾條規則主要靠提示詞；程式能驗的是提示詞真的寫進去、兩端鏡像一致、
句首人名的保險與篇幅檢查的門檻。模型實際產出仍須部署後重跑原文驗收。
"""
import unittest

from test_quality import p, empty, ROOT


class SpeakerNameTests(unittest.TestCase):
    def test_subject_names_removed(self):
        cases = [
            ('張正指出國巨外資成本在597，解套一定會賣', '指出國巨外資成本在597，解套一定會賣'),
            ('會員買了祥碩兩個多月，張正強調自己就是在等他噴出去', '會員買了祥碩兩個多月，強調自己就是在等他噴出去'),
            ('雖然張正已於昨天買進，但針對還沒有的人', '雖然已於昨天買進，但針對還沒有的人'),
            ('張震老師的會員昨天買進', '會員昨天買進'),
            ('台積電碰到特定線再注意', '台積電碰到特定線再注意'),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(p.strip_speaker_names(raw), expected)

    def test_reason_cleaning_applies_to_every_write(self):
        self.assertEqual(p.naturalize_reason('張正明確表示自己和會員手中抱著台積電'),
                         '明確表示自己和會員手中抱著台積電。')

    def test_market_text_name_removed_and_still_verified(self):
        q = '早盤指數跌到46620以下回補缺口之後就回升'
        sig = empty()
        sig['market'] = [{'kind': 'level', 'text': '張正指出早盤指數跌到46620以下回補缺口後隨即回升',
                          'evidence': [q]}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertEqual(len(sig['market']), 1)
        self.assertTrue(sig['market'][0]['_evidence_verified'])
        self.assertNotIn('張正', sig['market'][0]['text'])


class PromptRuleTests(unittest.TestCase):
    def test_policy_rules_present(self):
        for rule in ('不寫人名當主詞', 'watch_avoid 從寬：', '六、點名個股當負面示範', '外資買一天賣一天',
                     '不可替警示例子補寫「等待機會」', '原文充足時整理 6～10 點', '120～220 字',
                     'text 的數字必須出現在所列段落', '觀念標題'):
            self.assertIn(rule, p.POLICY)
        for gone in ('只說不要追高但可等拉回，應保留條件，不自動當全面不碰', '張正在昨天', '整理 5～8 點', '不採偏空優先'):
            self.assertNotIn(gone, p.POLICY)
        self.assertIn('六種句型', p.AUDIT_SYSTEM)
        self.assertIn('逐筆重看 watch_watch', p.AUDIT_SYSTEM)
        self.assertIn('6 到 10 點', p.ARTICLE_SYSTEM)

    def test_gas_mirrors_synced(self):
        gas = (ROOT / 'apps-script/Adminpipeline.gs').read_text(encoding='utf-8')
        self.assertEqual(gas.count('watch_avoid 從寬：'), 3)   # 擷取、覆核、複審三份
        self.assertNotIn('張正在昨天', gas)
        self.assertIn('6 到 10 點', gas)


class BatchCapacityTests(unittest.TestCase):
    def test_prompt_growth_keeps_long_transcript_in_one_batch(self):
        # 規則變長會吃掉單批能放的原文。9/10 原文 23493 字，既有門檻是 24396 個全形字一批跑完，
        # 分成兩批會多一次擷取與覆核呼叫，教學重點也會被切到兩批各自整理。
        self.assertEqual(len(p.assessment_batches('股' * 24396, '2026/09/10')), 1)


class LessonLengthGapTests(unittest.TestCase):
    def _sig(self, n, size):
        sig = empty()
        sig['market'] = [{'kind': 'level', 'text': '盤' * 100} for _ in range(6)]
        sig['market'] += [{'kind': 'view', 'text': '教' * size} for _ in range(n)]
        return sig

    def test_five_short_lessons_flagged(self):
        gaps = p.publication_gaps(self._sig(5, 100), '原文' * 3000)
        self.assertTrue(any(g.startswith('教學內容偏短') and '120–220' in g for g in gaps))

    def test_six_full_lessons_pass(self):
        gaps = p.publication_gaps(self._sig(6, 130), '原文' * 3000)
        self.assertFalse(any(g.startswith('教學內容偏短') for g in gaps))
        self.assertFalse(any(g.startswith('盤勢內容偏短') for g in gaps))


if __name__ == '__main__':
    unittest.main()
