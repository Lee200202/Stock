"""先保存原始封存，再組裝檢查及發布；檢查失敗不得隱藏成成功。"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def run(command, cwd=None):
    return subprocess.run(command, cwd=cwd, check=True)


def git(data, *args, capture=False):
    result = subprocess.run(['git', '-C', str(data), *args], check=True,
                            capture_output=capture, text=True)
    return result.stdout.strip() if capture else ''


def commit(data, directory, message):
    git(data, 'add', '-A', directory)
    result = subprocess.run(['git', '-C', str(data), 'diff', '--cached', '--quiet'])
    if result.returncode == 1:
        git(data, 'commit', '-q', '-m', message)
    elif result.returncode != 0:
        raise RuntimeError('無法檢查暫存變更')


def pull(data):
    try:
        git(data, 'pull', '-q', '--rebase', '-X', 'theirs', 'origin', 'market-history')
    except subprocess.CalledProcessError:
        subprocess.run(['git', '-C', str(data), 'rebase', '--abort'], check=False)
        raise


def save_archive(data, label):
    commit(data, 'archive', 'market history archive ' + label)
    for attempt in range(3):
        try:
            pull(data)
            git(data, 'push', '-q', 'origin', 'HEAD:market-history')
            return
        except subprocess.CalledProcessError:
            if attempt == 2:
                raise
            time.sleep(5)


def build_history(data, codes_file=None):
    # 在暫存輸出驗證，不讓失敗的組裝覆蓋原本可用的history。
    with tempfile.TemporaryDirectory(prefix='market-history-build-') as folder:
        target = Path(folder) / 'history'
        target.mkdir(parents=True, exist_ok=True)
        if (data / 'history').exists():
            shutil.copytree(data / 'history', target, dirs_exist_ok=True)
        selection = ['--codes-file', str(codes_file)] if codes_file else ['--sheets']
        run([sys.executable, '-u', '-X', 'utf8', 'scripts/market_history.py', 'build', '--archive', str(data / 'archive'), '--out', str(target), *selection])
        run([sys.executable, '-u', '-X', 'utf8', 'scripts/market_history.py', 'check', '--out', str(target), '--archive', str(data / 'archive')])
        shutil.copytree(target, data / 'history', dirs_exist_ok=True)


def publish(data, label, report, codes_file=None):
    state = {'status': 'saving_archive', 'archiveSaved': False, 'pagesDispatched': False}
    def record():
        Path(report).parent.mkdir(parents=True, exist_ok=True)
        Path(report).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    record()
    before = git(data, 'rev-parse', 'HEAD', capture=True)
    try:
        save_archive(data, label)
        state.update(archiveSaved=True, status='building', dataSha=git(data, 'rev-parse', 'HEAD', capture=True))
        record()
        for attempt in range(3):
            try:
                pull(data)
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                time.sleep(5)
                continue
            # 品質失敗直接回報；只有Git競爭才重試，不重算同一份錯誤三次。
            build_history(data, codes_file)
            commit(data, 'history', 'market history verified build ' + label)
            try:
                git(data, 'push', '-q', 'origin', 'HEAD:market-history')
                break
            except subprocess.CalledProcessError:
                if attempt == 2:
                    raise
                time.sleep(5)
        state.update(status='data_saved', dataSha=git(data, 'rev-parse', 'HEAD', capture=True))
        record()
        result = subprocess.run(['git', '-C', str(data), 'diff', '--quiet', before, 'HEAD', '--', 'history'])
        if result.returncode == 1:
            run(['gh', 'workflow', 'run', 'public-site.yml', '--ref', 'main'])
            state.update(pagesDispatched=True, status='awaiting_pages')
        elif result.returncode != 0:
            raise RuntimeError('無法確認發布差異')
        else:
            state['status'] = 'unchanged'
        record()
        return state
    except Exception as exc:
        state.update(status='failed', error=f'保存／發布失敗：{type(exc).__name__}；原封存保存狀態見archiveSaved，不宣稱網站已更新')
        record()
        print('::error title=歷史資料尚未發布::' + state['error'], flush=True)
        raise


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--report', required=True)
    p.add_argument('--codes-file')
    args = p.parse_args()
    data = Path(args.data).resolve()
    git(data, 'config', 'user.name', 'github-actions[bot]')
    git(data, 'config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    publish(data, args.label, args.report, args.codes_file)


if __name__ == '__main__':
    main()
