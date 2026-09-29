const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('public-site/gas-source/Adminservice.gs', 'utf8');
const start = source.indexOf('function apiAdminTodayStatus(key) {');
const end = source.indexOf('\n/* ------------------------------------------------------------------ *', start);
assert(start >= 0 && end > start);
const day = '2026/09/29';
let hour = '1000';
let heartbeat = Date.now();
const sheets = {
  '系統狀態': [], '影片清單': [], '操作紀錄': [], '會員持股': [],
  '每日推播內容': [], '每日績效': [], '持股追蹤': []
};
const context = {
  Date, JSON, Math, Object, String, Number, isFinite,
  TZ: 'Asia/Taipei', TX_AUTO_START_HM_: 1105,
  adminAuth_() {}, todayStr_: () => day, fmtDate_: x => x,
  isTradingDayToday_: () => true,
  readSheetObjects_: name => sheets[name] || [],
  getSheet_: () => ({ getLastColumn: () => 1, getLastRow: () => 1,
    getRange: () => ({ getValues: () => [['發文時間']] }) }),
  Utilities: { formatDate: (_d, _tz, fmt) => fmt === 'HHmm' ? hour : fmt === 'HH:mm' ? `${hour.slice(0, 2)}:${hour.slice(2)}` : day },
  dailyKState_: () => null, tradingDaysAfter_: () => 0, latestTradingDayStr_: () => day,
  deliverySummaryForDate_: () => ({}),
  MailApp: { getRemainingDailyQuota: () => 100 },
  recentTradingDays_: () => [], opsMailByDates_: () => ({}),
  opsTimelineFor_: () => [], opsMailNeed_: () => ({ dailySubs: 0, smsSubs: 0, tone: 'idle' }),
  whyClosed_: () => '', daySyncState_: () => null,
  ScriptApp: { getProjectTriggers: () => ['everyFiveMinJob', 'cmoneyPollJob', 'backfillDailyKJob',
    'rebuildHoldingsTrackerJob', 'snapshotPerformanceJob', 'backfillHourlyHistoryJob'].map(name => ({ getHandlerFunction: () => name })) },
  PropertiesService: { getScriptProperties: () => ({ getProperty: name => name === 'OPS_HEARTBEAT_AT' ? String(heartbeat) : '' }) },
  marketPayload_: () => null, opsIsTrading_: () => true, dailyPushStartTime_: () => '1200',
  hmText_: () => '11:05', opsRuntimeFor_: () => null
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

let status = context.apiAdminTodayStatus('test');
assert.equal(status.ok, true, status.reason);
assert.equal(status.automation[0].tone, 'ok');
assert.equal(status.automation[1].value, '等待 11:05');
assert.equal(status.automation[3].value, '目前無每日訂閱者');
assert.equal(status.smsOperations.length, 0);

sheets['操作紀錄'].push({ '日期': day, '來源影片ID': 'CMONEY-123', '股票名稱': '華城',
  '代號': '1519', '方向': '賣出', '價位說明': '775 以上', '理由摘錄': '核對後的簡訊說明。' });
sheets['操作紀錄'].push({ '日期': day, '來源影片ID': 'VIDEO-123', '股票名稱': '勤誠', '方向': '觀望注意' });
status = context.apiAdminTodayStatus('test');
assert.equal(status.smsOperations.length, 1, 'admin summary only contains CMONEY records');
assert.equal(status.smsOperations[0].name, '華城');
assert.equal(status.smsOperations[0].direction, '賣出');

hour = '2300';
heartbeat = Date.now() - 25 * 60000;
status = context.apiAdminTodayStatus('test');
assert.equal(status.automation[0].tone, 'err', 'stale trigger heartbeat needs action');
assert.equal(status.automation[3].tone, 'idle', 'no daily subscribers is not an email failure');
console.log('Admin health v79 evidence and private SMS summary passed');
