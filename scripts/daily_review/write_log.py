"""第四步：寫複核紀錄、排通知、解除「複核中」。

    python scripts/daily_review/write_log.py .daily-review/<日期>/judged.json

judged.json（AI 看完 diffs.json 後填）：
  {"date": "2026/11/02", "run": "排程執行編號或說明", "trial": true,
   "pipeline_status": "主流程 11:21 寫完",
   "diffs": [{"code","name","site","review","accept","type","severity","quote","reason","action","before","after"}],
       severity：明確錯誤／待確認／邊界／複核錯；action：只記錄／待套用／待管理者
       （待套用＝修正計畫已推到 daily-review/apply-<日期>，套用成功後由 mark_fixed.py 改成已修正）
   "agree_count": 17,
   "report": "完整日報內文（純文字）：差異、修改方向與內容、主流程執行狀況、提示詞候選",
   "prompt_candidates": [{"type","quote","suggestion","test"}]}
通知：只排 12:30「每日複核日誌」一筆（管理者 2026/11/03：不另寄複核結果）。
Apps Script 不單獨寄它，而是在 12:30 起的「爬取狀態」信裡用卡片列出（讀每日摘要與差異明細）。
有待套用的修正時，每日摘要記「修正中」，郵件繼續等到套用完成（最晚 13:00）。
"""
import sys
from datetime import datetime, timedelta

from common import WORK, norm_day, now, open_log, read_json, rows_of

SEVERE = ('明確錯誤', '待確認')


def summary_status(diffs):
    """有修正等著套用就是「修正中」（郵件繼續等），否則「已完成」。"""
    return '修正中' if any(d.get('action') == '待套用' for d in diffs) else '已完成'


def main():
    judged = read_json(sys.argv[1])
    day = norm_day(judged['date'])
    out = WORK / day.replace('/', '')
    digest = (out / 'reading.sha256').read_text(encoding='utf-8').split()[0] if (out / 'reading.sha256').exists() else ''
    status = read_json(out / 'status.json') if (out / 'status.json').exists() else {}
    log = open_log()
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    run = judged.get('run', '')

    diffs = judged.get('diffs') or []
    detail = log.worksheet('差異明細')
    if diffs:
        detail.append_rows([[day, d.get('code', ''), d.get('name', ''), d.get('site', ''), d.get('review', ''),
                             '／'.join(d.get('accept') or []), d.get('type', ''), d.get('severity', ''), d.get('quote', ''),
                             d.get('reason', ''), d.get('action', ''), d.get('before', ''), d.get('after', ''), run, '否']
                            for d in diffs], value_input_option='RAW')

    # 近兩週同類（明確錯誤／待確認）次數；達 3 次就列提示詞候選
    _, history = rows_of(detail)
    since = (datetime.strptime(day, '%Y/%m/%d') - timedelta(days=14)).strftime('%Y/%m/%d')
    counts = {}
    for r in history:
        if norm_day(r.get('日期')) >= since and r.get('嚴重度') in SEVERE:
            counts[r.get('差異類型')] = counts.get(r.get('差異類型'), 0) + 1
    candidates = list(judged.get('prompt_candidates') or [])
    for kind, n in counts.items():
        if n >= 3 and not any(c.get('type') == kind for c in candidates):
            candidates.append({'type': kind, 'quote': '', 'suggestion': f'近兩週同類 {n} 次，請整理反例與規則修改', 'test': ''})
    if candidates:
        log.worksheet('提示詞候選').append_rows([[day, c.get('type', ''), counts.get(c.get('type'), 0), c.get('quote', ''),
                                                c.get('suggestion', ''), c.get('test', ''), '待管理者'] for c in candidates],
                                               value_input_option='RAW')

    count = lambda sev: sum(1 for d in diffs if d.get('severity') == sev)
    fixed = sum(1 for d in diffs if d.get('action') == '已修正')
    summary = log.worksheet('每日摘要')
    head, rows = rows_of(summary)
    values = {'日期': day, '狀態': summary_status(diffs), '完成時間': now().strftime('%H:%M:%S'),
              '主流程狀態': judged.get('pipeline_status', ''), '郵件狀態': status.get('mail_status', ''),
              '比對檔數': len(diffs) + int(judged.get('agree_count') or 0), '明確錯誤': count('明確錯誤'),
              '待確認': count('待確認'), '邊界': count('邊界'), '複核錯': count('複核錯'), '已修正': fixed,
              '判讀雜湊': digest, '執行': run, '備註': '試行' if judged.get('trial') else ''}
    idx = next((i for i, r in enumerate(rows, 2) if norm_day(r.get('日期')) == day), None)
    if idx is None:
        summary.append_row([values.get(h, '') for h in head], value_input_option='RAW')
    else:
        current = rows[idx - 2]
        merged = [values.get(h, current.get(h, '')) if h != '開始時間' else current.get(h, '') for h in head]
        summary.update([merged], f'A{idx}')

    report = judged.get('report') or ''
    flags = f"明確錯誤 {count('明確錯誤')}、待確認 {count('待確認')}、邊界 {count('邊界')}、複核錯 {count('複核錯')}、已修正 {fixed}"
    notices = []
    at_1230 = datetime.strptime(day, '%Y/%m/%d').strftime('%Y/%m/%d') + ' 12:30:00'
    notices.append([stamp, day, '每日複核日誌', max(at_1230, stamp), f'[{day}] 每日複核日誌　{flags}', report, '待寄', ''])
    log.worksheet('通知').append_rows(notices, value_input_option='RAW')
    print('LOGGED', day, values['狀態'], flags, '12:30 併入爬取狀態信')


if __name__ == '__main__':
    main()
