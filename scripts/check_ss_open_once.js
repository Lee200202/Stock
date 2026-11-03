// 主資料試算表一次執行只開一次（Setup.gs getSS_）；換了 SPREADSHEET_ID 要重新開（2026/11/03 觸發器耗時）。
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Setup.gs'), 'utf8');
const block = src.match(/var _SS_OPEN_ = [\s\S]*?\nfunction getSS_\(\) \{[\s\S]*?\n\}/)[0];

let opened = 0, id = 'sheet-A';
const PROP = { getProperty: () => id };
const SpreadsheetApp = { openById: (x) => { opened++; return { id: x }; } };
const getSS_ = new Function('PROP', 'SpreadsheetApp', block + '\nreturn getSS_;')(PROP, SpreadsheetApp);

assert.strictEqual(getSS_().id, 'sheet-A');
getSS_(); getSS_();
assert.strictEqual(opened, 1, '同一次執行只開一次');
id = 'sheet-B';
assert.strictEqual(getSS_().id, 'sheet-B');
assert.strictEqual(opened, 2, '換 ID 要重新開');
id = '';
assert.throws(() => getSS_(), /SPREADSHEET_ID/);
console.log('主資料試算表一次執行只開一次：4 項通過');
