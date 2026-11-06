// 持股追蹤的交易日曆（SheetService.gs rebuildHoldingsTrackerJob 裡的 tradingDays）：
// 今天的節目處理完時，今天也算一個「沒有提到」的交易日；過去的日子仍以日K快取為準（2026/11/06 毅嘉、達邁）。
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'SheetService.gs'), 'utf8');
const start = src.indexOf('  var tradingDays = (function () {');
const end = src.indexOf('  var STALE_TRADING_DAYS = 10;', start);
assert(start > 0 && end > start, '找不到交易日曆區塊');
const block = src.slice(start, end);

const KDAYS = ['2026/10/15', '2026/10/16', '2026/10/19', '2026/10/20', '2026/10/21', '2026/10/22', '2026/10/27', '2026/10/28',
  '2026/10/29', '2026/10/30', '2026/11/02', '2026/11/03', '2026/11/04', '2026/11/05'];   // 10/23、10/24 颱風休市不在快取裡

function calendar(now, videoStatus, tradingToday = true) {
  const env = {
    now,
    fmtDate_: (d) => d,
    isTradingDateStr_: (d) => d !== now || tradingToday,
    Logger: { log() {} },
    readSheetObjects_: (name) => name === '日K快取'
      ? KDAYS.map((d) => ({ '日期': d, '收': 100 }))
      : [{ '發布日期': now, '處理狀態': videoStatus }],
  };
  return new Function(...Object.keys(env), block + '\nreturn { tradingDays: tradingDays, after: tradingDaysAfter_ };')(...Object.values(env));
}

// 11/06 節目已處理：最後提及 10/20 → 第 11 個交易日，超過 10，應出場；10/21 → 第 10 個，還不出場
let c = calendar('2026/11/06', '完成');
assert.strictEqual(c.after('2026/10/20'), 11);
assert.strictEqual(c.after('2026/10/21'), 10);
// 節目還沒處理完：今天不算（與先前相同）
c = calendar('2026/11/06', '處理中');
assert.strictEqual(c.after('2026/10/20'), 10);
// 休市日：今天不算
c = calendar('2026/11/07', '完成', false);
assert.strictEqual(c.after('2026/10/20'), 10);
// 過去的颱風休市（10/23、10/24）不會被補進來
assert.ok(!calendar('2026/11/06', '完成').tradingDays.includes('2026/10/23'));
console.log('持股追蹤交易日曆：5 項通過');
