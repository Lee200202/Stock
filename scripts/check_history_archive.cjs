const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const properties={},books={};let created=0;
function book(){const id='b'+(++created), values=[[]];const sheet={
 setName(){return this},setFrozenRows(){},appendRow(r){values.push(r)},getLastRow(){return values.length},
 getMaxRows(){return 10},getMaxColumns(){return 8},
 getDataRange(){return {getValues:()=>structuredClone(values)}},
 getRange(r,c,n,m){return {setValues(v){v.forEach((row,i)=>{values[r+i-1]||=[];row.forEach((x,j)=>values[r+i-1][c+j-1]=x)})}}}
};return books[id]={getId:()=>id,getSheets:()=>[sheet],getSheetByName:()=>sheet,setSpreadsheetTimeZone(){},addEditor(){}};}
const ctx={PropertiesService:{getScriptProperties:()=>({getProperty:k=>properties[k],setProperties:o=>Object.assign(properties,o)})},
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},
 SpreadsheetApp:{create:book,openById:id=>books[id]},
 DriveApp:{createFolder:()=>({getId:()=> 'folder'}),getFolderById:()=>({}),getFileById:()=>({moveTo(){}})},
 getSS_:()=>({getEditors:()=>[{getEmail:()=> 'svc@project.iam.gserviceaccount.com'}]}),nowStamp_:()=> '2026/11/25 18:00'};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('public-site/gas-source/Backuparchive.gs','utf8'),ctx);
assert.equal(ctx.historyArchiveRequest_('history-status',{}).configured,false);
const opts={dataset:'valuation-2025-01',cells:100,bytes:1000,sha:'a'.repeat(64),serviceEmail:'svc@project.iam.gserviceaccount.com'};
const a=ctx.historyArchiveRequest_('history-reserve',opts);
assert.equal(created,2);assert.equal(a.unchanged,false);
assert.equal(ctx.historyArchiveRequest_('history-reserve',opts).bookId,a.bookId);assert.equal(created,2);
ctx.historyArchiveRequest_('history-commit',{...opts,bookId:a.bookId,row:a.row});
assert.equal(ctx.historyArchiveRequest_('history-reserve',opts).unchanged,true);
assert.equal(ctx.historyArchiveRequest_('history-route',{dataset:opts.dataset}).bookId,a.bookId);
const b=ctx.historyArchiveRequest_('history-reserve',{...opts,sha:'b'.repeat(64)});
assert.notEqual(a.bookId,b.bookId);assert(books[a.bookId]);
assert.equal(ctx.historyArchiveRequest_('history-route',{dataset:opts.dataset}).bookId,a.bookId);
const before=created;assert.throws(()=>ctx.historyArchiveRequest_('history-reserve',{...opts,bytes:21000000}));
assert.equal(created,before);assert.throws(()=>ctx.historyArchiveRequest_('history-reserve',{...opts,serviceEmail:'other@project.iam.gserviceaccount.com'}));
assert.equal(created,before);assert.throws(()=>ctx.historyArchiveRequest_('history-commit',{...opts,bookId:b.bookId,row:a.row}));
console.log('Historical volumes: size preflight, reuse, complete-only routes, old volume preservation and editor restriction pass');
