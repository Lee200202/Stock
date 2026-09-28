// Free Cloudflare Worker: public Pages frontend -> existing Apps Script backend.
// No credentials are sent to the browser. Admin functions are never proxied.
const ALLOWED = 'https://lee200202.github.io';
const ADMIN_METHODS = ('apiAdminCancelCrawl apiAdminCancelDaySync apiAdminCancelFix ' +
  'apiAdminCancelFullFix apiAdminCancelJob apiAdminCancelRefresh apiAdminCancelSmsJob ' +
  'apiAdminChainState apiAdminCrawlState apiAdminCrawlTranscript apiAdminDayRows ' +
  'apiAdminDaySyncState apiAdminDecisions apiAdminDeleteRow apiAdminDeleteSms ' +
  'apiAdminDeleteSmsBatch apiAdminDispatch apiAdminFixState apiAdminFullFixState ' +
  'apiAdminHeldList apiAdminHoldToday apiAdminInstallSectorCatchup apiAdminJobStatus ' +
  'apiAdminKCoverage apiAdminLineLookup apiAdminLineRetry apiAdminLineSaveConfig ' +
  'apiAdminLineStatus apiAdminListSms apiAdminLogin apiAdminManualDays ' +
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

function reply(body, status, origin) {
  const headers = {
    'Cache-Control': 'no-store',
    'Access-Control-Allow-Origin': origin === ALLOWED ? origin : 'null',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Vary': 'Origin'
  };
  return status === 204 ? new Response(null, {status, headers}) : Response.json(body, {status, headers});
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const path = new URL(request.url).pathname;
    if (path === '/healthz') return reply({ok: true, ready: !!(env.GAS_WEBAPP_URL && env.SITE_BRIDGE_TOKEN)}, 200, origin);
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
    if (!METHODS.has(body.method) || !Array.isArray(body.args) || body.args.length > 5) {
      return reply({ok: false, error: 'method-not-allowed'}, 400, origin);
    }
    if (body.method.startsWith('apiAdmin') && (typeof body.args[0] !== 'string' || !body.args[0])) {
      return reply({ok: false, error: 'admin-key-required'}, 401, origin);
    }
    try {
      const downstream = await fetch(env.GAS_WEBAPP_URL + '?action=site-bridge', {
        method: 'POST',
        headers: {'Content-Type': 'application/json; charset=utf-8'},
        body: JSON.stringify({...body, bridgeToken: env.SITE_BRIDGE_TOKEN}),
        redirect: 'follow',
        signal: AbortSignal.timeout(60000)
      });
      if (!downstream.ok) return reply({ok: false, error: 'backend-http-' + downstream.status}, 502, origin);
      const contentType = downstream.headers.get('content-type') || '';
      if (!/json/i.test(contentType)) return reply({ok: false, error: 'backend-not-json'}, 502, origin);
      const result = await downstream.json();
      return reply(result, result.ok ? 200 : 502, origin);
    } catch {
      return reply({ok: false, error: 'backend-unavailable'}, 502, origin);
    }
  }
};
