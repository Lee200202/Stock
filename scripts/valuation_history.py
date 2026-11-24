"""個股本益比、殖利率、股價淨值比的歷史區間（2026/11/24，個股頁的歷史區間條）。

來源（免金鑰、一天一次請求涵蓋整個市場；2026/11/24 實測）：
  上市 證交所 exchangeReport/BWIBBU_d?date=YYYYMMDD&selectType=ALL，2005 年起有資料。
  上櫃 櫃買中心 www/zh-tw/afterTrading/peQryDate（POST date=YYYY/MM/DD），2007 年起有資料。
       舊端點 pera_result.php 的民國兩位數年份會被當成今年，不用。回傳日期與要求日期不同就不收。

交易所的本益比以近四季每股盈餘計算；虧損期間不提供（N/A 或空白），這些日子不計入本益比區間。

用法：
  python scripts/valuation_history.py fetch --archive DIR --market twse|tpex [--years 5] [--deadline-min N]
  python scripts/valuation_history.py build --archive DIR --out DIR [--years 5]
  python scripts/valuation_history.py check --out DIR

archive/<market>/YYYY/YYYYMMDD.json.gz：{"date","market","stat":"ok"|"empty","rows":{代號:[本益比,殖利率,淨值比]}}
  休市日存 empty；三天內的 empty 下次重抓（當天資料晚上才公布）。
out/<代號>.json：近 N 年每個指標的 101 個百分位點（p0…p100）與筆數，前端用來標出目前值的排名。
  只放摘要不放逐日序列，全部上市櫃個股合計約數 MB。
"""
import argparse
import gzip
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
CODE_RE = re.compile(r'^\d{4,6}[A-Z]?$')
FLOOR = {'twse': date(2005, 1, 3), 'tpex': date(2007, 1, 2)}
GAP = {'twse': 2.2, 'tpex': 1.4}   # 秒；證交所過快會暫時封鎖
METRICS = ('per', 'yld', 'pbr')
_last = {'twse': 0.0, 'tpex': 0.0}
SESSION = None


def _session():
    global SESSION
    if SESSION is None:
        import requests
        SESSION = requests.Session()
        SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
                                'Accept': 'application/json, text/plain, */*'})
    return SESSION


def _get(market, day, tries=4):
    for attempt in range(tries):
        wait = GAP[market] - (time.time() - _last[market])
        if wait > 0:
            time.sleep(wait)
        _last[market] = time.time()
        try:
            if market == 'twse':
                r = _session().get('https://www.twse.com.tw/exchangeReport/BWIBBU_d',
                                   params={'response': 'json', 'date': day.strftime('%Y%m%d'), 'selectType': 'ALL'}, timeout=45)
            else:
                r = _session().post('https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate',
                                    data={'date': day.strftime('%Y/%m/%d'), 'response': 'json'}, timeout=45)
            r.raise_for_status()
            text = r.content.decode('utf-8', 'replace')
            if text.lstrip().startswith('<'):
                raise ValueError('回傳網頁不是 JSON')
            return json.loads(text)
        except Exception as exc:
            if attempt == tries - 1:
                raise
            print(f'{market} {day} 重試 {attempt + 1}/{tries}：{type(exc).__name__}', flush=True)
            time.sleep(5 * (attempt + 1) ** 2)


def num(text):
    s = re.sub(r'<[^>]*>', '', str(text if text is not None else '')).replace(',', '').strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v and abs(v) != float('inf') else None


def parse(market, day, payload):
    """交易所回應 → {代號:[本益比,殖利率,淨值比]}；None＝當天沒有資料（休市或尚未公布）。"""
    if market == 'twse':
        if payload.get('stat') != 'OK' or str(payload.get('date') or '') != day.strftime('%Y%m%d'):
            return None
        fields, data = payload.get('fields') or [], payload.get('data') or []
    else:
        tables = payload.get('tables') or []
        t = tables[0] if tables else {}
        if str(payload.get('date') or '') != day.strftime('%Y%m%d'):
            return None
        fields, data = t.get('fields') or [], t.get('data') or []
    if not data:
        return None
    idx = {}
    for i, f in enumerate(fields):
        f = str(f)
        if '代號' in f: idx['code'] = i
        elif f.startswith('本益比'): idx['per'] = i
        elif f.startswith('殖利率'): idx['yld'] = i
        elif f.startswith('股價淨值比'): idx['pbr'] = i
    if not {'code', 'per', 'yld', 'pbr'} <= set(idx):
        raise ValueError(f'{market} {day} 欄位改版：{fields}')
    rows = {}
    for r in data:
        code = str(r[idx['code']]).strip()
        if not CODE_RE.match(code):
            continue
        per, yld, pbr = num(r[idx['per']]), num(r[idx['yld']]), num(r[idx['pbr']])
        per = per if per and per > 0 else None      # 虧損期間不計
        pbr = pbr if pbr and pbr > 0 else None
        yld = yld if yld is not None and yld >= 0 else None
        if per is None and pbr is None and yld is None:
            continue
        rows[code] = [per, yld, pbr]
    return rows


def _path(archive, market, day):
    return Path(archive) / market / f'{day:%Y}' / f'{day:%Y%m%d}.json.gz'


def _read(path):
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        return json.load(f)


def _write(path, rec):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, separators=(',', ':'))
    tmp.replace(path)


def today():
    return datetime.now(TZ).date()


def cmd_fetch(args):
    market = args.market
    end = today()
    start = max(FLOOR[market], end - timedelta(days=round(365.25 * args.years) + 7))
    stop_at = time.time() + args.deadline_min * 60 if args.deadline_min else None
    days, d = [], end
    while d >= start:                       # 新的先抓：預算用完時最近幾年已齊
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    got = empty = kept = failed = 0
    for day in days:
        p = _path(args.archive, market, day)
        if p.exists():
            rec = _read(p)
            if rec.get('stat') == 'ok' or (end - day).days > 3:
                kept += 1
                continue
        if stop_at and time.time() > stop_at:
            print(f'{market} 時間預算用完，下次續補', flush=True)
            break
        try:
            rows = parse(market, day, _get(market, day))
        except Exception as exc:
            failed += 1
            print(f'{market} {day} 失敗：{exc}', flush=True)
            if failed >= 20:
                print(f'{market} 連續失敗過多，先停', flush=True)
                break
            continue
        if rows is None:
            if day == end:
                continue                      # 今天尚未公布：不寫 empty
            _write(p, {'date': f'{day:%Y-%m-%d}', 'market': market, 'stat': 'empty', 'rows': {}})
            empty += 1
        else:
            _write(p, {'date': f'{day:%Y-%m-%d}', 'market': market, 'stat': 'ok', 'rows': rows})
            got += 1
        if (got + empty) % 50 == 0:
            print(f'{market} 進度 新增 {got} 休市 {empty} 沿用 {kept}（{day}）', flush=True)
    print(f'{market} 完成：新增 {got}、休市 {empty}、沿用 {kept}、失敗 {failed}', flush=True)
    return 0 if failed < 20 else 1


def quantiles(values):
    """p0…p100 共 101 點（線性內插），兩位小數。"""
    v = sorted(values)
    n = len(v)
    out = []
    for k in range(101):
        pos = (n - 1) * k / 100
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        out.append(round(v[lo] + (v[hi] - v[lo]) * (pos - lo), 2))
    return out


def build(archive, years, end=None):
    """讀封存 → {代號: 摘要}。兩個市場合併（同代號轉市場時以日期接續）。"""
    files = sorted(Path(archive).glob('*/*/*.json.gz'))
    recs = []
    for f in files:
        rec = _read(f)
        if rec.get('stat') == 'ok':
            recs.append(rec)
    if not recs:
        return {}
    last = end or max(r['date'] for r in recs)
    first_allowed = (date.fromisoformat(last) - timedelta(days=round(365.25 * years))).isoformat()
    series = {}
    for rec in sorted(recs, key=lambda r: r['date']):
        if rec['date'] < first_allowed or rec['date'] > last:
            continue
        for code, vals in rec['rows'].items():
            s = series.setdefault(code, {'market': rec['market'], 'dates': [], 'vals': []})
            s['market'] = rec['market']
            s['dates'].append(rec['date'])
            s['vals'].append(vals)
    out = {}
    for code, s in series.items():
        item = {'code': code, 'market': '上市' if s['market'] == 'twse' else '上櫃', 'years': years,
                'from': s['dates'][0], 'to': s['dates'][-1], 'tradingDays': len(s['dates'])}
        for i, m in enumerate(METRICS):
            pts = [(d, v[i]) for d, v in zip(s['dates'], s['vals']) if v[i] is not None]
            if len(pts) < 20:
                continue
            vals = [v for _, v in pts]
            item[m] = {'n': len(vals), 'from': pts[0][0], 'q': quantiles(vals), 'last': vals[-1], 'lastDate': pts[-1][0]}
        if any(m in item for m in METRICS):
            out[code] = item
    return out


def cmd_build(args):
    data = build(args.archive, args.years)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    keep = set()
    for code, item in data.items():
        item['built'] = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
        (outdir / f'{code}.json').write_text(json.dumps(item, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        keep.add(f'{code}.json')
    for f in outdir.glob('*.json'):
        if f.name not in keep and f.name != '_meta.json':
            f.unlink()
    meta = {'built': datetime.now(TZ).strftime('%Y-%m-%d %H:%M'), 'codes': len(data), 'years': args.years,
            'to': max((x['to'] for x in data.values()), default='')}
    (outdir / '_meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    print(f'輸出 {len(data)} 檔，資料到 {meta["to"]}')
    return 0


def cmd_check(args):
    outdir = Path(args.out)
    files = [f for f in outdir.glob('*.json') if f.name != '_meta.json']
    bad = []
    for f in files:
        item = json.loads(f.read_text(encoding='utf-8'))
        for m in METRICS:
            if m in item:
                q = item[m]['q']
                if len(q) != 101 or any(b < a for a, b in zip(q, q[1:])):
                    bad.append(f'{f.name}:{m}')
    print(f'{len(files)} 檔，異常 {len(bad)}', bad[:10])
    return 1 if bad or not files else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch'); f.add_argument('--archive', required=True); f.add_argument('--market', choices=['twse', 'tpex'], required=True)
    f.add_argument('--years', type=float, default=5); f.add_argument('--deadline-min', type=float, default=0)
    b = sub.add_parser('build'); b.add_argument('--archive', required=True); b.add_argument('--out', required=True); b.add_argument('--years', type=float, default=5)
    c = sub.add_parser('check'); c.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    return {'fetch': cmd_fetch, 'build': cmd_build, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
