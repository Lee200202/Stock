"""修飾稿的標點與斷句、⑤ 教學重點至少三點（2026/09/13）。

模型實際怎麼斷句要部署後看產出；這裡驗的是：提示詞允許的標點在合併時留得下來、
模型改的字仍換回原文，以及教學補問的觸發條件與證據核對。
"""
import json
import unittest
from unittest.mock import patch

from test_quality import p, empty

# 0910 原始逐字稿的原句（語音辨識稿，字與字之間有空白）
RAW_ASK = '昨 天 有 一 個 會 員 這 樣 問 我 張 總 這 樣 我 是 不 是 要 趕 快 賣 掉'
RAW_LIST = '我 連 後 面 要 什 麼 聖 輝 新 代 加 折 還 有 木 德 我 以 後 要 買 的 股 票 通 列 出 來 給 你 看 了'
RAW_PRICE = '我 從 1820,2025,2135 買 到 現 在 哦 買 這 三 次 我 沒 有 賣 掉 哦'


class PolishPunctuationTests(unittest.TestCase):
    def test_prompt_has_punctuation_and_break_rules(self):
        for rule in ('只用這幾種：，。？！、：；「」（）', '一句以 10～35 字為原則', '「對不對？」',
                     '「聖輝、新代、加折還有木德」', '【不能切開的位置】', '數字與單位', '一段約150至300字'):
            self.assertIn(rule, p.POLISH_SYSTEM)

    def test_allowed_punctuation_survives_merge(self):
        cases = [
            (RAW_ASK, '昨天有一個會員這樣問我：「張總，這樣我是不是要趕快賣掉？」'),
            (RAW_LIST, '我連後面要什麼聖輝、新代、加折還有木德，我以後要買的股票通列出來給你看了。'),
            (RAW_PRICE, '我從1820,2025,2135買到現在哦，買這三次我沒有賣掉哦。'),
        ]
        for raw, polished in cases:
            with self.subTest(polished=polished):
                merged, cover, restored, dropped = p._merge_polish(raw, polished)
                self.assertEqual(merged, polished)
                self.assertEqual((cover, restored, dropped), (1.0, 0, 0))

    def test_other_marks_are_kept_by_merge_and_forbidden_for_style(self):
        # 合併把 …、“” 當標點保留，字仍然全是原文；提示詞禁用它們是為了版面一致，
        # 網站那一端的比對另由 test_polish_display_gas.js 驗證。
        polished = '昨天有一個會員這樣問我“張總……這樣我是不是要趕快賣掉”'
        merged, cover, restored, dropped = p._merge_polish(RAW_ASK, polished)
        self.assertEqual((merged, cover, restored, dropped), (polished, 1.0, 0, 0))
        for mark in ('…', '“', '《'):
            self.assertIn(mark, p.POLISH_SYSTEM.split('不要用', 1)[1].splitlines()[0])

    def test_model_word_change_is_still_restored(self):
        merged, _, restored, dropped = p._merge_polish(RAW_LIST, '我連後面要什麼聖暉、新代、嘉澤還有牧德。')
        self.assertIn('聖輝、新代、加折還有木德', merged)
        self.assertTrue(restored and dropped)


def lesson_source():
    return '我在大漲的時候賣股票，我在跌的時候買股票，因為這三個月就是震盪整理。' * 200


def view(text, refs=('S0001',)):
    return {'kind': 'view', 'text': text, 'evidence_refs': list(refs)}


class LessonTopUpTests(unittest.TestCase):
    def run_topup(self, sig, reply=None, error=None, source=None):
        source = source or lesson_source()
        p.materialize_evidence(sig, source)
        for item in sig['market']:
            item['_evidence_verified'] = p.market_item_verified(item, p._ev_norm(source))
        kwargs = {'side_effect': error} if error else {'return_value': json.dumps(reply or {'market': []})}
        with patch.object(p, 'call_gemini', **kwargs) as ai, patch.object(p, 'budget_left', return_value=1000), \
             patch.object(p, '_QUOTA_STOP', {'daily': False}):
            out = p.ensure_min_lessons(sig, source, '2026/09/10')
        views = [r for r in out['market'] if r.get('kind') == 'view' and r.get('_evidence_verified')]
        return out, views, ai

    def test_tops_up_to_three_with_verified_distinct_points(self):
        sig = empty(); sig['market'] = [view('買賣節奏：大漲時賣股票，下跌時買股票。')]
        reply = {'market': [
            view('耐心等待：這三個月是震盪整理，不必每天追著買。', ['S0002']),
            view('張正提醒追高：大漲後隔天容易下跌，追高就賠。', ['S0003']),
            view('買賣節奏：重複的主題不收。'),
            view('亂寫數字：漲999塊。')]}
        out, views, ai = self.run_topup(sig, reply)
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(len(views), 3)
        self.assertTrue(views[2]['text'].startswith('提醒追高：'))     # 補回的點一樣拿掉人名
        self.assertFalse(any('999' in v['text'] for v in views))
        self.assertFalse(any('補問後仍只有' in g for g in out.get('_repair_gaps', [])))

    def test_enough_points_no_extra_call(self):
        sig = empty(); sig['market'] = [view(f'主題{c}：大漲時賣股票。') for c in '甲乙丙']   # 標題不帶數字，才過得了證據規則
        _, views, ai = self.run_topup(sig)
        ai.assert_not_called(); self.assertEqual(len(views), 3)

    def test_short_source_no_extra_call(self):
        sig = empty()
        _, _, ai = self.run_topup(sig, source='我在大漲的時候賣股票。' * 10)
        ai.assert_not_called()

    def test_still_short_is_recorded_not_fabricated(self):
        sig = empty()
        out, views, _ = self.run_topup(sig, {'market': [view('只有一點：大漲時賣股票。')]})
        self.assertEqual(len(views), 1)
        self.assertIn('教學內容偏短：補問後仍只有 1 點', out['_repair_gaps'])
        self.assertEqual(p.needs_review_gaps(out['_repair_gaps']), [])   # 屬篇幅提醒，不擋發布

    def test_quota_does_not_fail_the_run(self):
        sig = empty()
        out, views, _ = self.run_topup(sig, error=p.RateLimited('daily', daily=True))
        self.assertEqual(views, []); self.assertIn('教學內容偏短：補問未完成', out['_repair_gaps'])

    def test_policy_asks_for_minimum_three(self):
        self.assertIn('逐字稿超過五千字時至少 3 點', p.POLICY)
        self.assertIn('數字非必要就不寫', p.POLICY)
        self.assertEqual(len(p.assessment_batches('股' * 24396, '2026/09/10')), 1)   # 規則變長仍一批跑完


if __name__ == '__main__':
    unittest.main()
