"""逐字稿聽打的每分鐘輸入上限（2026/11/06：五段幾乎連續送出，第 5 段 429 才換金鑰）。

用假時鐘與假模型重演那一天：每段 15 分鐘約 88,000 輸入 tokens，回應 17～60 秒。
驗：任何一把金鑰在任何 60 秒內送出的量都不超過上限；有空間的金鑰時換金鑰不等待；
只有一把金鑰時改成等待，不再撞 429。
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('GEMINI_API_KEY', 'offline')
import transcript as tx  # noqa: E402


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def make(keys, per_segment_tokens=88000, latency=(60, 17, 16, 20, 13)):
    clock = FakeClock()
    clock.slept = []
    t = tx.Transcriber.__new__(tx.Transcriber)
    t.keys = ['k%d' % i for i in range(keys)]
    t.clients = [object()] * keys
    t.models = ['gemini-3.5-flash-lite']
    t.model_index = t.key_index = 0
    t.segment_seconds = 15 * 60
    t.fps = 0
    t.probe_enabled = False
    t.exhausted, t.stream_models, t.healthy_until = set(), set(), {}
    t.models_used, t.keys_used = [], []
    t.tpm_log, t.tokens_per_sec = {}, tx.INPUT_TOKENS_PER_SEC
    sends = []
    calls = {'n': 0}

    def run(request):
        key = t.key_index
        sends.append((clock.now, key, per_segment_tokens))
        window = sum(n for (at, k, n) in sends if k == key and clock.now - at < 60)
        if window > tx.INPUT_TPM:
            raise type('E', (Exception,), {'status_code': 429})('Quota exceeded for metric: generate_content_free_tier_input_token_count, limit: 250000')
        clock.now += latency[min(calls['n'], len(latency) - 1)]
        calls['n'] += 1
        return tx._StreamResult(status='completed', output_text='逐字稿內容', input_tokens=per_segment_tokens)

    t._run = run
    t._probe = lambda key: (True, '', False)
    return t, clock, sends


class TranscriptTpm(unittest.TestCase):
    def setUp(self):
        self._patch = (tx.time.monotonic, tx.time.sleep, tx.RUN_STARTED, tx.log)
        tx.log = lambda *a, **k: None

    def tearDown(self):
        tx.time.monotonic, tx.time.sleep, tx.RUN_STARTED, tx.log = self._patch

    def run_day(self, keys):
        t, clock, sends = make(keys)
        tx.time.monotonic, tx.time.sleep = clock.monotonic, clock.sleep
        tx.RUN_STARTED = clock.now
        text = t.transcribe('https://www.youtube.com/watch?v=X', 3966)   # 1:06:06，五段
        self.assertEqual(text.count('逐字稿內容'), 5)
        for at, key, _ in sends:
            window = sum(n for (a, k, n) in sends if k == key and 0 <= at - a < 60)
            self.assertLessEqual(window, tx.INPUT_TPM, f'金鑰 #{key + 1} 一分鐘內超過上限')
        return sends, clock

    def test_three_keys_rotate_instead_of_hitting_429(self):
        sends, clock = self.run_day(3)
        self.assertEqual(len(sends), 5, '每段只送一次，沒有 429 重送')
        self.assertEqual(clock.slept, [], '有空間的金鑰時不等待')
        self.assertGreater(len({k for _, k, _ in sends}), 1, '一分鐘內接近上限時換金鑰')

    def test_single_key_waits_instead_of_hitting_429(self):
        sends, clock = self.run_day(1)
        self.assertEqual(len(sends), 5)
        self.assertTrue(clock.slept, '只有一把時改成等待')

    def test_actual_usage_replaces_the_estimate(self):
        t, clock, _ = make(1, per_segment_tokens=60000)
        tx.time.monotonic, tx.time.sleep = clock.monotonic, clock.sleep
        tx.RUN_STARTED = clock.now
        t.transcribe('https://www.youtube.com/watch?v=X', 900)
        self.assertAlmostEqual(t.tokens_per_sec, 60000 / 900, places=3)
        self.assertEqual(t._tpm_used(0), 60000)


if __name__ == '__main__':
    unittest.main()
