"""離線契約測試的固定入口：逐支執行、逐支回報，不呼叫模型、不連網、不寫試算表。

    python scripts/run_offline_contracts.py              # 全部
    python scripts/run_offline_contracts.py --json out.json

每支測試在獨立的子行程從 repo 根目錄執行（有些測試用根目錄相對路徑）：
- GEMINI_API_KEY、SPREADSHEET_ID 換成離線佔位值，任何模型或試算表呼叫都會失敗而不是真的送出；
- socket 連線只放行本機，連外即拋錯；
- PYTHONUTF8=1（Windows 預設 cp950 會讀錯 UTF-8 檔）。
有任何一支失敗就以非零結束；不以總數代替逐支結果。
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]

# 流程正本（pipeline／單日重判／發布候選）的契約。市場歷史另有自己的 workflow，不在這裡。
SUITES = [
    'test_q12_review_cases',
    'test_model_stance_contract',
    'test_source_identity_context',
    'test_source_fact_ledger',
    'test_ph6_claim_projection',
    'test_ph9_evidence_contract',
    'test_price_context_contract',
    'test_query_source_evidence',
    'test_questionnaire_repair',
    'test_schema_compatibility',
    'test_mixed_current_view',
    'test_full_source_enrichment',
    'test_uncertain_verdict_contract',
    'test_model_release_candidate',
    'test_regenerator_preflight',
    'test_review_workflow_contract',
    'test_reviewed_note_context',
    'test_replay_reference',
    'test_source_review',
    'test_score_source_replays',
    'test_compare_skill_trials',
    'test_transcript_noon_cutoff',
]

GUARD = r'''
import socket, sys, unittest
sys.path.insert(0, 'scripts')   # 有些測試從同目錄匯入（from test_source_fact_ledger import p）
_connect = socket.socket.connect
def _local_only(self, address, *a, **k):
    host = address[0] if isinstance(address, tuple) else address
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise OSError('離線契約測試禁止連網：' + str(host))
    return _connect(self, address, *a, **k)
socket.socket.connect = _local_only
program = unittest.main(module=None, argv=['offline-contracts', sys.argv[1]], exit=False)
sys.exit(0 if program.result.wasSuccessful() else 1)
'''


def run(name):
    env = dict(os.environ, GEMINI_API_KEY='offline-placeholder', GEMINI_API_KEYS='', SPREADSHEET_ID='offline',
               PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for key in ('GOOGLE_CREDENTIALS', 'GOOGLE_APPLICATION_CREDENTIALS', 'GCP_SA_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    started = time.time()
    proc = subprocess.run([sys.executable, '-c', GUARD, f'scripts/{name}.py'], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=900)
    out = proc.stdout + proc.stderr
    ran = re.search(r'^Ran (\d+) tests?', out, re.M)
    failed = [line for line in out.splitlines() if re.match(r'^(FAIL|ERROR): ', line)]
    return {'suite': name, 'ok': proc.returncode == 0, 'tests': int(ran.group(1)) if ran else 0,
            'failed': failed, 'seconds': round(time.time() - started, 1),
            'tail': '' if proc.returncode == 0 else '\n'.join(out.strip().splitlines()[-12:])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default='')
    ap.add_argument('suites', nargs='*', help='只跑這幾支（預設全部）')
    a = ap.parse_args()
    names = a.suites or SUITES
    missing = [n for n in names if not (ROOT / 'scripts' / f'{n}.py').exists()]
    if missing:
        raise SystemExit('找不到測試：' + '、'.join(missing))
    results = []
    for name in names:
        r = run(name)
        results.append(r)
        print(f"{'OK  ' if r['ok'] else 'FAIL'} {name}（{r['tests']} 項，{r['seconds']} 秒）", flush=True)
        for line in r['failed']:
            print('     ' + line)
        if not r['ok'] and not r['failed']:
            print('     ' + r['tail'].replace('\n', '\n     '))
    bad = [r['suite'] for r in results if not r['ok']]
    print(f"\n{len(results) - len(bad)}／{len(results)} 支通過，共 {sum(r['tests'] for r in results)} 項"
          + (f"；未通過：{'、'.join(bad)}" if bad else ''))
    if a.json:
        Path(a.json).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
