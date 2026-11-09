"""管理者依通知方向裁示（apply_notice_ruling.py）的離線契約：通知指令判定、計畫守門、找列、日期補零。

通知句子全是合成的（公開 repo 不放會員通知原文）。不連試算表、不呼叫模型。"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('notice_ruling', ROOT / 'scripts/apply_notice_ruling.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
from common import norm_day  # noqa: E402
import rulings  # noqa: E402

ORDER = '張震-1:甲公司漲停續抱，若想買股者，請買進1234測試，於50元以下買進，已到年線，請買進1234測試！'
OPTIONAL = '張震-1:若想買股者，1234測試可以買，50元以下可留意。'
BLOCK = '張震-1:1234測試不急著買進，條件尚未出現，請耐心等待。'
T = ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']
S = ['文章ID', '發文時間', '原文', '解析明細', '內容指紋']


def plan(**kw):
    item = dict(code='1234', name='測試', notice_source_id='CMONEY-900', article_id='900', notice_sha256=m.sha(ORDER),
                previous_direction='觀望注意', direction='買入', previous_reason_sha256=m.sha('舊理由'), previous_reason_chars=3,
                text='測試在年線附近，講者看好後續回升。', quotes=['測試在年線附近'], rationale='通知明確請買進')
    item.update(kw)
    return dict(date='2026/11/09', authorization='管理者 LINE 裁示 R1', ruling_ids=['R1'], review_basis='依簡訊方向', items=[item])


def tables(day='2026/11/9', direction='觀望注意', extra=None):
    trades = [T, [day, '測試', '1234', direction, '50 元以下', '舊理由', 'CMONEY-900', '1'],
              ['2026/11/09', '別檔', '5678', '買入', '', 'x', 'VID', '1']] + (extra or [])
    detail = [{'name': '測試', 'code': '1234', 'dir': direction, 'reason': '舊理由'}]
    sms = [S, ['900', '2026/11/09 9:40:29', ORDER, json.dumps(detail, ensure_ascii=False), m.sha(ORDER)]]
    return trades, sms


class NoticeSupports(unittest.TestCase):
    def test_explicit_order_supports_buy(self):
        self.assertEqual(m.notice_supports(ORDER, '測試', '1234', '買入', '50 元以下'), (True, ''))

    def test_optional_or_blocked_does_not_support_buy(self):
        self.assertFalse(m.notice_supports(OPTIONAL, '測試', '1234', '買入')[0])
        ok, why = m.notice_supports(BLOCK, '測試', '1234', '買入')
        self.assertFalse(ok)
        self.assertNotIn('不急著', why)                      # 原因不帶通知原文

    def test_price_must_come_from_notice(self):
        self.assertFalse(m.notice_supports(ORDER, '測試', '1234', '買入', '45 元以下')[0])

    def test_stock_must_be_named(self):
        self.assertFalse(m.notice_supports(ORDER, '別檔', '5678', '買入')[0])
        self.assertTrue(m.notice_supports(OPTIONAL, '測試', '1234', '觀望注意')[0])

    def test_broadcast_prefix_is_not_a_stock(self):
        self.assertEqual(m.strip_prefix('張震6GJ-1：請買進'), '請買進')


class PlanGate(unittest.TestCase):
    def test_valid_plan(self):
        m.check_plan(plan(), '2026/11/09')

    def test_needs_admin_authorization(self):
        p = plan(); p['authorization'] = '雲端判斷'
        with self.assertRaises(ValueError):
            m.check_plan(p, '2026/11/09')
        p = plan(); p['ruling_ids'] = []
        with self.assertRaises(ValueError):
            m.check_plan(p, '2026/11/09')

    def test_no_notice_text_or_old_reason_in_public_plan(self):
        for k in ('previous_reason', 'notice_text'):
            p = plan(); p['items'][0][k] = 'x'
            with self.assertRaises(ValueError):
                m.check_plan(p, '2026/11/09')

    def test_text_must_not_leak_channel_words(self):
        for bad in ('依通知買進。', '若想買股者可留意。', '簡訊說要買。'):
            with self.assertRaises(ValueError):
                m.check_plan(plan(text=bad), '2026/11/09')

    def test_direction_must_change_and_source_must_be_notice(self):
        with self.assertRaises(ValueError):
            m.check_plan(plan(direction='觀望注意'), '2026/11/09')
        with self.assertRaises(ValueError):
            m.check_plan(plan(direction='會員持股'), '2026/11/09')     # 不跨表
        with self.assertRaises(ValueError):
            m.check_plan(plan(notice_source_id='VID'), '2026/11/09')


class Locate(unittest.TestCase):
    def test_unpadded_notice_date_is_found(self):
        trades, sms = tables()
        ti, trow, others, si, srow, detail = m.locate(trades, sms, '2026/11/09', plan()['items'][0])
        self.assertEqual((ti, trow['方向'], si, others), (2, '觀望注意', 2, []))

    def test_existing_same_direction_row_is_reported(self):
        trades, sms = tables(extra=[['2026/11/09', '測試', '1234', '買入', '', '影片', 'VID', '1']])
        _, _, others, _, _, _ = m.locate(trades, sms, '2026/11/09', plan()['items'][0])
        self.assertEqual(len(others), 1)

    def test_missing_row(self):
        trades, sms = tables(day='2026/11/08')
        with self.assertRaises(ValueError):
            m.locate(trades, sms, '2026/11/09', plan()['items'][0])


class Dates(unittest.TestCase):
    def test_norm_day_pads_and_reads_serials(self):
        self.assertEqual(norm_day('2026/11/9'), '2026/11/09')
        self.assertEqual(norm_day('2026-11-09'), '2026/11/09')
        self.assertEqual(norm_day('46335'), '2026/11/09')
        self.assertEqual(norm_day('2026/11/09 11:00:00'), '2026/11/09')

    def test_ruling_rows_find_unpadded_notice_row(self):
        trades, _ = tables()
        rows = rulings.site_rows({'操作紀錄': trades}, '2026/11/09', '1234')
        self.assertEqual([(r['category'], r['source_id']) for r in rows], [('watch_watch', 'CMONEY-900')])


class Workflow(unittest.TestCase):
    def test_line_ruling_apply_runs_notice_plan_only_when_committed(self):
        wf = yaml.safe_load((ROOT / '.github/workflows/line-ruling-apply.yml').read_text(encoding='utf-8'))
        steps = wf['jobs']['apply']['steps']
        self.assertEqual(steps[0]['with']['fetch-depth'], 2)
        run = steps[3]['run']
        self.assertIn('apply_notice_ruling.py --date "$DAY" --apply', run)
        self.assertIn('git diff --name-only HEAD~1 HEAD', run)
        self.assertIn('-notice-ruling.json', run)


if __name__ == '__main__':
    unittest.main()
