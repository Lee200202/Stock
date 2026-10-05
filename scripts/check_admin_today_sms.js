// 今日後台逐則原文：日期篩選、全文保留、缺欄訊息，不把解析操作當原文。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Adminservice.gs'), 'utf8');
const ctx = vm.createContext({});
vm.runInContext(source, ctx);
ctx.cmDate_ = value => String(value).slice(0, 10);
ctx.cmFmtTime_ = value => String(value);

const raw = '張震-1:請於955元以上獲利賣出5536聖暉，資金轉為880元以下市價買進8210勤誠！';
const rows = [
  { 文章ID: 'old', 發文時間: '2026/10/02 11:00:00' },
  { 文章ID: '185117227', 發文時間: '2026/10/05 09:41:55' },
  { 文章ID: 'new', 發文時間: '2026/10/05 10:20:00' }
];
let rangeRead;
ctx.getSheet_ = () => ({
  getLastColumn: () => 4,
  getRange: (row, col, count) => ({ getValues: () => {
    if (row === 1) return [['文章ID', '發文時間', '原文', '通知狀態']];
    rangeRead = { row, col, count };
    return [[raw], ['第二則完整原文，含換行\n與條件 50 元']];
  } })
});
const actual = ctx.adminTodaySmsOriginals_('2026/10/05', rows);
assert.equal(actual.length, 2);
assert.equal(actual[1].id, '185117227');
assert.equal(actual[1].text, raw);
assert.equal(actual[0].text, '第二則完整原文，含換行\n與條件 50 元');
assert.deepEqual(rangeRead, { row: 3, col: 3, count: 2 });
assert.equal(ctx.adminTodaySmsOriginals_('2026/10/03', rows).length, 0);
ctx.getSheet_ = () => ({ getLastColumn: () => 1, getRange: () => ({ getValues: () => [['文章ID']] }) });
const missing = ctx.adminTodaySmsOriginals_('2026/10/05', rows);
assert.equal(missing.length, 2);
assert.equal(missing[0].error, '原文讀取失敗');
console.log('PASS: today SMS returns full original text, separates dates, and reports missing source column.');
