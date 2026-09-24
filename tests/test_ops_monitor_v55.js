const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const root = path.resolve(__dirname, '..');
const props = { ADMIN_KEY: 'key' };
let pushStatus = '待寄送';
const ctx = vm.createContext({
  PropertiesService: { getScriptProperties: () => ({
    getProperty: k => props[k] || '', setProperty: (k, v) => { props[k] = v; }
  }) },
  Utilities: { formatDate: (_d, _tz, fmt) => fmt === 'HHmm' ? '1305' : '13:05' },
  ScriptApp: { getProjectTriggers: () => ['everyFiveMinJob', 'cmoneyPollJob', 'backfillDailyKJob',
    'rebuildHoldingsTrackerJob', 'snapshotPerformanceJob'].map(name => ({ getHandlerFunction: () => name })) },
  MailApp: { getRemainingDailyQuota: () => 470 },
  adminAuth_: () => {}, todayStr_: () => '2026/09/24', isTradingDayToday_: () => true,
  fmtDate_: v => v, latestTradingDayStr_: () => '2026/09/24', tradingDaysAfter_: () => 0,
  dailyKState_: () => ({ finishedAt: '2026/09/24 18:00', lastCode: '9999' }),
  getSheet_: () => ({ getRange: () => ({ getValues: () => [['發文時間']] }), getLastColumn: () => 1, getLastRow: () => 1 }),
  readSheetObjects_: name => ({
    '系統狀態': [], '影片清單': [{ '發布日期': '2026/09/24', '處理狀態': '完成',
      '原始逐字稿內容': '原稿', '修飾後逐字稿內容': '修飾稿' }],
    '操作紀錄': [], '會員持股': [], '每日推播內容': [{ '日期': '2026/09/24', '寄送狀態': pushStatus }],
    '每日績效': [{ '日期': '2026/09/24' }]
  })[name] || [],
  ADMIN_JOB_SHEET: '後台工單', AUTO_JOB_SHEET: '自動工單', TZ: 'Asia/Taipei'
});
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8'), ctx);
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Adminservice.gs'), 'utf8'), ctx);
ctx.isTradingDayToday_ = () => true;
ctx.deliverySummaryForDate_ = () => ({ a: { total: 3, accepted: 2, failed: 0, unknown: 0, open: 1 } });

assert.equal(ctx.dailyPushStartTime_('2026/09/24'), '1200');
assert(!ctx.apiAdminSetDailyPushStart('key', '11:55').ok);
assert(!ctx.apiAdminSetDailyPushStart('key', '14:03').ok);
assert(ctx.apiAdminSetDailyPushStart('key', '14:05').ok);
assert.equal(ctx.dailyPushStartTime_('2026/09/24'), '1405');
assert.equal(ctx.dailyPushStartTime_('2026/09/25'), '1200', '今日設定不影響明日');
pushStatus = '已寄送';
assert(!ctx.apiAdminSetDailyPushStart('key', '15:00').ok, '寄送後不可變更');
pushStatus = '待寄送';
props.dailyPushSentDates = JSON.stringify({ '2026/09/24': '2026/09/24 12:05' });
assert(!ctx.apiAdminSetDailyPushStart('key', '15:00').ok, '重建待寄列也不能繞過已寄旗標');
delete props.dailyPushSentDates;
props.OPS_HEARTBEAT_AT = String(Date.now() - 3 * 60000);
const status = ctx.apiAdminTodayStatus('key');
assert(status.ok, status.reason);
assert.equal(status.ops.dailyK.finishedAt, '2026/09/24 18:00', '日 K 狀態不能被投遞摘要鍵覆蓋');
assert.equal(status.ops.mailStart, '14:05');
assert.equal(status.ops.deliveries.open, 1);
assert.equal(status.ops.missingTriggers.length, 0);
console.log('ops monitor v55 ok');
