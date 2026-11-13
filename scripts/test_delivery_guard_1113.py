"""2026/11/13：套用修正的「沒有重寄」核對（delivery_guard.py），不連試算表。

當天 12:02 的 daily-review-apply 已寫好三列、讀回一致、網站刷新完成，卻因同時間管理者在 LINE 回覆裁示
（LINE 寄送帳本多了列）被判「同期寄送帳本變動」，後面的股名學習與①③章節步驟也被跳過。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from delivery_guard import resend_problems, status_regressed  # noqa: E402

DAY = '2026/11/13'
HEAD = ['訊息ID', '種類', '日期', '內容版本', '收件者', '狀態', '嘗試次數', '最後錯誤', '更新時間', '服務接受時間']
LINE_HEAD = ['訊息ID', '日期', '收件者', '狀態', '嘗試次數', '重試金鑰', '最後錯誤', '更新時間', '接受時間', '請求ID']


def row(mid, who, acc='', status='accepted'):
    return [mid, mid.split('|')[0], mid.split('|')[-1], 'v1', who, status, '1', '', '12:11', acc]


import unittest  # noqa: E402


class DeliveryGuard(unittest.TestCase):
    def test_1113_scenarios(self):
        before = [HEAD, row('daily|2026/11/12', 'a@x', '2026/11/12 12:05'), row('sms|A1', 'a@x', '12:20')]
        # 1. 今天的情形：別的種類的信、別人的列狀態更新、LINE 對話新增 → 不是重寄
        after = [HEAD, row('daily|2026/11/12', 'a@x', '2026/11/12 12:05'), row('sms|A1', 'a@x', '12:21', 'retry'),
                 row('sms|A2', 'b@x', '12:23')]
        assert resend_problems(before, after, DAY) == []
        line_before = [LINE_HEAD, ['ruling|R1', DAY, 'U1', 'accepted', '1', 'k', '', '12:02', '12:02', 'q']]
        line_after = line_before + [['ruling|R2', DAY, 'U1', 'accepted', '1', 'k2', '', '12:06', '12:06', 'q2']]
        assert resend_problems(line_before, line_after, DAY, 'LINE 寄送帳本') == []
        # 2. 套用期間整理信第一次寄出（12:10）→ 正常，不是重寄
        first = after + [row('daily|' + DAY, 'a@x', DAY + ' 12:10'), row('daily|' + DAY, 'b@x', DAY + ' 12:10')]
        assert resend_problems(after, first, DAY) == []
        assert not status_regressed('待寄送', '已寄送') and not status_regressed('', '寄送中')
        # 3. 已寄出的人又被寄一次（接受時間變了或多一列）→ 要擋
        again = [r[:] for r in first]
        again[-1][-1] = DAY + ' 12:40'
        assert resend_problems(first, again, DAY) and 'b@x'[:3] in resend_problems(first, again, DAY)[0]
        dup = first + [row('daily|' + DAY, 'a@x', DAY + ' 12:45')]
        assert any('2 列' in p for p in resend_problems(first, dup, DAY))
        # 4. 寄送狀態從已寄退回待寄送 → 要擋（下一棒會再寄）
        assert status_regressed('已寄送', '待寄送') and status_regressed('部分寄送', '')
        assert not status_regressed('已寄送', '已寄送')
        # 5. 沒有訊息ID／收件者欄（表頭不認得）→ 不判斷，不誤報
        assert resend_problems([['x']], [['x'], ['y']], DAY) == []


if __name__ == '__main__':
    unittest.main()
