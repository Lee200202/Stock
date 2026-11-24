"""Mirror the public valuation archive into the project's Google Sheets database."""
import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import time

INDEX = '估值歷史索引'
TABS = {'A': '估值歷史 A', 'B': '估值歷史 B'}
HEADER = ['代號', '市場', '起始日', '最後交易日', '交易日數', 'gzip/Base64']
CHUNK = 40000


def encode(doc):
    history = doc['history']
    public = {key: doc[key] for key in ('code', 'market', 'years', 'from', 'to', 'history')}
    payload = json.dumps(public, ensure_ascii=False, separators=(',', ':')).encode()
    value = base64.b64encode(gzip.compress(payload, mtime=0)).decode()
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    return [doc['code'], doc['market'], doc['from'], doc['to'], len(history), *parts]


def service_credentials():
    value = os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'].strip()
    if not value.startswith('{'):
        value = base64.b64decode(value + '=' * (-len(value) % 4)).decode()
    return json.loads(value)


def retry(call, *args, **kwargs):
    from gspread.exceptions import APIError
    for attempt in range(5):
        try:
            return call(*args, **kwargs)
        except APIError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(2 ** (attempt + 1))


def sync(directory):
    import gspread
    files = [path for path in sorted(directory.glob('*.json')) if path.stem != '_meta']
    rows = [encode(json.loads(path.read_text(encoding='utf-8'))) for path in files]
    if not rows or any(row[4] < 1 for row in rows):
        raise ValueError('估值檔沒有有效逐日資料')
    digest = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
    book = gspread.service_account_from_dict(service_credentials()).open_by_key(os.environ['SPREADSHEET_ID'])
    sheets = {sheet.title: sheet for sheet in retry(book.worksheets)}
    index = sheets.get(INDEX)
    if index is None:
        index = retry(book.add_worksheet, INDEX, rows=4, cols=5)
    active_row = (retry(index.get, 'A2:E2') or [[]])[0]
    if len(active_row) >= 5 and active_row[4] == digest:
        print(f'試算表已同步 {len(rows)} 檔；資料至 {active_row[3]}')
        return
    slot = 'B' if active_row and active_row[0] == 'A' else 'A'
    title = TABS[slot]
    width = max(len(row) for row in rows)
    table = [HEADER + [f'續段 {i}' for i in range(1, width - len(HEADER) + 1)]]
    table.extend(row + [''] * (width - len(row)) for row in rows)
    target = sheets.get(title)
    if target is None:
        target = retry(book.add_worksheet, title, rows=len(table), cols=width)
    elif target.row_count < len(table) or target.col_count < width:
        retry(target.resize, rows=max(len(table), target.row_count), cols=max(width, target.col_count))
    for start in range(0, len(table), 40):
        batch = table[start:start + 40]
        result = retry(target.update, batch, f'A{start + 1}', raw=True)
        if result.get('updatedRows') != len(batch):
            raise RuntimeError('試算表批次寫入未完成，保留上次快照')
    for i in sorted({2, len(table) // 2, len(table)}):
        readback = (retry(target.get, f'{i}:{i}') or [[]])[0]
        expected = table[i - 1].copy()
        while expected and expected[-1] == '':
            expected.pop()
        if list(map(str, readback)) != list(map(str, expected)):
            raise RuntimeError('試算表讀回不符，保留上次快照')
    last = max(row[3] for row in rows)
    retry(index.update, [
        ['資料分頁', '股票數', '最早資料日', '最新資料日', '資料指紋'],
        [slot, len(rows), min(row[2] for row in rows), last, digest],
    ], 'A1:E2', raw=True)
    print(f'試算表已同步 {len(rows)} 檔；資料至 {last}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    sync(parser.parse_args().directory)
