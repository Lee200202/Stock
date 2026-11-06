"""第二步：凍結獨立判讀。凍結後才可以讀網站或今天的流程結果，之後不得回頭改判讀。

    python scripts/daily_review/freeze_reading.py .daily-review/<日期>/reading.json

reading.json 格式（每一檔一列）：
  {"date": "2026/11/02",
   "stocks": [{"code": "2327", "name": "國巨", "accept": ["watch_avoid"], "quote": "原句", "why": "理由",
               "identity": "confirmed|uncertain_alias"}],
   "must_not_public": [{"name": "華邦電", "why": "..."}],
   "today_trades": {"buy": [], "sell": []},
   "rulings_read": ["docs/daily-review/裁示紀錄.md", ...],
   "chapters": {"macro":   [{"topic": "指數位階", "detail": "跌到年線；60分K 200均46,931、季線46,400", "quote": "原句"}],
                "lessons": [{"topic": "葛蘭碧負乖離", "detail": "跌離季線太遠會漲回季線，賣點在反彈後", "quote": "原句"}]}}
accept 可用值：buy sell holdings watch_watch watch_avoid history ignored uncertain
chapters（2026/11/06 起必填）：自己從原稿整理的①盤勢與③教學主題，各列講者今天真的講到的每一個主題；
quote 要逐字出自 raw.txt（空白不計），凍結後拿來和網站的兩章對照。
"""
import sys
from pathlib import Path

import re

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
    chapters = reading.get('chapters')
    raw_path = path.parent / 'raw.txt'
    raw = re.sub(r'\s', '', raw_path.read_text(encoding='utf-8')) if raw_path.exists() else ''
    if not isinstance(chapters, dict) or not isinstance(chapters.get('macro'), list) or not isinstance(chapters.get('lessons'), list):
        problems.append('缺 chapters.macro／chapters.lessons：①盤勢與③教學也要先獨立整理再看網站')
    else:
        for key in ('macro', 'lessons'):
            for t in chapters[key]:
                quote = re.sub(r'\s', '', str(t.get('quote') or ''))
                if not t.get('topic') or len(quote) < 6:
                    problems.append(f'chapters.{key} 缺主題或原句（至少 6 字）：{t}')
                elif raw and quote not in raw:
                    problems.append(f'chapters.{key} 的原句不在 raw.txt：{quote[:30]}')
    if problems:
        raise SystemExit('判讀格式不合格，未凍結：\n' + '\n'.join(problems))
    text = path.read_text(encoding='utf-8')
    digest = sha(text)
    (path.parent / 'reading.sha256').write_text(digest + '\n' + now().strftime('%Y/%m/%d %H:%M:%S') + '\n', encoding='utf-8')
    print('FROZEN', digest, f"{len(reading['stocks'])} 檔；盤勢主題 {len(chapters['macro'])}、教學主題 {len(chapters['lessons'])}")


if __name__ == '__main__':
    main()
