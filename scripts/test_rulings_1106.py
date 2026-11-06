"""管理者 2026/11/06 同意週檢討 Q1–Q5 的離線契約：假設句範圍、補列當日成交、通知持股列說明、問卷提示詞。"""
import json
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('GEMINI_API_KEY', 'offline')
os.environ.setdefault('SPREADSHEET_ID', 'offline')
import pipeline as pl  # noqa: E402
import apply_source_review as sr  # noqa: E402
import apply_notice_reason_fix as nr  # noqa: E402


class HypotheticalScope(unittest.TestCase):
    def test_unrelated_if_does_not_cancel_a_stated_trade(self):
        s = '啊再來，如果有的話，多拖一天321，就這樣子，倒數計時，張震今天告訴你買買買買，我今天買世芯-KY，2,950塊，2,950塊世芯-KY'
        self.assertTrue(pl.execution_statement(s, 'buy'))

    def test_conditionals_stay_hypothetical(self):
        for s in ('如果跌到2900，我今天就買世芯-KY', '如果跌破季線，我今天會買', '如果我今天買台積電，你們會不會罵我',
                  '如果有的話，如果大盤再跌，我今天買', '我就說，我要是昨天買，今天也是被套啦，我今天買，搞不好明天被套啦'):
            self.assertFalse(pl.execution_statement(s, 'buy'), s)


class ReviewedTrade(unittest.TestCase):
    def plan(self):
        return json.loads((ROOT / 'scripts/repairs/2026-11-05-source-review.json').read_text(encoding='utf-8'))

    def test_1105_plan_is_valid(self):
        p = self.plan()
        self.assertEqual(sr.reviewed_trade_problems(p['items'][0], p), [])

    def test_guards(self):
        p = self.plan()
        item = p['items'][0]
        bad = lambda **kw: sr.reviewed_trade_problems(dict(item, **kw), p)
        self.assertTrue(bad(action='update'), '只准新增')
        self.assertTrue(bad(trade_review=dict(item['trade_review'], price='2980')), '價位要出自原句')
        self.assertTrue(bad(trade_review=dict(item['trade_review'], event_date='2026/11/04')), '日期要是當天')
        self.assertTrue(bad(trade_review=dict(item['trade_review'], price_quote='如果跌到2950，我就買', )), '要本人今天成交')
        self.assertTrue(bad(category='sell'), '方向要和原句一致')
        self.assertTrue(bad(trade_review=dict(item['trade_review'], authorization='')), '要有管理者授權')

    def test_other_trade_edits_still_refused(self):
        p = {'date': '2026/11/05', 'items': [{'code': '3661', 'name': '世芯-KY', 'category': 'buy', 'action': 'insert',
                                              'text': 'x', 'quotes': ['y']}]}
        tables = {'操作紀錄': [['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']]}
        with self.assertRaises(ValueError):
            sr.make_changes(tables, p, {'3661': '世芯-KY'})


class NoticeHoldings(unittest.TestCase):
    def test_holdings_rows_are_targeted(self):
        self.assertEqual(nr.target({'sheet': '會員持股'})[:2], ('會員持股', '說明重點'))
        self.assertEqual(nr.target({})[:2], ('操作紀錄', '理由摘錄'))
        with self.assertRaises(ValueError):
            nr.target({'sheet': '寄送帳本'})

    def test_1106_plan_keeps_old_text_out(self):
        plan = json.loads((ROOT / 'scripts/repairs/2026-11-06-notice-reason.json').read_text(encoding='utf-8'))
        item = plan['items'][0]
        self.assertNotIn('previous_reason', item)
        self.assertFalse(any(b in item['text'] for b in nr.BANNED))
        trades = [['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID'], ['2026/11/06', '世芯-KY', '3661', '續抱', '舊', 'CMONEY-187886120']]
        sms = [['文章ID', '解析明細'], ['187886120', json.dumps([{'code': '3661', 'dir': '會員持股', 'reason': '舊'},
                                                                {'code': '2330', 'dir': '買入', 'reason': 'x'}], ensure_ascii=False)]]
        ti, trow, si, _srow, _d, mine = nr.locate(trades, sms, plan, item)
        self.assertEqual((ti, si, mine['dir']), (2, 2, '會員持股'))


class QuestionnairePrompt(unittest.TestCase):
    def test_rulings_are_in_the_prompt_without_stock_names(self):
        self.assertIn('本人今天沒有加碼不是否認部位', pl.ADJUDICATE_SYSTEM)
        self.assertIn('在族群一起下跌時被點名', pl.ADJUDICATE_SYSTEM)
        for name in ('勤誠', '亞德客', '創意', '金像電', '世芯'):
            self.assertNotIn(name, pl.ADJUDICATE_SYSTEM)
        self.assertTrue(pl.VERDICT_VERSION.startswith('v43-20261106'))


if __name__ == '__main__':
    unittest.main()
