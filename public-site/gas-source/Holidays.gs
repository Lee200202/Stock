/**
 * 檔案：Holidays.gs
 * 台股休市日。由 scripts/update_holidays.py 從證交所資料自動產生，不要手改。
 *
 * 為什麼需要：原本只看星期幾，國定假日落在平日時市場休市、沒有盤中直播，
 * 下游仍會照寄推播、狀態信與告警。那些信是假的，看久了會把真正的異常一起忽略。
 *
 * 十月起每週查隔年正式行事曆，查到後存指令碼屬性。未核實年份暫停台股自動流程，
 * 避免在未知假日誤抓影片；會員簡訊獨立續行。不可把今年資料冒充隔年資料。
 *
 * 管理者可執行 checkNextYearHolidays() 即時查看隔年盤點狀態。
 * 目前收錄：2025, 2026
 */

var MARKET_HOLIDAYS_ = [
  // ---- 2025 ----
  '2025-01-01',  // (三) 中華民國開國紀念日
  '2025-01-23',  // (四) 市場無交易，僅辦理結算交割作業
  '2025-01-24',  // (五) 市場無交易，僅辦理結算交割作業
  '2025-01-27',  // (一) 農曆除夕及春節
  '2025-01-28',  // (二) 農曆除夕及春節
  '2025-01-29',  // (三) 農曆除夕及春節
  '2025-01-30',  // (四) 農曆除夕及春節
  '2025-01-31',  // (五) 農曆除夕及春節
  '2025-02-28',  // (五) 和平紀念日
  '2025-04-03',  // (四) 兒童節及民族掃墓節
  '2025-04-04',  // (五) 兒童節及民族掃墓節
  '2025-05-01',  // (四) 勞動節
  '2025-05-30',  // (五) 端午節
  '2025-09-29',  // (一) 孔子誕辰紀念日/ 教師節補假
  '2025-10-06',  // (一) 中秋節
  '2025-10-10',  // (五) 國慶日
  '2025-10-24',  // (五) 臺灣光復節補假
  '2025-12-25',  // (四) 行憲紀念日
  // ---- 2026 ----
  '2026-01-01',  // (四) 中華民國開國紀念日
  '2026-02-12',  // (四) 市場無交易，僅辦理結算交割作業
  '2026-02-13',  // (五) 市場無交易，僅辦理結算交割作業
  '2026-02-15',  // (日) 農曆除夕及春節
  '2026-02-16',  // (一) 農曆除夕及春節
  '2026-02-17',  // (二) 農曆除夕及春節
  '2026-02-18',  // (三) 農曆除夕及春節
  '2026-02-19',  // (四) 農曆除夕及春節
  '2026-02-20',  // (五) 農曆除夕及春節
  '2026-02-27',  // (五) 和平紀念日
  '2026-02-28',  // (六) 和平紀念日
  '2026-04-03',  // (五) 兒童節及民族掃墓節
  '2026-04-04',  // (六) 兒童節及民族掃墓節
  '2026-04-05',  // (日) 兒童節及民族掃墓節
  '2026-04-06',  // (一) 兒童節及民族掃墓節
  '2026-05-01',  // (五) 勞動節
  '2026-06-19',  // (五) 端午節
  '2026-09-25',  // (五) 中秋節
  '2026-09-28',  // (一) 孔子誕辰紀念日/ 教師節
  '2026-10-09',  // (五) 國慶日
  '2026-10-10',  // (六) 國慶日
  '2026-10-25',  // (日) 臺灣光復暨金門古寧頭大捷紀念日
  '2026-10-26',  // (一) 臺灣光復暨金門古寧頭大捷紀念日
  '2026-12-25',  // (五) 行憲紀念日
];

var MARKET_HOLIDAY_SET_ = null;
var MARKET_HOLIDAY_YEARS_ = null;
var HOLIDAY_PROP_PREFIX_ = 'TWSE_HOLIDAYS_';

function marketHolidaySet_() {
  if (!MARKET_HOLIDAY_SET_) {
    MARKET_HOLIDAY_SET_ = {};
    MARKET_HOLIDAY_YEARS_ = {};
    MARKET_HOLIDAYS_.forEach(function (d) {
      MARKET_HOLIDAY_SET_[d] = 1;
      MARKET_HOLIDAY_YEARS_[d.slice(0, 4)] = 1;
    });
  }
  return MARKET_HOLIDAY_SET_;
}

/** 查證交所正式年度表；查詢尚未公布時不冒充該年已核實。 */
function refreshHolidayYear_(year, force) {
  marketHolidaySet_();
  var pr = PropertiesService.getScriptProperties();
  var key = HOLIDAY_PROP_PREFIX_ + year;
  var saved = pr.getProperty(key);
  if (saved) {
    try {
      var dates = JSON.parse(saved);
      if (dates.length >= 5 && dates.every(function (s) { return String(s).slice(0, 4) === String(year); })) {
        dates.forEach(function (s) { MARKET_HOLIDAY_SET_[s] = 1; });
        MARKET_HOLIDAY_YEARS_[String(year)] = 1;
        return true;
      }
    } catch (e) { Logger.log('休市日快取無法解析：' + e); }
  }
  var attemptKey = key + '_ATTEMPT';
  var last = Number(pr.getProperty(attemptKey) || '0');
  if (!force && Date.now() - last < 7 * 86400000) { return false; }
  pr.setProperty(attemptKey, String(Date.now()));
  try {
    var url = 'https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?response=json&queryYear=' + year;
    var resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    if (resp.getResponseCode() !== 200) { throw new Error('HTTP ' + resp.getResponseCode()); }
    var body = JSON.parse(resp.getContentText());
    if (body.stat !== 'ok') { throw new Error('stat=' + body.stat); }
    var dates = [];
    (body.data || []).forEach(function (row) {
      var day = String(row[0] || '');
      var name = String(row[1] || '');
      if (day.slice(0, 4) === String(year) && !/開始交易|最後交易/.test(name) && /^\d{4}-\d{2}-\d{2}$/.test(day)) {
        dates.push(day);
      }
    });
    dates = Array.from(new Set(dates)).sort();
    if (dates.length < 5) { throw new Error('該年尚未公布或內容不足'); }
    pr.setProperty(key, JSON.stringify(dates));
    dates.forEach(function (s) { MARKET_HOLIDAY_SET_[s] = 1; });
    MARKET_HOLIDAY_YEARS_[String(year)] = 1;
    Logger.log('證交所 ' + year + ' 年休市日已核實：' + dates.length + ' 天');
    return true;
  } catch (e) {
    Logger.log('證交所 ' + year + ' 年休市日尚無法核實：' + e);
    return false;
  }
}

/** 年底每週追查隔年表；已成功時只讀快取，不再呼叫外部 API。 */
function holidayCalendarTick_() {
  var now = new Date();
  var year = Number(Utilities.formatDate(now, 'Asia/Taipei', 'yyyy'));
  var month = Number(Utilities.formatDate(now, 'Asia/Taipei', 'MM'));
  if (month >= 10) { refreshHolidayYear_(year + 1, false); }
}

/** 管理者可在編輯器檢查次年；force 只突破七天重試間隔。 */
function checkNextYearHolidays() {
  var next = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy')) + 1;
  var ok = refreshHolidayYear_(next, true);
  var result = { year: next, verified: ok, count: ok ? Object.keys(marketHolidaySet_()).filter(function (d) { return d.slice(0, 4) === String(next); }).length : 0 };
  Logger.log(JSON.stringify(result));
  return result;
}

/** 這一天是不是休市日。不含週末，週末另外用星期判斷。 */
function isMarketHoliday_(d) {
  var set = marketHolidaySet_();
  var tz = 'Asia/Taipei';
  var ymd = Utilities.formatDate(d || new Date(), tz, 'yyyy-MM-dd');
  if (!MARKET_HOLIDAY_YEARS_[ymd.slice(0, 4)]) {
    if (!refreshHolidayYear_(Number(ymd.slice(0, 4)), false)) {
      Logger.log('警告：' + ymd.slice(0, 4) + ' 年休市日尚未核實，自動交易日工作暫停。');
      return true;
    }
  }
  return !!set[ymd];
}

/** 給日誌用的一句話。回空字串代表這天有開盤。 */
function whyClosed_(d) {
  var now = d || new Date();
  var wd = Number(Utilities.formatDate(now, 'Asia/Taipei', 'u'));
  if (wd > 5) { return '週末不開盤'; }
  if (isMarketHoliday_(now)) {
    return MARKET_HOLIDAY_YEARS_[Utilities.formatDate(now, 'Asia/Taipei', 'yyyy')] ? '台股休市日' : '年度休市日尚未核實';
  }
  return '';
}
