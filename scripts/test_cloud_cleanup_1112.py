"""2026/11/12：雲端產物保留 7 天的純函式（不連 GitHub）。"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloud_cleanup import branch_day, old_artifacts, old_branches, patch_name  # noqa: E402

names = ['main', 'daily-review/apply-20261103', 'claude/daily-review-apply-20261111', 'daily-review/apply-20261105',
         'claude/line-ruling-20261104-224207', 'claude/rulings-20261104', 'claude/pipeline-weekly-20261030',
         'claude/1021-verdict-v21', 'codex/x', 'daily-review/apply-2026110', 'daily-review/apply-20261399']
assert branch_day('claude/pipeline-weekly-20261030') is None and branch_day('claude/1021-verdict-v21') is None
assert branch_day('daily-review/apply-20261399') is None and branch_day('main') is None
# 今天 11/12、保留 7 天 → 11/05 之前（不含 11/05）才清；週檢討與開發分支不動
assert old_branches(names, date(2026, 11, 12)) == [
    'daily-review/apply-20261103', 'claude/line-ruling-20261104-224207', 'claude/rulings-20261104'], old_branches(names, date(2026, 11, 12))
now = datetime(2026, 11, 12, 6, 0, tzinfo=timezone.utc)
arts = [{'id': 1, 'created_at': '2026-11-01T00:00:00Z', 'expired': False},
        {'id': 2, 'created_at': '2026-11-10T00:00:00Z', 'expired': False},
        {'id': 3, 'created_at': '2026-10-01T00:00:00Z', 'expired': True}]
assert old_artifacts(arts, now) == [1]
assert patch_name('claude/daily-review-apply-20261111') == 'claude_daily-review-apply-20261111.patch'
print('11/12 雲端清理：只清超過 7 天的每日分支與產出檔、週檢討與開發分支不動通過')
