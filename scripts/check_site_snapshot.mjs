import assert from 'node:assert/strict';
import {readSnapshot, refreshSnapshots} from '../site-api/worker.mjs';

const now = Date.now();
const key = 'apiGetDashboard:[]';
const good = {ok: true, result: {updatedAt: '2026/10/07 14:25'}};
const data = new Map([
  ['beat:v1', {at: now - 5 * 60_000, hashes: {[key]: 'abc'}, validated: {[key]: now - 5 * 60_000}}],
  ['snap:v1:' + key, {at: now - 5 * 60_000, hash: 'abc', payload: good}]
]);
const env = {
  GAS_WEBAPP_URL: 'https://script.google.com/macros/s/test/exec', SITE_BRIDGE_TOKEN: 'test',
  SNAP: {
    async get(k) { return data.get(k) ?? null; },
    async put(k, v) { data.set(k, JSON.parse(v)); }
  }
};
assert.deepEqual((await readSnapshot(env, key))?.payload, good);
// An unchanged mail/article answer can have an old content timestamp while
// the backend has freshly validated it. These are separate ages.
data.get('snap:v1:' + key).at = now - 61 * 60_000;
data.get('beat:v1').validated[key] = now - 60_000;
const checked = await readSnapshot(env, key);
assert.equal(checked?.validatedAt, now - 60_000);
assert.equal(checked?.at, now - 61 * 60_000);
const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async () => Response.json({ok: false, error: 'temporary'}, {status: 400});
  const report = await refreshSnapshots(env);
  assert.equal(report[key], 'recent-on-failure:backend-http-400');
  assert.equal(data.get('beat:v1').validated[key], now - 60_000);
  assert.deepEqual((await readSnapshot(env, key))?.payload, good);
  data.get('beat:v1').validated[key] = now - 13 * 60_000;
  assert.equal(await readSnapshot(env, key), null);
  data.get('beat:v1').validated[key] = now;
  data.get('beat:v1').hashes[key] = 'wrong';
  assert.equal(await readSnapshot(env, key), null);
} finally {
  globalThis.fetch = originalFetch;
}
let active = 0, peak = 0;
try {
  globalThis.fetch = async (_url, options) => {
    active++;
    peak = Math.max(peak, active);
    await new Promise(resolve => setTimeout(resolve, 5));
    const {method} = JSON.parse(options.body);
    active--;
    return Response.json({ok: true, result: method === 'apiListMailDates' ? ['2026/10/08'] : {date: '2026/10/08'}});
  };
  const report = await refreshSnapshots(env);
  assert.equal(peak, 1, 'scheduled sheet reads should not run concurrently');
  assert.ok(report['apiGetDashboard:[]']);
  assert.ok(report['apiGetMailContent:["2026/10/08"]']);
} finally {
  globalThis.fetch = originalFetch;
}
console.log('snapshot: recent failure retained; stale or mismatched data rejected; scheduled reads serialized');
