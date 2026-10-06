/* Meaningful numeric and missing-data regression checks; no API, writes or sends. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = n => fs.readFileSync(path.join(root,'public-site/gas-source',n),'utf8');
const ctx = {window:{}, console}; vm.createContext(ctx);
vm.runInContext(read('BattleConfig.html').match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
vm.runInContext(read('Battle.html').match(/<script>([\s\S]*?)<\/script>/)[1],ctx);
const run = ctx.window.MarketBattle.evaluate;
let count = 0;
function test(name, fn) { fn(); count++; console.log('OK '+name); }
const calendar = []; let d = new Date('2026-07-01T12:00:00Z');
while(calendar.length<66) {if(d.getUTCDay()>0 && d.getUTCDay()<6) calendar.push(d.toISOString().slice(0,10).replaceAll('-','/'));d.setUTCDate(d.getUTCDate()+1);}
function fixture() {
 const bars = calendar.slice(0,60).map(date=>({date,open:100,high:101,low:99,close:100,volume:1000}));
 bars.push({date:calendar[60],open:103,high:107,low:102,close:106,volume:2500});
 return {code:'1234',name:'測試',bars};
}
const base = {date:calendar[60],calendar,volumeUnit:'lots',afterClose:true};
test('event excluded from 20-day volume mean; inclusive 2.5 multiplier',()=>{
 const r=run(fixture(),{},base); assert.equal(r.volumeMA,1000);assert.equal(r.volumeRatio,2.5);assert.equal(r.pass,true);assert.equal(r.complete,true);
});
test('percentage jump without clean price gap fails',()=>{
 const i=fixture();i.bars[59].high=105;assert.equal(run(i,{},base).checks[0].pass,false);
});
test('downward clean gap and black candle',()=>{
 const i=fixture();Object.assign(i.bars[60],{open:97,high:98,low:92,close:93});const r=run(i,{direction:'down',useBody:true},base);assert.equal(r.pass,true);
});
test('present-day bar does not become final during market hours',()=>{
 const i=fixture(); assert.equal(run(i,{}, {...base,afterClose:false}).status,'資料待核對');
 i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:2500,prevClose:100,stale:false};
 const r=run(i,{}, {...base,afterClose:false});assert.equal(r.status,'盤中條件符合');assert.equal(r.complete,false);
});
test('stale intraday quote blocks success',()=>{
 const i=fixture();i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:3000,prevClose:100,stale:true};assert.equal(run(i,{}, {...base,afterClose:false}).pass,false);
});
test('null volume is missing, not zero',()=>{const i=fixture();i.bars[60].volume=null;assert.equal(run(i,{},base).status,'資料待核對');});
test('prior trading-day gap blocks apparent success',()=>{const i=fixture();i.bars.splice(59,1);assert.equal(run(i,{},base).status,'資料待核對');});
test('missing interior baseline day blocks volume comparison',()=>{const i=fixture();i.bars.splice(48,1);assert.equal(run(i,{},base).checks[1].pass,null);});
test('calendar holiday is not counted as missing',()=>{const i=fixture();i.bars.splice(48,1);const c=calendar.filter((_,n)=>n!==48);assert.equal(run(i,{}, {...base,calendar:c}).pass,true);});
test('reference price mismatch blocks corporate-action false positive',()=>{const i=fixture();i.quote={date:base.date,prevClose:90};assert.equal(run(i,{},base).pass,false);});
test('volume units must be explicitly confirmed',()=>{assert.equal(run(fixture(),{}, {...base,volumeUnit:'shares'}).pass,false);});
test('duplicate bars require review',()=>{const i=fixture();i.bars.push({...i.bars[59]});assert.equal(run(i,{},base).status,'資料待核對');});
test('MA breakout MACD KD Bollinger and candle agree on rising fixture',()=>{
 const r=run(fixture(),{useTrend:true,useBreakout:true,useMacd:true,useKD:true,useBoll:true,useBody:true},base);assert.equal(r.pass,true);assert.equal(r.checks.length,8);
 const boll=r.checks.find(x=>x.key==='boll');assert.ok(Math.abs(boll.value-(100.3+2*Math.sqrt(1.71)))<1e-10);
});
test('disabled failing optional filter does not veto base conditions',()=>{const i=fixture();Object.assign(i.bars[60],{close:102.5});assert.equal(run(i,{},base).pass,true);assert.equal(run(i,{useBody:true},base).pass,false);});
test('OR permits gap alone while AND requires volume as well',()=>{const i=fixture();i.bars[60].volume=1500;assert.equal(run(i,{},base).pass,false);assert.equal(run(i,{logic:'any'},base).pass,true);});
test('OR known true is conclusive even if another selected baseline is missing',()=>{const i=fixture();i.bars.splice(48,1);const r=run(i,{logic:'any'},base);assert.equal(r.pass,true);assert.equal(r.status,'盤後條件符合');assert.ok(r.issues.some(x=>x.includes('成交量')));});
test('OR false and unknown remains pending; AND false and unknown is rejected',()=>{const i=fixture();i.bars.splice(48,1);i.bars[58].high=105;assert.equal(run(i,{logic:'any'},base).status,'資料待核對');assert.equal(run(i,{},base).status,'未符合設定');});
test('empty condition set cannot match every stock',()=>{assert.equal(run(fixture(),{useGap:false,useVolume:false},base).status,'資料待核對');});
test('optional-only combination respects settings and invalid logic defaults to AND',()=>{const i=fixture();i.bars[60].volume=1000;assert.equal(run(i,{useGap:false,useVolume:false,useTrend:true},base).pass,true);assert.equal(run(i,{logic:'invalid'},base).pass,false);});
test('invalid prefs reset; no NaN, unknown direction or invalid periods',()=>{const p=ctx.window.MarketBattle.cleanPrefs({gapPct:NaN,minLots:-1,volumeDays:1,direction:'either'});assert.equal(p.gapPct,2.5);assert.equal(p.minLots,1000);assert.equal(p.volumeDays,20);assert.equal(p.direction,'up');});
test('three future sessions are not borrowed at event time',()=>{assert.equal(run(fixture(),{},base).gaps[0].status,'等待／缺少後續日 K');});
function follow(n, fill) {const i=fixture(); for(let k=1;k<=n;k++)i.bars.push({date:calendar[60+k],open:106,high:108,low:fill&&k===1?100:104,close:107,volume:1000});return i;}
test('three completed subsequent sessions confirm unfilled candidate',()=>{const r=run(follow(3),{}, {...base,date:calendar[63]});assert.equal(r.gaps[0].status,'三個交易日未回補');});
test('third session still intraday cannot confirm mature gap',()=>{const r=run(follow(3),{}, {...base,date:calendar[63],afterClose:false});assert.equal(r.gaps[0].status,'等待／缺少後續日 K');});
test('filled gap remains filled even when another later session is missing',()=>{const i=follow(3,true);i.bars.splice(62,1);const r=run(i,{}, {...base,date:calendar[63]});assert.equal(r.gaps[0].status,'已回補');});
test('input bars and preferences are not mutated',()=>{const i=fixture(),before=JSON.stringify(i),p={useTrend:true};run(i,p,base);assert.equal(JSON.stringify(i),before);assert.deepEqual(p,{useTrend:true});});
// Execute the actual GAS API with strict no-write and no-fetch mocks.
test('API normalizes shares to lots and reads each sheet only once',()=>{
 let reads=0, propReads=0;
 class FrozenDate extends Date {constructor(...args){super(...(args.length?args:['2026-10-06T06:00:00Z']));}}
 const server={Date:FrozenDate,withSheetSnapshot_:fn=>fn(),loadCodeMap_:()=>({byCode:{1234:{name:'測試'}}}),trackedCodes_:()=>['1234'],TZ:'Asia/Taipei',
 Utilities:{formatDate:(d,tz,f)=>{const s=new Intl.DateTimeFormat('sv-SE',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(d);const iso=s.slice(0,10);return f==='yyyy/MM/dd'?iso.replaceAll('-','/'):f==='yyyy-MM-dd'?iso:f==='u'?String(new Date(iso+'T12:00:00Z').getUTCDay()||7):s.slice(11,16).replace(':','');}},
 marketHolidaySet_:()=>({}),MARKET_HOLIDAY_YEARS_:{2025:1,2026:1},HOLIDAY_PROP_PREFIX_:'TWSE_HOLIDAYS_',PropertiesService:{getScriptProperties:()=>({getProperty:()=>{propReads++;return null;}})},
 getQuoteCache:()=>({}),CACHE:{getAll:()=>({})},loadAllDailyK_:()=>{reads++;return {'1234':[{date:'2026/10/05',open:100,high:101,low:99,close:100,volume:1500000}]};},quoteFresh_:()=>true,nowStamp_:()=>'',opsRuntimeConserve_:()=>false,
 UrlFetchApp:{fetch:()=>{throw Error('Forbidden fetch');}},whyClosed_:()=>{throw Error('Forbidden calendar refresh');}};
 vm.createContext(server);vm.runInContext(read('BattleData.gs'),server);const r=server.apiGetBattleData(['1234']);assert.equal(r.items[0].bars[0].volume,1500);assert.equal(reads,1);assert.equal(r.volumeUnit,'lots');assert.equal(propReads,0);
 assert.throws(()=>server.apiGetBattleData(Array.from({length:25},(_,i)=>String(1000+i))),/24/);
});
console.log(`${count} battle checks passed`);
