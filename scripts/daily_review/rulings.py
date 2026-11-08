"""LINE 管理者裁示的狀態（複核紀錄「管理者裁示」分頁）。

    python scripts/daily_review/rulings.py list [--state 待處理]          列出裁示（JSON）
    python scripts/daily_review/rulings.py claim --ids R1,R2               待處理 → 處理中（雲端排程開始處理）
    python scripts/daily_review/rulings.py set --ids R1 --state 套用中 --branch B [--note 文字]
    python scripts/daily_review/rulings.py finish --branch B [--failed] [--note 文字]
                                                                        套用中 → 已完成／失敗（line-ruling-apply.yml）
    python scripts/daily_review/rulings.py rows --date 2026/11/06 --code 8299
                                                                        唯讀：那天網站上這一檔的列（分類、來源、說明與指紋），寫計畫用

狀態流：待處理（Apps Script 在管理者按「確認送出」後寫入）→ 處理中 → 套用中（推了分支）→ 已完成／失敗；
只記錄、不改網站的寫「已記錄」。Apps Script 每五分鐘把已完成／失敗／已記錄的列回報到管理者的 LINE。
只寫複核紀錄試算表，不寫主資料。
"""
import argparse
import json

from common import norm_day, now, open_book, open_log, pipeline, rows_of, sha

TAB = '管理者裁示'
TERMINAL = ('已完成', '失敗', '已記錄', '已取消')
STATES = ('待處理', '處理中', '套用中') + TERMINAL


def transition(head, rows, *, ids=None, branch=None, state, require=None, set_branch=None, note=None, stamp=''):
    """純函式：回傳 [(列號, 新的整列)]。ids 或 branch 選列；require 是允許的目前狀態。"""
    if state not in STATES:
        raise ValueError('不支援的狀態：' + state)
    want = set(ids or [])
    out = []
    for i, r in enumerate(rows, 2):
        if want and r.get('裁示ID') not in want:
            continue
        if branch and r.get('分支') != branch:
            continue
        if not want and not branch:
            continue
        if require and r.get('狀態') not in require:
            continue
        r = dict(r, 狀態=state, 更新時間=stamp)
        if set_branch is not None:
            r['分支'] = set_branch
        if note is not None:
            r['結果'] = note[:300]
        out.append((i, [r.get(h, '') for h in head]))
    return out


def detail_updates(head, rows, finished, texts):
    """純函式：已完成的裁示 → 差異明細同日同代號、還在「待管理者」的列改「已修正」（12:30 狀態信與 LINE 不再列成待裁示）。"""
    keys = {(r.get('日期', '').replace('-', '/'), str(r.get('代號'))) for r in finished}
    out = []
    for i, r in enumerate(rows, 2):
        if (str(r.get('日期', '')).replace('-', '/'), str(r.get('代號'))) not in keys or r.get('處理') != '待管理者':
            continue
        r = dict(r, 處理='已修正')
        if texts.get(str(r.get('代號'))) and not r.get('修正後'):
            r['修正後'] = texts[str(r['代號'])]
        out.append((i, [r.get(h, '') for h in head]))
    return out


SHEET_CAT = {'買入': 'buy', '賣出': 'sell', '觀望注意': 'watch_watch', '觀望不碰': 'watch_avoid'}


def site_rows(tables, day, code):
    """純函式：操作紀錄／會員持股裡那一天、那一檔的列 → 寫修正計畫需要的欄位。"""
    out = []
    for tab, values in tables.items():
        if not values:
            continue
        head = values[0]
        for cells in values[1:]:
            r = dict(zip(head, cells))
            if norm_day(r.get('日期')) != day or str(r.get('代號') or '').strip() != code:
                continue
            note = r.get('說明重點') or r.get('理由摘錄') or ''
            out.append({'sheet': tab, 'category': 'holdings' if tab == '會員持股' else SHEET_CAT.get(r.get('方向'), r.get('方向')),
                        'name': r.get('股票名稱', ''), 'source_id': r.get('來源影片ID', ''),
                        'previous_note_sha256': sha(note), 'note': note})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list'); p.add_argument('--state', default='')
    p = sub.add_parser('claim'); p.add_argument('--ids', required=True)
    p = sub.add_parser('set'); p.add_argument('--ids', required=True); p.add_argument('--state', required=True)
    p.add_argument('--branch'); p.add_argument('--note')
    p = sub.add_parser('finish'); p.add_argument('--branch', required=True)
    p.add_argument('--failed', action='store_true'); p.add_argument('--note')
    p = sub.add_parser('rows'); p.add_argument('--date', required=True); p.add_argument('--code', required=True)
    a = ap.parse_args()
    if a.cmd == 'rows':
        pl = pipeline()
        book = open_book(pl)
        tables = {n: book.worksheet(n).get_all_values() for n in ('操作紀錄', '會員持股')}
        print(json.dumps(site_rows(tables, norm_day(a.date), a.code.strip()), ensure_ascii=False, indent=1))
        return
    ws = open_log().worksheet(TAB)
    head, rows = rows_of(ws)
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    if a.cmd == 'list':
        keep = [r for r in rows if r.get('裁示ID') and (not a.state or r.get('狀態') == a.state)]
        print(json.dumps(keep, ensure_ascii=False, indent=1))
        return
    ids = [x.strip() for x in (getattr(a, 'ids', '') or '').split(',') if x.strip()]
    if a.cmd == 'claim':
        changed = transition(head, rows, ids=ids, state='處理中', require=('待處理',), stamp=stamp)
    elif a.cmd == 'set':
        changed = transition(head, rows, ids=ids, state=a.state, set_branch=a.branch, note=a.note, stamp=stamp)
    else:
        changed = transition(head, rows, branch=a.branch, state='失敗' if a.failed else '已完成',
                             require=('套用中', '處理中'), note=a.note, stamp=stamp)
    for i, values in changed:
        ws.update([values], f'A{i}', value_input_option='RAW')
    if a.cmd == 'finish' and not a.failed and changed:
        from mark_fixed import plan_texts
        finished = [dict(zip(head, values)) for _, values in changed]
        detail = ws.spreadsheet.worksheet('差異明細')
        dhead, drows = rows_of(detail)
        texts = {}
        for day in {r['日期'].replace('-', '/') for r in finished}:
            texts.update(plan_texts(day))
        for i, values in detail_updates(dhead, drows, finished, texts):
            detail.update([values], f'A{i}', value_input_option='RAW')
    print(json.dumps({'cmd': a.cmd, 'rows': [i for i, _ in changed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
