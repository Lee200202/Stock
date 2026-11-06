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


def run(cmd):
    print('$', ' '.join(cmd), flush=True)
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    sys.stdout.write(p.stdout[-20000:])
    sys.stderr.write(p.stderr[-5000:])
    return p.returncode, p.stdout


def replay(gate_path, out_path):
    gate = load(gate_path, {})
    days = [('本週', d) for d in gate.get('replay_days') or []] + [('保留日', d) for d in gate.get('holdout_days') or []]
    results, requests = [], 0
    for role, day in days:
        report = ROOT / '.weekly' / ('replay-' + day.replace('/', '') + '.json')
        code, out = run([sys.executable, 'scripts/replay_extract.py', '--date', day, '--fresh-verdicts',
                         '--golden', f'scripts/golden/{day.replace("/", "-")}.json', '--report-json', str(report)])
        rep = load(report, {}) or {}
        m = re.search(r'實際模型HTTP (\d+) 次', str(rep.get('usage') or '')) or re.search(r'呼叫 Gemini (\d+) 次', str(rep.get('usage') or ''))
        n = int(m.group(1)) if m else 0
        requests += n
        misses = [l.strip() for l in out.splitlines() if TICK.match(l) and l.strip().startswith('✗')]
        results.append({'role': role, 'date': day, 'exit_code': code, 'ok': code == 0, 'misses': misses[:20],
                        'errors': len(rep.get('errors') or []), 'requests': n})
        print(f"{role} {day}：{'通過' if code == 0 else '未通過（結束代碼 %d）' % code}；不符 {len(misses)} 項", flush=True)
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
        rows.append({'date': r['date'], 'item': f"重播（{r['role']}）", 'result': '通過' if r['ok'] else '未通過',
                     'note': f"結束代碼 {r['exit_code']}；通用錯誤 {r['errors']}；模型請求 {r['requests']}；" + '；'.join(r['misses'])})
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
