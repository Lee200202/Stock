const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('apps-script/Cmoney.gs','utf8');
let rows=[['日期','版本','狀態','更新時間','備註'],['2026/09/18','v1','等待同步','','']],calls=0,fail=false,advance=false,available=true;
const queue={getDataRange:()=>({getDisplayValues:()=>rows.map(r=>r.slice())}),getRange:(row,col,n,w)=>({
 getDisplayValue:()=>rows[row-1][col-1],setValues:values=>values[0].forEach((v,i)=>rows[row-1][col-1+i]=v)})};
const ctx=vm.createContext({Date,JSON,Logger:{log(){}},TZ:'Asia/Taipei',dkExecStart_:()=>Date.now(),
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},Utilities:{formatDate:()=> '2026/09/20 12:00'},
 getSheet_:name=>name==='簡訊內容同步'?queue:{getDataRange:()=>({getValues:()=>available?[['日期','內容'],['2026/09/18','原文章']]:[['日期','內容']]})},
 fmtDate_:x=>x,stepArticle_:job=>{assert(job._nested&&job._codesDone);calls++;if(advance)rows[1][1]='v2';if(fail)throw Error('暫時連線失敗');}});
vm.runInContext(src.match(/^function cmSyncContentTick_\(\)\{[\s\S]*?^}/m)[0],ctx);
available=false;ctx.cmSyncContentTick_();assert.equal(calls,0);assert.equal(rows[1][2],'等待同步');
available=true;fail=true;ctx.cmSyncContentTick_();assert.equal(rows[1][2],'等待續跑');
fail=false;advance=true;ctx.cmSyncContentTick_();assert.equal(rows[1][2],'等待續跑');
advance=false;ctx.cmSyncContentTick_();assert.equal(rows[1][2],'完成');const n=calls;ctx.cmSyncContentTick_();assert.equal(calls,n);
assert(!src.match(/^function cmSyncContentTick_\(\)\{[\s\S]*?^}/m)[0].includes('sendEmail'));
const html=fs.readFileSync('apps-script/Admin.html','utf8'),nodes={};
const node=id=>nodes[id]||(nodes[id]={style:{}});
const ui=vm.createContext({$:node,esc:s=>s,stepIndexOf:(list,s)=>Math.max(0,list.indexOf(s)),layoutSteps(){},setProgressA11y_(){}});
vm.runInContext(html.match(/^function renderProgress\(o\)\{[\s\S]*?^}/m)[0],ui);
const render=state=>ui.renderProgress({box:'box',steps:'steps',bar:'bar',note:'note',state,stepList:['準備','寫入','完成']});
render({status:'尚未執行',step:'準備',pct:0});assert.equal(nodes.bar.style.width,'0%');assert(!nodes.steps.innerHTML.includes('step on'));
render({status:'配額暫停',step:'寫入',pct:100});assert.equal(nodes.bar.style.width,'99%');assert(nodes.steps.innerHTML.includes('step wait'));
render({status:'完成',step:'完成',pct:100,remaining:3});assert.equal(nodes.bar.style.width,'99%');
render({status:'完成',step:'完成',pct:100});assert.equal(nodes.bar.style.width,'100%');
assert(html.indexOf('id="progCard"')<html.indexOf('<h2>判定歷程</h2>'));
console.log('PASS: SMS content queue retry/version safety/no resend; aligned progress states and task placement.');
