#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從證交所抓台股休市日，更新 Python 清單；若工作目錄有 GAS 檔，只更新其日期陣列。

每年年底證交所公布次年行事曆之後跑一次：

    python scripts/update_holidays.py                # 抓今年與明年
    python scripts/update_holidays.py 2026 2027 2028 # 指定年份

上游（Python）與下游（Apps Script）都要判斷休市，
而它們沒有共用的執行環境。不能靠手改日期；GitHub 只更新 Python，GAS 在年底從證交所自查，
並且有測試釘住兩邊的日期必須完全相同。

證交所在次年行事曆尚未公布時，會回傳當年度的資料而不是空的。
所以抓回來之後要核對日期真的屬於查詢的那一年，否則會把今年的假日
當成明年的寫進去——那種錯誤不會有任何徵兆。
"""

import datetime
import pathlib
import re
import sys

import requests

API = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
ROOT = pathlib.Path(__file__).resolve().parent.parent
WD = "一二三四五六日"


def fetch(year: int):
    r = requests.get(API, params={"response": "json", "queryYear": year},
                     timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    if data.get("stat") != "ok":
        raise RuntimeError(f"{year}：證交所回 stat={data.get('stat')}")

    rows = []
    for d, name, _note in data.get("data") or []:
        name = name.strip()
        # 「春節前最後交易日」「春節後開始交易日」那幾筆是正常交易日，不是休市
        if "開始交易" in name or "最後交易" in name:
            continue
        if not d.startswith(str(year)):
            continue              # API 回了別年的資料，捨棄
        rows.append((d, name))
    return sorted(set(rows))


HISTORICAL_HOLIDAYS = {
    2025: [
        ("2025-01-01", "中華民國開國紀念日"),
        ("2025-01-23", "市場無交易，僅辦理結算交割作業"),
        ("2025-01-24", "市場無交易，僅辦理結算交割作業"),
        ("2025-01-27", "農曆除夕及春節"),
        ("2025-01-28", "農曆除夕及春節"),
        ("2025-01-29", "農曆除夕及春節"),
        ("2025-01-30", "農曆除夕及春節"),
        ("2025-01-31", "農曆除夕及春節"),
        ("2025-02-28", "和平紀念日"),
        ("2025-04-03", "兒童節及民族掃墓節"),
        ("2025-04-04", "兒童節及民族掃墓節"),
        ("2025-05-01", "勞動節"),
        ("2025-05-30", "端午節"),
        ("2025-09-29", "孔子誕辰紀念日/ 教師節補假"),
        ("2025-10-06", "中秋節"),
        ("2025-10-10", "國慶日"),
        ("2025-10-24", "臺灣光復節補假"),
        ("2025-12-25", "行憲紀念日"),
    ]
}


def main(argv):
    this_year = datetime.date.today().year
    py = ROOT / "pipeline" / "market_holidays.py"
    src = py.read_text(encoding="utf-8")
    existing = {}
    for day, name in re.findall(r'\s*"(\d{4}-\d{2}-\d{2})",\s*#\s*\([^)]*\)\s*([^\r\n]+)', src):
        existing.setdefault(int(day[:4]), []).append((day, name.strip()))
    years = sorted(set([int(a) for a in argv] or [this_year, this_year + 1]) | set(existing))

    all_rows, covered = [], []
    for y in years:
        rows = []
        try:
            rows = fetch(y)
        except Exception as e:
            if y in existing or y in HISTORICAL_HOLIDAYS:
                rows = existing.get(y) or HISTORICAL_HOLIDAYS[y]
            else:
                print(f"  {y}：抓取失敗（{e}），略過")
                continue
        if not rows and (y in existing or y in HISTORICAL_HOLIDAYS):
            rows = existing.get(y) or HISTORICAL_HOLIDAYS[y]
        if not rows:
            print(f"  {y}：證交所尚未公布，略過")
            continue
        print(f"  {y}：{len(rows)} 天")
        all_rows.append((y, rows))
        covered.append(y)

    if not all_rows:
        print("一年都沒抓到，不覆寫任何檔案。")
        return 1

    py_lines, gs_lines = [], []
    for y, rows in all_rows:
        py_lines.append(f"    # ---- {y} ----")
        gs_lines.append(f"  // ---- {y} ----")
        for d, name in rows:
            wd = WD[datetime.date.fromisoformat(d).weekday()]
            py_lines.append(f'    "{d}",  # ({wd}) {name}')
            gs_lines.append(f"  '{d}',  // ({wd}) {name}")

    a = src.index("MARKET_HOLIDAYS = (\n") + len("MARKET_HOLIDAYS = (\n")
    b = src.index("\n)\n", a)
    py.write_text(src[:a] + "\n".join(py_lines) + src[b:], encoding="utf-8")
    print(f"已更新 {py.name}")

    gs = ROOT / "apps-script" / "Holidays.gs"
    if gs.exists():
        gas = gs.read_text(encoding="utf-8")
        start = gas.index("var MARKET_HOLIDAYS_ = [") + len("var MARKET_HOLIDAYS_ = [")
        stop = gas.index("\n];", start)
        gs.write_text(gas[:start] + "\n".join(["", *gs_lines]) + gas[stop:], encoding="utf-8")
        print(f"已更新 {gs.name} 的日期；其餘動態檢查程式保留")
    else:
        print("此 checkout 沒有 apps-script/；Python 清單已更新，Apps Script 由網站端自行向證交所查驗。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
