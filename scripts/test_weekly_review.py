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


class Robustness1106(unittest.TestCase):
    """2026/11/06 第一次週檢討：保留日的既有不符擋下合併；備份鎖忙碌中斷章節修正；合寫代號對不到網站。"""
    def test_existing_failures_on_main_do_not_block_the_merge(self):
        import workflow_steps as ws
        miss = '✗ 亞德客-KY（1590）應為 會員持股，實際 沒有收錄'
        branch = {'exit_code': 2, 'misses': [miss], 'errors': 0}
        self.assertEqual(ws.judge_day(branch, {'exit_code': 2, 'misses': [miss], 'errors': 0}), (True, []))
        self.assertEqual(ws.judge_day(branch, {'exit_code': 0, 'misses': [], 'errors': 0})[0], False, 'main 過、分支沒過＝退步')
        self.assertEqual(ws.judge_day(dict(branch, misses=[miss, '✗ 新的']), {'exit_code': 2, 'misses': [miss], 'errors': 0}),
                         (False, ['✗ 新的']))
        self.assertFalse(ws.judge_day(dict(branch, errors=2), {'exit_code': 2, 'misses': [miss], 'errors': 1})[0])
        self.assertEqual(ws.judge_day({'exit_code': 0, 'misses': [], 'errors': 0}, None), (True, []))
        self.assertFalse(ws.judge_day(branch, None)[0], '沒有對照就不放行')

    def test_one_off_misses_are_variance_not_regression(self):
        """10/29 實際資料：分支兩輪漏的股票完全不同，main 漏另外幾檔。"""
        import workflow_steps as ws
        r1 = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 大立光（3008）應為 觀望不碰，實際 沒有收錄']}
        r2 = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 亞德客-KY（1590）應為 會員持股／觀望注意，實際 沒有收錄',
                                                     '✗ 玉晶光（3406）應為 觀望不碰，實際 沒有收錄']}
        main = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 玉晶光（3406）應為 觀望不碰，實際 沒有收錄']}
        self.assertEqual(ws.judge_day(r1, None, r2), (True, []), '兩輪沒有共同的不符＝單次變異')
        steady = dict(r2, misses=r2['misses'] + r1['misses'])
        self.assertEqual(ws.judge_day(r1, main, steady), (False, r1['misses']), '兩輪都漏、main 沒漏才擋')
        self.assertEqual(ws.judge_day(r1, dict(main, misses=main['misses'] + r1['misses']), steady), (True, []))
        self.assertEqual(ws.judge_day(r1, None, {'exit_code': 0, 'errors': 0, 'misses': []}), (True, []))
        self.assertFalse(ws.judge_day(dict(r1, exit_code=3), None, dict(r2, exit_code=3))[0], '通用錯誤不能當變異放行')

    def test_same_stock_and_expectation_is_the_same_miss(self):
        """11/03 實際資料：分支兩輪「實際 觀望注意」、main「實際 沒有收錄」，都是亞德客-KY 應為會員持股。"""
        import workflow_steps as ws
        b = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 鴻海（2317）應為 觀望注意，實際 沒有收錄', '✗ 亞德客-KY（1590）應為 會員持股，實際 觀望注意']}
        b2 = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 亞德客-KY（1590）應為 會員持股，實際 觀望注意']}
        main = {'exit_code': 2, 'errors': 0, 'misses': ['✗ 台積電（2330）應為 會員持股／觀望注意，實際 沒有收錄',
                                                        '✗ 鴻海（2317）應為 觀望注意，實際 沒有收錄', '✗ 亞德客-KY（1590）應為 會員持股，實際 沒有收錄']}
        self.assertEqual(ws.judge_day(b, main, b2), (True, []))
        self.assertEqual(ws.miss_key('✗ 亞德客-KY（1590）應為 會員持股，實際 沒有收錄'), '✗ 亞德客-KY（1590）應為 會員持股')
        self.assertFalse(ws.judge_day(b, dict(main, misses=main['misses'][:2]), b2)[0], 'main 沒漏亞德客時仍要擋')

    def test_backup_lock_busy_is_retried(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import backup_archive as ba
        replies = [{'ok': False, 'error': '備份鎖忙碌，稍後重試'}, {'ok': False, 'error': '備份鎖忙碌，稍後重試'}, {'ok': True, 'verified': ['a']}]
        calls = []
        class R:
            def __init__(self, d): self.d = d
            def json(self): return self.d
        old = (ba.requests.get, ba.sleep)
        try:
            ba.requests.get = lambda *a, **k: (calls.append(1), R(replies[len(calls) - 1]))[1]
            ba.sleep = lambda s: None
            pl = type('P', (), {'APPS_SCRIPT_URL': 'x', 'ADMIN_KEY': 'k', 'SPREADSHEET_ID': 's'})
            self.assertTrue(ba.archive_request(pl, 'snapshot', ['a'])['ok'])
            self.assertEqual(len(calls), 3)
            ba.requests.get = lambda *a, **k: R({'ok': False, 'error': '來源不符'})
            with self.assertRaises(RuntimeError):
                ba.archive_request(pl, 'snapshot', ['a'])
        finally:
            ba.requests.get, ba.sleep = old

    def test_combined_codes_are_checked_one_by_one(self):
        days = week.week_of('2026/11/06')[1]
        row = {'日期': '2026/11/02', '代號': '8299／5289', '名稱': '群聯／宜鼎', '差異類型': '分類', '可接受集合': 'watch_watch',
               '嚴重度': '明確錯誤', '處理': '待管理者'}
        both = {'2026/11/02': {'8299': [{'category': 'watch_watch', 'note': ''}], '5289': [{'category': 'watch_watch', 'note': ''}]}}
        self.assertEqual(week.verify_rows([row], both, days)[0]['result'], '通過')
        one = {'2026/11/02': {'8299': [{'category': 'watch_watch', 'note': ''}], '5289': [{'category': 'watch_avoid', 'note': ''}]}}
        got = week.verify_rows([row], one, days)[0]
        self.assertEqual(got['result'], '未通過')
        self.assertIn('5289', got['why'])


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



class WeeklyNotifyAndInfra(unittest.TestCase):
    """2026/11/14：Google 服務故障不算退步、每週結果做成 LINE 卡片（notify.build_card）。"""

    def test_infra_failure_detection(self):
        import workflow_steps as ws
        self.assertTrue(ws.is_infra_failure(1, 'RuntimeError: Gemini 連續重試失敗（assess-json-1）：HTTP 503'))
        self.assertFalse(ws.is_infra_failure(3, 'Gemini 連續重試失敗 HTTP 503'))   # 結束代碼 3 是重播自己的結論
        self.assertFalse(ws.is_infra_failure(1, 'KeyError: x'))

    def test_card_infra_week_asks_to_rerun(self):
        import notify
        result = {'merged': False, 'verdict': '重播遇到 Google 服務暫時故障，未合併',
                  'rows': [{'item': '守門', 'result': '通過'}, {'item': '離線契約', 'result': '通過'}]}
        replays = {'requests': 361, 'results': [
            {'date': '2026/11/09', 'role': '本週', 'ok': False, 'infra': True, 'exit_code': 1, 'requests': 65},
            {'date': '2026/11/10', 'role': '本週', 'ok': True, 'exit_code': 0, 'requests': 60}]}
        card = notify.build_card('2026/11/13', result, {'pipeline_changed': True}, replays, {'checks': []}, ['改了一條規則'])
        self.assertEqual([d['status'] for d in card['days']], ['infra', 'pass'])
        self.assertEqual(len(card['asks']), 1)
        self.assertIn('Google', card['asks'][0]['question'])
        self.assertTrue(card['reportUrl'].endswith('docs/weekly-review/2026-11-13/報告.md'))

    def test_card_merged_week_asks_nothing(self):
        import notify
        result = {'merged': True, 'verdict': '已合併', 'rows': [{'item': '守門', 'result': '通過'}, {'item': '離線契約', 'result': '通過'}]}
        replays = {'results': [{'date': '2026/11/09', 'role': '本週', 'ok': True, 'exit_code': 0}]}
        card = notify.build_card('2026/11/13', result, {}, replays, {'checks': [{'result': '通過'}]}, [], merged_sha='abcdef1234')
        self.assertEqual((card['merged'], card['asks'], card['mergedSha']), (True, [], 'abcdef123'))
        self.assertIn('已合併', card['headline'])

if __name__ == '__main__':
    unittest.main()
