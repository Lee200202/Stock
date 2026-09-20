# -*- coding: utf-8 -*-
"""2026/09/14 工單日誌檢討後的流程修正。

一、第二次覆核回應 JSON 格式壞掉（Expecting property name enclosed in double quotes），整份作廢、沒有重試。
二、覆核作廢時沒把收錄／篇幅缺口記進去，日誌印「0 項篇幅提醒」，實際盤勢缺 2 點。
三、語氣核對用關鍵字把模型判的觀望注意改成觀望不碰（裕隆、鴻準），日誌看不到。
四、待複核只印數量，看不出是哪幾項。
"""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_quality import p, empty

SOURCE = '嘉澤我們手中還抱著。台積電也是手中持股。'


class JsonRepairTests(unittest.TestCase):
    def test_structural_glitches_are_repaired_without_touching_strings(self):
        cases = {
            '{"a": 1,,\n      "b": 2}': {'a': 1, 'b': 2},
            '{"a": [,\n 1]}': {'a': [1]},
            '{"a": 1,\n      名稱: "x"}': {'a': 1, '名稱': 'x'},
            '{"a": {"x":1}\n      "b": 2}': {'a': {'x': 1}, 'b': 2},
            '{"a": [{"x":1}\n {"y":2}]}': {'a': [{'x': 1}, {'y': 2}]},
            '{"note": "價位,理由: x,, y {a}",\n "b": [1, 2, true, null]}': {'note': '價位,理由: x,, y {a}', 'b': [1, 2, True, None]},
        }
        for raw, want in cases.items():
            self.assertEqual(p.safe_load_json(raw), want, raw)

    def test_missing_data_is_not_invented(self):
        with self.assertRaises(ValueError):
            p.safe_load_json('{"a": [1,\n      ...\n]}')


class ReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.sig = empty()
        self.sig['holdings'] = [{'name': '嘉澤', 'evidence_refs': ['S0001']}]
        self.good = copy.deepcopy(self.sig)
        self.good['holdings'].append({'name': '台積電', 'evidence_refs': ['S0001']})

    def run_audit(self, replies):
        out = io.StringIO()
        with patch.dict(p.os.environ, {'GEMINI_SEMANTIC_AUDIT': 'true'}), \
             patch.object(p, 'call_gemini', side_effect=replies) as ai, \
             patch.object(p, 'publication_gaps', return_value=[]), \
             patch.object(p, 'budget_left', return_value=3000), redirect_stdout(out):
            result = p.audit_context_json(SOURCE, copy.deepcopy(self.sig), '2026/09/14')
        return result, ai, out.getvalue()

    def test_malformed_reply_is_resent_once_and_used(self):
        result, ai, log = self.run_audit(['{"holdings": [ ... 壞掉', json.dumps(self.good)])
        self.assertEqual(ai.call_count, 2)
        self.assertEqual({r['name'] for r in result['holdings']}, {'嘉澤', '台積電'})
        self.assertIn('重送一次', log)
        self.assertNotIn('語意覆核未完成', ' '.join(result['_repair_gaps']))

    def test_second_malformed_reply_keeps_original_and_is_visible(self):
        result, ai, log = self.run_audit(['{"holdings": [ ... 壞掉', '{"holdings": [ ... 又壞'])
        self.assertEqual(ai.call_count, 2)
        self.assertEqual([r['name'] for r in result['holdings']], ['嘉澤'])
        self.assertTrue(result['_quality_requires_review'])
        self.assertIn('附近：', log, '錯誤訊息要帶出錯位置附近的片段')
        self.assertIn('待複核　語意覆核未完成', log, '待複核要逐項印出')

    def test_network_failure_is_not_retried_here(self):
        result, ai, _ = self.run_audit(TimeoutError('offline'))
        self.assertEqual(ai.call_count, 1)
        self.assertEqual([r['name'] for r in result['holdings']], ['嘉澤'])

    def test_failed_editorial_review_reports_its_gaps(self):
        gap = '盤勢內容偏短：目前 4 點，至少 6 點'
        out = io.StringIO()
        with patch.dict(p.os.environ, {'GEMINI_SEMANTIC_AUDIT': 'true'}), \
             patch.object(p, 'call_gemini', side_effect=TimeoutError('offline')), \
             patch.object(p, 'publication_gaps', return_value=[gap]), \
             patch.object(p, 'budget_left', return_value=3000), redirect_stdout(out):
            result = p.audit_context_json(SOURCE, copy.deepcopy(self.sig), '2026/09/14', editorial_retry=False)
        self.assertIn(gap, result['_repair_gaps'])
        self.assertIn('1 項篇幅提醒', out.getvalue())


class ToneOverrideTests(unittest.TestCase):
    def normalize(self, watch, avoid):
        s = empty()
        s['watch_watch'] = [{'name': n, 'reason': r} for n, r in watch]
        s['watch_avoid'] = [{'name': n, 'reason': r} for n, r in avoid]
        out = io.StringIO()
        with redirect_stdout(out):
            p.normalize_watch_tones(s)
        return [r['name'] for r in s['watch_watch']], [r['name'] for r in s['watch_avoid']], out.getvalue()

    def test_model_watch_is_kept_without_bearish_or_neutral_basis(self):
        w, a, log = self.normalize([('甲', 'AI伺服器訂單滿到明年')], [])
        self.assertEqual(w, ['甲'])
        self.assertIn('保留觀望注意', log)

    def test_basis_still_moves_rows(self):
        w, a, log = self.normalize([('乙', '原地橫盤整理'), ('丙', '外資先買再賣或先賣再買'), ('丁', '追高容易套牢')],
                                   [('戊', '900以下是買點'), ('己', '訂單滿到明年')])
        self.assertEqual(w, ['戊'])
        self.assertEqual(a, ['乙', '丙', '丁', '己'], '模型判不碰、又沒有正面依據的，照舊不碰')
        self.assertIn('乙　觀望注意 → 觀望不碰', log, '改類要印在日誌')

    def test_final_roster_is_printed_before_article(self):
        src = Path(p.__file__).read_text(encoding='utf-8')
        self.assertLess(src.index('最終分類　{signal_roster(signals)}'), src.index('step("撰稿"'))


if __name__ == '__main__':
    unittest.main()
