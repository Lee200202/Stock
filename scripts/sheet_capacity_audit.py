"""Read-only capacity inventory. Never exports cell values, workbook IDs or people data."""
import base64
import json
import os
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
    report = {'at': datetime.now(timezone.utc).isoformat(),
              'main': inventory(client.open_by_key(os.environ['SPREADSHEET_ID']))}
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
