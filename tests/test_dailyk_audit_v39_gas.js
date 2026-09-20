// v39（2026/09/16）：日K快取全面核對與修復。
// 起因：checkVolumeUnit('2385','2026/07') 顯示快取 07/27～07/31 的量是四捨五入後的張（7151／官方 7,150,763 股），
// 而且 7 月只有 5 天；同一張表 1506 卻是股且逐日完全相同。
// 官方資料取自證交所 MI_INDEX、STOCK_DAY 與櫃買 dailyQuotes（2026/09/16 實際查詢）。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const dir = path.resolve(__dirname, '..', 'apps-script');
const read = f => fs.readFileSync(path.join(dir, f), 'utf8');
const pad = n => String(n).padStart(2, '0');
const fmt = (d, tz, f) => f.replace('yyyy', d.getFullYear()).replace('MM', pad(d.getMonth() + 1)).replace('dd', pad(d.getDate()))
  .replace('HH', pad(d.getHours())).replace('mm', pad(d.getMinutes()));

// 證交所 MI_INDEX 2026/07/31（欄位與實際回應相同；漲跌欄帶 HTML）
const MI_INDEX_0731 = { stat: 'OK', date: '20260731', tables: [
  { title: '價格指數', fields: ['指數', '收盤指數'], data: [['發行量加權股價指數', '30,000']] },
  { title: '115年07月31日 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))',
    fields: ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '漲跌(+/-)', '漲跌價差', '最後揭示買價', '最後揭示買量', '最後揭示賣價', '最後揭示賣量', '本益比'],
    data: [
      ['0050', '元大台灣50', '345,660,955', '201,929', '35,123,786,635', '100.00', '102.85', '99.80', '102.85', '<p style= color:red>+</p>', '9.35', '102.85', '149', '--', '0', '0.00'],
      ['1506', '正道', '90,442', '69', '938,157', '10.40', '10.50', '10.25', '10.45', '<p style= color:red>+</p>', '0.10', '10.35', '2', '10.45', '26', '149.29'],
      ['2330', '台積電', '69,478,145', '215,037', '166,661,984,712', '2,350.00', '2,425.00', '2,345.00', '2,425.00', '<p style= color:red>+</p>', '220.00', '2,425.00', '1,989', '--', '0', '32.60'],
      ['2385', '群光', '7,150,763', '4,085', '760,290,962', '105.50', '108.00', '104.50', '106.00', '<p style= color:red>+</p>', '1.50', '105.50', '39', '106.00', '135', '11.96']
    ] }
] };
// 櫃買 dailyQuotes 2026/07/31（欄位與實際回應相同）
const TPEX_0731 = { stat: 'ok', date: '20260731', tables: [
  { title: '上櫃股票行情',
    fields: ['代號', '名稱', '收盤', '漲跌', '開盤', '最高', '最低', '均價', '成交股數', '成交金額(元)', '成交筆數', '最後買價', '最後買量(張數)', '最後賣價', '最後賣量(張數)', '發行股數', '次日 參考價', '次日 漲停價', '次日 跌停價'],
    data: [
      ['3529', '力旺', '2475.00', '+185.00', '2515.00', '2515.00', '2355.00', '2462.70', '2,269,939', '5,590,183,180', '3,635', '2470.00', '1', '2475.00', '4', '74,686,492', '2475.00', '2720.00', '2230.00'],
      ['6488', '環球晶', '855.00', '+77.00', '855.00', '855.00', '802.00', '842.14', '18,980,670', '15,984,456,371', '23,775', '855.00', '941', '0.00', '0', '478,113,725', '855.00', '940.00', '770.00']
    ] },
  { title: '管理股票', fields: ['代號', '名稱', '收盤', '漲跌', '開盤', '最高', '最低', '均價', '成交股數'], data: [] }
] };
// 2385 2026/07 證交所 STOCK_DAY（成交股數、開高低收）
const S2385_JUL = [
  ['07/01', 9556623, 111, 111, 105, 107], ['07/02', 4914245, 105.5, 106.5, 103.5, 105.5], ['07/03', 6185828, 105, 107.5, 105, 107],
  ['07/06', 5942293, 108.5, 110.5, 107, 107.5], ['07/07', 5586573, 108, 109, 105.5, 107], ['07/08', 3767154, 107.5, 108, 105.5, 107.5],
  ['07/09', 4291186, 108, 109, 107, 107], ['07/13', 3723953, 108, 108.5, 105.5, 106], ['07/14', 5181502, 106.5, 106.5, 103.5, 104.5],
  ['07/15', 2187044, 105.5, 106.5, 104.5, 105.5], ['07/16', 3943429, 106, 106.5, 105, 105.5], ['07/17', 6905032, 105.5, 106, 102.5, 102.5],
  ['07/20', 5822954, 104, 105.5, 102, 102.5], ['07/21', 4532467, 104, 106, 103.5, 104.5], ['07/22', 6317138, 105.5, 108, 105.5, 106.5],
  ['07/23', 3812816, 107, 108, 104.5, 106], ['07/24', 4097875, 106, 107, 105.5, 106.5], ['07/27', 2242244, 107, 107, 105, 106],
  ['07/28', 2918407, 104.5, 104.5, 102.5, 103], ['07/29', 5355387, 103, 103.5, 99.2, 101.5], ['07/30', 4293075, 101.5, 105, 100.5, 104.5],
  ['07/31', 7150763, 105.5, 108, 104.5, 106]
].map(r => ({ date: '2026/' + r[0], volume: r[1], open: r[2], high: r[3], low: r[4], close: r[5] }));

function load(opt) {
  opt = opt || {};
  const calls = { fetch: [], reset: 0, backfill: [], status: 0 };
  const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : k === 'then' ? undefined : stub()), apply: () => stub() });
  const logs = [];
  const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN, isFinite, parseInt, parseFloat, encodeURIComponent, Error, Infinity,
    Logger: { log: m => logs.push(String(m)) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => null, setProperty: () => {}, deleteProperty: () => {} }) },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {}, remove: () => {} }) },
    Utilities: { formatDate: fmt, sleep: () => {} },
    UrlFetchApp: { fetch: url => { calls.fetch.push(url); const body = (opt.urls || (() => null))(url); return { getContentText: () => (body == null ? '<html>error</html>' : JSON.stringify(body)) }; } },
    SpreadsheetApp: stub(), LockService: stub(), ScriptApp: stub(), MailApp: stub(), Session: stub()
  });
  for (const f of ['Presentationquality.gs', 'Quoteservice.gs', 'Cachebuilder.gs']) vm.runInContext(read(f), ctx, { filename: f });
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', todayStr_: () => '2026/09/16', withLock_: fn => fn(), isTradingNow_: () => false, hasFugle_: () => true,
    trackedCodes_: () => opt.codes || [],
    loadAllDailyK_: () => opt.index || {},
    getCachedDailyK: c => ((opt.index || {})[c] || []).map(r => Object.assign({}, r)),
    dailyKRange_: () => ({ from: '2025-07-31', to: '2026-07-31' })
  });
  return { ctx, calls, logs };
}
const bar = (date, volume, o, h, l, c) => ({ date, volume, open: o, high: h, low: l, close: c });
const series = (first, last, make) => {   // 產生 first～last 的平日
  const out = []; const p = first.split('/'); const d = new Date(+p[0], +p[1] - 1, +p[2]);
  const end = last;
  for (;;) { const s = fmt(d, '', 'yyyy/MM/dd'); if (s > end) break; if (d.getDay() && d.getDay() < 6) out.push(make(s)); d.setDate(d.getDate() + 1); }
  return out;
};

// ================================================================== 一、全市場官方行情解析
{
  const { ctx, calls } = load({ urls: u => (u.includes('MI_INDEX') ? MI_INDEX_0731 : u.includes('dailyQuotes') ? TPEX_0731 : null) });
  const off = ctx.officialDailyAll_('2026/07/31');
  assert(off.twse && off.tpex);
  assert.deepStrictEqual(Object.assign({}, off.rows['2385']), { market: '上市', volume: 7150763, open: 105.5, high: 108, low: 104.5, close: 106 });
  assert.deepStrictEqual(Object.assign({}, off.rows['2330']), { market: '上市', volume: 69478145, open: 2350, high: 2425, low: 2345, close: 2425 });
  assert.deepStrictEqual(Object.assign({}, off.rows['3529']), { market: '上櫃', volume: 2269939, open: 2515, high: 2515, low: 2355, close: 2475 });
  assert(!off.rows['發行量加權股價指數'], '指數表不能混進個股');
  // 同一代號兩邊都有（上市轉上櫃當天之類的異常資料）時以證交所為準，不被櫃買那一列蓋掉
  const both = JSON.parse(JSON.stringify(TPEX_0731));
  both.tables[0].data.push(['2385', '群光', '1.00', '0', '1.00', '1.00', '1.00', '1.00', '1', '1', '1', '1', '1', '1', '1', '1', '1', '1', '1']);
  const dup = load({ urls: u => (u.includes('MI_INDEX') ? MI_INDEX_0731 : u.includes('dailyQuotes') ? both : null) });
  assert.strictEqual(dup.ctx.officialDailyAll_('2026/07/31').rows['2385'].volume, 7150763);
  assert(calls.fetch[0].includes('MI_INDEX?date=20260731&type=ALLBUT0999'));
  assert(calls.fetch[1].includes('dailyQuotes?date=2026%2F07%2F31&type=EW'));
}

// ================================================================== 二、逐檔分類
{
  const good1506 = series('2025/08/01', '2026/07/31', d => bar(d, 1000, 10, 10, 10, 10));
  good1506[good1506.length - 1] = bar('2026/07/31', 90442, 10.40, 10.50, 10.25, 10.45);
  const index = {
    '1506': good1506,                                                                      // 正確（股、涵蓋一年）
    '2385': S2385_JUL.slice(17).map(r => Object.assign({}, r, { volume: Math.round(r.volume / 1000) })),   // 舊資料：張、只從 07/27 起
    '2330': series('2025/08/01', '2026/07/30', d => bar(d, 1, 1, 1, 1, 1)),               // 過期：沒有 07/31
    '3529': series('2025/08/01', '2026/07/31', d => bar(d, 1, 1, 1, 1, 1)),               // 上櫃，量差 0.5%：只列「接近」
    '6488': series('2025/08/01', '2026/07/31', d => bar(d, 1, 1, 1, 1, 1)),               // 上櫃，量是張：要重抓
    '0050': series('2025/08/01', '2026/07/31', d => bar(d, 1, 1, 1, 1, 1))                 // 開高低收不符
  };
  index['3529'][index['3529'].length - 1] = bar('2026/07/31', Math.round(2269939 * 1.005), 2515, 2515, 2355, 2475);
  index['6488'][index['6488'].length - 1] = bar('2026/07/31', 18981, 855, 855, 802, 855);
  index['0050'][index['0050'].length - 1] = bar('2026/07/31', 345660955, 100, 102.85, 99.8, 101);
  const { ctx, logs } = load({ codes: ['2385', '1506', '2330', '3529', '6488', '0050', '9999', '7777'], index,
    urls: u => (u.includes('MI_INDEX') ? MI_INDEX_0731 : u.includes('dailyQuotes') ? TPEX_0731 : null) });
  index['7777'] = series('2025/08/01', '2026/07/31', d => bar(d, 5, 1, 1, 1, 1));         // 官方查無：只列說明
  const r = ctx.auditDailyKCache();
  assert.strictEqual(r.date, '2026/07/31', '預設用快取裡最新的交易日');
  const why = c => (r.refetch.find(x => x.code === c) || { issues: [] }).issues.join('；');
  assert.deepStrictEqual(Array.from(r.matched).sort(), ['1506', '3529', '7777']);
  assert(/量存成張（快取 7151，官方 7150763 股）/.test(why('2385')), why('2385'));
  assert(/涵蓋不足：最早 2026\/07\/27/.test(why('2385')), why('2385'));
  assert(/過期：最後一根 2026\/07\/30/.test(why('2330')), why('2330'));
  assert(/量存成張（快取 18981，官方 18980670 股）/.test(why('6488')), why('6488'));
  assert(/開高低收不符/.test(why('0050')) && !/量/.test(why('0050')), why('0050'));
  assert(/快取沒有這一檔/.test(why('9999')));
  const note = c => (r.notes.find(x => x.code === c) || { notes: [] }).notes.join('；');
  assert(/量接近.*差 0\.50%/.test(note('3529')), note('3529'));
  assert(/官方 2026\/07\/31 查無這一檔/.test(note('7777')));
  const text = logs.join('\n');
  assert(/追蹤 8 檔：完全正確 3、需要重抓 5/.test(text), text);
  assert(/repairDailyKCache\(\)/.test(text));

  // 上市股就算量只差一點也要重抓（上市已逐日核對過是完全相同）
  index['2330'] = series('2025/08/01', '2026/07/31', d => bar(d, 1, 1, 1, 1, 1));
  index['2330'][index['2330'].length - 1] = bar('2026/07/31', 69478145 + 1000, 2350, 2425, 2345, 2425);
  const r2 = ctx.auditDailyKCache('2026/07/31');
  assert(/量不符（快取 69479145，官方 69478145 股，1\.0000 倍）/.test((r2.refetch.find(x => x.code === '2330') || { issues: [] }).issues.join()));
}

// 官方最新一天還沒公布：往前一天
{
  const index = { '2385': [bar('2026/07/30', 4293075, 101.5, 105, 100.5, 104.5), bar('2026/07/31', 7150763, 105.5, 108, 104.5, 106)] };
  const MI_0730 = JSON.parse(JSON.stringify(MI_INDEX_0731));
  MI_0730.tables[1].data = [['2385', '群光', '4,293,075', '', '', '101.50', '105.00', '100.50', '104.50']];
  const { ctx } = load({ codes: ['2385'], index,
    urls: u => (u.includes('MI_INDEX?date=20260731') ? { stat: '很抱歉，沒有符合條件的資料!' } : u.includes('MI_INDEX?date=20260730') ? MI_0730 : { stat: 'ok', tables: [] }) });
  const r = ctx.auditDailyKCache();
  assert.strictEqual(r.date, '2026/07/30');
  assert(r.refetch.length === 1 && /涵蓋不足/.test(r.refetch[0].issues.join()), '只剩涵蓋不足（量與開高低收都對）');
}

// ================================================================== 三、修復：先印狀態、重設進度、開新一輪
{
  const { ctx, calls, logs } = load();
  ctx.backfillDailyKStatus = () => { calls.status++; logs.push('補日K　進行中　做到 1506'); };
  ctx.resetDailyKCursor = () => { calls.reset++; return true; };
  ctx.backfillDailyKJob = o => { calls.backfill.push(o); return { note: '本次成功 30' }; };
  const r = ctx.repairDailyKCache();
  assert(r.started && calls.status === 1 && calls.reset === 1 && calls.backfill.length === 1);
  assert(/repairDailyKCache/.test(calls.backfill[0].source));
  assert(logs.indexOf('補日K　進行中　做到 1506') < logs.findIndex(l => /已開始/.test(l)), '重設前先印出上一輪狀態');
  // 真正的 backfillDailyKJob：手動帶的來源要傳進去；觸發器的事件物件不能被當成選項
  const real = load();
  const seen = [];
  real.ctx.scheduleDailyKContinue_ = () => true;
  real.ctx.afterDailyKRound_ = () => {};
  real.ctx.dailyKBackfillRound_ = o => { seen.push(o); return { note: '' }; };
  real.ctx.backfillDailyKJob({ source: '日K快取修復（repairDailyKCache）' });
  real.ctx.backfillDailyKJob({ authMode: 'FULL', triggerUid: 'x', hour: 14 });
  assert.strictEqual(seen[0].source, '日K快取修復（repairDailyKCache）');
  assert.strictEqual(seen[1].source, '排程或編輯器');
  assert.strictEqual(seen[1].deadline, undefined);
  // 正在寫入：不重設、不開新一輪
  ctx.resetDailyKCursor = () => false;
  assert.strictEqual(ctx.repairDailyKCache().started, false);
  assert.strictEqual(calls.backfill.length, 1);
}

// ================================================================== 四、checkVolumeUnit 指出舊資料存成張（重現管理者 2026/09/16 的執行結果）
{
  const { ctx, logs } = load({ index: { '2385': S2385_JUL.slice(17).map(r => Object.assign({}, r, { volume: Math.round(r.volume / 1000) })) } });
  ctx.fetchTwseMonth_ = () => S2385_JUL.map(r => Object.assign({}, r));
  ctx.getHourlyCandles_ = () => [];
  const r = ctx.checkVolumeUnit('2385', '2026/07');
  const text = logs.join('\n');
  assert(/官方 22 天，快取 5 天，完全相同 0 天/.test(text), text);
  assert(/2026\/07\/31　量 7151 股／官方 7150763 股（0\.001 倍）　← 舊資料存成張/.test(text), text);
  assert(r.daily.storedAsLots === true && r.daily.missing === 17);
  assert(/請執行 repairDailyKCache\(\)/.test(text));
  // 1506 那種正確的情況不提示
  const ok = load({ index: { '1506': [bar('2025/11/03', 70118, 12.35, 12.35, 12, 12.35)] } });
  ok.ctx.fetchTwseMonth_ = () => [bar('2025/11/03', 70118, 12.35, 12.35, 12, 12.35)];
  ok.ctx.getHourlyCandles_ = () => [];
  ok.ctx.checkVolumeUnit('1506', '2025/11');
  assert(!/repairDailyKCache/.test(ok.logs.join('\n')));
}

console.log('PASS: v39 auditDailyKCache checks every tracked stock against TWSE MI_INDEX / TPEx dailyQuotes (units, OHLC, staleness, coverage), repairDailyKCache refetches, and checkVolumeUnit flags lot-stored rows.');
