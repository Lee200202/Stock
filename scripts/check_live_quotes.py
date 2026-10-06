"""正式站報價的即時性核對（唯讀，只打公開 API）。

    python scripts/check_live_quotes.py            現在的狀態
    python scripts/check_live_quotes.py --strict   不符合就以代碼 1 結束（排程檢查用）

盤中（09:05–13:30）：追蹤清單每一檔的報價應該都在十分鐘內（排程五分鐘一棒，留一棒的緩衝）。
收盤後（13:45 之後）：每一檔都應該是收盤價，而且和當天日K的收盤相同（日K落地後才比）。
其他時段只列出現況。
"""
import argparse
import collections
import datetime
import json
import sys
import urllib.request

API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
TPE = datetime.timezone(datetime.timedelta(hours=8))


def call(method, *args):
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(API, data=json.dumps({'method': method, 'args': list(args)}).encode(),
                                         headers={'Content-Type': 'application/json', 'Origin': 'https://lee200202.github.io',
                                                  'User-Agent': 'Mozilla/5.0'}, method='POST')
            with urllib.request.urlopen(req, timeout=90) as res:
                body = json.load(res)
            if body.get('ok'):
                return body['result']
            last = body.get('error')
        except Exception as e:                                   # noqa: BLE001 - 重試三次，最後一併回報
            last = e
    raise SystemExit(f'{method} 讀取失敗：{last}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    now = datetime.datetime.now(TPE)
    hm, today = now.strftime('%H%M'), now.strftime('%Y/%m/%d')

    first = call('apiGetBattleData', None)
    codes = [u['code'] for u in first['universe']]
    quotes, closes = {}, {}
    for i in range(0, len(codes), 50):
        quotes.update(call('apiGetQuotesFor', codes[i:i + 50]) or {})
    after_close = hm >= '1345' and first.get('date') == today
    if after_close:
        for i in range(0, len(codes), 24):
            for it in (call('apiGetBattleData', codes[i:i + 24]) or {}).get('items', []):
                bars = it.get('bars') or []
                if bars and bars[-1]['date'] == today:
                    closes[it['code']] = bars[-1]['close']

    ages, labels, not_today, missing, wrong = [], collections.Counter(), [], [], []
    for c in codes:
        q = quotes.get(c)
        if not q or q.get('last') is None:
            missing.append(c)
            continue
        t = str(q.get('time') or '')
        is_close = '收盤' in t or t[6:] >= '13:30:00'
        labels['收盤' if is_close else '盤中'] += 1
        if t[:5] != now.strftime('%m-%d'):
            not_today.append(c)
        elif not is_close:
            h, m, s = (int(x) for x in t[6:].split(':'))
            ages.append((now.hour * 3600 + now.minute * 60 + now.second - (h * 3600 + m * 60 + s)) / 60)
        if c in closes and abs(float(q['last']) - float(closes[c])) > 1e-6:
            wrong.append((c, q.get('name'), q['last'], closes[c], t))

    print(f"{now:%Y/%m/%d %H:%M:%S}　追蹤 {len(codes)} 檔　資料日 {first.get('date')}　{first.get('quoteCadence', '')}")
    print(f"報價：收盤 {labels['收盤']} 檔、盤中 {labels['盤中']} 檔、不是今天的 {len(not_today)} 檔、沒有報價 {len(missing)} 檔")
    problems = []
    if ages:
        old = sum(1 for a in ages if a > 10)
        print(f"盤中報價距現在：最新 {min(ages):.1f} 分、最舊 {max(ages):.1f} 分；超過十分鐘的 {old} 檔")
    if '0905' <= hm <= '1330' and first.get('date') == today:
        stale = sum(1 for a in ages if a > 10) + len(missing) + max(0, len(not_today) - 3)
        print(f"盤中核對：十分鐘內 {len(codes) - stale}／{len(codes)} 檔")
        if stale > len(codes) * 0.05:
            problems.append(f'盤中有 {stale} 檔超過十分鐘沒有更新')
    elif after_close:
        stuck = labels['盤中'] + len(missing)
        # 少數股票當天沒有任何成交資料（興櫃、暫停交易）：顯示的是它最後一個交易日的收盤，列出來但不算錯。
        print(f"收盤核對：是今天收盤價的 {len(codes) - stuck - len(not_today)}／{len(codes)} 檔；日K已落地 {len(closes)} 檔，其中與日K收盤不同的 {len(wrong)} 檔"
              + (f"；當天沒有成交資料的 {len(not_today)} 檔：{'、'.join(not_today[:8])}" if not_today else ''))
        for w in wrong[:10]:
            print('   ', w)
        if stuck or wrong or len(not_today) > max(3, len(codes) * 0.02):
            problems.append(f'收盤後有 {stuck} 檔不是收盤價、{len(wrong)} 檔與日K收盤不同、{len(not_today)} 檔不是今天的資料')
    else:
        print('現在不是盤中也不是收盤後的核對時段，只列出現況。')
    print('結果：' + ('；'.join(problems) if problems else '符合'))
    if problems and args.strict:
        sys.exit(1)


if __name__ == '__main__':
    main()
