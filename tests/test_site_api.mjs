import assert from 'node:assert/strict';
import worker from '../site-api/worker.mjs';

const env = {
  GAS_WEBAPP_URL: 'https://script.google.com/macros/s/test/exec',
  SITE_BRIDGE_TOKEN: 'x'.repeat(40)
};
const api = 'https://site.example.workers.dev/api';
const origin = 'https://lee200202.github.io';
const preflight = await worker.fetch(new Request(api, {method: 'OPTIONS', headers: {Origin: origin}}), env);
assert.equal(preflight.status, 204);
const denied = await worker.fetch(new Request(api, {method: 'POST', headers: {Origin: origin},
  body: JSON.stringify({method: 'setupSpreadsheet', args: []})}), env);
assert.equal(denied.status, 400);
const noAdminKey = await worker.fetch(new Request(api, {method: 'POST', headers: {Origin: origin},
  body: JSON.stringify({method: 'apiAdminDeleteRow', args: []})}), env);
assert.equal(noAdminKey.status, 401);
const foreign = await worker.fetch(new Request(api, {method: 'POST', headers: {Origin: 'https://evil.example'},
  body: JSON.stringify({method: 'apiGetDashboard', args: []})}), env);
assert.equal(foreign.status, 403);

const originalFetch = globalThis.fetch;
let forwarded;
globalThis.fetch = async (_url, options) => {
  forwarded = JSON.parse(options.body);
  return new Response(JSON.stringify({ok: true, result: {date: '2026/09/28'}}),
    {status: 200, headers: {'Content-Type': 'application/json'}});
};
try {
  const good = await worker.fetch(new Request(api, {method: 'POST', headers: {Origin: origin},
    body: JSON.stringify({method: 'apiGetDashboard', args: []})}), env);
  assert.equal(good.status, 200);
  assert.equal((await good.json()).result.date, '2026/09/28');
  assert.equal(forwarded.bridgeToken, env.SITE_BRIDGE_TOKEN);
  for (const method of ['apiAdminLineBindCode', 'apiAdminLineClearTesters',
    'apiAdminLineSetupRichMenu', 'apiAdminLineTestPush', 'apiAdminLineValidate',
    'apiAdminSetSmsEmail']) {
    const response = await worker.fetch(new Request(api, {method: 'POST', headers: {Origin: origin},
      body: JSON.stringify({method, args: ['admin']})}), env);
    assert.equal(response.status, 200, `${method} should reach GAS`);
    assert.equal(forwarded.method, method);
  }
} finally { globalThis.fetch = originalFetch; }
console.log('site-api: preflight, allowlist, origin, forwarding OK');
