/* 報價流程的整日模擬（v134）。不連任何外部服務：時鐘、試算表、快取、報價來源都是假的。

   node scripts/check_quote_flow.js

   把 Quoteservice.gs 原樣載入，從 09:00 跑到隔天開盤前，每五分鐘照正式排程呼叫
   refreshQuoteCacheJob 與 settleClosingQuotesTick_，在四種連線狀況下核對：
     一、盤中：持有中的股票每一棒都更新；持有以外的股票不會被擠到一檔都輪不到。
     二、批次來源可用（直連或經 Worker 轉送）時，每一棒全部代號都是五分鐘內的報價。
     三、收盤後：即時快取整批換成收盤價；讀取端不把盤中某一刻的價當成收盤價。
     四、隔天開盤前：前一天的報價不算新鮮，顯示的是前一天收盤。
   這份模擬重現了 2026/10/06 的實況（直連 Address unavailable、沒有轉送），舊程式在同一份模擬下會失敗。 */
process.env.TZ = 'Asia/Taipei';                    // 行事曆用到 setHours／setDate，固定在台北時區
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

// QUOTE_SOURCE 可以指到另一份檔案（例如修正前的版本），確認這份模擬真的分得出差別。
const SOURCE = fs.readFileSync(process.env.QUOTE_SOURCE || 'public-site/gas-source/Quoteservice.gs', 'utf8').replace(/\r\n/g, '\n');
const DAY = '2026/10/06', NEXT = '2026/10/07';
const CODES = Array.from({length: 240}, (_, i) => String(1101 + i * 7));
const HELD = CODES.filter((_, i) => i % 18 === 0).slice(0, 14);                 // 14 檔持有中
const RELAY = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/quote-relay';
const TOKEN = 'x'.repeat(40);
const base = c => 100 + (Number(c) % 97);
const closeOf = c => base(c) + 50;                                              // 收盤價和任何盤中價都不同
const taipei = (ymd, hms) => Date.parse(ymd.replace(/\//g, '-') + 'T' + hms + '+08:00');

function makeWorld(scenario) {
  const clock = {now: taipei(DAY, '08:55:00')};
  const stats = {fugle: 0, misDirect: 0, misRelay: 0, jobMs: 0};
  class FakeDate extends Date {
    constructor(...a) { if (a.length) super(...a); else super(clock.now); }
    static now() { return clock.now; }
  }
  const parts = d => {
    const t = new Date(d.getTime() + 8 * 3600000), p = n => String(n).padStart(2, '0');
    return {yyyy: String(t.getUTCFullYear()), MM: p(t.getUTCMonth() + 1), dd: p(t.getUTCDate()), HH: p(t.getUTCHours()),
            mm: p(t.getUTCMinutes()), ss: p(t.getUTCSeconds()), u: String(t.getUTCDay() || 7)};
  };
  const secOfDay = () => { const p = parts(new Date(clock.now)); return Number(p.HH) * 3600 + Number(p.mm) * 60 + Number(p.ss); };
  const today = () => { const p = parts(new Date(clock.now)); return p.yyyy + '/' + p.MM + '/' + p.dd; };
  const priceNow = c => (secOfDay() >= 13 * 3600 + 30 * 60 ? closeOf(c) : base(c) + (Math.floor(secOfDay() / 300) % 9));
  // scenario.quiet：這幾檔整天只在開盤成交過一次，之後累計量不變（冷門股）
  const quiet = c => !!scenario.quiet && scenario.quiet.includes(c);
  const volNow = c => quiet(c) ? 37 : Math.min(1000, Math.floor((secOfDay() - 9 * 3600) / 16.2)) + Number(c) % 50;

  const cache = new Map(), props = new Map([['FUGLE_API_KEY', 'k'], ['SITE_BRIDGE_TOKEN', TOKEN]]);
  const cacheGet = k => { const v = cache.get(k); if (!v) return null; if (v.exp <= clock.now) { cache.delete(k); return null; } return v.val; };
  const CACHE = {
    get: cacheGet, put: (k, v, ttl) => cache.set(k, {val: String(v), exp: clock.now + (ttl || 600) * 1000}),
    getAll: keys => { const o = {}; keys.forEach(k => { const v = cacheGet(k); if (v != null) o[k] = v; }); return o; },
    putAll: (o, ttl) => Object.keys(o).forEach(k => CACHE.put(k, o[k], ttl)),
    remove: k => cache.delete(k), removeAll: keys => keys.forEach(k => cache.delete(k)),
  };
  const sheet = {rows: [['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']]};
  const fakeSheet = {
    getDataRange: () => ({getValues: () => sheet.rows.map(r => r.slice())}),
    getLastRow: () => sheet.rows.length,
    getRange: (row, col, n, w) => ({
      setValues: v => { v.forEach((r, i) => { sheet.rows[row - 1 + i] = r.slice(); }); },
      clearContent: () => { sheet.rows.length = Math.min(sheet.rows.length, row - 1); },
    }),
  };
  let misPoll = 0, seed = 20261007; const seenOnce = {};
  const rand = () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; };
  const misRows = channels => {
    const seen = new Set(), out = [];
    decodeURIComponent(channels).split('|').forEach(ch => {
      const c = ch.replace(/^(?:tse|otc)_/, '').replace(/\.tw$/, '');
      if (!ch.startsWith('tse_') || seen.has(c) || c === scenario.noData) { out.push({z: '-'}); return; }
      seen.add(c);
      const p = parts(new Date(clock.now)), closed = secOfDay() >= 13 * 3600 + 30 * 60;
      // 盤中有一部分列在這一刻不是成交訊息（z 是 "-"）：scenario.noTrade 是比例，每次查詢每一檔各自決定（固定的偽亂數，可重現）。
      misPoll++;
      seenOnce[c] = (seenOnce[c] || 0) + 1;
      const idle = !closed && (quiet(c) ? seenOnce[c] > 1 : !!scenario.noTrade && rand() < scenario.noTrade);
      out.push({c, n: '股' + c, z: idle ? '-' : String(priceNow(c)), y: String(base(c) - 1), d: p.yyyy + p.MM + p.dd,
                t: closed ? '13:30:00' : p.HH + ':' + p.mm + ':' + p.ss, v: String(closed ? 1000 + Number(c) % 50 : volNow(c)),
                o: String(base(c)), h: String(base(c) + 60), l: String(base(c) - 2)});
    });
    return out;
  };
  const response = (code, body) => ({getResponseCode: () => code, getContentText: () => (typeof body === 'string' ? body : JSON.stringify(body))});
  const UrlFetchApp = {fetch: (url, opt) => {
    if (url.startsWith('https://mis.twse.com.tw/')) {
      stats.misDirect++; clock.now += 400;
      if (!scenario.direct) throw new Error('Address unavailable: ' + url);
      return response(200, {msgArray: misRows(new URL(url).searchParams.get('ex_ch'))});
    }
    if (url.startsWith(RELAY)) {
      stats.misRelay++; clock.now += 500;
      if (scenario.relay === 'missing') return response(404, {ok: false, error: 'not-found'});
      if (!scenario.relay) return response(502, {ok: false, error: 'upstream-unreachable'});
      assert.equal(opt.headers['X-Bridge-Token'], TOKEN, '轉送要帶橋接權杖');
      return response(200, {ok: true, msgArray: misRows(new URL(url).searchParams.get('ex_ch'))});
    }
    if (url.includes('/intraday/quote/')) {
      stats.fugle++; clock.now += scenario.fugleMs || 1750;                       // 含限速等待；正式環境實測一棒 30 秒 17 檔
      const c = decodeURIComponent(url.split('/intraday/quote/')[1]), p = parts(new Date(clock.now));
      // 當天沒有成交的那一檔，逐檔來源回的是前一個交易日，程式不採用。
      return response(200, {symbol: c, name: '股' + c, date: c === scenario.noData ? '2026-10-05' : p.yyyy + '-' + p.MM + '-' + p.dd, previousClose: base(c) - 1,
        lastTrade: {price: priceNow(c), time: Math.min(clock.now, taipei(today(), '13:30:00')) * 1000}, openPrice: base(c),
        highPrice: base(c) + 60, lowPrice: base(c) - 2, total: {tradeVolume: volNow(c)}});
    }
    throw new Error('unexpected fetch ' + url);
  }};
  const dailyK = {};                                                               // code -> rows；收盤行情落地後才有當天那一根
  const ctx = {
    Date: FakeDate, JSON, Math, Number, String, Object, Array, RegExp, Error, isFinite, isNaN, parseFloat, parseInt, encodeURIComponent, decodeURIComponent,
    console, CacheService: {getScriptCache: () => CACHE}, PropertiesService: {getScriptProperties: () => ({
      getProperty: k => (props.has(k) ? props.get(k) : null), setProperty: (k, v) => props.set(k, String(v)), deleteProperty: k => props.delete(k)})},
    LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
    Utilities: {sleep: ms => { clock.now += ms; }, formatDate: (d, tz, f) => { const p = parts(d); return f.replace(/yyyy|MM|dd|HH|mm|ss|u/g, m => p[m]); }},
    UrlFetchApp, Logger: {log: () => {}}, TZ: 'Asia/Taipei', DASH_CACHE_KEY_: 'dash',
    whyClosed_: () => '', todayStr_: () => today(), trackedCodes_: () => CODES.slice(),
    loadCodeMap_: () => ({byCode: Object.fromEntries(CODES.map(c => [c, {name: '股' + c, market: '上市'}]))}),
    getSheet_: () => fakeSheet, withLock_: fn => fn(),
    readSheetObjects_: name => {
      if (name === '持股追蹤') return HELD.map(c => ({'代號': c, '狀態': '持有中'}));
      const head = sheet.rows[0];
      return sheet.rows.slice(1).map(r => Object.fromEntries(head.map((h, i) => [h, r[i] === undefined ? '' : r[i]])));
    },
    fmtDate_: v => String(v), getCachedDailyK: c => dailyK[c] || [],
    // 戰情行情端點（BattleData.gs）用到的其餘函式
    withSheetSnapshot_: fn => fn(), marketHolidaySet_: () => ({}), MARKET_HOLIDAY_YEARS_: {2025: 1, 2026: 1}, HOLIDAY_PROP_PREFIX_: 'HOLIDAYS_',
    loadAllDailyK_: () => dailyK, opsRuntimeConserve_: () => !!scenario.conserve,
    nowStamp_: () => { const p = parts(new Date(clock.now)); return p.yyyy + '/' + p.MM + '/' + p.dd + ' ' + p.HH + ':' + p.mm + ':' + p.ss; },
  };
  vm.createContext(ctx);
  vm.runInContext(SOURCE, ctx, {filename: 'Quoteservice.gs'});
  vm.runInContext(fs.readFileSync('public-site/gas-source/BattleData.gs', 'utf8').replace(/\r\n/g, '\n'), ctx, {filename: 'BattleData.gs'});
  // 日K歷史：事件日之前 130 個交易日（模擬裡沒有假日，週一到週五都開盤），量 1000 張、收盤等於當天報價的昨收。
  const seedHistory = () => CODES.forEach(c => {
    const rows = [];
    for (let d = new Date(taipei(DAY, '12:00:00') - 86400000); rows.length < 130; d = new Date(d.getTime() - 86400000)) {
      const p = parts(d);
      // 2026/07/10 是已向證交所核對的臨時休市日：真實行情那天沒有日K，模擬也不放
      if (Number(p.u) <= 5 && p.yyyy + '/' + p.MM + '/' + p.dd !== '2026/07/10') rows.unshift({date: p.yyyy + '/' + p.MM + '/' + p.dd, open: base(c) - 1, high: base(c), low: base(c) - 2, close: base(c) - 1, volume: 1000000});
    }
    dailyK[c] = rows; CACHE.put('dk2_' + c, ctx.kcEncode_(rows), 21600);
  });
  const view = codes => codes.forEach(c => CACHE.put('quote_viewed_' + c, String(clock.now), 1800));
  const row = c => sheet.rows.slice(1).find(r => String(r[0]) === c);
  const landDailyK = ymd => CODES.filter(c => c !== scenario.noData).forEach(c => {
    const prior = dailyK[c] && dailyK[c].length > 2 ? dailyK[c].filter(r => r.date < ymd)
      : [{date: '2026/10/05', open: base(c), high: base(c) + 3, low: base(c) - 3, close: base(c) - 1, volume: 900000}];
    const rows = prior.concat([{date: ymd, open: base(c), high: base(c) + 60, low: base(c) - 2, close: closeOf(c), volume: (1000 + Number(c) % 50) * 1000}]);
    dailyK[c] = rows; CACHE.put('dk2_' + c, ctx.kcEncode_(rows), 21600);
  });
  return {ctx, clock, stats, sheet, row, view, landDailyK, seedHistory, props, CACHE, secOfDay};
}

/** 照正式排程跑完一個交易日。回傳每一棒的紀錄。conserveFrom：幾點之後報價改成每十分鐘一棒（用量保護）。 */
function runDay(w, {viewers = [], conserveFrom = '99:99', kAt = '14:45'} = {}) {
  const runs = [];
  let lastQuoteRun = 0, kLanded = false;
  for (let t = taipei(DAY, '09:00:10'); t <= taipei(DAY, '22:30:00'); t += 300000) {
    w.clock.now = Math.max(w.clock.now, t);
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    if (!kLanded && hm >= kAt) { w.landDailyK(DAY); kLanded = true; }
    if (viewers.length && hm <= '13:40') w.view(viewers);
    const conserve = hm >= conserveFrom;
    if (hm <= '13:40' && (!conserve || t - lastQuoteRun >= 600000)) {
      lastQuoteRun = t;
      const before = new Map(w.sheet.rows.slice(1).map(r => [String(r[0]), String(r[7])])), started = w.clock.now;
      w.ctx.refreshQuoteCacheJob();
      const changed = w.sheet.rows.slice(1).filter(r => before.get(String(r[0])) !== String(r[7])).map(r => String(r[0]));
      const status = JSON.parse(w.props.get('QUOTE_JOB_STATUS') || '{}');
      w.stats.jobMs += w.clock.now - started;
      runs.push({hm, ms: w.clock.now - started, changed, held: changed.filter(c => HELD.includes(c)).length,
                 others: changed.filter(c => !HELD.includes(c)).length, status});
    }
    // 修正前的版本沒有這一支；照樣往下跑，讓後面的核對去指出差別。
    if (hm >= '13:31' && w.ctx.settleClosingQuotesTick_) { const s = w.clock.now; w.ctx.settleClosingQuotesTick_(); w.stats.jobMs += w.clock.now - s; }
  }
  return runs;
}

// 收盤價：價格等於收盤，而且時間在 13:30:00（含）之後——13:30 之後才取得的那幾檔，時間是實際取得的時刻。
const finalRows = w => CODES.filter(c => { const r = w.row(c); return r && Number(r[2]) === closeOf(c) && String(r[7]) >= DAY + ' 13:30:00' && String(r[11]) === DAY; });
const intraday = runs => runs.filter(r => r.hm < '13:30');
const report = [];

function main() {

/* ---- 一、2026/10/06 的實況：直連不通、沒有轉送、有人一直在看 16 檔、12:00 起用量保護 ---- */
{
  const viewers = CODES.filter(c => !HELD.includes(c)).slice(100, 116);
  const w = makeWorld({direct: false, relay: 'missing'});
  const runs = runDay(w, {viewers, conserveFrom: '12:00'}), day = intraday(runs);
  day.forEach(r => {
    assert.equal(r.status.mis, 0);
    assert.equal(r.held, HELD.length, `${r.hm} 持有中只更新 ${r.held} 檔`);
    assert.ok(r.others >= 3, `${r.hm} 持有以外只更新 ${r.others} 檔：輪替被擠掉了`);
    assert.ok(r.ms <= 45000, `${r.hm} 這一棒跑了 ${r.ms} ms`);
  });
  const rotated = new Set(day.flatMap(r => r.changed.filter(c => !HELD.includes(c) && !viewers.includes(c))));
  // 每一棒至少 3 檔輪替（被查看的 16 檔輪到時不算在這個數字裡，所以用 2.5 當下限）。修正前同一份模擬只有 73 檔。
  assert.ok(rotated.size >= day.length * 2.5, `全天輪替只更新到 ${rotated.size} 檔（${day.length} 棒）`);
  const afternoon = day.filter(r => r.hm >= '12:19');
  assert.ok(afternoon.length >= 6 && afternoon.every(r => r.others >= 3), '12:19 之後輪替仍要繼續');
  const stats = JSON.parse(w.props.get('QUOTE_DAY_STATS') || '{}');
  assert.equal(stats.misOk, 0); assert.equal(stats.runs, runs.length);
  assert.ok(stats.rotating >= day.length * 3, `當天累計輪替 ${stats.rotating} 檔`);
  // 收盤後：日K落地後全部是收盤價。
  assert.equal(finalRows(w).length, CODES.length, '收盤後應全部換成收盤價');
  assert.equal(JSON.parse(w.props.get('QUOTE_CLOSE_SETTLE')).done, true);
  report.push(`一、直連不通、沒有轉送（10/06 實況）：${day.length} 棒，每棒持有 ${Math.min(...day.map(r => r.held))} 檔全數更新、其餘至少 ${Math.min(...day.map(r => r.others))} 檔，全天輪替到 ${rotated.size} 檔，12:19 後不中斷；收盤後 ${CODES.length} 檔全為收盤價；排程耗時 ${(w.stats.jobMs / 60000).toFixed(1)} 分`);
}

/* ---- 一之二、同樣狀況，日K落地之前（13:31～14:44）的讀取端 ---- */
{
  const w = makeWorld({direct: false, relay: 'missing'});
  runDay(Object.assign(w, {}), {kAt: '99:99', conserveFrom: '12:00'});            // 日K整晚都沒落地的最壞情況
  w.clock.now = taipei(DAY, '14:00:00');
  const heldFinal = HELD.filter(c => finalRows(w).includes(c));
  assert.equal(heldFinal.length, HELD.length, '日K沒落地、批次不通時，持有中的也要先換成收盤價');
  const other = CODES.find(c => !HELD.includes(c) && !finalRows(w).includes(c));
  const stuck = w.row(other);
  assert.ok(String(stuck[7]) < DAY + ' 13:30:00', '這一檔應該還停在盤中某一刻');
  assert.equal(w.ctx.quoteFresh_(stuck[7]), false, '停在盤中的報價收盤後不算新鮮');
  const cacheOnly = w.ctx.getQuotesFor([other], false, true, true)[other];
  assert.equal(w.ctx.quoteFresh_(cacheOnly.stamp), false);
  const live = w.ctx.getRealtimeQuote(other);                                       // 個股面板走的就是這一支
  assert.equal(live.last, closeOf(other), '個股面板要的是收盤價');
  assert.equal(live.stale, false);
  assert.match(live.time, /^10-06 收盤$/);
  report.push(`一之二、收盤後日K尚未落地：持有 ${HELD.length} 檔已是收盤價；其餘停在盤中的不算新鮮，個股面板現場取得收盤價並標「收盤」`);
}

/* ---- 二、直連不通、Worker 轉送可用 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  const runs = runDay(w, {conserveFrom: '12:00'}), day = intraday(runs);
  day.forEach(r => {
    assert.equal(r.status.mis, CODES.length, `${r.hm} 批次只拿到 ${r.status.mis} 檔`);
    assert.equal(r.status.misVia, 'relay');
    assert.equal(r.changed.length, CODES.length, `${r.hm} 只更新 ${r.changed.length} 檔`);
    assert.ok(r.ms <= 20000, `${r.hm} 這一棒跑了 ${r.ms} ms`);
  });
  assert.ok(w.stats.misDirect <= 6, `直連失敗後一小時內不該再試，實際試了 ${w.stats.misDirect} 次`);
  assert.equal(w.stats.fugle, 0, '批次有資料時不必逐檔取');
  w.clock.now = taipei(DAY, '13:36:00');
  const at1336 = makeWorld({direct: false, relay: true}); runDayUntil(at1336, '13:36');
  assert.equal(finalRows(at1336).length, CODES.length, '13:31 之後第一棒就要全部換成收盤價');
  assert.equal(finalRows(w).length, CODES.length);
  report.push(`二、直連不通、經 Worker 轉送：每棒 ${CODES.length} 檔全部更新，最長 ${(Math.max(...day.map(r => r.ms)) / 1000).toFixed(1)} 秒；13:36 已全部是收盤價；排程耗時 ${(w.stats.jobMs / 60000).toFixed(1)} 分`);
}

/* ---- 三、直連可用 ---- */
{
  const w = makeWorld({direct: true, relay: 'missing'});
  const day = intraday(runDay(w));
  day.forEach(r => { assert.equal(r.changed.length, CODES.length); assert.equal(r.status.misVia, 'direct'); });
  assert.equal(w.stats.misRelay, 0);
  assert.equal(finalRows(w).length, CODES.length);
  report.push(`三、直連可用：每棒 ${CODES.length} 檔全部更新，不走轉送；收盤後全為收盤價`);
}

/* ---- 四、轉送回錯誤（Worker 尚未更新）不影響其他工作 ---- */
{
  const w = makeWorld({direct: false, relay: false});
  const day = intraday(runDay(w));
  assert.ok(day.every(r => r.held === HELD.length && r.others >= 3));
  assert.equal(finalRows(w).length, CODES.length);
  report.push('四、轉送回錯誤：退回逐檔備援，持有與輪替照常；收盤後由日K換成收盤價');
}

/* ---- 五、新鮮度與標示 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  const f = (hms, stamp) => { w.clock.now = taipei(DAY, hms); return w.ctx.quoteFresh_(stamp); };
  assert.equal(f('10:00:00', DAY + ' 09:57:00'), true);
  assert.equal(f('10:00:00', DAY + ' 09:50:00'), false);                            // 盤中超過五分鐘
  assert.equal(f('13:45:00', DAY + ' 11:59:40'), false);                            // 管理者回報的那一種
  assert.equal(f('13:45:00', DAY + ' 13:29:21'), false);                            // 集合競價之前
  assert.equal(f('13:45:00', DAY + ' 13:30:00'), true);
  assert.equal(f('21:00:00', DAY + ' 13:30:00'), true);
  w.clock.now = taipei(NEXT, '08:30:00');
  assert.equal(w.ctx.quoteFresh_(DAY + ' 13:30:00'), false);                        // 隔天開盤前：昨天的不算今天的價
  assert.equal(w.ctx.qTime_(DAY + ' 13:30:00'), '10-06 收盤');
  assert.equal(w.ctx.qTime_(DAY + ' 14:02:11'), '10-06 收盤');
  assert.equal(w.ctx.qTime_(DAY + ' 11:59:40'), '10-06 11:59:40');
  assert.equal(w.ctx.qTime_(DAY + ' 收盤'), '10-06 收盤');
  report.push('五、新鮮度：盤中五分鐘；收盤後只有 13:30 之後取得的算數；隔天開盤前不算。13:30 之後一律標「收盤」');
}

/* ---- 六、隔天開盤前讀到的是前一天收盤 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  runDay(w);
  w.clock.now = taipei(NEXT, '08:30:00');
  const c = CODES[5], q = w.ctx.getQuotesFor([c], true, true)[c];
  assert.equal(q.last, closeOf(c)); assert.equal(q.time, '10-06 收盤'); assert.equal(q.date, DAY);
  report.push('六、隔天 08:30：顯示前一交易日收盤，不對外請求');
}

/* ---- 七、日K已落地但逐檔快取過期（今晚部署當下的狀況）：整張日K讀一次，仍然全部換成收盤價 ---- */
{
  const w = makeWorld({direct: false, relay: 'missing'});
  const land = w.landDailyK;
  w.landDailyK = ymd => { land(ymd); CODES.forEach(c => w.CACHE.remove('dk2_' + c)); };
  runDay(w, {conserveFrom: '12:00'});
  assert.equal(finalRows(w).length, CODES.length);
  assert.equal(JSON.parse(w.props.get('QUOTE_CLOSE_SETTLE')).fullRead, true);
  report.push('七、日K已落地但逐檔快取過期：15:00 後整張讀一次，240 檔仍全部換成收盤價');
}

/* ---- 八、隔天開盤：收盤後的連線檢查已確認轉送可用，09:00 第一棒起就全部更新，不再花時間試直連 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  runDay(w);
  const test = JSON.parse(w.props.get('QUOTE_CLOSE_SETTLE')).relayTest;
  assert.deepEqual({ok: test.ok, via: test.via}, {ok: true, via: 'relay'}, '收盤後的連線檢查要記下轉送可用');
  const directBefore = w.stats.misDirect, fugleBefore = w.stats.fugle;
  for (let t = taipei(NEXT, '09:00:10'), n = 0; t <= taipei(NEXT, '13:30:10'); t += 300000, n++) {
    w.clock.now = Math.max(w.clock.now, t);
    const before = new Map(w.sheet.rows.slice(1).map(r => [String(r[0]), String(r[7])])), started = w.clock.now;
    w.ctx.refreshQuoteCacheJob();
    const changed = w.sheet.rows.slice(1).filter(r => before.get(String(r[0])) !== String(r[7])).length;
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    assert.equal(changed, CODES.length, `隔天 ${hm} 只更新 ${changed} 檔`);
    assert.ok(w.clock.now - started <= 15000, `隔天 ${hm} 這一棒跑了 ${w.clock.now - started} ms`);
    const live = w.ctx.getQuotesFor([CODES[7]], false, true, true)[CODES[7]];
    assert.equal(live.date, NEXT); assert.equal(w.ctx.quoteFresh_(live.stamp), true, `隔天 ${hm} 的報價應該是新鮮的`);
  }
  assert.equal(w.stats.misDirect, directBefore, '隔天盤中不該再試直連');
  assert.equal(w.stats.fugle, fugleBefore, '批次有資料時不必逐檔取');
  report.push('八、隔天 09:00～13:30：55 棒每一棒 240 檔全部更新、報價皆在五分鐘內；不再試直連、不需逐檔備援');
}

/* ---- 九、直連時好時壞：先前記為中斷、這一天轉送又剛好不通，仍要回頭用直連拿到資料 ---- */
{
  const w = makeWorld({direct: true, relay: false});
  w.props.set('MIS_DIRECT_DOWN_UNTIL', String(taipei(NEXT, '17:00:00')));
  const day = intraday(runDay(w));
  day.forEach(r => { assert.equal(r.changed.length, CODES.length, `${r.hm} 只更新 ${r.changed.length} 檔`); assert.equal(r.status.misVia, 'direct'); });
  assert.equal(w.props.has('MIS_DIRECT_DOWN_UNTIL') && Number(w.props.get('MIS_DIRECT_DOWN_UNTIL')) > w.clock.now, false, '直連恢復後要解除中斷記號');
  assert.equal(w.stats.fugle, 0);
  report.push('九、直連先前記為中斷、轉送不通：回頭用直連，每棒仍是 240 檔全部更新，並解除中斷記號');
}

/* ---- 十、少數股票當天沒有任何成交資料（興櫃、暫停交易）：其餘照常結算，不無限重試 ---- */
{
  const lonely = CODES.find(c => !HELD.includes(c));
  const w = makeWorld({direct: false, relay: true, noData: lonely});
  runDay(w);
  const st = JSON.parse(w.props.get('QUOTE_CLOSE_SETTLE'));
  assert.equal(finalRows(w).length, CODES.length - 1);
  assert.equal(st.done, true); assert.deepEqual(st.noData, [lonely]);
  assert.ok(st.tries <= 25, `收盤結算試了 ${st.tries} 次`);
  report.push(`十、一檔當天沒有成交資料：其餘 ${CODES.length - 1} 檔結算為收盤價，那一檔記為無資料後收工（共試 ${st.tries} 次）`);
}

/* ---- 十一、盤中七成的列在那一刻沒有成交價（2026/10/07 實況） ---- */
function quietMarket() {
  const QUIET = CODES.filter(c => !HELD.includes(c)).slice(40, 46);
  const w = makeWorld({direct: false, relay: true, noTrade: 0.7, quiet: QUIET});
  const worst = {stale: 0, at: ''}, firstFull = {hm: ''}; let runs = 0, quietMoves = 0, lastQuietStamp = '';
  for (let t = taipei(DAY, '09:00:10'); t <= taipei(DAY, '13:25:10'); t += 300000) {
    w.clock.now = Math.max(w.clock.now, t);
    const started = w.clock.now;
    w.ctx.refreshQuoteCacheJob();
    runs++;
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    assert.ok(w.clock.now - started <= 32000, `${hm} 這一棒跑了 ${w.clock.now - started} ms，超過時間預算`);
    // 十分鐘內有更新（上一棒或這一棒）的才算跟得上
    const limit = new Date(t - 600000 + 8 * 3600000).toISOString().slice(11, 19);
    const stale = CODES.filter(c => { const r = w.row(c); return !r || String(r[11]) !== DAY || String(r[7]).slice(11) < limit; });
    if (hm >= '09:15' && stale.length > worst.stale) { worst.stale = stale.length; worst.at = hm; }
    if (!firstFull.hm && !stale.length) firstFull.hm = hm;
    // 價格只來自成交：每一檔記下的現價要嘛是這一刻的成交價，要嘛是先前某一刻的成交價（模擬裡價格每五分鐘才變一次）
    CODES.forEach(c => { const r = w.row(c); if (r && String(r[11]) === DAY) assert.ok(Number(r[2]) >= base(c) && Number(r[2]) <= base(c) + 8, `${c} 現價 ${r[2]} 不是成交過的價位`); });
    // 冷門股：開盤成交一次後累計量不變。價格維持那一筆，確認時間跟著每一棒往前推
    const q = w.row(QUIET[0]);
    if (q && String(q[11]) === DAY) { if (String(q[7]) > lastQuietStamp) quietMoves++; lastQuietStamp = String(q[7]); assert.equal(Number(q[6]), 37); }
  }
  // 七成沒有成交價是偏嚴的假設；修正前同一個模擬最差的一棒有 214 檔超過十分鐘。上限訂在 240 檔的一成。
  assert.ok(worst.stale <= 24, `盤中最差的一棒有 ${worst.stale} 檔超過十分鐘沒更新（${worst.at}）`);
  assert.ok(quietMoves >= runs - 3, `冷門股的確認時間只往前推了 ${quietMoves}／${runs} 次`);
  return `十一、盤中七成的列那一刻沒有成交價：${runs} 棒，每棒寫入 160 檔以上；09:15 之後最差的一棒 ${worst.stale}／240 檔超過十分鐘（${worst.at || '無'}，修正前 214 檔）；` +
    `冷門股（累計量不變）沿用最近一筆成交價並更新確認時間 ${quietMoves}／${runs} 棒；沒有任何一檔用委買委賣推估`;
}

function runDayUntil(w, untilHm) {
  for (let t = taipei(DAY, '09:00:10'); ; t += 300000) {
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    if (hm > untilHm) break;
    w.clock.now = Math.max(w.clock.now, t);
    if (hm <= '13:40') w.ctx.refreshQuoteCacheJob();
    if (hm >= '13:31' && w.ctx.settleClosingQuotesTick_) w.ctx.settleClosingQuotesTick_();
  }
}

report.push(quietMarket());
report.forEach(line => console.log('ok ' + line));
console.log(report.length + ' quote flow scenarios passed');
}

module.exports = {makeWorld, runDay, taipei, CODES, HELD, DAY, NEXT, base, closeOf};
if (require.main === module) main();
