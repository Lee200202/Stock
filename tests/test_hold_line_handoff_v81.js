const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const sheet = fs.readFileSync('public-site/gas-source/SheetService.gs', 'utf8');
const context = vm.createContext({ HOLD_CONFIRM_DAYS: 3 });
vm.runInContext(sheet, context);
const days = ['2026/09/01', '2026/09/02', '2026/09/03', '2026/09/04'];
const lone = [{ date: '2026/09/01' }];
assert.equal(context.isManualHoldSource_('MANUALENTRY-20260929'), true);
assert.equal(context.isManualHoldSource_('MANUAL-20260929'), false);
assert.equal(context.isManualHoldSource_('CMONEY-123'), false);
assert.equal(context.holdConfirmForOpen_({ date: days[0], manual: false }, lone, days), 'unconfirmed');
assert.equal(context.holdConfirmForOpen_({ date: days[0], manual: true }, lone, days), 'confirmed');
assert.match(sheet, /manual: isManualHoldSource_\(r\['來源影片ID'\]\)/,
  'member holding row must carry its source into round construction');
assert.match(sheet, /holdConfirmForOpen_\(rd\.open, recs, tradingDays\) === 'unconfirmed'/);

const mail = fs.readFileSync('public-site/gas-source/MailService.gs', 'utf8');
const start = mail.indexOf('function pushReadyChannels_() {');
const end = mail.indexOf('\nfunction scheduleDailyPush_()', start);
assert(start >= 0 && end > start);
const calls = [];
const handoff = vm.createContext({
  dailyPushJob: () => { calls.push('email'); throw Error('email temporarily failed'); },
  lineDailyTick_: () => { calls.push('line'); },
  Logger: { log: () => {} }
});
vm.runInContext(mail.slice(start, end), handoff);
handoff.pushReadyChannels_();
assert.deepEqual(calls, ['email', 'line'], 'LINE must run when Email fails');
handoff.dailyPushJob = () => { calls.push('email-again'); };
handoff.lineDailyTick_ = () => { calls.push('line-failed'); throw Error('LINE temporarily failed'); };
handoff.pushReadyChannels_();
assert.deepEqual(calls.slice(2), ['email-again', 'line-failed'], 'Email must finish when LINE fails');
assert.match(mail, /function pushAfterGate_\(\) \{\s*cleanupPushTriggers_\(\);\s*pushReadyChannels_\(\);/);

const admin = fs.readFileSync('public-site/gas-source/Admin.html', 'utf8');
assert.match(admin, /window\.__openHeldSource/);
assert.match(admin, /class="ghost sm h-source"/);
assert.match(admin, /現價取行情、報酬由成本計算/);
console.log('manual holding confirmation, independent push, and source navigation passed');
