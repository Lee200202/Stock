// Free Cloudflare Worker: public Pages frontend -> existing Apps Script backend.
// The bridge token stays server-side. Admin methods still require the existing admin key.
const ALLOWED = 'https://lee200202.github.io';
const BUILD = 'site-api-v90';
const MAX_ARGS = 8;
const ADMIN_METHODS = ('apiAdminCancelCrawl apiAdminCancelDaySync apiAdminCancelFix ' +
  'apiAdminCancelFullFix apiAdminCancelJob apiAdminCancelRefresh apiAdminCancelSmsJob ' +
  'apiAdminChainState apiAdminCrawlState apiAdminCrawlTranscript apiAdminDayRows ' +
  'apiAdminDaySyncState apiAdminDecisions apiAdminDeleteRow apiAdminDeleteSms ' +
  'apiAdminDeleteSmsBatch apiAdminDispatch apiAdminFixState apiAdminFullFixState ' +
  'apiAdminHeldList apiAdminHoldToday apiAdminInstallSectorCatchup apiAdminJobStatus ' +
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
  'apiGetMarketOverview', 'apiGetMemberSms', 'apiGetPerformanceSeries',
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
  apiGetDashboard: 60, apiGetMarketOverview: 60, apiGetQuotesFor: 60, apiGetHoldingsTracker: 120,
  apiGetStockSummary: 120, apiSearchStock: 120, apiListRecordDates: 300, apiListMailDates: 300,
  apiGetMailContent: 300, apiSearchByDate: 300, apiGetCandlesBundle: 300, apiGetPerformanceSeries: 600,
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

function remember(key, body) {
  if (memo.has(key)) memo.delete(key);
  memo.set(key, {at: Date.now(), body});
  while (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value);
}

function reply(body, status, origin, extra) {
  const headers = {
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': origin === ALLOWED ? origin : 'null',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Expose-Headers': 'X-Cache, Server-Timing',
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
  let last = {status: 502, payload: {ok: false, error: 'backend-unavailable'}};
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    let transient = false, networkError = false;
    try {
      const downstream = await fetch(env.GAS_WEBAPP_URL + '?action=site-bridge', {
        method: 'POST',
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: JSON.stringify({...body, bridgeToken: env.SITE_BRIDGE_TOKEN}),
        redirect: 'follow',
        signal: AbortSignal.timeout(Math.max(1, Math.min(40000, 55000 - (Date.now() - started))))
      });
      const contentType = downstream.headers.get('content-type') || '';
      if (downstream.ok && /json/i.test(contentType)) {
        const payload = await downstream.json();
        return {status: payload.ok ? 200 : 502, payload};
      }
      transient = downstream.status === 404 || downstream.status === 429 || downstream.status >= 500 || (downstream.ok && !/json/i.test(contentType));
      last = {status: 502, payload: {ok: false, error: downstream.ok ? 'backend-not-json' : 'backend-http-' + downstream.status}};
    } catch {
      networkError = true;
      last = {status: 502, payload: {ok: false, error: 'backend-unavailable'}};
    }
    const canRetry = attempt < delays.length && Date.now() - started < 50000 && (networkError || (retryable && transient));
    if (!canRetry) break;
    await sleep(delays[attempt]);
  }
  return last;
}

export default {
  async fetch(request, env) {
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
    const timing = {'Server-Timing': 'backend;dur=' + (Date.now() - started)};
    if (out.payload && out.payload.ok) {
      if (key) remember(key, out.payload);
      return reply(out.payload, 200, origin, {...timing, 'X-Cache': key ? shared ? 'shared' : 'miss' : 'bypass'});
    }
    return reply(out.payload, out.status, origin, timing);
  }
};
