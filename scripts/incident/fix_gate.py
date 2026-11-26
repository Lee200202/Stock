"""雲端事故修正分支的守門（2026/11/26）。incident-fix.yml 第一步：

  python scripts/incident/fix_gate.py --base origin/main --branch claude/incident-20261126-INC... --json .incident/gate.json

只准：
  - 分支名稱 claude/incident-YYYYMMDD-INC…，且附 docs/incident/<事故ID>/plan.json（成因、修正、重現方式、修正對象、重播日）。
  - 改 pipeline/、transcript.py、market_data.py、scripts/（scripts/incident/ 除外：AI 不能改自己的規則與上限）、
    public-site/（網站與 Apps Script 原始碼）、site-api/、docs/incident/<事故ID>/。
  - 不准改 .github/、AGENTS.md、技能文件、requirements；既有答案檔只准加；run_offline_contracts.py 只准加測試。
  - 改了 pipeline/pipeline.py 就要有重播日與至少兩天保留日（沿用每週檢討的規則與上限）。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts' / 'weekly_review'))
from check_plan import MAX_DAYS, contracts_problems, golden_problems  # noqa: E402

ALLOWED = ('pipeline/', 'transcript.py', 'market_data.py', 'scripts/', 'public-site/', 'site-api/')
FORBIDDEN = ('scripts/incident/', '.github/', 'AGENTS.md', 'docs/stock-skills/', 'requirements.txt', 'pipeline/requirements.txt')


def git(*args):
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, encoding='utf-8').stdout


def show(ref, path):
    p = subprocess.run(['git', 'show', f'{ref}:{path}'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
    return p.stdout if p.returncode == 0 else None


def check(base, branch, changed, plan_text):
    out = []
    m = re.fullmatch(r'claude/incident-(\d{8})-(INC\d{14})', branch)
    if not m:
        return {'ok': False, 'problems': ['分支名稱要是 claude/incident-YYYYMMDD-INC事故ID']}
    iid = m.group(2)
    own_docs = f'docs/incident/{iid}/'
    for f in changed:
        if f.startswith(FORBIDDEN) or not (f.startswith(ALLOWED) or f.startswith(own_docs)):
            out.append(f'不准改：{f}')
    if plan_text is None:
        out.append(f'缺少 {own_docs}plan.json')
        plan = {}
    else:
        try:
            plan = json.loads(plan_text)
        except ValueError as e:
            plan, _ = {}, out.append(f'plan.json 不是合法 JSON（{e}）')
    for key in ('incident_id', 'target', 'cause', 'fix', 'repro'):
        if not plan.get(key):
            out.append(f'plan.json 缺 {key}')
    if plan.get('incident_id') and plan['incident_id'] != iid:
        out.append('plan.json 的 incident_id 與分支不符')
    pipeline_changed = 'pipeline/pipeline.py' in changed
    replay = [str(d) for d in plan.get('replay_days') or []]
    holdout = [str(d) for d in plan.get('holdout_days') or []]
    if pipeline_changed:
        if not replay:
            out.append('改了 pipeline/pipeline.py 但沒有重播日')
        if len(set(holdout) - set(replay)) < 2:
            out.append('改了 pipeline/pipeline.py 但保留日少於兩天')
    if len(set(replay + holdout)) > MAX_DAYS:
        out.append(f'重播日合計超過 {MAX_DAYS} 天')
    for d in replay + holdout:
        if not (ROOT / 'scripts/golden' / (d.replace('/', '-') + '.json')).exists():
            out.append(f'{d} 沒有答案檔，不能重播評分')
    for f in changed:
        if f.startswith('scripts/golden/') and f.endswith('.json'):
            new = show('HEAD', f)
            out += golden_problems(f, show(base, f), new) if new is not None else [f'刪除了答案檔 {f}']
        if f == 'scripts/run_offline_contracts.py':
            out += contracts_problems(show(base, f) or '', show('HEAD', f) or '')
    return {'ok': not out, 'problems': out, 'incident_id': iid, 'target': plan.get('target', ''),
            'pipeline_changed': pipeline_changed, 'gas_changed': any(f.startswith('public-site/gas-source/') for f in changed),
            'site_changed': any(f.startswith(('public-site/', 'site-api/')) for f in changed),
            'replay_days': replay, 'holdout_days': holdout, 'changed': changed}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--branch', required=True)
    ap.add_argument('--json', required=True)
    a = ap.parse_args()
    changed = [l for l in git('diff', '--name-only', f'{a.base}...HEAD').splitlines() if l.strip()]
    m = re.search(r'(INC\d{14})', a.branch)
    plan_text = show('HEAD', f'docs/incident/{m.group(1)}/plan.json') if m else None
    res = check(a.base, a.branch, changed, plan_text)
    Path(a.json).parent.mkdir(parents=True, exist_ok=True)
    Path(a.json).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if res['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
