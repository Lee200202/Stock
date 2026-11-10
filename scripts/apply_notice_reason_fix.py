"""修正由會員通知產生的買賣列「理由摘錄」措辭（通知用語外洩），不改成交事實。

只改兩個欄位的同一句公開理由：操作紀錄.理由摘錄、會員簡訊.解析明細[].reason。
2026/11/06 管理者核准（週檢討 Q4）：通知產生的會員持股列也可以改，item 加 "sheet": "會員持股"，改的是 會員持股.說明重點
與解析明細裡同一檔 dir=會員持股 那一項的 reason；兩處舊句不同時，解析明細用 previous_detail_sha256。
股票、方向、價位、來源、通知原文、寄送帳本一律不動。計畫在 scripts/repairs/<日期>-notice-reason.json，
引句逐字核對原稿；寫入前先做獨立備份（需 APPS_SCRIPT_URL／ADMIN_KEY），寫完逐欄讀回，再刷新郵件／追蹤／績效。
舊理由只存指紋（previous_reason_sha256、previous_reason_chars）：舊句就是外洩的通知用語，不能寫進公開 repo。
不加 --apply 只預覽。"""
import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
BANNED = ('通知', '簡訊', '盤中', '請全數獲利賣出')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True))


def day_of(value):
    return str(value or '').strip()[:10].replace('-', '/')


def norm_day(value):
    # 簡訊解析用 USER_ENTERED 寫列時，日期欄會變成日期格式，讀出來可能是序號（2026/11/03 晶心科列＝46329）
    text = str(value or '').strip()
    if re.fullmatch(r'\d{5}', text):
        return (datetime(1899, 12, 30) + timedelta(days=int(text))).strftime('%Y/%m/%d')
    parts = day_of(value).split('/')
    return '/'.join([parts[0]] + [p.zfill(2) for p in parts[1:]]) if len(parts) == 3 else day_of(value)


def target(item):
    """(工作表, 欄位, 解析明細的 dir 條件)。"""
    if item.get('sheet', '操作紀錄') == '會員持股':
        return '會員持股', '說明重點', lambda d: str(d.get('dir') or '') == '會員持股'
    if item.get('sheet', '操作紀錄') != '操作紀錄':
        raise ValueError('sheet 只能是 操作紀錄 或 會員持股')
    return '操作紀錄', '理由摘錄', lambda d: str(d.get('dir') or '') != '會員持股'


def locate(trades, sms, plan, item):
    th = trades[0]
    rows = [(i, dict(zip(th, c))) for i, c in enumerate(trades[1:], 2)]
    hit = [(i, r) for i, r in rows if norm_day(r.get('日期')) == plan['date'] and
           str(r.get('代號', '')).strip() == item['code'] and
           r.get('來源影片ID') == item['notice_source_id']]
    if len(hit) != 1:
        raise ValueError(target(item)[0] + '通知列不存在或不唯一：' + item['code'])
    ti, trow = hit[0]
    sh = sms[0]
    shit = [(i, dict(zip(sh, c))) for i, c in enumerate(sms[1:], 2) if str(c[0]).strip() == item['article_id']]
    if len(shit) != 1:
        raise ValueError('會員簡訊列不存在或不唯一：' + item['article_id'])
    si, srow = shit[0]
    detail = json.loads(srow.get('解析明細') or '[]')
    wanted = target(item)[2]
    mine = [d for d in detail if str(d.get('code')) == item['code'] and wanted(d)]
    if len(mine) != 1:
        raise ValueError('解析明細沒有唯一對應的項目')
    return ti, trow, si, srow, detail, mine[0]


def contains_sha(text, chars, digest_hex):
    """text 裡有沒有長度 chars、指紋相同的一段（確認舊理由已不在文章裡，而不必把舊句寫進計畫）。"""
    return any(sha(text[i:i + chars]) == digest_hex for i in range(0, max(0, len(text) - chars + 1)))


def plan_writes(plan, trades, sms, field, done, day):
    """純函式：回傳 (writes=[(item, 列號, 簡訊列號)], work={簡訊列號: 改好的解析明細}, planned_cells)。

    2026/11/10：同一則通知改兩檔（世芯-KY、宜鼎）時，兩檔共用同一格「解析明細」。以前每一檔各自從原始明細改一份再寫回，
    第二筆把第一筆蓋掉，讀回就不一致。現在同一列通知的修改累積在同一份明細上，最後每列只寫一次。"""
    writes, work, planned_cells = [], {}, {}
    for item in plan['items']:
        ti, trow, si, srow, detail, _ = locate(trades, sms, plan, item)
        detail = work.setdefault(si, detail)
        mine = [d for d in detail if str(d.get('code')) == item['code'] and target(item)[2](d)][0]
        current = trow.get(field)
        if done:
            if current != item['text'] or mine.get('reason') != item['text']:
                raise ValueError('已有收據但資料與計畫不一致（之後被改寫，或舊版工具同一則通知多檔互蓋）：' + item['code'] +
                                 '；請另寫新計畫，只補不一致的欄位（舊值用 previous_detail_sha256）')
            continue
        if sha(current or '') != item['previous_reason_sha256'] or \
                sha(mine.get('reason') or '') != item.get('previous_detail_sha256', item['previous_reason_sha256']):
            raise ValueError('理由已被改動，保留現值：' + item['code'])
        # reviewed：之後的簡訊解析（雙來源重寫）看到這個標記就不再改寫這一句
        mine.update(reason=item['text'], reviewed='通知理由修正 ' + day)
        planned_cells[(ti, si)] = (trades[ti - 1], sms[si - 1])
        writes.append((item, ti, si))
        print('改', item['code'], item['name'], '\n  舊理由指紋：', item['previous_reason_sha256'][:12], '\n  新：', item['text'])
    return writes, work, planned_cells


def position_only_text(name):
    return f'{name}為紀錄上仍持有的會員持股，今天原稿沒有談到本股，部位不變。'


def unchanged_rows_or_raise(plan, planned_cells, trades_now, sms_now):
    """純函式：備份後重讀，要寫的每一列（操作紀錄／會員持股列與會員簡訊列）列號與內容都沒變才放行。"""
    for item in plan['items']:
        ti, _, si, _, _, _ = locate(trades_now, sms_now, plan, item)
        before = planned_cells.get((ti, si))
        if before is None or trades_now[ti - 1] != before[0] or sms_now[si - 1] != before[1]:
            raise ValueError('備份期間這一列資料變動，未寫入：' + item['code'])


def check_item(item, raw_compact, source_quote, raw):
    if 'previous_reason' in item:
        raise ValueError('計畫不得寫入舊理由原句（通知用語），請改用 previous_reason_sha256／previous_reason_chars')
    if not item.get('previous_reason_sha256') or not item.get('previous_reason_chars'):
        raise ValueError('缺少舊理由指紋')
    if item.get('position_only'):
        # 2026/11/10 管理者授權（祥碩 5269）：通知產生的持股列說明照抄通知，但當天原稿完全沒談到這一檔，找不到原句可改寫。
        # 只准會員持股列、只准固定句型陳述部位事實，不需引句；任何評論或價位都不行。
        if item.get('sheet') != '會員持股' or item.get('text') != position_only_text(item.get('name', '')):
            raise ValueError('position_only 只用於會員持股列，且 text 必須是固定句：' + position_only_text(item.get('name', '')))
        if not item.get('rationale'):
            raise ValueError('缺少修正依據')
        return
    if not item.get('rationale') or not item.get('quotes') or not item.get('text'):
        raise ValueError('缺少修正依據')
    for q in item['quotes']:
        source_quote(raw, q)
    if any(b in item['text'] for b in BANNED):
        raise ValueError('新理由仍含通知或通道字眼：' + item['text'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--date', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
    import common  # noqa: F401  雲端環境的金鑰是 Base64 一行，先還原成 JSON（2026/11/10：排程才能在推送前預覽）
    import pipeline as pl
    from apply_reviewed_points import source_quote
    day = pl.norm_date(args.date)
    path = ROOT / 'scripts/repairs' / (day.replace('/', '-') + '-notice-reason.json')
    if not path.exists():
        print('沒有通知理由修正計畫：', path.name)
        return
    plan = json.loads(path.read_text(encoding='utf-8'))
    if plan['date'] != day or not plan.get('authorization') or not plan.get('review_basis'):
        raise ValueError('缺少日期一致的授權及審核說明')
    book = pl.open_sheets()
    source = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    if sha(raw) != plan['raw_sha256'] or source.get('video_id') != plan['video_id']:
        raise ValueError('原稿指紋／影片已變，未寫入')
    for item in plan['items']:
        check_item(item, None, source_quote, raw)
    sheets = {target(i)[0] for i in plan['items']}
    if len(sheets) != 1:
        raise ValueError('一份計畫只改一張表（操作紀錄或會員持股）')
    tab, field, _ = target(plan['items'][0])
    ws_t, ws_s, ws_r = book.worksheet(tab), book.worksheet('會員簡訊'), book.worksheet('修正建議')
    trades, sms, receipts = (pl.sheets_retry(w.get_all_values) for w in (ws_t, ws_s, ws_r))
    receipt = '通知理由修正:' + digest(plan)
    done = any(receipt in str(r) for r in receipts[1:])
    names = {w.title for w in book.worksheets()}
    ledgers = [n for n in ('寄送帳本', 'LINE 寄送帳本') if n in names]
    ledger_before = {n: digest(book.worksheet(n).get_all_values()) for n in ledgers}
    sent = pl._daily_article_status(book, day)
    th, sh = trades[0], sms[0]
    requests = []
    writes, work, planned_cells = plan_writes(plan, trades, sms, field, done, day)
    if done:
        print('既有收據與資料一致；只接續刷新')
    elif not args.apply:
        print('預覽完成：原稿與唯一列核對通過；成交事實、通知原文、寄送帳本不動；模型0、寄送0')
        return
    if args.apply and not done:
        from backup_archive import backup_tables
        backup_tables(pl, book, tuple(dict.fromkeys((tab, '操作紀錄', '會員簡訊', '每日推播內容', '持股追蹤'))))
        cell = lambda v: {'userEnteredValue': {'stringValue': str(v)}}
        for item, ti, si in writes:
            requests.append({'updateCells': {'start': {'sheetId': ws_t.id, 'rowIndex': ti - 1, 'columnIndex': th.index(field)},
                                             'rows': [{'values': [cell(item['text'])]}], 'fields': 'userEnteredValue'}})
        for si, detail in work.items():
            if any(w[2] == si for w in writes):
                requests.append({'updateCells': {'start': {'sheetId': ws_s.id, 'rowIndex': si - 1, 'columnIndex': sh.index('解析明細')},
                                                 'rows': [{'values': [cell(json.dumps(detail, ensure_ascii=False))]}], 'fields': 'userEnteredValue'}})
        stamp = datetime.now(pl.TAIPEI).strftime('%Y/%m/%d %H:%M:%S')
        meta = json.dumps({'receipt': receipt, 'plan': plan}, ensure_ascii=False)
        requests.append({'appendCells': {'sheetId': ws_r.id, 'fields': 'userEnteredValue', 'rows': [{'values': [cell(x) for x in
            [stamp, day, '通知用語外洩修正', tab + '／會員簡訊解析明細', '', '', meta, '已套用（獨立備份可復原）', stamp]]}]}})
        # 備份期間要改的那幾列有變就不寫。2026/11/10：以前比整張表，五分鐘排程一動「會員簡訊」任何一格就整組失敗；
        # 現在只比要寫的列（重新定位後列號與整列內容都要相同），其他列的變動不影響這次寫入。
        unchanged_rows_or_raise(plan, planned_cells, ws_t.get_all_values(), ws_s.get_all_values())
        book.batch_update({'requests': requests})
    if not args.apply:
        return
    trades2, sms2 = ws_t.get_all_values(), ws_s.get_all_values()
    for item in plan['items']:
        _, trow, _, _, _, mine = locate(trades2, sms2, plan, item)
        if trow.get(field) != item['text'] or mine.get('reason') != item['text']:
            raise RuntimeError('寫入後讀回不一致：' + item['code'])
    print('操作紀錄與解析明細逐欄讀回一致')
    result = pl.maybe_refresh_site(force=True, only=['smsmail', 'tracker', 'perf'], date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('資料已保存；只需重跑本模式接續刷新')
    article = next((r for r in book.worksheet('每日推播內容').get_all_values()[1:] if norm_day(r[0]) == day), [''])
    stale = [i['code'] for i in plan['items']
             if contains_sha(' '.join(article), int(i['previous_reason_chars']), i['previous_reason_sha256'])]
    if stale:
        raise RuntimeError('刷新後文章仍含舊理由，須人工核對：' + '、'.join(stale))
    if sent != pl._daily_article_status(book, day) or any(
            digest(book.worksheet(n).get_all_values()) != h for n, h in ledger_before.items()):
        raise RuntimeError('寄送狀態或帳本變動，請核對')
    print('文章不含舊理由、郵件／追蹤／績效刷新成功；寄送狀態及帳本未變')


if __name__ == '__main__':
    main()
