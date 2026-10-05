"""只讀不寫的實測：用真的模型，把某一天會員簡訊列的說明重寫一次，印出送進去的材料與寫出來的結果。

用途：確認「說明重點」的 prompt 在材料足夠時寫得出完整內容，而不是只留一句操作結論。
試算表只讀；任何寫入都被攔下來改成印出。會花 1 次模型呼叫。

    python scripts/audit_note_prompt.py --date 2026/10/05 \
        --baseline "8210=資金轉為買進。" --alias "行神=8210" --alias "程成=8210"

--baseline  把該代號那一列的說明換成指定文字再送（模擬只有簡訊那一句的起點）。
--alias     這次執行暫時把某個聽寫寫法當成該代號（模擬股名聽對的稿子），不改任何設定。
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pipeline_audit", ROOT / "pipeline" / "pipeline.py")
pl = importlib.util.module_from_spec(spec)
sys.modules["pipeline_audit"] = pl
spec.loader.exec_module(pl)

READS = ("get_all_values", "get_all_records", "row_values", "col_values", "title", "row_count", "col_count")


class ReadOnlySheet:
    def __init__(self, ws, overrides):
        self._ws, self._overrides, self.attempted = ws, overrides, []

    def get_all_values(self):
        values = self._ws.get_all_values()
        if not values or not self._overrides or "理由摘錄" not in values[0] or "代號" not in values[0]:
            return values
        code_i, note_i, date_i = values[0].index("代號"), values[0].index("理由摘錄"), values[0].index("日期")
        src_i = values[0].index("來源影片ID")
        for row in values[1:]:
            if (len(row) > max(code_i, note_i, src_i) and row[date_i] == self._overrides["date"]
                    and str(row[src_i]).startswith("CMONEY-") and row[code_i] in self._overrides["notes"]):
                row[note_i] = self._overrides["notes"][row[code_i]]
        return values

    def batch_update(self, changes, **kw):
        self.attempted.extend(changes)          # 攔下來，不送出

    def __getattr__(self, name):
        if name in READS:
            return getattr(self._ws, name)
        raise AttributeError(f"唯讀稽核不允許 {name}")


class ReadOnlyBook:
    def __init__(self, ss, overrides):
        self._ss, self._overrides, self.sheets = ss, overrides, {}

    def worksheet(self, name):
        if name not in self.sheets:
            self.sheets[name] = ReadOnlySheet(self._ss.worksheet(name), self._overrides)
        return self.sheets[name]

    def __getattr__(self, name):
        raise AttributeError(f"唯讀稽核不允許 {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--baseline", action="append", default=[])
    ap.add_argument("--alias", action="append", default=[])
    args = ap.parse_args()
    date = pl.norm_date(args.date)
    baseline = dict(x.split("=", 1) for x in args.baseline if "=" in x)
    code_map = pl.get_code_map() or {}
    for pair in args.alias:
        heard, code = pair.split("=", 1)
        pl.CONFIRMED_NAMES[heard.strip()] = (code.strip(), code_map.get(code.strip(), code.strip()))

    book = ReadOnlyBook(pl.open_sheets(), {"date": date, "notes": baseline})
    raw, _ = pl.existing_transcript(book, "", date)
    print(f"\n===== {date} 原始逐字稿 {len(raw)} 字 =====")

    current = {}
    values = book._ss.worksheet("操作紀錄").get_all_values()
    head = values[0]
    for row in values[1:]:
        if row[head.index("日期")] == date and str(row[head.index("來源影片ID")]).startswith("CMONEY-"):
            current[row[head.index("代號")]] = (row[head.index("股票名稱")], row[head.index("理由摘錄")])

    sent = []
    real_call = pl.call_gemini

    replies = []

    def spy(system, user, **kw):
        sent.append(json.loads(user))
        out = real_call(system, user, **kw)
        replies.append(out)
        return out
    pl.call_gemini = spy
    pl.enrich_sms_notes_from_signals(book, date, {}, raw)

    for payload in sent:
        for item in payload.get("items", []):
            print(f"\n----- 送進模型：{item['stock']}（{item['code']}）{item['direction']} -----")
            print(f"簡訊原說明：{item['sms_note']}")
            print(f"影片節錄 {len(item['transcript_excerpt'])} 字：{item['transcript_excerpt'][:700]}")
    print("\n===== 模型原始輸出（還沒經過本機檢查） =====")
    for out in replies:
        data = out if isinstance(out, dict) else pl.safe_load_json(out, default={})
        for note in (data or {}).get("notes", []):
            print(f"\n[{note.get('id')}] {len(str(note.get('text') or ''))} 字\n{note.get('text')}")
    print("\n===== 模型寫出、而且通過本機檢查的說明（沒有寫進試算表） =====")
    wrote = False
    for name, sheet in book.sheets.items():
        for change in sheet.attempted:
            text = change["values"][0][0]
            if name == "操作紀錄":
                wrote = True
                print(f"\n[{name} {change['range']}] {len(text)} 字\n{text}")
    if not wrote:
        print("（沒有任何一則通過；上面的日誌會寫送出幾筆、通過幾筆）")
    print("\n===== 目前試算表上的說明（對照用） =====")
    for code, (name, note) in current.items():
        print(f"\n{name}（{code}）{len(note)} 字\n{note}")


if __name__ == "__main__":
    main()
