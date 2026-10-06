"""只讀不寫的重播：用真的模型，把某一天存好的原始逐字稿重新跑一次「擷取→稽核→代號比對→品質關卡→最終分類」，
印出最後會寫進網站的每一檔分類與說明，並和人工核對過的答案比對。

試算表只讀：所有寫入（操作紀錄、會員持股、每日整理、判定歷程、名稱判定紀錄、辨識日誌）都被攔下。
一次大約 8～10 次模型呼叫。

三種檢查，前兩種不需要人工答案，任何一天都能跑：
  通用檢查　　每一天都該成立的事（原文明講今天賣掉的要在賣出、說明不帶內部字樣、同一句不講兩遍……）。
  網站比對　　和試算表裡這一天目前的資料（網站上看到的那一份，含後台補的）逐檔對照，只列出差異。
  答案比對　　scripts/golden/ 有這一天的人工答案時才做。
結束代碼：0 全部通過；2 答案不符；3 通用檢查有錯。2 和 3 是檢查結果，不是程式壞掉。

    python scripts/replay_extract.py --date 2026/10/06
    python scripts/replay_extract.py --date 2026/10/06 --golden scripts/golden/2026-10-06.json

golden 檔的格式見 scripts/golden/README.md。
"""
import argparse
import difflib
import importlib.util
import json
import re
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


INTERNAL_WORDS = re.compile(r'待確認註記|模型|補列|初稿|覆核|逐字稿|簡訊|盤中通知|會員通知|依通知|通知指出')
POSITIVE = re.compile(r'看好|佈局|布局|買點|續抱|大漲|噴出|翻揚|可以買|值得留意|值得注意')


def invariants(signals, raw):
    """每一天都該成立的事。回傳 (錯誤, 提醒)；錯誤代表流程有洞，提醒是品質上值得看一眼。"""
    errors, warns = [], []
    rows = [(cat, r) for cat in LABEL for r in signals.get(cat, []) or [] if isinstance(r, dict)]
    placed = set()
    for cat, r in rows:
        name = str(r.get("name") or "")
        names = {name, str(r.get("原始語音名稱") or "")} | {a for a in (r.get("aliases") or []) if isinstance(a, str)}
        names = {n for n in names if len(n) >= 2}
        placed |= names | {str(r.get("code") or "")}
        note = str(r.get("reason") or r.get("note") or "")
        # 一、原文明講今天賣掉的那一檔要在賣出。
        hits = pl.explicit_today_trades(raw, names, "sell")
        strong = len(hits) >= 2 or any("會員" in h and re.search(r"通知|告訴", h) for h in hits)
        if strong and cat != "sell":
            errors.append(f"{name} 原文明講今天賣出（{hits[0][:30]}…），卻列在{LABEL[cat]}")
        # 二、說明是公開文字。
        bad = INTERNAL_WORDS.search(note)
        if bad:
            errors.append(f"{name} 說明帶內部字樣「{bad.group(0)}」：{note[:50]}")
        parts = [x for x in (pl._ev_norm(p) for p in re.split(r"[。；;]", note)) if len(x) >= 8]
        for i, a in enumerate(parts):
            if any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.82 for b in parts[i + 1:]):
                errors.append(f"{name} 說明同一句講了兩遍：{note[:60]}")
                break
        if len(pl._ev_norm(note)) < 8:
            errors.append(f"{name} 沒有說明（{len(pl._ev_norm(note))} 字）")
        elif len(pl._ev_norm(note)) < 25:
            warns.append(f"{name} 說明只有 {len(pl._ev_norm(note))} 字")
        if re.search(r"(?:^|[，。；])(?:啊|欸|來)[，,]", note):
            warns.append(f"{name} 說明是口語原句：{note[:40]}")
        if re.search(r"老師|講者|張震|張總", note):
            warns.append(f"{name} 說明提到講者：{note[:40]}")
        # 三、分類和說明的方向要一致。
        plain = pl._REVERSAL_CUE.sub("", note)
        negative = bool(pl._NEGATIVE_CUE.search(plain) or pl._PROHIBIT.search(plain)
                        or re.search(r"不(?:予)?推薦|不建議|不買|沒有買|並未買|不敢|不考慮|不用追|不追", plain))
        if cat == "watch_avoid" and not negative and POSITIVE.search(plain):
            errors.append(f"{name} 列觀望不碰，說明卻只有偏多的內容：{note[:60]}")
        if cat in ("buy", "watch_watch") and pl.active_prohibit(note):
            warns.append(f"{name} 列{LABEL[cat]}，說明裡有明確的不要買：{note[:60]}")
    # 四、原文盤點到、講得很直白的台股不能沒有下落。
    try:
        for item in pl.source_inventory(pl.source_segments(raw)):
            heard, code = item["name"], str(item["code"])
            official = pl._display_name(item.get("official_name") or "")
            if code in placed or heard in placed or official in placed:
                continue
            pick = pl.plain_current_stance(heard, raw, weak=bool(item.get("weak")))
            if pick:
                errors.append(f"{official or heard}（{code}）原文有直白說法卻沒有收錄：{pick[1][:40]}")
    except Exception as e:
        warns.append(f"盤點檢查略過（{type(e).__name__}：{e}）")
    return errors, warns


def site_rows(book, date):
    """試算表裡這一天目前的資料：{代號: [(分類, 名稱, 來源)]}。來源＝影片／盤中／人工。"""
    found = {}
    back = {v: k for k, v in LABEL.items()}
    for sheet, fixed in (("操作紀錄", ""), ("會員持股", "holdings")):
        try:
            vals = book.worksheet(sheet).get_all_values()
        except Exception as e:
            print(f"（讀不到「{sheet}」：{type(e).__name__}）")
            continue
        if len(vals) < 2:
            continue
        head = [str(h).strip() for h in vals[0]]
        col = {k: head.index(k) for k in ("日期", "股票名稱", "代號", "方向", "來源影片ID") if k in head}
        if "日期" not in col or "代號" not in col:
            continue
        for row in vals[1:]:
            get = lambda k: str(row[col[k]]).strip() if k in col and col[k] < len(row) else ""
            if pl.norm_date(get("日期")) != date:
                continue
            cat = fixed or back.get(get("方向"), get("方向"))
            src = get("來源影片ID")
            origin = ("盤中" if src.startswith("CMONEY-") else
                      "人工" if not src or src == "人工補登" or src.startswith(pl.MANUAL_ENTRY_PREFIX) else "影片")
            found.setdefault(get("代號"), []).append((cat, get("股票名稱"), origin))
    return found


def site_compare(signals, current):
    """重播結果和網站目前資料逐檔對照。回傳文字報告與差異數（盤中來源的不算差異）。"""
    mine = {}
    for cat in LABEL:
        for r in signals.get(cat, []) or []:
            mine.setdefault(str(r.get("code") or ""), []).append((cat, str(r.get("name") or "")))
    lab = lambda c: LABEL.get(c, c)
    same, lines, diff = 0, [], 0
    for code in sorted(set(mine) | set(current)):
        a = sorted({c for c, _ in mine.get(code, [])})
        b = sorted({c for c, *_ in current.get(code, [])})
        name = (mine.get(code) or current.get(code))[0][1]
        origins = "、".join(sorted({o for *_, o in current.get(code, [])}))
        if a and b and set(a) & set(b):
            same += 1
            continue
        only_intraday = bool(b) and origins == "盤中"
        diff += 0 if only_intraday else 1
        lines.append(f"  {'・' if only_intraday else '≠'} {name}（{code}）網站：{'／'.join(map(lab, b)) or '沒有'}"
                     f"{'〔' + origins + '〕' if origins else ''}　重播：{'／'.join(map(lab, a)) or '沒有'}")
    head = f"分類相同 {same} 檔；不同 {diff} 檔" + ("（・＝只在盤中來源出現，影片重播本來就不會有）" if lines else "")
    return head + ("\n" + "\n".join(lines) if lines else ""), diff


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
    errors, warns = invariants(signals, raw)
    print(f"\n===== 通用檢查（不靠人工答案）：{'全部通過' if not errors else str(len(errors)) + ' 項錯誤'} =====")
    for e in errors:
        print("  ✗ " + e)
    for w in warns:
        print("  ・提醒　" + w)
    report, _ = site_compare(signals, site_rows(book, date))
    print(f"\n===== 與網站目前這一天的資料比對（僅供對照） =====\n{report}")
    code = 3 if errors else 0
    if args.golden:
        golden = json.loads(Path(args.golden).read_text(encoding="utf-8"))
        ok, report = compare(signals, golden)
        print(f"\n===== 與人工核對的答案比對：{'全部符合' if ok else '有不符'} =====\n{report}")
        if not ok:
            code = 2
    if code:
        print(f"\n結束代碼 {code}：這是檢查結果（{'答案不符' if code == 2 else '通用檢查有錯'}），不是程式錯誤。")
        sys.exit(code)


if __name__ == "__main__":
    main()
