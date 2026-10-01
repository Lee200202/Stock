"""個股 Yahoo 歷史分K備援（市場總覽已停用）。與文章流程隔離，不呼叫 Gemini、不寫正式日K績效。"""
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


def number(value):
    try:
        n = float(str(value).replace(',', '').strip())
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None






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
        if isinstance(data,dict):print(f'個股分K快取 {key}：{data.get("time",data.get("date",""))}，歷史K {len(data.get("candles",[]))} 根，已寫入')

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





def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode',choices=['hours'],default='hours')
    parser.add_argument('--codes',default='')
    parser.add_argument('--limit',type=int,default=40)
    args=parser.parse_args()
    automatic=os.environ.get('GITHUB_EVENT_NAME') in ('schedule','push')
    if automatic and not is_trading_day(datetime.now(TZ).date()):
        print('台股休市或行事曆未驗證，略過個股歷史分K；不開啟試算表');return
    if os.environ.get('YAHOO_DATA_ENABLED','true').lower()!='true':return
    ss=open_sheets();fetcher=Fetcher()
    update_hours(ss,fetcher,args.codes.split(',') if args.codes else [],min(100,max(1,args.limit)))

if __name__=='__main__':main()
