"""工作結束時保存相對起點新增／更新的原封存，供取消或推送故障後續補。"""
import argparse
import subprocess
import tarfile
from pathlib import Path


def bundle(data, logs):
    data, logs = Path(data).resolve(), Path(logs).resolve()
    logs.mkdir(parents=True, exist_ok=True)
    start = (logs / 'start-data-sha.txt').read_text(encoding='utf-8-sig').strip()
    changed = subprocess.check_output(['git', '-C', str(data), 'diff', '--name-only', '-z', start, '--', 'archive']).split(b'\0')
    new = subprocess.check_output(['git', '-C', str(data), 'ls-files', '--others', '--exclude-standard', '-z', 'archive']).split(b'\0')
    names = sorted({n.decode('utf-8') for n in changed + new if n})
    kept = []
    with tarfile.open(logs / 'changed-source-archive.tar.gz', 'w:gz') as out:
        for name in names:
            path = data / name
            if not name.startswith('archive/') or not path.is_file() or not path.resolve().is_relative_to(data):
                continue
            out.add(path, arcname=name, recursive=False)
            kept.append(name)
    (logs / 'changed-source-files.txt').write_text('\n'.join(kept), encoding='utf-8')
    print(f'本輪新增／更新封存保留 {len(kept)} 個檔案；不是全歷史完成數。')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--logs', required=True)
    a = p.parse_args()
    bundle(a.data, a.logs)
