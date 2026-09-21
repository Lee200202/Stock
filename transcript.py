#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcript.py —— 每日逐字稿抓取（自動 + 手動，手動優先）

用 Gemini API 直接讀公開的 YouTube 網址，由模型自己「聽」聲音聽打。

    這個頻道的影片**沒有任何字幕**，連 YouTube 自動產生的都沒有。
    所以任何靠抓字幕的工具都取不到逐字稿，只能讓模型聽。

不需要字幕、不需要下載影片、不需要瀏覽器，也**沒有任何會過期的憑證**——
金鑰是 API key，不會過期。

目標頻道
    https://www.youtube.com/@xinchenginsta/streams
    頻道 ID　UCPqyYS3n6yyXL2jygauXpzg
    標題　　2026/09/17(四)張震  股市盤中家教班（日期以標題為準）
    直播　　平日約 10:00 開播，11:01～11:19 結束，每集 57～70 分鐘
    回放　　直播結束後約 3～5 分鐘才處理好，那之後 Gemini 才讀得到
    保留　　頻道只留最近 5～6 部，舊的會被刪掉，所以一定要每天抓

兩套機制，手動永遠贏
    自動　排程時間到，找當天那一集，確認回放好了，用 Gemini 聽打，寫進試算表。
    手動　人直接把逐字稿貼進去（本程式 manual 子指令、後台投稿、或直接編輯試算表）。

    「影片清單」的「逐字稿來源」欄記錄那一列是誰寫的。只要是「手動」或
    「手動保留」，自動化立刻停手，不覆蓋、不重抓。

排程（台灣時間，平日）
    11:05 起每 3 分鐘敲一次，最晚到 14:00。
    直播 11:19 前後結束、回放再等 3～5 分鐘，所以通常 11:25～11:40 就抓得到。

退出碼
    0  有進展或沒事做（寫入成功、已有稿、手動優先停手、回放還沒好）
    1  真的失敗（設定缺漏、試算表寫不進去、Gemini 一直失敗）
    2  Gemini 額度用完。等額度重置（太平洋時間午夜）或補一把金鑰。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials

from pipeline.market_holidays import is_trading_day, why_closed

# ---------------------------------------------------------------- #
# 設定
# ---------------------------------------------------------------- #
TAIPEI = timezone(timedelta(hours=8))

DEFAULT_CHANNEL_ID = "UCPqyYS3n6yyXL2jygauXpzg"
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID

TITLE_KEYWORDS = [k.strip() for k in os.environ.get(
    "TITLE_KEYWORDS", "盤中家教班").split(",") if k.strip()]

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "").strip()

VIDEO_SHEET = "影片清單"
STATUS_SHEET = "系統狀態"

COL_ID, COL_DATE, COL_TITLE = "影片ID", "發布日期", "標題"
COL_STATUS, COL_REASON = "處理狀態", "失敗原因"
COL_RAW, COL_POLISHED = "原始逐字稿內容", "修飾後逐字稿內容"
COL_UPDATED, COL_SHA, COL_ALIAS = "原文更新時間", "原文SHA256", "來源別名"
COL_SOURCE = "逐字稿來源"
FULL_HEADER = [COL_ID, COL_DATE, COL_TITLE, COL_STATUS, COL_REASON,
               COL_RAW, COL_POLISHED, COL_UPDATED, COL_SHA, COL_ALIAS, COL_SOURCE]

SRC_MANUAL = "手動"
SRC_HOLD = "手動保留"
SRC_AUTO = "自動"

# 排程。實測最近幾集「進入頻道清單」的時間：
#   9/14 11:15　9/15 11:07　9/16 11:04　9/17 11:24　9/18 11:18
# 影片是在直播「結束時」才進清單，不是開播時，所以範圍落在 11:04～11:24。
# 起點設 11:05 才接得住早收的那幾天；設 11:20 的話，9/15、9/16 那種日子
# 明明 11:07 就抓得到，卻要白等十幾分鐘。
#
# 提早的代價幾乎是零：影片還沒出現時只用 YouTube API 問一句（約 2 units），
# 不呼叫 Gemini。撞到直播中也只會回報「直播中」然後等下一輪。
POLL_START = os.environ.get("POLL_START", "11:05").strip()
POLL_UNTIL = os.environ.get("POLL_UNTIL", "14:00").strip()
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SEC", "180"))
TIME_BUDGET = int(os.environ.get("TIME_BUDGET_SEC", "1500"))

# 直播結束後要等幾分鐘才送給 Gemini。YouTube 要先把回放處理好，
# 太早送過去會拿到「影片無法存取」或半截內容。
READY_DELAY_MIN = int(os.environ.get("READY_DELAY_MINUTES", "5"))

# Gemini
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-3.5-flash-lite"

# 備援模型。可以填多個，逗號分隔，會依序往下試。填 none／off／- 表示不要備援。
#
# **預設留空，刻意的。** 這裡不替你填一個沒驗證過的模型名稱：
# 模型不存在時 API 回 404，而 404 在本程式是不重試的硬失敗，
# 等於把「多一條退路」變成「多一個必定失敗的步驟」。
#
# 但有備援是有意義的，值得你花五分鐘設好。2026/09/17 的教訓：
# 主模型回 high demand 時，程式在兩把金鑰之間輪流重送，四次全敗、
# 耗掉 11 分鐘、整集失敗。壅塞的是模型本身，所有金鑰共用同一份容量，
# 換金鑰不可能有用——要換的是模型。額度也是每個模型分開算的，
# 所以備援模型對「額度用完」同樣有效。
#
# 你的金鑰實際能用哪些模型，跑 `python transcript.py check` 會列出來，
# 從那份清單挑一個填進 GEMINI_FALLBACK_MODEL 就好。
#
# 沒設也不會整集失敗：所有模型都忙時會判成「還沒好」，交給輪詢迴圈
# 三分鐘後重試，已完成的片段也會保留（見 Transcriber.transcribe 的 cache）。
_fb = os.environ.get("GEMINI_FALLBACK_MODEL", "").strip()
if _fb.lower() in ("none", "off", "false", "0", "-"):
    GEMINI_FALLBACK_MODELS = []
else:
    GEMINI_FALLBACK_MODELS = [m.strip() for m in _fb.split(",") if m.strip()]
GEMINI_FALLBACK_MODEL = GEMINI_FALLBACK_MODELS[0] if GEMINI_FALLBACK_MODELS else ""
SEGMENT_MINUTES = int(os.environ.get("SEGMENT_MINUTES", "30"))
# 0 = 不指定 fps（API 預設每秒一張畫面）。實測 fps=0.2 雖然省 token，
# 但部分片段會一直回 400，所以預設不帶。
VIDEO_FPS = float(os.environ.get("VIDEO_FPS", "0"))

VOCABULARY = os.environ.get("VOCABULARY", "").strip() or (
    "張震,信誠環球投顧,加權指數,櫃買指數,台積電,聯發科,鴻海,外資,投信,自營商,"
    "三大法人,融資,融券,當沖,月線,季線,半年線,年線,K線,KD,MACD,RSI,布林通道,"
    "多頭,空頭,停損,停利,除權息,法說會,費半,那斯達克,道瓊,聯準會,CPI,ETF")

SYSTEM_INSTRUCTION = """你是專業的中文逐字稿聽打員，負責把台灣股市直播節目「張震 股市盤中家教班」的語音完整轉成文字。

規則：
1. 逐字完整聽打，不可摘要、省略、改寫或自行補充；「嗯」「那個」等無意義口頭禪可略過，但所有觀點、數字、個股、價位都必須保留。
2. 使用繁體中文與台灣用語，標點符號用全形。
3. 股票代號、指數點位、價格、漲跌幅、日期一律用阿拉伯數字（例如：2330 台積電、跌破 22,500 點、漲 3.5%）。
4. 依語意或話題轉換分段，段落之間空一行。
5. 主持人唸出的觀眾留言照實聽打。
6. 片頭等待畫面、背景音樂、無人說話的片段直接略過，不要描述。
7. 聽不清楚的字詞寫成最合理的寫法，不要加任何註記、括號或說明。
8. 只輸出逐字稿本文，不要加標題、前言、摘要或結語。"""

MIN_TRANSCRIPT = int(os.environ.get("MIN_TRANSCRIPT", "200"))
SHORT_HINT = 5000
SHEET_CELL_LIMIT = 49000

RUN_STARTED = time.monotonic()
TRANSIENT = (429, 500, 502, 503, 504)

# Windows 主控台預設 cp950，中文訊息會變亂碼，那些訊息要看得懂才有用。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


class NotReadyYet(Exception):
    """回放還沒好。不是錯誤，是還沒輪到。"""


class QuotaExhausted(Exception):
    """Gemini 額度用完。等重置或補金鑰，重試沒有用。"""


class ModelOverloaded(Exception):
    """模型現在負載過高。換金鑰沒有用（壅塞的是模型），換模型或等一下才有用。"""


class Done(Exception):
    """這一天已經處理完，不必再敲。"""

    def __init__(self, reason: str, code: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.code = code


class SegmentCache(dict):
    """每完成一段就原子存檔，GitHub 下一個 job 可接續；未完成段不當原稿。"""
    def __init__(self, path):
        super().__init__()
        self.path = path
        if path.exists():
            try:
                for item in json.loads(path.read_text(encoding='utf-8')):
                    start, end, text = item
                    if isinstance(start,int) and isinstance(end,int) and end>start and isinstance(text,str) and text.strip():
                        dict.__setitem__(self,(start,end),text)
            except (ValueError, TypeError, OSError):
                self.clear()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps([[a,b,t] for (a,b),t in self.items()],ensure_ascii=False),encoding='utf-8')
        temp.replace(self.path)


def segment_cache_for(video):
    folder = os.environ.get('TRANSCRIPT_CACHE_DIR','').strip()
    if not folder:
        return _SEGMENT_CACHE.setdefault(video.id,{})
    identity = json.dumps([video.id,video.duration_sec,SEGMENT_MINUTES,SYSTEM_INSTRUCTION,VOCABULARY],ensure_ascii=False)
    return SegmentCache(Path(folder)/ (hashlib.sha256(identity.encode()).hexdigest()+'.json'))


def log(msg: str = ""):
    """所有輸出都帶台北時間。GitHub 與 Cloud 的時間戳是 UTC，差八小時，
    不標清楚對照日誌會一直算錯。"""
    if not msg:
        print(flush=True)
        return
    print(f"[{datetime.now(TAIPEI):%H:%M:%S}] {msg}", flush=True)


def hms(seconds) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{s:02d}"


def parse_hhmm(s: str):
    m = re.match(r"^(\d{1,2}):(\d{2})$", (s or "").strip())
    if not m:
        raise ValueError(f"時間格式必須是 HH:MM，收到「{s}」")
    return int(m.group(1)), int(m.group(2))


def at_taipei(d: date, hhmm: str) -> datetime:
    h, m = parse_hhmm(hhmm)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TAIPEI)


def norm_date(v) -> str:
    """把試算表各種日期寫法統一成 yyyy/MM/dd。"""
    if isinstance(v, datetime):
        return (v.astimezone(TAIPEI) if v.tzinfo else v).strftime("%Y/%m/%d")
    if isinstance(v, date):
        return v.strftime("%Y/%m/%d")
    s = str(v or "").strip().replace("-", "/")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
    eng = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
                    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
                    r"(\d{1,2})\s+(20\d{2})\b", s, re.I)
    if eng:
        months = {n.lower(): i for i, n in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
             "Oct", "Nov", "Dec"), 1)}
        return f"{eng.group(3)}/{months[eng.group(1).lower()]:02d}/{int(eng.group(2)):02d}"
    return ""


def sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def cell(text: str) -> str:
    text = text or ""
    if len(text) > SHEET_CELL_LIMIT:
        log(f"警告：內容 {len(text)} 字超過試算表單格上限，已截斷")
        return text[:SHEET_CELL_LIMIT] + "\n\n（超過試算表單格上限，已截斷）"
    return text


def gemini_keys() -> list:
    """收集所有可用的 Gemini 金鑰。

    額度是「每把金鑰、每個模型分開算」的，多備幾把就能在免費額度內
    處理完一整集。支援三種寫法，可混用：
        GEMINI_API_KEY=key1
        GEMINI_API_KEY=key1,key2,key3
        GEMINI_API_KEY_2=key2　GEMINI_API_KEY_3=key3　…
    """
    keys = []
    for part in os.environ.get("GEMINI_API_KEY", "").split(","):
        part = part.strip()
        if part:
            keys.append(part)
    for name, value in sorted(os.environ.items()):
        if re.fullmatch(r"GEMINI_API_KEY_\d+", name):
            v = value.strip()
            if v and v not in keys:
                keys.append(v)
    return keys


# ---------------------------------------------------------------- #
# 試算表
# ---------------------------------------------------------------- #
def sheets_retry(fn, *args, **kwargs):
    """Google Sheets 偶發 429 / 5xx。429 是每分鐘配額，退避要跨過整個
    一分鐘窗口才有意義，所以最長等到 70 秒，並加抖動避免重試又一起撞上去。"""
    last = None
    for i, delay in enumerate((0, 10, 30, 70)):
        if delay:
            time.sleep(delay + random.uniform(0, 3))
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code not in TRANSIENT:
                raise
            last = code
            log(f"Sheets 回傳 {code}，第 {i + 1} 次重試")
    raise RuntimeError(f"Google Sheets 連續重試失敗，最後狀態 {last}")


SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
                 "https://www.googleapis.com/auth/drive.file"]


def _sheets_credentials():
    """取得寫試算表用的憑證，並回報用的是哪一種身分。

    服務帳號 JSON 金鑰不會過期，ADC 也不會。這支程式已經沒有任何
    「會自己失效」的憑證了。
    """
    raw = os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", "").strip() \
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if raw:
        # 有些部署方式會把 JSON 轉成 base64 再存進 Secret，兩種都收。
        if not raw.lstrip().startswith("{"):
            import base64
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except Exception as e:
                raise SystemExit(f"服務帳號內容既不是 JSON 也不是合法 base64：{e}")
        try:
            info = json.loads(raw)
        except Exception as e:
            raise SystemExit(f"服務帳號 JSON 解析失敗：{e}")
        return (Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES),
                info.get("client_email", "（讀不到）"))

    try:
        import google.auth
        creds, _ = google.auth.default(scopes=SHEETS_SCOPES)
    except Exception as e:
        raise SystemExit(
            "找不到可用的 Google 憑證。二選一：\n"
            "  1. 設 GOOGLE_SHEETS_SERVICE_ACCOUNT（服務帳號 JSON 整份內容，或其 base64）\n"
            "  2. 跑在有 ADC 的環境（Cloud Run／GCE）\n"
            f"原始訊息：{e}")
    return creds, getattr(creds, "service_account_email", "") or "（ADC 身分）"


def open_sheets():
    if not SPREADSHEET_ID:
        raise SystemExit("缺少 SPREADSHEET_ID。就是試算表網址中 /d/ 與 /edit 之間那一長串。")
    creds, who = _sheets_credentials()
    gc = gspread.authorize(creds)
    def _blocked(code, extra=""):
        return SystemExit(
            f"開不了試算表（HTTP {code}）。\n"
            f"\n"
            f"  要分享給這個信箱（權限給「編輯者」）：\n"
            f"      {who}\n"
            f"\n"
            f"  試算表 →「共用」→ 貼上上面那個信箱 → 權限選「編輯者」\n"
            f"  →「通知使用者」取消勾選 →「共用」。\n"
            f"\n"
            f"  另一個可能：專案沒有啟用 Google Sheets API 與 Google Drive API。\n"
            + (f"\n原始訊息：{extra[:300]}" if extra else ""))

    # gspread 6 會把 HTTP 錯誤換成別的型別再丟出來，不是原本的 APIError：
    #   403 → 內建的 PermissionError（而且訊息是空的）
    #   404 → SpreadsheetNotFound
    # 只接 APIError 的話這兩種最常見的狀況都會漏掉，使用者看到的是一長串
    # traceback 加一行 PermissionError，完全看不出「要去分享試算表給誰」。
    try:
        return sheets_retry(gc.open_by_key, SPREADSHEET_ID)
    except PermissionError as e:
        raise _blocked(403, str(e))
    except gspread.exceptions.SpreadsheetNotFound as e:
        raise SystemExit(
            f"找不到這張試算表（HTTP 404）。SPREADSHEET_ID 可能貼錯了。\n"
            f"  ID 是網址中 /d/ 與 /edit 之間那一長串。\n"
            f"原始訊息：{str(e)[:200]}")
    except gspread.exceptions.APIError as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (403, 404):
            raise _blocked(code, str(e))
        raise


def video_sheet(ss):
    """取得「影片清單」，順便補齊表頭缺掉的欄位。

    只補「尾巴少掉的那幾欄」；中間對不上時不動，那種情況要人看過，
    自動補只會把整排欄位錯開，比缺一欄糟得多。
    """
    try:
        ws = ss.worksheet(VIDEO_SHEET)
    except gspread.WorksheetNotFound:
        ws = sheets_retry(ss.add_worksheet, title=VIDEO_SHEET,
                          rows=2000, cols=len(FULL_HEADER))
        sheets_retry(ws.update, range_name="A1", values=[FULL_HEADER])
        log(f"已建立「{VIDEO_SHEET}」分頁")
        return ws, list(FULL_HEADER)

    header = [str(h).strip() for h in sheets_retry(ws.row_values, 1)]
    for i, have in enumerate(header[:7]):
        if have != FULL_HEADER[i]:
            log(f"注意：「{VIDEO_SHEET}」第 {i + 1} 欄是「{have}」，"
                f"與預期的「{FULL_HEADER[i]}」不同，不自動補欄，請人工確認表頭。")
            return ws, header
    # 排版欄可能早於來源欄加入，不能用總欄數猜「已補齊」。既有欄位一律不搬動。
    add = [h for h in FULL_HEADER if h not in header]
    if not add:
        return ws, header
    if ws.col_count < len(header) + len(add):
        sheets_retry(ws.add_cols, len(header) + len(add) - ws.col_count)
    sheets_retry(ws.update,
                 range_name=gspread.utils.rowcol_to_a1(1, len(header) + 1),
                 values=[add])
    log(f"「{VIDEO_SHEET}」補上欄位：{'、'.join(add)}")
    return ws, header + add


def col_a1(header, name, row):
    if name not in header:
        return None
    return gspread.utils.rowcol_to_a1(row, header.index(name) + 1)


def pick_row(rows, video_id, date_str):
    """挑出代表當天的那一列。

    ID 優先；同 ID／同日有多列時，以原文更新時間、原文長度、列號決定，
    不能直接拿第一筆——重複投稿時第一筆往往是最舊的那份。
    """
    cands = [(i, r) for i, r in enumerate(rows, 2)
             if (video_id and str(r.get(COL_ID) or "").strip() == video_id)
             or norm_date(r.get(COL_DATE)) == date_str]
    if not cands:
        return None, None

    def rank(pair):
        i, r = pair
        return (source_of(r) in (SRC_MANUAL, SRC_HOLD),
                bool(video_id and str(r.get(COL_ID) or "").strip() == video_id),
                str(r.get(COL_UPDATED) or ""),
                len(str(r.get(COL_RAW) or "")), i)
    return max(cands, key=rank)


def read_state(ss, video_id, date_str):
    ws, header = video_sheet(ss)
    rows = sheets_retry(ws.get_all_records)
    idx, row = pick_row(rows, video_id, date_str)
    return ws, header, idx, (row or {})


def source_of(row) -> str:
    """那一列的逐字稿是誰寫的。

    「逐字稿來源」欄空白但已有原文的舊資料，一律當成手動——那一欄是後來
    才加的，在它之前所有原文都是人貼的，當成自動就等於允許機器覆蓋人工
    成果，那是這支程式唯一真正不可逆的傷害。
    """
    src = str(row.get(COL_SOURCE) or "").strip()
    if src:
        return src
    return SRC_MANUAL if str(row.get(COL_RAW) or "").strip() else ""


def write_status_log(ss, kind: str, detail: str = ""):
    """每一輪往「系統狀態」寫一列。寫入失敗不可以影響主流程。"""
    try:
        try:
            ws = ss.worksheet(STATUS_SHEET)
        except Exception:
            ws = sheets_retry(ss.add_worksheet, title=STATUS_SHEET, rows=2000, cols=6)
            sheets_retry(ws.append_row, ["時間", "類別", "說明", "模式", "執行環境"])
        where = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "本機"
        sheets_retry(ws.append_row, [
            datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S"),
            kind, str(detail)[:800], "逐字稿抓取", where])
    except Exception as e:
        log(f"（系統狀態寫入失敗，不影響本次結果：{e}）")


def save_transcript(ss, video, date_str, text, source, note=""):
    """把日期與逐字稿寫進「影片清單」。同一天已經有列就更新，沒有就新增。"""
    ws, header, idx, row = read_state(ss, video.get("id", ""), date_str)
    if any(h not in header for h in FULL_HEADER):
        raise RuntimeError('影片清單缺必要欄位，尚未寫入；請核對表頭')
    if source == SRC_AUTO:
        if source_of(row) in (SRC_MANUAL, SRC_HOLD):
            raise Done('聽打期間收到手動投稿／保留，保留人工內容，自動結果不覆蓋。')
        if len(str(row.get(COL_RAW) or '').strip()) >= MIN_TRANSCRIPT:
            raise Done('聽打期間已有完整原稿，本次不重複寫入。')
        # Sheets API 沒有與 GAS 共用的 compare-and-swap。自動結果只追加，
        # 即使人工在這次讀取後才貼稿，也不會覆寫它。三端選列均手動優先。
        idx = None
    now = datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")
    body = cell(text)
    values = {
        COL_ID: video.get("id", "") or str(row.get(COL_ID) or ""),
        COL_DATE: date_str,
        COL_TITLE: video.get("title", "") or str(row.get(COL_TITLE) or ""),
        COL_STATUS: "逐字稿已就緒",
        COL_REASON: note,
        COL_RAW: body,
        COL_UPDATED: now,
        COL_SHA: sha256(body),
        COL_SOURCE: source,
        COL_POLISHED: '',
        '排版稿JSON': '', '排版稿指紋': '', '排版稿方式': '',
    }

    if idx is None:
        sheets_retry(ws.append_row, [values.get(h, "") for h in header],
                     value_input_option="RAW", table_range="A1")
        log(f"新增一列：{date_str}　{values[COL_TITLE]}")
    else:
        updates = []
        for name, val in values.items():
            a1 = col_a1(header, name, idx)
            if a1:
                updates.append({"range": a1, "values": [[val]]})
        sheets_retry(ws.batch_update, updates, value_input_option="RAW")
        log(f"更新第 {idx} 列：{date_str}")

    log(f"逐字稿 {len(body)} 字　SHA256={values[COL_SHA]}　來源={source}")
    if len(body) < SHORT_HINT:
        log(f"注意：只有 {len(body)} 字，對一小時的直播而言偏短，請抽查是不是完整的一份。")
    return idx


# ---------------------------------------------------------------- #
# YouTube
# ---------------------------------------------------------------- #
TITLE_DATE = re.compile(r"(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})")
ISO_DURATION = re.compile(r"P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


@dataclass
class Video:
    id: str
    title: str
    date: date
    url: str = ""
    duration_sec: int = 0
    live_status: str = "none"       # live / upcoming / none
    privacy: str = "public"
    ended_at: datetime = None
    detailed: bool = field(default=False)

    def __post_init__(self):
        if not self.url:
            self.url = f"https://www.youtube.com/watch?v={self.id}"

    def is_ready(self, now: datetime) -> bool:
        """直播已結束、回放處理完成且公開，Gemini 才讀得到。

        四個條件缺一不可。少了 duration > 0 這一條最容易出事：直播剛結束時
        YouTube 還沒算出片長，那時送給 Gemini 會拿到半截或整個失敗。
        """
        return (self.live_status == "none"
                and self.ended_at is not None
                and now - self.ended_at >= timedelta(minutes=READY_DELAY_MIN)
                and self.privacy == "public"
                and self.duration_sec > 0)

    def status_text(self, now: datetime) -> str:
        if self.live_status == "upcoming":
            return "尚未開播"
        if self.live_status == "live":
            return "直播中"
        if self.privacy != "public":
            return f"非公開影片（{self.privacy}）"
        if not self.detailed:
            return "還沒查詳細狀態（缺 YOUTUBE_API_KEY）"
        if self.ended_at is None or self.duration_sec == 0:
            return "直播已結束，YouTube 回放處理中"
        if not self.is_ready(now):
            waited = (now - self.ended_at).total_seconds() / 60
            return f"回放剛好，等滿 {READY_DELAY_MIN} 分鐘緩衝（已等 {waited:.0f} 分）"
        return "可轉錄"


def parse_duration(value: str) -> int:
    m = ISO_DURATION.fullmatch((value or "").strip())
    if not m:
        return 0
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def _iso_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def date_from_title(title: str, fallback):
    """標題日期優先於 published。

    直播的 published 是「排程建立時間」，可能早於實際開播日；
    但標題「2026/09/17(四)張震 股市盤中家教班」一定是當天。
    """
    m = TITLE_DATE.search(title or "")
    if not m:
        return fallback
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                        tzinfo=TAIPEI).date()
    except ValueError:
        return fallback


def is_target(title: str) -> bool:
    return any(k in (title or "") for k in TITLE_KEYWORDS)


def fetch_feed():
    """列出頻道最近的影片。有 API 金鑰就用 API，沒有才退回 RSS。

    RSS 對機房 IP 會穩定回 404，不是暫時節流、重試無效。而且 RSS 也拿不到
    片長與直播結束時間，所以沒有金鑰時只能知道「影片在不在」，
    不能判斷「回放好了沒」。
    """
    if YOUTUBE_API_KEY:
        try:
            return fetch_feed_api()
        except Exception as e:
            log(f"YouTube Data API 失敗（{e}），改用 RSS 備援")
    return fetch_feed_rss()


def _yt_get(endpoint, **params):
    params["key"] = YOUTUBE_API_KEY
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}",
                     params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"{endpoint} HTTP {r.status_code}：{r.text[:200]}")
    return r.json()


def fetch_feed_api(limit=15):
    """uploads 播放清單 → videos 詳情。

    頻道 ID 的 UC 換成 UU 就是 uploads 播放清單。兩次呼叫共約 3 units，
    每日配額 10,000，離上限很遠。
    """
    if not CHANNEL_ID.startswith("UC"):
        raise RuntimeError(
            f"頻道 ID「{CHANNEL_ID}」不是 UC 開頭。@xinchenginsta 這種代稱"
            f"不能直接用，要填 {DEFAULT_CHANNEL_ID}。")
    data = _yt_get("playlistItems", part="contentDetails",
                   playlistId="UU" + CHANNEL_ID[2:], maxResults=limit)
    ids = [it["contentDetails"]["videoId"] for it in data.get("items", [])
           if it.get("contentDetails", {}).get("videoId")]
    if not ids:
        return []

    out = []
    detail = _yt_get("videos", part="snippet,contentDetails,liveStreamingDetails,status",
                     id=",".join(ids))
    for v in detail.get("items", []):
        sn = v.get("snippet", {})
        live = v.get("liveStreamingDetails", {}) or {}
        title = (sn.get("title") or "").strip()
        started = _iso_time(live.get("actualStartTime"))
        fallback = (started or _iso_time(live.get("scheduledStartTime"))
                    or _iso_time(sn.get("publishedAt")))
        fallback = fallback.astimezone(TAIPEI).date() if fallback else date.today()
        out.append(Video(
            id=v["id"], title=title, date=date_from_title(title, fallback),
            duration_sec=parse_duration(v.get("contentDetails", {}).get("duration", "")),
            live_status=sn.get("liveBroadcastContent", "none") or "none",
            privacy=(v.get("status", {}) or {}).get("privacyStatus", "public"),
            ended_at=_iso_time(live.get("actualEndTime")),
            detailed=True))
    out.sort(key=lambda x: x.date, reverse=True)
    log(f"YouTube Data API 取得 {len(out)} 支影片")
    return out


def fetch_feed_rss():
    """RSS 備援。拿得到影片與標題，拿不到片長與結束時間。"""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    last = ""
    for attempt, delay in enumerate((0, 4, 12, 30)):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, timeout=30, headers=headers)
        except Exception as e:
            last = str(e)
            continue
        if r.status_code == 200:
            root = ET.fromstring(r.text)
            ns = {"a": "http://www.w3.org/2005/Atom",
                  "yt": "http://www.youtube.com/xml/schemas/2015"}
            out = []
            for e in root.findall("a:entry", ns):
                title = (e.find("a:title", ns).text or "").strip()
                pub = _iso_time(e.find("a:published", ns).text)
                out.append(Video(id=e.find("yt:videoId", ns).text, title=title,
                                 date=date_from_title(title, pub.astimezone(TAIPEI).date())))
            out.sort(key=lambda x: x.date, reverse=True)
            log(f"RSS 取得 {len(out)} 支影片（沒有片長與結束時間）")
            return out
        last = f"HTTP {r.status_code}"
        log(f"RSS 第 {attempt + 1} 次回 {last}，重試")
    raise RuntimeError(
        f"YouTube RSS 連續失敗（最後 {last}）。設定 YOUTUBE_API_KEY 可以避開——"
        f"RSS 對機房 IP 會穩定回 404，而且拿不到判斷回放是否就緒所需的欄位。")


def find_video(target: date):
    """找出指定日期那一集。找不到回 None——早上還沒開播時那是常態。"""
    for v in fetch_feed():
        if v.date == target and is_target(v.title):
            return v
    return None


# ---------------------------------------------------------------- #
# Gemini 聽打
#
# 這是整支程式的核心，也是「免費」的來源：
# Gemini API 可以直接讀公開的 YouTube 網址，自己聽聲音轉文字。
# 不需要字幕（這個頻道根本沒有）、不需要下載影片、不需要瀏覽器，
# 而且 API key 不會過期。
#
# 一小時的直播不能一次送完，輸出會超過單次上限，所以切成 30 分鐘一段，
# 用 start_offset / end_offset 指定範圍分別送出，最後接起來。
# ---------------------------------------------------------------- #
POLL_SECONDS = 10
SEGMENT_TIMEOUT = 15 * 60       # 實測每段 1～3 分鐘，15 分鐘沒結果就重送
MIN_SPLIT_SECONDS = 5 * 60
MAX_ROUNDS = 3
RETRY_WAIT = (30, 90)           # 兩次合計超過一分鐘，順便避開每分鐘額度限制
PROBE_PROMPT = "請只回覆：OK"
PROBE_TIMEOUT = int(os.environ.get("KEY_PROBE_TIMEOUT_SECONDS", "30"))
NON_RETRYABLE = {401, 403, 404}  # 金鑰無效、沒權限、模型不存在：重試不會成功
# 串流連線被中途切斷時，從中斷處接續幾次（實測約 4 分半就會斷一次）。
STREAM_RESUME_ATTEMPTS = 3
STREAM_RESUME_WAIT = 5

# 模型負載過高。這是**模型層級**的問題，不是金鑰層級的。
#
# 2026/09/17 實測：gemini-3.5-flash-lite 回 high demand 時，兩把金鑰
# 輪流送了四次、耗掉 11 分鐘，每一次都是同樣的錯誤——因為壅塞的是模型本身，
# 所有金鑰共用同一份容量。換金鑰完全沒有用，只是把時間燒掉。
#
# 正確做法是**換模型**（或退開等一下），所以這一類要跟額度、跟一般失敗分開認。
OVERLOAD_HINTS = ("high demand", "overloaded", "unavailable", "try again later",
                  "resource exhausted", "server is busy", "temporarily")


def _err(exc):
    """SDK 的 HTTP 錯誤帶 status_code 與 body；連線錯誤則沒有 status_code。"""
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    return status, (str(body) if body else f"{type(exc).__name__}: {exc}")


def _is_overload(status, message: str) -> bool:
    """這個失敗是不是「模型現在忙不過來」。

    503 一律算；api_error 帶 high demand 那類措辭也算。
    要注意不能把 429（額度）誤判進來——那是兩回事，處理方式也不同：
    額度要換金鑰或等重置，負載過高要換模型。
    """
    if status == 429:
        return False
    low = (message or "").lower()
    if status == 503:
        return True
    return any(k in low for k in OVERLOAD_HINTS)


def _is_daily_quota(message: str) -> bool:
    low = (message or "").lower()
    return any(k in low for k in ("perday", "per day", "per_day", "daily"))


def _quota_summary(message: str) -> str:
    found = re.findall(
        r"Quota exceeded for metric: (?:[\w.-]+/)?([\w.-]+), limit: (\d+)(?:, model: ([\w.-]+))?",
        message or "")
    return "；".join(f"{metric}, limit: {limit}" + (f", model: {model}" if model else "")
                     for metric, limit, model in found)


def _output_text(result) -> str:
    text = getattr(result, "output_text", None)
    if text:
        return text
    parts = []
    for step in getattr(result, "steps", None) or []:
        if getattr(step, "type", None) != "model_output":
            continue
        for content in getattr(step, "content", None) or []:
            if getattr(content, "type", None) == "text":
                parts.append(getattr(content, "text", "") or "")
    return "".join(parts)


class Transcriber:
    """用 Gemini 把一支 YouTube 影片聽打成逐字稿。

    額度是「每把金鑰 × 每個模型」分開算的，所以多備幾把金鑰、設一個備援
    模型，就能在免費額度內處理完一整集。
    """

    def __init__(self, keys, model=GEMINI_MODEL, fallback=None,
                 segment_minutes=SEGMENT_MINUTES, fps=VIDEO_FPS, probe=True):
        try:
            from google import genai
        except ImportError:
            raise SystemExit(
                "沒有安裝 google-genai。請執行：pip install google-genai")
        self.keys = list(keys) or [""]
        self.clients = []
        for k in self.keys:
            c = genai.Client(api_key=k or "dummy",
                             http_options={"retry_options": {"attempts": 1}})
            # SDK 自己的重試會把「換下一把金鑰」拖慢好幾分鐘，關掉自己控。
            if hasattr(c, "interactions") and hasattr(c.interactions, "sdk_configuration"):
                c.interactions.sdk_configuration.retry_config = None
            self.clients.append(c)
        chain = list(GEMINI_FALLBACK_MODELS if fallback is None else
                     ([fallback] if isinstance(fallback, str) else list(fallback)))
        self.models = [model] + [m for m in chain if m and m != model]
        self.model_index = 0
        self.key_index = 0
        self.segment_seconds = max(segment_minutes, 5) * 60
        self.fps = fps
        self.probe_enabled = probe
        self.exhausted = set()          # (金鑰編號, 模型) 額度已用完
        self.overloaded = set()         # 這一輪負載過高的模型
        self.stream_models = set()      # 不支援 background 的模型，改用串流
        self.healthy_until = {}
        self.models_used = []
        self.keys_used = []

    @property
    def model(self):
        return self.models[self.model_index]

    @property
    def client(self):
        return self.clients[self.key_index]

    # ---- 對外 ---- #
    def transcribe(self, video_url: str, duration_sec: int, cache=None) -> str:
        """把整支影片聽打成逐字稿。

        cache：跨次重試共用的「已完成片段」。一集切成 2～3 段，如果第 1 段成功、
        第 2 段遇到模型壅塞，沒有快取的話下一輪會把第 1 段整個重做——那既浪費
        額度，也讓每一輪都更容易再撞上壅塞。有了它，重試只補還沒完成的那幾段。
        """
        self.models_used, self.keys_used = [], []
        done = cache if cache is not None else {}
        sections = []
        total = max(1, (duration_sec + self.segment_seconds - 1) // self.segment_seconds)
        for n, start in enumerate(range(0, duration_sec, self.segment_seconds), 1):
            end = min(start + self.segment_seconds, duration_sec)
            key = (start, end)
            if key in done:
                log(f"  第 {n}/{total} 段　{hms(start)}–{hms(end)}　沿用上一輪已完成的結果")
                sections.append(done[key])
                continue
            log(f"  第 {n}/{total} 段　{hms(start)}–{hms(end)}")
            if time.monotonic() - RUN_STARTED > TIME_BUDGET - 60:
                raise NotReadyYet('本輪時間不足，已完成片段保留，下一棒接續')
            text = self._range(video_url, start, end)
            block = f"【{hms(start)} – {hms(end)}】\n{text.strip()}"
            done[key] = block
            sections.append(block)
        return "\n\n".join(sections)

    def summary(self) -> str:
        return (f"模型 {'、'.join(self.models_used) or self.model}"
                f"　金鑰 #{'、#'.join(str(k) for k in self.keys_used) or 1}")

    def _timeout(self):
        left = TIME_BUDGET - (time.monotonic() - RUN_STARTED) - 30
        if left <= 0:
            raise NotReadyYet('本輪時間預算用盡，已完成片段保留')
        return max(1, min(SEGMENT_TIMEOUT, left))

    # ---- 內部 ---- #
    def _available(self):
        return [i for i in range(len(self.keys)) if (i, self.model) not in self.exhausted]

    def _next_model(self, why: str) -> bool:
        """換下一個還能用的模型。沒得換就回 False。"""
        old = self.model
        for nxt in range(self.model_index + 1, len(self.models)):
            if self.models[nxt] in self.overloaded:
                continue
            if all((k, self.models[nxt]) in self.exhausted for k in range(len(self.keys))):
                continue
            self.model_index = nxt
            self.key_index = (self._available() or [0])[0]
            log(f"  {old} {why}，改用 {self.model}")
            return True
        return False

    def _range(self, video_url, start, end) -> str:
        while True:
            try:
                text, complete = self._request(video_url, start, end)
                break
            except QuotaExhausted as e:
                # 這個模型所有金鑰都沒額度了。額度是每個模型分開算的，換模型有用。
                if not self._next_model(f"所有金鑰額度用完（{e}）"):
                    raise
            except ModelOverloaded as e:
                # 負載過高。壅塞的是模型本身，所有金鑰共用同一份容量，
                # 換金鑰不可能有用——2026/09/17 就是這樣白燒了 11 分鐘。
                self.overloaded.add(self.model)
                if not self._next_model(f"負載過高（{e}）"):
                    # 每個模型都在忙。這是暫時的，交給外層的輪詢迴圈三分鐘後再試，
                    # 比在這裡繼續重送有用得多——而且不會把這一集判成失敗。
                    raise NotReadyYet(
                        f"所有模型（{'、'.join(self.models)}）目前都負載過高。"
                        f"這是 Google 端的暫時壅塞，等下一輪重試。")

        if self.model not in self.models_used:
            self.models_used.append(self.model)
        if self.key_index + 1 not in self.keys_used:
            self.keys_used.append(self.key_index + 1)

        if complete:
            return text
        if end - start < MIN_SPLIT_SECONDS * 2:
            log(f"  {hms(start)}–{hms(end)} 輸出被截斷且無法再切分，保留已取得的內容")
            return text
        mid = (start + end) // 2
        log(f"  {hms(start)}–{hms(end)} 輸出被截斷，切成兩段重做")
        return self._range(video_url, start, mid) + "\n\n" + self._range(video_url, mid, end)

    def _request(self, video_url, start, end):
        """送出一段影片並等結果，回傳 (逐字稿, 是否完整)。

        失敗處理，依「這個失敗是誰造成的」分流：
          模型負載過高　→ ModelOverloaded，立刻換模型。**不換金鑰。**
                          壅塞的是模型，所有金鑰共用同一份容量。
          額度用完　　　→ 429／每日額度 → 標記這把金鑰在這個模型用完，換下一把；
                          全部用完就 QuotaExhausted，由上層換模型。
          其他可重試　　→ 400、逾時、連線錯誤 → 換下一把金鑰重送
          401/404/非額度的 403 → 直接失敗，重試沒有用

        fps：實測帶 fps 的片段可能一直回 400「invalid argument」，不帶就成功，
        所以帶 fps 失敗後這一段改成不帶重送。
        """
        use_fps = self.fps > 0
        prompt = (f"請聽打這段影片 {hms(start)} 到 {hms(end)} 的完整逐字稿。\n"
                  f"可能出現的專有名詞：{VOCABULARY}")
        multi = len(self.keys) > 1
        attempt = 0
        last_error = ""
        quota_rounds = 0

        for round_no in range(1, MAX_ROUNDS + 1):
            if round_no > 1:
                wait = RETRY_WAIT[min(round_no - 2, len(RETRY_WAIT) - 1)]
                log(f"    {'所有金鑰這一輪都失敗，' if multi else ''}{wait} 秒後重送")
                time.sleep(wait)

            keys = self._available()
            if not keys:
                raise QuotaExhausted(f"所有金鑰在 {self.model} 的額度都已用完：{last_error}")
            first = keys.index(self.key_index) if self.key_index in keys else 0
            order = keys[first:] + keys[:first]

            all_quota = True
            for pos, key in enumerate(order):
                if (key, self.model) in self.exhausted:
                    continue
                self.key_index = key
                tag = f"，金鑰 #{key + 1}" if multi else ""

                ok, why, is_quota = self._probe(key)
                if not ok:
                    last_error = f"金鑰 #{key + 1} 快速檢查未通過（{self.model}）：{why}"
                    log(f"    {last_error}")
                    if not is_quota:
                        all_quota = False
                    continue

                attempt += 1
                processing = {"type": "static",
                              "start_offset": f"{start}s", "end_offset": f"{end}s"}
                if use_fps:
                    processing["fps"] = self.fps
                request = {
                    "model": self.model,
                    "system_instruction": SYSTEM_INSTRUCTION,
                    "input": [
                        {"type": "video", "uri": video_url,
                         "processing": processing, "resolution": "low"},
                        {"type": "text", "text": prompt},
                    ],
                    "generation_config": {"thinking_level": "low",
                                          "max_output_tokens": 65536},
                }
                log(f"    送出 {hms(start)}–{hms(end)}（第 {attempt} 次{tag}"
                    f"{('，fps=' + str(self.fps)) if use_fps else ''}）")
                try:
                    result = self._run(request)
                except Exception as exc:
                    status, message = _err(exc)
                    low = message.lower()
                    is_quota_403 = status == 403 and any(
                        k in low for k in ("quota", "exhausted", "ratelimit", "rate_limit"))
                    if status in NON_RETRYABLE and not is_quota_403:
                        raise
                    if _is_overload(status, message):
                        # 換金鑰沒有用，馬上把這個模型讓出去。
                        raise ModelOverloaded(message[:200])
                    if status == 429 or is_quota_403:
                        detail = _quota_summary(message) or message[:300]
                        if _is_daily_quota(message) or is_quota_403:
                            self.exhausted.add((key, self.model))
                    else:
                        all_quota = False
                        detail = message[:300]
                        if status == 400 and use_fps:
                            use_fps = self._drop_fps(message)
                    last_error = f"HTTP {status}：{detail}"
                    log(f"    失敗　{last_error}")
                    continue

                # 失敗不一定是拋例外。負載過高時 API 常常是「正常回應、
                # status=failed、錯誤訊息放在 errors 裡」——2026/09/17 那次就是
                # 走這條路，所以這裡也要認 high demand，否則會一路輪金鑰到放棄。
                status = str(getattr(result, "status", "") or "")
                errors = getattr(result, "errors", None)
                text = _output_text(result)
                if status in ("failed", "cancelled") or not text.strip():
                    detail = str(errors) if errors else (status or "無輸出")
                    if _is_overload(None, detail):
                        raise ModelOverloaded(detail[:200])
                    all_quota = False
                    last_error = f"狀態 {status or '無輸出'}：{detail[:200]}"
                    log(f"    失敗　{last_error}")
                    if use_fps:
                        use_fps = self._drop_fps(detail)
                    continue

                self.healthy_until[(key, self.model)] = time.monotonic() + 300
                complete = status not in ("incomplete", "budget_exceeded", "max_tokens")
                return text, complete

            if all_quota:
                quota_rounds += 1
                if quota_rounds >= 2:
                    raise QuotaExhausted(f"連續兩輪所有金鑰都回額度錯誤：{last_error}")
            else:
                quota_rounds = 0

        raise RuntimeError(f"Gemini 轉錄 {hms(start)}–{hms(end)} 重試 {attempt} 次仍失敗。"
                           f"最後錯誤：{last_error}")

    def _drop_fps(self, message) -> bool:
        if "fps" in str(message).lower():
            log(f"    API 不接受 fps={self.fps}，之後所有片段改用預設取樣")
            self.fps = 0
        else:
            log("    這一段改成不指定 fps 重送")
        return False

    def _run(self, request):
        """送出請求並取得結果。

        長影片處理久，預設走背景模式再輪詢，避免連線逾時。
        部分模型（實測 gemini-3.5-flash-lite）不支援背景，會立刻回 HTTP 400
        「does not support background interactions」——那種請求沒被處理、不佔額度，
        所以直接改用串流重送，不算一次重試，之後這個模型都走串流。
        """
        if self.model not in self.stream_models:
            try:
                interaction = self.client.interactions.create(**request, background=True, timeout=self._timeout())
            except Exception as exc:
                status, message = _err(exc)
                if not (status == 400 and "does not support background" in message.lower()):
                    raise
                self.stream_models.add(self.model)
                log(f"    {self.model} 不支援背景模式，改用串流")
            else:
                return self._wait(interaction)
        return self._run_stream(request)

    def _wait(self, interaction):
        """背景模式：輪詢到結束。"""
        deadline = time.monotonic() + self._timeout()
        while str(getattr(interaction, "status", "")) in ("queued", "in_progress"):
            if time.monotonic() > deadline:
                # 放棄前先取消，否則背景工作還在跑、還在計額度。
                try:
                    self.client.interactions.cancel(id=interaction.id)
                except Exception:
                    pass
                raise TimeoutError(f"等待 Gemini 超過 {SEGMENT_TIMEOUT // 60} 分鐘")
            time.sleep(POLL_SECONDS)
            interaction = self.client.interactions.get(id=interaction.id, timeout=self._timeout())
        return interaction

    def _run_stream(self, request):
        """串流模式：連線開著，邊收邊累積文字，直到 interaction.completed。

        事件的形狀不能猜，這是實際的樣子：
            event.event_type == "step.delta"          → event.delta.text 才是文字
            event.event_type == "interaction.completed" → event.interaction.status 才是最終狀態
            event.event_type == "error"                → event.error
        我第一版寫成讀 event.delta 與 event.status，結果一個字都沒收到，
        於是每一段都被判成「無輸出」而失敗（2026/09/19 實測）。

        另外長影片處理期間連線會被中途切斷（實測約 4 分半後
        「peer closed connection without sending complete message body」）。
        這時用 interaction ID ＋ 最後一個 event_id 從中斷處接續，
        已收到的文字保留、不重送影片、不多花額度。
        """
        client = self.client
        stream = client.interactions.create(**request, stream=True, timeout=self._timeout())
        deadline = time.monotonic() + self._timeout()
        parts, completed = [], None
        interaction_id = last_event_id = None
        resumes = 0

        while True:
            disconnect = None
            try:
                for event in stream:
                    if time.monotonic() > deadline:
                        raise NotReadyYet('本輪聽打時間已到，已完成片段保留，下輪接續')
                    ev_id = getattr(event, "event_id", None)
                    if ev_id:
                        last_event_id = ev_id
                    kind = getattr(event, "event_type", None)
                    if kind == "interaction.created":
                        interaction_id = getattr(
                            getattr(event, "interaction", None), "id", None) or interaction_id
                    elif kind == "step.delta":
                        delta = getattr(event, "delta", None)
                        if getattr(delta, "type", None) == "text":
                            parts.append(getattr(delta, "text", "") or "")
                    elif kind == "interaction.completed":
                        completed = getattr(event, "interaction", None)
                    elif kind == "error":
                        return _StreamResult(status="failed", output_text="",
                                             errors=getattr(event, "error", None))
            except Exception as exc:
                if isinstance(exc, NotReadyYet):
                    if interaction_id:
                        try:
                            client.interactions.cancel(id=interaction_id, timeout=10)
                        except Exception:
                            pass
                    raise
                status, message = _err(exc)
                # 只有「連線中斷」（沒有 HTTP 狀態碼）而且拿得到 interaction ID 才接續
                if status is not None or not interaction_id or resumes >= STREAM_RESUME_ATTEMPTS:
                    raise
                disconnect = message
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()

            if disconnect is None:
                break
            resumes += 1
            log(f"    串流中斷（{disconnect[:100]}），{STREAM_RESUME_WAIT} 秒後從中斷處接續（第 {resumes} 次）")
            time.sleep(STREAM_RESUME_WAIT)
            if last_event_id is None:
                parts.clear()      # 還沒收到可定位的事件：從頭收，避免文字重複
            stream = client.interactions.get(id=interaction_id, stream=True,
                                             last_event_id=last_event_id,
                                             timeout=self._timeout())

        if completed is None:
            raise RuntimeError("串流在收到完成事件前就結束了")
        text = "".join(parts) or _output_text(completed)
        return _StreamResult(status=str(getattr(completed, "status", "")),
                             output_text=text,
                             errors=getattr(completed, "errors", None))

    def _probe(self, key):
        """送一個極小的文字請求，幾秒內確認這把金鑰＋模型現在可用。

        轉錄一段影片要 1～3 分鐘，額度或金鑰問題常常要等到最後才回報。
        先探一下就能提早換金鑰，省好幾分鐘。通過的金鑰 300 秒內不重複檢查。

        但要知道它的限制：**探得過不代表大請求會成功。**
        小文字請求幾乎不佔容量，模型忙不過來時它照樣 0.7 秒回 OK，
        接著同一把金鑰送 30 分鐘影片就 high demand。所以探測只用來擋掉
        「金鑰壞了／沒額度」，容量問題要靠 _request 那邊認。

        探測本身逾時則是另一回事：連 16 個 token 都回不了，代表整個服務在塞，
        那要當成模型負載過高（丟 ModelOverloaded 換模型），而不是
        「這把金鑰不行」——後者會讓程式繼續在同一個塞住的模型上輪金鑰。
        """
        if not self.probe_enabled:
            return True, "", False
        if self.healthy_until.get((key, self.model), 0) > time.monotonic():
            return True, "", False
        try:
            self.clients[key].interactions.create(
                model=self.model, input=PROBE_PROMPT,
                generation_config={"max_output_tokens": 16},
                store=False, timeout=PROBE_TIMEOUT)
        except Exception as exc:
            status, message = _err(exc)
            low = message.lower()
            if status == 404:
                raise                     # 模型不存在，換金鑰也沒用
            is_quota = status == 429 or (status == 403 and any(
                k in low for k in ("quota", "exhausted", "ratelimit", "rate_limit")))
            if status in (401, 403) and not is_quota:
                self.exhausted.add((key, self.model))
                return False, f"金鑰無效或沒有權限（HTTP {status}）", False
            if is_quota:
                if _is_daily_quota(message) or status == 403:
                    self.exhausted.add((key, self.model))
                return False, f"額度或速率限制（HTTP {status}）", True
            if _is_overload(status, message) or "timed out" in low or "timeout" in low:
                raise ModelOverloaded(
                    f"快速檢查 {PROBE_TIMEOUT} 秒內沒有回應（{message[:120]}）")
            return False, f"HTTP {status}：{message[:150]}", False
        self.healthy_until[(key, self.model)] = time.monotonic() + 300
        return True, "", False


@dataclass
class _StreamResult:
    status: str
    output_text: str
    errors: object = None


# ---------------------------------------------------------------- #
# 一次探詢
# ---------------------------------------------------------------- #
# 已經聽打完成的片段，以影片 ID 分組。只活在這一次執行的記憶體裡：
# 輪詢迴圈每 3 分鐘重試一次，靠它避免把成功的片段一再重做。
# 換一次排程觸發就從頭開始，那是可以接受的——真正貴的是同一輪裡的重複。
_SEGMENT_CACHE = {}


def tick(ss, target: date, force: bool) -> bool:
    """敲一次門。

    回 True 代表這一輪真的寫進去了；回 False 代表還沒好，等下一輪。
    已經不必再做的情況一律丟 Done，由呼叫端收掉並結束整個迴圈。
    """
    date_str = target.strftime("%Y/%m/%d")

    # ---- 第一關：試算表。最便宜，而且手動優先一定要排在最前面 ----
    #
    # 順序不能反。先去 YouTube、先呼叫 Gemini 的話，人已經貼好稿的日子
    # 還是會白花額度，甚至可能在寫入那一刻覆蓋掉人工成果。
    ws, header, idx, row = read_state(ss, "", date_str)
    src = source_of(row)
    raw = str(row.get(COL_RAW) or "").strip()

    if src in (SRC_MANUAL, SRC_HOLD):
        if raw:
            raise Done(f"{date_str} 已有手動輸入的逐字稿 {len(raw)} 字"
                       f"（第 {idx} 列，來源「{src}」）。手動優先，自動化停止，不覆蓋。")
        raise Done(f"{date_str} 已標記為「{src}」，等人工貼稿。自動化停止，不介入。")

    if raw and len(raw) >= MIN_TRANSCRIPT:
        raise Done(f"{date_str} 已有逐字稿 {len(raw)} 字"
                   f"（第 {idx} 列，來源「{src or '未標記'}」），不重抓。")

    # ---- 第二關：YouTube。當天那一集出現了沒有、回放好了沒有 ----
    video = find_video(target)
    if not video:
        log(f"{date_str} 的影片還沒出現在頻道清單上（標題要含 {TITLE_KEYWORDS}）。")
        return False

    now = datetime.now(TAIPEI)
    log(f"對到影片：{video.title}")
    log(f"　{video.url}　片長 {hms(video.duration_sec)}　狀態：{video.status_text(now)}")

    if not video.detailed:
        # RSS 沒有片長與結束時間，判斷不了回放好了沒。硬送給 Gemini 很可能
        # 拿到半截，而那種半截還會被寫進試算表，比等下一輪糟得多。
        raise Done(
            f"{date_str} 只能用 RSS，查不到片長與直播結束時間，無法判斷回放是否就緒。"
            f"請設定 YOUTUBE_API_KEY 之後再跑。", code=1)

    if not video.is_ready(now) and not force:
        raise NotReadyYet(video.status_text(now))
    if not video.is_ready(now):
        log("（--force：略過回放就緒檢查，直接送出）")
    if video.duration_sec <= 0:
        raise NotReadyYet("YouTube 還沒算出片長，切段會算錯，等下一輪")

    # ---- 第三關：聽打 ----
    keys = gemini_keys()
    if not keys:
        raise Done("缺少 GEMINI_API_KEY。到 https://aistudio.google.com/apikey 建一把，"
                   "多把可用逗號分隔或用 GEMINI_API_KEY_2、_3 追加。", code=1)

    log(f"Gemini 聽打　模型 {GEMINI_MODEL}"
        + (f"（備援 {'、'.join(GEMINI_FALLBACK_MODELS)}）" if GEMINI_FALLBACK_MODELS else "")
        + f"　金鑰 {len(keys)} 把　每段 {SEGMENT_MINUTES} 分鐘")

    # 已完成的片段留著跨輪重用。一集切 2～3 段，若第 1 段成功、第 2 段撞上
    # 模型壅塞，沒有這個快取的話下一輪會把第 1 段整個重做——既浪費額度，
    # 也讓每一輪都更容易再撞上壅塞。
    cache = segment_cache_for(video)
    tr = Transcriber(keys)
    try:
        text = tr.transcribe(video.url, video.duration_sec, cache=cache)
    except NotReadyYet:
        raise            # 模型都在忙，交給輪詢迴圈三分鐘後再試
    except QuotaExhausted:
        raise            # 額度用完，由 cmd_auto 處理並給明確指示
    except Exception as e:
        # 其他失敗多半也是暫時的（連線、單段逾時）。判成「還沒好」讓下一輪重試，
        # 比讓整個工作亮紅燈有用——下一輪還會沿用已經完成的片段。
        done = len(cache)
        raise NotReadyYet(
            f"{type(e).__name__}: {str(e)[:200]}"
            + (f"（已完成 {done} 段，下一輪會接續）" if done else ""))

    if len(text.strip()) < MIN_TRANSCRIPT:
        raise NotReadyYet(f"聽打結果只有 {len(text.strip())} 字，不像完整的一份，等下一輪")

    save_transcript(ss, {"id": video.id, "title": video.title},
                    date_str, text, SRC_AUTO, note=tr.summary())
    write_status_log(ss, "逐字稿自動取得",
                     f"{date_str} {len(text)} 字　{tr.summary()}　{video.title}")
    return True


# ---------------------------------------------------------------- #
# 子指令
# ---------------------------------------------------------------- #
def cmd_auto(args) -> int:
    target = args.date or datetime.now(TAIPEI).date()
    date_str = target.strftime("%Y/%m/%d")
    log(f"自動抓取　目標日期 {date_str}　頻道 {CHANNEL_ID}")

    closed = why_closed(target)
    if closed and not args.force:
        # 國定假日休市時沒有盤中直播。不擋的話這一天會一路空跑到 14:00，
        # 還會在系統狀態留下「敲了 N 次仍未取得」，看起來像壞掉。
        log(f"{closed}，沒有盤中直播，不執行。（要硬跑請加 --force）")
        return 0

    now = datetime.now(TAIPEI)
    start, until = at_taipei(target, POLL_START), at_taipei(target, POLL_UNTIL)
    if not args.force:
        if now < start:
            log(f"現在 {now:%H:%M}，還沒到 {POLL_START}，不執行。")
            return 0
        if now > until:
            log(f"現在 {now:%H:%M}，已過 {POLL_UNTIL}，今天不再探詢。")
            return 0

    ss = open_sheets()
    round_no = 0
    try:
        while True:
            round_no += 1
            log("")
            log(f"─── 第 {round_no} 次探詢 ───")
            try:
                if tick(ss, target, args.force):
                    log("逐字稿已寫進試算表。後面的潤飾與擷取由 pipeline.py 接手。")
                    return 0
            except NotReadyYet as e:
                log(f"還沒好：{e}")

            if args.once:
                log("單次模式，不等下一輪。")
                return 0

            now = datetime.now(TAIPEI)
            spent = time.monotonic() - RUN_STARTED
            if not args.force and now + timedelta(seconds=POLL_INTERVAL) > until:
                log(f"下一輪會超過 {POLL_UNTIL}，本次收工。")
                break
            if spent + POLL_INTERVAL > TIME_BUDGET:
                log(f"已跑 {spent / 60:.1f} 分鐘，接近 {TIME_BUDGET / 60:.0f} 分鐘預算，"
                    f"本次收工，交給下一次排程接力。")
                break
            log(f"等 {POLL_INTERVAL} 秒再敲一次。")
            time.sleep(POLL_INTERVAL)

        write_status_log(ss, "逐字稿等待中", f"{date_str} 敲了 {round_no} 次仍未取得")
        return 0

    except Done as e:
        log(e.reason)
        return e.code
    except QuotaExhausted as e:
        log("")
        log(f"::error::Gemini 額度用完：{e}")
        log("重試沒有用。三個選項：")
        log("  1. 等額度重置（免費方案每日額度在太平洋時間午夜重置，約台灣下午 3～4 點）")
        log("  2. 到 https://aistudio.google.com/apikey 用別的 Google 帳號再建一把，"
            "以逗號接在 GEMINI_API_KEY 後面")
        log("  3. 設 GEMINI_FALLBACK_MODEL 換一個模型繼續（額度每個模型分開算）")
        write_status_log(ss, "Gemini額度用完", str(e)[:400])
        return 2


def cmd_manual(args) -> int:
    """手動輸入。寫進去之後自動化就不會再碰這一天。"""
    target = args.date or datetime.now(TAIPEI).date()
    date_str = target.strftime("%Y/%m/%d")

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    elif args.text:
        text = args.text
    else:
        log("從標準輸入讀取逐字稿，貼完按 Ctrl+Z（Windows）或 Ctrl+D（Mac/Linux）結束：")
        text = sys.stdin.read()

    text = (text or "").strip()
    if len(text) < MIN_TRANSCRIPT:
        log(f"只讀到 {len(text)} 字，低於 {MIN_TRANSCRIPT} 字門檻，不寫入。"
            f"請確認貼上的是完整的一份。")
        return 1

    ss = open_sheets()
    video = {"id": args.video_id or "", "title": args.title or ""}
    if not video["id"]:
        found = None
        try:
            found = find_video(target)
        except Exception as e:
            log(f"（對影片失敗，改用人工編號：{e}）")
        if found:
            video = {"id": found.id, "title": found.title}
            log(f"對到影片：{found.title}")
        else:
            # 前綴與 Apps Script 的 MANUAL_ENTRY_PREFIX 一致，
            # 下游靠這個前綴認出「這一列不准被排程清掉」。
            video = {"id": "MANUALENTRY-" + target.strftime("%Y%m%d"),
                     "title": args.title or f"{date_str} 人工補登"}

    save_transcript(ss, video, date_str, text, SRC_MANUAL)
    write_status_log(ss, "逐字稿手動輸入", f"{date_str} {len(text)} 字")
    log(f"{date_str} 已標記為「{SRC_MANUAL}」。自動抓取從現在起不會再碰這一天。")
    return 0


def cmd_hold(args) -> int:
    """宣告「今天我自己貼」。還沒貼，但先把自動化擋掉。"""
    target = args.date or datetime.now(TAIPEI).date()
    date_str = target.strftime("%Y/%m/%d")
    ss = open_sheets()
    ws, header, idx, row = read_state(ss, "", date_str)

    if args.release:
        if idx is None:
            log(f"{date_str} 在「{VIDEO_SHEET}」沒有對應的列，沒有東西要解除。")
            return 0
        if source_of(row) == SRC_MANUAL and str(row.get(COL_RAW) or "").strip():
            log(f"{date_str} 已經有手動輸入的逐字稿，解除保留不會讓自動化覆蓋它。"
                f"真要重抓請先自己清掉第 {idx} 列的「{COL_RAW}」與「{COL_SOURCE}」。")
            return 1
        a1 = col_a1(header, COL_SOURCE, idx)
        if a1:
            sheets_retry(ws.update, range_name=a1, values=[[""]], value_input_option="RAW")
        log(f"{date_str} 的手動保留已解除，自動抓取恢復。")
        return 0

    if idx is None:
        sheets_retry(ws.append_row,
                     [{COL_DATE: date_str, COL_STATUS: "等待人工貼稿",
                       COL_SOURCE: SRC_HOLD}.get(h, "") for h in header],
                     value_input_option="RAW", table_range="A1")
        log(f"{date_str} 已新增一列並標記「{SRC_HOLD}」。")
    else:
        a1 = col_a1(header, COL_SOURCE, idx)
        if not a1:
            log(f"「{VIDEO_SHEET}」沒有「{COL_SOURCE}」欄，無法標記。")
            return 1
        sheets_retry(ws.update, range_name=a1, values=[[SRC_HOLD]], value_input_option="RAW")
        log(f"{date_str} 第 {idx} 列已標記「{SRC_HOLD}」。")
    log("自動抓取從現在起不會碰這一天，直到你用 manual 貼稿或 hold --release 解除。")
    return 0


def cmd_status(args) -> int:
    target = args.date or datetime.now(TAIPEI).date()
    date_str = target.strftime("%Y/%m/%d")
    ss = open_sheets()
    ws, header, idx, row = read_state(ss, "", date_str)
    raw = str(row.get(COL_RAW) or "")
    src = source_of(row)

    log(f"日期　　　{date_str}（{'平日' if target.weekday() < 5 else '週末'}）")
    log(f"試算表　　{'第 ' + str(idx) + ' 列' if idx else '尚無對應的列'}")
    log(f"影片ID　　{row.get(COL_ID) or '（空）'}")
    log(f"標題　　　{row.get(COL_TITLE) or '（空）'}")
    log(f"逐字稿　　{len(raw)} 字" + (f"　SHA256={sha256(raw)}" if raw else ""))
    log(f"來源　　　{src or '（未標記）'}")
    log(f"修飾稿　　{len(str(row.get(COL_POLISHED) or ''))} 字")
    log(f"更新時間　{row.get(COL_UPDATED) or '（空）'}")
    log("")
    if src in (SRC_MANUAL, SRC_HOLD):
        log(f"→ 自動化對這一天停手（來源是「{src}」，手動優先）。")
    elif len(raw) >= MIN_TRANSCRIPT:
        log("→ 已經有稿，自動化不重抓。")
    else:
        log(f"→ 自動化會在平日 {POLL_START}～{POLL_UNTIL} 每 {POLL_INTERVAL} 秒探詢一次。")

    log("")
    try:
        v = find_video(target)
        if not v:
            log("頻道　　　這一天還沒有符合的影片")
        else:
            now = datetime.now(TAIPEI)
            log(f"頻道　　　{v.title}")
            log(f"　　　　　{v.url}")
            log(f"片長　　　{hms(v.duration_sec)}"
                + (f"　約需 {max(1, (v.duration_sec + SEGMENT_MINUTES * 60 - 1) // (SEGMENT_MINUTES * 60))} 段"
                   if v.duration_sec else ""))
            log(f"回放狀態　{v.status_text(now)}")
    except Exception as e:
        log(f"頻道　　　查詢失敗：{e}")

    log("")
    keys = gemini_keys()
    log(f"Gemini　　金鑰 {len(keys)} 把　模型 {GEMINI_MODEL}"
        + (f"　備援 {GEMINI_FALLBACK_MODEL}" if GEMINI_FALLBACK_MODEL else ""))
    if not keys:
        log("　　　　　::warning::沒有 GEMINI_API_KEY，自動抓取跑不起來。")
    return 0


def _list_models(tr):
    """列出這把金鑰實際看得到的模型。

    設 GEMINI_FALLBACK_MODEL 時最怕的是填一個不存在的名字：API 回 404，
    而 404 是不重試的硬失敗，備援反而變成必定失敗的一步。
    所以 check 順便把真正可用的清單印出來，照著挑就不會填錯。
    """
    try:
        models = tr.clients[0].models.list()
    except Exception as e:
        log(f"（列不出模型清單，不影響上面的檢查：{type(e).__name__}: {str(e)[:120]}）")
        return
    names = []
    for m in models:
        name = str(getattr(m, "name", "") or "").replace("models/", "")
        if name:
            names.append(name)
    gen = sorted(n for n in names if "embedding" not in n and "aqa" not in n)
    if not gen:
        return
    log(f"這把金鑰可用的模型（{len(gen)} 個）：")
    for n in gen:
        mark = ""
        if n == GEMINI_MODEL:
            mark = "　← 目前主模型"
        elif n in GEMINI_FALLBACK_MODELS:
            mark = "　← 目前備援"
        log(f"  {n}{mark}")
    if not GEMINI_FALLBACK_MODELS:
        log("")
        log("目前沒有設備援模型。從上面挑一個與主模型不同的，設成 GEMINI_FALLBACK_MODEL，")
        log("主模型忙不過來或額度用完時就有退路（額度是每個模型分開算的）。")


def cmd_check(args) -> int:
    """確認 Gemini 金鑰與模型現在可用。只送一個極小的文字請求，幾乎不花額度。"""
    keys = gemini_keys()
    if not keys:
        log("::error::沒有 GEMINI_API_KEY。")
        log("到 https://aistudio.google.com/apikey 建一把（用個人 Gmail，"
            "學校帳號常被管理員擋）。")
        log("多把金鑰可以用逗號分隔，或用 GEMINI_API_KEY_2、GEMINI_API_KEY_3 追加。")
        return 1

    log(f"金鑰 {len(keys)} 把　模型 {GEMINI_MODEL}"
        + (f"　備援 {GEMINI_FALLBACK_MODEL}" if GEMINI_FALLBACK_MODEL else ""))
    log("")
    tr = Transcriber(keys, probe=True)
    bad = 0
    for model_index in range(len(tr.models)):
        tr.model_index = model_index
        log(f"模型 {tr.model}")
        for i in range(len(keys)):
            tr.healthy_until.pop((i, tr.model), None)
            try:
                ok, why, _ = tr._probe(i)
            except Exception as e:
                ok, why = False, f"{type(e).__name__}: {str(e)[:150]}"
            log(f"  金鑰 #{i + 1}　{'可用' if ok else '不可用　' + why}")
            if not ok:
                bad += 1
    log("")
    _list_models(tr)
    log("")
    if bad == 0:
        log("全部可用。")
        return 0
    if bad >= len(keys) * len(tr.models):
        log("::error::沒有任何一組可用，自動抓取一定會失敗。")
        return 1
    log("::warning::有部分不可用，仍可運作（程式會自動跳過不可用的那幾把）。")
    return 0


def parse_date(s: str) -> date:
    s = (s or "").strip()
    if s.lower() in ("today", "今天", ""):
        return datetime.now(TAIPEI).date()
    m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$", s)
    if not m:
        raise argparse.ArgumentTypeError(f"日期要寫成 YYYY/MM/DD，收到「{s}」")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def build_parser():
    p = argparse.ArgumentParser(
        prog="transcript.py",
        description="每日逐字稿抓取：Gemini 直接聽 YouTube，手動優先，結果寫進雲端試算表。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例
  python transcript.py auto                         今天，照排程規則跑（預設）
  python transcript.py auto --once                  只敲一次就走，不等下一輪
  python transcript.py auto --date 2026/09/18 --force
                                                    指定日期、無視平日與時段限制
  python transcript.py manual --file 稿.txt          手動貼稿，之後自動化不再碰這天
  python transcript.py hold                         先擋住今天的自動化，稍後自己貼
  python transcript.py hold --release                解除上面的保留
  python transcript.py status                       看今天目前是什麼狀態
  python transcript.py check                        Gemini 金鑰與模型現在可用嗎

需要的環境變數
  GEMINI_API_KEY                必要。可逗號分隔多把，或用 GEMINI_API_KEY_2、_3 追加
  YOUTUBE_API_KEY               必要。要靠它判斷回放處理好了沒
  SPREADSHEET_ID                必要
  GOOGLE_SHEETS_SERVICE_ACCOUNT 服務帳號 JSON（或其 base64）；跑在 GCP 上可省略改用 ADC
""")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("auto", help="自動抓取（預設）")
    a.add_argument("--date", type=parse_date, help="目標日期 YYYY/MM/DD，預設今天")
    a.add_argument("--once", action="store_true", help="只敲一次，不進輪詢迴圈")
    a.add_argument("--force", action="store_true",
                   help="無視平日／時段／回放就緒的限制，硬跑")
    a.set_defaults(func=cmd_auto)

    m = sub.add_parser("manual", help="手動輸入逐字稿（寫入後自動化停手）")
    m.add_argument("--date", type=parse_date, help="目標日期 YYYY/MM/DD，預設今天")
    m.add_argument("--file", help="從檔案讀（UTF-8）")
    m.add_argument("--text", help="直接把整份逐字稿當參數傳（短稿測試用）")
    m.add_argument("--video-id", default="", help="指定影片ID，不給就自動對")
    m.add_argument("--title", default="", help="指定標題，不給就自動對")
    m.set_defaults(func=cmd_manual)

    h = sub.add_parser("hold", help="標記為手動保留，先擋住自動化")
    h.add_argument("--date", type=parse_date, help="目標日期 YYYY/MM/DD，預設今天")
    h.add_argument("--release", action="store_true", help="改為解除保留")
    h.set_defaults(func=cmd_hold)

    s = sub.add_parser("status", help="看某一天目前的狀態")
    s.add_argument("--date", type=parse_date, help="目標日期 YYYY/MM/DD，預設今天")
    s.set_defaults(func=cmd_status)

    c = sub.add_parser("check", help="確認 Gemini 金鑰與模型可用")
    c.set_defaults(func=cmd_check)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    argv = list(argv if argv is not None else sys.argv[1:])
    # 不給子指令時預設 auto，這樣排程只要寫 `python transcript.py` 就好。
    # -h/--help 例外：那要看的是總覽，不是 auto 那一支的說明。
    if not argv or (argv[0].startswith("-") and argv[0] not in ("-h", "--help")):
        argv = ["auto"] + argv
    args = parser.parse_args(argv)
    for name in ("date", "once", "force"):
        if not hasattr(args, name):
            setattr(args, name, None)
    try:
        return args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log("使用者中斷。")
        return 1
    except Exception as e:
        log(f"::error::執行失敗：{type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
