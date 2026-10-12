"""A spoken stock alias must not split the verb 持有 from another stock."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
sys.path.insert(0, str(ROOT / "pipeline"))
import pipeline as pl  # noqa: E402

darwin = {"name": "達邁", "code": "3645"}
assert pl.public_narrative("已持有達邁者續抱即可。", darwin) == "已持有達邁者續抱即可。"
assert pl.public_narrative("已持友達邁者續抱即可。", darwin) == "已持有達邁者續抱即可。"
assert "友達" in pl.public_narrative("你叫我們買有達？不是。", {"name": "友達", "code": "2409"})
signals = {"holdings": [{"name": "祥碩", "code": "5269"}],
           "watch_watch": [{"name": "祥碩", "code": "5269"}],
           "watch_avoid": [{"name": "友達", "code": "2409"}]}
pl.finalize_name_and_owned_watch(signals, "我持有祥碩。你有達美輪是膝蓋。", "2026/10/12")
assert len(signals["holdings"]) == 1
assert not signals["watch_watch"] and not signals["watch_avoid"]
named = {"holdings": [], "watch_watch": [], "watch_avoid": [{"name": "友達", "code": "2409"}]}
pl.finalize_name_and_owned_watch(named, "我不買友達。", "2026/10/02")
assert len(named["watch_avoid"]) == 1
print("Python public narrative: alias boundary and stored text repaired")
