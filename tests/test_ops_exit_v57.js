const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.resolve(__dirname, '..');
const source = f => fs.readFileSync(path.join(root, 'apps-script', f), 'utf8');

const ctx = vm.createContext({ Logger: { log() {} } });
vm.runInContext(source('SheetService.gs'), ctx);
const candles = [{ date: '2026/09/22', close: 2710 }, { date: '2026/09/24', close: 3230 }];
assert.equal(ctx.closeOnOrBefore_(candles, '2026/09/23').price, 2710, '缺 9/23 時不可借用 9/24 收盤');
assert.equal(ctx.closeOnOrBefore_(candles, '2026/09/21').price, '', '首次 K 之前不可借用未來價格');
assert(source('SheetService.gs').includes("rd.closeKind === '觀望不碰') {"), '觀望出場走獨立的收盤分支');

let tracker = { '代號': '3529', '狀態': '已出場', '最近賣出日': '2026/09/23', '出場價': '' };
let cache = [{ date: '2026/09/22', open: 2710, high: 2720, low: 2700, close: 2710, volume: 1000 }];
let rebuilt = 0, history = '';
Object.assign(ctx, {
  readSheetObjects_: name => name === '持股追蹤' ? [tracker] : [],
  fmtDate_: x => x, todayStr_: () => '2026/09/24',
  getCachedDailyK: () => cache,
  validDailyK_: x => !!x && x.close > 0,
  acquireDailyKLease_: () => 'lease', releaseDailyKLease_: () => {},
  fugleShiftDay_: (d, n) => new Date(Date.parse(d + 'T00:00:00Z') + n * 86400000).toISOString().slice(0, 10),
  hasFugle_: () => true,
  fugleHistorical_: (_code, from, to) => {
    assert.equal(from, '2026-09-22'); assert.equal(to, '2026-09-24');
    return [{ date: '2026/09/23', open: 2700, high: 2800, low: 2650, close: 2750, volume: 4000 }];
  },
  mergeDailyK_: (oldRows, newRows) => oldRows.concat(newRows),
  writeDailyKRows_: items => { cache = items[0].rows; },
  rebuildHoldingsTrackerJob: () => { rebuilt++; tracker['出場價'] = cache.find(x => x.date === '2026/09/23').close; },
  rebuildPerformanceHistoryJob: d => { history = d; }
});
const repaired = ctx.repairMissingTrackerExitPrice('3529');
assert.equal(repaired.close, 2750);
assert.equal(repaired.trackerExit, 2750);
assert.equal(rebuilt, 1);
assert.equal(history, '2026/09/23');

const p = { OPS_HEARTBEAT_AT: String(Date.now() - 60000), marketSnapshotStatus: JSON.stringify({ at: '2026/09/24 13:35:00', ok: true }), hourHistoryProgress: '3529｜24/33｜2026/09/23' };
const admin = vm.createContext({
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => p[k] || '' }) },
  Utilities: { formatDate: (_d, _tz, f) => f === 'HHmm' ? '1340' : '13:40' },
  ScriptApp: { getProjectTriggers: () => ['everyFiveMinJob', 'cmoneyPollJob', 'backfillDailyKJob', 'rebuildHoldingsTrackerJob', 'snapshotPerformanceJob'].map(x => ({ getHandlerFunction: () => x })) },
  MailApp: { getRemainingDailyQuota: () => 96 },
  adminAuth_: () => {}, todayStr_: () => '2026/09/24', isTradingDayToday_: () => true,
  fmtDate_: x => x, latestTradingDayStr_: () => '2026/09/24', tradingDaysAfter_: () => 0,
  dailyKState_: () => ({ finishedAt: '2026/09/23 16:54' }), dailyPushStartTime_: () => '1200',
  getSheet_: () => ({ getRange: () => ({ getValues: () => [['發文時間']] }), getLastColumn: () => 1, getLastRow: () => 1 }),
  readSheetObjects_: name => ({
    '系統狀態': [], '影片清單': [], '操作紀錄': [], '會員持股': [],
    '每日推播內容': [{ '日期': '2026/09/24', '寄送狀態': '已寄送' }],
    '每日績效': [{ '日期': '2026/09/23' }],
    '持股追蹤': [{ '代號': '3529', '股票名稱': '力旺', '狀態': '已出場', '最近賣出日': '2026/09/23', '出場價': '' }]
  })[name] || [],
  deliverySummaryForDate_: () => ({
    'daily|2026/09/24': { kind: 'daily', total: 2, accepted: 2, failed: 0, unknown: 0, open: 0, firstAcceptedAt: '2026/09/24 12:05:00', lastAcceptedAt: '2026/09/24 12:05:01' },
    'sms|123': { kind: 'sms', total: 2, accepted: 1, failed: 0, unknown: 0, open: 1, firstAcceptedAt: '2026/09/24 11:15:00', lastAcceptedAt: '2026/09/24 11:15:00' }
  }),
  ADMIN_JOB_SHEET: '後台工單', AUTO_JOB_SHEET: '自動工單', TZ: 'Asia/Taipei'
});
vm.runInContext(source('Adminservice.gs'), admin);
admin.adminAuth_ = () => {};
const status = admin.apiAdminTodayStatus('key');
assert(status.ok, status.reason);
assert.equal(status.ops.mailByKind.daily.accepted, 2);
assert.equal(status.ops.mailByKind.sms.accepted, 1);
assert.equal(status.ops.mailByKind.sms.open, 1);
assert.equal(status.ops.blankExits.length, 1);
assert.equal(status.ops.marketScheduled, true);
assert.equal(status.ops.hourlyScheduled, false);
assert(status.timeline.some(x => x.kind === '每日整理信' && x.time === '12:05'));
assert(status.timeline.some(x => x.kind === '盤中即時信' && x.time === '11:15'));

const mail = vm.createContext({
  fmtDate_: x => x,
  TZ: 'Asia/Taipei',
  Utilities: { formatDate: () => '2026/09/24 12:05:00' },
  readSheetObjects_: () => [{ '日期': '2026/09/24', '訊息ID': 'daily|2026/09/24', '種類': 'daily',
    '狀態': 'accepted', '服務接受時間': '2026/09/24 12:05:00' }]
});
vm.runInContext(source('MailService.gs'), mail);
assert.equal(mail.deliverySummaryForDate_('2026/09/24')['daily|2026/09/24'].firstAcceptedAt, '2026/09/24 12:05:00');
console.log('ops and exit v57 ok');
