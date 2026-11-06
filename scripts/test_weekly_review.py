"""每週流程檢討（管理者 2026/11/06：每週五 17:00 雲端排程修整流程並線上驗收）的離線契約。"""
from datetime import date
import json
from pathlib import Path
import sys
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'weekly_review'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
import week  # noqa: E402
import check_plan  # noqa: E402


class WeekRange(unittest.TestCase):
    def test_friday_and_weekend_map_to_the_same_week(self):
        for d in ('2026/11/02', '2026/11/06', '2026/11/07', '2026/11/08'):
            self.assertEqual(week.week_of(d), ('2026/11/06', ['2026/11/02', '2026/11/03', '2026/11/04', '2026/11/05', '2026/11/06']))

    def test_monday_starts_a_new_week(self):
        self.assertEqual(week.week_of('2026/11/09')[0], '2026/11/13')


def row(kind, accept='', handling='待套用'):
    return {'日期': '2026/11/04', '代號': '2317', '名稱': '鴻海', '差異類型': kind, '可接受集合': accept,
            '嚴重度': '明確錯誤', '處理': handling}


class OnlineAcceptance(unittest.TestCase):
    def test_class_error_passes_only_when_site_is_in_the_accepted_set(self):
        good = [{'category': 'watch_watch', 'note': '等大盤穩'}]
        bad = [{'category': 'watch_avoid', 'note': '先不要'}]
        self.assertEqual(week.judge(row('分類', 'watch_watch'), good)[0], '通過')
        self.assertEqual(week.judge(row('分類', 'watch_watch'), bad)[0], '未通過')
        self.assertEqual(week.judge(row('漏收', 'watch_watch'), [])[0], '未通過', '還沒收錄不算通過')
        self.assertEqual(week.judge(row('漏收', 'watch_watch／ignored'), [])[0], '通過', '可接受不公開時不收錄也對')

    def test_wrongly_public_passes_when_gone(self):
        self.assertEqual(week.judge(row('應排除卻公開'), [])[0], '通過')
        self.assertEqual(week.judge(row('應排除卻公開'), [{'category': 'holdings', 'note': ''}])[0], '未通過')

    def test_unverifiable_kinds_never_pass_silently(self):
        self.assertEqual(week.judge(row('通知用語外洩', handling='待管理者'), [])[0], '需人工')
        self.assertEqual(week.judge(row('通知用語外洩', handling='已修正'), [])[0], '通過')
        self.assertEqual(week.judge(row('分類', ''), [])[0], '需人工')

    def test_mark_only_touches_this_weeks_passing_clear_errors(self):
        head = ['日期', '代號', '嚴重度', '處理', '是否轉成規則或測試']
        rows = [{'日期': '2026/11/04', '代號': '2317', '嚴重度': '明確錯誤', '處理': '修正失敗', '是否轉成規則或測試': '否'},
                {'日期': '2026/11/04', '代號': '2308', '嚴重度': '明確錯誤', '處理': '待管理者', '是否轉成規則或測試': '否'},
                {'日期': '2026/11/04', '代號': '2330', '嚴重度': '待確認', '處理': '待管理者', '是否轉成規則或測試': '否'},
                {'日期': '2026/10/30', '代號': '2317', '嚴重度': '明確錯誤', '處理': '修正失敗', '是否轉成規則或測試': '否'}]
        checks = [{'date': '2026/11/04', 'code': '2317', 'result': '通過'}, {'date': '2026/11/04', 'code': '2308', 'result': '未通過'},
                  {'date': '2026/11/04', 'code': '2330', 'result': '通過'}, {'date': '2026/10/30', 'code': '2317', 'result': '通過'}]
        days = week.week_of('2026/11/06')[1]
        out = week.mark_updates(head, rows, days, checks, {('2026/11/04', '2317')}, 'claude/pipeline-weekly-20261106')
        self.assertEqual([i for i, _ in out], [2])
        self.assertEqual(out[0][1], ['2026/11/04', '2317', '明確錯誤', '週檢討確認已正確', '是（claude/pipeline-weekly-20261106）'])


class Gate(unittest.TestCase):
    def test_branch_name_and_date_window(self):
        today = date(2026, 11, 6)
        self.assertEqual(check_plan.stamp_problems('claude/pipeline-weekly-20261106', today), ('2026/11/06', []))
        self.assertEqual(check_plan.stamp_problems('pipeline-weekly/20261105', today)[0], '2026/11/05')
        self.assertIsNone(check_plan.stamp_problems('claude/pipeline-weekly-20261030', today)[0], '超過兩天的舊分支不跑')
        self.assertIsNone(check_plan.stamp_problems('claude/daily-review-apply-20261106', today)[0])

    def test_existing_golden_answers_can_only_grow(self):
        old = json.dumps({'must': {'2317': ['watch_watch']}, 'must_not': ['3483'], 'raw_sha256': 'a'})
        grown = json.dumps({'must': {'2317': ['watch_watch'], '2308': ['watch_avoid']}, 'must_not': ['3483'], 'raw_sha256': 'a'})
        changed = json.dumps({'must': {'2317': ['watch_watch', 'watch_avoid']}, 'must_not': ['3483'], 'raw_sha256': 'a'})
        dropped = json.dumps({'must': {'2317': ['watch_watch']}, 'must_not': [], 'raw_sha256': 'a'})
        self.assertEqual(check_plan.golden_problems('g.json', old, grown), [])
        self.assertTrue(check_plan.golden_problems('g.json', old, changed))
        self.assertTrue(check_plan.golden_problems('g.json', old, dropped))
        self.assertEqual(check_plan.golden_problems('g.json', None, grown), [], '新的答案檔可以新增')

    def test_contracts_runner_may_only_gain_suites(self):
        old = "X = 1\nSUITES = [\n    'a',\n    'b',\n]\nY = 2\n"
        self.assertEqual(check_plan.contracts_problems(old, old.replace("'b',", "'b',\n    'c',")), [])
        self.assertTrue(check_plan.contracts_problems(old, old.replace("    'a',\n", '')))
        self.assertTrue(check_plan.contracts_problems(old, old.replace('Y = 2', 'Y = 3')))

    def test_rule_change_needs_this_week_replays_and_two_holdouts(self):
        days = week.week_of('2026/11/06')[1]
        plan = {'replay_days': ['2026/11/03'], 'holdout_days': ['2026/10/29', '2026/10/28'],
                'changes': [{'cause': '語氣關鍵字', 'rule': 'r', 'cases': [{'date': '2026/11/03', 'code': '2317'}], 'ruling': '裁示紀錄一-3'}]}
        self.assertEqual(check_plan.plan_problems(plan, days, True), [])
        self.assertTrue(check_plan.plan_problems(dict(plan, holdout_days=['2026/10/29']), days, True))
        self.assertTrue(check_plan.plan_problems(dict(plan, replay_days=[]), days, True))
        self.assertTrue(check_plan.plan_problems(dict(plan, replay_days=['2026/10/27']), days, True), '重播日要在本週')
        self.assertTrue(check_plan.plan_problems(dict(plan, changes=[{'rule': 'r'}]), days, True), '要寫成因與依據的裁示')
        self.assertEqual(check_plan.plan_problems({'repairs': [{'date': '2026/11/04'}]}, days, False), [])
        self.assertTrue(check_plan.plan_problems({'repairs': [{'date': '2026/10/30'}]}, days, False), '修正只限本週')

    def test_gas_mirrored_constants_are_the_ones_sync_quality_writes(self):
        src = (ROOT / 'scripts/sync_quality.py').read_text(encoding='utf-8')
        for name in check_plan.MIRRORED:
            self.assertIn(name, src, f'{name} 不在 sync_quality.py，守門清單過期')


class Workflow(unittest.TestCase):
    def setUp(self):
        self.wf = yaml.safe_load((ROOT / '.github/workflows/pipeline-weekly.yml').read_text(encoding='utf-8'))
        self.steps = {s.get('id') or s.get('name'): s for s in self.wf['jobs']['weekly']['steps']}

    def test_runs_on_the_routine_branches_only(self):
        on = self.wf.get('on') or self.wf.get(True)
        self.assertEqual(on['push']['branches'], ['claude/pipeline-weekly-*', 'pipeline-weekly/*'])

    def test_merge_requires_gate_offline_and_replay(self):
        cond = self.steps['merge']['if']
        for need in ("steps.gate.outcome == 'success'", "steps.offline.outcome == 'success'", "steps.replay.outcome == 'success'"):
            self.assertIn(need, cond)
        self.assertIn('run_offline_contracts.py', self.steps['merge']['run'], '合併後的結果要再跑一次離線契約')

    def test_repairs_need_the_gate_and_never_send_mail(self):
        self.assertIn("steps.gate.outcome == 'success'", self.steps['repairs']['if'])
        text = (ROOT / '.github/workflows/pipeline-weekly.yml').read_text(encoding='utf-8')
        for word in ('clasp', 'wrangler', 'MailApp', 'GmailApp'):
            self.assertNotIn(word, text)

    def test_replays_share_the_quality_replay_queue(self):
        self.assertEqual(self.wf['concurrency']['group'], 'gemini-readonly-quality-replay')
        self.assertIn('--fresh-verdicts', (ROOT / 'scripts/weekly_review/workflow_steps.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
