const fs=require('fs'),vm=require('vm'),assert=require('assert');
const cache={},props={},c={Date,JSON,Math,Logger:{log(){}},TZ:'Asia/Taipei',PERFORMANCE_START_DATE:'2026/07/08',
 CACHE:{get:k=>cache[k],put:(k,v)=>cache[k]=v,remove:k=>delete cache[k]},fmtDate_:x=>x,
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v})},withLock_:fn=>fn(),nowStamp_:()=>new Date().toISOString(),adminAuth_(){},
 ScriptApp:{getProjectTriggers:()=>[],newTrigger:()=>({timeBased:()=>({after:()=>({create(){}})})})}};
vm.createContext(c);vm.runInContext(fs.readFileSync('apps-script/Cachebuilder.gs','utf8'),c);
const bar=d=>({date:d,open:10,high:12,low:9,close:11,volume:1000});
let r=c.missingKDateRanges_([bar('2026/09/17'),bar('2026/09/18')],'2026-09-17','2026-09-21','2026/09/21',false);assert.equal(r.length,0,'已有歷史+未收盤當日不打來源');
r=c.missingKDateRanges_([bar('2026/09/17')],'2026-09-17','2026-09-21','2026/09/21',true);assert.equal(r[0].from,'2026-09-18');assert.equal(r[0].to,'2026-09-21');
assert.equal(c.mergeDailyK_([bar('2026/09/18')],[{...bar('2026/09/18'),close:12},bar('2026/09/21')])[0].close,11,'完整舊K不能被重抓覆蓋');
assert.equal(c.mergeDailyK_([{...bar('2026/09/18'),volume:null}],[bar('2026/09/18')])[0].volume,1000,'無效量允許補齊');
c.Utilities={formatDate:(_,tz,f)=>f==='HHmm'?'1700':'2026/09/21'};
c.fugleHistorical_=()=>{throw Error('已有資料不應呼叫');};assert.equal(c.fetchMissingDailyK_('2330','2026-09-17','2026-09-18',[bar('2026/09/17'),bar('2026/09/18')]).length,0);
const pending=c.fetchMissingDailyK_('2330','2026-09-17','2026-09-18',[],Date.now());assert(pending.pending,'來不及查缺口不能標完成');
c.readSheetObjects_=name=>name==='日K快取'?['2026/04/01','2026/07/08'].map(d=>({'日期':d})):['2026/04/01','2026/07/08'].map(d=>({'日期':d,'追蹤檔數':1,'持有檔數':1,'平均報酬率':10,'正報酬比例':100}));
cache.perf_series=JSON.stringify({rows:[{date:'2026/04/01'}]});
const perf=JSON.stringify(c.getPerformanceSeries());assert(!perf.includes('2026/04/01'));assert(perf.includes('2026/07/08'));
vm.runInContext(fs.readFileSync('apps-script/Evidencequality.gs','utf8'),c);
c.stepArticle_=()=>{};c.rebuildHoldingsTrackerJob=()=>{};c.rebuildPerformanceHistoryJob=()=>({ok:true});c.snapshotPerformanceJob=()=>{};
c.queueDayEditSync_('2026/09/18');let st=c.daySyncState_();const id=st.id;
c.apiDaySyncDrive_('','step','OLD',0);assert.equal(c.daySyncState_().index,0);
c.apiDaySyncDrive_('','step',id,0);assert.equal(c.daySyncState_().index,1);
c.apiDaySyncDrive_('','step',id,0);assert.equal(c.daySyncState_().index,1,'重送不得跳第二步');
c.githubCfg_=()=>({repo:'x/y',token:'test',ref:'main'});let dispatched=0;c.UrlFetchApp={fetch:()=>{dispatched++;return {getResponseCode:()=>204};}};
st=c.daySyncState_();st.githubUntil=0;c.saveDaySync_(st);c.scheduleDaySync_();c.scheduleDaySync_();assert.equal(dispatched,1,'GitHub租約內不重派');
c.runDayEditSync_();c.runDayEditSync_();c.runDayEditSync_();assert.equal(c.daySyncState_().status,'完成');
c.apiDaySyncDrive_('','performance');assert.equal(c.daySyncState_().date,'2026/07/08');assert.equal(c.daySyncState_().index,1,'績效重建不重寫文章');
console.log('PASS v48: explicit performance boundary/cache, missing-only OHLCV, partial deadline, idempotent Actions steps, dispatch lease.');
