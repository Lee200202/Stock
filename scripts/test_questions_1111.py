"""2026/11/11：管理者問題的選項與整列（純函式，不連試算表）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / 'daily_review'))
import os
os.environ.setdefault('GEMINI_API_KEY', 'offline')
from questions import clean_options, new_row  # noqa: E402
from common import LOG_TABS  # noqa: E402

head = LOG_TABS['管理者問題']
assert clean_options('["行程＝勤誠 8210", "行程＝其他股票", "行程＝勤誠 8210"]') == ['行程＝勤誠 8210', '行程＝其他股票']
for bad in ('["只有一個"]', '["a","b","c","d","e"]', '{"a":1}', '["' + '長' * 41 + '","b"]'):
    try:
        clean_options(bad)
    except (ValueError, Exception):
        continue
    raise AssertionError('應該擋下：' + bad)
qid, values = new_row(head, day='2026-11-11', question='「行程」指的是哪一檔？', context='原稿：行程這檔…',
                      options=['勤誠 8210', '另一檔，我來說'], stamp='2026/11/11 12:00:00', seq=3)
row = dict(zip(head, values))
assert qid == 'Q202611111200003' and row['問題ID'] == qid and row['日期'] == '2026/11/11' and row['狀態'] == '待回答'
assert row['選項'] == '["勤誠 8210", "另一檔，我來說"]'
print('11/11 管理者問題：選項 2–4 個、去重、長度上限、整列欄位通過')
