"""自主事故處理：GitHub 端（2026/11/26 管理者核准方案）。

  python scripts/incident/triage.py triage --run-id 123 [--dry-run]   # 某個正式工作失敗時（incident-triage.yml 的 workflow_run）
  python scripts/incident/triage.py sweep [--dry-run]                 # 每 30 分鐘：到期的重跑、重跑結果、整點彙整的通知
  python scripts/incident/triage.py resolve --id INC... --state 已修正 --note ... [--branch ...]   # 雲端修正工作流程回報

紀錄寫在複核紀錄「事故紀錄」；通知寫「管理者通知」（種類＝事故，Apps Script 推到管理者 LINE，同一小時合併成一則）。
分類與決策規則在 rules.py（純函式）。不印試算表 ID、不印金鑰；錯誤摘要取自 GitHub 已遮蔽的執行紀錄。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
import rules  # noqa: E402

API = 'https://api.github.com'
REPO = os.environ.get('GITHUB_REPOSITORY', 'Lee200202/Stock')
TAB = '事故紀錄'
HEAD = ['建立時間', '事故ID', '日期', '工作流程', '執行', '嘗試', '失敗步驟', '分類', '指紋', '連續失敗', '決策', '預定重跑',
        '說明', '處理狀態', '分支', '結果', '更新時間', '錯誤摘要']
NOTICE_FEATURE = 'incident-notice-v233'


def now():
    return datetime.now(rules.TAIPEI)


def stamp(t=None):
    return (t or now()).strftime('%Y/%m/%d %H:%M:%S')


def gh(method, path, **kw):
    import requests
    token = os.environ.get('GITHUB_TOKEN', '')
    r = requests.request(method, API + path, headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'},
                         timeout=60, **kw)
    return r


def failed_step(run_id):
    """第一個失敗的 job 與步驟，以及該 job 錯誤紀錄的尾段（去掉時間戳記，最多 80 行）。"""
    jobs = gh('GET', f'/repos/{REPO}/actions/runs/{run_id}/jobs?per_page=50').json().get('jobs', [])
    for job in jobs:
        if job.get('conclusion') not in ('failure', 'timed_out', 'startup_failure'):
            continue
        step = next((s.get('name', '') for s in job.get('steps') or [] if s.get('conclusion') in ('failure', 'timed_out')), job.get('name', ''))
        log = gh('GET', f'/repos/{REPO}/actions/jobs/{job["id"]}/logs').text if job.get('id') else ''
        lines = [re.sub(r'^\d{4}-\d\d-\d\dT[\d:.]+Z\s?', '', l) for l in log.splitlines()]
        lines = [l for l in lines if l.strip() and not l.startswith(('##[group]', '##[endgroup]'))]
        return job.get('name', ''), step, '\n'.join(lines[-80:])
    return '', '', ''


def consecutive_failures(workflow_id, upto_run_id):
    runs = gh('GET', f'/repos/{REPO}/actions/workflows/{workflow_id}/runs?per_page=20').json().get('workflow_runs', [])
    n = 0
    for r in runs:     # 新到舊
        if r['id'] > upto_run_id or r.get('status') != 'completed':
            continue
        if r.get('conclusion') in ('failure', 'timed_out', 'startup_failure'):
            n += 1
        elif r.get('conclusion') == 'success':
            break
    return max(n, 1)


def open_tabs():
    from common import open_log, rows_of
    book = open_log()
    names = {w.title for w in book.worksheets()}
    if TAB not in names:
        ws = book.add_worksheet(TAB, rows=500, cols=len(HEAD))
        ws.update([HEAD], 'A1')
    return book, rows_of


def history(book, rows_of, days=7):
    _, rows = rows_of(book.worksheet(TAB))
    cut = (now() - timedelta(days=days)).strftime('%Y/%m/%d')
    out = []
    for r in rows:
        if r.get('日期', '') >= cut:
            out.append({'day': r.get('日期', ''), 'workflow': r.get('工作流程', ''), 'decision': r.get('決策', ''),
                        'fingerprint': r.get('指紋', ''), 'category': r.get('分類', ''), 'run': r.get('執行', ''),
                        'attempt': r.get('嘗試', '')})
    return out


def gas_supports_notice():
    import requests
    url = os.environ.get('APPS_SCRIPT_URL', '').strip()
    if not url:
        return False
    try:
        data = requests.get(url, params={'action': 'ping'}, timeout=60).json()
        return NOTICE_FEATURE in (data.get('features') or [])
    except Exception:  # noqa: BLE001
        return False


def notice(book, rows_of, item, urgent, dry_run=False):
    """同一小時的事故合併成一則「管理者通知」（種類＝事故）。急件或整點過後改成「待推送」。
    Apps Script 還不認得事故卡時（舊 /exec）先留「彙整中」，等新版上線再推，避免被舊版標成格式不明。"""
    hour = now().strftime('%Y/%m/%d %H')
    ws = book.worksheet('管理者通知')
    head, rows = rows_of(ws)
    col = {h: i + 1 for i, h in enumerate(head)}
    target = None
    for i, r in enumerate(rows, start=2):
        if r.get('種類') == '事故' and r.get('狀態') in ('彙整中', '待新版') and r.get('建立時間', '').startswith(hour):
            target = (i, r)
    if target:
        card = json.loads(target[1].get('內容') or '{}')
        card['items'].append(item)
    else:
        card = {'type': 'incident', 'hour': hour, 'items': [item]}
    card['urgent'] = bool(card.get('urgent') or urgent)
    titles = [f"{x['workflow']}：{x['label']}" for x in card['items']]
    title = f"事故 {len(card['items'])} 件：" + '、'.join(titles)[:80]
    state = '彙整中'
    if card['urgent']:
        state = '待推送' if gas_supports_notice() else '待新版'
    if dry_run:
        print('（試跑）通知：', state, json.dumps(card, ensure_ascii=False)[:400])
        return
    if target:
        i = target[0]
        ws.update_cell(i, col['內容'], json.dumps(card, ensure_ascii=False))
        ws.update_cell(i, col['標題'], title)
        ws.update_cell(i, col['狀態'], state)
    else:
        nid = 'I' + now().strftime('%Y%m%d%H%M%S')
        row = {'建立時間': stamp(), '通知ID': nid, '日期': now().strftime('%Y/%m/%d'), '種類': '事故', '標題': title,
               '內容': json.dumps(card, ensure_ascii=False), '狀態': state, 'LINE推送': ''}
        ws.append_row([row.get(h, '') for h in head], value_input_option='RAW')


def cmd_triage(run_id, dry_run=False):
    run = gh('GET', f'/repos/{REPO}/actions/runs/{run_id}').json()
    wf, concl = run.get('name', ''), run.get('conclusion', '')
    if wf not in rules.WATCHED:
        print(f'{wf} 不在事故處理範圍，略過')
        return 0
    if concl not in ('failure', 'timed_out', 'startup_failure'):
        print(f'{wf} 結束狀態 {concl}，不是失敗，略過')
        return 0
    job, step, tail = failed_step(run_id)
    cat = rules.classify(tail, concl)
    fp = rules.fingerprint(wf, step, tail)
    consec = consecutive_failures(run['workflow_id'], int(run_id))
    switched = bool(re.search(r'已整本換到新卷|換到第 \d+ 卷', tail))
    if dry_run and not os.environ.get('REVIEW_LOG_ID'):
        hist, book, rows_of = [], None, None
    else:
        book, rows_of = open_tabs()
        hist = history(book, rows_of)
    attempt = str(run.get('run_attempt', 1))
    if any(h['run'] == str(run_id) and h['attempt'] == attempt for h in hist):
        print('這一次執行已經處理過，略過')
        return 0
    d = rules.decide(cat, wf, now(), hist, fp, switched, consec)
    iid = 'INC' + now().strftime('%Y%m%d%H%M%S')
    state = {'record': '已記錄', 'retry': '待重跑', 'notify': '待管理者', 'cloud': '待雲端處理', 'stop': '停止（待管理者）'}[d['action']]
    row = {'建立時間': stamp(), '事故ID': iid, '日期': now().strftime('%Y/%m/%d'), '工作流程': wf, '執行': str(run_id),
           '嘗試': attempt, '失敗步驟': f'{job}／{step}', '分類': rules.LABEL[cat], '指紋': fp, '連續失敗': str(consec),
           '決策': d['action'], '預定重跑': stamp(d['due']) if d['due'] else '', '說明': d['why'], '處理狀態': state,
           '分支': '', '結果': '', '更新時間': stamp(), '錯誤摘要': tail[-30000:]}
    print(json.dumps({k: v for k, v in row.items() if k != '錯誤摘要'}, ensure_ascii=False, indent=1))
    print('錯誤摘要（最後 12 行）：\n' + '\n'.join(tail.splitlines()[-12:]))
    if dry_run:
        print('（試跑，不寫紀錄、不重跑、不通知）')
        return 0
    ws = book.worksheet(TAB)
    head = ws.row_values(1)
    ws.append_row([row.get(h, '') for h in head], value_input_option='RAW')
    if d['notify']:
        notice(book, rows_of, {'id': iid, 'workflow': wf, 'label': rules.LABEL[cat], 'decision': d['action'], 'why': d['why'],
                               'due': row['預定重跑'][11:16], 'step': step[:60], 'runUrl': run.get('html_url', ''),
                               'time': stamp()[11:16]}, urgent=d['action'] in ('notify', 'stop') or cat == 'capacity')
    if d['action'] == 'retry' and d['due'] and d['due'] <= now() + timedelta(minutes=1):
        cmd_sweep(False)
    return 0


def cmd_sweep(dry_run=False):
    book, rows_of = open_tabs()
    ws = book.worksheet(TAB)
    head, rows = rows_of(ws)
    col = {h: i + 1 for i, h in enumerate(head)}
    t = now()
    for i, r in enumerate(rows, start=2):
        st = r.get('處理狀態', '')
        if st == '待重跑' and r.get('預定重跑') and r['預定重跑'] <= stamp(t):
            print(f"{r['事故ID']} 到期，重跑 {r['工作流程']} 執行 {r['執行']} 的失敗工作")
            if not dry_run:
                res = gh('POST', f"/repos/{REPO}/actions/runs/{r['執行']}/rerun-failed-jobs")
                ok = res.status_code in (201, 204)
                ws.update_cell(i, col['處理狀態'], '已重跑' if ok else '重跑請求失敗')
                ws.update_cell(i, col['結果'], '' if ok else f'HTTP {res.status_code} {res.text[:120]}')
                ws.update_cell(i, col['更新時間'], stamp())
                if not ok:
                    notice(book, rows_of, {'id': r['事故ID'], 'workflow': r['工作流程'], 'label': r['分類'], 'decision': 'notify',
                                           'why': f'自動重跑請求被拒（HTTP {res.status_code}）', 'due': '', 'step': '', 'runUrl': '',
                                           'time': stamp()[11:16]}, urgent=True)
        elif st == '已重跑':
            run = gh('GET', f"/repos/{REPO}/actions/runs/{r['執行']}").json()
            if run.get('status') != 'completed':
                continue
            if run.get('conclusion') == 'success':
                print(f"{r['事故ID']} 重跑成功")
                if not dry_run:
                    ws.update_cell(i, col['處理狀態'], '已恢復')
                    ws.update_cell(i, col['結果'], '重跑成功')
                    ws.update_cell(i, col['更新時間'], stamp())
            else:
                print(f"{r['事故ID']} 重跑仍失敗")
                if not dry_run:
                    ws.update_cell(i, col['處理狀態'], '重跑失敗')
                    ws.update_cell(i, col['更新時間'], stamp())
                    # 重跑的結果不會再觸發 workflow_run（GITHUB_TOKEN 發起的事件不再觸發其他工作流程），所以這裡補一次判斷
                    cmd_triage_followup(book, rows_of, r)
    flush_notices(book, rows_of, dry_run)
    return 0


def cmd_triage_followup(book, rows_of, r):
    """重跑仍失敗：用同一套規則再判斷一次（會算進每天重跑上限）。"""
    os.environ.setdefault('INCIDENT_FOLLOWUP', '1')
    run_id = r['執行']
    run = gh('GET', f'/repos/{REPO}/actions/runs/{run_id}').json()
    job, step, tail = failed_step(run_id)
    cat = rules.classify(tail, run.get('conclusion', 'failure'))
    fp = rules.fingerprint(r['工作流程'], step, tail)
    hist = history(book, rows_of)
    if any(h['run'] == str(run_id) and h['attempt'] == str(run.get('run_attempt', '')) for h in hist):
        return      # 這次重跑的結果已由 workflow_run 判斷過
    d = rules.decide(cat, r['工作流程'], now(), hist, fp, False, rules.CONSECUTIVE_NOTIFY)
    iid = 'INC' + now().strftime('%Y%m%d%H%M%S')
    state = {'record': '已記錄', 'retry': '待重跑', 'notify': '待管理者', 'cloud': '待雲端處理', 'stop': '停止（待管理者）'}[d['action']]
    ws = book.worksheet(TAB)
    head = ws.row_values(1)
    row = {'建立時間': stamp(), '事故ID': iid, '日期': now().strftime('%Y/%m/%d'), '工作流程': r['工作流程'], '執行': str(run_id),
           '嘗試': str(run.get('run_attempt', '')), '失敗步驟': f'{job}／{step}', '分類': rules.LABEL[cat], '指紋': fp,
           '連續失敗': str(rules.CONSECUTIVE_NOTIFY), '決策': d['action'], '預定重跑': stamp(d['due']) if d['due'] else '',
           '說明': '重跑後仍失敗；' + d['why'], '處理狀態': state, '分支': '', '結果': '', '更新時間': stamp(), '錯誤摘要': tail[-30000:]}
    ws.append_row([row.get(h, '') for h in head], value_input_option='RAW')
    notice(book, rows_of, {'id': iid, 'workflow': r['工作流程'], 'label': rules.LABEL[cat], 'decision': d['action'],
                           'why': row['說明'], 'due': row['預定重跑'][11:16], 'step': step[:60], 'runUrl': run.get('html_url', ''),
                           'time': stamp()[11:16]}, urgent=d['action'] in ('notify', 'stop'))


def flush_notices(book, rows_of, dry_run=False):
    """整點過後，上一個小時的「彙整中」改成「待推送」；Apps Script 還是舊版時改成「待新版」。「待新版」在新版上線後推送。"""
    ws = book.worksheet('管理者通知')
    head, rows = rows_of(ws)
    col = {h: i + 1 for i, h in enumerate(head)}
    hour = now().strftime('%Y/%m/%d %H')
    ready = None
    for i, r in enumerate(rows, start=2):
        if r.get('種類') != '事故':
            continue
        st = r.get('狀態')
        due = (st == '彙整中' and not r.get('建立時間', '').startswith(hour)) or st == '待新版'
        if not due:
            continue
        if ready is None:
            ready = gas_supports_notice()
        new = '待推送' if ready else '待新版'
        if new != st:
            print(f"通知 {r.get('通知ID')}：{st} → {new}")
            if not dry_run:
                ws.update_cell(i, col['狀態'], new)


def cmd_resolve(iid, state, note, branch=''):
    book, rows_of = open_tabs()
    ws = book.worksheet(TAB)
    head, rows = rows_of(ws)
    col = {h: i + 1 for i, h in enumerate(head)}
    for i, r in enumerate(rows, start=2):
        if r.get('事故ID') == iid:
            ws.update_cell(i, col['處理狀態'], state)
            ws.update_cell(i, col['結果'], note[:500])
            if branch:
                ws.update_cell(i, col['分支'], branch)
            ws.update_cell(i, col['更新時間'], stamp())
            notice(book, rows_of, {'id': iid, 'workflow': r.get('工作流程', ''), 'label': r.get('分類', ''), 'decision': 'resolve',
                                   'why': f'{state}：{note}'[:200], 'due': '', 'step': '', 'runUrl': os.environ.get('RUN_URL', ''),
                                   'branch': branch, 'time': stamp()[11:16]}, urgent=True)
            print(f'{iid} → {state}')
            return 0
    print(f'找不到 {iid}')
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('triage')
    t.add_argument('--run-id', required=True)
    t.add_argument('--dry-run', action='store_true')
    s = sub.add_parser('sweep')
    s.add_argument('--dry-run', action='store_true')
    r = sub.add_parser('resolve')
    r.add_argument('--id', required=True)
    r.add_argument('--state', required=True)
    r.add_argument('--note', default='')
    r.add_argument('--branch', default='')
    a = ap.parse_args()
    if a.cmd == 'triage':
        return cmd_triage(a.run_id, a.dry_run)
    if a.cmd == 'sweep':
        return cmd_sweep(a.dry_run)
    return cmd_resolve(a.id, a.state, a.note, a.branch)


if __name__ == '__main__':
    sys.exit(main())
