"""每日複核自動修正（管理者 2026/11/03：試行提前結束、股名聽錯可自行修、複核併入 12:30 狀態信）的離線契約。"""
from pathlib import Path
import hashlib
import json
import re
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
import aliases  # noqa: E402
import name_memo  # noqa: E402
import mark_fixed  # noqa: E402
import write_log  # noqa: E402
import apply_notice_reason_fix as notice  # noqa: E402

OFFICIAL = {'6719': '力智', '3483': '力致', '1590': '亞德客-KY', '3533': '嘉澤'}
BUILTIN = {'加澤': ('3533', '嘉澤')}


def alias(heard, code, name):
    return {'heard': heard, 'code': code, 'name': name, 'date': '2026/11/03', 'basis': '測試'}


class AliasRules(unittest.TestCase):
    def test_misheard_name_that_is_not_a_company_is_accepted(self):
        self.assertEqual(aliases.problems([alias('雅德客', '1590', '亞德客-KY')], OFFICIAL, BUILTIN), [])

    def test_heard_name_that_is_another_company_only_fixes_the_day(self):
        bad = aliases.problems([alias('力致', '6719', '力智')], OFFICIAL, BUILTIN)
        self.assertTrue(any('另一家公司' in b for b in bad))

    def test_wrong_official_name_and_builtin_conflict_are_rejected(self):
        self.assertTrue(aliases.problems([alias('雅德客', '1590', '亞德客')], OFFICIAL, BUILTIN))
        self.assertTrue(aliases.problems([alias('加澤', '6719', '力智')], OFFICIAL, BUILTIN))

    def test_merge_keeps_main_and_refuses_a_conflicting_code(self):
        base = [alias('雅德客', '1590', '亞德客-KY')]
        self.assertEqual(len(aliases.merge(base, base + [alias('牙德客', '1590', '亞德客-KY')])), 2)
        with self.assertRaises(ValueError):
            aliases.merge(base, [alias('雅德客', '6719', '力智')])

    def test_repository_alias_file_is_well_formed_and_loaded_by_the_pipeline(self):
        entries = json.loads((ROOT / 'pipeline/reviewed_aliases.json').read_text(encoding='utf-8'))
        for e in entries:
            self.assertEqual(set(e), set(aliases.FIELDS))
        self.assertNotIn('力致', {e['heard'] for e in entries})
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("CONFIRMED_NAMES.update({k: v for k, v in _load_reviewed_aliases().items() if k not in CONFIRMED_NAMES})", src)


class ReviewLogStatus(unittest.TestCase):
    def test_pending_fixes_keep_the_mail_waiting(self):
        self.assertEqual(write_log.summary_status([{'action': '待套用'}, {'action': '只記錄'}]), '修正中')
        self.assertEqual(write_log.summary_status([{'action': '待管理者'}]), '已完成')

    def test_mark_fixed_only_touches_that_days_pending_rows(self):
        head = ['日期', '代號', '處理', '修正後']
        rows = [{'日期': '2026/11/04', '代號': '1590', '處理': '待套用', '修正後': ''},
                {'日期': '2026/11/04', '代號': '2330', '處理': '只記錄', '修正後': ''},
                {'日期': '2026/11/03', '代號': '6533', '處理': '待套用', '修正後': ''}]
        done = mark_fixed.updates('2026/11/04', head, rows, {'1590': '新說明'}, False)
        self.assertEqual(done, [(2, ['2026/11/04', '1590', '已修正', '新說明'])])
        failed = mark_fixed.updates('2026/11/04', head, rows, {}, True)
        self.assertEqual(failed[0][1][2], '修正失敗')

    def test_no_immediate_notice_is_scheduled(self):
        src = (ROOT / 'scripts/daily_review/write_log.py').read_text(encoding='utf-8')
        self.assertNotIn('複核結果（試行）', src)


class NoticeReasonPlan(unittest.TestCase):
    def test_plan_may_not_carry_the_leaked_sentence(self):
        item = {'previous_reason': '舊句', 'text': '新', 'quotes': ['x'], 'rationale': 'y'}
        with self.assertRaises(ValueError):
            notice.check_item(item, None, lambda raw, q: None, '')
        for path in (ROOT / 'scripts/repairs').glob('*-notice-reason.json'):
            for it in json.loads(path.read_text(encoding='utf-8'))['items']:
                self.assertNotIn('previous_reason', it)
                self.assertEqual(len(it['previous_reason_sha256']), 64)

    def test_reviewed_reason_is_locked_against_the_sms_rewrite(self):
        tool = (ROOT / 'scripts/apply_notice_reason_fix.py').read_text(encoding='utf-8')
        self.assertIn("reviewed='通知理由修正 ' + day", tool)
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        body = src.split('stored_notes, edited_rows, reviewed_codes = {}, set(), set()')[1].split('\ndef ')[0]
        self.assertIn('if code in reviewed_codes:', body)
        self.assertIn("if not isinstance(item,dict) or item.get('reviewed'):continue", body)

    def test_fingerprint_search_finds_the_old_sentence(self):
        old = '今天全部賣出。'
        digest = hashlib.sha256(old.encode('utf-8')).hexdigest()
        self.assertTrue(notice.contains_sha('前文' + old + '後文', len(old), digest))
        self.assertFalse(notice.contains_sha('改寫過的文章', len(old), digest))


class NameLearning(unittest.TestCase):
    HEAD = ['聽到的名稱', '判定', '正式名稱', '代號', '情境關鍵詞', '依據', '來源', '建立日期', '命中次數', '最後命中']

    def item(self, heard, code, name, keys=()):
        return {'heard': heard, 'code': code, 'name': name, 'keys': list(keys), 'why': '測試', 'quotes': [heard + '原句']}

    def test_misheard_name_is_written_as_review_source(self):
        new, skipped, bad = name_memo.plan_rows(self.HEAD, [], [self.item('雅德客', '1590', '亞德客-KY')], OFFICIAL, '2026/11/03')
        self.assertEqual(bad, [])
        self.assertEqual(new[0][:5] + new[0][6:8], ['雅德客', 'stock', '亞德客-KY', '1590', '', '複核', '2026/11/03'])

    def test_a_real_company_name_needs_context_keys(self):
        _, _, bad = name_memo.plan_rows(self.HEAD, [], [self.item('力致', '6719', '力智')], OFFICIAL, '2026/11/03')
        self.assertTrue(any('情境關鍵詞' in b for b in bad))
        new, _, bad = name_memo.plan_rows(self.HEAD, [], [self.item('力致', '6719', '力智', ['加澤', '180'])], OFFICIAL, '2026/11/03')
        self.assertEqual(bad, [])
        self.assertEqual(new[0][4], '加澤、180')

    def test_existing_row_is_skipped_and_a_conflicting_trusted_row_stops(self):
        rows = [['雅德客', 'stock', '亞德客-KY', '1590', '', '', '人工', '', '1', '']]
        _, skipped, _ = name_memo.plan_rows(self.HEAD, rows, [self.item('雅德客', '1590', '亞德客-KY')], OFFICIAL, 'd')
        self.assertEqual(skipped, ['雅德客'])
        _, _, bad = name_memo.plan_rows(self.HEAD, rows, [self.item('雅德客', '6719', '力智')], OFFICIAL, 'd')
        self.assertTrue(any('管理者' in b for b in bad))

    def test_pipeline_applies_review_source_like_manual(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("NAME_MEMO_TRUSTED = ('人工', '複核')", src)
        self.assertIn("if e.get('source') not in NAME_MEMO_TRUSTED:", src)


class ApplyWorkflowAndMail(unittest.TestCase):
    def test_apply_then_mark_then_name_learning_and_failure_only_after_apply(self):
        wf = yaml.safe_load((ROOT / '.github/workflows/daily-review-apply.yml').read_text(encoding='utf-8'))
        steps = wf['jobs']['apply']['steps']
        names = [s.get('name', '') for s in steps]
        i_apply = next(i for i, s in enumerate(steps) if s.get('id') == 'apply')
        i_mark = next(i for i, n in enumerate(names) if '標記已修正' in n)
        i_alias = next(i for i, n in enumerate(names) if '股名學習' in n)
        self.assertLess(i_apply, i_mark)
        self.assertLess(i_mark, i_alias)
        fail = next(s for s in steps if '失敗' in s.get('name', ''))
        self.assertIn("steps.apply.outcome == 'failure'", fail['if'])
        self.assertIn('name_memo.py --date "$DAY" --apply', steps[i_alias]['run'])
        self.assertNotIn('git push', yaml.safe_dump(wf, allow_unicode=True))   # 不再推 main，只寫資料庫
        self.assertEqual(wf['permissions'], {'contents': 'read'})

    def test_review_mail_is_folded_into_the_status_mail(self):
        gs = (ROOT / 'public-site/gas-source/Dailyreview.gs').read_text(encoding='utf-8')
        self.assertIn('function dailyReviewNoticeTick_() {}', gs)
        self.assertNotIn('MailApp.sendEmail(mail)', gs.split('function dailyReviewNoticeTick_')[1])
        self.assertRegex(gs, r"st === '複核中' \|\| st === '修正中'")
        mail = (ROOT / 'public-site/gas-source/MailService.gs').read_text(encoding='utf-8')
        body = mail.split('function statusReportJob()')[1].split('\nfunction ')[0]
        self.assertIn('dailyReviewStatusSection_(today)', body)
        self.assertIn("review ? review.state : ''", body)
        self.assertTrue(re.search(r"hhmm >= 1230 && hhmm <= 1800 && dueEvery_\('statusReport'",
                                  (ROOT / 'public-site/gas-source/Setup.gs').read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
