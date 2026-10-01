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
  dayVideoRow_: date => (sheets['影片清單'] || []).find(r => r['發布日期'] === date) || null,
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

sheets['影片清單'].push({ '發布日期': day, '原始逐字稿內容': '今日原文'.repeat(60), '處理狀態': '完成' });
status = context.apiAdminTodayStatus('test');
assert.equal(status.ops.pipelineDone, false, '原文與完成旗標不能代替已發布文章');
sheets['每日推播內容'].push({ '日期': day, '內文Markdown': '已核對的文章' });
status = context.apiAdminTodayStatus('test');
assert.equal(status.ops.pipelineDone, true, '文章與逐字稿都就緒才算完成');

hour = '2300';
heartbeat = Date.now() - 25 * 60000;
status = context.apiAdminTodayStatus('test');
assert.equal(status.automation[0].tone, 'err', 'stale trigger heartbeat needs action');
assert.equal(status.automation[3].tone, 'idle', 'no daily subscribers is not an email failure');
console.log('Admin health v79 evidence and private SMS summary passed');

// 歷史原文不能跟著每次後台輪詢搬回來；同日多列交給共用選列函式。
const rowReader = source.slice(source.indexOf('function dayVideoRow_(day){'), source.indexOf('function transcriptTodayState_(day){'));
const table = [['發布日期','原始逐字稿內容','處理狀態'],
  ['2026/09/28','舊日期長文','完成'], [day,'舊當日稿','完成'], [day,'新當日稿','待處理']];
const ranges = [];
const readerContext = { fmtDate_: x => x, getSheet_: () => ({
  getLastRow: () => table.length, getLastColumn: () => table[0].length,
  getRange: (row,col,count,width) => { ranges.push([row,col,count,width]); return {
    getDisplayValues: () => table.slice(row-1,row-1+count).map(r=>r.slice(col-1,col-1+width)) }; }
}), selectTranscriptRow_: rows => ({row:rows[rows.length-1]}) };
vm.createContext(readerContext); vm.runInContext(rowReader,readerContext);
assert.equal(readerContext.dayVideoRow_(day)['原始逐字稿內容'],'新當日稿');
assert.deepEqual(ranges, [[1,1,1,3],[2,1,3,1],[3,1,1,3],[4,1,1,3]],'只讀日期欄及當日兩列，不讀其他日期長文');
