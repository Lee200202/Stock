"""Small, non-secret daily receipts; a green workflow alone is never completion."""
import argparse
from datetime import datetime
import json
import os
import re
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

KINDS = ('transcript', 'daily', 'screen')
SCREEN_MAX_ATTEMPTS = 3


def screen_title(day, source, attempt):
    return f"直播畫面辨識 {day_key(day).replace('-', '/')} {source} 第{attempt}次"


def screen_runs():
    repo = os.environ['GITHUB_REPOSITORY']
    result = subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/screen-identify.yml/runs?per_page=100'],
                            check=True, capture_output=True)
    return json.loads(result.stdout)['workflow_runs']


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
    runs = screen_runs()
    if any(r['status'] != 'completed' for r in runs):
        print('Screen work already queued or running')
        return
    if any(r.get('display_title') == screen_title(day, done['source'], SCREEN_MAX_ATTEMPTS)
           and r.get('conclusion') == 'failure' for r in runs):
        print('Screen retry limit reached for this day and source; inspect the failure or retry manually')
        return
    body = {'ref': 'main', 'inputs': {'date': day_key(day).replace('-', '/'),
                                    'source': done['source'], 'automatic': 'true', 'attempt': '1'}}
    subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/screen-identify.yml/dispatches',
                    '--method', 'POST', '--input', '-'], input=json.dumps(body).encode(), check=True)
    print('Dispatched screen identification for ready source')


def retry_screen(day, source, attempt):
    if not source or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', source):
        raise ValueError('Retry needs the original ready source')
    if not 1 <= attempt <= SCREEN_MAX_ATTEMPTS:
        raise ValueError('Invalid retry attempt')
    if attempt == SCREEN_MAX_ATTEMPTS:
        print('Automatic retry limit reached; original failure remains visible')
        return
    if read_completion('screen', day, source):
        print('Already complete; do not retry')
        return
    delay = 60 * attempt
    print(f'Retry after {delay} seconds; next attempt {attempt + 1}/{SCREEN_MAX_ATTEMPTS}', flush=True)
    time.sleep(delay)
    current = str(os.environ.get('GITHUB_RUN_ID', ''))
    runs = screen_runs()
    if any(r['status'] != 'completed' and str(r.get('id')) != current for r in runs):
        print('Another screen job is queued or running; do not duplicate it')
        return
    repo = os.environ['GITHUB_REPOSITORY']
    body = {'ref': 'main', 'inputs': {'date': day_key(day).replace('-', '/'), 'source': source,
                                    'automatic': 'true', 'attempt': str(attempt + 1)}}
    subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/screen-identify.yml/dispatches',
                    '--method', 'POST', '--input', '-'], input=json.dumps(body).encode(), check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=('date', 'check', 'dispatch-screen', 'retry-screen'))
    p.add_argument('kind', nargs='?', choices=KINDS)
    p.add_argument('--date', default='')
    p.add_argument('--source', default='')
    p.add_argument('--attempt', type=int, default=1)
    a = p.parse_args()
    day = day_key(a.date)
    if a.mode == 'date':
        output(day=day)
    elif a.mode == 'check':
        done = read_completion(a.kind, day, a.source)
        output(complete=bool(done), source=(done or {}).get('source', ''))
        print('Already complete; initialization and model steps can be skipped' if done
              else 'No valid completion receipt; check the authoritative source')
    elif a.mode == 'dispatch-screen':
        dispatch_screen(a.kind, day)
    else:
        retry_screen(day, a.source, a.attempt)


if __name__ == '__main__':
    main()
