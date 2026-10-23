"""定向重查已隔離官方月資料；只有官方回覆有效更正才替換原列。"""
import argparse
import json
from pathlib import Path
import re
import time
try:
    from . import market_history as history
except ImportError:
    import market_history as history


def recheck(archive, meta, report_dir, limit=12, budget_seconds=240, fetch=None):
    archive, report_dir = Path(archive).resolve(), Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    fetch = fetch or history.fetch_tpex_month
    excluded = json.loads(Path(__file__).with_name('market_history_exclusions.json').read_text(encoding='utf-8'))
    groups = {}
    for row in meta.get('quarantinedRows', []):
        groups.setdefault(row.get('sourceFile', ''), []).append(row)
    result = {'attemptedMonths': 0, 'repairedRows': 0, 'keptCooldownMonths': 0,
              'failedMonths': 0, 'pendingMonths': 0, 'months': [], 'published': False}
    started = time.monotonic()
    for name, targets in sorted(groups.items()):
        match = re.fullmatch(r'tpex/(\d{4,6}[A-Z]?)/(\d{4})(\d{2})\.json', name)
        if not match or match[1] in excluded:
            continue
        path = archive / name
        cached = history._read(path)
        if not cached:
            result['months'].append({'sourceFile': name, 'status': '原封存缺少，交回一般補抓'})
            continue
        cooldown = 86400 if cached.get('qualityRecheckStatus') == 'failed' else 7*86400
        if history._age(cached.get('qualityRecheckedAt') or cached.get('fetchedAt')) < cooldown:
            result['keptCooldownMonths'] += 1
            continue
        if result['attemptedMonths'] >= limit or time.monotonic()-started >= budget_seconds:
            result['pendingMonths'] += 1
            continue
        result['attemptedMonths'] += 1
        detail = {'sourceFile': name, 'checkedAt': history._iso_now(), 'repaired': [], 'unresolved': []}
        try:
            rec = fetch(match[1], int(match[2]), int(match[3]))
            # 留下實際解析回覆；來源回空也不刪除舊封存。
            history._write(report_dir / f'{match[1]}-{match[2]}{match[3]}-response.json', rec)
            if not rec.get('rows'):
                raise ValueError('本次來源未回有效列，不能當成沒有歷史')
            latest = history._read(path)
            if not latest:
                raise ValueError('回查期間原封存已不可讀，停止採用')
            cached = latest
            old = {r[0]: r for r in cached.get('rows', [])}
            fresh = {r[0]: r for r in rec['rows']}
            for target in targets:
                day = target['date']
                before, after = old.get(day), fresh.get(day)
                if before != target.get('raw'):
                    detail['unresolved'].append({'date': day, 'reason': '原列指紋已變，不覆寫其他工作'})
                    continue
                if after is None or after[0][:7] != f'{match[2]}-{match[3]}' or history.history_row_issue(after):
                    detail['unresolved'].append({'date': day, 'reason': '官方未提供有效更正'})
                    continue
                old[day] = after
                detail['repaired'].append({'date': day, 'before': before, 'after': after,
                    'source': f'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={match[1]}&date={match[2]}/{match[3]}/01&response=json'})
            cached['rows'] = [old[d] for d in sorted(old)]
            cached['qualityRecheckStatus'] = 'checked'
            if detail['repaired']:
                cached.setdefault('qualityRepairs', []).extend(detail['repaired'])
                result['repairedRows'] += len(detail['repaired'])
        except Exception as exc:
            result['failedMonths'] += 1
            latest = history._read(path)
            if latest:
                cached = latest
            cached['qualityRecheckStatus'] = 'failed'
            detail['error'] = type(exc).__name__ + ': ' + str(exc)[:180]
        cached['qualityRecheckedAt'] = detail['checkedAt']
        history._write(path, cached)
        result['months'].append(detail)
        print(f'官方異常月定向複核 {name}：有效更正 {len(detail["repaired"])} 筆，未採用 {len(detail["unresolved"])} 筆', flush=True)
    history._write(report_dir/'source-recheck.json', result)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--archive', required=True)
    p.add_argument('--meta', required=True)
    p.add_argument('--report-dir', required=True)
    args = p.parse_args()
    result = recheck(args.archive, json.loads(Path(args.meta).read_text(encoding='utf-8')), args.report_dir)
    if result['failedMonths']:
        print(f'::warning::官方異常月複查有{result["failedMonths"]}個來源失敗，原封存保留，未視為修復成功。')
    print(json.dumps(result, ensure_ascii=False))
