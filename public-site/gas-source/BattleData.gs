/* 一次可以讀幾檔（v136）。
   2026/10/06 實測：一次 24 檔約 6.7 秒、回應 354KB（每根日K 約 101 位元組），240 檔要 10 次請求、約 25 秒。
   精簡格式把每根日K 從物件改成陣列 [日期, 開, 高, 低, 收, 量]——同樣六個值、同樣 160 根，只是不重複寫欄位名稱，
   每根約 40 位元組；一次 60 檔的回應大小與原本 24 檔相當（Worker 與瀏覽器已經驗證過的大小），240 檔只要 4 次請求。
   舊版前台不帶第二個參數，照舊拿到原格式、一次 24 檔。 */
var BATTLE_BATCH_V1_ = 24, BATTLE_BATCH_V2_ = 40;
/* 計算用的日K 長度（v138）。原本一律只給最近 160 根，是本站 API 自己截的，不是資料只有這麼多（日K快取約有 12 個月、245 根上下）。
   指標需要暖機：EMA、Wilder 平滑的第一筆是種子，前面的根數越少，尾端的數字越受種子影響；240 日均線更是直接需要 240 根。
   精簡格式改給手上有的全部（最多 260 根），一批的檔數由 60 降到 40，回應大小和原本相當；240 檔由 4 次請求變成 6 次，
   前台一次同時發 3 個，仍是兩輪。原格式（舊版前台）維持 160 根、一批 24 檔，行為不變。 */
var BATTLE_BARS_V1_ = 160, BATTLE_BARS_V2_ = 260;

/** 一檔的日K 轉成精簡陣列。日期寫成 20261006 這種數字；量與原格式相同（張，沒有成交量是 null）。 */
function battleCompactBars_(bars) {
  return bars.map(function (b) {
    return [Number(String(b.date).replace(/\D/g, '')), b.open, b.high, b.low, b.close, b.volume];
  });
}

/* 臨時休市（颱風等）不在年度假日表裡（v138）。
   行事曆若仍把那一天當交易日，每一檔那天都沒有日K，前台需要連續日K 的指標全部變成資料不足
   （2026/07/10 即是：正式站的 MACD、KD 與所有長週期條件都算不出來）。

   多檔同一天缺日K 也可能只是共同抓取失敗，所以「缺K」只用來找候選日，不能直接當成休市：
     候選：參考股票裡至少兩檔前後都有日K、而且沒有任何一檔那天有日K。
     核對：向證交所要那個月的加權指數歷史（官方資料）。那個月有資料、候選日前後都有交易日、唯獨沒有那一天 → 核對為休市。
           官方有那一天 → 是我們缺資料，記為「待補」，行事曆不動，該日照實是資料缺口。
           查不到或回應不完整 → 不下結論，下次再查。
   核對過的日子存在指令碼屬性；戰情行情只讀這份結果，讀行情時不對外連線。 */
var BATTLE_CLOSED_KNOWN_ = ['2026-07-10'];        // 2026/10/06 以證交所 7 月指數歷史與 2330 日成交核對：07/09 之後是 07/13
var BATTLE_CLOSED_PROP_ = 'TWSE_CLOSED_EXTRA';    // {closed: ['yyyy-MM-dd'], gaps: {'yyyy-MM-dd': 查核時間}, at: 上次查核}
var BATTLE_CALENDAR_REFS_ = ['2330', '2317', '2454', '2303', '2308'];

function battleClosedState_() {
  var st = { closed: [], gaps: {}, at: 0 };
  try {
    var raw = JSON.parse(PropertiesService.getScriptProperties().getProperty(BATTLE_CLOSED_PROP_) || 'null');
    if (raw && Array.isArray(raw.closed)) {
      st.closed = raw.closed.filter(function (d) { return /^\d{4}-\d{2}-\d{2}$/.test(d); });
      st.gaps = raw.gaps && typeof raw.gaps === 'object' ? raw.gaps : {};
      st.at = Number(raw.at) || 0;
    }
  } catch (e) {}
  return st;
}

/** 已核對的臨時休市日（yyyy-MM-dd → 1）。 */
function battleExtraClosed_() {
  var set = {};
  BATTLE_CLOSED_KNOWN_.concat(battleClosedState_().closed).forEach(function (d) { set[d] = 1; });
  return set;
}

/** 候選休市日：只看日K 的缺口，不下結論。calendar 與回傳值都是 yyyy/MM/dd。 */
function battleClosedCandidates_(calendar, seriesList, today) {
  var has = {}, spans = [];
  seriesList.forEach(function (bars) {
    var ds = (bars || []).map(function (b) { return String(b.date || '').replace(/-/g, '/'); }).filter(function (d) { return /^\d{4}\/\d{2}\/\d{2}$/.test(d); }).sort();
    if (ds.length < 2) { return; }
    ds.forEach(function (d) { has[d] = 1; });
    spans.push([ds[0], ds[ds.length - 1]]);
  });
  return calendar.filter(function (d) {
    if (!(d < today) || has[d]) { return false; }
    var n = 0;
    for (var i = 0; i < spans.length && n < 2; i++) { if (spans[i][0] < d && d < spans[i][1]) { n++; } }
    return n >= 2;
  });
}

/** 證交所某個月有交易的日子（yyyy/MM/dd）。查不到或格式不對回傳 null，不猜。 */
function twseTradingDaysOfMonth_(yyyymm) {
  var url = 'https://www.twse.com.tw/indicesReport/MI_5MINS_HIST?response=json&date=' + yyyymm + '01';
  var resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (resp.getResponseCode() !== 200) { return null; }
  var body = JSON.parse(resp.getContentText());
  if (body.stat !== 'OK' || !Array.isArray(body.data)) { return null; }
  var days = [];
  body.data.forEach(function (row) {
    var m = String(row[0] || '').match(/^(\d{2,3})\/(\d{2})\/(\d{2})$/);          // 民國年／月／日
    if (m) { days.push((Number(m[1]) + 1911) + '/' + m[2] + '/' + m[3]); }
  });
  return days.length ? days.sort() : null;
}

/** 一個候選日對照官方那個月的交易日：'closed' 休市、'gap' 官方有交易（我們缺資料）、'' 無法下結論。 */
function battleJudgeCandidate_(day, officialDays) {
  if (!officialDays || officialDays.length < 5 || officialDays[0].slice(0, 7) !== day.slice(0, 7)) { return ''; }
  if (officialDays.indexOf(day) >= 0) { return 'gap'; }
  // 候選日前後都要有官方交易日（同一個月內），才知道這個月的資料已經涵蓋那一天
  var before = officialDays.some(function (d) { return d < day; }), after = officialDays.some(function (d) { return d > day; });
  return before && after ? 'closed' : '';
}

/**
 * 找候選休市日並向證交所核對。一天最多跑一次、一次最多查三個月；沒有候選就不對外連線。
 * 排程每天收盤後呼叫；管理者也可在編輯器執行 checkMarketClosures() 立即查並看結果。
 */
function confirmMarketClosuresTick_(force) {
  var pr = PropertiesService.getScriptProperties(), st = battleClosedState_(), now = new Date();
  var todayKey = Utilities.formatDate(now, TZ, 'yyyy-MM-dd');
  if (!force && Utilities.formatDate(new Date(st.at || 0), TZ, 'yyyy-MM-dd') === todayKey) { return { skipped: '今天已查過' }; }
  var known = battleExtraClosed_(), holidays = marketHolidaySet_(), calendar = [], cursor = new Date(now.getTime());
  cursor.setHours(12, 0, 0, 0);
  for (var i = 0; i < 370; i++) {
    var iso = Utilities.formatDate(cursor, TZ, 'yyyy-MM-dd');
    if (Number(Utilities.formatDate(cursor, TZ, 'u')) <= 5 && !holidays[iso] && !known[iso]) { calendar.unshift(iso.replace(/-/g, '/')); }
    cursor.setDate(cursor.getDate() - 1);
  }
  var series = [], got = {};
  try { got = CACHE.getAll(BATTLE_CALENDAR_REFS_.map(function (c) { return 'dk2_' + c; })); } catch (e) {}
  BATTLE_CALENDAR_REFS_.forEach(function (c) { if (got['dk2_' + c]) { try { series.push(kcDecode_(got['dk2_' + c])); } catch (e) {} } });
  var today = todayKey.replace(/-/g, '/');
  var candidates = battleClosedCandidates_(calendar, series, today).filter(function (d) { return !st.gaps[d.replace(/\//g, '-')] || force; });
  var out = { candidates: candidates, closed: [], gaps: [], unknown: [], references: series.length };
  if (candidates.length) {
    var months = {}, asked = 0;
    candidates.forEach(function (d) {
      var ym = d.slice(0, 4) + d.slice(5, 7);
      if (!(ym in months)) { if (asked >= 3) { out.unknown.push(d); return; } asked++; try { months[ym] = twseTradingDaysOfMonth_(ym); } catch (e) { months[ym] = null; } }
      var verdict = battleJudgeCandidate_(d, months[ym]), key = d.replace(/\//g, '-');
      if (verdict === 'closed') { if (st.closed.indexOf(key) < 0) { st.closed.push(key); } out.closed.push(d); }
      else if (verdict === 'gap') { st.gaps[key] = nowStamp_(); out.gaps.push(d); }
      else { out.unknown.push(d); }
    });
  }
  st.closed.sort(); st.at = now.getTime();
  pr.setProperty(BATTLE_CLOSED_PROP_, JSON.stringify(st));
  if (out.closed.length || out.gaps.length) {
    Logger.log('臨時休市查核：核對為休市 ' + (out.closed.join('、') || '無') + '；官方有交易、本站缺日K（待補）' + (out.gaps.join('、') || '無'));
  }
  return out;
}

/** 管理者在編輯器執行：立即查核並回報。 */
function checkMarketClosures() {
  var r = confirmMarketClosuresTick_(true), st = battleClosedState_();
  var text = '候選 ' + (r.candidates || []).join('、') + '\n核對為休市 ' + (r.closed || []).join('、') + '\n官方有交易、本站缺日K ' + (r.gaps || []).join('、') +
    '\n無法下結論 ' + (r.unknown || []).join('、') + '\n目前已核對的臨時休市日 ' + BATTLE_CLOSED_KNOWN_.concat(st.closed).join('、');
  Logger.log(text);
  return r;
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
    var extraClosed = battleExtraClosed_(), closed = [];      // 已向官方核對的臨時休市日：不算交易日
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
      if (years[year] && Number(Utilities.formatDate(cursor, TZ, 'u')) <= 5 && !holidays[iso]) {
        if (extraClosed[iso]) { closed.unshift(iso.replace(/-/g, '/')); } else { calendar.unshift(iso.replace(/-/g, '/')); }
      }
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
      var list = bars.slice(-(v2 ? BATTLE_BARS_V2_ : BATTLE_BARS_V1_)).map(function (b) { return { date: b.date,
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
      limits: { sameTimeVolume: false, turnover: false, institutions: false, calendarYearsMissing: Object.keys(unknown), marketClosed: closed,
        corporateActions: '未取得完整股務事件；參考價異常時阻擋判定' } };
    if (v2) {
      out.format = 2; out.batchMax = batchMax; out.barsMax = BATTLE_BARS_V2_;
      // 行事曆與清單的指紋：省略它們的回應靠這兩個值確認和第一個回應是同一份，對不上前台會整份重讀。
      out.calendarKey = calendar.length + ':' + (calendar[0] || '') + ':' + (calendar[calendar.length - 1] || '');
      out.universeKey = universe.length + ':' + (universe.length ? universe[0].code + ':' + universe[universe.length - 1].code : '');
      if (lite) { delete out.calendar; delete out.universe; out.lite = true; }
    }
    return out;
  });
}
