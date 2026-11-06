"""每週流程檢討分支的守門：只讓週檢討該改的東西進來，其餘一律擋下（不合併）。

    python scripts/weekly_review/check_plan.py --base origin/main --branch claude/pipeline-weekly-20261106 [--json out.json]

規則（管理者 2026/11/06 授權每週五自動修整流程；守門依 AGENTS.md「未通過實驗不合併」）：
- 分支名稱 claude/pipeline-weekly-YYYYMMDD 或 pipeline-weekly/YYYYMMDD，日期是台北時間今天或前兩天；週＝該日所在的週一到週五。
- 計畫檔 docs/weekly-review/<YYYY-MM-DD 週五>/plan.json 必須存在。
- 只准改：pipeline/pipeline.py；新增 scripts/test_*.py；scripts/run_offline_contracts.py 只准加測試名稱；
  本週日期的 scripts/golden/*.json（新增，或在既有檔只加不改不刪）；本週日期的 scripts/repairs/ 修正計畫（只准新增）；
  docs/weekly-review/<該週五>/ 底下的檔案。
- 既有測試一個字都不准改（不改期望值讓測試變綠）。
- Apps Script 有對應片段的提示詞與名稱表（sync_quality.py 同步的那幾個）不准變：要同步 GAS 就留給管理者。
- 改了 pipeline/pipeline.py 就必須有本週重播日（有答案檔）與至少兩個保留日，合計不超過六天。
不呼叫模型、不連網（只讀本機 git）。
"""
import argparse
import ast
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from week import week_of  # noqa: E402

TAIPEI = timezone(timedelta(hours=8))
BRANCH = re.compile(r'^(?:claude/pipeline-weekly-|pipeline-weekly/)(20\d{6})$')
MIRRORED = ('COT_FIVE_LAYERS', 'POLICY', 'POLISH_SYSTEM', 'EXTRACT_SYSTEM', 'AUDIT_SYSTEM', 'ARTICLE_SYSTEM',
            'TX_FORMAT_SYSTEM', 'CM_PARSE_SYSTEM', 'CONFIRMED_NAMES')
REPAIR = re.compile(r'^scripts/repairs/(\d{4}-\d{2}-\d{2})-(source-review|notice-reason|points-review)\.json$')
GOLDEN = re.compile(r'^scripts/golden/(\d{4}-\d{2}-\d{2})\.json$')
MAX_DAYS = 6


def git(*args):
    return subprocess.run(['git', '-c', 'core.quotepath=false', *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8').stdout


def show(ref, path):
    try:
        return git('show', f'{ref}:{path}')
    except subprocess.CalledProcessError:
        return None


def stamp_problems(branch, today):
    m = BRANCH.match(branch)
    if not m:
        return None, ['分支名稱要是 claude/pipeline-weekly-YYYYMMDD 或 pipeline-weekly/YYYYMMDD']
    day = datetime.strptime(m.group(1), '%Y%m%d').date()
    if not (today - timedelta(days=2) <= day <= today):
        return None, [f'分支日期 {day} 不是台北時間今天或前兩天（今天 {today}）']
    return day.strftime('%Y/%m/%d'), []


def golden_problems(path, old, new):
    """既有答案檔只准加：原有的 must／must_not／required／forbidden 每一項都要原封保留。"""
    try:
        new_obj = json.loads(new)
    except Exception as e:
        return [f'{path} 不是合法 JSON（{e}）']
    if old is None:
        return []
    old_obj = json.loads(old)
    out = []
    for key in ('must', 'required_categories', 'forbidden_stock_categories', 'note_must_not', 'reviewed_notes'):
        for code, value in (old_obj.get(key) or {}).items():
            if (new_obj.get(key) or {}).get(code) != value:
                out.append(f'{path}：既有答案 {key}.{code} 被改動或刪除')
    for key in ('must_not', 'forbidden_categories', 'single'):
        missing = set(old_obj.get(key) or []) - set(new_obj.get(key) or [])
        if missing:
            out.append(f'{path}：既有答案 {key} 少了 {sorted(missing)}')
    for key in ('chapter_required', 'chapter_must_not'):
        if [x for x in old_obj.get(key) or [] if x not in (new_obj.get(key) or [])]:
            out.append(f'{path}：既有章節答案 {key} 被改動或刪除')
    for key, n in (old_obj.get('chapter_min') or {}).items():
        if int((new_obj.get('chapter_min') or {}).get(key, 0)) < int(n):
            out.append(f'{path}：chapter_min.{key} 被調低')
    if old_obj.get('raw_sha256') and new_obj.get('raw_sha256') != old_obj.get('raw_sha256'):
        out.append(f'{path}：raw_sha256 被改動')
    return out


def contracts_problems(old, new):
    """run_offline_contracts.py 只准在 SUITES 加名稱。"""
    def suites(src):
        tree = ast.parse(src)
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'SUITES')
        return ast.literal_eval(node.value), src.replace(ast.get_source_segment(src, node), 'SUITES = []')
    try:
        old_s, old_rest = suites(old)
        new_s, new_rest = suites(new)
    except Exception as e:
        return [f'run_offline_contracts.py 讀不到 SUITES（{e}）']
    out = []
    if old_rest != new_rest:
        out.append('run_offline_contracts.py 除了 SUITES 以外的內容被改動')
    if [s for s in old_s if s not in new_s]:
        out.append('run_offline_contracts.py 刪掉了既有測試：' + '、'.join(s for s in old_s if s not in new_s))
    return out


def mirrored_values(source, label):
    """把一份 pipeline.py 載入（離線佔位值），取 GAS 有對應片段的常數。"""
    import importlib.util
    import os
    tmp = ROOT / 'pipeline' / f'_weekly_check_{label}.py'
    tmp.write_text(source, encoding='utf-8')
    os.environ.setdefault('GEMINI_API_KEY', 'offline')
    os.environ.setdefault('SPREADSHEET_ID', 'offline')
    sys.path.insert(0, str(ROOT / 'pipeline'))
    try:
        spec = importlib.util.spec_from_file_location(f'_weekly_check_{label}', tmp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return {k: getattr(mod, k, None) for k in MIRRORED}
    finally:
        tmp.unlink(missing_ok=True)


def plan_problems(plan, week_days, pipeline_changed):
    out = []
    replay = [str(d) for d in plan.get('replay_days') or []]
    holdout = [str(d) for d in plan.get('holdout_days') or []]
    if any(d not in week_days for d in replay):
        out.append('replay_days 只能是本週日期：' + '、'.join(d for d in replay if d not in week_days))
    if any(d in week_days for d in holdout):
        out.append('holdout_days 不能是本週日期')
    for d in replay + holdout:
        if not (ROOT / 'scripts/golden' / (d.replace('/', '-') + '.json')).exists():
            out.append(f'{d} 沒有答案檔 scripts/golden/{d.replace("/", "-")}.json，不能重播評分')
    if len(set(replay + holdout)) > MAX_DAYS:
        out.append(f'重播日合計 {len(set(replay + holdout))} 天，超過 {MAX_DAYS} 天（模型用量上限）')
    if pipeline_changed:
        if not replay:
            out.append('改了 pipeline/pipeline.py 但沒有本週重播日')
        if len(set(holdout)) < 2:
            out.append('改了 pipeline/pipeline.py 但保留日少於兩天')
        if not plan.get('changes'):
            out.append('改了 pipeline/pipeline.py 但計畫沒有寫 changes（成因、規則、案例、依據的裁示）')
    for ch in plan.get('changes') or []:
        for key in ('cause', 'rule', 'cases', 'ruling'):
            if not ch.get(key):
                out.append(f'changes 的「{ch.get("rule", "?")}」缺 {key}')
    for r in plan.get('repairs') or []:
        if str(r.get('date')) not in week_days:
            out.append(f'repairs 只能是本週日期：{r.get("date")}')
    return out


def check(base, branch, today):
    friday_day, problems = stamp_problems(branch, today)
    if not friday_day:
        return {'ok': False, 'problems': problems}
    friday, week_days = week_of(friday_day)
    plan_path = f'docs/weekly-review/{friday.replace("/", "-")}/plan.json'
    changed = [l.split('\t') for l in git('diff', '--name-status', '--no-renames', f'{base}...HEAD').splitlines() if l]
    plan_src = show('HEAD', plan_path)
    if plan_src is None:
        return {'ok': False, 'problems': [f'缺計畫檔 {plan_path}'], 'week': friday}
    plan = json.loads(plan_src)
    week_dash = {d.replace('/', '-') for d in week_days}
    files, pipeline_changed = [], False
    for status, path in changed:
        files.append({'status': status, 'path': path})
        if path == 'pipeline/pipeline.py' and status == 'M':
            pipeline_changed = True
        elif path.startswith(f'docs/weekly-review/{friday.replace("/", "-")}/'):
            pass
        elif re.match(r'^scripts/test_[\w]+\.py$', path):
            if status != 'A':
                problems.append(f'既有測試不准改：{path}')
        elif path == 'scripts/run_offline_contracts.py' and status == 'M':
            problems += contracts_problems(show(base, path), show('HEAD', path))
        elif GOLDEN.match(path):
            if GOLDEN.match(path).group(1) not in week_dash:
                problems.append(f'只能新增或補充本週的答案檔：{path}')
            elif status == 'D':
                problems.append(f'答案檔不准刪：{path}')
            else:
                problems += golden_problems(path, show(base, path), show('HEAD', path))
        elif REPAIR.match(path):
            if REPAIR.match(path).group(1) not in week_dash:
                problems.append(f'只能新增本週的修正計畫：{path}')
            elif status != 'A':
                problems.append(f'修正計畫只准新增，不改既有：{path}')
        else:
            problems.append(f'週檢討不准改這個檔：{path}（{status}）')
    gas = False
    if pipeline_changed:
        try:
            old_v, new_v = mirrored_values(show(base, 'pipeline/pipeline.py'), 'base'), mirrored_values(show('HEAD', 'pipeline/pipeline.py'), 'head')
            moved = [k for k in MIRRORED if old_v[k] != new_v[k]]
        except Exception as e:
            moved = [f'無法載入比對（{type(e).__name__}: {e}）']
        if moved:
            gas = True
            problems.append('Apps Script 有對應片段的常數變了，要同步並部署 GAS，留給管理者：' + '、'.join(moved))
    problems += plan_problems(plan, week_days, pipeline_changed)
    listed = {str(r.get('file')) for r in plan.get('repairs') or []}
    for f in files:
        if REPAIR.match(f['path']) and f['path'] not in listed:
            problems.append(f'修正計畫 {f["path"]} 沒有列在 plan.json 的 repairs')
    return {'ok': not problems, 'problems': problems, 'week': friday, 'week_days': week_days, 'plan_path': plan_path,
            'pipeline_changed': pipeline_changed, 'needs_gas': gas, 'files': files,
            'replay_days': plan.get('replay_days') or [], 'holdout_days': plan.get('holdout_days') or [],
            'repairs': plan.get('repairs') or []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='origin/main')
    ap.add_argument('--branch', required=True)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    result = check(a.base, a.branch, datetime.now(TAIPEI).date())
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    for p in result['problems']:
        print('✗', p)
    print('守門通過' if result['ok'] else f'守門不通過（{len(result["problems"])} 項），不合併')
    sys.exit(0 if result['ok'] else 1)


if __name__ == '__main__':
    main()
