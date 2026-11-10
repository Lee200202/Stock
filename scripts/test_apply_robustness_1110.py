"""2026/11/10 每日複核套用推了五輪才成功；這裡固定修好的四件事（不連試算表、不呼叫模型，通知句子是合成的）：

1. 同一則通知改兩檔時，解析明細要累積修改、每列只寫一次（以前第二筆蓋掉第一筆 → 3661 讀回不一致）。
2. 備份後只比要寫的列；其他列變動、表尾新增列不讓整組失敗，要寫的列變了才停。
3. 推送前檢查（preflight）抓得到：引句不到 6 字、刪除項缺舊說明指紋、通知計畫跨兩張表。
4. 收據判斷「真的套用過」；重推成功時，先前標「修正失敗」的列改回已修正；備份「核對不符」會重試。
"""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))


def load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nr = load('notice_reason', 'scripts/apply_notice_reason_fix.py')
sr = load('source_review', 'scripts/apply_source_review.py')
import backup_archive  # noqa: E402
import mark_fixed  # noqa: E402
import plan_receipt  # noqa: E402
import preflight  # noqa: E402

sha = lambda t: hashlib.sha256(t.encode('utf-8')).hexdigest()
T = ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']
S = ['文章ID', '發文時間', '原文', '解析明細', '內容指紋']
RAW = '今天早上在漲停賣出甲公司獲利了結，資金改買乙公司，乙公司走完ABC波跌破前低是右下角的位置。'


def tables():
    trades = [T,
              ['2026/11/10', '甲公司', '1111', '賣出', '', '舊理由甲', 'CMONEY-900', '1'],
              ['2026/11/10', '乙公司', '2222', '買入', '', '舊理由乙', 'CMONEY-900', '2'],
              ['2026/11/10', '丙公司', '3333', '觀望注意', '', 'x', 'VID', '1']]
    detail = [{'code': '1111', 'dir': '賣出', 'reason': '舊理由甲'}, {'code': '2222', 'dir': '買入', 'reason': '舊理由乙'}]
    sms = [S, ['900', '2026/11/10 9:40', '（合成通知）', json.dumps(detail, ensure_ascii=False), 'h']]
    return trades, sms


def item(code, name, old, new):
    return dict(code=code, name=name, notice_source_id='CMONEY-900', article_id='900',
                previous_reason_sha256=sha(old), previous_reason_chars=len(old), text=new,
                quotes=['今天早上在漲停賣出甲公司'], rationale='原稿重寫')


PLAN = dict(date='2026/11/10', video_id='VID', raw_sha256=sha(RAW), authorization='管理者授權', review_basis='複核',
            items=[item('1111', '甲公司', '舊理由甲', '甲公司今天在漲停賣出獲利了結。'),
                   item('2222', '乙公司', '舊理由乙', '乙公司走完ABC波、跌破前低，是右下角的位置。')])


class SameNoticeTwoStocks(unittest.TestCase):
    def test_both_reasons_survive_in_one_detail_cell(self):
        trades, sms = tables()
        writes, work, planned = nr.plan_writes(PLAN, trades, sms, '理由摘錄', False, '2026/11/10')
        self.assertEqual(len(writes), 2)
        self.assertEqual(list(work), [2])                      # 同一列通知只寫一次
        reasons = {d['code']: d['reason'] for d in work[2]}
        self.assertEqual(reasons['1111'], PLAN['items'][0]['text'])
        self.assertEqual(reasons['2222'], PLAN['items'][1]['text'])

    def test_repair_detail_only_when_trade_row_already_fixed(self):
        # 11/10 的實際情況：操作紀錄已是新句，只剩解析明細是舊句 → 用 previous_detail_sha256 補
        trades, sms = tables()
        new = PLAN['items'][0]['text']
        trades[1][5] = new
        one = dict(PLAN, items=[dict(PLAN['items'][0], previous_reason_sha256=sha(new), previous_reason_chars=len(new),
                                     previous_detail_sha256=sha('舊理由甲'))])
        _, work, _ = nr.plan_writes(one, trades, sms, '理由摘錄', False, '2026/11/10')
        self.assertEqual({d['code']: d['reason'] for d in work[2]}['1111'], new)

    def test_receipt_but_mismatch_explains_how_to_repair(self):
        trades, sms = tables()
        with self.assertRaisesRegex(ValueError, 'previous_detail_sha256'):
            nr.plan_writes(PLAN, trades, sms, '理由摘錄', True, '2026/11/10')


class PositionOnlyHoldingNote(unittest.TestCase):
    """祥碩 11/10：通知產生的持股列說明照抄通知、原稿沒談到本股 → 只准固定句陳述部位，不需引句。"""

    def base(self, **kw):
        it = dict(code='5269', name='祥碩', sheet='會員持股', position_only=True, notice_source_id='CMONEY-1', article_id='1',
                  previous_reason_sha256='a' * 64, previous_reason_chars=10, text=nr.position_only_text('祥碩'),
                  rationale='原稿沒有談到祥碩')
        it.update(kw)
        return it

    def test_fixed_sentence_on_holdings_passes_without_quotes(self):
        nr.check_item(self.base(), None, lambda r, q: None, RAW)

    def test_any_other_text_or_sheet_is_refused(self):
        for bad in (self.base(text='祥碩續抱，目標價1000元。'), self.base(sheet='操作紀錄'), self.base(rationale='')):
            with self.assertRaises(ValueError):
                nr.check_item(bad, None, lambda r, q: None, RAW)


class RowScopedGuard(unittest.TestCase):
    def test_other_rows_may_change(self):
        trades, sms = tables()
        _, _, planned = nr.plan_writes(PLAN, trades, sms, '理由摘錄', False, '2026/11/10')
        now_t = [r[:] for r in trades] + [['2026/11/10', '丁', '4444', '買入', '', 'n', 'CMONEY-901', '1']]
        now_t[3][5] = '別人改了丙'
        now_s = [r[:] for r in sms] + [['901', 't', '新通知', '[]', 'h2']]
        nr.unchanged_rows_or_raise(PLAN, planned, now_t, now_s)   # 不丟例外

    def test_target_row_change_stops(self):
        trades, sms = tables()
        _, _, planned = nr.plan_writes(PLAN, trades, sms, '理由摘錄', False, '2026/11/10')
        now_t = [r[:] for r in trades]
        now_t[2][5] = '別人改了乙'
        with self.assertRaisesRegex(ValueError, '這一列資料變動'):
            nr.unchanged_rows_or_raise(PLAN, planned, now_t, sms)

    def test_prefix_unchanged_allows_appended_rows_only(self):
        a = [['h1', 'h2'], ['1', '2']]
        self.assertTrue(sr.prefix_unchanged(a, a + [['3', '4']]))
        self.assertTrue(sr.prefix_unchanged(a, [['h1', 'h2'], ['1', '2', '']]))
        self.assertFalse(sr.prefix_unchanged(a, [['h1', 'h2'], ['1', 'x']]))
        self.assertFalse(sr.prefix_unchanged(a, a[:1]))


class Preflight(unittest.TestCase):
    def run_pf(self, files):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'scripts/repairs').mkdir(parents=True)
            raw = root / 'raw.txt'
            raw.write_text(RAW, encoding='utf-8')
            for kind, plan in files.items():
                (root / 'scripts/repairs' / f'2026-11-10-{kind}.json').write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            return preflight.preflight('2026/11/10', str(raw), root=root)

    def test_good_plan_passes(self):
        seen, problems = self.run_pf({'notice-reason': PLAN})
        self.assertEqual(problems, [])
        self.assertEqual(seen, ['2026-11-10-notice-reason.json'])

    def test_short_quote_is_caught(self):
        bad = json.loads(json.dumps(PLAN))
        bad['items'][0]['quotes'] = ['昨天漲停']
        _, problems = self.run_pf({'notice-reason': bad})
        self.assertTrue(any('不到 6 字' in p for p in problems), problems)

    def test_quote_not_in_raw_is_caught(self):
        bad = json.loads(json.dumps(PLAN))
        bad['items'][1]['quotes'] = ['原稿裡沒有這一句話喔']
        _, problems = self.run_pf({'notice-reason': bad})
        self.assertTrue(any('不在原稿' in p for p in problems), problems)

    def test_delete_without_fingerprint_is_caught(self):
        plan = dict(date='2026/11/10', video_id='VID', raw_sha256=sha(RAW), authorization='a', review_basis='b',
                    items=[dict(code='5347', name='世界', category='watch_watch', action='delete', rationale='一般詞誤收')])
        _, problems = self.run_pf({'source-review': plan})
        self.assertTrue(any('previous_note_sha256' in p for p in problems), problems)

    def test_notice_plan_spanning_two_sheets_is_caught(self):
        bad = json.loads(json.dumps(PLAN))
        bad['items'][1]['sheet'] = '會員持股'
        _, problems = self.run_pf({'notice-reason': bad})
        self.assertTrue(any('一份計畫只改一張表' in p for p in problems), problems)

    def test_wrong_raw_fingerprint_is_caught(self):
        bad = dict(PLAN, raw_sha256='0' * 64)
        _, problems = self.run_pf({'notice-reason': bad})
        self.assertTrue(any('raw_sha256' in p for p in problems), problems)


class ReceiptsAndMarks(unittest.TestCase):
    def test_receipt_means_applied(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / 'scripts/repairs').mkdir(parents=True)
            (root / 'scripts/repairs/2026-11-10-notice-reason.json').write_text(json.dumps(PLAN, ensure_ascii=False), encoding='utf-8')
            (root / 'scripts/repairs/2026-11-10-source-review.json').write_text('{"items": []}', encoding='utf-8')
            r = plan_receipt.receipt_of('notice-reason', PLAN)
            rows = [['t', '2026/11/10', '通知用語外洩修正', '', '', '', json.dumps({'receipt': r}, ensure_ascii=False)]]
            self.assertEqual(plan_receipt.applied('2026/11/10', rows, root=root),
                             ['scripts/repairs/2026-11-10-notice-reason.json'])
            self.assertEqual(plan_receipt.applied('2026/11/10', [], root=root), [])

    def test_receipt_matches_tool_digest(self):
        self.assertEqual(plan_receipt.receipt_of('notice-reason', PLAN), '通知理由修正:' + nr.digest(PLAN))
        self.assertEqual(plan_receipt.receipt_of('source-review', PLAN), '來源核對修正:' + sr.digest(PLAN))

    def test_success_flips_earlier_failed_rows(self):
        head = ['日期', '代號', '處理', '修正後']
        rows = [{'日期': '2026/11/10', '代號': '2330', '處理': '修正失敗', '修正後': ''},
                {'日期': '2026/11/10', '代號': '①', '處理': '修正失敗', '修正後': ''},
                {'日期': '2026/11/10', '代號': '5269', '處理': '待管理者', '修正後': ''},
                {'日期': '2026/11/10', '代號': '9999', '處理': '修正失敗', '修正後': ''},
                {'日期': '2026/11/09', '代號': '2330', '處理': '修正失敗', '修正後': ''}]
        out = dict(mark_fixed.updates('2026/11/10', head, rows, {'2330': '新說明'}, False))
        self.assertEqual(sorted(out), [2, 3])                 # 計畫內的代號與章節列；待管理者、計畫外、其他日期不動
        self.assertEqual(out[2][2], '已修正')
        self.assertEqual(out[2][3], '新說明')
        self.assertEqual(mark_fixed.updates('2026/11/10', head, rows, {'2330': 'x'}, True), [])

    def test_backup_retries_when_source_changed_during_snapshot(self):
        answers = iter([{'ok': False, 'error': '備份核對不符：會員簡訊；原分頁保留'},
                        {'ok': True, 'sourceId': 'B', 'verified': ['a']}])
        resp = mock.Mock()
        resp.json.side_effect = lambda: next(answers)
        pl = mock.Mock(APPS_SCRIPT_URL='u', ADMIN_KEY='k', SPREADSHEET_ID='B')
        with mock.patch.object(backup_archive.requests, 'get', return_value=resp), \
                mock.patch.object(backup_archive, 'sleep'):
            self.assertTrue(backup_archive.archive_request(pl, 'snapshot', ['a'])['ok'])

    def test_backup_other_errors_still_stop(self):
        resp = mock.Mock()
        resp.json.return_value = {'ok': False, 'error': '權限不足'}
        pl = mock.Mock(APPS_SCRIPT_URL='u', ADMIN_KEY='k', SPREADSHEET_ID='B')
        with mock.patch.object(backup_archive.requests, 'get', return_value=resp), \
                mock.patch.object(backup_archive, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '權限不足'):
                backup_archive.archive_request(pl, 'snapshot', ['a'])


class WorkflowSkipUsesReceipts(unittest.TestCase):
    """11/10：第三輪通知理由計畫失敗，第四、五輪卻因「這次沒改到」而跳過。現在有收據才跳過。"""

    def script_out(self, applied_lines):
        import os
        import shutil
        import subprocess
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('沒有 bash')
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
            subprocess.run(['git', 'init', '-q', d], check=True)
            rep = Path(d, 'scripts/repairs'); rep.mkdir(parents=True)
            for k in ('notice-reason', 'points-review'):
                (rep / f'2026-11-10-{k}.json').write_text('{}', encoding='utf-8')
            fake = Path(d, 'scripts/daily_review'); fake.mkdir(parents=True)
            (fake / 'plan_receipt.py').write_text('print(' + repr('\n'.join(applied_lines)) + ')', encoding='utf-8')
            subprocess.run(['git', '-C', d, 'add', '-A'], check=True, env=env)
            subprocess.run(['git', '-C', d, 'commit', '-qm', 'r1'], check=True, env=env)
            before = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            (rep / '2026-11-10-source-review.json').write_text('{}', encoding='utf-8')
            subprocess.run(['git', '-C', d, 'add', '-A'], check=True, env=env)
            subprocess.run(['git', '-C', d, 'commit', '-qm', 'r2'], check=True, env=env)
            sha_ = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            wf = (ROOT / '.github/workflows/daily-review-apply.yml').read_text(encoding='utf-8')
            block = wf[wf.index('          # 2026/11/06：同一天第二輪'):wf.index('          # 2026/11/06：暫時性錯誤')]
            script = ('DAY=2026/11/10\nPLAN="scripts/repairs/${DAY//\\//-}-source-review.json"\n'
                      'POINTS="scripts/repairs/${DAY//\\//-}-points-review.json"\nNOTICE="scripts/repairs/${DAY//\\//-}-notice-reason.json"\n'
                      + '\n'.join(l[10:] for l in block.splitlines()) + '\necho "PLAN=$PLAN"; echo "POINTS=$POINTS"; echo "NOTICE=$NOTICE"\n')
            py = Path(sys.executable).as_posix()
            return subprocess.run([bash, '-c', f'python() {{ "{py}" "$@"; }}\n' + script], cwd=d, capture_output=True,
                                  text=True, env=dict(env, BEFORE=before, GITHUB_SHA=sha_)).stdout

    def test_failed_previous_round_is_retried_and_applied_one_skipped(self):
        out = self.script_out(['APPLIED scripts/repairs/2026-11-10-points-review.json'])
        self.assertIn('NOTICE=scripts/repairs/2026-11-10-notice-reason.json', out, '沒有收據（前一輪失敗）要再套')
        self.assertIn('POINTS=/nonexistent/POINTS', out, '有收據且沒改的不重套')
        self.assertIn('PLAN=scripts/repairs/2026-11-10-source-review.json', out, '這次改到的照套')


if __name__ == '__main__':
    unittest.main()
