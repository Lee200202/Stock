/* The original Apps Script frontend is unchanged. This replaces only the
   google.script.run transport when that frontend is served by GitHub Pages. */
(function () {
  'use strict';
  const endpoint = '__SITE_API_URL__';
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

  function runner(success, failure) {
    return new Proxy({}, {
      get(_target, name) {
        if (name === 'withSuccessHandler') return callback => runner(callback, failure);
        if (name === 'withFailureHandler') return callback => runner(success, callback);
        if (typeof name !== 'string' || !/^api[A-Z][A-Za-z0-9_]*$/.test(name)) return undefined;
        return (...args) => {
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), 65000);
          fetch(endpoint, {
            method: 'POST',
            mode: 'cors',
            cache: 'no-store',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({method: name, args}),
            signal: controller.signal
          }).then(async response => {
            const payload = await response.json();
            if (!response.ok || !payload.ok) throw new Error(payload.error || `HTTP ${response.status}`);
            if (success) success(payload.result);
          }).catch(error => {
            if (failure) failure(error);
            else console.error('網站資料呼叫失敗', name, error);
          }).finally(() => clearTimeout(timer));
        };
      }
    });
  }
  window.google = window.google || {};
  window.google.script = window.google.script || {};
  window.google.script.run = runner(null, null);
})();
