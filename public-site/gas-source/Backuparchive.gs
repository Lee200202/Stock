/** Private backup archive. Copies are verified before any original backup tab is removed. */
var BACKUP_ARCHIVE_HEADER_=['來源試算表','來源分頁ID','原分頁名稱','來源SHA256','備份分頁ID','備份名稱','核對時間','資料列數','資料欄數','公式格數','類型','狀態'];
function isArchiveBackupName_(name){return /^(?:備份_|持股備份_|影片整併備份-)/.test(String(name||''));}
function backupArchiveBook_(){
  var p=PropertiesService.getScriptProperties(),id=p.getProperty('BACKUP_SPREADSHEET_ID');
  if(!id){throw new Error('尚未建立獨立備份試算表，請由擁有者執行 setupBackupArchive；本次不寫入');}
  var source=getSS_();
  if(id===source.getId()||p.getProperty('BACKUP_SOURCE_SPREADSHEET_ID')!==source.getId()){throw new Error('備份目的地與來源設定不符');}
  return SpreadsheetApp.openById(id);
}
/** Owner-only, one-time setup. No sharing, mail, triggers, or public permissions are added. */
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
  // Values additionally detect formulas that stopped working when copied to another workbook.
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
  if(id){var archive=backupArchiveBook_();out.url=archive.getUrl();out.archived=Math.max(0,archive.getSheets().length-1);}
  return out;
}
function migrateBackupArchive_(){
  var lock=LockService.getScriptLock();if(!lock.tryLock(10000)){throw new Error('備份鎖忙碌，稍後重試');}
  try{
    var source=getSS_(),archive=backupArchiveBook_(),started=Date.now(),verified=[],errors=[];
    var pending=source.getSheets().filter(function(s){return isArchiveBackupName_(s.getName());});
    // Do not remove a backup that an active sheet still references.
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
  if(op!=='snapshot'){throw new Error('不支援的備份操作');}
  var names=JSON.parse(namesJson),source=getSS_();
  var allowed=['操作紀錄','會員持股','每日推播內容','逐字稿判讀稽核','持股追蹤','影片清單'];
  if(!Array.isArray(names)||!names.length||names.length>5||names.some(function(n){return allowed.indexOf(n)<0;})||names.some(function(n,i){return names.indexOf(n)!==i;})){throw new Error('備份分頁不在允許範圍');}
  var stamp=Utilities.formatDate(new Date(),'Asia/Taipei','MMdd_HHmmss'),out=[];
  names.forEach(function(n){var sheet=source.getSheetByName(n);if(!sheet){throw new Error('來源分頁不存在：'+n);}out.push(archiveLiveSheet_(sheet,'備份_'+stamp+'_'+n));});
  return {ok:true,sourceId:source.getId(),verified:out};
}
