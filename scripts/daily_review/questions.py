"""每日複核要管理者決定的問題（複核紀錄「管理者問題」分頁）。2026/11/11 管理者。

    python scripts/daily_review/questions.py add --date 2026/11/11 --question "…" --context "…" --options '["…","…"]'
    python scripts/daily_review/questions.py list [--state 待回答] [--date 2026/11/11]
    python scripts/daily_review/questions.py set --ids Q1,Q2 --state 已處理

雲端複核遇到要管理者決定的事（聽寫詞是哪一檔、某種說法算不算外洩、邊界怎麼歸）時，每一題寫一列，
附 2–4 個預想的回答選項（雲端先猜管理者可能怎麼答；LINE 另外一定有「自訂回答」）。
Apps Script 每五分鐘把「待回答」的題目推到管理者的 LINE，管理者選一個選項或自己輸入、確認後，
Apps Script 把題目改成「已回答」並寫一則「管理者裁示」（裁示分類「指示」、名稱「管理者回答」），由雲端排程照答案處理。

狀態流：待回答 → 已回答（管理者在 LINE 確認）→ 已處理（雲端排程照答案處理完）；不用問了寫「已取消」。
不得寫會員通知原文；只寫複核紀錄試算表。
"""
import argparse
import json

from common import norm_day, now, open_log, rows_of

TAB = '管理者問題'
STATES = ('待回答', '已回答', '已處理', '已取消')


def clean_options(options):
    """純函式：選項 2–4 個、各 1–40 字、不重複；不合格丟 ValueError。"""
    if isinstance(options, str):
        options = json.loads(options)
    if not isinstance(options, list):
        raise ValueError('選項要是 JSON 陣列')
    out = []
    for o in options:
        text = ' '.join(str(o or '').split())
        if not text:
            continue
        if len(text) > 40:
            raise ValueError('選項超過 40 字：' + text)
        if text in out:
            continue
        out.append(text)
    if not 2 <= len(out) <= 4:
        raise ValueError('選項要 2–4 個（LINE 另外一定有「自訂回答」）')
    return out


def new_row(head, *, day, question, context, options, stamp, seq):
    """純函式：一題的整列。問題 1–200 字、背景 0–300 字。"""
    q = ' '.join(str(question or '').split())
    if not 1 <= len(q) <= 200:
        raise ValueError('問題要 1–200 字')
    ctx = ' '.join(str(context or '').split())[:300]
    qid = 'Q' + stamp.replace('/', '').replace(':', '').replace(' ', '') + str(seq)
    row = {'建立時間': stamp, '問題ID': qid, '日期': norm_day(day), '問題': q, '背景': ctx,
           '選項': json.dumps(clean_options(options), ensure_ascii=False), '狀態': '待回答',
           '回答': '', '裁示ID': '', '更新時間': stamp, 'LINE推送': ''}
    return qid, [row.get(h, '') for h in head]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('add'); p.add_argument('--date', required=True); p.add_argument('--question', required=True)
    p.add_argument('--context', default=''); p.add_argument('--options', required=True)
    p = sub.add_parser('list'); p.add_argument('--state', default=''); p.add_argument('--date', default='')
    p = sub.add_parser('set'); p.add_argument('--ids', required=True); p.add_argument('--state', required=True)
    a = ap.parse_args()
    ws = open_log().worksheet(TAB)
    head, rows = rows_of(ws)
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    if a.cmd == 'add':
        qid, values = new_row(head, day=a.date, question=a.question, context=a.context, options=a.options, stamp=stamp, seq=len(rows) % 10)
        ws.append_row(values, value_input_option='RAW')
        print(json.dumps({'cmd': 'add', 'id': qid}, ensure_ascii=False))
        return
    if a.cmd == 'list':
        day = norm_day(a.date) if a.date else ''
        keep = [r for r in rows if r.get('問題ID') and (not a.state or r.get('狀態') == a.state) and (not day or norm_day(r.get('日期')) == day)]
        print(json.dumps(keep, ensure_ascii=False, indent=1))
        return
    if a.state not in STATES:
        raise SystemExit('不支援的狀態：' + a.state)
    ids = {x.strip() for x in a.ids.split(',') if x.strip()}
    done = []
    for i, r in enumerate(rows, 2):
        if r.get('問題ID') in ids:
            r = dict(r, 狀態=a.state, 更新時間=stamp)
            ws.update([[r.get(h, '') for h in head]], f'A{i}', value_input_option='RAW')
            done.append(i)
    print(json.dumps({'cmd': 'set', 'rows': done}, ensure_ascii=False))


if __name__ == '__main__':
    main()
