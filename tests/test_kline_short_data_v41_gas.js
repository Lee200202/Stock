// v41、v42（2026/09/16）：K 線讀取加速（逐檔 JSON 快取一次寫入多筆、三種週期一次取回、前端暫存），
// 資料少的股票怎麼呈現（區間按鈕、K 棒寬度、副圖），MACD 正確起算，停用 repairDailyKVolume。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const dir = path.resolve(__dirname, '..', 'apps-script');
const read = f => fs.readFileSync(path.join(dir, f), 'utf8');
const pad = n => String(n).padStart(2, '0');

// ================================================================== 後端
function backend(opt) {
  opt = opt || {};
  const store = {}, calls = { putAll: [], removeAll: [], loads: 0, hourLoads: 0 };
  const cache = {
    get: k => (k in store ? store[k] : null),
    put: (k, v) => { store[k] = v; },
    putAll: (o, ttl) => { calls.putAll.push({ keys: Object.keys(o), ttl }); Object.assign(store, o); },
    remove: k => { delete store[k]; },
    removeAll: ks => { calls.removeAll.push(ks.slice()); ks.forEach(k => delete store[k]); }
  };
  const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : k === 'then' ? undefined : stub()), apply: () => stub() });
  const now = opt.now || new Date(2026, 8, 16, 10, 30);     // 週三 10:30
  class FDate extends Date { constructor(...a) { a.length ? super(...a) : super(now.getTime()); } static now() { return now.getTime(); } }
  const fmt = (d, tz, f) => f.replace('yyyy', d.getFullYear()).replace('MM', pad(d.getMonth() + 1)).replace('dd', pad(d.getDate()))
    .replace('HH', pad(d.getHours())).replace('mm', pad(d.getMinutes())).replace('ss', pad(d.getSeconds())).replace('u', String(d.getDay() || 7));
  const ctx = vm.createContext({ console, JSON, Math, Date: FDate, String, Number, Object, Array, RegExp, isNaN, isFinite, parseInt, parseFloat, encodeURIComponent, Error, Infinity,
    Logger: { log: () => {} },
    CacheService: { getScriptCache: () => cache },
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => null, setProperty: () => {}, deleteProperty: () => {} }) },
    Utilities: { formatDate: fmt, sleep: () => {} },
    UrlFetchApp: stub(), SpreadsheetApp: stub(), LockService: stub(), ScriptApp: stub(), MailApp: stub(), Session: stub() });
  for (const f of ['Presentationquality.gs', 'Quoteservice.gs', 'Cachebuilder.gs']) vm.runInContext(read(f), ctx, { filename: f });
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', CACHE: cache,
    hasFugle_: () => opt.fugle !== false,
    getQuoteCache: () => ({}),
    loadAllDailyK_: () => { calls.loads++; return opt.daily || {}; },
    loadAllHourly_: () => { calls.hourLoads++; return opt.hourly || {}; },
    fugleHourly_: () => { calls.live = (calls.live || 0) + 1; return []; },
    getSheet_: () => { throw new Error('不應讀寫試算表'); }
  });
  return { ctx, store, calls };
}
const bar = (date, c, v) => ({ date, open: c, high: c + 1, low: c - 1, close: c, volume: v === undefined ? 1000 : v });
const days = (n, start) => { const out = []; const d = new Date(start || '2026-01-05'); while (out.length < n) { if (d.getDay() && d.getDay() < 6) out.push(`${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`); d.setDate(d.getDate() + 1); } return out; };

{
  // 精簡 JSON 來回不失真
  const { ctx } = backend();
  const rows = [bar('2026/09/15', 29.85, 70118), bar('2026/09/16', 30.2, 0)];
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.kcDecode_(ctx.kcEncode_(rows)))), rows);
  assert.strictEqual(ctx.kcEncode_(rows), '[["2026/09/15",29.85,30.85,28.85,29.85,70118],["2026/09/16",30.2,31.2,29.2,30.2,0]]');
}
{
  // 冷讀：整張讀一次，所有代號一次寫進快取；之後任何一檔都不再讀表
  const daily = { '2201': days(5).map((d, i) => bar(d, 30 + i)), '2385': days(3).map(d => bar(d, 104)), '1506': [bar('2025/11/03', 12.35, 70118)] };
  const { ctx, store, calls } = backend({ daily });
  const a = ctx.getCachedDailyK('2201');
  assert.strictEqual(a.length, 5); assert.strictEqual(calls.loads, 1);
  assert.strictEqual(calls.putAll.length, 1);
  assert.deepStrictEqual(calls.putAll[0].keys.sort(), ['dk2_1506', 'dk2_2201', 'dk2_2385']);
  assert.strictEqual(calls.putAll[0].ttl, 21600, '快取最長 6 小時');
  const b = ctx.getCachedDailyK('2385'); const c = ctx.getCachedDailyK('1506');
  assert.strictEqual(calls.loads, 1, '其他代號直接從快取取');
  assert.strictEqual(b.length, 3); assert.strictEqual(c[0].volume, 70118);
  // 沒有資料的代號也存一個空陣列，不會每點一次就整張重讀
  assert.deepStrictEqual(Array.from(ctx.getCachedDailyK('9999')), []);
  assert.strictEqual(store['dk2_9999'], '[]');
  ctx.getCachedDailyK('9999');
  assert.strictEqual(calls.loads, 2, '第一次查 9999 讀一次，之後不再讀');
  // 60 分 K 同一套
  const h = backend({ hourly: { '2201': [bar('2026/09/03 09:00', 31.5, 906)] } });
  h.ctx.getHourlyCandles_('2201'); h.ctx.getHourlyCandles_('2201');
  assert.strictEqual(h.calls.hourLoads, 1);
  assert(h.store['hk2_2201']);
}
{
  // 代號太多（快取總數上限 1,000 個）：只寫被點的那一檔；單一項目超過 100 KB 不寫
  const daily = {}; for (let i = 0; i < 450; i++) daily[String(3000 + i)] = [bar('2026/09/16', 10)];
  const { ctx, calls } = backend({ daily });
  ctx.getCachedDailyK('3001');
  assert.deepStrictEqual(Array.from(calls.putAll[0].keys), ['dk2_3001']);
  const big = backend({ daily: { '2330': days(3000, '2010-01-04').map(d => bar(d, 1000.55, 123456789)) } });
  big.ctx.getCachedDailyK('2330');
  assert.strictEqual(big.calls.putAll.length, 0, '超過 100 KB 的不寫進快取');
  // 批次：每次 putAll 最多 100 筆
  const many = {}; for (let i = 0; i < 250; i++) many[String(4000 + i)] = [bar('2026/09/16', 10)];
  const m = backend({ daily: many });
  m.ctx.getCachedDailyK('4000');
  assert.deepStrictEqual(m.calls.putAll.map(p => p.keys.length), [100, 100, 50]);
}
{
  // 資料寫入的地方都會刪掉那幾檔的快取
  const cb = read('Cachebuilder.gs'), qs = read('Quoteservice.gs');
  const body = (src, name) => { const a = src.indexOf('function ' + name + '('); return src.slice(a, src.indexOf('\nfunction ', a + 10)); };
  assert(/kcDrop_\('dk2_'/.test(body(cb, 'writeDailyKRows_')));
  assert(/kcDrop_\('dk2_', \[code\]\)/.test(body(cb, 'fetchCodeOnDemand')));
  assert(/kcDrop_\("dk2_", ok\)/.test(body(cb, 'fillMissingDailyK_')));
  assert(/kcDrop_\('hk2_', Object\.keys\(byCode\)\)/.test(body(cb, 'aggregateSnapshotJob')));
  assert(/kcDrop_\('hk2_', Object\.keys\(fetched\)\)/.test(body(qs, 'aggregateHourlyJob')));
  assert(/warmKCaches_\(\)/.test(body(cb, 'afterDailyKDoneJob')), '補日K整輪完成後預熱');
  assert(/dueEvery_\('kcacheWarm', 330\)\) \{ safe_\('warmKCaches_', warmKCaches_\)/.test(read('Setup.gs')), '每 5.5 小時預熱');
  const { ctx, calls } = backend();
  ctx.kcDrop_('dk2_', ['2201', ' 2385']);
  assert.deepStrictEqual(Array.from(calls.removeAll[0]), ['dk2_2201', 'dk2_2385']);
}
{
  // 一次取回三種週期，與逐一呼叫 getCandles 的結果相同
  const daily = { '2201': days(12).map((d, i) => bar(d, 30 + i, 1000 * (i + 1))) };
  const hourly = { '2201': ['09:00', '10:00', '11:00', '12:00', '13:00'].map(t => bar('2026/09/03 ' + t, 31, 900)) };
  const { ctx } = backend({ daily, hourly, fugle: false });
  const b = ctx.getCandlesBundle('2201');
  const plain = x => JSON.parse(JSON.stringify(x));
  assert.deepStrictEqual(plain(b.day), plain(ctx.getCandles('2201', 'day')));
  assert.deepStrictEqual(plain(b.week), plain(ctx.getCandles('2201', 'week')));
  assert.deepStrictEqual(plain(b.hour), plain(ctx.getCandles('2201', 'hour')));
  assert.strictEqual(b.day[0].volume, 1, '日K量仍是張');
  assert(/function apiGetCandlesBundle\(code\) \{\s*return getCandlesBundle\(code\);/.test(read('API.gs')));
}
{
  // 60 分 K 只在需要時才向富果要當天的部分
  const has13 = [bar('2026/09/16 13:00', 30)];
  const at = (y, mo, d, h, mi) => new Date(y, mo - 1, d, h, mi);
  assert.strictEqual(backend({ now: at(2026, 9, 16, 10, 30) }).ctx.hourLiveMergeNeeded_([]), true, '平日盤中、還沒落地');
  assert.strictEqual(backend({ now: at(2026, 9, 16, 18, 0) }).ctx.hourLiveMergeNeeded_(has13), false, '當天 13:00 那根已落地');
  assert.strictEqual(backend({ now: at(2026, 9, 16, 18, 0) }).ctx.hourLiveMergeNeeded_([]), true, '收盤後還沒落地');
  assert.strictEqual(backend({ now: at(2026, 9, 16, 8, 30) }).ctx.hourLiveMergeNeeded_([]), false, '開盤前');
  assert.strictEqual(backend({ now: at(2026, 9, 19, 11, 0) }).ctx.hourLiveMergeNeeded_([]), false, '週六');
  assert.strictEqual(backend({ now: at(2026, 9, 16, 10, 30), fugle: false }).ctx.hourLiveMergeNeeded_([]), false);
  const e = backend({ now: at(2026, 9, 19, 11, 0), hourly: { '2201': has13 } });
  e.ctx.getCandles('2201', 'hour');
  assert(!e.calls.live, '週末開 60 分 K 不再打富果');
}
{
  // repairDailyKVolume 停用：不讀不寫試算表
  const { ctx } = backend();
  assert.deepStrictEqual(Object.assign({}, ctx.repairDailyKVolume()), { disabled: true });
  assert.deepStrictEqual(Object.assign({}, ctx.repairDailyKVolume(true)), { disabled: true });
  assert(!/Math\.round\(x\.v \/ 1000\)/.test(read('Quoteservice.gs')), '把股改成四捨五入張數的程式碼已移除');
}

// ================================================================== 前端
const js = read('JavaScript.html');
function fnSource(name) {
  const at = js.indexOf('function ' + name + '(');
  assert(at >= 0, '找不到 ' + name);
  let i = js.indexOf('{', at), depth = 0;
  for (; i < js.length; i++) { if (js[i] === '{') depth++; else if (js[i] === '}' && !--depth) break; }
  return js.slice(at, i + 1);
}
function front(extra) {
  const els = {};
  const $ = id => (els[id] = els[id] || { id, hidden: false, textContent: '', clientWidth: 486, classList: { contains: () => true } });
  const buttons = ['1', '3', '6', '12', 'all'].map(r => ({ dataset: { krange: r }, disabled: false, title: '', attrs: {}, setAttribute(k, v) { this.attrs[k] = v; } }));
  const caps = { '#macdCap span': { textContent: '' }, '#kdCap span': { textContent: '' } };
  const ctx = vm.createContext(Object.assign({ Math, String, Number, Date, isFinite, Array, Object, JSON,
    $, document: { querySelectorAll: () => buttons, querySelector: s => caps[s] || null },
    css: () => '#000', period: 'day', rawData: [], chartRange: 'all', panes: { macd: true, kd: true },
    macdMap: {}, kdMap: {} }, extra || {}));
  vm.runInContext(['toTime', 'ema', 'computeMacd', 'rangeStartIndex', 'rangeUsable', 'effectiveRange', 'updateRangeButtons',
    'applyChartRange', 'updateIndicatorCaps', 'paneShown', 'kCoverageNote'].map(fnSource).join('\n'), ctx);
  return { ctx, els, buttons, caps };
}
function fakeChart(width) {
  const c = { opts: [], ranges: [] };
  c.applyOptions = o => c.opts.push(o);
  c.timeScale = () => ({ width: () => width, setVisibleLogicalRange: r => c.ranges.push(r) });
  return c;
}
const series = (n, first, stepDays) => { const out = []; const d = new Date(first); for (let i = 0; i < n; i++) { out.push({ date: `${d.getFullYear()}/${pad(d.getMonth() + 1)}/${pad(d.getDate())}`, open: 10, high: 11, low: 9, close: 10 + Math.sin(i / 3) * 2 + i * 0.05, volume: 100 }); d.setDate(d.getDate() + (stepDays || 1)); } return out; };

{
  // EMA（v42）：以第一根的值當起點，每一根都有值；null 跳過，起點取第一個非 null
  const { ctx } = front();
  const e = ctx.ema([1, 2, 3, 4, 5, 6, 7], 5);
  const k = 2 / 6;
  assert.strictEqual(e[0], 1);
  assert(Math.abs(e[1] - (2 * k + 1 * (1 - k))) < 1e-12);
  const e2 = Array.from(ctx.ema([null, null, 2, 4], 2));
  assert.deepStrictEqual(e2.slice(0, 3), [null, null, 2]);
  assert(Math.abs(e2[3] - (4 * (2 / 3) + 2 * (1 / 3))) < 1e-12);
  // MACD：每一根都有值（週K 一年 53 根，左半邊不再空白），數值與獨立算法一致
  const short = ctx.computeMacd(series(15, '2026-09-03'));
  assert(short.m.every(p => 'value' in p) && short.s.every(p => 'value' in p) && short.h.every(p => 'value' in p));
  assert.strictEqual(Object.keys(ctx.macdMap).length, 15);
  const data = series(53, '2025-09-19', 7);
  const md = ctx.computeMacd(data);
  assert.strictEqual(md.m.findIndex(p => 'value' in p), 0);
  assert.strictEqual(md.h.findIndex(p => 'value' in p), 0);
  const closes = data.map(r => r.close);
  const refEma = (v, n) => { let prev = null; const kk = 2 / (n + 1); return v.map(x => (prev = prev == null ? x : x * kk + prev * (1 - kk))); };
  const e12 = refEma(closes, 12), e26 = refEma(closes, 26);
  const dif = closes.map((_, i) => e12[i] - e26[i]);
  const dea = refEma(dif, 9);
  for (const i of [0, 1, 25, 33, 52]) {
    assert(Math.abs(md.m[i].value - dif[i]) < 1e-12 && Math.abs(md.s[i].value - dea[i]) < 1e-12, 'MACD 第 ' + i + ' 根');
    assert(Math.abs(md.h[i].value - (dif[i] - dea[i]) * 2) < 1e-12);
  }
  assert.strictEqual(md.m[0].value, 0, '第一根 EMA12＝EMA26＝收盤價，DIF＝0');
}
{
  // 區間按鈕：資料不夠長的停用；選好的區間記著，換到資料夠長的股票自動恢復
  const f = front();
  f.ctx.rawData = series(15, '2026-09-03');   // 60 分 K 那種只有幾天的
  f.ctx.chartRange = '3';
  f.ctx.updateRangeButtons();
  const state = () => f.buttons.map(b => b.dataset.krange + (b.disabled ? '×' : '') + (b.attrs['aria-pressed'] === 'true' ? '*' : '')).join(' ');
  assert.strictEqual(state(), '1× 3× 6× 12× all*');
  assert(/這一檔的資料從 2026\/09\/03 開始，不滿 3 個月，與「全部」相同/.test(f.buttons[1].title));
  assert(/不滿一年/.test(f.buttons[3].title));
  f.ctx.rawData = series(400, '2025-05-01');   // 換回資料超過一年的股票（逐日，400 天）
  f.ctx.updateRangeButtons();
  assert.strictEqual(state(), '1 3* 6 12 all', '偏好的 3 個月恢復');
  f.ctx.rawData = series(200, '2026-03-01');
  f.ctx.updateRangeButtons();
  assert.strictEqual(state(), '1 3* 6 12× all');
}
{
  // K 棒寬度（v42）：不論資料多少一律填滿寬度；邊界鎖在資料兩端；滾輪縮放、拖曳、觸控都開著
  const charts = { chart: fakeChart(1730), volChart: fakeChart(1730), macdChart: fakeChart(1730), kdChart: fakeChart(1730) };
  const f = front(charts);
  const zoomable = o => o.timeScale.fixLeftEdge === true && o.timeScale.fixRightEdge === true &&
    o.handleScale.mouseWheel === true && o.handleScale.pinch === true && o.handleScroll.mouseWheel === true && o.handleScroll.pressedMouseMove === true;
  // 60 分 K 只有 15 根：照樣填滿
  f.ctx.rawData = series(15, '2026-09-03');
  f.ctx.applyChartRange();
  const r = charts.chart.ranges.pop();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(r)), { from: -0.5, to: 14.5 }, '15 根也填滿，不靠右留白');
  assert(zoomable(charts.chart.opts.pop()), '滾輪可以縮放');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(charts.volChart.ranges.pop())), { from: -0.5, to: 14.5 }, '成交量同一個範圍');
  assert.strictEqual(charts.macdChart.ranges.length, 0, '不到 26 根，MACD 那張圖收起來、不套範圍');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(charts.kdChart.ranges.pop())), { from: -0.5, to: 14.5 }, 'KD 有 9 根以上，照樣對齊');
  assert(/資料 15 根，MACD 至少要 26 根才顯示/.test(f.caps['#macdCap span'].textContent));
  assert.strictEqual(f.caps['#kdCap span'].textContent, '9');
  // 週K 一年 53 根（管理者回報的情境）：填滿、可縮放
  f.ctx.rawData = series(53, '2025-09-19', 7);
  f.ctx.applyChartRange();
  assert.deepStrictEqual(JSON.parse(JSON.stringify(charts.chart.ranges.pop())), { from: -0.5, to: 52.5 });
  assert(zoomable(charts.chart.opts.pop()));
  assert.deepStrictEqual(JSON.parse(JSON.stringify(charts.macdChart.ranges.pop())), { from: -0.5, to: 52.5 });
  assert(zoomable(charts.macdChart.opts.pop()), '副圖也可以滾輪縮放');
  assert.strictEqual(f.caps['#macdCap span'].textContent, '12 / 26 / 9');
  // 選 1 個月而資料更長：照選的區間
  f.ctx.rawData = series(261, '2025-09-17');
  f.ctx.chartRange = '1';
  f.ctx.applyChartRange();
  assert(charts.chart.ranges.pop().from > 200);
  // 只有 5 根：MACD 與 KD 都收起來
  f.ctx.rawData = series(5, '2026-09-10');
  assert.strictEqual(f.ctx.paneShown('macd'), false); assert.strictEqual(f.ctx.paneShown('kd'), false);
  f.ctx.updateIndicatorCaps(5);
  assert(/KD 至少要 9 根才算得出/.test(f.caps['#kdCap span'].textContent));
  f.ctx.rawData = series(30, '2026-08-01');
  f.ctx.updateIndicatorCaps(30);
  assert.strictEqual(f.caps['#macdCap span'].textContent, '12 / 26 / 9', '26 根以上不再加註');
  f.ctx.panes.macd = false;
  assert.strictEqual(f.ctx.paneShown('macd'), false, '使用者關掉的副圖本來就不顯示');
  // 不再有靠右留白、鎖住縮放的模式
  assert(!/kShortMode|KBAR_MAX_PX|handleScale: false|fixLeftEdge: false/.test(js));
}
{
  // 資料範圍說明
  const f = front();
  const hour = ['2026/09/03', '2026/09/04', '2026/09/16'].flatMap(d => ['09:00', '10:00', '11:00', '12:00', '13:00'].map(t => ({ date: d + ' ' + t })));
  const note = f.ctx.kCoverageNote(hour, 'hour');
  assert(/共 15 根60分K（3 個交易日）/.test(note) && /從 2026\/09\/03 起才收錄，所以各區間顯示的是同一段/.test(note), note);
  assert(/每天 16:45 會自動往回補/.test(f.ctx.kCoverageNote(series(5, '2026-09-10'), 'day')));
  assert(!/14:35/.test(js), '排程時間已改為 16:45');
}
{
  // 前端暫存：一次取回三種週期；取的途中換週期不重複送出；3 分鐘內切換不再問伺服器
  let pending = null, calls = 0;
  const runStub = { withSuccessHandler(f) { this.ok = f; return this; }, withFailureHandler(f) { this.fail = f; return this; },
    apiGetCandlesBundle(code) { calls++; pending = this.ok; } };
  const shown = [];
  let clock = 1000000;
  const ctx = vm.createContext({ Date: { now: () => clock }, Object, candleRequest: 0, candleMemo: {}, candlePending: {}, CANDLE_MEMO_MS: 180000,
    rawData: [], currentCode: '2201', period: 'day', google: { script: { get run() { return Object.create(runStub); } } },
    $: id => (id === 'detail' ? { classList: { contains: () => true } } : { textContent: '' }),
    setChartVisible: () => {}, showCandles: (p, rows) => shown.push(p + ':' + rows.length) });
  vm.runInContext(fnSource('loadCandles'), ctx);
  ctx.loadCandles('2201', 'day');
  ctx.period = 'hour';
  ctx.loadCandles('2201', 'hour');
  assert.strictEqual(calls, 1, '同一檔正在取時不重複送出');
  pending({ day: [1, 2, 3], week: [1], hour: [1, 2] });
  assert.deepStrictEqual(shown, ['hour:2'], '回來時畫當下選的週期');
  ctx.period = 'week'; ctx.loadCandles('2201', 'week');
  ctx.period = 'day'; ctx.loadCandles('2201', 'day');
  assert.strictEqual(calls, 1, '切換週期直接用暫存');
  assert.deepStrictEqual(shown.slice(1), ['week:1', 'day:3']);
  clock += 181000;
  ctx.loadCandles('2201', 'day');
  assert.strictEqual(calls, 2, '超過 3 分鐘重新取');
}
{
  // 副圖同步規則（原始碼核對；瀏覽器實測見 Changelog）
  const init = fnSource('initCharts');
  assert(/if \(from !== chart && !\(Date\.now\(\) - \(userInputAt\[id\] \|\| 0\) < 1500\)\) \{ return; \}/.test(init), '副圖只在使用者操作時帶動其他圖');
  assert(/'pointerdown', 'pointermove', 'wheel', 'touchstart', 'touchmove'/.test(init));
  assert(/\.pill:disabled \{ opacity: \.38; cursor: not-allowed; \}/.test(read('Stylesheet.html')));
}

console.log('PASS: v41 per-stock K-line JSON caches written with putAll (≤100 per call, 6h, empty stocks cached), one bundle request for day/week/60-min with client memo, short-history layout (disabled ranges, always fill width with wheel zoom, collapsed MACD/KD), MACD from the first bar, sync only from user-driven sub-charts, repairDailyKVolume disabled.');
