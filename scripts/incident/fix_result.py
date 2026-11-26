"""incident-fix.yml 最後一步：依守門、離線契約、重播的結果下結論。

全部通過：INCIDENT_AUTO_MERGE 不是 true（管理者 2026/11/26：前兩週由管理者核准）→ 開 PR 等核准；是 true → 合併 main。
不通過：寫明哪一關、為什麼。結果寫回「事故紀錄」並推 LINE（triage.py resolve）。
改到 Apps Script 原始碼時註明：合併後還要建立 Apps Script 新版本，正式 /exec 由擁有者切換。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = os.environ.get('GITHUB_REPOSITORY', 'Lee200202/Stock')


def load(p, default):
    try:
        return json.loads(Path(p).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def git(*a):
    return subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')


def verdict(gate, offline, replay, gate_json, replays):
    if gate != 'success':
        return '守門不通過', '；'.join(gate_json.get('problems') or ['守門步驟失敗'])[:300]
    if offline != 'success':
        return '測試不通過', '離線契約或網站檢查失敗，見執行紀錄'
    if gate_json.get('pipeline_changed'):
        res = replays.get('results') or []
        if replay != 'success' or not res or not all(r.get('ok') for r in res):
            if res and all(r.get('ok') or r.get('infra') for r in res):
                return '重播遇到服務故障', 'Gemini 暫時故障，重播無法完成；不是這次修改造成的退步'
            bad = [f"{r.get('date')}：" + '、'.join(r.get('new_misses') or r.get('misses') or [])[:80] for r in res if not r.get('ok')]
            return '重播未通過', '；'.join(bad)[:300] or '重播步驟失敗'
    return '通過', ''


def open_pr(branch, iid, gate_json):
    import requests
    plan = load(ROOT / 'docs' / 'incident' / iid / 'plan.json', {})
    body = (f"事故 {iid}\n\n成因：{plan.get('cause', '')}\n\n修正：{plan.get('fix', '')}\n\n重現方式：{plan.get('repro', '')}\n\n"
            f"修正對象：{plan.get('target', '')}；改動檔案：{', '.join(gate_json.get('changed') or [])}\n\n"
            f"守門、離線契約{'、真實模型重播' if gate_json.get('pipeline_changed') else ''}全部通過。執行紀錄：{os.environ.get('RUN_URL', '')}\n"
            + ('\n合併後還要建立 Apps Script 新版本，正式 /exec 由擁有者切換。\n' if gate_json.get('gas_changed') else ''))
    r = requests.post(f'https://api.github.com/repos/{REPO}/pulls', timeout=60,
                      headers={'Authorization': f"Bearer {os.environ['GH_TOKEN']}", 'Accept': 'application/vnd.github+json'},
                      json={'title': f"事故修正 {iid}：{plan.get('cause', '')}"[:120], 'head': branch, 'base': 'main', 'body': body})
    if r.status_code in (200, 201):
        return r.json().get('html_url', '')
    if r.status_code == 422 and 'already exists' in r.text:
        return f'https://github.com/{REPO}/pulls?q=head%3A{branch}'
    print(f'開 PR 失敗（HTTP {r.status_code}），改附比較頁連結')
    return f'https://github.com/{REPO}/compare/main...{branch}'


def merge(branch):
    git('fetch', '-q', 'origin', 'main')
    m = git('merge', '--no-edit', 'origin/main')
    if m.returncode != 0:
        git('merge', '--abort')
        return False, '與 main 有衝突，改由管理者處理'
    p = git('push', 'origin', 'HEAD:main')
    return p.returncode == 0, ('' if p.returncode == 0 else p.stderr[-200:])


def main():
    branch = os.environ['BRANCH']
    gate_json = load('.incident/gate.json', {})
    replays = load('.incident/replays.json', {})
    iid = gate_json.get('incident_id') or (branch.rsplit('-', 1)[-1] if 'INC' in branch else '')
    v, why = verdict(os.environ.get('GATE', ''), os.environ.get('OFFLINE', ''), os.environ.get('REPLAY', 'skipped'), gate_json, replays)
    if v == '通過':
        if os.environ.get('AUTO_MERGE', '').strip().lower() == 'true':
            ok, err = merge(branch)
            state, note = ('已合併', '守門與測試通過，已合併 main') if ok else ('待管理者', '自動合併失敗：' + err)
        else:
            url = open_pr(branch, iid, gate_json)
            state, note = '待你核准合併', f'守門與測試通過，請核准合併：{url}'
        if gate_json.get('gas_changed'):
            note += '；合併後要建立 Apps Script 新版本，/exec 由擁有者切換'
    else:
        state, note = v, why
    print(f'{iid}：{state}；{note}')
    with open(os.environ.get('GITHUB_STEP_SUMMARY', os.devnull), 'a', encoding='utf-8') as f:
        f.write(f'### 事故修正 {iid}\n\n{state}：{note}\n')
    if iid and os.environ.get('REVIEW_LOG_ID'):
        subprocess.run([sys.executable, '-B', 'scripts/incident/triage.py', 'resolve', '--id', iid, '--state', state,
                        '--note', note, '--branch', branch], cwd=ROOT, check=False)
    return 0 if v == '通過' else 1


if __name__ == '__main__':
    sys.exit(main())
