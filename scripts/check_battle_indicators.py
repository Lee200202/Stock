"""戰情指標的獨立核對（v137）。

前台（Battle.html）的指標是 JavaScript 寫的；這裡用 Python 照定義另外寫一份，兩邊對同樣的日 K 逐根比對。
兩份程式沒有共用任何一行，數字要一樣才算過。另外有幾組用手算得出答案的小例子。
只讀本機檔案，不連網、不寫入任何資料。

定義（和 Battle.html 開頭註解一致）：
  SMA 簡單平均；EMA 以第一筆為種子、k = 2／(n+1)
  Wilder 平滑：前 n 筆的平均為第一個值，之後 prev + (v − prev)／n（RSI、ATR、ADX）
  KD：台股 9／3／3，RSV 以 9 根高低計算，K、D 各以 1/3 遞迴平滑，種子 50
  MACD：12／26／9，柱體 = (DIF − DEA) × 2
  布林：20 根、母體標準差 2 倍；帶寬 = (上軌 − 下軌)／中軌
"""
import json
import math
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

NODE = r"""
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const read = n => fs.readFileSync(path.join(process.argv[1], 'public-site/gas-source', n), 'utf8');
const ctx = {window: {}, console}; vm.createContext(ctx);
vm.runInContext(read('BattleConfig.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
vm.runInContext(read('Battle.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
const I = ctx.window.MarketBattle.indicators;
const out = JSON.parse(fs.readFileSync(0, 'utf8')).map(bars => {
  const S = I.series(bars), d = I.dmi(S, 14), m = I.macd(S), k = I.kd(S), b = I.boll(S, 20, 2);
  return {sma5: I.sma(S, 5), sma20: I.sma(S, 20), sma60: I.sma(S, 60), atr14: I.atr(S, 14), atr22: I.atr(S, 22), rsi14: I.rsi(S, 14),
    pdi: d.pdi, mdi: d.mdi, adx: d.adx, dif: m.dif, dea: m.dea, hist: m.hist, k: k.k, d: k.d, obv: I.obv(S), cmf20: I.cmf(S, 20),
    cci20: I.cci(S, 20), mfi14: I.mfi(S, 14), roc12: I.roc(S, 12), bmid: b.mid, bup: b.up, blo: b.lo, bwidth: b.width,
    pos120: bars.map((_, i) => I.rangePos(S, i, 120)), pivl: I.pivots(S, true), pivh: I.pivots(S, false), version: I.version};
});
process.stdout.write(JSON.stringify(out));
"""


# ── 參考實作：照定義寫，不求快 ──
def sma(a, n):
    return [None if i < n - 1 else sum(a[i - n + 1:i + 1]) / n for i in range(len(a))]


def ema(a, n):
    k, out = 2 / (n + 1), []
    for v in a:
        out.append(v if not out else v * k + out[-1] * (1 - k))
    return out


def wilder(a, n, start):
    out = [None] * len(a)
    for i in range(start + n - 1, len(a)):
        out[i] = sum(a[start:start + n]) / n if i == start + n - 1 else out[i - 1] + (a[i] - out[i - 1]) / n
    return out


def reference(bars):
    O = [b['open'] for b in bars]; H = [b['high'] for b in bars]; L = [b['low'] for b in bars]
    C = [b['close'] for b in bars]; V = [b['volume'] for b in bars]; n = len(bars)
    tr = [H[i] - L[i] if i == 0 else max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(n)]
    gain = [0] + [max(C[i] - C[i - 1], 0) for i in range(1, n)]
    loss = [0] + [max(C[i - 1] - C[i], 0) for i in range(1, n)]
    ag, al = wilder(gain, 14, 1), wilder(loss, 14, 1)
    rsi = [None if ag[i] is None else (None if ag[i] == 0 else 100.0) if al[i] == 0 else 100 - 100 / (1 + ag[i] / al[i]) for i in range(n)]
    plus, minus = [0], [0]
    for i in range(1, n):
        up, dn = H[i] - H[i - 1], L[i - 1] - L[i]
        plus.append(up if up > dn and up > 0 else 0); minus.append(dn if dn > up and dn > 0 else 0)
    st, sp, sm = wilder(tr, 14, 1), wilder(plus, 14, 1), wilder(minus, 14, 1)
    pdi = [None if st[i] is None or st[i] <= 0 else 100 * sp[i] / st[i] for i in range(n)]
    mdi = [None if st[i] is None or st[i] <= 0 else 100 * sm[i] / st[i] for i in range(n)]
    dx = [0 if pdi[i] is None or pdi[i] + mdi[i] == 0 else 100 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i]) for i in range(n)]
    adx = wilder(dx, 14, 14)
    e12, e26 = ema(C, 12), ema(C, 26)
    dif = [e12[i] - e26[i] for i in range(n)]; dea = ema(dif, 9)
    K, D, k, d = [], [], 50.0, 50.0
    for i in range(n):
        if i < 8:
            K.append(None); D.append(None); continue
        hi, lo = max(H[i - 8:i + 1]), min(L[i - 8:i + 1])
        rsv = 50.0 if hi == lo else (C[i] - lo) / (hi - lo) * 100
        k = k * 2 / 3 + rsv / 3; d = d * 2 / 3 + k / 3
        K.append(k); D.append(d)
    obv = [0]
    for i in range(1, n):
        obv.append(obv[-1] + (V[i] if C[i] > C[i - 1] else -V[i] if C[i] < C[i - 1] else 0))
    mfv = [0 if H[i] == L[i] else ((C[i] - L[i]) - (H[i] - C[i])) / (H[i] - L[i]) * V[i] for i in range(n)]
    cmf = [None if i < 19 else (sum(mfv[i - 19:i + 1]) / sum(V[i - 19:i + 1]) if sum(V[i - 19:i + 1]) > 0 else None) for i in range(n)]
    tp = [(H[i] + L[i] + C[i]) / 3 for i in range(n)]
    tpma = sma(tp, 20); cci = []
    for i in range(n):
        if tpma[i] is None:
            cci.append(None); continue
        md = sum(abs(x - tpma[i]) for x in tp[i - 19:i + 1]) / 20
        cci.append(None if md <= 1e-9 * max(1.0, abs(tpma[i])) else (tp[i] - tpma[i]) / (0.015 * md))
    mfi = []
    for i in range(n):
        if i < 14:
            mfi.append(None); continue
        pos = sum(tp[j] * V[j] for j in range(i - 13, i + 1) if tp[j] > tp[j - 1])
        neg = sum(tp[j] * V[j] for j in range(i - 13, i + 1) if tp[j] < tp[j - 1])
        mfi.append((None if pos == 0 else 100.0) if neg == 0 else 100 - 100 / (1 + pos / neg))
    roc = [None if i < 12 else (C[i] / C[i - 12] - 1) * 100 for i in range(n)]
    mid = sma(C, 20); up, lo, width = [], [], []
    for i in range(n):
        if mid[i] is None:
            up.append(None); lo.append(None); width.append(None); continue
        sd = math.sqrt(sum((x - mid[i]) ** 2 for x in C[i - 19:i + 1]) / 20)
        up.append(mid[i] + 2 * sd); lo.append(mid[i] - 2 * sd); width.append(4 * sd / mid[i])
    pos120 = []
    for i in range(n):
        if i < 120:
            pos120.append(None); continue
        hi, lw = max(H[i - 120:i]), min(L[i - 120:i])
        pos120.append(None if hi == lw else (C[i] - lw) / (hi - lw))
    pivl = [j for j in range(3, n - 3) if all(L[j] < L[j - k] and L[j] < L[j + k] for k in (1, 2, 3))]
    pivh = [j for j in range(3, n - 3) if all(H[j] > H[j - k] and H[j] > H[j + k] for k in (1, 2, 3))]
    return dict(sma5=sma(C, 5), sma20=sma(C, 20), sma60=sma(C, 60), atr14=wilder(tr, 14, 0), atr22=wilder(tr, 22, 0), rsi14=rsi,
                pdi=pdi, mdi=mdi, adx=adx, dif=dif, dea=dea, hist=[(dif[i] - dea[i]) * 2 for i in range(n)], k=K, d=D, obv=obv,
                cmf20=cmf, cci20=cci, mfi14=mfi, roc12=roc, bmid=mid, bup=up, blo=lo, bwidth=width, pos120=pos120, pivl=pivl, pivh=pivh)


def walk(seed, n, drift=0.0, vol=2.0, flat_from=None):
    """隨機走勢。flat_from 之後價格完全不動（測零分母）。"""
    rnd, bars, price = random.Random(seed), [], 100.0
    for i in range(n):
        if flat_from is not None and i >= flat_from:
            bars.append(dict(date=f'd{i}', open=price, high=price, low=price, close=price, volume=0 if i % 2 else 500))
            continue
        open_ = round(price * (1 + rnd.uniform(-0.01, 0.01)), 2)
        close = round(max(1.0, open_ * (1 + drift + rnd.gauss(0, vol / 100))), 2)
        high = round(max(open_, close) * (1 + abs(rnd.gauss(0, 0.004))), 2)
        low = round(min(open_, close) * (1 - abs(rnd.gauss(0, 0.004))), 2)
        bars.append(dict(date=f'd{i}', open=open_, high=high, low=low, close=close, volume=round(rnd.uniform(200, 9000))))
        price = close
    return bars


def same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def main():
    series = [walk(1, 160), walk(2, 160, drift=0.004), walk(3, 160, drift=-0.004, vol=3.5), walk(4, 45), walk(5, 160, flat_from=100),
              walk(6, 300, vol=1.0)]
    # 手算得出答案的小例子：Wilder《New Concepts》常被引用的 RSI 範例（前 14 筆漲跌平均 0.2386／0.1000 → RSI 70.46）。
    wilder_close = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00]
    series.append([dict(date=f'w{i}', open=c, high=c + 0.2, low=c - 0.2, close=c, volume=1000) for i, c in enumerate(wilder_close)])
    # OBV／CMF／ATR 的小例子：五根，數字都能心算。
    series.append([dict(date='a', open=10, high=12, low=8, close=10, volume=100), dict(date='b', open=10, high=13, low=10, close=13, volume=200),
                   dict(date='c', open=13, high=13, low=11, close=11, volume=300), dict(date='d', open=11, high=11, low=11, close=11, volume=50),
                   dict(date='e', open=11, high=15, low=11, close=14, volume=400)])
    series.append(walk(9, 40, flat_from=0))
    run = subprocess.run(['node', '-e', NODE, str(ROOT)], input=json.dumps(series), capture_output=True, text=True, encoding='utf-8')
    if run.returncode:
        sys.exit('node 執行失敗：\n' + run.stderr)
    got, checked = json.loads(run.stdout), 0
    for n, bars in enumerate(series):
        want = reference(bars)
        for key, ref in want.items():
            js = got[n][key]
            assert len(js) == len(ref), (n, key, len(js), len(ref))
            for i, (a, b) in enumerate(zip(js, ref)):
                assert same(a, b), f'第 {n} 組 {key}[{i}]：前台 {a}，參考 {b}'
                checked += 1
    # 手算答案
    w = got[6]
    assert abs(w['rsi14'][14] - 70.46) < 0.01, w['rsi14'][14]                       # 100 − 100／(1 + 0.23857／0.1)
    assert abs(w['rsi14'][15] - 66.25) < 0.01, w['rsi14'][15]                       # 平均漲 0.22153、平均跌 0.11286
    s = got[7]
    assert s['obv'] == [0, 200, -100, -100, 300], s['obv']                          # 漲加量、跌減量、平盤不動
    # 真實區間：4、3（13−10）、2、0、4（15−11，前收 11）
    assert all(v is None for v in s['atr14'])                                       # 不到 14 根，不硬算
    # 價格不動：CCI 無法定義（零分母）、帶寬是 0；KD 在高低相同時 RSV 取 50，仍有值
    flat = got[4]
    assert flat['cci20'][-1] is None and abs(flat['bwidth'][-1]) < 1e-12 and flat['k'][-1] is not None, (flat['cci20'][-1], flat['bwidth'][-1])
    # 從頭到尾都沒有漲跌：RSI 無法定義，不是 0 也不是 50；ADX 的方向指標也沒有值
    still = got[8]
    assert still['rsi14'][-1] is None and still['pdi'][-1] is None and still['mfi14'][-1] is None, (still['rsi14'][-1], still['pdi'][-1])
    # 暖機：不足長度一律是 None
    short = got[3]
    assert short['sma60'][-1] is None and short['pos120'][-1] is None and short['adx'][26] is None and short['adx'][27] is not None
    print(f'ok 指標版本 {got[0]["version"]}：{len(series)} 組日 K、{len(want)} 種指標、{checked:,} 個數值與獨立的參考實作逐一相同')
    print('ok 手算例：RSI 70.46／66.25、OBV 0, 200, −100, −100, 300；價格不動時 RSI、CCI 無法定義；不足長度不硬算')


if __name__ == '__main__':
    main()
