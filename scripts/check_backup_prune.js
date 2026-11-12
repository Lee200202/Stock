// 2026/11/12：獨立備份「保留 7 天、自動清理」的規則（不連 Google）。node scripts/check_backup_prune.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Backuparchive.gs'), 'utf8');
const day = 86400000;
const now = Date.now();
const stamp = t => { const d = new Date(t); return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} 10:00:00`; };
// 目錄：來源、分頁ID、原分頁名稱、SHA、備份分頁ID、備份名稱、核對時間、列、欄、公式、類型、狀態
const index = [['來源試算表', '來源分頁ID', '原分頁名稱', '來源SHA256', '備份分頁ID', '備份名稱', '核對時間', '資料列數', '資料欄數', '公式格數', '類型', '狀態'],
  ['S', '1', '操作紀錄', 'a', '11', '備份_1101_100000_操作紀錄', stamp(now - 11 * day), 1, 1, 0, '更新前快照', '核對完成'],
  ['S', '1', '操作紀錄', 'b', '12', '備份_1103_100000_操作紀錄', stamp(now - 9 * day), 1, 1, 0, '更新前快照', '核對完成'],
  ['S', '1', '操作紀錄', 'c', '13', '備份_1110_100000_操作紀錄', stamp(now - 2 * day), 1, 1, 0, '更新前快照', '核對完成'],
  ['S', '2', '會員持股', 'd', '21', '備份_1101_100000_會員持股', stamp(now - 11 * day), 1, 1, 0, '更新前快照', '核對完成'],   // 這個分頁只有這一份 → 留
  ['S', '3', '每日推播內容', 'e', '31', '備份_1101_100000_每日推播內容', new Date(now - 10 * day), 1, 1, 0, '更新前快照', '核對完成'],  // 日期物件也看得懂
  ['S', '3', '每日推播內容', 'f', '32', '備份_1111_100000_每日推播內容', new Date(now - 1 * day), 1, 1, 0, '更新前快照', '核對完成'],
  ['S', '1', '操作紀錄', 'g', '14', '備份_1020_100000_操作紀錄', stamp(now - 23 * day), 1, 1, 0, '更新前快照', '已清理 2026/11/01（保留 7 天）']];
const sheets = {};
for (const r of index.slice(1)) sheets[r[4]] = {name: r[5], rows: 100, cols: 10};
delete sheets['14'];
const statusCells = {};
const archive = {
  getSheetByName: n => (n === '備份目錄' ? {getDataRange: () => ({getValues: () => index}),
    getRange: (row, col) => ({setValue: v => { statusCells[row] = v; }})} : null),
  getSheetById: id => { const s = sheets[id]; return s ? {name: s.name, getName: () => s.name, getMaxRows: () => 100, getMaxColumns: () => 10} : null; },
  getSheets: () => Object.values(sheets).map(s => ({getMaxRows: () => s.rows, getMaxColumns: () => s.cols})),
  deleteSheet: s => { const n = s.getName(); for (const k of Object.keys(sheets)) if (sheets[k].name === n) delete sheets[k]; },
};
const ctx = {
  Utilities: {formatDate: d => { const x = new Date(d); return `${x.getFullYear()}/${x.getMonth() + 1}/${x.getDate()}`; }},
  LockService: {getScriptLock: () => ({tryLock: () => true, releaseLock: () => {}})},
  PropertiesService: {getScriptProperties: () => ({getProperty: () => 'B'})},
  getSS_: () => ({getId: () => 'S', getSheets: () => []}),
  SpreadsheetApp: {openById: () => archive},
};
vm.createContext(ctx);
vm.runInContext(src + '\nthis.backupArchiveBook_ = function(){ return SpreadsheetApp.openById("B"); };', ctx);

const plan = ctx.backupArchivePrunePlan_(archive, 7);
assert.strictEqual(JSON.stringify(plan.drop.map(d => d.name)), JSON.stringify(['備份_1101_100000_操作紀錄', '備份_1103_100000_操作紀錄', '備份_1101_100000_每日推播內容']));
assert.strictEqual(plan.cells, 3000);
const dry = ctx.backupArchivePrune_(7, true);
assert.strictEqual(dry.pruned, 0); assert.strictEqual(Object.keys(sheets).length, 6);
assert.strictEqual(dry.prunable, 3); assert.strictEqual(dry.cellLimit, 10000000);
const real = ctx.backupArchivePrune_(7, false);
assert.strictEqual(real.pruned, 3);
assert.deepStrictEqual(Object.keys(sheets).sort(), ['13', '21', '32']);
assert(/^已清理 /.test(statusCells[2]) && /^已清理 /.test(statusCells[3]) && /^已清理 /.test(statusCells[6]));
assert.strictEqual(ctx.backupArchivePrune_(3, true).days, 7);          // 不能比 7 天短
console.log('11/12 獨立備份保留 7 天：每個分頁留最新一份、已清理不重算、預覽不刪、目錄留紀錄通過');
