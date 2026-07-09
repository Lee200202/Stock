"""
資料蒐集後端主流程（GitHub Actions 執行）

對應規格書 v3 二、1.3 節與三、3.1 到 3.4 節。

紅線：所有輸出僅能根據影片中明確講述的內容產生，不可自行推論或補完。
金鑰：全部從環境變數讀取，不得寫入程式碼，不得印進 workflow logs。

需要你修改的地方共 3 處，都標了「【要改】」，直接搜尋這三個字就找得到。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials

TAIPEI = timezone(timedelta(hours=8))
NOT_MENTIONED = "本支影片未說明"

# 【要改】1：你的 YouTube 頻道 ID。
# 到 youtube.com/@xinchenginsta 頁面按右鍵檢視原始碼，搜尋 "channelId"，
# 會看到一段 UCxxxxxxxxxxxxxxxxxxxxxx，把它填進 GitHub 的 Variables（不是 Secrets），
# 名稱 YOUTUBE_CHANNEL_ID。沒設定的話就用下面這個預設值。
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()

# 【要改】2：影片標題關鍵字。RSS 會回傳頻道所有近期影片，
# 只有標題命中這裡列出的任一關鍵字才會被當成當日直播來處理。
TITLE_KEYWORDS = ["盤中", "家教班"]

# 【要改】3：Gemini 模型。想省錢就留 flash，想要更強的語意判斷就換成 pro 系列。
GEMINI_MODEL = "gemini-2.5-flash"

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
BACKFILL = os.environ.get("BACKFILL", "false").lower() == "true"


# ---------------------------------------------------------------- #
# 試算表
# ---------------------------------------------------------------- #
def open_sheets():
    info = json.loads(os.environ["GOOGLE_SHEETS_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)


def video_rows(ss):
    return ss.worksheet("影片清單").get_all_records()


def find_video_row(ss, video_id):
    ws = ss.worksheet("影片清單")
    for idx, row in enumerate(ws.get_all_records(), start=2):
        if str(row.get("影片ID")) == video_id:
            return ws, idx
    return ws, None


def mark_status(ss, video_id, published, title, status, reason=""):
    ws, idx = find_video_row(ss, video_id)
    if idx is None:
        ws.append_row([video_id, published, title, status, reason, "", ""])
    else:
        ws.update(f"D{idx}:E{idx}", [[status, reason]])


def write_transcripts(ss, video_id, v1, v2):
    ws, idx = find_video_row(ss, video_id)
    if idx:
        ws.update(f"F{idx}:G{idx}", [[v1[:49000], v2[:49000]]])


# ---------------------------------------------------------------- #
# 影片偵測（YouTube 公開 RSS，不需金鑰，不下載影音）
# ---------------------------------------------------------------- #
def fetch_feed():
    if not CHANNEL_ID:
        raise RuntimeError("尚未設定 YOUTUBE_CHANNEL_ID。")
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

    out = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns).text
        title = e.find("a:title", ns).text or ""
        published = e.find("a:published", ns).text
        dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(TAIPEI)
        out.append({"id": vid, "title": title, "date": dt.date(), "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def is_target(title):
    return any(k in title for k in TITLE_KEYWORDS)


# ---------------------------------------------------------------- #
# 逐字稿：notebooklm-py 來源全文存取
# ---------------------------------------------------------------- #
async def fetch_fulltext(video_url, title):
    from notebooklm import NotebookLMClient  # 由 NOTEBOOKLM_AUTH_JSON 環境變數讀取授權

    async with NotebookLMClient.from_storage() as client:
        notebook = await client.notebooks.create(title=title)
        try:
            source = await client.sources.add_url(notebook.id, video_url, wait=True, wait_timeout=1800)
            fulltext = await client.sources.get_fulltext(notebook.id, source.id)
            return fulltext.content
        finally:
            try:
                await client.notebooks.delete(notebook.id)
            except Exception:
                pass


# ---------------------------------------------------------------- #
# Gemini：文字修飾 + 結構化擷取
# ---------------------------------------------------------------- #
def call_gemini(system_text, user_text, want_json=False):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192},
    }
    if want_json:
        body["generationConfig"]["responseMimeType"] = "application/json"

    for attempt, delay in enumerate([0, 5, 15]):
        if delay:
            time.sleep(delay)
        r = requests.post(url, json=body, timeout=180)
        if r.status_code == 200:
            data = r.json()
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        # 錯誤訊息不含金鑰，也不印出回應內容
        if r.status_code != 429 and r.status_code < 500:
            raise RuntimeError(f"Gemini 呼叫失敗，HTTP {r.status_code}")
    raise RuntimeError("Gemini 呼叫連續失敗 3 次")


POLISH_SYSTEM = """你負責整理一段中文直播的逐字稿。
只做三件事：修正同音錯字、補上合理的斷句與標點、刪除純粹的口頭贅字。
嚴格禁止：新增任何事實資訊、刪除任何事實資訊、改寫語意、摘要、補完語意不清的地方。
若某處聽起來像是股票名稱但拼字有誤，可依常見台股名稱修正，其餘一律照原文保留。
全文使用繁體中文，直接輸出整理後的逐字稿，不要加任何說明。"""

EXTRACT_SYSTEM = """你從一段直播逐字稿中，擷取講者「明確講出」操作紀錄。

嚴格禁止：
1. 禁止創造逐字稿中沒有提到的股票名稱、價位、操作紀錄或會員持股。
2. 禁止引用其他日期或其他來源的內容。
3. 禁止產出含糊語句，例如可能、應該、大約。
4. 某一類若逐字稿中完全沒有提到，該陣列回傳空陣列，不要編造。

只回傳 JSON，格式如下，不要有其他文字：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":""}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch": [{"name":"", "code":"", "price":"", "reason":""}],
  "holdings": [{"name":"", "code":"", "stance":"", "note":""}],
  "article": ""
}
code 欄位若無法確定對應代號，填「代號待確認」，不要猜。
price 或 reason 若逐字稿未提及，填「未說明」。
article 欄位放一份依規範格式撰寫的完整文字稿，章節順序為：①文章標題 ②基本資訊
③盤勢總覽重點整理 ④會員操作紀錄與持股明細 ⑤分析師操作邏輯與教學重點 ⑥風險揭露與重要提醒。
④ 底下使用 Markdown 表格。任何一段若逐字稿未提及，寫「本段內容：本支影片未說明，故不予記錄。」"""


def polish_and_extract(transcript_v1, date_str):
    v2 = call_gemini(POLISH_SYSTEM, transcript_v1)
    raw = call_gemini(EXTRACT_SYSTEM, f"影片日期：{date_str}\n\n逐字稿：\n{v2}", want_json=True)
    raw = re.sub(r"^```json|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return v2, json.loads(raw)


# ---------------------------------------------------------------- #
# 寫入
# ---------------------------------------------------------------- #
def write_results(ss, video_id, date_str, v1, v2, signals):
    write_transcripts(ss, video_id, v1, v2)

    trades = ss.worksheet("操作紀錄")
    rows = []
    for d, label in (("buy", "買入"), ("sell", "賣出"), ("watch", "不碰／觀望")):
        for r in signals.get(d, []):
            rows.append([date_str, r.get("name", ""), r.get("code", "代號待確認"), label,
                         r.get("price", "未說明"), r.get("reason", "未說明"), video_id])
    if rows:
        trades.append_rows(rows)

    holds = [[date_str, r.get("name", ""), r.get("code", "代號待確認"),
              r.get("stance", "未說明"), r.get("note", "未說明"), video_id]
             for r in signals.get("holdings", [])]
    if holds:
        ss.worksheet("會員持股").append_rows(holds)

    article = signals.get("article") or f"本日內容：{NOT_MENTIONED}。"
    ss.worksheet("每日推播內容").append_row([date_str, article[:49000], "待寄送"])


# ---------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------- #
def process_one(ss, video):
    date_str = video["date"].strftime("%Y/%m/%d")
    mark_status(ss, video["id"], date_str, video["title"], "處理中")
    try:
        v1 = asyncio.run(fetch_fulltext(video["url"], f"張震_{date_str}"))
        if not v1 or len(v1) < 200:
            raise RuntimeError("取回的逐字稿全文過短或為空，視為索引失敗")
        v2, signals = polish_and_extract(v1, date_str)
        write_results(ss, video["id"], date_str, v1, v2, signals)
        mark_status(ss, video["id"], date_str, video["title"], "完成")
        print(f"完成 {video['id']}")
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise


def main():
    ss = open_sheets()
    feed = [v for v in fetch_feed() if is_target(v["title"])]
    done = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}

    if BACKFILL:
        targets = [v for v in feed if done.get(v["id"]) != "完成"]
        if not targets:
            print("沒有需要回補的影片")
            return
        for v in targets:
            process_one(ss, v)
        return

    today = datetime.now(TAIPEI).date()
    todays = [v for v in feed if v["date"] == today]

    if not todays:
        # 只有過了 13:00 才判定今天真的沒有直播，避免 RSS 尚未更新就誤判
        if datetime.now(TAIPEI).hour >= 13:
            mark_status(ss, f"NO_VIDEO_{today}", today.strftime("%Y/%m/%d"), "", "今日無影片")
            print("今日無影片")
        else:
            print("RSS 尚未出現今日影片，等下一輪")
        return

    v = todays[0]
    status = done.get(v["id"], "")
    if status == "完成":
        print("今日影片已處理完成")
        return
    if status == "處理中":
        print("另一個任務正在處理同一支影片")
        return

    process_one(ss, v)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"流程失敗：{e}", file=sys.stderr)
        sys.exit(1)   # 讓 GitHub Actions 顯示為失敗，觸發 GAS 端的告警檢查
