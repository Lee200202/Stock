/** v94：僅保留個股歷史分 K 與富果限流；市場總覽已停用。 */
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
  // v67：觸發器耗時紀錄（見 Setup.gs 的 opsTimed_）；本體在 backfillHourlyHistoryJobRun_
  return typeof opsTimed_ === 'function' ? opsTimed_('backfillHourlyHistoryJob', backfillHourlyHistoryJobRun_, arguments) : backfillHourlyHistoryJobRun_.apply(null, arguments);
}
function backfillHourlyHistoryJobRun_(force, restart) {
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
    var codes=trackedCodes_().sort(),cursor={},tally={ok:0,none:[],fail:[]};
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
        tally.ok++;
        p.setProperty('hourHistoryProgress',code+'｜'+(i+1)+'/'+codes.length+'｜'+day);
      } catch(e) {
        p.setProperty('hourHistoryProgress',code+'｜待補：'+String(e.message||e));
        // 權限與限流不是沒有行情；本輪停，不高速重試，更不能用空值蓋掉好資料。
        if([401,403,429].indexOf(Number(e.httpCode))>=0){Logger.log('分K歷史停止：'+e.message);complete=true;break;}
        if(Number(e.httpCode)===404){tally.none.push(code);}else{tally.fail.push(code);}
        if(Number(e.httpCode)===404){
          var kept=[];try{kept=prior?JSON.parse(prior.values[2]||'[]'):[];}catch(ignore){}
          saveHourHistory_(state,code,kept,'來源404；保留既有資料，隔日再試，Yahoo另補空缺');
        }
        Logger.log(code+' 分K待補：'+e.message);
      }
      // 失敗檔也記本輪已嘗試，避免前幾檔故障讓後面的永遠輪不到。隔日會重試。
      cursor.code=code;p.setProperty('hourHistoryCursor',JSON.stringify(cursor));
    }
    if(complete){
      // v107：本輪跑完寫摘要，不讓最後一檔（例如 9958 的 404）蓋成「整批卡住」。404＝來源沒有這檔 60 分 K，不算待補。
      p.setProperty('hourHistoryProgress',('本輪結束｜完成 '+tally.ok+' 檔｜來源無資料 '+tally.none.length+' 檔'+
        (tally.none.length?'（'+tally.none.slice(0,4).join('、')+(tally.none.length>4?'…':'')+'）':'')+'｜待補 '+tally.fail.length+' 檔｜'+day).slice(0,100));
      Logger.log('分K本輪結束；各檔狀態可用 auditHourlyCoverage(代號) 核對');
    }
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
  getSheet_('分K歷史');getSheet_('Yahoo分K');
  if(!ScriptApp.getProjectTriggers().some(function(t){return t.getHandlerFunction()==='backfillHourlyHistoryJob';})){
    ScriptApp.newTrigger('backfillHourlyHistoryJob').timeBased().everyDays(1).atHour(19).nearMinute(15).inTimezone(TZ).create();
  }
  Logger.log('已確認歷史分K排程；手動先執行 backfillHourlyHistoryNow() 補第一輪。');
}


/** v94：舊版獨立觸發器部署前可能還存在；入口不抓資料，正式移除由 migrateV94 執行。 */
function marketSnapshotJob(){return {retired:true};}
function sectorCatchupJob(){return {retired:true};}
function migrateV94(){
  var removed=[];
  ScriptApp.getProjectTriggers().forEach(function(t){
    var n=t.getHandlerFunction();
    if(['marketSnapshotJob','sectorCatchupJob'].indexOf(n)>=0){ScriptApp.deleteTrigger(t);removed.push(n);}
  });
  var p=PropertiesService.getScriptProperties();
  ['marketSnapshotStatus','sectorCatchupStatus','sectorCatchupLease','market_live_index','sectorCatchup'].forEach(function(k){p.deleteProperty(k);});
  auditTrackedSymbolsJob(true);
  Logger.log('v94 市場總覽已停用，移除觸發器：'+removed.join('、')+'。歷史資料未刪除；個股五分鐘報價、日K、分K、取稿與寄送排程保留。');
  return {ok:true,removed:removed};
}
