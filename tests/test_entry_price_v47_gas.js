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
    '回合數', '本回合進場日', '出場原因', '回合明細', '參考價位', '參考價位來源', '首次進場方式', '本回合進場方式', '累積報酬', '各回合報酬', '回合JSON',
    '最新說明日期', '進場明講', '出場明講'];
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

// ---- v58 起（2026/09/24 管理者）：進場價一律取進場日當日最低、出場價一律取出場日當日最高 ----
// 張震明講的價位不再當成交價，改記在「進場明講」「出場明講」欄，介面以灰字標示。
// 觸價、回述的判斷仍然決定「哪一天」成交；那一天的最低／最高才是價格。

// ---- 一、明講買入、沒講價 → 當日最低 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補')], []);
  const row = r.byCode('5269');
  assert.equal(row['進場價'], lowOn(1300, '2026/07/06'), '進場價應為當日最低');
  assert(String(row['進場價來源']).indexOf('取 2026/07/06 當日最低 ' + lowOn(1300, '2026/07/06')) === 0, row['進場價來源']);
  assert.equal(row['進場明講'], '', '沒講價就沒有明講附註');
}

// ---- 二、明講賣出、沒講價 → 當日最高 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補'),
                 T('2026/07/20', '祥碩', '5269', '賣出', '獲利了結')], []);
  const row = r.byCode('5269');
  assert.equal(row['出場價'], highOn(1300, '2026/07/20'), '出場價應為當日最高');
  assert.equal(row['狀態'], '已出場');
  const json = JSON.parse(row['回合JSON'])[0];
  assert.equal(json.el, lowOn(1300, '2026/07/06')); assert.equal(json.eh, highOn(1300, '2026/07/06'));
  assert.equal(json.xh, highOn(1300, '2026/07/20')); assert.equal(json.ck, '賣出');
}

// ---- 三、轉為觀望不碰：出場日（當日或之前）的最高 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補'),
                 T('2026/07/20', '祥碩', '5269', '觀望不碰', '這裡不要追')], []);
  const row = r.byCode('5269');
  assert.equal(row['出場價'], highOn(1300, '2026/07/20'), '轉觀望不碰也取出場日當日最高');
  assert(/取 2026\/07\/20 當日最高 .*（轉為觀望不碰）/.test(row['進場價來源']), row['進場價來源']);
}

// ---- 四、首次明講會員持有：持有聲明當日的最低（不往後找） ----
{
  const r = run([], [H('2026/07/06', '祥碩', '5269', '會員持有'),
                     H('2026/07/07', '祥碩', '5269', '續抱'),
                     H('2026/07/08', '祥碩', '5269', '續抱')]);
  const row = r.byCode('5269');
  if (row) {
    assert.equal(row['進場價'], lowOn(1300, '2026/07/06'), '持有聲明當日最低');
    assert(String(row['進場價來源']).indexOf('（首次明講會員持有）') > 0, row['進場價來源']);
  }
}

// ---- 五、有明講價：價格仍取當日最低，明講價記在附註 ----
{
  const say = closeOn(1300, '2026/07/06');   // 落在當天區間內
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補', '買在 ' + say)], []);
  const row = r.byCode('5269');
  assert.equal(row['進場價'], lowOn(1300, '2026/07/06'), '明講價不再當成交價');
  assert.equal(row['進場明講'], '明講 ' + say + ' 買進');
  assert(String(row['進場價來源']).indexOf('明講 ' + say + ' 買進') > 0, row['進場價來源']);
  assert.equal(JSON.parse(row['回合JSON'])[0].ev, say);
}

// ---- 六、條件價賣出：「漲到 N 以上賣出」→ 觸價那一天的最高，附註寫明講 ----
{
  const target = closeOn(1300, '2026/07/20') + 10;   // 7/20 當天摸不到，之後才漲到
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補'),
                 T('2026/07/20', '祥碩', '5269', '賣出', '漲到再賣', target + '以上')], []);
  const row = r.byCode('5269');
  const json = JSON.parse(row['回合JSON'])[0];
  const fillDay = DAYS.find(d => d > '2026/07/20' && highOn(1300, d) >= target);
  assert.equal(row['出場明講'], '明講 ' + target + ' 以上賣出');
  assert.equal(row['出場價'], highOn(1300, fillDay), '觸價日 ' + fillDay + ' 的最高');
  assert.equal(json.xv, target);
  assert(/當日最高/.test(row['回合明細']) && /［明講 \d+ 以上賣出］/.test(row['回合明細']), row['回合明細']);
}

// ---- 七、「跌到 N 以下買」一直沒碰到：仍取發話日最低，附註標明查無成交 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '等回檔', '1200以下')], []);
  const row = r.byCode('5269');
  assert.equal(row['進場價'], lowOn(1300, '2026/07/06'));
  assert(/^明講 1200 以下買進（(查無成交紀錄|期間未成交)）$/.test(row['進場明講']), row['進場明講']);
}

// ---- 八、數字不是這一檔的價位（例如 8）：不當明講附註 ----
{
  const r = run([T('2026/07/06', '祥碩', '5269', '買入', '外資回補', '8')], []);
  const row = r.byCode('5269');
  assert.equal(row['進場明講'], '', '被判定不是價位的數字不能變成附註');
  assert.equal(row['進場價'], lowOn(1300, '2026/07/06'));
}

console.log('PASS: v58 進場取當日最低、出場取當日最高（含持有聲明、轉觀望、逾期）；明講價只作附註。');
