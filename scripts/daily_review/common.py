"""每日寄信前複核的共用工具：開試算表、日期、紀錄表分頁定義。

讀主資料用服務帳戶（環境變數 GOOGLE_SHEETS_SERVICE_ACCOUNT、SPREADSHEET_ID，和主流程相同）；
寫紀錄只寫獨立的複核紀錄試算表（REVIEW_LOG_ID），不寫主資料。
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TAIPEI = timezone(timedelta(hours=8))
WORK = Path(os.environ.get('DAILY_REVIEW_WORKDIR') or ROOT / '.daily-review')

LOG_TABS = {
    '每日摘要': ['日期', '狀態', '開始時間', '完成時間', '主流程狀態', '郵件狀態', '比對檔數', '明確錯誤', '待確認',
              '邊界', '複核錯', '已修正', '判讀雜湊', '執行', '備註'],
    '差異明細': ['日期', '代號', '名稱', '網站分類', '複核分類', '可接受集合', '差異類型', '嚴重度', '原文依據',
              '判斷理由', '處理', '修正前', '修正後', '執行', '是否轉成規則或測試'],
    '通知': ['建立時間', '日期', '類型', '預定寄送', '主旨', '內容', '狀態', '寄出時間'],
    '提示詞候選': ['日期', '差異類型', '近兩週次數', '反例原句', '建議修改', '要新增的測試', '狀態'],
}
LABEL = {'buy': '買入', 'sell': '賣出', 'holdings': '會員持股', 'watch_watch': '觀望注意',
         'watch_avoid': '觀望不碰', 'history': '回顧', 'ignored': '排除', 'uncertain': '待確認', 'missing': '未收錄'}
DIRECTION = {'買入': 'buy', '賣出': 'sell', '觀望注意': 'watch_watch', '觀望不碰': 'watch_avoid'}


def now():
    return datetime.now(TAIPEI)


def today():
    return now().strftime('%Y/%m/%d')


def norm_day(value):
    return str(value or '').strip()[:10].replace('-', '/')


def sha(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def pipeline():
    """主流程模組（重用它的試算表與原稿讀取，確保複核讀的是同一份原稿）。"""
    sys.path.insert(0, str(ROOT / 'pipeline'))
    import pipeline as pl  # noqa: E402
    return pl


def open_book(pl):
    return pl.open_sheets()


def open_log():
    """複核紀錄試算表；缺分頁就補表頭。"""
    import gspread
    from google.oauth2.service_account import Credentials
    log_id = os.environ.get('REVIEW_LOG_ID', '').strip()
    if not log_id:
        raise SystemExit('缺少 REVIEW_LOG_ID（複核紀錄試算表 ID）')
    info = json.loads(os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'])
    creds = Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    book = gspread.authorize(creds).open_by_key(log_id)
    names = {ws.title for ws in book.worksheets()}
    for tab, head in LOG_TABS.items():
        if tab not in names:
            ws = book.add_worksheet(tab, rows=200, cols=len(head))
            ws.update([head], 'A1')
    return book


def rows_of(ws):
    values = ws.get_all_values()
    if not values:
        return [], []
    return values[0], [dict(zip(values[0], r)) for r in values[1:]]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
