#!/usr/bin/env python3
"""雲端每日產物保留 7 天、自動清理（2026/11/12 管理者：「每天推上去的東西越積越多，保持版面整潔」→ 選「保留 7 天、自動清理」）。

    python scripts/cloud_cleanup.py [--dry-run] [--keep-days 7]

清三種東西，每一種都先留紀錄再刪：
1. GitHub 每日分支（雲端複核修正 daily-review/apply-YYYYMMDD、claude/daily-review-apply-YYYYMMDD；
   LINE 裁示 claude/line-ruling-YYYYMMDD-HHMMSS；指示 claude/rulings-YYYYMMDD）超過 7 天：
   已併進 main 的直接刪；沒併的先把整份差異存成 docs/雲端排程/封存/<分支>.patch、
   scripts/repairs/ 裡 main 還沒有的修正計畫複製進 main，提交後才刪分支。
   週檢討（claude/pipeline-weekly-*）與開發分支不動——要不要合併是管理者的事。
2. GitHub Actions 產出檔（artifacts）超過 7 天的刪掉。
3. Google 雲端硬碟的獨立備份試算表：請 Apps Script 清掉 7 天前的備份分頁（每個分頁永遠留最新一份，
   目錄列標「已清理」留紀錄）。Apps Script 還沒部署這個功能時只回報，不算失敗。

--dry-run：全部只列出來，不刪、不提交。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = timezone(timedelta(hours=8))
ARCHIVE_DIR = Path('docs') / '雲端排程' / '封存'
DAILY = [re.compile(r'^(?:daily-review/apply-|claude/daily-review-apply-)(\d{8})$'),
         re.compile(r'^claude/line-ruling-(\d{8})-\d{6}$'),
         re.compile(r'^claude/rulings-(\d{8})$')]


def branch_day(name):
    """純函式：每日分支名稱 → 日期；不是每日分支回 None（週檢討、開發分支、main 都是 None）。"""
    for rx in DAILY:
        m = rx.match(name)
        if m:
            try:
                return datetime.strptime(m.group(1), '%Y%m%d').date()
            except ValueError:
                return None
    return None


def old_branches(names, today: date, keep_days=7):
    """純函式：超過保留天數的每日分支（舊的在前）。今天往前 keep_days 天內的都留著。"""
    cutoff = today - timedelta(days=keep_days)
    picked = [(branch_day(n), n) for n in names]
    return [n for d, n in sorted(p for p in picked if p[0] and p[0] < cutoff)]


def old_artifacts(items, now: datetime, keep_days=7):
    """純函式：建立超過保留天數、還沒過期的產出檔 id。"""
    cutoff = now - timedelta(days=keep_days)
    out = []
    for a in items:
        made = datetime.fromisoformat(str(a.get('created_at', '')).replace('Z', '+00:00'))
        if not a.get('expired') and made < cutoff:
            out.append(a['id'])
    return out


def patch_name(branch):
    return re.sub(r'[^0-9A-Za-z._-]+', '_', branch) + '.patch'


def git(*args, check=True):
    return subprocess.run(['git', *args], cwd=ROOT, check=check, capture_output=True, text=True, encoding='utf-8').stdout


class GitHub:
    def __init__(self):
        import requests
        self.s = requests.Session()
        self.s.headers.update({'Authorization': 'Bearer ' + os.environ['GITHUB_TOKEN'],
                               'Accept': 'application/vnd.github+json'})
        self.base = 'https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY']

    def pages(self, path, key=None):
        out, page = [], 1
        while True:
            r = self.s.get(f'{self.base}{path}', params={'per_page': 100, 'page': page}, timeout=60)
            r.raise_for_status()
            data = r.json()
            rows = data[key] if key else data
            out.extend(rows)
            if len(rows) < 100:
                return out
            page += 1

    def delete(self, path):
        r = self.s.delete(f'{self.base}{path}', timeout=60)
        if r.status_code not in (204, 404):
            r.raise_for_status()


def clean_branches(gh, today, keep, dry):
    names = [b['name'] for b in gh.pages('/branches')]
    targets = old_branches(names, today, keep)
    print(f'每日分支：共 {len(names)} 個分支，超過 {keep} 天的每日分支 {len(targets)} 個')
    if not targets:
        return []
    git('fetch', '-q', 'origin', 'main', *[f'+refs/heads/{b}:refs/remotes/origin/{b}' for b in targets])
    archived, merged = [], []
    for b in targets:
        if subprocess.run(['git', 'merge-base', '--is-ancestor', f'origin/{b}', 'origin/main'], cwd=ROOT).returncode == 0:
            merged.append(b)
            print(f'  {b}：已併進 main，直接刪')
            continue
        diff = git('diff', '--binary', f'origin/main...origin/{b}')
        files = [f for f in git('diff', '--name-only', f'origin/main...origin/{b}').splitlines() if f]
        print(f'  {b}：未併，封存差異（{len(files)} 個檔：{"、".join(files[:4])}{"…" if len(files) > 4 else ""}）')
        if dry:
            archived.append(b)
            continue
        out = ROOT / ARCHIVE_DIR / patch_name(b)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(diff, encoding='utf-8')
        for f in files:
            if f.startswith('scripts/repairs/') and not (ROOT / f).exists():
                content = subprocess.run(['git', 'show', f'origin/{b}:{f}'], cwd=ROOT, capture_output=True, check=False).stdout
                if content:
                    (ROOT / f).write_bytes(content)
                    print(f'    修正計畫存進 main：{f}')
        archived.append(b)
    if archived and not dry:
        git('add', str(ARCHIVE_DIR), 'scripts/repairs')
        if git('diff', '--cached', '--name-only').strip():
            git('-c', 'user.name=github-actions[bot]', '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                'commit', '-q', '-m', f'chore(cleanup): archive {len(archived)} daily cloud branches older than {keep} days')
            git('pull', '-q', '--rebase', 'origin', 'main')        # 清理期間 main 有新提交就接在後面
            git('push', '-q', 'origin', 'HEAD:main')
            print(f'  封存提交已推到 main（{ARCHIVE_DIR}）')
    gone = merged + archived
    if not dry:
        for b in gone:
            gh.delete(f'/git/refs/heads/{b}')
        print(f'  已刪除 {len(gone)} 個分支')
    return gone


def clean_artifacts(gh, keep, dry):
    items = gh.pages('/actions/artifacts', key='artifacts')
    ids = old_artifacts(items, datetime.now(timezone.utc), keep)
    size = sum(a['size_in_bytes'] for a in items if a['id'] in set(ids))
    print(f'產出檔：共 {len(items)} 個，超過 {keep} 天的 {len(ids)} 個（{size / 1e6:.1f} MB）')
    if not dry:
        for i in ids:
            gh.delete(f'/actions/artifacts/{i}')
        print(f'  已刪除 {len(ids)} 個')
    return ids


def clean_backups(dry):
    import requests
    url, key, sid = (os.environ.get(k, '') for k in ('APPS_SCRIPT_URL', 'ADMIN_KEY', 'SPREADSHEET_ID'))
    if not (url and key):
        print('雲端硬碟備份：缺 APPS_SCRIPT_URL／ADMIN_KEY，略過')
        return None
    op = 'prune-preview' if dry else 'prune'
    try:
        r = requests.get(url, params={'action': 'backup-archive', 'key': key, 'source': sid, 'op': op}, timeout=280)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        print(f'雲端硬碟備份：Apps Script 沒有回應（{type(exc).__name__}），下次再清')
        return None
    if not data.get('ok'):
        err = str(data.get('error') or '')
        if '不支援' in err:
            print('雲端硬碟備份：正式 Apps Script 還沒有清理功能（等管理者部署新版本），這次只略過')
        else:
            print('雲端硬碟備份：' + err[:200])
        return None
    pct = 100 * data.get('cells', 0) / max(1, data.get('cellLimit', 10000000))
    print(f"雲端硬碟備份：{data.get('archived')} 個備份分頁、{data.get('cells', 0):,} 格（上限的 {pct:.1f}%），"
          f"最舊 {data.get('oldest') or '—'}；超過 7 天可清 {data.get('prunable')} 個（{data.get('prunableCells', 0):,} 格）")
    for d in (data.get('plan') or [])[:12]:
        print(f"  {d.get('at')} {d.get('name')}")
    if not dry:
        print(f"  已清理 {data.get('pruned')} 個" + (f"，還剩 {data.get('left')} 個下次再清" if data.get('left') else ''))
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--keep-days', type=int, default=7)
    a = ap.parse_args()
    keep = max(7, a.keep_days)
    today = datetime.now(TAIPEI).date()
    print(f'{today} 清理（保留 {keep} 天）' + ('　試跑：只列出，不刪' if a.dry_run else ''))
    gh = GitHub()
    clean_branches(gh, today, keep, a.dry_run)
    clean_artifacts(gh, keep, a.dry_run)
    clean_backups(a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
