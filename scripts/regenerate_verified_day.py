"""Regenerate one saved day only after real-model quality gates pass.

Default is read-only. Apply preserves the source, manual rows, SMS priority and
delivery states. Before writing, make recoverable worksheet copies in the same
private spreadsheet. No notification function is called.
"""
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
    args = parser.parse_args()
    day = pl.norm_date(args.date)
    target = datetime.strptime(day, '%Y/%m/%d').date()
    today = datetime.now(ZoneInfo('Asia/Taipei')).date()
    if target > today or target < today - timedelta(days=7):
        raise SystemExit('只允許最近七天的既有原稿，不執行歷史整批重算')
    golden_file = Path('scripts/golden') / (target.isoformat() + '.json')
    if args.apply and not golden_file.exists():
        raise SystemExit('正式更新須有該日人工核對答案；本次未寫入')
    real = pl.open_sheets()
    dry = DryBook(real)
    snapshot = {}
    raw, polished = pl.existing_transcript(dry, '', day, snapshot=snapshot)
    if not raw or not snapshot.get('video_id'):
        raise SystemExit('原始逐字稿或來源 ID 不完整；本次未寫入')
    tables = ('操作紀錄', '會員持股', '每日推播內容')
    initial = {name: digest(real.worksheet(name).get_all_values()) for name in tables}
    ledger_names = [ws.title for ws in real.worksheets() if ws.title in ('寄送帳本', 'LINE 寄送帳本')]
    ledger_before = {name: digest(real.worksheet(name).get_all_values()) for name in ledger_names}
    writer, audit = pl.write_results, pl.save_evidence_audit
    captured, audits = {}, []

    def stop(ss, date, signals, *a, **kw):
        captured.update(date=date, signals=signals, args=a, kwargs=kw)
        raise Reached(signals)

    pl.write_results = stop
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
    errors, warnings = invariants(captured['signals'], raw)
    overview, hard, soft = pl.quality_overview(captured['signals'], raw)
    for line in overview:
        print('品質概況：' + line)
    errors = list(errors) + hard                     # 硬傷和通用檢查一樣擋下，不寫入正式資料
    warnings = list(warnings) + [x for x in soft if x not in warnings]
    for error in errors:
        print('品質錯誤：' + error)
    if errors:
        raise SystemExit('通用檢查未通過，正式資料保留')
    if golden_file.exists():
        ok, report = compare(captured['signals'], json.loads(golden_file.read_text(encoding='utf-8')), day)
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
    for name in tables:
        real.duplicate_sheet(real.worksheet(name).id, new_sheet_name=f'備份_{stamp}_{name}')
    print('三張工作表已在原私人試算表完成備份')
    sent_before = pl._daily_article_status(real, day)
    # 本輪的股票代號已核對，不順便修改其他日期的待確認代號。
    pl.run_post_write_steps = lambda book, days: (pl.apply_sms_priority(book, days), pl.resolve_cost_prices(book, days))
    writer(real, captured['date'], captured['signals'], *captured['args'], **captured['kwargs'])
    pl.enrich_sms_notes_from_signals(real, day, captured['signals'], raw)
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
