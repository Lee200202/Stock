// 全部離線：模擬KV／DO／GAS，驗證操作次數、同時呼叫、UTC跨日及逐鍵過期。
import assert from 'node:assert/strict';
import test from 'node:test';
import worker from '../site-api/worker.mjs';
import {BUNDLE_KEY, SNAP_FRESH_MS, SnapshotCoordinator, buildSnapshotBundle, readSnapshot} from '../site-api/snapshots.mjs';

const make = () => {
  const data = new Map(), store = new Map(), calls = [], counts = {get: 0, put: 0};
  const env = {GAS_WEBAPP_URL:'https://script.google.com/macros/s/test/exec', SITE_BRIDGE_TOKEN:'x'.repeat(40),
    SNAP: {async get(k) { counts.get++; return data.get(k) ?? null; },
      async put(k, v) { counts.put++; data.set(k, JSON.parse(v)); }} };
  const state = {storage: {async get(k) { return store.get(k); }, async put(k, v) { store.set(k, structuredClone(v)); },
    async transaction(fn) { return fn(this); }}};
  const backend = async (_env, {method}) => {
    calls.push(method);
    const result = method === 'apiGetDashboard' ? {today:{date:'2026/11/13'}, tracker:{held:[],exited:[],summary:{n:1}}}
      : method === 'apiListMailDates' ? ['2026/11/13'] : {method};
    return {payload:{ok:true,result}};
  };
  return {env,data,store,calls,counts,state,backend};
};
const req = () => new Request('https://snapshot.internal/refresh', {method:'POST'});
const withClock = async fn => { const original=Date.now; let clock=Date.parse('2026-11-13T00:00:00Z');
  Date.now=()=>clock; try { await fn({set:n=>{clock=n;}, advance:n=>{clock+=n;}, now:()=>clock}); } finally { Date.now=original; } };

test('一包一次寫入；共用首頁追蹤，六個邏輯後端查詢減為五個', async () => {
  const f=make(); const report=await buildSnapshotBundle(f.env,f.backend);
  assert.equal(f.counts.put,1); assert.equal(f.counts.get,1);
  assert.equal(f.calls.length,5); assert.equal(f.calls.includes('apiGetHoldingsTracker'),false);
  assert.equal(Object.keys(f.data.get(BUNDLE_KEY).entries).length,6);
  assert.equal(report.kvWrites,1); assert.equal(report.backendKeys,5);
  assert.deepEqual((await readSnapshot(f.env,'apiGetHoldingsTracker:[]')).payload.result,{held:[],exited:[],summary:{n:1}});
});
test('首頁缺持股內容就獨立取追蹤，不從null製造成功資料', async () => {
  const f=make(); const backend=async(e,b)=>b.method==='apiGetDashboard'?{payload:{ok:true,result:{tracker:null}}}:f.backend(e,b);
  await buildSnapshotBundle(f.env,backend);
  assert.equal(f.calls.includes('apiGetHoldingsTracker'),true);
});
test('不同方法同時讀快照，只共用一次KV讀取', async () => {
  const f=make(); await buildSnapshotBundle(f.env,f.backend);
  // 換binding物件代表另一個冷執行個體；之後同時到達仍只讀一次。
  f.env.SNAP={...f.env.SNAP}; f.counts.get=0;
  await Promise.all(['apiGetDashboard:[]','apiGetHoldingsTracker:[]','apiListRecordDates:[]'].map(k=>readSnapshot(f.env,k)));
  assert.equal(f.counts.get,1);
});
test('同一時槽十次並行刷新只寫一次；重建DO也不能再寫', async () => {
  const f=make(); const writer=new SnapshotCoordinator(f.state,f.env,f.backend);
  await Promise.all(Array.from({length:10},()=>writer.fetch(req())));
  await new SnapshotCoordinator(f.state,f.env,f.backend).fetch(req());
  assert.equal(f.counts.put,1); assert.equal(f.calls.length,5);
});
test('UTC一天即使每個五分鐘時槽都手動觸發，也只有288次寫入', async () => withClock(async clock=>{
  const f=make(); const writer=new SnapshotCoordinator(f.state,f.env,f.backend);
  for(let i=0;i<288;i++){await writer.fetch(req());clock.advance(300_000);}
  assert.equal(f.counts.put,288); assert.equal(f.store.get('budget:v2').used,288);
}));
test('正常216輪固定上界216次，而不是每輪最多7次的1512次', async () => withClock(async clock=>{
  const f=make(); const writer=new SnapshotCoordinator(f.state,f.env,f.backend);
  for(let i=0;i<216;i++){await writer.fetch(req());clock.advance(300_000);}
  assert.equal(f.counts.put,216); assert.ok(f.counts.put<500);
  assert.equal(f.calls.length,1080); // 舊版正常六鍵×216=1296。
}));
test('UTC跨日重新給預算；不是依台北午夜計算', async () => withClock(async clock=>{
  const f=make(); const day='2026-11-13';
  f.store.set('budget:v2',{day,used:450,lastSlot:-1});
  const writer=new SnapshotCoordinator(f.state,f.env,f.backend);
  assert.equal((await(await writer.fetch(req())).json()).skipped,'daily-budget');
  clock.set(Date.parse('2026-11-14T00:00:00Z'));
  await writer.fetch(req());assert.equal(f.counts.put,1);assert.equal(f.store.get('budget:v2').used,1);
}));
test('KV寫入結果不明仍消耗該時槽；同槽重試不再寫', async () => {
  const f=make();f.env.SNAP.put=async()=>{f.counts.put++;throw new Error('unknown');};
  const writer=new SnapshotCoordinator(f.state,f.env,f.backend);
  assert.equal((await writer.fetch(req())).status,502);
  await writer.fetch(req());assert.equal(f.counts.put,1);assert.equal(f.store.get('budget:v2').used,1);
});
test('單鍵失敗保留原核對時間，其他成功不會讓它續命', async () => withClock(async clock=>{
  const f=make();await buildSnapshotBundle(f.env,f.backend);
  const first=clock.now(); clock.advance(300_000);
  await buildSnapshotBundle(f.env,async(e,b)=>b.method==='apiGetPerformanceSeries'?{payload:{ok:false,error:'temporary'}}:f.backend(e,b));
  const entry=f.data.get(BUNDLE_KEY).entries['apiGetPerformanceSeries:[]'];
  assert.equal(entry.validatedAt,first);
  clock.advance(8*60_000);
  assert.equal(await readSnapshot(f.env,'apiGetPerformanceSeries:[]'),null);
  assert.ok(await readSnapshot(f.env,'apiGetDashboard:[]'));
}));
test('資料沒變可保留原內容時間，但核對時間可以更新', async () => withClock(async clock=>{
  const f=make();await buildSnapshotBundle(f.env,f.backend);const first=clock.now();clock.advance(300_000);
  await buildSnapshotBundle(f.env,f.backend);
  const got=await readSnapshot(f.env,'apiGetDashboard:[]');
  assert.equal(got.at,first);assert.equal(got.validatedAt,clock.now());assert.equal(f.counts.put,2);
}));
test('KV故障回null；不暴露快照寫入的公開入口', async () => {
  const f=make();f.env.SNAP={get:async()=>{throw new Error('quota');}};
  assert.equal(await readSnapshot(f.env,'apiGetDashboard:[]'),null);
  const response=await worker.fetch(new Request('https://worker.test/refresh'),f.env,{});
  assert.equal(response.status,404);
});
test('邊緣快取命中時，不讀KV、不回源', async () => {
  const originalCaches=globalThis.caches, originalFetch=globalThis.fetch;
  let kvReads=0,backend=0;
  const now=Date.now(), method='apiGetMailContent',args=['2026/01/19'];
  globalThis.caches={default:{match:async()=>Response.json({at:now,payload:{ok:true,result:{date:args[0]}}}),put:async()=>{}}};
  globalThis.fetch=async()=>{backend++;throw new Error('must-not-fetch');};
  try {
    const f=make(); f.env.SNAP={get:async()=>{kvReads++;return null;}};
    const response=await worker.fetch(new Request('https://worker.test/api',{method:'POST',headers:{Origin:'https://lee200202.github.io','Content-Type':'application/json'},body:JSON.stringify({method,args})}),f.env,{waitUntil(){}});
    assert.equal(response.headers.get('X-Cache'),'edge-hit');assert.equal(kvReads,0);assert.equal(backend,0);
  } finally {globalThis.caches=originalCaches;globalThis.fetch=originalFetch;}
});
test('快照到12分鐘上限時，memo與邊緣快取都不可延長它', async () => withClock(async clock=>{
  const originalCaches=globalThis.caches,originalFetch=globalThis.fetch;
  const f=make(),method='apiGetHoldingsTracker',key=method+':[]';
  const now=clock.now(),entry={at:now-11*60_000-50_000,validatedAt:now-11*60_000-50_000,payload:{ok:true,result:{old:true}},hash:'h'};
  f.data.set(BUNDLE_KEY,{version:2,entries:{[key]:entry}});
  let edge=null,backends=0;
  globalThis.caches={default:{match:async()=>edge?.clone(),put:async(_k,res)=>{edge=res;}}};
  globalThis.fetch=async()=>{backends++;return Response.json({ok:true,result:{fresh:true}});};
  const ask=()=>worker.fetch(new Request('https://worker.test/api',{method:'POST',headers:{Origin:'https://lee200202.github.io','Content-Type':'application/json'},body:JSON.stringify({method,args:[]})}),f.env,{waitUntil(){}});
  try {
    const first=await ask();assert.equal(first.headers.get('X-Cache'),'snapshot');
    clock.advance(11_000);const next=await ask();assert.equal((await next.json()).result.fresh,true);assert.equal(backends,1);
  } finally {globalThis.caches=originalCaches;globalThis.fetch=originalFetch;}
}));
