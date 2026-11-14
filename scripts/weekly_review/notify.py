"""每週流程檢討的結果寄到管理者的 LINE（2026/11/14 管理者）。

管理者：「每週確定定義問題之後，pipeline 也要同步更新；把審核的結果用卡片的形式寄給 LINE，需要管理者指示的在卡片裡提出。」
11/13 那一週的流程修正因為重播遇到 Google 503 而沒有合併，結果只寫進複核紀錄，管理者完全不知道。從這一週起：
  - 每一次 pipeline-weekly.yml 跑完（合併或沒合併）都寫一列「管理者通知」，Apps Script 五分鐘排程把它做成卡片推到 LINE；
  - 沒合併時另寫一題「管理者問題」（附選項按鈕），管理者在 LINE 點選，答案照既有流程交給雲端。

    python scripts/weekly_review/notify.py --folder .weekly --date 2026/11/13 [--dry-run]

純函式 build_card() 不連線，離線測試見 scripts/test_weekly_review.py。
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_URL = 'https://github.com/Lee200202/Stock'


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def branch_changes(branch):
    """這個分支比 main 多的提交主旨（卡片「本週改了什麼」）。"""
    try:
        # 合併後 origin/main..分支 是空的，所以看分支最近 7 天的週檢討提交（weekly(…) 開頭）
        out = subprocess.run(['git', 'log', '--since=7.days', '--grep=^weekly(', '--format=%s', 'HEAD'], cwd=ROOT,
                             capture_output=True, text=True, encoding='utf-8', timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [l.split(':', 1)[-1].strip() for l in out.splitlines() if l.strip()][:8]


def build_card(day, result, gate, replays, verify, changes, run_url='', merged_sha=''):
    """純函式：卡片內容（Apps Script 的 lineAdminNoticeFlex_ 照這個畫）。"""
    merged = bool(result.get('merged'))
    days = []
    for r in replays.get('results') or []:
        if r.get('ok'):
            status, note = 'pass', ('通過' if r.get('exit_code') == 0 else '通過（不符只是單次變異或 main 也有）')
        elif r.get('infra'):
            status, note = 'infra', 'Google 服務暫時故障，重跑後仍無法完成'
        else:
            status, note = 'fail', '新增不符：' + '；'.join(r.get('new_misses') or r.get('misses') or [])[:160]
        days.append({'date': r.get('date', ''), 'role': r.get('role', ''), 'status': status, 'note': note,
                     'requests': r.get('requests', 0)})
    checks = verify.get('checks') or []
    still = [c for c in checks if c.get('result') != '通過']
    offline = next((r.get('result') for r in result.get('rows') or [] if r.get('item') == '離線契約'), '未執行')
    gate_row = next((r for r in result.get('rows') or [] if r.get('item') == '守門'), {})
    asks = []
    if not merged:
        if days and all(d['status'] in ('pass', 'infra') for d in days) and any(d['status'] == 'infra' for d in days):
            asks.append({'question': f'{day} 週檢討的流程修正沒有合併：重播遇到 Google 服務暫時故障。要怎麼處理？',
                         'context': '其他重播日都沒有退步；只是有一天被 Google 503 打斷，無法完成比對。',
                         'options': ['今晚重跑本週重播，通過就合併', '下週一起重跑', '這週的修正先不要']})
        elif gate_row.get('result') == '不通過':
            asks.append({'question': f'{day} 週檢討改到不該改的檔案，守門擋下沒有合併。要怎麼處理？',
                         'context': '；'.join([gate_row.get('note', '')])[:300],
                         'options': ['退回重做本週檢討', '這週的修正先不要']})
        else:
            fails = [d for d in days if d['status'] == 'fail']
            asks.append({'question': f'{day} 週檢討的流程修正沒有合併：重播有退步。要怎麼處理？',
                         'context': '；'.join(f"{d['date']} {d['note']}" for d in fails)[:300] or result.get('verdict', ''),
                         'options': ['退回重做本週檢討，修掉退步再合併', '只合併沒有退步的部分', '這週的修正先不要']})
    for c in still[:3]:
        asks.append({'question': f"{c.get('date', '')} {c.get('name', '')}（{c.get('code', '')}）網站仍不正確：{c.get('type', '')}",
                     'context': str(c.get('why', ''))[:300],
                     'options': ['照本週檢討的結論修正網站', '維持網站現在的內容', '我再看一下原稿']})
    headline = ('流程修正已合併，下一次每日流程開始生效' if merged else result.get('verdict') or '未合併')
    return {
        'type': 'weekly', 'date': day, 'merged': merged, 'headline': headline,
        'verdict': result.get('verdict', ''), 'mergedSha': (merged_sha or '')[:9],
        'pipelineChanged': bool(gate.get('pipeline_changed')), 'changes': changes,
        'gate': gate_row.get('result', ''), 'offline': offline,
        'days': days, 'requests': replays.get('requests', 0),
        'live': {'total': len(checks), 'ok': len(checks) - len(still),
                 'problems': [{'date': c.get('date', ''), 'name': c.get('name', ''), 'code': c.get('code', ''),
                               'type': c.get('type', ''), 'why': str(c.get('why', ''))[:120]} for c in still[:6]]},
        'asks': asks, 'reportUrl': f"{REPO_URL}/blob/main/docs/weekly-review/{day.replace('/', '-')}/報告.md",
        'runUrl': run_url,
    }


def write(card, dry_run=False):
    sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
    from common import now, open_log, rows_of
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    nid = 'W' + card['date'].replace('/', '') + now().strftime('%H%M%S')
    print(json.dumps(card, ensure_ascii=False, indent=1))
    if dry_run:
        print('（試跑，不寫入）')
        return nid
    book = open_log()
    ws = book.worksheet('管理者通知')
    head = rows_of(ws)[0]
    row = {'建立時間': stamp, '通知ID': nid, '日期': card['date'], '種類': '每週檢討', '標題': card['headline'],
           '內容': json.dumps(card, ensure_ascii=False), '狀態': '待推送', 'LINE推送': ''}
    ws.append_row([row.get(h, '') for h in head], value_input_option='RAW')
    if card['asks']:
        from questions import new_row
        qws = book.worksheet('管理者問題')
        qhead, qrows = rows_of(qws)
        for i, a in enumerate(card['asks']):
            _, values = new_row(qhead, day=card['date'], question=a['question'], context=a['context'],
                                options=json.dumps(a['options'], ensure_ascii=False), stamp=stamp, seq=(len(qrows) + i) % 10)
            qws.append_row(values, value_input_option='RAW')
    print(f"管理者通知 {nid} 已寫入；要你決定 {len(card['asks'])} 題；Apps Script 五分鐘內推到 LINE")
    return nid


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--folder', default='.weekly')
    ap.add_argument('--date', required=True)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    f = Path(a.folder)
    result, gate = load(f / 'result.json', {}) or {}, load(f / 'gate.json', {}) or {}
    replays, verify = load(f / 'replays.json', {}) or {}, load(f / 'verify.json', {}) or {}
    branch = os.environ.get('BRANCH', '')
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', 'Lee200202/Stock')}"
               f"/actions/runs/{os.environ['GITHUB_RUN_ID']}") if os.environ.get('GITHUB_RUN_ID') else ''
    card = build_card(a.date, result, gate, replays, verify, branch_changes(branch) if branch else [], run_url,
                      os.environ.get('MERGED_SHA', ''))
    write(card, a.dry_run)


if __name__ == '__main__':
    main()
