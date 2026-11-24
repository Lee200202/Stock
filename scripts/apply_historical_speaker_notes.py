"""Apply reviewed wording changes to the historical public stock notes."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'pipeline'), str(ROOT / 'scripts' / 'daily_review')]
import common
import pipeline as pl
from backup_archive import backup_tables

FIELDS = {'操作紀錄': '理由摘錄', '會員持股': '說明重點'}
LABEL = {'buy': '買入', 'sell': '賣出', 'watchWatch': '觀望注意',
         'watchAvoid': '觀望不碰', 'holdings': '會員持股'}


def requests_for(sheets, values, plan):
    requests = []
    unmatched = []
    used = set()
    for day, kind, code, name, substitutions in plan:
        tab = '會員持股' if kind == 'holdings' else '操作紀錄'
        head, *rows = values[tab]
        col = head.index(FIELDS[tab])
        candidates = []
        diagnostics = []
        for index, row in enumerate(rows, 1):
            record = dict(zip(head, row))
            if (pl.norm_date(record.get('日期')) != day
                    or (str(record.get('代號') or '').strip().removesuffix('.0') != code and code != '代號待確認')
                    or str(record.get('股票名稱') or '').strip() != name
                    or (kind != 'holdings' and LABEL[kind].removeprefix('觀望') not in record.get('方向', ''))):
                continue
            old = record.get(FIELDS[tab], '')
            new = old
            for phrase, replacement in substitutions:
                pattern = r'\s*'.join(re.escape(c) for c in phrase if not c.isspace())
                if len(re.findall(pattern, new)) != 1:
                    diagnostics.append((index, '講者' in old, substitutions.index([phrase, replacement])))
                    break
                new = re.sub(pattern, lambda _: replacement, new, count=1)
            else:
                candidates.append((index, old, new))
        if len(candidates) != 1:
            unmatched.append((day, kind, code, name, len(candidates), diagnostics))
            continue
        index, old, new = candidates[0]
        key = (tab, index, col)
        if key in used:
            raise ValueError(f'重複修改 {key}')
        used.add(key)
        requests.append({'findReplace': {
            'find': old, 'replacement': new, 'matchCase': True,
            'matchEntireCell': True, 'searchByRegex': False, 'includeFormulas': False,
            'range': {'sheetId': sheets[tab].id, 'startRowIndex': index,
                      'endRowIndex': index + 1, 'startColumnIndex': col,
                      'endColumnIndex': col + 1}}})
    return requests, unmatched


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    plan = json.loads((ROOT / 'scripts' / 'repairs' / 'historical-speaker-notes.json').read_text(encoding='utf-8'))
    book = pl.open_sheets()
    sheets = {tab: book.worksheet(tab) for tab in FIELDS}

    def read():
        return {tab: pl.sheets_retry(sheet.get_all_values) for tab, sheet in sheets.items()}

    requests, unmatched = requests_for(sheets, read(), plan)
    for item in unmatched:
        print('無法唯一比對', *item)
    print(f'可修改 {len(requests)} 格，待比對 {len(unmatched)} 筆')
    if not args.apply or not requests:
        return
    backup_tables(pl, book, tuple(FIELDS))
    fresh_requests, fresh_unmatched = requests_for(sheets, read(), plan)
    if fresh_requests != requests or fresh_unmatched != unmatched:
        raise RuntimeError('備份期間目標資料有變動，未寫入')
    result = book.batch_update({'requests': requests})
    changed = sum(r.get('findReplace', {}).get('occurrencesChanged', 0)
                  for r in result['replies'])
    print(f'已寫入 {changed} 格個股說明，日期查詢由主表供應')
    if changed != len(requests):
        raise RuntimeError('部分原文在寫入前變動，保留該列')


if __name__ == '__main__':
    main()
