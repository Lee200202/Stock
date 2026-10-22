"""來源可取得的完整日K歷史（免金鑰）：上市用證交所每日收盤行情（一個交易日一次請求涵蓋全部上市），
上櫃用櫃買中心個股月成交資訊（一檔一個月一次請求）。

為什麼兩個市場來源不同（2026/10/22 實測）：證交所 MI_INDEX 的成交股數與個股 STOCK_DAY 逐日相同，含零股；
櫃買的全市場日報（afterTrading/otc）只算整股，群聯一天少一成多；櫃買個股月資料的成交張數含零股，與證交所口徑一致，
也和網站現有的日K相同。櫃買另一個全市場端點（stk_quote_result）不理會日期，只回最新一天，不能拿來補歷史。

用法：
  python scripts/market_history.py fetch-twse --archive DIR [--start YYYY-MM-DD] [--end YYYY-MM-DD] [--years 10] [--deadline-min N]
  python scripts/market_history.py fetch-tpex --archive DIR (--codes ...|--codes-file F|--sheets) [--years 10] [--deadline-min N]
  python scripts/market_history.py build --archive DIR --out DIR (--codes ...|--codes-file F|--sheets)
  python scripts/market_history.py fetch-events --archive DIR   # 交易所公告的面額變更、減資、ETF 分割／反分割
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


def _get(url, host, tries=4, form=None):
    for attempt in range(tries):
        wait = GAP[host] - (time.time() - _last[host])
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()
        try:
            response = SESSION.post(url, data=form, timeout=45) if form else SESSION.get(url, timeout=45)
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


# ---------------------------------------------------------------- 少問幾次（v200r2）
# 檔案時間不能拿來判斷多久沒查：Actions 每次 checkout，所有檔案時間都是「現在」——空回應永遠不重查、本月櫃買資料永遠不更新；
# 本機則相反，每七天把所有休市日、週六、掛牌前的月份全部重問一次。改用檔案內記錄的查詢時間。
RECHECK_SEC = 3 * 86400


def _iso_now():
    return datetime.now(TZ).isoformat(timespec='seconds')


def _age(stamp):
    try:
        return (datetime.now(TZ) - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float('inf')


def _checks(rec):
    return list((rec or {}).get('checks') or ([rec['checkedAt']] if (rec or {}).get('checkedAt') else []))


def _empty_settled(rec, last_day, calendar_closed=False):
    """空回應還要不要再問：'settled' 不再問、'wait' 上次查不到三天先不問、'ask' 再問一次。
    定案：櫃買日曆也說那天沒開盤；或在那天 30 天之後、不同的兩天各查過一次都是空的（交易所不會再補登）。"""
    if calendar_closed:
        return 'settled'
    checks = _checks(rec)
    late = {c[:10] for c in checks if (date.fromisoformat(c[:10]) - last_day).days > 30}
    if len(late) >= 2:
        return 'settled'
    return 'wait' if checks and _age(checks[-1]) < RECHECK_SEC else 'ask'


def tpex_calendar(archive):
    """櫃買個股月資料拼出的交易日曆（上市、上櫃同一套休市日）。同一個月至少兩檔有資料才算知道那個月——單一檔可能整天停牌。
    回傳 (知道的月份 {'YYYYMM'}, 交易日 {'YYYY-MM-DD'})。"""
    per_month, days = {}, set()
    for f in (Path(archive) / 'tpex').glob('*/*.json'):
        rows = (_read(f) or {}).get('rows') or []
        if rows:
            per_month[f.stem] = per_month.get(f.stem, 0) + 1
            days.update(r[0] for r in rows)
    return {k for k, n in per_month.items() if n >= 2}, days


def listing_dates(archive):
    """上櫃日期、上市公司成立日期（櫃買與證交所的公司基本資料 OpenAPI），存在 archive/meta/listing.json，一週更新一次。
    用途：上櫃股票從掛牌那個月開始查，不必一路問到 1994；轉上市的股票不早於成立那個月。查不到就回空的，照舊探查。"""
    p = Path(archive) / 'meta' / 'listing.json'
    cached = _read(p)
    if cached and _age(cached.get('fetchedAt')) < 7 * 86400:
        return cached['codes']
    codes = dict((cached or {}).get('codes') or {})
    try:
        for r in _get('https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O', 'tpex', tries=1):
            c = str(r.get('SecuritiesCompanyCode') or '').strip()
            if CODE_RE.match(c):
                codes.setdefault(c, {})['otc'] = _ymd(r.get('DateOfListing'))
                codes[c]['founded'] = _ymd(r.get('DateOfIncorporation'))
        for r in _get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L', 'twse', tries=1):
            c = str(r.get('公司代號') or '').strip()
            if CODE_RE.match(c):
                codes.setdefault(c, {})['listed'] = _ymd(r.get('上市日期'))
                codes[c]['founded'] = _ymd(r.get('成立日期'))
        _write(p, {'fetchedAt': _iso_now(), 'codes': codes})
    except Exception as e:
        print(f'公司基本資料暫時取不到，照舊探查：{type(e).__name__}', flush=True)
    return codes


def _ymd(text):
    s = re.sub(r'\D', '', str(text or ''))
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8])).isoformat() if len(s) == 8 else None
    except ValueError:
        return None


def _month_index(iso):
    return int(iso[:4]) * 12 + int(iso[5:7]) - 1


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
    known, trading = tpex_calendar(args.archive)
    days = [end - timedelta(days=n) for n in range((end - start).days + 1)]   # 預設由近往遠：時間不夠時最近的先有
    if getattr(args, 'order', 'newest') == 'oldest':
        days.reverse()   # 第二台機器由遠往近，與 Actions 不重疊（兩個 IP 各自限速）
    stop, done, kept, skipped = _deadline(args), 0, 0, 0
    for day in days:
        if time.time() > stop:
            print(f'時間到，停在 {day}，下次續抓'); break
        if day.weekday() == 6:   # 星期日從不交易；星期六有補行上班日交易（2017/02/18 等）
            continue
        iso, p = day.isoformat(), Path(args.archive) / 'twse' / f'{day:%Y}' / f'{day:%Y%m%d}.json.gz'
        closed = f'{day:%Y%m}' in known and iso not in trading
        fresh = (now.date() - day).days <= 1
        cached = _read(p, True)
        if cached and not fresh and (cached.get('rows') or _empty_settled(cached, day, closed) != 'ask'):
            kept += 1; continue
        if not cached and day.weekday() == 5 and closed:
            skipped += 1; continue   # 櫃買日曆說這個星期六沒開盤，不必問證交所
        try:
            rec = {'date': iso, **fetch_twse_day(day)}
        except Exception as e:
            print(f'{day} 上市抓取失敗，下次再補：{type(e).__name__}: {str(e)[:80]}', flush=True); continue
        if rec['stat'] == 'no-table':
            print(f'{day} 來源欄位改變，不保存成休市', flush=True); continue
        if not rec['rows']:
            rec['checks'] = _checks(cached) + [_iso_now()]
            rec['checkedAt'] = rec['checks'][0]
        _write(p, rec, zipped=True)
        done += 1
        if done % 50 == 0:
            print(f'上市 {day}：已抓 {done} 天（當天 {len(rec["rows"])} 檔）', flush=True)
    print(f'上市抓取：新抓 {done} 天、沿用 {kept} 天、依櫃買日曆略過 {skipped} 個休市週六（{start} ～ {end}）')


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
    listing = listing_dates(args.archive)
    for code in sorted(_code_set(args)):
        d, info = first.get(code), listing.get(code) or {}
        if not d:
            # 上櫃股票從掛牌那個月查起；查不到掛牌日（ETF、債券等）照舊往回探查
            plan.append((code, this_month, _month_index(info['otc']) if info.get('otc') else None))
        elif earliest and date.fromisoformat(d) > date.fromisoformat(earliest) + timedelta(days=31):
            plan.append((code, _month_index(d), _month_index(info['founded']) if info.get('founded') else None))
    # 先補完全沒有上市封存的股票，避免新上市股票的轉板探查擋住真正上櫃缺口。
    plan.sort(key=lambda item: (item[0] in first, item[0]), reverse=getattr(args, 'order', 'newest') == 'reverse')
    stop, done, kept = _deadline(args), 0, 0
    start = _start(args, now.date(), TPEX_START)
    first_month = start.year * 12 + start.month - 1
    for code, k, floor in plan:
        empty = 0
        # 櫃買偶爾對有資料的月份也回「沒有符合條件的資料」（2026/10/22 實測，隔幾分鐘就正常）。
        # 這一檔確定拿得到資料之前，空的月份不存檔，下次再問；拿到過資料之後，更早的空月份才當作尚未掛牌存下來。
        seen = any(json.loads(f.read_text(encoding='utf-8')).get('rows') for f in (Path(args.archive) / 'tpex' / code).glob('*.json'))
        while k >= max(first_month, floor if floor is not None else first_month):   # 由近往遠，到掛牌（或成立）那個月為止
            if time.time() > stop:
                print(f'時間到，停在 {code} {k // 12}/{k % 12 + 1:02d}，下次續抓'); print(f'上櫃抓取：新抓 {done} 個月、沿用 {kept} 個月'); return
            y, m = k // 12, k % 12 + 1
            p = Path(args.archive) / 'tpex' / code / f'{y}{m:02d}.json'
            recent = this_month - k <= 1                                # 本月與上月還在變：重抓
            cached = _read(p)
            last_day = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
            if cached and (cached.get('rows') and (not recent or _age(cached.get('fetchedAt')) < 20 * 3600)
                           or not cached.get('rows') and _empty_settled(cached, last_day) != 'ask'):
                rec = cached; kept += 1
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
                rec['fetchedAt'] = _iso_now()
                if not rec['rows']:
                    rec['checks'] = _checks(cached) + [rec['fetchedAt']]
                    rec['checkedAt'] = rec['checks'][0]
                _write(p, rec)
                done += 1
            empty = 0 if rec.get('rows') else empty + 1
            k -= 1
        print(f'上櫃 {code}：完成', flush=True)
    print(f'上櫃抓取：{len(plan)} 檔，新抓 {done} 個月、沿用 {kept} 個月')


# ---------------------------------------------------------------- 官方股數變動事件（v200r1）
# 面額變更、減資、ETF 分割／反分割會讓股價一夕變成幾分之一或幾倍（例：國巨 2025/08/25 面額 10 元改 2.5 元，546 → 136.5）。
# 不還原的話，均線、MACD、KD 跨過那一天全部失真。只用交易所公告的「停止買賣前收盤價 ÷ 恢復買賣參考價」還原，
# 不憑價差猜；除權息仍不還原。來源起點：證交所減資表 2011 起（更早回「查詢開始日期小於100年1月1日」），
# 面額變更 2014 年彈性面額制度後才有；櫃買四張表照年份查。查不到的年份只能靠 splitCandidates 提示人看。
EVENT_FIRST_YEAR = 2004
TWSE_EVENTS = {'par': 'change/TWTB8U', 'reduction': 'reducation/TWTAUU', 'etf-split': 'split/TWTCAU'}
TPEX_EVENTS = {'par': 'pvChgRslt', 'reduction': 'revivt', 'etf-split': 'etfSplitRslt', 'etf-reverse': 'etfRvsRslt'}
EVENT_KIND = {'par': '面額變更', 'reduction': '減資', 'etf-split': 'ETF分割', 'etf-reverse': 'ETF反分割'}


def _roc_date(text):
    """「114/08/25」或「1140825」→ 2025-08-25。"""
    s = re.sub(r'\D', '', str(text or ''))
    if len(s) not in (6, 7):
        return None
    try:
        return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:])).isoformat()
    except ValueError:
        return None


def event_rows(fields, data, key):
    f = {re.sub(r'\s', '', n): i for i, n in enumerate(fields or [])}
    di = f.get('恢復買賣日期')
    ci = next((f[k] for k in ('股票代號', '證券代號', 'ETF代號') if k in f), None)
    pi = next((f[k] for k in ('停止買賣前收盤價格', '最後交易日之收盤價格') if k in f), None)
    ri = next((i for k, i in f.items() if '參考價' in k and '除權' not in k), None)
    si = f.get('分割(反分割)')
    if data and None in (di, ci, pi, ri):
        raise ValueError(f'事件表欄位改變：{list(f)[:8]}')
    out = []
    for a in data or []:
        d, code, prev, ref = _roc_date(a[di]), str(a[ci]).strip(), _num(a[pi]), _num(a[ri])
        if not d or not CODE_RE.match(code) or not prev or not ref or prev <= 0 or ref <= 0:
            continue
        kind = ('etf-reverse' if '反' in str(a[si]) else 'etf-split') if si is not None else key
        out.append({'date': d, 'code': code, 'prev': prev, 'ref': ref, 'kind': kind})
    return out


def fetch_events_year(market, key, y):
    if market == 'twse':
        j = _get(f'https://www.twse.com.tw/rwd/zh/{TWSE_EVENTS[key]}?startDate={y}0101&endDate={y}1231&response=json', 'twse')
        stat, fields, data = str(j.get('stat') or ''), j.get('fields'), j.get('data')
        if stat.upper() != 'OK':
            return {'stat': stat, 'rows': []}   # 例：減資表 2011 年以前不受理
    else:
        j = _get(f'https://www.tpex.org.tw/www/zh-tw/bulletin/{TPEX_EVENTS[key]}', 'tpex',
                 form={'startDate': f'{y}/01/01', 'endDate': f'{y}/12/31', 'response': 'json'})
        t = (j.get('tables') or [{}])[0]
        stat, fields, data = str(j.get('stat') or ''), t.get('fields'), t.get('data')
    return {'stat': stat, 'rows': event_rows(fields, data, key)}


def cmd_fetch_events(args):
    """每年一個檔：archive/events/市場-種類-年.json。過去年份查過就沿用，今年與去年每次重查。"""
    now = datetime.now(TZ)
    done = kept = 0
    for market, table in (('twse', TWSE_EVENTS), ('tpex', TPEX_EVENTS)):
        for key in table:
            for y in range(now.year, EVENT_FIRST_YEAR - 1, -1):
                p = Path(args.archive) / 'events' / f'{market}-{key}-{y}.json'
                if _read(p) is not None and now.year - y > 1:
                    kept += 1
                    continue
                try:
                    rec = fetch_events_year(market, key, y)
                except Exception as e:
                    print(f'{market} {EVENT_KIND[key]} {y} 抓取失敗，下次再補：{type(e).__name__}: {str(e)[:60]}', flush=True)
                    continue
                _write(p, rec)
                done += 1
    print(f'股數變動事件：新抓 {done} 個年度表、沿用 {kept} 個')


def load_events(archive):
    """代號 → [事件]；同一天同一檔只留一筆（轉板當天兩個市場可能都有）。"""
    out = {}
    for p in sorted((Path(archive) / 'events').glob('*.json')):
        for e in (_read(p) or {}).get('rows', []):
            out.setdefault(e['code'], {})[e['date']] = e
    return {c: [v[d] for d in sorted(v)] for c, v in out.items()}


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


def build_series(entries, events=None):
    """entries：[(date, o, h, l, c, chg, vol)] 依日期排序；events：交易所公告的股數變動（load_events）。回傳 (rows, adjustments)。
    價差只能產生疑似事件（splitCandidates），不能自行改寫歷史價格；只有官方事件才還原。"""
    adjustments = []
    for e in events or []:
        if not entries or not entries[0][0] < e['date'] <= entries[-1][0]:
            continue   # 事件不在這段資料裡：前後沒有 K 棒要接
        before = [x for x in entries if x[0] < e['date']][-1]
        adjustments.append({'date': e['date'], 'factor': round(e['prev'] / e['ref'], 6), 'kind': EVENT_KIND.get(e['kind'], e['kind']),
                            'prevClose': e['prev'], 'refPrice': e['ref'],
                            # 封存裡停牌前最後一根的收盤應等於公告的停止買賣前收盤價；不等代表封存有缺口，check 會列出
                            'archiveClose': before[4], 'matchesArchive': abs(before[4] - e['prev']) < 0.006})
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
    events = load_events(args.archive)
    series, names, market = {c: {} for c in codes}, {}, {}
    scanned_days, empty_days, month_coverage = set(), [], {}
    known, trading = tpex_calendar(args.archive)
    for p in sorted((Path(args.archive) / 'twse').glob('*/*.json.gz')):
        with gzip.open(p, 'rt', encoding='utf-8') as fh:
            rec = json.load(fh)
        scanned_days.add(rec['date'])
        d0 = date.fromisoformat(rec['date'])
        if not rec.get('rows') and _empty_settled(rec, d0, f'{d0:%Y%m}' in known and rec['date'] not in trading) != 'settled':
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
        rows, adjustments = build_series(entries, events.get(code))
        doc = {'code': code, 'name': names.get(code, ''), 'market': market.get(code, ''),
               'source': '上市：證交所 MI_INDEX 每日收盤行情；上櫃：櫃買中心個股月成交資訊', 'basis': 'raw-except-official-share-events',
               'volumeBasis': 'raw-traded-shares-incl-odd-lots', 'adjustments': adjustments,
               'first': rows[0][0], 'latest': rows[-1][0], 'fields': ['date', 'open', 'high', 'low', 'close', 'volume'], 'rows': rows}
        official = {a['date'] for a in adjustments}
        doc['splitCandidates'] = [{'date': b[0], 'factor': split_factor(a[4], b[4], b[5])} for a, b in zip(entries, entries[1:])
                                  if split_factor(a[4], b[4], b[5]) and b[0] not in official]
        _write(out / f'{code}.json', doc)
        meta['codes'][code] = {'first': doc['first'], 'latest': doc['latest'], 'bars': len(rows), 'adjustments': adjustments}
    meta['latest'] = max((v['latest'] for v in meta['codes'].values()), default='')
    meta['missingCodes'] = sorted(codes - set(meta['codes']))
    now = datetime.now(TZ)
    end = now.date() - timedelta(days=1 if now.hour * 100 + now.minute < 1430 else 0)
    missing_days, missing_saturdays, day = [], [], TWSE_START
    while day <= end:
        if day.weekday() < 5 and day.isoformat() not in scanned_days:
            missing_days.append(day.isoformat())
        if day.weekday() == 5 and day.isoformat() not in scanned_days and not (f'{day:%Y%m}' in known and day.isoformat() not in trading):
            missing_saturdays.append(day.isoformat())
        day += timedelta(days=1)
    # 平日不等於交易日；空檔也不等於已核實休市。完整度不能只看根數或工作流成功。
    meta['archiveCoverage'] = {'twse': {'requestedStart': TWSE_START.isoformat(), 'requestedEnd': end.isoformat(),
        'daysScanned': len(scanned_days), 'missingWeekdays': missing_days,
        'missingSaturdayCandidates': missing_saturdays, 'emptyDaysUnverified': sorted(empty_days)},
        'tpex': month_coverage}
    before = _read(out / '_meta.json')
    if before and {k: v for k, v in before.items() if k != 'updated'} == {k: v for k, v in meta.items() if k != 'updated'}:
        meta['updated'] = before['updated']
    _write(out / '_meta.json', meta)
    print(f'產生 {len(meta["codes"])} 檔（要求 {len(codes)} 檔），最新 {meta["latest"]}')
    print(f'完整度待核：上市尚有 {len(missing_days)} 個平日、{len(missing_saturdays)} 個週六候選日未抓、{len(empty_days)} 個空回應未核實；缺少歷史檔 {len(meta["missingCodes"])} 檔')


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
        for a in doc.get('adjustments') or []:
            if a.get('matchesArchive') is False:
                print(p.name, f"{a['date']} {a.get('kind', '')}：公告停牌前收盤 {a.get('prevClose')}，封存最後一根 {a.get('archiveClose')}，封存可能有缺口")
    if getattr(args, 'archive', None):
        # 平日沒有資料的天數（國定假日、颱風假）。一年超過 25 天多半是抓取時被擋，回報讓人看（不自動刪）
        off = {}
        for f in (Path(args.archive) / 'twse').glob('*/*.json.gz'):
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                rec = json.load(fh)
            if not rec.get('rows') and date.fromisoformat(rec['date']).weekday() < 5:   # 星期六沒開盤是常態，不算
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
            s.add_argument('--order', choices=['newest', 'oldest'], default='newest', help='oldest：第二台機器由遠往近')
        else:
            s.add_argument('--codes'); s.add_argument('--codes-file'); s.add_argument('--sheets', action='store_true')
        if name == 'fetch-tpex':
            s.add_argument('--order', choices=['newest', 'reverse'], default='newest', help='reverse：股票順序倒過來，與另一台不重疊')
        if name == 'build':
            s.add_argument('--out', required=True)
    sub.add_parser('fetch-events').add_argument('--archive', required=True)
    c = sub.add_parser('check'); c.add_argument('--out', required=True); c.add_argument('--archive')
    args = ap.parse_args()
    {'fetch-twse': cmd_fetch_twse, 'fetch-tpex': cmd_fetch_tpex, 'build': cmd_build,
     'fetch-events': cmd_fetch_events}.get(args.cmd, lambda a: sys.exit(cmd_check(a)))(args)


if __name__ == '__main__':
    main()
