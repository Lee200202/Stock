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
  if (/^(overview|tracker|battle|perf|subscribe|mail|sms|tx|tech)$/.test(params.get('tab') || '')) {
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
  const sharedReads = new Set(['apiGetDashboard', 'apiGetHoldingsTracker', 'apiGetMarketOverview', 'apiListRecordDates', 'apiGetLineEntry', 'apiGetQuotesFor', 'apiGetStockSummary', 'apiGetBattleData']);
  const inFlight = new Map();
  const recentReads = new Map();
  // v88：只呈現本次讀取結果；失敗照實通知，不回放 localStorage 的舊總覽。
  const retryReads = new Set([...sharedReads, 'apiListMailDates', 'apiGetMailContent', 'apiGetTechStats',
    'apiGetPerformanceSeries', 'apiSearchByDate', 'apiSearchStock', 'apiGetCandlesBundle', 'apiGetStockFundamentals',
    'apiListTranscriptDates', 'apiGetTranscript', 'apiSuggestCodes', 'apiLookupSubscription', 'apiUnsubscribeConfirm']);
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
        // Worker 已完成後端重試時，不再由瀏覽器重跑整組，避免三層重試拖成數分鐘。
        const backendHandled = response.headers && response.headers.get('Server-Timing');
        error.retry = !response.ok ? response.status >= 500 && !backendHandled : false;
        throw error;
      }
      return payload.result;
    } catch (error) {
      if (error && error.retry === undefined) error.retry = true;   // 逾時、網路中斷
      throw error;
    } finally { clearTimeout(timer); }
  }
  /* 首頁總覽提早出發（v138）。頁面開頭有一小段程式，在整頁（將近 900KB）還沒解析完之前就先送出同一個
     apiGetDashboard 請求；畫面程式第一次要總覽時直接接這個已經在路上的請求，省掉一秒多的等待。
     這不是快取：它是這一次開頁當下送出的即時請求（no-store），只用一次，超過 15 秒沒被接走就作廢；
     失敗、逾時或等超過 6 秒沒有結果時，照原本的流程重新送，不會顯示任何先前保存的內容。 */
  function takeEarlyDashboard(name, args) {
    const early = window.__earlyDashboard;
    if (name !== 'apiGetDashboard' || args.length || !early || !early.promise) return null;
    window.__earlyDashboard = null;
    if (Date.now() - early.at >= 15000) return null;
    return Promise.race([early.promise, new Promise((_, reject) => setTimeout(() => reject(new Error('early-timeout')), 6000))]);
  }
  async function callApi(name, args) {
    const key = sharedReads.has(name) ? `${name}:${JSON.stringify(args)}` : null;
    const cached = key && recentReads.get(key);
    if (cached && Date.now() - cached.at < 60000) return cached.value;
    if (key && inFlight.has(key)) return inFlight.get(key);
    const early = takeEarlyDashboard(name, args);
    const promise = (async () => {
      try {
        let result, lastError;
        const delays = retryReads.has(name) ? [1500, 4000] : [];
        for (let attempt = 0; attempt <= delays.length; attempt++) {
          try {
            // 提早送的那一次沒成功（連線中斷、後端錯誤、等太久）：不算一次重試，直接照原本的流程重新送。
            if (attempt === 0 && early) { try { result = await early; lastError = null; break; } catch (e) { /* 往下重新送 */ } }
            result = await requestOnce(name, args); lastError = null; break;
          }
          catch (error) {
            lastError = error;
            if (!error.retry || attempt === delays.length) break;
            await wait(delays[attempt]);
          }
        }
        if (lastError) throw lastError;
        const payload = {result};
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
          // 與 Apps Script 原生 google.script.run 相同：成功回呼裡的例外不算請求失敗（v88）。
          // 先前用 .then().catch()，畫面程式一出錯就被當成連線失敗，首頁因此一直顯示「資料讀取失敗」。
          callApi(name, args).then(result => {
            if (!success) return;
            try { success(result); }
            catch (error) { console.error('畫面處理資料時出錯', name, error); }
          }, error => {
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
