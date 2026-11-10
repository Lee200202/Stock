"""2026/11/10：影片 1:00:01，第 5 段只剩一秒，Gemini 每次回 400，前 4 段早已完成卻每三分鐘重試、拖了二十多分鐘。
片尾不足兩分鐘的零頭併進前一段。這裡固定切段計畫，並確認聽打不會送出不到兩分鐘的片尾段。不連網、不呼叫模型。"""
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('transcript', ROOT / 'transcript.py')
tx = importlib.util.module_from_spec(spec)
sys.modules['transcript'] = tx
spec.loader.exec_module(tx)


class TailMerge(unittest.TestCase):
    def test_one_second_tail_joins_previous_segment(self):
        self.assertEqual(tx.plan_segments(3601, 900),
                         [(0, 900), (900, 1800), (1800, 2700), (2700, 3601)])

    def test_exact_multiple_unchanged(self):
        self.assertEqual(tx.plan_segments(3600, 900), [(0, 900), (900, 1800), (1800, 2700), (2700, 3600)])

    def test_long_tail_kept_as_own_segment(self):
        self.assertEqual(tx.plan_segments(3600 + 300, 900)[-1], (3600, 3900))

    def test_short_video_single_segment(self):
        self.assertEqual(tx.plan_segments(50, 900), [(0, 50)])
        self.assertEqual(tx.plan_segments(0, 900), [])

    def test_transcribe_never_requests_a_tiny_tail(self):
        t = tx.Transcriber.__new__(tx.Transcriber)
        t.segment_seconds = 900
        asked = []
        t._range = lambda url, s, e: asked.append((s, e)) or f'{s}-{e}'
        old = tx.RUN_STARTED
        try:
            tx.RUN_STARTED = __import__('time').monotonic()
            out = t.transcribe('u', 3601, cache={})
        finally:
            tx.RUN_STARTED = old
        self.assertEqual(asked[-1], (2700, 3601))
        self.assertTrue(all(e - s >= tx.TAIL_MERGE_SECONDS for s, e in asked))
        self.assertIn('2700-3601', out)


if __name__ == '__main__':
    unittest.main()
