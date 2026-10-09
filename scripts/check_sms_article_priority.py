"""Same-day notice decides article category even when a later transcript disagrees."""
import os

os.environ.setdefault('SPREADSHEET_ID', 'test-sms-priority')
from replay_extract import pl


class Sheet:
    def get_all_values(self):
        return [
            ['日期', '代號', '股票名稱', '方向', '價位說明', '理由摘錄', '來源影片ID'],
            ['2026/10/08', '3661', '世芯-KY', '賣出', '4400 元', '全數賣出，獲利了結。', 'CMONEY-101'],
            ['2026/10/08', '1590', '亞德客-KY', '買入', '1260 元', '買入。', 'CMONEY-101'],
        ]


class Book:
    def worksheet(self, name):
        assert name == '操作紀錄'
        return Sheet()


signals = {
    'holdings': [{'code': '3661', 'name': '世芯-KY', 'note': '仍持有', '_evidence_verified': True}],
    'watch_watch': [{'code': '1590', 'name': '亞德客-KY', 'reason': '年線盤整八天、MACD 翻揚。',
                     '_evidence_verified': True}],
    'watch_avoid': [{'code': '8299', 'name': '群聯', 'reason': '等待回測季線。'}],
}
pl.sms_first_article_records(Book(), signals, '2026/10/08')
article = signals['_article_records']
assert [r['code'] for r in article['sell']] == ['3661']
assert [r['code'] for r in article['buy']] == ['1590']
assert [r['code'] for r in article['watch_avoid']] == ['8299']
assert all(r['code'] != '3661' for r in article['holdings'])
assert all(r['code'] != '1590' for r in article['watch_watch'])
chapter = pl.render_record_chapter(signals, '2026/10/08')
assert '世芯-KY | 3661 | 賣出' in chapter and '亞德客-KY | 1590 | 買入' in chapter
assert '4400' not in chapter and '1260' not in chapter
assert pl._protected_nonvideo_row('MANUALENTRY-123')
print('SMS first article: conflicting transcript categories removed, public prices hidden, manual rows protected')
