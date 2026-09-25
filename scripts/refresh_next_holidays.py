#!/usr/bin/env python3
"""Annually add the next TWSE calendar without rewriting previously verified years.

The TWSE endpoint can return the *current* year's rows for an unpublished year.
fetch() rejects those rows; an unpublished calendar never produces a commit.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from update_holidays import WD, fetch

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "pipeline" / "market_holidays.py"


def install(year: int, rows: list[tuple[str, str]], target: Path = TARGET) -> bool:
    if len(rows) < 5 or any(not day.startswith(f"{year}-") for day, _ in rows):
        raise ValueError(f"{year} 行事曆尚未公布或資料不完整，不修改檔案")
    source = target.read_text(encoding="utf-8")
    marker = f"    # ---- {year} ----"
    if marker in source:
        return False
    end = source.index("\n)\n", source.index("MARKET_HOLIDAYS = ("))
    lines = [marker]
    for day, name in sorted(set(rows)):
        from datetime import date
        weekday = WD[date.fromisoformat(day).weekday()]
        lines.append(f'    "{day}",  # ({weekday}) {name}')
    updated = source[:end] + "\n" + "\n".join(lines) + source[end:]
    target.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int)
    args = parser.parse_args()
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    year = args.year or (today.year if today.month == 1 else today.year + 1)
    rows = fetch(year)
    if not rows:
        print(f"{year} 年正式休市表尚未公布；不改清單，下週再查。")
        return 0
    changed = install(year, rows)
    print(f"{year} 台股休市日 {len(rows)} 筆；" + ("已更新 Python 清單" if changed else "已收錄，無須變更"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
