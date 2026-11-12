/** 私人備份封存：驗證複本後，才能移除原備份分頁。 */
var BACKUP_ARCHIVE_HEADER_=['來源試算表','來源分頁ID','原分頁名稱','來源SHA256','備份分頁ID','備份名稱','核對時間','資料列數','資料欄數','公式格數','類型','狀態'];
function isArchiveBackupName_(name){return /^(?:備份_|持股備份_|影片整併備份-)/.test(String(name||''));}
function backupArchiveBook_(){
  var p=PropertiesService.getScriptProperties(),id=p.getProperty('BACKUP_SPREADSHEET_ID');
  if(!id){throw new Error('尚未建立獨立備份試算表，請由擁有者執行 setupBackupArchive；本次不寫入');}
  var source=getSS_();
  if(id===source.getId()||p.getProperty('BACKUP_SOURCE_SPREADSHEET_ID')!==source.getId()){throw new Error('備份目的地與來源設定不符');}
  return SpreadsheetApp.openById(id);
}
/** 僅管理者執行的一次性設定，不新增共用、郵件、觸發器或公開權限。 */
function setupBackupArchive(){
  var lock=LockService.getScriptLock();lock.waitLock(10000);
  try{
    var p=PropertiesService.getScriptProperties();
    if(!p.getProperty('BACKUP_SPREADSHEET_ID')){
      var source=getSS_(),book=SpreadsheetApp.create('盤勢有據－資料備份');
      book.setSpreadsheetTimeZone('Asia/Taipei');
      var index=book.getSheets()[0];index.setName('備份目錄');index.getRange(1,1,1,BACKUP_ARCHIVE_HEADER_.length).setValues([BACKUP_ARCHIVE_HEADER_]);
      index.setFrozenRows(1);index.getRange(1,1,1,BACKUP_ARCHIVE_HEADER_.length).setFontWeight('bold').setBackground('#dceee5');index.setColumnWidths(1,BACKUP_ARCHIVE_HEADER_.length,160);
      p.setProperties({BACKUP_SPREADSHEET_ID:book.getId(),BACKUP_SOURCE_SPREADSHEET_ID:source.getId()});
    }
    var archive=backupArchiveBook_();Logger.log('獨立備份試算表：'+archive.getUrl());return {ok:true,url:archive.getUrl()};
  }finally{lock.releaseLock();}
}
function archiveSheetState_(sheet){
  var range=sheet.getDataRange(),v=range.getValues(),f=range.getFormulas(),n=range.getNotes(),formats=range.getNumberFormats(),count=0;
  var inputs=v.map(function(row,i){return row.map(function(x,j){if(f[i][j]){count++;return {formula:f[i][j]};}return x instanceof Date?{date:x.getTime()}:x;});});
  var body=JSON.stringify({inputs:inputs,notes:n,formats:formats,rows:sheet.getMaxRows(),cols:sheet.getMaxColumns(),frozenRows:sheet.getFrozenRows(),frozenCols:sheet.getFrozenColumns(),merges:range.getMergedRanges().map(function(r){return r.getA1Notation();})});
  var hash=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,body,Utilities.Charset.UTF_8).map(function(b){return ('0'+(b&255).toString(16)).slice(-2);}).join('');
  return {hash:hash,rows:v.length,cols:v[0].length,formulas:count,values:JSON.stringify(v)};
}
function archiveCopyVerified_(sheet,name,kind){
  var source=sheet.getParent(),archive=backupArchiveBook_(),index=archive.getSheetByName('備份目錄');
  if(!index){throw new Error('缺少備份目錄，未更動來源');}
  var before=archiveSheetState_(sheet),key=[source.getId(),String(sheet.getSheetId()),before.hash,kind].join('|');
  var rows=index.getDataRange().getValues(),found=null,indexRow=0;
  for(var i=1;i<rows.length;i++){
    if([String(rows[i][0]),String(rows[i][1]),String(rows[i][3]),String(rows[i][10])].join('|')===key){found=rows[i];indexRow=i+1;break;}
  }
  var copy=found?archive.getSheetById(Number(found[4])):null;
  if(!copy){
    var title=name.slice(0,100);if(archive.getSheetByName(title)){title=name.slice(0,90)+'_'+before.hash.slice(0,8);}
    var orphan=archive.getSheetByName(title);
    if(orphan&&archiveSheetState_(orphan).hash===before.hash){copy=orphan;}
    else{if(orphan){title=name.slice(0,80)+'_'+Utilities.getUuid().slice(0,8);}copy=sheet.copyTo(archive);copy.setName(title);}
    if(sheet.isSheetHidden()){copy.hideSheet();}
    var entry=[source.getId(),String(sheet.getSheetId()),sheet.getName(),before.hash,String(copy.getSheetId()),copy.getName(),nowStamp_(),before.rows,before.cols,before.formulas,kind,'已複製，待核對'];
    index.appendRow(entry);indexRow=index.getLastRow();
  }
  SpreadsheetApp.flush();
  var after=archiveSheetState_(copy),current=archiveSheetState_(sheet);
  // 同時比對值，可發現公式複製到另一個活頁簿後失效的情況。
  if(before.hash!==after.hash||before.values!==after.values||current.hash!==before.hash||current.values!==before.values){throw new Error('備份核對不符：'+sheet.getName()+'；原分頁保留');}
  index.getRange(indexRow,12).setValue('核對完成');
  return {name:sheet.getName(),sheetId:sheet.getSheetId(),archiveSheetId:copy.getSheetId(),hash:before.hash,rows:before.rows,cols:before.cols,formulas:before.formulas,indexRow:indexRow};
}
function archiveLiveSheet_(sheet,name){
  var lock=LockService.getScriptLock();if(!lock.tryLock(10000)){throw new Error('備份鎖忙碌，稍後重試');}
  try{return archiveCopyVerified_(sheet,name,'更新前快照');}finally{lock.releaseLock();}
}
function backupArchiveStatus_(){
  var source=getSS_(),id=PropertiesService.getScriptProperties().getProperty('BACKUP_SPREADSHEET_ID'),pending=source.getSheets().filter(function(s){return isArchiveBackupName_(s.getName());});
  var out={ok:true,configured:!!id,sourceId:source.getId(),pending:pending.map(function(s){return {id:s.getSheetId(),name:s.getName()};}),remaining:pending.length};
  if(id){
    var archive=backupArchiveBook_(),sheets=archive.getSheets();out.archived=Math.max(0,sheets.length-1);
    // 2026/11/12 管理者「保留 7 天、自動清理」：回報大小（試算表上限 1,000 萬格）與可清理的份數，只讀。
    out.cells=sheets.reduce(function(n,s){return n+s.getMaxRows()*s.getMaxColumns();},0);
    out.cellLimit=10000000;
    var plan=backupArchivePrunePlan_(archive,7);out.prunable=plan.drop.length;out.prunableCells=plan.cells;out.oldest=plan.oldest;
  }
  return out;
}
/* 2026/11/12 管理者：雲端每天的備份越積越多，「保留 7 天、自動清理」。
   純規則：核對時間早於 days 天的備份分頁才清；每個來源分頁（來源試算表＋原分頁名稱去掉時間戳）永遠留最新一份，
   確保隨時有一個還原點；目錄列不刪，狀態改成「已清理 日期」留紀錄。 */
function backupArchiveAge_(value){
  if(value&&typeof value.getTime==='function'){return value.getTime();}
  var m=String(value||'').match(/(\d{4})\/(\d{1,2})\/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?/);
  return m?new Date(+m[1],+m[2]-1,+m[3],+(m[4]||0),+(m[5]||0)).getTime():0;
}
function backupArchiveTabKey_(row){
  return String(row[0])+'|'+String(row[2]).replace(/^(?:備份_|持股備份_|影片整併備份-)\d{4}_\d{6}_/,'').replace(/^(?:備份_|持股備份_|影片整併備份-)/,'');
}
function backupArchivePrunePlan_(archive,days){
  var index=archive.getSheetByName('備份目錄');if(!index){throw new Error('缺少備份目錄');}
  var rows=index.getDataRange().getValues(),cutoff=Date.now()-days*86400000,latest={},drop=[],cells=0,oldest=0;
  for(var i=1;i<rows.length;i++){
    if(/^已清理/.test(String(rows[i][11]))){continue;}
    var at=backupArchiveAge_(rows[i][6]),key=backupArchiveTabKey_(rows[i]);
    if(at&&(!oldest||at<oldest)){oldest=at;}
    if(!latest[key]||at>latest[key].at){latest[key]={at:at,row:i};}
  }
  for(var j=1;j<rows.length;j++){
    if(/^已清理/.test(String(rows[j][11]))){continue;}
    var t=backupArchiveAge_(rows[j][6]),k=backupArchiveTabKey_(rows[j]);
    if(!t||t>=cutoff||latest[k].row===j){continue;}
    var sheet=archive.getSheetById(Number(rows[j][4]));
    if(sheet&&sheet.getName()!=='備份目錄'){cells+=sheet.getMaxRows()*sheet.getMaxColumns();}
    drop.push({row:j+1,sheetId:Number(rows[j][4]),name:String(rows[j][5]),at:Utilities.formatDate(new Date(t),'Asia/Taipei','yyyy/MM/dd HH:mm'),exists:!!sheet});
  }
  return {drop:drop,cells:cells,oldest:oldest?Utilities.formatDate(new Date(oldest),'Asia/Taipei','yyyy/MM/dd'):''};
}
function backupArchivePrune_(days,dry){
  days=Math.max(7,Number(days)||7);
  var lock=LockService.getScriptLock();if(!lock.tryLock(10000)){throw new Error('備份鎖忙碌，稍後重試');}
  try{
    var archive=backupArchiveBook_(),index=archive.getSheetByName('備份目錄'),plan=backupArchivePrunePlan_(archive,days),done=[],started=Date.now();
    if(!dry){
      var stamp=Utilities.formatDate(new Date(),'Asia/Taipei','yyyy/MM/dd');
      for(var i=0;i<plan.drop.length&&Date.now()-started<200000;i++){
        var d=plan.drop[i],sheet=d.exists?archive.getSheetById(d.sheetId):null;
        if(sheet&&sheet.getName()!=='備份目錄'){archive.deleteSheet(sheet);}
        index.getRange(d.row,12).setValue('已清理 '+stamp+'（保留 '+days+' 天）');done.push(d.name);
      }
    }
    var status=backupArchiveStatus_();status.days=days;status.dry=!!dry;status.plan=plan.drop.slice(0,60);status.pruned=done.length;
    status.left=plan.drop.length-done.length;return status;
  }finally{lock.releaseLock();}
}
function migrateBackupArchive_(){
  var lock=LockService.getScriptLock();if(!lock.tryLock(10000)){throw new Error('備份鎖忙碌，稍後重試');}
  try{
    var source=getSS_(),archive=backupArchiveBook_(),started=Date.now(),verified=[],errors=[];
    var pending=source.getSheets().filter(function(s){return isArchiveBackupName_(s.getName());});
    // 使用中的工作表仍引用某備份時，不得移除該備份。
    var formulas=source.getSheets().filter(function(s){return !isArchiveBackupName_(s.getName());}).map(function(s){return JSON.stringify(s.getDataRange().getFormulas());}).join('\n');
    var named=source.getNamedRanges().map(function(r){return r.getRange().getSheet().getSheetId();});
    for(var i=0;i<pending.length&&Date.now()-started<120000&&verified.length<12;i++){
      var sheet=pending[i];
      try{
        if(formulas.indexOf(sheet.getName())>=0||named.indexOf(sheet.getSheetId())>=0){throw new Error('正式公式／命名範圍仍引用此备份，先保留');}
        var result=archiveCopyVerified_(sheet,sheet.getName(),'移出主檔');
        source.deleteSheet(sheet);
        archive.getSheetByName('備份目錄').getRange(result.indexRow,12).setValue('核對完成，已移出主檔');verified.push(result);
      }catch(e){errors.push({name:sheet.getName(),error:String(e.message||e).slice(0,180)});}
    }
    var status=backupArchiveStatus_();status.verified=verified;status.errors=errors;status.done=status.remaining===0;return status;
  }finally{lock.releaseLock();}
}
function backupArchiveRequest_(key,op,namesJson,expectedSource){
  var expected=PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if(!expected||String(key)!==expected){throw new Error('未授權');}
  if(expectedSource&&expectedSource!==getSS_().getId()){throw new Error('備份來源試算表與請求不符');}
  if(op==='status'){return backupArchiveStatus_();}
  if(op==='migrate'){return migrateBackupArchive_();}
  // 2026/11/12：prune-preview 只列會清哪些；prune 才真的刪（保留 7 天，每個分頁至少留最新一份）。
  if(op==='prune-preview'){return backupArchivePrune_(7,true);}
  if(op==='prune'){return backupArchivePrune_(7,false);}
  if(op!=='snapshot'){throw new Error('不支援的備份操作');}
  var names=JSON.parse(namesJson),source=getSS_();
  var allowed=['操作紀錄','會員持股','每日推播內容','逐字稿判讀稽核','持股追蹤','影片清單','會員簡訊'];
  if(!Array.isArray(names)||!names.length||names.length>5||names.some(function(n){return allowed.indexOf(n)<0;})||names.some(function(n,i){return names.indexOf(n)!==i;})){throw new Error('備份分頁不在允許範圍');}
  var stamp=Utilities.formatDate(new Date(),'Asia/Taipei','MMdd_HHmmss'),out=[];
  names.forEach(function(n){var sheet=source.getSheetByName(n);if(!sheet){throw new Error('來源分頁不存在：'+n);}out.push(archiveLiveSheet_(sheet,'備份_'+stamp+'_'+n));});
  return {ok:true,sourceId:source.getId(),verified:out};
}
