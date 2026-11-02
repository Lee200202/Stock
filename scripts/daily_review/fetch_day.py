"""第一步：取當日輸入包，不讀今天的流程結果。

    python scripts/daily_review/fetch_day.py [--date YYYY/MM/DD] [--hold]

輸出（.daily-review/<日期>/）：
  raw.txt        主流程實際讀的那一份原始逐字稿（pipeline.existing_transcript）
  polished.txt   修飾後逐字稿（只輔助辨認同音字）
  notices.txt    當日會員通知原文（只用來判斷部位；公開內容不得出現通知才有的用語）
  positions.json 今天以前的部位：最近一筆為買入、15 天內持股列、較舊的持股列
  status.json    主流程是否已寫完當天、郵件狀態、原稿指紋
--hold：主流程已寫完時，在複核紀錄「每日摘要」把今天設為「複核中」（寄信程式會等，最晚 13:00）。
主流程還沒寫完時印出 NOT_READY 並以代碼 3 結束，呼叫端過幾分鐘再試；非交易日印出 NO_TRADING_DAY、代碼 4；
11:40 當下沒有原始／修飾後逐字稿、而且沒有抓到影片的列（或已標「今日無影片」）印出 NO_VIDEO、代碼 5，
並在「每日摘要」記「未執行」——呼叫端立即結束，不判讀。
"""
import argparse
import json
import sys

from common import WORK, DIRECTION, norm_day, now, open_book, open_log, pipeline, rows_of, sha, today, write_json


def github_runs_today(day):
    """GitHub 上今天逐字稿工作流程跑了幾次（公開 API，不需權杖）；讀不到就回說明文字。"""
    import urllib.request
    url = ('https://api.github.com/repos/Lee200202/Stock/actions/workflows/transcript.yml/runs?per_page=30&created=%3E%3D'
           + day.replace('/', '-'))
    try:
        data = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'daily-review'}), timeout=20))
        runs = data.get('workflow_runs') or []
        done = [r for r in runs if r.get('status') == 'completed']
        return f"執行 {len(runs)} 次（完成 {len(done)} 次）" if runs else '沒有執行紀錄'
    except Exception as e:
        return f'讀不到（{type(e).__name__}）'


def mark_summary(day, state, note=''):
    """在複核紀錄「每日摘要」寫下今天的狀態（複核中／未執行）。"""
    log = open_log()
    ws = log.worksheet('每日摘要')
    head, rows = rows_of(ws)
    stamp = now().strftime('%H:%M:%S')
    idx = next((i for i, r in enumerate(rows, 2) if norm_day(r.get('日期')) == day), None)
    values = {'日期': day, '狀態': state, '開始時間': stamp, '備註': note}
    if idx is None:
        ws.append_row([values.get(h, '') for h in head], value_input_option='RAW')
    else:
        current = rows[idx - 2]
        ws.update([[values.get(h, current.get(h, '')) for h in head]], f'A{idx}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default='')
    ap.add_argument('--hold', action='store_true')
    a = ap.parse_args()
    day = norm_day(a.date) if a.date else today()
    from datetime import datetime as _dt
    sys.path.insert(0, str(__import__('common').ROOT / 'pipeline'))
    from market_holidays import is_trading_day, why_closed
    if not is_trading_day(_dt.strptime(day, '%Y/%m/%d').date()):
        print('NO_TRADING_DAY', day, why_closed(_dt.strptime(day, '%Y/%m/%d').date()))
        sys.exit(4)
    pl = pipeline()
    book = open_book(pl)
    out = WORK / day.replace('/', '')

    source = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    videos = [r for r in rows_of(book.worksheet('影片清單'))[1] if norm_day(r.get('發布日期')) == day]
    has_text = any(str(r.get('原始逐字稿內容') or '').strip() or str(r.get('修飾後逐字稿內容') or '').strip() for r in videos)
    marked_none = any(str(r.get('處理狀態') or '') == '今日無影片' for r in videos)
    if not has_text and (not videos or marked_none):
        # 管理者 2026/11/02：11:40 當下沒有原始／修飾後逐字稿，GitHub 也沒抓到影片（颱風、國定假日、休播）→ 今天不複核，省用量
        runs = github_runs_today(day)
        reason = ('今日無影片：11:40 時影片清單沒有今天的逐字稿' + ('，且已標「今日無影片」' if marked_none else '，也沒有抓到影片的列')
                  + f'；GitHub 逐字稿抓取今天 {runs}')
        print('NO_VIDEO', day, reason)
        if a.hold:
            mark_summary(day, '未執行', reason)
        sys.exit(5)
    video = next((r for r in videos if str(r.get('影片ID')) == source.get('video_id')), videos[0] if videos else {})
    article_row = next((r for r in rows_of(book.worksheet('每日推播內容'))[1] if norm_day(r.get('日期')) == day), None)
    status = {
        'date': day, 'video_id': source.get('video_id', ''), 'raw_sha256': sha(raw) if raw else '',
        'raw_chars': len(raw), 'video_status': str(video.get('處理狀態') or ''),
        'article_written': bool(article_row and str(article_row.get('文字稿') or '').strip()),
        'mail_status': str((article_row or {}).get('寄送狀態') or ''), 'checked_at': now().strftime('%Y/%m/%d %H:%M:%S'),
    }
    ready = bool(raw) and status['video_status'] == '完成' and status['article_written']
    status['ready'] = ready
    write_json(out / 'status.json', status)
    if not ready:
        print('NOT_READY', json.dumps(status, ensure_ascii=False))
        sys.exit(3)

    (out / 'raw.txt').write_text(raw, encoding='utf-8')
    (out / 'polished.txt').write_text(str(video.get('修飾後逐字稿內容') or ''), encoding='utf-8')
    notices = [r for r in rows_of(book.worksheet('會員簡訊'))[1] if norm_day(r.get('發文時間')) == day]
    (out / 'notices.txt').write_text(''.join(f"{r.get('發文時間')}｜{r.get('標題')}\n{r.get('原文')}\n----\n" for r in notices),
                                     encoding='utf-8')

    # 部位只看今天以前；今天的列是流程輸出，判讀凍結前不讀。
    last, holds = {}, {}
    for r in rows_of(book.worksheet('操作紀錄'))[1]:
        d, code = norm_day(r.get('日期')), str(r.get('代號') or '').strip()
        if code and d < day and DIRECTION.get(str(r.get('方向') or '')) in ('buy', 'sell'):
            if code not in last or d >= last[code][0]:
                last[code] = (d, DIRECTION[str(r.get('方向'))], str(r.get('股票名稱') or ''))
    for r in rows_of(book.worksheet('會員持股'))[1]:
        d, code = norm_day(r.get('日期')), str(r.get('代號') or '').strip()
        if code and d < day and (code not in holds or d > holds[code][0]):
            holds[code] = (d, str(r.get('股票名稱') or ''))
    from datetime import datetime, timedelta
    cutoff = (datetime.strptime(day, '%Y/%m/%d') - timedelta(days=15)).strftime('%Y/%m/%d')
    positions = {
        'open_by_last_buy': {c: {'date': v[0], 'name': v[2]} for c, v in last.items() if v[1] == 'buy'},
        'holdings_rows_last_15_days': {c: {'date': v[0], 'name': v[1]} for c, v in holds.items() if v[0] >= cutoff},
        'older_holdings_rows': {c: {'date': v[0], 'name': v[1]} for c, v in holds.items() if v[0] < cutoff},
        'note': '持股要有紀錄（10/28 裁示）；讀不到紀錄是「不知道」，不等於沒有部位',
    }
    write_json(out / 'positions.json', positions)
    print('READY', json.dumps({k: status[k] for k in ('date', 'video_id', 'raw_chars', 'mail_status')}, ensure_ascii=False),
          f"通知 {len(notices)} 則；近期部位 {len(positions['open_by_last_buy']) + len(positions['holdings_rows_last_15_days'])} 筆")
    print('輸入包：', out)

    if a.hold:
        mark_summary(day, '複核中')
        print('已設定複核中（寄信最晚等到 13:00）')


if __name__ == '__main__':
    main()
