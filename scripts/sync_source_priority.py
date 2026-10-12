"""Back up a verified day, apply source priority and refresh without model calls."""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import pipeline as pl

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    day = pl.norm_date(args.date)
    target = datetime.strptime(day, '%Y/%m/%d').date()
    today = datetime.now(ZoneInfo('Asia/Taipei')).date()
    if not today - timedelta(days=30) <= target <= today:
        raise SystemExit('只能核對最近三十天的既有資料')
    if not (ROOT / 'scripts/golden' / (target.isoformat() + '.json')).exists():
        raise SystemExit('須有該日人工核對答案；本次未修改')
    book = pl.open_sheets()
    names = [w.title for w in book.worksheets()]
    ledgers = [n for n in names if n in ('寄送帳本', 'LINE 寄送帳本')]
    before = {n: digest(book.worksheet(n).get_all_values()) for n in ledgers}
    sent = pl._daily_article_status(book, day)
    stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%m%d_%H%M%S')
    for name in ('操作紀錄', '會員持股', '每日推播內容'):
        book.duplicate_sheet(book.worksheet(name).id, new_sheet_name=f'備份_{stamp}_{name}')
    print('三張工作表已備份；僅套用既有來源優先，不重新判讀')
    result = pl.apply_sms_priority(book, [day])
    print('來源優先結果：' + json.dumps(result, ensure_ascii=False))
    if sent != pl._daily_article_status(book, day) or any(
            before[n] != digest(book.worksheet(n).get_all_values()) for n in ledgers):
        raise RuntimeError('同期寄送狀態有變動，需核對帳本；本次未呼叫寄送')
    steps = ['smsmail', 'tracker', 'perfhist', 'perf']
    for attempt in range(3):
        refreshed = pl.maybe_refresh_site(force=True, only=steps, date_str=day) or {}
        if refreshed.get('ok'):
            print('來源優先及網站刷新完成；未呼叫模型、取稿或寄送')
            return
        steps = steps[int(refreshed.get('done') or 0):] or steps
        if not refreshed.get('transient') or attempt == 2:
            raise RuntimeError('來源已更新，請只續跑網站刷新：' + '、'.join(steps))
        time.sleep(60)

if __name__ == '__main__':
    main()
