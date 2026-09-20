const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..'),props={ADMIN_KEY:'offline'};
const ctx=vm.createContext({console,Logger:{log:()=>{}},PropertiesService:{getScriptProperties:()=>({
 getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v,deleteProperty:k=>delete props[k]})},
 Utilities:{formatDate:()=> '2026/09/12 12:00:00',getUuid:()=>String(Math.random())},
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},withLock_:fn=>fn()});
for(const name of ['Config','Logic','DB','API','Refreshrunner'])
 vm.runInContext(fs.readFileSync(path.join(root,'apps-script',name+'.gs'),'utf8'),ctx);
ctx.rebuildPerformanceHistoryJob=()=>({ok:true,skipped:true,reason:'日K快取還沒有資料'});
let out=ctx.apiRefreshStep_('offline','perfhist','2026/09/11');
assert.equal(out.ok,false);assert(out.pending);assert.equal(out.done,false);
assert.equal(ctx.chainState_().status,'待補資料');
assert.equal(ctx.apiAdminChainState('offline').names[0],'重算績效歷史');
ctx.rebuildPerformanceHistoryJob=()=>({ok:true,days:3,codes:2});
out=ctx.apiRefreshStep_('offline','perfhist','2026/09/11');assert(out.ok && out.done);
let calls=0;
ctx.REFRESH_ORDER_=['batch','finish'];ctx.REFRESH_STEPS_={
 batch:{name:'分批',chunked:true,fn:()=>({done:++calls===2,processed:calls,total:2})},
 finish:{name:'結束',fn:()=> '完成'}};
out=ctx.apiRefreshStep_('offline','all','2026/09/11');assert(!out.done);assert.equal(calls,1);
out=ctx.apiRefreshStep_('offline','all','2026/09/11');assert(!out.done);assert.equal(calls,2);
out=ctx.apiRefreshStep_('offline','all','2026/09/11');assert(out.done);assert.equal(calls,2);
ctx.REFRESH_STEPS_.batch.fn=()=>{throw new Error('failed')};
out=ctx.apiRefreshStep_('offline','all','2026/09/11');assert(!out.ok);
props['refreshAllLease:2026/09/11']=JSON.stringify({token:'another-request',until:Date.now()+60000});
out=ctx.apiRefreshStep_('offline','all','2026/09/11');assert(out.busy);assert(!out.ok);
delete props['refreshAllLease:2026/09/11'];
const writes=[];
ctx.writeSubscriptionFields_({head:['Email','訂閱項目','關注股票代號','公式','狀態'],rowNum:2,
 sheet:{getRange:(...a)=>({setValues:v=>writes.push({a,v})})}},
 {'訂閱項目':'每日總覽','關注股票代號':'2330','狀態':'生效中'});
assert.equal(writes.length,2);assert.deepEqual(writes[0].a,[2,2,1,2]);assert.deepEqual(writes[1].a,[2,5,1,1]);
// Render functions are exercised with a small DOM contract, independently of API work.
const nodes={};function node(id){return nodes[id]||(nodes[id]={style:{},parentElement:{setAttribute(k,v){this[k]=v;}}});}
const admin=fs.readFileSync(path.join(root,'apps-script/Admin.html'),'utf8');
const ui=vm.createContext({$:node,esc:s=>s,layoutSteps:()=>{},CHAIN_NAMES:['資料','績效'],refDone:false});
for(const name of ['setProgressA11y_','renderChain']){
 const m=admin.match(new RegExp('^function '+name+'\\([^\\n]*\\) \\{?[\\s\\S]*?^}', 'm'));
 // Functions without a space before { use the same extraction boundary.
 const match=m||admin.match(new RegExp('^function '+name+'\\([^\\n]*\\)\\{[\\s\\S]*?^}', 'm'));
 assert(match,name);vm.runInContext(match[0],ui);
}
ui.renderChain({index:1,status:'執行中',sub:{done:10,total:10}});
assert.equal(nodes.refBar.style.width,'99%');assert.equal(nodes.refBar.parentElement['aria-valuenow'],'99');
ui.renderChain({index:1,status:'待補資料',error:'缺日K'});assert(ui.refDone);assert(!nodes.refSteps.innerHTML.includes('class="step on"'));
ui.renderChain({index:1,status:'完成'});assert.equal(nodes.refBar.style.width,'100%');
console.log('PASS: pending performance, resumable all, failure status, contiguous writes, progress boundaries and accessibility.');
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/MailService.gs'),'utf8'),ctx);
const safe=ctx.mdToHtml_('③ 盤勢總覽重點整理\n\n<script>alert(1)</script>\n\n價格 <900');
assert(safe.includes('<h2 '));assert(!safe.includes('<script>'));assert(safe.includes('&lt;900'));
assert(ctx.mailShell_('內容').includes('class="mail-shell"'));
console.log('PASS: semantic article headings, HTML escaping and themed mail shell.');
