// Free Cloudflare Worker: public Pages frontend -> existing Apps Script backend.
// The bridge token stays server-side. Admin methods still require the existing admin key.
const ALLOWED = 'https://lee200202.github.io';
const BUILD = 'site-api-v107-r1';
const MAX_ARGS = 8;
const ADMIN_METHODS = ('apiAdminCancelCrawl apiAdminCancelDaySync apiAdminCancelFix ' +
  'apiAdminCancelFullFix apiAdminCancelJob apiAdminCancelRefresh apiAdminCancelSmsJob ' +
  'apiAdminChainState apiAdminCrawlState apiAdminCrawlTranscript apiAdminDayRows ' +
  'apiAdminDaySyncState apiAdminDecisions apiAdminDeleteRow apiAdminDeleteSms ' +
  'apiAdminDeleteSmsBatch apiAdminDispatch apiAdminFixState apiAdminFullFixState ' +
  'apiAdminHeldList apiAdminHoldToday apiAdminJobStatus ' +
  'apiAdminKCoverage apiAdminLineBindCode apiAdminLineClearTesters ' +
  'apiAdminLineDiagnose apiAdminLineLookup apiAdminLineRetry apiAdminLineSaveConfig ' +
  'apiAdminLineSetupRichMenu apiAdminLineStatus apiAdminLineTestPush ' +
  'apiAdminLineValidate apiAdminListSms apiAdminLogin apiAdminManualDays ' +
  'apiAdminSetSmsEmail ' +
  'apiAdminManualEntry apiAdminMergeTranscripts apiAdminOpsDay apiAdminPreviewCleanup ' +
  'apiAdminRebuildMail apiAdminReclassify apiAdminResumeFullFix apiAdminResumeJob ' +
  'apiAdminRetryFullFixStep apiAdminRunInfo apiAdminSetDailyPushStart ' +
  'apiAdminSetHoldingCost apiAdminSmsDays apiAdminSmsState apiAdminStartDaySync ' +
  'apiAdminStartFullFix apiAdminStartRefreshFrom apiAdminStartSmsJob apiAdminSubmit ' +
  'apiAdminTodayStatus apiAdminUpdateRow').split(' ');
const METHODS = new Set([
  'apiAsk', 'apiFormatTranscript', 'apiGetCandlesBundle', 'apiGetDashboard',
  'apiGetHoldingsTracker', 'apiGetLineEntry', 'apiGetMailContent',
  'apiGetMemberSms', 'apiGetPerformanceSeries',
  'apiGetQuotesFor', 'apiGetStockFundamentals', 'apiGetStockSummary',
  'apiGetTechStats', 'apiGetTranscript', 'apiGetUserContext',
  'apiListMailDates', 'apiListModels', 'apiListRecordDates', 'apiListSmsDates',
  'apiListTranscriptDates', 'apiLogUsage', 'apiLookupSubscription',
  'apiResetSession', 'apiSearchByDate', 'apiSearchStock',
  'apiSendUnsubscribeLink', 'apiStopAllMail', 'apiSubscribe',
  'apiSuggestCodes', 'apiUnsubscribeConfirm', 'apiUpdateSubscription', 'apiValidateKey', ...ADMIN_METHODS
]);

/* 公開唯讀結果只在有效期內重用；不以過期資料掩蓋後端失敗。
   同時到達的相同查詢共用一個進行中請求，避免每位訪客都啟動 GAS 讀表。 */
const READ_TTL = {
  apiGetDashboard: 60, apiGetQuotesFor: 60, apiGetHoldingsTracker: 120,
  apiGetStockSummary: 120, apiSearchStock: 120, apiListRecordDates: 300, apiListMailDates: 300,
  apiGetMailContent: 60, apiSearchByDate: 60, apiGetCandlesBundle: 300, apiGetPerformanceSeries: 600,
  apiGetLineEntry: 600, apiListTranscriptDates: 600, apiGetTranscript: 600, apiGetTechStats: 1800,
  apiGetStockFundamentals: 3600, apiSuggestCodes: 3600, apiListModels: 3600
};
const MEMO_MAX = 300;
const memo = new Map();
const inFlight = new Map();
/* 可以放心重送的方法：唯讀，或重送結果相同（退訂、查詢與更新訂閱）。
   Apps Script 的 POST 在回 302 之前就已經執行完，轉址那一步回 404 時事情其實做過了；
   寄信、問答這類重送會多做一次的方法只重試一次連線錯誤，不重試 404。 */
const IDEMPOTENT = new Set([...Object.keys(READ_TTL), 'apiLookupSubscription', 'apiUnsubscribeConfirm',
  'apiUpdateSubscription', 'apiStopAllMail', 'apiSubscribe', 'apiAdminLogin', 'apiAdminTodayStatus',
  'apiAdminLineStatus', 'apiAdminLineDiagnose', 'apiAdminOpsDay']);

function remember(key, body, at = Date.now()) {
  if (memo.has(key)) memo.delete(key);
  memo.set(key, {at, body});
  while (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value);
}

// 公開唯讀查詢跨 Worker 執行個體共用；過期即未命中，不回退到過期內容。
async function edgeKey(request, env, key) {
  if (!key || typeof caches === 'undefined') return null;
  const bytes = new TextEncoder().encode(BUILD + ':' + env.GAS_WEBAPP_URL + ':' + key);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  const hex = Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
  return new Request(new URL('/__public_read/' + hex, request.url), {method: 'GET'});
}

function reply(body, status, origin, extra) {
  const headers = {
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': origin === ALLOWED ? origin : 'null',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Expose-Headers': 'X-Cache, X-Snapshot-Age, X-Edge-Cache, X-Backend-Requests, X-Result-Requests, Server-Timing',
    'Vary': 'Origin',
    ...(extra || {})
  };
  return status === 204 ? new Response(null, {status, headers}) : Response.json(body, {status, headers});
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* 呼叫 Apps Script。可重送的方法最多三次（間隔 0.8、2.5 秒）；其他方法只在連線錯誤時再試一次。
   回 HTML（Apps Script 錯誤頁或 Google 登入頁）與 404／429／5xx 都算暫時性。總時間超過 50 秒就不再重試。 */
async function callBackend(env, body, retryable) {
  const started = Date.now();
  const delays = retryable ? [800, 2500] : [1200];
  let resultUrl = '', backendRequests = 0, resultRequests = 0, executionMs = 0, resultMs = 0;
  const finish = out => ({...out, backendRequests, resultRequests, executionMs, resultMs});
  const fetchResult = async () => {
    const at = Date.now(); resultRequests++;
    // Google 結果頁偶爾還會再轉址一次；GET 沒有 bridgeToken、管理密鑰或 body。
    try { return await fetch(resultUrl, {method: 'GET', redirect: 'follow',
      signal: AbortSignal.timeout(Math.max(1, Math.min(40000, 55000 - (Date.now() - started))))}); }
    finally { resultMs += Date.now() - at; }
  };
  let last = {status: 502, payload: {ok: false, error: 'backend-unavailable'}};
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    let transient = false, networkError = false;
    try {
      const fetchStarted = Date.now();
      let downstream;
      if (resultUrl) {
        downstream = await fetchResult();
      } else {
        backendRequests++;
        try { downstream = await fetch(env.GAS_WEBAPP_URL + '?action=site-bridge', {
          method: 'POST',
          headers: {'Content-Type': 'application/json; charset=utf-8'},
          body: JSON.stringify({...body, bridgeToken: env.SITE_BRIDGE_TOKEN}),
          redirect: 'manual',
          signal: AbortSignal.timeout(Math.max(1, Math.min(40000, 55000 - (Date.now() - started))))
        }); } finally { executionMs += Date.now() - fetchStarted; }
        // ContentService 的 302 表示程式已執行完。結果頁暫時失敗時只重取結果，
        // 不再 POST 重做讀表或寫入；密鑰永遠只送既有 /exec，不跟隨到結果網址。
        if ([302, 303].includes(downstream.status)) {
          let location;
          try { location = new URL(downstream.headers.get('Location') || '', env.GAS_WEBAPP_URL); }
          catch { return finish({status: 502, payload: {ok: false, error: 'backend-redirect-invalid'}}); }
          if (location.protocol !== 'https:' || location.hostname !== 'script.googleusercontent.com' || location.pathname !== '/macros/echo') {
            return finish({status: 502, payload: {ok: false, error: 'backend-redirect-invalid'}});
          }
          resultUrl = location.href;
          downstream = await fetchResult();
        }
      }
      const contentType = downstream.headers.get('content-type') || '';
      if (downstream.ok && /json/i.test(contentType)) {
        const payload = await downstream.json();
        return finish({status: payload.ok ? 200 : 502, payload});
      }
      transient = downstream.status === 404 || downstream.status === 429 || downstream.status >= 500 || (downstream.ok && !/json/i.test(contentType));
      last = {status: 502, payload: {ok: false, error: downstream.ok ? 'backend-not-json' : 'backend-http-' + downstream.status}};
    } catch {
      networkError = true;
      last = {status: 502, payload: {ok: false, error: 'backend-unavailable'}};
    }
    const canRetry = attempt < delays.length && Date.now() - started < 50000 && (networkError || ((retryable || resultUrl) && transient));
    if (!canRetry) break;
    await sleep(delays[attempt]);
  }
  return finish(last);
}


/* v107：常用公開唯讀資料的全域快照（Workers KV）。
   排程每 5 分鐘向原 Apps Script 取一次，內容有變才寫入；另寫一筆心跳記錄這輪各鍵的雜湊。
   讀取時心跳在 12 分鐘內、且雜湊對得上才使用快照；排程停擺或後端失敗超過 12 分鐘就回到即時讀取，
   不以舊資料掩蓋失敗。快照只收成功且非空的結果。 */
const SNAP_FRESH_MS = 12 * 60 * 1000;
const SNAP_BEAT = 'beat:v1';
const SNAPPED = new Set(['apiGetDashboard', 'apiListMailDates', 'apiGetMailContent', 'apiListRecordDates']);
const snapKey = key => 'snap:v1:' + key;

async function digest(text) {
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes).slice(0, 12), b => b.toString(16).padStart(2, '0')).join('');
}
function usable(payload) {
  if (!payload || !payload.ok) return false;
  const r = payload.result;
  if (r === null || r === undefined) return false;
  if (Array.isArray(r)) return r.length > 0;
  if (typeof r === 'object') return Object.keys(r).length > 0;
  return true;
}
async function readSnapshot(env, key) {
  if (!env.SNAP || !SNAPPED.has(key.split(':')[0])) return null;
  try {
    const [beat, entry] = await Promise.all([env.SNAP.get(SNAP_BEAT, 'json'), env.SNAP.get(snapKey(key), 'json')]);
    if (!beat || !entry || Date.now() - beat.at > SNAP_FRESH_MS) return null;
    if (!beat.hashes || beat.hashes[key] !== entry.hash) return null;
    return {payload: entry.payload, at: entry.at};
  } catch { return null; }
}
async function refreshSnapshots(env) {
  if (!env.SNAP) return {skipped: 'no-kv'};
  const old = await env.SNAP.get(SNAP_BEAT, 'json').catch(() => null);
  const hashes = {}, report = {};
  const take = async (method, args) => {
    const key = method + ':' + JSON.stringify(args);
    const out = await callBackend(env, {method, args}, true);
    if (!usable(out.payload)) { report[key] = out.payload?.error || 'empty'; return null; }
    const body = JSON.stringify(out.payload);
    const hash = await digest(method === 'apiGetDashboard' ? body.replace(/"updatedAt":"[^"]*"/, '') : body);
    hashes[key] = hash;
    if (!old || !old.hashes || old.hashes[key] !== hash) {
      await env.SNAP.put(snapKey(key), JSON.stringify({at: Date.now(), hash, payload: out.payload}), {expirationTtl: 86400});
      report[key] = 'written';
    } else report[key] = 'same';
    return out.payload.result;
  };
  const results = await Promise.allSettled([
    take('apiGetDashboard', []),
    take('apiListRecordDates', []),
    take('apiListMailDates', []).then(list => {
      const first = Array.isArray(list) && list[0];
      const date = first && (typeof first === 'string' ? first : first.date);
      return date ? take('apiGetMailContent', [date]) : null;
    })
  ]);
  results.forEach(r => { if (r.status === 'rejected') report.error = String(r.reason); });
  // 失敗的鍵不進心跳，讀取端自然回到即時讀取。
  await env.SNAP.put(SNAP_BEAT, JSON.stringify({at: Date.now(), hashes}), {expirationTtl: 86400});
  return report;
}

export default {
  async fetch(request, env, ctx) {
    const origin = request.headers.get('Origin') || '';
    const path = new URL(request.url).pathname;
    if (path === '/healthz') return reply({ok: true, ready: !!(env.GAS_WEBAPP_URL && env.SITE_BRIDGE_TOKEN), build: BUILD}, 200, origin);
    if (path !== '/api') return reply({ok: false, error: 'not-found'}, 404, origin);
    if (origin !== ALLOWED) return reply({ok: false, error: 'origin'}, 403, origin);
    if (request.method === 'OPTIONS') return reply({}, 204, origin);
    if (request.method !== 'POST') return reply({ok: false, error: 'method'}, 405, origin);
    if (!/^https:\/\/script\.google\.com\/macros\/s\/[^/]+\/exec$/.test(env.GAS_WEBAPP_URL || '') ||
        !env.SITE_BRIDGE_TOKEN || env.SITE_BRIDGE_TOKEN.length < 32) {
      return reply({ok: false, error: 'bridge-not-configured'}, 503, origin);
    }
    const raw = await request.text();
    if (!raw || new TextEncoder().encode(raw).length > 64000) return reply({ok: false, error: 'size'}, 413, origin);
    let body;
    try { body = JSON.parse(raw); } catch { return reply({ok: false, error: 'json'}, 400, origin); }
    // v90：上限原本是 5，後台「現有持股」存成本要帶 8 個參數（金鑰、代號、回合開始日、成本、備註、名稱、新代號、新開始日），
    // 在 Pages 後台按儲存一律回 method-not-allowed。最多的是 apiAdminSetHoldingCost 的 8 個。
    if (!METHODS.has(body.method) || !Array.isArray(body.args) || body.args.length > MAX_ARGS) {
      return reply({ok: false, error: 'method-not-allowed'}, 400, origin);
    }
    if (body.method.startsWith('apiAdmin') && (typeof body.args[0] !== 'string' || !body.args[0])) {
      return reply({ok: false, error: 'admin-key-required'}, 401, origin);
    }
    // 帶信箱的個股搜尋會記錄使用者查詢，不能當成公開唯讀查詢共用。
    const ttl = body.method === 'apiSearchStock' && body.args[1] ? 0 : READ_TTL[body.method];
    const key = ttl ? body.method + ':' + JSON.stringify(body.args) : '';
    const hit = key ? memo.get(key) : null;
    if (hit && Date.now() - hit.at < ttl * 1000) return reply(hit.body, 200, origin, {'X-Cache': 'hit'});
    const snap = key ? await readSnapshot(env, key) : null;
    if (snap) {
      remember(key, snap.payload, Math.max(snap.at, Date.now() - ttl * 1000 + 30000));
      return reply(snap.payload, 200, origin, {'X-Cache': 'snapshot', 'X-Snapshot-Age': String(Math.round((Date.now() - snap.at) / 1000))});
    }
    let edge = null, edgeStatus = 'unavailable';
    try {
      edge = await edgeKey(request, env, key);
      edgeStatus = edge ? 'miss' : 'bypass';
      const cached = edge && await caches.default.match(edge);
      if (cached) {
        const entry = await cached.json();
        if (entry.payload?.ok && Date.now() - entry.at < ttl * 1000) {
          // 保留原建立時間，不能每次命中把期限延長。
          remember(key, entry.payload, entry.at);
          return reply(entry.payload, 200, origin, {'X-Cache': 'edge-hit'});
        }
      }
    } catch (error) { edgeStatus = error.name || 'unavailable'; }
    const started = Date.now();
    let pending = key && inFlight.get(key);
    const shared = !!pending;
    if (!pending) {
      pending = callBackend(env, {method: body.method, args: body.args}, IDEMPOTENT.has(body.method));
      if (key) inFlight.set(key, pending);
    }
    let out;
    try { out = await pending; }
    finally { if (key && inFlight.get(key) === pending) inFlight.delete(key); }
    const timing = {'Server-Timing': 'backend;dur=' + (Date.now() - started) + ', gas_request;dur=' + out.executionMs + ', result;dur=' + out.resultMs,
      'X-Backend-Requests': String(out.backendRequests), 'X-Result-Requests': String(out.resultRequests)};
    if (out.payload && out.payload.ok) {
      if (key) remember(key, out.payload);
      if (edge) {
        const save = caches.default.put(edge, Response.json({at: Date.now(), payload: out.payload}, {
          headers: {'Cache-Control': 'public, max-age=' + ttl}
        })).catch(() => { console.warn('public read cache write unavailable'); });
        // 跨執行個體快取寫入不是顯示資料的前置條件，不再阻塞成功回應。
        if (ctx && typeof ctx.waitUntil === 'function') { ctx.waitUntil(save); edgeStatus = 'scheduled'; }
        else { await save; edgeStatus = 'stored'; }
      }
      return reply(out.payload, 200, origin, {...timing, 'X-Edge-Cache':edgeStatus, 'X-Cache': key ? shared ? 'shared' : 'miss' : 'bypass'});
    }
    return reply(out.payload, out.status, origin, timing);
  },
  async scheduled(_event, env, ctx) {
    if (!/^https:\/\/script\.google\.com\/macros\/s\/[^/]+\/exec$/.test(env.GAS_WEBAPP_URL || '') || !env.SITE_BRIDGE_TOKEN) return;
    ctx.waitUntil(refreshSnapshots(env).then(r => console.log('snapshot refresh', JSON.stringify(r))));
  }
};
export {refreshSnapshots, readSnapshot};
