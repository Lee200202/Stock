"""Read-only capacity inventory. Never exports cell values, workbook IDs or people data."""
import base64
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def forecast(current, previous):
    result = {}
    elapsed = (datetime.fromisoformat(current['at']) - datetime.fromisoformat(previous['at'])).total_seconds() / 86400
    for name, field in [('main', 'allocatedCells'), ('backup', 'cells')]:
        now, before = current.get(name, {}), previous.get(name, {})
        if now.get(field) is None or before.get(field) is None:
            continue
        delta = now[field] - before[field]
        rate = delta / elapsed if elapsed > 0 else 0
        remaining = max(0, 10_000_000 - now[field])
        result[name] = {'sampleDays': round(elapsed, 4), 'netGrowthCells': delta,
                        'cellsPerDay': round(rate, 2),
                        'daysToProjectRotationThreshold': round(remaining / rate, 2) if rate > 0 and elapsed >= 1 else None,
                        'daysToPublishedCellLimit': round(max(0, 20_000_000 - now[field]) / rate, 2) if rate > 0 and elapsed >= 1 else None,
                        'status': 'measured' if elapsed >= 1 and rate > 0 else 'insufficient-positive-daily-history'}
    return result


def inventory(book):
    meta = book.fetch_sheet_metadata()
    tabs = []
    for sheet in meta['sheets']:
        p = sheet['properties']
        grid = p.get('gridProperties', {})
        tabs.append({'name': p['title'], 'rows': grid.get('rowCount', 0),
                     'columns': grid.get('columnCount', 0),
                     'cells': grid.get('rowCount', 0) * grid.get('columnCount', 0)})
    cells = sum(t['cells'] for t in tabs)
    return {'allocatedCells': cells, 'publishedCellLimit': 20_000_000,
            'projectRotationThreshold': 10_000_000,
            'remainingCellsToProjectThreshold': max(0, 10_000_000 - cells),
            'publishedCellUtilization': round(cells / 20_000_000, 6),
            'tabs': sorted(tabs, key=lambda x: x['cells'], reverse=True)}


def main():
    import gspread
    raw = os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'].strip()
    if not raw.startswith('{'):
        raw = base64.b64decode(raw + '=' * (-len(raw) % 4)).decode()
    client = gspread.service_account_from_dict(json.loads(raw))
    # 2026/11/25 分卷：盤點目前使用中的一卷（查不到就是 SPREADSHEET_ID）
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pipeline import main_volumes
    book = client.open_by_key(main_volumes.active_id(os.environ['SPREADSHEET_ID'], json.loads(raw).get('client_email', '')))
    report = {'at': datetime.now(timezone.utc).isoformat(), 'main': inventory(book)}
    # One batch, date columns only. No visitor identifiers, messages or contact fields are read.
    ranges = ["'日K快取'!B2:B", "'小時K'!A2:A", "'盤中快照'!A2:A", "'使用紀錄'!A2:A"]
    started = time.perf_counter()
    batch = book.values_batch_get(ranges)
    histograms = []
    for name, values in zip(['日K快取','小時K','盤中快照','使用紀錄'], batch.get('valueRanges', [])):
        counts = {}
        for row in values.get('values', []):
            match = re.search(r'(20\d{2})[-/](\d{1,2})[-/](\d{1,2})', str(row[0]) if row else '')
            if match:
                day = '%s-%02d-%02d' % (match[1], int(match[2]), int(match[3]))
                counts[day] = counts.get(day, 0) + 1
        histograms.append({'name': name, 'rowsByDate': dict(sorted(counts.items())[-7:])})
    report['main']['dateColumnReadSeconds'] = round(time.perf_counter() - started, 3)
    report['main']['recentRowCounts'] = histograms
    from google.auth.transport.requests import AuthorizedSession
    drive = AuthorizedSession(client.http_client.auth)
    meta = drive.get('https://www.googleapis.com/drive/v3/files/' + os.environ['SPREADSHEET_ID'],
                     params={'fields': 'size,quotaBytesUsed', 'supportsAllDrives': 'true'}, timeout=60)
    payload = meta.json() if meta.ok else {}
    report['main']['driveSizeBytes'] = int(payload['size']) if payload.get('size') else None
    report['main']['driveQuotaBytesUsed'] = int(payload['quotaBytesUsed']) if payload.get('quotaBytesUsed') else None
    report['main']['driveSizeStatus'] = 'available' if payload.get('size') else 'not-reported' if meta.ok else 'metadata-unavailable'
    report['main']['publishedSizeLimitMB'] = 100
    # Main metadata is cheap; backup status reports only aggregate capacity and retention.
    import requests
    endpoint, key = os.environ.get('APPS_SCRIPT_URL'), os.environ.get('ADMIN_KEY')
    if endpoint and key:
        response = requests.get(endpoint, params={'action': 'backup-archive', 'key': key,
                                'op': 'status', 'source': os.environ['SPREADSHEET_ID']}, timeout=230)
        try:
            data = response.json()
            report['backup'] = {k: data.get(k) for k in
                                ('ok','configured','archived','cells','cellLimit','prunable','prunableCells','oldest')}
        except ValueError:
            report['backup'] = {'ok': False, 'reason': 'non-json-response', 'httpStatus': response.status_code}
    report['forecast'] = {'status': 'requires-two-timestamped-inventories',
                          'note': 'API latency cannot predict capacity exhaustion; measure cell and payload growth over time.'}
    previous = Path('previous-capacity-report.json')
    samples = []
    for sample in Path('.capacity-samples').glob('*.json'):
        doc = json.loads(sample.read_text(encoding='utf-8'))
        age = (datetime.fromisoformat(report['at']) - datetime.fromisoformat(doc['at'])).total_seconds()
        if age > 0:
            samples.append((age, doc))
    if samples:
        daily = sorted((x for x in samples if x[0] >= 86400), key=lambda x: x[0])
        baseline = daily[0][1] if daily else max(samples, key=lambda x: x[0])[1]
        previous.write_text(json.dumps(baseline), encoding='utf-8')
    if previous.exists():
        report['forecast'] = forecast(report, json.loads(previous.read_text(encoding='utf-8')))
    # A cell forecast does not cover Google's separate document-size edit restriction.
    report['limitsNote'] = 'Google publishes 20M cells or 100MB; project rotation stays conservative at 10M cells. Drive sizes may not equal the internal Sheets document-size calculation.'
    Path('capacity-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, field in [('main', 'allocatedCells'), ('backup', 'cells')]:
        value = report.get(name, {}).get(field)
        if value is not None and value >= 8_000_000:
            print(f'::warning::{name} spreadsheet allocated cells: {value:,}; prepare a new archive before writing.')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
