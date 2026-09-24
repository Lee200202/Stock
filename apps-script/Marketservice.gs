/** v46：市場總覽與歷史分 K；補充資料分頁與原小時K分開，避免排程互相覆蓋。 */
var MARKET_INDEX_ = {};

/** 自設上限只會更保守；帳戶方案若低於預設，使用屬性降低 RPM。 */
function marketGap_(bucket, fallback) {
  var n=Number(PropertiesService.getScriptProperties().getProperty('FUGLE_'+bucket.toUpperCase()+'_RPM')||fallback);
  return Math.ceil(60000/Math.max(1,Math.min(fallback,isFinite(n)?n:fallback)));
}

/** 跨執行保留下一個請求位置；等待放在鎖外，不堵住其他寫入。 */
function marketPace_(bucket, gap) {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(1000)){throw new Error('行情請求忙碌，稍後由快取接續');}
  var wait;
  try {
    var cache=CacheService.getScriptCache(),key='market_pace_'+bucket;
    var store=bucket==='all'?PropertiesService.getScriptProperties():null;
    var now=Date.now(),next=Math.max(now,Number(store?store.getProperty(key):cache.get(key)||0));
    wait=next-now;
    if(wait>5000){throw new Error('行情排隊已滿，保留快取');}
    if(store){store.setProperty(key,String(next+gap));}else{cache.put(key,String(next+gap),120);}
  } finally {lock.releaseLock();}
  if(wait>0){Utilities.sleep(wait);}
}

function marketRows_(name) {
  if(Object.prototype.hasOwnProperty.call(MARKET_INDEX_,name)){return MARKET_INDEX_[name];}
  try { return MARKET_INDEX_[name]=readSheetObjects_(name); } catch(e){Logger.log(name+' 讀取失敗：'+e.message);return [];}
}
function marketPayload_(name, code, force) {
  var key='market_payload_'+name+'_'+code;
  if(!force){
    var raw=CACHE.get(key);
    if(raw){try{return JSON.parse(raw);}catch(e){}}
  }
  if(force){delete MARKET_INDEX_[name];}
  var rows=marketRows_(name),found=null;
  rows.forEach(function(r){
    if(String(r['代號'])===String(code)){
      try{found={data:JSON.parse(r['資料JSON']||'null'),updated:String(r['更新時間']||''),source:String(r['來源']||''),status:String(r['狀態']||'')};}catch(e){}
    }
  });
  if(found){try{CACHE.put(key,JSON.stringify(found),300);}catch(e){}}
  return found;
}
function hourBars_(payload) {
  var rows=payload && payload.data;
  if(!Array.isArray(rows)){return [];}
  return rows.filter(function(a){return Array.isArray(a)&&a.length>=6&&a.slice(1,6).every(function(v){return isFinite(v)&&Number(v)>=0;})&&a[4]>0;})
    .map(function(a){return {date:String(a[0]),open:+a[1],high:+a[2],low:+a[3],close:+a[4],volume:+a[5]};});
}
/** 優先序：Fugle 歷史 > 既有小時K > Yahoo。完全相同時間只留一根，不相加成交量。 */
function mergedHourlyHistory_(code, original) {
  var map={},official=hourBars_(marketPayload_('分K歷史',code)),yahoo=hourBars_(marketPayload_('Yahoo分K',code)),basis={};
  (original||[]).concat(official).forEach(function(r){basis[r.date]=r.close;});
  // Yahoo 的還原口徑可能隨除權息不同；重疊時間價格明顯不符時整份備援不用，不能接出假跳空。
  if(yahoo.some(function(r){return basis[r.date]&&Math.abs(r.close/basis[r.date]-1)>.02;})){yahoo=[];}
  [yahoo,original||[],official].forEach(function(rows){
    rows.forEach(function(r){map[r.date]=r;});
  });
  return Object.keys(map).sort().map(function(k){return map[k];});
}
function hourlyCoverage_(code, bars, daily) {
  var today=Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd'),have={},slots={};
  bars.forEach(function(r){var d=r.date.slice(0,10);have[d]=true;(slots[d]||(slots[d]={}))[r.date.slice(11,16)]=true;});
  var first=bars.length?bars[0].date.slice(0,10):'',missing=[],partial=[];
  (daily||[]).forEach(function(r){
    if(!first||r.date<first||r.date>=today||!(r.volume>0)){return;}
    if(!have[r.date]){missing.push(r.date);}
    else if(['09:00','10:00','11:00','12:00','13:00'].some(function(s){return !slots[r.date][s];})){partial.push(r.date);}
  });
  return {missingDates:missing,partialDates:partial,source:'Fugle 歷史／日內與既有快取優先；Yahoo 僅補空缺，量統一為張',
    note:missing.length?'區間內仍缺 '+missing.length+' 個有日K的交易日（'+missing.slice(0,5).join('、')+'），缺口未補造。':
      partial.length?'有 '+partial.length+' 天時段不齊；可能為缺資料或無成交，未補造K棒。':''};
}
function hourHistorySheetState_() {
  var sh=getSheet_('分K歷史'),values=sh.getDataRange().getValues(),by={};
  values.slice(1).forEach(function(r,i){by[String(r[0])]={index:i+2,values:r};});
  return {sheet:sh,by:by,next:values.length+1};
}
function saveHourHistory_(state,code,bars,status) {
  var prior=state.by[code],stamp=Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd HH:mm:ss');
  var row=[code,stamp,JSON.stringify(bars), 'Fugle historical 60m（張）',status];
  if(row[2].length>45000){throw new Error('分K歷史超過單格安全長度，保留原快取');}
  var index=prior?prior.index:state.next++;
  if(index>state.sheet.getMaxRows()){state.sheet.insertRowsAfter(state.sheet.getMaxRows(),100);}
  state.sheet.getRange(index,1,1,5).setValues([row]);
  state.by[code]={index:index,values:row};
  CACHE.remove('market_payload_分K歷史_'+code);
}
function scheduleHourlyContinue_() {
  if(!ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='backfillHourlyHistoryContinueJob';})){
    try{ScriptApp.newTrigger('backfillHourlyHistoryContinueJob').timeBased().after(60000).create();}catch(e){Logger.log('分K續跑未排入：'+e.message);}
  }
}
/** 每日19:15／手動一次啟動；當日已補過的代號略過。每檔寫回後才往下，超時仍能接續。 */
function backfillHourlyHistoryJob(force, restart) {
  if(force!==true&&whyClosed_(new Date())){Logger.log('非交易日，歷史分K等待下個交易日');return;}
  if(!hasFugle_()){Logger.log('分K歷史：缺 FUGLE_API_KEY，保留既有資料');return;}
  var p=PropertiesService.getScriptProperties(),lock=LockService.getScriptLock(),started=Date.now();
  if(!lock.tryLock(1000)){return;}
  try {
    if(Number(p.getProperty('hourHistoryLease')||0)>started){return;}
    p.setProperty('hourHistoryLease',String(started+360000));
    p.setProperty('hourHistoryForce',force===true?'true':'false');
    if(restart===true){p.deleteProperty('hourHistoryCursor');}
  } finally {lock.releaseLock();}
  var complete=true;
  try {
    var now=new Date(),day=Utilities.formatDate(now,TZ,'yyyy/MM/dd');
    var from=new Date(now.getTime()-89*86400000),state=hourHistorySheetState_();
    var codes=trackedCodes_().sort(),cursor={};
    try{cursor=JSON.parse(p.getProperty('hourHistoryCursor')||'{}');}catch(e){}
    if(cursor.day!==day){cursor={day:day,code:''};}
    // 保險觸發器比六分鐘硬上限晚，若平台強制中止也不失聯。
    if(!ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='backfillHourlyHistoryContinueJob';})){
      try{ScriptApp.newTrigger('backfillHourlyHistoryContinueJob').timeBased().after(420000).create();}catch(e){Logger.log(e.message);}
    }
    for(var i=0;i<codes.length;i++){
      var code=codes[i],prior=state.by[code];
      if(cursor.code&&code<=cursor.code){continue;}
      if(prior&&String(prior.values[1]).slice(0,10)===day&&(/^(完成|來源404)/.test(String(prior.values[4])))){continue;}
      if(Date.now()-started>210000){complete=false;break;}
      try {
        var existing=[];try{existing=prior?JSON.parse(prior.values[2]||'[]'):[];}catch(ignore){}
        var ranges=missingHourlyRanges_(existing,Utilities.formatDate(from,TZ,'yyyy/MM/dd'),day),bars=existing.slice();
        for(var ri=0;ri<ranges.length;ri++){
          if(Date.now()-started>210000){complete=false;break;}
          fugleHistPace_();
          var range=ranges[ri],json=fugleFetch_('/historical/candles/'+encodeURIComponent(code)+'?timeframe=60&sort=asc&from='+range.from+'&to='+range.to);
          var fresh=(json.data||[]).map(function(r){return [Utilities.formatDate(new Date(r.date),TZ,'yyyy/MM/dd HH:mm'),Number(r.open),Number(r.high),Number(r.low),Number(r.close),Number(r.volume)];})
            .filter(function(a){return a[0].slice(0,10)<day&&a[4]>0&&a.slice(1).every(function(v){return isFinite(v)&&v>=0;});});
          var by={};bars.forEach(function(b){by[b[0]]=b;});fresh.forEach(function(b){if(!by[b[0]]){by[b[0]]=b;}});bars=Object.keys(by).sort().map(function(k){return by[k];});
        }
        if(!bars.length){throw new Error('來源沒有有效分K，保留原資料');}
        saveHourHistory_(state,code,bars,'完成');
        if(!complete){saveHourHistory_(state,code,bars,'部分缺口已補；等待續跑');break;}
        p.setProperty('hourHistoryProgress',code+'｜'+(i+1)+'/'+codes.length+'｜'+day);
      } catch(e) {
        p.setProperty('hourHistoryProgress',code+'｜待補：'+String(e.message||e));
        // 權限與限流不是沒有行情；本輪停，不高速重試，更不能用空值蓋掉好資料。
        if([401,403,429].indexOf(Number(e.httpCode))>=0){Logger.log('分K歷史停止：'+e.message);complete=true;break;}
        if(Number(e.httpCode)===404){
          var kept=[];try{kept=prior?JSON.parse(prior.values[2]||'[]'):[];}catch(ignore){}
          saveHourHistory_(state,code,kept,'來源404；保留既有資料，隔日再試，Yahoo另補空缺');
        }
        Logger.log(code+' 分K待補：'+e.message);
      }
      // 失敗檔也記本輪已嘗試，避免前幾檔故障讓後面的永遠輪不到。隔日會重試。
      cursor.code=code;p.setProperty('hourHistoryCursor',JSON.stringify(cursor));
    }
    if(complete){Logger.log('分K本輪結束；各檔狀態可用 auditHourlyCoverage(代號) 核對');}
  } finally {
    p.deleteProperty('hourHistoryLease');
    ScriptApp.getProjectTriggers().filter(function(t){return t.getHandlerFunction()==='backfillHourlyHistoryContinueJob';}).forEach(function(t){ScriptApp.deleteTrigger(t);});
    if(!complete){p.setProperty('hourHistoryForce',force===true?'true':'false');scheduleHourlyContinue_();}
  }
}
/** 台股一般盤每天五根；缺一根才要求該日，不用每次重抓89天。 */
function missingHourlyRanges_(bars,from,to){
  var dates={},complete=[];(bars||[]).forEach(function(b){if(b[4]>0&&b[5]!=null&&isFinite(b[5])){var d=b[0].slice(0,10);dates[d]=dates[d]||{};dates[d][b[0].slice(11,16)]=true;}});
  Object.keys(dates).forEach(function(d){if(['09:00','10:00','11:00','12:00','13:00'].every(function(h){return dates[d][h];})){complete.push({date:d,open:1,high:1,low:1,close:1,volume:0});}});
  var first=Object.keys(dates).sort()[0];if(first&&first>from){from=first;}
  // 只有完整日可跳過；不足五根的停牌／無成交日由每日一次的既有檢查節流。
  return missingKDateRanges_(complete,from,to,to,false,true);
}
function backfillHourlyHistoryContinueJob(){backfillHourlyHistoryJob(PropertiesService.getScriptProperties().getProperty('hourHistoryForce')==='true');}
/** 編輯器手動入口；週末也能補上已結束交易日，不會取得尚未發生的行情。 */
function backfillHourlyHistoryNow(){backfillHourlyHistoryJob(true,true);}
function auditHourlyCoverage(code) {
  code=String(code||'6533');
  var bars=getHourlyCandles_(code),result=hourlyCoverage_(code,bars,getCachedDailyK(code));
  result.code=code;result.bars=bars.length;result.progress=PropertiesService.getScriptProperties().getProperty('hourHistoryProgress');
  Logger.log(JSON.stringify(result));return result;
}
/** 只安裝新增的歷史分K觸發器，不動既有排程。 */
function installMarketDataJobs() {
  getSheet_('分K歷史');getSheet_('Yahoo分K');getSheet_('市場總覽快取');
  if(!ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='backfillHourlyHistoryJob';})){
    ScriptApp.newTrigger('backfillHourlyHistoryJob').timeBased().everyDays(1).atHour(19).nearMinute(15).inTimezone(TZ).create();
  }
  Logger.log('已確認歷史分K排程；手動先執行 backfillHourlyHistoryNow() 補第一輪。');
}

/** 市場資料有自己的日期／來源。舊資料仍可看，但不標成今天即時。 */
function apiGetMarketOverview(force) {
  // market_board_v50 相容快取標記
  var cached=null;try{cached=JSON.parse(CACHE.get('market_board_v53')||'null');}catch(e){}
  if(!force&&cached&&Date.now()-cached.fetchedAt<300000){return cached;}
  var p=PropertiesService.getScriptProperties(),token=String(Date.now())+'-'+Math.random();
  var acquired=withLock_(function(){
    var lease=JSON.parse(p.getProperty('marketBoardLease')||'null');
    if(lease&&lease.until>Date.now()){return false;}
    p.setProperty('marketBoardLease',JSON.stringify({token:token,until:Date.now()+360000}));
    return true;
  });
  if(!acquired){return cached||{at:nowStamp_(),cards:[],sectors:{},warnings:['行情更新中']};}
  try{
    if(force){CACHE.remove('market_board_v53');}
    var result=buildMarketOverview_(force);
    result.fetchedAt=Date.now();
    try{CACHE.put('market_board_v53',JSON.stringify(result),21600);}catch(e){}
    return result;
  }
  finally{withLock_(function(){var lease=JSON.parse(p.getProperty('marketBoardLease')||'null');if(lease&&lease.token===token){p.deleteProperty('marketBoardLease');}});}
}
function buildMarketOverview_(force) {
  var out={at:Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd HH:mm:ss'),cards:[],sectors:[],warnings:[]};
  // 納入台指期 (tx)、加權指數、國際指標與產業比重
  ['taiex','tx','dxy','usdtwd','wti','brent','sectors'].forEach(function(key){
    var item=marketPayload_('市場總覽快取',key,force);
    if(item&&item.data){if(key==='sectors'){out.sectors=item.data;}else{out.cards.push(item.data);}}
    else{out.warnings.push(key+' 尚無快取');}
  });
  attachYahooIntraday_(out, force);

  var stamp=Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd'),hhmm=Number(Utilities.formatDate(new Date(),TZ,'HHmm'));
  var isTradingDay=!isMarketHoliday_(new Date())&&[6,7].indexOf(Number(Utilities.formatDate(new Date(),TZ,'u')))<0;
  var isMarketOpen=isTradingDay&&hhmm>=900&&hhmm<=1335;

  var live=null,hit=CACHE.get('market_live_index');
  if(hit){try{live=JSON.parse(hit);}catch(e){}}

  // 檢查快取中的折線是否為完整收盤資料（最後一根時間在 13:25 以上）
  var hasCompleteLine=live&&live.line&&live.line.length>0&&function(){
    var last=live.line[live.line.length-1];
    if(!last||!last.time){return false;}
    var t=new Date(last.time);
    if(isNaN(t.getTime())){return false;}
    return Number(Utilities.formatDate(t,TZ,'HHmm'))>=1325;
  }();

  var needFetch=false;
  if(force||!live){
    needFetch=true;
  } else if(isMarketOpen){
    // 盤中：超過 5 分鐘即重抓
    if(Date.now()-(live.fetchedAt||0)>=300000){needFetch=true;}
  } else {
    // 盤後：若現存快取尚未取得完整收盤走勢（例如早盤 10:45 快取），必須重抓一次補齊
    if(!hasCompleteLine){needFetch=true;}
  }

  if(needFetch&&!CACHE.get('market_index_blocked')){
    try{
      var res=UrlFetchApp.fetch('https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?interval=5m&range=1d',{
        muteHttpExceptions:true,
        headers:{'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
      });
      var status=res.getResponseCode();
      if(status!==200){
        if([401,403,429].indexOf(status)>=0){CACHE.put('market_index_blocked','1',120);}
        throw new Error('HTTP '+status);
      }
      var json=JSON.parse(res.getContentText()), result=json.chart.result[0], meta=result.meta, quote=result.indicators.quote[0];
      var timestamp=result.timestamp||[];
      var line=[];
      for(var i=0;i<timestamp.length;i++){
        if(quote.close[i]!=null){
          var t=new Date(timestamp[i]*1000);
          var hm=Utilities.formatDate(t,TZ,'HH:mm');
          if(hm>='09:00'&&hm<='13:30'){
            line.push({time:t.toISOString(),value:Number(quote.close[i]),volume:quote.volume[i]!=null?Number(quote.volume[i]):null});
          }
        }
      }
      var lastPrice=Number(meta.regularMarketPrice)||(line.length?line[line.length-1].value:0);
      var prevClose=Number(meta.chartPreviousClose||meta.previousClose);
      var change=prevClose>0?lastPrice-prevClose:0;
      var changePercent=prevClose>0?(change/prevClose)*100:0;
      var isDone=!isMarketOpen&&line.length>0&&function(){
        var last=line[line.length-1];
        return Number(Utilities.formatDate(new Date(last.time),TZ,'HHmm'))>=1325;
      }();

      live={key:'taiex',label:'加權指數',value:lastPrice,change:change,percent:changePercent,
        time:Utilities.formatDate(new Date(meta.regularMarketTime*1000),TZ,'yyyy/MM/dd HH:mm:ss'),
        source:isDone?'Yahoo Finance 收盤走勢':'Yahoo Finance 日內資料（可能延遲）',unit:'點',line:line,
        turnover:null,estimatedTurnover:null,tradeVolume:null,volumeUnit:'億',estimatedVolume:null,
        open:null,high:null,low:null,amplitude:null,
        estimateNote:isDone?'':'即時資料僅供參考',fetchedAt:Date.now()};

      if(live.value>0){
        CACHE.put('market_live_index',JSON.stringify(live),21600);
      } else {
        live=null;
      }
    }catch(e){
      out.warnings.push('大盤即時不可用，顯示最近快取');
      CACHE.put('market_index_blocked','1',60);
    }
  }

  if(!live&&hit){
    try{
      live=JSON.parse(hit);
      if(isMarketOpen&&Date.now()-(live.fetchedAt||0)>=300000){
        live.source+='（保留快取，非本次即時回應）';
      }
    }catch(e){}
  }

  if(live&&!live.unavailable){
    var daily=out.cards.filter(function(r){return r.key==='taiex';})[0];
    if(daily){
      live.candles=daily.candles;
      if(String(daily.time).replace(/-/g,'/').slice(0,10)===stamp&&daily.value>0){
        daily.line=(live.line&&live.line.length>0)?live.line:daily.line;
      } else {
        out.cards=out.cards.filter(function(r){return r.key!=='taiex';});
        out.cards.unshift(live);
      }
    } else {
      out.cards=out.cards.filter(function(r){return r.key!=='taiex';});
      out.cards.unshift(live);
    }
  }
  attachLiveFuture_(out);

  out.cards.forEach(function(c) {
    if (!c.candles60m || !c.candles60m.length) {
      if (c.key === 'taiex') {
        c.candles60m = fetchYahoo60m_('^TWII');
      } else if (c.key === 'dxy') {
        c.candles60m = fetchYahoo60m_('DX-Y.NYB');
      } else if (c.key === 'usdtwd') {
        c.candles60m = fetchYahoo60m_('TWD=X');
      } else if (c.key === 'wti') {
        c.candles60m = fetchYahoo60m_('CL=F');
      } else if (c.key === 'brent') {
        c.candles60m = fetchYahoo60m_('BZ=F');
      } else if (c.key === 'tx') {
        // 現貨加基差不是期貨歷史成交，日 K 也不能冒充 60 分 K。
        c.candles60m = [];
      }
    }
  });

  return out;
}

/** 抓取期交所特定盤別 (0: 日盤, 1: 夜盤) 當月主力合約 */
function fetchTaifexQuoteByType_(marketType) {
  var url = 'https://mis.taifex.com.tw/futures/api/getQuoteList';
  var payload = {
    "MarketType": marketType,
    "SymbolType": "F",
    "KindID": 1,
    "CID": "TXF",
    "ExpireMonths": "",
    "RowSize": "全部",
    "PageNo": "",
    "SortColumn": "",
    "AscDesc": "A"
  };
  var res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  var json = JSON.parse(res.getContentText());
  if (json.RtCode !== "0" || !json.RtData || !json.RtData.QuoteList) return null;
  var contracts = json.RtData.QuoteList.filter(function(c) {
    return c.DispEName && c.DispEName.match(/^TX\d+$/);
  }).sort(function(a, b) {
    var volA = Number(a.CTotalVolume) || 0;
    var volB = Number(b.CTotalVolume) || 0;
    if (volB !== volA) return volB - volA;
    return String(a.DispEName).localeCompare(String(b.DispEName));
  });
  return contracts.length ? contracts[0] : null;
}

function parseTaifexItem_(chosen, sessionType) {
  if (!chosen) return null;
  var closePrice = Number(chosen.CLastPrice);
  if (!(closePrice > 0)) return null;

  var cDigits = String(chosen.DispEName || chosen.DispCName || '').replace(/[^0-9]/g, '');
  var yyyymm = '';
  if (cDigits.length === 6) {
    yyyymm = cDigits;
  } else if (cDigits.length >= 2) {
    var cYear = '202' + cDigits.slice(-1);
    var cMonth = cDigits.slice(0, -1);
    if (cMonth.length === 1) cMonth = '0' + cMonth;
    yyyymm = cYear + cMonth;
  } else {
    yyyymm = Utilities.formatDate(new Date(), TZ, 'yyyyMM');
  }

  var dStr = chosen.CDate || Utilities.formatDate(new Date(), TZ, 'yyyyMMdd');
  var tStr = chosen.CTime || Utilities.formatDate(new Date(), TZ, 'HHmmss');
  var timeFmt = dStr.slice(0,4) + '/' + dStr.slice(4,6) + '/' + dStr.slice(6,8) + ' ' + tStr.slice(0,2) + ':' + tStr.slice(2,4) + ':' + tStr.slice(4,6);
  var curIso = dStr.slice(0,4) + '-' + dStr.slice(4,6) + '-' + dStr.slice(6,8) + 'T' + tStr.slice(0,2) + ':' + tStr.slice(2,4) + ':' + tStr.slice(4,6) + '+08:00';

  return {
    contract: yyyymm,
    symbol: chosen.SymbolID,
    session: sessionType,
    value: closePrice,
    open: Number(chosen.COpenPrice) || null,
    high: Number(chosen.CHighPrice) || null,
    low: Number(chosen.CLowPrice) || null,
    change: Number(chosen.CDiff),
    percent: Number(chosen.CDiffRate),
    time: timeFmt,
    curIso: curIso,
    dStr: dStr,
    tStr: tStr,
    source: '台灣期交所 ' + (sessionType === 'AFTERHOURS' ? '夜盤' : '日盤') + '即時報價',
    tradeVolume: chosen.CTotalVolume ? Number(chosen.CTotalVolume) : null,
    singleVolume: chosen.CSingleVolume ? Number(chosen.CSingleVolume) : null,
    volumeUnit: '口',
    fetchedAt: Date.now()
  };
}

/** 台指期折線只放期交所實際報價快照；現貨走勢加期現價差不是期貨成交價。 */
function buildTxActualLine_(quote) {
  if (!quote || !quote.curIso || !quote.contract || !(Number(quote.value) > 0)) return [];
  var session = quote.session === 'AFTERHOURS' ? 'night' : 'day';
  var quoteDate = new Date(quote.curIso);
  if (isNaN(quoteDate.getTime())) return [];
  var start = new Date(quoteDate.getTime());
  if (session === 'night' && Number(Utilities.formatDate(quoteDate, TZ, 'HHmm')) < 845) {
    start = new Date(start.getTime() - 86400000);
  }
  var day = Utilities.formatDate(start, TZ, 'yyyyMMdd');
  var prefix = 'txsnap53_' + session + '_' + quote.contract + '_' + day + '_';
  var hour = Number(Utilities.formatDate(quoteDate, TZ, 'HH'));
  var segment = String(Math.floor(hour / 4));
  var store = PropertiesService.getScriptProperties();
  var readLine = function () {
    var all = [];
    for (var i = 0; i < 6; i++) {
      try {
        var rows = JSON.parse(store.getProperty(prefix + i) || '[]');
        if (Array.isArray(rows)) all = all.concat(rows);
      } catch (e) { Logger.log('台指期快照區段讀取失敗：' + e); }
    }
    var byTime = {};
    all.forEach(function (r) {
      if (Array.isArray(r) && r.length >= 2 && Number(r[1]) > 0) byTime[r[0]] = r;
    });
    var previous=null;
    return Object.keys(byTime).sort().map(function (t) {
      var r=byTime[t],total=r[2]==null?null:Number(r[2]);
      var volume=previous!=null&&total!=null&&total>=previous?total-previous:null;
      previous=total;
      return {time:t,value:Number(r[1]),volume:volume};
    });
  };
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(500)) return readLine();
  try {
    var key = prefix + segment, rows;
    try { rows = JSON.parse(store.getProperty(key) || '[]'); } catch (e) { rows = []; }
    if (!Array.isArray(rows)) rows = [];
    var next = [quote.curIso, Number(quote.value), quote.tradeVolume == null ? null : Number(quote.tradeVolume)];
    var bucket=Math.floor(quoteDate.getTime()/300000);
    var pos = rows.findIndex(function (r) { return Math.floor(Date.parse(r[0])/300000) === bucket; });
    if (pos >= 0) rows[pos] = next; else rows.push(next);
    rows.sort(function (a,b) { return String(a[0]).localeCompare(String(b[0])); });
    // 四小時一段低於單一 Script Property 的 9KB 上限；過期時只刪舊快照，不動行情來源。
    store.setProperty(key, JSON.stringify(rows.slice(-60)));
    var registry;
    try { registry = JSON.parse(store.getProperty('txreal_keys') || '[]'); } catch (e) { registry = []; }
    if (!Array.isArray(registry)) registry = [];
    if (registry.indexOf(key) < 0) registry.push(key);
    var cutoff = Utilities.formatDate(new Date(Date.now() - 3 * 86400000), TZ, 'yyyyMMdd');
    registry = registry.filter(function (k) {
      var match = k.match(/_(\d{8})_\d$/);
      if (match && match[1] < cutoff) { store.deleteProperty(k); return false; }
      return true;
    });
    store.setProperty('txreal_keys', JSON.stringify(registry));
  } finally { lock.releaseLock(); }
  return readLine();
}

/** 期權有獨立權限；未授權只降級收盤快照，不連續重打、不冒充即時。 */
function attachLiveFuture_(out, snapshotOnly){
  var now = new Date(), hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
  var isNightWindow = (hhmm >= 1500 || hhmm <= 505);
  var old = out.cards.filter(function(c){ return c.key === 'tx'; })[0];
  var live = null;
  try { live = JSON.parse(CACHE.get('market_live_tx_v53') || 'null'); } catch(e) {}

  var isCurrent = live && live.activeSession === (isNightWindow ? 'afterhours' : 'regular') && (Date.now() - live.fetchedAt < 300000);
  var props=PropertiesService.getScriptProperties(),captureToken='';
  if(!isCurrent&&!CACHE.get('market_tx_blocked')){
    captureToken=withLock_(function(){
      var lease=JSON.parse(props.getProperty('txCaptureLease')||'null');
      if(lease&&lease.until>Date.now()){return '';}
      var token=String(Date.now())+'-'+Math.random();
      props.setProperty('txCaptureLease',JSON.stringify({token:token,until:Date.now()+120000}));return token;
    });
  }
  if (captureToken) {
    try {
      // 雙盤抓取：日盤 (MarketType=0) 與夜盤 (MarketType=1)
      var chosenReg = fetchTaifexQuoteByType_(0);
      var chosenAfter = fetchTaifexQuoteByType_(1);

      var regData = parseTaifexItem_(chosenReg, 'REGULAR');
      var afterData = parseTaifexItem_(chosenAfter, 'AFTERHOURS');

      var regLine = buildTxActualLine_(regData);
      var afterLine = buildTxActualLine_(afterData);

      if (regData) { regData.line = regLine; }
      if (afterData) { afterData.line = afterLine; }

      var primary = isNightWindow ? (afterData || regData) : (regData || afterData);
      if (!primary) throw new Error('期交所尚無有效成交報價');

      var yyyymm = primary.contract;
      live = {
        key: 'tx',
        label: '台指期 ' + yyyymm + (primary.session==='AFTERHOURS' ? ' (夜盤)' : ' (日盤)'),
        contract: yyyymm,
        symbol: primary.symbol,
        session: primary.session,
        activeSession: primary.session==='AFTERHOURS' ? 'afterhours' : 'regular',
        unit: '點',
        value: primary.value,
        open: primary.open,
        high: primary.high,
        low: primary.low,
        change: primary.change,
        percent: primary.percent,
        time: primary.time,
        source: primary.source,
        tradeVolume: primary.tradeVolume,
        volumeUnit: '口',
        fetchedAt: Date.now(),
        line: primary.line || [],
        sessions: {
          regular: regData ? {
            contract:regData.contract,session:'REGULAR',
            value: regData.value, change: regData.change, percent: regData.percent,
            open: regData.open, high: regData.high, low: regData.low,
            time: regData.time, source: regData.source, tradeVolume: regData.tradeVolume,
            volumeUnit: '口', line: regLine
          } : null,
          afterhours: afterData ? {
            contract:afterData.contract,session:'AFTERHOURS',
            value: afterData.value, change: afterData.change, percent: afterData.percent,
            open: afterData.open, high: afterData.high, low: afterData.low,
            time: afterData.time, source: afterData.source, tradeVolume: afterData.tradeVolume,
            volumeUnit: '口', line: afterLine
          } : null
        }
      };
      CACHE.put('market_live_tx_v53', JSON.stringify(live), 21600);
    } catch(e) {
      CACHE.put('market_tx_blocked', '1', 180);
      out.warnings.push('期交所連線失敗');
    } finally {
      withLock_(function(){var lease=JSON.parse(props.getProperty('txCaptureLease')||'null');
        if(lease&&lease.token===captureToken){props.deleteProperty('txCaptureLease');}});
    }
  }

  if (snapshotOnly) { if(live)out.cards.push(live); return; }
  if (live && Date.now() - live.fetchedAt <= 21600000) {
    if (old && old.contract === live.contract) {
      live.candles = old.candles;
      live.historySource = old.source;
    }
    if (!live.candles || live.candles.length <= 1) {
      try {
        var fmStart = Utilities.formatDate(new Date(Date.now() - 100 * 86400000), TZ, 'yyyy-MM-dd');
        var fmToken = (typeof getFinMindToken_ === 'function') ? getFinMindToken_() : '';
        var fmUrl = 'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date=' + fmStart +
                    (fmToken ? ('&token=' + encodeURIComponent(fmToken)) : '');
        var fmRes = UrlFetchApp.fetch(fmUrl, { muteHttpExceptions: true });
        if (fmRes.getResponseCode() === 200) {
          var fmJson = JSON.parse(fmRes.getContentText()), fmData = fmJson.data || [];
          var cMap = {};
          fmData.forEach(function(r) {
            if (r.futures_id === 'TX' && String(r.contract_date) === live.contract && Number(r.close) > 0) {
              var d = String(r.date).replace(/-/g, '/');
              if (r.trading_session === 'position' || !cMap[d]) {
                cMap[d] = [d, Number(r.open), Number(r.max), Number(r.min), Number(r.close), Number(r.volume) || 0];
              }
            }
          });
          var bars = Object.keys(cMap).sort().map(function(k) { return cMap[k]; });
          if (bars.length > 1) { live.candles = bars; live.historySource = 'FinMind 台指期近月日K（盤後資料）'; }
        }
      } catch(fmErr) { Logger.log('FinMind 台指期補日K失敗：' + fmErr); }
    }
    if (!isCurrent && Date.now() - live.fetchedAt >= 300000) {
      live.source += '（保留快取，非本次即時回應）';
    }
    out.cards = out.cards.filter(function(c) { return c.key !== 'tx'; });
    out.cards.splice(1, 0, live);
  }
}

/** 夜盤在午夜後仍屬前一個開盤日，星期六凌晨要接續星期五夜盤。 */
function marketSnapshotWindow_(now){
  var hm=Number(Utilities.formatDate(now,TZ,'HHmm'));
  var base=new Date(now.getTime());
  if(hm<=505){base=new Date(base.getTime()-86400000);}
  var weekday=Number(Utilities.formatDate(base,TZ,'u'))<=5;
  return weekday&&!isMarketHoliday_(base)&&((hm>=845&&hm<=1350)||hm>=1500||hm<=505);
}

/** 專用五分鐘取樣，不讀日 K、不跑模型；無人開網站也累積日／夜盤。 */
function marketSnapshotJob(){
  if(!marketSnapshotWindow_(new Date())){return;}
  var props=PropertiesService.getScriptProperties(),out={cards:[],warnings:[]};
  try{
    attachLiveFuture_(out,true);
    var live=out.cards[0];
    props.setProperty('marketSnapshotStatus',JSON.stringify({at:nowStamp_(),
      ok:!!live&&Date.now()-live.fetchedAt<360000,
      quoteAt:live?live.time:'',dayPoints:live&&live.sessions.regular?live.sessions.regular.line.length:0,
      nightPoints:live&&live.sessions.afterhours?live.sessions.afterhours.line.length:0,
      note:out.warnings.join('；')}));
    // 前台下次打開要拿到剛才採樣的資料，而非舊的整站市場包。
    CACHE.remove('market_board_v53');
  }catch(e){props.setProperty('marketSnapshotStatus',JSON.stringify({at:nowStamp_(),ok:false,note:String(e.message||e)}));}
}

function installMarketSnapshotJob(){
  if(ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='everyFiveMinJob';})){
    Logger.log('市場快照已由 everyFiveMinJob 每五分鐘執行；不新增重複觸發器。');
    return;
  }
  if(!ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='marketSnapshotJob';})){
    ScriptApp.newTrigger('marketSnapshotJob').timeBased().everyMinutes(5).create();
  }
  Logger.log('已確認台指期每五分鐘背景取樣；日夜盤分開保存。');
}

function marketSnapshotStatus(){
  var result=JSON.parse(PropertiesService.getScriptProperties().getProperty('marketSnapshotStatus')||'{}');
  result.trigger=ScriptApp.getProjectTriggers().some(function(t){return ['marketSnapshotJob','everyFiveMinJob'].indexOf(t.getHandlerFunction())>=0;});
  result.mode=ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='everyFiveMinJob';})?'五分鐘總排程':result.trigger?'獨立排程':'未排程';
  Logger.log(JSON.stringify(result));return result;
}

/** 部署後只讀驗證；不把金鑰或原始回應印進日誌。 */
function checkMarketLiveReadiness(){
  var result={futures:false,index:false};
  try{var res=UrlFetchApp.fetch('https://mis.taifex.com.tw/futures/api/getQuoteList',{method:'post',contentType:'application/json',payload:JSON.stringify({"MarketType":0,"SymbolType":"F","KindID":1,"CID":"TXF","ExpireMonths":"","RowSize":"全部","PageNo":"","SortColumn":"","AscDesc":"A"}),muteHttpExceptions:true});var json=JSON.parse(res.getContentText());result.futures=(json.RtCode==="0");if(result.futures){CACHE.remove('market_tx_blocked');}}catch(e){result.futuresError='TAIFEX API ERROR';}
  try{var res=UrlFetchApp.fetch('https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII',{muteHttpExceptions:true});result.index=(res.getResponseCode()===200);if(result.index){CACHE.remove('market_index_blocked');}}catch(e){result.indexError='YAHOO API ERROR';}
  Logger.log(JSON.stringify(result));return result;
}

/* 國際行情的折線範圍（v54，管理者 2026/09/23）：取「最近 6 小時」。

   原本用 range=1d 再依交易所時區的日期切一天。WTI、Brent、美元指數的交易所在紐約，
   紐約午夜＝台北中午 12:00（夏令時間），於是下午兩點打開只看得到 12:00 以後的兩個小時。

   現在的規則：
     一、以最新一筆的時間 T 為準，取 T−6 小時 到 T。下午兩點看，就是 08:00–14:00。
     二、這 6 小時裡如果有休市（相鄰兩筆有效報價相隔超過 30 分鐘，例如美元指數台北 06:00–07:30 每天休息），
         不把休市那段連起來，改從重新開盤那一筆畫起；時間軸仍是 6 小時，右邊還沒發生的部分留白。
     三、資料不足 6 小時（剛開盤）同第二點。
   前端（MarketCharts.html 的 marketRollingBounds）用同一套規則；舊快取沒有 windowStart 時由前端自己算。 */
var MARKET_ROLL_MS_ = 6 * 3600000, MARKET_ROLL_GAP_MS_ = 30 * 60000, MARKET_ROLL_STEP_MS_ = 300000;
function marketRollingWindow_(msList) {
  var ms = (msList || []).filter(function (t) { return isFinite(t); }).sort(function (a, b) { return a - b; });
  if (!ms.length) { return null; }
  var T = ms[ms.length - 1], S = T - MARKET_ROLL_MS_, dataStart = null, prev = null;
  ms.forEach(function (t) {
    if (t < S) { return; }
    if (dataStart === null || (prev !== null && t - prev > MARKET_ROLL_GAP_MS_)) { dataStart = t; }
    prev = t;
  });
  var resumed = dataStart - S > MARKET_ROLL_GAP_MS_, start, end;
  if (resumed) { start = Math.floor(dataStart / MARKET_ROLL_STEP_MS_) * MARKET_ROLL_STEP_MS_; end = start + MARKET_ROLL_MS_; }
  else { end = Math.ceil(T / MARKET_ROLL_STEP_MS_) * MARKET_ROLL_STEP_MS_; start = end - MARKET_ROLL_MS_; }
  return { start: start, end: end, dataStart: dataStart, last: T, resumed: resumed };
}

/** Yahoo 日內可能延遲；折線取最近 6 小時（見上），不拿歷史日線充當今日折線。 */
function yahooIntradayCard_(payload,key,label,unit){
  var r=payload&&payload.chart&&payload.chart.result&&payload.chart.result[0];
  if(!r){throw new Error('來源未提供日內資料');}
  var meta=r.meta||{},quote=r.indicators&&r.indicators.quote&&r.indicators.quote[0]||{},times=r.timestamp||[],offset=Number(meta.gmtoffset)||0;
  var points=times.map(function(t,i){return {time:new Date(Number(t)*1000).toISOString(),value:quote.close&&quote.close[i],volume:quote.volume&&quote.volume[i],epoch:Number(t)};}).filter(function(p){return p.value!=null&&isFinite(p.value)&&p.value>0&&p.epoch*1000<=Date.now()+60000;});
  if(!points.length){throw new Error('沒有有效成交點');}
  var latest=points[points.length-1],sessionDay=new Date((latest.epoch+offset)*1000).toISOString().slice(0,10);
  var win=marketRollingWindow_(points.map(function(p){return p.epoch*1000;}));
  points=points.filter(function(p){return p.epoch*1000>=win.dataStart;});
  /* 前一日收盤：range 超過一天時 chartPreviousClose 是「整段區間開始前」的收盤（兩天前），不能拿來算漲跌。
     Yahoo 在 meta.previousClose 另給前一交易日收盤（2026/09/23 實測 CL=F：previousClose 90.52、chartPreviousClose 94.59）；
     只有 range=1d 的舊回應才退回 chartPreviousClose。 */
  var prev=Number(meta.previousClose);
  if(!(prev>0)&&(!meta.range||meta.range==='1d')){prev=Number(meta.chartPreviousClose);}
  var value=latest.value;
  return {key:key,label:label,unit:unit,value:value,change:prev>0?value-prev:null,percent:prev>0?(value/prev-1)*100:null,
    time:Utilities.formatDate(new Date(latest.epoch*1000),TZ,'yyyy/MM/dd HH:mm:ss'),sessionDate:sessionDay,
    windowStart:win.start,windowEnd:win.end,windowResumed:win.resumed,
    source:'Yahoo 日內5分資料（可能延遲）',line:points,quoteMode:'近 6 小時／可能延遲',fetchedAt:Date.now()};
}
function attachYahooIntraday_(out, force){
  var specs=[['dxy','DX-Y.NYB','美元指數','點'],['usdtwd','TWD=X','美元／新台幣','新台幣'],['wti','CL=F','WTI 近月期貨','美元／桶'],['brent','BZ=F','Brent 近月期貨','美元／桶']];
  // 最近 6 小時要跨過紐約午夜，range=1d 不夠。週一與週末往回取 5 天，才拿得到上週五最後一段。
  var dow=Number(Utilities.formatDate(new Date(),TZ,'u')),yRange=(dow===1||dow>=6)?'5d':'2d';
  specs.forEach(function(s){
    // 快取鍵升版（v54）：舊鍵裡是只有紐約當天的折線，換規則後不能再沿用。
    var key='market_yahoo_v54_'+s[0],live=null;try{live=JSON.parse(CACHE.get(key)||'null');}catch(e){}
    if((force||!live||Date.now()-live.fetchedAt>=300000)&&!CACHE.get('market_yahoo_blocked')){
      try{
        // 一次頁面最多四個請求，各市場共用五分鐘快取。429後半小時不再打任何Yahoo來源。
        var response=UrlFetchApp.fetch('https://query1.finance.yahoo.com/v8/finance/chart/'+encodeURIComponent(s[1])+'?interval=5m&range='+yRange,{muteHttpExceptions:true});
        var status=response.getResponseCode();
        if(status!==200){if([401,403,429].indexOf(status)>=0){CACHE.put('market_yahoo_blocked','1',1800);}throw new Error('HTTP '+status);}
        live=yahooIntradayCard_(JSON.parse(response.getContentText()),s[0],s[2],s[3]);CACHE.put(key,JSON.stringify(live),21600);
      }catch(e){out.warnings.push(s[2]+' 日內來源未就緒，保留最近資料');}
    }
    if(!live){return;}
    if(!force&&Date.now()-live.fetchedAt>=300000){live.source+='（保留快取）';}
    var old=out.cards.filter(function(c){return c.key===s[0];})[0];if(old){live.candles=old.candles;live.historySource=old.historySource||old.source;}
    out.cards=out.cards.filter(function(c){return c.key!==s[0];});out.cards.push(live);
  });
}

function fetchYahoo60m_(symbol) {
  var key = 'market_60m_' + symbol;
  var hit = CACHE.get(key);
  if (hit) {
    try { return JSON.parse(hit); } catch (e) {}
  }
  try {
    var res = UrlFetchApp.fetch('https://query1.finance.yahoo.com/v8/finance/chart/' + encodeURIComponent(symbol) + '?interval=60m&range=1mo', {
      muteHttpExceptions: true,
      headers: { 'User-Agent': 'Mozilla/5.0' }
    });
    if (res.getResponseCode() === 200) {
      var json = JSON.parse(res.getContentText()), result = json.chart && json.chart.result && json.chart.result[0];
      if (result) {
        var times = result.timestamp || [], quote = result.indicators && result.indicators.quote && result.indicators.quote[0] || {};
        var bars = [];
        for (var i = 0; i < times.length; i++) {
          var c = quote.close && quote.close[i];
          if (c != null && isFinite(c) && c > 0) {
            var t = new Date(times[i] * 1000);
            var o = quote.open && quote.open[i] != null ? Number(quote.open[i]) : c;
            var h = quote.high && quote.high[i] != null ? Number(quote.high[i]) : Math.max(o, c);
            var l = quote.low && quote.low[i] != null ? Number(quote.low[i]) : Math.min(o, c);
            var v = quote.volume && quote.volume[i] != null ? Number(quote.volume[i]) : 0;
            bars.push([Utilities.formatDate(t, TZ, 'yyyy/MM/dd HH:mm'), o, h, l, c, v]);
          }
        }
        if (bars.length) {
          try { CACHE.put(key, JSON.stringify(bars), 600); } catch (e) {}
          return bars;
        }
      }
    }
  } catch (e) {}
  return [];
}
