/* The original Apps Script frontend is unchanged. This replaces only the
   google.script.run transport when that frontend is served by GitHub Pages. */
(function () {
  'use strict';
  const endpoint = '__SITE_API_URL__';
  // 讓畫面知道現在由 GitHub Pages 提供：載入失敗的提示不再叫人處理 Google 帳號（那只發生在 Apps Script 網址）。
  try { document.documentElement.dataset.host = 'pages'; } catch (e) {}
  const params = new URLSearchParams(location.search);
  const routePage = params.get('page');
  const routeAction = params.get('action');
  if (routePage === 'admin' || routePage === 'admin-legacy') {
    location.replace(new URL(routePage === 'admin' ? 'admin.html' : 'admin-legacy.html', location.href).href);
    return;
  }
  if (routePage === 'unsubscribe' || routeAction === 'unsubscribe') {
    location.replace(new URL('unsubscribe.html' + location.search, location.href).href);
    return;
  }
  if (/^(overview|market|tracker|perf|subscribe|mail|sms|tx|tech)$/.test(params.get('tab') || '')) {
    document.body.dataset.tab = params.get('tab');
  }
  if (/^\d{4,6}[A-Z]?$/.test(params.get('stock') || '')) {
    document.body.dataset.stock = params.get('stock');
  }
  // GAS 的同站 ?page=... 在靜態主機要對應到原樣組裝的 HTML 頁。
  document.addEventListener('click', event => {
    const link = event.target.closest?.('a[href]');
    if (!link) return;
    const target = new URL(link.href, location.href);
    if (target.origin !== location.origin || target.pathname !== new URL('./', location.href).pathname) return;
    const page = target.searchParams.get('page');
    const action = target.searchParams.get('action');
    if (page === 'admin' || page === 'admin-legacy') {
      link.href = new URL(page === 'admin' ? 'admin.html' : 'admin-legacy.html', location.href).href;
    } else if (page === 'unsubscribe' || action === 'unsubscribe') {
      link.href = new URL('unsubscribe.html' + target.search, location.href).href;
    }
  }, true);

  // 同頁不同元件若同時要同一份唯讀資料，共用正在進行的請求與短暫結果。
  // 管理／訂閱／寫入 API 不快取；主頁重新整理仍會向後端取最新資料。
  const sharedReads = new Set(['apiGetDashboard', 'apiGetHoldingsTracker', 'apiGetMarketOverview', 'apiListRecordDates', 'apiGetLineEntry', 'apiGetQuotesFor', 'apiGetStockSummary']);
  const inFlight = new Map();
  const recentReads = new Map();
  /* v87（2026/09/30）：後端偶爾回 404／逾時，先前一次失敗就整頁顯示「網站資料暫時無法載入」。
     唯讀與可重送的請求失敗時自動再試兩次；還是失敗，就用這台瀏覽器上次成功的同一份資料（12 小時內），
     畫面上的「更新於」時間照資料本身顯示，看得出是舊的。寫入類請求不重送、不用舊資料。 */
  const retryReads = new Set([...sharedReads, 'apiListMailDates', 'apiGetMailContent', 'apiGetTechStats',
    'apiGetPerformanceSeries', 'apiSearchByDate', 'apiSearchStock', 'apiGetCandlesBundle', 'apiGetStockFundamentals',
    'apiListTranscriptDates', 'apiGetTranscript', 'apiSuggestCodes', 'apiLookupSubscription', 'apiUnsubscribeConfirm']);
  const keepLocal = new Set(['apiGetDashboard', 'apiGetMarketOverview', 'apiGetHoldingsTracker', 'apiListRecordDates',
    'apiListMailDates', 'apiGetTechStats', 'apiGetPerformanceSeries', 'apiGetLineEntry']);
  const LOCAL_PREFIX = 'zzLastGood:';
  function saveLocal(name, args, value) {
    if (!keepLocal.has(name)) return;
    try {
      const text = JSON.stringify({at: Date.now(), value});
      if (text.length < 400000) localStorage.setItem(LOCAL_PREFIX + name + ':' + JSON.stringify(args), text);
    } catch (e) {}
  }
  function loadLocal(name, args) {
    if (!keepLocal.has(name)) return null;
    try {
      const hit = JSON.parse(localStorage.getItem(LOCAL_PREFIX + name + ':' + JSON.stringify(args)) || 'null');
      return hit && Date.now() - hit.at < 12 * 3600 * 1000 ? hit : null;
    } catch (e) { return null; }
  }
  const wait = ms => new Promise(r => setTimeout(r, ms));
  async function requestOnce(name, args) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 75000);
    try {
      const response = await fetch(endpoint, {
        method: 'POST', mode: 'cors', cache: 'no-store',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({method: name, args}), signal: controller.signal
      });
      let payload = null;
      try { payload = await response.json(); } catch (e) { payload = null; }
      if (!response.ok || !payload || !payload.ok) {
        const error = new Error((payload && payload.error) || `HTTP ${response.status}`);
        // 400／401／403／413 是請求本身不被接受，重送也一樣；5xx 與連線錯誤才重試。
        error.retry = !response.ok ? response.status >= 500 : false;
        throw error;
      }
      return payload.result;
    } catch (error) {
      if (error && error.retry === undefined) error.retry = true;   // 逾時、網路中斷
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function callApi(name, args) {
    const key = sharedReads.has(name) ? `${name}:${JSON.stringify(args)}` : null;
    const cached = key && recentReads.get(key);
    if (cached && Date.now() - cached.at < 60000) return cached.value;
    if (key && inFlight.has(key)) return inFlight.get(key);
    const promise = (async () => {
      try {
        let result, lastError;
        const delays = retryReads.has(name) ? [1500, 4000] : [];
        for (let attempt = 0; attempt <= delays.length; attempt++) {
          try { result = await requestOnce(name, args); lastError = null; break; }
          catch (error) {
            lastError = error;
            if (!error.retry || attempt === delays.length) break;
            await wait(delays[attempt]);
          }
        }
        if (lastError) {
          const local = loadLocal(name, args);
          if (local) { window.__siteStale = true; return local.value; }
          throw lastError;
        }
        const payload = {result};
        saveLocal(name, args, payload.result);
        if (key) recentReads.set(key, {at: Date.now(), value: payload.result});
        // 若 dashboard 一次打包了持股追蹤與 LINE 入口，順手填入快取，省下後續輪詢 roundtrip
        if (name === 'apiGetDashboard' && payload.result) {
          if (payload.result.tracker) recentReads.set('apiGetHoldingsTracker:[]', {at: Date.now(), value: payload.result.tracker});
          if (payload.result.lineEntry) recentReads.set('apiGetLineEntry:[]', {at: Date.now(), value: payload.result.lineEntry});
        }
        return payload.result;
      } finally { if (key) inFlight.delete(key); }
    })();
    if (key) inFlight.set(key, promise);
    return promise;
  }

  function runner(success, failure) {
    return new Proxy({}, {
      get(_target, name) {
        if (name === 'withSuccessHandler') return callback => runner(callback, failure);
        if (name === 'withFailureHandler') return callback => runner(success, callback);
        if (typeof name !== 'string' || !/^api[A-Z][A-Za-z0-9_]*$/.test(name)) return undefined;
        return (...args) => {
          callApi(name, args).then(result => {
            if (success) success(result);
          }).catch(error => {
            if (failure) failure(error);
            else console.error('網站資料呼叫失敗', name, error);
          });
        };
      }
    });
  }
  window.google = window.google || {};
  window.google.script = window.google.script || {};
  window.google.script.run = runner(null, null);
})();
