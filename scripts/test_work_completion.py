"""Completion is durable per day, never inferred from exit 0, and failed screen batches retry."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'scripts'), str(ROOT/'scripts/daily_review')]
spec = importlib.util.spec_from_file_location('completion_test', ROOT/'pipeline/work_completion.py')
c = importlib.util.module_from_spec(spec); spec.loader.exec_module(c)
import screen_identify as screen


class Completion(unittest.TestCase):
    def setUp(self):
        self.folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, WORK_COMPLETION_DIR=self.folder,
            WORK_COMPLETION_KIND='transcript', GITHUB_REPOSITORY='owner/repo'))

    def test_done_only_blocks_matching_day_and_kind(self):
        c.record_completion('transcript', '2026/11/25', 'PUBLIC_VIDEO')
        self.assertTrue(c.read_completion('transcript','2026-11-25'))
        self.assertIsNone(c.read_completion('transcript','2026/11/26'))
        self.assertIsNone(c.read_completion('daily','2026/11/25'))

    def test_no_opt_in_no_receipt_and_green_without_source_cannot_close_day(self):
        c.record_completion('daily','2026/11/25','PUBLIC_VIDEO')
        self.assertFalse(c.receipt_path('daily').exists())
        with self.assertRaises(ValueError): c.record_completion('transcript','2026/11/25')
        self.assertIsNone(c.read_completion('transcript','2026/11/25'))

    def test_confirmed_no_video_closes_day_but_does_not_dispatch(self):
        c.record_completion('transcript','2026/11/25',status='no_video')
        with patch.object(c.subprocess,'run') as run:
            c.dispatch_screen('transcript','2026/11/25')
        run.assert_not_called()

    def test_finished_screen_or_active_run_is_not_dispatched_again(self):
        c.record_completion('transcript','2026/11/25','PUBLIC_VIDEO')
        with patch.dict(os.environ,WORK_COMPLETION_KIND='screen'):
            c.record_completion('screen','2026/11/25','PUBLIC_VIDEO')
        with patch.object(c.subprocess,'run') as run:
            c.dispatch_screen('transcript','2026/11/25')
        run.assert_not_called()
        c.receipt_path('screen').unlink()
        with patch.object(c.subprocess,'run',return_value=SimpleNamespace(stdout=json.dumps({'workflow_runs':[{'status':'in_progress'}]}).encode())) as run:
            c.dispatch_screen('transcript','2026/11/25')
        self.assertEqual(run.call_count,1)

    def test_ready_new_day_dispatches_without_secrets_in_body(self):
        c.record_completion('transcript','2026/11/26','PUBLIC_VIDEO')
        with patch.object(c.subprocess,'run',side_effect=[SimpleNamespace(stdout=b'{"workflow_runs":[]}'),SimpleNamespace()]) as run:
            c.dispatch_screen('transcript','2026/11/26')
        body=json.loads(run.call_args.kwargs['input'])
        self.assertEqual(body,{'ref':'main','inputs':{'date':'2026/11/26','source':'PUBLIC_VIDEO','automatic':'true','attempt':'1'}})

    def test_failure_waits_then_retries_without_restarting_completed_work(self):
        with patch.object(c.time,'sleep') as sleep,patch.object(c,'screen_runs',return_value=[]), \
             patch.object(c.subprocess,'run') as run:
            c.retry_screen('2026/11/25','PUBLIC_VIDEO',1)
        sleep.assert_called_once_with(60)
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['inputs']['attempt'],'2')
        with patch.object(c.time,'sleep') as sleep,patch.object(c.subprocess,'run') as run:
            c.retry_screen('2026/11/25','PUBLIC_VIDEO',3)
        sleep.assert_not_called();run.assert_not_called()

    def test_retry_does_not_replace_other_pending_work_or_revive_after_limit(self):
        with patch.object(c.time,'sleep'),patch.object(c,'screen_runs',return_value=[{'id':9,'status':'queued'}]), \
             patch.object(c.subprocess,'run') as run:
            c.retry_screen('2026/11/25','PUBLIC_VIDEO',1)
        run.assert_not_called()
        c.record_completion('transcript','2026/11/25','PUBLIC_VIDEO')
        finished={'status':'completed','conclusion':'failure','display_title':c.screen_title('2026/11/25','PUBLIC_VIDEO',3)}
        with patch.object(c,'screen_runs',return_value=[finished]),patch.object(c.subprocess,'run') as run:
            c.dispatch_screen('transcript','2026/11/25')
        run.assert_not_called()

    def test_corrupt_receipt_fails_open_for_recheck_not_finished(self):
        for bad in ('{bad','[]','null','{"version":1,"source":123}'):
            c.receipt_path('transcript').write_text(bad,encoding='utf-8')
            self.assertIsNone(c.read_completion('transcript','2026/11/25'))

    def test_different_video_same_day_does_not_reuse_screen_completion(self):
        with patch.dict(os.environ,WORK_COMPLETION_KIND='screen'):
            c.record_completion('screen','2026/11/25','FIRST_VIDEO')
        self.assertIsNone(c.read_completion('screen','2026/11/25','SECOND_VIDEO'))
        self.assertTrue(c.read_completion('screen','2026/11/25','FIRST_VIDEO'))


class ScreenCompletion(unittest.TestCase):
    def row(self,state='待核對',stamp='2026/11/25 11:30:00'):
        return {'日期':'2026/11/25','影片ID':'PUBLIC_VIDEO','建立時間':stamp,'狀態':state}

    def test_completed_or_reviewed_is_finished_but_failure_is_not(self):
        for status in ('待核對','無','已上網站','網站已有','待你決定','不需處理'):
            self.assertTrue(screen.completed_batch([self.row(status)],'2026/11/25'))
        for status in ('失敗','部分失敗',''):
            self.assertIsNone(screen.completed_batch([self.row(status)],'2026/11/25'))

    def test_latest_batch_failure_retries_and_later_success_finishes(self):
        old=self.row();failed=self.row('部分失敗','2026/11/25 11:40:00')
        self.assertIsNone(screen.completed_batch([old,failed],'2026/11/25'))
        later=self.row('無','2026/11/25 11:50:00')
        self.assertTrue(screen.completed_batch([old,failed,later],'2026/11/25'))
        self.assertIsNone(screen.completed_batch([later],'2026/11/26'))
        self.assertIsNone(screen.completed_batch([later],'2026/11/25','NEW_VIDEO'))

    def test_check_only_never_reads_video_or_calls_model(self):
        import common
        with patch.object(sys,'argv',['screen_identify.py','--date','2026/11/25','--check-only']), \
             patch.object(common,'open_log') as log,patch.object(common,'rows_of',return_value=([],[])), \
             patch.object(screen,'Reader') as reader:
            self.assertEqual(screen.main(),0)
        reader.assert_not_called()
        log.return_value.worksheet.return_value.append_rows.assert_not_called()

    def test_readonly_log_client_does_not_create_missing_tabs(self):
        import common
        with patch.dict(os.environ,REVIEW_LOG_ID='offline',GOOGLE_SHEETS_SERVICE_ACCOUNT='{}'), \
             patch('gspread.authorize') as auth, \
             patch('google.oauth2.service_account.Credentials.from_service_account_info') as credentials:
            book=common.open_log(ensure_tabs=False)
        credentials.assert_called_once_with({},scopes=['https://www.googleapis.com/auth/spreadsheets.readonly'])
        book.worksheets.assert_not_called()
        book.add_worksheet.assert_not_called()


if __name__ == '__main__': unittest.main()
