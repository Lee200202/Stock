// Offline contracts: real backend functions with an in-memory workbook; no network/models.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict'),crypto=require('crypto');
const path=require('path'),root=path.join(__dirname,'..'),src=path.join(root,'public-site/gas-source');
let n=0;function check(label,fn){fn();n++;console.log('OK '+label);}
class Sheet{
 constructor(rows,id=1){this.rows=rows;this.id=id;}
 getLastRow(){return this.rows.length;}getLastColumn(){return this.rows[0]?.length||0;}getSheetId(){return this.id;}setFrozenRows(){}
 getRange(row,col,height=1,width=1){let sh=this;return {
 getValues(){return Array.from({length:height},(_,i)=>Array.from({length:width},(_,j)=>sh.rows[row-1+i]?.[col-1+j]??''));},
 getDisplayValues(){return this.getValues().map(r=>r.map(String));},
 setValues(values){values.forEach((r,i)=>{sh.rows[row-1+i]??=[];r.forEach((v,j)=>sh.rows[row-1+i][col-1+j]=v);});}
 };}
}
const detailHead=['日期','代號','名稱','網站分類','複核分類','嚴重度','原文依據','判斷理由','處理','修正前','修正後','執行'];
const sheets={
 '差異明細':new Sheet([detailHead,...Array.from({length:81},(_,i)=>['2026/11/04',String(1000+i),'股票'+i,'觀望注意','觀望不碰',i%2?'邊界':'明確錯誤','原句'+i,'完整原因'+i,'待核','舊文','新文','run-1'])],12),
 '每日摘要':new Sheet([['日期','狀態'],['2026/11/04','完成']]),
 '通知':new Sheet([['日期','內容','主旨'],['2026/11/04','完整日報'.repeat(500),'日報']]),
 '提示詞候選':new Sheet([['日期','建議修改'],['2026/11/04','通用修改']])
};
const book={getSheetByName:n=>sheets[n],getUrl:()=> 'https://docs.google.com/spreadsheets/d/example/edit',insertSheet:n=>(sheets[n]=new Sheet([]))};
let locks=0,writes=0;
const c={Date,JSON,Object,Array,String,Number,Math,isFinite,Error,
 transcriptSha256_:s=>crypto.createHash('sha256').update(s).digest('hex'),
 adminAuth_:key=>{if(key!=='valid')throw Error('unauthorized');},dailyReviewBook_:()=>book,
 Utilities:{getUuid:()=>crypto.randomUUID()},nowStamp_:()=> '2026/11/04 12:00:00',todayStr_:()=> '2026/11/04',
 LockService:{getScriptLock:()=>({tryLock:()=>{assert.equal(locks,0);locks++;return true;},releaseLock:()=>locks--})},
 apiAdminDayRows:()=>({ok:true,rows:[{sheet:'操作紀錄',row:2,name:'股票0',code:'1000',cls:'觀望注意',reason:'網站原文',price:''}]}),
 getSheet_:()=>new Sheet([['日期','名稱'],['2026/11/04','股票0']]),
 apiAdminUpdateRow:(key,payload)=>{writes++;return {ok:true,message:'已儲存',sync:{queued:true,date:payload.date}};}
};vm.createContext(c);vm.runInContext(fs.readFileSync(path.join(src,'Reviewhistory.gs'),'utf8'),c);
const read=o=>c.apiAdminReviewHistory('valid',o),range={start:'2026/11/04',end:'2026/11/04'};
check('auth is required',()=>assert.equal(c.apiAdminReviewHistory('bad',range).ok,false));
check('invalid calendar dates rejected',()=>assert.equal(c.reviewHistoryDate_('2026/02/30'),''));
check('raw Date contributes to write hash',()=>assert.notEqual(c.reviewHistoryHash_([new Date('2026-01-01')]),c.reviewHistoryHash_([new Date('2026-01-02')])));
let first=read(range),entry=first.entries[0],second=read({...range,offset:80,token:first.token});
check('81 records paginated without loss',()=>{assert.equal(first.total,81);assert.equal(first.entries.length,80);assert.equal(second.entries.length,1);assert.equal(second.nextOffset,null);});
check('full reports are not mail-truncated',()=>assert.equal(first.reports[0].text.length,2000));
check('reports only supplied once across pages',()=>assert.equal(second.reports.length,0));
check('range drift is explicit',()=>{sheets['差異明細'].rows[2][7]='更新原因';assert.equal(read({...range,offset:80,token:first.token}).stale,true);});
let original=JSON.stringify(sheets['差異明細'].rows),op='operation_001';
let note={scope:'note',id:entry.id,sourceHash:entry.sourceHash,revision:'0',operationId:op,edits:{'管理者註記':'已核對'}};
let saved=c.apiAdminReviewHistorySave('valid',note);
check('append revision preserves original sheet',()=>{assert.equal(saved.ok,true);assert.equal(JSON.stringify(sheets['差異明細'].rows),original);});
check('repeat operation is idempotent',()=>{assert.equal(c.apiAdminReviewHistorySave('valid',note).ok,true);assert.equal(sheets['管理者修訂'].getLastRow(),2);});
check('concurrent revision rejected',()=>assert.equal(c.apiAdminReviewHistorySave('valid',{...note,operationId:'operation_002'}).ok,false));
check('edited values overlay only matching source hash',()=>assert.equal(read(range).entries[0].current['管理者註記'],'已核對'));
check('unknown fields rejected',()=>assert.equal(c.apiAdminReviewHistorySave('valid',{...note,operationId:'operation_003',revision:saved.revision,edits:{'日期':'changed'}}).ok,false));
let site=read({action:'site',id:entry.id,sourceHash:entry.sourceHash}).rows[0];
let web={scope:'website',id:entry.id,sourceHash:entry.sourceHash,operationId:'operation_004',writeHash:site.writeHash,payload:{sheet:site.sheet,row:site.row,newName:site.name,newCode:site.code,cls:site.cls,reason:site.reason,price:''}};
check('same website data is a non-writing operation',()=>{assert.equal(c.apiAdminReviewHistorySave('valid',web).noChange,true);assert.equal(writes,0);});
check('stale website is rejected',()=>assert.equal(c.apiAdminReviewHistorySave('valid',{...web,writeHash:'old'}).ok,false));
check('website edit calls existing updater and sync',()=>{let r=c.apiAdminReviewHistorySave('valid',{...web,payload:{...web.payload,reason:'已核對修改'}});assert.equal(r.ok,true);assert.equal(r.sync.date,'2026/11/04');assert.equal(writes,1);});
check('operation retry never duplicates website write',()=>{c.apiAdminReviewHistorySave('valid',web);assert.equal(writes,1);});
check('source change invalidates old edits rather than overwriting',()=>{sheets['差異明細'].rows[1][8]='雲端已修正';assert.equal(read(range).entries[0].current['管理者註記'],undefined);assert.equal(c.apiAdminReviewHistorySave('valid',{...note,operationId:'operation_005'}).ok,false);});
check('read failure is not zero records',()=>{c.dailyReviewBook_=()=>null;assert.equal(read(range).ok,false);c.dailyReviewBook_=()=>book;});
const html=fs.readFileSync(path.join(src,'ReviewHistoryPanel.html'),'utf8');
let js=html.match(/<script>([\s\S]*?)<\/script>/)[1];new vm.Script(js);
// Expose pure helpers only in this in-memory copy; no browser state or production hooks.
js=js.replace("if(document.readyState==='loading')",'this.probe={esc,iso,fence,markdown,state};if(document.readyState===\'loading\')');
const ui={document:{readyState:'loading',addEventListener(){}},window:{},console};vm.createContext(ui);vm.runInContext(js.replace("'use strict';",''),ui);
check('MD uses real newlines and safe code fences',()=>{assert.equal(ui.probe.iso('2026/11/04'),'2026-11-04');assert.equal(ui.probe.fence('a\n```\nb'),'````text\na\n```\nb\n````');});
check('untrusted content is HTML escaped',()=>assert.equal(ui.probe.esc('<img onerror="x">'),'&lt;img onerror=&quot;x&quot;&gt;'));
check('MD retains original, revisions and full report',()=>{Object.assign(ui.probe.state,{start:range.start,end:range.end,entries:[entry],reports:first.reports,summaries:[],candidates:[]});let md=ui.probe.markdown();assert(md.includes('原始複核紀錄'));assert(md.includes(first.reports[0].text));assert(md.includes(entry.sourceHash));});
check('website write checks raw hash inside existing lock',()=>{let s=fs.readFileSync(path.join(src,'Adminservice.gs'),'utf8');assert(s.includes('payload.expectedRowHash'));});
console.log('Review history: '+n+' contracts passed');
