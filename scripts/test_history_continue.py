"""雲端續補的停止、保留與派工邊界。"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import base64
import json

spec = importlib.util.spec_from_file_location('continuation', Path(__file__).with_name('history_continue.py'))
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def progress(phase='paused', saved=1, failed=0):
    return {'saved': saved, 'markets': {m: {'phase': phase, 'remaining': 10 if phase == 'paused' else 0, 'failed': failed} for m in ('上市', '上櫃')}}


def run(status='completed', conclusion='success', id=1):
    return dict(id=id, status=status, conclusion=conclusion, head_branch='main')


class ContinuationTests(unittest.TestCase):
    def test_scope_change_rebuilds_once_even_when_old_version_scanned(self):
        calls = []
        def api(path, body=None):
            calls.append((path, body)); return {'workflow_runs': [run()]}
        with patch.object(c,'selection_update_due',return_value=['3182']):
            self.assertEqual(c.control(api,lambda _:progress('scanned'))['action'],'dispatch')
        self.assertEqual(len([body for _,body in calls if body]),1)
        with patch.object(c,'selection_update_due',return_value=[]):
            self.assertEqual(c.control(api,lambda _:progress('scanned'))['action'],'wait')

    def test_scope_state_checks_only_explicit_human_exclusions(self):
        for requested, expected in [(['3182','8299'],['3182']),(['8299'],[])]:
            record={'content':base64.b64encode(json.dumps({'requestedCodes':requested}).encode()).decode()}
            self.assertEqual(c.selection_update_due(lambda _:record),expected)

    def test_budget_pause_continues(self):
        self.assertTrue(c.decision(run(), progress())[0])

    def test_not_complete_flag_loop(self):
        p = progress('scanned'); p['coverageVerified'] = False
        self.assertFalse(c.decision(run(), p)[0])

    def test_cancel_or_failure_does_not_resurrect(self):
        for conclusion in ('cancelled', 'failure', 'timed_out'):
            self.assertFalse(c.decision(run(conclusion=conclusion), progress())[0])

    def test_two_stalled_runs_stop(self):
        self.assertFalse(c.decision(run(), progress(saved=0), progress(saved=0))[0])
        self.assertTrue(c.decision(run(), progress(saved=0), progress(saved=1))[0])

    def test_missing_progress_stays_review(self):
        self.assertFalse(c.decision(run(), {'markets': {}})[0])

    def test_scanned_source_failure_waits(self):
        self.assertFalse(c.decision(run(), progress('scanned', failed=1))[0])

    def test_pending_does_not_download_or_dispatch(self):
        for status in ('queued', 'in_progress', 'pending', 'waiting', 'requested'):
            calls = []
            def api(path, body=None):
                calls.append((path, body)); return {'workflow_runs': [run(status)]}
            self.assertEqual(c.control(api, lambda _: self.fail('不可讀尚未完成日誌'))['action'], 'keep')
            self.assertEqual(len(calls), 1)

    def test_dispatch_once_after_second_active_check(self):
        calls = []
        def api(path, body=None):
            calls.append((path, body)); return {'workflow_runs': [run()]}
        self.assertEqual(c.control(api, lambda _: progress())['action'], 'dispatch')
        self.assertEqual(len([body for _, body in calls if body]), 1)
        self.assertEqual(calls[-1][1]['inputs']['years'], '0')

    def test_racing_new_run_kept(self):
        calls = []
        def api(path, body=None):
            calls.append(path); return {'workflow_runs': [run() if len(calls) == 1 else run('queued', id=2)]}
        self.assertEqual(c.control(api, lambda _: progress())['action'], 'keep')
        self.assertEqual(len(calls), 2)

    def test_current_version_console_is_compatible(self):
        text = '\n'.join(f'{m}探查 [████] 50.0% 5/10（尚餘 5）｜新增封存 2｜複核/更新 1｜沿用 2｜空回應待核 0｜失敗 0｜休市略過 0｜8299／2004-12｜耗時 90 秒｜本輪預算到，待續補' for m in ('上市', '上櫃'))
        p = c.parse_progress(text)
        self.assertEqual(p['saved'], 6)
        self.assertTrue(c.decision(run(), p)[0])


if __name__ == '__main__':
    unittest.main()
