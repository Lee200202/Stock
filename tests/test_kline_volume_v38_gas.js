// v38（2026/09/16）：個股 K 線全區間＋區間切換、成交量獨立一張圖、60 分 K、成交量單位查證。
// 基準資料取自證交所 STOCK_DAY（2026/09/16 實際查詢）與管理者提供的日K快取、小時K 截圖。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const dir = path.resolve(__dirname, '..', 'apps-script');
const read = f => fs.readFileSync(path.join(dir, f), 'utf8');

// ------------------------------------------------------------------ 官方與快取資料
// 1506 正道 2025/10/27～11/21：證交所成交股數與開高低收（日K快取截圖逐列相同）
const T1506 = [
  ['2025/10/27', 192398, 12.50, 12.60, 12.35, 12.45], ['2025/10/28', 162606, 12.30, 12.55, 12.20, 12.35],
  ['2025/10/29', 70902, 12.35, 12.35, 12.15, 12.20], ['2025/10/30', 227838, 12.10, 12.10, 11.85, 12.00],
  ['2025/10/31', 125841, 12.00, 12.35, 11.90, 12.35], ['2025/11/03', 70118, 12.35, 12.35, 12.00, 12.35],
  ['2025/11/04', 29138, 12.05, 12.15, 12.05, 12.10], ['2025/11/05', 76100, 12.00, 12.05, 11.90, 12.00],
  ['2025/11/06', 106455, 12.00, 12.10, 12.00, 12.10], ['2025/11/07', 244892, 11.85, 12.70, 11.85, 12.35],
  ['2025/11/10', 32060, 12.30, 12.30, 12.05, 12.05], ['2025/11/11', 68166, 12.05, 12.15, 11.95, 12.15],
  ['2025/11/12', 214183, 12.35, 12.45, 12.00, 12.45], ['2025/11/13', 109965, 12.55, 12.55, 12.10, 12.15],
  ['2025/11/14', 121061, 12.10, 12.10, 11.95, 12.05], ['2025/11/17', 27788, 12.05, 12.05, 11.95, 12.00],
  ['2025/11/18', 134149, 12.00, 12.00, 11.70, 11.85], ['2025/11/19', 113803, 11.80, 11.85, 11.70, 11.85],
  ['2025/11/20', 174291, 11.90, 11.90, 11.60, 11.75], ['2025/11/21', 154509, 11.75, 11.75, 11.50, 11.60]
].map(r => ({ date: r[0], volume: r[1], open: r[2], high: r[3], low: r[4], close: r[5] }));
// 2385 群光 2026/07/21 小時K 截圖（張）與證交所當日（成交股數 4,532,467，開 104 高 106 低 103.5 收 104.5）
const H2385 = [['9:00', 104, 104.5, 103.5, 104, 906], ['10:00', 104, 105, 104, 104.5, 948],
  ['11:00', 104.5, 105.5, 104, 105.5, 922], ['12:00', 105.5, 106, 105, 105.5, 610], ['13:00', 105, 105.5, 104.5, 104.5, 1119]];
const OFFICIAL_2385 = { date: '2026/07/21', volume: 4532467, open: 104, high: 106, low: 103.5, close: 104.5 };

// ------------------------------------------------------------------ 載入後端
const pad = n => String(n).padStart(2, '0');
const fmt = (d, tz, f) => f.replace('yyyy', d.getFullYear()).replace('MM', pad(d.getMonth() + 1)).replace('dd', pad(d.getDate()))
  .replace('HH', pad(d.getHours())).replace('mm', pad(d.getMinutes()));
function backend(opt) {
  opt = opt || {};
  const props = Object.assign({}, opt.props);
  const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : k === 'then' ? undefined : stub()), apply: () => stub() });
  const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN, isFinite, parseInt, parseFloat,
    encodeURIComponent, Error, Infinity,
    Logger: { log: m => { ctx.__log = String(m); } },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = v; } }) },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {}, remove: () => {} }) },
    Utilities: { formatDate: fmt, sleep: () => {}, getUuid: () => 'u' },
    UrlFetchApp: stub(), SpreadsheetApp: stub(), LockService: stub(), ScriptApp: stub(), MailApp: stub(), Session: stub()
  });
  for (const f of ['Presentationquality.gs', 'Quoteservice.gs', 'Cachebuilder.gs']) vm.runInContext(read(f), ctx, { filename: f });
  Object.assign(ctx, {
    TZ: 'Asia/Taipei',
    fmtDate_: v => (Object.prototype.toString.call(v) === '[object Date]' ? fmt(v, '', 'yyyy/MM/dd') : String(v || '').trim().replace(/-/g, '/')),
    todayStr_: () => opt.today || '2026/07/21',
    withLock_: fn => fn(),
    getQuoteCache: () => opt.quotes || {},
    isTradingNow_: () => !!opt.trading,
    hasFugle_: () => opt.fugle !== false,
    trackedCodes_: () => opt.codes || []
  });
  return { ctx, props };
}
const near = (a, b, msg) => assert(Math.abs(a - b) < 1e-9, (msg || '') + ' ' + a + ' ≠ ' + b);

// ================================================================== 一、寫入端的單位（不再看 FUGLE_VOLUME_UNIT）
for (const unit of [null, 'lot', 'share']) {
  const { ctx } = backend({ props: unit ? { FUGLE_VOLUME_UNIT: unit } : {} });
  ctx.fugleFetch_ = p => {
    if (p.startsWith('/historical/')) return { data: [{ date: '2025-11-03', open: 12.35, high: 12.35, low: 12, close: 12.35, volume: 70118 }] };
    if (p.startsWith('/intraday/candles/')) return { data: [{ date: '2026-07-21T09:00:00.000+08:00', open: 104, high: 104.5, low: 103.5, close: 104, volume: 906 }] };
    if (p.startsWith('/intraday/quote/')) return { date: '2026-07-21', lastPrice: 104.5, total: { tradeVolume: 4505 } };
    throw new Error(p);
  };
  assert.strictEqual(ctx.fugleHistoricalOnce_('1506', '2025-11-01', '2025-11-30')[0].volume, 70118, '富果歷史日K：股，原樣存（設定 ' + unit + '）');
  const h = ctx.fugleHourly_('2385')[0];
  assert.strictEqual(h.volume, 906, '富果日內 60 分 K：張，原樣存（設定 ' + unit + '）');
  assert.strictEqual(h.date.slice(0, 10), '2026/07/21');
  assert.strictEqual(ctx.fugleQuote_('2385').volume, 4505, '富果即時報價：張');
}
{
  // 證交所 STOCK_DAY：成交股數原樣存（先前除以 1000，與富果那條路單位不一致）
  const { ctx } = backend();
  ctx.UrlFetchApp = { fetch: () => ({ getContentText: () => JSON.stringify({ stat: 'OK', data: [
    ['114/11/03', '70,118', '848,819', '12.35', '12.35', '12.00', '12.35', ' 0.00', '48', ''],
    ['114/11/04', '29,138', '352,127', '12.05', '12.15', '12.05', '12.10', '-0.25', '26', '']] }) }) };
  const rows = ctx.fetchTwseMonth_('1506', '2025/11');
  assert.deepStrictEqual(Array.from(rows, r => [r.date, r.volume, r.open, r.high, r.low, r.close]),
    [['2025/11/03', 70118, 12.35, 12.35, 12, 12.35], ['2025/11/04', 29138, 12.05, 12.15, 12.05, 12.1]]);
  assert(!/FUGLE_VOLUME_UNIT/.test(read('Quoteservice.gs').replace(/\/\*[\s\S]*?\*\//g, '')), '程式碼不再讀 FUGLE_VOLUME_UNIT');
}

// ================================================================== 二、讀取端：一律回傳張，週K 先加總股數
{
  const { ctx } = backend({ today: '2025/11/21' });
  const index = { '1506': T1506.map(r => Object.assign({}, r)) };
  ctx.getCachedDailyK = c => index[c] || [];
  const day = ctx.getCandles('1506', 'day');
  assert.strictEqual(day.length, 20);
  T1506.forEach((r, i) => { near(day[i].volume, r.volume / 1000, r.date); assert.strictEqual(day[i].close, r.close); });
  assert.strictEqual(day[5].volume, 70.118);
  assert.strictEqual(index['1506'][5].volume, 70118, '不能改動共用的日K索引');
  near(ctx.getCandles('1506', 'day')[5].volume, 70.118, '再呼叫一次仍然正確（沒有被除兩次）');

  const week = ctx.getCandles('1506', 'week');
  assert.strictEqual(week.length, 4);
  // 10/27～10/31：量 779,585 股、開 12.50 高 12.60 低 11.85 收 12.35
  assert.deepStrictEqual([week[0].open, week[0].high, week[0].low, week[0].close], [12.5, 12.6, 11.85, 12.35]);
  near(week[0].volume, 779.585, '週K 量');
  // 11/03～11/07：量 526,703 股、開 12.35 高 12.70 低 11.85 收 12.35
  assert.deepStrictEqual([week[1].open, week[1].high, week[1].low, week[1].close], [12.35, 12.7, 11.85, 12.35]);
  near(week[1].volume, 526.703, '週K 量');
  assert.strictEqual(week[1].date, '2025/11/07', '週K 的日期是當週最後一個交易日');
  const total = week.reduce((s, w) => s + w.volume, 0);
  near(Math.round(total * 1000), T1506.reduce((s, r) => s + r.volume, 0), '週K 加總等於日K 加總');

  // 盤中預覽那一根來自即時報價（張），接在換算成張的日K 後面，單位一致
  const q = backend({ today: '2025/11/24', quotes: { '1506': { date: '2025/11/24', open: 11.6, high: 11.7, low: 11.5, last: 11.55, volume: 156, time: '13:00' } } });
  q.ctx.getCachedDailyK = () => T1506.map(r => Object.assign({}, r));
  q.ctx.Utilities.formatDate = (d, tz, f) => (f === 'yyyy/MM/dd' ? '2025/11/24' : fmt(d, tz, f));
  const withPreview = q.ctx.getCandles('1506', 'day');
  const last = withPreview[withPreview.length - 1];
  assert(last._provisional && last.volume === 156 && withPreview[withPreview.length - 2].volume === 154.509);

  // 盤後用日K快取補的報價：量也是張
  const g = backend({ trading: false });
  g.ctx.getCachedDailyK = () => T1506.slice(-2).map(r => Object.assign({}, r));
  g.ctx.qTime_ = s => s;
  const quotes = g.ctx.getQuotesFor(['1506']);
  near(quotes['1506'].volume, 154.509, '盤後報價的量');
}

// ================================================================== 三、小時K：時段欄、重複列、60 分 K 回傳
function sheet(head, rows, display) {
  const sh = {
    rows: [head].concat(rows), formats: [], deleted: [],
    getLastRow: () => sh.rows.length, getLastColumn: () => head.length,
    getRange: (r, c, nr, nc) => ({
      getValues: () => sh.rows.slice(r - 1, r - 1 + (nr || 1)).map(x => x.slice(c - 1, c - 1 + (nc || 1))),
      getDisplayValues: () => sh.rows.slice(r - 1, r - 1 + (nr || 1)).map((x, i) => x.slice(c - 1, c - 1 + (nc || 1))
        .map((v, j) => (display ? display(r - 1 + i, c - 1 + j, v) : String(v)))),
      setValues: vals => { vals.forEach((v, i) => { sh.rows[r - 1 + i] = v.slice(); }); },
      setNumberFormat: f => { sh.formats.push([r, c, nr, f]); }
    }),
    getDataRange: () => ({ getValues: () => sh.rows.map(x => x.slice()) }),
    deleteRow: i => { sh.deleted.push(i); sh.rows.splice(i - 1, 1); }
  };
  return sh;
}
const timeCell = hm => { const [h, m] = hm.split(':').map(Number); return new Date(1899, 11, 30, h, m); };
const HK_HEAD = ['日期', '時段', '代號', '開', '高', '低', '收', '量'];
{
  const { ctx } = backend({ fugle: false });
  // hourSlot_：畫面文字優先；時間值、兩位數、垃圾值
  assert.strictEqual(ctx.hourSlot_(timeCell('9:00'), '9:00'), '09:00');
  assert.strictEqual(ctx.hourSlot_('13:00', '13:00'), '13:00');
  assert.strictEqual(ctx.hourSlot_(timeCell('10:00'), ''), '10:00');
  assert.strictEqual(ctx.hourSlot_('Sa:00', 'Sa:00'), '', '先前把時間值 String() 之後切出的垃圾時段要丟掉');
  // Apps Script 裡 1899/12/30 的時間值用台北時區格式化，會帶到當年的地方平時（+08:06），分鐘會偏掉。
  // 模擬那個偏差：畫面上顯示 9:00，就要以畫面為準，不能用時間值算出來的 09:06。
  const realFormat = ctx.Utilities.formatDate;
  ctx.Utilities.formatDate = (d, tz, f) => (d.getFullYear() === 1899 && f === 'HH:mm'
    ? pad(d.getHours()) + ':' + pad(d.getMinutes() + 6) : realFormat(d, tz, f));
  assert.strictEqual(ctx.hourSlot_(timeCell('9:00'), '9:00'), '09:00', '以畫面顯示的 9:00 為準');
  assert.strictEqual(ctx.hourSlot_(timeCell('9:00'), ''), '09:06', '沒有畫面文字時才退回時間值（此時會偏，這正是要讀畫面文字的原因）');
  ctx.Utilities.formatDate = realFormat;

  // 截圖那樣：日期、時段都被試算表轉成日期／時間值；另有一列重複（後寫的是正確值）與一列垃圾
  const rows = H2385.map(r => [new Date(2026, 6, 21), timeCell(r[0]), 2385, r[1], r[2], r[3], r[4], r[5]]);
  rows.unshift([new Date(2026, 6, 21), timeCell('9:00'), 2385, 104, 104.2, 103.9, 104, 500]);   // 13:45 快照備援（較粗）
  rows.push([new Date(2026, 6, 21), 'Sa:00', 2385, 1, 1, 1, 1, 1]);                              // 舊版寫壞的列
  const hk = sheet(HK_HEAD, rows, (ri, ci, v) => (ci === 1 && v instanceof Date ? v.getHours() + ':' + pad(v.getMinutes()) : String(v)));
  ctx.getSheet_ = name => { assert.strictEqual(name, '小時K'); return hk; };
  const bars = ctx.getCandles('2385', 'hour');
  assert.deepStrictEqual(Array.from(bars, b => b.date), ['2026/07/21 09:00', '2026/07/21 10:00', '2026/07/21 11:00', '2026/07/21 12:00', '2026/07/21 13:00']);
  assert.strictEqual(bars[0].volume, 906, '重複的 09:00 取後寫的那一筆');
  const lots = bars.reduce((s, b) => s + b.volume, 0);
  assert.strictEqual(lots, 4505);
  const ratio = lots / (OFFICIAL_2385.volume / 1000);
  assert(ratio > 0.99 && ratio < 1.0, '60 分 K 加總是官方成交張數的 99.4%（官方含零股、盤後、鉅額）：' + ratio);
  assert.deepStrictEqual([bars[0].open, Math.max(...bars.map(b => b.high)), Math.min(...bars.map(b => b.low)), bars[4].close],
    [OFFICIAL_2385.open, OFFICIAL_2385.high, OFFICIAL_2385.low, OFFICIAL_2385.close], '開高低收與官方一致');
  assert.strictEqual(ctx.getHourlyMeta().bars, 6, '中繼資料只算讀得懂的列（5 根＋1 筆重複）');
  // 複製一份，不動共用索引
  bars.push({ date: 'x' });
  assert.strictEqual(ctx.getCandles('2385', 'hour').length, 5);
}

// ================================================================== 四、快照聚合：成交量＝本小時最後累計 − 上一小時最後累計
{
  const { ctx } = backend();
  const SN_HEAD = ['日期', '時間', '代號', '成交價', '累計成交量'];
  const d = new Date(2026, 6, 21);
  const snaps = [
    ['09:05', 104, 1200], ['09:55', 104.5, 2000],             // 09:00：2000（含開盤集合競價）
    ['10:05', 104.6, 2100], ['10:55', 105, 2600],             // 10:00：600（含 09:55～10:05 之間的 100）
    ['11:10', 105.2, 0], ['11:30', 105.5, 3000],              // 11:00：400（中間一筆報價沒取到，量是 0）
    ['13:35', 104.5, 5000]                                    // 13:00：2000（含收盤集合競價）
  ].map(s => [d, timeCell(s[0]), '2385', s[1], s[2]]);
  const sn = sheet(SN_HEAD, snaps, (ri, ci, v) => (ci === 1 && v instanceof Date ? pad(v.getHours()) + ':' + pad(v.getMinutes()) : String(v)));
  const hk = sheet(HK_HEAD, [[new Date(2026, 6, 20), '09:00', '2385', 1, 1, 1, 1, 7]]);
  ctx.getSheet_ = n => (n === '盤中快照' ? sn : hk);
  sn.getRange = ((orig) => (r, c, nr, nc) => Object.assign(orig(r, c, nr, nc), { clearContent: () => {} }))(sn.getRange);
  ctx.aggregateSnapshotJob();
  const out = hk.rows.slice(1).filter(r => r[0] === '2026/07/21');
  assert.deepStrictEqual(out.map(r => [r[1], r[7]]), [['09:00', 2000], ['10:00', 600], ['11:00', 400], ['13:00', 2000]]);
  assert.strictEqual(out.reduce((s, r) => s + r[7], 0), 5000, '各小時加總等於最後的當日累計');
  assert(hk.formats.some(f => f[1] === 2 && f[3] === '@'), '時段欄寫成純文字');
  assert.strictEqual(hk.rows[1][7], 7, '前一天的列不動');
}

// ================================================================== 五、富果 60 分 K 落地：同一天同一檔取代快照備援
{
  const { ctx } = backend({ codes: ['2385', '2404'] });
  const d = new Date(2026, 6, 21);
  const hk = sheet(HK_HEAD, [
    [new Date(2026, 6, 20), '13:00', '2385', 1, 1, 1, 1, 1],
    [d, timeCell('9:00'), '2385', 104, 104.2, 103.9, 104, 500],
    [d, timeCell('9:00'), '2404', 1165, 1170, 1160, 1165, 300]
  ]);
  ctx.getSheet_ = () => hk;
  ctx.fugleHourly_ = code => (code === '2385'
    ? H2385.map(r => ({ date: '2026/07/21 ' + pad(r[0].split(':')[0]) + ':00', open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5] }))
    : (() => { throw new Error('429'); })());
  ctx.aggregateHourlyJob();
  const today2385 = hk.rows.slice(1).filter(r => String(r[2]) === '2385' && (r[0] === '2026/07/21' || (r[0] instanceof Date && r[0].getDate() === 21)));
  assert.deepStrictEqual(today2385.map(r => [r[1], r[7]]), H2385.map(r => [pad(r[0].split(':')[0]) + ':00', r[5]]), '2385 當天只剩富果的 5 根');
  assert(hk.rows.some(r => String(r[2]) === '2404' && r[7] === 300), '富果抓不到的 2404 保留快照備援');
  assert(hk.rows.some(r => String(r[2]) === '2385' && r[7] === 1), '前一天的列不動');
  assert(hk.formats.some(f => f[1] === 2 && f[3] === '@'));
}

// ================================================================== 六、checkVolumeUnit：逐日對證交所
{
  const { ctx } = backend({ trading: false });
  const nov = T1506.filter(r => r.date.startsWith('2025/11'));
  ctx.fetchTwseMonth_ = () => nov.map(r => Object.assign({}, r));
  ctx.getHourlyCandles_ = () => [];
  ctx.getCachedDailyK = () => T1506.map(r => Object.assign({}, r));
  let res = ctx.checkVolumeUnit('1506', '2025/11');
  assert.strictEqual(res.daily.official, 15); assert.strictEqual(res.daily.matched, 15);
  assert.strictEqual(res.daily.mismatch.length, 0);
  assert(/完全相同 15 天/.test(ctx.__log));
  // 快取若是張（先前證交所那條路除以 1000 寫進去的），逐日抓出來並標出倍數
  ctx.getCachedDailyK = () => T1506.map(r => Object.assign({}, r, { volume: r.volume / 1000 }));
  res = ctx.checkVolumeUnit('1506', '2025/11');
  assert.strictEqual(res.daily.matched, 0);
  assert(/0\.001 倍/.test(res.daily.mismatch[0]));
  // 小時K 對官方
  ctx.fetchTwseMonth_ = () => [Object.assign({}, OFFICIAL_2385)];
  ctx.getCachedDailyK = () => [Object.assign({}, OFFICIAL_2385)];
  ctx.getHourlyCandles_ = () => H2385.map(r => ({ date: '2026/07/21 ' + pad(r[0].split(':')[0]) + ':00', open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5] }));
  res = ctx.checkVolumeUnit('2385', '2026/07');
  assert.strictEqual(res.hourly[0].lots, 4505);
  assert(res.hourly[0].ok && res.hourly[0].ohlc, JSON.stringify(res.hourly[0]));
}

// ================================================================== 七、前端
const js = read('JavaScript.html'), index = read('Index.html'), css = read('Stylesheet.html');
function fnSource(name) {
  const at = js.indexOf('function ' + name + '(');
  assert(at >= 0, '找不到 ' + name);
  let i = js.indexOf('{', at), depth = 0;
  for (; i < js.length; i++) { if (js[i] === '{') depth++; else if (js[i] === '}' && !--depth) break; }
  return js.slice(at, i + 1);
}
{
  const f = vm.createContext({ Math, String, Number, Date, isFinite, Array, period: 'day', kdMap: {} });
  vm.runInContext(['toTime', 'fmtLots', 'fmtLotsAxis', 'volText', 'rangeStartIndex', 'computeKD'].map(fnSource).join('\n'), f);
  // 60 分 K 的時間：軸上要是台北時間 09:00，不是 01:00
  const t = f.toTime('2026/07/21 09:00');
  assert.strictEqual(new Date(t * 1000).toISOString().slice(0, 16), '2026-07-21T09:00');
  assert.strictEqual(f.toTime('2026/07/21'), '2026-07-21');
  // 量的顯示
  assert.strictEqual(f.fmtLots(70.118), '70');
  assert.strictEqual(f.fmtLots(4532.467), '4,532');
  assert.strictEqual(f.fmtLots(0.521), '0.521');
  assert.strictEqual(f.fmtLotsAxis(12000), '1.2萬');
  assert.strictEqual(f.volText(70.118), '70 張（70,118 股）');
  assert.strictEqual(f.volText(526.703), '527 張（526,703 股）');
  f.period = 'hour';
  assert.strictEqual(f.volText(906), '906 張');
  // 區間起點：往回 N 個月，月底夾到當月最後一天
  const data = ['2026/02/27', '2026/03/02', '2026/03/31', '2026/06/15', '2026/06/16', '2026/09/16'].map(date => ({ date }));
  assert.strictEqual(f.rangeStartIndex(data, 3), 4);
  assert.strictEqual(f.rangeStartIndex([{ date: '2026/02/27' }, { date: '2026/02/28' }, { date: '2026/03/31' }], 1), 1, '3/31 往回一個月是 2/28，不是 3/3');
  assert.strictEqual(f.rangeStartIndex(data, 12), 0);
  // KD 前 8 根補空白點，根數與 K 線相同（四張圖用第幾根同步）
  const bars = T1506.map(r => Object.assign({}, r));
  const kd = f.computeKD(bars, 9);
  assert.strictEqual(kd.k.length, bars.length); assert.strictEqual(kd.d.length, bars.length); assert.strictEqual(kd.j.length, bars.length);
  assert(kd.k.slice(0, 8).every(p => !('value' in p)) && 'value' in kd.k[8]);
}
// 版面與行為（原始碼核對）
assert(index.includes('data-period="hour"') && />60分</.test(index));
for (const r of ['1', '3', '6', '12', 'all']) assert(index.includes('data-krange="' + r + '"'));
assert(/<div id="chart"><\/div>\s*<div class="subcap" id="volCap">成交量　<span>張<\/span><\/div>\s*<div id="volChart"><\/div>/.test(index), '成交量是 K 線下方獨立的一張');
assert(/#volChart \{ height: \d+px; border-top: none; \}/.test(css) && /#chart \{ height: \d+px; \}/.test(css), '成交量是獨立一張、有固定高度（數值見 v42 測試）');
assert(/volSeries = volChart\.addHistogramSeries/.test(js) && !/chart\.addHistogramSeries/.test(js.replace(/volChart\.addHistogramSeries|macdChart\.addHistogramSeries/g, '')), '量柱不再畫在 K 線圖上');
assert(/minimumWidth: 72/.test(fnSource('chartOptions')), '四張圖的縱軸同寬，橫向才對得齊');
assert(/minBarSpacing: 0\.5/.test(fnSource('chartOptions')));
assert(/\[chart, volChart, macdChart, kdChart\]/.test(fnSource('applyChartRange')), '區間直接套到每一張圖');
assert(!/VISIBLE_BARS/.test(js), '不再預設只顯示 30 根');
{
  // 沒有資料時，有「日／週／60分」的那一列不能收起來
  const bars = [{ period: true, hidden: false }, { period: false, hidden: false }];
  const els = {};
  const doc = { querySelectorAll: () => bars.map(b => ({ set hidden(v) { b.hidden = v; }, querySelector: () => (b.period ? {} : null) })) };
  const g = vm.createContext({ document: doc, $: id => (els[id] = els[id] || {}) });
  vm.runInContext(fnSource('setChartVisible'), g);
  g.setChartVisible(false);
  assert.strictEqual(bars[0].hidden, false); assert.strictEqual(bars[1].hidden, true);
  assert.strictEqual(els.volChart.hidden, true); assert.strictEqual(els.volCap.hidden, true);
}

console.log('PASS: v38 K-line full range and range buttons, separate volume chart, 60-min bars in Taipei time, volume units verified against TWSE (daily cache = shares, hourly = lots, site shows lots).');
