/** 戰情追蹤僅讀落地行情；不抓新行情、不重算績效、不啟動觸發器。 */
function apiGetBattleData(requested) {
  return withSheetSnapshot_(function () {
    var map = loadCodeMap_().byCode, tracked = trackedCodes_();
    var universe = tracked.map(function (c) { return { code: c, name: (map[c] || {}).name || c }; });
    var codes = Array.isArray(requested) ? requested : tracked.slice(0, 24);
    codes = codes.map(function (c) { return String(c).trim(); }).filter(function (c, i, a) {
      return /^(?:00981A|\d{4,6})$/.test(c) && a.indexOf(c) === i;
    });
    if (codes.length > 24) { throw new Error('一次最多讀取 24 檔，請換頁查詢'); }
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
      return { code: code, name: (map[code] || {}).name || code, error: error,
        bars: bars.slice(-160).map(function (b) { return { date: b.date,
          open: b.open, high: b.high, low: b.low, close: b.close,
          volume: b.volume == null || !(Number(b.volume) > 0) ? null : Number(b.volume) / 1000 }; }),
        quote: q ? { date: q.date || '', open: q.open, high: q.high, low: q.low,
          last: q.last, prevClose: q.prevClose, volume: q.volume, stamp: q.stamp instanceof Date ? q.stamp.toISOString() : q.stamp,
          time: q.time || '', stale: !quoteFresh_(q.stamp) } : null };
    });
    return { ok: true, date: date, today: today, at: nowStamp_(),
      afterClose: date < today || hhmm >= 1400, calendar: calendar,
      volumeUnit: 'lots', universe: universe, items: items,
      quoteCadence: opsRuntimeConserve_() ? '用量保護中，排程約 10 分鐘' : '排程約 5 分鐘',
      limits: { sameTimeVolume: false, turnover: false, institutions: false, calendarYearsMissing: Object.keys(unknown),
        corporateActions: '未取得完整股務事件；參考價異常時阻擋判定' } };
  });
}
