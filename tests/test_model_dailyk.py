import json
import copy
from contextlib import ExitStack
from unittest.mock import Mock, patch
import unittest
from test_quality import p, ROOT


class ModelAndDailyKTests(unittest.TestCase):
    def test_configurable_thinking(self):
        for level in ('low', 'medium', 'high'):
            with patch.dict(p.os.environ, {'GEMINI_THINKING_LEVEL': level}):
                self.assertEqual(p.gemini_generation_config('gemini-3.5-flash-lite')['thinkingConfig']['thinkingLevel'], level)
        with patch.dict(p.os.environ, {'GEMINI_THINKING_LEVEL': 'invalid'}):
            with self.assertRaises(ValueError): p.gemini_generation_config('gemini-3.5-flash-lite')

    def test_model_specific_generation_config(self):
        cfg = p.gemini_generation_config('gemini-3.5-flash-lite', 16000, 2048, True)
        self.assertEqual(cfg['thinkingConfig'], {'thinkingLevel': 'medium'})
        self.assertNotIn('temperature', cfg)
        self.assertEqual(cfg['responseMimeType'], 'application/json')
        self.assertEqual(p.gemini_generation_config('gemini-2.5-flash', 16000, 2048)['thinkingConfig'], {'thinkingBudget': 2048})
        self.assertNotIn('thinkingConfig', p.gemini_generation_config('gemini-2.0-flash'))

    def test_key_model_override_and_inheritance(self):
        with patch.object(p, 'GEMINI_MODEL', 'gemini-3.5-flash-lite'), patch.dict(p.os.environ, {'GEMINI_MODEL_3': ''}):
            self.assertEqual(p._model_for_source('GEMINI_API_KEY_3'), 'gemini-3.5-flash-lite')
            with patch.dict(p.os.environ, {'GEMINI_MODEL_3': 'gemini-3.1-flash-lite'}):
                self.assertEqual(p._model_for_source('GEMINI_API_KEY_3'), 'gemini-3.1-flash-lite')

    def test_smoke_uses_production_model_config(self):
        with patch.object(p.requests, 'post', return_value=Mock(status_code=200)) as post:
            p.smoke_generate('offline-key', 'gemini-3.5-flash-lite')
        self.assertEqual(post.call_args.kwargs['json']['generationConfig'], p.gemini_generation_config('gemini-3.5-flash-lite'))

    def test_retry_rebuilds_config_for_current_model(self):
        sent = []
        def post(*args, **kwargs):
            sent.append(copy.deepcopy(kwargs['json']['generationConfig']))
            if len(sent) == 1:
                raise p.requests.ConnectionError('offline retry')
            return Mock(status_code=200, json=lambda: {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'OK'}]}}]})
        with patch.object(p, 'require_gemini_key'), patch.object(p, 'GEMINI_KEYS', []), patch.object(p, '_QUOTA_STOP', {'daily': False}), patch.object(p, 'current_gemini_model', side_effect=['gemini-2.5-flash', 'gemini-2.5-flash', 'gemini-3.5-flash-lite']), patch.object(p, 'current_gemini_key', return_value='offline'), patch.object(p, 'throttle_gemini'), patch.object(p, 'budget_left', return_value=1000), patch.object(p.time, 'sleep'), patch.object(p.requests, 'post', side_effect=post):
            self.assertEqual(p.call_gemini('system', 'input', want_json=True), 'OK')
        self.assertIn('thinkingBudget', sent[0]['thinkingConfig'])
        self.assertEqual(sent[1]['thinkingConfig'], {'thinkingLevel': 'medium'})
        self.assertNotIn('temperature', sent[1])

    def run_daily(self, preflight=False, result=None):
        with ExitStack() as stack:
            flags = ('CHECK_KEYS', 'ADMIN_JOB', 'SMS_PRIORITY', 'FULL_FIX', 'PARSE_SMS', 'REPAIR_CODES', 'RECLASSIFY', 'FIX_PRICES', 'RECONCILE', 'FILL_BLANKS', 'BACKFILL', 'REFRESH_SITE')
            for flag in flags: stack.enter_context(patch.object(p, flag, False))
            stack.enter_context(patch.object(p, 'DAILYK_ONLY', True))
            stack.enter_context(patch.object(p, 'PREFLIGHT', preflight))
            stack.enter_context(patch.object(p, 'open_sheets', return_value=Mock()))
            stack.enter_context(patch.object(p, 'write_status_log'))
            probe = stack.enter_context(patch.object(p, 'write_preflight'))
            for method in ('require_gemini_key', 'preflight_gemini_keys', 'call_gemini'):
                stack.enter_context(patch.object(p, method, side_effect=AssertionError('daily K cannot call AI')))
            refresh = stack.enter_context(patch.object(p, 'maybe_refresh_site', return_value=result))
            p.main()
            return probe, refresh

    def test_daily_k_probe_bypasses_video_and_ai(self):
        probe, refresh = self.run_daily(True)
        self.assertEqual(probe.call_args.args[0], 'true'); refresh.assert_not_called()

    def test_daily_k_main_runs_only_daily_k_without_ai(self):
        _, refresh = self.run_daily(result={'ok': True})
        refresh.assert_called_once_with(only=['dailyk'], force=True)

    def test_daily_k_partial_is_normal_exit(self):
        self.run_daily(result={'ok': False, 'partial': True})

    def test_daily_k_real_error_is_not_reported_as_success(self):
        with self.assertRaisesRegex(RuntimeError, '補日K失敗'):
            self.run_daily(result={'ok': False, 'failed': 1})

    def refresh_batches(self, responses, budget):
        ping = Mock(text=json.dumps({'features': ['refresh-step', 'evidence-v2', 'dailyk-safe-chunks'], 'steps': ['dailyk']}))
        with patch.object(p, 'DAILYK_ONLY', True), patch.object(p, 'APPS_SCRIPT_URL', 'https://example.invalid'), patch.object(p, 'ADMIN_KEY', 'offline'), patch.object(p, 'budget_left', side_effect=budget), patch.object(p.time, 'sleep'), patch.object(p.requests, 'get', side_effect=[ping] + [Mock(text=json.dumps(r)) for r in responses]) as get:
            result = p.maybe_refresh_site(only=['dailyk'], force=True)
            return result, get.call_count

    def test_budget_empty_does_not_start_batch(self):
        result, calls = self.refresh_batches([], [30])
        self.assertTrue(result['partial']); self.assertEqual(calls, 1)

    def test_budget_low_after_batch_preserves_progress(self):
        result, calls = self.refresh_batches([{'ok': True, 'chunked': True, 'done': False, 'processed': 8, 'total': 221}], [200, 200, 110])
        self.assertTrue(result['partial']); self.assertEqual(calls, 2)

    def test_budget_available_continues_until_complete(self):
        result, calls = self.refresh_batches([{'ok': True, 'chunked': True, 'done': False}, {'ok': True, 'done': True}], [1000] * 5)
        self.assertTrue(result['ok']); self.assertEqual(calls, 3)

    def test_final_batch_complete_even_when_budget_low(self):
        result, calls = self.refresh_batches([{'ok': True, 'done': True}], [121, 121])
        self.assertTrue(result['ok']); self.assertEqual(calls, 2)

    def test_workflow_keeps_independent_queue_and_json_variables(self):
        workflow = (ROOT / '.github/workflows/daily.yml').read_text(encoding='utf-8')
        self.assertIn("- cron: '45 5 * * 1-5'", workflow)
        self.assertIn("'daily-dailyk' || 'daily-pipeline'", workflow)
        self.assertEqual(workflow.count("DAILYK_ONLY: ${{ github.event.schedule == '45 5 * * 1-5'"), 2)
        self.assertIn('GEMINI_ASSESSMENT_TOKEN_BUDGET: ${{ vars.', workflow)


if __name__ == '__main__': unittest.main()
