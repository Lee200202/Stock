const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
let hourMinute = '1105', status = '預告停播', dispatched = 0;
const sheet = {
  getLastRow: () => status ? 2 : 1,
  getRange: () => ({ getDisplayValues: () => [['2026/09/24 11:05:00', status]] })
};
const props = {};
const ctx = vm.createContext({
  TZ: 'Asia/Taipei',
  Utilities: { formatDate: (_now, _tz, fmt) => ({
    'yyyy/MM/dd': '2026/09/24', HHmm: hourMinute, u: '4'
  })[fmt] },
  isMarketHoliday_: () => false,
  getSheet_: () => sheet,
  transcriptTodayState_: () => ({ ready: false, complete: false, manual: false }),
  githubCfg_: () => ({ repo: 'owner/repo', token: 'test' }),
  transcriptWorkflowRuns_: () => [],
  withLock_: f => f(),
  PropertiesService: { getScriptProperties: () => ({
    getProperty: k => props[k] || '', setProperty: (k, v) => { props[k] = v; }
  }) },
  dispatchGithub_: () => { dispatched++; return { ok: true }; },
  Logger: { log: () => {} }
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'apps-script', 'Adminservice.gs'), 'utf8'), ctx);
// Override only the I/O adapters after loading the source.
ctx.transcriptTodayState_ = () => ({ ready: false, complete: false, manual: false });
ctx.transcriptWorkflowRuns_ = () => [];
ctx.githubCfg_ = () => ({ repo: 'owner/repo', token: 'test' });
ctx.withLock_ = f => f();
ctx.dispatchGithub_ = () => { dispatched++; return { ok: true }; };
ctx.isMarketHoliday_ = () => false;
ctx.getSheet_ = () => sheet;

assert.equal(ctx.todayNoShowState_('2026/09/24'), '預告停播');
ctx.transcriptAutomationTick_();
assert.equal(dispatched, 0, '預告停播時，不可繼續每五分鐘補派');
hourMinute = '1231';
ctx.transcriptAutomationTick_();
assert.equal(dispatched, 1, '確認時段後應再查一次影片');
status = '今日無直播';
hourMinute = '1300';
ctx.transcriptAutomationTick_();
assert.equal(dispatched, 1, '正式判定無直播後不得再補派');

let completed = false;
const mail = vm.createContext({
  readSheetObjects_: name => name === '系統狀態'
    ? [{ '時間': '2026/09/24 12:31:00', '類別': '今日無直播' }]
    : name === '影片清單' && completed
      ? [{ '發布日期': '2026/09/24', '處理狀態': '完成', '原始逐字稿內容': '原'.repeat(300) }]
      : [],
  fmtDate_: v => v
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'apps-script', 'MailService.gs'), 'utf8'), mail);
assert.equal(mail.noVideoToday_('2026/09/24'), '今日無直播');
completed = true;
assert.equal(mail.noVideoToday_('2026/09/24'), '', '人工補稿完成後，舊停播標記不得擋寄信');
console.log('no-show dispatch ok');
