"""除權息事件（2026/11/24，個股 K 線「除權息還原」切換與歷年事件時間線）。

來源（免金鑰、一年一次請求涵蓋整個市場；2026/11/24 實測）：
  上市 證交所 rwd/zh/exRight/TWT49U?startDate=YYYYMMDD&endDate=YYYYMMDD（除權除息計算結果表）
  上櫃 櫃買中心 www/zh-tw/bulletin/exDailyQ（POST startDate=YYYY/MM/DD&endDate=YYYY/MM/DD）
欄位取「除權息前收盤價」「除權息參考價」：還原倍數＝前收盤 ÷ 參考價，與 market_history 的減資／面額變更同一口徑。
減資與面額變更已在 history/代號.json 的 adjustments，這裡不重複。

用法：
  python scripts/corp_events.py fetch --archive DIR [--from-year 2004]
  python scripts/corp_events.py build --archive DIR --out DIR
  python scripts/corp_events.py check --out DIR

archive/<market>/YYYY.json：該年全部事件（今年與去年每次重抓，更早的年份抓過就沿用）。
out/<代號>.json：{"code","dividends":[{"date","kind","prev","ref","value","factor"}],"built"}
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
CODE_RE = re.compile(r'^\d{4,6}[A-Z]?$')
GAP = {'twse': 3.0, 'tpex': 1.5}
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


def _get(market, year, tries=4):
    for attempt in range(tries):
        wait = GAP[market] - (time.time() - _last[market])
        if wait > 0:
            time.sleep(wait)
        _last[market] = time.time()
        try:
            if market == 'twse':
                r = _session().get('https://www.twse.com.tw/rwd/zh/exRight/TWT49U',
                                   params={'startDate': f'{year}0101', 'endDate': f'{year}1231', 'response': 'json'}, timeout=60)
            else:
                r = _session().post('https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ',
                                    data={'startDate': f'{year}/01/01', 'endDate': f'{year}/12/31', 'response': 'json'}, timeout=60)
            r.raise_for_status()
            text = r.content.decode('utf-8', 'replace')
            if text.lstrip().startswith('<'):
                raise ValueError('回傳網頁不是 JSON')
            return json.loads(text)
        except Exception as exc:
            if attempt == tries - 1:
                raise
            print(f'{market} {year} 重試 {attempt + 1}/{tries}：{type(exc).__name__}', flush=True)
            time.sleep(5 * (attempt + 1) ** 2)


def num(text):
    s = re.sub(r'<[^>]*>', '', str(text if text is not None else '')).replace(',', '').strip()
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v else None


def roc_date(text):
    """「114年06月13日」「114/06/13」「2025/06/13」→ 2025-06-13；看不懂回 None。"""
    m = re.match(r'^\s*(\d{2,4})\D+(\d{1,2})\D+(\d{1,2})', str(text or ''))
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 1911:
        y += 1911
    try:
        return datetime(y, mo, d).strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse(market, payload):
    """交易所回應 → [{code,date,kind,prev,ref,value}]。欄位依名稱找，不依位置。"""
    if market == 'twse':
        if payload.get('stat') not in ('OK', 'ok'):
            return []
        fields, data = payload.get('fields') or [], payload.get('data') or []
    else:
        t = (payload.get('tables') or [payload])[0]
        fields, data = t.get('fields') or [], t.get('data') or t.get('aaData') or []
    idx = {}
    for i, f in enumerate(fields):
        f = str(f)
        if ('日期' in f) and 'date' not in idx: idx['date'] = i
        elif '代號' in f: idx['code'] = i
        elif f.startswith('除權息前收盤'): idx['prev'] = i
        elif f.startswith('除權息參考價'): idx['ref'] = i
        elif f.startswith('權值+息值') or f.startswith('權值＋息值'): idx['value'] = i
        elif f.startswith('權/息') or f.startswith('權／息'): idx['kind'] = i
    if not {'date', 'code', 'prev', 'ref'} <= set(idx):
        if data:
            raise ValueError(f'{market} 欄位改版：{fields}')
        return []
    out = []
    for r in data:
        code = str(r[idx['code']]).strip()
        d = roc_date(r[idx['date']])
        prev, ref = num(r[idx['prev']]), num(r[idx['ref']])
        if not CODE_RE.match(code) or not d or not prev or not ref or prev <= 0 or ref <= 0:
            continue
        kind = str(r[idx['kind']]).strip() if 'kind' in idx else ''
        kind = '權息' if ('權' in kind and '息' in kind) else '除權' if '權' in kind else '除息' if '息' in kind else kind
        out.append({'code': code, 'date': d, 'kind': kind, 'prev': prev, 'ref': ref,
                    'value': num(r[idx['value']]) if 'value' in idx else None})
    return out


def cmd_fetch(args):
    now_year = datetime.now(TZ).year
    failed = 0
    for market, floor in (('twse', max(args.from_year, 2004)), ('tpex', max(args.from_year, 2007))):
        for year in range(floor, now_year + 1):
            p = Path(args.archive) / market / f'{year}.json'
            if p.exists() and year < now_year - 1:
                continue
            try:
                rows = parse(market, _get(market, year))
            except Exception as exc:
                failed += 1
                print(f'{market} {year} 失敗：{exc}', flush=True)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps({'market': market, 'year': year, 'rows': rows}, ensure_ascii=False), encoding='utf-8')
            print(f'{market} {year}：{len(rows)} 筆', flush=True)
    return 1 if failed > 4 else 0


def build(archive):
    by = {}
    for f in sorted(Path(archive).glob('*/*.json')):
        for e in json.loads(f.read_text(encoding='utf-8')).get('rows', []):
            factor = round(e['prev'] / e['ref'], 6)
            if not (0.2 < factor < 5):        # 參考價異常（例如停牌重新掛牌）不採用
                continue
            by.setdefault(e['code'], {})[e['date']] = {'date': e['date'], 'kind': e['kind'], 'prev': e['prev'], 'ref': e['ref'],
                                                      'value': e['value'], 'factor': factor}
    return {code: sorted(d.values(), key=lambda x: x['date']) for code, d in by.items()}


def cmd_build(args):
    data = build(args.archive)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y-%m-%d %H:%M')
    keep = set()
    for code, items in data.items():
        (out / f'{code}.json').write_text(json.dumps({'code': code, 'dividends': items, 'built': stamp}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        keep.add(f'{code}.json')
    for f in out.glob('*.json'):
        if f.name not in keep and f.name != '_meta.json':
            f.unlink()
    (out / '_meta.json').write_text(json.dumps({'built': stamp, 'codes': len(data)}, ensure_ascii=False), encoding='utf-8')
    print(f'輸出 {len(data)} 檔')
    return 0


def cmd_check(args):
    files = [f for f in Path(args.out).glob('*.json') if f.name != '_meta.json']
    bad = [f.name for f in files if any(not (0.2 < x['factor'] < 5) for x in json.loads(f.read_text(encoding='utf-8'))['dividends'])]
    print(f'{len(files)} 檔，異常 {len(bad)}', bad[:5])
    return 1 if bad or not files else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    f = sub.add_parser('fetch'); f.add_argument('--archive', required=True); f.add_argument('--from-year', type=int, default=2004)
    b = sub.add_parser('build'); b.add_argument('--archive', required=True); b.add_argument('--out', required=True)
    c = sub.add_parser('check'); c.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    return {'fetch': cmd_fetch, 'build': cmd_build, 'check': cmd_check}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
