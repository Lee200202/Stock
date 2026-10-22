"""人工審閱可接受標點差異，不接受字詞或數字改變。"""
import os
os.environ.setdefault('SPREADSHEET_ID', 'test')
os.environ.setdefault('GEMINI_API_KEY', 'AIzaSyDUMMY_local_import_only_0000000000')
from apply_reviewed_points import source_quote

raw = '最高點，就是1545。季線 向上，先確認好。'
assert source_quote(raw, '最高點就是1545') == '最高點，就是1545'
assert source_quote(raw, '季線向上先確認好') == '季線 向上，先確認好'
for quote in ('最高點就是1546', '半年線向上先確認好', 'W底打出來'):
    try:
        source_quote(raw, quote)
    except ValueError:
        pass
    else:
        raise AssertionError('must reject ' + quote)
print('Reviewed source quotations: 5 checks passed')
