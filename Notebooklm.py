#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notebooklm.py —— 每日逐字稿抓取（自動 + 手動，手動優先）

這支程式只做一件事：把「當天那一集直播的逐字稿」放進雲端試算表的
「影片清單」分頁。後面的潤飾、擷取、寫網站、寄信全部由既有的
pipeline.py 與 Apps Script 接手，這裡一概不碰。

為什麼要獨立成一支：
    pipeline.py 一萬三千行，裡面把取稿、Gemini、寫表、撰稿綁在一起。
    取稿是最容易壞的一段（cookie 會過期、VOD 要等轉檔），
    把它抽出來獨立排程，壞掉的時候不會拖累其他完全用得到的流程，
    日誌也只剩這一件事，好查。

目標頻道：
    https://www.youtube.com/@xinchenginsta/streams
    頻道 ID：UCPqyYS3n6yyXL2jygauXpzg（@ 開頭的代稱不能直接餵給 API，要用 UC 開頭這組）
    標題格式：2026/09/17(四)張震 股市盤中家教班
              日期一定寫在標題最前面，所以日期以標題為準，不用 published，
              因為直播的 published 是「排程建立時間」，可能早於實際開播日。

兩套機制，手動永遠贏：
    自動　　排程時間到，去 NotebookLM 索引當天影片、取回全文、寫進試算表。
    手動　　人直接把逐字稿貼進去（本程式的 manual 子指令，或後台投稿、或直接編輯試算表）。

    「影片清單」新增一欄「逐字稿來源」記錄那一列是誰寫的。
    只要那一欄是「手動」或「手動保留」，自動化立刻停手，不覆蓋、不重抓。
    這一欄在既有試算表上不存在時，本程式與 Apps Script 的 ensureHeaders_
    都會自動把它補在最後面，不必手動加。

排程（台灣時間，平日）：
    11:20 起每 3 分鐘敲一次門，問「逐字稿拿得到了嗎」，
    拿到就寫進去結束，拿不到就等下一輪，最晚敲到 15:00。

    要有心理準備：直播大約 11:20 開到 13:00，結束後 YouTube 還要花十幾分鐘
    轉出 VOD，NotebookLM 才索引得到。所以 11:20 到 12:50 這段幾乎一定是
    「還沒好」。這段期間本程式只用 YouTube API 問一句「還在直播嗎」，
    還在直播就直接跳過，不去戳 NotebookLM——省額度，也不會在日誌洗版。

退出碼：
    0   有進展或沒事做（寫入成功、已經有稿、手動優先停手、非交易日、還沒到時間）
    1   真的失敗（設定缺漏、試算表寫不進去、NotebookLM 壞掉）
    2   登入憑證失效，要人重新產生 storage_state.json。重試沒有用。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------------- #
# 設定
# ---------------------------------------------------------------- #
TAIPEI = timezone(timedelta(hours=8))

# 頻道。@xinchenginsta 的正式 ID，與 pipeline.py 的 DEFAULT_CHANNEL_ID 相同。
DEFAULT_CHANNEL_ID = "UCPqyYS3n6yyXL2jygauXpzg"
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID

# 標題要含這個關鍵字才算數。頻道偶爾會上其他影片，不能一律當成當天那一集。
TITLE_KEYWORDS = [k for k in os.environ.get(
    "TITLE_KEYWORDS", "盤中家教班").split(",") if k.strip()]

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "").strip()

VIDEO_SHEET = "影片清單"
AUTH_SHEET = "登入憑證"
STATUS_SHEET = "系統狀態"

# 「影片清單」的欄位名稱。程式一律用表頭名稱找欄，不寫死欄號，
# 這樣試算表欄位順序被調動也不會寫錯格。
COL_ID, COL_DATE, COL_TITLE = "影片ID", "發布日期", "標題"
COL_STATUS, COL_REASON = "處理狀態", "失敗原因"
COL_RAW, COL_POLISHED = "原始逐字稿內容", "修飾後逐字稿內容"
COL_UPDATED, COL_SHA, COL_ALIAS = "原文更新時間", "原文SHA256", "來源別名"
COL_SOURCE = "逐字稿來源"          # 本次新增
FULL_HEADER = [COL_ID, COL_DATE, COL_TITLE, COL_STATUS, COL_REASON,
               COL_RAW, COL_POLISHED, COL_UPDATED, COL_SHA, COL_ALIAS, COL_SOURCE]

SRC_MANUAL = "手動"          # 人貼的。自動化永遠不碰。
SRC_HOLD = "手動保留"        # 人宣告「今天我自己貼」，還沒貼。自動化也不碰。
SRC_AUTO = "自動"            # 本程式抓的。

# 輪詢。全部可用環境變數覆寫，方便本機測試不必改程式。
POLL_START = os.environ.get("POLL_START", "11:20").strip()      # 台灣時間，這之前不動作
POLL_UNTIL = os.environ.get("POLL_UNTIL", "15:00").strip()      # 這之後不再敲
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SEC", "180"))  # 每 3 分鐘
TIME_BUDGET = int(os.environ.get("TIME_BUDGET_SEC", "1500"))    # 單次 job 最多跑 25 分鐘
INDEX_TIMEOUT = int(os.environ.get("INDEX_TIMEOUT_SEC", "240"))  # 單次索引等 4 分鐘就放棄

# 直播通常 13:00 前後才結束。這個時間之前先問 YouTube「還在直播嗎」，
# 還在就不去戳 NotebookLM。設成 00:00 可以關掉這個省額度的判斷。
LIVE_GUARD_UNTIL = os.environ.get("LIVE_GUARD_UNTIL", "13:20").strip()

# 取回的全文短於這個字數，視為索引還沒完成，不當成成功。
# 一小時的直播正常在兩萬字以上，200 字一定是殘缺。
MIN_TRANSCRIPT = int(os.environ.get("MIN_TRANSCRIPT", "200"))
SHORT_HINT = 5000            # 低於這個字數會提醒，但仍然收下
SHEET_CELL_LIMIT = 49000     # 試算表單格上限保護

AUTH_PATHS = [
    os.path.expanduser("~/.notebooklm/storage_state.json"),
    os.path.expanduser("~/.notebooklm/profiles/default/storage_state.json"),
]

RUN_STARTED = time.monotonic()
TRANSIENT = (429, 500, 502, 503, 504)

# Windows 的主控台預設是 cp950，中文訊息會整片變成亂碼，
# 於是「登入憑證已失效」這種要人看懂才有用的訊息反而看不懂。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


class NotReadyYet(Exception):
    """VOD 還沒轉好、或 NotebookLM 還在索引。不是錯誤，是還沒輪到。"""


class AuthExpired(Exception):
    """Google web session cookie 失效。重試沒有用，要換一份新的。"""


AUTH_HINTS = (
    "authentication expired", "authentication invalid", "not authenticated",
    "accounts.google.com", "notebooklm login", "re-authenticate",
    "unauthorized", "401", "403", "sign in", "login required",
    "unauthenticated", "status code 16", "rpc_code=16",
    "token refresh failed", "invalid_grant",
    "servicelogin", "weblitesignin", "confirmidentifier",
)


def looks_like_auth_error(e) -> bool:
    m = str(e).lower()
    return any(k in m for k in AUTH_HINTS)


def log(msg: str = ""):
    """所有輸出都帶台北時間。GitHub 介面的時間戳是 UTC，兩邊差八小時，
    不標清楚的話對照日誌會一直算錯。"""
    if not msg:
        print()
        return
    print(f"[{datetime.now(TAIPEI):%H:%M:%S}] {msg}", flush=True)


def parse_hhmm(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2}):(\d{2})$", (s or "").strip())
    if not m:
        raise ValueError(f"時間格式必須是 HH:MM，收到「{s}」")
    return int(m.group(1)), int(m.group(2))


def at_taipei(d: date, hhmm: str) -> datetime:
    h, m = parse_hhmm(hhmm)
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TAIPEI)


def norm_date(v) -> str:
    """把試算表各種日期寫法統一成 yyyy/MM/dd。
    Apps Script 寫回的值有時是 JavaScript Date 字串，那也是完整可用的日期，
    不能因為長得不像數字就丟掉。"""
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


# ---------------------------------------------------------------- #
# 試算表
# ---------------------------------------------------------------- #
def sheets_retry(fn, *args, **kwargs):
    """Google Sheets 偶發 429 / 5xx。429 是每分鐘配額，退避要跨過整個一分鐘窗口
    才有意義，所以最長等到 70 秒，並加抖動避免重試又一起撞上去。"""
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


def open_sheets():
    raw = os.environ.get("GOOGLE_SHEETS_SERVICE_ACCOUNT", "").strip()
    if not raw:
        raise SystemExit("缺少 GOOGLE_SHEETS_SERVICE_ACCOUNT。"
                         "請到 Cloud Console 建服務帳號、下載 JSON 金鑰，"
                         "把整份 JSON 內容放進這個環境變數或 GitHub Secret。")
    if not SPREADSHEET_ID:
        raise SystemExit("缺少 SPREADSHEET_ID。就是試算表網址中 /d/ 與 /edit 之間那一長串。")
    try:
        info = json.loads(raw)
    except Exception as e:
        raise SystemExit(f"GOOGLE_SHEETS_SERVICE_ACCOUNT 不是合法 JSON：{e}")
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    gc = gspread.authorize(creds)
    try:
        return sheets_retry(gc.open_by_key, SPREADSHEET_ID)
    except gspread.exceptions.APIError as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        if code in (403, 404):
            raise SystemExit(
                f"開不了試算表（HTTP {code}）。兩個最常見的原因：\n"
                f"  1. 試算表沒有分享給服務帳號 {info.get('client_email', '（讀不到）')}（要給編輯權限）\n"
                f"  2. Cloud 專案沒有啟用 Google Sheets API\n"
                f"原始訊息：{str(e)[:300]}")
        raise


def video_sheet(ss):
    """取得「影片清單」，順便補齊表頭缺掉的欄位。

    改版新增欄位時，既有的試算表不會自己長出那一欄，程式用表頭名稱找欄會拿到
    -1，然後那一欄安靜地永遠寫不進去，沒有任何錯誤訊息。所以這裡順手補。
    只補「尾巴少掉的那幾欄」；中間對不上時不動，那種情況要人看過，
    自動補只會把欄位整排錯開，比缺一欄糟得多。
    """
    try:
        ws = ss.worksheet(VIDEO_SHEET)
    except gspread.WorksheetNotFound:
        ws = sheets_retry(ss.add_worksheet, title=VIDEO_SHEET,
                          rows=2000, cols=len(FULL_HEADER))
        sheets_retry(ws.update, range_name="A1",
                     values=[FULL_HEADER])
        log(f"已建立「{VIDEO_SHEET}」分頁")
        return ws, list(FULL_HEADER)

    header = [str(h).strip() for h in sheets_retry(ws.row_values, 1)]
    if len(header) >= len(FULL_HEADER):
        return ws, header
    for i, have in enumerate(header):
        if have != FULL_HEADER[i]:
            log(f"注意：「{VIDEO_SHEET}」第 {i + 1} 欄是「{have}」，"
                f"與預期的「{FULL_HEADER[i]}」不同，不自動補欄，請人工確認表頭。")
            return ws, header
    add = FULL_HEADER[len(header):]
    if ws.col_count < len(FULL_HEADER):
        sheets_retry(ws.add_cols, len(FULL_HEADER) - ws.col_count)
    sheets_retry(ws.update,
                 range_name=gspread.utils.rowcol_to_a1(1, len(header) + 1),
                 values=[add])
    log(f"「{VIDEO_SHEET}」補上欄位：{'、'.join(add)}")
    return ws, header + add


def col_a1(header, name, row):
    """欄位名稱換成 A1 位址。找不到就回 None，由呼叫端決定要不要吵。"""
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
        return (bool(video_id and str(r.get(COL_ID) or "").strip() == video_id),
                str(r.get(COL_UPDATED) or ""),
                len(str(r.get(COL_RAW) or "")), i)
    return max(cands, key=rank)


def read_state(ss, video_id, date_str):
    """讀出當天那一列現在長什麼樣。回傳 (ws, header, idx, row)。"""
    ws, header = video_sheet(ss)
    rows = sheets_retry(ws.get_all_records)
    idx, row = pick_row(rows, video_id, date_str)
    return ws, header, idx, (row or {})


def source_of(row) -> str:
    """那一列的逐字稿是誰寫的。

    「逐字稿來源」欄還沒填的舊資料，只要有原文就一律當成手動——
    那一欄是本次才加的，在它之前所有原文都是人貼進去的，
    把它們當成自動就等於允許覆蓋人工成果，那是最不能出的錯。
    """
    src = str(row.get(COL_SOURCE) or "").strip()
    if src:
        return src
    return SRC_MANUAL if str(row.get(COL_RAW) or "").strip() else ""


def write_status_log(ss, kind: str, detail: str = ""):
    """每一輪往「系統狀態」寫一列，Apps Script 讀這張表寄狀態信與告警。
    寫入失敗不可以影響主流程。"""
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


def save_transcript(ss, video, date_str, text, source):
    """把日期與逐字稿寫進「影片清單」。同一天已經有列就更新，沒有就新增一列。"""
    ws, header, idx, row = read_state(ss, video.get("id", ""), date_str)
    now = datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")
    body = cell(text)
    values = {
        COL_ID: video.get("id", "") or str(row.get(COL_ID) or ""),
        COL_DATE: date_str,
        COL_TITLE: video.get("title", "") or str(row.get(COL_TITLE) or ""),
        COL_STATUS: "逐字稿已就緒",
        COL_RAW: body,
        COL_UPDATED: now,
        COL_SHA: sha256(body),
        COL_SOURCE: source,
    }

    if idx is None:
        sheets_retry(ws.append_row,
                     [values.get(h, "") for h in header],
                     value_input_option="RAW",
                     table_range="A1")
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
# 影片偵測
# ---------------------------------------------------------------- #
TITLE_DATE = re.compile(r"(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})")


def date_from_title(title: str, fallback):
    """標題日期優先於 published。

    直播的 published 是「排程建立時間」，有時會早於實際開播日；
    但標題「2026/09/17(四)張震 股市盤中家教班」一定是當天，不會錯。
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
    """取影片清單。優先走 YouTube Data API。

    為什麼不直接用 RSS：feeds/videos.xml 對 GitHub 機房 IP 會穩定回 404，
    那不是暫時節流，重試無效。有金鑰就用 API，沒有才退回 RSS，
    讓本機（住宅 IP）也能跑。
    """
    if YOUTUBE_API_KEY:
        try:
            return fetch_feed_api()
        except Exception as e:
            log(f"YouTube Data API 失敗（{e}），改用 RSS 備援")
    return fetch_feed_rss()


def fetch_feed_api():
    """用 uploads 播放清單列出最近上傳。

    頻道 ID 的 UC 開頭換成 UU 就是該頻道的 uploads 播放清單 ID，單次 1 unit 配額。
    """
    if not CHANNEL_ID.startswith("UC"):
        raise RuntimeError(
            f"頻道 ID「{CHANNEL_ID}」不是 UC 開頭，推不出 uploads 播放清單。"
            f"@xinchenginsta 這種代稱不能直接用，要填 {DEFAULT_CHANNEL_ID}。")
    r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems",
                     params={"part": "snippet", "maxResults": 25,
                             "playlistId": "UU" + CHANNEL_ID[2:],
                             "key": YOUTUBE_API_KEY},
                     timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}：{r.text[:200]}")
    out = []
    for it in r.json().get("items", []):
        sn = it.get("snippet", {})
        vid = (sn.get("resourceId") or {}).get("videoId")
        if not vid:
            continue
        title = (sn.get("title") or "").strip()
        pub = datetime.fromisoformat(
            sn["publishedAt"].replace("Z", "+00:00")).astimezone(TAIPEI).date()
        out.append({"id": vid, "title": title,
                    "date": date_from_title(title, pub),
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    out.sort(key=lambda v: v["date"], reverse=True)
    log(f"YouTube Data API 取得 {len(out)} 支影片")
    return out


def fetch_feed_rss():
    """RSS 備援。對機房 IP 常被擋，所以帶 User-Agent 並重試。"""
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
            return parse_feed_xml(r.text)
        last = f"HTTP {r.status_code}"
        log(f"RSS 第 {attempt + 1} 次回 {last}，重試")
    raise RuntimeError(
        f"YouTube RSS 連續失敗（最後 {last}）。"
        f"設定 YOUTUBE_API_KEY 可以避開這個問題——RSS 對機房 IP 會穩定回 404。")


def parse_feed_xml(text):
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom",
          "yt": "http://www.youtube.com/xml/schemas/2015"}
    out = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns).text
        title = (e.find("a:title", ns).text or "").strip()
        pub = datetime.fromisoformat(
            e.find("a:published", ns).text.replace("Z", "+00:00")).astimezone(TAIPEI).date()
        out.append({"id": vid, "title": title,
                    "date": date_from_title(title, pub),
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    out.sort(key=lambda v: v["date"], reverse=True)
    log(f"RSS 取得 {len(out)} 支影片")
    return out


def find_video(target: date):
    """找出指定日期那一集。找不到回 None——那是常態，不是錯誤：早上還沒開播。"""
    for v in fetch_feed():
        if v["date"] == target and is_target(v["title"]):
            return v
    return None


def still_live(video_id: str):
    """這支影片現在還在直播嗎。

    True  還在直播或還沒開始
    False 確定播完了
    None  問不到（沒金鑰或 API 掛了）。這時不該當成任何一種結論，照常往下走。
    """
    if not (YOUTUBE_API_KEY and video_id):
        return None
    try:
        r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                         params={"part": "snippet,liveStreamingDetails",
                                 "id": video_id, "key": YOUTUBE_API_KEY},
                         timeout=20)
        if r.status_code != 200:
            return None
        items = r.json().get("items", [])
        if not items:
            return None
        it = items[0]
        state = (it.get("snippet") or {}).get("liveBroadcastContent") or "none"
        if state in ("live", "upcoming"):
            return True
        details = it.get("liveStreamingDetails") or {}
        if details and not details.get("actualEndTime"):
            return True
        return False
    except Exception:
        return None


# ---------------------------------------------------------------- #
# 登入憑證
#
# NotebookLM 用的是 Google 的 web session cookie，而 Google 會在使用過程中
# 輪換它：每用一次就可能發一組新的回來，用戶端寫回 storage_state.json，
# 下次用新的。在自己電腦上這個循環是完整的。
#
# 在 CI 上循環是斷的：storage_state.json 每次從 Secret 還原，工作結束就連同
# 整台機器消失，輪換後的新 cookie 從來沒被保存。於是每次都拿著同一份、
# 越來越舊的 cookie 去敲門。Google 對舊 cookie 有寬限期，寬限期內時好時壞
# ——這就是「上午失敗、下午又好了，而中間什麼都沒改」的原因。
#
# 解法是把輪換後的 cookie 存回試算表。選試算表而不是 GitHub Secret，
# 是因為不必額外申請可以寫 Secret 的個人存取權杖：這支程式本來就有
# 試算表寫入權限，不引入新的憑證。
#
# 安全性：那份 cookie 等同這個 Google 帳號在 NotebookLM 的登入狀態。
# 存放的試算表必須維持私有，絕對不要開成「知道連結的人都可以檢視」。
# ---------------------------------------------------------------- #
def _read_local_auth():
    for path in AUTH_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("cookies"):
                return d
        except Exception:
            continue
    return None


def _write_local_auth(d: dict):
    for path in AUTH_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as e:
            log(f"寫入 {path} 失敗：{e}")


def _auth_fp(d) -> str:
    try:
        items = sorted((c.get("name", ""), str(c.get("value", "")))
                       for c in d.get("cookies", []))
        return hashlib.sha256(repr(items).encode()).hexdigest()
    except Exception:
        return ""


def load_saved_auth(ss) -> str:
    """把試算表保存的最新憑證覆蓋到本機，回傳套用後的指紋。"""
    raw, saved_at = "", ""
    try:
        ws = ss.worksheet(AUTH_SHEET)
        raw = str(sheets_retry(ws.acell, "B2").value or "").strip()
        saved_at = str(sheets_retry(ws.acell, "B1").value or "").strip()
    except Exception:
        pass
    if raw:
        try:
            d = json.loads(raw)
            if isinstance(d, dict) and d.get("cookies"):
                _write_local_auth(d)
                log(f"已套用試算表保存的登入憑證（上次更新 {saved_at or '未知'}）")
        except Exception as e:
            log(f"{AUTH_SHEET}的內容無法解析（{e}），改用 Secret 還原的版本")
    local = _read_local_auth()
    return _auth_fp(local) if local else ""


def save_rotated_auth(ss, before_fp: str):
    """執行後把輪換過的憑證存回試算表。指紋沒變就不寫，避免無謂的寫入。"""
    d = _read_local_auth()
    if not d or _auth_fp(d) == before_fp:
        return
    try:
        try:
            ws = ss.worksheet(AUTH_SHEET)
        except Exception:
            ws = sheets_retry(ss.add_worksheet, title=AUTH_SHEET, rows=10, cols=2)
            sheets_retry(ws.update, range_name="A1",
                         values=[["最後更新"], ["憑證內容"], ["說明"]])
            sheets_retry(ws.update, range_name="B3",
                         values=[["這是 NotebookLM 的登入狀態，等同帳號登入憑證。"
                                  "請維持本試算表私有，不要開放連結分享。"
                                  "由程式自動維護，不需手動編輯。"]])
        sheets_retry(ws.update, range_name="B1",
                     values=[[datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")]])
        sheets_retry(ws.update, range_name="B2",
                     values=[[json.dumps(d, ensure_ascii=False)]])
        log("登入憑證已輪換，新版本存回試算表，下次執行會沿用。")
    except Exception as e:
        log(f"保存輪換後的憑證失敗（不影響本次結果）：{e}")


def auth_days_left():
    """本機 storage_state 裡最早到期的那個 cookie 還剩幾天。讀不到回 None。"""
    d = _read_local_auth()
    if not d:
        return None
    exps = [c["expires"] for c in d.get("cookies", [])
            if isinstance(c.get("expires"), (int, float)) and c["expires"] > 0]
    if not exps:
        return None
    return (min(exps) - time.time()) / 86400


# ---------------------------------------------------------------- #
# NotebookLM 取稿
# ---------------------------------------------------------------- #
async def fetch_fulltext(video_url: str, title: str, timeout: int) -> str:
    """把影片丟給 NotebookLM 索引，取回全文。

    索引不完與真的壞掉必須分開處理。輪詢的逾時只有四分鐘，索引不完是常態，
    那要丟 NotReadyYet 讓下一輪接手；如果一律當成錯誤，每一輪都會亮紅燈、
    每一輪都發告警，真的壞掉那次就被淹掉了。

    用完就把 notebook 刪掉。不刪的話帳號裡每天多一本，很快撞到數量上限。
    """
    from notebooklm import NotebookLMClient

    try:
        client_cm = NotebookLMClient.from_storage()
    except Exception as e:
        if looks_like_auth_error(e):
            raise AuthExpired(str(e)[:300])
        raise

    async with client_cm as client:
        try:
            notebook = await client.notebooks.create(title=title)
        except Exception as e:
            if looks_like_auth_error(e):
                raise AuthExpired(str(e)[:300])
            raise
        try:
            try:
                source = await client.sources.add_url(
                    notebook.id, video_url, wait=True, wait_timeout=timeout)
            except NotReadyYet:
                raise
            except Exception as e:
                # 認證失效要先判。否則會被下面的關鍵字誤判成「還沒好」而無限重試，
                # 而認證失效重試永遠不會成功。
                if looks_like_auth_error(e):
                    raise AuthExpired(str(e)[:300])
                msg = str(e).lower()
                if any(k in msg for k in
                       ("timeout", "timed out", "processing", "pending", "queue")):
                    raise NotReadyYet(f"NotebookLM 在 {timeout} 秒內尚未完成索引")
                raise

            fulltext = await client.sources.get_fulltext(notebook.id, source.id)
            content = fulltext.content or ""
            # 索引剛開始時可能回一段極短的殘缺內容，那也算還沒好。
            if len(content) < MIN_TRANSCRIPT:
                raise NotReadyYet(f"取回的全文只有 {len(content)} 字，索引尚未完成")
            return content
        finally:
            try:
                await client.notebooks.delete(notebook.id)
            except Exception:
                pass


# ---------------------------------------------------------------- #
# 一次探詢
# ---------------------------------------------------------------- #
class Done(Exception):
    """這一天已經處理完，不必再敲。帶一個要印出來的理由。"""

    def __init__(self, reason: str, code: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.code = code


def tick(ss, target: date, force: bool) -> bool:
    """敲一次門。

    回 True 代表這一輪真的寫進去了；回 False 代表還沒好，等下一輪。
    已經不必再做的情況一律丟 Done，由呼叫端收掉並結束整個迴圈。
    """
    date_str = target.strftime("%Y/%m/%d")

    # ---- 第一關：試算表。最便宜，而且手動優先的判斷一定要排在最前面 ----
    #
    # 順序不能反。先去 YouTube、先去 NotebookLM 的話，人已經貼好稿的日子
    # 還是會白跑一趟 API 與索引，甚至可能在寫入那一刻覆蓋掉人工成果。
    ws, header, idx, row = read_state(ss, "", date_str)
    src = source_of(row)
    raw = str(row.get(COL_RAW) or "").strip()

    if src in (SRC_MANUAL, SRC_HOLD):
        if raw:
            raise Done(f"{date_str} 已有手動輸入的逐字稿 {len(raw)} 字"
                       f"（第 {idx} 列，來源「{src}」）。手動優先，自動化停止，不覆蓋。")
        raise Done(f"{date_str} 已標記為「{src}」，等人工貼稿。自動化停止，不介入。")

    if raw and len(raw) >= MIN_TRANSCRIPT:
        raise Done(f"{date_str} 已有逐字稿 {len(raw)} 字（第 {idx} 列，來源「{src or '未標記'}」），不重抓。")

    # ---- 第二關：YouTube。當天那一集出現了沒有 ----
    video = find_video(target)
    if not video:
        log(f"{date_str} 的影片還沒出現在頻道清單上（標題要含 {TITLE_KEYWORDS}）。")
        return False
    log(f"對到影片：{video['title']}　{video['url']}")

    # ---- 第三關：還在直播就不要戳 NotebookLM ----
    #
    # 直播進行中 VOD 根本還沒生成，NotebookLM 一定索引不到。這段期間每敲一次
    # 就白開一本 notebook、白等四分鐘。用 YouTube API 問一句便宜太多。
    now = datetime.now(TAIPEI)
    if not force and LIVE_GUARD_UNTIL != "00:00" and now < at_taipei(target, LIVE_GUARD_UNTIL):
        live = still_live(video["id"])
        if live is True:
            log(f"還在直播中，VOD 尚未生成，這一輪不戳 NotebookLM（{LIVE_GUARD_UNTIL} 後不再檢查）。")
            return False
        if live is None:
            log("問不到直播狀態（沒有 YOUTUBE_API_KEY 或 API 暫時不通），照常往下試。")

    # ---- 第四關：取稿 ----
    log(f"向 NotebookLM 索取全文，最多等 {INDEX_TIMEOUT} 秒……")
    text = asyncio.run(fetch_fulltext(video["url"], video["title"], INDEX_TIMEOUT))

    save_transcript(ss, video, date_str, text, SRC_AUTO)
    write_status_log(ss, "逐字稿自動取得", f"{date_str} {len(text)} 字　{video['title']}")
    return True


# ---------------------------------------------------------------- #
# 子指令
# ---------------------------------------------------------------- #
def cmd_auto(args) -> int:
    target = args.date or datetime.now(TAIPEI).date()
    date_str = target.strftime("%Y/%m/%d")
    log(f"自動抓取　目標日期 {date_str}　頻道 {CHANNEL_ID}")

    if target.weekday() >= 5 and not args.force:
        log("週末沒有盤中直播，不執行。（要硬跑請加 --force）")
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
    before_fp = load_saved_auth(ss)
    left = auth_days_left()
    if left is not None:
        if left < 0:
            log(f"::error::登入憑證已於 {-left:.1f} 天前過期。")
        elif left < 3:
            log(f"::warning::登入憑證約 {left:.1f} 天後到期，請盡快在本機重跑 notebooklm login。")
        else:
            log(f"登入憑證還有約 {left:.1f} 天。")

    round_no = 0
    try:
        while True:
            round_no += 1
            log("")
            log(f"─── 第 {round_no} 次探詢 ───")
            try:
                if tick(ss, target, args.force):
                    log("逐字稿已寫進試算表。後面的潤飾與擷取由 pipeline.py 接手。")
                    save_rotated_auth(ss, before_fp)
                    return 0
            except NotReadyYet as e:
                log(f"還沒好：{e}")

            if args.once:
                log("單次模式，不等下一輪。")
                save_rotated_auth(ss, before_fp)
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
        save_rotated_auth(ss, before_fp)
        return 0

    except Done as e:
        log(e.reason)
        save_rotated_auth(ss, before_fp)
        return e.code
    except AuthExpired as e:
        log("")
        log(f"::error::NotebookLM 登入狀態已失效：{e}")
        log("重試沒有用。請在本機執行 notebooklm login，把新的")
        log("  ~/.notebooklm/profiles/default/storage_state.json")
        log("整份內容更新到 GitHub Secret NOTEBOOKLM_AUTH_JSON，再清掉試算表"
            f"「{AUTH_SHEET}」分頁的 B2。")
        write_status_log(ss, "認證失效", str(e)[:400])
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
            f"確定要寫請加大 MIN_TRANSCRIPT，或確認貼上的是完整的一份。")
        return 1

    ss = open_sheets()
    video = {"id": args.video_id or "", "title": args.title or ""}
    if not video["id"]:
        # 對得到當天影片就用真實 ID，對不到就給一個看得出是人工的假 ID。
        # 假 ID 的前綴與 Apps Script 的 MANUAL_ENTRY_PREFIX 一致，
        # 下游的保護規則靠這個前綴認出「這一列不准被排程清掉」。
        found = None
        try:
            found = find_video(target)
        except Exception as e:
            log(f"（對影片失敗，改用人工編號：{e}）")
        if found:
            video = {"id": found["id"], "title": found["title"]}
            log(f"對到影片：{found['title']}")
        else:
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

    try:
        v = find_video(target)
        log(f"頻道　　　{('對到：' + v['title'] + '　' + v['url']) if v else '這一天還沒有符合的影片'}")
        if v:
            live = still_live(v["id"])
            label = {True: "還在直播", False: "已結束",
                     None: "問不到（沒有金鑰或 API 不通）"}[live]
            log(f"直播狀態　{label}")
    except Exception as e:
        log(f"頻道　　　查詢失敗：{e}")
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
        prog="Notebooklm.py",
        description="每日逐字稿抓取：自動走 NotebookLM，手動優先，結果寫進雲端試算表。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例
  python Notebooklm.py auto                         今天，照排程規則跑（預設）
  python Notebooklm.py auto --once                  只敲一次就走，不等下一輪
  python Notebooklm.py auto --date 2026/09/17 --force
                                                    指定日期、無視平日與時段限制
  python Notebooklm.py manual --file 稿.txt          手動貼稿，之後自動化不再碰這天
  python Notebooklm.py manual --date 2026/09/17      從鍵盤貼，Ctrl+Z 結束
  python Notebooklm.py hold                         先擋住今天的自動化，稍後自己貼
  python Notebooklm.py hold --release                解除上面的保留
  python Notebooklm.py status                       看今天目前是什麼狀態
""")
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("auto", help="自動抓取（預設）")
    a.add_argument("--date", type=parse_date, help="目標日期 YYYY/MM/DD，預設今天")
    a.add_argument("--once", action="store_true", help="只敲一次，不進輪詢迴圈")
    a.add_argument("--force", action="store_true",
                   help="無視平日／時段／直播中的限制，硬跑")
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
    return p


def main(argv=None) -> int:
    parser = build_parser()
    argv = list(argv if argv is not None else sys.argv[1:])
    # 不給子指令時預設 auto，這樣排程只要寫 `python Notebooklm.py` 就好。
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
