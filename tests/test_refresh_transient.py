"""刷新時下游暫時性錯誤與名稱補正（2026/09/13 重跑 9/11）。

實際日誌：
  一、smsmail 之後「操作紀錄『信化』觀望不碰 少了 1 筆」——其實是撰稿前代號補齊把
      代號待確認的「信化」改成正式名稱「信驊」，筆數沒少，被計數方式誤報成刪除。
  二、代號比對前的 ping 收到 script.googleusercontent.com 的 HTTP 404 網頁，
      工單被標成失敗、日誌叫人去改 GitHub Secret；那是 Apps Script 前端的暫時性錯誤。
"""
import io
import unittest
from collections import Counter
from contextlib import ExitStack, redirect_stdout
from unittest.mock import Mock, patch

from test_quality import p
import test_overwrite_0913 as overwrite_tests      # 匯入模組而非類別，避免被 unittest 重複收集
import test_model_dailyk as dailyk_tests

D = overwrite_tests.D

URL = 'https://script.google.com/macros/s/AKfy-test/exec'
ECHO = 'https://script.googleusercontent.com/macros/echo?user_content_key=abc&lib=xyz'
GOOGLE_404 = "<!DOCTYPE html><html lang=\"en\"><head><script nonce=\"x\">window['ppConfig'] = {productName: 'x'}"
PING_OK = '{"ok":true,"pong":true,"build":"2026-09-13-quality-v11","features":["evidence-v2","refresh-step","dailyk-safe-chunks"],"steps":["smsmail","codes","tracker","perfhist","perf","dailyk"]}'


def resp(status, text, url=ECHO):
    return Mock(status_code=status, text=text, url=url)


class PingTests(unittest.TestCase):
    def ping(self, responses):
        sleeps, out = [], io.StringIO()
        with patch.object(p, 'APPS_SCRIPT_URL', URL), p.ping_session(), \
             patch.object(p.requests, 'get', side_effect=list(responses)) as get, \
             patch.object(p.time, 'sleep', side_effect=sleeps.append), redirect_stdout(out):
            first = p.ping_downstream()
            second = p.ping_downstream() if first[0] else None
        return first, second, get, sleeps, out.getvalue()

    def test_transient_404_is_retried_then_cached(self):
        (info, fail), second, get, sleeps, out = self.ping([resp(404, GOOGLE_404), resp(200, PING_OK, URL)])
        self.assertIsNone(fail); self.assertEqual(info['build'], '2026-09-13-quality-v11')
        self.assertEqual(sleeps, [8]); self.assertEqual(get.call_count, 2)       # 第二次呼叫沿用，不再發請求
        self.assertEqual(second[0]['build'], info['build'])
        self.assertIn('本輪已確認', out)

    def test_persistent_echo_404_is_transient_and_not_blamed_on_secret(self):
        (info, fail), _, get, sleeps, out = self.ping([resp(404, GOOGLE_404)] * 3)
        self.assertIsNone(info); self.assertTrue(fail['transient'])
        self.assertEqual(get.call_count, 3); self.assertEqual(sleeps, [8, 16])
        self.assertIn('不需要改 GitHub Secret', out)
        self.assertNotIn('showDeployInfo', out)

    def test_login_page_is_not_retried_and_not_transient(self):
        (info, fail), _, get, sleeps, out = self.ping([resp(200, '<html>accounts.google.com ServiceLogin</html>', URL)])
        self.assertFalse(fail['transient']); self.assertEqual(get.call_count, 1); self.assertEqual(sleeps, [])
        self.assertIn('誰可以存取', out)

    def test_old_deployment_without_ping_still_points_to_deploy_info(self):
        (info, fail), _, get, _, out = self.ping([resp(200, '<html><body>old doGet page</body></html>', URL)] * 3)
        self.assertFalse(fail['transient']); self.assertIn('showDeployInfo', out)

    def test_connection_error_is_transient(self):
        err = p.requests.exceptions.ConnectionError('reset')
        (info, fail), _, get, sleeps, _ = self.ping([err, err, err])
        self.assertTrue(fail['transient']); self.assertEqual(get.call_count, 3)

    def test_refresh_returns_structured_transient_failure(self):
        with patch.object(p, 'APPS_SCRIPT_URL', URL), patch.object(p, 'ADMIN_KEY', 'k'), \
             patch.object(p, 'ping_downstream', return_value=(None, {'transient': True, 'error': 'HTTP 404'})):
            result = p.maybe_refresh_site(only=['codes'], force=True, date_str=D)
        self.assertEqual(result['ok'], False); self.assertTrue(result['transient'])


class RefreshDriverTests(unittest.TestCase):
    def test_transient_step_becomes_pending_not_failure(self):
        saved = []
        calls = iter([{'ok': True}, {'ok': False, 'transient': True, 'error': '下游 ping 回 HTTP 404 網頁（暫時性）'}])
        with patch.object(p, 'load_refresh_checkpoint', return_value=None), \
             patch.object(p, 'save_refresh_checkpoint', side_effect=lambda *a, **k: saved.append(list(a[5]) if len(a) > 5 else [])), \
             patch.object(p, '_transcript_rows_of_day', return_value=None), \
             patch.object(p, 'maybe_refresh_site', side_effect=lambda **kw: next(calls)), \
             patch.object(p, 'budget_left', return_value=900):
            result = p.finish_transcript_refresh(Mock(), 'MANUAL-20260911', '2026/09/11', 'raw', ['2026/09/11'])
        self.assertTrue(result['pending']); self.assertTrue(result['transient'])
        self.assertTrue(result['note'].startswith('文章已更新；codes:2026/09/11 暫停'))
        self.assertIn('smsmail:2026/09/11', saved[-1])              # 已完成的步驟保存在檢查點
        self.assertEqual(p.waiting_status(result), '等待續跑')
        self.assertEqual(p.waiting_status({'pending': True}), '等待日K')

    def test_rename_is_reported_as_correction_not_loss(self):
        before = Counter({('操作紀錄', '信化', '觀望不碰'): 1, ('操作紀錄', '川湖', '觀望不碰'): 1})
        after = Counter({('操作紀錄', '信驊', '觀望不碰'): 1, ('操作紀錄', '川湖', '觀望不碰'): 1})
        renamed, lost_items, lost_n = p.describe_row_changes(before, after)
        self.assertEqual(renamed, ['操作紀錄觀望不碰「信化」→「信驊」'])
        self.assertEqual((lost_items, lost_n), ([], 0))

    def test_real_deletion_is_still_reported(self):
        before = Counter({('操作紀錄', '記憶體', '觀望不碰'): 1, ('操作紀錄', '川湖', '觀望不碰'): 1})
        after = Counter({('操作紀錄', '川湖', '觀望不碰'): 1})
        renamed, lost_items, lost_n = p.describe_row_changes(before, after)
        self.assertEqual(renamed, []); self.assertEqual(lost_n, 1)
        self.assertIn('記憶體', lost_items[0])

    def test_monitor_logs_rename_without_counting_loss(self):
        rows = iter([Counter({('操作紀錄', '信化', '觀望不碰'): 1}), Counter({('操作紀錄', '信驊', '觀望不碰'): 1}),
                     Counter({('操作紀錄', '信驊', '觀望不碰'): 1})])
        out = io.StringIO()
        with patch.object(p, 'load_refresh_checkpoint', return_value=None), patch.object(p, 'save_refresh_checkpoint'), \
             patch.object(p, '_transcript_rows_of_day', side_effect=lambda *a: next(rows)), \
             patch.object(p, 'maybe_refresh_site', return_value={'ok': True}), \
             patch.object(p, 'note_decision') as note, patch.object(p, 'flush_decisions'), \
             patch.object(p, '_DEFER_BACKGROUND', False), patch.object(p, 'budget_left', return_value=900), \
             redirect_stdout(out):
            result = p.finish_transcript_refresh(Mock(), 'MANUAL-20260911', '2026/09/11', 'raw', ['2026/09/11'])
        self.assertEqual(result['lost'], [])
        self.assertIn('名稱補正', out.getvalue()); self.assertNotIn('少了', out.getvalue())
        self.assertEqual(note.call_args_list[0].args[1], 'smsmail 之後名稱補正')


class JobStatusTests(unittest.TestCase):
    setup_admin = overwrite_tests.OverwriteTests.setup_admin

    def test_admin_job_waits_for_resume_instead_of_failing(self):
        with ExitStack() as st:
            progress = self.setup_admin(st, checkpoint={'affected': [D], 'completed': ['smsmail:' + D]})
            st.enter_context(patch.object(p, 'finish_transcript_refresh', return_value={
                'ok': False, 'pending': True, 'transient': True, 'note': '文章已更新；codes 暫停'}))
            p.run_admin_job(Mock())
        self.assertEqual(progress.call_args.kwargs['status'], '等待續跑')

    def test_daily_k_transient_is_normal_exit(self):
        dailyk_tests.ModelAndDailyKTests.run_daily(self, result={'ok': False, 'failed': 1, 'transient': True})

    def test_ping_reuse_is_limited_to_one_refresh_chain(self):
        # 流程外每次都問；流程內沿用一次；流程結束就清除，不留跨呼叫的全域快取。
        ok = resp(200, PING_OK, URL)
        with patch.object(p, 'APPS_SCRIPT_URL', URL), patch.object(p.requests, 'get', side_effect=[ok, ok, ok]) as get:
            p.ping_downstream(); p.ping_downstream()
            self.assertEqual(get.call_count, 2)
            with p.ping_session():
                p.ping_downstream(); p.ping_downstream()
            self.assertEqual(get.call_count, 3)
        self.assertEqual(p._PING_SESSION, {'depth': 0, 'url': None, 'info': None})


if __name__ == '__main__':
    unittest.main()
