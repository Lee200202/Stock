"""pipeline-weekly.yml 的步驟：重播、套用本週修正計畫、整理結論。

    python scripts/weekly_review/workflow_steps.py replay  .weekly/gate.json .weekly/replays.json
    python scripts/weekly_review/workflow_steps.py repairs .weekly/gate.json .weekly/repairs.json
    python scripts/weekly_review/workflow_steps.py result  .weekly     # 讀各步驟結果，寫 .weekly/result.json

重播：本週重播日＋保留日，分支程式、真實模型、停用裁決快取（量新規則本身），答案檔只在推論後評分。唯讀。
修正：只走既有修正工具（先預覽再套用；引句與舊說明指紋不符就失敗，不寫入）。
"""
import json
import os
import re
import time
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TICK = re.compile(r'^\s*[✗✓]')


def load(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default


def save(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')


def run(cmd, cwd=None):
    print('$', ' '.join(cmd), flush=True)
    p = subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    sys.stdout.write(p.stdout[-20000:])
    sys.stderr.write(p.stderr[-5000:])
    return p.returncode, p.stdout


def run_replay(day, golden, report, cwd=None):
    code, out = run([sys.executable, 'scripts/replay_extract.py', '--date', day, '--fresh-verdicts',
                     '--golden', str(golden), '--report-json', str(report)], cwd=cwd)
    rep = load(report, {}) or {}
    m = re.search(r'實際模型HTTP (\d+) 次', str(rep.get('usage') or '')) or re.search(r'呼叫 Gemini (\d+) 次', str(rep.get('usage') or ''))
    misses = [l.strip() for l in out.splitlines() if TICK.match(l) and l.strip().startswith('✗')]
    return {'exit_code': code, 'misses': misses, 'errors': len(rep.get('errors') or []), 'requests': int(m.group(1)) if m else 0,
            'infra': is_infra_failure(code, out)}


INFRA_RE = re.compile(r'Gemini 連續重試失敗|HTTP (?:429|500|502|503|504)|RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED|額度')


def is_infra_failure(code, out):
    """純函式：這一輪是被 Google 服務暫時故障打斷（不是流程判讀的結果）。
    2026/11/13 週檢討：11/09 重播第二輪連續 503 中斷，被當成未通過，這週的流程修正因此沒合併。
    結束代碼 0／2／3 是重播自己的結論（通過／答案不符／通用錯誤），不算服務故障。"""
    return code not in (0, 2, 3) and bool(INFRA_RE.search(str(out or '')))


def run_replay_retry(day, golden, report, cwd=None, waits=(90, 180)):
    """服務故障就等一下再跑同一天（最多再跑兩次）；結果帶 attempts 與最後是否仍是服務故障。"""
    res, used = run_replay(day, golden, report, cwd=cwd), 0
    for wait in waits:
        if not res.get('infra'):
            break
        print(f'{day} 重播遇到 Google 服務暫時故障，{wait} 秒後重跑同一天', flush=True)
        time.sleep(wait)
        used += res['requests']
        res = run_replay(day, golden, report, cwd=cwd)
    res['requests'] += used
    return res


def miss_key(line):
    """不符的比對鍵：股票＋應為，不含「實際」（11/06：分支「實際 觀望注意」、main「實際 沒有收錄」是同一個漏收持股）。"""
    return re.split(r'[，,]\s*實際', str(line), maxsplit=1)[0].strip()


def judge_day(branch, base, again=None):
    """純函式：這一天算不算通過，回傳 (通過, 擋下合併的不符)。

    模型每輪判讀不同（2026/11/06 第一次週檢討：10/29 分支兩輪漏的股票完全不同，main 又漏另外幾檔）。
    所以只有「分支兩輪都出現、main 沒有」的不符才算這次改動造成的退步；只有一輪出現的是單次變異，記錄但不擋。
    通用錯誤分支不得比 main 多。沒有第二輪或 main 對照時不放行。"""
    if branch['exit_code'] == 0:
        return True, []
    if again is not None and again['exit_code'] == 0:
        return True, []
    again_keys = {miss_key(m) for m in (again or {}).get('misses', [])}
    steady = [m for m in branch['misses'] if again is None or miss_key(m) in again_keys]
    if again is not None and not steady and again['exit_code'] != 3 and branch['exit_code'] != 3:
        return True, []
    if base is None:
        return False, steady or branch['misses']
    base_keys = {miss_key(m) for m in base['misses']}
    new = [m for m in steady if miss_key(m) not in base_keys]
    errors_ok = min(branch['errors'], (again or branch)['errors']) <= base['errors']
    if base['exit_code'] != 0 and not new and errors_ok:
        return True, []
    return False, new or steady


def main_checkout():
    """main 的程式放在 .weekly/main（只建一次），重播既有問題的對照組。"""
    path = ROOT / '.weekly' / 'main'
    if not path.exists():
        subprocess.run(['git', 'worktree', 'add', '--detach', str(path), 'origin/main'], cwd=ROOT, check=True)
    return path


def replay(gate_path, out_path):
    gate = load(gate_path, {})
    days = [('本週', d) for d in gate.get('replay_days') or []] + [('保留日', d) for d in gate.get('holdout_days') or []]
    results, requests = [], 0
    for role, day in days:
        stamp = day.replace('/', '')
        golden = ROOT / 'scripts/golden' / f'{day.replace("/", "-")}.json'
        branch = run_replay_retry(day, golden, ROOT / '.weekly' / f'replay-{stamp}.json')
        requests += branch['requests']
        base = again = None
        if branch['exit_code'] != 0 and gate.get('pipeline_changed'):
            print(f'{role} {day} 分支未通過，再跑分支一輪，量哪些不符是穩定的', flush=True)
            again = run_replay_retry(day, golden, ROOT / '.weekly' / f'replay-{stamp}-2.json')
            requests += again['requests']
            # 2026/11/14：兩輪都有的不符，或分支有通用錯誤（以前通用錯誤不跑 main 對照，main 也有的錯誤照樣擋下合併）
            steady_miss = {miss_key(m) for m in branch['misses']} & {miss_key(m) for m in again['misses']}
            if again['exit_code'] != 0 and (steady_miss or branch['errors'] or again['errors']) and not branch.get('infra'):
                print(f'{role} {day} 分支兩輪都未通過，改用 main 的程式重播同一天、同一份答案做對照', flush=True)
                base = run_replay_retry(day, golden, ROOT / '.weekly' / f'replay-{stamp}-main.json', cwd=main_checkout())
                requests += base['requests']
        ok, new = judge_day(branch, base, again)
        infra = bool(branch.get('infra') and (again is None or again.get('infra')))
        results.append({'role': role, 'date': day, 'exit_code': branch['exit_code'], 'ok': ok, 'infra': infra and not ok,
                        'misses': branch['misses'][:20],
                        'new_misses': new[:20], 'baseline_misses': (base or {}).get('misses', [])[:20],
                        'baseline_exit_code': (base or {}).get('exit_code'), 'errors': branch['errors'],
                        'second_misses': (again or {}).get('misses', [])[:20],
                        'requests': branch['requests'] + (base or {}).get('requests', 0) + (again or {}).get('requests', 0)})
        verdict = ('通過' if branch['exit_code'] == 0 else '不符只是單次變異或 main 也有，不算本次退步' if ok
                   else 'Google 服務暫時故障，重跑後仍無法完成（不是流程退步）' if infra else '未通過（分支兩輪都有、main 沒有的不符）')
        print(f"{role} {day}：{verdict}；不符 {len(branch['misses'])} 項，新增 {len(new)} 項", flush=True)
    save(out_path, {'results': results, 'requests': requests, 'ran': bool(days)})
    sys.exit(0 if all(r['ok'] for r in results) else 1)


REPAIR_TOOLS = {
    'notice-reason': lambda d: [['scripts/apply_notice_reason_fix.py', '--date', d], ['scripts/apply_notice_reason_fix.py', '--date', d, '--apply']],
    'source-review': lambda d: [['scripts/apply_source_review.py', '--date', d], ['scripts/apply_source_review.py', '--date', d, '--apply']],
    'points-review': lambda d: [['scripts/apply_reviewed_points.py', '--date', d]],
}


def repairs(gate_path, out_path):
    gate = load(gate_path, {})
    done, ok = [], True
    for r in gate.get('repairs') or []:
        m = re.match(r'^scripts/repairs/(\d{4}-\d{2}-\d{2})-(source-review|notice-reason|points-review)\.json$', str(r.get('file')))
        if not m:
            done.append({'date': r.get('date'), 'file': r.get('file'), 'ok': False, 'note': '檔名格式不對'})
            ok = False
            continue
        day = m.group(1).replace('-', '/')
        codes = []
        for cmd in REPAIR_TOOLS[m.group(2)](day):
            code, _ = run([sys.executable, *cmd])
            codes.append(code)
            if code:
                break
        good = all(c == 0 for c in codes)
        ok &= good
        done.append({'date': day, 'file': r.get('file'), 'ok': good, 'note': r.get('why', '')})
    save(out_path, {'repairs': done})
    sys.exit(0 if ok else 1)


def result(folder):
    folder = Path(folder)
    gate = load(folder / 'gate.json', {}) or {}
    replays = load(folder / 'replays.json', {}) or {}
    reps = load(folder / 'repairs.json', {}) or {}
    verify = load(folder / 'verify.json', {}) or {}
    env = os.environ
    outcome = {k: env.get(k, '') for k in ('GATE', 'OFFLINE', 'REPLAY', 'REPAIRS', 'MERGE')}
    rows = []
    rows.append({'item': '守門', 'result': '通過' if outcome['GATE'] == 'success' else '不通過',
                 'note': '；'.join(gate.get('problems') or []) or ('改了流程' if gate.get('pipeline_changed') else '沒有改流程程式')})
    rows.append({'item': '離線契約', 'result': {'success': '通過', 'failure': '不通過'}.get(outcome['OFFLINE'], '未執行')})
    for r in replays.get('results') or []:
        same = r['ok'] and r['exit_code'] != 0
        rows.append({'date': r['date'], 'item': f"重播（{r['role']}）",
                     'result': ('通過（不符只是單次變異或 main 也有）' if same else '通過') if r['ok'] else '未通過',
                     'note': f"結束代碼 {r['exit_code']}；通用錯誤 {r['errors']}；模型請求 {r['requests']}；"
                             + ('不符：' + '；'.join(r['misses']) if r['misses'] else '')
                             + ('；分支第二輪不符：' + ('；'.join(r.get('second_misses') or []) or '無') if r.get('second_misses') is not None and r['exit_code'] != 0 else '')
                             + (f"；main 對照結束代碼 {r.get('baseline_exit_code')}，main 也不符：" + '；'.join(r.get('baseline_misses') or [])
                                if r.get('baseline_exit_code') is not None else '')
                             + ('；本次新增的不符：' + '；'.join(r.get('new_misses') or []) if not r['ok'] else '')})
    for r in reps.get('repairs') or []:
        rows.append({'date': r.get('date', ''), 'item': '修正計畫', 'result': '已套用' if r['ok'] else '套用失敗',
                     'note': f"{r.get('file')}　{r.get('note', '')}"})
    for c in verify.get('checks') or []:
        rows.append({'date': c['date'], 'code': c['code'], 'name': c['name'], 'item': f"線上驗收：{c['type']}",
                     'result': c['result'], 'note': c['why']})
    merged = outcome['MERGE'] == 'success'
    changed_code = bool(gate.get('pipeline_changed'))
    failed = [r for r in rows if r['result'] in ('不通過', '未通過', '套用失敗')]
    if merged:
        verdict = '已合併'
    elif outcome['GATE'] != 'success':
        verdict = '守門不通過，未合併'
    elif outcome['OFFLINE'] != 'success':
        verdict = '離線契約不通過，未合併'
    elif outcome['REPLAY'] not in ('success', 'skipped', '') and replays.get('results') and all(
            r['ok'] or r.get('infra') for r in replays['results']):
        verdict = '重播遇到 Google 服務暫時故障，未合併'
    elif outcome['REPLAY'] not in ('success', 'skipped', ''):
        verdict = '重播未通過，未合併'
    else:
        verdict = '未合併（合併步驟失敗）'
    still = [c for c in verify.get('checks') or [] if c['result'] != '通過']
    note = (f"{env.get('BRANCH', '')}；流程程式{'有' if changed_code else '沒有'}改動；"
            f"重播 {len(replays.get('results') or [])} 天、模型請求約 {replays.get('requests', 0)}；"
            f"線上驗收明確錯誤 {len(verify.get('checks') or [])} 項，未通過或需人工 {len(still)} 項"
            + (f"；合併 {env.get('MERGED_SHA', '')[:9]}" if merged else ''))
    rows.append({'item': '結論', 'result': verdict, 'note': note})
    save(folder / 'result.json', {'rows': rows, 'checks': verify.get('checks') or [], 'verdict': verdict, 'merged': merged})
    print(verdict, '｜', note)
    for r in failed:
        print('✗', r.get('date', ''), r['item'], r['result'], r.get('note', ''))
    return verdict


if __name__ == '__main__':
    cmd = sys.argv[1]
    if cmd == 'replay':
        replay(sys.argv[2], sys.argv[3])
    elif cmd == 'repairs':
        repairs(sys.argv[2], sys.argv[3])
    elif cmd == 'result':
        result(sys.argv[2])
    else:
        raise SystemExit('replay｜repairs｜result')
