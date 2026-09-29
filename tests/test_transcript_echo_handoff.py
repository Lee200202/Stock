"""The raw transcription cleaner and downstream audit must agree on prompt echoes."""

import importlib.util
import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


raw = load("raw_transcript_echo", "transcript.py")
daily = load("daily_transcript_echo", "pipeline/pipeline.py")


class TranscriptEchoHandoffTests(unittest.TestCase):
    def test_two_echoes_removed_without_losing_speech(self):
        text = ("今天談毅嘉，不要把類股題材當成持股聲明。\n"
                "請聽打這段影片 0:30:00 到 1:00:00 的完整逐字稿。\n"
                "盤中量能還要看收盤確認。\n"
                "可能出現的專有名詞：毅嘉，鴻準，勤誠，晶心科。\n"
                "有些人會說請聽打，但這是講者原話。")
        expected = ("今天談毅嘉，不要把類股題材當成持股聲明。\n"
                    "盤中量能還要看收盤確認。\n"
                    "有些人會說請聽打，但這是講者原話。")
        for cleaner in (raw.strip_transcribe_echo, daily.strip_transcribe_echo):
            result, removed = cleaner(text)
            self.assertEqual(result, expected)
            self.assertEqual(len(removed), 2)

    def test_only_echo_is_not_a_usable_transcript(self):
        for cleaner in (raw.strip_transcribe_echo, daily.strip_transcribe_echo):
            cleaned, removed = cleaner("請聽打這段影片 0:00:00 到 0:30:00 的完整逐字稿。")
            self.assertFalse(cleaned.strip())
            self.assertEqual(len(removed), 1)


if __name__ == "__main__":
    unittest.main()
