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
test('intraday estimate uses quote timestamp and past-only baseline',()=>{
 const i=fixture();i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:1800,prevClose:100,stamp:base.date+' 09:30:00',stale:false};
 const r=run(i,{volumeMode:'estimate'}, {...base,afterClose:false});
 assert.equal(r.volumeRatio,1.8);assert.equal(r.estimateMinutes,30);
 assert.ok(Math.abs(r.estimatedVolume-(1800+1000*240/270))<1e-8);
 assert.equal(r.pass,true);assert.equal(r.estimated,true);
});
test('estimate does not waive actual minimum traded lots',()=>{
 const i=fixture();i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:800,prevClose:100,stamp:base.date+' 09:30:00',stale:false};
 const r=run(i,{volumeMode:'estimate',volumeMultiple:1.5}, {...base,afterClose:false});assert.equal(r.pass,false);
});
test('preopen, first five minutes, invalid time and after hours do not fabricate estimates',()=>{
 for(const t of ['08:59:00','09:04:59','15:00:00','bad']){
  const i=fixture();i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:3000,prevClose:100,stamp:base.date+' '+t,stale:false};
  const r=run(i,{volumeMode:'estimate'}, {...base,afterClose:false});assert.equal(r.comparisonRatio,null,t);assert.equal(r.pass,false,t);
 }
});
test('UTC quote time converts to Taipei; final daily K never remains estimated',()=>{
 const i=fixture();i.quote={date:base.date,open:103,high:107,low:102,last:106,volume:1800,prevClose:100,stamp:base.date.replaceAll('/','-')+'T01:30:00Z',stale:false};
 assert.equal(run(i,{volumeMode:'estimate'}, {...base,afterClose:false}).estimateMinutes,30);
 const r=run(i,{volumeMode:'estimate'},base);assert.equal(r.estimated,false);assert.equal(r.comparisonRatio,2.5);assert.equal(r.estimatedVolume,null);
});
/* ---- v136 條件組：同一組一種邏輯、組與組之間另一種 ---- */
const clean = ctx.window.MarketBattle.cleanPrefs;
// 事件日：開盤跳空成立；量與均線可以各自調成不成立。
function mixed({volume = 2500, close = 106, low = 102} = {}) { const i = fixture(); Object.assign(i.bars[60], {volume, close, low}); return i; }
const AandB_orC = {groupLogic: 'anyOfAll', groups: [['useGap', 'useVolume'], ['useTrend']]};
test('(A and B) or C: either group is enough',()=>{
 let r = run(mixed({volume: 1500}), AandB_orC, base);                       // 量不夠，但收盤在均線上
 assert.deepEqual(r.groups.map(g => g.pass), [false, true]); assert.equal(r.pass, true);
 assert.equal(r.formula, '（向上開盤缺口 且 成交量倍數） 或 20 日均線');
 r = run(mixed({close: 99.5, low: 99}), AandB_orC, base);                    // 收盤跌回均線下，但跳空加爆量成立
 assert.deepEqual(r.groups.map(g => g.pass), [true, false]); assert.equal(r.pass, true);
 r = run(mixed({volume: 1500, close: 99.5, low: 99}), AandB_orC, base);
 assert.deepEqual(r.groups.map(g => g.pass), [false, false]); assert.equal(r.pass, false); assert.equal(r.status, '未符合設定');
});
test('(A or B) and C: every group must hold',()=>{
 const p = {groupLogic: 'allOfAny', groups: [['useGap', 'useBreakout'], ['useVolume']]};
 let r = run(mixed(), p, base); assert.equal(r.pass, true); assert.equal(r.formula, '（向上開盤缺口 或 20 日突破） 且 成交量倍數');
 r = run(mixed({volume: 1500}), p, base); assert.deepEqual(r.groups.map(g => g.pass), [true, false]); assert.equal(r.pass, false);
});
test('three groups with three different conditions, and one condition in two groups',()=>{
 const p = {groupLogic: 'anyOfAll', groups: [['useGap', 'useVolume'], ['useGap', 'useTrend'], ['useBoll']]};
 const r = run(mixed({volume: 1500}), p, base);                              // 第 1 組不成立、第 2 組成立
 assert.equal(r.groups.length, 3); assert.equal(r.groups[0].pass, false); assert.equal(r.groups[1].pass, true); assert.equal(r.pass, true);
 assert.equal(r.checks.filter(c => c.enabled).map(c => c.key).join(), 'gap,volume,trend,boll');
});
test('missing data inside a group is neither a pass nor a fail',()=>{
 const i = mixed({volume: 1500}); i.bars.splice(48, 1);                      // 均量與均線的歷史缺一天
 let r = run(i, AandB_orC, base);                                           // 第 1 組：缺口成立、量資料不足 → 未定；第 2 組：資料不足
 assert.deepEqual(r.groups.map(g => g.pass), [null, null]); assert.equal(r.pass, false); assert.equal(r.status, '資料待核對');
 i.bars[58].high = 105;                                                     // 缺口不成立 → 第 1 組確定不成立，第 2 組仍未定
 r = run(i, AandB_orC, base); assert.deepEqual(r.groups.map(g => g.pass), [false, null]); assert.equal(r.status, '資料待核對');
 r = run(i, {groupLogic: 'allOfAny', groups: [['useGap'], ['useTrend']]}, base);   // 「且」的那一層遇到確定不成立就是不符合
 assert.equal(r.status, '未符合設定');
});
test('legacy settings map to one equivalent group',()=>{
 assert.deepEqual(JSON.parse(JSON.stringify(clean({useGap: true, useVolume: false, useTrend: true, logic: 'any'}).groups)), [['useGap', 'useTrend']]);
 assert.equal(clean({logic: 'any'}).groupLogic, 'allOfAny'); assert.equal(clean({logic: 'all'}).groupLogic, 'anyOfAll');
 assert.equal(clean({}).useGap, true); assert.equal(clean({groups: [['useTrend']]}).useGap, false);
});
test('groups are cleaned: unknown keys, duplicates, empty groups, at most four',()=>{
 const g = JSON.parse(JSON.stringify(clean({groups: [['useGap', 'useGap', 'nope'], [], 'x', ['useKD'], ['useMacd'], ['useBoll'], ['useBody']]}).groups));
 assert.deepEqual(g, [['useGap'], ['useKD'], ['useMacd'], ['useBoll']]);
 assert.equal(run(fixture(), {groups: []}, base).status, '資料待核對');
});
test('custom volume basis: any whole number of days from 2 to 120',()=>{
 const i = fixture(); i.bars.slice(53, 60).forEach(b => { b.volume = 500; });   // 最近 7 天量縮
 let r = run(i, {volumeDays: 7}, base); assert.equal(r.volumeMA, 500); assert.equal(r.volumeRatio, 5); assert.match(r.checks[1].rule, /前 7 個交易日均量/);
 r = run(i, {volumeDays: 20}, base); assert.equal(r.volumeMA, (13 * 1000 + 7 * 500) / 20);
 assert.equal(clean({volumeDays: 7}).volumeCustom, true); assert.equal(clean({volumeDays: 20}).volumeCustom, false); assert.equal(clean({volumeDays: 20, volumeCustom: true}).volumeCustom, true);
 for (const bad of [1, 121, 7.5, 'abc', -3]) assert.equal(clean({volumeDays: bad}).volumeDays, 20, String(bad));
 assert.equal(clean({volumeDays: '45'}).volumeDays, 45);
 assert.equal(run(i, {volumeDays: 61}, base).status, '資料待核對');             // 只有 60 天歷史：資料不足，不硬算
});
console.log(`${count} battle checks passed`);
