const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync('apps-script/Marketservice.gs','utf8');
const payload={};
const ctx=vm.createContext({console,Date,JSON,Math,isFinite,Logger:{log(){}},
  TZ:'Asia/Taipei',Utilities:{formatDate:()=> '2026/09/19'}});
vm.runInContext(src,ctx);
ctx.marketPayload_=(name,code)=>payload[name]||null;
const bar=(date,close=100)=>({date,open:close,high:close,low:close,close,volume:10});
const arr=(date,close=100)=>[date,close,close,close,close,20];
payload['Yahoo分K']={data:[arr('2026/09/04 09:00'),arr('2026/09/07 09:00'),arr('2026/09/17 09:00')]};
payload['分K歷史']={data:[arr('2026/09/17 09:00',101)]};
let merged=ctx.mergedHourlyHistory_('6533',[bar('2026/09/04 09:00')]);
assert.equal(merged.length,3);assert.equal(merged[0].volume,10);assert.equal(merged[2].close,101);
// 備援口徑不同不拼接成假跳空。
payload['Yahoo分K'].data[2][4]=50;
merged=ctx.mergedHourlyHistory_('6533',[bar('2026/09/04 09:00')]);assert.equal(merged.length,2);
const coverage=ctx.hourlyCoverage_('6533',merged,[{date:'2026/09/04',volume:10},{date:'2026/09/07',volume:10},{date:'2026/09/08',volume:0},{date:'2026/09/17',volume:10}]);
assert.equal(coverage.missingDates.join(','),'2026/09/07');assert.equal(coverage.partialDates.length,2);
// 新版必須走歷史60分，而不是重複打只能回今天的端點。
assert(src.includes('/historical/candles/'));assert(src.includes('timeframe=60'));assert(src.includes('fugleHistPace_()'));
const q=fs.readFileSync('apps-script/Quoteservice.gs','utf8');
assert(q.includes("month: volumeInLots_(aggregate_(daily, 'month'))"));
assert(q.includes('hist[seen[r.date]]=r'));assert(q.includes('hhmm<1405'));
console.log('PASS: hourly source priority, duplicate volume, adjustment conflict, missing-day diagnostics, monthly and live refresh.');
