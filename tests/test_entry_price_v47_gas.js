// 未明講價位時的取價（2026/09/20 起）：
//   買入 → 當日最低價
//   賣出 → 當日最高價
//   其餘 → 當日收盤
//
// 「其餘」是重點，不是補充。有兩條路會走到同一個取價函式，但它們都不是成交：
//   首次明講會員持有　部位是更早之前建立的，那天沒有買進動作
//   轉為觀望不碰　　　立場轉變，那天沒有賣出動作
// 這兩種若也套用最低／最高價，等於替沒發生的交易灌出更好看的報酬。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const root = path.resolve(__dirname, '..');

function weekdays(from, to) {
  const out = [];
  for (let d = new Date(from + 'T00:00:00Z'); d <= new Date(to + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + 1)) {
    const w = d.getUTCDay(); if (w && w < 6) out.push(d.toISOString().slice(0, 10).replace(/-/g, '/'));
  }
  return out;
}
const DAYS = weekdays('2026-07-01', '2026-09-11');
// 刻意讓三個價差得開，斷言才分得出用了哪一個：low = close-2、high = close+2
function candles(base) {
  return DAYS.map((d, i) => ({ date: d, open: base + i, high: base + i + 2, low: base + i - 2, close: base + i, volume: 1000 }));
}
const closeOn = (base, d) => base + DAYS.indexOf(d);
const lowOn = (base, d) => closeOn(base, d) - 2;
const highOn = (base, d) => closeOn(base, d) + 2;

function run(trades, holds) {
  trades = trades.concat([T('2026/09/01', '台泥', '1101', '觀望注意', '填充列')]);
  const written = { rows: null };
  const K = { '5269': candles(1300), '9958': candles(80), '2330': candles(2000) };
  const sheets = {
    '操作紀錄': trades, '會員持股': holds,
    '日K快取': DAYS.map(d => ({ '代號': '0050', '日期': d, '收': 100 }))
  };
  const logs = [];
  const ctx = vm.createContext({
    console, JSON, Math, Date, String, Number, Object, Array,
    Logger: { log: m => logs.push(String(m)) },
    CACHE: { get: () => null, put: () => {}, remove: () => {} },
    LockService: { getScriptLock: () => ({ waitLock: () => {}, tryLock: () => true, releaseLock: () => {} }) },
    Utilities: { formatDate: () => '2026/09/14', sleep: () => {} },
    PropertiesService: { getScriptProperties: () => ({ getProperty: () => null, setProperty: () => {} }) }
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/SheetService.gs'), 'utf8'), ctx);
  Object.assign(ctx, {
    readSheetObjects_: n => (sheets[n] || []).map(r => Object.assign({}, r)),
    fillMissingDailyK_: () => ({ checked: 0 }), getCachedDailyK: c => K[c] || [], getQuoteCache: () => ({}),
    naturalReason_: s => String(s || ''), todayStr_: () => '2026/09/14',
    getSheet_: () => ({ clearContents: () => {}, getRange: () => ({ setValues: v => { if (v.length > 1 || v[0][0] !== '代號') written.rows = v; } }) }),
    withLock_: fn => fn()
  });
  ctx.rebuildHoldingsTrackerJob();
  const head = ['代號', '股票名稱', '首次買入日', '進場價', '進場價來源', '最近賣出日', '出場價', '狀態', '提及次數', '首次理由', '逐日說明', '更新時間',
    '回合數', '本回合進場日', '出場原因', '回合明細', '參考價位', '參考價位來源', '首次進場方式', '本回合進場方式', '累積報酬', '各回合報酬', '回合JSON'];
  const rows = (written.rows || []).map(r => Object.fromEntries(head.map((h, i) => [h, r[i]])));
  return { rows, logs, ctx, byCode: c => rows.find(r => r['代號'] === c) };
}

const T = (d, name, code, dir, reason, price = '未說明') =>
  ({ '日期': d, '股票名稱': name, '代號': code, '方向': dir, '價位說明': price, '理由摘錄': reason, '來源影片ID': 'MANUAL-x', '序': 1 });
const H = (d, name, code, note) =>
  ({ '日期': d, '股票名稱': name, '代號': code, '目前立場': '續抱', '說明重點': note, '來源影片ID': 'MANUAL-x' });

// ---- unstatedPrice_ 本身 ----
{
  const ctx = run([], []).ctx;
  const d = { low: 90, high: 110, close: 100 };
  assert.deepEqual(ctx.unstatedPrice_(d, 'low'), { price: 90, src: '當日最低價' }, '買入要取最低價');
  assert.deepEqual(ctx.unstatedPrice_(d, 'high'), { price: 110, src: '當日最高價' }, '賣出要取最高價');
  assert.deepEqual(ctx.unstatedPrice_(d, 'close'), { price: 100, src: '當日收盤' }, '其餘維持收盤');
  assert.deepEqual(ctx.unstatedPrice_(d, undefined), { price: 100, src: '當日收盤' }, '沒指定時預設收盤');
  // 日K缺欄位時不能回傳 undefined，那會讓進場價整欄空白、報酬變破折號
  assert.deepEqual(ctx.unstatedPrice_({ close: 100 }, 'low'), { price: 100, src: '當日收盤' }, '沒有 low 欄位時退回收盤');
  assert.deepEqual(ctx.unstatedPrice_({ close: 100 }, 'high'), { price: 100, src: '當日收盤' }, '沒有 high 欄位時退回收盤');
}

// ---- 一、明講買入、沒講價 → 當日最低價 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補')], []);
  const row = r.byCode('5269');
  assert.equal(row['進場價'], lowOn(1300, '2026/07/06'), '買入未明講價，進場價應為當日最低價');
  assert.notEqual(row['進場價'], closeOn(1300, '2026/07/06'), '不應再取當日收盤');
  assert(String(row['進場價來源']).indexOf('當日最低價') >= 0,
    '來源欄要寫明是最低價，否則看不出這一筆是估的：' + row['進場價來源']);
}

// ---- 二、明講賣出、沒講價 → 當日最高價 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補'),
                 T('2026/07/20', '祥碩', '5269', '賣出', '獲利了結')], []);
  const row = r.byCode('5269');
  assert.equal(row['出場價'], highOn(1300, '2026/07/20'), '賣出未明講價，出場價應為當日最高價');
  assert.notEqual(row['出場價'], closeOn(1300, '2026/07/20'), '不應再取當日收盤');
  assert.equal(row['狀態'], '已出場');
}

// ---- 三、轉為觀望不碰：不是成交，維持當日收盤 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補'),
                 T('2026/07/20', '祥碩', '5269', '觀望不碰', '這裡不要追')], []);
  const row = r.byCode('5269');
  assert.equal(row['出場價'], closeOn(1300, '2026/07/20'),
    '觀望不碰那天並沒有賣出動作，用最高價等於替沒發生的交易灌高報酬');
  assert.notEqual(row['出場價'], highOn(1300, '2026/07/20'));
}

// ---- 四、首次明講會員持有：那天沒有成交，維持當日收盤 ----
{
  const r = run([], [H('2026/07/06', '祥碩', '5269', '會員持有'),
                     H('2026/07/07', '祥碩', '5269', '續抱'),
                     H('2026/07/08', '祥碩', '5269', '續抱')]);
  const row = r.byCode('5269');
  if (row) {
    assert.equal(row['進場價'], closeOn(1300, '2026/07/06'),
      '持有聲明那天部位早就在手上，當天最低價與他的成本無關');
    assert.notEqual(row['進場價'], lowOn(1300, '2026/07/06'));
  }
}

// ---- 五、有明講價時完全不受影響 ----
{
  const say = closeOn(1300, '2026/07/06');   // 落在當天區間內，會被直接採用
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補', '買在 ' + say)], []);
  const row = r.byCode('5269');
  assert.equal(row['進場價'], say, '明講價仍然優先，取價規則只作用在沒明講的時候');
  assert(String(row['進場價來源']).indexOf('明講') >= 0, row['進場價來源']);
}

console.log('PASS: 買入未明講取當日最低價、賣出未明講取當日最高價；持有聲明與觀望不碰維持收盤；明講價不受影響。');
