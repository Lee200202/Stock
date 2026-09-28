import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import worker, { validSignature, callback, forward } from '../line-webhook/worker.mjs';

const secret = '0123456789abcdef0123456789abcdef';
const gas = 'https://script.google.com/macros/s/test-deployment/exec';
const body = JSON.stringify({ destination: 'Utest', events: [{ type: 'follow', webhookEventId: 'test-1' }] });
const sig = createHmac('sha256', secret).update(body).digest('base64');
const sent = [];
const assetPaths = [];
const env = { LINE_CHANNEL_SECRET: secret, GAS_WEBAPP_URL: gas,
  LINE_EVENTS: { async send(x) { sent.push(x); } }, ASSETS: { async fetch(request) { assetPaths.push(new URL(request.url).pathname); return new Response('png'); } } };

assert.equal(await validSignature(new TextEncoder().encode(body), sig, secret), true);
assert.equal(await validSignature(new TextEncoder().encode(body), sig.slice(1), secret), false);
assert.equal((await callback(new Request('https://relay.example/callback', { method: 'POST', body,
  headers: { 'x-line-signature': sig } }), env)).status, 200);
assert.deepEqual(sent[0], { v: 1, kind: 'webhook', body, sig });
assert.equal((await callback(new Request('https://relay.example/callback', { method: 'POST', body,
  headers: { 'x-line-signature': 'invalid' } }), env)).status, 401);
assert.equal(sent.length, 1);
const empty = JSON.stringify({ destination: 'Utest', events: [] });
assert.equal((await callback(new Request('https://relay.example/callback', { method: 'POST', body: empty,
  headers: { 'x-line-signature': createHmac('sha256', secret).update(empty).digest('base64') } }), env)).status, 200);
assert.equal(sent.length, 1);
const broken = { ...env, LINE_EVENTS: { async send() { throw new Error('queue unavailable'); } } };
assert.equal((await callback(new Request('https://relay.example/callback', { method: 'POST', body,
  headers: { 'x-line-signature': sig } }), broken)).status, 503);
assert.equal((await worker.fetch(new Request('https://relay.example/healthz'), env)).status, 200);
assert.equal((await worker.fetch(new Request('https://relay.example/static/richmenu-query.png'), env)).status, 200);
assert.deepEqual(assetPaths, ['/richmenu-query.png']);

const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async (url, opts) => {
    assert.equal(url, gas + '?action=line');
    assert.equal(JSON.parse(opts.body).body, body);
    return Response.json({ ok: true });
  };
  assert.equal(await forward(sent[0], env), 'ok');
  globalThis.fetch = async () => Response.json({ ok: false, error: 'busy' });
  assert.equal(await forward(sent[0], env), 'retry');
  globalThis.fetch = async () => Response.json({ ok: false, error: 'signature' });
  assert.equal(await forward(sent[0], env), 'discard');
  const actions = [];
  globalThis.fetch = async () => Response.json({ ok: false, error: 'busy' });
  await worker.queue({ messages: [{ body: sent[0], attempts: 1, retry: x => actions.push(['retry', x.delaySeconds]), ack: () => actions.push(['ack']) }] }, env);
  assert.deepEqual(actions, [['retry', 30]]);
} finally { globalThis.fetch = originalFetch; }
console.log('LINE free relay offline tests passed');
