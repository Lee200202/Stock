// node tests/test_performance_count_v82.js
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..', 'public-site', 'gas-source');
const code = fs.readFileSync(path.join(root, 'Cachebuilder.gs'), 'utf8');
const from = code.indexOf('function snapshotPerformanceJobRun_() {');
const to = code.indexOf('/** 空白、非數字', from);
assert(from >= 0 && to > from, 'performance functions must exist');

const rows = [
  ['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例'],
  ['2026/09/24', 12, 12, 2, 75]
];
const sheet = {
  getRange(row, col) {
    return {
      getValue: () => rows[row - 1] && rows[row - 1][col - 1],
      setValues(values) {
        values.forEach((value, i) => { rows[row - 1 + i] = value.slice(); });
      }
    };
  },
  getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
  clearContents() { rows.length = 0; },
  appendRow(value) { rows.push(value.slice()); }
};
const logs = [];
const ctx = {
  PERFORMANCE_START_DATE: '2026/07/08',
  Logger: { log: value => logs.push(String(value)) },
  CACHE: { remove() {} },
  getSheet_: name => { assert.strictEqual(name, '每日績效'); return sheet; },
  withLock_: fn => fn(),
  fmtDate_: value => String(value || ''),
  todayStr_: () => '2026/09/29',
  latestTradingDayStr_: () => '2026/09/29',
  getHoldingsTracker: () => ({
    summary: { total: 34, holding: 12, priced: 10, avgReturn: 1.71, positiveRatio: 80 }
  }),
  readSheetObjects_: name => {
    if (name === '日K快取') return [
      { 日期: '2026/09/24', 代號: '1111', 收: 12 },
      { 日期: '2026/09/29', 代號: '1111', 收: 13 },
      { 日期: '2026/09/24', 代號: '2222', 收: 20 }
    ];
    if (name === '持股追蹤') return [
      { 代號: '1111', 股票名稱: '甲', 回合JSON: JSON.stringify([{ od: '2026/09/20', e: 10 }]) },
      { 代號: '2222', 股票名稱: '乙', 回合JSON: JSON.stringify([{ od: '2026/09/20', e: 10 }]) },
      { 代號: '3333', 股票名稱: '已出場', 回合JSON: JSON.stringify([{ od: '2026/09/20', c: '2026/09/23', e: 10 }]) }
    ];
    if (name === '操作紀錄' || name === '會員持股') return [];
    throw Error('unexpected sheet ' + name);
  },
  getSheetForStatus: () => sheet,
  nowStamp_: () => '2026/09/29 17:30',
  Math, Date, JSON, String, Number, Object, isFinite
};
vm.createContext(ctx);
vm.runInContext(code.slice(from, to), ctx);

const setup = fs.readFileSync(path.join(root, 'Setup.gs'), 'utf8');
const config = fs.readFileSync(path.join(root, 'Config.gs'), 'utf8');
assert(setup.includes("var PROJECT_BUILD_ = '2026-09-29-line-visual-v85'"));
assert(config.includes("var GAS_BUILD = '2026-09-29-line-visual-v85'"));
assert(setup.includes("marker: ['snapshotPerformanceJobRun_', 't.summary.priced']"));
assert(setup.includes("{ file: 'Index', marker: '持有檔數包含暫時缺價' }"));
assert(setup.includes("{ file: 'JavaScript', marker: 'tbl-merged' }"));
assert(String(ctx.snapshotPerformanceJobRun_).includes('t.summary.priced'),
  'project-file check must inspect a marker inside its target function');

const readNormal = ctx.readSheetObjects_;
ctx.readSheetObjects_ = name => name === '持股追蹤'
  ? readNormal(name).concat([{ 代號: '4444', 股票名稱: '舊列', 回合JSON: '' }])
  : readNormal(name);
const refused = ctx.rebuildPerformanceHistoryJob('2026/09/29');
assert.strictEqual(refused.ok, false, 'do not erase history while old holdings lack round JSON');
assert.strictEqual(rows[0][1], '追蹤檔數');
assert.strictEqual(rows[1][1], 12);
ctx.readSheetObjects_ = readNormal;

// A partial request against the old header must recalculate the whole history.
const rebuilt = ctx.rebuildPerformanceHistoryJob('2026/09/29');
assert.strictEqual(rebuilt.ok, true);
assert.strictEqual(rows[0][1], '計入報酬檔數');
assert.deepStrictEqual(rows.map(r => r[0]), ['日期', '2026/09/24', '2026/09/29']);
assert.deepStrictEqual(Array.from(rows[1].slice(1, 3)), [2, 2]);
assert.deepStrictEqual(Array.from(rows[2].slice(1, 3)), [1, 2],
  'missing close is held but excluded from the average; exited stock is excluded');
assert(logs.some(line => line.includes('改為全表重算')));

// Daily snapshot must use the same denominator, not all 34 ever-tracked stocks.
ctx.snapshotPerformanceJobRun_();
assert.deepStrictEqual(Array.from(rows[2].slice(1, 3)), [10, 12]);
assert.strictEqual(rows[2][3], 1.71);
console.log('performance count v82: historical migration and daily snapshot passed');
