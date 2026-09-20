// 績效走勢不得早於本站真正有紀錄的那一天（2026/09/20）。
//
// 圖上出現一條從四月就開始的線，但本站的第一筆紀錄是 7/08——那段是憑空的。
// 成因：進場日可以被「回述成本」往前推（講者說「會員在 2135 買的」，
// 程式往回找到日K快取裡某天曾成交在 2135，就把進場日訂在那天），
// 而日K快取往回涵蓋很長一段歷史，於是持有狀態被一路延伸到沒有資料的日子。
//
// 回合本身這樣判是合理的（成本確實是那時候的）。要擋的是「據此畫出走勢」。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const root = path.resolve(__dirname, '..');

function weekdays(from, to) {
  const out = [];
  for (let d = new Date(from + 'T00:00:00Z'); d <= new Date(to + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + 1)) {
    const w = d.getUTCDay(); if (w && w < 6) out.push(d.toISOString().slice(0, 10).replace(/-/g, '/'));
  }
  return out;
}
// 日K快取從 4 月就有，但本站的紀錄要到 7 月才開始
const DAYS = weekdays('2026-04-01', '2026-09-11');
const FLOOR = '2026/07/08';

function run(opts) {
  opts = opts || {};
  const kRows = [];
  DAYS.forEach((d, i) => { kRows.push({ '代號': '2330', '日期': d, '收': 2000 + i }); });

  // 進場日被回述成本推到 4/02——遠早於本站的第一筆紀錄
  const tracker = [{
    '代號': '2330', '股票名稱': '台積電',
    '回合JSON': JSON.stringify([{ o: '2026/04/02', od: '2026/04/02', e: 2000, c: '', x: null, k: 'buy', s: 0 }])
  }];

  // 本站的來源紀錄：最早 7/08
  const trades = opts.noRecords ? [] : [
    { '日期': FLOOR, '股票名稱': '台積電', '代號': '2330', '方向': '買入' },
    { '日期': '2026/08/03', '股票名稱': '台積電', '代號': '2330', '方向': '買入' }
  ];
  const holds = opts.noRecords ? [] : [
    { '日期': '2026/07/20', '股票名稱': '台積電', '代號': '2330', '目前立場': '續抱' }
  ];

  // 既有的每日績效：從 4 月就有（要被清掉的那些）
  let perf = [['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例']]
    .concat(DAYS.map(d => [d, 1, 1, 1, 100]));

  const logs = [];
  const ctx = vm.createContext({
    console, JSON, Math, Date, String, Number, Object, Array, isFinite,
    Logger: { log: m => logs.push(String(m)) },
    CACHE: { get: () => null, put: () => {}, remove: () => {} },
    readSheetObjects_: n => n === '日K快取' ? kRows
      : n === '持股追蹤' ? tracker
        : n === '操作紀錄' ? trades
          : n === '會員持股' ? holds : [],
    fmtDate_: s => String(s || ''), withLock_: fn => fn(),
    getSheet_: () => ({
      getDataRange: () => ({ getValues: () => perf.map(r => r.slice()) }),
      clearContents: () => { perf = []; },
      getRange: (r, c, n, m) => ({ setValues: v => { v.forEach((row, i) => { perf[r - 1 + i] = row; }); } })
    })
  });
  const src = fs.readFileSync(path.join(root, 'apps-script/Cachebuilder.gs'), 'utf8');
  const m = src.match(/^function rebuildPerformanceHistoryJob\(fromDate\) \{[\s\S]*?^\}/m);
  assert(m, 'rebuildPerformanceHistoryJob not found');
  vm.runInContext(m[0], ctx);
  const res = ctx.rebuildPerformanceHistoryJob(opts.fromDate);
  const rows = perf.slice(1).filter(r => r && r[0]);
  return { res, logs, rows, dates: rows.map(r => r[0]) };
}

// ---- 一、全量重算：資料起點之前不產生任何點 ----
{
  const r = run();
  assert(r.res.ok, JSON.stringify(r.res));
  assert(r.dates.length, '不該整條空掉');
  assert.equal(r.dates[0], FLOOR, '第一個點必須是本站最早的紀錄日，得到 ' + r.dates[0]);
  assert(!r.dates.some(d => d < FLOOR),
    '出現了早於 ' + FLOOR + ' 的點：' + r.dates.filter(d => d < FLOOR).join('、'));
  assert(r.logs.some(l => l.indexOf('最早的紀錄是 ' + FLOOR) >= 0),
    '日誌要寫明起點與被排除的天數：\n' + r.logs.join('\n'));
}

// ---- 二、舊的錯誤資料要被清掉，不是只跳過 ----
{
  // 重算前 perf 從 4/01 就有（run() 裡預先塞的）；重算後不該還在
  const r = run();
  assert(!r.rows.some(row => String(row[0]) < FLOOR),
    '重算後仍留著資料起點之前的舊列，等於把「沒有資料」畫成一條線');
}

// ---- 三、指定起點時，保留的舊列也要擋 ----
{
  // 從 8/03 往後重算：8/03 之前的舊點原樣保留是對的，
  // 但 7/08 之前那些本來就不該存在的，不能因為「在起點之前」就被保留下來。
  const r = run({ fromDate: '2026/08/03' });
  assert(r.res.ok);
  assert(!r.dates.some(d => d < FLOOR),
    '指定起點時漏擋了資料起點之前的舊列：' + r.dates.filter(d => d < FLOOR).join('、'));
  assert(r.dates.indexOf('2026/07/20') >= 0,
    '資料起點之後、重算起點之前的舊點應該保留');
}

// ---- 四、兩張來源表都空的時候不設限，維持原本行為 ----
{
  // 沒有任何來源紀錄時硬設起點會把整條曲線清成空白，
  // 那比多幾個點糟得多；這種情況本來就沒有東西可畫。
  const r = run({ noRecords: true });
  assert(r.res.ok);
  assert(r.logs.some(l => l.indexOf('不設資料起點') >= 0),
    '應明講沒有設起點：\n' + r.logs.join('\n'));
  // 回合 4/02 才開倉，4/01 沒有持倉本來就不會有點——那是既有行為，不是這次要改的。
  assert.equal(r.dates[0], '2026/04/02', '不設限時維持原本的範圍（從回合開倉日起）');
  assert(r.dates.length > 100, '不設限時應該涵蓋四月以來的整段，得到 ' + r.dates.length + ' 天');
}


// ---- 五、有持股但算不出報酬的那一天：整列不寫 ----
//
// 這是走勢圖出現假平線的直接原因，而且與日期範圍無關：
// 那些日子 avg 與 pos 會是空字串，讀取端的 Number('') 是 0，
// 於是「不知道」被畫成「0%」，連成一條平在 0 的線。
{
  const kRows = [];   // 日K只涵蓋 8 月之後，7 月那段有持股卻沒有收盤價
  weekdays('2026-08-03', '2026-09-11').forEach((d, i) => kRows.push({ '代號': '2330', '日期': d, '收': 2000 + i }));
  weekdays('2026-07-08', '2026-07-31').forEach(d => kRows.push({ '代號': '9999', '日期': d, '收': 50 }));  // 只提供交易日曆

  const tracker = [{
    '代號': '2330', '股票名稱': '台積電',
    '回合JSON': JSON.stringify([{ o: '2026/07/08', od: '2026/07/08', e: 2000, c: '', x: null, k: 'buy', s: 0 }])
  }];
  const trades = [{ '日期': '2026/07/08', '股票名稱': '台積電', '代號': '2330', '方向': '買入' }];
  let perf = [['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例']];
  const logs = [];
  const ctx = vm.createContext({
    console, JSON, Math, Date, String, Number, Object, Array, isFinite,
    Logger: { log: m => logs.push(String(m)) },
    CACHE: { get: () => null, put: () => {}, remove: () => {} },
    readSheetObjects_: n => n === '日K快取' ? kRows : n === '持股追蹤' ? tracker
      : n === '操作紀錄' ? trades : [],
    fmtDate_: x => String(x || ''), withLock_: fn => fn(),
    getSheet_: () => ({
      getDataRange: () => ({ getValues: () => perf.map(r => r.slice()) }),
      clearContents: () => { perf = []; },
      getRange: (r, c, n, m) => ({ setValues: v => { v.forEach((row, i) => { perf[r - 1 + i] = row; }); } })
    })
  });
  const src = fs.readFileSync(path.join(root, 'apps-script/Cachebuilder.gs'), 'utf8');
  vm.runInContext(src.match(/^function rebuildPerformanceHistoryJob\(fromDate\) \{[\s\S]*?^\}/m)[0], ctx);
  ctx.rebuildPerformanceHistoryJob();
  const dates = perf.slice(1).filter(r => r && r[0]).map(r => r[0]);

  assert(!dates.some(d => d < '2026/08/03'),
    '7 月那段有持股但沒有收盤價，算不出報酬就不該寫進去：' + dates.filter(d => d < '2026/08/03').join('、'));
  assert(dates.length, '8 月之後算得出來的那段要留著');
  assert.equal(dates[0], '2026/08/03');
  // 確認沒有任何一列的報酬欄是空的——空的就會被 Number('') 變成 0
  assert(!perf.slice(1).some(r => r && r[0] && (r[3] === '' || r[4] === '')),
    '不該再有報酬欄空白的列，那會在圖上畫成 0');
}

// ---- 六、numOrNull_：空白要回 null，不能回 0 ----
{
  const src = fs.readFileSync(path.join(root, 'apps-script/Cachebuilder.gs'), 'utf8');
  const ctx = vm.createContext({ Number, isFinite });
  vm.runInContext(src.match(/^function numOrNull_\(v\) \{[\s\S]*?^\}/m)[0], ctx);
  assert.strictEqual(ctx.numOrNull_(''), null, "空字串要回 null，Number('') 是 0 會變成假的零報酬");
  assert.strictEqual(ctx.numOrNull_(null), null);
  assert.strictEqual(ctx.numOrNull_(undefined), null);
  assert.strictEqual(ctx.numOrNull_('abc'), null);
  assert.strictEqual(ctx.numOrNull_(0), 0, '真正的 0 要保留');
  assert.strictEqual(ctx.numOrNull_('12.5'), 12.5);
  assert.strictEqual(ctx.numOrNull_(-3), -3, '負報酬要保留');
}

// ---- 七、前端要略過畫不出來的點 ----
{
  const js = fs.readFileSync(path.join(root, 'apps-script/JavaScript.html'), 'utf8');
  const i = js.indexOf('var pts = perfSlice()');
  assert(i > 0, '找不到繪圖的點陣列');
  const block = js.slice(i, i + 420);
  assert(block.indexOf('isFinite') >= 0 && block.indexOf('.filter(') >= 0,
    '前端仍可能把 null／空字串當成 0 畫出來：' + block);
}

console.log('PASS: 績效走勢不早於本站最早紀錄；舊的錯誤列會被清掉；指定起點時同樣擋；無來源紀錄時不設限；算不出報酬的日子不寫也不畫。');
