"""只讀不寫的重播：用真的模型，把某一天存好的原始逐字稿重新跑一次「擷取→稽核→代號比對→品質關卡→最終分類」，
印出最後會寫進網站的每一檔分類與說明，並和人工核對過的答案比對。

試算表只讀：所有寫入（操作紀錄、會員持股、每日整理、判定歷程、名稱判定紀錄、辨識日誌）都被攔下。
一次大約 8～10 次模型呼叫。

    python scripts/replay_extract.py --date 2026/10/06
    python scripts/replay_extract.py --date 2026/10/06 --golden scripts/golden/2026-10-06.json

golden 檔的格式見 scripts/golden/README.md。
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("pipeline_replay", ROOT / "pipeline" / "pipeline.py")
pl = importlib.util.module_from_spec(spec)
sys.modules["pipeline_replay"] = pl
spec.loader.exec_module(pl)

READS = {"get_all_values", "get_all_records", "row_values", "col_values", "get", "get_values", "batch_get",
         "acell", "cell", "find", "findall", "title", "row_count", "col_count", "id", "url"}
LABEL = {"buy": "買入", "sell": "賣出", "holdings": "會員持股", "watch_watch": "觀望注意", "watch_avoid": "觀望不碰"}


class DrySheet:
    def __init__(self, ws, log):
        self._ws, self._log = ws, log

    def __getattr__(self, name):
        if name in READS:
            return getattr(self._ws, name)

        def blocked(*a, **kw):
            self._log.append(f"{self._ws.title}.{name}")
        return blocked


class DryBook:
    def __init__(self, ss):
        self._ss, self.blocked, self._sheets = ss, [], {}

    def worksheet(self, name):
        if name not in self._sheets:
            self._sheets[name] = DrySheet(self._ss.worksheet(name), self.blocked)
        return self._sheets[name]

    def worksheets(self):
        return [self.worksheet(w.title) for w in self._ss.worksheets()]

    def __getattr__(self, name):
        if name in ("title", "id", "url"):
            return getattr(self._ss, name)

        def blocked(*a, **kw):
            self.blocked.append(f"spreadsheet.{name}")
            raise RuntimeError(f"唯讀重播不允許 spreadsheet.{name}")
        return blocked


class Reached(Exception):
    """流程走到寫入前一步，帶著最終結果離開。"""

    def __init__(self, signals):
        self.signals = signals


def compare(signals, golden):
    """回傳 (通過與否, 文字報告)。golden：{"must": {代號: [可接受的分類…]}, "must_not": [代號…], "names": {代號: 名稱}}"""
    where = {}
    for cat in LABEL:
        for r in signals.get(cat, []) or []:
            where.setdefault(str(r.get("code") or r.get("name")), []).append(cat)
    names = golden.get("names", {})
    lines, ok = [], True
    for code, allowed in golden.get("must", {}).items():
        got = where.get(code, [])
        good = any(c in allowed for c in got)
        ok &= good
        lines.append(f"  {'✓' if good else '✗'} {names.get(code, '')}（{code}）應為 {'／'.join(LABEL[a] for a in allowed)}，"
                     f"實際 {'／'.join(LABEL[g] for g in got) or '沒有收錄'}")
    for code in golden.get("must_not", []):
        bad = code in where
        ok &= not bad
        lines.append(f"  {'✗' if bad else '✓'} {names.get(code, '')}（{code}）不應出現" + (f"，實際列在 {'／'.join(LABEL[g] for g in where[code])}" if bad else ""))
    extra = [c for c in where if c not in golden.get("must", {}) and c not in golden.get("must_not", [])]
    if extra:
        lines.append("  其他收錄（答案沒有列，僅供參考）：" + "、".join(extra))
    return ok, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--golden", default="")
    args = ap.parse_args()
    date = pl.norm_date(args.date)

    book = DryBook(pl.open_sheets())
    snap = {}
    raw, polished = pl.existing_transcript(book, "", date, snapshot=snap)
    if not raw:
        raise SystemExit(f"{date} 沒有原始逐字稿")

    def stop(ss, date_str, signals, *a, **kw):
        raise Reached(signals)
    pl.write_results = stop
    pl.save_evidence_audit = lambda *a, **kw: None
    pl.save_refresh_checkpoint = lambda *a, **kw: None
    try:
        pl.name_memo_load(book)
    except Exception as e:
        print(f"名稱判定紀錄讀取略過（{type(e).__name__}）")

    print(f"===== 重播 {date}：原始逐字稿 {len(raw)} 字（唯讀，不寫入） =====")
    try:
        pl._stage_extract_impl(book, {"id": snap.get("video_id", ""), "title": ""}, date, polished or raw,
                               set(), set(), replace_video=False, v1=raw)
        raise SystemExit("流程沒有走到寫入前一步（可能判定這一天已經做完）")
    except Reached as r:
        signals = r.signals

    print(f"\n===== 重播結果 {date}（沒有寫進試算表；攔下寫入 {len(book.blocked)} 次） =====")
    for cat, label in LABEL.items():
        rows = signals.get(cat, []) or []
        print(f"\n【{label}】{len(rows)} 檔")
        for r in rows:
            note = str(r.get("reason") or r.get("note") or "")
            print(f"- {r.get('name')}（{r.get('code')}）{len(note)} 字：{note}")
    print("\n" + pl.gemini_usage_report())
    if args.golden:
        golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
        ok, report = compare(signals, golden)
        print(f"\n===== 與人工核對的答案比對：{'全部符合' if ok else '有不符'} =====\n{report}")
        if not ok:
            sys.exit(2)


if __name__ == "__main__":
    main()
