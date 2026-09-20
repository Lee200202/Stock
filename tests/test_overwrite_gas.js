const fs=require('fs'),vm=require('vm'),assert=require('assert'),crypto=require('crypto'),path=require('path');
const root=path.resolve(__dirname,'..'),props={},rows={};let writes=[],job=null,dispatches=0,backup=0;
const head=['影片ID','發布日期','標題','處理狀態','失敗原因','原始逐字稿內容','修飾後逐字稿內容','原文更新時間','原文SHA256','來源別名'];
let values=[head];
const sheet={getDataRange:()=>({getValues:()=>values.map(r=>r.slice())}),
 getRange:(r,c,n,m)=>({setValues:data=>{writes.push([r,c,data]);data.forEach((v,i)=>v.forEach((x,j)=>values[r-1+i][c-1+j]=x));}}),
 deleteRow:r=>values.splice(r-1,1),copyTo:()=>{backup++;return {setName:()=>{}};}};
const ctx=vm.createContext({console,Logger:{log:()=>{}},CACHE:{remove:()=>{}},
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v,deleteProperty:k=>delete props[k]})},
 Utilities:{DigestAlgorithm:{SHA_256:'sha256'},Charset:{UTF_8:'utf8'},
 computeDigest:(_,s)=>Array.from(crypto.createHash('sha256').update(s).digest()),formatDate:()=> '20260913090000'},
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
 adminAuth_:()=>{},fmtDate_:s=>s,getSheet_:()=>sheet,getSS_:()=>({}),
 readSheetObjects_:name=>rows[name]||[],currentJob_:()=>job,
 updateJob_:patch=>{Object.assign(job,patch);writes.push(patch)},
 dispatchGithub_:()=>{dispatches++;return {ok:true}}});
for(const name of ['Transcriptstore','Articlequality'])vm.runInContext(fs.readFileSync(path.join(root,'apps-script',name+'.gs'),'utf8'),ctx);
const d='2026/09/10',manual='MANUAL-20260910';
const sample=[{'影片ID':'abcdefghijk','發布日期':d,'原始逐字稿內容':'old'.repeat(100)},
 {'影片ID':manual,'發布日期':d,'原始逐字稿內容':'new','原文更新時間':'2026-09-13T00:00:00.000Z'}];
assert.equal(ctx.selectTranscriptRow_(sample,manual,d).row['原始逐字稿內容'],'new');
assert.equal(ctx.selectTranscriptRow_(sample,'',d).row['原始逐字稿內容'],'new');
assert.equal(ctx.transcriptSha256_('新原文'),crypto.createHash('sha256').update('新原文').digest('hex'));
rows['影片清單']=sample;
rows['操作紀錄']=[{'日期':d,'來源影片ID':'CMONEY-1'}];assert(!ctx.hasTranscriptRecordsOnDate_(d));
rows['操作紀錄'].push({'日期':d,'來源影片ID':'abcdefghijk'});assert(ctx.hasTranscriptRecordsOnDate_(d));
function record(video,batch,cat,item){return {'影片日期':d,'來源影片ID':video,'規則版本':'context-json-v4','判讀JSON':JSON.stringify({batch,category:cat,item})};}
rows['逐字稿判讀稽核']=[record('old','b1','market',{text:'old',_evidence_verified:true}),
 record('old','b1','manifest',{status:'published'}),record('new','b2','market',{text:'new',_evidence_verified:true}),
 record('new','b2','manifest',{status:'published'}),record('candidate','b3','manifest',{status:'needs_review'})];
assert.deepEqual(Array.from(ctx.attachArticleEvidence_({},d).market,x=>x.text),['new']);
values=[head,...sample.map(r=>head.map(h=>r[h]||''))];
let plan=ctx.apiAdminMergeTranscripts('key',false,'');assert(plan.ok);assert.equal(writes.length,0);assert.equal(backup,0);
assert.equal(plan.plan[0].source,3);assert.equal(plan.plan[0].chars,3);
let failed=ctx.apiAdminMergeTranscripts('key',true,'wrong');assert(!failed.ok);assert.equal(values.length,3);
let result=ctx.apiAdminMergeTranscripts('key',true,plan.fingerprint);assert(result.ok);assert.equal(backup,1);
assert.equal(values.length,2);assert.equal(values[1][0],'abcdefghijk');assert.equal(values[1][5],'new');
assert(values[1][9].includes(manual));assert.equal(ctx.apiAdminMergeTranscripts('key',false,'').plan.length,0);
values=[head,...Array.from({length:85},(_,i)=>head.map(h=>({'影片ID':'id'+i,'發布日期':d,'原始逐字稿內容':'same'}[h]||'')))];
plan=ctx.apiAdminMergeTranscripts('key',false,'');assert.equal(plan.plan[0].remove.length,40);
result=ctx.apiAdminMergeTranscripts('key',true,plan.fingerprint);assert(result.ok);assert.equal(values.length,46);
assert.equal(ctx.apiAdminMergeTranscripts('key',false,'').plan[0].remove.length,40);
job={id:'J1',status:'等待日K'};ctx.resumePendingTranscriptRefresh_();assert.equal(dispatches,1);assert.equal(job.status,'處理中');
job.status='等待日K';ctx.resumePendingTranscriptRefresh_();assert.equal(dispatches,1);
props['txResume:J1']=JSON.stringify({count:6,at:0});ctx.resumePendingTranscriptRefresh_();assert.equal(job.status,'待處理');assert.equal(dispatches,1);
job.status='已取消';ctx.resumePendingTranscriptRefresh_();assert.equal(dispatches,1);
job={id:'J2',status:'等待日K'};ctx.dispatchGithub_=()=>({ok:false,reason:'network'});ctx.resumePendingTranscriptRefresh_();assert.equal(job.status,'等待日K');
console.log('PASS: SHA256, source selection, overwrite gate, latest published batch, dry-run/backup/idempotent merge, retry/cooldown/cancellation/limit.');

// 真實 renderJob 函式驗證警示結局；不能被綠色成功覆蓋。
const html=fs.readFileSync(path.join(root,'apps-script/Admin.html'),'utf8'),nodes={};
const node=id=>nodes[id]||(nodes[id]={style:{},parentElement:{setAttribute:()=>{}}});let stopped=0,started=0;
const ui=vm.createContext({$:node,esc:s=>s,STEPS:['讀取原文','刷新網站','完成'],
 stepIndexOf:(s,n)=>s.indexOf(n),layoutSteps:()=>{},setProgressA11y_:()=>{},
 startPoll:()=>started++,stopPoll:()=>stopped++});
vm.runInContext(html.match(/^function renderJob\(j\)\{[\s\S]*?^}/m)[0],ui);
ui.renderJob({status:'待複核',step:'完成',done:15,total:15,note:'未覆蓋，保留舊資料'});
assert.equal(nodes.barFill.style.background,'#b7791f');assert(nodes.progNote.textContent.includes('保留舊資料'));assert.equal(stopped,1);
ui.renderJob({status:'等待日K',step:'刷新網站',done:13,total:15});assert.equal(started,1);assert.equal(nodes.cancelBtn.disabled,false);
assert(!nodes.progHint.textContent.includes('處理完成'));
console.log('PASS: review warning, automatic daily-K waiting, cancellation affordance and progress polling.');

// 實際投稿與存原文函式：新稿較短、舊真實影片在前也必須綁定本次指紋。
const admin=fs.readFileSync(path.join(root,'apps-script/Adminservice.gs'),'utf8');
for(const fn of ['submitTranscriptLocked_','saveRawTranscript_']){
 vm.runInContext(admin.match(new RegExp('^function '+fn+'\\([^\\n]*\\) \\{[\\s\\S]*?^}', 'm'))[0],ctx);
}
const jobHead=['工單ID','日期','影片ID','狀態','步驟','已完成','總數','備註','開始時間','更新時間','來源','原文SHA256'];
const jobValues=[jobHead],jobSheet={getDataRange:()=>({getValues:()=>jobValues}),appendRow:r=>jobValues.push(r)};
ctx.ADMIN_JOB_SHEET='後台工單';ctx.SHEET_SCHEMA={'影片清單':head,'後台工單':jobHead};
ctx.getSheet_=name=>name==='後台工單'?jobSheet:sheet;
ctx.setVideoCell_=(r,name,value)=>values[r-1][head.indexOf(name)]=value;
ctx.extractVideoId_=()=>'';ctx.todayStr_=()=>d;ctx.nowStamp_=()=> '2026/09/13 09:00:00';
ctx.dispatchGithub_=()=>({ok:true});job=null;
values=[head,...sample.map(r=>head.map(h=>r[h]||''))];
let submitted=ctx.submitTranscriptLocked_('key',{date:d,transcript:' 最新的原文。'.repeat(100),allowOverwrite:false});
assert(!submitted.ok);assert.equal(jobValues.length,1);
submitted=ctx.submitTranscriptLocked_('key',{date:d,transcript:' 最新的原文。'.repeat(100),allowOverwrite:true});
assert(submitted.ok);assert.equal(values.length,3);assert.equal(jobValues.length,2);
assert.equal(values[2][5],' 最新的原文。'.repeat(100).trim());assert.equal(values[2][6],'');
assert.equal(values[2][8],jobValues[1][11]);assert.equal(ctx.transcriptSha256_(values[2][5]),jobValues[1][11]);
console.log('PASS: actual submission gate, unchanged duplicate count, current raw write, cleared polish and matching job SHA256.');
