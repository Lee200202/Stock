"""Read-only smoke check for the Pages-to-GAS API bridge."""

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.getenv("SITE_API_URL", "https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api")
req = urllib.request.Request(
    BASE,
    data=json.dumps({"method": "apiGetDashboard", "args": []}).encode(),
    headers={
        "Content-Type": "application/json",
        "Origin": "https://lee200202.github.io",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    },
    method="POST",
)

max_attempts = 4
delays = [3, 5, 8]

for attempt in range(1, max_attempts + 1):
    try:
        with urllib.request.urlopen(req, timeout=70) as response:
            payload = json.load(response)
            if not payload.get("ok"):
                raise ValueError("橋接失敗：" + str(payload.get("error", "unknown")))
            overview = payload.get("result", {}).get("today", {})
            print("橋接成功；最新日期：" + str(overview.get("date", "未提供")))
            break
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as error:
        detail = "unknown"
        if isinstance(error, urllib.error.HTTPError):
            try:
                detail = json.loads(error.read().decode()).get("error", "unknown")
            except (ValueError, UnicodeDecodeError):
                detail = "non-json"
            err_msg = f"HTTP {error.code}: {detail}"
        else:
            err_msg = str(error)

        if attempt < max_attempts:
            delay = delays[attempt - 1]
            print(f"橋接探測第 {attempt} 次暫時性未就緒（{err_msg}），等待 {delay} 秒後重試（處理 GAS 冷啟動）...")
            time.sleep(delay)
        else:
            raise SystemExit(f"橋接重試 {max_attempts} 次後仍失敗：{err_msg}") from None

