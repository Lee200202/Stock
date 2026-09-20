"""逐字稿排版進工作流（2026/09/16）：寫入時排好存起來，排版不得改動任何一個字。
v44（2026/09/17）起模型只回分段位置與小標，不抄原文；詳細測試見 test_tx_layout_v44.py。"""
import json
import unittest
from unittest.mock import patch

from test_quality import p

SOURCE = ('各位投資朋友大家早。今天早上平盤震盪，很多投資人在這個地方忍不住。'
          '我昨天告訴你們，台積電季線以下沒有意外都是買點。'
          '好，紅海呢？紅海沒事啦，紅海就在這邊做一個收斂壓縮。'
          '來，紅海講完講紅準，跟你講我買這邊啊，我沒有賣我就抱著。') * 3


class TranscriptLayoutTests(unittest.TestCase):
    def test_model_layout_is_used_when_nothing_changed(self):
        n = len(p._tx_sentences(SOURCE))
        reply = json.dumps({'sections': [{'start': 1, 'title': '開盤震盪'},
                                         {'start': n // 2, 'title': '紅海與紅準'}]}, ensure_ascii=False)
        with patch.object(p, 'call_gemini', return_value=reply) as ai, patch.object(p, 'budget_left', return_value=900), \
             patch.dict(p._QUOTA_STOP, {'daily': False}):
            secs = p.format_transcript_sections(SOURCE)
        ai.assert_called_once()
        self.assertEqual([s['title'] for s in secs], ['開盤震盪', '紅海與紅準'])
        joined = ''.join(''.join(s['paras']) for s in secs)
        self.assertEqual(joined.replace(' ', ''), SOURCE.replace(' ', ''), '排版不可以動到任何一個字')

    def test_layout_that_rewrites_the_text_is_rejected(self):
        # 模型「順手」把話修順或漏一段：v44 起模型回的文字一律不用，沒有分段位置就退回機械分段（每段仍有小標）。
        reply = json.dumps({'sections': [{'title': '摘要', 'text': '今天盤勢震盪，建議觀望。'}]}, ensure_ascii=False)
        with patch.object(p, 'call_gemini', return_value=reply), patch.object(p, 'budget_left', return_value=900), \
             patch.dict(p._QUOTA_STOP, {'daily': False}):
            secs = p.format_transcript_sections(SOURCE)
        joined = ''.join(''.join(s['paras']) for s in secs)
        self.assertEqual(joined.replace(' ', ''), SOURCE.replace(' ', ''))
        self.assertNotIn('摘要', [s['title'] for s in secs])
        self.assertTrue(all(s['title'] for s in secs))

    def test_model_failure_falls_back_instead_of_losing_the_day(self):
        with patch.object(p, 'call_gemini', side_effect=p.RateLimited('配額用盡')):
            secs = p.format_transcript_sections(SOURCE)
        self.assertTrue(secs and secs[0]['paras'])
        self.assertEqual(''.join(secs[0]['paras']).replace(' ', ''), SOURCE.replace(' ', ''))

    def test_short_transcript_skips_the_model(self):
        with patch.object(p, 'call_gemini') as ai:
            secs = p.format_transcript_sections('今天沒什麼好講的。')
        ai.assert_not_called()
        self.assertEqual(secs[0]['paras'], ['今天沒什麼好講的。'])

    def test_fingerprint_ignores_whitespace_only_changes(self):
        self.assertEqual(p.transcript_fingerprint('我 買 這 邊'), p.transcript_fingerprint('我買這邊'))
        self.assertNotEqual(p.transcript_fingerprint('我買這邊'), p.transcript_fingerprint('我賣這邊'))

    def test_layout_runs_in_the_daily_flow(self):
        src = (p.Path(__file__).resolve().parents[1] / 'pipeline/pipeline.py').read_text(encoding='utf-8') \
            if hasattr(p, 'Path') else open('pipeline/pipeline.py', encoding='utf-8').read()
        self.assertIn('ensure_transcript_layout(ss, date_str)', src)
        self.assertIn('排版稿JSON', src)


if __name__ == '__main__':
    unittest.main()
