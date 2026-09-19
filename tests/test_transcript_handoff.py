# -*- coding: utf-8 -*-
"""
取稿 → 潤飾稽核 的交棒。

兩支程式不互相呼叫，只透過試算表「影片清單」的「原始逐字稿內容」欄溝通：

    transcript.py  （transcript.yml，11:20 起每 3 分鐘）
        ↓ 寫入 原始逐字稿內容
    pipeline.py    （daily.yml，11:20 起，內部每 3 分鐘）
        ↓ stage_transcript 看到原文就往下走
    潤飾 → 擷取 → 稽核 → 代號 → 寫入 → 撰稿 → 刷新

這裡要釘住的是那個交接點：**只要原文在，就一定要往下走，不能停在等待。**
停在等待而沒人發現的話，當天就完全沒有產出，而且工作是綠燈，不會有告警。
"""

import importlib.util
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")
_spec = importlib.util.spec_from_file_location("pl", ROOT / "pipeline.py")
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)

import sys                                  # noqa: E402
sys.path.insert(0, str(ROOT))
import transcript as T                      # noqa: E402


class WritesWhereTheNextStageReads(unittest.TestCase):
    """上游寫的欄位，必須就是下游讀的欄位。

    兩邊各自用自己的常數，改了一邊沒改另一邊的話，逐字稿會寫進一個
    沒有人讀的格子——而且兩邊都不會報錯。
    """

    def test_same_sheet_name(self):
        self.assertEqual(T.VIDEO_SHEET, "影片清單")

    def test_same_column_names(self):
        src = (ROOT / "pipeline.py").read_text(encoding="utf-8")
        for col in (T.COL_RAW, T.COL_POLISHED, T.COL_DATE, T.COL_ID):
            self.assertIn(f"'{col}'", src, f"pipeline.py 沒有讀 {col}")


class StageTranscriptProceedsWhenRawExists(unittest.TestCase):
    """stage_transcript 的分流：有原文就往下，沒有才等。"""

    def test_raw_present_triggers_polish(self):
        """有原文、沒修飾稿 → 要呼叫 polish，不能停在等待。"""
        calls = []
        real_existing, real_polish, real_write = (
            pl.existing_transcript, pl.polish, pl.write_transcripts)
        pl.existing_transcript = lambda ss, vid, d: ("原文" * 300, "")
        pl.polish = lambda v1: calls.append("polish") or ("修飾" * 200)
        pl.write_transcripts = lambda *a, **k: calls.append("write")
        try:
            v1, v2 = pl.stage_transcript(None, {"id": "x"}, "2026/09/21")
        finally:
            pl.existing_transcript, pl.polish, pl.write_transcripts = (
                real_existing, real_polish, real_write)
        self.assertIn("polish", calls)
        self.assertIn("write", calls)
        self.assertTrue(v2)

    def test_polished_present_skips_polish(self):
        """原文與完整修飾稿都在 → 直接沿用，不重跑潤飾（省額度）。"""
        calls = []
        real_existing, real_polish = pl.existing_transcript, pl.polish
        raw = "原文" * 300
        pl.existing_transcript = lambda ss, vid, d: (raw, "修飾" * 300)
        pl.polish = lambda v1: calls.append("polish")
        try:
            v1, v2 = pl.stage_transcript(None, {"id": "x"}, "2026/09/21")
        finally:
            pl.existing_transcript, pl.polish = real_existing, real_polish
        self.assertEqual(calls, [], "已有完整修飾稿時不該重跑潤飾")
        self.assertTrue(v2)

    def test_no_raw_waits_instead_of_failing(self):
        """沒有原文 → NotReadyYet（等下一輪），不是失敗。

        丟一般例外的話工作會亮紅燈、發告警，但「還沒到」是每天中午的常態。
        """
        real = pl.existing_transcript
        pl.existing_transcript = lambda ss, vid, d: ("", "")
        try:
            with self.assertRaises(pl.NotReadyYet) as cm:
                pl.stage_transcript(None, {"id": "x"}, "2026/09/21")
        finally:
            pl.existing_transcript = real
        self.assertIn("transcript.yml", str(cm.exception),
                      "訊息要講清楚逐字稿是誰負責寫進來的")

    def test_truncated_polished_is_redone(self):
        """修飾稿只有原文的一小截 → 是上一輪沒寫完的殘骸，要重跑。

        2026/09/10 實際發生過：只寫回 13% 就掛掉，而 3080 > 200，
        於是被當成好的沿用，後面每一關讀到的都是那 13%。
        """
        calls = []
        real_existing, real_polish, real_write = (
            pl.existing_transcript, pl.polish, pl.write_transcripts)
        pl.existing_transcript = lambda ss, vid, d: ("原文" * 1000, "殘骸" * 60)
        pl.polish = lambda v1: calls.append("polish") or ("修飾" * 800)
        pl.write_transcripts = lambda *a, **k: None
        try:
            pl.stage_transcript(None, {"id": "x"}, "2026/09/21")
        finally:
            pl.existing_transcript, pl.polish, pl.write_transcripts = (
                real_existing, real_polish, real_write)
        self.assertIn("polish", calls, "殘缺的修飾稿要重跑，不能將就")


class ScheduleAlignment(unittest.TestCase):
    """兩條排程的時間要接得上，否則逐字稿寫進去了卻沒人接。"""

    def test_vod_hour_matches_the_real_schedule(self):
        """直播 11:19 結束、回放 11:25 前後就緒。

        這個值原本是 12（舊時程留下的），會讓 11:20 與 11:50 那兩輪
        在「影片還沒列出來」時被當成必定空手而跳過，白白晚半小時。
        """
        self.assertLessEqual(pl.VOD_EARLIEST_HOUR, 11)

    def test_pipeline_polls_internally(self):
        """daily.yml 那邊要有內部輪詢，才接得到中途才寫進來的逐字稿。

        只靠 cron 準點的話，11:20 那輪看不到（逐字稿 11:30 才到），
        就得等 11:35 那輪——而 GitHub 的 cron 常常漏跑。
        """
        self.assertEqual(pl.POLL_INTERVAL, 180)
        self.assertGreaterEqual(pl.TIME_BUDGET, 900)

    def test_transcript_side_starts_no_later_than_pipeline(self):
        """取稿要先開始，不然下游會一直等一個還沒開始抓的東西。"""
        self.assertEqual(T.POLL_START, "11:20")


if __name__ == "__main__":
    unittest.main(verbosity=2)
