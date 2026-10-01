// v94 replaces retired overview collectors; individual stock K history remains.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const s=fs.readFileSync('apps-script/Marketservice.gs','utf8'),ctx=vm.createContext({});vm.runInContext(s,ctx);
assert.equal(ctx.marketSampleTaiex_,undefined);assert.equal(ctx.apiGetMarketOverview,undefined);
assert.equal(ctx.marketSnapshotJob().retired,true);assert.equal(ctx.sectorCatchupJob().retired,true);
assert.equal(typeof ctx.backfillHourlyHistoryJob,'function');assert.equal(typeof ctx.mergedHourlyHistory_,'function');
let removed=[],audit=0;ctx.ScriptApp={getProjectTriggers:()=>['marketSnapshotJob','sectorCatchupJob','everyFiveMinJob','cmoneyPollJob','backfillDailyKJob'].map(n=>({getHandlerFunction:()=>n})),deleteTrigger:t=>removed.push(t.getHandlerFunction())};
ctx.PropertiesService={getScriptProperties:()=>({deleteProperty(){}})};ctx.auditTrackedSymbolsJob=()=>audit++;ctx.Logger={log(){}};
ctx.migrateV94();assert.deepEqual(removed,['marketSnapshotJob','sectorCatchupJob']);assert.equal(audit,1);
console.log('v94 market retirement preserves stock history and automation triggers');
