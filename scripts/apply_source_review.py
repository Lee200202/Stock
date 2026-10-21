"""Publish a reviewed, source-bound correction plan, without model or delivery calls.

Plans are corrections, never classification answer files or automatic aliases.
Atomic mutations include their receipt. Re-running verifies final state and only
resumes refresh. SMS and all other dates are outside the write set; legacy manual
transcript rows require explicit per-item, date-bound source review.
"""
import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = {'buy': '買入', 'sell': '賣出', 'holdings': '會員持股',
         'watch_watch': '觀望注意', 'watch_avoid': '觀望不碰'}
TABLES = ('操作紀錄', '會員持股')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True))


def category(sheet, row):
    if sheet == '會員持股':
        return 'holdings'
    return next((k for k, v in LABEL.items() if v == row.get('方向')), None)


def normalize_day(value):
    return str(value or '').strip()[:10].replace('-', '/')


def reviewed_source(item, plan):
    source = item.get('previous_source_id', plan['video_id'])
    if source != plan['video_id']:
        # Old manually submitted transcript rows can have a different ID than
        # the subsequently saved video. This is explicit repair authorization,
        # not permission for the classifier to overwrite manual or SMS rows.
        if (source != 'MANUAL-' + plan['date'].replace('/', '') or
                item.get('reviewed_manual_source') is not True or
                not item.get('manual_source_review_reason')):
            raise ValueError('非本影片來源未經逐筆覆核')
    if source.upper().startswith(('CMONEY-', 'SMS-')):
        raise ValueError('此工具不修改通知列')
    return source


def make_changes(tables, plan, names):
    """Pure planner. Fail closed on duplicates, changed source, or changed notes."""
    changes = []
    seen = set()
    for item in plan['items']:
        code, cat = item['code'], item['category']
        if cat not in LABEL or (code, cat) in seen:
            raise ValueError('無效或重複的分類：' + code)
        seen.add((code, cat))
        if item.get('action') != 'delete' and names.get(code) != item['name']:
            raise ValueError('官方名稱與代號不符：' + code)
        historical = item.get('historical_trade_review') or {}
        try:
            event_date = datetime.strptime(historical.get('event_date', ''), '%Y/%m/%d').strftime('%Y/%m/%d')
        except (ValueError, TypeError):
            event_date = ''
        historic_ok = (historical.get('actor_and_time_reviewed') is True and
                       bool(event_date) and event_date < plan['date'] and
                       event_date == historical.get('event_date'))
        if cat in ('buy', 'sell') and not (item.get('action') == 'delete' and historic_ok):
            raise ValueError('此修正工具不新增或更動成交事實；須另走交易覆核')
        old_cat = item.get('previous_category', cat)
        old_sheet = '會員持股' if old_cat == 'holdings' else '操作紀錄'
        new_sheet = '會員持股' if cat == 'holdings' else '操作紀錄'
        if old_sheet != new_sheet:
            raise ValueError('不允許跨表改動部位事實')
        head = tables[old_sheet][0]
        candidates = []
        # Match identity + previous category across all sources. A protected row
        # must never be bypassed by inserting a second row from the video.
        for i, cells in enumerate(tables[old_sheet][1:], 2):
            row = dict(zip(head, cells))
            if (normalize_day(row.get('日期')) == plan['date'] and
                    str(row.get('代號', '')).strip() == code and
                    category(old_sheet, row) == old_cat):
                candidates.append((i, row))
        if item.get('action') == 'insert':
            if candidates:
                raise ValueError('新增身分已存在，須重新審核：' + code)
            row = {'日期': plan['date'], '股票名稱': item['name'], '代號': code,
                   '來源影片ID': plan['video_id'], '序': '1', '價位說明': '未說明',
                   '方向': LABEL[cat], '持有態度': '續抱'}
            field = '說明重點' if new_sheet == '會員持股' else '理由摘錄'
            row[field] = item['text']
            # Actual holding header has varied over time; set known stance fields.
            for stance in ('態度', '立場', '目前立場', '持有態度', '持股態度'):
                if stance in head:
                    row[stance] = '續抱'
            changes.append({'kind': 'insert', 'sheet': new_sheet,
                            'cells': [row.get(h, '') for h in head], 'item': item})
            continue
        if len(candidates) != 1:
            raise ValueError('修正身分不存在或不唯一：' + code)
        index, old = candidates[0]
        field = '說明重點' if old_sheet == '會員持股' else '理由摘錄'
        if (old.get('來源影片ID') != reviewed_source(item, plan) or
                sha(old.get(field, '')) != item.get('previous_note_sha256')):
            raise ValueError('來源或說明指紋改變，保留現值：' + code)
        if old_cat in ('buy', 'sell') and not historic_ok:
            raise ValueError('更正歷史誤記成交須另附已覆核的主體與實際事件日')
        if old_cat in ('buy', 'sell'):
            earlier = [dict(zip(tables[old_sheet][0], cells)) for cells in tables[old_sheet][1:]]
            if not any(normalize_day(r.get('日期')) == event_date and
                       str(r.get('代號') or '').strip() == code and
                       category(old_sheet, r) == old_cat for r in earlier):
                raise ValueError('實際事件日沒有相應交易；先核對完整帳本，不移除：' + code)
        if item.get('action') == 'delete':
            changes.append({'kind': 'delete', 'sheet': old_sheet,
                            'index': index, 'item': item})
            continue
        if item.get('action') != 'update':
            raise ValueError('不認得的修正動作')
        new = dict(old)
        new[field] = item['text']
        if item.get('clear_non_trade_price') is True:
            if old_sheet != '操作紀錄' or old_cat not in ('watch_watch', 'watch_avoid'):
                raise ValueError('只可清除觀望列誤填的非股價數字')
            if not item.get('non_trade_price_review_reason'):
                raise ValueError('清除非股價數字須附原句覆核理由')
            new['價位說明'] = '未說明'
        if old_sheet == '操作紀錄':
            new['方向'] = LABEL[cat]
        changes.append({'kind': 'update', 'sheet': old_sheet, 'index': index,
                        'cells': [new.get(h, '') for h in head], 'item': item})
    return changes


def projected(tables, changes):
    out = {name: [list(r) for r in values] for name, values in tables.items()}
    for change in changes:
        if change['kind'] == 'update':
            out[change['sheet']][change['index'] - 1] = change['cells']
    for name in out:
        for change in sorted((c for c in changes if c['sheet'] == name and
                              c['kind'] == 'delete'), key=lambda c: c['index'], reverse=True):
            del out[name][change['index'] - 1]
    for change in changes:
        if change['kind'] == 'insert':
            out[change['sheet']].append(change['cells'])
    return out


def requests_for(changes, ids, receipt_rows):
    def cells(values):
        return [{'userEnteredValue': {'stringValue': str(x)}} for x in values]
    requests = []
    for c in changes:
        if c['kind'] == 'update':
            requests.append({'updateCells': {
                'start': {'sheetId': ids[c['sheet']], 'rowIndex': c['index'] - 1, 'columnIndex': 0},
                'rows': [{'values': cells(c['cells'])}], 'fields': 'userEnteredValue'}})
    for name in TABLES:
        for c in sorted((c for c in changes if c['sheet'] == name and c['kind'] == 'delete'),
                        key=lambda c: c['index'], reverse=True):
            requests.append({'deleteDimension': {'range': {
                'sheetId': ids[name], 'dimension': 'ROWS',
                'startIndex': c['index'] - 1, 'endIndex': c['index']}}})
    for c in changes:
        if c['kind'] == 'insert':
            requests.append({'appendCells': {'sheetId': ids[c['sheet']],
                'rows': [{'values': cells(c['cells'])}], 'fields': 'userEnteredValue'}})
    requests.append({'appendCells': {'sheetId': ids['修正建議'],
        'rows': [{'values': cells(row)} for row in receipt_rows], 'fields': 'userEnteredValue'}})
    return requests


def verify_final(tables, plan):
    for item in plan['items']:
        tab = '會員持股' if item['category'] == 'holdings' else '操作紀錄'
        rows = [dict(zip(tables[tab][0], cells)) for cells in tables[tab][1:]]
        rows = [r for r in rows if normalize_day(r.get('日期')) == plan['date'] and
                str(r.get('代號', '')).strip() == item['code'] and
                r.get('來源影片ID') == reviewed_source(item, plan)]
        if item['action'] == 'delete':
            if rows:
                raise ValueError('待移除的當日錯列仍存在：' + item['code'])
        else:
            rows = [r for r in rows if category(tab, r) == item['category']]
            field = '說明重點' if tab == '會員持股' else '理由摘錄'
            if len(rows) != 1 or rows[0].get(field) != item['text']:
                raise ValueError('修正後分類／說明未一致：' + item['code'])
            if item.get('clear_non_trade_price') is True and rows[0].get('價位說明') != '未說明':
                raise ValueError('非股價數字尚未清除：' + item['code'])
            if item.get('previous_category') and item['previous_category'] != item['category']:
                if any(category(tab, r) == item['previous_category'] for r in
                       [dict(zip(tables[tab][0], x)) for x in tables[tab][1:]]
                       if normalize_day(r.get('日期')) == plan['date'] and
                       str(r.get('代號', '')).strip() == item['code'] and
                       r.get('來源影片ID') == reviewed_source(item, plan)):
                    raise ValueError('舊分類未移除：' + item['code'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--inspect-only', action='store_true')
    args = parser.parse_args()
    if args.apply and args.inspect_only:
        raise ValueError('inspect-only不能同時寫入')
    sys.path.insert(0, str(ROOT / 'pipeline'))
    import pipeline as pl
    from apply_reviewed_points import source_quote
    from backup_archive import backup_tables
    day = pl.norm_date(args.date)
    plan_path = ROOT / 'scripts/repairs' / (day.replace('/', '-') + '-source-review.json')
    if args.inspect_only and not plan_path.exists():
        # 還沒有計畫時先看保存的原稿：計畫的引句必須逐字來自流程實際讀的那一份（可能是後台投稿的 MANUAL 列），
        # 不能用網站顯示的另一份。唯讀：模型0、備份0、寫入0、寄送0。
        source = {}
        raw, _ = pl.existing_transcript(pl.open_sheets(), '', day, snapshot=source)
        print('原稿來源', json.dumps({k: v for k, v in source.items() if not isinstance(v, (list, dict))}, ensure_ascii=False))
        print('原稿指紋', sha(raw), '字數', len(raw))
        print('===== 原稿開始 =====')
        for i in range(0, len(raw), 400):
            print(raw[i:i + 400])
        print('===== 原稿結束 =====')
        print('唯讀原稿核對完成；模型0、備份0、寫入0、寄送0')
        return
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['date'] != day or not plan.get('authorization') or not plan.get('review_basis'):
        raise ValueError('缺少日期一致的授權及來源審核說明')
    book = pl.open_sheets()
    source = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    if sha(raw) != plan['raw_sha256'] or source.get('video_id') != plan['video_id']:
        raise ValueError('原稿指紋／影片已變，未寫入')
    for item in plan['items']:
        if not item.get('rationale') or not item.get('quotes'):
            raise ValueError('缺少分類或刪除依據：' + item['code'])
        for quote in item['quotes']:
            source_quote(raw, quote)
        if item['action'] != 'delete' and not item.get('text'):
            raise ValueError('缺少書面說明')
    worksheets = {n: book.worksheet(n) for n in TABLES + ('修正建議',)}
    tables = {n: pl.sheets_retry(worksheets[n].get_all_values) for n in worksheets}
    if not args.apply:
        for tab in TABLES:
            for cells in tables[tab][1:]:
                r = dict(zip(tables[tab][0], cells))
                if normalize_day(r.get('日期')) == day:
                    print('來源列', tab, r.get('代號'), category(tab, r),
                          r.get('來源影片ID'), sha(r.get('說明重點') or r.get('理由摘錄') or ''))
                    target = any(i['code'] == str(r.get('代號') or '') for i in plan['items'])
                    if args.inspect_only and target and r.get('來源影片ID') in (
                            plan['video_id'], 'MANUAL-' + day.replace('/', '')):
                        print('計畫原列', json.dumps({'code': str(r.get('代號') or ''),
                            'category': category(tab, r), 'sourceId': r.get('來源影片ID'),
                            'note': r.get('說明重點') or r.get('理由摘錄') or ''}, ensure_ascii=False))
    if args.inspect_only:
        print('唯讀原列核對完成；模型0、備份0、寫入0、寄送0')
        return
    ledger_names = [w.title for w in book.worksheets()
                    if w.title in ('寄送帳本', 'LINE 寄送帳本')]
    ledger_hashes = {n: digest(book.worksheet(n).get_all_values()) for n in ledger_names}
    sent = pl._daily_article_status(book, day)
    receipt = '來源核對修正:' + digest(plan)
    complete = any(receipt in str(row) for row in tables['修正建議'][1:])
    names = {str(k): pl._display_name(v) for k, v in pl.get_code_map().items()}
    names_expected = {i['code']: pl._display_name(i['name']) for i in plan['items']}
    for code, name in names_expected.items():
        if names.get(code) != name:
            raise ValueError('官方名稱核對未通過：' + code)
    if complete:
        verify_final(tables, plan)
        print('既有原子收據與正式資料一致；不重寫，只接續刷新')
    else:
        # Names supplied to pure planner retain official display spelling.
        changes = make_changes(tables, plan, {i['code']: i['name'] for i in plan['items']})
        expected = projected(tables, changes)
        verify_final(expected, plan)
        for c in changes:
            print(c['kind'], c['item']['code'], c['item']['name'],
                  LABEL[c['item']['category']], c['item']['rationale'])
        print('原文及官方身分核對通過；計畫', len(changes), '筆；模型0、寄送0')
        if not args.apply:
            return
        backup_tables(pl, book, TABLES + ('每日推播內容',))
        for name, values in tables.items():
            if digest(worksheets[name].get_all_values()) != digest(values):
                raise ValueError('備份期間資料變動，未寫入：' + name)
        fresh = {}
        latest, _ = pl.existing_transcript(book, plan['video_id'], day, snapshot=fresh)
        if sha(latest) != plan['raw_sha256']:
            raise ValueError('備份期間原稿變動，未寫入')
        stamp = datetime.now(pl.TAIPEI).strftime('%Y/%m/%d %H:%M:%S')
        meta = json.dumps({'receipt': receipt, 'plan': plan}, ensure_ascii=False)
        row = [stamp, day, '來源核對修正', '逐字稿公開列', '', '', meta,
               '已套用（獨立備份可復原）', stamp]
        logs = [row]
        for item in plan['items']:
            if item['action'] == 'delete':
                continue
            tab = '會員持股' if item['category'] == 'holdings' else '操作紀錄'
            edit = {'v': 1, 'sourceId': reviewed_source(item, plan), 'sourceSha256': plan['raw_sha256'],
                    'category': item['category'], 'note': item['text']}
            logs.append([stamp, day, '保留人工說明', tab, item['name'], item['code'],
                         json.dumps(edit, ensure_ascii=False), '已套用（人工說明）', stamp])
        requests = requests_for(changes, {n: w.id for n, w in worksheets.items()}, logs)
        # Never retry an ambiguous mutation. A rerun checks receipt and final state.
        book.batch_update({'requests': requests})
        after = {n: worksheets[n].get_all_values() for n in TABLES}
        for name in TABLES:
            # get_all_values trims trailing empty cells; compare canonically.
            canon = lambda rows: [
                list(r)[:len(tables[name][0])] +
                [''] * max(0, len(tables[name][0]) - len(r)) for r in rows]
            if canon(after[name]) != canon(expected[name]):
                raise RuntimeError('原子寫入後非預期變動，需核對備份：' + name)
        verify_final(after, plan)
        print('正式列逐欄讀回一致；其他日期與受保護來源未變')
    if not args.apply:
        return
    steps = ['smsmail', 'tracker', 'perf']
    if any(item.get('historical_trade_review') for item in plan['items']):
        steps.insert(2, 'perfhist')
    result = pl.maybe_refresh_site(force=True, only=steps, date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('資料已保存；只需重跑本模式接續刷新，不重新判讀')
    verify_final({n: worksheets[n].get_all_values() for n in TABLES}, plan)
    if sent != pl._daily_article_status(book, day):
        raise RuntimeError('同期寄送狀態變動，請核對')
    if any(digest(book.worksheet(n).get_all_values()) != h for n, h in ledger_hashes.items()):
        raise RuntimeError('同期寄送帳本變動，請核對')
    print('分類／說明正式讀回、郵件查詢／持股／績效刷新成功；寄送狀態及帳本未變')


if __name__ == '__main__':
    main()
