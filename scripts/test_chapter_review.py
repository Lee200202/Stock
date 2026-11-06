"""郵件①盤勢總覽與③教學重點的複核（管理者 2026/11/06：每日與每週都要審兩章的品質）的離線契約。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
sys.path.insert(0, str(ROOT / 'scripts' / 'weekly_review'))
import chapters  # noqa: E402
import week  # noqa: E402

FILLER = '今天的節目先講大盤，再講個股。' * 400   # 讓原稿超過 5000 字
RAW = FILLER + '今天最多跌3,737點。季線立抖，46,400多。如果股票跌得離季線很遠，叫做負乖離過大。'
ARTICLE = """文章標題：測試！

① 盤勢總覽重點整理

• 加權指數早盤最多跌3,737點，季線約46,400多點，指數跌離季線、負乖離過大，講者預期會漲回季線附近，下週偏多操作。
• 指數拉回到47,500點以上。

② 會員操作紀錄與持股明細

②-1 當日明確說明之買入／賣出紀錄

本支影片未說明當日具體買賣紀錄。

③ 分析師操作邏輯與教學重點

本集未整理出可引用的操作教學。"""


class Parse(unittest.TestCase):
    def test_points_are_split_per_chapter_and_empty_chapter_is_empty(self):
        got = chapters.chapter_points(ARTICLE)
        self.assertEqual(len(got['macro']), 2)
        self.assertEqual(got['lessons'], [])

    def test_mechanical_hints(self):
        kinds = [(d['code'], d['type']) for d in chapters.chapter_diffs(ARTICLE, RAW)]
        self.assertIn(('③', '章節空白'), kinds, '整章空白是最嚴重的錯（10/29）')
        self.assertIn(('①', '章節點數不足'), kinds)
        self.assertIn(('①', '章節數字不在原稿'), kinds, '47,500 不在原稿')
        self.assertIn(('①', '章節過短'), kinds)
        nums = [d for d in chapters.chapter_diffs(ARTICLE, RAW) if d['type'] == '章節數字不在原稿']
        self.assertEqual(len(nums), 1, '3,737 與 46,400 去掉千分位後都在原稿，不算')

    def test_short_source_does_not_demand_three_points(self):
        kinds = [d['type'] for d in chapters.chapter_diffs(ARTICLE, '今天最多跌3,737點。季線立抖，46,400多。')]
        self.assertNotIn('章節點數不足', kinds)

    def test_missing_numbers_ignores_single_digits_and_commas(self):
        self.assertEqual(chapters.missing_numbers('9點出頭跌3,737點', '早上9點跌3737'), [])
        self.assertEqual(chapters.missing_numbers('VIX 59.08', 'VIX 59'), ['59.08'])


class TodayPlan(unittest.TestCase):
    """2026/11/06 的章節修正計畫：①③各 6 點、每句引句都是原稿原文（原稿不在 repo，這裡只驗格式與規格）。"""
    def test_plan_shape(self):
        plan = json.loads((ROOT / 'scripts/repairs/2026-11-06-points-review.json').read_text(encoding='utf-8'))
        points = plan['public_points_review']['points']
        macro = [p for p in points if p['kind'] != 'view']
        lessons = [p for p in points if p['kind'] == 'view']
        self.assertGreaterEqual(len(macro), 3)
        self.assertGreaterEqual(len(lessons), 3)
        for p in points:
            self.assertTrue(p['quotes'], p['text'][:20])
            self.assertGreaterEqual(len(p['text']), chapters.SHORT['lessons' if p['kind'] == 'view' else 'macro'], p['text'][:20])
            self.assertEqual(chapters.missing_numbers(p['text'], ' '.join(p['quotes'])), [], p['text'][:20])
        text = json.dumps(plan, ensure_ascii=False)
        for word in ('簡訊', '會員通知', '盤中通知', '02-2577'):
            self.assertNotIn(word, text)


class Freeze(unittest.TestCase):
    def run_freeze(self, reading):
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'raw.txt').write_text(RAW, encoding='utf-8')
            Path(d, 'reading.json').write_text(json.dumps(reading, ensure_ascii=False), encoding='utf-8')
            env = dict(os.environ, PYTHONUTF8='1', GOOGLE_SHEETS_SERVICE_ACCOUNT='')
            return subprocess.run([sys.executable, str(ROOT / 'scripts/daily_review/freeze_reading.py'), str(Path(d, 'reading.json'))],
                                  cwd=ROOT / 'scripts/daily_review', capture_output=True, text=True, encoding='utf-8', env=env)

    def base(self, **kw):
        r = {'date': '2026/11/06', 'rulings_read': ['docs/daily-review/裁示紀錄.md'],
             'stocks': [{'code': '2330', 'name': '台積電', 'accept': ['holdings'], 'quote': '原句'}]}
        r.update(kw)
        return r

    def test_chapters_are_required(self):
        p = self.run_freeze(self.base())
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('chapters', p.stdout + p.stderr)

    def test_chapter_quotes_must_be_in_raw(self):
        bad = self.base(chapters={'macro': [{'topic': '位階', 'quote': '指數拉回到47,500點以上'}], 'lessons': []})
        self.assertNotEqual(self.run_freeze(bad).returncode, 0)
        good = self.base(chapters={'macro': [{'topic': '位階', 'quote': '季線立抖，46,400多'}],
                                   'lessons': [{'topic': '負乖離', 'quote': '如果股票跌得離季線很遠，叫做負乖離過大'}]})
        p = self.run_freeze(good)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class ReplayChapters(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('GEMINI_API_KEY', 'offline')
        os.environ.setdefault('SPREADSHEET_ID', 'offline')
        import replay_extract
        self.rx = replay_extract

    def signals(self):
        mk = lambda kind, text, **kw: dict({'kind': kind, 'text': text, '_evidence_verified': True}, **kw)
        return {'market': [mk('level', '跌到年線，季線約46,400多點'), mk('volume', '成交量1.11兆'),
                           mk('view', '負乖離過大會漲回季線'), mk('view', '重複的點', _duplicate_point=True)]}

    def test_chapter_keys_score_after_inference(self):
        ok, report = self.rx.compare(self.signals(), {'chapter_min': {'macro': 2, 'lessons': 1},
                                                      'chapter_required': [{'chapter': 'macro', 'topic': '量能', 'any_of': ['1.11兆']}],
                                                      'chapter_must_not': [{'chapter': 'lessons', 'phrase': '一定漲停'}]})
        self.assertTrue(ok, report)
        ok, report = self.rx.compare(self.signals(), {'chapter_min': {'lessons': 2}})
        self.assertFalse(ok, '重複的點不算')
        ok, _ = self.rx.compare(self.signals(), {'chapter_required': [{'chapter': 'lessons', 'any_of': ['1.11兆']}]})
        self.assertFalse(ok, '要在指定的那一章')


class WeeklyChapters(unittest.TestCase):
    def row(self, kind, code='③', site='', handling='待套用'):
        return {'日期': '2026/11/06', '代號': code, '名稱': '③教學重點', '差異類型': kind, '網站分類': site,
                '嚴重度': '明確錯誤', '處理': handling}

    def test_countable_chapter_errors_are_verified_from_the_article(self):
        self.assertEqual(week.judge(self.row('章節空白'), [], ARTICLE)[0], '未通過')
        self.assertEqual(week.judge(self.row('章節空白', code='①'), [], ARTICLE)[0], '通過')
        self.assertEqual(week.judge(self.row('章節點數不足', code='①'), [], ARTICLE)[0], '未通過')

    def test_semantic_chapter_errors_need_the_point_gone_and_a_fix(self):
        stem = '指數拉回到47,500點以上'
        self.assertEqual(week.judge(self.row('章節幻覺', code='①', site=stem), [], ARTICLE)[0], '未通過')
        self.assertEqual(week.judge(self.row('章節幻覺', code='①', site='早已改掉的那一句', handling='已修正'), [], ARTICLE)[0], '通過')
        self.assertEqual(week.judge(self.row('章節漏主題', code='①', site='', handling='待管理者'), [], ARTICLE)[0], '需人工')
        self.assertEqual(week.judge(self.row('章節空白'), [], '')[0], '需人工', '讀不到文章不能當通過')


class SecondRoundApply(unittest.TestCase):
    """2026/11/06：同一天第二輪推到同一分支，工作流程又把上午的章節計畫重套一次，蓋掉下午核對過的兩章。"""
    def test_only_plans_changed_in_this_push_are_applied(self):
        import yaml
        wf = yaml.safe_load((ROOT / '.github/workflows/daily-review-apply.yml').read_text(encoding='utf-8'))
        step = next(s for s in wf['jobs']['apply']['steps'] if s.get('id') == 'apply')
        self.assertEqual(step['env'].get('BEFORE'), '${{ github.event.before }}')
        run = step['run']
        self.assertIn('git diff --name-only "$BEFORE" "$GITHUB_SHA"', run)
        skip = run.index('for F in PLAN POINTS NOTICE')
        self.assertLess(skip, run.index('apply_reviewed_points.py'), '要在套用之前決定略過')
        self.assertLess(skip, run.index('apply_source_review.py'))

    def test_skip_logic_in_bash(self):
        import shutil
        bash = shutil.which('bash')
        if not bash:
            self.skipTest('沒有 bash')
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(['git', 'init', '-q', d], check=True)
            env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')
            rep = Path(d, 'scripts/repairs'); rep.mkdir(parents=True)
            (rep / '2026-11-06-points-review.json').write_text('{}', encoding='utf-8')
            subprocess.run(['git', '-C', d, 'add', '-A'], check=True, env=env)
            subprocess.run(['git', '-C', d, 'commit', '-qm', 'r1'], check=True, env=env)
            before = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            (rep / '2026-11-06-source-review.json').write_text('{}', encoding='utf-8')
            subprocess.run(['git', '-C', d, 'add', '-A'], check=True, env=env)
            subprocess.run(['git', '-C', d, 'commit', '-qm', 'r2'], check=True, env=env)
            sha = subprocess.run(['git', '-C', d, 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            wf = (ROOT / '.github/workflows/daily-review-apply.yml').read_text(encoding='utf-8')
            block = wf[wf.index('          # 2026/11/06：同一天第二輪'):wf.index('          if [ -f "$NOTICE" ]; then')]
            script = ('DAY=2026/11/06\nPLAN="scripts/repairs/${DAY//\\//-}-source-review.json"\n'
                      'POINTS="scripts/repairs/${DAY//\\//-}-points-review.json"\nNOTICE="scripts/repairs/${DAY//\\//-}-notice-reason.json"\n'
                      + '\n'.join(l[10:] for l in block.splitlines()) + '\necho "PLAN=$PLAN"; echo "POINTS=$POINTS"\n')
            out = subprocess.run([bash, '-c', script], cwd=d, capture_output=True, text=True,
                                 env=dict(env, BEFORE=before, GITHUB_SHA=sha)).stdout
            self.assertIn('PLAN=scripts/repairs/2026-11-06-source-review.json', out, '這次新增的計畫要套用')
            self.assertIn('POINTS=/nonexistent/POINTS', out, '前一輪的章節計畫不重套')
            out = subprocess.run([bash, '-c', script], cwd=d, capture_output=True, text=True,
                                 env=dict(env, BEFORE='0' * 40, GITHUB_SHA=sha)).stdout
            self.assertIn('POINTS=scripts/repairs/2026-11-06-points-review.json', out, '新分支第一次推送全部套用')


if __name__ == '__main__':
    unittest.main()
