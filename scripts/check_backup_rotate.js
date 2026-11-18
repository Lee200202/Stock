// 2026/11/18：備份檔超過 1,000 萬格被 Google 設為唯讀後，換新備份檔的規則（不連 Google）。node scripts/check_backup_rotate.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Backuparchive.gs'), 'utf8');

function world(oldCells, openFails) {
  const props = {BACKUP_SPREADSHEET_ID: 'OLD', BACKUP_SOURCE_SPREADSHEET_ID: 'SRC'};
  const created = [];
  const sheet = cells => ({getMaxRows: () => cells / 10, getMaxColumns: () => 10});
  const range = {setValues() { return range; }, setFontWeight() { return range; }, setBackground() { return range; }};
  const newBook = id => {
    const index = {setName() {}, getRange: () => range, setFrozenRows() {}, setColumnWidths() {}};
    return {getId: () => id, getUrl: () => 'https://docs/' + id, setSpreadsheetTimeZone() {}, getSheets: () => [index]};
  };
  const ctx = {
    PropertiesService: {getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null),
      setProperties: o => Object.assign(props, o)})},
    LockService: {getScriptLock: () => ({tryLock: () => true, waitLock() {}, releaseLock() {}})},
    SpreadsheetApp: {
      create: () => { const b = newBook('NEW' + (created.length + 1)); created.push(b); return b; },
      openById: () => { if (openFails) throw new Error('read-only'); return {getSheets: () => [sheet(oldCells)]}; }},
    getSS_: () => ({getId: () => 'SRC'}),
    Utilities: {}, Logger: {log() {}},
  };
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return {ctx, props, created};
}

// 強制換新：新 ID 寫入、舊 ID 進歷史、來源不變
let w = world(1000, false);
let r = w.ctx.backupArchiveRotate_(true);
assert.strictEqual(r.rotated, true);
assert.strictEqual(w.props.BACKUP_SPREADSHEET_ID, 'NEW1');
assert.strictEqual(w.props.BACKUP_SPREADSHEET_HISTORY, 'OLD');
assert.strictEqual(w.props.BACKUP_SOURCE_SPREADSHEET_ID, 'SRC');

// 快照前的自動檢查：未到 950 萬格不換；到了才換；舊檔打不開也換
w = world(9000000, false);
assert.strictEqual(w.ctx.backupArchiveRotate_(false).rotated, false);
assert.strictEqual(w.props.BACKUP_SPREADSHEET_ID, 'OLD');
w = world(9600000, false);
assert.strictEqual(w.ctx.backupArchiveRotate_(false).rotated, true);
w = world(0, true);
assert.strictEqual(w.ctx.backupArchiveRotate_(false).rotated, true);

// 連換兩次：歷史依序累積、不重複
w = world(1000, false);
w.ctx.backupArchiveRotate_(true);
w.ctx.backupArchiveRotate_(true);
assert.strictEqual(w.props.BACKUP_SPREADSHEET_HISTORY, 'OLD,NEW1');
assert.strictEqual(w.props.BACKUP_SPREADSHEET_ID, 'NEW2');

console.log('11/18 備份檔換新：強制換新、950 萬格自動換新、舊檔打不開也換、舊 ID 留歷史通過');
