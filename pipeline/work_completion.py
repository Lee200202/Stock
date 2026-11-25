"""Small, non-secret daily receipts; a green workflow alone is never completion."""
import argparse
from datetime import datetime
import json
import os
import re
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

KINDS = ('transcript', 'daily', 'screen')


def day_key(value=''):
    return (datetime.strptime(value.replace('-', '/'), '%Y/%m/%d').strftime('%Y-%m-%d')
            if value else datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d'))


def receipt_path(kind):
    if kind not in KINDS:
        raise ValueError('Unknown work kind')
    return Path(os.environ.get('WORK_COMPLETION_DIR', '.work-completion')) / (kind + '.json')


def record_completion(kind, day, source='', status='complete'):
    # Only the actual production step opts in, not imports or offline tests.
    if os.environ.get('WORK_COMPLETION_KIND') != kind:
        return
    if status not in ('complete', 'no_video') or (status == 'complete' and not source):
        raise ValueError('Completion needs a verified source or confirmed no-video state')
    if source and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', source):
        raise ValueError('Invalid source identifier')
    p = receipt_path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps({'version': 1, 'kind': kind, 'day': day_key(day),
                               'source': source, 'status': status}), encoding='utf-8')
    tmp.replace(p)


def read_completion(kind, day, source=''):
    try:
        data = json.loads(receipt_path(kind).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('source'), str):
        return None
    return data if (data.get('version') == 1 and data.get('kind') == kind
                    and data.get('day') == day_key(day)
                    and (not source or data.get('source') == source)
                    and data.get('status') in ('complete', 'no_video')
                    and (data.get('status') == 'no_video' or data.get('source'))) else None


def output(**values):
    lines = ''.join(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n'
                    for key, value in values.items())
    target = os.environ.get('GITHUB_OUTPUT')
    if target:
        with open(target, 'a', encoding='utf-8') as f:
            f.write(lines)
    else:
        print(lines, end='')


def dispatch_screen(kind, day):
    done = read_completion(kind, day)
    if not done or done['status'] != 'complete' or read_completion('screen', day, done['source']):
        print('No screen work to dispatch')
        return
    # The screen workflow rechecks its own receipt under its concurrency lock.
    repo = os.environ['GITHUB_REPOSITORY']
    result = subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/screen-identify.yml/runs?per_page=20'],
                            check=True, capture_output=True)
    if any(r['status'] != 'completed' for r in json.loads(result.stdout)['workflow_runs']):
        print('Screen work already queued or running')
        return
    body = {'ref': 'main', 'inputs': {'date': day_key(day).replace('-', '/'),
                                    'source': done['source'], 'automatic': 'true'}}
    subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/screen-identify.yml/dispatches',
                    '--method', 'POST', '--input', '-'], input=json.dumps(body).encode(), check=True)
    print('Dispatched screen identification for ready source')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('date', 'check', 'dispatch-screen'))
    p.add_argument('kind', nargs='?', choices=KINDS)
    p.add_argument('--date', default='')
    p.add_argument('--source', default='')
    a = p.parse_args()
    day = day_key(a.date)
    if a.mode == 'date':
        output(day=day)
    elif a.mode == 'check':
        done = read_completion(a.kind, day, a.source)
        output(complete=bool(done), source=(done or {}).get('source', ''))
        print('Already complete; initialization and model steps can be skipped' if done
              else 'No valid completion receipt; check the authoritative source')
    else:
        dispatch_screen(a.kind, day)


if __name__ == '__main__':
    main()
