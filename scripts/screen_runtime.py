"""Gemini 有限次數讀取；統計每次嘗試，不記金鑰、原話或模型原始錯誤。"""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from screen_contracts import SYSTEM, prompt, schema


def request_timeout():
    """單次請求逾時秒數（15 分鐘片段實測 4–28 秒，偶爾超過 75 秒）。"""
    return float(os.environ.get('SCREEN_TIMEOUT_SECONDS', '150'))


class Reader:
    def __init__(self):
        import transcript as tx
        from google import genai
        keys = tx.gemini_keys()
        if not keys:
            raise RuntimeError('未設定 Gemini 金鑰')
        # 同專案金鑰不輪替來繞過 RPM／RPD；驗證失敗請管理者修設定。
        self.client = genai.Client(api_key=keys[0], http_options={
            'timeout': int(request_timeout() * 1000), 'retry_options': {'attempts': 1}})
        self.timeout = request_timeout()
        primary = os.environ.get('SCREEN_MODEL', '').strip() or tx.GEMINI_MODEL
        self.models = list(dict.fromkeys([primary] + list(tx.GEMINI_FALLBACK_MODELS)))
        self.used, self.attempts, self.last_send = [], 0, -1e9
        self.deadline = time.monotonic() + float(os.environ.get('SCREEN_BUDGET_SECONDS', '480'))
        self.max_calls = int(os.environ.get('SCREEN_MAX_REQUESTS', '12'))
        self.path = Path(os.environ.get('SCREEN_METRICS_PATH', 'scratch/screen-identify/metrics.jsonl'))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.thinking = os.environ.get('SCREEN_THINKING', 'low')
        self.resolution = os.environ.get('SCREEN_RESOLUTION', 'default').lower()
        self.fps = float(os.environ.get('SCREEN_FPS', '0.5'))
        self.blocked = False
        self.total_input = 0
        self.input_budget = int(os.environ.get('SCREEN_INPUT_BUDGET', '1800000'))

    def request(self, url, start, end, system, text, output_schema, fps=None):
        from google.genai import types
        if getattr(self, 'blocked', False):
            raise RuntimeError('本輪已遇專案限流，停止後續片段請求')
        fps = self.fps if fps is None else fps
        if not 0 < fps <= 2 or self.thinking not in ('low', 'minimal'):
            raise RuntimeError('不合法的 fps 或 thinking 設定')
        if self.resolution not in ('default', 'low', 'medium', 'high'):
            raise RuntimeError('不合法的解析度')
        last = '未執行'
        for n in range(2):
            wait = max(0, 5 - (time.monotonic() - self.last_send))
            if self.attempts >= self.max_calls or time.monotonic() + wait + self.timeout > self.deadline or self.total_input >= self.input_budget:
                raise RuntimeError('達到本輪時間／請求／輸入用量預算')
            time.sleep(wait)
            model = self.models[min(n, len(self.models) - 1)]
            config = dict(system_instruction=system, response_mime_type='application/json',  # Gemini 3 建議溫度維持預設，不設 0
                          max_output_tokens=int(os.environ.get('SCREEN_MAX_OUTPUT', '8192')))
            # SCREEN_SCHEMA=off：不用 response_schema（2026/11/12 實跑：3.5-flash-lite 加 schema 後四段都回空陣列），格式由 parse_report 逐筆檢查。
            if os.environ.get('SCREEN_SCHEMA', 'on').strip().lower() != 'off':
                config['response_schema'] = output_schema
            if re.search(r'gemini-3(?:\.|-)', model):
                config['thinking_config'] = types.ThinkingConfig(thinking_level=self.thinking)
            if self.resolution != 'default':
                config['media_resolution'] = 'MEDIA_RESOLUTION_' + self.resolution.upper()
            # 400 不自動移除 fps/schema；要測相容性，另開明確設定的實驗。
            cfg = types.GenerateContentConfig(**config)
            part = types.Part(file_data=types.FileData(file_uri=url),
                              video_metadata=types.VideoMetadata(start_offset=f'{start}s', end_offset=f'{end}s', fps=fps))
            ident = hashlib.sha256(json.dumps([url, start, end, model, text, config], sort_keys=True, default=str).encode()).hexdigest()
            self.attempts += 1
            self.last_send = began = time.monotonic()
            record = dict(request_hash=ident, model=model, start=start, end=end, fps=fps,
                          resolution=self.resolution, thinking=self.thinking, attempt=self.attempts)
            try:
                res = self.client.models.generate_content(model=model,
                    contents=types.Content(role='user', parts=[part, types.Part(text=text)]), config=cfg)
            except Exception as exc:
                status = getattr(exc, 'code', None) or getattr(exc, 'status_code', None)
                record.update(status='error', http_status=status if isinstance(status, int) else None,
                              error_type=type(exc).__name__, input_tokens=None, output_tokens=None)
                last = f'{type(exc).__name__} HTTP {status if isinstance(status, int) else "unknown"}'
                self._record(record, began)
                # 不把錯誤全文印到公開 Actions log。
                if status == 429:
                    self.blocked = True
                    # 可能是專案 RPM/TPM/RPD，停止本輪，不能換三把同專案金鑰重送。
                    raise RuntimeError('專案限流／額度不足；本輪停止，排程稍後再處理') from None
                # 2026/11/12 實跑：45–60 分那段讀到 75 秒逾時（沒有 HTTP 狀態）就整段沒了；逾時、網路錯與 5xx 都換備援模型重試一次。
                if n == 0 and (not isinstance(status, int) or status in (500, 502, 503, 504)):
                    continue
                raise RuntimeError(last) from None
            usage = getattr(res, 'usage_metadata', None)
            get = lambda k: getattr(usage, k, None) if usage else None
            record.update(status='received', input_tokens=get('prompt_token_count'),
                          output_tokens=get('candidates_token_count'), thinking_tokens=get('thoughts_token_count'),
                          cached_tokens=get('cached_content_token_count'), total_tokens=get('total_token_count'))
            self.total_input += record['input_tokens'] or 0
            candidates = getattr(res, 'candidates', None) or []
            finish = str(getattr(candidates[0], 'finish_reason', '')) if candidates else ''
            record['finish_reason'] = finish
            self._record(record, began)
            if finish not in ('STOP', 'FinishReason.STOP') or not res.text:
                raise RuntimeError('模型輸出未完整結束；該段不可當成功空結果')
            self.used.append(model)
            return res.text
        raise RuntimeError(last)

    def _record(self, data, began):
        data['latency_seconds'] = round(time.monotonic() - began, 3)
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def read(self, url, start, end, learned=()):
        return self.request(url, start, end, SYSTEM, prompt(start, end, learned), schema(start, end))
