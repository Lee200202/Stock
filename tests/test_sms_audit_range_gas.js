// 會員簡訊逐日稽核範圍（2026/09/15）：新簡訊自動派的「只更新今天」工單跑完，逐日稽核不可縮成今天一格。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const pad = n => String(n).padStart(2, '0');
const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN,
  Logger: { log: () => {} },
  Utilities: { formatDate: d => d.getFullYear() + '/' + pad(d.getMonth() + 1) + '/' + pad(d.getDate()) } });
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Cmoney.gs'), 'utf8'), ctx);
ctx.TZ = 'Asia/Taipei';
const row = (id, time, state) => ({ '文章ID': id, '發文時間': time, '解析狀態': state || '已解析' });
let sheet = [row('1', '2026/09/10 09:01:00'), row('2', '2026/09/11 10:00:00'), row('3', '2026/09/11 11:00:00'),
             row('4', '2026/09/15 11:28:00'), row('5', '2026/09/16 09:00:00')];
ctx.readSheetObjects_ = name => { assert.equal(name, '會員簡訊'); return sheet; };

// 一、自動派工（mode=today、since=今天）：範圍是會員簡訊分頁的第一天到今天，不是今天一格
let a = ctx.cmBuildSmsAudit_({ jobId: 'J1', mode: 'today', execution: 'github', since: '2026/09/15', through: '2026/09/15',
                               scanned: 0, zhang: 0, saved: 0 });
assert.equal(a.basis, 'sheet');
assert.equal(a.start, '2026/09/10'); assert.equal(a.end, '2026/09/15');
assert.deepEqual(a.days.map(d => d.date), ['2026/09/10', '2026/09/11', '2026/09/12', '2026/09/13', '2026/09/14', '2026/09/15']);
assert.deepEqual(a.days.map(d => d.status), ['ok', 'ok', 'empty', 'empty', 'empty', 'ok']);
assert.equal(a.days[1].articles, 2);
assert.deepEqual(JSON.parse(JSON.stringify(a.summary)), { articles: 4, activeDays: 3, errors: 0 }, '回報分頁數字，不拿自動工單的 0');
assert(!a.days.some(d => d.date === '2026/09/16'), '未來日期不列');

// 二、手動重新解析指定起始日：照管理者選的範圍
a = ctx.cmBuildSmsAudit_({ mode: 'reparse', since: '2026/09/11', through: '2026/09/15' });
assert.equal(a.start, '2026/09/11'); assert.equal(a.days.length, 5);
assert(!a.days.some(d => d.date === '2026/09/10'));
// 合併、補空白（since 空白）：全部歷史
a = ctx.cmBuildSmsAudit_({ mode: 'merge', since: '', through: '2026/09/15' });
assert.equal(a.start, '2026/09/10');

// 三、日期不明與待解析：列入需處理，未歸入任何一天
sheet = sheet.concat([row('6', '', '日期錯誤'), row('7', '2026/09/14 09:00:00', '待解析')]);
a = ctx.cmBuildSmsAudit_({ mode: 'today', since: '2026/09/15', through: '2026/09/15' });
assert.equal(a.days.find(d => d.date === '2026/09/14').status, 'error');
assert.equal(a.summary.errors, 2);
assert.deepEqual(a.errors.map(e => e.id).sort(), ['6', '7']);

// 四、抓取作業（最近七天）仍依本次工單的稽核列與範圍
ctx.cmAuditRowsForJob_ = () => [{ values: ['', '9', '', '2026/09/14 09:00:00', '', '', '是', '', '已收錄', ''] }];
a = ctx.cmBuildSmsAudit_({ jobId: 'J2', mode: 'recent', since: '2026/09/09', through: '2026/09/15', scanned: 12, zhang: 3, saved: 1 });
assert.equal(a.basis, 'job'); assert.equal(a.start, '2026/09/09'); assert.equal(a.days.length, 7);
assert.equal(a.summary.scanned, 12);

// 五、後台呈現：分頁核對換一套摘要文字，空白日不宣稱「來源沒有文章」
const admin = fs.readFileSync(path.join(root, 'apps-script/Admin.html'), 'utf8');
assert(admin.includes("smsCalState.basis=audit.basis||'job';"));
assert(admin.includes("'｜依會員簡訊分頁核對｜張震文章 '"));
assert(/day\.status === 'empty'\) return smsCalState\.basis==='sheet' \?\s*'會員簡訊分頁當日沒有張震文章/.test(admin));
console.log('PASS: SMS daily audit keeps the full sheet range after automatic same-day jobs; manual reparse and crawl jobs keep their own range.');
