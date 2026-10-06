/* 一次可以讀幾檔（v136）。
   2026/10/06 實測：一次 24 檔約 6.7 秒、回應 354KB（每根日K 約 101 位元組），240 檔要 10 次請求、約 25 秒。
   精簡格式把每根日K 從物件改成陣列 [日期, 開, 高, 低, 收, 量]——同樣六個值、同樣 160 根，只是不重複寫欄位名稱，
   每根約 40 位元組；一次 60 檔的回應大小與原本 24 檔相當（Worker 與瀏覽器已經驗證過的大小），240 檔只要 4 次請求。
   舊版前台不帶第二個參數，照舊拿到原格式、一次 24 檔。 */
var BATTLE_BATCH_V1_ = 24, BATTLE_BATCH_V2_ = 60;

/** 一檔的日K 轉成精簡陣列。日期寫成 20261006 這種數字；量與原格式相同（張，沒有成交量是 null）。 */
function battleCompactBars_(bars) {
  return bars.map(function (b) {
    return [Number(String(b.date).replace(/\D/g, '')), b.open, b.high, b.low, b.close, b.volume];
  });
}

/** 戰情追蹤僅讀落地行情；不抓新行情、不重算績效、不啟動觸發器。options.v=2 用精簡格式；options.lite 省略這一輪第一個回應已經給過的清單與行事曆。 */
function apiGetBattleData(requested, options) {
  var v2 = !!options && Number(options.v) >= 2, lite = v2 && options.lite === true, batchMax = v2 ? BATTLE_BATCH_V2_ : BATTLE_BATCH_V1_;
  return withSheetSnapshot_(function () {
    var map = loadCodeMap_().byCode, tracked = trackedCodes_();
    var universe = tracked.map(function (c) { return { code: c, name: (map[c] || {}).name || c }; });
    var codes = Array.isArray(requested) ? requested : tracked.slice(0, batchMax);
    codes = codes.map(function (c) { return String(c).trim(); }).filter(function (c, i, a) {
      return /^(?:00981A|\d{4,6})$/.test(c) && a.indexOf(c) === i;
    });
    if (codes.length > batchMax) { throw new Error('一次最多讀取 ' + batchMax + ' 檔，請換頁查詢'); }
    var now = new Date(), today = Utilities.formatDate(now, TZ, 'yyyy/MM/dd');
    var calendar = [], cursor = new Date(now.getTime()), holidays = marketHolidaySet_();
    var years = Object.assign({}, MARKET_HOLIDAY_YEARS_), unknown = {};
    var savedProperties = PropertiesService.getScriptProperties();
    cursor.setHours(12, 0, 0, 0);
    for (var i = 0; i < 370; i++) {
      var iso = Utilities.formatDate(cursor, TZ, 'yyyy-MM-dd'), year = iso.slice(0, 4);
      if (!years[year] && !unknown[year]) {
        try {
          var savedDays = JSON.parse(savedProperties.getProperty(HOLIDAY_PROP_PREFIX_ + year) || 'null');
          if (Array.isArray(savedDays) && savedDays.length >= 5 && savedDays.every(function (s) { return /^\d{4}-\d{2}-\d{2}$/.test(s) && s.slice(0, 4) === year; })) {
            savedDays.forEach(function (s) { holidays[s] = 1; }); years[year] = 1;
          } else { unknown[year] = 1; }
        } catch (e) { unknown[year] = 1; }
      }
      if (years[year] && Number(Utilities.formatDate(cursor, TZ, 'u')) <= 5 && !holidays[iso]) { calendar.unshift(iso.replace(/-/g, '/')); }
      cursor.setDate(cursor.getDate() - 1);
    }
    var date = years[today.slice(0, 4)] ? calendar[calendar.length - 1] || '' : '', quotes = getQuoteCache();
    var hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
    var keys = codes.map(function (c) { return 'dk2_' + c; }), cached = {}, daily = null;
    try { cached = CACHE.getAll(keys); } catch (e) {}
    var items = codes.map(function (code) {
      var bars = [], error = '';
      try {
        if (cached['dk2_' + code]) { bars = kcDecode_(cached['dk2_' + code]); }
        else { if (!daily) { daily = loadAllDailyK_(); } bars = daily[code] || []; }
      } catch (e) { error = '日 K 讀取失敗'; }
      var q = quotes[code] || null;
      var list = bars.slice(-160).map(function (b) { return { date: b.date,
          open: b.open, high: b.high, low: b.low, close: b.close,
          volume: b.volume == null || !(Number(b.volume) > 0) ? null : Number(b.volume) / 1000 }; });
      var quote = q ? { date: q.date || '', open: q.open, high: q.high, low: q.low,
          last: q.last, prevClose: q.prevClose, volume: q.volume, stamp: q.stamp instanceof Date ? q.stamp.toISOString() : q.stamp,
          time: q.time || '', stale: !quoteFresh_(q.stamp) } : null;
      // 精簡格式：同一份 list 換個寫法（b），報價欄位一個不少（q）。
      return v2 ? { code: code, name: (map[code] || {}).name || code, error: error, b: battleCompactBars_(list), q: quote }
                : { code: code, name: (map[code] || {}).name || code, error: error, bars: list, quote: quote };
    });
    var out = { ok: true, date: date, today: today, at: nowStamp_(),
      afterClose: date < today || hhmm >= 1400, calendar: calendar,
      volumeUnit: 'lots', universe: universe, items: items,
      quoteCadence: opsRuntimeConserve_() ? '用量保護中，排程約 10 分鐘' : '排程約 5 分鐘',
      limits: { sameTimeVolume: false, turnover: false, institutions: false, calendarYearsMissing: Object.keys(unknown),
        corporateActions: '未取得完整股務事件；參考價異常時阻擋判定' } };
    if (v2) {
      out.format = 2; out.batchMax = batchMax;
      // 行事曆與清單的指紋：省略它們的回應靠這兩個值確認和第一個回應是同一份，對不上前台會整份重讀。
      out.calendarKey = calendar.length + ':' + (calendar[0] || '') + ':' + (calendar[calendar.length - 1] || '');
      out.universeKey = universe.length + ':' + (universe.length ? universe[0].code + ':' + universe[universe.length - 1].code : '');
      if (lite) { delete out.calendar; delete out.universe; out.lite = true; }
    }
    return out;
  });
}
