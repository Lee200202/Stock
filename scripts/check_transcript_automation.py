"""Offline regression checks: no API calls, sheet writes or notifications.

Run: python scripts/check_transcript_automation.py
"""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
import tempfile
import contextlib
import io
from unittest.mock import Mock, patch
from datetime import datetime, date, timedelta

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("checked_transcript", ROOT / "transcript.py")
t = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = t
with patch.dict(os.environ, {"TRANSCRIPT_CHANNEL": "classic"}):
    spec.loader.exec_module(t)

DAY = date(2026, 10, 5)
MULTI = "Multiple authentication credentials received. Please pass only one."
URL = "https://www.youtube.com/watch?v=test"


class APIError(Exception):
    def __init__(self, message=MULTI):
        super().__init__(message)
        self.code, self.message = 400, message


class OverloadError(Exception):
    code, message = 503, "This model is currently experiencing high demand."


def at(clock):
    h, m = map(int, clock.split(":"))
    return datetime(DAY.year, DAY.month, DAY.day, h, m, tzinfo=t.TAIPEI)


def fixed_clock(clock):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at(clock)
    return Clock


def video(**extra):
    args = dict(id="test", title="2026/10/05 張震 股市盤中家教班", date=DAY,
                duration_sec=3406, ended_at=at("11:04"), detailed=True)
    args.update(extra)
    return t.Video(**args)


class Checks(unittest.TestCase):
    def setUp(self):
        self.stack = []
        self.addCleanup(self.cleanup)
        self.mock("log")
        self.mock("YOUTUBE_API_KEY", "test-only")
        self.mock("RUN_STARTED", t.time.monotonic())
        t._CHANNEL["classic"] = True

    def cleanup(self):
        for p in reversed(self.stack):
            p.stop()

    def mock(self, name, value=None, **kw):
        p = patch.object(t, name, value, **kw) if value is not None else patch.object(t, name, **kw)
        self.stack.append(p)
        return p.start()

    def transcriber(self, classic=True):
        from google import genai
        with patch.object(genai, "Client"):
            tr = t.Transcriber(["dummy1", "dummy2", "dummy3"], probe=False)
        t._CHANNEL["classic"] = classic
        tr.clients = [NS(interactions=Mock(), models=Mock()) for _ in tr.keys]
        return tr

    def test_default_channel_is_classic(self):
        self.assertTrue(t._CHANNEL["classic"])

    def test_default_request_skips_interactions(self):
        tr = self.transcriber()
        tr._run_classic = Mock(return_value="ok")
        self.assertEqual(tr._run({}), "ok")
        tr.client.interactions.create.assert_not_called()

    def test_create_auth_conflict_switches_same_key(self):
        tr = self.transcriber(False)
        tr.client.interactions.create.side_effect = APIError()
        tr._run_classic = Mock(return_value="ok")
        self.assertEqual(tr._run({}), "ok")
        self.assertEqual(tr.key_index, 0)
        tr.client.interactions.create.assert_called_once()
        tr._run_classic.assert_called_once()

    def test_stream_auth_conflict_switches_same_key(self):
        tr = self.transcriber(False)
        tr.stream_models.add(tr.model)
        tr._run_stream = Mock(side_effect=APIError())
        tr._run_classic = Mock(return_value="ok")
        self.assertEqual(tr._run({}), "ok")
        tr._run_stream.assert_called_once()
        tr._run_classic.assert_called_once()

    def test_probe_matches_default_channel(self):
        tr = self.transcriber()
        tr.probe_enabled = True
        self.assertEqual(tr._probe(0), (True, "", False))
        tr.clients[0].models.generate_content.assert_called_once()
        tr.clients[0].interactions.create.assert_not_called()
        tr._probe(0)  # five-minute health cache
        tr.clients[0].models.generate_content.assert_called_once()

    def test_probe_conflict_falls_back_once(self):
        tr = self.transcriber(False)
        tr.probe_enabled = True
        tr.clients[0].interactions.create.side_effect = APIError()
        self.assertEqual(tr._probe(0), (True, "", False))
        tr.clients[0].interactions.create.assert_called_once()
        tr.clients[0].models.generate_content.assert_called_once()

    def test_classic_conflict_stops_without_rotating(self):
        tr = self.transcriber()
        tr._run_classic = Mock(side_effect=APIError())
        with self.assertRaises(t.AuthenticationConflict):
            tr._request(URL, 0, 1800)
        tr._run_classic.assert_called_once()
        self.assertEqual(tr.key_index, 0)

    def test_probe_classic_conflict_stops(self):
        tr = self.transcriber()
        tr.probe_enabled = True
        tr.clients[0].models.generate_content.side_effect = APIError()
        with self.assertRaises(t.AuthenticationConflict):
            tr._probe(0)
        tr.clients[0].models.generate_content.assert_called_once()

    def test_all_primary_keys_overloaded_use_backup_without_wait(self):
        tr = self.transcriber()
        tr.models = ["main", "backup"]
        calls = []
        def run(request):
            calls.append((tr.model, tr.key_index))
            if tr.model == "main":
                raise OverloadError()
            return t._StreamResult("completed", "備援取得完整原稿。")
        tr._run = run
        with patch.object(t.time, "sleep") as sleep:
            self.assertEqual(tr._range(URL, 0, 1800), "備援取得完整原稿。")
        self.assertEqual(calls, [("main", 0), ("main", 1), ("main", 2), ("backup", 0)])
        sleep.assert_not_called()

    def test_one_overload_still_allows_second_key_on_primary(self):
        tr = self.transcriber()
        tr._run = Mock(side_effect=[OverloadError(), t._StreamResult("completed", "主模型換金鑰恢復。")])
        self.assertEqual(tr._range(URL, 0, 1800), "主模型換金鑰恢復。")
        self.assertEqual(tr.model_index, 0)
        self.assertEqual(tr.key_index, 1)

    def test_short_truncated_response_is_not_cached_as_complete(self):
        tr = self.transcriber()
        tr._request = Mock(return_value=("截斷稿", False))
        cache = {}
        with self.assertRaises(t.NotReadyYet):
            tr.transcribe(URL, t.MIN_SPLIT_SECONDS, cache=cache)
        self.assertEqual(cache, {})

    def test_live_upcoming_and_processing_do_not_call_gemini(self):
        self.mock("read_state", return_value=(None, [], None, {}))
        transcriber = self.mock("Transcriber")
        self.mock("datetime", fixed_clock("11:05"))
        for v in (video(live_status="live"), video(live_status="upcoming"),
                  video(ended_at=None), video(duration_sec=0),
                  video(privacy="private"), video()):
            with self.subTest(status=v.status_text(at("11:05"))):
                self.mock("find_video", return_value=v)
                with self.assertRaises(t.NotReadyYet):
                    t.tick(object(), DAY, False, {})
        transcriber.assert_not_called()

    def test_exact_five_minute_boundary(self):
        v = video()
        self.assertFalse(v.is_ready(at("11:09") - timedelta(seconds=1)))
        self.assertTrue(v.is_ready(at("11:09")))

    def test_existing_transcript_skips_all_external_work(self):
        self.mock("read_state", return_value=(None, [], 2, {
            t.COL_RAW: "原稿" * t.MIN_TRANSCRIPT, t.COL_SOURCE: t.SRC_AUTO}))
        find = self.mock("find_video")
        with self.assertRaises(t.Done):
            t.tick(object(), DAY, False, {})
        find.assert_not_called()

    def test_youtube_failure_is_unknown_and_never_uses_rss(self):
        self.mock("fetch_feed_api", side_effect=RuntimeError("HTTP 503"))
        rss = self.mock("fetch_feed_rss")
        with self.assertRaises(t.NotReadyYet):
            t.find_video(DAY, require_details=True, verify_absence=True)
        rss.assert_not_called()

    def test_missing_api_key_fails_configuration(self):
        self.mock("YOUTUBE_API_KEY", "")
        with self.assertRaises(t.Done) as exc:
            t.find_video(DAY, require_details=True)
        self.assertEqual(exc.exception.code, 1)

    def test_normal_missing_poll_is_cheap(self):
        self.mock("fetch_feed_api", return_value=[])
        search = self.mock("_yt_get")
        self.assertIsNone(t.find_video(DAY, require_details=True))
        search.assert_not_called()

    def test_final_missing_checks_all_three_event_lists(self):
        self.mock("fetch_feed_api", return_value=[])
        search = self.mock("_yt_get", return_value={"items": []})
        self.assertIsNone(t.find_video(DAY, require_details=True, verify_absence=True))
        self.assertEqual([c.kwargs["eventType"] for c in search.call_args_list],
                         ["live", "upcoming", "completed"])

    def test_late_live_is_found_outside_uploads(self):
        self.mock("fetch_feed_api", return_value=[])
        self.mock("_yt_get", side_effect=[{"items": [{"id": {"videoId": "test"}}]},
                                           {"items": []}, {"items": []}])
        v = video(live_status="live")
        self.mock("fetch_video_details", return_value=[v])
        self.assertIs(t.find_video(DAY, True, True), v)

    def test_partial_details_never_claim_absence(self):
        self.mock("_yt_get", return_value={"items": []})
        with self.assertRaises(t.NotReadyYet):
            t.fetch_video_details(["missing"])

    def test_search_failure_never_claims_absence(self):
        self.mock("fetch_feed_api", return_value=[])
        self.mock("_yt_get", side_effect=RuntimeError("quotaExceeded"))
        with self.assertRaises(t.NotReadyYet):
            t.find_video(DAY, True, True)

    def test_search_pagination_has_budget(self):
        self.mock("fetch_feed_api", return_value=[])
        search = self.mock("_yt_get", return_value={"items": [], "nextPageToken": "same"})
        with self.assertRaises(t.NotReadyYet):
            t.find_video(DAY, True, True)
        self.assertEqual(search.call_count, 3)

    def test_adaptive_polling_and_final_boundary(self):
        self.assertEqual(t.next_poll_delay(DAY, at("11:05"), {"video": False}), 180)
        self.assertEqual(t.next_poll_delay(DAY, at("12:30"), {"video": False}), 600)
        self.assertEqual(t.next_poll_delay(DAY, at("12:30"), {"video": True}), 180)
        self.assertEqual(t.next_poll_delay(DAY, at("12:30"), {}), 180)
        self.assertEqual(t.next_poll_delay(DAY, at("15:19"), {"video": False}), 60)

    def prepare_auto(self, clock):
        self.mock("datetime", fixed_clock(clock))
        self.mock("why_closed", return_value="")
        self.mock("open_sheets", return_value=object())
        self.mock("already_marked_no_show", return_value=False)
        self.mock("prior_show_notice", return_value=False)
        return self.mock("write_status_log")

    def test_previous_missing_state_is_cleared_on_next_failed_poll(self):
        status = self.prepare_auto("15:20")
        def poll(ss, target, force, seen):
            if poll.calls == 0:
                seen.update(video=False, absence_verified=False)
                poll.calls += 1
                return False
            self.assertEqual(seen, {})
            raise t.Done("stop fixture")
        poll.calls = 0
        self.mock("tick", side_effect=poll)
        with patch.object(t.time, "sleep"):
            self.assertEqual(t.cmd_auto(NS(date=DAY, force=False, once=False)), 0)
        status.assert_not_called()

    def test_verified_absence_written_once(self):
        status = self.prepare_auto("15:20")
        def absent(ss, target, force, seen):
            seen.update(video=False, absence_verified=True)
            return False
        self.mock("tick", side_effect=absent)
        self.assertEqual(t.cmd_auto(NS(date=DAY, force=False, once=False)), 0)
        self.assertEqual(status.call_args.args[1], t.NO_SHOW_KIND)
        status.assert_called_once()

    def test_authentication_conflict_is_visible_job_failure(self):
        status = self.prepare_auto("11:05")
        self.mock("tick", side_effect=t.AuthenticationConflict("fixture"))
        self.assertEqual(t.cmd_auto(NS(date=DAY, force=False, once=False)), 1)
        self.assertEqual(status.call_args.args[1], "轉錄認證衝突")

    def test_live_verifier_saves_two_segments_without_production_writes(self):
        import verify_transcript_live as verifier
        vt = verifier.t
        def transcribe(url, duration, cache):
            cache[(0, 1800)] = "第一段盤勢內容。" * 30
            cache[(1800, duration)] = "第二段產業內容。" * 30
            return "\n\n".join(cache.values())
        tr = NS(segment_seconds=1800, transcribe=transcribe, summary=lambda: "fixture")
        with tempfile.TemporaryDirectory() as folder, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["verify", "--date", "2026/10/05"]))
            stack.enter_context(patch.dict(os.environ, {"TRANSCRIPT_VERIFY_OUTPUT": folder}))
            for name, value in {
                "find_video": video(), "open_sheets": object(), "load_day_vocabulary": [],
                "read_state": (None, [], 1, {vt.COL_RAW: "原稿" * 150}),
                "gemini_keys": ["dummy"], "Transcriber": tr,
            }.items():
                stack.enter_context(patch.object(vt, name, return_value=value))
            save = stack.enter_context(patch.object(vt, "save_transcript"))
            status = stack.enter_context(patch.object(vt, "write_status_log"))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verifier.main(), 0)
            save.assert_not_called()
            status.assert_not_called()
            import json
            report = json.loads((Path(folder) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["completed_segments"], 2)
            self.assertEqual(report["segments"][1]["end_sec"], 3406)


if __name__ == "__main__":
    unittest.main(verbosity=2)
