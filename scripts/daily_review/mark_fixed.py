"""修正套用之後：把複核紀錄裡當天「待套用」的差異改成「已修正」，每日摘要改「已完成」（郵件不再等）。

    python scripts/daily_review/mark_fixed.py --date 2026/11/04            套用成功
    python scripts/daily_review/mark_fixed.py --date 2026/11/04 --failed   套用失敗：標「修正失敗」，郵件照原內容寄

由 daily-review-apply.yml 在套用並讀回之後執行；12:30 的狀態信會讀這裡列出今天網站修改了什麼。
"""
import argparse
import json

from common import ROOT, norm_day, now, open_log, rows_of

PENDING = ('待套用', '修正中')
FAIL_NOTE = '修正套用失敗，郵件照原內容寄出'


def plan_texts(day):
    """修正計畫裡每一檔的新說明（代號 → 文字），給「修正後」欄。"""
    out = {}
    for kind in ('source-review', 'notice-reason'):
        path = ROOT / 'scripts/repairs' / f'{day.replace("/", "-")}-{kind}.json'
        if path.exists():
            for item in json.loads(path.read_text(encoding='utf-8')).get('items', []):
                out[str(item['code'])] = item.get('text') or ('移除' if item.get('action') == 'delete' else '')
    return out


def updates(day, head, rows, texts, failed):
    """純函式：回傳 [(列號, 新的整列)]；只動當天、處理欄是待套用的列。

    2026/11/10：同一天第一輪失敗會把待套用標成「修正失敗」，之後重推成功時那些列卻還留著「修正失敗」
    （11/10 留了 19 列）。成功時，當天標「修正失敗」且在修正計畫裡的代號，以及章節列（代號不是數字），一併改回已修正。"""
    out = []
    for i, r in enumerate(rows, 2):
        if norm_day(r.get('日期')) != day:
            continue
        code = str(r.get('代號') or '').strip()
        retried = (not failed and r.get('處理') == '修正失敗' and
                   (code in texts or (code and not code.isdecimal())))
        if r.get('處理') not in PENDING and not retried:
            continue
        r = dict(r, 處理='修正失敗' if failed else '已修正')
        if not failed and texts.get(str(r.get('代號'))) and not r.get('修正後'):
            r['修正後'] = texts[str(r['代號'])]
        out.append((i, [r.get(h, '') for h in head]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--failed', action='store_true')
    args = ap.parse_args()
    day = norm_day(args.date)
    log = open_log()
    detail = log.worksheet('差異明細')
    head, rows = rows_of(detail)
    changed = updates(day, head, rows, plan_texts(day), args.failed)
    for i, values in changed:
        detail.update([values], f'A{i}', value_input_option='RAW')
    summary = log.worksheet('每日摘要')
    shead, srows = rows_of(summary)
    idx = next((i for i, r in enumerate(srows, 2) if norm_day(r.get('日期')) == day), None)
    if idx is not None:
        row = srows[idx - 2]
        fixed = sum(1 for r in rows_of(detail)[1] if norm_day(r.get('日期')) == day and r.get('處理') == '已修正')
        row.update({'狀態': '修正失敗' if args.failed else '已完成', '已修正': fixed,
                    '完成時間': now().strftime('%H:%M:%S'),
                    '備註': (row.get('備註', '') + ' ' + FAIL_NOTE).strip() if args.failed
                            else row.get('備註', '').replace(FAIL_NOTE, '').strip()})   # 重跑成功就拿掉先前的失敗註記
        summary.update([[row.get(h, '') for h in shead]], f'A{idx}', value_input_option='RAW')
    print('MARKED', day, '修正失敗' if args.failed else '已修正', len(changed), '列')


if __name__ == '__main__':
    main()
