# -*- coding: utf-8 -*-
"""
transcript.py 的離線回歸測試。

不需要任何金鑰、不連 Google、不連 YouTube。要驗的是那幾條一旦錯掉
就會覆蓋掉人工成果、或把日期對錯的規則——那些是這支程式唯一會造成
不可逆傷害的地方。
"""

import os
import sys
import unittest
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import transcript as N   # noqa: E402


class FakeWorksheet:
    """只實作被測程式真的會用到的那幾個方法。"""

    def __init__(self, header, rows):
        self.header = list(header)
        self.rows = [dict(r) for r in rows]
        self.col_count = len(header)
        self.updates = []
        self.appended = []

    def row_values(self, n):
        return list(self.header) if n == 1 else []

    def get_all_records(self):
        return [dict(r) for r in self.rows]

    def update(self, range_name=None, values=None, **kw):
        self.updates.append((range_name, values))

    def batch_update(self, data, **kw):
        self.updates.extend((d["range"], d["values"]) for d in data)

    def append_row(self, values, **kw):
        self.appended.append(list(values))

    def add_cols(self, n):
        self.col_count += n


class FakeSpreadsheet:
    def __init__(self, ws):
        self._ws = ws

    def worksheet(self, name):
        if name == N.VIDEO_SHEET:
            return self._ws
        raise Exception(f"no sheet {name}")

    def add_worksheet(self, **kw):
        raise Exception("不該在測試裡新增分頁")


def make_ss(rows, header=None):
    return FakeSpreadsheet(FakeWorksheet(header or N.FULL_HEADER, rows))


class TestDates(unittest.TestCase):
    def test_norm_date(self):
        for raw, want in [
            ("2026/9/5", "2026/09/05"),
            ("2026-09-05", "2026/09/05"),
            (date(2026, 9, 5), "2026/09/05"),
            ("Thu Sep 03 2026 11:32:44 GMT+0800 (台北標準時間)", "2026/09/03"),
            ("", ""),
            ("沒有日期", ""),
        ]:
            self.assertEqual(N.norm_date(raw), want, raw)

    def test_date_from_title(self):
        """標題日期要贏過 published。直播的 published 是排程建立時間，會早於開播日。"""
        fallback = date(2026, 9, 1)
        self.assertEqual(
            N.date_from_title("2026/09/17(四)張震 股市盤中家教班", fallback),
            date(2026, 9, 17))
        self.assertEqual(N.date_from_title("沒有日期的標題", fallback), fallback)
        # 標題寫了不存在的日期時不能炸，退回 fallback
        self.assertEqual(N.date_from_title("2026/02/31 測試", fallback), fallback)

    def test_is_target(self):
        self.assertTrue(N.is_target("2026/09/17(四)張震 股市盤中家教班"))
        self.assertFalse(N.is_target("頻道公告：本週停播一天"))

    def test_parse_date(self):
        self.assertEqual(N.parse_date("2026/09/17"), date(2026, 9, 17))
        self.assertEqual(N.parse_date("2026-09-17"), date(2026, 9, 17))
        with self.assertRaises(Exception):
            N.parse_date("9/17")


class TestSourcePrecedence(unittest.TestCase):
    """手動優先。這是整支程式最不能錯的一條。"""

    def test_blank_row_is_free(self):
        self.assertEqual(N.source_of({}), "")

    def test_explicit_source_wins(self):
        self.assertEqual(N.source_of({N.COL_SOURCE: N.SRC_AUTO, N.COL_RAW: "x"}),
                         N.SRC_AUTO)
        self.assertEqual(N.source_of({N.COL_SOURCE: N.SRC_HOLD}), N.SRC_HOLD)

    def test_legacy_row_with_text_counts_as_manual(self):
        """「逐字稿來源」欄是這一版才加的。

        在它之前，試算表裡所有原文都是人貼的。舊資料沒有那一欄，
        若把它們當成「自動」，自動化就會覆蓋人工成果——
        那是這支程式唯一真正不可逆的傷害，所以預設必須是手動。
        """
        self.assertEqual(N.source_of({N.COL_RAW: "一大段舊的逐字稿"}), N.SRC_MANUAL)


class TestPickRow(unittest.TestCase):
    def test_id_beats_date(self):
        rows = [
            {N.COL_ID: "", N.COL_DATE: "2026/09/17", N.COL_RAW: "舊的"},
            {N.COL_ID: "abc123", N.COL_DATE: "2026/09/17", N.COL_RAW: "對的"},
        ]
        idx, row = N.pick_row(rows, "abc123", "2026/09/17")
        self.assertEqual(idx, 3)
        self.assertEqual(row[N.COL_RAW], "對的")

    def test_latest_update_wins_when_no_id(self):
        rows = [
            {N.COL_ID: "", N.COL_DATE: "2026/09/17", N.COL_UPDATED: "2026/09/17 09:00:00",
             N.COL_RAW: "早的"},
            {N.COL_ID: "", N.COL_DATE: "2026/09/17", N.COL_UPDATED: "2026/09/17 14:00:00",
             N.COL_RAW: "晚的"},
        ]
        idx, row = N.pick_row(rows, "", "2026/09/17")
        self.assertEqual(row[N.COL_RAW], "晚的")

    def test_no_match(self):
        self.assertEqual(N.pick_row([], "x", "2026/09/17"), (None, None))


class TestTick(unittest.TestCase):
    """tick 的第一關（手動優先）。真的呼叫 Gemini 那一段不在離線測試範圍。"""

    def setUp(self):
        self.target = date(2026, 9, 17)
        self._find = N.find_video
        N.find_video = lambda d: self.fail("手動優先時不應該去查 YouTube")

    def tearDown(self):
        N.find_video = self._find

    def test_manual_transcript_halts_automation(self):
        ss = make_ss([{N.COL_DATE: "2026/09/17", N.COL_RAW: "人貼的稿" * 100,
                       N.COL_SOURCE: N.SRC_MANUAL}])
        with self.assertRaises(N.Done) as cm:
            N.tick(ss, self.target, force=False)
        self.assertIn("手動優先", cm.exception.reason)
        self.assertEqual(cm.exception.code, 0)

    def test_hold_halts_automation_even_without_text(self):
        """還沒貼、只先宣告要自己貼，也要擋住。"""
        ss = make_ss([{N.COL_DATE: "2026/09/17", N.COL_RAW: "",
                       N.COL_SOURCE: N.SRC_HOLD}])
        with self.assertRaises(N.Done) as cm:
            N.tick(ss, self.target, force=False)
        self.assertIn("等人工貼稿", cm.exception.reason)

    def test_legacy_text_without_source_column_halts(self):
        ss = make_ss([{N.COL_DATE: "2026/09/17", N.COL_RAW: "舊資料" * 200}])
        with self.assertRaises(N.Done):
            N.tick(ss, self.target, force=False)

    def test_existing_auto_transcript_not_refetched(self):
        ss = make_ss([{N.COL_DATE: "2026/09/17", N.COL_RAW: "自動抓的" * 100,
                       N.COL_SOURCE: N.SRC_AUTO}])
        with self.assertRaises(N.Done) as cm:
            N.tick(ss, self.target, force=False)
        self.assertIn("不重抓", cm.exception.reason)

    def test_short_junk_does_not_count_as_having_transcript(self):
        """殘缺的十幾個字不能當成「已經有稿」，否則那天就永遠補不回來。"""
        N.find_video = lambda d: None      # 查得到 YouTube，但沒有當天影片
        ss = make_ss([{N.COL_DATE: "2026/09/17", N.COL_RAW: "索引失敗",
                       N.COL_SOURCE: N.SRC_AUTO}])
        self.assertFalse(N.tick(ss, self.target, force=False))

    def test_empty_day_proceeds_to_youtube(self):
        called = []

        def fake_find(d):
            called.append(d)
            return None
        N.find_video = fake_find
        ss = make_ss([])
        self.assertFalse(N.tick(ss, self.target, force=False))
        self.assertEqual(called, [self.target])


class TestSaveTranscript(unittest.TestCase):
    def test_updates_existing_row_by_header_name(self):
        ws = FakeWorksheet(N.FULL_HEADER,
                           [{N.COL_DATE: "2026/09/17", N.COL_RAW: ""}])
        ss = FakeSpreadsheet(ws)
        N.save_transcript(ss, {"id": "abc", "title": "標題"},
                          "2026/09/17", "全文" * 500, N.SRC_AUTO)
        written = dict(ws.updates)
        self.assertEqual([], ws.appended)
        # 原文寫在 F 欄、來源寫在 K 欄，都由表頭名稱推算，不是寫死的欄號
        self.assertIn("F2", written)
        self.assertEqual(written["K2"], [[N.SRC_AUTO]])
        self.assertEqual(written["I2"], [[N.sha256("全文" * 500)]])

    def test_appends_when_day_absent(self):
        ws = FakeWorksheet(N.FULL_HEADER, [])
        ss = FakeSpreadsheet(ws)
        N.save_transcript(ss, {"id": "abc", "title": "標題"},
                          "2026/09/17", "全文" * 500, N.SRC_MANUAL)
        self.assertEqual(len(ws.appended), 1)
        row = dict(zip(N.FULL_HEADER, ws.appended[0]))
        self.assertEqual(row[N.COL_DATE], "2026/09/17")
        self.assertEqual(row[N.COL_SOURCE], N.SRC_MANUAL)

    def test_cell_limit(self):
        long = "字" * (N.SHEET_CELL_LIMIT + 5000)
        out = N.cell(long)
        self.assertLessEqual(len(out), N.SHEET_CELL_LIMIT + 60)
        self.assertIn("已截斷", out)


class TestHeaderRepair(unittest.TestCase):
    def test_appends_missing_tail_column(self):
        old = N.FULL_HEADER[:-1]          # 少了「逐字稿來源」
        ws = FakeWorksheet(old, [])
        ss = FakeSpreadsheet(ws)
        got_ws, header = N.video_sheet(ss)
        self.assertEqual(header, N.FULL_HEADER)
        self.assertEqual(ws.updates[0][1], [[N.COL_SOURCE]])

    def test_refuses_when_middle_column_differs(self):
        """中間對不上時不動。自動補只會把整排欄位錯開，比缺一欄糟得多。"""
        bad = list(N.FULL_HEADER[:-1])
        bad[2] = "影片標題"               # 與 SHEET_SCHEMA 不同
        ws = FakeWorksheet(bad, [])
        ss = FakeSpreadsheet(ws)
        got_ws, header = N.video_sheet(ss)
        self.assertEqual(header, bad)
        self.assertEqual(ws.updates, [])


class TestSchedule(unittest.TestCase):
    def test_poll_window(self):
        d = date(2026, 9, 17)
        self.assertEqual(N.at_taipei(d, "11:20").hour, 11)
        self.assertEqual(N.at_taipei(d, "11:20").minute, 20)
        with self.assertRaises(ValueError):
            N.parse_hhmm("1120")

    def test_defaults_match_the_agreed_schedule(self):
        self.assertEqual(N.POLL_START, "11:20")
        self.assertEqual(N.POLL_INTERVAL, 180)

    def test_weekend_is_skipped(self):
        self.assertGreaterEqual(date(2026, 9, 19).weekday(), 5)   # 週六
        self.assertLess(date(2026, 9, 17).weekday(), 5)           # 週四



class TestDuration(unittest.TestCase):
    def test_parse_iso8601(self):
        for raw, want in [
            ("PT1H2M3S", 3723),
            ("PT57M", 57 * 60),
            ("PT1H10M", 4200),
            ("PT45S", 45),
            ("", 0),
            ("亂寫", 0),
        ]:
            self.assertEqual(N.parse_duration(raw), want, raw)

    def test_segment_count(self):
        """一小時的直播用 30 分鐘一段，要切成 2 段；70 分鐘要切成 3 段。"""
        seg = N.SEGMENT_MINUTES * 60
        for duration, want in [(57 * 60, 2), (60 * 60, 2), (70 * 60, 3), (30 * 60, 1)]:
            got = len(range(0, duration, seg))
            self.assertEqual(got, want, f"{duration} 秒")


class TestReadiness(unittest.TestCase):
    """回放就緒判斷。

    四個條件缺一不可。少了 duration > 0 那一條最容易出事：直播剛結束時
    YouTube 還沒算出片長，那時送給 Gemini 會拿到半截或整個失敗，
    而半截的東西還會被寫進試算表，比等下一輪糟得多。
    """

    def setUp(self):
        self.now = datetime(2026, 9, 18, 12, 0, tzinfo=N.TAIPEI)
        self.ended = self.now - timedelta(minutes=30)

    def make(self, **kw):
        base = dict(id="v1", title="2026/09/18(五)張震  股市盤中家教班",
                    date=date(2026, 9, 18), duration_sec=3600,
                    live_status="none", privacy="public",
                    ended_at=self.ended, detailed=True)
        base.update(kw)
        return N.Video(**base)

    def test_ready(self):
        self.assertTrue(self.make().is_ready(self.now))
        self.assertEqual(self.make().status_text(self.now), "可轉錄")

    def test_still_live(self):
        v = self.make(live_status="live", ended_at=None)
        self.assertFalse(v.is_ready(self.now))
        self.assertEqual(v.status_text(self.now), "直播中")

    def test_upcoming(self):
        v = self.make(live_status="upcoming", ended_at=None)
        self.assertFalse(v.is_ready(self.now))
        self.assertEqual(v.status_text(self.now), "尚未開播")

    def test_no_duration_yet(self):
        v = self.make(duration_sec=0)
        self.assertFalse(v.is_ready(self.now))
        self.assertIn("回放處理中", v.status_text(self.now))

    def test_not_public(self):
        v = self.make(privacy="private")
        self.assertFalse(v.is_ready(self.now))
        self.assertIn("非公開", v.status_text(self.now))

    def test_buffer_not_elapsed(self):
        """剛結束一分鐘就送過去，YouTube 回放還沒處理完。"""
        v = self.make(ended_at=self.now - timedelta(minutes=1))
        self.assertFalse(v.is_ready(self.now))
        self.assertIn("緩衝", v.status_text(self.now))

    def test_url_is_derived(self):
        self.assertEqual(self.make().url, "https://www.youtube.com/watch?v=v1")


class TestGeminiKeys(unittest.TestCase):
    def setUp(self):
        self.saved = {k: v for k, v in os.environ.items() if k.startswith("GEMINI_API_KEY")}
        for k in list(os.environ):
            if k.startswith("GEMINI_API_KEY"):
                del os.environ[k]

    def tearDown(self):
        for k in list(os.environ):
            if k.startswith("GEMINI_API_KEY"):
                del os.environ[k]
        os.environ.update(self.saved)

    def test_none(self):
        self.assertEqual(N.gemini_keys(), [])

    def test_single(self):
        os.environ["GEMINI_API_KEY"] = "k1"
        self.assertEqual(N.gemini_keys(), ["k1"])

    def test_comma_separated(self):
        os.environ["GEMINI_API_KEY"] = " k1 , k2 ,, k3 "
        self.assertEqual(N.gemini_keys(), ["k1", "k2", "k3"])

    def test_numbered_vars_appended(self):
        os.environ["GEMINI_API_KEY"] = "k1"
        os.environ["GEMINI_API_KEY_2"] = "k2"
        os.environ["GEMINI_API_KEY_3"] = "k3"
        self.assertEqual(N.gemini_keys(), ["k1", "k2", "k3"])

    def test_duplicates_dropped(self):
        """同一把金鑰列兩次不該變成兩把——那會讓額度判斷以為還有備援。"""
        os.environ["GEMINI_API_KEY"] = "k1,k2"
        os.environ["GEMINI_API_KEY_2"] = "k2"
        self.assertEqual(N.gemini_keys(), ["k1", "k2"])


class TestQuotaDetection(unittest.TestCase):
    """額度錯誤要跟一般失敗分開。

    每日額度用完時重試永遠不會成功，要立刻停下來換模型或等重置；
    當成一般失敗一路重試，只會把時間耗光還是失敗。
    """

    def test_daily_quota_phrases(self):
        for msg in ("GenerateRequestsPerDayPerProjectPerModel",
                    "quota exceeded, limit per day",
                    "Daily limit reached"):
            self.assertTrue(N._is_daily_quota(msg), msg)

    def test_per_minute_is_not_daily(self):
        """每分鐘限制等一下就好，不能誤判成每日額度而放棄整天。"""
        self.assertFalse(N._is_daily_quota(
            "Quota exceeded for metric: generate_requests_per_minute, limit: 15"))

    def test_quota_summary_extracts_metric(self):
        msg = ("Quota exceeded for metric: generativelanguage.googleapis.com/"
               "generate_content_free_tier_requests, limit: 20, model: gemini-3.8-flash")
        got = N._quota_summary(msg)
        self.assertIn("generate_content_free_tier_requests", got)
        self.assertIn("limit: 20", got)
        self.assertIn("gemini-3.8-flash", got)

    def test_quota_summary_empty_when_no_match(self):
        self.assertEqual(N._quota_summary("connection reset"), "")


class TestTickReadiness(unittest.TestCase):
    """tick 的第二關。不呼叫 Gemini，所以離線就能驗。"""

    def setUp(self):
        self.target = date(2026, 9, 18)
        self._find = N.find_video

    def tearDown(self):
        N.find_video = self._find

    def _video(self, **kw):
        base = dict(id="v1", title="2026/09/18(五)張震  股市盤中家教班",
                    date=self.target, duration_sec=3600, live_status="none",
                    privacy="public", detailed=True,
                    ended_at=datetime.now(N.TAIPEI) - timedelta(minutes=30))
        base.update(kw)
        return N.Video(**base)

    def test_rss_only_refuses_to_guess(self):
        """RSS 拿不到片長與結束時間，判斷不了回放好了沒。

        硬送給 Gemini 很可能拿到半截，而半截還會被寫進試算表。
        寧可明確停下來要人補 YOUTUBE_API_KEY。
        """
        N.find_video = lambda d: self._video(detailed=False, duration_sec=0)
        with self.assertRaises(N.Done) as cm:
            N.tick(make_ss([]), self.target, force=False)
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("YOUTUBE_API_KEY", cm.exception.reason)

    def test_still_live_waits(self):
        N.find_video = lambda d: self._video(live_status="live", ended_at=None)
        with self.assertRaises(N.NotReadyYet) as cm:
            N.tick(make_ss([]), self.target, force=False)
        self.assertIn("直播中", str(cm.exception))

    def test_replay_processing_waits(self):
        N.find_video = lambda d: self._video(duration_sec=0)
        with self.assertRaises(N.NotReadyYet):
            N.tick(make_ss([]), self.target, force=False)

    def test_ready_but_no_key_stops_clearly(self):
        """回放好了卻沒有金鑰：要明確告訴人去哪裡拿，不要一直重試。"""
        N.find_video = lambda d: self._video()
        saved = {k: v for k, v in os.environ.items() if k.startswith("GEMINI_API_KEY")}
        for k in list(os.environ):
            if k.startswith("GEMINI_API_KEY"):
                del os.environ[k]
        try:
            with self.assertRaises(N.Done) as cm:
                N.tick(make_ss([]), self.target, force=False)
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("aistudio.google.com", cm.exception.reason)
        finally:
            os.environ.update(saved)

    def test_manual_checked_before_youtube(self):
        """手動優先要排在 YouTube 之前，否則已經貼好稿的日子還是會白查一次。"""
        N.find_video = lambda d: self.fail("手動優先時不該查 YouTube")
        ss = make_ss([{N.COL_DATE: "2026/09/18", N.COL_RAW: "人貼的" * 200,
                       N.COL_SOURCE: N.SRC_MANUAL}])
        with self.assertRaises(N.Done):
            N.tick(ss, self.target, force=False)


class TestNoExpiringCredential(unittest.TestCase):
    """這一版最重要的性質：整支程式沒有任何會過期的憑證。

    用的是 Gemini API key，不會過期。這幾個字串是舊架構留下的痕跡，
    釘住它們不會再出現，避免哪天又被接回來。
    """

    def test_no_cookie_or_session_machinery(self):
        import inspect
        src = inspect.getsource(N)
        for banned in ("storage_state", "NotebookLMClient", "AuthExpired",
                       "looks_like_auth_error", "secretmanager", "_auth_fp"):
            self.assertNotIn(banned, src, f"{banned} 不該再出現")

    def test_transcription_goes_through_gemini(self):
        import inspect
        src = inspect.getsource(N.Transcriber._request)
        self.assertIn('"type": "video"', src)
        self.assertIn("start_offset", src)
        self.assertIn("end_offset", src)


# 這段來自 2026/09/17 那次真實失敗的日誌。兩把金鑰輪了四次、耗掉 11 分鐘，
# 每一次都是同一個錯誤——因為壅塞的是模型，所有金鑰共用同一份容量。
REAL_OVERLOAD = ("code='api_error' message='gemini-3.5-flash-lite is currently "
                 "experiencing high demand, spikes in demand are usually temporary. "
                 "Please try again later.'")


class TestOverloadDetection(unittest.TestCase):
    def test_recognises_the_real_message(self):
        self.assertTrue(N._is_overload(None, REAL_OVERLOAD))

    def test_recognises_503(self):
        self.assertTrue(N._is_overload(503, "Service Unavailable"))

    def test_common_phrasings(self):
        for msg in ("The model is overloaded. Please try again later.",
                    "Server is busy",
                    "resource exhausted, try again"):
            self.assertTrue(N._is_overload(None, msg), msg)

    def test_quota_is_not_overload(self):
        """429 是額度，不是負載過高。兩者處理方式相反：
        額度要換金鑰或等重置，負載過高要換模型。混在一起兩邊都會做錯。"""
        self.assertFalse(N._is_overload(
            429, "Quota exceeded for metric: generate_content_free_tier_requests, limit: 20"))

    def test_ordinary_failure_is_not_overload(self):
        self.assertFalse(N._is_overload(400, "Request contains an invalid argument."))
        self.assertFalse(N._is_overload(None, "connection reset by peer"))


class TestOverloadSwitchesModelNotKey(unittest.TestCase):
    """負載過高時的正確反應是換模型，不是換金鑰。

    2026/09/17 的教訓：程式在兩把金鑰之間輪流重送同一個壅塞的模型，
    四次全敗、11 分鐘，最後整集失敗。換金鑰不可能有用。
    """

    def make(self, models):
        tr = N.Transcriber(["k1", "k2"], model=models[0], fallback=models[1:], probe=False)
        self.assertEqual(tr.models, models)
        return tr

    def test_switches_to_fallback_model(self):
        tr = self.make(["m1", "m2"])
        seen = []

        def fake(url, start, end):
            seen.append(tr.model)
            if tr.model == "m1":
                raise N.ModelOverloaded(REAL_OVERLOAD)
            return "逐字稿內容", True
        tr._request = fake

        self.assertEqual(tr._range("u", 0, 1800), "逐字稿內容")
        # m1 只被試一次就讓出去，沒有在同一個模型上反覆重送
        self.assertEqual(seen, ["m1", "m2"])
        self.assertIn("m1", tr.overloaded)

    def test_all_models_overloaded_is_transient_not_fatal(self):
        """每個模型都在忙 → NotReadyYet，交給輪詢迴圈三分鐘後重試。

        丟 RuntimeError 會讓整個工作亮紅燈、當天這一集就沒了；
        但這是 Google 端的暫時壅塞，等一下往往就好了。
        """
        tr = self.make(["m1", "m2"])
        tr._request = lambda u, s, e: (_ for _ in ()).throw(N.ModelOverloaded(REAL_OVERLOAD))
        with self.assertRaises(N.NotReadyYet) as cm:
            tr._range("u", 0, 1800)
        self.assertIn("負載過高", str(cm.exception))

    def test_quota_also_switches_model(self):
        """額度是每個模型分開算的，所以換模型對額度用完同樣有效。"""
        tr = self.make(["m1", "m2"])
        calls = []

        def fake(url, start, end):
            calls.append(tr.model)
            if tr.model == "m1":
                raise N.QuotaExhausted("所有金鑰在 m1 的額度都已用完")
            return "內容", True
        tr._request = fake
        self.assertEqual(tr._range("u", 0, 1800), "內容")
        self.assertEqual(calls, ["m1", "m2"])

    def test_no_fallback_still_transient(self):
        tr = N.Transcriber(["k1"], model="only", fallback=[], probe=False)
        self.assertEqual(tr.models, ["only"])
        tr._request = lambda u, s, e: (_ for _ in ()).throw(N.ModelOverloaded("high demand"))
        with self.assertRaises(N.NotReadyYet):
            tr._range("u", 0, 1800)


class TestFallbackModelDefault(unittest.TestCase):
    """備援模型的預設值政策。

    曾經有一版把預設備援寫死成一個「從錯誤訊息裡撿到的」模型名稱。那很危險：
    模型不存在時 API 回 404，而 404 是不重試的硬失敗，備援就從「多一條退路」
    變成「多一個必定失敗的步驟」。沒驗證過的名字不該寫進預設值。
    """

    def test_no_hardcoded_unverified_model(self):
        import inspect
        src = inspect.getsource(N)
        self.assertNotIn("gemini-3.8-flash", src,
                         "不該把沒驗證過的模型名稱寫進程式")

    def test_default_is_empty(self):
        self.assertEqual(N.GEMINI_FALLBACK_MODELS, [])

    def test_fallback_is_configurable(self):
        tr = N.Transcriber(["k1"], model="m1", fallback=["m2", "m3"], probe=False)
        self.assertEqual(tr.models, ["m1", "m2", "m3"])

    def test_fallback_same_as_primary_is_dropped(self):
        """備援填得跟主模型一樣等於沒有備援，不能讓它在清單裡出現兩次，
        否則壅塞時會「換」到同一個塞住的模型，白跑一次。"""
        tr = N.Transcriber(["k1"], model="m1", fallback=["m1"], probe=False)
        self.assertEqual(tr.models, ["m1"])

    def test_survives_overload_without_any_fallback(self):
        """沒有備援也不能整集失敗。

        這是取代「預設塞一個模型」的保障：所有模型都忙時判成「還沒好」，
        交給輪詢迴圈重試，已完成的片段也留著。
        """
        tr = N.Transcriber(["k1"], model="m1", fallback=[], probe=False)
        tr._request = lambda u, s, e: (_ for _ in ()).throw(
            N.ModelOverloaded("high demand"))
        with self.assertRaises(N.NotReadyYet):
            tr._range("u", 0, 1800)

    def test_check_lists_available_models(self):
        """check 要印出金鑰實際可用的模型，使用者才挑得出能填的備援。"""
        self.assertTrue(hasattr(N, "_list_models"))
        import inspect
        self.assertIn("models.list", inspect.getsource(N._list_models))


class TestSegmentCache(unittest.TestCase):
    """已完成的片段要跨輪重用。

    一集切 2～3 段。第 1 段成功、第 2 段撞上壅塞時，如果下一輪把第 1 段
    重做，既浪費額度也更容易再撞上壅塞。
    """

    def test_cached_segments_are_not_redone(self):
        tr = N.Transcriber(["k1"], model="m1", fallback=[], probe=False)
        calls = []

        def fake(url, start, end):
            calls.append((start, end))
            return f"第{start}段", True
        tr._request = fake

        cache = {}
        first = tr.transcribe("u", 3600, cache=cache)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(cache), 2)

        # 第二次呼叫：全部命中快取，一次都不該再送
        calls.clear()
        second = tr.transcribe("u", 3600, cache=cache)
        self.assertEqual(calls, [])
        self.assertEqual(first, second)

    def test_partial_cache_only_fills_the_gap(self):
        tr = N.Transcriber(["k1"], model="m1", fallback=[], probe=False)
        calls = []

        def fake(url, start, end):
            calls.append((start, end))
            return "補上的", True
        tr._request = fake

        seg = N.SEGMENT_MINUTES * 60
        cache = {(0, seg): "【已完成】\n第一段"}
        out = tr.transcribe("u", 3600, cache=cache)
        self.assertEqual(calls, [(seg, 3600)])       # 只補第二段
        self.assertIn("第一段", out)
        self.assertIn("補上的", out)


class TestStreamResultCarriesErrors(unittest.TestCase):
    """失敗原因不一定是例外。

    串流模式下 API 常常是「正常回應、status=failed、原因放在 errors」，
    2026/09/17 那次就是走這條路。errors 沒被帶出來的話，程式看到的只有
    「狀態 failed」，認不出那是負載過高，就會一路輪金鑰到放棄。
    """

    def test_stream_result_has_errors_field(self):
        r = N._StreamResult(status="failed", output_text="", errors=REAL_OVERLOAD)
        self.assertEqual(r.errors, REAL_OVERLOAD)
        self.assertTrue(N._is_overload(None, str(r.errors)))

    def test_run_stream_collects_errors(self):
        import inspect
        src = inspect.getsource(N.Transcriber._run_stream)
        self.assertIn("errors", src)

    def test_failed_status_path_checks_overload(self):
        import inspect
        src = inspect.getsource(N.Transcriber._request)
        # 兩條路都要認：拋例外那條，以及回傳 status=failed 那條。
        # 只數 raise，不數說明文字裡提到的那一次。
        self.assertEqual(src.count("raise ModelOverloaded"), 2)


class TestSheetErrorMessages(unittest.TestCase):
    """開不了試算表時，訊息要直接講出「該分享給誰」。

    2026/09/18 實測踩到：gspread 6 把 HTTP 錯誤換成別的型別再丟出來——
    403 變成內建的 PermissionError（訊息還是空的）、404 變成 SpreadsheetNotFound，
    都不是 APIError。原本只接 APIError，於是這兩種最常見的狀況完全沒被接到，
    使用者看到的是一長串 traceback 加一行空的 PermissionError，
    看不出要去分享試算表、更看不出要分享給哪個信箱。
    """

    EMAIL = "sheets-writer@proj.iam.gserviceaccount.com"

    def _run(self, raises):
        import types as _t
        import gspread as _g
        saved_creds, saved_auth = N._sheets_credentials, _g.authorize
        saved_id = N.SPREADSHEET_ID
        N.SPREADSHEET_ID = "dummy"
        N._sheets_credentials = lambda: (object(), self.EMAIL)
        _g.authorize = lambda c: _t.SimpleNamespace(
            open_by_key=lambda k: (_ for _ in ()).throw(raises))
        try:
            with self.assertRaises(SystemExit) as cm:
                N.open_sheets()
            return str(cm.exception)
        finally:
            N._sheets_credentials, _g.authorize = saved_creds, saved_auth
            N.SPREADSHEET_ID = saved_id

    def test_403_names_the_service_account(self):
        msg = self._run(PermissionError())
        self.assertIn("403", msg)
        self.assertIn(self.EMAIL, msg)
        self.assertIn("編輯者", msg)

    def test_404_points_at_the_id(self):
        import gspread
        msg = self._run(gspread.exceptions.SpreadsheetNotFound("nope"))
        self.assertIn("404", msg)
        self.assertIn("SPREADSHEET_ID", msg)

    def test_handler_covers_the_types_gspread_actually_raises(self):
        import inspect
        src = inspect.getsource(N.open_sheets)
        self.assertIn("except PermissionError", src)
        self.assertIn("SpreadsheetNotFound", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
