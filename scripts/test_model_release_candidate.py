"""驗證答案隔離、失敗阻擋、來源漂移及同一候選精確發布；不呼叫模型。"""
import copy
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
from model_release_candidate import VERSION, TABLES, digest, seal, validate, inference_values, plan_tables, cell_requests
from check_candidate_run import validate_run

DAY = '2026/10/27'


class CandidateContract(unittest.TestCase):
    def payload(self):
        return dict(version=VERSION, date=DAY, commit='abc', source_sha256='raw', state={'影片清單': 's'},
            reference_sha256='policy', mode='fresh_model_only', answer_injection=False, status='passed',
            errors=[], run_id='123', model_calls=3, signals={'buy': [{'code': '1111', 'reason': '本股量價條件。'}]}, article='模型文章')

    def check(self, envelope, **kw):
        options = dict(day=DAY, commit='abc', source_sha='raw', state={'影片清單': 's'}, reference_sha='policy')
        options.update(kw)
        return validate(envelope, **options)

    def test_exact_candidate_is_not_regenerated(self):
        original = self.payload()
        self.assertEqual(self.check(seal(original)), original)
        with patch('requests.post', side_effect=AssertionError('不該呼叫模型')):
            self.check(seal(original))

    def test_modified_bytes_fail(self):
        envelope = seal(self.payload())
        envelope['payload']['signals']['buy'][0]['reason'] = '事後填答案'
        with self.assertRaises(ValueError): self.check(envelope)

    def test_failed_unscored_injected_and_no_model_cannot_publish(self):
        for changes in ({'status': 'failed'}, {'errors': ['世芯來源不足']}, {'answer_injection': True},
                        {'reference_sha256': ''}, {'model_calls': 0}, {'mode': 'assisted'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(seal(dict(self.payload(), **changes)))

    def test_source_state_policy_and_code_drift_fail(self):
        for kw in ({'source_sha': 'new'}, {'state': {}}, {'commit': 'other'}, {'reference_sha': 'new'}, {'day': '2026/10/26'}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): self.check(seal(self.payload()), **kw)

    def test_answers_and_future_positions_are_absent_from_inference(self):
        rows = [['日期', '代號', '來源影片ID', '理由摘錄'],
                ['2026/10/26', '1111', 'video', '既有部位'],
                [DAY, '2222', 'video', '人工作答'],
                [DAY, '3333', 'CMONEY-1', '人工說明'],
                ['2026/10/28', '4444', 'video', '未來部位']]
        frozen = inference_values('操作紀錄', rows, DAY, lambda x: x)
        self.assertEqual([r[1] for r in frozen[1:]], ['1111', '3333'])
        self.assertEqual(frozen[-1][-1], '')
        self.assertEqual(rows[3][-1], '人工說明')
        for name in ('分類裁決', '修正建議', '持股追蹤', '逐字稿判讀稽核'):
            self.assertEqual(inference_values(name, rows, DAY, lambda x: x), [rows[0]])
        self.assertEqual(inference_values('會員簡訊', [['原文', '解析明細'], ['原始指令', '[人工答案]']], DAY, str)[1], ['原始指令', '[]'])

    def test_planner_preserves_notice_manual_other_dates_and_sent_flags(self):
        head = ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']
        before = {'操作紀錄': [head, [DAY, '通知甲', '1111', '買入', '100', '通知原說明', 'CMONEY-1', '1'],
            [DAY, '舊股', '2222', '觀望不碰', '', '舊版', 'old-video', '1'],
            ['2026/10/26', '別天', '9999', '買入', '1', '不能動', 'video', '1']],
            '會員持股': [['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID']],
            '每日推播內容': [['日期', '文章', '寄送'], [DAY, '舊文章', '已寄送']]}
        original = copy.deepcopy(before)
        sig = {'watch_watch': [{'code': '3333', 'name': '新股', 'reason': '本輪模型自然說明。'}]}
        after = plan_tables(before, sig, '本輪模型文章', DAY, 'video', str, lambda s: s.startswith('CMONEY-'))
        self.assertEqual(before, original)
        self.assertIn(before['操作紀錄'][1], after['操作紀錄'])
        self.assertIn(before['操作紀錄'][3], after['操作紀錄'])
        self.assertNotIn(before['操作紀錄'][2], after['操作紀錄'])
        self.assertEqual(after['每日推播內容'][1], [DAY, '本輪模型文章', '已寄送'])
        requests = cell_requests(before, after, {n: i for i, n in enumerate(TABLES)})
        self.assertTrue(requests)
        self.assertFalse(any(r['updateCells']['range']['sheetId'] == 2 and
            r['updateCells']['range']['startColumnIndex'] == 2 for r in requests))

    def test_wrong_or_failed_github_run_cannot_promote(self):
        run = dict(id=123, repository={'full_name': 'owner/repo'}, path='.github/workflows/regenerate-verified.yml',
            event='workflow_dispatch', status='completed', conclusion='success', head_sha='abc',
            display_title=f'單日品質核對 {DAY}（preview）')
        validate_run(run, '123', 'owner/repo', 'abc', DAY)
        for change in ({'conclusion': 'failure'}, {'head_sha': 'other'}, {'event': 'pull_request'},
                       {'path': 'other.yml'}, {'display_title': f'單日品質核對 {DAY}（source-review-apply）'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_run(dict(run, **change), '123', 'owner/repo', 'abc', DAY)

    def test_preview_contains_no_target_answer_wrappers(self):
        from pathlib import Path
        source = Path(__file__).with_name('regenerate_verified_day.py').read_text(encoding='utf-8')
        for obsolete in ('verified_cache', 'polish_then_enforce', 'reviewed_then_quarantine', 'enrich_sms_notes_from_signals'):
            self.assertNotIn(obsolete, source)
        self.assertIn('replace_video=False', source)


if __name__ == '__main__':
    unittest.main()
