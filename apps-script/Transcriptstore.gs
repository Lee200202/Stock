/** 原文版本、同日選列與整併。原文較短也可能較新，不能拿篇幅取代版本。 */
function transcriptSha256_(text) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text), Utilities.Charset.UTF_8)
    .map(function(b){return ('0'+((b+256)%256).toString(16)).slice(-2);}).join('');
}

/** 新欄位只能附加；舊表頭若曾被人工改序，先停下而非把指紋寫到錯欄。 */
function requireTranscriptHeaders_() {
  ['影片清單','後台工單'].forEach(function(name){
    var sh=getSheet_(name),head=sh.getDataRange().getValues()[0];
    var expected=SHEET_SCHEMA[name];
    if(expected.some(function(h,i){return name==='影片清單' && i>=7 ? head.indexOf(h)<0 : String(head[i]||'').trim()!==h;})){
      throw new Error(name+'表頭不符合目前欄位，請先核對 Setup.gs；本輪尚未投稿');
    }
  });
}

/** 與 Python select_transcript_row 共用排序：人工優先、ID、更新時間、原文長度、列號。 */
function selectTranscriptRow_(rows, videoId, d) {
  var best=null, rank=null;
  rows.forEach(function(r,i){
    var same=!!videoId && String(r['影片ID']||'').trim()===videoId;
    if(!same && fmtDate_(r['發布日期'])!==d){return;}
    var stamp=r['原文更新時間'];
    stamp=stamp instanceof Date ? stamp.toISOString() : String(stamp||'');
    var source=String(r['逐字稿來源']||'').trim();
    var manual=source==='手動'||source==='手動保留'||(!source&&!!String(r['原始逐字稿內容']||'').trim());
    var score=[manual?1:0,same?1:0,stamp,String(r['原始逐字稿內容']||'').length,i];
    for(var j=0;j<score.length;j++){
      if(!rank || score[j]>rank[j]){best={row:r,index:i+2};rank=score;break;}
      if(score[j]<rank[j]){break;}
    }
  });
  return best;
}

function hasTranscriptRecordsOnDate_(d) {
  if(readSheetObjects_('影片清單').some(function(r){
    return fmtDate_(r['發布日期'])===d && String(r['處理狀態'])==='完成';
  })){return true;}
  return ['操作紀錄','會員持股'].some(function(name){
    return readSheetObjects_(name).some(function(r){
      var source=String(r['來源影片ID']||'');
      return fmtDate_(r['日期'])===d && !/^(CMONEY-|MANUALENTRY-)/.test(source) && source!=='人工補登';
    });
  });
}

/**
 * 五分鐘既有觸發器接續等待中的工單；加鎖與冷卻，避免同時派出多輪。
 *   等待日K　 日K／績效相依資料還沒補齊。
 *   等待續跑　Apps Script 前端暫時沒有回應（例如 ping 或步驟請求收到 HTTP 404 網頁）。
 *             資料與刷新檢查點都已保存，續跑只會從停下的那一步接著做，不重跑 AI、不重寄信。
 * 2026/09/13：代號比對前的 ping 收到暫時性 404，工單被標成失敗，只能等人按續跑。
 */
function resumePendingTranscriptRefresh_() {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(1000)){return;}
  try {
    var j=currentJob_();
    if(!j || ['等待日K','等待續跑'].indexOf(j.status)<0){return;}
    var waiting=j.status, what=waiting==='等待續跑'?'下游暫時無回應':'日K相依資料';
    var p=PropertiesService.getScriptProperties(), key='txResume:'+j.id;
    var state=JSON.parse(p.getProperty(key)||'{"count":0,"at":0}');
    if(Date.now()-state.at<10*60*1000){return;}
    // 空行情或權限持續失效不能無限消耗 Actions 時數；資料與游標仍保留。
    if(state.count>=6){
      updateJob_({status:'待處理',note:waiting==='等待續跑'
        ?'Apps Script 連續 6 輪自動續跑都沒有回應；請用瀏覽器開部署網址加 ?action=ping 確認，檢查點保留，可手動續跑。'
        :'日K已自動續跑6輪仍未備齊；請檢查行情來源與日K快取。游標保留，可手動續跑。'});
      return;
    }
    state={count:state.count+1,at:Date.now()};p.setProperty(key,JSON.stringify(state));
    updateJob_({status:'處理中',note:what+'自動續跑第 '+state.count+' 輪，沿用刷新檢查點'});
    var result=dispatchGithub_({admin_job:'true',refresh_site:'true'});
    if(!result.ok){updateJob_({status:waiting,note:'續跑派工暫時失敗，稍後再試：'+result.reason});}
  } catch(e) {
    updateJob_({status:(typeof waiting==='string'&&waiting)||'等待日K',note:'續跑暫時失敗：'+String(e.message||e)});
  } finally {lock.releaseLock();}
}

/** 先乾跑，再以相同快照指紋套用；內容全部備份，不把舊稿不可逆地丟掉。 */
function apiAdminMergeTranscripts(key, apply, expected) {
  try {
    adminAuth_(key);
    var lock=LockService.getScriptLock();
    if(!lock.tryLock(1000)){return {ok:false,reason:'目前有其他寫入，稍後再試'};}
    try {
      var job=currentJob_();
      if(job && ['處理中','等待日K','等待續跑'].indexOf(job.status)>=0){throw new Error('工單尚未結束，請完成或取消後再整併');}
      var sh=getSheet_('影片清單'),values=sh.getDataRange().getValues(),head=values[0];
      var fingerprint=transcriptSha256_(JSON.stringify(values)),groups={},plan=[];
      values.slice(1).forEach(function(v,i){
        var r={};head.forEach(function(h,j){r[h]=v[j];});
        var d=fmtDate_(r['發布日期']);if(d){(groups[d]||(groups[d]=[])).push({r:r,v:v,index:i+2});}
      });
      Object.keys(groups).sort().forEach(function(d){
        var group=groups[d];if(group.length<2){return;}
        var chosen=selectTranscriptRow_(group.map(function(x){return x.r;}),'',d);
        var source=group[chosen.index-2],target=group[0],merged=source.v.slice();
        var ids=[];group.forEach(function(x){
          ids=ids.concat(String(x.r['來源別名']||'').split(','),[String(x.r['影片ID']||'')]);
        });ids=ids.filter(function(v,i,a){return v && a.indexOf(v)===i;});
        var real=ids.filter(function(v){return /^[A-Za-z0-9_-]{11}$/.test(v);})[0];
        merged[head.indexOf('影片ID')]=real||source.r['影片ID'];
        merged[head.indexOf('來源別名')]=ids.join(',');
        // 只有同一份原文的狀態才可提升，不能把新稿借用舊稿的「完成」。
        var stateRank={'完成':5,'待複核':4,'處理中':3,'失敗':2,'等待中':1};
        var same=group.filter(function(x){return String(x.r['原始逐字稿內容']||'')===String(source.r['原始逐字稿內容']||'');});
        same.sort(function(a,b){return (stateRank[b.r['處理狀態']]||0)-(stateRank[a.r['處理狀態']]||0);});
        merged[head.indexOf('處理狀態')]=same[0].r['處理狀態'];
        plan.push({date:d,keep:target.index,source:source.index,remove:group.slice(1).map(function(x){return x.index;}),
          chars:String(source.r['原始逐字稿內容']||'').length,
          rule:'人工優先，再依影片ID／更新時間／原文長度；請核對',values:merged});
      });
      // 大量歷史重複列分次整併，每次最多20日／40列，避免編輯器6分鐘硬中止。
      var totalGroups=plan.length, remainingDeletes=40, bounded=[];
      plan.slice(0,20).forEach(function(x){
        if(remainingDeletes<=0){return;}
        x.remove=x.remove.slice(0,remainingDeletes);remainingDeletes-=x.remove.length;bounded.push(x);
      });
      plan=bounded;
      if(apply){
        if(!expected || expected!==fingerprint){throw new Error('影片清單已變動，請重新乾跑');}
        if(plan.length){
          var backup=sh.copyTo(getSS_());
          backup.setName('影片整併備份-'+Utilities.formatDate(new Date(),'Asia/Taipei','yyyyMMddHHmmss'));
          plan.forEach(function(x){sh.getRange(x.keep,1,1,head.length).setValues([x.values]);});
          var remove=[];plan.forEach(function(x){remove=remove.concat(x.remove);});
          remove.sort(function(a,b){return b-a;}).forEach(function(i){sh.deleteRow(i);});
          CACHE.remove('txDates');
        }
      }
      return {ok:true,applied:!!apply,totalGroups:totalGroups,fingerprint:fingerprint,plan:plan.map(function(x){
        return {date:x.date,keep:x.keep,source:x.source,remove:x.remove,chars:x.chars,rule:x.rule};
      })};
    } finally {lock.releaseLock();}
  } catch(e){return {ok:false,reason:String(e.message||e)};}
}
