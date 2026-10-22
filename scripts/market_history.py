"""來源可取得的完整日K歷史（免金鑰）：上市用證交所每日收盤行情（一個交易日一次請求涵蓋全部上市），
上櫃用櫃買中心個股月成交資訊（一檔一個月一次請求）。

為什麼兩個市場來源不同（2026/10/22 實測）：證交所 MI_INDEX 的成交股數與個股 STOCK_DAY 逐日相同，含零股；
櫃買的全市場日報（afterTrading/otc）只算整股，群聯一天少一成多；櫃買個股月資料的成交張數含零股，與證交所口徑一致，
也和網站現有的日K相同。櫃買另一個全市場端點（stk_quote_result）不理會日期，只回最新一天，不能拿來補歷史。

用法：
  python scripts/market_history.py fetch-twse --archive DIR [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--years 10] [--deadline-min N]
  python scripts/market_history.py fetch-tpex --archive DIR (--codes ...|--codes-file F|--sheets) [--years 10] [--deadline-min N]
  python scripts/market_history.py build --archive DIR --out DIR (--codes ...|--codes-file F|--sheets)
  python scripts/market_history.py check --out DIR

archive/twse/YYYY/YYYYMMDD.json.gz：當天上市全部個股與 ETF 的原始收盤（過去日期不重抓，休市日也存 stat）。
archive/tpex/代號/YYYYMM.json：上櫃個股一個月的原始收盤（過去月份不重抓）。
history/代號.json：保留原始成交價與股數。疑似分割只記候選，不憑價差自動改價。
不產生假 K 棒：休市、查無資料、價格不是數字或收盤 ≤ 0 的列一律略過。
"""
import argparse
import gzip
import json
import re
import sys
import time
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Taipei')
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36',
      'Accept': 'application/json, text/plain, */*'}
SESSION = requests.Session()  # 重用 TLS 連線；保持 requests 預設的憑證與主機驗證
SESSION.headers.update(UA)
TWSE_START = date(2004, 2, 11)  # MI_INDEX 官方公告起點；不是所有歷史來源的起點
TPEX_START = date(1994, 1, 1)   # 探查範圍，尚未取得的月份維持缺口，不宣稱完整
GAP = {'twse': 1.6, 'tpex': 1.2}     # 請求間隔（秒）；過快會被暫時封鎖
CODE_RE = re.compile(r'^(?:\d{4,6}[A-Z]?)$')
_last = {'twse': 0.0, 'tpex': 0.0}


def _get(url, host, tries=4):
    for attempt in range(tries):
        wait = GAP[host] - (time.time() - _last[host])
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        try:
            response = SESSION.get(url, timeout=45)
            response.raise_for_status()
            text = response.content.decode('utf-8', 'replace')
            if text.lstrip().startswith('<'):
                raise ValueError('回傳網頁不是 JSON')
            return json.loads(text)
        except Exception:  # 逾時、5xx、被暫時封鎖：退避後重試
            if attempt == tries - 1:
                raise
            time.sleep(4 * (attempt + 1) ** 2)


def _num(text):
    s = re.sub(r'<[^>]*>', '', str(text if text is not None else '')).replace(',', '').replace(' ', '').strip()
    try:
        return float(s)
    except ValueError:
        return None


def _signed_change(sign_text, diff_text):
    """證交所：「漲跌(+/-)」含 HTML 的 +／-／X，「漲跌價差」是絕對值。X（不比價）回 None。"""
    sign = re.sub(r'<[^>]*>', '', str(sign_text or '')).strip()
    diff = _num(diff_text)
    if diff is None or sign.upper() == 'X':
        return None
    return -diff if sign == '-' else diff


def _row(d, code, name, o, h, l, c, chg, vol):
    if not CODE_RE.match(code) or None in (o, h, l, c, vol) or min(o, h, l, c) <= 0:
        return None
    return [d, code, name, o, h, l, c, chg, int(vol)]


def _code_set(args):
    if getattr(args, 'sheets', False):
        return sheet_codes()
    if getattr(args, 'codes_file', None):
        return {c.strip() for c in Path(args.codes_file).read_text(encoding='utf-8').split() if CODE_RE.match(c.strip())}
    return {c.strip() for c in (getattr(args, 'codes', '') or '').split(',') if CODE_RE.match(c.strip())}


def sheet_codes():
    """網站追蹤的股票：操作紀錄與會員持股出現過的代號（與 Apps Script trackedCodes_ 相同）。只讀。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from transcript import open_sheets  # noqa: E402  與 market_data.py 同一個開表方式（需要 SPREADSHEET_ID 與服務帳戶）
    ss = open_sheets()
    codes = set()
    for name in ('操作紀錄', '會員持股'):
        values = ss.worksheet(name).get_all_values()
        if values and '代號' in values[0]:
            i = values[0].index('代號')
            codes |= {str(r[i]).strip() for r in values[1:] if i < len(r) and CODE_RE.match(str(r[i]).strip())}
    return codes


def _deadline(args):
    return time.time() + 60 * args.deadline_min if getattr(args, 'deadline_min', None) else float('inf')


def _start(args, end, floor):
    if getattr(args, 'start', None):
        return max(floor, date.fromisoformat(args.start))
    years = getattr(args, 'years', 0)
    return max(floor, date(end.year - years, end.month, min(end.day, 28))) if years else floor


def _write(path, rec, zipped=False):
    """完整寫入後原子替換；內容未改不改檔案與提交。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(rec, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    data = gzip.compress(raw, mtime=0) if zipped else raw
    if path.exists() and path.read_bytes() == data:
        return
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_bytes(data)
    tmp.replace(path)


def _read(path, zipped=False):
    try:
        return json.loads(gzip.decompress(path.read_bytes()) if zipped else path.read_bytes())
    except (OSError, ValueError, EOFError):
        return None  # 損壞檔案必須重新抓，不能略過或中止全部股票


# ---------------------------------------------------------------- 上市：證交所全市場日報

def fetch_twse_day(day):
    j = _get(f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day:%Y%m%d}&type=ALLBUT0999&response=json', 'twse')
    if str(j.get('stat')).upper() != 'OK':
        return {'stat': str(j.get('stat') or ''), 'rows': []}
    t = next((t for t in j.get('tables') or [] if (t.get('fields') or [''])[0] == '證券代號' and '收盤價' in t['fields']), None)
    if not t:
        return {'stat': 'no-table', 'rows': []}
    f = {name: i for i, name in enumerate(t['fields'])}
    rows = []
    for a in t.get('data') or []:
        r = _row(day.isoformat(), str(a[f['證券代號']]).strip(), str(a[f['證券名稱']]).strip(), _num(a[f['開盤價']]), _num(a[f['最高價']]),
                 _num(a[f['最低價']]), _num(a[f['收盤價']]), _signed_change(a[f['漲跌(+/-)']], a[f['漲跌價差']]), _num(a[f['成交股數']]))
        if r:
            rows.append(r[1:])
    return {'stat': 'OK', 'rows': rows}


def cmd_fetch_twse(args):
    now = datetime.now(TZ)
    end = date.fromisoformat(args.end) if args.end else now.date()
    start = _start(args, end, TWSE_START)
    if end == now.date() and now.hour * 100 + now.minute < 1430:     # 14:30 前今天還沒收盤
        end -= timedelta(days=1)
    stop, day, done, kept = _deadline(args), end, 0, 0
    while day >= start:                                               # 由近往遠抓：時間不夠時最近的先有
        if time.time() > stop:
            print(f'時間到，停在 {day}，下次續抓'); break
        if day.weekday() < 5:
            p = Path(args.archive) / 'twse' / f'{day:%Y}' / f'{day:%Y%m%d}.json.gz'
            fresh = (now.date() - day).days <= 1
            cached = _read(p, True)
            # 有價格的舊檔才永久沿用；空回應隔七天重核，不能當作已確認休市。
            empty_recent = cached and not cached.get('rows') and time.time() - p.stat().st_mtime < 7 * 86400
            if cached and ((cached.get('rows') and not fresh) or empty_recent):
                kept += 1
            else:
                try:
                    rec = {'date': day.isoformat(), **fetch_twse_day(day)}
                except Exception as e:
                    print(f'{day} 上市抓取失敗，下次再補：{type(e).__name__}: {str(e)[:80]}', flush=True)
                    day -= timedelta(days=1); continue
                # mtime=0：內容沒變時檔案逐位元相同，重抓今天、昨天不會每天多一筆提交
                if rec['stat'] == 'no-table':
                    print(f'{day} 來源欄位改變，不保存成休市', flush=True)
                    day -= timedelta(days=1); continue
                if not rec['rows']:
                    rec['checkedAt'] = now.isoformat(timespec='seconds')
                _write(p, rec, zipped=True)
                done += 1
                if done % 50 == 0:
                    print(f'上市 {day}：已抓 {done} 天（當天 {len(rec["rows"])} 檔）', flush=True)
        day -= timedelta(days=1)
    print(f'上市抓取：新抓 {done} 天、沿用 {kept} 天（{start} ～ {end}）')


# ---------------------------------------------------------------- 上櫃：櫃買個股月資料

def fetch_tpex_month(code, y, m):
    j = _get(f'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?code={code}&date={y}/{m:02d}/01&response=json', 'tpex')
    t = (j.get('tables') or [{}])[0]
    f = {re.sub(r'\s', '', name): i for i, name in enumerate(t.get('fields') or [])}
    rows = []
    for a in t.get('data') or []:
        roc = str(a[f['日期']]).strip().replace('＊', '')
        mm = re.match(r'(\d{2,3})/(\d{2})/(\d{2})', roc)
        if not mm:
            continue
        d = f'{int(mm.group(1)) + 1911}-{mm.group(2)}-{mm.group(3)}'
        # 較早的月份欄名是「成交仟股」（2019 年等），近年是「成交張數」；兩者都是千股
        lots = _num(a[f['成交張數'] if '成交張數' in f else f['成交仟股']])
        r = _row(d, code, '', _num(a[f['開盤']]), _num(a[f['最高']]), _num(a[f['最低']]), _num(a[f['收盤']]),
                 _num(a[f['漲跌']]), None if lots is None else lots * 1000)
        if r:
            rows.append([r[0]] + r[3:])
    return {'stat': str(j.get('stat') or ''), 'name': str(j.get('name') or ''), 'rows': rows}


def twse_first_dates(archive):
    """上市封存裡每個代號第一次出現的日期，以及封存最早的交易日。"""
    first, earliest = {}, ''
    for p in sorted((Path(archive) / 'twse').glob('*/*.json.gz')):
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            rec = json.load(fh)
        if not rec.get('rows'):
            continue
        earliest = earliest or rec['date']
        for r in rec['rows']:
            first.setdefault(r[0], rec['date'])
    return first, earliest


def cmd_fetch_tpex(args):
    """不在上市的追蹤股票整段到櫃買查；上市之前在櫃買交易過的（轉上市），補上市第一天以前的月份。"""
    now = datetime.now(TZ)
    first, earliest = twse_first_dates(args.archive)
    this_month = now.year * 12 + now.month - 1
    plan = []
    for code in sorted(_code_set(args)):
        d = first.get(code)
        if not d:
            plan.append((code, this_month))
        elif earliest and date.fromisoformat(d) > date.fromisoformat(earliest) + timedelta(days=31):
            plan.append((code, int(d[:4]) * 12 + int(d[5:7]) - 1))
    # 先補完全沒有上市封存的股票，避免新上市股票的轉板探查擋住真正上櫃缺口。
    plan.sort(key=lambda item: (item[0] in first, item[0]))
    stop, done, kept = _deadline(args), 0, 0
    start = _start(args, now.date(), TPEX_START)
    first_month = start.year * 12 + start.month - 1
    for code, k in plan:
        empty = 0
        # 櫃買偶爾對有資料的月份也回「沒有符合條件的資料」（2026/10/22 實測，隔幾分鐘就正常）。
        # 這一檔確定拿得到資料之前，空的月份不存檔，下次再問；拿到過資料之後，更早的空月份才當作尚未掛牌存下來。
        seen = any(json.loads(f.read_text(encoding='utf-8')).get('rows') for f in (Path(args.archive) / 'tpex' / code).glob('*.json'))
        while k >= first_month:                                       # 由近往遠；連續三個月沒有資料視為尚未掛牌
            if time.time() > stop:
                print(f'時間到，停在 {code} {k // 12}/{k % 12 + 1:02d}，下次續抓'); print(f'上櫃抓取：新抓 {done} 個月、沿用 {kept} 個月'); return
            y, m = k // 12, k % 12 + 1
            p = Path(args.archive) / 'tpex' / code / f'{y}{m:02d}.json'
            recent = this_month - k <= 1                                # 本月與上月還在變：重抓
            cached = _read(p)
            age = time.time() - p.stat().st_mtime if p.exists() else float('inf')
            if cached and ((cached.get('rows') and (not recent or age < 20 * 3600)) or (not cached.get('rows') and age < 7 * 86400)):
                rec = json.loads(p.read_text(encoding='utf-8')); kept += 1
            else:
                try:
                    rec = fetch_tpex_month(code, y, m)
                    if not rec['rows'] and not seen:
                        time.sleep(5)
                        rec = fetch_tpex_month(code, y, m)
                except Exception as e:
                    print(f'{code} {y}/{m:02d} 上櫃抓取失敗，下次再補：{type(e).__name__}', flush=True); k -= 1; continue
                seen = seen or bool(rec['rows'])
                # 空月只代表待核；不以連續空月推測掛牌日，且仍檢查更早月份。
                if not rec['rows']:
                    rec['checkedAt'] = now.isoformat(timespec='seconds')
                _write(p, rec)
                done += 1
            empty = 0 if rec.get('rows') else empty + 1
            k -= 1
        print(f'上櫃 {code}：完成', flush=True)
    print(f'上櫃抓取：{len(plan)} 檔，新抓 {done} 個月、沿用 {kept} 個月')


# ---------------------------------------------------------------- 組成每檔歷史

def split_factor(prev_close, close, change):
    """前一筆收盤 ÷ 今天參考價（收盤 − 帶號漲跌）。接近整數倍才算分割；接近整數分之一算反分割。其餘（除權息）不處理。"""
    if not prev_close or change is None:
        return None
    ref = close - change
    if ref <= 0:
        return None
    ratio = prev_close / ref
    if ratio >= 1.5:
        k = round(ratio)
        return k if abs(ratio / k - 1) < 0.03 else None
    if ratio <= 0.67:
        k = round(1 / ratio)
        return 1 / k if k >= 2 and abs(ratio * k - 1) < 0.03 else None
    return None


def build_series(entries):
    """entries：[(date, o, h, l, c, chg, vol)] 依日期排序。回傳 (rows, adjustments)。"""
    adjustments, prev = [], None
    for e in entries:
        f = split_factor(prev, e[4], e[5])
        # 價差只能產生疑似事件，不能自行改寫歷史價格。正式調整需另附官方事件證據。
        prev = e[4]
    rows = []
    for e in entries:
        k = 1.0
        for a in adjustments:
            if e[0] < a['date']:
                k *= a['factor']
        rows.append([e[0]] + [round(v / k, 4) for v in e[1:5]] + [e[6]])
    return rows, adjustments


def cmd_build(args):
    codes = _code_set(args)
    series, names, market = {c: {} for c in codes}, {}, {}
    scanned_days, empty_days, month_coverage = set(), [], {}
    for p in sorted((Path(args.archive) / 'twse').glob('*/*.json.gz')):
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            rec = json.load(fh)
        scanned_days.add(rec['date'])
        if not rec.get('rows'):
            empty_days.append(rec['date'])
        for code, name, o, h, l, c, chg, vol in rec.get('rows', []):
            if code in series:
                series[code][rec['date']] = (rec['date'], o, h, l, c, chg, vol); names[code] = name; market[code] = '上市'
    for code in codes:
        months, empty_months = [], []
        for p in sorted((Path(args.archive) / 'tpex' / code).glob('*.json')):
            rec = json.loads(p.read_text(encoding='utf-8'))
            months.append(p.stem)
            if not rec.get('rows'):
                empty_months.append(p.stem)
            for d, o, h, l, c, chg, vol in rec.get('rows', []):
                series[code].setdefault(d, (d, o, h, l, c, chg, vol))   # 轉上市前的櫃買資料；同一天以上市為準
            if rec.get('rows'):
                market.setdefault(code, '上櫃')
                if rec.get('name'):
                    names.setdefault(code, rec['name'])
        month_coverage[code] = {'monthsScanned': len(months), 'oldestMonthScanned': min(months, default=None),
                               'emptyMonthsUnverified': empty_months}
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    meta = {'updated': datetime.now(TZ).isoformat(timespec='seconds'), 'codes': {},
            'scope': '全部可探查範圍；非固定十年。覆蓋不足不代表掛牌起點。',
            'sourceFloors': {'twse': TWSE_START.isoformat(), 'tpexProbe': TPEX_START.isoformat()},
            'coverageVerified': False, 'requestedCodes': sorted(codes)}
    for code in sorted(series):
        entries = [series[code][d] for d in sorted(series[code])]
        if not entries:
            continue
        rows, adjustments = build_series(entries)
        doc = {'code': code, 'name': names.get(code, ''), 'market': market.get(code, ''),
               'source': '上市：證交所 MI_INDEX 每日收盤行情；上櫃：櫃買中心個股月成交資訊', 'basis': 'raw-unadjusted',
               'volumeBasis': 'raw-traded-shares-incl-odd-lots', 'adjustments': adjustments,
               'first': rows[0][0], 'latest': rows[-1][0], 'fields': ['date', 'open', 'high', 'low', 'close', 'volume'], 'rows': rows}
        doc['splitCandidates'] = [{'date': b[0], 'factor': split_factor(a[4], b[4], b[5])} for a, b in zip(entries, entries[1:]) if split_factor(a[4], b[4], b[5])]
        _write(out / f'{code}.json', doc)
        meta['codes'][code] = {'first': doc['first'], 'latest': doc['latest'], 'bars': len(rows), 'adjustments': adjustments}
    meta['latest'] = max((v['latest'] for v in meta['codes'].values()), default='')
    meta['missingCodes'] = sorted(codes - set(meta['codes']))
    now = datetime.now(TZ)
    end = now.date() - timedelta(days=1 if now.hour * 100 + now.minute < 1430 else 0)
    missing_days, day = [], TWSE_START
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in scanned_days:
            missing_days.append(day.isoformat())
        day += timedelta(days=1)
    # 平日不等於交易日；空檔也不等於已核實休市。完整度不能只看根數或工作流成功。
    meta['archiveCoverage'] = {'twse': {'requestedStart': TWSE_START.isoformat(), 'requestedEnd': end.isoformat(),
        'daysScanned': len(scanned_days), 'missingWeekdays': missing_days, 'emptyDaysUnverified': sorted(empty_days)},
        'tpex': month_coverage}
    before = _read(out / '_meta.json')
    if before and {k: v for k, v in before.items() if k != 'updated'} == {k: v for k, v in meta.items() if k != 'updated'}:
        meta['updated'] = before['updated']
    _write(out / '_meta.json', meta)
    print(f'產生 {len(meta["codes"])} 檔（要求 {len(codes)} 檔），最新 {meta["latest"]}')
    print(f'完整度待核：上市尚有 {len(missing_days)} 個平日未抓、{len(empty_days)} 個空回應未核實；缺少歷史檔 {len(meta["missingCodes"])} 檔')


def cmd_check(args):
    """資料健全檢查：日期遞增、開高低收合理、單日變動超過 ±40% 列出（多半是減資或除權，另由人看）。"""
    bad = 0
    for p in sorted(Path(args.out).glob('[0-9]*.json')):
        doc = json.loads(p.read_text(encoding='utf-8'))
        rows = doc['rows']
        if any(a[0] >= b[0] for a, b in zip(rows, rows[1:])):
            print(p.name, '日期沒有遞增'); bad += 1
        broken = [r for r in rows if not (r[3] <= min(r[1], r[4]) + 1e-9 and r[2] >= max(r[1], r[4]) - 1e-9)]
        if broken:
            print(p.name, '開高低收不合理', broken[:2]); bad += 1
        jumps = [(a[0], b[0], round(b[4] / a[4], 3)) for a, b in zip(rows, rows[1:]) if b[4] / a[4] > 1.6 or b[4] / a[4] < 0.6]
        if jumps:
            print(p.name, '單日變動超過 ±40%：', jumps[:4])
    if getattr(args, 'archive', None):
        # 平日沒有資料的天數（國定假日、颱風假）。一年超過 25 天多半是抓取時被擋，回報讓人看（不自動刪）
        off = {}
        for f in (Path(args.archive) / 'twse').glob('*/*.json.gz'):
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                rec = json.load(fh)
            if not rec.get('rows'):
                off.setdefault(rec['date'][:4], []).append(rec['date'])
        for y in sorted(off):
            print(f'{y} 平日休市 {len(off[y])} 天' + ('　偏多，請人工確認' if len(off[y]) > 25 else ''))
    print('檢查完成：' + ('有日期或價格錯誤' if bad else '沒有日期或價格錯誤'))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('fetch-twse', 'fetch-tpex', 'build'):
        s = sub.add_parser(name)
        s.add_argument('--archive', required=True)
        s.add_argument('--years', type=int, default=0, help='0 為來源可探查全部範圍，不以十年截斷')
        s.add_argument('--deadline-min', type=float)
        s.add_argument('--start')
        if name == 'fetch-twse':
            s.add_argument('--end')
        else:
            s.add_argument('--codes'); s.add_argument('--codes-file'); s.add_argument('--sheets', action='store_true')
        if name == 'build':
            s.add_argument('--out', required=True)
    c = sub.add_parser('check'); c.add_argument('--out', required=True); c.add_argument('--archive')
    args = ap.parse_args()
    {'fetch-twse': cmd_fetch_twse, 'fetch-tpex': cmd_fetch_tpex, 'build': cmd_build}.get(args.cmd, lambda a: sys.exit(cmd_check(a)))(args)


if __name__ == '__main__':
    main()
