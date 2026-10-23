"""唯讀驗收正式歷史 JSON；格式通過與來源完整度分開。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import math
from pathlib import Path
import subprocess
import time
import urllib.request


BASE = 'https://lee200202.github.io/Stock/history/'


def download(name):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(BASE + name + '.json', timeout=60) as response:
                return json.load(response)
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt)


def check_code(item):
    code, meta = item
    try:
        doc = download(code)
        rows = doc.get('rows', [])
        errors = []
        dates = []
        for row in rows:
            if len(row) != 6:
                errors.append('欄位數不符'); continue
            day, o, h, l, c, v = row
            try:
                if date.fromisoformat(day).weekday() == 6:
                    errors.append(f'{day}：星期日')
            except ValueError:
                errors.append(f'{day}：日期格式錯誤')
            dates.append(day)
            if not all(isinstance(x, (float, int)) and math.isfinite(x) for x in (o, h, l, c, v)):
                errors.append(f'{day}：非有限數值'); continue
            if min(o, h, l, c) <= 0 or v < 0 or not l <= min(o, c) <= max(o, c) <= h:
                errors.append(f'{day}：OHLC或成交量矛盾')
        if dates != sorted(set(dates)):
            errors.append('日期未排序或重複')
        first, latest = dates[0] if dates else None, dates[-1] if dates else None
        if not rows or len(rows) != meta.get('bars'):
            errors.append('筆數不符或沒有日K')
        if first != doc.get('first') or latest != doc.get('latest'):
            errors.append('個股資料起訖不符')
        for key, value in [('first', first), ('latest', latest)]:
            if key in meta and meta[key] != value:
                errors.append(f'總表{key}不符')
        if doc.get('code') != code or doc.get('volumeBasis') != 'raw-traded-shares-incl-odd-lots':
            errors.append('代號或成交股數口徑不符')
        return {'code': code, 'name': doc.get('name'), 'bars': len(rows), 'first': first,
                'latest': latest, 'adjustments': doc.get('adjustments', []), 'errors': errors}
    except Exception as exc:
        return {'code': code, 'errors': [f'正式讀取失敗：{type(exc).__name__}']}


def audit(ref=None):
    meta = download('_meta')
    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(check_code, sorted(meta['codes'].items())))
    result = {'metaUpdated': meta.get('updated'), 'files': len(codes),
              'requested': len(meta.get('requestedCodes', [])), 'bars': sum(c.get('bars', 0) for c in codes),
              'missing': meta.get('missingCodes', []), 'quarantinedRows': meta.get('quarantinedRows', []),
              'inputCodeCount': len(meta.get('inputCodes', meta.get('requestedCodes', []))),
              'excludedCodes': meta.get('excludedCodes', {}),
              'coverageVerified': meta.get('coverageVerified'),
              'twseCoverage': meta.get('archiveCoverage', {}).get('twse'),
              'errors': [c for c in codes if c['errors']], 'codes': codes,
              'snapshotChanged': download('_meta').get('updated') != meta.get('updated')}
    if ref:
        listing = json.loads(subprocess.check_output(['git', 'show', f'{ref}:archive/meta/listing.json']))['codes']
        paths = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', ref, 'archive/tpex']).decode().splitlines()
        archived = {}
        for path in paths:
            parts = path.split('/')
            if len(parts) == 4:
                archived.setdefault(parts[2], set()).add(parts[3].removesuffix('.json'))
        end = meta['latest'][:7]
        end_index = int(end[:4])*12 + int(end[5:7])-1
        missing_months, unverified_in_range, empty_before_listing = {}, {}, {}
        for code in meta.get('requestedCodes', []):
            start = (listing.get(code) or {}).get('otc')
            if not start:
                continue
            begin = int(start[:4])*12 + int(start[5:7])-1
            missing = [f'{i//12}{i%12+1:02d}' for i in range(begin, end_index+1)
                       if f'{i//12}{i%12+1:02d}' not in archived.get(code, set())]
            if missing:
                missing_months[code] = missing
            empty = meta.get('archiveCoverage', {}).get('tpex', {}).get(code, {}).get('emptyMonthsUnverified', [])
            within = [month for month in empty if start[:7].replace('-', '') <= month <= end.replace('-', '')]
            before = [month for month in empty if month < start[:7].replace('-', '')]
            if within:
                unverified_in_range[code] = within
            if before:
                empty_before_listing[code] = before
        result['archiveRef'] = subprocess.check_output(['git', 'rev-parse', ref]).decode().strip()
        result['currentOtcMissingArchiveMonths'] = missing_months
        result['currentOtcUnverifiedMonthsWithinListing'] = unverified_in_range
        result['currentOtcUnverifiedMonthsBeforeListing'] = empty_before_listing
        result['missingCodeIdentity'] = {c: {'officialCurrentListing': listing.get(c),
                'archivedMonths': len(archived.get(c, set()))} for c in result['missing']}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive-ref')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    result = audit(args.archive_ref)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('codes', 'quarantinedRows')}, ensure_ascii=False))
    raise SystemExit(1 if result['errors'] or result['snapshotChanged'] else 0)
