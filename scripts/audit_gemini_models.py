#!/usr/bin/env python3
"""Monthly, read the official Gemini model list and probe Flash models per configured key.

The JSON report contains model IDs and key slot numbers only, never API keys.
Temporary quota/server failures are labelled uncertain, not retired.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

BASE = 'https://generativelanguage.googleapis.com/v1beta/models'
OUT = Path(os.environ.get('MODEL_AUDIT_OUTPUT', 'data/gemini-models.json'))


def candidates(models):
    rows = []
    for model in models:
        name = str(model.get('name', '')).removeprefix('models/')
        if ('flash' in name.lower() and
                not re.search(r'pro|image|audio|tts|live|computer|robot|embedding|experimental', name, re.I) and
                'generateContent' in model.get('supportedGenerationMethods', [])):
            rows.append(name)
    default = os.environ.get('GEMINI_MODEL', '').strip() or 'gemini-3.5-flash-lite'
    def rank(name):
        match = re.search(r'gemini-(\d+)(?:\.(\d+))?', name)
        version = (int(match[1]), int(match[2] or 0)) if match else (0, 0)
        return (name == default, *version, name)
    return sorted(set(rows), key=rank, reverse=True)[:12]


def list_models(key):
    models, token, calls = [], '', 0
    while True:
        params = {'key': key, 'pageSize': 100}
        if token:
            params['pageToken'] = token
        try:
            reply = requests.get(BASE, params=params, timeout=20)
            reply.raise_for_status()
        except requests.RequestException as error:
            # requests 的錯誤字串可能含完整 query URL，絕不能把 key 印進 Actions。
            status = getattr(getattr(error, 'response', None), 'status_code', None)
            raise RuntimeError(f'models.list 失敗：HTTP {status}' if status else
                               f'models.list 連線失敗：{type(error).__name__}') from None
        data = reply.json()
        models.extend(data.get('models', []))
        calls += 1
        token = data.get('nextPageToken', '')
        if not token:
            return models, calls
        if calls >= 5:
            raise RuntimeError('模型清單超過五頁；拒絕以不完整清單汰換舊模型')


def probe(model, key):
    try:
        reply = requests.post(
            f'{BASE}/{model}:generateContent', params={'key': key}, timeout=20,
            json={'contents': [{'role': 'user', 'parts': [{'text': '只回覆 OK。'}]}],
                  'generationConfig': {'maxOutputTokens': 32, 'temperature': 0.1}})
    except requests.RequestException:
        return 'uncertain-network'
    if reply.status_code == 200:
        return 'usable'
    if reply.status_code in (429, 500, 502, 503, 504):
        return f'uncertain-{reply.status_code}'
    return f'unavailable-{reply.status_code}'


def main():
    keys = [os.environ.get('GEMINI_API_KEY', ''),
            os.environ.get('GEMINI_API_KEY_2', ''),
            os.environ.get('GEMINI_API_KEY_3', '')]
    keys = [key.strip() for key in keys if key.strip()]
    if not keys:
        raise RuntimeError('沒有 Gemini API 金鑰，月度模型盤點無法進行')
    models, list_calls = list_models(keys[0])
    names = candidates(models)
    if not names:
        raise RuntimeError('官方清單沒有 generateContent Flash 模型；拒絕發布空清單')
    availability = {}
    for slot, key in enumerate(keys, 1):
        availability[str(slot)] = {}
        for name in names:
            availability[str(slot)][name] = probe(name, key)
            time.sleep(6.1)  # 免費層以每把金鑰不高於每分鐘十次為目標
    usable = [name for name in names if any(
        availability[str(slot)].get(name) == 'usable' for slot in range(1, len(keys) + 1))]
    uncertain = [name for name in names if name not in usable and any(
        availability[str(slot)].get(name, '').startswith('uncertain') for slot in range(1, len(keys) + 1))]
    checked_at = datetime.now(ZoneInfo('Asia/Taipei'))
    report = {'checked_month': checked_at.strftime('%Y-%m'),
              'checked_at_taipei': checked_at.isoformat(timespec='seconds'),
              'official_models': len(models), 'list_calls': list_calls,
              'probe_calls': len(names) * len(keys), 'key_slots': len(keys),
              'usable_models': usable, 'uncertain_models': uncertain,
              'availability_by_key_slot': availability}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"官方 {len(models)} 款，查表 {list_calls} 次、短回覆 {report['probe_calls']} 次；可用：{', '.join(usable)}")
    current = os.environ.get('GEMINI_MODEL', '').strip() or 'gemini-3.5-flash-lite'
    if current not in usable:
        print(f'::warning::目前 GEMINI_MODEL={current} 未通過本月查驗；請檢查報告與 GitHub Variables。')
    if uncertain:
        print('暫時無法判定（不視為過期）：' + ', '.join(uncertain))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'月度模型盤點失敗，保留既有設定：{error}', file=sys.stderr)
        raise SystemExit(1)
