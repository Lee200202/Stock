"""Offline checks for source ownership and cross-table category priority."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'test')
os.environ.setdefault('GEMINI_API_KEY', 'AIzaSyDUMMY_local_import_only_0000000000')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as pl

class Sheet:
    def __init__(self, rows):
        self.rows = rows
    def get_all_values(self):
        return [r[:] for r in self.rows]
    def delete_rows(self, number):
        self.rows.pop(number - 1)

class Book:
    def __init__(self, rows):
        self.sheet = Sheet(rows)
    def worksheet(self, name):
        assert name == '操作紀錄'
        return self.sheet

head = ['日期', '代號', '方向', '來源影片ID', '股票名稱']
day = '2026/10/05'
rows = [head,
        [day, '4916', '觀望注意', 'video-id', '事欣科'],
        [day, '4916', '買入', 'video-id', '事欣科'],
        [day, '4916', '觀望注意', 'CMONEY-123', '事欣科'],
        [day, '4916', '觀望不碰', pl.MANUAL_ENTRY_PREFIX + '123', '事欣科'],
        ['2026/10/02', '4916', '觀望注意', 'video-id', '事欣科'],
        [day, '2317', '觀望注意', 'video-id', '鴻海']]
book = Book(rows)
assert pl._drop_video_watch_covered_by_holds(book, {(day, '4916')}) == 1
assert len(book.sheet.rows) == 6
assert book.sheet.rows[1][2] == '買入'
assert pl._drop_video_watch_covered_by_holds(book, {(day, '4916')}) == 0

old = pl._CODE_MAP
try:
    pl._CODE_MAP = {'5347': '世界'}
    row = {'code': '5347', 'name': '世界', 'aliases': ['世界先進']}
    quote = '我不可能叫你現在買世界先進吧。你反而要注意這一支股票。'
    assert pl._named_current_prohibition(row, quote)
    assert not pl._named_current_prohibition(row, '昨天我不可能叫你現在買世界先進。')
    assert not pl._named_current_prohibition(row, '我不可能叫你現在買世界先進季線以上，回測季線以下可以買。')
    signals = {'watch_avoid': [dict(row, reason='目前不追買。', evidence=[quote])]}
    with patch.object(pl, '_verdict_known', return_value=({}, set(), {})), \
         patch.object(pl, '_verdict_cache', return_value={}), \
         patch.object(pl, 'call_gemini', side_effect=AssertionError('明確禁買不應呼叫模型')):
        pl._adjudicate_classes(None, signals, quote, '2026/10/06', {}, {}, 'on')
    assert signals['_verdicts']['5347']['tier'] == '直接歸類'
    assert signals['_verdicts']['5347']['cls'] == 'watch_avoid'
finally:
    pl._CODE_MAP = old

print('Source priority: protected holdings, trade preservation, date isolation, idempotence and named refusal passed')
