"""持股追蹤取價稽核（v145）：進場價、出場價是不是「當日最高與最低的平均」。唯讀，只用公開 API。

不沿用後端的計算：每一檔、每一個回合，直接拿日 K 的最高與最低重算，逐一比對
  一、回合記下的當日區間（entryLo／entryHi、exitLo／exitHi）和日 K 那一天的最低、最高一致；
  二、進場價、出場價等於那一天最高與最低的平均（四捨五入到小數兩位）；
  三、已結束回合的報酬等於（出場價－進場價）÷進場價；持有中的報酬等於（目前價－進場價）÷進場價；
  四、後端自己留下的稽核結果（priceAudit）是「當日高低平均」、不符為 0；
  五、已出場清單裡沒有「同一天進場、同一天因未再提及而出場」的列。
管理者在後台修正過成本的回合，進場價以管理者填的為準，另外列出來、不算不符。

    python scripts/check_tracker_midprice.py            正式站；有不符時結束代碼 1
    python scripts/check_tracker_midprice.py --rule old  用舊規則（進場最低、出場最高）比，確認換版前後的差別
"""
import argparse
import json
import sys
import time
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'


def call(method, *args):
    body = json.dumps({'method': method, 'args': list(args)}).encode()
    last = None
    for attempt in range(6):
        try:
            req = urllib.request.Request(API, data=body, headers={'Content-Type': 'application/json', 'Origin': ORIGIN,
                                                                     'User-Agent': 'Mozilla/5.0 (stock-site audit; read-only)'})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.loads(r.read())
            if data.get('ok') and data.get('result') is not None:
                return data['result']
            last = data
        except OSError as e:
            last = e
        time.sleep(4 + attempt * 4)
    raise SystemExit(f'{method}{args} 讀取失敗：{str(last)[:120]}')


def cents(v):
    return int((Decimal(str(v)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def mid(lo, hi):
    """最高與最低的平均，四捨五入到小數兩位（半分進位）。"""
    return float((Decimal(cents(lo) + cents(hi)) / 2).quantize(Decimal('1'), rounding=ROUND_HALF_UP) / 100)


def pct(a, b):
    return float(((Decimal(str(b)) - Decimal(str(a))) / Decimal(str(a)) * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rule', choices=['mid', 'old'], default='mid')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    tracker = call('apiGetDashboard')['tracker']
    items = (tracker.get('held') or []) + (tracker.get('exited') or [])
    bad, notes, checked, overridden, rounds_total, moved = [], [], 0, [], 0, []
    for item in items:
        code, name = str(item.get('code')), item.get('name')
        if not item.get('valid', True):
            continue
        summary = call('apiGetStockSummary', code)
        t = (summary or {}).get('tracker') or {}
        rounds = t.get('roundList') or []
        if not rounds:
            notes.append(f'{name}（{code}）沒有回合明細，略過')
            continue
        bars = {str(k['date'])[:10]: k for k in (call('apiGetCandlesBundle', code).get('day') or [])}
        rets = [float(x.replace('−', '-').replace('－', '-')) for x in __import__('re').findall(r'([+\-−－]?\d+(?:\.\d+)?)\s*%', str(t.get('roundRets') or ''))]
        admin = '管理者' in str(t.get('entrySrc') or '') or (t.get('entrySrc') and '當日' not in str(t.get('entrySrc')) and len(rounds) and rounds[-1].get('entryLo') is not None
                                                           and float(t.get('entry') or 0) not in (mid(rounds[-1]['entryLo'], rounds[-1]['entryHi']), float(rounds[-1]['entryLo'])))
        for k, r in enumerate(rounds):
            rounds_total += 1
            last = k == len(rounds) - 1
            who = f'{name}（{code}）第 {k + 1} 回合'
            for side, day, price, lo, hi in (('進場', r.get('entryDate') or r.get('open'), r.get('entry'), r.get('entryLo'), r.get('entryHi')),
                                             ('出場', r.get('exitTradeDate') or r.get('exitDate'), r.get('exit'), r.get('exitLo'), r.get('exitHi'))):
                if price in (None, '') or lo in (None, '') or hi in (None, ''):
                    if side == '進場' or r.get('exitDate'):
                        notes.append(f'{who}{side}沒有當日區間（缺日K），略過')
                    continue
                bar = bars.get(str(day))
                if bar and side == '出場' and not r.get('exitTradeDate') and (cents(bar['low']) != cents(lo) or cents(bar['high']) != cents(hi)):
                    # 舊資料沒有記出場取價日：條件價的觸價日或往前取的那一天不等於出場日，只核對回合自己記下的區間。
                    notes.append(f'{who}出場取價日不是 {day}（舊資料未記取價日），只核對回合記下的區間 {lo}～{hi}')
                elif bar and (cents(bar['low']) != cents(lo) or cents(bar['high']) != cents(hi)):
                    bad.append(f'{who}{side} {day} 的區間 {lo}～{hi} 和日K的 {bar["low"]}～{bar["high"]} 不同')
                    continue
                elif not bar:
                    notes.append(f'{who}{side} {day} 公開日K沒有這一天，只核對回合自己記下的區間')
                want = mid(lo, hi) if args.rule == 'mid' else float(lo if side == '進場' else hi)
                if side == '進場' and last and admin:
                    overridden.append(f'{who} 進場價 {t.get("entry")}（管理者修正；系統取價為 {want}）')
                    continue
                checked += 1
                if cents(price) != cents(want):
                    bad.append(f'{who}{side}價 {price} ≠ {"高低平均" if args.rule == "mid" else "舊規則"} {want}（{day} 最低 {lo}、最高 {hi}）')
                elif args.rule == 'mid' and cents(price) != cents(lo if side == '進場' else hi):
                    moved.append((who, side, float(lo if side == '進場' else hi), float(price)))
            # 報酬
            entry = t.get('entry') if last else r.get('entry')
            end = (t.get('current') if (last and t.get('stillHeld')) else r.get('exit'))
            shown = t.get('ret') if last else (rets[k] if len(rets) == len(rounds) else None)
            if entry and end and shown is not None:
                checked += 1
                if abs(pct(entry, end) - float(shown)) > 0.011:
                    bad.append(f'{who}報酬 {shown}% ≠ （{end}－{entry}）÷{entry}＝{pct(entry, end)}%')
    # 五、已出場清單裡不該有「同一天進場、同一天因未再提及而出場」的列（只提到一天的買進不算一段持有，v146）。
    for item in tracker.get('exited') or []:
        if '未再提及' in str(item.get('exitReason') or '') and item.get('entryDate') and item.get('entryDate') == item.get('lastSell'):
            bad.append(f"{item.get('name')}（{item.get('code')}）{item.get('entryDate')} 同一天進場、同一天因未再提及出場，不應列在已出場")
    audit = tracker.get('priceAudit')
    print(f'持股追蹤 {len(items)} 檔、{rounds_total} 個回合；核對 {checked} 個價位與報酬（規則：{"當日最高與最低的平均" if args.rule == "mid" else "舊規則：進場最低、出場最高"}）')
    if args.rule == 'mid':
        if not audit:
            bad.append('後端沒有留下取價稽核結果（priceAudit）：重算還沒有用新規則跑過')
        else:
            print(f'後端自我稽核：規則「{audit.get("rule")}」，核對 {audit.get("checked")} 個、不符 {audit.get("mismatch")} 個，{audit.get("at")} 重算')
            if audit.get('rule') != '當日高低平均' or audit.get('mismatch'):
                bad.append(f'後端自我稽核不符：{json.dumps(audit, ensure_ascii=False)[:160]}')
    for line in overridden:
        print('  管理者修正成本（不算不符）：' + line)
    for line in notes[:12]:
        print('  ・' + line)
    if moved:
        print(f'  和舊規則相比有 {len(moved)} 個價位不同，例如：' + '；'.join(f'{w}{s} {a}→{b}' for w, s, a, b in moved[:4]))
    if bad:
        print(f'不符 {len(bad)} 項：')
        for line in bad[:40]:
            print('  ✗ ' + line)
        raise SystemExit(1)
    print('結果：全部符合')


if __name__ == '__main__':
    main()
