"""Store public historical valuation in private independent volumes, never the main workbook."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_valuation_sheets import encode, retry, service_credentials


def route(op, payload=None):
    import requests
    response = requests.get(os.environ['APPS_SCRIPT_URL'], params={
        'action': 'backup-archive', 'key': os.environ['ADMIN_KEY'],
        'source': os.environ['SPREADSHEET_ID'], 'op': op,
        'names': json.dumps(payload or {}, separators=(',', ':'))}, timeout=230)
    data = response.json()
    if not data.get('ok'):
        raise RuntimeError('歷史封存服務尚未就緒或請求被拒；主檔未寫入')
    return data


def partitions(directory):
    by_year = {}
    for file in sorted(directory.glob('*.json')):
        if file.stem.startswith('_'):
            continue
        doc = json.loads(file.read_text(encoding='utf-8'))
        years = {}
        for row in doc.get('history', []):
            years.setdefault(row[0][:4], []).append(row)
        for year, history in years.items():
            part = dict(doc, history=history, **{'from': history[0][0], 'to': history[-1][0]})
            by_year.setdefault(year, []).append(encode(part))
    for year, rows in sorted(by_year.items()):
        shard, batch, size = 1, [], 0
        for row in rows:
            n = len(json.dumps(row, ensure_ascii=False).encode())
            if n > 15_000_000:
                raise ValueError('單檔歷史超過分片預算')
            if batch and size + n > 15_000_000:
                yield f'valuation-{year}-{shard:02d}', batch
                shard, batch, size = shard + 1, [], 0
            batch.append(row)
            size += n
        if batch:
            yield f'valuation-{year}-{shard:02d}', batch


def table_for(rows):
    width = max(map(len, rows))
    head = ['代號', '市場', '起始日', '最後交易日', '交易日數', 'gzip/Base64']
    head += ['續段 ' + str(i) for i in range(1, width - 5)]
    return [head] + [row + [''] * (width - len(row)) for row in rows]


def digest(table):
    return hashlib.sha256(json.dumps(table, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def main():
    import gspread
    p = argparse.ArgumentParser()
    p.add_argument('directory', type=Path, nargs='?')
    p.add_argument('--status', action='store_true')
    p.add_argument('--verify', metavar='DATASET')
    args = p.parse_args()
    if args.status:
        data = route('history-status')
        print(json.dumps({'configured': data.get('configured'), 'volumes': [
            {k: v.get(k) for k in ('dataset', 'volume', 'state', 'cells', 'bytes')}
            for v in data.get('volumes', [])]}, ensure_ascii=False))
        return
    if not args.directory and not args.verify:
        p.error('需要歷史資料目錄')
    credentials = service_credentials()
    client = gspread.service_account_from_dict(credentials)
    if args.verify:
        selected = route('history-route', {'dataset': args.verify})
        if not selected.get('found'):
            raise RuntimeError('目錄尚無已核對的資料卷')
        values = client.open_by_key(selected['bookId']).sheet1.get_all_values()
        width = len(values[0])
        for i, row in enumerate(values):
            row.extend([''] * (width - len(row)))
            if i:
                row[4] = int(row[4])
        if digest(values) != selected['sha']:
            raise RuntimeError('封存讀取路由指紋不符')
        print(args.verify, '卷', selected['volume'], '讀取路由與指紋核對完成')
        return
    for dataset, rows in partitions(args.directory):
        table = table_for(rows)
        sha = digest(table)
        size = len(json.dumps(table, ensure_ascii=False).encode())
        reservation = route('history-reserve', {'dataset': dataset, 'sha': sha,
            'cells': len(table) * len(table[0]), 'bytes': size,
            'serviceEmail': credentials['client_email']})
        if reservation.get('unchanged'):
            print(dataset, '已核對，沿用既有卷')
            continue
        if reservation['bookId'] == os.environ['SPREADSHEET_ID']:
            raise RuntimeError('目的地不可為主試算表')
        book = client.open_by_key(reservation['bookId'])
        sheet = retry(book.worksheets)[0]
        # Every sheet stays under 20MB; old verified volumes are never overwritten.
        retry(sheet.resize, rows=len(table), cols=len(table[0]))
        for start in range(0, len(table), 40):
            retry(sheet.update, table[start:start+40], f'A{start+1}', raw=True)
        read = retry(sheet.get_all_values)
        restored = []
        for i, row in enumerate(read):
            padded = row + [''] * (len(table[0]) - len(row))
            if i:
                padded[4] = int(padded[4])
            restored.append(padded)
        if digest(restored) != sha:
            raise RuntimeError('封存完整讀回指紋不符；舊卷保留，目錄未切換')
        route('history-commit', {'dataset': dataset, 'bookId': reservation['bookId'],
            'row': reservation['row'], 'sha': sha, 'bytes': size})
        print(dataset, '卷', reservation['volume'], '完整讀回核對完成', len(rows), '檔')


if __name__ == '__main__':
    main()
