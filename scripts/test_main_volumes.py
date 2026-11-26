"""2026/11/25 主資料分卷——GitHub 端（不連 Google、不連 Apps Script）：python -B scripts/test_main_volumes.py

重現 11/25 11:34 的失敗：逐字稿寫入主表回 400「This document is too large to continue editing」。
改版後應該：請 Apps Script 先清空白欄 → 仍被拒就整本換新 → 同一個呼叫轉到新卷同名分頁重試成功，流程不中斷。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['SPREADSHEET_ID'] = 'MAIN'   # 測試自己的主表 ID（離線契約入口會放別的佔位值）

import gspread  # noqa: E402
from pipeline import main_volumes as mv  # noqa: E402
import transcript as tx  # noqa: E402

TOO_LARGE = 'This document is too large to continue editing.'


class Resp:
    def __init__(self, code, msg):
        self.status_code, self._msg, self.text = code, msg, msg

    def json(self):
        return {'error': {'code': self.status_code, 'message': self._msg, 'status': 'INVALID_ARGUMENT'}}


class Book(gspread.Spreadsheet):
    def __init__(self, bid, tabs, broken=False):   # 不呼叫父類別（不連網）
        self._properties = {'id': bid, 'title': bid}
        self.tabs, self.broken = tabs, broken

    def worksheet(self, title):
        return Sheet(self, title)


class Sheet(gspread.Worksheet):
    def __init__(self, book, title):
        self._spreadsheet, self._properties = book, {'title': title, 'sheetId': 0, 'index': 0}

    @property
    def spreadsheet(self):
        return self._spreadsheet

    def append_rows(self, rows, **kw):
        if self._spreadsheet.broken:
            raise gspread.exceptions.APIError(Resp(400, TOO_LARGE))
        self._spreadsheet.tabs.setdefault(self.title, [['h']]).extend(rows)

    def get_all_values(self):
        return [list(r) for r in self._spreadsheet.tabs.get(self.title, [])]


class Client:
    def __init__(self, books):
        self.books, self.opened = books, []

    def open_by_key(self, bid):
        self.opened.append(bid)
        return self.books[bid]


def setup(calls, route_after=None):
    mv._reset_for_tests()
    log = []
    state = {'switched': False}

    def fake_call(op, payload=None, source=None, timeout=330):
        log.append((op, source, dict(payload or {})))
        if op == 'main-route':
            return route_after if state['switched'] and route_after else {'ok': True, 'active': 'MAIN', 'archives': []}
        r = calls.pop(0)
        if r.get('switched'):
            state['switched'] = True
        return r
    mv._call = fake_call
    return log


DK = ['代號', '日期', '開', '高', '低', '收', '量']

# 一、11/25 失敗型態：清空白欄仍被拒 → 換卷 → 轉到新卷重試成功
main = Book('MAIN', {'逐字稿': [['日期', '內容']], '日K快取': [DK, ['2330', '2026/11/24', '1', '2', '1', '2', '9']]}, broken=True)
new = Book('NEW', {'逐字稿': [['日期', '內容']], '日K快取': [['日期', '代號', '開', '高', '低', '收', '量'], ['2026/11/24', '2330', '1', '3', '1', '3', '8'], ['2026/11/25', '2330', '2', '3', '2', '3', '7']]})
client = Client({'MAIN': main, 'NEW': new})
log = setup([{'ok': True, 'step': 'trim', 'cells': 1200000}, {'ok': True, 'switched': True, 'volume': 2}],
            route_after={'ok': True, 'active': 'NEW', 'archives': ['MAIN']})
mv.remember(client, 'bot@proj.iam.gserviceaccount.com')
assert mv.active_id('MAIN') == 'MAIN'
ws = client.open_by_key(mv.active_id('MAIN')).worksheet('逐字稿')
tx.sheets_retry(ws.append_rows, [['2026/11/25', '今天的逐字稿']], value_input_option='RAW')
assert new.tabs['逐字稿'][-1] == ['2026/11/25', '今天的逐字稿'], '今天的資料寫進新卷'
assert main.tabs['逐字稿'] == [['日期', '內容']], '舊卷沒有被寫'
relieves = [x for x in log if x[0] == 'main-relieve']
assert len(relieves) == 2 and relieves[0][2]['trimmed'] is False and relieves[1][2]['trimmed'] is True, '先清空白欄，再換卷'
assert relieves[0][1] == 'MAIN', '告訴 Apps Script 是哪一卷寫不進去'
assert relieves[0][2]['serviceEmail'].endswith('.iam.gserviceaccount.com'), '帶服務帳號讓新卷分享給它'
# 換卷後，仍綁在舊卷的讀取也改讀新卷
assert tx.sheets_retry(ws.get_all_values)[-1] == ['2026/11/25', '今天的逐字稿']
# 累積表合併讀取：舊卷在前、使用中在後；表頭以新卷為準；同一筆（代號＋日期）以新卷為準
merged = mv.merged_values(ws.spreadsheet, '日K快取', ('代號', '日期'), tx.sheets_retry)
assert merged[0] == ['日期', '代號', '開', '高', '低', '收', '量']
assert [r[:2] + [r[3]] for r in merged[1:]] == [['2026/11/24', '2330', '3'], ['2026/11/25', '2330', '3']], merged

# 二、別的 400 照舊直接丟出，不請求換卷
main2 = Book('MAIN', {'x': [['h']]})
client2 = Client({'MAIN': main2})
log2 = setup([])
mv.remember(client2, '')


class Bad(Sheet):
    def append_rows(self, rows, **kw):
        raise gspread.exceptions.APIError(Resp(400, 'Unable to parse range'))


try:
    tx.sheets_retry(Bad(main2, 'x').append_rows, [['a']])
    raise AssertionError('應該丟出')
except gspread.exceptions.APIError:
    pass
assert not [x for x in log2 if x[0] == 'main-relieve'], '不相干的錯誤不換卷'

# 三、Apps Script 還是舊版（沒有分卷動作）或查不到：照舊用 SPREADSHEET_ID，換卷請求失敗時把原錯誤丟出
mv._reset_for_tests()
mv._call = lambda *a, **k: {'ok': False, 'error': '備份分頁不在允許範圍'}
assert mv.active_id('MAIN') == 'MAIN' and mv.route()['fallback'] is True
broken = Book('MAIN', {'逐字稿': [['h']]}, broken=True)
mv.remember(Client({'MAIN': broken}), '')
try:
    tx.sheets_retry(broken.worksheet('逐字稿').append_rows, [['a']])
    raise AssertionError('應該丟出')
except gspread.exceptions.APIError as e:
    assert mv.is_too_large(e)

# 四、pipeline.py 用的是同一套
import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location('pl_check', ROOT / 'pipeline' / 'pipeline.py')
src = (ROOT / 'pipeline' / 'pipeline.py').read_text(encoding='utf-8')
assert 'main_volumes.rebind(fn)' in src and 'main_volumes.active_id(SPREADSHEET_ID, who)' in src and 'main_volumes.merged_values(ss, "日K快取"' in src
print('主資料分卷（GitHub 端）：11/25 寫不進去會先清空白欄、再換新卷並重試成功；舊卷綁定改讀新卷；兩卷合併讀取對齊；舊版 Apps Script 照舊 — 通過')
