"""Read-only capacity inventory. Never exports cell values, workbook IDs or people data."""
import base64
import json
import os
from datetime import datetime, timezone
from pathlib import Path


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
    return {'allocatedCells': cells, 'cellLimit': 10_000_000,
            'remainingCells': max(0, 10_000_000 - cells),
            'utilization': round(cells / 10_000_000, 6),
            'tabs': sorted(tabs, key=lambda x: x['cells'], reverse=True)}


def main():
    import gspread
    raw = os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'].strip()
    if not raw.startswith('{'):
        raw = base64.b64decode(raw + '=' * (-len(raw) % 4)).decode()
    client = gspread.service_account_from_dict(json.loads(raw))
    report = {'at': datetime.now(timezone.utc).isoformat(),
              'main': inventory(client.open_by_key(os.environ['SPREADSHEET_ID']))}
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
    Path('capacity-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
