"""Build the public, read-only GitHub Pages mirror from the existing Sheets.

Only explicitly listed public columns are exported.  In particular, this never
reads subscriber lists, LINE IDs, delivery ledgers, admin jobs or audit sheets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "public-site"
DAY = re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$")

PUBLIC_COLUMNS = {
    "操作紀錄": ("日期", "股票名稱", "代號", "方向", "價位說明", "理由摘錄", "來源影片ID", "序"),
    "會員持股": ("日期", "股票名稱", "代號", "目前立場", "說明重點", "來源影片ID"),
    "每日推播內容": ("日期", "文字稿"),
    "會員簡訊": ("文章ID", "發文時間", "標題", "原文", "網址", "解析狀態"),
    "影片清單": ("發布日期", "標題", "處理狀態", "原始逐字稿內容", "修飾後逐字稿內容"),
    "持股追蹤": ("代號", "股票名稱", "首次買入日", "進場價", "進場價來源", "最近賣出日", "出場價", "狀態", "提及次數", "最新說明日期", "逐日說明", "回合JSON"),
    "每日績效": ("日期", "追蹤檔數", "持有檔數", "平均報酬", "正報酬比例"),
    "市場總覽快取": ("代號", "更新時間", "資料JSON", "來源", "狀態"),
    "即時快取": ("代號", "名稱", "現價", "昨收", "漲跌", "漲跌幅", "成交量", "更新時間"),
}
REQUIRED = {"操作紀錄", "會員持股", "每日推播內容", "影片清單"}


def date_key(value: str) -> str:
    text = str(value or "").strip()[:10]
    if not DAY.fullmatch(text):
        return ""
    parts = re.split(r"[/-]", text)
    return f"{int(parts[0]):04d}/{int(parts[1]):02d}/{int(parts[2]):02d}"


def read_public_rows(spreadsheet, name: str) -> list[dict[str, str]]:
    try:
        values = spreadsheet.worksheet(name).get_all_values()
    except Exception:
        if name in REQUIRED:
            raise
        return []
    if not values:
        if name in REQUIRED:
            raise ValueError(f"{name} 沒有表頭；保留上一版網站")
        return []
    header = values[0]
    missing = set(PUBLIC_COLUMNS[name]) - set(header)
    if missing:
        if name in REQUIRED:
            raise ValueError(f"{name} 缺欄 {sorted(missing)}；保留上一版網站")
        return []
    indexes = {key: header.index(key) for key in PUBLIC_COLUMNS[name]}
    return [
        {key: row[index] if index < len(row) else "" for key, index in indexes.items()}
        for row in values[1:] if any(row)
    ]


def build_snapshot(spreadsheet, now: datetime | None = None) -> dict:
    rows = {name: read_public_rows(spreadsheet, name) for name in PUBLIC_COLUMNS}
    completed: dict[str, dict] = {}
    for row in rows["影片清單"]:
        day = date_key(row["發布日期"])
        raw = row["原始逐字稿內容"].strip()
        if not day or row["處理狀態"].strip() != "完成" or len(raw) < 200:
            continue
        # 同日重複列取原文最完整的一筆；若同長，取工作表較後的一筆。
        old = completed.get(day)
        if old is None or len(raw) >= len(old["raw"]):
            completed[day] = {
                "date": day, "title": row["標題"], "raw": raw,
                "reading": row["修飾後逐字稿內容"].strip() or raw,
            }

    articles = []
    for row in rows["每日推播內容"]:
        day = date_key(row["日期"])
        # 沒影片的日子不公開一封貌似已寄的日報；會員簡訊另外呈現。
        if day in completed and row["文字稿"].strip():
            articles.append({"date": day, "body": row["文字稿"]})

    def dated(items: list[dict], key: str) -> list[dict]:
        out = []
        for row in items:
            day = date_key(row[key])
            if day:
                row = dict(row)
                row[key] = day
                out.append(row)
        return out

    market = {}
    for row in rows["市場總覽快取"]:
        if row["狀態"] != "完成" or row["代號"] not in {
            "taiex", "tx", "dxy", "usdtwd", "wti", "brent", "sectors"
        }:
            continue
        try:
            payload = json.loads(row["資料JSON"])
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            market[row["代號"]] = {
                "updated": row["更新時間"], "source": row["來源"], "data": payload
            }

    perf = [r for r in dated(rows["每日績效"], "日期") if r["日期"] >= "2026/07/08"]
    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    return {
        "generatedAt": now.isoformat(timespec="seconds"),
        "articles": sorted(articles, key=lambda r: r["date"], reverse=True),
        "transcripts": sorted(completed.values(), key=lambda r: r["date"], reverse=True),
        "trades": dated(rows["操作紀錄"], "日期"),
        "holdings": dated(rows["會員持股"], "日期"),
        "sms": rows["會員簡訊"],
        "tracker": rows["持股追蹤"],
        "performance": sorted(perf, key=lambda r: r["日期"]),
        "market": market,
        "quotes": rows["即時快取"],
    }


def write_site(snapshot: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "style.css", "app.js"):
        shutil.copyfile(ASSETS / name, output / name)
    (output / ".nojekyll").write_text("", encoding="utf-8")
    (output / "data.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


def open_spreadsheet():
    import gspread
    from google.oauth2.service_account import Credentials

    info = json.loads(os.environ["GOOGLE_SHEETS_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return gspread.authorize(creds).open_by_key(os.environ["SPREADSHEET_ID"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    snapshot = build_snapshot(open_spreadsheet())
    if not snapshot["articles"] and not snapshot["trades"]:
        raise ValueError("公開紀錄為空；保留上一版網站，不發布空白快照")
    write_site(snapshot, args.output)
    print(f"公開頁已生成：{len(snapshot['articles'])} 篇日報、"
          f"{len(snapshot['transcripts'])} 篇逐字稿、{len(snapshot['trades'])} 筆操作；"
          "未匯出訂閱名單、管理資料或金鑰")


if __name__ == "__main__":
    main()
