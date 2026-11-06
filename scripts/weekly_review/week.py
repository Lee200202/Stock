"""每週流程檢討（每週五 17:00 雲端排程，管理者 2026/11/06）的固定動作。AI 只做判讀、歸因與改規則，其餘交給這支。

    python scripts/weekly_review/week.py fetch  [--date YYYY/MM/DD]   # 取本週紀錄＋目前網站現況，寫 .weekly-review/<週五>/
    python scripts/weekly_review/week.py verify [--date ...] [--json out.json]   # 線上驗收：本週每一項明確錯誤現在網站上對不對
    python scripts/weekly_review/week.py log --stage 排程 --item 結論 --result 已推送 --note "..." [--date ...]
    python scripts/weekly_review/week.py log --stage 工作流程 --json result.json [--mark-fixed]
    python scripts/weekly_review/week.py wait   [--date ...] [--minutes 90]   # 等工作流程寫回結論

「本週」＝含 --date（預設今天）的週一到週五；週末執行就算剛過的那一週。
讀：複核紀錄（每日摘要、差異明細、提示詞候選），主資料（操作紀錄、會員持股、逐字稿判讀稽核）。
寫：.weekly-review/<YYYYMMDD>/week.json、cases.md、verify.json；複核紀錄「每週檢討」分頁（log）；
    差異明細的「處理」與「是否轉成規則或測試」兩欄（只在 log --mark-fixed，且只改本週、明確錯誤、現在已通過的列）。
不呼叫模型、不寄信、不改主資料。
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'daily_review'))
from common import (ROOT, LABEL, DIRECTION, norm_day, now, today, open_book, open_log, pipeline,  # noqa: E402
                    rows_of, write_json, read_json)

WORK = Path(os.environ.get('WEEKLY_REVIEW_WORKDIR') or ROOT / '.weekly-review')
WEEK_TAB = '每週檢討'
WEEK_HEAD = ['週', '紀錄時間', '階段', '日期', '代號', '名稱', '項目', '結果', '說明', '執行']
PUBLIC = ('buy', 'sell', 'holdings', 'watch_watch', 'watch_avoid')
HIDDEN = ('history', 'ignored', 'uncertain')
CLASS_TYPES = ('分類', '漏收', '今日買賣')
GONE_TYPES = ('應排除卻公開', '複核未涵蓋')


# ---- 週的範圍 ---- #
def week_of(day):
    """回傳 (週五 YYYY/MM/DD, [週一..週五])。週末算剛過的那一週。"""
    d = datetime.strptime(norm_day(day), '%Y/%m/%d')
    if d.weekday() >= 5:
        d -= timedelta(days=d.weekday() - 4)
    monday = d - timedelta(days=d.weekday())
    days = [(monday + timedelta(days=i)).strftime('%Y/%m/%d') for i in range(5)]
    return days[-1], days


def trading(days):
    sys.path.insert(0, str(ROOT / 'pipeline'))
    from market_holidays import is_trading_day
    return [d for d in days if is_trading_day(datetime.strptime(d, '%Y/%m/%d').date())]


def stamp_of(friday):
    return friday.replace('/', '')


# ---- 網站現況 ---- #
def site_rows(book, days):
    """{日期: {代號: [{category, name, note}]}}，與 compare_site 讀同樣的兩張表。"""
    days = set(days)
    out = {d: {} for d in days}
    for r in rows_of(book.worksheet('操作紀錄'))[1]:
        d, code = norm_day(r.get('日期')), str(r.get('代號') or '').strip()
        cat = DIRECTION.get(str(r.get('方向') or ''))
        if d in days and code and cat:
            out[d].setdefault(code, []).append({'category': cat, 'name': r.get('股票名稱'), 'note': str(r.get('理由摘錄') or '')})
    for r in rows_of(book.worksheet('會員持股'))[1]:
        d, code = norm_day(r.get('日期')), str(r.get('代號') or '').strip()
        if d in days and code:
            out[d].setdefault(code, []).append({'category': 'holdings', 'name': r.get('股票名稱'), 'note': str(r.get('說明重點') or '')})
    return out


def judge(row, items):
    """純函式：一列差異明細（明確錯誤）對照目前網站的那一檔，回傳 (結果, 說明)。結果：通過／未通過／需人工。"""
    from compare_site import duplicated_sentence
    kind = str(row.get('差異類型') or '')
    public = [x['category'] for x in items if x['category'] in PUBLIC]
    shown = '、'.join(LABEL[c] for c in public) or '未收錄'
    if kind in CLASS_TYPES:
        accept = [a for a in str(row.get('可接受集合') or '').split('／') if a]
        if not accept:
            return '需人工', '差異明細沒有可接受集合'
        hidden_ok = bool(set(accept) & set(HIDDEN))
        ok = all(c in accept for c in public) and (bool(public) or hidden_ok)
        want = '／'.join(LABEL.get(a, a) for a in accept)
        return ('通過' if ok else '未通過'), f'網站現在：{shown}；應為：{want}'
    if kind in GONE_TYPES:
        return ('通過' if not public else '未通過'), f'網站現在：{shown}；應不公開'
    if kind == '說明重複':
        dup = any(duplicated_sentence(x['note']) for x in items)
        return ('未通過' if dup else '通過'), '說明仍有重複句' if dup else '說明已無重複句'
    if str(row.get('處理') or '') in ('已修正', '週檢討確認已正確'):
        return '通過', f'{kind}：依處理狀態「{row.get("處理")}」（這一類沒有逐字自動驗收）'
    return '需人工', f'{kind}：沒有自動驗收方法，處理狀態「{row.get("處理") or "空白"}」'


def verify_rows(detail_rows, sites, days):
    out = []
    for r in detail_rows:
        d = norm_day(r.get('日期'))
        if d not in days or r.get('嚴重度') != '明確錯誤':
            continue
        code = str(r.get('代號') or '').strip()
        result, why = judge(r, sites.get(d, {}).get(code, []))
        out.append({'date': d, 'code': code, 'name': r.get('名稱', ''), 'type': r.get('差異類型', ''),
                    'handling': r.get('處理', ''), 'result': result, 'why': why})
    return out


# ---- 指令 ---- #
def load_week(day):
    friday, days = week_of(day)
    log = open_log()
    ensure_tab(log)
    _, summary = rows_of(log.worksheet('每日摘要'))
    _, detail = rows_of(log.worksheet('差異明細'))
    _, cands = rows_of(log.worksheet('提示詞候選'))
    since = (datetime.strptime(days[0], '%Y/%m/%d') - timedelta(days=14)).strftime('%Y/%m/%d')
    return friday, days, log, {
        'summary': [r for r in summary if norm_day(r.get('日期')) in days],
        'detail': [r for r in detail if norm_day(r.get('日期')) in days],
        'candidates': [r for r in cands if norm_day(r.get('日期')) >= since and r.get('狀態') != '已處理'],
    }


def ensure_tab(log):
    if WEEK_TAB not in {ws.title for ws in log.worksheets()}:
        ws = log.add_worksheet(WEEK_TAB, rows=500, cols=len(WEEK_HEAD))
        ws.update([WEEK_HEAD], 'A1')
    return log.worksheet(WEEK_TAB)


def audit_rows(book, days, limit=40, width=200):
    """逐字稿判讀稽核分頁本週的列（流程自己記下的疑點）；欄位截短，只供歸因。"""
    try:
        head, rows = rows_of(book.worksheet('逐字稿判讀稽核'))
    except Exception as e:  # 分頁不存在或讀不到：照樣做週檢討
        return {'error': f'讀不到逐字稿判讀稽核：{type(e).__name__}'}
    key = next((h for h in head if '日期' in h), None)
    if not key:
        return {'error': '逐字稿判讀稽核沒有日期欄'}
    mine = [r for r in rows if norm_day(r.get(key)) in days]
    counts = {}
    for r in mine:
        counts[norm_day(r.get(key))] = counts.get(norm_day(r.get(key)), 0) + 1
    return {'counts': counts, 'rows': [{h: str(r.get(h) or '')[:width] for h in head} for r in mine[-limit:]]}


def golden_index(days):
    gold = sorted(p.stem for p in (ROOT / 'scripts/golden').glob('20??-??-??.json'))
    week = {d: (d.replace('/', '-') in gold) for d in days}
    holdout = [g.replace('-', '/') for g in gold if g.replace('-', '/') < days[0]][-4:]
    return week, holdout


def cmd_fetch(a):
    friday, days, log, data = load_week(a.date or today())
    pl = pipeline()
    book = open_book(pl)
    open_days = trading(days)
    sites = site_rows(book, days)
    checks = verify_rows(data['detail'], sites, days)
    week_gold, holdout = golden_index(days)
    out = WORK / stamp_of(friday)
    payload = {'week': friday, 'days': days, 'trading_days': open_days, 'fetched_at': now().strftime('%Y/%m/%d %H:%M:%S'),
               'summary': data['summary'], 'detail': data['detail'], 'candidates': data['candidates'],
               'audit': audit_rows(book, days), 'verify': checks,
               'golden_this_week': week_gold, 'holdout_candidates': holdout,
               'site_now': {d: {c: [x['category'] for x in v] for c, v in s.items()} for d, s in sites.items()}}
    write_json(out / 'week.json', payload)
    lines = [f'# {days[0]}–{friday} 每週流程檢討輸入', '',
             f'交易日：{"、".join(open_days) or "無"}；有答案檔的本週日期：{"、".join(d for d, v in week_gold.items() if v) or "無"}；'
             f'保留日候選：{"、".join(holdout) or "無"}', '', '## 每日摘要', '',
             '| 日期 | 狀態 | 明確錯誤 | 待確認 | 邊界 | 複核錯 | 已修正 | 備註 |', '|---|---|---|---|---|---|---|---|']
    for r in data['summary']:
        lines.append('| ' + ' | '.join(str(r.get(k, '')) for k in ('日期', '狀態', '明確錯誤', '待確認', '邊界', '複核錯', '已修正', '備註')) + ' |')
    lines += ['', '## 差異明細（本週全部）', '', '| 日期 | 代號 | 名稱 | 網站 | 複核 | 類型 | 嚴重度 | 處理 | 原句 | 理由 |',
              '|---|---|---|---|---|---|---|---|---|---|']
    for r in data['detail']:
        lines.append('| ' + ' | '.join(str(r.get(k, '')).replace('|', '／').replace('\n', ' ')[:80] for k in
                                       ('日期', '代號', '名稱', '網站分類', '複核分類', '差異類型', '嚴重度', '處理', '原文依據', '判斷理由')) + ' |')
    lines += ['', '## 線上驗收（明確錯誤現在網站上的狀態）', '', '| 日期 | 代號 | 名稱 | 類型 | 處理 | 結果 | 說明 |', '|---|---|---|---|---|---|---|']
    for c in checks:
        lines.append(f"| {c['date']} | {c['code']} | {c['name']} | {c['type']} | {c['handling']} | {c['result']} | {c['why']} |")
    lines += ['', '## 提示詞候選（近三週未處理）', '']
    for r in data['candidates']:
        lines.append(f"- {r.get('日期')}｜{r.get('差異類型')}｜近兩週 {r.get('近兩週次數')} 次｜{r.get('建議修改')}｜狀態 {r.get('狀態')}")
    audit = payload['audit']
    lines += ['', '## 流程自己的疑點（逐字稿判讀稽核）', '',
              audit.get('error') or ('、'.join(f'{d} {n} 列' for d, n in sorted(audit['counts'].items())) or '本週沒有列')]
    (out / 'cases.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))
    print('\n輸入包：', out)


def cmd_verify(a):
    friday, days, _log, data = load_week(a.date or today())
    book = open_book(pipeline())
    checks = verify_rows(data['detail'], site_rows(book, days), days)
    if a.json:
        write_json(Path(a.json), {'week': friday, 'checks': checks})
    for c in checks:
        print(f"{c['result']}｜{c['date']} {c['name']}（{c['code']}）{c['type']}｜{c['why']}")
    n = {k: sum(1 for c in checks if c['result'] == k) for k in ('通過', '未通過', '需人工')}
    print(f"線上驗收 {friday} 週：明確錯誤 {len(checks)} 項，通過 {n['通過']}、未通過 {n['未通過']}、需人工 {n['需人工']}")
    return checks


def mark_updates(head, rows, days, checks, rule_cases, tag):
    """純函式：回傳 [(列號, 新的整列)]。只動本週、明確錯誤、線上驗收通過的列。"""
    passed = {(c['date'], c['code']) for c in checks if c['result'] == '通過'}
    out = []
    for i, r in enumerate(rows, 2):
        d, code = norm_day(r.get('日期')), str(r.get('代號') or '').strip()
        if d not in days or r.get('嚴重度') != '明確錯誤' or (d, code) not in passed:
            continue
        new = dict(r)
        if new.get('處理') not in ('已修正', '週檢討確認已正確'):
            new['處理'] = '週檢討確認已正確'
        if (d, code) in rule_cases:
            new['是否轉成規則或測試'] = f'是（{tag}）'
        if new != r:
            out.append((i, [new.get(h, '') for h in head]))
    return out


def cmd_log(a):
    friday, days, log, data = load_week(a.date or today())
    ws = ensure_tab(log)
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    run = a.run or os.environ.get('GITHUB_RUN_ID', '')
    rows = []
    if a.json:
        result = read_json(a.json)
        for x in result.get('rows', []):
            rows.append([friday, stamp, a.stage, x.get('date', ''), x.get('code', ''), x.get('name', ''), x.get('item', ''),
                         x.get('result', ''), str(x.get('note', ''))[:1000], run])
    if a.item:
        rows.append([friday, stamp, a.stage, '', '', '', a.item, a.result or '', (a.note or '')[:2000], run])
    if rows:
        ws.append_rows(rows, value_input_option='RAW')
    if a.mark_fixed:
        checks = read_json(a.json).get('checks', []) if a.json else []
        plan = read_json(a.plan) if a.plan and Path(a.plan).exists() else {}
        cases = {(norm_day(c.get('date')), str(c.get('code'))) for ch in plan.get('changes', []) for c in ch.get('cases', [])}
        detail = log.worksheet('差異明細')
        head, all_rows = rows_of(detail)
        for i, values in mark_updates(head, all_rows, days, checks, cases if a.merged else set(), a.tag or friday):
            detail.update([values], f'A{i}', value_input_option='RAW')
    print('LOGGED', friday, a.stage, len(rows), '列')


def cmd_wait(a):
    friday, _days, log, _ = load_week(a.date or today())
    deadline = time.time() + a.minutes * 60
    while True:
        _, rows = rows_of(ensure_tab(log))
        done = [r for r in rows if r.get('週') == friday and r.get('階段') == '工作流程' and r.get('項目') == '結論'
                and (not a.after or r.get('紀錄時間', '') >= a.after)]
        if done:
            r = done[-1]
            print('WORKFLOW_DONE', r.get('結果'), r.get('說明'), '執行', r.get('執行'))
            return
        if time.time() > deadline:
            print('WORKFLOW_PENDING', f'{a.minutes} 分鐘內工作流程還沒寫回結論；結果之後會出現在複核紀錄「{WEEK_TAB}」')
            sys.exit(6)
        time.sleep(300)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('fetch', 'verify', 'log', 'wait'):
        p = sub.add_parser(name)
        p.add_argument('--date', default='')
    sub.choices['verify'].add_argument('--json', default='')
    lg = sub.choices['log']
    lg.add_argument('--stage', required=True, choices=('排程', '工作流程', '線上驗收'))
    lg.add_argument('--item', default='')
    lg.add_argument('--result', default='')
    lg.add_argument('--note', default='')
    lg.add_argument('--json', default='')
    lg.add_argument('--run', default='')
    lg.add_argument('--plan', default='')
    lg.add_argument('--tag', default='')
    lg.add_argument('--merged', action='store_true')
    lg.add_argument('--mark-fixed', action='store_true')
    wt = sub.choices['wait']
    wt.add_argument('--minutes', type=int, default=90)
    wt.add_argument('--after', default='', help='只認這個時間（YYYY/MM/DD HH:MM:SS）之後的結論')
    a = ap.parse_args()
    {'fetch': cmd_fetch, 'verify': cmd_verify, 'log': cmd_log, 'wait': cmd_wait}[a.cmd](a)


if __name__ == '__main__':
    main()
