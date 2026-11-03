"""複核修正過的通知理由（解析明細 reviewed 標記）在簡訊重新解析、沿用明細時都要原樣保留（2026/11/03 晶心科）。"""
import json, os, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ.setdefault('SPREADSHEET_ID', 'offline'); os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as pl  # noqa: E402
import apply_notice_reason_fix as notice  # noqa: E402

FIXED = '晶心科現金增資認股（認購價195元）今日取得股票，持有者全部以市價賣出；賣在270幾元，每張賺快80元。'
DETAIL = json.dumps([{'name': '晶心科', 'code': '6533', 'dir': '賣出', 'price': '', 'priceText': '未說明',
                      'reason': FIXED, 'reviewed': '通知理由修正 2026/11/03'}], ensure_ascii=False)


class ReviewedSmsReason(unittest.TestCase):
    def test_reparse_keeps_the_reviewed_reason(self):
        items = [{'name': '晶心科', 'code': '6533', 'action': '賣出', 'note': '模型重新產生的通知口吻理由'}]
        out = pl.keep_admin_edited_notes(items, 'CMONEY-1', DETAIL, {('CMONEY-1', '6533', '賣出'): FIXED})
        self.assertEqual(out[0]['note'], FIXED)
        self.assertTrue(out[0]['reviewed'])

    def test_a_different_direction_is_not_locked(self):
        items = [{'name': '晶心科', 'code': '6533', 'action': '買入', 'note': '新的買入理由'}]
        out = pl.keep_admin_edited_notes(items, 'CMONEY-1', DETAIL, {})
        self.assertEqual(out[0]['note'], '新的買入理由')

    def test_reuse_round_trip_keeps_text_and_marker(self):
        ok, items = pl.load_saved_sms_items(DETAIL)
        self.assertTrue(ok)
        self.assertEqual(items[0]['note'], FIXED)
        again = json.loads(pl.serialize_sms_items(items))[0]
        self.assertEqual(again['reviewed'], '通知理由修正 2026/11/03')

    def test_tool_reads_a_date_cell_stored_as_a_serial_number(self):
        self.assertEqual(notice.norm_day('46329'), '2026/11/03')
        self.assertEqual(notice.norm_day('2026/11/3'), '2026/11/03')


if __name__ == '__main__':
    unittest.main()
