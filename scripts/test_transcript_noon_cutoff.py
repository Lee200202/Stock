"""中午無片即停止當日：使用真實取稿控制流程，外部服務全部替身。"""
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import transcript as T


class NoonCutoffTests(unittest.TestCase):
    def setUp(self):
        self.target = date(2026, 10, 22)
        self.ss = Mock()
        self.args = SimpleNamespace(date=self.target, force=False, once=True)
        self.hour, self.minute = 12, 0
        owner = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 10, 22, owner.hour, owner.minute, tzinfo=T.TAIPEI)
        for name, replacement in [('datetime', Clock), ('why_closed', lambda _: None),
                                  ('open_sheets', lambda: self.ss), ('NO_SHOW_AFTER', '12:00'),
                                  ('prior_show_notice', lambda *_: False)]:
            self.enterContext(patch.object(T, name, replacement))
        self.enterContext(patch.object(T, 'read_state', return_value=(None, [], 2, {})))
        self.marked = self.enterContext(patch.object(T, 'already_marked_no_show', return_value=False))
        self.find = self.enterContext(patch.object(T, 'find_video', return_value=None))
        self.write = self.enterContext(patch.object(T, 'write_status_log'))
        self.transcriber = self.enterContext(patch.object(T, 'Transcriber'))

    def test_before_noon_missing_video_keeps_waiting(self):
        self.hour, self.minute = 11, 59
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.assertFalse(self.find.call_args.kwargs['verify_absence'])
        self.write.assert_not_called()

    def test_at_noon_confirmed_absence_stops_without_model(self):
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.assertTrue(self.find.call_args.kwargs['verify_absence'])
        self.assertEqual(self.write.call_args.args[1], T.NO_SHOW_KIND)
        self.transcriber.assert_not_called()

    def test_later_same_day_exits_before_youtube(self):
        self.marked.return_value = True
        self.hour = 13
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.find.assert_not_called()

    def test_next_trading_day_ignores_yesterday_status(self):
        self.ss.worksheet.return_value.get_all_records.return_value = [
            {'時間': '2026/10/22 12:00:00', '類別': T.NO_SHOW_KIND}]
        self.assertTrue(T.already_marked_status(self.ss, '2026/10/22', T.NO_SHOW_KIND))
        self.assertFalse(T.already_marked_status(self.ss, '2026/10/23', T.NO_SHOW_KIND))

    def test_api_error_does_not_mark_absence(self):
        self.find.side_effect = T.NotReadyYet('查詢失敗')
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.write.assert_not_called()

    def test_detected_live_keeps_waiting_for_replay(self):
        video = Mock(title='當日節目', detailed=True, duration_sec=100,
                     status_text=lambda _: '直播中')
        video.is_ready.return_value = False
        self.find.return_value = video
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.write.assert_not_called()
        self.transcriber.assert_not_called()

    def test_manual_transcript_has_priority(self):
        with patch.object(T, 'source_of', return_value=T.SRC_MANUAL):
            self.assertEqual(T.cmd_auto(self.args), 0)
        self.find.assert_not_called()
        self.write.assert_not_called()

    def test_force_can_override_same_day_stop(self):
        self.args.force = True
        self.marked.return_value = True
        self.assertEqual(T.cmd_auto(self.args), 0)
        self.marked.assert_not_called()
        self.assertFalse(self.find.call_args.kwargs['verify_absence'])
        self.write.assert_not_called()

    def test_sleep_never_skips_noon_boundary(self):
        before = datetime(2026, 10, 22, 11, 59, 40, tzinfo=T.TAIPEI)
        self.assertEqual(T.next_poll_delay(self.target, before, {'video': False}), 20)


if __name__ == '__main__':
    unittest.main()
