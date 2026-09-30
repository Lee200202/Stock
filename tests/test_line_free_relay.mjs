import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { inflateSync } from 'node:zlib';
import worker, { validSignature, callback, forward, startLoading, renewLoadingIfLate, LOADING_SECONDS } from '../line-webhook/worker.mjs';
import { chartResponse } from '../line-webhook/chart.mjs';

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
const { queuedAt, ...queued } = sent[0];
assert.deepEqual(queued, { v: 1, kind: 'webhook', body, sig });
assert(Number.isFinite(queuedAt) && queuedAt > 0, 'Worker 應記下入列時間供回覆延遲稽核');
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
const chart = chartResponse(new Request('https://relay.example/chart.png?ref=100&v=99,101,100,103'));
assert.equal(chart.status, 200);
assert.equal(chart.headers.get('content-type'), 'image/png');
const chartBytes = new Uint8Array(await chart.arrayBuffer());
assert.deepEqual(Array.from(chartBytes.slice(0, 8)), [137, 80, 78, 71, 13, 10, 26, 10]);
const idatAt = 8 + 25, idatSize = new DataView(chartBytes.buffer).getUint32(idatAt);
assert.equal(new TextDecoder().decode(chartBytes.slice(idatAt + 4, idatAt + 8)), 'IDAT');
assert.equal(inflateSync(chartBytes.slice(idatAt + 8, idatAt + 8 + idatSize)).length, 180 * (480 * 4 + 1));
assert.equal((await worker.fetch(new Request('https://relay.example/chart.png?ref=100&v=99,101,100'), env)).status, 200);
assert.equal(chartResponse(new Request('https://relay.example/chart.png?ref=100&v=99,101')).status, 400);
assert.equal(chartResponse(new Request('https://relay.example/chart.png?ref=100&v=99,NaN,101')).status, 400);

const originalFetch = globalThis.fetch;
try {
  let loading = null;
  globalThis.fetch = async (url, opts) => {
    loading = { url, authorization: opts.headers.Authorization, body: JSON.parse(opts.body) };
    return new Response(null, { status: 202 });
  };
  await startLoading([{ type: 'message', message: { type: 'text' }, source: { type: 'user', userId: 'Ufirst' } }],
    { LINE_CHANNEL_ACCESS_TOKEN: 'test-only' });
  assert.equal(loading.url, 'https://api.line.me/v2/bot/chat/loading/start');
  // v5：等待動畫用滿 LINE 允許的 60 秒
  assert.equal(LOADING_SECONDS, 60);
  assert.deepEqual(loading.body, { chatId: 'Ufirst', loadingSeconds: 60 });
  assert.equal(loading.authorization, 'Bearer test-only');
  loading = null;
  await startLoading([{ type: 'message', message: { type: 'text' }, source: { type: 'user', userId: 'Ufirst' } }], env);
  assert.equal(loading, null);
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

  // v5：第一次轉交且已排隊超過 20 秒才重開動畫；重試（可能已回覆過）與剛入列的都不重開
  const renewed = [];
  globalThis.fetch = async (url, opts) => { renewed.push([url, JSON.parse(opts.body)]); return new Response(null, { status: 202 }); };
  const tokenEnv = { ...env, LINE_CHANNEL_ACCESS_TOKEN: 'test-only' };
  const chatBody = JSON.stringify({ destination: 'Utest', events: [{ type: 'message', message: { type: 'text', text: '毅嘉' }, source: { type: 'user', userId: 'Ulate' } }] });
  const now = Date.now();
  assert.equal(await renewLoadingIfLate({ body: chatBody, queuedAt: now - 30000 }, 1, tokenEnv, now), true);
  assert.deepEqual(renewed, [['https://api.line.me/v2/bot/chat/loading/start', { chatId: 'Ulate', loadingSeconds: 60 }]]);
  assert.equal(await renewLoadingIfLate({ body: chatBody, queuedAt: now - 30000 }, 2, tokenEnv, now), false);
  assert.equal(await renewLoadingIfLate({ body: chatBody, queuedAt: now - 5000 }, 1, tokenEnv, now), false);
  assert.equal(await renewLoadingIfLate({ body: '{bad', queuedAt: now - 30000 }, 1, tokenEnv, now), false);
  assert.equal(renewed.length, 1);
} finally { globalThis.fetch = originalFetch; }
console.log('LINE free relay offline tests passed');
