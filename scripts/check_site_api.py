"""Read-only smoke check for the Pages-to-GAS API bridge."""

import json
import os
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
try:
    with urllib.request.urlopen(req, timeout=70) as response:
        payload = json.load(response)
        if not payload.get("ok"):
            raise SystemExit("橋接失敗：" + str(payload.get("error", "unknown")))
        overview = payload.get("result", {}).get("today", {})
        print("橋接成功；最新日期：" + str(overview.get("date", "未提供")))
except urllib.error.HTTPError as error:
    try:
        detail = json.loads(error.read().decode()).get("error", "unknown")
    except (ValueError, UnicodeDecodeError):
        detail = "non-json"
    raise SystemExit(f"橋接 HTTP {error.code}: {detail}") from None

