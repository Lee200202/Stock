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
console.log('no-show dispatch ok');
