"""市場快取與 Yahoo 歷史分K備援。與文章流程隔離，不呼叫 Gemini、不寫正式日K績效。"""
import argparse
import html
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import gspread
from transcript import open_sheets, sheets_retry
from pipeline.market_holidays import is_trading_day

TZ = ZoneInfo('Asia/Taipei')
HEADERS = ['代號', '更新時間', '資料JSON', '來源', '狀態']
MACROS = [('dxy', 'DX-Y.NYB', '美元指數', '點'),
          ('usdtwd', 'TWD=X', '美元／新台幣', '台幣／美元'),
          ('wti', 'CL=F', 'WTI 近月期貨', '美元／桶'),
          ('brent', 'BZ=F', 'Brent 近月期貨', '美元／桶')]


def number(value):
    try:
        n = float(str(value).replace(',', '').strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def industry_labels(text):
    # 官方選單提供名稱，不在程式裡猜產業代碼。
    return {code: html.unescape(re.sub('<[^>]+>', '', label)).strip()
            for code, label in re.findall(r'<option[^>]*value="(\d{2})"[^>]*>(.*?)</option>', text, re.S)}


def parse_twse(report, companies, labels):
    if report.get('stat') != 'OK':
        raise ValueError('證交所尚無有效資料')
    rawdate = report['date']
    day = f'{rawdate[:4]}/{rawdate[4:6]}/{rawdate[6:8]}'
    industry = {str(r['公司代號']): str(r.get('產業別', '')).zfill(2) for r in companies}
    amounts = defaultdict(float)
    unmatched = 0.0
    taiex = None
    for table in report.get('tables', []):
        fields = table.get('fields') or []
        for values in table.get('data') or []:
            row = dict(zip(fields, values))
            if row.get('指數') == '發行量加權股價指數':
                change = number(row.get('漲跌點數'))
                if change is not None and '-' in str(row.get('漲跌(+/-)', '')):
                    change = -abs(change)
                taiex = dict(key='taiex', label='加權指數', value=number(row['收盤指數']),
                             change=change, percent=number(row.get('漲跌百分比(%)')), time=day,
                             source='證交所收盤', unit='點', line=[])
            code = str(row.get('證券代號', ''))
            amount = number(row.get('成交金額'))
            if code in industry and amount is not None and amount >= 0:
                sector = industry[code]
                # 電子只算一次，不把母類與八個子類一起加總。
                if sector in {'13','24','25','26','27','28','29','30','31'}:
                    sector = '13'
                label = labels.get(sector)
                if label:
                    amounts[label] += amount
                else:
                    unmatched += amount
    if unmatched:
        amounts['未分類'] += unmatched
    total = sum(amounts.values())
    if not taiex or not (taiex['value'] and total > 0):
        raise ValueError('大盤或產業成交金額為空，保留舊快取')
    sectors = dict(date=day, market='集中市場', basis='上市公司普通股成交金額占比（不含 ETF／權證；電子合併子產業）',
                   source='證交所 MI_INDEX 與上市公司基本資料', total=total,
                   rows=[dict(name=k, amount=v, percent=v/total*100)
                         for k,v in sorted(amounts.items(), key=lambda x:-x[1])])
    return taiex, sectors


class Fetcher:
    """Yahoo 未公布可保證的固定免費配額；批次下載減少呼叫，429 立即停本轮，不換 IP。"""
    BATCH_SIZE = 10  # 每批最多 10 檔，兼顧穩定性

    def __init__(self, gap=2):
        self.last = 0
        self.gap = max(2, gap)
        self.limited = False

    def _throttle(self):
        time.sleep(max(0, self.last + self.gap - time.monotonic()))
        self.last = time.monotonic()

    def _check_rate_limit(self, exc):
        if 'ratelimit' in type(exc).__name__.lower() or '429' in str(exc) or 'Too Many' in str(exc):
            self.limited = True

    def history(self, symbol, **kwargs):
        if self.limited:
            raise RuntimeError('本輪 Yahoo 已限流，等待下一次排程')
        import yfinance as yf
        config=getattr(yf,'config',None)
        network=getattr(config,'network',None) or getattr(config,'debug',None)
        if network is not None:
            network.hide_exceptions = False
        self._throttle()
        history_kwargs = dict(auto_adjust=False, back_adjust=False,
            repair=False, actions=False, prepost=False, timeout=20, **kwargs)
        if network is None:
            history_kwargs['raise_errors'] = True
        try:
            return yf.Ticker(symbol).history(**history_kwargs)
        except Exception as exc:
            self._check_rate_limit(exc)
            raise

    def batch_download(self, symbols, **kwargs):
        """用 yf.download 批量抓取多檔，回傳 {symbol: DataFrame}。
        yfinance 仍對每個代號發請求；限制為兩個工作執行緒，批次只是回傳與寫入的組織方式。"""
        if self.limited:
            raise RuntimeError('本輪 Yahoo 已限流，等待下一次排程')
        import yfinance as yf
        self._throttle()
        owner=self
        class LimitCapture(logging.Handler):
            def emit(self, record):
                message=record.getMessage()
                if any(s in message.lower() for s in ('ratelimit','429','too many requests')):
                    owner.limited=True
        capture=LimitCapture();logger=logging.getLogger('yfinance');logger.addHandler(capture)
        try:
            df = yf.download(
                tickers=symbols, group_by='ticker', threads=2,
                auto_adjust=False, back_adjust=False, repair=False,
                actions=False, prepost=False, timeout=30, **kwargs)
            if self.limited:raise RuntimeError('Yahoo 429；本輪停止後續批次')
            if df.empty:
                return {}
            if len(symbols) == 1:
                if getattr(df.columns,'nlevels',1)>1:
                    try:df=df[symbols[0]]
                    except KeyError:df=df.xs(symbols[0],axis=1,level=-1)
                return {symbols[0]: df}
            result = {}
            for sym in symbols:
                try:
                    sub = df[sym].dropna(how='all')
                    if not sub.empty:
                        result[sym] = sub
                except (KeyError, TypeError):
                    pass
            return result
        except Exception as exc:
            self._check_rate_limit(exc)
            raise
        finally:
            logger.removeHandler(capture)


def hourly_rows(frame, now):
    result = {}
    if frame.empty:
        return []
    if frame.index.tz is None:
        raise ValueError('Yahoo 分K沒有時區，不猜 UTC 或台北時間')
    for at, row in frame.iterrows():
        local = at.tz_convert(TZ).to_pydatetime()
        if local.date() >= now.date() or local.minute != 0 or not 9 <= local.hour <= 13:
            continue  # 備援只收已結束日期，盤中最後一根交給 Fugle。
        nums = [number(row[k]) for k in ['Open','High','Low','Close','Volume']]
        if any(n is None for n in nums) or min(nums[:4]) <= 0 or nums[4] < 0:
            continue
        o,h,l,c,v = nums
        if h < max(o,c,l) or l > min(o,c,h):
            continue
        key = local.strftime('%Y/%m/%d %H:%M')
        result[key] = [key,o,h,l,c,v/1000]  # Yahoo 股數→張；只在來源邊界換一次。
    return [result[k] for k in sorted(result)]


def macro_card(frame, key, label, unit):
    valid = [(at, number(row['Close'])) for at,row in frame.iterrows() if number(row['Close']) is not None]
    if len(valid) < 2:
        raise ValueError(label+' 沒有足夠資料')
    at,value = valid[-1]
    prev = valid[-2][1]
    return dict(key=key,label=label,unit=unit,value=value,change=value-prev,
                percent=(value/prev-1)*100 if prev else None,
                time=at.strftime('%Y/%m/%d'),source='Yahoo Finance 日資料（非即時）',candles=daily_bars(frame),
                line=[dict(time=d.strftime('%Y/%m/%d'),value=v) for d,v in valid])


def daily_bars(frame):
    bars=[]
    for at,r in frame.iterrows():
        values=[number(r.get(k)) for k in ('Open','High','Low','Close','Volume')]
        o,h,l,c,v=values
        if any(x is None for x in values[:4]) or min(o,h,l,c)<=0 or h<max(o,c,l) or l>min(o,c,h):continue
        bars.append([at.strftime('%Y/%m/%d')]+[round(x,6) for x in values[:4]]+[max(0,v or 0)])
    return bars[-270:]


def hourly_bars(frame):
    bars = []
    if frame is None or frame.empty:
        return []
    for at, r in frame.iterrows():
        values = [number(r.get(k)) for k in ('Open', 'High', 'Low', 'Close', 'Volume')]
        o, h, l, c, v = values
        if any(x is None for x in values[:4]) or min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h):
            continue
        local = at.tz_convert(TZ).to_pydatetime() if at.tz is not None else at.to_pydatetime()
        bars.append([local.strftime('%Y/%m/%d %H:%M')] + [round(x, 4) for x in values[:4]] + [max(0, v or 0)])
    return bars[-180:]


def parse_finmind_futures(data, previous=None):
    if not data:
        raise ValueError('FinMind 台指期資料為空')
    # 篩選標準單一契約（月份為 6 碼數字）
    valid_rows = [r for r in data if r.get('futures_id') == 'TX'
                  and re.fullmatch(r'\d{6}', str(r.get('contract_date', '')))
                  and number(r.get('close')) is not None and number(r.get('close')) > 0]
    if not valid_rows:
        raise ValueError('FinMind 無有效台指期合約報價')

    by_date = defaultdict(list)
    for r in valid_rows:
        by_date[r['date']].append(r)

    latest_date = max(by_date.keys())
    latest_rows = by_date[latest_date]
    day_session_rows = [r for r in latest_rows if r.get('trading_session') == 'position']
    target_rows = day_session_rows if day_session_rows else latest_rows
    best_contract_row = max(target_rows, key=lambda r: number(r.get('volume')) or 0)
    contract = str(best_contract_row['contract_date'])

    bars = []
    prev_bars = (previous or {}).get('candles', []) if (previous or {}).get('contract') == contract else []
    bar_map = {b[0]: b for b in prev_bars}

    for dt in sorted(by_date.keys()):
        match_rows = [r for r in by_date[dt] if str(r.get('contract_date')) == contract]
        if not match_rows:
            continue
        pos_match = [r for r in match_rows if r.get('trading_session') == 'position']
        chosen = pos_match[0] if pos_match else match_rows[0]
        o = number(chosen.get('open'))
        h = number(chosen.get('max'))
        l = number(chosen.get('min'))
        c = number(chosen.get('close'))
        v = number(chosen.get('volume')) or 0
        if None in (o, h, l, c) or min(o, h, l, c) <= 0:
            continue
        fmt_date = dt.replace('-', '/')
        bar_map[fmt_date] = [fmt_date, o, h, l, c, v]

    bars = [bar_map[k] for k in sorted(bar_map)][-270:]
    if not bars:
        raise ValueError('無法組合有效台指期 K 棒')

    last_bar = bars[-1]
    val = last_bar[4]
    chg = number(best_contract_row.get('spread'))
    pct = number(best_contract_row.get('spread_per'))
    if chg is None and len(bars) >= 2:
        chg = val - bars[-2][4]
        pct = (chg / bars[-2][4]) * 100 if bars[-2][4] else None

    date_str = latest_date.replace('-', '/')
    return dict(
        key='tx',
        label='台指期 ' + contract,
        unit='點',
        contract=contract,
        value=val,
        change=chg,
        percent=pct,
        time=date_str,
        source='FinMind 台指期資料（期交所近月契約）',
        candles=bars,
        line=[dict(time=b[0], value=b[4]) for b in bars]
    )


def fetch_futures_card(fetcher=None, previous=None, session=None):
    default_token = 'eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiczcxMTMzMzEwNUBnbS5udHB1LmVkdS50dyIsImVtYWlsIjoiczcxMTMzMzEwNUBnbS5udHB1LmVkdS50dyIsInRva2VuX3ZlcnNpb24iOjB9.ENGoo6kpdfREEoPVzw1w7pB3kOi3IZbMT892M5vNEn4'
    token = os.environ.get('FINMIND_API_TOKEN', '').strip() or default_token
    s = session or requests.Session()
    start_date = (datetime.now(TZ) - timedelta(days=90)).strftime('%Y-%m-%d')
    url = f'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date={start_date}'
    if token:
        url += f'&token={token}'
    try:
        r = s.get(url, timeout=20)
        if r.status_code == 200:
            data = r.json().get('data', [])
            if data:
                return parse_finmind_futures(data, previous=previous)
    except Exception as exc:
        print('::warning::FinMind 台指期請求失敗，切換期交所備援：' + str(exc))

    response = s.get('https://openapi.taifex.com.tw/v1/DailyMarketReportFut', timeout=25)
    response.raise_for_status()
    return futures_card(response.json(), previous)


def futures_card(rows, previous=None):
    # 只用最近交易日、一般盤、實際近月單一契約。換月不把價差拼成假漲跌。
    rows=[r for r in rows if r.get('Contract')=='TX' and r.get('TradingSession')=='一般'
          and re.fullmatch(r'\d{6}',r.get('ContractMonth(Week)','')) and number(r.get('Last')) is not None]
    if not rows:raise ValueError('期交所沒有台指期一般盤有效收盤')
    day=max(r['Date'] for r in rows)
    row=min((r for r in rows if r['Date']==day),key=lambda r:r['ContractMonth(Week)'])
    contract=row['ContractMonth(Week)'];date=f'{day[:4]}/{day[4:6]}/{day[6:8]}'
    bars=(previous or {}).get('candles',[]) if (previous or {}).get('contract')==contract else []
    bar=[date]+[number(row.get(k)) for k in ('Open','High','Low','Last','Volume')]
    if any(x is None for x in bar[1:]):raise ValueError('台指期 OHLC 不完整')
    merged={b[0]:b for b in bars};merged[date]=bar;bars=[merged[k] for k in sorted(merged)][-270:]
    return dict(key='tx',label='台指期 '+contract,unit='點',contract=contract,value=bar[4],
      change=number(row.get('Change')),percent=number(row.get('%','').replace('%','')),
      time=date,source='期交所一般盤收盤；單一近月契約，換月重新累積',candles=bars,
      line=[dict(time=b[0],value=b[4]) for b in bars])


class Store:
    def __init__(self, ss, name):
        try:
            self.ws=ss.worksheet(name)
        except gspread.WorksheetNotFound:
            self.ws=sheets_retry(ss.add_worksheet,title=name,rows=1000,cols=5)
            sheets_retry(self.ws.update,range_name='A1',values=[HEADERS])
        values=sheets_retry(self.ws.get_all_values)
        if not values:
            sheets_retry(self.ws.update,range_name='A1',values=[HEADERS])
            values=[HEADERS]
        if values[0] != HEADERS:
            raise ValueError(name+' 表頭不符，尚未寫入')
        self.rows={r[0]:(i+2,r) for i,r in enumerate(values[1:]) if r and r[0]}

    def put(self,key,data,source):
        encoded=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        if key in self.rows and self.rows[key][1][2] == encoded:
            print(key+' 資料未變，略過寫入'); return
        if len(encoded)>45000:
            raise ValueError(key+' 超過單格長度，保留舊快取')
        row=[key,datetime.now(TZ).strftime('%Y/%m/%d %H:%M:%S'),encoded,source,'完成']
        if key in self.rows:
            index=self.rows[key][0]
            sheets_retry(self.ws.update,range_name=f'A{index}:E{index}',values=[row],value_input_option='RAW')
        else:
            sheets_retry(self.ws.append_row,row,value_input_option='RAW',insert_data_option='INSERT_ROWS')
            index=max([v[0] for v in self.rows.values()]+[1])+1
        self.rows[key]=(index,row)
        if isinstance(data,dict):print(f'市場快取 {key}：{data.get("time",data.get("date",""))}，歷史K {len(data.get("candles",[]))} 根，已寫入')

    def batch_put(self, items):
        """items = [(key, data, source), ...]，一次寫入多筆，減少 Sheets API 呼叫。"""
        updates = []
        appends = []
        next_idx = max([v[0] for v in self.rows.values()] + [1]) + 1
        now_str = datetime.now(TZ).strftime('%Y/%m/%d %H:%M:%S')
        written = 0
        for key, data, source in items:
            encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            if key in self.rows and self.rows[key][1][2] == encoded:
                print(key + ' 資料未變，略過寫入'); continue
            if len(encoded) > 45000:
                print('::warning::' + key + ' 超過單格長度，保留舊快取'); continue
            row = [key, now_str, encoded, source, '完成']
            if key in self.rows:
                idx = self.rows[key][0]
                updates.append({'range': f'A{idx}:E{idx}', 'values': [row]})
                self.rows[key] = (idx, row)
            else:
                appends.append(row)
                self.rows[key] = (next_idx, row)
                next_idx += 1
            written += 1
        if updates:
            sheets_retry(self.ws.batch_update, updates, value_input_option='RAW')
        if appends:
            for r in appends:
                sheets_retry(self.ws.append_row, r, value_input_option='RAW', insert_data_option='INSERT_ROWS')
        if written:
            print(f'批次寫入 {written} 筆（更新 {len(updates)}＋新增 {len(appends)}）')


def fetch_twse(session):
    def get(url):
        r=session.get(url,timeout=25);r.raise_for_status();return r
    report=get('https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&type=ALLBUT0999').json()
    companies=get('https://openapi.twse.com.tw/v1/opendata/t187ap03_L').json()
    page=get('https://www.twse.com.tw/zh/trading/historical/mi-index.html')
    return parse_twse(report,companies,industry_labels(page.content.decode('utf-8')))


def update_sectors(ss):
    store=Store(ss,'市場總覽快取')
    _,sectors=fetch_twse(requests.Session())
    today=datetime.now(TZ).strftime('%Y/%m/%d')
    if is_trading_day(datetime.now(TZ).date()) and datetime.now(TZ).hour>=15 and sectors['date']!=today:
        raise ValueError('證交所最新產業資料仍是 '+sectors['date']+'，預期 '+today+'；保留原快取並等待下次排程')
    store.put('sectors',sectors,'TWSE')
    print('產業成交比重已核對：'+sectors['date']+'，'+str(len(sectors['rows']))+' 個產業')
    return sectors


def update_dashboard(ss, fetcher, yahoo, taiwan=True):
    store=Store(ss,'市場總覽快取')
    errors=[]
    taiex=None
    try:
        if not taiwan:raise ValueError('台股休市，略過台股來源，保留最近交易日')
        taiex,sectors=fetch_twse(requests.Session())
        expected=datetime.now(TZ).strftime('%Y/%m/%d')
        if is_trading_day(datetime.now(TZ).date()) and datetime.now(TZ).hour>=15 and sectors['date']!=expected:
            raise ValueError('證交所最新產業資料仍是 '+sectors['date']+'，預期 '+expected+'；保留原快取並等待下次排程')
        # 先落地最需要盤後更新的產業比重；Yahoo 補圖耗時或限流時，不得拖到整輪結束才寫。
        store.put('sectors',sectors,'TWSE')
        # 已存的每日收盤點累積成折線；盤中另由 GAS 的 Fugle 指數覆蓋。
        old=store.rows.get('taiex')
        series=json.loads(old[1][2]).get('line',[]) if old else []
        points={r['time']:r for r in series}
        points[taiex['time']]={'time':taiex['time'],'value':taiex['value']}
        taiex['line']=[points[k] for k in sorted(points)][-90:]
        taiex['candles']=json.loads(old[1][2]).get('candles',[]) if old else []
        if yahoo:
            try:
                taiex['candles']=incremental_daily(fetcher,'^TWII',taiex['candles'])
                taiex['line']=[dict(time=b[0],value=b[4]) for b in taiex['candles']][-90:]
                taiex['historySource']='Yahoo 加權指數日K，與盤中指數分開顯示'
                try:
                    df60 = fetcher.history('^TWII', interval='60m', period='1mo')
                    h_bars = hourly_bars(df60)
                    if h_bars:
                        taiex['candles60m'] = h_bars
                except Exception as e_h:
                    print('::warning::大盤60分K：' + str(e_h))
            except Exception as exc:print('::warning::大盤歷史K：'+str(exc))
        store.put('taiex',taiex,'TWSE')
        print('證交所大盤與產業成交比重：'+taiex['time'])
    except Exception as exc:
        if taiwan:errors.append('證交所：'+str(exc));print('::warning::'+errors[-1])
        else:print('台股休市，略過台股來源，保留最近交易日；國際資料獨立更新')
    if taiwan:
        try:
            old=store.rows.get('tx');previous=json.loads(old[1][2]) if old else None
            card=fetch_futures_card(fetcher, previous=previous)
            if taiex and taiex.get('candles60m'):
                twii60 = taiex['candles60m']
                basis = (card['value'] - twii60[-1][4]) if (card.get('value') and twii60) else 0
                card['candles60m'] = [
                    [b[0], round(b[1] + basis, 2), round(b[2] + basis, 2), round(b[3] + basis, 2), round(b[4] + basis, 2), b[5]]
                    for b in twii60
                ]
            store.put('tx',card,card.get('source','FinMind / TAIFEX'))
            print('台指期更新成功：'+card['label']+'，最新價：'+str(card['value']))
        except Exception as exc:errors.append('台指期：'+str(exc));print('::warning::'+errors[-1])
    if yahoo:
        for key,symbol,label,unit in MACROS:
            try:
                old=store.rows.get(key);previous=json.loads(old[1][2]) if old else {}
                bars=incremental_daily(fetcher,symbol,previous.get('candles',[]))
                if len(bars)<2:raise ValueError('來源未提供足夠日K')
                value,prev=bars[-1][4],bars[-2][4]
                card=dict(key=key,label=label,unit=unit,value=value,change=value-prev,
                  percent=(value/prev-1)*100,time=bars[-1][0],source='Yahoo Finance 日資料（非即時）',
                  candles=bars,line=[dict(time=b[0],value=b[4]) for b in bars])
                try:
                    df60 = fetcher.history(symbol, interval='60m', period='1mo')
                    h_bars = hourly_bars(df60)
                    if h_bars:
                        card['candles60m'] = h_bars
                except Exception:
                    pass
                store.put(key,card,'Yahoo Finance 日資料')
            except Exception as exc:
                errors.append(label+'：'+str(exc));print('::warning::'+errors[-1])
                if fetcher.limited:break
    return errors


def update_hours(ss, fetcher, codes, limit):
    store=Store(ss,'Yahoo分K');now=datetime.now(TZ)
    mapping={}
    try:
        for row in ss.worksheet('代號對照快取').get_all_records():
            code=str(row.get('代號') or row.get('股票代號') or '')
            market=str(row.get('市場') or row.get('市場別') or '')
            if market in ('上市','TSE','TWSE'):mapping[code]=code+'.TW'
            elif market in ('上櫃','OTC','TPEX'):mapping[code]=code+'.TWO'
    except gspread.WorksheetNotFound:
        print('無官方代號對照快取，Yahoo 不猜市場尾碼');return
    if not codes:
        codes=sorted({str(r.get('代號','')) for tab in ('操作紀錄','會員持股')
                      for r in ss.worksheet(tab).get_all_records()})
    # 先補最久未更新的，限額不會讓後面的代號永遠輪不到。
    codes=sorted(set(codes),key=lambda c:store.rows.get(c,(0,['','']))[1][1])

    # ── 第一步：收集所有需要抓取的代號，跳過已抓過和資料已齊的 ──
    todo = []  # [(code, yahoo_symbol, previous_bars, missing_ranges)]
    today_str = now.strftime('%Y/%m/%d')
    for code in codes:
        if len(todo) >= limit:
            break
        if code not in mapping:
            print('::warning::' + code + ' 不在現有官方市場對照，無法確認 .TW／.TWO，保留既有資料、不猜代號')
            continue
        old = store.rows.get(code)
        if old and old[1][1][:10] == today_str:
            continue  # 今天已抓過，節省呼叫次數
        previous = json.loads(old[1][2]) if old else []
        ranges = hourly_missing_ranges(previous, now)
        if not ranges:
            print(code + ' 分K已齊，略過外部請求')
            continue
        todo.append((code, mapping[code], previous, ranges))

    if not todo:
        print('所有代號分K已齊或今天已更新，無需抓取')
        return

    print(f'待抓取 {len(todo)} 檔：{", ".join(c for c,_,_,_ in todo)}')

    # 相同缺口才併批。取所有股票的最大區間會把已齊的歷史也重抓。
    grouped=defaultdict(list)
    merged={code:{b[0]:b for b in previous if valid_bar(b)} for code,_,previous,_ in todo}
    for code,sym,previous,ranges in todo:
        for start,end in ranges:grouped[(start,end)].append((code,sym))
    deadline=time.monotonic()+17*60
    for (start,end),items in sorted(grouped.items()):
        for i in range(0,len(items),Fetcher.BATCH_SIZE):
            if fetcher.limited or time.monotonic()>deadline:return
            batch=items[i:i+Fetcher.BATCH_SIZE]
            try:
                results=fetcher.batch_download([sym for _,sym in batch],start=start,end=end,interval='60m')
                writes=[]
                for code,sym in batch:
                    frame=results.get(sym)
                    if frame is None or frame.empty:
                        print(f'::warning::{code} 缺口 {start}–{end} 無資料，保留舊值');continue
                    for bar in hourly_rows(frame,now):merged[code].setdefault(bar[0],bar)
                    bars=[merged[code][k] for k in sorted(merged[code])]
                    if bars:writes.append((code,bars,'Yahoo Finance 60m；股轉張；未還原'))
                if writes:store.batch_put(writes)
            except Exception as exc:
                print(f'::warning::缺口批次暫不可用，已完成批次保留：{exc}')
                # 不中途再重送整段，避免下載失敗與降級造成雙倍流量。
                if fetcher.limited:return


def valid_bar(b):
    return (isinstance(b,list) and len(b)==6 and all(number(x) is not None for x in b[1:])
            and min(b[1:5])>0 and b[5]>=0 and b[2]>=max(b[1],b[3],b[4]) and b[3]<=min(b[1],b[2],b[4]))


def hourly_missing_ranges(bars,now):
    have={b[0] for b in bars if valid_bar(b)}
    start=now.date()-timedelta(days=59)
    if have:start=max(start,datetime.strptime(min(have)[:10],'%Y/%m/%d').date())
    missing=[]
    while start<now.date():
        if is_trading_day(start) and any(f'{start:%Y/%m/%d} {h:02}:00' not in have for h in range(9,14)):
            missing.append(start)
        start+=timedelta(days=1)
    groups=[]
    for day in missing:
        if groups and day==groups[-1][1]:groups[-1]=(groups[-1][0],day+timedelta(days=1))
        else:groups.append((day,day+timedelta(days=1)))
    return groups


def incremental_daily(fetcher,symbol,previous):
    """已完整的歷史不重抓；最後一日可能尚未收盤，與新日資料一起更新。"""
    previous=[b for b in previous if valid_bar(b)]
    if not previous:return daily_bars(fetcher.history(symbol,period='1y',interval='1d'))
    by={b[0]:b for b in previous};last=max(by)
    fresh=daily_bars(fetcher.history(symbol,start=last.replace('/','-'),interval='1d'))
    for b in fresh:
        if b[0]>=last or b[0] not in by:by[b[0]]=b
    return [by[k] for k in sorted(by)][-270:]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['dashboard','sectors','hours','all'],default='dashboard')
    parser.add_argument('--codes',default='')
    parser.add_argument('--limit',type=int,default=40)
    args=parser.parse_args()
    yahoo=os.environ.get('YAHOO_DATA_ENABLED','true').lower()=='true'
    scheduled=os.environ.get('GITHUB_EVENT_NAME')=='schedule'
    taiwan=not scheduled or is_trading_day(datetime.now(TZ).date())
    if scheduled and not taiwan:
        print('台股休市或年度行事曆未驗證，略過市場資料排程；不開啟試算表');return
    ss=open_sheets();fetcher=Fetcher()
    if args.mode=='sectors':
        if taiwan:update_sectors(ss)
        else:print('台股休市，不補產業成交比重')
        return
    if args.mode in ('dashboard','all'):
        errors=update_dashboard(ss,fetcher,yahoo,taiwan)
        if any(e.startswith('證交所：') for e in errors):
            raise RuntimeError('產業成交比重未更新：'+next(e for e in errors if e.startswith('證交所：')))
    if args.mode in ('hours','all') and yahoo and taiwan:update_hours(ss,fetcher,args.codes.split(',') if args.codes else [],min(100,max(1,args.limit)))

if __name__=='__main__':main()
