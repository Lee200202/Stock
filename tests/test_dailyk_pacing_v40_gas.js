// v40（2026/09/16）：補日K不再固定只做 4 分鐘，改以富果每分鐘次數控速、用滿單次執行 6 分鐘；
// 一年區間一次請求；每日排程移到 16:45，整輪完成後自動重算持股追蹤與績效。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

function makeEnv(opt) {
  opt = opt || {};
  const clock = { now: Date.parse(opt.start || '2026-09-16T08:45:00Z') };   // 台北 16:45
  class FDate extends Date {
    constructor(...a) { if (a.length) { super(...a); } else { super(clock.now); } }
    static now() { return clock.now; }
  }
  const pad = n => String(n).padStart(2, '0');
  const fmt = (d, tz, f) => {
    const x = new Date(d.getTime() + 8 * 3600e3);
    const m = { yyyy: x.getUTCFullYear(), MM: pad(x.getUTCMonth() + 1), dd: pad(x.getUTCDate()), HH: pad(x.getUTCHours()), mm: pad(x.getUTCMinutes()), ss: pad(x.getUTCSeconds()) };
    return f.replace(/yyyy|MM|dd|HH|mm|ss/g, k => m[k]);
  };
  const props = Object.assign({}, opt.props);
  const cacheStore = opt.cacheStore || {};
  const triggers = [], logs = [], requests = [];
  const rows = [['代號', '日期', '開', '高', '低', '收', '量']];
  let maxRows = 1000;
  const sheet = {
    getLastRow: () => rows.length, getMaxRows: () => maxRows, insertRowsAfter: (r, n) => { maxRows += n; },
    getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
    getRange: (r, c, nr, nc) => ({
      getValues: () => rows.slice(r - 1, r - 1 + (nr || 1)).map(x => x.slice(c - 1, c - 1 + (nc || 1))),
      setValues: v => { clock.now += opt.writeMs || 0; v.forEach((row, i) => { rows[r - 1 + i] = row.slice(); }); }
    }),
    deleteRows: (s, n) => { rows.splice(s - 1, n); }
  };
  const newTrigger = h => {
    const spec = { h };
    const t = { getHandlerFunction: () => h, spec };
    const chain = {
      timeBased: () => chain,
      after: ms => { spec.after = ms; return chain; },
      atHour: x => { spec.hour = x; return chain; },
      nearMinute: x => { spec.minute = x; return chain; },
      everyDays: x => { spec.everyDays = x; return chain; },
      create: () => { if (opt.triggersFull) { throw new Error('This script has too many triggers.'); } triggers.push(t); return t; }
    };
    return chain;
  };
  const ctx = vm.createContext({
    console, JSON, Math, Date: FDate, String, Number, Object, Array, isFinite, RegExp, Error,
    Logger: { log: m => logs.push(String(m)) },
    CacheService: { getScriptCache: () => ({ get: k => (k in cacheStore ? cacheStore[k] : null), put: (k, v) => { cacheStore[k] = String(v); }, remove: k => { delete cacheStore[k]; } }) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = String(v); }, deleteProperty: k => { delete props[k]; } }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, waitLock: () => {}, releaseLock: () => {} }) },
    Utilities: { formatDate: fmt, sleep: ms => { clock.now += ms; }, getUuid: () => Math.random().toString(16).slice(2) + 'abcdef' },
    ScriptApp: { getProjectTriggers: () => triggers.slice(), deleteTrigger: t => { const i = triggers.indexOf(t); if (i >= 0) { triggers.splice(i, 1); } }, newTrigger },
    UrlFetchApp: { fetch: () => { throw new Error('測試不應真的連網'); } }
  });
  vm.runInContext(read('apps-script/Quoteservice.gs'), ctx);
  vm.runInContext(read('apps-script/Cachebuilder.gs'), ctx);
  // v48 補缺口會先讀現有K；假試算表只替代快取服務邊界。
  ctx.CACHE=ctx.CacheService.getScriptCache();
  ctx.getCachedDailyK=code=>rows.slice(1).filter(r=>r[0]===code).map(r=>({date:r[1],open:r[2],high:r[3],low:r[4],close:r[5],volume:r[6]}));
  const codes = opt.codes || [];
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', getSheet_: () => sheet, withLock_: fn => fn(), fmtDate_: v => String(v || ''),
    trackedCodes_: () => codes.slice(), hasFugle_: () => true, getFugleKey_: () => 'k',
    loadCodeMap_: () => ({ byCode: {} }), nowStamp_: () => fmt(new FDate(), '', 'yyyy/MM/dd HH:mm:ss'),
    fugleHistoricalOnce_: (code, from, to) => {
      requests.push({ code, from, to, at: clock.now });
      clock.now += opt.latencyMs || 700;
      const span = Math.round((Date.parse(to) - Date.parse(from)) / 86400e3);
      if (span >= 365 || (opt.maxDays && span > opt.maxDays)) { const e = new Error('Fugle HTTP 400：range must be less than 1 year'); e.httpCode = 400; throw e; }
      return [{ date: to.replace(/-/g, '/'), open: 1, high: 1, low: 1, close: 1, volume: 1000 }];
    }
  });
  return { ctx, clock, props, triggers, logs, requests, rows, cacheStore };
}
const codeList = n => Array.from({ length: n }, (_, i) => String(1101 + i * 7));
const maxInWindow = (times, ms) => { let best = 0, j = 0; for (let i = 0; i < times.length; i++) { while (times[i] - times[j] >= ms) j++; best = Math.max(best, i - j + 1); } return best; };

// ================================================================== 一、一年區間一次請求
{
  const env = makeEnv();
  const rng = env.ctx.dailyKRange_();
  assert.deepStrictEqual([rng.from, rng.to], ['2025-09-17', '2026-09-16'], '一年少一天：富果單次查詢要小於一年');
  // 月底與閏年：一樣小於一年
  for (const [now, from] of [['2026-03-31T08:00:00Z', '2025-04-01'], ['2028-02-29T08:00:00Z', '2027-03-02']]) {
    const e = makeEnv({ start: now }); assert.strictEqual(e.ctx.dailyKRange_().from, from, now);
  }
  // 舊版留下「365 天被拒、270 天可以」的紀錄：不再當上限，一檔一次請求
  env.props.FUGLE_RANGE_LIMIT = JSON.stringify({ days: 270, rejected: 365, at: env.clock.now });
  env.ctx.fugleHistorical_('2385', rng.from, rng.to);
  assert.strictEqual(env.requests.length, 1, '一年少一天應一次拿完｜' + JSON.stringify(env.requests));
  // 真正的方案上限（例如 180 天被拒 270）仍然照分段
  const env2 = makeEnv({ maxDays: 180, props: { FUGLE_RANGE_LIMIT: JSON.stringify({ days: 180, rejected: 270, at: Date.parse('2026-09-16T08:45:00Z') }) } });
  env2.ctx.fugleHistorical_('2385', rng.from, rng.to);
  assert.strictEqual(env2.requests.length, 3);
  // 另外兩個抓日K的地方也用同一個區間
  const cb = read('apps-script/Cachebuilder.gs');
  const body = name => { const a = cb.indexOf('function ' + name + '('); return cb.slice(a, cb.indexOf('\nfunction ', a + 10)); };
  assert(/dailyKRange_\(\)/.test(body('fetchCodeOnDemand')) && /dailyKRange_\(\)/.test(body('fillMissingDailyK_')));
  assert(!/setMonth\(d\.getMonth\(\) - KLINE_MONTHS\);\s*var from/.test(cb), '不再自己算剛好一年的區間');
}

// ================================================================== 二、控速：每分鐘不超過 55 次，請求本身的時間不重複等
{
  const env = makeEnv({ latencyMs: 300 });
  for (let i = 0; i < 120; i++) { env.ctx.fugleHistorical_('2385', '2026-01-01', '2026-01-10'); }
  const t = env.requests.map(r => r.at);
  assert(maxInWindow(t, 60000) <= 55, '任一分鐘內不能超過 55 次｜' + maxInWindow(t, 60000));
  for (let i = 1; i < t.length; i++) assert(t[i] - t[i - 1] >= 1091, '相鄰請求至少相隔 60/55 秒');
  // 請求本身就要 1.5 秒時不再多等（先前固定再睡 1.1 秒）
  const slow = makeEnv({ latencyMs: 1500 });
  const t0 = slow.clock.now;
  for (let i = 0; i < 20; i++) { slow.ctx.fugleHistorical_('2385', '2026-01-01', '2026-01-10'); }
  assert(slow.clock.now - t0 <= 20 * 1500 + 1091, '慢的請求不應再額外等待｜' + (slow.clock.now - t0));
  // 跨執行：上一次執行最後一次請求的時間記在 CacheService，下一次執行（新的程式環境）照同一個節奏
  const shared = {};
  const a = makeEnv({ latencyMs: 100, cacheStore: shared });
  a.ctx.fugleHistorical_('2385', '2026-01-01', '2026-01-10');
  const b = makeEnv({ latencyMs: 100, cacheStore: shared, start: new Date(a.clock.now).toISOString() });
  b.ctx.fugleHistorical_('2330', '2026-01-01', '2026-01-10');
  assert(b.requests[0].at - a.requests[0].at >= 1091, '接力的下一次執行不能緊接著打｜' + (b.requests[0].at - a.requests[0].at));
}

// ================================================================== 三、單次執行用滿 6 分鐘、保留收尾時間
{
  const codes = codeList(400);
  const env = makeEnv({ codes, latencyMs: 700 });
  const start = env.clock.now;
  const r = env.ctx.backfillDailyKJob();
  const lastReq = env.requests[env.requests.length - 1].at;
  assert(!r.done && r.processed > 250, '6 分鐘、每分鐘 55 次應做到兩百多檔（舊版 4 分鐘固定睡 1.1 秒約 130 檔）｜' + r.processed);
  assert(lastReq - start <= 6 * 60e3 - 45e3, '最後一次請求要在 6 分鐘前 45 秒以前｜' + (lastReq - start) / 1000);
  assert(env.clock.now - start < 6 * 60e3, '整次執行（含寫回）不能超過 6 分鐘');
  assert.strictEqual(r.processed, env.rows.slice(1).length, '記成完成的每一檔都已寫入');
  assert(maxInWindow(env.requests.map(x => x.at), 60000) <= 55);
  assert.strictEqual(env.requests.length, r.processed, '一檔一次請求');
  // 寫入很慢時（試算表很大）：依實際寫入耗時提早收尾，仍在 6 分鐘內結束
  const slow = makeEnv({ codes, latencyMs: 700, writeMs: 25000 });
  const s0 = slow.clock.now;
  const r2 = slow.ctx.backfillDailyKJob();
  assert(slow.clock.now - s0 < 6 * 60e3, '寫入慢也要在 6 分鐘內結束｜' + (slow.clock.now - s0) / 1000);
  assert(r2.processed < r.processed);
  // 呼叫端指定的截止時間（網站分批 45 秒）照舊
  const chunk = makeEnv({ codes, latencyMs: 700 });
  const c0 = chunk.clock.now;
  chunk.ctx.backfillDailyKChunk_(45);
  assert(chunk.clock.now - c0 < 45e3, '分批請求維持 45 秒內');
}

// ================================================================== 四、整輪完成 → 一分鐘後重算持股追蹤 → 記錄績效
{
  const env = makeEnv({ codes: codeList(5) });
  const calls = [];
  env.ctx.rebuildHoldingsTrackerJob = () => calls.push('tracker');
  env.ctx.snapshotPerformanceJob = () => calls.push('perf');
  const r = env.ctx.backfillDailyKJob();
  assert(r.done && r.finished);
  const after = env.triggers.filter(t => t.getHandlerFunction() === 'afterDailyKDoneJob');
  assert.strictEqual(after.length, 1); assert.strictEqual(after[0].spec.after, 60000);
  assert(env.logs.some(l => /自動重算持股追蹤並記錄當日績效/.test(l)));
  const res = env.ctx.afterDailyKDoneJob();
  assert.deepStrictEqual(calls, ['tracker', 'perf'], '順序：先追蹤、後績效');
  assert(res.tracker && res.perf);
  assert.strictEqual(env.triggers.filter(t => t.getHandlerFunction() === 'afterDailyKDoneJob').length, 0, '執行時刪掉自己的一次性觸發器');
  // 追蹤失敗就不記績效（否則記到的是舊的）
  env.ctx.rebuildHoldingsTrackerJob = () => { throw new Error('boom'); };
  calls.length = 0;
  assert.deepStrictEqual(Object.assign({}, env.ctx.afterDailyKDoneJob()), { tracker: false, perf: false });
  assert.deepStrictEqual(calls, []);
  // 沒有進行中的一輪、續跑觸發器空轉：不排重算
  const idle = makeEnv({ codes: codeList(3) });
  idle.ctx.backfillDailyKContinueJob();
  assert.strictEqual(idle.triggers.filter(t => t.getHandlerFunction() === 'afterDailyKDoneJob').length, 0);
  // 觸發器已滿：照常完成，並說明要手動執行
  const full = makeEnv({ codes: codeList(2), triggersFull: true });
  assert(full.ctx.backfillDailyKJob().done);
  assert(full.logs.some(l => /請手動執行 afterDailyKDoneJob/.test(l)));
}

// ================================================================== 五、刷新鏈的分批請求：今天 16:30 後的完整一輪做完就不重抓
{
  const env = makeEnv({ codes: codeList(4), start: '2026-09-16T10:00:00Z' });   // 台北 18:00
  env.props.dailyKBackfillState = JSON.stringify({ runId: 'x', day: '2026/09/16', from: '2025-09-17', to: '2026-09-16', lastCode: '1122',
    ok: 4, failed: 0, skipped: 0, failedList: [], rounds: 1, startedAt: '2026/09/16 16:45:10', finishedAt: '2026/09/16 16:49:02', updatedAt: '', lastError: '' });
  const r = env.ctx.backfillDailyKChunk_(45);
  assert(r.done && env.requests.length === 0, JSON.stringify(r));
  assert(/16:45 開始的完整一輪已於 16:49:02 完成，不重抓/.test(r.note));
  // 下午 14:36 那種資料還不是最終版的一輪：照舊重開
  const early = makeEnv({ codes: codeList(4), start: '2026-09-16T10:00:00Z' });
  early.props.dailyKBackfillState = env.props.dailyKBackfillState.replace('16:45:10', '14:36:00');
  early.ctx.backfillDailyKChunk_(45);
  assert(early.requests.length > 0, '16:30 以前開始的一輪不算最終資料');
}

// ================================================================== 六、排程：只移動補日K這一個觸發器
{
  const env = makeEnv();
  const keep = ['everyFiveMinJob', 'cmoneyPollJob', 'rebuildHoldingsTrackerJob', 'snapshotPerformanceJob'];
  keep.concat(['backfillDailyKJob']).forEach(h => env.ctx.ScriptApp.newTrigger(h).timeBased().atHour(14).nearMinute(35).everyDays(1).create());
  const r = env.ctx.rescheduleDailyKTrigger();
  assert.strictEqual(r.removed, 1);
  const bk = env.triggers.filter(t => t.getHandlerFunction() === 'backfillDailyKJob');
  assert.strictEqual(bk.length, 1);
  assert.deepStrictEqual([bk[0].spec.hour, bk[0].spec.minute, bk[0].spec.everyDays], [16, 45, 1]);
  keep.forEach(h => assert.strictEqual(env.triggers.filter(t => t.getHandlerFunction() === h).length, 1, h + ' 不能被動到'));
  const setup = read('apps-script/Setup.gs');
  assert(/newTrigger\('backfillDailyKJob'\)\.timeBased\(\)\.atHour\(DK_DAILY_HOUR_\)\.nearMinute\(DK_DAILY_MINUTE_\)/.test(setup));
  assert(!/atHour\(14\)\.nearMinute\(35\)/.test(setup));
  assert(/<td>16:45<\/td><td>backfillDailyKJob<\/td>/.test(read('apps-script/Tech.html')));
  assert(/onceAfter_\(today, 'hourly', hhmm, 1345, 'aggregateSnapshotJob'/.test(setup), '13:45 是快照聚合成 60 分 K，不是補日K');
}

console.log('PASS: v40 daily K backfill paced at 55 req/min (also across executions), uses the full 6-minute execution with a measured write reserve, one request per stock (364-day range), chains tracker → performance after a finished round, chunk skips a finished post-16:30 round, trigger moved to 16:45 only.');
