"""LINE 管理者裁示：狀態轉換、差異明細同步與 line-ruling-apply.yml 的守門（離線，不連試算表）。"""
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
from common import LOG_TABS  # noqa: E402
import rulings  # noqa: E402

HEAD = LOG_TABS['管理者裁示']


def row(i, state, branch='', day='2026/11/06', code='8299'):
    return {'裁示ID': f'R{i}', '日期': day, '代號': code, '狀態': state, '分支': branch, '管理者說明': '說明'}


class Transitions(unittest.TestCase):
    def test_claim_only_pending(self):
        rows = [row(1, '待處理'), row(2, '處理中'), row(3, '待處理')]
        out = rulings.transition(HEAD, rows, ids=['R1', 'R2'], state='處理中', require=('待處理',), stamp='t')
        self.assertEqual([i for i, _ in out], [2])
        self.assertEqual(dict(zip(HEAD, out[0][1]))['狀態'], '處理中')

    def test_set_branch_and_finish_by_branch(self):
        rows = [row(1, '處理中'), row(2, '處理中')]
        out = rulings.transition(HEAD, rows, ids=['R1'], state='套用中', set_branch='claude/line-ruling-20261106-120000', stamp='t')
        rows[0] = dict(zip(HEAD, out[0][1]))
        done = rulings.transition(HEAD, rows, branch='claude/line-ruling-20261106-120000', state='已完成',
                                  require=('套用中', '處理中'), note='ok', stamp='t2')
        self.assertEqual([i for i, _ in done], [2])
        self.assertEqual(dict(zip(HEAD, done[0][1]))['結果'], 'ok')

    def test_nothing_selected_without_ids_or_branch(self):
        self.assertEqual(rulings.transition(HEAD, [row(1, '待處理')], state='已完成'), [])

    def test_unknown_state(self):
        with self.assertRaises(ValueError):
            rulings.transition(HEAD, [row(1, '待處理')], ids=['R1'], state='完成了')

    def test_detail_rows_follow_finished_rulings(self):
        dhead = LOG_TABS['差異明細']
        drows = [{'日期': '2026/11/06', '代號': '8299', '處理': '待管理者', '修正後': ''},
                 {'日期': '2026/11/06', '代號': '8299', '處理': '只記錄'},
                 {'日期': '2026/11/05', '代號': '8299', '處理': '待管理者'}]
        out = rulings.detail_updates(dhead, drows, [row(1, '已完成')], {'8299': '新說明'})
        self.assertEqual([i for i, _ in out], [2])
        r = dict(zip(dhead, out[0][1]))
        self.assertEqual((r['處理'], r['修正後']), ('已修正', '新說明'))


class SiteRows(unittest.TestCase):
    def test_rows_for_day_and_code(self):
        tables = {'操作紀錄': [['日期', '股票名稱', '代號', '方向', '理由摘錄', '來源影片ID'],
                              ['2026/11/06', '群聯', '8299', '觀望不碰', '舊說明', 'VID'],
                              ['2026/11/05', '群聯', '8299', '觀望注意', '別天', 'VID0']],
                  '會員持股': [['日期', '股票名稱', '代號', '說明重點', '來源影片ID'],
                              ['2026-11-06', '群聯', '8299', '持股說明', 'VID']]}
        rows = rulings.site_rows(tables, '2026/11/06', '8299')
        self.assertEqual([(r['sheet'], r['category']) for r in rows], [('操作紀錄', 'watch_avoid'), ('會員持股', 'holdings')])
        self.assertEqual(rows[0]['previous_note_sha256'], rulings.sha('舊說明'))


class Workflow(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load((ROOT / '.github/workflows/line-ruling-apply.yml').read_text(encoding='utf-8'))
        self.run = self.wf['jobs']['apply']['steps'][3]['run']

    def test_trigger_and_concurrency(self):
        on = self.wf.get('on') or self.wf.get(True)
        self.assertEqual(on['push']['branches'], ['claude/line-ruling-*'])
        self.assertEqual(self.wf['concurrency']['group'], 'regenerate-verified')   # 與其他正式寫入排隊

    def test_gate_rejects_future_old_and_bad_names(self):
        gate = self.run.split('PLAN=')[0]
        bash = shutil.which('bash') or ''
        if os.name == 'nt':   # Windows 的 bash 可能是 WSL；改用 Git Bash，沒有就略過（CI 在 Linux 上一定會跑）
            bash = next((b for b in (r'C:\Program Files\Git\bin\bash.exe', r'C:\Program Files\Git\usr\bin\bash.exe')
                         if os.path.exists(b)), '')
        if not bash:
            self.skipTest('沒有可用的 bash')
        def check(branch, today='20261108', oldest='20261009'):
            script = gate.replace('$(TZ=Asia/Taipei date +%Y%m%d)', today).replace("$(TZ=Asia/Taipei date -d '30 days ago' +%Y%m%d)", oldest)
            return subprocess.run([bash, '-c', script], env=dict(os.environ, BRANCH=branch), capture_output=True)
        self.assertEqual(check('claude/line-ruling-20261106-153000').returncode, 0)
        self.assertEqual(check('claude/line-ruling-20261108-0930').returncode, 0)
        self.assertNotEqual(check('claude/line-ruling-20261109-153000').returncode, 0)
        self.assertNotEqual(check('claude/line-ruling-20261001-153000').returncode, 0)
        self.assertNotEqual(check('claude/line-ruling-2026116-1').returncode, 0)

    def test_uses_existing_apply_tool_and_marks_rulings(self):
        self.assertIn('apply_source_review.py --date "$DAY" --apply', self.run)
        steps = ' '.join(s.get('run', '') for s in self.wf['jobs']['apply']['steps'])
        self.assertIn('rulings.py finish --branch "$BRANCH" --note', steps)
        self.assertIn('rulings.py finish --branch "$BRANCH" --failed', steps)


if __name__ == '__main__':
    unittest.main()
