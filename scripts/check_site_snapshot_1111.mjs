// 2026/11/11：持股追蹤與績效走勢進五分鐘快照（報價維持不快取，見 check_quote_relay_fresh.mjs）。不連網（fetch 全部假造）。
import assert from 'node:assert/strict';
import worker, {readSnapshot, refreshSnapshots} from '../site-api/worker.mjs';

const data = new Map();
const env = {
  GAS_WEBAPP_URL: 'https://script.google.com/macros/s/test/exec', SITE_BRIDGE_TOKEN: 'x'.repeat(40),
  SNAP: { async get(k) { return data.get(k) ?? null; }, async put(k, v) { data.set(k, JSON.parse(v)); } }
};
const calls = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (_url, options) => {
  const {method, args} = JSON.parse(options.body);
  calls.push(method);
  if (method === 'apiListMailDates') return Response.json({ok: true, result: ['2026/11/10']});
  if (method === 'apiGetQuotesFor') return Response.json({ok: true, result: Object.fromEntries((args[0] || []).map(c => [c, {price: 100}]))});
  return Response.json({ok: true, result: {method}});
};
try {
  // 1. 排程那一輪也取持股追蹤與績效走勢，讀取時就從快照回
  const report = await refreshSnapshots(env);
  for (const k of ['apiGetHoldingsTracker:[]', 'apiGetPerformanceSeries:[]']) {
    assert.ok(report[k], '快照這一輪要包含 ' + k);
    assert.deepEqual((await readSnapshot(env, k))?.payload?.result, {method: k.split(':')[0]});
  }
  // 2. 透過 Worker 讀持股追蹤：命中快照，不打後端
  const ask = (method, args = []) => worker.fetch(new Request('https://w.example/api', {method: 'POST',
    headers: {'Origin': 'https://lee200202.github.io', 'Content-Type': 'application/json'},
    body: JSON.stringify({method, args})}), env, {waitUntil() {}});
  calls.length = 0;
  const tr = await ask('apiGetHoldingsTracker');
  assert.equal(tr.headers.get('X-Cache'), 'snapshot');
  assert.equal(calls.length, 0, '持股追蹤應由快照回覆');
} finally {
  globalThis.fetch = originalFetch;
}
console.log('11/11：持股追蹤、績效走勢由五分鐘快照回覆，不打後端');
