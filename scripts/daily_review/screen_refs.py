"""直播畫面辨識的結果（複核紀錄「畫面辨識」分頁）。2026/11/12 管理者。

    python scripts/daily_review/screen_refs.py list --date 2026/11/12 [--wait 900]
        印出那天的辨識結果（JSON）。--wait：還沒有結果就每 60 秒再看一次，最多等這麼多秒
        （screen-identify.yml 通常在回放好後幾分鐘內寫入；等不到就印空陣列，複核照常進行）。
    python scripts/daily_review/screen_refs.py set --date 2026/11/12 --time 0:23:45 --state 已上網站 --note "…"
        更新某一列的狀態：已上網站／網站已有／待你決定／不需處理。
    python scripts/daily_review/screen_refs.py set-batch --date 2026/11/12 --file refs.tsv
        一次標很多列（TSV：時間<TAB>狀態<TAB>說明）；只讀寫各一次，不會撞到 Sheets 讀取上限。
    python scripts/daily_review/screen_refs.py vocab-list --date 2026/11/12
        印出那天畫面辨識學到的詞（詞庫學習分頁；新股名已採用、聽錯待核對）。
    python scripts/daily_review/screen_refs.py vocab-set --heard 四星KY --name 世芯-KY --state 已採用 --note "原稿 0:03:17 同一段在談世芯-KY"
        對照原稿確認聽錯詞：已採用（之後聽打與畫面辨識都會用）／不採用。

「把握」欄：清楚＝畫面清楚顯示名稱或代號且就是主持人在談的那一檔；模糊；沒有顯示（畫面沒有個股，可能設備或切換問題）。
「狀態」欄：待核對（剛寫入）→ 雲端複核核對後改成上面四種之一；整天一列「無」＝沒有需要看畫面的地方，「失敗」＝Gemini 沒讀到。
"""
import argparse
import json
import time
from pathlib import Path

from common import norm_day, now, open_log, rows_of

TAB = '畫面辨識'
STATES = ('待核對', '已上網站', '網站已有', '待你決定', '不需處理')


def day_rows(rows, day):
    """純函式：那一天的列（含列號）。"""
    return [dict(r, _row=i) for i, r in enumerate(rows, 2) if norm_day(r.get('日期')) == day]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('list'); p.add_argument('--date', required=True); p.add_argument('--wait', type=int, default=0)
    p = sub.add_parser('set'); p.add_argument('--date', required=True); p.add_argument('--time', required=True)
    p.add_argument('--state', required=True); p.add_argument('--note', default='')
    # 2026/11/12：一列一列 set 會每次重讀整張表，46 列撞到 Sheets 每分鐘讀取上限（429）；一次標完用 set-batch。
    p = sub.add_parser('set-batch'); p.add_argument('--date', required=True)
    p.add_argument('--file', required=True, help='TSV：時間<TAB>狀態<TAB>說明，一列一筆')
    p = sub.add_parser('vocab-list'); p.add_argument('--date', default='')
    p = sub.add_parser('vocab-set'); p.add_argument('--heard', required=True); p.add_argument('--name', required=True)
    p.add_argument('--state', required=True, choices=('已採用', '不採用')); p.add_argument('--note', default='')
    a = ap.parse_args()
    if a.cmd.startswith('vocab'):
        return vocab(a)
    day = norm_day(a.date)
    ws = open_log().worksheet(TAB)
    if a.cmd == 'list':
        deadline = time.time() + max(0, a.wait)
        while True:
            head, rows = rows_of(ws)
            got = day_rows(rows, day)
            if got or time.time() >= deadline:
                break
            print(f'（{day} 還沒有畫面辨識結果，60 秒後再看）', flush=True)
            time.sleep(60)
        print(json.dumps(got, ensure_ascii=False, indent=1))
        return
    if a.cmd == 'set-batch':
        return set_batch(ws, day, Path(a.file).read_text(encoding='utf-8'))
    if a.state not in STATES:
        raise SystemExit('狀態只能是：' + '、'.join(STATES))
    head, rows = rows_of(ws)
    hit = [r for r in day_rows(rows, day) if r.get('時間') == a.time]
    if not hit:
        raise SystemExit(f'{day} {a.time} 沒有這一列')
    stamp = now().strftime('%Y/%m/%d %H:%M')
    for r in hit:
        r = dict(r, 狀態=a.state, 處理說明=(a.note + '（' + stamp + '）')[:300])
        ws.update([[r.get(h, '') for h in head]], f"A{r['_row']}", value_input_option='RAW')
    print(json.dumps({'cmd': 'set', 'rows': [r['_row'] for r in hit]}, ensure_ascii=False))



def parse_batch(text):
    """純函式：TSV（時間、狀態、說明）→ [(時間, 狀態, 說明)]；狀態不對就報錯。"""
    out = []
    for n, line in enumerate(str(text).splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split('\t')
        if len(parts) < 2 or parts[1].strip() not in STATES:
            raise SystemExit(f'第 {n} 行格式不對（時間<TAB>狀態<TAB>說明；狀態只能是 ' + '、'.join(STATES) + '）')
        out.append((parts[0].strip(), parts[1].strip(), (parts[2] if len(parts) > 2 else '').strip()))
    return out


def set_batch(ws, day, text):
    """讀一次、寫一次（batch_update），不會撞到每分鐘讀取上限。"""
    items = parse_batch(text)
    head, rows = rows_of(ws)
    mine = day_rows(rows, day)
    stamp = now().strftime('%Y/%m/%d %H:%M')
    data, done, missing = [], [], []
    for t, state, note in items:
        hit = [r for r in mine if r.get('時間') == t]
        if not hit:
            missing.append(t)
            continue
        for r in hit:
            r = dict(r, 狀態=state, 處理說明=(note + '（' + stamp + '）')[:300])
            data.append({'range': f"A{r['_row']}", 'values': [[r.get(h, '') for h in head]]})
            done.append(r['_row'])
    if data:
        ws.batch_update(data, value_input_option='RAW')
    print(json.dumps({'cmd': 'set-batch', 'rows': done, 'missing': missing}, ensure_ascii=False))
    if missing:
        raise SystemExit('這些時間沒有列：' + '、'.join(missing))


def vocab(a):
    import vocab_learn as vl
    ws = open_log().worksheet(vl.TAB)
    head, rows = rows_of(ws)
    if a.cmd == 'vocab-list':
        day = norm_day(a.date) if a.date else ''
        got = [dict(r, _row=i) for i, r in enumerate(rows, 2) if not day or norm_day(r.get('日期')) == day]
        print(json.dumps(got, ensure_ascii=False, indent=1))
        return
    hit = [(i, r) for i, r in enumerate(rows, 2) if r.get('聽到') == a.heard and r.get('股名') == a.name]
    if not hit:
        raise SystemExit(f'詞庫學習沒有「{a.heard} → {a.name}」這一列')
    stamp = now().strftime('%Y/%m/%d %H:%M')
    for i, r in hit:
        r = dict(r, 狀態=a.state, 說明=(a.note + '（雲端複核 ' + stamp + '）')[:300])
        ws.update([[r.get(h, '') for h in head]], f'A{i}', value_input_option='RAW')
    print(json.dumps({'cmd': 'vocab-set', 'rows': [i for i, _ in hit], 'state': a.state}, ensure_ascii=False))


if __name__ == '__main__':
    main()
