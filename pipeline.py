"""
資料蒐集後端主流程（GitHub Actions 執行）

紅線：所有輸出僅能根據影片中明確講述的內容產生，不可自行推論或補完。
金鑰：全部從環境變數讀取，不得寫入程式碼，不得印進 workflow logs。

本版修正三件事：
  1. 頻道 ID 內建預設值（公開資訊），Variables 沒設也能跑
  2. Google Sheets 偶發 503 自動重試
  3. Gemini 靜默截斷 —— 切塊處理 + 關閉 thinking + 檢查 finishReason + 長度守門
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials
from pypinyin import lazy_pinyin
import difflib

TAIPEI = timezone(timedelta(hours=8))
NOT_MENTIONED = "本支影片未說明"

# ---------------------------------------------------------------- #
# 設定
# ---------------------------------------------------------------- #

# 頻道 ID 是公開資訊，寫死當預設值。GitHub Variables 有設就以 Variables 為準。
DEFAULT_CHANNEL_ID = "UCPqyYS3n6yyXL2jygauXpzg"
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID

# 標題必須含此關鍵字才視為當日直播。頻道標題格式：2026/07/15(三)張震 股市盤中家教班
TITLE_KEYWORDS = ["盤中家教班"]

# 只處理這個日期（含）以後的影片。頻道 RSS 裡混有 2025 年的宣傳片，一律略過。
MIN_DATE = date(2026, 1, 1)

# 潤飾後長度佔原文的比例門檻。
#   低於 FAIL：研判模型改成摘要而非潤飾，中止。
#   介於 FAIL 與 WARN：印警告但照常寫入。中文逐字稿贅字多時，7 成上下是正常的。
# 真正的「輸出被截斷」由 finishReason == MAX_TOKENS 直接攔截，不靠這個比例判斷。
RATIO_FAIL = 0.45
RATIO_WARN = 0.70

# 一小時直播的逐字稿約 13000 字以上。低於此值印警告，提醒抽查上游是否索引不全。
SHORT_TRANSCRIPT_HINT = 5000

# 股票名稱比對門檻。字面比對優先，過不了才用拼音比對抓同音錯字。
# 實測：四星科 對 事欣科 拼音 0.88，紅傑科 對 宏捷科 拼音 1.00。
# PINYIN_LOOSE 用於首字相同且長度相近的情形，例如 旭準 對 旭隼 只有 0.73，
# 但首字都是「旭」，是很強的訊號。放寬的前提是非個股已經先被剔除。
NAME_CUTOFF = 0.75
PINYIN_CUTOFF = 0.80
PINYIN_LOOSE = 0.68
UNRESOLVED = "代號待確認"
REJECT = "__REJECT__"

GEMINI_MODEL = "gemini-2.5-flash"

# 潤飾切塊大小。逐字稿標點稀疏時靠 CHUNK_HARD 保底。
CHUNK_SIZE = 5000
CHUNK_HARD = 7500

# gemini-2.5-flash 輸出上限 65,535 tokens
MAX_OUT = 65535

# Google 試算表單格上限 50,000 字元
SHEET_CELL_LIMIT = 49000

TRANSIENT = (429, 500, 502, 503, 504)


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"缺少環境變數 {name}，請到 GitHub Secrets 或 Variables 補上。")
    return v


SPREADSHEET_ID = env("SPREADSHEET_ID")
GEMINI_API_KEY = env("GEMINI_API_KEY")
BACKFILL = os.environ.get("BACKFILL", "false").strip().lower() == "true"
FINAL_ATTEMPT = os.environ.get("FINAL_ATTEMPT", "false").strip().lower() == "true"

# 純修代號模式。只把試算表既有的股票名稱重跑一次拼音比對，
# 不碰 NotebookLM，不呼叫 Gemini，幾十秒就跑完。
REPAIR_CODES = os.environ.get("REPAIR_CODES", "false").strip().lower() == "true"

# ---------------------------------------------------------------- #
# 輪詢逾時
#
# 這是整套排程能不能在 11:30 開始運作的關鍵。
#
# 11:30 直播還在進行，VOD 尚未生成，NotebookLM 一定索引不到。
# 若像先前那樣一次等 30 分鐘，11:33 那次會一路卡到 12:03，
# concurrency 又把後面每一輪全擋在佇列，等於一整個中午只敲了三次門。
#
# 改成 4 分鐘。索引不到就立刻放棄，讓下一輪接手。
# 回補模式與手動長跑則給足時間。
# ---------------------------------------------------------------- #
POLL_TIMEOUT = 240
FULL_TIMEOUT = 1800
INDEX_TIMEOUT = FULL_TIMEOUT if (BACKFILL or FINAL_ATTEMPT) else POLL_TIMEOUT

# 過了這個時間仍拿不到逐字稿，才判定今天真的沒有影片
GIVE_UP_HOUR = 15


class NotReadyYet(Exception):
    """VOD 還沒好。這不是錯誤，是還沒輪到。工作要顯示綠色。"""
    pass


# ---------------------------------------------------------------- #
# 試算表
# ---------------------------------------------------------------- #
def sheets_retry(fn, *args, **kwargs):
    """Google Sheets 偶發 503 / 429，重試四次後才放棄。"""
    last = None
    for i, delay in enumerate((0, 3, 8, 20)):
        if delay:
            time.sleep(delay)
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code not in TRANSIENT:
                raise
            last = code
            print(f"Sheets 回傳 {code}，第 {i + 1} 次重試")
    raise RuntimeError(f"Google Sheets 連續重試失敗，最後狀態 {last}")


def open_sheets():
    info = json.loads(env("GOOGLE_SHEETS_SERVICE_ACCOUNT"))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    return sheets_retry(gc.open_by_key, SPREADSHEET_ID)


def video_rows(ss):
    return sheets_retry(ss.worksheet("影片清單").get_all_records)


def norm_date(v) -> str:
    """把試算表各種日期寫法統一成 yyyy/MM/dd。"""
    s = str(v or "").strip().replace("-", "/")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}" if m else ""


def existing_transcript(ss, video_id, date_str):
    """
    查雲端是否已經有這一天的逐字稿。影片ID 與日期任一對上就算數。
    回傳 (原始逐字稿, 修飾後逐字稿)，沒有則回 ("", "")。
    """
    for row in video_rows(ss):
        same_id = str(row.get("影片ID")) == video_id
        same_date = norm_date(row.get("發布日期")) == date_str
        if same_id or same_date:
            return (str(row.get("原始逐字稿內容") or ""),
                    str(row.get("修飾後逐字稿內容") or ""))
    return "", ""


def existing_dates(ss, sheet_name) -> set:
    """某張表已經有哪些日期的資料。用來避免重複寫入操作紀錄與會員持股。"""
    try:
        rows = sheets_retry(ss.worksheet(sheet_name).get_all_records)
    except Exception:
        return set()
    return {norm_date(r.get("日期")) for r in rows} - {""}


def find_video_row(ss, video_id):
    ws = ss.worksheet("影片清單")
    for idx, row in enumerate(sheets_retry(ws.get_all_records), start=2):
        if str(row.get("影片ID")) == video_id:
            return ws, idx
    return ws, None


def mark_status(ss, video_id, published, title, status, reason=""):
    ws, idx = find_video_row(ss, video_id)
    if idx is None:
        sheets_retry(ws.append_row, [video_id, published, title, status, reason, "", ""])
    else:
        sheets_retry(ws.update, range_name=f"D{idx}:E{idx}", values=[[status, reason]])


def cell(text: str) -> str:
    """試算表單格上限保護。超長時明確標示截斷，不靜默吞掉。"""
    text = text or ""
    if len(text) > SHEET_CELL_LIMIT:
        print(f"警告：內容 {len(text)} 字超過試算表單格上限，已截斷")
        return text[:SHEET_CELL_LIMIT] + "\n\n（超過試算表單格上限，已截斷）"
    return text


def write_transcripts(ss, video_id, v1, v2):
    ws, idx = find_video_row(ss, video_id)
    if idx:
        sheets_retry(ws.update, range_name=f"F{idx}:G{idx}", values=[[cell(v1), cell(v2)]])


# ---------------------------------------------------------------- #
# 影片偵測（YouTube 公開 RSS，不需金鑰，不下載影音）
# ---------------------------------------------------------------- #
TITLE_DATE = re.compile(r"(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})")


def date_from_title(title: str, fallback):
    """
    標題日期優先於 RSS published。
    直播的 published 是「排程建立時間」，可能早於實際開播日；
    但標題 2026/07/15(三)張震 股市盤中家教班 一定是當天。
    """
    m = TITLE_DATE.search(title or "")
    if not m:
        return fallback
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TAIPEI).date()
    except ValueError:
        return fallback


def fetch_feed():
    """
    YouTube 的 RSS 對機房 IP 常常回 500，同一個網址用瀏覽器開卻正常。
    這是他們對 datacenter IP 的節流，不是網址寫錯。所以必須重試，
    而且要帶 User-Agent，不然更容易被擋。
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    last = ""
    for attempt, delay in enumerate((0, 4, 12, 30, 60)):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, timeout=30, headers=headers)
        except Exception as e:
            last = str(e)
            print(f"  RSS 第 {attempt + 1} 次連線失敗（{last}），重試")
            continue

        if r.status_code == 200:
            if attempt:
                print(f"  RSS 第 {attempt + 1} 次成功")
            return parse_feed_xml(r.text)

        last = f"HTTP {r.status_code}"
        print(f"  RSS 第 {attempt + 1} 次回 {last}，重試")

    raise RuntimeError(
        f"YouTube RSS 連續 5 次失敗（最後 {last}）。"
        f"這通常是 YouTube 對 GitHub 機房 IP 的暫時性節流，下一輪排程會再試。"
    )


def parse_feed_xml(text):
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

    out = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns).text
        title = (e.find("a:title", ns).text or "").strip()
        published = e.find("a:published", ns).text
        rss_date = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(TAIPEI).date()
        out.append({
            "id": vid,
            "title": title,
            "date": date_from_title(title, rss_date),
            "rss_date": rss_date,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    out.sort(key=lambda v: v["date"], reverse=True)
    return out


def is_target(title: str) -> bool:
    return any(k in (title or "") for k in TITLE_KEYWORDS)


# ---------------------------------------------------------------- #
# 逐字稿：notebooklm-py 來源全文存取
# ---------------------------------------------------------------- #
async def fetch_fulltext(video_url, title, timeout):
    """
    timeout 短的時候（輪詢），索引不完就丟 NotReadyYet，讓下一輪接手。
    索引不完與真的出錯必須分開，不然每一輪都會亮紅燈並發告警。
    """
    from notebooklm import NotebookLMClient

    async with NotebookLMClient.from_storage() as client:
        notebook = await client.notebooks.create(title=title)
        try:
            try:
                source = await client.sources.add_url(
                    notebook.id, video_url, wait=True, wait_timeout=timeout
                )
            except Exception as e:
                msg = str(e).lower()
                # 逾時、還在處理、佇列中，都代表 VOD 還沒好，不是壞掉
                if any(k in msg for k in ("timeout", "timed out", "processing", "pending", "queue")):
                    raise NotReadyYet(f"NotebookLM 在 {timeout} 秒內尚未完成索引")
                raise

            fulltext = await client.sources.get_fulltext(notebook.id, source.id)
            content = fulltext.content or ""

            # 索引剛開始時可能回傳極短的殘缺內容，這也算還沒好
            if len(content) < 200:
                raise NotReadyYet(f"取回的全文僅 {len(content)} 字，索引尚未完成")

            return content
        finally:
            try:
                await client.notebooks.delete(notebook.id)
            except Exception:
                pass


# ---------------------------------------------------------------- #
# Gemini
# ---------------------------------------------------------------- #
def call_gemini(system_text, user_text, want_json=False, thinking=0, max_out=MAX_OUT, tag=""):
    """
    thinking=0 關閉思考。gemini-2.5-flash 的 thinking 預設開啟，
    且思考 token 計入 maxOutputTokens，是造成輸出被截斷的主因之一。

    finishReason 必須檢查。MAX_TOKENS 時 API 仍回 200 加上半截文字，
    不檢查就會靜默寫入不完整資料。
    """
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")

    cfg = {
        "temperature": 0.1,
        "maxOutputTokens": max_out,
        "thinkingConfig": {"thinkingBudget": thinking},
    }
    if want_json:
        cfg["responseMimeType"] = "application/json"

    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": cfg,
    }

    last = ""
    for delay in (0, 5, 15, 40):
        if delay:
            time.sleep(delay)

        r = requests.post(url, json=body, timeout=600)

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            if r.status_code not in TRANSIENT:
                # 錯誤訊息不含金鑰，也不回傳原始回應內容
                raise RuntimeError(f"Gemini 呼叫失敗（{tag}）：{last}")
            print(f"Gemini {tag} 回傳 {r.status_code}，重試中")
            continue

        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            reason = data.get("promptFeedback", {}).get("blockReason", "")
            raise RuntimeError(f"Gemini 未回傳候選（{tag}），blockReason={reason or '無'}")

        cand = cands[0]
        finish = cand.get("finishReason", "STOP")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)

        u = data.get("usageMetadata", {})
        print(f"  [{tag}] finish={finish} 輸入={u.get('promptTokenCount')} "
              f"思考={u.get('thoughtsTokenCount', 0)} 輸出={u.get('candidatesTokenCount')} "
              f"文字={len(text)} 字")

        if finish == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini 輸出遭截斷（{tag}）：finishReason=MAX_TOKENS。請調小 CHUNK_SIZE 後重跑。"
            )
        if finish not in ("STOP", "", None):
            raise RuntimeError(f"Gemini 異常結束（{tag}）：finishReason={finish}")
        if not text.strip():
            raise RuntimeError(f"Gemini 回傳空內容（{tag}）")
        return text

    raise RuntimeError(f"Gemini 連續重試失敗（{tag}）：{last}")


def split_transcript(text, size=CHUNK_SIZE, hard=CHUNK_HARD):
    """在句末標點切段。原始逐字稿標點常常稀疏，故加硬上限保底。"""
    seps = "。！？!?\n"
    chunks, cur, n = [], [], 0
    for ch in text:
        cur.append(ch)
        n += 1
        if (n >= size and ch in seps) or n >= hard:
            chunks.append("".join(cur))
            cur, n = [], 0
    tail = "".join(cur)
    if tail.strip():
        chunks.append(tail)
    return chunks or [text]


POLISH_SYSTEM = """你負責整理一段中文直播逐字稿的其中一個片段。

你只能做這三件事：
1. 修正同音錯字。
2. 補上合理的斷句與標點。
3. 刪除純粹的填充詞，僅限「嗯、啊、呃、那個、就是說」這類完全沒有實質意義的字。

除了上述三項，原文的每一句話都必須保留下來，逐句對應輸出。

嚴格禁止：
- 禁止摘要、濃縮、改寫語意。
- 禁止省略任何一句有實質內容的話，即使它重複、離題或聽起來不重要。
- 禁止新增或刪除任何事實資訊。
- 禁止補完語意不清的地方。

若某處聽起來像是股票名稱但拼字有誤，可依常見台股名稱修正，其餘一律照原文保留。

這是長逐字稿的其中一段，可能從句子中間開始或結束，這是正常的，照樣逐句處理即可。
必須處理到片段的最後一個字，不可中途停止。

輸出的長度應該與輸入相近。直接輸出整理後的文字，全文使用繁體中文。
不要加開場白、結語、標題、片段編號或任何說明。"""


# ---------------------------------------------------------------- #
# 股票名稱與代號比對
#
# 這一段的存在理由：語音辨識會把「事欣科」聽成「四星科」。
# 原本把代號交給 Gemini 憑記憶填，模型既記不全上市櫃三千多檔，
# 也無從得知「四星科」根本不是一家公司。所以比對必須在 Python 這邊，
# 拿證交所與櫃買中心的權威清單做，不是靠提示詞拜託模型。
# ---------------------------------------------------------------- #
_CODE_MAP = None

# 上市與上櫃的代號名稱來源，依序嘗試，第一個成功就停。
#
# 為什麼需要備援：櫃買中心的 openapi 路徑改版過，舊網址回傳的是 HTML 錯誤頁，
# 而 .json() 只會吐一句 Expecting value: line 1 column 1，完全看不出端點死了。
# 這正是先前對照表只有 1090 檔（純上市）、所有上櫃股全部對不到的原因。
LISTED_SOURCES = [
    {"label": "證交所 OpenAPI 上市公司基本資料", "kind": "json",
     "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
    {"label": "公開資訊觀測站 上市 CSV", "kind": "csv",
     "url": "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
]

OTC_SOURCES = [
    {"label": "公開資訊觀測站 上櫃 CSV", "kind": "csv",
     "url": "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
    {"label": "櫃買 OpenAPI 本益比表", "kind": "json",
     "url": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
     "code": ["SecuritiesCompanyCode", "Code"],
     "name": ["CompanyName", "CompanyAbbreviation", "Name"]},
    {"label": "櫃買 OpenAPI 上櫃公司基本資料", "kind": "json",
     "url": "https://www.tpex.org.tw/openapi/v1/opendata_t187ap03_O",
     "code": ["SecuritiesCompanyCode", "公司代號"],
     "name": ["CompanyAbbreviation", "CompanyName", "公司簡稱"]},
]


def _fetch_rows(src):
    r = requests.get(src["url"], timeout=40,
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")

    text = r.text
    if text.lstrip()[:1] == "<":
        raise RuntimeError("回傳的是網頁不是資料，這個端點多半已改版")

    if src["kind"] == "csv":
        import csv, io
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    else:
        rows = r.json()

    if not rows:
        raise RuntimeError("回傳 0 筆")
    return rows


def _pick(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _try_sources(sources, market):
    for s in sources:
        try:
            raw = _fetch_rows(s)
            out = {}
            for r in raw:
                c, n = _pick(r, s["code"]), _pick(r, s["name"])
                if re.fullmatch(r"\d{4,6}", c) and n:
                    out[c] = n
            if not out:
                raise RuntimeError("解析後 0 筆，欄位名稱可能改了")
            print(f"  {market}：{s['label']} 成功，{len(out)} 檔")
            return out
        except Exception as e:
            print(f"  {market}：{s['label']} 失敗（{e}），換下一個來源")
    return {}


def get_code_map() -> dict:
    """{代號: 簡稱}，含上市與上櫃。"""
    global _CODE_MAP
    if _CODE_MAP is not None:
        return _CODE_MAP

    print("載入代號對照表")
    m = {}
    listed = _try_sources(LISTED_SOURCES, "上市")
    otc = _try_sources(OTC_SOURCES, "上櫃")
    m.update(listed)
    m.update(otc)

    if not m:
        raise RuntimeError("上市與上櫃的所有來源都失敗，無法進行代號比對。")

    if not listed:
        print("警告：上市清單全部來源都失敗，對照表只有上櫃的部分。")
    if not otc:
        print("警告：上櫃清單全部來源都失敗。上櫃股票將全部無法對到代號。")

    _CODE_MAP = m
    print(f"代號對照表載入 {len(m)} 檔（上市 {len(listed)}，上櫃 {len(otc)}）")
    return m

# ---------------------------------------------------------------- #
# 非個股剔除
#
# 逐字稿裡會出現「台塑集團」「高速傳輸股」「PMIC」這些東西。
# 它們是集團、族群、產業縮寫，不是個股，不該給代號也不該留在操作紀錄裡。
#
# 這一關必須跑在比對之前。實測「台塑集團」對「台積電」拼音相似度 0.70，
# 首字又都是「台」，放寬門檻後會被硬湊成 2330。先剔除才不會製造錯誤資料。
# ---------------------------------------------------------------- #
NON_STOCK_SUFFIX = ("集團", "族群", "概念股", "概念", "類股", "板塊", "產業", "供應鏈", "相關股", "相關")
NON_STOCK_EXACT = {
    "權值股", "中小型股", "傳產", "電子股", "金融股", "航運股", "生技股", "觀光股",
    "大盤", "加權指數", "台股", "美股", "陸股", "日股", "期貨", "選擇權", "ETF",
    "個股", "多方", "空方", "現金", "空手",
}


def is_non_stock(name: str):
    """回傳 (是否非個股, 原因)。"""
    n = str(name or "").strip()
    if not n:
        return True, "空白"
    if n in NON_STOCK_EXACT:
        return True, "市場泛稱"
    for s in NON_STOCK_SUFFIX:
        if n.endswith(s):
            return True, f"以「{s}」結尾，是集團或族群不是個股"
    if not re.search(r"[\u4e00-\u9fff]", n):
        return True, "無中文字，是產業縮寫或英文術語"
    if len(n) >= 3 and n.endswith("股"):
        return True, "以「股」結尾，是族群不是個股"
    if len(n) > 8:
        return True, "過長，不像股票簡稱"
    return False, ""



def _pin(s: str) -> str:
    return "".join(lazy_pinyin(str(s)))


def _norm_pin(p: str) -> str:
    """
    台灣國語音變正規化。這一步是同音錯字比對能不能成立的關鍵。

    台灣人講國語普遍前後鼻音不分（chen 對 cheng、yin 對 ying、xin 對 xing），
    捲舌音也不分（zh 對 z、ch 對 c、sh 對 s）。語音辨識忠實地反映了這個特徵，
    所以「誠美材」會被聽成「陳美」，「英濟」會被聽成「引細」。

    不做這一步的話，chen 對 cheng 的首字比對會判定為不同音，
    放寬門檻不會生效，這些字就永遠對不上。
    """
    p = re.sub(r"([aeiou])ng", r"\1n", p)          # cheng -> chen
    return p.replace("zh", "z").replace("ch", "c").replace("sh", "s")


def _npin(s: str) -> str:
    return _norm_pin(_pin(s))


def _npin1(s: str) -> str:
    """首字的正規化拼音。續 和 旭 都是 xu，誠 和 陳 正規化後都是 cen。"""
    s = str(s or "")
    return _norm_pin(lazy_pinyin(s[:1])[0]) if s else ""


def _base(s: str) -> str:
    """去掉 -KY、*、投控 這類後綴。讓「譜瑞」能對上「譜瑞-KY」。"""
    return re.sub(r"(-KY|-DR|\*|投控|控股)$", "", str(s or "")).strip()


def resolve_code(name: str, hint: str):
    """
    回傳 (代號, 名稱, 方式)。
      REJECT      不是個股，呼叫端整列剔除
      UNRESOLVED  是個股但對不上，保留並標示待確認
    絕不亂猜。
    """
    name = str(name or "").strip()
    hint = str(hint or "").strip()

    m = get_code_map()

    # 0. 代號格式合法就直接採用，不再往下模糊比對。
    #
    #    這一條是硬規則。先前對照表因為上櫃清單掛掉而殘缺，
    #    「美琪瑪（4721）」這種本來就正確的資料，因為 4721 不在殘缺的表裡，
    #    就一路掉進模糊比對，被改成「美利達 9914」。把對的改成錯的，
    #    比對不到還糟。四位數代號是硬證據，優先於任何名稱推測。
    if re.fullmatch(r"\d{4,6}", hint):
        if hint in m:
            return hint, m[hint], "代號直接命中"
        return hint, name, "代號格式合法，直接採用（不在對照表，可能是新上市或清單未更新）"

    # 1. 非個股先剔除。必須在比對之前，否則「台塑集團」會被硬湊成「台積電」。
    bad, why = is_non_stock(name)
    if bad:
        return REJECT, name, f"剔除：{why}"

    # 2. 名稱完全相同
    for c, n in m.items():
        if n == name:
            return c, n, "名稱完全相同"

    # 3. 去後綴後相同。譜瑞 對 譜瑞-KY。
    nb = _base(name)
    if nb:
        for c, n in m.items():
            if _base(n) == nb:
                return c, n, "去後綴後相同"

    # 4. 字面相似。原名與去後綴版各比一次，取高者。
    best_c, best_s = None, 0.0
    for c, n in m.items():
        s = max(difflib.SequenceMatcher(None, name, n).ratio(),
                difflib.SequenceMatcher(None, nb, _base(n)).ratio())
        if s > best_s:
            best_c, best_s = c, s
    if best_s >= NAME_CUTOFF:
        return best_c, m[best_c], f"字面相似 {best_s:.2f}"

    # 5. 拼音相似。首字「同音」時放寬門檻。
    #    比的是正規化後的拼音：續 對 旭 都是 xu，誠 對 陳 正規化後都是 cen。
    #    台灣國語前後鼻音與捲舌音不分，不正規化的話這些永遠對不上。
    nk, nk1 = _npin(name), _npin1(name)
    cands = []
    for c, n in m.items():
        s = max(difflib.SequenceMatcher(None, nk, _npin(n)).ratio(),
                difflib.SequenceMatcher(None, _npin(nb), _npin(_base(n))).ratio())
        loose = (nk1 and nk1 == _npin1(n) and abs(len(name) - len(n)) <= 2)
        if s >= (PINYIN_LOOSE if loose else PINYIN_CUTOFF):
            cands.append((s, c, loose))
    if cands:
        cands.sort(reverse=True)
        s, c, loose = cands[0]
        return c, m[c], "拼音相似 %.2f%s" % (s, "，首字同音" if loose else "")

    return UNRESOLVED, name, f"無法確定（字面 {best_s:.2f}）"


def resolve_signals(signals: dict) -> dict:
    """每一筆都跑代號比對。判定為非個股的整筆剔除，不寫進試算表。"""
    stat = {"命中": 0, "修正": 0, "待確認": 0, "剔除": 0}

    for key in ("buy", "sell", "watch", "holdings"):
        kept = []
        for r in signals.get(key, []):
            raw = str(r.get("name", "")).strip()
            code, fixed, how = resolve_code(raw, r.get("code", ""))

            if code == REJECT:
                stat["剔除"] += 1
                print(f"  代號比對　{raw} -> 剔除（{how.replace('剔除：', '')}）")
                continue

            r["code"] = code
            if code == UNRESOLVED:
                stat["待確認"] += 1
                print(f"  代號比對　{raw} -> 待確認（{how}）")
            elif fixed != raw:
                stat["修正"] += 1
                r["name"] = fixed
                r["原始語音名稱"] = raw
                print(f"  代號比對　{raw} -> {code} {fixed}（{how}）")
            else:
                stat["命中"] += 1
            kept.append(r)
        signals[key] = kept

    print(f"代號比對結果：命中 {stat['命中']}，同音修正 {stat['修正']}，"
          f"待確認 {stat['待確認']}，剔除非個股 {stat['剔除']}")
    return signals


POLISH_SYSTEM = """你負責整理一段中文直播逐字稿的其中一個片段。

你只能做這三件事：
1. 修正同音錯字。
2. 補上合理的斷句與標點。
3. 刪除純粹的填充詞，僅限「嗯、啊、呃、那個、就是說」這類完全沒有實質意義的字。

除了上述三項，原文的每一句話都必須保留下來，逐句對應輸出。

嚴格禁止：
- 禁止摘要、濃縮、改寫語意。
- 禁止省略任何一句有實質內容的話，即使它重複、離題或聽起來不重要。
- 禁止新增或刪除任何事實資訊。
- 禁止補完語意不清的地方。

若某處聽起來像是股票名稱但拼字有誤，可依常見台股名稱修正，其餘一律照原文保留。

這是長逐字稿的其中一段，可能從句子中間開始或結束，這是正常的，照樣逐句處理即可。
必須處理到片段的最後一個字，不可中途停止。

輸出的長度應該與輸入相近。直接輸出整理後的文字，全文使用繁體中文。
不要加開場白、結語、標題、片段編號或任何說明。"""


def polish(transcript: str) -> str:
    chunks = split_transcript(transcript)
    print(f"逐字稿 {len(transcript)} 字，切成 {len(chunks)} 段送出潤飾")

    out = []
    for i, c in enumerate(chunks, 1):
        r = call_gemini(POLISH_SYSTEM, c, thinking=0, tag=f"polish {i}/{len(chunks)}")
        cr = len(r) / max(len(c), 1)
        flag = "" if cr >= RATIO_WARN else "  ← 這段壓縮偏多"
        print(f"潤飾第 {i}/{len(chunks)} 段：{len(c)} → {len(r)} 字（{cr:.0%}）{flag}")
        out.append(r)
        time.sleep(1)

    joined = "\n".join(out)
    ratio = len(joined) / max(len(transcript), 1)
    print(f"潤飾完成：{len(transcript)} → {len(joined)} 字（{ratio:.0%}）")

    # 輸出被截斷已由 finishReason == MAX_TOKENS 攔截。
    # 這裡只防「模型改成摘要」，門檻放寬，避免對贅字多的短片誤判。
    if ratio < RATIO_FAIL:
        raise RuntimeError(
            f"潤飾後長度僅原文的 {ratio:.0%}，低於 {RATIO_FAIL:.0%} 下限，"
            f"研判模型改成了摘要而非逐句潤飾，中止以免寫入不完整資料。"
        )
    if ratio < RATIO_WARN:
        print(f"警告：潤飾後長度為原文的 {ratio:.0%}。逐字稿贅字多時這是正常的，"
              f"但請抽查試算表的「修飾後逐字稿內容」是否有整段消失。")
    return joined


EXTRACT_SYSTEM = """你從一段完整的直播逐字稿中，擷取講者「明確講出」的操作紀錄。

嚴格禁止：
1. 禁止創造逐字稿中沒有提到的股票名稱、價位、操作紀錄或會員持股。
2. 禁止引用其他日期或其他來源的內容。
3. 禁止產出含糊語句，例如可能、應該、大約。
4. 某一類若逐字稿中完全沒有提到，該陣列回傳空陣列，不要編造。

分類定義：
- buy：影片中明講「今天」執行的買入。
- sell：影片中明講「今天」執行的賣出。
- watch：明講不碰、觀望、先看看的標的。
- holdings：明確說「會員目前持有」或語意明顯等同的股票。

name 欄位請填逐字稿裡實際聽到的名稱，即使你覺得可能是同音錯字也照填，不要自行更正。
code 欄位若逐字稿中講者有明講代號就填，沒有就留空字串。
不要自己回想或推測代號，比對官方清單是後續程式的工作。

只擷取「單一上市櫃公司」。以下這些不是個股，不要放進來：
- 集團或控股：台塑集團、鴻海集團、遠東集團
- 族群或概念：高速傳輸股、AI概念股、權值股、航運股、記憶體族群
- 產業或技術縮寫：PMIC、ABF、CoWoS、HBM、光通訊
- 指數與市場泛稱：大盤、加權指數、台股、美股、期貨、選擇權
若講者只說了族群而沒有指名個股，該類就當作沒有提到，不要硬找一檔填。

price 或 reason 若逐字稿未提及，填「未說明」。

逐字稿是完整的一小時內容。請從頭掃到尾，中段與後段的操作紀錄一樣重要，不可只看開頭。

只回傳 JSON，不要有其他文字：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":""}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch": [{"name":"", "code":"", "price":"", "reason":""}],
  "holdings": [{"name":"", "code":"", "stance":"", "note":""}]
}"""


ARTICLE_SYSTEM = """你依據逐字稿與已擷取的操作紀錄，撰寫一份每日整理文字稿。

章節順序固定，不可增刪或調換：
① 文章標題
② 基本資訊
③ 盤勢總覽重點整理
④ 會員操作紀錄與持股明細
   ④-1 影片中明講之「今日買賣紀錄」
        僅記錄本支影片中有明講「今天」執行的買入或賣出。
        若未提及任何進出紀錄，寫：「本支影片未說明當日具體買賣紀錄。」
        表格欄位固定：股票名稱、代號、方向、價位說明、理由摘錄。
        某欄位影片未提供資料就填「未說明」。
   ④-2 影片中明講之「會員目前持有股票」
        僅列出影片中有明確說「會員目前持有」或明顯語意等同的股票。
        若影片中完全沒提會員持股，寫：「本支影片未說明會員目前持股清單。」
        表格欄位固定：股票名稱、代號、目前立場、說明重點。
⑤ 分析師操作邏輯與教學重點
⑥ 風險揭露與重要提醒
⑦ 會員手中目前持有股票總表
   把 ④-2 的持股，加上 ④-1 當日買入的標的，合併列成一張表。
   與 ④ 重複是正常的，不需要迴避。
   表格欄位固定：股票名稱、代號、來源（影片明講持有／今日買入）。
   若兩者都沒有，寫：「本支影片未說明。」

所有表格使用 Markdown 表格。
代號一律直接抄用我提供的「已擷取的操作紀錄」裡的 code 欄位，那是比對過官方清單的結果。
不要自己判斷或修改代號。code 為「代號待確認」時就照樣寫「代號待確認」。

嚴格禁止新增逐字稿中沒有的資訊，禁止提供任何投資建議、目標價或看多看空判斷。
全文繁體中文，直接輸出，不要加開場白。
不要使用 emoji，不要使用破折號，項目符號一律用實心圓點或數字。"""


def extract_signals(v2: str, date_str: str) -> dict:
    raw = call_gemini(
        EXTRACT_SYSTEM,
        f"影片日期：{date_str}\n\n完整逐字稿：\n{v2}",
        want_json=True, thinking=0, tag="extract",
    )
    raw = re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def build_article(v2: str, signals: dict, date_str: str) -> str:
    clean = {k: v for k, v in signals.items() if not k.startswith("_")}
    return call_gemini(
        ARTICLE_SYSTEM,
        f"影片日期：{date_str}\n\n"
        f"已擷取的操作紀錄（代號已比對官方清單，請直接引用，不要改動）：\n"
        f"{json.dumps(clean, ensure_ascii=False, indent=2)}\n\n"
        f"完整逐字稿：\n{v2}",
        thinking=0, tag="article",
    )


# ---------------------------------------------------------------- #
# 寫入
# ---------------------------------------------------------------- #
def write_results(ss, date_str, signals, article, done_trades, done_holds):
    video_id = signals.get("_video_id", "")

    if date_str in done_trades:
        print(f"{date_str} 操作紀錄已存在，不重複寫入")
    else:
        rows = []
        for key, label in (("buy", "買入"), ("sell", "賣出"), ("watch", "不碰／觀望")):
            for r in signals.get(key, []):
                rows.append([date_str, r.get("name", ""), r.get("code", UNRESOLVED), label,
                             r.get("price", "未說明"), r.get("reason", "未說明"), video_id])
        if rows:
            sheets_retry(ss.worksheet("操作紀錄").append_rows, rows)
        print(f"操作紀錄寫入 {len(rows)} 筆")
        done_trades.add(date_str)

    if date_str in done_holds:
        print(f"{date_str} 會員持股已存在，不重複寫入")
    else:
        holds = [[date_str, r.get("name", ""), r.get("code", UNRESOLVED),
                  r.get("stance", "未說明"), r.get("note", "未說明"), video_id]
                 for r in signals.get("holdings", [])]
        if holds:
            sheets_retry(ss.worksheet("會員持股").append_rows, holds)
        print(f"會員持股寫入 {len(holds)} 筆")
        done_holds.add(date_str)

    if date_str not in existing_dates(ss, "每日推播內容"):
        sheets_retry(ss.worksheet("每日推播內容").append_row,
                     [date_str, cell(article or f"本日內容：{NOT_MENTIONED}。"), "待寄送"])


# ---------------------------------------------------------------- #
# 兩階段處理
# ---------------------------------------------------------------- #
def stage_transcript(ss, video, date_str):
    """
    階段一：取得逐字稿。最貴也最容易壞的一段。
    雲端已經有修飾後逐字稿就直接沿用，不重跑。
    """
    v1, v2 = existing_transcript(ss, video["id"], date_str)
    if v2 and len(v2) > 200:
        print(f"{date_str} 雲端已有修飾後逐字稿 {len(v2)} 字，略過轉錄與潤飾")
        return v1, v2

    if v1 and len(v1) > 200:
        print(f"{date_str} 已有原始逐字稿 {len(v1)} 字但缺修飾後版本，只補潤飾")
    else:
        mode = "長逾時" if INDEX_TIMEOUT == FULL_TIMEOUT else "輪詢短逾時"
        print(f"向 NotebookLM 索取逐字稿（{mode}，上限 {INDEX_TIMEOUT} 秒）")
        v1 = asyncio.run(fetch_fulltext(video["url"], f"張震_{date_str}", INDEX_TIMEOUT))
        print(f"取得原始逐字稿 {len(v1)} 字")
        if len(v1) < SHORT_TRANSCRIPT_HINT:
            print(f"警告：逐字稿僅 {len(v1)} 字，對一小時直播而言偏短。"
                  f"可能是 NotebookLM 索引不完整，或這支影片本身就短。")

    v2 = polish(v1)
    write_transcripts(ss, video["id"], v1, v2)   # 潤飾完立刻落地，下次就不用重跑
    return v1, v2


def stage_extract(ss, video, date_str, v2, done_trades, done_holds):
    """階段二：擷取結構化紀錄。與階段一分開，因為它便宜、可重跑。"""
    if date_str in done_trades and date_str in done_holds:
        print(f"{date_str} 操作紀錄與會員持股都已存在，略過擷取")
        return

    signals = extract_signals(v2, date_str)
    signals = resolve_signals(signals)
    signals["_video_id"] = video["id"]
    article = build_article(v2, signals, date_str)
    write_results(ss, date_str, signals, article, done_trades, done_holds)


def process_one(ss, video, done_trades, done_holds):
    date_str = video["date"].strftime("%Y/%m/%d")
    print(f"\n=== 處理 {date_str}　{video['title']}　{video['id']} ===")

    try:
        _, v2 = stage_transcript(ss, video, date_str)
    except NotReadyYet as e:
        # 這不是失敗。VOD 還在轉檔，下一輪會再敲一次門。
        mark_status(ss, video["id"], date_str, video["title"], "等待中", str(e)[:200])
        print(f"尚未就緒：{e}")
        print("這是正常的，直播結束後 YouTube 要一段時間轉檔。下一輪排程會再試。")
        raise
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise

    try:
        mark_status(ss, video["id"], date_str, video["title"], "處理中")
        stage_extract(ss, video, date_str, v2, done_trades, done_holds)
        mark_status(ss, video["id"], date_str, video["title"], "完成")
        print(f"完成 {video['id']}")
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise


# ---------------------------------------------------------------- #
# 純修代號
# ---------------------------------------------------------------- #
def repair_codes_only(ss):
    """
    不碰 NotebookLM，不呼叫 Gemini，只把試算表既有的股票名稱
    重跑一次 resolve_code。

    非個股（台塑集團、PMIC、高速傳輸股）整列刪除，不留在資料裡。
    由後往前刪，這樣刪掉一列不會讓還沒處理的列號位移。
    """
    get_code_map()

    total = {"fixed": 0, "ok": 0, "still": 0, "deleted": 0}
    unresolved, deleted = [], []

    for sheet_name, name_col, code_col in (("操作紀錄", 2, 3), ("會員持股", 2, 3)):
        ws = ss.worksheet(sheet_name)
        values = sheets_retry(ws.get_all_values)
        if len(values) < 2:
            print(f"{sheet_name} 是空的，略過")
            continue

        print(f"\n=== {sheet_name}　{len(values) - 1} 列 ===")

        keep_rows, to_delete = [], []

        for i, row in enumerate(values[1:], start=2):
            old_name = (row[name_col - 1] if len(row) >= name_col else "").strip()
            old_code = (row[code_col - 1] if len(row) >= code_col else "").strip()

            if not old_name:
                to_delete.append(i)
                total["deleted"] += 1
                continue

            code, fixed, how = resolve_code(old_name, old_code)

            if code == REJECT:
                to_delete.append(i)
                total["deleted"] += 1
                deleted.append((sheet_name, i, old_name, how.replace("剔除：", "")))
                print(f"  第 {i:>3} 列　{old_name} -> 刪除（{how.replace('剔除：', '')}）")
                continue

            new_row = list(row) + [""] * (max(name_col, code_col) - len(row))
            new_row[name_col - 1] = fixed
            new_row[code_col - 1] = code
            keep_rows.append(new_row)

            if code == UNRESOLVED:
                total["still"] += 1
                unresolved.append((sheet_name, i, old_name, how))
                print(f"  第 {i:>3} 列　{old_name} -> 仍待確認（{how}）")
            elif fixed != old_name or code != old_code:
                total["fixed"] += 1
                print(f"  第 {i:>3} 列　{old_name}（{old_code or '空白'}）"
                      f" -> {fixed}（{code}）　{how}")
            else:
                total["ok"] += 1

        # 由後往前刪，避免列號位移
        for r in sorted(to_delete, reverse=True):
            sheets_retry(ws.delete_rows, r)
        if to_delete:
            print(f"  已刪除 {len(to_delete)} 列非個股")

        # 刪完之後才寫回名稱與代號，此時列號已經重新對齊
        if keep_rows:
            width = len(values[0])
            padded = [r[:width] + [""] * (width - len(r)) for r in keep_rows]
            sheets_retry(ws.update, range_name=f"A2:{chr(64 + width)}{len(padded) + 1}",
                         values=padded)
            print(f"  已寫回 {len(padded)} 列")

    print("\n" + "=" * 56)
    print(f"修正 {total['fixed']} 筆，本來就正確 {total['ok']} 筆，"
          f"仍待確認 {total['still']} 筆，刪除非個股 {total['deleted']} 筆")

    if deleted:
        print("\n已刪除的非個股：")
        for sheet, row, name, why in deleted:
            print(f"  {sheet} 原第 {row} 列　{name}　{why}")

    if unresolved:
        print("\n以下是個股但對不上，需要人工看影片填入代號：")
        for sheet, row, name, how in unresolved:
            print(f"  {sheet} 第 {row} 列　{name}　{how}")
        print("\n填法：直接在試算表的「代號」欄填四位數字，「股票名稱」欄改成正式簡稱，")
        print("      然後回 Apps Script 執行 rebuildHoldingsTrackerJob()。")

    print("\n下一步：回到 Apps Script 執行 rebuildHoldingsTrackerJob()。")
    return total


# ---------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------- #
def main():
    src = "Variables" if os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() else "內建預設值"
    print(f"頻道 ID：{CHANNEL_ID}（{src}）")

    ss = open_sheets()

    if REPAIR_CODES:
        print("模式：純修代號。不碰 NotebookLM，不呼叫 Gemini。")
        repair_codes_only(ss)
        return

    feed = [v for v in fetch_feed() if is_target(v["title"])]
    print(f"RSS 取得 {len(feed)} 支符合關鍵字的影片")

    old = [v for v in feed if v["date"] < MIN_DATE]
    feed = [v for v in feed if v["date"] >= MIN_DATE]
    if old:
        print(f"略過 {len(old)} 支 {MIN_DATE:%Y/%m/%d} 之前的舊影片："
              + "、".join(v["date"].strftime("%Y/%m/%d") for v in old))
    print(f"待處理範圍內共 {len(feed)} 支")

    if not feed:
        raise RuntimeError(
            f"RSS 沒有任何標題含 {TITLE_KEYWORDS} 且日期在 {MIN_DATE:%Y/%m/%d} 之後的影片，"
            f"請確認頻道 ID 與關鍵字設定。"
        )

    done = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}
    done_trades = existing_dates(ss, "操作紀錄")
    done_holds = existing_dates(ss, "會員持股")
    print(f"雲端已有操作紀錄 {len(done_trades)} 天、會員持股 {len(done_holds)} 天")

    if BACKFILL:
        targets = [v for v in feed if done.get(v["id"]) != "完成"]
        if not targets:
            print("沒有需要回補的影片")
            return
        print(f"回補模式：共 {len(targets)} 支")
        targets.sort(key=lambda v: v["date"])       # 由舊到新，維持試算表時序
        for v in targets:
            process_one(ss, v, done_trades, done_holds)
        return

    today = datetime.now(TAIPEI).date()
    now_h = datetime.now(TAIPEI).hour
    todays = [v for v in feed if v["date"] == today]

    if not todays:
        if now_h >= GIVE_UP_HOUR:
            mark_status(ss, f"NO_VIDEO_{today}", today.strftime("%Y/%m/%d"), "", "今日無影片")
            print("今日無影片")
        else:
            print(f"RSS 尚未出現 {today} 的影片，等下一輪")
        return

    v = todays[0]
    status = done.get(v["id"], "")

    if status == "完成":
        print("今日影片已處理完成，本輪無事可做")
        return
    if status == "處理中":
        print("偵測到前次殘留的『處理中』狀態，重新處理")
    if status == "等待中":
        print("前一輪 VOD 尚未就緒，本輪再敲一次門")

    process_one(ss, v, done_trades, done_holds)


if __name__ == "__main__":
    try:
        main()
    except NotReadyYet as e:
        # 綠燈離開。VOD 還沒好不是壞掉，不該亮紅燈，也不該觸發失敗告警。
        print(f"本輪未取得逐字稿：{e}")
        sys.exit(0)
    except Exception as e:
        print(f"流程失敗：{e}", file=sys.stderr)
        sys.exit(1)
