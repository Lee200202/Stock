"""第二步：凍結獨立判讀。凍結後才可以讀網站或今天的流程結果，之後不得回頭改判讀。

    python scripts/daily_review/freeze_reading.py .daily-review/<日期>/reading.json

reading.json 格式（每一檔一列）：
  {"date": "2026/11/02",
   "stocks": [{"code": "2327", "name": "國巨", "accept": ["watch_avoid"], "quote": "原句", "why": "理由",
               "identity": "confirmed|uncertain_alias"}],
   "must_not_public": [{"name": "華邦電", "why": "..."}],
   "today_trades": {"buy": [], "sell": []},
   "rulings_read": ["docs/daily-review/裁示紀錄.md", ...]}
accept 可用值：buy sell holdings watch_watch watch_avoid history ignored uncertain
"""
import sys
from pathlib import Path

from common import LABEL, now, read_json, sha

ALLOWED = set(LABEL) - {'missing'}


def main():
    path = Path(sys.argv[1])
    reading = read_json(path)
    problems = []
    if not reading.get('rulings_read'):
        problems.append('rulings_read 是空的：判讀前要先讀 docs/daily-review/裁示紀錄.md 與分類技能')
    for s in reading.get('stocks') or []:
        if not s.get('code') or not s.get('name'):
            problems.append(f'缺代號或名稱：{s}')
        bad = [x for x in s.get('accept') or [] if x not in ALLOWED]
        if not s.get('accept') or bad:
            problems.append(f"{s.get('code')} accept 不合法：{s.get('accept')}")
        if not s.get('quote'):
            problems.append(f"{s.get('code')} 缺原句")
    if problems:
        raise SystemExit('判讀格式不合格，未凍結：\n' + '\n'.join(problems))
    text = path.read_text(encoding='utf-8')
    digest = sha(text)
    (path.parent / 'reading.sha256').write_text(digest + '\n' + now().strftime('%Y/%m/%d %H:%M:%S') + '\n', encoding='utf-8')
    print('FROZEN', digest, f"{len(reading['stocks'])} 檔")


if __name__ == '__main__':
    main()
