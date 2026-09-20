"""Gemini 503 直接換鑰匙（2026/09/13）。

實際日誌：polish 2/4 在同一把金鑰上連回三次 503，退避 12、30、75 秒後才成功。
503 是服務端暫時過載，換一把立刻重試；每一把都過載時才退避。
"""
import threading
import unittest
from unittest.mock import patch, Mock

from test_quality import p

OK = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'OK'}]}}],
      'usageMetadata': {}}


def resp(status):
    if status == 200:
        return Mock(status_code=200, text='', headers={}, json=lambda: OK)
    return Mock(status_code=status, text='{"error":{"status":"UNAVAILABLE"}}', headers={})


class KeySwitchTests(unittest.TestCase):
    def run_call(self, statuses, keys=('k1', 'k2', 'k3')):
        sent, sleeps = [], []
        seq = list(statuses)

        def post(url, params=None, json=None, timeout=None):
            sent.append((params['key'], url))
            return resp(seq.pop(0))

        entries = [(f'GEMINI_API_KEY_{i + 1}' if i else 'GEMINI_API_KEY', k, 'gemini-3.5-flash-lite')
                   for i, k in enumerate(keys)]
        state = {'idx': 0, 'dead': {}}
        with patch.object(p, 'GEMINI_KEY_ENTRIES', entries), \
             patch.object(p, 'GEMINI_KEYS', list(keys)), \
             patch.object(p, 'GEMINI_MODELS', ['gemini-3.5-flash-lite'] * len(keys)), \
             patch.object(p, '_KEY_STATE', state), \
             patch.object(p, '_QUOTA_STOP', {'daily': False}), \
             patch.object(p, 'throttle_gemini'), \
             patch.object(p, 'budget_left', return_value=100000), \
             patch.object(p.time, 'sleep', side_effect=sleeps.append), \
             patch.object(p.random, 'uniform', return_value=0), \
             patch.object(p.requests, 'post', side_effect=post):
            try:
                result = p.call_gemini('system', 'input', tag='polish 2/4')
            except RuntimeError as e:
                result = e
        return result, sent, sleeps, state

    def test_first_503_switches_key_without_waiting(self):
        result, sent, sleeps, state = self.run_call([503, 200])
        self.assertEqual(result, 'OK')
        self.assertEqual([k for k, _ in sent], ['k1', 'k2'])
        self.assertEqual(sleeps, [])                  # 不退避，直接換
        self.assertEqual(state['dead'], {})           # 503 不代表金鑰壞掉
        self.assertEqual(state['idx'], 1)             # 之後的呼叫沿用新的那一把

    def test_waits_only_after_every_key_returned_503(self):
        result, sent, sleeps, state = self.run_call([503, 503, 503, 200])
        self.assertEqual(result, 'OK')
        self.assertEqual([k for k, _ in sent], ['k1', 'k2', 'k3', 'k3'])
        self.assertEqual(sleeps, [12])                # 三把都過載才等第一格
        self.assertEqual(state['dead'], {})

    def test_switches_do_not_consume_backoff_slots(self):
        # 服務持續過載：每一格退避前都把三把試過一輪，等待時間與原本相同。
        result, sent, sleeps, state = self.run_call([503] * 18)
        self.assertIsInstance(result, RuntimeError)
        self.assertEqual(sleeps, [12, 30, 75, 150, 240])
        self.assertEqual(len(sent), 18)               # 6 格退避 × 每格三把各一次，上限就是這個數
        self.assertEqual(state['dead'], {})

    def test_single_key_keeps_existing_backoff(self):
        result, sent, sleeps, _ = self.run_call([503, 200], keys=('k1',))
        self.assertEqual(result, 'OK')
        self.assertEqual([k for k, _ in sent], ['k1', 'k1'])
        self.assertEqual(sleeps, [12])

    def test_other_transient_codes_unchanged(self):
        result, sent, sleeps, state = self.run_call([500, 200])
        self.assertEqual([k for k, _ in sent], ['k1', 'k1'])
        self.assertEqual(sleeps, [12])
        self.assertEqual(state['idx'], 0)

    def test_dead_key_is_never_switched_to(self):
        state = {'idx': 0, 'dead': {1: 'quota'}}
        with patch.object(p, 'GEMINI_KEYS', ['k1', 'k2', 'k3']), patch.object(p, '_KEY_STATE', state):
            self.assertTrue(p.switch_gemini_key_transient('t', 0, {0}, 503))
        self.assertEqual(state['idx'], 2)


class ConcurrentRotationTests(unittest.TestCase):
    def test_quota_on_old_key_does_not_kill_key_another_segment_switched_to(self):
        # 另一段已因 503 換到第 2 把；這一段先前用第 1 把送出的請求才回每日額度用完。
        state = {'idx': 1, 'dead': {}}
        with patch.object(p, 'GEMINI_KEYS', ['k1', 'k2', 'k3']), patch.object(p, '_KEY_STATE', state):
            self.assertTrue(p.rotate_gemini_key('polish 1/4', used=0))
        self.assertEqual(state['dead'], {0: 'quota'})
        self.assertEqual(state['idx'], 1)

    def test_parallel_503_on_same_key_switches_once(self):
        state = {'idx': 0, 'dead': {}}
        results, barrier = [], threading.Barrier(3)

        def worker():
            barrier.wait()
            results.append(p.switch_gemini_key_transient('polish', 0, {0}, 503))
        with patch.object(p, 'GEMINI_KEYS', ['k1', 'k2', 'k3']), patch.object(p, '_KEY_STATE', state):
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join()
        self.assertEqual(results, [True, True, True])
        self.assertEqual(state['idx'], 1)             # 三段同時收到 503，只往前換一把，不連跳


if __name__ == '__main__':
    unittest.main()
