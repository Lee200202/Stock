/** 複核紀錄後台：完整讀取外部紀錄表；原始紀錄與管理者修訂分開保存。 */
var REVIEW_HISTORY_REV_TAB_ = '管理者修訂';
var REVIEW_HISTORY_REV_HEAD_ = ['記錄ID','原始指紋','修訂ID','更新時間','類型','操作ID','內容JSON'];
var REVIEW_HISTORY_FIELDS_ = ['代號','名稱','網站分類','複核分類','可接受集合','差異類型','嚴重度','原文依據','判斷理由','處理','修正前','修正後','是否轉成規則或測試','建議修改','驗收結果','管理者註記'];

function reviewHistoryCanonical_(v) {
  if (v instanceof Date) { return JSON.stringify(v.toISOString()); }
  if (Array.isArray(v)) { return '[' + v.map(reviewHistoryCanonical_).join(',') + ']'; }
  if (v && typeof v === 'object') { return '{' + Object.keys(v).sort().map(function(k){return JSON.stringify(k)+':'+reviewHistoryCanonical_(v[k]);}).join(',') + '}'; }
  return JSON.stringify(v == null ? null : v);
}
function reviewHistoryHash_(v) { return transcriptSha256_(reviewHistoryCanonical_(v)); }
function reviewHistoryDate_(v) {
  var s = String(v || '').trim().replace(/-/g,'/');
  if (!/^\d{4}\/\d{2}\/\d{2}$/.test(s)) { return ''; }
  var t = new Date(s.replace(/\//g,'-')+'T00:00:00Z');
  return isFinite(t.getTime()) && t.toISOString().slice(0,10).replace(/-/g,'/')===s ? s : '';
}
function reviewHistoryRows_(book, name, required) {
  var sh = book.getSheetByName(name);
  if (!sh) { if(required){throw new Error('複核紀錄缺少「'+name+'」分頁，不能當成零筆。');}return []; }
  if (!sh.getLastRow()) { if(required){throw new Error('複核紀錄「'+name+'」缺少表頭。');}return []; }
  var values=sh.getRange(1,1,sh.getLastRow(),sh.getLastColumn()).getDisplayValues(),head=values[0];
  if (required && head.indexOf('日期')<0) { throw new Error(name+'缺少日期欄，停止讀取。'); }
  return values.slice(1).map(function(row,i){var d={};head.forEach(function(h,j){d[String(h).trim()]=String(row[j] == null ? '' : row[j]);});return {row:i+2,data:d};});
}
function reviewHistoryBook_() {
  var book=dailyReviewBook_();
  if(!book){throw new Error('尚未連接每日複核紀錄試算表，請核對 REVIEW_LOG_ID；不是沒有錯誤紀錄。');}
  return book;
}
function reviewHistorySnapshot_(book) {
  var details=reviewHistoryRows_(book,'差異明細',true),counts={},warnings=[];
  var changes=reviewHistoryRows_(book,REVIEW_HISTORY_REV_TAB_,false).map(function(row){
    try {var d=row.data;return {id:d['記錄ID'],sourceHash:d['原始指紋'],revision:d['修訂ID'],at:d['更新時間'],kind:d['類型'],operationId:d['操作ID'],content:JSON.parse(d['內容JSON'] || '{}')};}
    catch(e){warnings.push('一筆管理者修訂格式不完整，原始差異仍保留。');return null;}
  }).filter(Boolean);
  var detailSheet=book.getSheetByName('差異明細');
  var entries=details.filter(function(x){return Object.keys(x.data).some(function(k){return !!x.data[k];});}).map(function(x){
    var d=x.data,day=reviewHistoryDate_(d['日期']);
    if(!day){warnings.push('差異明細第'+x.row+'列日期格式不完整，未納入日期篩選。');}
    var anchor=reviewHistoryHash_([day,d['代號'],d['執行'],d['差異類型'],d['原文依據']]);
    counts[anchor]=(counts[anchor]||0)+1;
    var id=anchor+'-'+counts[anchor],hash=reviewHistoryHash_(d);
    var history=changes.filter(function(c){return c.id===id;}),effective=Object.assign({},d),revision='0',editedAt='';
    history.forEach(function(c){if(c.sourceHash===hash && c.kind==='紀錄修訂'){Object.keys(c.content.edits||{}).forEach(function(k){effective[k]=c.content.edits[k];});revision=c.revision;editedAt=c.at;}});
    return {id:id,date:day,sourceHash:hash,revision:revision,editedAt:editedAt,sourceRow:x.row,
      sourceUrl:book.getUrl()+'#gid='+detailSheet.getSheetId()+'&range=A'+x.row,
      original:d,current:effective,history:history};
  });
  return {bookUrl:book.getUrl(),entries:entries,warnings:warnings,
    summaries:reviewHistoryRows_(book,'每日摘要',true).map(function(x){return x.data;}),
    reports:reviewHistoryRows_(book,'通知',false).map(function(x){return x.data;}),
    candidates:reviewHistoryRows_(book,'提示詞候選',false).map(function(x){return x.data;})};
}
function reviewHistoryEntry_(snap,id,hash) {
  var entry=snap.entries.filter(function(e){return e.id===String(id||'');})[0];
  if(!entry || entry.sourceHash!==hash){throw new Error('來源紀錄已更新或移動，請重新讀取；本次沒有覆寫。');}
  return entry;
}
function reviewHistorySite_(key,entry) {
  var result=apiAdminDayRows(key,entry.date);
  if(!result || !result.ok){throw new Error(result && result.reason || '讀不到當天網站資料。');}
  return (result.rows||[]).filter(function(r){return entry.original['代號'] ? String(r.code)===String(entry.original['代號']) : r.name===entry.original['名稱'];}).map(function(r){
    var sh=getSheet_(r.sheet),width=sh.getLastColumn();
    r.writeHash=reviewHistoryHash_(sh.getRange(r.row,1,1,width).getValues()[0]);
    return r;
  });
}
/** 僅管理者；index列日期，list完整分頁，site只讀目前真實股票列。 */
function apiAdminReviewHistory(key, options) {
  try {
    adminAuth_(key);var opt=options||{},book=reviewHistoryBook_(),snap=reviewHistorySnapshot_(book);
    if(opt.action==='site'){
      var entry=reviewHistoryEntry_(snap,opt.id,opt.sourceHash);
      return {ok:true,rows:reviewHistorySite_(key,entry),date:entry.date};
    }
    var dates={};snap.entries.forEach(function(e){if(e.date)dates[e.date]=true;});
    snap.summaries.forEach(function(s){var d=reviewHistoryDate_(s['日期']);if(d)dates[d]=true;});
    if(opt.action==='index') {return {ok:true,dates:Object.keys(dates).sort(),today:todayStr_(),bookUrl:snap.bookUrl,warnings:snap.warnings};}
    var start=reviewHistoryDate_(opt.start),end=reviewHistoryDate_(opt.end);
    if(!start || !end || start>end){throw new Error('請選擇有效的起訖日期，起日不能晚於迄日。');}
    var inRange=function(d){d=reviewHistoryDate_(d);return d && d>=start && d<=end;};
    var entries=snap.entries.filter(function(e){return inRange(e.date);}).sort(function(a,b){return b.date.localeCompare(a.date)||a.sourceRow-b.sourceRow;});
    var summaries=snap.summaries.filter(function(r){return inRange(r['日期']);});
    var reports=snap.reports.filter(function(r){return inRange(r['日期']);}).map(function(r){return {date:r['日期'],createdAt:r['建立時間'],type:r['類型'],subject:r['主旨'],text:r['內容'],status:r['狀態']};});
    var candidates=snap.candidates.filter(function(r){return inRange(r['日期']);});
    var token=reviewHistoryHash_({entries:entries,summaries:summaries,reports:reports,candidates:candidates});
    if(opt.token && opt.token!==token){return {ok:false,stale:true,reason:'查詢期間紀錄有更新，請重新讀取完整區間，避免匯出不同版本的資料。'};}
    var offset=Number(opt.offset||0);if(!isFinite(offset)||offset<0||offset%1){throw new Error('分頁位置不正確。');}
    var chunk=entries.slice(offset,offset+80);
    return {ok:true,start:start,end:end,token:token,total:entries.length,entries:chunk,
      nextOffset:offset+chunk.length<entries.length ? offset+chunk.length : null,
      summaries:offset===0?summaries:[],reports:offset===0?reports:[],candidates:offset===0?candidates:[],
      bookUrl:snap.bookUrl,warnings:snap.warnings,generatedAt:nowStamp_()};
  } catch(e){return {ok:false,reason:String(e && e.message || e)};}
}
function reviewHistoryAppend_(book,entry,kind,operationId,content) {
  var sh=book.getSheetByName(REVIEW_HISTORY_REV_TAB_);
  if(!sh){sh=book.insertSheet(REVIEW_HISTORY_REV_TAB_);sh.getRange(1,1,1,REVIEW_HISTORY_REV_HEAD_.length).setValues([REVIEW_HISTORY_REV_HEAD_]);sh.setFrozenRows(1);}
  var json=JSON.stringify(content);if(json.length>48000){throw new Error('修訂內容太長，請分次儲存；原始紀錄未更動。');}
  var revision=Utilities.getUuid();
  sh.getRange(sh.getLastRow()+1,1,1,7).setValues([[entry.id,entry.sourceHash,revision,nowStamp_(),kind,operationId,json]]);
  return revision;
}
/** note只附加管理者版本；website沿用既有逐日編輯及四步同步，先保存操作紀錄。 */
function apiAdminReviewHistorySave(key, input) {
  var lock=null,writeStarted=false;
  try {
    adminAuth_(key);input=input||{};
    if(['note','website'].indexOf(input.scope)<0){throw new Error('儲存範圍不正確。');}
    var op=String(input.operationId||'');if(!/^[A-Za-z0-9_-]{8,100}$/.test(op)){throw new Error('缺少本次操作識別，請重新開啟編輯。');}
    lock=LockService.getScriptLock();if(!lock.tryLock(5000)){lock=null;throw new Error('其他資料正在儲存，稍後再試。');}
    var book=reviewHistoryBook_(),snap=reviewHistorySnapshot_(book),entry=reviewHistoryEntry_(snap,input.id,input.sourceHash);
    var old=entry.history.filter(function(h){return h.operationId===op;});
    if(old.length){var done=old.filter(function(h){return h.content.result && h.content.result.ok;}).pop();return done ? done.content.result : {ok:false,reason:'此操作已有紀錄，請重新讀取網站資料確認結果，不要重複送出。'};}
    if(input.scope==='note'){
      if(String(input.revision||'0')!==entry.revision){throw new Error('另一位管理者已修改這筆說明，請重新讀取再修改。');}
      var edits={};Object.keys(input.edits||{}).forEach(function(k){if(REVIEW_HISTORY_FIELDS_.indexOf(k)<0){throw new Error('不能修改此紀錄欄位：'+k);}edits[k]=String(input.edits[k] == null ? '' : input.edits[k]);});
      var unchanged=Object.keys(edits).every(function(k){return String(entry.current[k]||'')===edits[k];});
      if(unchanged){return {ok:true,noChange:true,message:'內容未變更，沒有新增修訂。'};}
      var result={ok:true,message:'複核說明已儲存；原始紀錄保留，未修改網站分類。'};
      var revision=reviewHistoryAppend_(book,entry,'紀錄修訂',op,{edits:edits,before:entry.current,result:result});
      result.revision=revision;return result;
    }
    var payload=input.payload||{},matches=reviewHistorySite_(key,entry);
    var target=matches.filter(function(r){return r.sheet===payload.sheet && Number(r.row)===Number(payload.row);})[0];
    if(!target || target.writeHash!==input.writeHash){throw new Error('網站這一列已變更，請重新讀取當前網站資料再儲存。');}
    var cls=String(payload.cls||''),reason=String(payload.reason||'').trim(),name=String(payload.newName||'').trim(),code=String(payload.newCode||'').trim();
    if(!reason||!name||['買入','賣出','觀望不碰','觀望注意','會員持股'].indexOf(cls)<0){throw new Error('請填完整股票名稱、合法分類與說明。');}
    if(target.name===name && target.code===code && target.cls===cls && target.reason===reason && String(target.price||'')===String(payload.price||'')){
      return {ok:true,noChange:true,message:'網站內容未變更，沒有寫入或啟動同步。'};
    }
    reviewHistoryAppend_(book,entry,'網站修改',op,{stage:'準備儲存',before:target,requested:payload});
    // apiAdminUpdateRow內部自行加鎖；先釋放紀錄表鎖，避免重入。
    lock.releaseLock();lock=null;
    var safe={sheet:target.sheet,row:target.row,date:entry.date,name:target.name,code:target.code,
      newName:name,newCode:code,cls:cls,price:String(payload.price||''),reason:reason,expectedRowHash:target.writeHash};
    writeStarted=true;var requestedAt=nowStamp_();
    var result=apiAdminUpdateRow(key,safe);
    if(result.sync){result.sync.requestedAt=requestedAt;}
    var finalLock=LockService.getScriptLock();
    if(finalLock.tryLock(5000)){
      try{reviewHistoryAppend_(book,entry,'網站修改',op,{stage:result.ok?'已儲存':'未儲存',result:result,requested:safe});}
      finally{finalLock.releaseLock();}
    } else {result.auditWarning='網站回應已收到，但修訂紀錄尚待補記；請重新讀取確認，不要重送。';}
    return result;
  } catch(e){return {ok:false,unknown:writeStarted,reason:(writeStarted?'網站儲存回應未完整取得，請先重新讀取確認，不要重送。 ':'')+String(e && e.message || e)};}
  finally{if(lock){lock.releaseLock();}}
}
