"""Regenerate one saved day only after real-model quality gates pass.

Default is read-only. Apply preserves the source, manual rows, SMS priority and
delivery states. Before writing, make recoverable worksheet copies in the separate
private backup spreadsheet. No notification function is called.
"""
from backup_archive import backup_tables
import argparse
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from replay_extract import DryBook, Reached, compare, invariants, pl


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--date', required=True)
    parser.add_argument('--apply', action='store_true')
    # raw：唯讀，不把人工答案的分類與不得出現的說法套進流程，只拿來比對（驗證規則本身用）
    parser.add_argument('--raw', action='store_true')
    parser.add_argument('--rules-only', action='store_true', help='分類只用正式規則，人工答案只核對、不注入分類')
    args = parser.parse_args()
    day = pl.norm_date(args.date)
    target = datetime.strptime(day, '%Y/%m/%d').date()
    today = datetime.now(ZoneInfo('Asia/Taipei')).date()
    # 最多回看三十天（管理者 2026/10/11 同意歷史日期可以重新套用）。正式更新另外必須有該日的人工答案，寫入前照樣先備份三張工作表。
    if target > today or target < today - timedelta(days=30):
        raise SystemExit('只處理最近三十天的既有原稿')
    golden_file = Path('scripts/golden') / (target.isoformat() + '.json')
    if args.apply and not golden_file.exists():
        raise SystemExit('正式更新須有該日人工核對答案；本次未寫入')
    golden = json.loads(golden_file.read_text(encoding='utf-8')) if golden_file.exists() else {}
    real = pl.open_sheets()
    dry = DryBook(real)
    snapshot = {}
    raw, polished = pl.existing_transcript(dry, '', day, snapshot=snapshot)
    if not raw or not snapshot.get('video_id'):
        raise SystemExit('原始逐字稿或來源 ID 不完整；本次未寫入')
    if golden.get('raw_sha256') and golden['raw_sha256'] != hashlib.sha256(raw.encode()).hexdigest():
        raise SystemExit('人工答案綁定的原稿已改變；本次未寫入')
    reviewed = {} if args.raw else (golden.get('reviewed_notes') or {})
    if reviewed:
        if golden.get('reviewed_notes_sha256') != hashlib.sha256(raw.encode()).hexdigest():
            raise SystemExit('人工說明的原稿指紋已變更，須重新核對')
        bad_quotes = []
        for code, item in reviewed.items():
            if item.get('category') not in pl.SIGNAL_CATEGORIES or not item.get('text') or not item.get('quotes'):
                raise SystemExit('人工說明欄位不完整：' + code)
            missing = [q for q in item['quotes'] if not pl._quote_is_real(q, pl._ev_norm(raw))]
            if missing:
                print('引用未對上原始稿：' + code + '：' + '／'.join(missing))
                probe = {'code': code, 'name': (golden.get('names') or {}).get(code, '')}
                for chunk, _ in pl._entity_scope(probe, {}, raw, before_chars=80, after_chars=450)[:2]:
                    print('本股原始聽打片段：' + chunk)
                bad_quotes.append(code)
        if bad_quotes:
            raise SystemExit('人工說明的引用不在原稿：' + '、'.join(bad_quotes))
    tables = ('操作紀錄', '會員持股', '每日推播內容')
    initial = {name: digest(real.worksheet(name).get_all_values()) for name in tables}
    ledger_names = [ws.title for ws in real.worksheets() if ws.title in ('寄送帳本', 'LINE 寄送帳本')]
    ledger_before = {name: digest(real.worksheet(name).get_all_values()) for name in ledger_names}
    writer, audit, verdict_cache = pl.write_results, pl.save_evidence_audit, pl._verdict_cache
    captured, audits = {}, []

    # 人工逐段核對後明列在該日答案的分類，交由既有裁決流程在撰稿前套用。
    # 只在這支單日更新腳本生效；一般每日流程仍由證據與裁決規則判定。
    if args.raw and args.apply:
        raise SystemExit('raw 只能唯讀預覽；本次未寫入')
    unassisted = args.raw or args.rules_only
    verified = {} if unassisted else {str(code): cats[0] for code, cats in golden.get('must', {}).items() if len(cats) == 1}
    if not unassisted:
        verified.update({str(code): 'ignored' for code in golden.get('must_not', [])})
    else:
        print('分類依正式規則：人工答案只拿來比對，不套進流程')
    existing_verdicts = verdict_cache(dry, day)
    conflicts = [code for code, category in verified.items() if (existing_verdicts.get(code) or {}).get('manual')
                 not in (None, '', category)]
    if conflicts:
        raise SystemExit('分類裁決表與人工答案相衝突：' + '、'.join(conflicts) + '；本次未寫入')

    def verified_cache(book, date_str):
        cache = verdict_cache(book, date_str)
        if date_str == day:
            for code, category in verified.items():
                cache.setdefault(code, {})['manual'] = category
                cache[code]['manual_source'] = '單日已核對人工答案'
        return cache

    def stop(ss, date, signals, *a, **kw):
        captured.update(date=date, signals=signals, args=a, kwargs=kw)
        raise Reached(signals)

    # 人工答案裡「這一檔不得出現的說法」：改寫成書面語之後、產生文章之前，把含有那個說法的子句刪掉。
    # 只在這支單日更新腳本生效；比對那一關照樣在後面把關。
    forbidden = {} if args.raw else {str(code): [x for x in phrases if x] for code, phrases in (golden.get('note_must_not') or {}).items()}
    polish = pl.polish_public_texts
    quarantine = pl.quarantine_public_rows

    def reviewed_then_quarantine(signals, transcript):
        for cat in pl.SIGNAL_CATEGORIES:
            for row in signals.get(cat, []) or []:
                item = reviewed.get(str(row.get('code') or ''))
                if item and item['category'] == cat:
                    row['note' if cat == 'holdings' else 'reason'] = item['text']
                    row['evidence'] = item['quotes']
        return quarantine(signals, transcript)

    def polish_then_enforce(signals, transcript, date_str):
        signals = polish(signals, transcript, date_str)
        for cat in pl.SIGNAL_CATEGORIES:
            for row in signals.get(cat, []) or []:
                phrases = forbidden.get(str(row.get('code') or '')) if isinstance(row, dict) else None
                field = 'note' if cat == 'holdings' else 'reason'
                text = str(row.get(field) or '') if phrases else ''
                if not text or not any(x in text for x in phrases):
                    continue
                kept = [[clause, punct] for clause, punct in pl._split_clauses(text) if not any(x in clause for x in phrases)]
                row[field] = ''.join(c + punct for c, punct in kept).strip('，,；;')
                if row[field] and row[field][-1] not in '。！？':
                    row[field] += '。'
                print(f"  人工答案　{row.get('name')}：說明裡有不得出現的說法（{'、'.join(x for x in phrases if x in text)}），已刪掉那個子句")
                pl.note_decision('人工答案', '刪掉不得出現的說法', str(row.get('name') or ''), text[:120])
        return signals

    pl.write_results = stop
    pl._verdict_cache = verified_cache
    pl.polish_public_texts = polish_then_enforce
    pl.quarantine_public_rows = reviewed_then_quarantine
    pl.save_evidence_audit = lambda *a, **kw: audits.append((a[1:], kw))
    pl.save_refresh_checkpoint = lambda *a, **kw: None
    pl.name_memo_load(dry)
    video = {'id': snapshot['video_id'], 'title': '', '_raw_sha256': snapshot.get('sha256', '')}
    try:
        pl._stage_extract_impl(dry, video, day, polished or raw, set(), set(), replace_video=True, v1=raw)
        raise SystemExit('覆蓋核對拒絕更新，正式資料保留')
    except Reached:
        pass
    finally:
        pl.write_results = writer
        pl.save_evidence_audit = audit
        pl._verdict_cache = verdict_cache
        pl.polish_public_texts = polish
        pl.quarantine_public_rows = quarantine
    errors, warnings = invariants(captured['signals'], raw)
    overview, hard, soft = pl.quality_overview(captured['signals'], raw)
    for line in overview:
        print('品質概況：' + line)
    # 這一輪實際會公開的文字（2026/10/08）：預覽只印統計看不出說明寫得好不好，逐檔印出來才能在套用前判斷。
    signals_now = captured['signals']
    print('公開內容預覽：')
    for row in signals_now.get('market') or []:
        if isinstance(row, dict) and row.get('_evidence_verified') and not row.get('_duplicate_point'):
            print(f"  {'教學' if row.get('kind') == 'view' else '盤勢'}　{str(row.get('text') or '')}")
    for cat, label in (('buy', '買入'), ('sell', '賣出'), ('holdings', '持股'), ('watch_watch', '觀望注意'), ('watch_avoid', '觀望不碰')):
        for row in signals_now.get(cat) or []:
            if not isinstance(row, dict) or (row.get('_date') or day) != day:
                continue
            text = pl.public_narrative(str(row.get('note' if cat == 'holdings' else 'reason') or ''), row, signals_now)
            print(f"  {label}　{row.get('name')}（{len(text)} 字）：{text}")
    errors = list(errors) + hard                     # 硬傷和通用檢查一樣擋下，不寫入正式資料
    warnings = list(warnings) + [x for x in soft if x not in warnings]
    for error in errors:
        print('品質錯誤：' + error)
    if errors:
        raise SystemExit('通用檢查未通過，正式資料保留')
    if golden_file.exists():
        ok, report = compare(captured['signals'], golden, day)
        print(report)
        if not ok:
            raise SystemExit('人工答案比對未通過，正式資料保留')
    print(f'品質核對通過；{len(warnings)} 項文字提醒')
    if not args.apply:
        print('唯讀模式，尚未更新正式資料')
        return
    fresh = {}
    current_raw, _ = pl.existing_transcript(real, video['id'], day, snapshot=fresh)
    if current_raw != raw or fresh.get('sha256') != snapshot.get('sha256'):
        raise SystemExit('原稿已變更，取消寫入')
    if any(digest(real.worksheet(name).get_all_values()) != initial[name] for name in tables):
        raise SystemExit('判讀期間正式資料已變動，取消寫入以保護其他工作')
    stamp = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%m%d_%H%M%S')
    backup_tables(pl,real,tables)
    print('三張工作表已在獨立私人試算表完成備份')
    sent_before = pl._daily_article_status(real, day)
    # 本輪的股票代號已核對，不順便修改其他日期的待確認代號。
    pl.run_post_write_steps = lambda book, days: (pl.apply_sms_priority(book, days), pl.resolve_cost_prices(book, days))
    writer(real, captured['date'], captured['signals'], *captured['args'], **captured['kwargs'])
    pl.save_verdicts(real, day, captured['signals'])      # 這一輪問到的分類裁決存起來，之後同一段原文不再重問
    pl.save_stock_industries(real, captured['signals'])      # 新查到的個股產業存起來，之後不再問
    pl.enrich_sms_notes_from_signals(real, day, captured['signals'], raw)
    # 會員通知列的說明是在寫入這一步才用同日原文改寫的，前面的人工答案比對看不到（2026/10/14 鈺邦的台幣子句）：
    # 寫入後讀回這一天的列，人工答案「不得出現的說法」所在的子句拿掉，並逐項印出核對結果。
    if forbidden:
        from gspread.utils import rowcol_to_a1
        for tab, field in [('操作紀錄', '理由摘錄'), ('會員持股', '說明重點')]:
            ws = real.worksheet(tab)
            values = pl.sheets_retry(ws.get_all_values)
            head = values[0]
            if not all(k in head for k in ('日期', '代號', field)):
                continue
            ci = {k: head.index(k) for k in ('日期', '代號', field)}
            edits = []
            for num, cells in enumerate(values[1:], 2):
                cell = lambda key: cells[ci[key]] if ci[key] < len(cells) else ''
                phrases = forbidden.get(str(cell('代號')))
                text = str(cell(field))
                if not phrases or pl.norm_date(cell('日期')) != day or not any(x in text for x in phrases):
                    continue
                kept = [[c, t] for c, t in pl._split_clauses(text) if not any(x in c for x in phrases)]
                fixed = ''.join(c + t for c, t in kept).strip('，,；;')
                if fixed and fixed[-1] not in '。！？':
                    fixed += '。'
                if fixed and fixed != text:
                    edits.append({'range': rowcol_to_a1(num, ci[field] + 1), 'values': [[fixed]]})
                    print(f"  寫入後核對　{tab} {cell('代號')}：說明裡有不得出現的說法，已拿掉那個子句")
            if edits:
                pl.sheets_retry(ws.batch_update, edits, value_input_option='RAW')
            values = pl.sheets_retry(ws.get_all_values)
            for cells in values[1:]:
                cell = lambda key: cells[ci[key]] if ci[key] < len(cells) else ''
                for phrase in forbidden.get(str(cell('代號')), []) if pl.norm_date(cell('日期')) == day else []:
                    print(f"  寫入後核對 {'✗' if phrase in str(cell(field)) else '✓'} {tab} {cell('代號')} 說明不得含「{phrase}」")
    # 本輪明示核對的說明可修正受保護的人工列；不改來源、分類、通知或其他日期。
    # 上面已備份、檢查指紋及並發變更。通知原文與交易價位不在修改範圍。
    if reviewed:
        from gspread.utils import rowcol_to_a1
        reviewed_memory=[]
        for tab, field in [('操作紀錄', '理由摘錄'), ('會員持股', '說明重點')]:
            ws = real.worksheet(tab)
            values = pl.sheets_retry(ws.get_all_values)
            head = values[0]
            ci = {k: head.index(k) for k in ('日期', '代號', '來源影片ID', field)}
            edits = []
            for num, cells in enumerate(values[1:], 2):
                cell = lambda key: cells[ci[key]] if ci[key] < len(cells) else ''
                item = reviewed.get(str(cell('代號')))
                if not item or pl.norm_date(cell('日期')) != day or pl._is_sms_row(str(cell('來源影片ID'))):
                    continue
                cat = 'holdings' if tab == '會員持股' else {'觀望注意':'watch_watch','觀望不碰':'watch_avoid','買入':'buy','賣出':'sell'}.get(cells[head.index('方向')])
                if cat == item['category'] and cell(field) != item['text']:
                    edits.append({'range': rowcol_to_a1(num, ci[field]+1), 'values': [[item['text']]]})
                if cat==item['category'] and cell('來源影片ID')==video['id']:
                    meta={'v':1,'sourceId':video['id'],'sourceSha256':hashlib.sha256(raw.encode()).hexdigest(),'category':cat,'note':item['text']}
                    reviewed_memory.append((tab,str(cell('代號')),meta))
            if edits:
                pl.sheets_retry(ws.batch_update, edits, value_input_option='RAW')
                print(f'已核對人工說明：{tab} 更新 {len(edits)} 列，分類與來源未改')
        if reviewed_memory:
            memo=real.worksheet('修正建議');saved=pl.sheets_retry(memo.get_all_values)
            existing={str(r[6]) for r in saved[1:] if len(r)>6 and r[2]=='保留人工說明'}
            now=datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d %H:%M:%S');memory=[]
            for tab,code,meta in reviewed_memory:
                payload=json.dumps(meta,ensure_ascii=False)
                if payload not in existing:
                    memory.append([now,day,'保留人工說明',tab,golden.get('names',{}).get(code,code),code,payload,'已套用（人工說明）',now]);existing.add(payload)
            if memory:
                pl.sheets_retry(memo.append_rows,memory,value_input_option='RAW')
                print(f'已核對說明指紋保護：{len(memory)} 筆；原稿、股票、來源或分類變更時不沿用')
    for a, kw in audits:
        audit(real, *a, **kw)
    # 把這一輪標成已發布。網站刷新時，每日整理的 ① 盤勢與 ③ 教學重點取自「最後一個已發布批次」的稽核列
    # （Articlequality.gs attachArticleEvidence_）；正式流程在寫入後會做這一步，這支腳本攔在寫入那一刻、沒有走到，
    # 結果表格（②）換成新的，①③ 卻一直停在早上那一輪——2026/10/07 更新後兩章仍各只有 1 點。
    pl.commit_evidence_manifest(real, video['id'], day, raw)
    if pl._daily_article_status(real, day) != sent_before:
        raise RuntimeError('每日寄送狀態不一致，請從備份核對；本程式沒有寄送')
    changed_ledgers = [name for name in ledger_names if digest(real.worksheet(name).get_all_values()) != ledger_before[name]]
    if changed_ledgers:
        raise RuntimeError('執行期間寄送帳本有其他工作寫入，需核對：' + '、'.join(changed_ledgers))
    print('正式單日資料已更新；Email／LINE 帳本與每日寄送狀態均保持不變')
    # 資料已經寫好，剩下的是請網站重算。Apps Script 暫時連不上或回空白時（2026/10/08 最後一步「記錄績效」），
    # 等一分鐘只續跑還沒做完的步驟，最多三輪；做完的不重來，也不再判讀。
    steps = ['smsmail', 'tracker', 'perfhist', 'perf']
    for attempt in range(3):
        result = pl.maybe_refresh_site(force=True, only=steps, date_str=day) or {}
        if result.get('ok'):
            break
        steps = steps[int(result.get('done') or 0):] or steps
        if not result.get('transient') or attempt == 2:
            raise RuntimeError('資料已更新，網站刷新尚未完成（剩 ' + '、'.join(steps) + '）；請只續跑刷新，不再判讀')
        print(f'網站刷新剩 {len(steps)} 步未完成（暫時性錯誤），60 秒後只續跑這幾步：' + '、'.join(steps))
        time.sleep(60)
    print('網站郵件查詢、持股與績效刷新完成；未重新取稿或寄送')


if __name__ == '__main__':
    main()
