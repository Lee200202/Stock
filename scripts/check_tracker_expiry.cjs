const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const sheet=fs.readFileSync('public-site/gas-source/SheetService.gs','utf8');
const admin=fs.readFileSync('public-site/gas-source/Adminservice.gs','utf8');
const get=sheet.slice(sheet.indexOf('function trackerStaleTradingDays_()'),sheet.indexOf('var AVOID_REOPEN_TRADING_DAYS'));
const set=admin.slice(admin.indexOf('function apiAdminSetTrackerDays('),admin.indexOf('function apiAdminHeldList('));
function env(value){const props=value==null?{}:{TRACKER_STALE_TRADING_DAYS:String(value)};let kicks=0,active=null;const p={getProperty:k=>props[k]??null,setProperties:v=>Object.assign(props,v)};const c={PropertiesService:{getScriptProperties:()=>p},adminAuth_:k=>{if(k!=='test')throw Error('unauthorized');},withLock_:f=>f(),daySyncState_:()=>active,PERFORMANCE_START_DATE:'2026/07/08',dayEditSyncTick_:()=>{kicks++},Logger:{log(){}},Date,JSON};vm.createContext(c);vm.runInContext(get+'\n'+set,c);return {c,props,kicks:()=>kicks,busy:()=>active={status:'處理中'}};}
let e=env();assert.equal(e.c.trackerStaleTradingDays_(),10);
for(const v of ['',0,-1,1.5,61,'15days','NaN']){assert.equal(e.c.apiAdminSetTrackerDays('test',v).ok,false);assert.equal(e.props.TRACKER_STALE_TRADING_DAYS,undefined);}
assert.equal(e.c.apiAdminSetTrackerDays('bad',15).ok,false);assert.equal(e.props.TRACKER_STALE_TRADING_DAYS,undefined);
let r=e.c.apiAdminSetTrackerDays('test',15);assert(r.ok&&r.changed);assert.equal(e.c.trackerStaleTradingDays_(),15);assert(JSON.parse(e.props.dayEditQueueV47)['COST:2026/07/08']);assert.equal(e.kicks(),1);
r=e.c.apiAdminSetTrackerDays('test',15);assert(r.ok&&!r.changed);assert.equal(e.kicks(),1);
e.busy();assert.equal(e.c.apiAdminSetTrackerDays('test',20).ok,false);assert.equal(e.c.trackerStaleTradingDays_(),15);
// Run the real expiry block, including the explicit-close guard; days 10/15 stay open until exceeded.
const a=sheet.indexOf('    if (!lastRound.close) {',sheet.indexOf('var STALE_TRADING_DAYS ='));
const b=sheet.indexOf('    /* v146',a);const expiry=sheet.slice(a,b);
function closeAfter(days,threshold,closed){const c={lastRound:closed?{close:{date:'2026/11/02'},closeKind:'明講賣出'}:{},recs:[{date:'2026/11/01'}],tradingDaysAfter_:()=>days,STALE_TRADING_DAYS:threshold,stat:{autoClosed:0},tr_(){},g:{}};vm.runInNewContext(expiry,c);return c.lastRound;}
assert(!closeAfter(10,10).close);assert(closeAfter(11,10).close);assert(!closeAfter(15,15).close);assert(closeAfter(16,15).close);assert.equal(closeAfter(20,15,true).closeKind,'明講賣出');
for(const raw of ['bad','0','61','1.5'])assert.equal(env(raw).c.trackerStaleTradingDays_(),10);
console.log('Tracker expiry setting: validation, auth, atomic queue, idempotency, busy guard, exact 10/15-day boundaries and explicit exits passed');
