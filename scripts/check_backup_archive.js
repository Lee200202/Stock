// Real archive functions with a fake Sheets service: failure must never remove the source.
const fs=require('fs'),vm=require('vm'),crypto=require('crypto'),assert=require('assert');
let next=1,corrupt=false,props={},books={},main;
class Range{
 constructor(s,r=1,c=1,n=s.values.length,m=s.values[0].length){Object.assign(this,{s,r,c,n,m});}
 grid(k,empty=''){const a=this.s[k]||[];return Array.from({length:this.n},(_,i)=>Array.from({length:this.m},(_,j)=>a[this.r+i-1]?.[this.c+j-1]??empty));}
 getValues(){return this.grid('values');}getFormulas(){return this.grid('formulas');}getNotes(){return this.grid('notes');}getNumberFormats(){return this.grid('formats','General');}getMergedRanges(){return[];}
 setValues(v){for(let i=0;i<v.length;i++){this.s.values[this.r+i-1]??=[];for(let j=0;j<v[i].length;j++)this.s.values[this.r+i-1][this.c+j-1]=v[i][j];}return this;}
 setValue(v){return this.setValues([[v]]);}setFontWeight(){return this;}setBackground(){return this;}
}
class Sheet{
 constructor(book,name,v=[['h'],['x']]){Object.assign(this,{book,name,values:v,id:next++,fr:0,fc:0,hidden:false});book.sheets.push(this);}
 getName(){return this.name;}setName(n){if(this.book.getSheetByName(n))throw Error('duplicate');this.name=n;return this;}getSheetId(){return this.id;}getParent(){return this.book;}getDataRange(){return new Range(this);}getRange(...a){return new Range(this,...a);}getMaxRows(){return 100;}getMaxColumns(){return 15;}getFrozenRows(){return this.fr;}getFrozenColumns(){return this.fc;}setFrozenRows(n){this.fr=n;}isSheetHidden(){return this.hidden;}hideSheet(){this.hidden=true;}
 setColumnWidths(){}getLastRow(){return this.values.length;}appendRow(v){this.values.push(v);}
 copyTo(book){let x=new Sheet(book,'copy'+next,structuredClone(this.values));for(const k of ['formulas','notes','formats','fr','fc','hidden'])x[k]=structuredClone(this[k]);if(corrupt)x.values[1][0]='BROKEN';return x;}
}
class Book{
 constructor(id){this.id=id;this.sheets=[];books[id]=this;new Sheet(this,'Sheet1');}
 getId(){return this.id;}getUrl(){return 'https://docs.google.com/spreadsheets/d/'+this.id+'/edit';}getSheets(){return [...this.sheets];}getSheetByName(n){return this.sheets.find(s=>s.name===n);}getSheetById(n){return this.sheets.find(s=>s.id===n);}deleteSheet(s){this.sheets=this.sheets.filter(x=>x!==s);}getNamedRanges(){return [];}setSpreadsheetTimeZone(){}
}
const property={getProperty:k=>props[k],setProperties:v=>Object.assign(props,v)};
const ctx={console,Logger:{log(){}},PropertiesService:{getScriptProperties:()=>property},LockService:{getScriptLock:()=>({waitLock(){},tryLock(){return true;},releaseLock(){}})},SpreadsheetApp:{create:()=>new Book('archive'),openById:id=>books[id],flush(){}},getSS_:()=>main,nowStamp_:()=> '2026/10/14 15:00:00',Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},computeDigest:(_,b)=>Array.from(crypto.createHash('sha256').update(b).digest()),formatDate:()=> '1014_150000',getUuid:()=>crypto.randomUUID()}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('public-site/gas-source/Backuparchive.gs','utf8'),ctx);
function reset(){props={ADMIN_KEY:'test'};books={};main=new Book('main');main.getSheets()[0].setName('操作紀錄');ctx.setupBackupArchive();}
reset();let a=new Sheet(main,'備份_1011_105920_每日推播內容',[['日期','內容'],['2026/10/11','原文']]);a.notes=[['來源',''],['註記','']];a.formats=[['General','General'],['yyyy/mm/dd','General']];a.fr=1;
let result=ctx.backupArchiveRequest_('test','migrate','[]');assert(result.done);assert(!main.getSheetByName(a.name));assert.equal(books.archive.getSheetByName(a.name).values[1][1],'原文');assert.equal(result.verified.length,1);
assert(ctx.backupArchiveRequest_('test','migrate','[]').done);assert.equal(books.archive.getSheets().length,2);
reset();a=new Sheet(main,'備份_error');corrupt=true;result=ctx.migrateBackupArchive_();assert.equal(result.remaining,1);assert.equal(result.errors.length,1);assert(main.getSheetByName(a.name));corrupt=false;
reset();a=new Sheet(main,'備份_referenced');main.getSheets()[0].formulas=[["='備份_referenced'!A1"],['']];result=ctx.migrateBackupArchive_();assert.equal(result.remaining,1);assert(main.getSheetByName(a.name));
reset();result=ctx.backupArchiveRequest_('test','snapshot','["操作紀錄"]');assert.equal(result.verified.length,1);let count=books.archive.getSheets().length;ctx.backupArchiveRequest_('test','snapshot','["操作紀錄"]');assert.equal(books.archive.getSheets().length,count);assert(main.getSheetByName('操作紀錄'));
assert.throws(()=>ctx.backupArchiveRequest_('wrong','migrate','[]'),/未授權/);assert.throws(()=>ctx.backupArchiveRequest_('test','snapshot','["寄送帳本"]'),/範圍/);props.BACKUP_SPREADSHEET_ID='main';assert.throws(()=>ctx.backupArchiveBook_(),/設定不符/);
assert(!ctx.isArchiveBackupName_('每日推播內容'));assert(ctx.isArchiveBackupName_('持股備份_1014_v178'));assert(ctx.isArchiveBackupName_('影片整併備份-20261014'));
console.log('backup copy/verify/migrate, failed copy preserved, references preserved, idempotence, main destination rejection and auth passed');
