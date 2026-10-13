"""Offline regression: completed trade != today's trade; quarantine is local."""
import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'test')
os.environ.setdefault('GEMINI_API_KEY', 'AIzaSyDUMMY_local_import_only_0000000000')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as pl
pl._CODE_MAP = {'3443': '創意', '3533': '嘉澤'}
pl._CODE_MAP_FULL = True

cases = [
    ('放假前的最後一個交易日，我聖輝賣了。今天跌停。', '聖輝', False),
    ('賣出的鴻海，10月6號260元。今天239元。', '鴻海', False),
    ('世芯-KY上禮拜四賣掉了，今天3900元。', '世芯-KY', False),
    ('鴻海賣掉了。', '鴻海', False),
    ('賣出的奇鋐，現在跌下來了。', '奇鋐', False),
    ('好，啊你今天才在賣鴻海，你們還記不記得當時260全部賣出？', '鴻海', False),
    ('今天不能賣鴻海。', '鴻海', False),
    ('今天早上我賣出鴻海。', '鴻海', True),
    ('今天我告訴會員鴻海獲利賣出。', '鴻海', True),
    ('今天鴻海要不要賣？', '鴻海', False),
    ('今天外資賣出鴻海。', '鴻海', False),
]
for text, name, want in cases:
    assert bool(pl.explicit_today_trades(text, [name], 'sell')) == want, text
assert pl.explicit_today_trades('今天我買進勤誠。', ['勤誠'], 'buy')
assert pl.plain_current_stance('連電', '連電腦軟體我都不推薦。', names=['連電']) is None
assert pl.plain_current_stance('連電', '連電不要買。', names=['連電'])
old = {'name': '聖輝', 'when': 'today', 'time_evidence': '今天跌停。'}
tx = '放假前的最後一個交易日，我聖輝賣了。今天跌停。'
pl.settle_trade_time(old, ['今天跌停。'], tx, pl._ev_norm(tx), '2026/10/13', ['聖輝'], 'sell')
assert old['when'] == 'unknown'

with patch.object(pl, 'note_decision'):
    signals = {'watch_avoid': [{'name': '創意', 'code': '3443', 'reason': '老師說有傳聞，現在要買嗎？'}],
               'holdings': [{'name': '嘉澤', 'code': '3533', 'note': '嘉澤仍持有，未提出新的出場指示。', 'evidence': ['我的股票嘉澤。']}],
               'market': [{'kind': 'view', 'text': '保留原來章節'}]}
    pl.quarantine_public_rows(signals, '我的股票嘉澤。')
    assert not signals['watch_avoid'] and len(signals['holdings']) == 1
    assert signals['_publication_quarantine'][0]['code'] == '3443'
    assert signals['market'][0]['text'] == '保留原來章節'
    pl.quarantine_public_rows(signals, '我的股票嘉澤。')
    assert len(signals['_publication_quarantine']) == 1
    _, errors, _ = pl.quality_overview(signals, '長篇原稿' * 4000)
    assert any('盤勢只有' in e for e in errors), 'global article gate must remain'
print('10/13 publication regression: 13 checks passed, no model/network calls')
