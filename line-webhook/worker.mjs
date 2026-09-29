// Workers Free + Queues Free：驗過 LINE 原始簽章並成功入列，才向 LINE 回 200。
// GAS 收到原始 body 與簽章後還會再驗一次；所有訂閱與去重仍在 Line.gs。
import { chartResponse } from './chart.mjs';
const BUILD = '2026-09-29-line-chart-v3';
const MAX_BYTES = 120_000; // Queues 每筆上限 128 KB，保留信封開銷。
const FINAL_ERRORS = new Set(['signature', 'destination-mismatch', 'bad-envelope', 'bad-body']);
const encoder = new TextEncoder();

function response(body, status = 200) {
  return Response.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
}

async function validSignature(raw, signature, secret) {
  if (!signature || !secret) return false;
  let supplied;
  try { supplied = Uint8Array.from(atob(signature.trim()), c => c.charCodeAt(0)); }
  catch { return false; }
  if (supplied.length !== 32) return false;
  const key = await crypto.subtle.importKey('raw', encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
  return crypto.subtle.verify('HMAC', key, supplied, raw);
}

function ready(env) {
  return !!(env.LINE_CHANNEL_SECRET && /^https:\/\/script\.google\.com\/macros\/s\/[^/]+\/exec$/.test(env.GAS_WEBAPP_URL || '') && env.LINE_EVENTS);
}

async function startLoading(events, env) {
  if (!env.LINE_CHANNEL_ACCESS_TOKEN) return;
  const users = [...new Set(events.filter(e => ((e.type === 'message' && e.message?.type === 'text') || e.type === 'postback') && e.source?.type === 'user')
    .map(e => e.source.userId).filter(Boolean))];
  await Promise.allSettled(users.map(userId => fetch('https://api.line.me/v2/bot/chat/loading/start', {
    method: 'POST', headers: { 'Authorization': `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ chatId: userId, loadingSeconds: 45 }), signal: AbortSignal.timeout(3000)
  })));
}

async function callback(request, env, ctx) {
  if (!ready(env)) return response({ ok: false, error: 'not-configured' }, 503);
  if (request.method !== 'POST') return response({ ok: false }, 405);
  const raw = new Uint8Array(await request.arrayBuffer());
  if (!raw.length || raw.length > MAX_BYTES) return response({ ok: false, error: 'size' }, 413);
  const sig = request.headers.get('x-line-signature') || '';
  if (!await validSignature(raw, sig, env.LINE_CHANNEL_SECRET)) return response({ ok: false, error: 'signature' }, 401);
  let body;
  try { body = new TextDecoder('utf-8', { fatal: true }).decode(raw); }
  catch { return response({ ok: false, error: 'encoding' }, 400); }
  let payload;
  try { payload = JSON.parse(body); } catch { return response({ ok: false, error: 'json' }, 400); }
  if (!payload || !Array.isArray(payload.events)) return response({ ok: false, error: 'events' }, 400);
  if (payload.events.length) {
    const envelope = { v: 1, kind: 'webhook', sig, body };
    if (encoder.encode(JSON.stringify(envelope)).length > 127_000) return response({ ok: false, error: 'queue-size' }, 413);
    try { await env.LINE_EVENTS.send(envelope); }
    catch (error) {
      console.error('queue enqueue failed', String(error).slice(0, 120));
      return response({ ok: false, error: 'enqueue' }, 503);
    }
    // 入列成功才啟動等待動畫；用 waitUntil，不讓 LINE Webhook 因圖示 API 變慢。
    // 權杖為選填 secret；未設定時 GAS 接手後仍會啟動同一動畫。
    if (ctx && typeof ctx.waitUntil === 'function') ctx.waitUntil(startLoading(payload.events, env));
  }
  return response({ ok: true });
}

async function forward(envelope, env) {
  if (!ready(env) || !envelope || envelope.v !== 1 || envelope.kind !== 'webhook' ||
      typeof envelope.body !== 'string' || typeof envelope.sig !== 'string') return 'retry';
  if (!await validSignature(encoder.encode(envelope.body), envelope.sig, env.LINE_CHANNEL_SECRET)) return 'discard';
  const target = `${env.GAS_WEBAPP_URL}?action=line`;
  const res = await fetch(target, {
    method: 'POST', headers: { 'Content-Type': 'application/json; charset=utf-8' },
    body: JSON.stringify(envelope), redirect: 'follow', signal: AbortSignal.timeout(45000)
  });
  if (!res.ok) return 'retry';
  let result;
  try { result = await res.json(); } catch { return 'retry'; }
  if (result.ok) return 'ok';
  if (FINAL_ERRORS.has(String(result.error || ''))) return 'discard';
  return 'retry';
}

export { validSignature, callback, forward, startLoading };
export default {
  async fetch(request, env, ctx) {
    const path = new URL(request.url).pathname;
    if (path === '/healthz') return response({ ok: true, build: BUILD, configured: ready(env), loadingConfigured: !!env.LINE_CHANNEL_ACCESS_TOKEN });
    if (path === '/callback') return callback(request, env, ctx);
    if (path === '/chart.png' && request.method === 'GET') return chartResponse(request);
    if (path === '/static/richmenu-query.png' || path === '/static/richmenu-notify.png') {
      // Assets 的根目錄已指向 ./static；舊後台仍使用 /static/... URL。
      const assetUrl = new URL(request.url);
      assetUrl.pathname = path.replace(/^\/static/, '');
      return env.ASSETS.fetch(new Request(assetUrl, request));
    }
    return response({ ok: false }, 404);
  },
  async queue(batch, env) {
    for (const message of batch.messages) {
      try {
        const result = await forward(message.body, env);
        if (result === 'retry') message.retry({ delaySeconds: Math.min(300, 30 * message.attempts) });
        else {
          if (result === 'discard') console.error('LINE event rejected permanently', message.id);
          message.ack();
        }
      } catch (error) {
        console.error('forward failed; queue will retry', String(error).slice(0, 120));
        message.retry({ delaySeconds: Math.min(300, 30 * message.attempts) });
      }
    }
  }
};
