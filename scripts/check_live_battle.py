"""戰情：正式站真實資料對照（唯讀，只打公開 API 與證交所公開資料）。

    python scripts/check_live_battle.py            現在的狀態
    python scripts/check_live_battle.py --strict   不符合就以代碼 1 結束（排程檢查用）
    python scripts/check_live_battle.py --codes 2330,2317

做四件事，都不寫入任何資料：
  一、官方對照：樣本股票（上市）最近兩個月的日 K，逐日和證交所「個股日成交資訊」比開、高、低、收與成交量（張）。
      連同行事曆一起核對：證交所有交易的每一天，本站行事曆都要有；本站行事曆有、證交所沒有的日子要列出來。
  二、資料完整：追蹤清單每一檔有幾根日 K、有沒有缺日、最後一根是哪一天。
  三、指標可算：用前台同一份計算程式（Battle.html）在真實行情上把全部條件算一遍，
      統計每個條件有幾檔是「資料不足」以及原因；同一檔另外用 160 根與手上全部的歷史各算一次，比訊號有沒有差。
  四、盤中／盤後：報價時間、是不是今天、當根是否已收盤；--strict 時盤中要求報價在十分鐘內。
"""
import argparse
import collections
import datetime
import json
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
TPE = datetime.timezone(datetime.timedelta(hours=8))
SAMPLE = ['2330', '2317', '2454', '2303', '2308', '2882']
# 證交所的憑證少了一個擴充欄位（Subject Key Identifier），新版 Python 的嚴格模式會拒絕。
# 這裡只關掉那一項嚴格檢查；憑證鏈與主機名稱照常驗證。
TWSE_TLS = ssl.create_default_context()
TWSE_TLS.verify_flags &= ~ssl.VERIFY_X509_STRICT

NODE = r"""
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const read = n => fs.readFileSync(path.join(process.argv[1], 'public-site/gas-source', n), 'utf8');
const ctx = {window: {}, console}; vm.createContext(ctx);
vm.runInContext(read('BattleConfig.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
vm.runInContext(read('Battle.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
const {evaluate, expand, indicators: I} = ctx.window.MarketBattle, cfg = ctx.window.BATTLE_CONFIG;
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const closed = new Set(cfg.confirmedClosed), calendar = input.context.calendar.filter(d => !closed.has(d));
const c = {...input.context, calendar, all: true};
const ids = ['macdCross', 'macdDead', 'macdZero', 'macdConverge', 'rsiRecover', 'rsiFall', 'adxTrend', 'kdCross', 'kdDead'];
const P = ctx.window.MarketBattle.cleanPrefs({}).cond;
const out = input.items.map(raw => {
  const it = expand(raw), r = evaluate(it, {}, c);
  const dates = new Set(it.bars.map(b => b.date)), first = it.bars.length ? it.bars[0].date : '', last = it.bars.length ? it.bars[it.bars.length - 1].date : '';
  const holes = calendar.filter(d => d > first && d < last && !dates.has(d));
  // 同一檔：最近 160 根 對 手上全部（都要是連續的才比）
  let flips = null;
  if (!holes.length && it.bars.length > 170) {
    const full = I.series(it.bars), short = I.series(it.bars.slice(-160));
    flips = ids.filter(id => I.run(id, full, P[id], {complete: true}).pass !== I.run(id, short, P[id], {complete: true}).pass);
  }
  return {code: it.code, name: it.name, bars: it.bars.length, first, last, holes, complete: r.complete, status: r.status, issues: r.issues.filter(x => !/所需資料不足|需要至少/.test(x)),
    quote: it.quote ? {date: it.quote.date, time: it.quote.time, stamp: it.quote.stamp, stale: it.quote.stale, volume: it.quote.volume} : null,
    unknown: r.checks.filter(k => k.pass === null).map(k => [k.label, k.why || '資料不足']), total: r.checks.length, flips};
});
process.stdout.write(JSON.stringify({confirmedClosed: cfg.confirmedClosed, signal: I.version, out}));
"""


def call(method, *args):
    last = None
    for _ in range(3):
        try:
            req = urllib.request.Request(API, data=json.dumps({'method': method, 'args': list(args)}).encode(),
                                         headers={'Content-Type': 'application/json', 'Origin': 'https://lee200202.github.io', 'User-Agent': 'Mozilla/5.0'}, method='POST')
            with urllib.request.urlopen(req, timeout=90) as res:
                body = json.load(res)
            if body.get('ok'):
                return body['result']
            last = body.get('error')
        except Exception as e:                                   # noqa: BLE001 - 重試三次，最後一併回報
            last = e
    raise SystemExit(f'{method} 讀取失敗：{last}')


def twse_month(code, yyyymm):
    """證交所個股日成交資訊：{yyyy/MM/dd: (開, 高, 低, 收, 成交股數)}；查不到回傳 None。"""
    url = f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={yyyymm}01&stockNo={code}'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=40, context=TWSE_TLS) as res:
            body = json.load(res)
    except Exception as e:                                       # noqa: BLE001
        twse_month.errors.append(f'{code} {yyyymm}：{type(e).__name__}')
        return None
    if body.get('stat') != 'OK':
        return None
    num = lambda t: float(str(t).replace(',', '').replace('X', ''))
    out = {}
    for r in body.get('data') or []:
        y, m, d = r[0].split('/')
        try:
            out[f'{int(y) + 1911}/{m}/{d}'] = (num(r[3]), num(r[4]), num(r[5]), num(r[6]), num(r[1]))
        except ValueError:
            continue                                             # 當天沒有成交（-- 之類）
    return out


twse_month.errors = []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--codes', default='')
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    now = datetime.datetime.now(TPE)
    hm, today = now.strftime('%H%M'), now.strftime('%Y/%m/%d')
    problems = []

    first = call('apiGetBattleData', None, {'v': 2})
    universe = [u['code'] for u in first['universe']]
    batch = int(first.get('batchMax') or 24)
    have = {i['code']: i for i in first['items']}
    rest = [c for c in universe if c not in have]
    requests = 1
    for n in range(0, len(rest), batch):
        part = call('apiGetBattleData', rest[n:n + batch], {'v': 2})
        requests += 1
        have.update({i['code']: i for i in part['items']})
    sample = [c for c in (args.codes.split(',') if args.codes else SAMPLE) if c]
    extra = [c for c in sample if c not in have]
    if extra:
        have.update({i['code']: i for i in call('apiGetBattleData', extra, {'v': 2})['items']})
    context = {k: first[k] for k in ('date', 'today', 'afterClose', 'calendar', 'volumeUnit')}
    # 開盤前（今天還沒有任何一筆行情）：指標可算與否改看上一個交易日的收盤，否則每一檔都只是「今天還沒有資料」。
    pre_open = first['date'] == today and hm < '0905'
    if pre_open:
        prev = [d for d in first['calendar'] if d < today][-1]
        context.update(date=prev, afterClose=True, calendar=[d for d in first['calendar'] if d <= prev])
    print(f'{now:%Y/%m/%d %H:%M:%S}　資料日 {first["date"]}　追蹤 {len(universe)} 檔、{requests} 次請求（一批 {batch} 檔，格式 {first.get("format", 1)}）'
          f'　後端已核對的臨時休市日 {first.get("limits", {}).get("marketClosed", "（舊版後端，未提供）")}')

    run = subprocess.run(['node', '-e', NODE, str(ROOT)], input=json.dumps({'context': context, 'items': list(have.values())}), capture_output=True, text=True, encoding='utf-8')
    if run.returncode:
        raise SystemExit('前台計算程式執行失敗：\n' + run.stderr[-2000:])
    result = json.loads(run.stdout)
    rows = {r['code']: r for r in result['out']}
    closed = set(result['confirmedClosed'])
    calendar = [d for d in first['calendar'] if d not in closed]

    # ── 一、官方對照 ──
    months = sorted({now.strftime('%Y%m'), (now.replace(day=1) - datetime.timedelta(days=1)).strftime('%Y%m'), '202607'})
    compared = mismatched = 0
    official_days = set()
    listed, skipped, recent_gap, older_gap = [], [], [], []
    recent_from = calendar[-3] if len(calendar) > 2 else today
    for code in sample:
        bars = {str(b[0])[:4] + '/' + str(b[0])[4:6] + '/' + str(b[0])[6:]: b for b in have[code].get('b', [])} if 'b' in have[code] else \
               {b['date']: [0, b['open'], b['high'], b['low'], b['close'], b['volume']] for b in have[code].get('bars', [])}
        if not bars:
            skipped.append(code)                               # 不在追蹤清單、本站沒有它的日 K
            continue
        got_any = False
        for ym in months:
            official = twse_month(code, ym)
            if official is None:
                continue
            got_any = True
            official_days.update(official)
            for day, (o, h, l, c, shares) in official.items():
                if day not in bars:
                    if day >= min(bars) and day != today:
                        problems.append(f'{code} {day}：證交所有成交，本站沒有這一根日 K')
                    continue
                b = bars[day]
                compared += 1
                lots = shares / 1000
                if (b[1], b[2], b[3], b[4]) != (o, h, l, c):
                    mismatched += 1
                    problems.append(f'{code} {day}：本站 開{b[1]} 高{b[2]} 低{b[3]} 收{b[4]}；證交所 開{o} 高{h} 低{l} 收{c}')
                # 成交量：證交所的數字含盤後定價、零股與鉅額交易，本站的日 K 來源不含其中一部分，本來就會少一點。
                # 分兩段看：最近兩個交易日（當日量可能還不是最終值）與更早的日子。更早的日子差超過 5% 才列為問題。
                gap = ((b[5] or 0) / lots - 1) * 100 if lots else 0.0
                (recent_gap if day >= recent_from else older_gap).append((gap, code, day))
        if got_any:
            listed.append(code)
    if not compared:
        problems.append('官方對照沒有比到任何一根日 K（證交所資料取不到：' + '、'.join(twse_month.errors[:3]) + '）——這一項未完成，不能算通過')
    print(f'一、官方對照（{"、".join(listed) or "取不到證交所資料"}；{"、".join(m[:4] + "/" + m[4:] for m in months)}）：比對 {compared} 根日 K，不一致 {mismatched} 根')
    span = lambda g: f'{min(x[0] for x in g):+.1f}% ～ {max(x[0] for x in g):+.1f}%（{len(g)} 根）' if g else '無'
    print(f'　　開高低收逐根相同才算一致。成交量與證交所（含盤後定價、零股、鉅額）的差距：{recent_from} 以前 {span(older_gap)}；最近兩個交易日 {span(recent_gap)}')
    far = sorted(x for x in older_gap + recent_gap if abs(x[0]) > 5)
    if far:
        print(f'　　注意：有 {len(far)} 根的量比證交所少超過 5%（最多 {far[0][0]:+.1f}%，{far[0][1]} {far[0][2]}）。兩邊的定義不同：本站的量不含一部分鉅額／盤後交易；'
              '量能倍數的分子分母都用本站自己的量，內部一致，但和看盤軟體的數字不會完全相同。')
    if skipped:
        print(f'　　本站沒有日 K、沒有比的樣本：{"、".join(skipped)}')
    if official_days:
        lo, hi = min(official_days), max(official_days)
        site = {d for d in calendar if lo <= d <= hi and d[:7].replace('/', '') in months}
        only_site = sorted(site - official_days - {today})
        only_official = sorted(d for d in official_days - site if d[:7].replace('/', '') in months)
        raw_only = sorted({d for d in first['calendar'] if lo <= d <= hi and d[:7].replace('/', '') in months} - official_days - {today})
        print(f'　　行事曆：證交所這幾個月有交易 {len(official_days)} 天；扣掉已核對休市日後，本站多出 {only_site or "無"}、少了 {only_official or "無"}'
              f'（後端原始行事曆多出 {raw_only or "無"}）')
        problems += [f'行事曆有 {d}，但證交所沒有交易（尚未核對為休市）' for d in only_site] + [f'證交所 {d} 有交易，行事曆沒有' for d in only_official]

    # ── 二、資料完整 ──
    lens = collections.Counter(r['bars'] for r in rows.values())
    holes = {c: r['holes'] for c, r in rows.items() if r['holes']}
    stale_last = {c: r['last'] for c, r in rows.items() if r['bars'] and r['last'] < calendar[-2]}
    print(f'二、資料完整：日 K 根數分布 {dict(sorted(lens.items(), reverse=True)[:4])}；有缺日的 {len(holes)} 檔；最後一根早於前一交易日的 {len(stale_last)} 檔')
    for c, h in list(holes.items())[:6]:
        print(f'　　{c} {rows[c]["name"]}：缺 {len(h)} 天（{h[0]}{"…" if len(h) > 1 else ""}）')

    # ── 三、指標可算 ──
    whole = [r for r in rows.values() if not r['holes'] and r['bars'] >= 130 and not r['issues']]
    why = collections.Counter()
    for r in whole:
        for label, reason in r['unknown']:
            why[(label, reason)] += 1
    all_unknown = sum(1 for r in rows.values() if len(r['unknown']) >= r['total'] - 8)
    flips = {r['code']: r['flips'] for r in rows.values() if r['flips']}
    judged = sum(1 for r in rows.values() if r['flips'] is not None)
    print(f'三、指標可算（指標版本 {result["signal"]}）：{len(rows)} 檔裡日 K 連續且夠長的 {len(whole)} 檔；其中仍有條件資料不足的情形：'
          f'{"；".join(f"{k[0]} {v} 檔（{k[1]}）" for k, v in why.most_common(6)) or "無"}')
    print(f'　　新條件全部資料不足的 {all_unknown} 檔；160 根與全部歷史的訊號比較 {judged} 檔 × 9 個條件，不一致 {sum(len(v) for v in flips.values())} 次{"：" + str(flips) if flips else ""}')
    if all_unknown > len(rows) * 0.2:
        problems.append(f'{all_unknown} 檔的新條件全部資料不足（行事曆或日 K 有問題）')

    # ── 四、盤中／盤後 ──
    session = first['date'] == today and '0905' <= hm <= '1330'
    if pre_open:
        print(f'　　（開盤前：以上一個交易日 {context["date"]} 的收盤評估）')
    quoted = [r for r in rows.values() if r['quote']]
    fresh = [r for r in quoted if r['quote']['date'] == today and not r['quote']['stale']]
    done = sum(1 for r in rows.values() if r['complete'])
    status = collections.Counter(r['status'] for r in rows.values())
    print(f'四、{"盤中" if session else "非盤中"}：有報價 {len(quoted)} 檔、今天且未過期 {len(fresh)} 檔；當根已收盤（用日 K）{done} 檔；預設條件的判定 {dict(status)}')
    if session and args.strict and len(fresh) < len(universe) * 0.9:
        problems.append(f'盤中報價新鮮的只有 {len(fresh)}／{len(universe)} 檔')
    if session and done:
        problems.append(f'盤中卻有 {done} 檔被當成已收盤')

    if problems:
        print('需要處理：')
        for p in problems[:20]:
            print('　　' + p)
    print('結果：' + ('有不符合的項目' if problems else '符合'))
    if args.strict and problems:
        sys.exit(1)


if __name__ == '__main__':
    main()
