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
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
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
async def fetch_fulltext(video_url, title):
    from notebooklm import NotebookLMClient

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

逐字稿是完整的一小時內容。請從頭掃到尾，中段與後段的操作紀錄一樣重要，不可只看開頭。

只回傳 JSON，不要有其他文字：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":""}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch": [{"name":"", "code":"", "price":"", "reason":""}],
  "holdings": [{"name":"", "code":"", "stance":"", "note":""}]
}

code 若無法確定對應代號，填「代號待確認」，不要猜。
price 或 reason 若逐字稿未提及，填「未說明」。"""


ARTICLE_SYSTEM = """你依據逐字稿與已擷取的操作紀錄，撰寫一份每日整理文字稿。

章節順序固定，不可增刪或調換：
① 文章標題
② 基本資訊
③ 盤勢總覽重點整理
④ 會員操作紀錄與持股明細
⑤ 分析師操作邏輯與教學重點
⑥ 風險揭露與重要提醒

④ 使用 Markdown 表格。
任何一段若逐字稿未提及，該段寫「本段內容：本支影片未說明，故不予記錄。」

嚴格禁止新增逐字稿中沒有的資訊，禁止提供任何投資建議、目標價或看多看空判斷。
全文繁體中文，直接輸出，不要加開場白。"""


def extract_signals(v2: str, date_str: str) -> dict:
    raw = call_gemini(
        EXTRACT_SYSTEM,
        f"影片日期：{date_str}\n\n完整逐字稿：\n{v2}",
        want_json=True, thinking=0, tag="extract",
    )
    raw = re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def build_article(v2: str, signals: dict, date_str: str) -> str:
    return call_gemini(
        ARTICLE_SYSTEM,
        f"影片日期：{date_str}\n\n"
        f"已擷取的操作紀錄：\n{json.dumps(signals, ensure_ascii=False, indent=2)}\n\n"
        f"完整逐字稿：\n{v2}",
        thinking=0, tag="article",
    )


# ---------------------------------------------------------------- #
# 寫入
# ---------------------------------------------------------------- #
def write_results(ss, video_id, date_str, v1, v2, signals, article):
    write_transcripts(ss, video_id, v1, v2)

    rows = []
    for key, label in (("buy", "買入"), ("sell", "賣出"), ("watch", "不碰／觀望")):
        for r in signals.get(key, []):
            rows.append([date_str, r.get("name", ""), r.get("code", "代號待確認"), label,
                         r.get("price", "未說明"), r.get("reason", "未說明"), video_id])
    if rows:
        sheets_retry(ss.worksheet("操作紀錄").append_rows, rows)

    holds = [[date_str, r.get("name", ""), r.get("code", "代號待確認"),
              r.get("stance", "未說明"), r.get("note", "未說明"), video_id]
             for r in signals.get("holdings", [])]
    if holds:
        sheets_retry(ss.worksheet("會員持股").append_rows, holds)

    sheets_retry(ss.worksheet("每日推播內容").append_row,
                 [date_str, cell(article or f"本日內容：{NOT_MENTIONED}。"), "待寄送"])

    print(f"寫入完成：操作紀錄 {len(rows)} 筆，會員持股 {len(holds)} 筆")


# ---------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------- #
def process_one(ss, video):
    date_str = video["date"].strftime("%Y/%m/%d")
    print(f"\n=== 處理 {date_str}　{video['title']}　{video['id']} ===")
    mark_status(ss, video["id"], date_str, video["title"], "處理中")

    try:
        v1 = asyncio.run(fetch_fulltext(video["url"], f"張震_{date_str}"))
        if not v1 or len(v1) < 200:
            raise RuntimeError(f"取回的逐字稿全文僅 {len(v1 or '')} 字，視為索引失敗")
        print(f"取得原始逐字稿 {len(v1)} 字")
        if len(v1) < SHORT_TRANSCRIPT_HINT:
            print(f"警告：逐字稿僅 {len(v1)} 字，對一小時直播而言偏短。"
                  f"可能是 NotebookLM 索引不完整，或這支影片本身就短。")

        v2 = polish(v1)
        signals = extract_signals(v2, date_str)
        article = build_article(v2, signals, date_str)

        write_results(ss, video["id"], date_str, v1, v2, signals, article)
        mark_status(ss, video["id"], date_str, video["title"], "完成")
        print(f"完成 {video['id']}")
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise


def main():
    src = "Variables" if os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() else "內建預設值"
    print(f"頻道 ID：{CHANNEL_ID}（{src}）")

    ss = open_sheets()
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

    if BACKFILL:
        targets = [v for v in feed if done.get(v["id"]) != "完成"]
        if not targets:
            print("沒有需要回補的影片")
            return
        print(f"回補模式：共 {len(targets)} 支")
        targets.sort(key=lambda v: v["date"])       # 由舊到新，維持試算表時序
        for v in targets:
            process_one(ss, v)
        return

    today = datetime.now(TAIPEI).date()
    todays = [v for v in feed if v["date"] == today]

    if not todays:
        if datetime.now(TAIPEI).hour >= 13:
            mark_status(ss, f"NO_VIDEO_{today}", today.strftime("%Y/%m/%d"), "", "今日無影片")
            print("今日無影片")
        else:
            print(f"RSS 尚未出現 {today} 的影片，等下一輪")
        return

    v = todays[0]
    status = done.get(v["id"], "")
    if status == "完成":
        print("今日影片已處理完成")
        return
    if status == "處理中":
        # daily.yml 的 concurrency 已保證不會有平行執行，
        # 所以「處理中」只可能是前一次執行中途崩潰留下的殘留，直接重跑。
        print("偵測到前次殘留的『處理中』狀態，重新處理")

    process_one(ss, v)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"流程失敗：{e}", file=sys.stderr)
        sys.exit(1)
