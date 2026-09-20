const fs=require('fs'),vm=require('vm'),assert=require('assert');
const props={},ctx={Date,JSON,Math,Logger:{log(){}},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v})},ScriptApp:{getProjectTriggers:()=>[],newTrigger:()=>({timeBased:()=>({after:()=>({create(){}})})})},withLock_:f=>f(),fmtDate_:s=>s,nowStamp_:()=>new Date().toISOString(),adminAuth_(){},stepArticle_(){},rebuildHoldingsTrackerJob(){},rebuildPerformanceHistoryJob:()=>({ok:true}),snapshotPerformanceJob(){}};
vm.createContext(ctx);vm.runInContext(fs.readFileSync('apps-script/Evidencequality.gs','utf8'),ctx);
ctx.queueDayEditSync_('2026/09/17');assert.equal(ctx.daySyncState_().index,0);
ctx.runDayEditSync_();assert.equal(ctx.daySyncState_().step,'重算持股追蹤');assert.equal(ctx.daySyncState_().index,1);
// 中途又存同日以及別日：不得蓋掉正在進行的游標，結束後按日期佇列處理。
ctx.queueDayEditSync_('2026/09/17');ctx.queueDayEditSync_('2026/09/18');assert.equal(ctx.daySyncState_().index,1);
ctx.runDayEditSync_();ctx.runDayEditSync_();ctx.runDayEditSync_();assert.equal(ctx.daySyncState_().status,'完成');
ctx.dayEditSyncTick_();assert.equal(ctx.daySyncState_().date,'2026/09/17');assert.equal(ctx.daySyncState_().index,0);
// 執行租約內的重入不能同時重建持股。
let st=ctx.daySyncState_();st.running=true;st.stepStartedMs=Date.now();ctx.saveDaySync_(st);ctx.runDayEditSync_();assert.equal(ctx.daySyncState_().index,0);
st.running=false;ctx.saveDaySync_(st);ctx.stepArticle_=()=>{throw Error('network');};ctx.runDayEditSync_();assert.equal(ctx.daySyncState_().status,'等待續跑');
st=ctx.daySyncState_();st.retryAt=0;ctx.saveDaySync_(st);ctx.dayEditSyncTick_();assert.equal(ctx.daySyncState_().index,0);assert.equal(ctx.daySyncState_().status,'處理中');
const html=fs.readFileSync('apps-script/Market.html','utf8'),window={};vm.runInNewContext(fs.readFileSync('apps-script/MarketCharts.html','utf8').replace(/<\/?script>/g,'')+'\n'+html.match(/<script>\s*window.marketTechnicalHtml[\s\S]*?<\/script>/)[0].replace(/<\/?script>/g,''),{window,Date,Math,Number,Array,isFinite});
const rows=Array.from({length:40},(_,i)=>['2026/08/'+String(i%28+1).padStart(2,'0'),100,101,99,100,10]);
const ind=window.marketIndicators(rows);assert.equal(ind[18].ma,null);assert.equal(ind[19].ma,100);assert.equal(ind[39].upper,100);assert.equal(ind[39].hist,0);
assert(window.marketTechnicalHtml(rows,true).includes('61.8%'));assert(window.marketTechnicalHtml(rows.slice(0,3),false).includes('目前 3 根'));
const weekly=window.marketAggregate([['2026/09/18',10,12,9,11,100],['2026/09/21',11,13,10,12,200]],'week');assert.equal(weekly.length,2);
assert(!html.includes('class="empty"'));assert(html.includes('}},180000)'));
console.log('PASS v47: automatic queue, concurrent edits, progress names, retry lease, indicators, market empty layout.');
