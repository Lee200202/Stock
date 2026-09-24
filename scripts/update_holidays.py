#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從證交所抓台股休市日，重新產生 pipeline/market_holidays.py 與 apps-script/Holidays.gs。

每年年底證交所公布次年行事曆之後跑一次：

    python scripts/update_holidays.py                # 抓今年與明年
    python scripts/update_holidays.py 2026 2027 2028 # 指定年份

兩個檔案一起產生，是因為上游（Python）與下游（Apps Script）都要判斷休市，
而它們沒有共用的執行環境。分開手改遲早會不一致，所以一律由這支產生，
並且有測試釘住兩邊的日期必須完全相同。

證交所在次年行事曆尚未公布時，會回傳當年度的資料而不是空的。
所以抓回來之後要核對日期真的屬於查詢的那一年，否則會把今年的假日
當成明年的寫進去——那種錯誤不會有任何徵兆。
"""

import datetime
import pathlib
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
    years = [int(a) for a in argv] or [2025, this_year, this_year + 1]

    all_rows, covered = [], []
    for y in years:
        rows = []
        try:
            rows = fetch(y)
        except Exception as e:
            if y in HISTORICAL_HOLIDAYS:
                rows = HISTORICAL_HOLIDAYS[y]
            else:
                print(f"  {y}：抓取失敗（{e}），略過")
                continue
        if not rows and y in HISTORICAL_HOLIDAYS:
            rows = HISTORICAL_HOLIDAYS[y]
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

    py = ROOT / "pipeline" / "market_holidays.py"
    src = py.read_text(encoding="utf-8")
    a = src.index("MARKET_HOLIDAYS = (\n") + len("MARKET_HOLIDAYS = (\n")
    b = src.index("\n)\n", a)
    py.write_text(src[:a] + "\n".join(py_lines) + src[b:], encoding="utf-8")
    print(f"已更新 {py.name}")

    gs = ROOT / "apps-script" / "Holidays.gs"
    gs.write_text(GS_TEMPLATE.replace("%ROWS%", "\n".join(gs_lines))
                             .replace("%YEARS%", ", ".join(str(y) for y in covered)),
                  encoding="utf-8")
    print(f"已更新 {gs.name}")
    print("\n兩個檔案都要重新部署：Python 推上 GitHub，Holidays.gs 貼進 Apps Script。")
    return 0


GS_TEMPLATE = """/**
 * 檔案：Holidays.gs
 * 台股休市日。由 scripts/update_holidays.py 從證交所資料自動產生，不要手改。
 *
 * 為什麼需要：原本只看星期幾，國定假日落在平日時市場休市、沒有盤中直播，
 * 下游仍會照寄推播、狀態信與告警。那些信是假的，看久了會把真正的異常一起忽略。
 *
 * 年份不在清單裡時一律當成交易日——寧可多跑一天，也不要因為忘記更新而
 * 安靜地跳過整年的交易日。那種錯誤不會有任何徵兆。
 *
 * 更新：執行 python scripts/update_holidays.py，然後把本檔重新貼進 Apps Script。
 * 目前收錄：%YEARS%
 */

var MARKET_HOLIDAYS_ = [
%ROWS%
];

var MARKET_HOLIDAY_SET_ = null;
var MARKET_HOLIDAY_YEARS_ = null;

function marketHolidaySet_() {
  if (!MARKET_HOLIDAY_SET_) {
    MARKET_HOLIDAY_SET_ = {};
    MARKET_HOLIDAY_YEARS_ = {};
    MARKET_HOLIDAYS_.forEach(function (d) {
      MARKET_HOLIDAY_SET_[d] = 1;
      MARKET_HOLIDAY_YEARS_[d.slice(0, 4)] = 1;
    });
  }
  return MARKET_HOLIDAY_SET_;
}

/** 這一天是不是休市日。不含週末，週末另外用星期判斷。 */
function isMarketHoliday_(d) {
  var set = marketHolidaySet_();
  var tz = 'Asia/Taipei';
  var ymd = Utilities.formatDate(d || new Date(), tz, 'yyyy-MM-dd');
  if (!MARKET_HOLIDAY_YEARS_[ymd.slice(0, 4)]) {
    Logger.log('注意：休市日清單沒有 ' + ymd.slice(0, 4) + ' 年，這一年當成交易日。');
    return false;
  }
  return !!set[ymd];
}

/** 給日誌用的一句話。回空字串代表這天有開盤。 */
function whyClosed_(d) {
  var now = d || new Date();
  var wd = Number(Utilities.formatDate(now, 'Asia/Taipei', 'u'));
  if (wd > 5) { return '週末不開盤'; }
  if (isMarketHoliday_(now)) { return '台股休市日'; }
  return '';
}
"""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
