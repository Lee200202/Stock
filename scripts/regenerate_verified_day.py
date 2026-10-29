"""先唯讀實跑模型保存候選；發布使用同版本、同快照的候選，不重新生成或注入答案。"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from backup_archive import backup_tables
from model_release_candidate import VERSION, TABLES, cell_requests, digest, inference_values, plan_tables, seal, validate
from replay_extract import DryBook, Reached, compare, invariants, pl
from replay_reference import load_reference, compare_reference

ROOT = Path(__file__).resolve().parents[1]
INPUTS = set(TABLES) | {'影片清單', '會員簡訊', '名稱判定紀錄', '代號對照快取',
    '分類裁決', '修正建議', '個股產業', '股票產業', '逐字稿判讀稽核', '持股追蹤'}


class FrozenSheet:
    """重讀一律使用已凍結內容，未知介面拒絕。"""
    def __init__(self, title, values):
        self.title, self.values = title, copy.deepcopy(values)

    def get_all_values(self, **kw):
        return copy.deepcopy(self.values)

    def get_all_records(self, **kw):
        rows = self.get_all_values()
        return [dict(zip(rows[0], r + [''] * (len(rows[0]) - len(r)))) for r in rows[1:]] if rows else []

    def row_values(self, row):
        return copy.deepcopy(self.values[row - 1]) if row <= len(self.values) else []

    def col_values(self, column):
        return [r[column - 1] if len(r) >= column else '' for r in self.values]

    def __getattr__(self, name):
        raise RuntimeError('凍結快照不允許：' + self.title + '.' + name)


class FrozenBook:
    def __init__(self, sheets):
        self.sheets = sheets

    def worksheet(self, name):
        if name not in self.sheets:
            raise RuntimeError('未凍結的資料來源：' + name)
        return self.sheets[name]

    def worksheets(self):
        return list(self.sheets.values())


def reference_check(path, day, raw, signals):
    """推論完成後才讀參考內容；只回報差異，不修改模型輸出。"""
    if not path.exists():
        return ['缺少來源參考，分類品質未評估'], ''
    ref = json.loads(path.read_text(encoding='utf-8-sig'))
    if ref.get('mode') == 'independent_review':
        result, report = compare_reference(signals, load_reference(path, day, raw), day)
        return ([] if result['status'] == 'aligned_with_draft' else ['獨立來源草稿差異待核']), report
    if ref.get('raw_sha256') and ref['raw_sha256'] != hashlib.sha256(raw.encode()).hexdigest():
        return ['參考原稿指紋不同'], ''
    ok, report = compare(signals, ref, day)
    return ([] if ok else ['來源參考比對有差異']), report


# 管理者明示「以這一次模型結果為準」時可轉為警告的兩類（2026/10/29）：被來源審閱擋下的單檔不公開（照樣隔離、不寫入公開分類），
# 以及與參考答案的差異。硬傷（流程錯誤、文章品質、不變式、人工說明混入）照樣擋。
MANAGER_WAIVABLE = ('仍有隔離／待裁決個股，完整單日候選不予發布', '來源參考比對有差異', '獨立來源草稿差異待核')


def publication_errors(signals):
    errors = []
    if signals.get('_classification_runtime_error'):
        errors.append('分類裁決未完成')
    verdicts = signals.get('_verdicts') or {}
    # 原本就不公開、三輪全部缺答的候選（verdict.hidden_base）照樣標待確認，但它不會公開，不擋整天的發布；
    # 原本公開的列沒有定案、或有隔離列，仍擋（2026/10/28）。
    blocking = [r for r in signals.get('uncertain') or []
                if not (isinstance(r, dict) and (verdicts.get(str(r.get('code') or '')) or {}).get('hidden_base'))]
    if signals.get('_publication_quarantine') or blocking:
        errors.append('仍有隔離／待裁決個股，完整單日候選不予發布')
    for cat in pl.SIGNAL_CATEGORIES:
        if any(r.get('_manual_note_kept') for r in signals.get(cat, []) or []):
            errors.append('模型候選混入人工保留說明')
    return errors


def state_hashes(book, names):
    return {n: digest(book.worksheet(n).get_all_values()) for n in names}


def preview(book, day, raw, source, commit, state, reference, output, snapshots, accept_model=False):
    frozen = {name: FrozenSheet(name, inference_values(name, values, day, pl.norm_date))
              for name, values in snapshots.items() if name in INPUTS}
    dry = DryBook(FrozenBook(frozen))
    captured = {}
    def stop(ss, date, signals, article, *a, **kw):
        captured.update(signals=copy.deepcopy(signals), article=article)
        raise Reached(signals)
    old = {n: getattr(pl, n) for n in ('write_results', '_verdict_cache', 'prior_published_rows',
                                      'save_evidence_audit', 'save_refresh_checkpoint')}
    pl.write_results, pl._verdict_cache = stop, lambda *a, **kw: {}
    pl.prior_published_rows = lambda *a, **kw: []
    def capture_audit(ss, video_id, date, transcript, signals):
        captured['signals'] = copy.deepcopy(signals)
    pl.save_evidence_audit = capture_audit
    pl.save_refresh_checkpoint = lambda *a, **kw: None
    errors, warnings = [], []
    try:
        pl.name_memo_load(dry)
        pl._stage_extract_impl(dry, {'id': source['video_id'], 'title': ''}, day,
                               raw, set(), set(), replace_video=False, v1=raw)
        errors.append('流程未到達寫入前候選節點')
    except Reached:
        pass
    except Exception as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
    finally:
        for n, value in old.items():
            setattr(pl, n, value)
    signals = captured.get('signals') or {}
    if captured.get('signals'):
        hard, warnings = invariants(signals, raw)
        overview, quality_hard, soft = pl.quality_overview(signals, raw)
        errors.extend(hard + quality_hard + publication_errors(signals))
        warnings.extend(soft)
        warnings.extend(str(x) for x in signals.get('_repair_gaps', []))
        for line in overview:
            print('品質概況：' + line)
        issues, report = reference_check(reference, day, raw, signals)
        errors.extend(issues)
        print(report)
    errors = list(dict.fromkeys(errors))
    waived = [e for e in errors if accept_model and e in MANAGER_WAIVABLE]
    if waived:
        errors = [e for e in errors if e not in waived]
        warnings.extend('管理者接受模型結果：' + e for e in waived)
        print('管理者指示以本次模型結果為準，以下改列警告：' + '；'.join(waived))
    payload = {'version': VERSION, 'date': day, 'commit': commit,
        'run_id': os.environ.get('GITHUB_RUN_ID', ''), 'mode': 'fresh_model_only',
        'answer_injection': False, 'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
        'video_id': source['video_id'], 'state': state,
        'reference_sha256': hashlib.sha256(reference.read_bytes()).hexdigest() if reference.exists() else '',
        'input_snapshot_sha256': {n: digest(ws.values) for n, ws in frozen.items()},
        'signals': signals, 'article': captured.get('article', ''),
        'manager_accepted': {'waived': waived, 'quarantined': sorted({str(r.get('code') or r.get('name') or '')
            for r in signals.get('_publication_quarantine') or [] if isinstance(r, dict)})} if waived else None,
        'errors': list(dict.fromkeys(errors)), 'warnings': list(dict.fromkeys(warnings)),
        'status': 'failed' if errors else 'passed', 'model_calls': pl._GEMINI_CALLS['n'],
        'usage': pl.gemini_usage_report(), 'blocked_writes': len(dry.blocked),
        'protected_sms_notes': '既有通知說明不由本候選改寫，不計為本輪模型產出'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(seal(payload), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(payload['usage'])
    print('模型候選', payload['status'], '指紋', digest(payload))
    for cat in ('buy', 'sell', 'holdings', 'watch_watch', 'watch_avoid'):
        for row in signals.get(cat, []):
            print(cat, row.get('code'), row.get('name'), row.get('reason') or row.get('note'))
    for error in payload['errors']:
        print('未通過：' + error)
    if errors:
        raise SystemExit(4)


def apply_candidate(book, payload, raw):
    """原子套用已保存內容；不呼叫模型或答案修補器，不改通知列。"""
    signals, day = payload['signals'], payload['date']
    before = {n: book.worksheet(n).get_all_values() for n in TABLES}
    after = plan_tables(before, signals, payload['article'], day, payload['video_id'], pl.norm_date,
                       lambda src: pl._is_sms_row(src) or pl._protected_nonvideo_row(src), pl.stance_zh)
    expected = signals.get('_article_records') or signals
    wanted = {(str(r.get('code') or ''), cat) for cat in pl.SIGNAL_CATEGORIES
              for r in expected.get(cat, []) or [] if (r.get('_date') or day) == day}
    actual = set()
    labels = {'買入': 'buy', '賣出': 'sell', '觀望注意': 'watch_watch', '觀望不碰': 'watch_avoid'}
    for name in TABLES[:2]:
        for cells in after[name][1:]:
            r = dict(zip(after[name][0], cells))
            if pl.norm_date(r.get('日期', '')) == day:
                actual.add((str(r.get('代號', '')), 'holdings' if name == '會員持股' else labels.get(r.get('方向'))))
    if actual != wanted:
        raise ValueError('保留來源與模型公開候選不同，禁止靠舊列補答：' + str(sorted(actual ^ wanted)))
    pending = publication_errors(signals)
    if pending and not ((payload.get('manager_accepted') or {}).get('waived') and set(pending) <= set(MANAGER_WAIVABLE)):
        raise ValueError('候選仍有未完成查核')
    backup_tables(pl, book, TABLES)
    if state_hashes(book, payload['state']) != payload['state']:
        raise ValueError('備份期間快照已變，未寫入')
    fresh = {}
    latest, _ = pl.existing_transcript(book, payload['video_id'], day, snapshot=fresh)
    if latest != raw:
        raise ValueError('備份期間原稿已變，未寫入')
    requests = cell_requests(before, after, {n: book.worksheet(n).id for n in TABLES})
    if requests:
        book.batch_update({'requests': requests})
    for name in TABLES:
        rows = book.worksheet(name).get_all_values()
        canon = lambda rs: [list(r) + [''] * max(0, len(after[name][0]) - len(r)) for r in rs if any(str(x) for x in r)]
        if canon(rows) != canon(after[name]):
            raise RuntimeError('正式讀回不符，須核對獨立備份：' + name)
    pl.save_evidence_audit(book, payload['video_id'], day, raw, signals)
    pl.commit_evidence_manifest(book, payload['video_id'], day, raw)
    ledgers = {n: h for n, h in payload['state'].items() if n in ('寄送帳本', 'LINE 寄送帳本')}
    if state_hashes(book, ledgers) != ledgers:
        raise RuntimeError('同期寄送帳本變動，須核對')
    result = pl.maybe_refresh_site(force=True, only=['smsmail', 'tracker', 'perfhist', 'perf'], date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('候選已寫入；只需續刷新，不再生成模型內容')
    print('同一模型候選已原子套用並讀回；通知列、寄送旗標與帳本保留，沒有重新生成或寄送')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--date', required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--candidate', default='')
    p.add_argument('--reference', default='')
    p.add_argument('--output', default='.model-candidate/candidate.json')
    p.add_argument('--raw', action='store_true', help='相容入口；所有預覽均不注入答案')
    p.add_argument('--rules-only', action='store_true', help='相容入口；所有預覽均不注入答案')
    p.add_argument('--accept-model', action='store_true', help='管理者明示以這一次模型結果為準：隔離與參考差異改列警告')
    a = p.parse_args()
    if a.apply and not a.candidate:
        raise SystemExit('發布必須提供已成功預覽的候選檔，不再重新生成或灌入答案')
    day = pl.norm_date(a.date)
    target = datetime.strptime(day, '%Y/%m/%d').date()
    today = datetime.now(pl.TAIPEI).date()
    if not today - timedelta(days=30) <= target <= today:
        raise SystemExit('限最近三十天既有原稿')
    reference = Path(a.reference) if a.reference else ROOT / 'scripts/golden' / (day.replace('/', '-') + '.json')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    book, source = pl.open_sheets(), {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    if not raw or not source.get('video_id'):
        raise SystemExit('原稿或影片ID不完整')
    names = {ws.title for ws in book.worksheets()}
    snapshots = {n: book.worksheet(n).get_all_values()
                 for n in sorted((INPUTS | {'寄送帳本', 'LINE 寄送帳本'}) & names)}
    state = {n: digest(rows) for n, rows in snapshots.items()}
    if a.apply:
        payload = validate(json.loads(Path(a.candidate).read_text(encoding='utf-8')), day=day, commit=commit,
            source_sha=hashlib.sha256(raw.encode()).hexdigest(), state=state,
            reference_sha=hashlib.sha256(reference.read_bytes()).hexdigest() if reference.exists() else '')
        if payload['run_id'] != os.environ.get('CANDIDATE_RUN'):
            raise SystemExit('artifact內的run ID與已驗證GitHub預覽不同')
        pl.call_gemini = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('發布階段禁止模型呼叫'))
        apply_candidate(book, payload, raw)
    else:
        preview(book, day, raw, source, commit, state, reference, Path(a.output), snapshots, accept_model=a.accept_model)


if __name__ == '__main__':
    main()
