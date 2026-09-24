"""A prior-day cancellation must stop dense polling without hiding a late video."""

import importlib.util
import pathlib
import sys
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("transcript_under_test", ROOT / "transcript.py")
tx = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = tx
spec.loader.exec_module(tx)


class PlannedNoShowTest(unittest.TestCase):
    def test_only_explicit_first_person_notice_counts(self):
        self.assertTrue(tx.planned_no_show("不要問我明天怎麼沒有上，我已經跟你們請假了。"))
        self.assertTrue(tx.planned_no_show("明天我放假，我們下禮拜見。"))
        self.assertTrue(tx.planned_no_show("如果明天我放假會怎樣？不過明天我放假，已請假。"))
        for raw in ("明天股市放假嗎？", "明天如果我請假，股票怎麼辦？",
                    "如果明天我放假，再來討論", "明天我放假嗎？",
                    "有人問明天要不要放假", "今天我放假，明天照常上"):
            self.assertFalse(tx.planned_no_show(raw), raw)

    def test_reads_previous_trading_days_original_only(self):
        rows = []
        def read(_ss, _id, day):
            rows.append(day)
            return None, [], 2, {
                tx.COL_RAW: "明天我放假，下一次再見。",
                tx.COL_POLISHED: "明天照常上。"
            }
        with patch.object(tx, "read_state", side_effect=read):
            self.assertTrue(tx.prior_show_notice(object(), date(2026, 9, 24)))
        self.assertEqual(rows, ["2026/09/23"])

    def run_auto(self, hour, minute, video=False):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 24, hour, minute, tzinfo=tz)

        calls = []
        def tick(_ss, _day, _force, seen):
            calls.append("tick")
            seen["video"] = video
            return video

        args = SimpleNamespace(date=date(2026, 9, 24), force=False, once=False)
        with patch.object(tx, "datetime", FixedDateTime), \
             patch.object(tx, "why_closed", return_value=""), \
             patch.object(tx, "open_sheets", return_value=object()), \
             patch.object(tx, "already_marked_no_show", return_value=False), \
             patch.object(tx, "already_marked_status", return_value=False), \
             patch.object(tx, "prior_show_notice", return_value=True), \
             patch.object(tx, "tick", side_effect=tick), \
             patch.object(tx, "write_status_log") as write:
            result = tx.cmd_auto(args)
        return result, calls, write.call_args_list

    def test_1105_checks_once_and_records_provisional_state(self):
        code, calls, writes = self.run_auto(11, 5)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["tick"])
        self.assertEqual([w.args[1] for w in writes], [tx.PLANNED_NO_SHOW_KIND])

    def test_1230_confirms_no_show(self):
        code, calls, writes = self.run_auto(12, 31)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["tick"])
        self.assertEqual([w.args[1] for w in writes], [tx.NO_SHOW_KIND])

    def test_actual_video_overrides_prior_notice(self):
        code, calls, writes = self.run_auto(11, 5, video=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["tick"])
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
