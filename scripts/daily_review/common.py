"""每日寄信前複核的共用工具：開試算表、日期、紀錄表分頁定義。

讀主資料用服務帳戶（環境變數 GOOGLE_SHEETS_SERVICE_ACCOUNT、SPREADSHEET_ID，和主流程相同）；
寫紀錄只寫獨立的複核紀錄試算表（REVIEW_LOG_ID），不寫主資料。
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def _normalize_service_account():
    """雲端環境變數欄位是一行一值：金鑰可以貼成 Base64 一行，或壓成一行的 JSON；這裡統一還原成 JSON 字串。"""
    import base64
    value = os.environ.get('GOOGLE_SHEETS_SERVICE_ACCOUNT', '').strip()
    if not value or value.startswith('{'):
        return
    try:
        decoded = base64.b64decode(value + '=' * (-len(value) % 4)).decode('utf-8').strip()
    except Exception:
        decoded = ''
    if decoded.startswith('{'):
        os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'] = decoded
        return
    raise SystemExit('GOOGLE_SHEETS_SERVICE_ACCOUNT 不是服務帳戶金鑰（要整份 JSON 或它的 Base64，'
                     f'目前只有 {len(value)} 字，可能貼成了金鑰 ID）')


_normalize_service_account()
TAIPEI = timezone(timedelta(hours=8))
WORK = Path(os.environ.get('DAILY_REVIEW_WORKDIR') or ROOT / '.daily-review')

LOG_TABS = {
    '每日摘要': ['日期', '狀態', '開始時間', '完成時間', '主流程狀態', '郵件狀態', '比對檔數', '明確錯誤', '待確認',
              '邊界', '複核錯', '已修正', '判讀雜湊', '執行', '備註'],
    '差異明細': ['日期', '代號', '名稱', '網站分類', '複核分類', '可接受集合', '差異類型', '嚴重度', '原文依據',
              '判斷理由', '處理', '修正前', '修正後', '執行', '是否轉成規則或測試'],
    '通知': ['建立時間', '日期', '類型', '預定寄送', '主旨', '內容', '狀態', '寄出時間'],
    '提示詞候選': ['日期', '差異類型', '近兩週次數', '反例原句', '建議修改', '要新增的測試', '狀態'],
    # 2026/11/08：管理者在 LINE 下的裁示（Apps Script 寫入「待處理」；雲端排程「管理者裁示」處理；line-ruling-apply.yml 標結果）
    '管理者裁示': ['建立時間', '裁示ID', '日期', '代號', '名稱', '裁示分類', '管理者說明', '來源', '管理者', '狀態',
              '分支', '結果', '更新時間', 'LINE回報'],
    # 2026/11/11：每日複核要管理者決定的問題（附預想選項；Apps Script 推到 LINE，管理者選或自訂後轉成「管理者裁示」）
    '管理者問題': ['建立時間', '問題ID', '日期', '問題', '背景', '選項', '狀態', '回答', '裁示ID', '更新時間', 'LINE推送'],
    # 2026/11/12：直播畫面辨識（scripts/screen_identify.py 由 Gemini 看回放片段寫入；11:40 雲端複核核對後更新狀態）
    '畫面辨識': ['建立時間', '日期', '影片ID', '時間', '講者原話', '畫面股名', '代號', '把握', '說明', '狀態', '處理說明'],
    # 2026/11/12：畫面辨識學到的詞（新股名直接採用；聽錯待雲端複核對原稿確認）；transcript.py 聽打詞表會讀（見 vocab_learn.py）
    '詞庫學習': ['建立時間', '日期', '類型', '聽到', '股名', '代號', '次數', '時間', '狀態', '說明'],
}
LABEL = {'buy': '買入', 'sell': '賣出', 'holdings': '會員持股', 'watch_watch': '觀望注意',
         'watch_avoid': '觀望不碰', 'history': '回顧', 'ignored': '排除', 'uncertain': '待確認', 'missing': '未收錄'}
DIRECTION = {'買入': 'buy', '賣出': 'sell', '觀望注意': 'watch_watch', '觀望不碰': 'watch_avoid'}


def now():
    return datetime.now(TAIPEI)


def today():
    return now().strftime('%Y/%m/%d')


def norm_day(value):
    """'2026-11-09'、'2026/11/9'（簡訊解析寫的列沒有補零）、試算表日期序號 → '2026/11/09'。
    2026/11/09：勤誠通知列日期是 2026/11/9，舊寫法比不到，複核與裁示都說「找不到這一列」。"""
    text = str(value or '').strip()
    if re.fullmatch(r'\d{5}', text):
        return (datetime(1899, 12, 30) + timedelta(days=int(text))).strftime('%Y/%m/%d')
    text = text[:10].replace('-', '/')
    m = re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})', text)
    return f'{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}' if m else text


def sha(text):
    return hashlib.sha256(str(text).encode('utf-8')).hexdigest()


def pipeline():
    """主流程模組（重用它的試算表與原稿讀取，確保複核讀的是同一份原稿）。"""
    sys.path.insert(0, str(ROOT / 'pipeline'))
    import pipeline as pl  # noqa: E402
    return pl


def key_diagnostics():
    """金鑰載入失敗時只印結構（不印任何金鑰內容），用來判斷是貼上時被截斷、換行被改掉，還是內容被改動。"""
    import base64
    try:
        info = json.loads(os.environ.get('GOOGLE_SHEETS_SERVICE_ACCOUNT', ''))
    except Exception as e:
        return f'金鑰不是合法 JSON（{type(e).__name__}）'
    key = str(info.get('private_key') or '')
    lines = key.strip().splitlines()
    body = ''.join(l for l in lines if not l.startswith('-----'))
    try:
        der = len(base64.b64decode(body, validate=True))
    except Exception:
        der = -1
    return ('金鑰結構：type=%s，email 結尾=%s，private_key_id 長度=%d，私鑰行數=%d，開頭標記=%s，結尾標記=%s，'
            '內含字面反斜線n=%s，含空白=%s，本體長度=%d，本體可解碼=%s（DER %d 位元組）') % (
        info.get('type'), str(info.get('client_email', '')).split('@')[-1], len(str(info.get('private_key_id') or '')),
        len(lines), lines[0] == '-----BEGIN PRIVATE KEY-----' if lines else False,
        lines[-1] == '-----END PRIVATE KEY-----' if lines else False, chr(92) + 'n' in key, ' ' in body, len(body),
        der > 0, der)


def open_book(pl):
    try:
        return pl.open_sheets()
    except ValueError as e:
        raise SystemExit('試算表連線失敗：' + str(e) + chr(10) + key_diagnostics())


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
