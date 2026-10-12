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
assert pl.strip_editorial_wrappers('拉回季線找買點。因「回測季線」故引用這些段落。') == '拉回季線找買點。'
assert pl.strip_editorial_wrappers('成交量放大，因此買盤增加。') == '成交量放大，因此買盤增加。'
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
    pl._CODE_MAP = {'3661': '世芯-KY', '4916': '事欣科'}
    source = '世芯KY目前仍持有。' + '其他技術說明。' * 40 + '四新科上半年EPS5.49元。'
    at = source.index('四新科')
    index = {'3661': {0, at}, '4916': {at}}
    notes = {'holdings': [
        {'name': '世芯-KY', 'code': '3661', 'note': '仍持有。上半年EPS為5.49。'},
        {'name': '事欣科', 'code': '4916', 'note': '上半年EPS為5.49。'}]}
    with patch.object(pl, '_mention_index', return_value=index), \
         patch.object(pl, 'source_inventory', return_value=[]):
        pl.strip_foreign_price_claims(notes, source)
    assert notes['holdings'][0]['note'] == '仍持有。'
    assert notes['holdings'][1]['note'] == '上半年EPS為5.49。'
    shared = '世芯KY上半年EPS5.49元。四新科上半年EPS5.49元。'
    second = shared.index('四新科')
    assert pl._price_attribution(shared, {'3661': {0, second}, '4916': {second}},
                                 '3661', '5.49', {'3661', '4916'})[0] == 'ok'
finally:
    pl._CODE_MAP = old

print('Source priority: protected holdings, trade preservation, date isolation, idempotence and named refusal passed')

class PriorityBook:
    def __init__(self):
        self.sheets = {
            '操作紀錄': Sheet([head, [day, '4916', '觀望注意', pl.MANUAL_ENTRY_PREFIX + '20261005', '事欣科']]),
            '會員持股': Sheet([['日期', '股票名稱', '代號', '來源影片ID'],
                             [day, '事欣科', '4916', 'video-id'],
                             ['2026/10/06', '事欣科', '4916', 'video-id'],
                             [day, '力積電', '6770', 'CMONEY-123']])}
    def worksheet(self, name):
        return self.sheets[name]

priority = PriorityBook()
assert pl.apply_sms_priority(priority, [day])['dropped'] == 1
assert len(priority.sheets['操作紀錄'].rows) == 2
assert len(priority.sheets['會員持股'].rows) == 3
assert priority.sheets['會員持股'].rows[1][0] == '2026/10/06'
assert pl.apply_sms_priority(priority, [day])['dropped'] == 0
bad = {'market': [{'kind': 'view', 'text': '股價出現創新高卻未再創新高的背離現象。', '_evidence_verified': True}]}
assert any('指標主詞' in x for x in pl.quality_overview(bad)[1])
good = {'market': [{'kind': 'view', 'text': '股價創新高而技術指標未同步創新高。', '_evidence_verified': True}]}
assert not pl.quality_overview(good)[1]
print('Cross-table manual priority and divergence subject checks passed')
