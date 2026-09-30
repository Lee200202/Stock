const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..', 'public-site', 'gas-source');
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const mail = { stripMetaClauses_: x => x, stripConceptParens_: x => x };
vm.createContext(mail);
vm.runInContext(read('MailService.gs'), mail);

const hold = mail.mdToHtml_('②-2 影片中明講之「會員目前持有股票」\n' +
  '| 股票名稱 | 代號 | 目前立場 | 說明重點 |\n|---|---|---|---|\n' +
  '| 鴻準 | 2354 | 持有 | 會員續抱。 |\n');
const table = hold.slice(hold.indexOf('<table'), hold.indexOf('</table>') + 8);
assert.strictEqual((table.match(/<th /g) || []).length, 0, 'stacked stock cards have no squeezed column headers');
assert.match(table, /mc-table mc-stack/);
assert(!table.includes('<colgroup'), 'no fixed narrow name column');
assert.strictEqual((table.match(/<td /g) || []).length, 2, 'hold status does not consume a column');
assert.match(table, /鴻準<\/span> <span class="mc-code"[^>]*>2354<\/span>/);
assert(!table.includes('>持有<'), 'repeated status is absent');
assert.match(table, /會員續抱。/);
const escaped = mail.mdToHtml_('| 股票名稱 | 代號 | 說明重點 |\n|---|---|---|\n| 甲 | 1234 | <script>alert(1)</script> |\n');
assert(!escaped.includes('<script>'), 'transcript text must remain escaped');

const site = read('JavaScript.html');
assert.match(site, /function dailyNameCell\(name, code\)/);
assert.match(site, /tbl-daily tbl-merged tbl-simple/);
assert(!site.includes('計入<span class="th-long">報酬檔數'), 'extra performance column removed');
const admin = read('Admin.html');
assert.match(admin, /id="opsTimePanel"/);
assert(!admin.includes('type="time"'), 'custom time picker replaces native control');
assert.match(admin, /\.hc'\)\.addEventListener\('dblclick'/);
assert.match(admin, /classList\.toggle\('dirty'/);
assert(!admin.includes('要對照改版前的後台'));

const cm = read('Cmoney.gs');
const start = cm.indexOf('function cmSyncContentTick_(){');
const end = cm.indexOf('var CM_TX_RAW_CACHE_', start);
assert(start >= 0 && end > start);
let articleRead = false;
const tick = { Date, dkExecStart_: () => Date.now(), getSheet_: name => {
  if (name === '每日推播內容') { articleRead = true; throw Error('article must not be read'); }
  assert.strictEqual(name, '簡訊內容同步');
  return { getLastRow: () => 1 };
} };
vm.createContext(tick);
vm.runInContext(cm.slice(start, end), tick);
tick.cmSyncContentTick_();
assert.strictEqual(articleRead, false, 'empty SMS sync queue must skip article scan');

const setup = read('Setup.gs');
assert.match(setup, /nightRecovery/);
assert.match(setup, /OPS_STAGE_SAMPLE_/);
assert.match(setup, /workspaceLimit:360/);
assert.strictEqual(fs.existsSync(path.join(root, 'AdminLegacy.html')), false);

const g = {
  CacheService: { getScriptCache: () => ({ get: () => null, put: () => {} }) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: () => '', setProperty: () => {} }) },
  ScriptApp: { getService: () => ({ getUrl: () => 'https://script.google.com/macros/s/xxx/exec' }), getProjectTriggers: () => [] },
  Utilities: { formatDate: () => '2026/09/29' },
  Logger: { log: () => {} }
};
g.globalThis = g;
vm.createContext(g);

const files = fs.readdirSync(root).filter(f => f.endsWith('.gs'));
for (const f of files) {
  vm.runInContext(fs.readFileSync(path.join(root, f), 'utf8'), g, { filename: f });
}

const problems = [];
g.PROJECT_FILES_.forEach(f => {
  const missing = f.names.filter(n => typeof g[n] === 'undefined');
  if (missing.length === f.names.length) {
    problems.push({ file: f.file, reason: '檔案沒載入' });
    return;
  }
  if (missing.length) {
    problems.push({ file: f.file, reason: '缺 ' + missing.join('、') });
    return;
  }
  if (f.marker) {
    let src = '';
    try { src = String(g[f.marker[0]]); } catch (e) {}
    if (src.indexOf(f.marker[1]) < 0) {
      problems.push({ file: f.file, reason: '還是舊版（' + f.marker[0] + ' 裡找不到「' + f.marker[1] + '」）。' });
    }
  }
});
g.PROJECT_HTML_.forEach(h => {
  const filePath = path.join(root, h.file + '.html');
  if (!fs.existsSync(filePath)) {
    problems.push({ file: h.file + '.html', reason: '檔案不存在' });
    return;
  }
  const raw = fs.readFileSync(filePath, 'utf8');
  if (h.marker && raw.indexOf(h.marker) < 0) {
    problems.push({ file: h.file + '.html', reason: '找不到「' + h.marker + '」' });
  }
});

assert.strictEqual(problems.length, 0, 'checkProjectFiles must have 0 problems: ' + JSON.stringify(problems));
console.log('layout/runtime v83: mail table, admin controls, empty queue, night recovery, and checkProjectFiles passed');
