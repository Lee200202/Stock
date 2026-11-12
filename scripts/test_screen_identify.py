"""2026/11/12：直播畫面辨識的純函式（不連 Gemini、不連試算表）。"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'daily_review'))
os.environ.setdefault('GEMINI_API_KEY', 'offline')
from screen_identify import dedupe, parse_items, prompt  # noqa: E402
from screen_refs import day_rows  # noqa: E402
from common import LOG_TABS  # noqa: E402

reply = '''```json
[{"time":"0:17:05","said":"你看這一檔，底部爆量","screen_name":"勤誠","screen_code":"8210","confidence":"清楚","note":"K線圖標題"},
 {"time":"2:10","said":"這檔先不要","screen_name":"","screen_code":"","confidence":"清楚","note":"只有講者"},
 {"time":"0:17:20","said":"這一檔","screen_name":"勤誠","screen_code":"8210","confidence":"模糊","note":""},
 {"time":"1:40:00","said":"超出範圍","screen_name":"x","screen_code":"1234","confidence":"清楚","note":""},
 {"time":"0:20:00","said":"代號亂寫","screen_name":"宜鼎","screen_code":"5289-KY","confidence":"怪","note":""}]
```'''
items = parse_items(reply, 900, 1800)
assert [i['time'] for i in items] == ['0:17:05', '0:17:10', '0:17:20', '0:20:00'], items   # 2:10 是片段內時間→0:17:10；超出範圍的丟掉
assert items[1]['confidence'] == '模糊'                       # 清楚卻沒有名稱代號 → 模糊
assert items[3]['code'] == '5289' and items[3]['confidence'] == '模糊'
kept = dedupe(items)
assert [(i['time'], i['confidence']) for i in kept] == [('0:17:05', '清楚'), ('0:17:10', '模糊'), ('0:20:00', '模糊')], kept   # 30 秒內同一檔只留一筆
assert parse_items('不是 JSON', 0, 900) is None and parse_items('[]', 0, 900) == []
assert '行程' in prompt(0, 900) and '0:15:00' in prompt(0, 900)
rows = [{'日期': '2026/11/12', '時間': '0:17:05'}, {'日期': '2026-11-11', '時間': '0:01:00'}, {'日期': '2026/11/12', '時間': ''}]
assert [r['_row'] for r in day_rows(rows, '2026/11/12')] == [2, 4]
assert LOG_TABS['畫面辨識'][:4] == ['建立時間', '日期', '影片ID', '時間']
print('11/12 畫面辨識：時間換算、範圍、把握、去重、日期篩選通過')
