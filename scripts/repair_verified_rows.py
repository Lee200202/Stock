"""Remove explicitly approved mismatched rows after verified private backup.

The version-controlled plan binds date, source fingerprint, identity and note
digest. This is a one-off data repair, not a classification rule. No model or
notification calls. Atomic Sheets batch also records the repair receipt.
"""
import argparse
import hashlib
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
from backup_archive import backup_tables
spec = importlib.util.spec_from_file_location('verified_row_repair_pipeline', ROOT / 'pipeline/pipeline.py')
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def find_rows(values, sheet, day, plans):
    """All expected identities must exist exactly once and still match the plan."""
    head = values[0]
    field = '說明重點' if sheet == '會員持股' else '理由摘錄'
    required = ['日期', '股票名稱', '代號', '來源影片ID', field]
    if any(k not in head for k in required):
        raise ValueError('缺少核對欄位：' + sheet)
    found = []
    for plan in plans:
        candidates = []
        for num, row in enumerate(values[1:], 2):
            r = dict(zip(head, row))
            if (pl.norm_date(r.get('日期')) == day and
                    str(r.get('代號', '')).strip() == plan['code'] and
                    r.get('股票名稱', '').strip() == plan['name']):
                candidates.append((num, r))
        if len(candidates) != 1:
            raise ValueError('身分不存在或不唯一：' + plan['code'])
        num, r = candidates[0]
        note = r.get(field, '')
        print('身分指紋', sheet, plan['code'], plan['name'],
              hashlib.sha256(note.encode()).hexdigest(), '字數', len(note))
        if hashlib.sha256(note.encode()).hexdigest() != plan['reason_sha256']:
            raise ValueError('說明已變更，保留資料：' + plan['code'])
        if pl._is_sms_row(r.get('來源影片ID', '')):
            raise ValueError('不得刪除通知衍生列：' + plan['code'])
        actual_class = '會員持股' if sheet == '會員持股' else r.get('方向')
        if actual_class != plan['class']:
            raise ValueError('分類已變更：' + plan['code'])
        if not note.startswith(plan['foreign_subject']) or plan['name'] in note:
            raise ValueError('錯位主詞前提不成立：' + plan['code'])
        found.append(num)
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    day = pl.norm_date(args.date)
    plan_path = ROOT / 'scripts/repairs' / (day.replace('/', '-') + '-row-identity.json')
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['date'] != day or not plan.get('authorization'):
        raise SystemExit('缺少日期一致且已授權的修正計畫')
    book = pl.open_sheets()
    snapshot = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=snapshot)
    if (snapshot.get('video_id') != plan['video_id'] or
            hashlib.sha256(raw.encode()).hexdigest() != plan['raw_sha256']):
        raise SystemExit('來源已變更；未寫入')
    tables = ['操作紀錄', '會員持股', '每日推播內容', '修正建議']
    sheets = {name: book.worksheet(name) for name in tables}
    values = {name: pl.sheets_retry(ws.get_all_values) for name, ws in sheets.items()}
    receipt = '身分錯位修復:' + digest(plan)
    if any(receipt in str(r) for r in values['修正建議'][1:]):
        print('本計畫已有完成收據；沒有重複刪除、沒有寄送')
        return
    selected = {}
    for name in ['操作紀錄', '會員持股']:
        selected[name] = find_rows(values[name], name, day,
                                  [r for r in plan['rows'] if r['sheet'] == name])
    print('已核對錯位身分：' + '、'.join(r['name'] + '(' + r['code'] + ')' for r in plan['rows']))
    if not args.apply:
        print('唯讀預覽通過；模型請求0、正式寫入0、通知0')
        return
    ledgers = {ws.title: digest(pl.sheets_retry(ws.get_all_values)) for ws in book.worksheets()
               if ws.title in ('寄送帳本', 'LINE 寄送帳本')}
    sent = pl._daily_article_status(book, day)
    backup_tables(pl, book, tables)
    for name, ws in sheets.items():
        if digest(pl.sheets_retry(ws.get_all_values)) != digest(values[name]):
            raise SystemExit('備份期間正式資料變動，取消寫入：' + name)
    fresh = {}
    current, _ = pl.existing_transcript(book, plan['video_id'], day, snapshot=fresh)
    if current != raw:
        raise SystemExit('備份期間原稿變動；取消寫入')
    requests = []
    for name, nums in selected.items():
        for num in sorted(nums, reverse=True):
            requests.append({'deleteDimension': {'range': {'sheetId': sheets[name].id,
                             'dimension': 'ROWS', 'startIndex': num - 1, 'endIndex': num}}})
    stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d %H:%M:%S')
    receipts = [[stamp, day, '移除錯位資料', r['sheet'], r['name'], r['code'],
                 receipt, '已套用（備份可復原）', stamp] for r in plan['rows']]
    requests.append({'appendCells': {'sheetId': sheets['修正建議'].id, 'fields': 'userEnteredValue',
                     'rows': [{'values': [{'userEnteredValue': {'stringValue': x}} for x in row]}
                              for row in receipts]}})
    # Do not retry an uncertain mutation. A rerun checks its receipt first.
    book.batch_update({'requests': requests})
    for name in selected:
        after = pl.sheets_retry(sheets[name].get_all_values)
        for r in plan['rows']:
            if r['sheet'] != name:
                continue
            h = after[0]
            if any(pl.norm_date(dict(zip(h, row)).get('日期')) == day and
                   str(dict(zip(h, row)).get('代號', '')).strip() == r['code'] and
                   dict(zip(h, row)).get('股票名稱') == r['name'] for row in after[1:]):
                raise RuntimeError('移除後身分仍存在：' + r['code'])
    if pl._daily_article_status(book, day) != sent:
        raise RuntimeError('寄送狀態被其他工作改動，請核對')
    for name, before in ledgers.items():
        if digest(pl.sheets_retry(book.worksheet(name).get_all_values)) != before:
            raise RuntimeError('寄送帳本被其他工作改動，請核對：' + name)
    print('四筆錯位列已移除並讀回；獨立備份與修正收據已保存；寄送狀態及帳本未動')
    print('後續只執行已授權單日apply-rules與刷新；本步驟模型0、寄送0')


if __name__ == '__main__':
    main()
