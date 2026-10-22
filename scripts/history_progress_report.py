"""GitHub 補抓摘要：探查比例、封存增量與發布狀態分開。"""
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def snapshot(data):
    root = Path(data)
    meta = read(root / 'history/_meta.json')
    return {'twse': len(list((root / 'archive/twse').glob('*/*.json.gz'))),
            'tpex': len(list((root / 'archive/tpex').glob('*/*.json'))),
            'files': len(meta.get('codes', {})), 'requested': len(meta.get('requestedCodes', [])),
            'bars': sum(c.get('bars', 0) for c in meta.get('codes', {}).values()),
            'missing': meta.get('missingCodes', []), 'quarantined': len(meta.get('quarantinedRows', []))}


def render(data, logs):
    root = Path(logs)
    baseline = read(root / 'baseline.json')
    current = snapshot(data)
    lines = ['# 完整歷史日K：增量補齊進度',
             datetime.now(ZoneInfo('Asia/Taipei')).strftime('更新：%Y/%m/%d %H:%M:%S（台北）'), '',
             '**探查100%只代表本輪範圍已走訪，不代表全歷史已核實或網站已發布。**', '',
             '| 階段／市場 | 探查進度 | 尚餘 | 新增檔 | 複核／更新 | 沿用 | 空回應待核 | 失敗 | 目前位置 |',
             '|---|---|---:|---:|---:|---:|---:|---:|---|']
    for p in sorted(root.glob('*-progress.json')):
        s = read(p)
        pct = min(100, max(0, s.get('percent', 0)))
        bar = '█' * int(pct / 5) + '░' * (20-int(pct / 5))
        phase = {'running': '進行中', 'paused': '預算到，待續補', 'scanned': '已走訪，非完整核實'}.get(s.get('phase'), '待開始')
        lines.append(f'| {p.stem.replace("-progress", "")}（{phase}） | `{bar}` {pct}% · {s.get("processed", 0)}/{s.get("total", 0)} | {s.get("total", 0)-s.get("processed", 0)} | {s.get("new", 0)} | {s.get("refreshed", 0)} | {s.get("kept", 0)} | {s.get("emptyUnverified", 0)} | {s.get("failed", 0)} | {s.get("current", "—")} |')
    lines += ['', '## 與本次開始比較', '', '| 指標 | 開始時 | 目前 | 增加 |', '|---|---:|---:|---:|']
    for key, label in [('twse', '上市日封存'), ('tpex', '上櫃月封存'), ('files', '已組裝個股'), ('bars', '已組裝有效日K')]:
        a, b = baseline.get(key, 0), current[key]
        lines.append(f'| {label} | {a:,} | {b:,} | {b-a:+,} |')
    lines += ['', f'要求代號：{current["requested"]}；已組裝：{current["files"]}；缺少：{len(current["missing"])}；矛盾來源隔離：{current["quarantined"]}筆。',
              '缺少代號：' + ('、'.join(current['missing']) or '目前沒有缺少整檔；仍需檢查各檔來源起點與日期缺口。'), '',
              '## 保存與發布（不與抓取混為一談）', '']
    for p in sorted(root.glob('publication-*.json')):
        s = read(p)
        status = {'failed': '失敗（failed）', 'saving_archive': '保存原始封存中', 'building': '驗證組裝中', 'data_saved': '有效資料已推送', 'awaiting_pages': '等待Pages發布', 'unchanged': '無變更，沿用網站'}.get(s.get('status'), '未知')
        lines.append(f'- **{p.stem}：{status}**；封存已推送={s.get("archiveSaved", False)}；資料分支={s.get("dataSha", "—")[:10]}；Pages已要求發布={s.get("pagesDispatched", False)}。{s.get("error", "")}')
    lines += ['', 'Pages已要求發布不代表正式網址已讀回；完整度仍以正式 `_meta.json`、逐檔資料與官方邊界核對。',
              '舊檔沿用不是白工：避免重打來源。新增檔含待核空回應，不能當成新增交易日；請同看有效日K增量與待核欄。']
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--logs', required=True)
    p.add_argument('--baseline', action='store_true')
    args = p.parse_args()
    Path(args.logs).mkdir(parents=True, exist_ok=True)
    if args.baseline:
        Path(args.logs, 'baseline.json').write_text(json.dumps(snapshot(args.data)), encoding='utf-8')
    report = render(args.data, args.logs)
    Path(args.logs, 'progress-summary.md').write_text(report, encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        Path(summary).write_text(report, encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
