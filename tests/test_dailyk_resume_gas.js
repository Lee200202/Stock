// 補日K可續跑＋Fugle 回溯區間（2026/09/14）
//   一、Fugle：診斷工具用 46 天測過一次就被記成上限，之後補一年只拿 46 天（快取從 7/27 開始的原因）。
//   二、三百多檔一次跑不完：每次從停下的代號接著補、自動排續跑、整輪完成才停，資料不重複不遺漏。
//   三、錯誤分類、同時執行、開新一輪的時機、分批請求（step=dailyk）與證交所逐月路線。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const root = path.resolve(__dirname, '..');

function makeEnv(opt) {
  opt = opt || {};
  const clock = { now: Date.parse(opt.start || '2026-09-14T07:00:00Z') };   // 台北 15:00
  class FDate extends Date {
    constructor(...a) { if (a.length) { super(...a); } else { super(clock.now); } }
    static now() { return clock.now; }
  }
  const pad = n => String(n).padStart(2, '0');
  const fmt = (d, tz, f) => {
    const x = new Date(d.getTime() + 8 * 3600e3);
    const map = { yyyy: x.getUTCFullYear(), MM: pad(x.getUTCMonth() + 1), dd: pad(x.getUTCDate()),
                  HH: pad(x.getUTCHours()), mm: pad(x.getUTCMinutes()), ss: pad(x.getUTCSeconds()) };
    return f.replace(/yyyy|MM|dd|HH|mm|ss/g, k => map[k]);
  };
  const props = Object.assign({}, opt.props || {});
  const triggers = [];
  const logs = [];
  // 日K快取分頁（含表頭）
  const rows = [['代號', '日期', '開', '高', '低', '收', '量']].concat(opt.sheetRows || []);
  let maxRows = Math.max(rows.length, 1000);
  const sheet = {
    getLastRow: () => rows.length,
    getMaxRows: () => maxRows,
    insertRowsAfter: (r, n) => { maxRows += n; },
    getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
    getRange: (r, c, nr, nc) => ({
      getValues: () => rows.slice(r - 1, r - 1 + (nr || 1)).map(x => x.slice(c - 1, c - 1 + (nc || 1))),
      setValues: v => {
        assert(r - 1 + v.length <= maxRows, '寫入超出分頁列數（沒有先 insertRowsAfter）');
        v.forEach((row, i) => { rows[r - 1 + i] = row.slice(); });
      }
    }),
    deleteRows: (s, n) => {
      assert(s >= 2, '不能刪表頭');
      assert(rows.length - n >= 2 || s > 1, '');
      rows.splice(s - 1, n);
    },
    clearContents: () => { rows.length = 0; }
  };
  const fugle = opt.fugle || {};
  const stats = { requests: [] };
  const ctx = vm.createContext({
    console, JSON, Math, Date: FDate, String, Number, Object, Array, isFinite, RegExp, Error,
    Logger: { log: m => logs.push(String(m)) },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {}, remove: () => {} }) },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null),
      setProperty: (k, v) => { props[k] = String(v); },
      deleteProperty: k => { delete props[k]; }
    }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, waitLock: () => {}, releaseLock: () => {} }) },
    Utilities: { formatDate: fmt, sleep: ms => { clock.now += ms; }, getUuid: () => Math.random().toString(16).slice(2) + 'abcdef' },
    ScriptApp: {
      getProjectTriggers: () => triggers.slice(),
      deleteTrigger: t => { const i = triggers.indexOf(t); if (i >= 0) { triggers.splice(i, 1); } },
      newTrigger: h => ({ timeBased: () => ({ after: ms => ({ create: () => {
        const t = { h, ms, getHandlerFunction: () => h }; triggers.push(t); return t; } }) }) })
    },
    UrlFetchApp: { fetch: () => { throw new Error('測試不應真的連網'); } }
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Quoteservice.gs'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Cachebuilder.gs'), 'utf8'), ctx);

  // v48 補缺口會先讀現有K；假試算表只替代快取服務邊界。
  ctx.CACHE=ctx.CacheService.getScriptCache();
  ctx.getCachedDailyK=code=>rows.slice(1).filter(r=>r[0]===code).map(r=>({date:r[1],open:r[2],high:r[3],low:r[4],close:r[5],volume:r[6]}));
  const codes = opt.codes || [];
  Object.assign(ctx, {
    TZ: 'Asia/Taipei',
    getSheet_: () => sheet,
    withLock_: fn => fn(),
    fmtDate_: v => String(v || ''),
    trackedCodes_: () => codes.slice().reverse(),              // 故意亂序，確認內部會排序
    hasFugle_: () => opt.useFugle !== false,
    getFugleKey_: () => 'k',
    loadCodeMap_: () => ({ byCode: opt.codeMap || {} }),
    nowStamp_: () => fmt(new FDate(), '', 'yyyy/MM/dd HH:mm:ss'),
    fetchTwseMonth_: opt.twse || (() => []),
    // Fugle 歷史行情：預設單段超過 maxDays 天回 400，其餘回區間內每個平日一根
    fugleHistoricalOnce_: (code, from, to) => {
      clock.now += fugle.latencyMs || 700;
      stats.requests.push([code, from, to]);
      const failure = fugle.fail && fugle.fail(code, from, to);
      if (failure) { throw failure; }
      const span = Math.round((Date.parse(to) - Date.parse(from)) / 86400e3);
      if (fugle.maxDays && span > fugle.maxDays) { const e = new Error('Fugle HTTP 400：range too long'); e.httpCode = 400; throw e; }
      const out = [];
      for (let t = Date.parse(from); t <= Date.parse(to); t += 86400e3) {
        const d = new Date(t); const w = d.getUTCDay(); if (!w || w === 6) { continue; }
        const ds = d.toISOString().slice(0, 10).replace(/-/g, '/');
        out.push({ date: ds, open: 10, high: 11, low: 9, close: 10 + (fugle.version || 0), volume: 1 });
      }
      return out;
    }
  });
  return { ctx, clock, props, triggers, logs, rows, stats,
           byCode: c => rows.slice(1).filter(r => r[0] === c) };
}
const codeList = n => Array.from({ length: n }, (_, i) => String(1101 + i * 7));

// ================= 一、Fugle 回溯區間 =================
{
  // 舊版被污染的 46 天紀錄：新版不採信，照樣拿滿一年
  const env = makeEnv({ props: { FUGLE_MAX_DAYS: '46' }, codes: ['2330'] });
  const rows = env.ctx.fugleHistorical_('2330', '2025-09-14', '2026-09-14');
  assert.equal(rows[0].date, '2025/09/15', '舊的 46 天紀錄不能再截短區間｜實際從 ' + rows[0].date);
  assert.equal(rows[rows.length - 1].date, '2026/09/14');
  assert.equal(env.stats.requests.length, 1, '沒被拒絕時一次拿完');
  assert(!('FUGLE_RANGE_LIMIT' in env.props), '沒被拒絕過就不記上限');

  // 呼叫端自己要 46 天（診斷工具）：成功也不記成上限
  env.ctx.fugleHistorical_('3105', '2026-06-01', '2026-07-17');
  assert(!('FUGLE_RANGE_LIMIT' in env.props), '呼叫端要的區間短，不代表金鑰只能回溯這麼短');
}
{
  // 真的有上限（單段 > 180 天回 400）：分段拿回完整一年，並記住每段長度
  const env = makeEnv({ codes: ['2330'], fugle: { maxDays: 180 } });
  const rows = env.ctx.fugleHistorical_('2330', '2025-09-14', '2026-09-14');
  assert.equal(rows[0].date, '2025/09/15', '有上限也要分段補滿整個區間｜實際從 ' + rows[0].date);
  assert.equal(new Set(rows.map(r => r.date)).size, rows.length, '分段交界不能重複');
  const lim = JSON.parse(env.props.FUGLE_RANGE_LIMIT);
  assert.equal(lim.days, 180); assert.equal(lim.rejected, 270);
  // 下一檔直接用 180 天分段，不再撞 400
  env.stats.requests.length = 0;
  const rows2 = env.ctx.fugleHistorical_('2317', '2025-09-14', '2026-09-14');
  assert.equal(rows2[0].date, '2025/09/15');
  assert(env.stats.requests.every(q => Math.round((Date.parse(q[2]) - Date.parse(q[1])) / 86400e3) <= 180), '記住上限後每段都不超過上限');
  assert.equal(env.stats.requests.length, 3, '365 天以 180 天分段＝3 次請求');

  // 404 等非 400 錯誤原樣拋出，不縮短重試
  const env2 = makeEnv({ codes: ['9999'], fugle: { fail: () => Object.assign(new Error('Fugle HTTP 404：not found'), { httpCode: 404 }) } });
  assert.throws(() => env2.ctx.fugleHistorical_('9999', '2025-09-14', '2026-09-14'), /404/);
  assert.equal(env2.stats.requests.length, 1);
}

// ================= 二、三百多檔續跑 =================
{
  const codes = codeList(320);
  // 快取裡原本只有 7/27 起的舊資料，且有一檔（codes[5]）的舊列散在別處
  const old = [];
  codes.forEach(c => { old.push([c, '2026/07/27', 1, 1, 1, 1, 1]); old.push([c, '2026/09/11', 1, 1, 1, 1, 1]); });
  old.push(['0000', '2026/09/11', 1, 1, 1, 1, 1]);                      // 不在追蹤宇宙的列不能被動到
  const env = makeEnv({ codes, sheetRows: old, fugle: { latencyMs: 800 } });

  // 第一次：編輯器按 backfillDailyKJob（不帶參數，觸發器則會帶事件物件）
  let r = env.ctx.backfillDailyKJob({ authMode: 'FULL', triggerUid: 'x' });
  assert(!r.done, '320 檔一次跑不完');
  assert(r.processed > 50 && r.processed < 320, '應該做了一部分｜' + r.processed);
  const st1 = JSON.parse(env.props.dailyKBackfillState);
  assert.equal(st1.lastCode, codes.slice().sort()[r.processed - 1]);
  // 記成已完成的每一檔都真的寫進去了（時間到時最後一批不足 25 檔也會先寫回再記進度）
  codes.slice().sort().slice(0, r.processed).forEach(c =>
    assert.equal(env.byCode(c)[0][1], '2025/09/15', c + ' 記成已完成卻沒有寫入'));
  codes.slice().sort().slice(r.processed).forEach(c =>
    assert.equal(env.byCode(c)[0][1], '2026/07/27', c + ' 還沒輪到卻被動了'));
  assert.equal(env.triggers.filter(t => t.h === 'backfillDailyKContinueJob').length, 1, '應排一個續跑，而且只有一個');
  assert.equal(env.triggers[0].ms, 60000, '正常交棒是一分鐘後續跑（保險觸發器已換掉）');
  assert(!('dailyKBackfillLease' in env.props), '結束後要釋放寫入權');
  const firstDone = r.processed;
  // 已完成的代號：舊列換成完整一年；未完成的代號：舊列原封不動
  const doneCode = codes.slice().sort()[0], todoCode = codes.slice().sort()[319];
  assert.equal(env.byCode(doneCode)[0][1], '2025/09/15', '補過的代號要有一整年');
  assert.equal(env.byCode(doneCode).filter(x => x[1] === '2026/09/11').length, 1, '不能重複');
  assert.deepEqual(env.byCode(todoCode).map(x => x[1]), ['2026/07/27', '2026/09/11'], '還沒輪到的不能被動');

  // 觸發器續跑：從停下的代號接著做，不重抓前面的
  let rounds = 1;
  while (!r.done && rounds < 10) {
    env.clock.now += 60000;
    env.stats.requests.length = 0;
    const before = JSON.parse(env.props.dailyKBackfillState).lastCode;
    r = env.ctx.backfillDailyKContinueJob();
    rounds++;
    assert(env.stats.requests.every(q => q[0] > before), '續跑不能重抓已完成的代號');
  }
  assert(r.done, '續跑幾次後應完成｜' + JSON.stringify(r));
  assert.equal(r.processed, 320);
  assert(rounds >= 2 && rounds <= 4, '320 檔約兩三次完成｜實際 ' + rounds);
  assert.equal(env.triggers.filter(t => t.h === 'backfillDailyKContinueJob').length, 0, '整輪完成要把續跑觸發器清掉');
  // v40：整輪完成後排一個一次性觸發器接著重算持股追蹤與績效
  assert.equal(env.triggers.filter(t => t.h === 'afterDailyKDoneJob').length, 1, '整輪完成要排補日K之後的重算');
  const st = JSON.parse(env.props.dailyKBackfillState);
  assert(st.finishedAt, '整輪完成要記完成時間');
  assert.equal(st.ok, 320);
  codes.forEach(c => {
    const b = env.byCode(c);
    assert.equal(b[0][1], '2025/09/15', c + ' 應涵蓋一整年');
    assert.equal(new Set(b.map(x => x[1])).size, b.length, c + ' 不能有重複日期');
  });
  assert.equal(env.byCode('0000').length, 1, '不在追蹤宇宙的列不能被刪');
  assert(firstDone < 320);

  // 完成後續跑觸發器再被喚醒：什麼都不做
  env.stats.requests.length = 0;
  r = env.ctx.backfillDailyKContinueJob();
  assert(r.done); assert.equal(env.stats.requests.length, 0, '已完成的一輪，續跑不能自己開新一輪');
  assert.equal(env.triggers.filter(t => t.h === 'backfillDailyKContinueJob').length, 0);
  assert.equal(env.triggers.filter(t => t.h === 'afterDailyKDoneJob').length, 1, '空轉的續跑不能再多排一次重算');

  // 同一天再手動按一次：開新一輪（使用者明確要求重補）
  r = env.ctx.backfillDailyKJob();
  assert.notEqual(JSON.parse(env.props.dailyKBackfillState).runId, st.runId, '已完成後再按要開新一輪');
}

// ================= 三、錯誤、同時執行、開新一輪、分批、證交所 =================
{
  // 暫時性錯誤（429）：不推進，連續三次才記失敗往下走
  const codes = ['1101', '1102', '1103'];
  let n429 = 0;
  const env = makeEnv({ codes, fugle: { fail: c => (c === '1102' ? (n429++, Object.assign(new Error('Fugle HTTP 429'), { httpCode: 429 })) : null) } });
  let r = env.ctx.backfillDailyKJob();
  assert(!r.done && r.stop === 'transient', JSON.stringify(r));
  assert.equal(JSON.parse(env.props.dailyKBackfillState).lastCode, '1101', '429 那一檔不能推進');
  assert.equal(env.triggers.length, 1, '暫時性錯誤要排續跑');
  r = env.ctx.backfillDailyKContinueJob();
  assert(!r.done, '第二次仍 429，仍不推進');
  r = env.ctx.backfillDailyKContinueJob();
  assert(r.done, '第三次 429 記失敗、往下做完｜' + JSON.stringify(r));
  const st = JSON.parse(env.props.dailyKBackfillState);
  assert.equal(st.failed, 1); assert.equal(st.ok, 2);
  assert(/1102/.test(st.failedList[0]));
  assert.equal(n429, 3);
}
{
  // 金鑰無效（401）：整輪停下，不排續跑，進度保留
  const env = makeEnv({ codes: ['1101', '1102', '1103'], fugle: { fail: c => (c === '1102' ? Object.assign(new Error('Fugle HTTP 401'), { httpCode: 401 }) : null) } });
  const r = env.ctx.backfillDailyKJob();
  assert(r.fatal && !r.done, JSON.stringify(r));
  assert.equal(env.triggers.length, 0, '金鑰無效不能一直排續跑');
  assert.equal(JSON.parse(env.props.dailyKBackfillState).lastCode, '1101', '停在出錯之前，修好後接著做');
  // 分批請求遇到同樣狀況：回 ok:false，讓 GitHub 那邊標失敗而不是重打四十次
  const c = env.ctx.backfillDailyKChunk_(45);
  assert.equal(c.ok, false); assert(/401/.test(c.error));
}
{
  // 404 查無此股：記失敗、繼續下一檔
  const env = makeEnv({ codes: ['1101', '2155', '3665'], fugle: { fail: c => (c === '2155' ? Object.assign(new Error('Fugle HTTP 404'), { httpCode: 404 }) : null) } });
  const r = env.ctx.backfillDailyKJob();
  assert(r.done); assert.equal(r.failed, 1); assert.equal(r.ok, 2);
}
{
  // 另一個正在寫：不重複執行，回報忙碌並排續跑等它
  const env = makeEnv({ codes: ['1101'], props: { dailyKBackfillLease: JSON.stringify({ token: 'other', until: Date.parse('2026-09-14T07:05:00Z') }) } });
  const r = env.ctx.backfillDailyKJob();
  assert(r.busy && !r.done); assert.equal(env.stats.requests.length, 0);
  assert.equal(env.triggers.length, 1);
  assert.equal(env.props.dailyKBackfillLease && JSON.parse(env.props.dailyKBackfillLease).token, 'other', '不能釋放別人的寫入權');
  // 租約過期（例如那次執行被硬砍）就可以接手
  env.clock.now = Date.parse('2026-09-14T07:06:00Z');
  const r2 = env.ctx.backfillDailyKJob();
  assert(r2.done && !r2.busy, '租約過期後應能接手補完｜' + JSON.stringify(r2));
  assert.equal(env.byCode('1101').length > 200, true);
  // 正在寫入時不准重設（那一段結束會把進度寫回去，重設等於沒設還會搶）
  env.props.dailyKBackfillLease = JSON.stringify({ token: 'x', until: env.clock.now + 60000 });
  assert.equal(env.ctx.resetDailyKCursor(), false);
  delete env.props.dailyKBackfillLease;
  assert(env.ctx.resetDailyKCursor() === true);
}
{
  // 前一天沒補完的一輪：今天手動或排程會開新一輪；續跑觸發器則接著做完它
  const prev = { runId: 'old', day: '2026/09/13', from: '2025-09-13', to: '2026-09-13', lastCode: '1101', ok: 1, failed: 0,
                 skipped: 0, failedList: [], rounds: 1, retryCode: '', retryN: 0, startedAt: '', updatedAt: '', finishedAt: '', lastError: '' };
  let env = makeEnv({ codes: ['1101', '1102'], props: { dailyKBackfillState: JSON.stringify(prev) } });
  env.ctx.backfillDailyKJob();
  let st = JSON.parse(env.props.dailyKBackfillState);
  assert.notEqual(st.runId, 'old'); assert.equal(st.to, '2026-09-14'); assert.equal(st.ok, 2);
  env = makeEnv({ codes: ['1101', '1102'], props: { dailyKBackfillState: JSON.stringify(prev) } });
  env.ctx.backfillDailyKContinueJob();
  st = JSON.parse(env.props.dailyKBackfillState);
  assert.equal(st.runId, 'old', '續跑只接著做'); assert.equal(st.ok, 2); assert(st.finishedAt);
  assert.deepEqual(env.stats.requests.map(q => q[0]), ['1102']);
  assert.equal(env.stats.requests[0][2], '2026-09-11', '接著做的那一輪沿用開輪時的區間');
}
{
  // 分批請求（網站／GitHub step=dailyk）：每次約 45 秒，共用同一份進度，由呼叫端重打到 done
  const codes = codeList(60);
  const env = makeEnv({ codes, fugle: { latencyMs: 900 } });
  let r, calls = 0;
  do { r = env.ctx.backfillDailyKChunk_(45); calls++; env.clock.now += 2000; } while (!r.done && calls < 40);
  assert(r.done, '分批應能跑完｜' + JSON.stringify(r));
  assert.equal(r.processed, 60); assert.equal(r.total, 60);
  assert(calls >= 3 && calls <= 8, '60 檔、每批約 30 秒，應在數批內完成｜' + calls);
  assert.equal(env.triggers.length, 0, '分批請求由呼叫端驅動，不排觸發器');
  assert(typeof r.note === 'string' && /進度 60\/60/.test(r.note));
  codes.forEach(c => assert.equal(new Set(env.byCode(c).map(x => x[1])).size, env.byCode(c).length));
  // 舊版數字游標的屬性會被 reset 一併清掉
  env.props.dailyKCursor = '17';
  assert(env.ctx.resetDailyKCursor());
  assert(!('dailyKCursor' in env.props) && !('dailyKBackfillState' in env.props));
}
{
  // 證交所路線（沒有 Fugle 金鑰）：上櫃略過；已有的舊月份不重抓；只替換真的抓到資料的月份
  const got = [];
  const env = makeEnv({
    useFugle: false, codes: ['2330', '3105'],
    codeMap: { '2330': { market: '上市' }, '3105': { market: '上櫃' } },
    sheetRows: [['2330', '2025/12/05', 1, 1, 1, 1, 1], ['2330', '2026/08/05', 1, 1, 1, 1, 1], ['2330', '2026/09/01', 1, 1, 1, 1, 1]],
    twse: (code, ym) => {
      got.push(ym);
      if (ym === '2026/08') { return []; }                         // 模擬這個月連線失敗
      return [{ date: ym + '/10', open: 2, high: 2, low: 2, close: 2, volume: 1 }];
    }
  });
  const r = env.ctx.backfillDailyKJob();
  assert(r.done, JSON.stringify(r));
  assert.equal(r.skipped, 1, '上櫃股沒有 Fugle 金鑰要略過');
  assert(got.includes('2025/12'), '舊月份只有一根，仍須補缺口');
  assert(got.includes('2026/09') && got.includes('2026/08'), '最近兩個月一律重抓');
  const d = env.byCode('2330').map(x => x[1]).sort();
  assert(d.includes('2026/08/05'), '抓取失敗的月份不能把快取裡的舊資料刪掉');
  assert(d.includes('2026/09/01') && d.includes('2026/09/10'), '補缺口保留既有收盤日K');
  assert(d.includes('2025/12/05'));
}

{
  // 觸發器已滿 20 個：排不進續跑也要照常補、進度照常推進，並講清楚原因
  const env = makeEnv({ codes: ['1101', '1102'] });
  env.ctx.ScriptApp.newTrigger = () => ({ timeBased: () => ({ after: () => ({ create: () => { throw new Error('This script has too many triggers.'); } }) }) });
  const r = env.ctx.backfillDailyKJob();
  assert(r.done && r.ok === 2, JSON.stringify(r));
  assert(env.logs.some(l => l.includes('無法排定自動續跑')));
}

console.log('PASS: Fugle range no longer poisoned by short probes and splits long ranges; 320-code daily K backfill resumes by code with auto-continue, no duplicates; transient/fatal/404 handling, lease, new-pass rules, chunk path, TWSE month safety.');
