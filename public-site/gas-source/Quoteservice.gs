/**
 * 檔案：QuoteService.gs
 *
 * 報價來源分工：
 *   證交所 MIS 批次即時報價；Fugle 補取未回傳標的、當日與歷史 60 分 K。
 *   Yahoo 在獨立分頁補歷史空缺。
 *   歷史日K    由 CacheBuilder 依既有來源落地；週／月從相同日K聚合。
 *
 * 優先讀共用快取，盤中預覽按需取 Fugle；請求跨執行控速。
 *
 * 金鑰規範：FUGLE_API_KEY 只存在 Script Properties，只在本檔的
 * UrlFetchApp 呼叫中使用。前端、回傳值、錯誤訊息都不會出現金鑰內容。
 */

var CACHE = CacheService.getScriptCache();
var CODE_TTL = 21600;

/* ------------------------------------------------------------------ *
 * Fugle
 *
 * 本站預設上限：日內 50 次/分、歷史 55 次/分。不是所有方案的額度保證。
 * 帳戶配額更低時，使用 FUGLE_INTRADAY_RPM／FUGLE_HISTORY_RPM 降低上限。
 * ------------------------------------------------------------------ */
var FUGLE_BASE = 'https://api.fugle.tw/marketdata/v1.0/stock';

function getFugleKey_() {
  return PropertiesService.getScriptProperties().getProperty('FUGLE_API_KEY') || '';
}

function hasFugle_() { return !!getFugleKey_(); }

function getFinMindToken_() {
  var prop = PropertiesService.getScriptProperties().getProperty('FINMIND_API_TOKEN') ||
             PropertiesService.getScriptProperties().getProperty('FINMIND_TOKEN');
  if (prop && prop.trim()) { return prop.trim(); }
  if (typeof FINMIND_API_TOKEN_DEFAULT !== 'undefined' && FINMIND_API_TOKEN_DEFAULT) {
    return FINMIND_API_TOKEN_DEFAULT.trim();
  }
  return '';
}

function hasFinMind_() {
  var token = getFinMindToken_();
  if (token) { return true; }
  var pref = PropertiesService.getScriptProperties().getProperty('USE_FINMIND');
  if (pref === 'true') { return true; }
  var provider = PropertiesService.getScriptProperties().getProperty('DAILYK_PROVIDER');
  if (provider === 'finmind') { return true; }
  return false;
}

function fugleFetch_(path, market) {
  // 所有股票／指數共用帳戶上限，避免各自未超標、合計卻過量。
  if(typeof marketPace_==='function'){marketPace_('all',marketGap_('all',50));}
  if(path.indexOf('/intraday/')===0 && typeof marketPace_==='function'){marketPace_('intraday', marketGap_('intraday',50));}
  var key = market==='futopt' ? (PropertiesService.getScriptProperties().getProperty('FUGLE_FUTOPT_API_KEY')||getFugleKey_()) : getFugleKey_();
  if (!key) { throw new Error('尚未設定 FUGLE_API_KEY。'); }

  var res = UrlFetchApp.fetch((market==='futopt'?'https://api.fugle.tw/marketdata/v1.0/futopt':FUGLE_BASE) + path, {
    muteHttpExceptions: true,
    headers: { 'X-API-KEY': key }
  });
  var code = res.getResponseCode();

  if (code !== 200) {
    // 金鑰在 header 不在 URL，所以回應內容可以安全顯示。
    // 先前只印 HTTP 400，完全看不出是區間太長還是別的問題，白花很多時間。
    var body = '';
    try {
      var j = JSON.parse(res.getContentText());
      body = (j.message || j.error || res.getContentText()).toString().slice(0, 160);
    } catch (e) {
      body = res.getContentText().slice(0, 160);
    }
    var err = new Error('Fugle HTTP ' + code + (body ? '：' + body : ''));
    err.httpCode = code;
    throw err;
  }
  return JSON.parse(res.getContentText());
}

/** 當日即時報價 */
/* 成交量單位（2026/09/16 v38 查證後改寫）。

   先前所有富果來源共用一個指令碼屬性 FUGLE_VOLUME_UNIT 決定「股或張」，但富果的單位依端點不同：
     歷史 K（/historical/candles，日／週／月）　整股是「股」
     日內 K（/intraday/candles）與即時報價　　　整股是「張」
   （富果開發文件 Historical Candles 的 CAUTION、Intraday Candles 的 volume 欄位說明。）
   一個設定管不了兩種單位：設 lot 時日K快取存成股、設 share 時 60 分 K 與即時報價被多除 1000。

   實際核對證交所 STOCK_DAY 的成交股數：
     1506 2025/10/27～11/21 共 20 天，日K快取的量與成交股數逐日完全相同（開高低收也相同）。
     2385、2387、2392、2404 在 2026/07/21 的 60 分 K 加總（張）是官方成交張數的 88%～99%，
     差額是官方含零股、盤後定價、鉅額交易，60 分 K 不含；四檔的開高低收與官方完全相同。

   現在固定：
     日K快取「量」＝股（與證交所成交股數同單位，逐日可核對）
     小時K「量」、即時報價「成交量」＝張
     網站一律顯示張：getCandles 把日K／週K 的股數除以 1000，週K 先加總股數再換算。
   FUGLE_VOLUME_UNIT 屬性不再讀取。 */
var SHARES_PER_LOT_ = 1000;

/** 股 → 張。不取整：29,138 股就是 29.138 張，要取整留給畫面決定。 */
function sharesToLots_(shares) {
  var n = Number(shares) || 0;
  return n / SHARES_PER_LOT_;
}

/** 一整串日K（量＝股）換成量＝張的新陣列。不改動傳進來的物件（它們是整張表的共用索引）。 */
function volumeInLots_(rows) {
  return (rows || []).map(function (r) {
    var o = {};
    for (var k in r) { if (Object.prototype.hasOwnProperty.call(r, k)) { o[k] = r[k]; } }
    o.volume = sharesToLots_(r.volume);
    return o;
  });
}

/**
 * 成交量核對（2026/09/16 v38 改寫）。只讀，不寫任何表，也不改任何設定。
 *
 * 在編輯器執行 checkVolumeUnit('1506', '2025/11')，看執行紀錄。代號預設 2330、月份預設上個月。
 *   一、日K快取 vs 證交所 STOCK_DAY（上市股）：逐日比對成交股數與開高低收，應該完全相同。
 *   二、小時K 加總 vs 證交所：60 分 K 是整股成交張數、不含零股／盤後定價／鉅額交易，
 *       加總除以官方成交張數應落在 80%～100.5%，開盤＝第一根開、收盤＝最後一根收、高低＝極值。
 *   三、盤中執行時多比一項：富果即時報價 vs 證交所 MIS，兩者都是張，比值應接近 1。
 */
function checkVolumeUnit(code, ym) {
  code = String(code || '2330').trim();
  if (!ym) {
    var now = new Date();
    ym = Utilities.formatDate(new Date(now.getFullYear(), now.getMonth() - 1, 1), TZ, 'yyyy/MM');
  }
  ym = String(ym).replace('-', '/').slice(0, 7);
  var out = ['========================================',
             '  成交量核對　' + code + '　' + ym,
             '========================================'];
  var same = function (a, b) { return Math.abs(Number(a) - Number(b)) < 1e-6; };

  var official = {};
  fetchTwseMonth_(code, ym).forEach(function (r) { official[r.date] = r; });
  var days = Object.keys(official).sort();

  // 一、日K快取（量＝股）
  var cache = getCachedDailyK(code).filter(function (r) { return r.date.slice(0, 7) === ym; });
  var daily = { official: days.length, cached: cache.length, matched: 0, mismatch: [] };
  if (!days.length) {
    out.push('一、證交所查無 ' + code + ' ' + ym + ' 的日成交資訊（上櫃股請到櫃買中心核對，或該月沒有交易）。');
  } else {
    cache.forEach(function (r) {
      var o = official[r.date];
      if (!o) { daily.mismatch.push(r.date + ' 證交所沒有這一天'); return; }
      var diff = [];
      if (!same(r.volume, o.volume)) {
        diff.push('量 ' + r.volume + ' 股／官方 ' + o.volume + ' 股' +
                  (o.volume ? '（' + (r.volume / o.volume).toFixed(3) + ' 倍）' : '') +
                  // 剛好是官方股數 ÷ 1000 四捨五入：舊版程式存成張的資料
                  (o.volume >= 1000 && Math.abs(r.volume - Math.round(o.volume / 1000)) <= 1 ? '　← 舊資料存成張' : ''));
        if (o.volume >= 1000 && Math.abs(r.volume - Math.round(o.volume / 1000)) <= 1) { daily.storedAsLots = true; }
      }
      ['open', 'high', 'low', 'close'].forEach(function (k) {
        if (!same(r[k], o[k])) { diff.push(k + ' ' + r[k] + '／官方 ' + o[k]); }
      });
      if (diff.length) { daily.mismatch.push(r.date + '　' + diff.join('，')); } else { daily.matched++; }
    });
    var missing = days.filter(function (d) { return !cache.some(function (r) { return r.date === d; }); });
    out.push('一、日K快取 vs 證交所：官方 ' + days.length + ' 天，快取 ' + cache.length + ' 天，完全相同 ' + daily.matched + ' 天。');
    if (missing.length) { out.push('   快取缺 ' + missing.length + ' 天：' + missing.slice(0, 10).join('、')); }
    daily.mismatch.slice(0, 10).forEach(function (m) { out.push('   ✗ ' + m); });
    if (daily.mismatch.length > 10) { out.push('   …另有 ' + (daily.mismatch.length - 10) + ' 天不同'); }
    daily.missing = missing.length;
    if (daily.storedAsLots || missing.length) {
      out.push('   → 這一檔的日K快取' + (daily.storedAsLots ? '是舊版存成「張」的資料' : '') +
               (daily.storedAsLots && missing.length ? '，而且' : '') + (missing.length ? '缺交易日' : '') +
               '。四捨五入過的張乘回 1000 也不是正確股數，請執行 repairDailyKCache() 重新抓取，' +
               '完成後用 auditDailyKCache() 核對全部股票。');
    }
  }

  // 二、小時K（量＝張）
  var byDay = {};
  getHourlyCandles_(code).forEach(function (b) {
    var d = b.date.slice(0, 10);
    if (d.slice(0, 7) !== ym) { return; }
    (byDay[d] = byDay[d] || []).push(b);
  });
  var hourly = [];
  Object.keys(byDay).sort().forEach(function (d) {
    var bars = byDay[d], o = official[d];
    var lots = bars.reduce(function (s, b) { return s + (Number(b.volume) || 0); }, 0);
    var row = { date: d, bars: bars.length, lots: lots };
    if (o && o.volume) {
      row.officialLots = o.volume / 1000;
      row.ratio = lots / row.officialLots;
      row.ohlc = same(bars[0].open, o.open) && same(bars[bars.length - 1].close, o.close) &&
                 same(Math.max.apply(null, bars.map(function (b) { return b.high; })), o.high) &&
                 same(Math.min.apply(null, bars.map(function (b) { return b.low; })), o.low);
      row.ok = row.ratio >= 0.8 && row.ratio <= 1.005;
    }
    hourly.push(row);
  });
  if (!hourly.length) {
    out.push('二、小時K 在 ' + ym + ' 沒有這一檔的資料。');
  } else {
    out.push('二、小時K 加總 vs 證交所（張）：');
    hourly.forEach(function (h) {
      out.push('   ' + (h.ok === false ? '✗ ' : h.ok ? '✓ ' : '・ ') + h.date + '　' + h.bars + ' 根 ' + h.lots + ' 張' +
               (h.officialLots != null ? '／官方 ' + h.officialLots.toFixed(3) + ' 張（' + (h.ratio * 100).toFixed(1) + '%）' +
                 (h.ohlc ? '　開高低收一致' : '　開高低收不一致') : '　證交所無資料'));
    });
  }

  // 三、盤中即時報價（兩邊都是張）
  var quote = null;
  if (isTradingNow_() && hasFugle_()) {
    try {
      var mis = misQuote_(code), fug = fugleQuote_(code);
      if (mis && fug && mis.volume && fug.volume) {
        quote = { mis: mis.volume, fugle: fug.volume, ratio: fug.volume / mis.volume };
        out.push('三、即時報價：富果 ' + fug.volume + ' 張／MIS ' + mis.volume + ' 張（比值 ' + quote.ratio.toFixed(3) + '）');
      }
    } catch (e) { out.push('三、即時報價取不到：' + e); }
  } else {
    out.push('三、即時報價：不在盤中（或沒有富果金鑰），略過。');
  }

  Logger.log(out.join('\n'));
  return { code: code, month: ym, daily: daily, hourly: hourly, quote: quote };
}

/**
 * 已停用（2026/09/16 v41）。
 *
 * 這一支把「每日成交量中位數 ≥ 1,000,000」的股票判定成「股沒換算」，整欄除以 1000 四捨五入寫回。
 * 那時的假設是日K快取應該存「張」；v38 查證後統一改存「股」（與證交所成交股數同單位），
 * 而它正是日K快取裡出現兩種單位的原因：2385 群光一天四、五百萬股，中位數過門檻被改成 2242、7151 這種張數；
 * 1506 正道一天十萬股左右沒過門檻，維持股數——與 checkVolumeUnit 核對的結果完全吻合。
 * 四捨五入過的張數乘回 1000 也不是原本的股數，再跑一次只會把更多股票改壞，所以整支拿掉。
 * 要檢查請用 auditDailyKCache()，要修正請用 repairDailyKCache()（重新向富果抓取，量存股）。
 */
function repairDailyKVolume() {
  Logger.log('repairDailyKVolume 已停用：它會把日K快取的量改成四捨五入的張數，與現在「量存股」的規則相反。\n' +
             '檢查請執行 auditDailyKCache()，修正請執行 repairDailyKCache()。');
  return { disabled: true };
}


function fugleQuote_(code) {
  var memo = CACHE.get('quote_live_' + code);
  if (memo) { var saved = JSON.parse(memo); if (quoteFresh_(saved.stamp)) { return saved; } }
  var j = fugleFetch_('/intraday/quote/' + encodeURIComponent(code));
  if (String(j.symbol || code) !== String(code)) { return null; }
  // lastPrice/change 含試撮；價格與漲跌必須以同一筆真正成交計算。
  var last = Number(j.lastTrade && j.lastTrade.price) || Number(j.closePrice);
  if (!(last > 0)) { return null; }
  var prev = Number(j.previousClose) || null;
  var micros = Number(j.lastTrade && j.lastTrade.time) || Number(j.closeTime);
  var tradeStamp = micros > 0 ? Utilities.formatDate(new Date(micros / 1000), TZ, 'yyyy/MM/dd HH:mm:ss') : '';
  var checked = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss');
  var date = String(j.date || '').replace(/-/g, '/');
  if (date !== todayStr_()) { return null; }

  var q = {
    date: date, stamp: checked, tradeStamp: tradeStamp,
    time: qTime_(tradeStamp || checked), source: '富果成交報價',
    code: String(j.symbol || code),
    name: j.name || '',
    last: last,
    prevClose: prev,
    open: j.openPrice != null ? Number(j.openPrice) : null,
    high: j.highPrice != null ? Number(j.highPrice) : null,
    low: j.lowPrice != null ? Number(j.lowPrice) : null,
    volume: Number(j.total && j.total.tradeVolume) || 0,   // 即時報價：張
    change: prev ? Math.round((last - prev) * 100) / 100 : null,
    changePct: prev ? Math.round((last - prev) / prev * 10000) / 100 : null
  };
  try { CACHE.put('quote_live_' + code, JSON.stringify(q), 60); } catch (e) {}
  return q;
}

/** 當日 60 分 K。Fugle 只提供當日，歷史部分靠每天收盤落地累積。 */
function fugleHourly_(code) {
  var key='fugle_hour_live_'+code,hit=CACHE.get(key);
  if(hit){return JSON.parse(hit);}
  var j = fugleFetch_('/intraday/candles/' + encodeURIComponent(code) + '?timeframe=60');
  var bars=(j.data || []).map(function (r) {
    var d = new Date(r.date);
    return {
      date: Utilities.formatDate(d, TZ, 'yyyy/MM/dd') + ' ' + Utilities.formatDate(d, TZ, 'HH:mm'),
      open: Number(r.open), high: Number(r.high), low: Number(r.low),
      close: Number(r.close), volume: Number(r.volume) || 0   // 日內 60 分 K：張
    };
  }).filter(function (r) { return r.close; });
  CACHE.put(key,JSON.stringify(bars),60);
  return bars;
}

/**
 * 歷史日K。一次拿一檔的完整區間，上市上櫃通吃。
 *
 * 這條路是上櫃股票日K 的唯一來源。櫃買的 st43_result.php 掛在舊站
 * wwwov.tpex.org.tw，該站 2025/05/31 已停用，現在回傳 404。
 * 證交所的 STOCK_DAY 只有上市，而且要逐月抓。
 *
 * 區間：呼叫端要多長就給多長，一段拿不完就分段拿再合併。
 *
 * 先前的做法是「從長的開始試，撞牆就砍短，拿到多少算多少」，並把成功的
 * 天數記成這把金鑰的上限。問題出在「成功」不代表「那是上限」：
 * 診斷工具用 46 天的區間測一次（2026/06/01～07/17），成功了就被記成
 * 「這把金鑰只能回溯 46 天」，之後補日K要一整年，也只拿最近 46 天——
 * 9/11 往回 46 天剛好是 7/27，日K快取就從那天開始，
 * 績效歷史只剩 35 個交易日，更早的進場價也全都查不到K線。
 *
 * 現在的規則：
 *   1. 只有真的被 400 拒絕、而且改用較短的區間成功，才記成上限；
 *      呼叫端自己要的區間短，不算上限。
 *   2. 記住的上限只用來決定「每一段多長」，不再截短總區間——
 *      區間比上限長就分成好幾段依序拿，拿完合併、去重、排序。
 *   3. 上限 30 天後失效重新探測，方案升級後會自己恢復。
 *   舊的 FUGLE_MAX_DAYS 屬性不再採信（它可能就是被污染的那個值），
 *   留著不刪，auditDailyKCoverage() 會印出來供核對。
 */
var FUGLE_RANGE_PROP = 'FUGLE_MAX_DAYS';          // 舊版，已不採信
var FUGLE_LIMIT_PROP = 'FUGLE_RANGE_LIMIT';       // 新版：{ days, rejected, at }
var FUGLE_LIMIT_TTL_MS = 30 * 86400000;
var FUGLE_WINDOW_LADDER = [365, 270, 180, 90, 45];
var FUGLE_MIN_WINDOW = 45;
var FUGLE_MAX_SPAN_DAYS = 800;                    // 防呆：區間異常長時不要打出上百次請求

/** 記住「被拒絕過、改用較短區間才成功」的單段上限。rejected 是被拒的那個長度。 */
function rememberFugleRange_(days, rejected) {
  PropertiesService.getScriptProperties().setProperty(FUGLE_LIMIT_PROP,
    JSON.stringify({ days: days, rejected: rejected, at: Date.now() }));
}
function knownFugleRange_() {
  try {
    var v = JSON.parse(PropertiesService.getScriptProperties().getProperty(FUGLE_LIMIT_PROP) || 'null');
    /* 被拒的是 365 天以上的區間，不算方案上限（2026/09/16 v40）。
       富果文件：單次查詢區間須「小於 1 年」，恰好 1 年（例如 2025-09-16～2026-09-16）一律回 400。
       補日K原本就要剛好一年，第一檔被拒後記成「每段 270 天」，之後每一檔都拆兩次請求、多花一倍時間。
       現在補日K的區間改成一年少一天（dailyKRange_），這種紀錄不再當成上限。 */
    if (v && Number(v.rejected) >= 365) { return 0; }
    if (v && Number(v.days) > 0 && Date.now() - Number(v.at) < FUGLE_LIMIT_TTL_MS) { return Number(v.days); }
  } catch (e) { /* 壞掉的值當作沒有 */ }
  return 0;
}

/** yyyy-MM-dd 加減天數。 */
function fugleShiftDay_(ymd, delta) {
  var d = new Date(ymd);
  d.setDate(d.getDate() + delta);
  return Utilities.formatDate(d, TZ, 'yyyy-MM-dd');
}

/* 富果歷史行情的抓取節奏（2026/09/16 v40）。

   富果歷史行情每分鐘 60 次（基本、開發者、進階三種方案都一樣）。先前每抓一檔固定睡 1.1 秒、
   分段之間再睡 1.1 秒，請求本身的時間另計，實際每分鐘只有三十多次。
   改成「相鄰兩次請求的開始時間至少相隔 60/55 秒」：請求本身花掉的時間算在間隔裡，不重複等，
   上限 55 次留 5 次給時鐘誤差。上一次請求的時間記在 CacheService，
   續跑觸發器、網站／GitHub 的分批請求這些「下一次執行」也照同一個節奏，不會接力時瞬間超量。 */
var FUGLE_HIST_PER_MIN_ = 55;
var FUGLE_HIST_GAP_MS_ = Math.ceil(60000 / FUGLE_HIST_PER_MIN_);
var _fugleHistLastAt = 0;

function fugleHistPace_() {
  if(typeof marketPace_==='function'){marketPace_('history', marketGap_('history',55));return;}
  var cache = null, last = _fugleHistLastAt;
  try {
    cache = CacheService.getScriptCache();
    last = Math.max(last, Number(cache.get('fugle_hist_last') || 0));
  } catch (e) { cache = null; }
  var wait = Math.min(FUGLE_HIST_GAP_MS_, last + FUGLE_HIST_GAP_MS_ - Date.now());
  if (wait > 0) { Utilities.sleep(wait); }
  _fugleHistLastAt = Date.now();
  try { if (cache) { cache.put('fugle_hist_last', String(_fugleHistLastAt), 120); } } catch (e) { /* 記不進去只影響跨執行的節奏 */ }
}

function fugleHistorical_(code, from, to) {
  var span = Math.round((new Date(to) - new Date(from)) / 86400000);
  if (!(span > 0)) { fugleHistPace_(); return fugleHistoricalOnce_(code, from, to); }
  if (span > FUGLE_MAX_SPAN_DAYS) {
    from = fugleShiftDay_(to, -FUGLE_MAX_SPAN_DAYS);
    span = FUGLE_MAX_SPAN_DAYS;
  }

  var cap = knownFugleRange_();
  var win = cap ? Math.min(cap, span) : span;
  var byDate = {};
  var end = to, rejected = 0, requests = 0;

  while (end >= from) {
    if (++requests > 40) { throw new Error('分段次數異常（' + code + '），停止以免耗盡額度'); }
    var start = fugleShiftDay_(end, -win);
    if (start < from) { start = from; }
    var rows;
    try {
      fugleHistPace_();   // 每一次請求（含分段、被拒後縮短重試）都照每分鐘 55 次的節奏
      rows = fugleHistoricalOnce_(code, start, end);
    } catch (e) {
      // 只有 400 值得縮短再試；401 403 404 429 縮了也沒用，原樣拋出。
      if (e && e.httpCode === 400 && win > FUGLE_MIN_WINDOW) {
        rejected = win;
        win = FUGLE_WINDOW_LADDER.filter(function (d) { return d < rejected; })[0] || FUGLE_MIN_WINDOW;
        continue;
      }
      // 較舊的一段被拒（例如上市以前），保留已經拿到的較新資料。
      if (e && e.httpCode === 400 && Object.keys(byDate).length) { break; }
      throw e;
    }

    if (rejected) {
      if (!cap || win < cap) {
        rememberFugleRange_(win, rejected);
        cap = win;
        Logger.log('    Fugle 單段 ' + rejected + ' 天被拒、' + win + ' 天可以；之後改成每段 ' +
                   win + ' 天分段取回（總區間不變）。');
      }
      rejected = 0;
    }
    rows.forEach(function (r) { byDate[r.date] = r; });
    if (start <= from) { break; }
    end = fugleShiftDay_(start, -1);
  }

  var out = Object.keys(byDate).sort().map(function (k) { return byDate[k]; });
  if (!out.length) { throw new Error('回傳 0 根'); }
  return out;
}

function fugleHistoricalOnce_(code, from, to) {
  var j = fugleFetch_('/historical/candles/' + encodeURIComponent(code) +
    '?from=' + from + '&to=' + to + '&fields=open,high,low,close,volume');

  return (j.data || []).map(function (r) {
    return {
      date: String(r.date).replace(/-/g, '/'),
      open: Number(r.open), high: Number(r.high),
      low: Number(r.low), close: Number(r.close),
      volume: Number(r.volume) || 0   // 歷史日K：股（見 sharesToLots_ 上方說明）
    };
  }).filter(function (r) {
    return r.close && r.date;
  }).sort(function (a, b) {
    return a.date < b.date ? -1 : 1;
  });
}

/* ------------------------------------------------------------------ *
 * 即時快取
 * 盤中每 5 分鐘更新一次寫進試算表。前端直接讀，不對外請求。
 * ------------------------------------------------------------------ */

var QUOTE_CURSOR_KEY_ = 'QUOTE_CURSOR';
/* 60 秒（v87，原本 3 分鐘）。2026/09/30 盤中每一棒平均跑 2.5 分鐘，全天觸發器累計約 217 分鐘（這一支就佔 172 分鐘），
   五分鐘總排程單次最長 326 秒、逼近六分鐘上限。沒輪到的代號下一棒由游標接著抓，讀取端遇到過期報價會即時補抓。 */
var QUOTE_JOB_BUDGET_MS_ = 30000; // v107：45→30 秒。10/02 本支一天 37.9 分，多數耗在 MIS 斷線後的逐檔備援；持有與查看中仍優先。
var MIS_DOWN_KEY_ = 'mis_down_v107';   // MIS 連線層失敗（Address unavailable、逾時）後 10 分鐘內不再打 MIS

function isTradingNow_() {
  var now = new Date();
  // 星期與時分都用台北時間。先前星期取自 now.getDay()（走專案時區）、
  // 時分取自 Utilities.formatDate(TZ)，兩者不一致時週一清晨與週五深夜會判錯。
  // 休市日先前完全沒看，那幾天會白打七十幾檔對外請求。
  if (typeof whyClosed_ === 'function' && whyClosed_(now)) { return false; }
  var hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
  return hhmm >= 900 && hhmm <= 1340;
}


/* ------------------------------------------------------------------ *
 * 快取新鮮度
 *
 * 即時快取的「更新時間」長期以來只被寫入與顯示，沒有任何一處拿它做判斷。
 * 於是 refreshQuoteCacheJob 一旦停擺（觸發器被停用、連續逾時、來源全掛），
 * getQuotesFor 仍然把最後一次成功寫入的那一列當成現價回給前端，
 * 網站看起來一切正常——2026/09/21 盤中的個股面板顯示 09-18 13:40:45
 * 就是這樣來的：週五收盤前的最後一棒寫進去之後，再也沒有寫過。
 *
 * 時間一律用 Utilities.formatDate(TZ) 產生的字串去比，與寫入端同一個基準，
 * 不做 new Date(字串) 的解析，免得受專案時區設定影響。
 * ------------------------------------------------------------------ */

var QUOTE_STALE_MIN_ = 5;         // v93：盤中五分鐘重新核對成交報價

/** 本輪更新結果與目前表上新鮮度分開呈現，不把排程啟動當成報價成功。 */
function quoteCacheStatus() {
  var pr = PropertiesService.getScriptProperties(), last = {};
  try { last = JSON.parse(pr.getProperty('QUOTE_JOB_STATUS') || '{}'); } catch (e) {}
  var quotes = getQuoteCache(), fresh = 0, old = 0, codes = trackedCodes_();
  codes.forEach(function (c) { if (!quotes[c]) { return; } if (quoteFresh_(quotes[c].stamp)) { fresh++; } else { old++; } });
  var day = {}, close = {};
  try { day = JSON.parse(pr.getProperty('QUOTE_DAY_STATS') || '{}'); } catch (e) {}
  try { close = JSON.parse(pr.getProperty(QUOTE_CLOSE_PROP_) || '{}'); } catch (e) {}
  var result = { job: last, fresh: fresh, old: old, total: fresh + old, eligible: codes.length, trading: isTradingNow_(), day: day, close: close };
  Logger.log(JSON.stringify(result));
  return result;
}

function rememberViewedQuote_(code) {
  // 單檔記錄各用一個快取鍵，避免訪客同時寫同一份列表而互相蓋過。
  try { CACHE.put('quote_viewed_' + code, String(Date.now()), 1800); } catch (e) {}
}

function quoteStampParts_(v) {
  if (v == null || v === '') { return null; }

  var d = null;
  if (Object.prototype.toString.call(v) === '[object Date]') {
    d = v;
  } else if (/^\d{4}-\d{2}-\d{2}T/.test(String(v).trim())) {
    var parsed = new Date(String(v).trim());
    if (!isNaN(parsed.getTime())) { d = parsed; }
  }
  if (d) {
    return {
      date: Utilities.formatDate(d, TZ, 'yyyy/MM/dd'),
      sec: Number(Utilities.formatDate(d, TZ, 'HH')) * 3600 +
           Number(Utilities.formatDate(d, TZ, 'mm')) * 60 +
           Number(Utilities.formatDate(d, TZ, 'ss'))
    };
  }

  var m = String(v).trim().match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) { return null; }
  return {
    date: m[1] + '/' + pad2_(m[2]) + '/' + pad2_(m[3]),
    sec: Number(m[4]) * 3600 + Number(m[5]) * 60 + Number(m[6] || 0)
  };
}

/** 這一列多久沒更新了（分鐘）。跨日與解析不出來的一律回很大的值。 */
function quoteAgeMin_(stamp) {
  var t = quoteStampParts_(stamp);
  if (!t) { return 1e9; }

  var now = new Date();
  // 跨日一定算舊：隔天的盤前盤後都不該把昨天的價當成現價。
  if (t.date !== Utilities.formatDate(now, TZ, 'yyyy/MM/dd')) { return 1e9; }

  var nowSec = Number(Utilities.formatDate(now, TZ, 'HH')) * 3600 +
               Number(Utilities.formatDate(now, TZ, 'mm')) * 60 +
               Number(Utilities.formatDate(now, TZ, 'ss'));
  return Math.max(0, (nowSec - t.sec) / 60);
}

/* ------------------------------------------------------------------ *
 * 收盤價（v134，2026/10/06 管理者回報：收盤後個股面板寫的不是收盤價）
 *
 * 先前的假設是「盤後只要是今天的報價就是收盤價」。那只有在最後一棒剛好在 13:30 之後輪到這一檔時才成立：
 * 每一棒只輪得到一部分代號，2026/10/06 收盤後 240 檔裡有 125 檔停在盤中某一刻
 * （聯電 11:59:40、台積電 13:29:21 的 2580，收盤是 2585），其中 104 檔的價格與當天日K收盤不同，
 * 而且整個晚上都被當成「新鮮」不會再更新。
 *
 * 收盤價在 13:30:00 的集合競價成交。所以：
 *   今天 13:30:00（含）之後取得的成交價才是收盤價；時間停在那之前的只是盤中某一刻的價。
 *   收盤後由 settleClosingQuotesTick_ 把即時快取整批換成收盤價（來源：當天日K，或證交所 MIS 的收盤後報價）。
 *   還沒換到的那幾檔，讀取端自己用當天日K或現場查一次，不把盤中價當收盤價回出去。
 * ------------------------------------------------------------------ */
var MARKET_CLOSE_SEC_ = 13 * 3600 + 30 * 60;
var QUOTE_CLOSE_PROP_ = 'QUOTE_CLOSE_SETTLE';

/** 這個時間戳是不是當天收盤之後取得的。 */
function quoteIsClose_(stamp) {
  var t = quoteStampParts_(stamp);
  return !!t && t.sec >= MARKET_CLOSE_SEC_;
}

/** 今天是交易日，而且已經過了收盤集合競價（13:30）。 */
function marketClosedToday_() {
  var now = new Date();
  if (typeof whyClosed_ === 'function' && whyClosed_(now)) { return false; }
  return Number(Utilities.formatDate(now, TZ, 'HHmm')) >= 1330;
}

/** 日K最後一根當成收盤報價。回傳的 date 是那一根的日期，呼叫端要自己確認是不是今天。 */
function closeQuoteFromK_(code, rows) {
  var k = rows || getCachedDailyK(code);
  if (!k || !k.length) { return null; }
  var last = k[k.length - 1], prev = k.length > 1 ? k[k.length - 2] : null;
  if (!(Number(last.close) > 0)) { return null; }
  var chg = prev && prev.close ? last.close - prev.close : null;
  return {
    name: '', last: last.close, prevClose: prev ? prev.close : null,
    change: chg != null ? Math.round(chg * 100) / 100 : null,
    changePct: chg != null ? Math.round(chg / prev.close * 10000) / 100 : null,
    volume: sharesToLots_(last.volume),
    open: last.open || null, high: last.high || null, low: last.low || null,
    date: last.date, time: qTime_(last.date + ' 收盤'), stamp: last.date + ' 13:30:00', source: '日K收盤'
  };
}

/* 這一列還能不能當現價用。
   盤中限 QUOTE_STALE_MIN_ 分鐘。收盤後要是今天、而且是 13:30 之後取得的才算——停在盤中某一刻的不是收盤價。
   還沒開盤（或 13:30 以前的非盤中時段）沿用「今天的就算數」。 */
function quoteFresh_(stamp) {
  if (isTradingNow_()) { return quoteAgeMin_(stamp) <= QUOTE_STALE_MIN_; }
  if (quoteAgeMin_(stamp) > 1440) { return false; }
  return !marketClosedToday_() || quoteIsClose_(stamp);
}

/* 收盤後把即時快取換成收盤價。每五分鐘排程呼叫；不在時段內、今天已經做完、或距上次嘗試太近時立刻返回。
   來源依序：一、日K快取裡今天那一根（官方收盤行情約 14:40 之後落地，一次讀全部代號的快取，不讀整張表）；
             二、證交所 MIS 收盤後的批次報價（13:30 之後回的就是收盤價與全日量，一次 50 檔）。
   寫進表的時間一律記成當天 13:30:00，讀取端據此知道這是收盤價。 */
function settleClosingQuotesTick_(force) {
  if (!marketClosedToday_()) { return 'not-closed'; }
  var hhmm = Number(Utilities.formatDate(new Date(), TZ, 'HHmm'));
  if (hhmm < 1331 || hhmm > 2230) { return 'window'; }
  var pr = PropertiesService.getScriptProperties(), today = todayStr_(), st = {};
  try { st = JSON.parse(pr.getProperty(QUOTE_CLOSE_PROP_) || '{}'); } catch (e) { st = {}; }
  if (st.date !== today) { st = { date: today, done: false, tries: 0, misTries: 0, at: 0 }; }
  /* 批次來源的連線自我檢查：每個交易日收盤後做一次（失敗最多再試兩次），結果記在狀態裡。
     盤中才發現批次來源不通就太晚了——2026/10/06 整天不通，是事後從報價的時間戳倒推才知道的。 */
  if (!st.relayTest || (st.relayTest.ok === false && (st.relayTries || 0) < 3)) {
    st.relayTries = (st.relayTries || 0) + 1;
    var checkedAt = Utilities.formatDate(new Date(), TZ, 'HH:mm:ss');
    try {
      MIS_LAST_VIA_ = '';
      try { pr.deleteProperty(MIS_DIRECT_DOWN_PROP_); } catch (ignore) {}      // 每天在這裡重試一次直連
      var probe = misRows_(['tse_2330.tw', 'otc_2330.tw']);
      st.relayTest = { ok: probe.some(function (m) { return m && String(m.c) === '2330'; }), via: MIS_LAST_VIA_, rows: probe.length, at: checkedAt };
    } catch (e) {
      st.relayTest = { ok: false, via: '', at: checkedAt, error: String(e && e.message || e).replace(/https?:\/\/\S+/g, '').slice(0, 80) };
    }
    pr.setProperty(QUOTE_CLOSE_PROP_, JSON.stringify(st));
    Logger.log('批次報價連線檢查：' + JSON.stringify(st.relayTest));
  }
  if (st.done && !force) { return 'done'; }
  // 日K落地前（約 14:45 前）每五分鐘試一次 MIS；之後日K是主要來源，十五分鐘看一次；一天最多 40 次。
  var gapMin = hhmm < 1445 ? 4 : 14;
  if (!force && (st.tries >= 40 || (st.at && Date.now() - st.at < gapMin * 60000))) { return 'wait'; }

  var codes = trackedCodes_();
  if (!codes.length) { return 'empty'; }
  var tracked = {};
  codes.forEach(function (c) { tracked[c] = 1; });
  var sh = getSheet_('即時快取'), rows = sh.getDataRange().getValues().slice(1), have = {};
  rows.forEach(function (r) { var c = String(r[0] || '').trim(); if (c) { have[c] = r; } });
  var isFinal = function (r) {
    if (!r) { return false; }
    var t = quoteStampParts_(r[7]);
    return !!t && t.date === today && t.sec >= MARKET_CLOSE_SEC_ && Number(r[2]) > 0;
  };
  var pending = codes.filter(function (c) { return !isFinal(have[c]); });
  st.tries = (st.tries || 0) + 1; st.at = Date.now();
  if (!pending.length) {
    st.done = true; st.pending = 0;
    pr.setProperty(QUOTE_CLOSE_PROP_, JSON.stringify(st));
    return 'done';
  }

  var map = {}, fresh = {}, fromK = 0, fromMis = 0, stamp = today + ' 13:30:00';
  try { map = loadCodeMap_().byCode; } catch (e) { map = {}; }
  var put = function (c, q) {
    fresh[c] = [c, q.name || (have[c] && have[c][1]) || (map[c] ? map[c].name : ''), q.last, q.prevClose || '',
      q.change != null ? q.change : '', q.changePct != null ? q.changePct : '', Math.round((Number(q.volume) || 0) * 1000) / 1000,
      stamp, q.open || '', q.high || '', q.low || '', today];
  };
  // 一、日K快取（一次 getAll，不逐檔讀、不讀整張表）。
  var cached = null;
  try {
    cached = CACHE.getAll(pending.map(function (c) { return 'dk2_' + c; }));
    pending.forEach(function (c) {
      var text = cached['dk2_' + c];
      if (!text) { return; }
      var q = null;
      try { q = closeQuoteFromK_(c, kcDecode_(text)); } catch (e) { q = null; }
      if (q && q.date === today) { put(c, q); fromK++; }
    });
  } catch (e) { Logger.log('收盤價：日K快取讀取未成功：' + e); }
  // 日K快取裡沒有的代號（快取過期，不是日K缺今天）：15:00 之後整張日K讀一次補上，一天只做一次。
  var uncached = pending.filter(function (c) { return !fresh[c] && !(cached && cached['dk2_' + c]); });
  if (uncached.length && !st.fullRead && hhmm >= 1500) {
    st.fullRead = true;
    uncached.forEach(function (c) {
      var q = null;
      try { q = closeQuoteFromK_(c); } catch (e) { q = null; }
      if (q && q.date === today) { put(c, q); fromK++; }
    });
  }
  // 二、MIS 收盤後報價。只在日K還沒落地的時段用，連線層失敗就記下、十分鐘內不再打。
  var rest = pending.filter(function (c) { return !fresh[c]; }), misNote = '';
  var misDown = false;
  try { misDown = !!CACHE.get(MIS_DOWN_KEY_); } catch (e) {}
  if (rest.length && !misDown && (st.misTries || 0) < 8) {
    st.misTries = (st.misTries || 0) + 1;
    var deadline = Date.now() + 20000;
    for (var b = 0; b < rest.length && Date.now() < deadline; b += 50) {
      try {
        var qs = misBatchQuotes_(rest.slice(b, b + 50));
        Object.keys(qs).forEach(function (c) { if (tracked[c] && qs[c].date === today && qs[c].last > 0) { put(c, qs[c]); fromMis++; } });
      } catch (e) {
        misNote = String(e && e.message || e).replace(/https?:\/\/\S+/g, '').slice(0, 60);
        if (!(e && e.transientBusy)) { try { CACHE.put(MIS_DOWN_KEY_, '1', 600); } catch (ignore) {} }
        break;
      }
    }
  }

  // 三、MIS 沒有回資料時，持有中的那幾檔先用富果逐檔取（最多 16 檔、15 秒）：持股追蹤的現價與報酬靠它。
  var stillOpen = pending.filter(function (c) { return !fresh[c]; }), fromFugle = 0;
  if (stillOpen.length && !fromMis && hasFugle_()) {
    var heldNow = {};
    try { readSheetObjects_('持股追蹤').forEach(function (r) { if (String(r['狀態']) === '持有中') { heldNow[String(r['代號']).trim()] = true; } }); } catch (e) {}
    var fugleDeadline = Date.now() + 15000;
    stillOpen.filter(function (c) { return heldNow[c]; }).slice(0, 16).forEach(function (c) {
      if (Date.now() > fugleDeadline) { return; }
      try { var live = fugleQuote_(c); if (live && live.date === today && live.last > 0) { put(c, live); fromFugle++; } }
      catch (e) { /* 這一檔留給下一次或日K */ }
    });
  }

  var settled = Object.keys(fresh);
  if (settled.length) {
    withLock_(function () {
      var keep = [];
      sh.getDataRange().getValues().slice(1).forEach(function (r) {
        var c = String(r[0] || '').trim();
        if (c && !fresh[c] && tracked[c]) { keep.push(r.slice(0, 12)); }
      });
      var out = settled.map(function (c) { return fresh[c]; }).concat(keep), oldLast = sh.getLastRow();
      sh.getRange(1, 1, out.length + 1, 12).setValues([
        ['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']
      ].concat(out));
      if (oldLast > out.length + 1) {
        try { sh.getRange(out.length + 2, 1, oldLast - out.length - 1, 12).clearContent(); } catch (e) {}
      }
    });
    try { CACHE.removeAll(settled.map(function (c) { return 'quote_live_' + c; })); } catch (e) {}
    CACHE.remove('qcache'); CACHE.remove('tracker'); CACHE.remove(DASH_CACHE_KEY_);
  }
  st.pending = pending.length - settled.length;
  st.done = st.pending === 0;
  // 剩下的少數幾檔（興櫃、暫停交易）日K沒有今天、批次來源也沒有：15:00 之後試過三次就記下來收工，
  // 讀取端會照實顯示它最後一個交易日的收盤（2026/10/06 的 6597）。
  if (!st.done && st.pending <= 5 && st.tries >= 3 && hhmm >= 1500) {
    st.done = true;
    st.noData = pending.filter(function (c) { return !fresh[c]; });
  }
  st.last = { fromK: fromK, fromMis: fromMis, fromFugle: fromFugle, misNote: misNote };
  pr.setProperty(QUOTE_CLOSE_PROP_, JSON.stringify(st));
  Logger.log('收盤價：換成收盤價 ' + settled.length + ' 檔（日K ' + fromK + '、MIS ' + fromMis + '、富果 ' + fromFugle + '），尚餘 ' + st.pending + ' 檔' + (misNote ? '；MIS：' + misNote : ''));
  return { settled: settled.length, fromK: fromK, fromMis: fromMis, fromFugle: fromFugle, pending: st.pending };
}

function refreshQuoteCacheJob() {
  if (!isTradingNow_()) { return; }

  var codes = trackedCodes_();
  if (!codes.length) { return; }

  var map = loadCodeMap_().byCode;
  // 帶秒。盤中每幾分鐘更新一次，只到分的話連兩次抓的時間看起來一樣，
  // 分不出「這個價剛更新」還是「已經停在這裡好幾分鐘了」。
  var stamp = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss');

  /* 時間預算與接力游標。

     先前是固定從第一檔開始、七十幾檔全部跑完才一次寫回。來源正常時沒問題，
     但 Fugle 失效那天每一檔都退到 MIS，而 MIS 有 5 秒 5 次的節流、
     misQuote_ 猜錯市場還會再打一次——整輪逼近 Apps Script 的六分鐘上限。
     被切斷時這一棒抓到的全部丟掉，下一棒又從第一檔開始，於是永遠寫不進去，
     表上停在最後一次順利跑完的時間，而且不會有任何錯誤訊息。

     現在：抓到多少寫多少，時間到就記下停在哪一檔，下一棒接著抓。 */
  var pr = PropertiesService.getScriptProperties();
  var cursor = Number(pr.getProperty(QUOTE_CURSOR_KEY_) || 0);
  if (!(cursor >= 0) || cursor >= codes.length) { cursor = 0; }

  var deadline = Date.now() + QUOTE_JOB_BUDGET_MS_;
  var fresh = {};
  var taken = 0, i, batchQuotes = {}, fallbackTaken = 0, lastFallback = -1, errors = [], fugleUnavailable = false;
  // v88：追蹤宇宙每 50 檔一起取，避免五分鐘排程全天耗在逐檔等待。
  // 缺成交價的標的稍後走富果備援；每棒優先最多十六檔，再留四檔給背景游標。
  var misDown = false;
  try { misDown = !!CACHE.get(MIS_DOWN_KEY_); } catch (e) {}
  if (misDown) { errors.push('MIS 連線中斷，10 分鐘內改用備援'); }
  /* 批次從上一棒停下的那一批接著打（v134）：先前每一棒都從第一批開始，時間不夠時後面幾批永遠輪不到。
     第一批一檔都沒有回（HTTP 非 200、或回空陣列）就不再試其餘幾批，並記 10 分鐘：
     那幾次請求不會有資料，只會吃掉留給逐檔備援的時間。 */
  // 每一輪從乾淨的狀態開始：上一次記下的報價重新讀、追問名單與計數歸零。
  MIS_PREVIOUS_ = null; MIS_NO_TRADE_ = []; MIS_CARRIED_ = 0; MIS_ROWS_TODAY_ = 0;
  var misStarted = Date.now(), misState = misDown ? 'down' : 'skip';
  var batchCount = Math.ceil(codes.length / 50), batchStart = Number(pr.getProperty('QUOTE_BATCH_START') || 0) % Math.max(1, batchCount), batchDone = 0;
  for (var bn = 0; !misDown && bn < batchCount && Date.now() < deadline - 10000; bn++) {
    var b = ((batchStart + bn) % batchCount) * 50;
    try {
      var qs = misBatchQuotes_(codes.slice(b, b + 50)), got = 0;
      Object.keys(qs).forEach(function (c) { if (qs[c].date === todayStr_()) { batchQuotes[c] = qs[c]; got++; } });
      batchDone++;
      // 來源有回今天的列、只是這一批剛好那一刻都沒有成交價：來源是通的，不能記成中斷（v139）。
      if (!got && !Object.keys(batchQuotes).length && !MIS_ROWS_TODAY_) {
        misState = 'empty'; errors.push('MIS 沒有回任何成交報價，10 分鐘內改用備援');
        try { CACHE.put(MIS_DOWN_KEY_, '1', 600); } catch (ignore) {}
        break;
      }
      misState = 'ok';
    } catch (e) {
      misState = e && e.transientBusy ? 'busy' : 'error';
      errors.push('MIS：' + String(e && e.message || e).replace(/https?:\/\/\S+/g, '').slice(0, 60));
      Logger.log('即時快取批次來源暫時失敗：' + e);
      // 同一把寫入鎖忙碌時，連續五批各等二十秒只會延誤寄送與重算。
      if (e && e.transientBusy) { break; }
      // v107：連線層失敗（非 HTTP 狀態）其餘批次也會一樣失敗，這一輪停打並記 10 分鐘。
      try { CACHE.put(MIS_DOWN_KEY_, '1', 600); } catch (ignore) {}
      break;
    }
  }
  if (batchDone && batchDone < batchCount) { pr.setProperty('QUOTE_BATCH_START', String((batchStart + batchDone) % batchCount)); }
  /* 這一刻沒有成交價的再問幾輪（v139）。每一輪只問還沒拿到的；用同一個時間預算（留 10 秒給逐檔備援與寫入），最多三輪。 */
  var misRounds = 0, misAsked = MIS_NO_TRADE_.length;
  while (misState === 'ok' && misRounds < 3 && Date.now() < deadline - 10000) {
    var again = MIS_NO_TRADE_.filter(function (c) { return !batchQuotes[c]; });
    MIS_NO_TRADE_ = [];
    if (!again.length) { break; }
    var stop = false;
    for (var rb = 0; rb < again.length && !stop && Date.now() < deadline - 10000; rb += 50) {
      try {
        var rq = misBatchQuotes_(again.slice(rb, rb + 50));
        Object.keys(rq).forEach(function (c) { if (rq[c].date === todayStr_()) { batchQuotes[c] = rq[c]; } });
      } catch (e) { stop = true; }                       // 限流忙碌或連線失敗：不再追問，交給備援與下一輪
    }
    misRounds++;
    if (stop) { break; }
  }
  if (misAsked) { Logger.log('批次報價：這一刻沒有成交價的 ' + misAsked + ' 檔，追問 ' + misRounds + ' 輪；累計量未變沿用 ' + MIS_CARRIED_ + ' 檔'); }
  var misMs = Date.now() - misStarted;

  // v93：會員持有及最近半小時查看的股票優先；另留四檔輪替背景標的。
  var priority = {};
  try { readSheetObjects_('持股追蹤').forEach(function (r) {
    if (String(r['狀態']) === '持有中') { priority[String(r['代號']).trim()] = true; }
  }); } catch (e) {}
  var viewedCache = {};
  try { viewedCache = CACHE.getAll(codes.map(function (c) { return 'quote_viewed_' + c; })); } catch (e) {}
  var held = codes.filter(function (c) { return priority[c]; });
  var viewed = codes.filter(function (c) { return viewedCache['quote_viewed_' + c] && !priority[c]; });
  viewed.sort(function (a, b) { return Number(viewedCache['quote_viewed_' + b]) - Number(viewedCache['quote_viewed_' + a]); });
  /* 逐檔備援的順序（v134）。批次來源沒有資料時，一棒 30 秒大約只夠逐檔取十幾檔。
     先前是「持有＋最近查看」最多 16 檔排最前面、其餘只留 4 檔輪替：被查看的股票一多，16 檔就把時間用完，
     輪替的 4 檔一檔都輪不到——2026/10/06 12:19 之後到收盤，持有以外的股票沒有任何一檔被更新。
     現在：持有 10 檔 → 輪替 3 檔 → 其餘持有 → 最近查看 4 檔 → 繼續輪替，時間到為止
     （實測一棒約 17 檔：14 檔持有時剛好是全部持有加 3 檔輪替）。
     輪替的名額排在第二順位，不會被擠掉；正在被查看的那一檔本來就由查看的人那一次請求現場更新。
     持有超過 10 檔時，每一棒輪流讓不同的幾檔排前面。 */
  var QUOTE_HELD_FIRST_ = 10, QUOTE_ROTATE_FIRST_ = 3, QUOTE_VIEWED_SLOTS_ = 4, QUOTE_FALLBACK_MAX_ = 30;
  if (held.length > QUOTE_HELD_FIRST_) {
    var turn = Math.floor(Date.now() / 300000) % held.length;
    held = held.slice(turn).concat(held.slice(0, turn));
  }
  var rotation = [], rotating = {}, placed = {};
  held.forEach(function (c) { placed[c] = 1; });
  for (i = 0; i < codes.length; i++) {
    var rotated = codes[(cursor + i) % codes.length];
    if (!placed[rotated]) { rotation.push(rotated); rotating[rotated] = 1; placed[rotated] = 1; }
  }
  var viewedFirst = viewed.slice(0, QUOTE_VIEWED_SLOTS_).filter(function (c) { return rotation.indexOf(c) >= QUOTE_ROTATE_FIRST_; });
  var ordered = held.slice(0, QUOTE_HELD_FIRST_)
    .concat(rotation.slice(0, QUOTE_ROTATE_FIRST_))
    .concat(held.slice(QUOTE_HELD_FIRST_))
    .concat(viewedFirst)
    .concat(rotation.slice(QUOTE_ROTATE_FIRST_).filter(function (c) { return viewedFirst.indexOf(c) < 0; }));
  var heldSet = {}, doneHeld = 0, doneRotating = 0, doneViewed = 0;
  held.forEach(function (c) { heldSet[c] = 1; });
  for (i = 0; i < ordered.length; i++) {
    var code = ordered[i];

    var q = batchQuotes[code] || null;
    /* 時間用完時只停逐檔備援，批次已經拿到的照樣寫入（v139）。
       先前是整個迴圈中斷：批次明明拿到一百多檔的成交價，排在逐檔備援後面的那些卻沒有被寫進去，
       一輪只更新十幾檔——2026/10/07 盤中 240 檔有 165 檔超過十分鐘沒更新，主要就是這個原因。 */
    if (!q && Date.now() > deadline) { continue; }
    if (!q && (fallbackTaken >= QUOTE_FALLBACK_MAX_ || fugleUnavailable)) { continue; }
    // 游標記的是「輪替順序裡最後一檔試過的」，被提前的查看中股票不動游標，否則會跳過中間沒輪到的。
    if (!q) { fallbackTaken++; if (rotating[code] && viewedFirst.indexOf(code) < 0) { lastFallback = codes.indexOf(code); } }
    if (!q && hasFugle_()) {
      try { q = fugleQuote_(code); } catch (e) {
        q = null; errors.push(code + '：' + (e.httpCode ? 'HTTP ' + e.httpCode : '來源暫時讀不到'));
        if ([401, 403, 429].indexOf(e.httpCode) >= 0) { fugleUnavailable = true; }
      }
    }
    // 批次 MIS 已經查過，缺成交價時再打同一個 MIS 不會產生資料。
    if (q && q.date && q.date !== todayStr_()) { continue; }
    if (!q || q.last == null) { continue; }

    var chg = q.change;
    var pct = q.changePct;
    if (chg == null && q.prevClose) { chg = q.last - q.prevClose; }
    if (pct == null && q.prevClose) { pct = (q.last - q.prevClose) / q.prevClose * 100; }

    fresh[code] = [
      code,
      q.name || (map[code] ? map[code].name : ''),
      q.last,
      q.prevClose || '',
      chg != null ? Math.round(chg * 100) / 100 : '',
      pct != null ? Math.round(pct * 100) / 100 : '',
      q.volume || 0,
      q.stamp || stamp, q.open || "", q.high || "", q.low || "", q.date || ""
    ];
    taken++;
    if (!batchQuotes[code]) { if (heldSet[code]) { doneHeld++; } else if (viewedFirst.indexOf(code) >= 0) { doneViewed++; } else { doneRotating++; } }
  }

  var nextCursor = String(lastFallback >= 0 ? (lastFallback + 1) % codes.length : cursor);
  /* 當天累計（v134）：一天跑了幾棒、批次來源成功幾棒、逐檔輪替一共更新幾檔。
     先前只留最後一棒的狀態，「批次來源整天沒有回資料」這種事要事後從報價的時間戳倒推才看得出來。 */
  var day = {};
  try { day = JSON.parse(pr.getProperty('QUOTE_DAY_STATS') || '{}'); } catch (e) { day = {}; }
  if (day.date !== todayStr_()) { day = { date: todayStr_(), runs: 0, misOk: 0, misFail: 0, held: 0, rotating: 0, viewed: 0, empty: 0, first: stamp.slice(11, 16) }; }
  day.runs++; day.last = stamp.slice(11, 16);
  if (misState === 'ok') { day.misOk++; } else { day.misFail++; }
  day.held += doneHeld; day.rotating += doneRotating; day.viewed += doneViewed;
  if (!taken) { day.empty++; }
  day.misState = misState; day.via = misState === 'ok' ? MIS_LAST_VIA_ : '';
  if (misState === 'ok' && MIS_LAST_VIA_ === 'relay') { day.relayOk = (day.relayOk || 0) + 1; }
  try { pr.setProperty('QUOTE_DAY_STATS', JSON.stringify(day)); } catch (e) {}
  var jobStatus = { at: stamp, updated: 0, collected: taken, requested: codes.length, writePending: taken > 0,
    mis: Object.keys(batchQuotes).length, misState: misState, misVia: misState === 'ok' ? MIS_LAST_VIA_ : '', misMs: misMs, held: doneHeld, rotating: doneRotating, viewed: doneViewed, day: day,
    fallback: fallbackTaken, errors: errors.slice(0, 8),
    partial: taken < codes.length, configured: hasFugle_(), note: '缺成交價不採用委買／委賣；持有中與正在查看的股票優先，其餘由游標續抓。' };
  pr.setProperty('QUOTE_JOB_STATUS', JSON.stringify(jobStatus));

  if (!taken) {
    pr.setProperty(QUOTE_CURSOR_KEY_, nextCursor);
    Logger.log('即時快取：這一棒 ' + codes.length + ' 檔都沒取到報價，保留舊值');
    return;
  }

  var tracked = {};
  codes.forEach(function (c) { tracked[c] = 1; });

  try { withLock_(function () {
    var sh = getSheet_('即時快取');
    /* 這一棒沒輪到的保留舊列，連同它各自的舊時間戳。
       讀取端據此判斷新鮮度：輪不到的會被當成過期而即時補抓，
       不會因為「表上還有這一檔」就被當成現價。 */
    var keep = [];
    sh.getDataRange().getValues().slice(1).forEach(function (r) {
      var c = String(r[0] || '').trim();
      if (c && !fresh[c] && tracked[c]) { keep.push(r.slice(0, 12)); }
    });

    var rows = Object.keys(fresh).map(function (c) { return fresh[c]; }).concat(keep);
    var oldLast = sh.getLastRow();
    // 先一次寫完整資料，再清舊尾列；寫入失敗時保留原表，不先清空。
    sh.getRange(1, 1, rows.length + 1, 12).setValues([
      ['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']
    ].concat(rows));
    if (oldLast > rows.length + 1) {
      try { sh.getRange(rows.length + 2, 1, oldLast - rows.length - 1, 12).clearContent(); }
      catch (e) { Logger.log('即時快取已寫入，舊尾列待下次清理：' + e); }
    }
  }); } catch (e) {
    jobStatus.writePending = false;
    jobStatus.errors.push('報價寫入未完成，保留舊資料等待下一輪');
    pr.setProperty('QUOTE_JOB_STATUS', JSON.stringify(jobStatus));
    throw e;
  }
  jobStatus.updated = taken;
  jobStatus.writePending = false;
  pr.setProperty('QUOTE_JOB_STATUS', JSON.stringify(jobStatus));
  pr.setProperty(QUOTE_CURSOR_KEY_, nextCursor);

  CACHE.remove('qcache');
  CACHE.remove('tracker');
  CACHE.remove(DASH_CACHE_KEY_);
  Logger.log('即時快取：更新 ' + taken + ' / ' + codes.length + ' 檔');
}

/* 報價時間一律轉成「MM-dd HH:mm:ss」（台北時間）。

   問題長這樣：前端印出「2026-09-04T05:38:00.000Z」。
   試算表把「2026/09/04 13:38」這種字串存成日期值，readSheetObjects_ 讀回來
   是 Date 物件，google.script.run 再把它序列化成 ISO 字串送到瀏覽器——
   於是變成 UTC、帶毫秒、還少了八小時。

   看盤的人要的是「這個價是幾點的」。年份、毫秒、時區標記都不是答案的一部分，
   而時區錯八小時會讓人以為報價是早上抓的。

   轉換放在讀出的那一刻，不是放在前端：前端只是其中一個消費者，
   狀態信、AI 助手的上下文都會讀到同一個欄位。 */
function qTime_(v) {
  if (v == null || v === '') { return ''; }

  // 13:30:00（含）之後的是收盤價，寫「收盤」而不是幾點幾分（v134）：看的人要知道的是「這是不是收盤價」。
  var closeLabel = function (text) { return text.slice(6) >= '13:30:00' ? text.slice(0, 5) + ' 收盤' : text; };

  if (Object.prototype.toString.call(v) === '[object Date]') {
    return closeLabel(Utilities.formatDate(v, TZ, 'MM-dd HH:mm:ss'));
  }

  var t = String(v).trim();

  // 已經被序列化成 ISO 的（舊快取、或別處直接塞了 toISOString）
  if (/^\d{4}-\d{2}-\d{2}T/.test(t)) {
    var d = new Date(t);
    if (!isNaN(d.getTime())) { return closeLabel(Utilities.formatDate(d, TZ, 'MM-dd HH:mm:ss')); }
  }

  // 「2026/09/04 13:38」→「09-04 13:38:00」
  var m = t.match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    return closeLabel(pad2_(m[2]) + '-' + pad2_(m[3]) + ' ' +
           pad2_(m[4]) + ':' + m[5] + ':' + (m[6] || '00'));
  }

  // 「2026/09/03 收盤」→「09-03 收盤」
  var m2 = t.match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})\s*(.*)$/);
  if (m2) {
    return pad2_(m2[2]) + '-' + pad2_(m2[3]) + (m2[4] ? ' ' + m2[4] : '');
  }

  return t;   // 「即時」這類文字原樣留著
}

function pad2_(v) { return ('0' + String(v)).slice(-2); }

/** 讀即時快取。這是前端表格現價的來源。 */
function getQuoteCache() {
  var hit = CACHE.get('qcache');
  if (hit) { return mergeLiveQuotes_(JSON.parse(hit)); }

  var out = {};
  readSheetObjects_('即時快取').forEach(function (r) {
    var c = String(r['代號']).trim();
    if (!c) { return; }
    out[c] = {
      name: r['名稱'], last: Number(r['現價']) || null,
      prevClose: Number(r['昨收']) || null,
      change: r['漲跌'] === '' ? null : Number(r['漲跌']),
      changePct: r['漲跌幅'] === '' ? null : Number(r['漲跌幅']),
      volume: Number(r['成交量']) || 0,
      open: Number(r['開']) || null, high: Number(r['高']) || null, low: Number(r['低']) || null,
      date: r['行情日期'] ? fmtDate_(r['行情日期']) : '',
      time: qTime_(r['更新時間']),
      /* 原始時間戳。time 是給人看的（沒有年份），判斷新鮮度要用這個。
         這裡原樣帶出去：直接讀是 Date 物件，經過 CACHE 的 JSON 往返會變成
         ISO 字串，quoteStampParts_ 兩種都認得。 */
      stamp: r['更新時間']
    };
  });

  // 快取只是加速層；單筆超過服務容量或暫時寫不進時仍回傳本次表格資料。
  try { CACHE.put('qcache', JSON.stringify(out), 120); } catch (e) {}
  return mergeLiveQuotes_(out);
}

function mergeLiveQuotes_(quotes) {
  var keys = Object.keys(quotes).map(function (c) { return 'quote_live_' + c; });
  if (!keys.length) { return quotes; }
  try {
    var live = CACHE.getAll(keys);
    Object.keys(live).forEach(function (key) {
      var q = JSON.parse(live[key]), code = key.replace('quote_live_', '');
      if (quoteFresh_(q.stamp) && (!quotes[code] || quoteAgeMin_(q.stamp) <= quoteAgeMin_(quotes[code].stamp))) { quotes[code] = q; }
    });
  } catch (e) {}
  return quotes;
}

/**
 * 前端要的批次報價。先讀快取，快取沒有的補抓。
 *
 * 先前這裡寫了 missing.slice(0, 10)，只補抓十檔。追蹤清單有七十幾檔，
 * 而 refreshQuoteCacheJob 只在 09:00 到 13:40 執行，所以盤後或假日
 * 快取是空的，七十幾檔只有十檔拿得到價格。
 *
 * 更糟的是這會污染平均報酬：沒有現價的標的 ret 是 null 而被排除，
 * 平均值其實只用那十檔算出來，卻標示成全部。所以上限拿掉，
 * 並且缺價的用日K快取的最後一根收盤價補，這樣至少每一檔都有數字。
 *
 * refreshStale：過期的那幾檔要不要當場對外重抓。
 *
 *   先前只問「表上有沒有這一檔」，有就直接當現價用，不看更新時間。
 *   refreshQuoteCacheJob 停擺時這裡照樣回上週五的價，而且一點徵兆都沒有。
 *
 *   但不能無條件重抓：總覽一次要七十幾檔，逐檔補抓每檔 sleep 1.1 秒，
 *   整頁會卡上一分鐘。所以只有單檔面板（getRealtimeQuote）帶 true——
 *   那是使用者正盯著看的那一檔，一次請求就回來。
 *   批次呼叫沿用舊值，畫面上的時間戳本來就寫著它是什麼時候的。
 */
function getQuotesFor(codes, refreshStale, batchOnly, cacheOnly) {
  var cache = getQuoteCache();
  var out = {}, missing = [];

  (codes || []).forEach(function (c) {
    c = String(c).trim();
    if (!/^(?:00\d{3,4}|\d{4,6})[A-Z]?$/.test(c)) { return; }
    if (missing.indexOf(c) >= 0 || out[c]) { return; }
    var hit = cache[c];
    try { var shared = CACHE.get('quote_live_' + c); if (shared) {
      var checked = JSON.parse(shared); if (quoteFresh_(checked.stamp)) { hit = checked; }
    } } catch (e) {}
    if (!hit) { missing.push(c); return; }
    if (quoteFresh_(hit.stamp)) { out[c] = hit; return; }
    out[c] = hit;
    if (refreshStale) { missing.push(c); }
  });

  if (cacheOnly || !missing.length) { return out; } // v94 首屏不等待外部報價；五分鐘排程與單檔刷新獨立。

  // 盤中才值得為了即時性逐檔對外請求。盤後直接用日K快取的最後收盤價，
  // 那本來就是正確答案，而且不花任何請求。
  if (!isTradingNow_()) {
    var today = todayStr_(), closedNow = marketClosedToday_(), needLive = [];
    missing.forEach(function (c) {
      var hit = out[c] || null, hitToday = !!hit && quoteAgeMin_(hit.stamp) <= 1440;
      var lc = closeQuoteFromK_(c);
      // 今天的日K已經落地，或表上那一列根本不是今天的：日K最後一根就是答案。
      if (lc && (!hitToday || lc.date === today)) { lc.name = (hit && hit.name) || ''; out[c] = lc; return; }
      // 今天已收盤、日K還沒落地、表上停在盤中某一刻：先留著（時間照實寫），下面現場查一次收盤價。
      if (closedNow && hitToday) { needLive.push(c); }
    });
    if (needLive.length) {
      // 一次批次請求（最多 50 檔）；13:30 之後 MIS 回的是收盤價與全日量。連線失敗不擋回應。
      var misDownNow = false;
      try { misDownNow = !!CACHE.get(MIS_DOWN_KEY_); } catch (e) {}
      if (!misDownNow) {
        try {
          var closing = misBatchQuotes_(needLive.slice(0, 50));
          Object.keys(closing).forEach(function (c) {
            if (closing[c].date !== today) { return; }
            var q = closing[c];
            q.stamp = today + ' 13:30:00'; q.time = qTime_(q.stamp);
            out[c] = q;
            try { CACHE.put('quote_live_' + c, JSON.stringify(q), 600); } catch (e) {}
          });
        } catch (e) {
          Logger.log('收盤後補取報價未成功：' + e);
          if (!(e && e.transientBusy)) { try { CACHE.put(MIS_DOWN_KEY_, '1', 600); } catch (ignore) {} }
        }
      }
      // 單檔與小批查詢再用富果補（最多四檔）；13:30 之後取到的成交價就是收盤價。
      var extra = 0;
      needLive.forEach(function (c) {
        if (quoteFresh_(out[c].stamp) || extra >= 4 || !hasFugle_()) { return; }
        extra++;
        try { var live = fugleQuote_(c); if (live && live.date === today) { out[c] = live; } }
        catch (e) { Logger.log('收盤後補取 ' + c + ' 未成功：' + (e.httpCode || '暫時性錯誤')); }
      });
    }
    return out;
  }

  // v88：網站批次報價只發一個 MIS 請求，不逐檔等待 Fugle＋sleep。
  // 查不到仍保留有時間戳的表上行情／日K，不能以委買價冒充成交價。
  if (batchOnly) {
    try {
      var batch = misBatchQuotes_(missing.slice(0, 50));
      Object.keys(batch).forEach(function (c) { if (batch[c].date === todayStr_()) { out[c] = batch[c]; } });
    } catch (e) { Logger.log('批次報價暫時讀不到：' + e); }
    // 單檔與小批查詢不能被批次來源的空值鎖死；限四檔，維持全頁批次的時間上限。
    var supplemental = 0;
    missing.forEach(function (c) {
      if (out[c] && quoteFresh_(out[c].stamp)) { return; }
      if (supplemental >= 4 || !hasFugle_()) { return; }
      supplemental++;
      try { var live = fugleQuote_(c); if (live && live.date === todayStr_()) { out[c] = live; } }
      catch (e) { Logger.log('補取 ' + c + ' 成交報價未成功：' + (e.httpCode || '暫時性錯誤')); }
    });
    missing.forEach(function (c) {
      if (out[c]) { return; }
      var lc = lastCloseOf_(c);
      if (!lc) { return; }
      var chg = lc.prevClose ? lc.last - lc.prevClose : null;
      out[c] = { name: '', last: lc.last, prevClose: lc.prevClose,
        change: chg == null ? null : Math.round(chg * 100) / 100,
        changePct: chg == null || !lc.prevClose ? null : Math.round(chg / lc.prevClose * 10000) / 100,
        volume: sharesToLots_(lc.volume), time: qTime_(lc.date + ' 收盤'), date: lc.date };
    });
    return out;
  }

  // 非網站的既有補抓入口仍沿用富果控速。
  var fetched = 0;
  for (var i = 0; i < missing.length && fetched < 50; i++) {
    var c = missing[i];
    var q = null;
    if (hasFugle_()) { try { q = fugleQuote_(c); } catch (e) { q = null; } }
    if (!q) { try { q = misQuote_(c); } catch (e) { q = null; } }

    if (q && q.date !== todayStr_()) { q = null; }
    if (!q) {
      // 對外也拿不到就退回日K最後一根，總比顯示破折號好
      var k = getCachedDailyK(c);
      if (k.length) {
        var l = k[k.length - 1];
        out[c] = { name: '', last: l.close, prevClose: null, change: null,
                   changePct: null, volume: sharesToLots_(l.volume),
                   time: qTime_(l.date + ' 收盤') };
      }
      continue;
    }
    fetched++;

    var chg = q.change, pct = q.changePct;
    if (chg == null && q.prevClose) { chg = q.last - q.prevClose; }
    if (pct == null && q.prevClose) { pct = (q.last - q.prevClose) / q.prevClose * 100; }

    out[c] = {
      name: q.name, last: q.last, prevClose: q.prevClose,
      change: chg != null ? Math.round(chg * 100) / 100 : null,
      changePct: pct != null ? Math.round(pct * 100) / 100 : null,
      volume: q.volume, time: q.time || qTime_(q.stamp), date: q.date,
      stamp: q.stamp, source: q.source || '', tradeStamp: q.tradeStamp || ''
    };
    Utilities.sleep(1100);
  }

  // 逐檔補抓有 50 檔上限（對外請求速率限制）。
  // 先前超過上限的那些會直接被跳過，連日K收盤價都沒有，
  // 前端就顯示破折號。這裡把所有還沒有值的補上收盤價。
  missing.forEach(function (c) {
    if (out[c]) { return; }
    var lc = lastCloseOf_(c);
    if (!lc) { return; }
    var chg = lc.prevClose ? lc.last - lc.prevClose : null;
    out[c] = {
      name: '', last: lc.last, prevClose: lc.prevClose,
      change: chg != null ? Math.round(chg * 100) / 100 : null,
      changePct: (chg != null && lc.prevClose) ? Math.round(chg / lc.prevClose * 10000) / 100 : null,
      volume: sharesToLots_(lc.volume), time: qTime_(lc.date + ' 收盤')
    };
  });

  return out;
}

/* ------------------------------------------------------------------ *
 * 證交所 MIS 批次報價與單檔備援
 * 本站共用限流為每 5 秒最多 3 次；這是本站保守設定，並非來源額度保證。
 * ------------------------------------------------------------------ */

function throttleMis_() {
  for (var attempt = 0; attempt < 3; attempt++) {
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(200)) {
      var busy = new Error('報價共用鎖忙碌，留待下一輪'); busy.transientBusy = true; throw busy;
    }
    var wait = 0;
    try {
      var now = Date.now(), raw = CACHE.get('mis_calls');
      var calls = raw ? JSON.parse(raw) : [];
      calls = calls.filter(function (t) { return now - t < 5000; });
      if (calls.length < 3) {
        calls.push(now); CACHE.put('mis_calls', JSON.stringify(calls), 30); return;
      }
      wait = Math.max(5000 - (now - calls[0]) + 120, 200);
    } finally { lock.releaseLock(); }
    // 等待來源限流時不占用全站寫入鎖；醒來後重新核對並預約這一次請求。
    Utilities.sleep(wait);
  }
  var limited = new Error('MIS 共用限流仍忙碌，留待下一輪'); limited.transientBusy = true; throw limited;
}

function misQuote_(code) {
  var map = loadCodeMap_().byCode;
  // 對照表市場別可能分類錯或缺（KY 股、剛上市櫃、上市轉上櫃等），
  // 前綴一錯 MIS 就回空。所以先猜一個，抓不到再換另一個市場前綴試一次。
  var first = (map[code] && map[code].market === '上櫃') ? 'otc' : 'tse';
  var second = first === 'otc' ? 'tse' : 'otc';
  return misOnce_(code, first, map) || misOnce_(code, second, map);
}

/* ------------------------------------------------------------------ *
 * 批次報價的連線（v134）
 *
 * 2026/10/06 整個交易日，Apps Script 直連證交所 MIS 每一次都是「Address unavailable」（後台報價排程紀錄），
 * 68 棒沒有一棒拿到批次資料，只剩逐檔備援：一棒 30 秒約 17 檔，240 檔裡持有以外的股票幾個小時才輪到一次。
 * 同一時間從 Cloudflare 連 MIS 是通的（240 檔分 5 批，合計約 1 秒）。
 * 所以：先直連；連線層失敗或非 200 就記一整天，這段時間改由本站 Worker 的 /quote-relay 代為連線。
 * 直連每天只由收盤後的連線檢查重試一次（settleClosingQuotesTick_）：盤中不拿寶貴的三十秒去試一條昨天還不通的路。
 * 轉送只接受 tse_／otc_ 代號清單，並用橋接權杖（SITE_BRIDGE_TOKEN，Worker 與這裡本來就共用）驗證，不是開放代理。
 * 指令碼屬性 QUOTE_RELAY_URL 可改網址；設成 off 就不走轉送。
 * ------------------------------------------------------------------ */
var QUOTE_RELAY_DEFAULT_ = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/quote-relay';
var MIS_DIRECT_DOWN_PROP_ = 'MIS_DIRECT_DOWN_UNTIL';
var MIS_LAST_VIA_ = '';

function quoteRelayUrl_() {
  var v = '';
  try { v = String(PropertiesService.getScriptProperties().getProperty('QUOTE_RELAY_URL') || '').trim(); } catch (e) {}
  if (v === 'off') { return ''; }
  return /^https:\/\/[^\s]+$/.test(v) ? v : QUOTE_RELAY_DEFAULT_;
}

/** MIS 回傳的原始列（msgArray）。兩條路都不通時丟出例外，呼叫端照舊記 MIS 中斷、改用逐檔備援。 */
function misRows_(channels) {
  var query = encodeURIComponent(channels.join('|')), firstError = null, directDown = false, store = null;
  try { store = PropertiesService.getScriptProperties(); directDown = Date.now() < Number(store.getProperty(MIS_DIRECT_DOWN_PROP_) || 0); } catch (e) {}
  var direct = function () {
    try {
      var res = UrlFetchApp.fetch('https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=' + query + '&json=1&delay=0&_=' + Date.now(), {
        muteHttpExceptions: true, headers: { Referer: 'https://mis.twse.com.tw/stock/index.jsp' }
      });
      if (res.getResponseCode() === 200) {
        var rows = JSON.parse(res.getContentText()).msgArray || [];
        MIS_LAST_VIA_ = 'direct';
        try { if (directDown) { store.deleteProperty(MIS_DIRECT_DOWN_PROP_); } } catch (ignore) {}
        return rows;
      }
      firstError = firstError || new Error('MIS HTTP ' + res.getResponseCode());
    } catch (e) { firstError = firstError || e; }
    try { store.setProperty(MIS_DIRECT_DOWN_PROP_, String(Date.now() + 24 * 3600000)); } catch (ignore) {}
    return null;
  };
  var viaRelay = function () {
    var relay = quoteRelayUrl_(), token = '';
    try { token = PropertiesService.getScriptProperties().getProperty('SITE_BRIDGE_TOKEN') || ''; } catch (e) {}
    if (!relay || !token) { firstError = firstError || new Error('沒有可用的報價轉送'); return null; }
    try {
      var r = UrlFetchApp.fetch(relay + '?ex_ch=' + query, { muteHttpExceptions: true, headers: { 'X-Bridge-Token': token } });
      if (r.getResponseCode() !== 200) { firstError = firstError || new Error('報價轉送 HTTP ' + r.getResponseCode()); return null; }
      var body = JSON.parse(r.getContentText());
      if (!body || !body.ok || !Array.isArray(body.msgArray)) { firstError = firstError || new Error('報價轉送回應無效'); return null; }
      MIS_LAST_VIA_ = 'relay';
      return body.msgArray;
    } catch (e) { firstError = firstError || e; return null; }
  };
  /* 順序：直連（沒被記為中斷時）→ 轉送 →（直連這一次被略過的話）回頭再試一次直連。
     2026/10/06 收盤後的紀錄：16:59 直連成功、17:04 直連失敗而轉送成功——直連是時好時壞，不是永久不通，
     所以兩條路互為備援，任何一條通就有資料。 */
  var out = directDown ? null : direct();
  if (!out) { out = viaRelay(); }
  if (!out && directDown) { out = direct(); }
  if (!out) { throw firstError || new Error('批次報價兩條路都沒有回應'); }
  return out;
}

/* 盤中的批次報價，很多列在那一瞬間不是成交訊息：成交價欄位 z 是 "-"（只有委買委賣在變）。
   2026/10/07 盤中實測，同一時刻 5 檔裡有 4 檔是這樣，連 2330 也是；先前把這種列整列丟掉，
   結果一輪只更新到三四成，其餘靠逐檔備援每輪補 17 檔，240 檔裡有 165 檔超過十分鐘沒更新、82 檔整個上午停在昨天收盤。
   收盤後 z 一定有值，所以收盤後的驗證看不出來。

   本站只用可確認的成交價，不拿委買、委賣或試撮價補（個股頁也是這樣寫的），所以做法是：
     一、累計成交量和上次記下的一樣 → 這段時間沒有新的成交，上一筆成交價仍然是最新的成交價；只把確認時間往前推。這是確定的，不是推估。
     二、累計量變了但這一刻沒有成交價 → 記進 MIS_NO_TRADE_，排程在同一輪的時間預算內再問幾次（下一次回來的常常就是成交訊息）；
         仍然問不到的交給逐檔備援，或留到下一輪。不推估。 */
var MIS_NO_TRADE_ = [], MIS_CARRIED_ = 0, MIS_PREVIOUS_ = null, MIS_ROWS_TODAY_ = 0;   // MIS_ROWS_TODAY_：來源回了幾列今天的資料（不論那一刻有沒有成交價）
function misPrevious_() {
  if (!MIS_PREVIOUS_) { try { MIS_PREVIOUS_ = getQuoteCache() || {}; } catch (e) { MIS_PREVIOUS_ = {}; } }
  return MIS_PREVIOUS_;
}

/** 同一批上市／上櫃代號一次查詢，依回傳代號對應，不依陣列順序。 */
function misBatchQuotes_(codes) {
  var out = {}, channels = [], wanted = {};
  codes.forEach(function (c) {
    if (!/^(?:00\d{3,4}|\d{4,6})[A-Z]?$/.test(c) || wanted[c]) { return; }
    wanted[c] = true; channels.push('tse_' + c + '.tw', 'otc_' + c + '.tw');
  });
  if (!channels.length) { return out; }
  throttleMis_();
  var data = { msgArray: misRows_(channels) };
  (data.msgArray || []).forEach(function (m) {
    var code = String(m.c || ''), last = Number.parseFloat(m.z), prev = Number.parseFloat(m.y);
    if (!wanted[code]) { return; }
    var date = String(m.d || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1/$2/$3');
    var hm = String(m.t || '');
    if (!/^\d{4}\/\d{2}\/\d{2}$/.test(date) || !/^\d{2}:\d{2}:\d{2}$/.test(hm)) { return; }
    var carried = false;
    if (date === todayStr_()) { MIS_ROWS_TODAY_++; }
    if (!(last > 0)) {
      var before = misPrevious_()[code], volume = parseInt(m.v, 10) || 0;
      var sameDay = before && String(before.date || '') === date && Number(before.last) > 0;
      if (sameDay && volume > 0 && Number(before.volume) === volume) { last = Number(before.last); carried = true; MIS_CARRIED_++; }
      else { if (volume > 0 && MIS_NO_TRADE_.indexOf(code) < 0) { MIS_NO_TRADE_.push(code); } return; }
    }
    var chg = prev > 0 ? last - prev : null;
    out[code] = { name: m.n || '', last: last, prevClose: prev > 0 ? prev : null,
      change: chg == null ? null : Math.round(chg * 100) / 100,
      changePct: chg == null ? null : Math.round(chg / prev * 10000) / 100,
      volume: parseInt(m.v, 10) || 0, open: parseFloat(m.o) || null, high: parseFloat(m.h) || null, low: parseFloat(m.l) || null,
      date: date, time: qTime_(date + ' ' + hm),
      stamp: date && hm ? date + ' ' + hm : '', source: carried ? '證交所 MIS（累計量未變，最近一筆成交價仍有效）' : '證交所 MIS' };
  });
  return out;
}

function misOnce_(code, market, map) {
  throttleMis_();
  var url = 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=' +
    encodeURIComponent(market + '_' + code + '.tw') + '&json=1&delay=0&_=' + Date.now();

  var res = UrlFetchApp.fetch(url, {
    muteHttpExceptions: true,
    headers: { 'Referer': 'https://mis.twse.com.tw/stock/index.jsp' }
  });

  var data;
  try { data = JSON.parse(res.getContentText()); } catch (e) { return null; }
  if (!data || !data.msgArray || !data.msgArray.length) { return null; }

  var m = data.msgArray[0];
  var last = parseFloat(m.z);
  // 委買價、昨收都不能冒充最新成交。
  if (!(last > 0)) { return null; }

  var prev = parseFloat(m.y);
  return {
    date: String(m.d || '').replace(/^(\d{4})(\d{2})(\d{2})$/, '$1/$2/$3'),
    code: code, name: m.n || (map[code] ? map[code].name : ''),
    last: last, prevClose: isNaN(prev) ? null : prev,
    open: parseFloat(m.o) || null, high: parseFloat(m.h) || null, low: parseFloat(m.l) || null,
    volume: parseInt(m.v, 10) || 0, change: null, changePct: null
  };
}

/** 單檔即時報價 */
function getRealtimeQuote(code) {
  code = String(code || '').trim();
  if (!code) { return null; }

  // 單檔：使用者正盯著這一檔，過期就當場重抓（只有一次對外請求）。
  rememberViewedQuote_(code);
  var q = getQuotesFor([code], true, true)[code];
  if (!q) { return { code: code, ok: false, message: '目前取不到這檔股票的報價。' }; }

  var map = loadCodeMap_().byCode;
  return {
    code: code, ok: true,
    name: q.name || (map[code] ? map[code].name : code),
    last: q.last, prevClose: q.prevClose,
    change: q.change, changePct: q.changePct,
    volume: q.volume, time: q.time, date: q.date || '', stamp: q.stamp || '',
    tradeStamp: q.tradeStamp || '', source: q.source || '', stale: !quoteFresh_(q.stamp),
    market: (map[code] && map[code].market) || ''
  };
}

/* ------------------------------------------------------------------ *
 * 代號建議
 * ------------------------------------------------------------------ */
function getCodeMap_() {
  var m = loadCodeMap_().byCode;
  var out = {};
  Object.keys(m).forEach(function (c) {
    out[c] = { name: m[c].name, market: m[c].market === '上櫃' ? 'otc' : 'tse' };
  });
  return out;
}

function suggestCodes(keyword) {
  var kw = String(keyword || '').replace(/[*＊\s]+/g, '').trim();
  if (!kw) { return []; }

  var m = loadCodeMap_().byCode;
  var out = [];
  var codes = Object.keys(m);
  // 聽錯的寫法（秦城、立即電）先放正式那一檔（v54）；別名表與 pipeline 同一份。
  var table = (typeof PUBLIC_CONFIRMED_NAMES === 'object' && PUBLIC_CONFIRMED_NAMES) || {};
  if (table[kw] && m[table[kw][0]]) {
    out.push({ code: table[kw][0], name: m[table[kw][0]].name, market: m[table[kw][0]].market, alias: kw });
  }

  for (var i = 0; i < codes.length; i++) {
    var c = codes[i];
    if ((c.indexOf(kw) === 0 || m[c].name.indexOf(kw) >= 0) && !out.some(function (o) { return o.code === c; })) {
      out.push({ code: c, name: m[c].name, market: m[c].market });
    }
  }
  // 先核對完整代號／股名，再依前綴與包含排序；避免熱門完整股名被前十二筆截掉。
  function rank(o) {
    return o.code === kw ? 0 : o.name === kw ? 1 : o.alias === kw ? 2 :
      o.code.indexOf(kw) === 0 ? 3 : o.name.indexOf(kw) === 0 ? 4 : 5;
  }
  return out.sort(function (a, b) { return rank(a) - rank(b) || a.code.localeCompare(b.code); }).slice(0, 12);
}

/* ------------------------------------------------------------------ *
 * K 線
 * ------------------------------------------------------------------ */

/**
 * period：'hour' | 'day' | 'week' | 'month'
 *
 * 回傳的 volume 一律是「張」（日K快取存股數，這裡換算；60 分 K 本來就是張）。
 *
 * hour  已落地的歷史 60 分 K，加上 Fugle 提供的當日部分。
 * day   讀日K快取。快取由 backfillDailyKJob 每日補，涵蓋 12 個月（KLINE_MONTHS）。
 * week  由日K聚合。
 * month 由日K聚合。網站的個股面板已經不提供月K了（只留日與週），
 *       但這一支是公開 API，這個分支保留著。
 *       拿掉的話 period='month' 會掉到最後一行回傳日K——
 *       那不是壞掉，是安靜地回錯資料，比直接不支援糟得多。
 */
function getCandles(code, period) {
  code = String(code || '').trim();

  if (period === 'hour') {
    var hist = getHourlyCandles_(code).slice();   // 複製一份：下面會接上當日資料並排序，不能動到共用索引
    if (hourLiveMergeNeeded_(hist)) {
      try {
        var today = fugleHourly_(code);
        var seen = {};
        hist.forEach(function (r,i) { seen[r.date] = i; });
        today.forEach(function (r) { if (seen[r.date] != null) {hist[seen[r.date]]=r;} else {hist.push(r);} });
        hist.sort(function (a, b) { return a.date < b.date ? -1 : 1; });
      } catch (e) { /* Fugle 取不到就只顯示已落地的部分 */ }
    }
    return hist;
  }

  var daily = getCachedDailyK(code);   // 量＝股
  // 週／月K 先用股數加總再換成張，避免逐日換算的小數誤差累積
  if (period === 'week') { return volumeInLots_(aggregate_(daily, 'week')); }
  if (period === 'month') { return volumeInLots_(aggregate_(daily, 'month')); }
  // 盤中預覽那一根的量來自即時報價（張），所以日K要先換成張再接上去
  return intradayPreview_(volumeInLots_(daily), getQuoteCache()[code], Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd'));
}

/** 已落地的 60 分 K。與日K同理，整張表只讀一次並建索引。 */
var _HK_INDEX = null;

/* 「時段」欄的正規化（2026/09/16 v38）。

   寫進去的是 "09:00" 這種字，但試算表會自動把它轉成「時間」格式（畫面上顯示 9:00、靠右對齊），
   getValues 讀回來變成 1899/12/30 的 Date 物件，String() 之後是一長串英文日期，
   組出來的 "2026/07/21 Sat Dec 30 1899 …" 前端完全讀不懂，60 分 K 因此一根都畫不出來。
   1899 年的台北時區還是地方平時 +08:06，用 formatDate 轉回來也可能差幾分鐘，
   所以以畫面上顯示的文字（getDisplayValues）為準，統一成兩位數的 HH:mm。 */
function hourSlot_(value, display) {
  var m = String(display == null ? '' : display).match(/^\s*(\d{1,2}):(\d{2})/);
  if (!m) { m = String(value == null ? '' : value).match(/^\s*(\d{1,2}):(\d{2})/); }
  if (m) { return ('0' + m[1]).slice(-2) + ':' + m[2]; }
  if (Object.prototype.toString.call(value) === '[object Date]') {
    return Utilities.formatDate(value, TZ, 'HH:mm');
  }
  return '';
}

/** 小時K 整張表讀成 [{代號, 日期, 時段, 開, 高, 低, 收, 量}]，時段已正規化。 */
function readHourlyRows_() {
  var sh = getSheet_('小時K');
  var last = sh.getLastRow(), width = sh.getLastColumn();
  if (last < 2 || width < 1) { return []; }
  var head = sh.getRange(1, 1, 1, width).getValues()[0].map(function (h) { return String(h).trim(); });
  var col = function (n) { return head.indexOf(n); };
  var need = ['日期', '時段', '代號', '開', '高', '低', '收', '量'];
  var missing = need.filter(function (n) { return col(n) < 0; });
  if (missing.length) { throw new Error('小時K 缺欄位：' + missing.join('、')); }
  var values = sh.getRange(2, 1, last - 1, width).getValues();
  var shown = sh.getRange(2, col('時段') + 1, last - 1, 1).getDisplayValues();
  var out = [];
  for (var i = 0; i < values.length; i++) {
    var row = values[i];
    var code = String(row[col('代號')]).trim();
    var date = fmtDate_(row[col('日期')]);
    var slot = hourSlot_(row[col('時段')], shown[i][0]);
    if (!code || !date || !slot) { continue; }
    out.push({ code: code, date: date, slot: slot,
               open: Number(row[col('開')]), high: Number(row[col('高')]), low: Number(row[col('低')]),
               close: Number(row[col('收')]), volume: Number(row[col('量')]) || 0 });
  }
  return out;
}

function loadAllHourly_() {
  if (_HK_INDEX) { return _HK_INDEX; }
  var map = {}, at = {};
  readHourlyRows_().forEach(function (r) {
    if (!r.close) { return; }
    var bar = {
      date: r.date + ' ' + r.slot,
      open: r.open, high: r.high, low: r.low, close: r.close,
      volume: r.volume                  // 張
    };
    /* 同一根重複落地時取「最後寫入」的那一筆：當天 13:45 先由快照聚合出備援，
       14:05 富果的 60 分 K 才寫進來，後寫的是交易所逐筆算出的正確值。 */
    var key = r.code + '|' + bar.date;
    if (!map[r.code]) { map[r.code] = []; }
    if (at[key] != null) { map[r.code][at[key]] = bar; return; }
    at[key] = map[r.code].length;
    map[r.code].push(bar);
  });
  Object.keys(map).forEach(function (c) {
    map[c].sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
  });
  _HK_INDEX = map;
  return map;
}

/* K 線讀取快取（2026/09/16 v41）。

   個股面板一打開就停在「K 線載入中…」好幾秒：每一次請求都把整張日K快取（三百檔×一年）
   或整張小時K 讀進來，只為了取其中一檔。CacheService 原本逐檔各存一份，但只有被點過的那一檔會進快取，
   其他人點別檔時又整張重讀。

   現在：整張表讀一次，就把每一檔各壓成一個精簡的 JSON 陣列（[日期, 開, 高, 低, 收, 量]），
   用 putAll 一次寫進快取（每批 100 筆），之後任何一檔都直接從快取取。
   CacheService 限制：單一項目 100 KB、最長 6 小時、總數 1,000 個——日K、60 分 K 各一個前綴，
   代號超過 400 檔時只快取被點的那一檔，避免把其他快取擠掉。
   資料有寫入（補日K、快照聚合、60 分 K 落地、補缺的日K）就刪掉那幾檔的快取；
   補日K整輪完成後與每 5.5 小時會預先整批寫好（warmKCaches_），不會輪到訪客去等整張讀取。
   沒有資料的代號也存一個空陣列，未列入追蹤的股票不會每點一次就整張重讀。 */
var KC_TTL_ = 21600;
var KC_MAX_CODES_ = 400;
var KC_MAX_BYTES_ = 95000;

function kcEncode_(rows) {
  return JSON.stringify((rows || []).map(function (r) { return [r.date, r.open, r.high, r.low, r.close, r.volume]; }));
}

function kcDecode_(text) {
  return JSON.parse(text).map(function (a) {
    return { date: a[0], open: a[1], high: a[2], low: a[3], close: a[4], volume: a[5] };
  });
}

/** 整份索引依代號寫進快取，一次 putAll 最多 100 筆。only：代號太多時只寫這一檔。 */
function kcPutAll_(prefix, index, only) {
  var codes = Object.keys(index || {});
  if (codes.length > KC_MAX_CODES_) { codes = only ? [only] : []; }
  if (only && codes.indexOf(only) < 0) { codes.push(only); }
  var batch = {}, n = 0, written = 0;
  var flush = function () {
    if (!n) { return; }
    try { CACHE.putAll(batch, KC_TTL_); written += n; } catch (e) { /* 快取寫不進去只影響速度 */ }
    batch = {}; n = 0;
  };
  codes.forEach(function (c) {
    var text = kcEncode_(index[c] || []);
    if (text.length > KC_MAX_BYTES_) { return; }
    batch[prefix + c] = text; n++;
    if (n >= 100) { flush(); }
  });
  flush();
  return written;
}

/** 資料寫入後刪掉那幾檔的快取。 */
function kcDrop_(prefix, codes) {
  var keys = (codes || []).map(function (c) { return prefix + String(c).trim(); });
  if (!keys.length) { return; }
  try { CACHE.removeAll(keys); } catch (e) { keys.forEach(function (k) { try { CACHE.remove(k); } catch (e2) {} }); }
}

function getHourlyCandles_(code) {
  code = String(code || '').trim();
  var hit = null;
  try { hit = CACHE.get('hk2_' + code); } catch (e) { hit = null; }
  if (hit) { try { return typeof mergedHourlyHistory_==='function' ? mergedHourlyHistory_(code,kcDecode_(hit)) : kcDecode_(hit); } catch (e) { /* 壞掉的快取當作沒有 */ } }
  var index = loadAllHourly_();
  kcPutAll_('hk2_', index, code);
  return typeof mergedHourlyHistory_==='function' ? mergedHourlyHistory_(code,index[code]||[]) : index[code]||[];
}

/* 60 分 K 要不要再向富果要當天的部分：平日 09:00 以後、而且當天 13:00 那一根還沒落地才要。
   先前每次開 60 分 K 都打一次富果，晚上、週末也打，白白多等一秒。 */
function hourLiveMergeNeeded_(hist) {
  if (!hasFugle_()) { return false; }
  var now = new Date();
  var dow = Number(Utilities.formatDate(now, TZ, 'u'));
  var hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
  if (!(dow >= 1 && dow <= 5) || !(hhmm >= 900)) { return false; }
  if(typeof isMarketHoliday_==='function'&&isMarketHoliday_(now)){return false;}
  if(hhmm<1405){return true;} // 13:00 那根在13:05出現時還沒收完，不能因此停止更新。
  var last = Utilities.formatDate(now, TZ, 'yyyy/MM/dd') + ' 13:00';
  return !(hist || []).some(function (r) { return r.date === last; });
}

/**
 * 個股 K 線一次取回日K、週K、60 分 K（2026/09/16 v41）。
 * 前端切換日／週／60分不必再等伺服器；三種週期共用同一份日K讀取。
 */
function getCandlesBundle(code) {
  code = String(code || '').trim();
  var daily = getCachedDailyK(code);   // 量＝股
  var today = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
  var hourly=getCandles(code,'hour');
  return {
    code: code,
    day: intradayPreview_(volumeInLots_(daily), getQuoteCache()[code], today),
    week: volumeInLots_(aggregate_(daily, 'week')),
    month: volumeInLots_(aggregate_(daily, 'month')),
    hour: hourly,
    hourCoverage: typeof hourlyCoverage_==='function'?hourlyCoverage_(code,hourly,daily):null,
    at: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss')
  };
}

/** 14:05 觸發。把當日 60 分 K 從 Fugle 抓下來落地。 */
function aggregateHourlyJob() {
  // v67：觸發器耗時紀錄與休市略過（見 Setup.gs 的 opsTimed_）；本體在 aggregateHourlyJobRun_
  return typeof opsTimed_ === 'function' ? opsTimed_('aggregateHourlyJob', aggregateHourlyJobRun_, arguments, true) : aggregateHourlyJobRun_.apply(null, arguments);
}
function aggregateHourlyJobRun_() {
  if (!hasFugle_()) {
    Logger.log('尚未設定 FUGLE_API_KEY，跳過 60 分 K 落地。');
    return;
  }

  var today = todayStr_();
  var codes = trackedCodes_();
  if (!codes.length) { return; }

  /* 同一天、同一檔以富果的 60 分 K 為準（2026/09/16 v38）。
     13:45 的 aggregateSnapshotJob 會先用五分鐘快照聚合出當天的小時K 當備援，
     快照的開高低只取得到每五分鐘一個點，富果的是交易所逐筆成交算出來的。
     先前這裡遇到「已經有這一根」就跳過，保留的反而是比較粗的那一份；
     而時段欄被試算表轉成時間格式，比對的鍵永遠對不上，實際效果是同一根重複寫兩次。
     現在：富果抓得到的那幾檔，先刪掉當天既有的列再寫入；抓不到的保留快照聚合的備援。 */
  var rows = [], fetched = {};
  codes.forEach(function (code) {
    try {
      fugleHourly_(code).forEach(function (b) {
        var parts = b.date.split(' ');
        if (parts[0] !== today) { return; }
        fetched[code] = 1;
        rows.push([parts[0], parts[1], code, b.open, b.high, b.low, b.close, b.volume]);
      });
    } catch (e) { /* 單檔失敗不影響其他 */ }
    Utilities.sleep(1100);   // Fugle 日內行情 60 次/分
  });

  if (!rows.length) { Logger.log('今日無新的 60 分 K'); return; }

  withLock_(function () {
    var sh = getSheet_('小時K');
    var last = sh.getLastRow();
    if (last >= 2) {
      var key = sh.getRange(2, 1, last - 1, 3).getValues();
      var drop = [];
      for (var i = 0; i < key.length; i++) {
        if (fmtDate_(key[i][0]) === today && fetched[String(key[i][2]).trim()]) { drop.push(i + 2); }
      }
      for (var j = drop.length - 1; j >= 0; j--) { sh.deleteRow(drop[j]); }
    }
    var first = sh.getLastRow() + 1;
    // 時段欄先設成純文字，"09:00" 才不會被轉成 1899 年的時間值
    sh.getRange(first, 2, rows.length, 1).setNumberFormat('@');
    sh.getRange(first, 1, rows.length, 8).setValues(rows);
  });

  _HK_INDEX = null;
  CACHE.remove('hk_meta');
  kcDrop_('hk2_', Object.keys(fetched));
  Logger.log('落地 ' + rows.length + ' 根 60 分 K');
}

function getHourlyMeta() {
  var hit = CACHE.get('hk_meta');
  if (hit) { return JSON.parse(hit); }

  var rows = readHourlyRows_();
  var dates = rows.map(function (r) { return r.date; }).filter(String).sort();
  var meta = {
    since: dates.length ? dates[0] : '',
    until: dates.length ? dates[dates.length - 1] : '',
    days: dates.filter(function (v, i, a) { return a.indexOf(v) === i; }).length,
    bars: rows.length,
    hasFugle: hasFugle_()
  };
  CACHE.put('hk_meta', JSON.stringify(meta), 1800);
  return meta;
}

/** 日 K 聚合為週 K 或月 K */
function aggregate_(daily, unit) {
  var buckets = {};
  daily.forEach(function (r) {
    var key;
    if (unit === 'month') {
      key = r.date.slice(0, 7);
    } else {
      var d = new Date(r.date);
      var day = d.getDay() === 0 ? 7 : d.getDay();
      d.setDate(d.getDate() - day + 1);
      key = Utilities.formatDate(d, TZ, 'yyyy/MM/dd');
    }
    if (!buckets[key]) {
      buckets[key] = { date: r.date, open: r.open, high: r.high, low: r.low, close: r.close, volume: r.volume };
    } else {
      var b = buckets[key];
      b.high = Math.max(b.high, r.high);
      b.low = Math.min(b.low, r.low);
      b.close = r.close;
      b.volume += r.volume;
      b.date = r.date;
    }
  });
  return Object.keys(buckets).sort().map(function (k) { return buckets[k]; });
}

/* ------------------------------------------------------------------ *
 * 基本面
 * ------------------------------------------------------------------ */

function getValuationTable_() {
  var hit = CACHE.get('val_tbl');
  if (hit) { return JSON.parse(hit); }

  var map = {};

  try {
    fetchJson_('https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL').forEach(function (r) {
      var c = pickField_(r, ['Code', '證券代號']);
      if (!c) { return; }
      map[c] = {
        per: parseFloat(pickField_(r, ['PEratio', '本益比'])) || null,
        pbr: parseFloat(pickField_(r, ['PBratio', '股價淨值比'])) || null,
        yld: parseFloat(pickField_(r, ['DividendYield', '殖利率(%)'])) || null
      };
    });
  } catch (e) {
    Logger.log('上市估值表取得失敗：' + e.message + '。本益比等欄位會顯示未提供。');
  }

  try {
    fetchJson_('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis').forEach(function (r) {
      var c = pickField_(r, ['SecuritiesCompanyCode', 'Code']);
      if (!c) { return; }
      map[c] = {
        per: parseFloat(pickField_(r, ['PriceEarningRatio', 'PERatio'])) || null,
        pbr: parseFloat(pickField_(r, ['PriceBookRatio', 'PBRatio'])) || null,
        yld: parseFloat(pickField_(r, ['YieldRatio', 'DividendYield'])) || null
      };
    });
  } catch (e) {
    Logger.log('上櫃估值表取得失敗：' + e.message + '。上櫃股票的本益比等欄位會顯示未提供。');
  }

  if (Object.keys(map).length) {
    var p = JSON.stringify(map);
    if (p.length < 95000) { CACHE.put('val_tbl', p, 21600); }
  }
  return map;
}

function getProfileTable_() {
  var hit = CACHE.get('prof_tbl');
  if (hit) { return JSON.parse(hit); }

  var map = {};

  // 公司基本資料與對照表同源，直接沿用 trySources_，一個死了自動換下一個
  [{ src: LISTED_SOURCES, mk: '上市' }, { src: OTC_SOURCES, mk: '上櫃' }].forEach(function (cfg) {
    for (var i = 0; i < cfg.src.length; i++) {
      var s = cfg.src[i];
      try {
        var raw = (s.type === 'csv') ? fetchCsv_(s.url) : fetchJson_(s.url);
        raw.forEach(function (r) {
          var c = pickField_(r, s.code);
          if (!/^(?:00981A|\d{4,6})$/.test(c)) { return; }
          map[c] = {
            full: pickField_(r, ['公司名稱', 'CompanyName']),
            industry: pickField_(r, s.industry),
            listed: pickField_(r, ['上市日期', '上櫃日期', 'ListingDate']),
            chairman: pickField_(r, ['董事長', 'Chairman'])
          };
        });
        break;
      } catch (e) { /* 換下一個來源 */ }
    }
  });

  if (Object.keys(map).length) {
    var p = JSON.stringify(map);
    if (p.length < 95000) { CACHE.put('prof_tbl', p, 21600); }
  }
  return map;
}

/**
 * 每日 14:30 觸發。把追蹤清單的基本面抓下來落地。
 *
 * 存在理由：先前每次開個股面板都現抓 BWIBBU_ALL 加 t187ap03_L 共兩千筆，
 * 快取一過期就要等好幾秒，整個面板卡在「讀取中」。改成每天算一次存起來。
 */
function rebuildFundamentalsJob() {
  // v67：觸發器耗時紀錄與休市略過（見 Setup.gs 的 opsTimed_）；本體在 rebuildFundamentalsJobRun_
  return typeof opsTimed_ === 'function' ? opsTimed_('rebuildFundamentalsJob', rebuildFundamentalsJobRun_, arguments, true) : rebuildFundamentalsJobRun_.apply(null, arguments);
}
function rebuildFundamentalsJobRun_() {
  var codes = trackedCodes_();
  if (!codes.length) { Logger.log('沒有追蹤中的代號，略過'); return; }

  var val = getValuationTable_();
  var prof = getProfileTable_();
  var map = loadCodeMap_().byCode;
  var now = todayStr_();

  var rows = codes.sort().map(function (c) {
    var v = val[c] || {}, p = prof[c] || {}, m = map[c] || {};
    return [
      c, m.name || '', m.market || '', p.industry || m.industry || '',
      v.per || '', v.pbr || '', v.yld || '',
      p.listed || '', p.chairman || '', now
    ];
  });

  withLock_(function () {
    var sh = getSheet_('基本面快取');
    sh.clearContents();
    sh.getRange(1, 1, 1, 10).setValues([['代號', '名稱', '市場', '產業', '本益比',
      '股價淨值比', '殖利率', '上市櫃日', '董事長', '更新時間']]);
    sh.getRange(2, 1, rows.length, 10).setValues(rows);
  });

  CACHE.remove('fund_cache');

  var withVal = rows.filter(function (r) { return r[4] !== ''; }).length;
  Logger.log('基本面快取更新：' + rows.length + ' 檔，其中 ' + withVal + ' 檔有估值資料');
  if (withVal < rows.length) {
    Logger.log('沒有估值的多半是上櫃股。若上櫃全部沒有，執行 probeEndpoints() 看櫃買端點是否改版。');
  }
}

function loadFundCache_() {
  var hit = CACHE.get('fund_cache');
  if (hit) { return JSON.parse(hit); }

  var out = {};
  readSheetObjects_('基本面快取').forEach(function (r) {
    var c = String(r['代號']).trim();
    if (!c) { return; }
    out[c] = {
      name: r['名稱'], market: r['市場'], industry: industryName_(r['產業']),
      per: Number(r['本益比']) || null, pbr: Number(r['股價淨值比']) || null,
      yld: Number(r['殖利率']) || null,
      listed: listedDate_(r['上市櫃日']), chairman: r['董事長'], updated: r['更新時間']
    };
  });

  var p = JSON.stringify(out);
  if (p.length < 95000) { CACHE.put('fund_cache', p, 3600); }
  return out;
}

/* ------------------------------------------------------------------ *
 * 基本面欄位的顯示轉換
 *
 * 兩個欄位是直接把來源的原始值搬到畫面上的：
 *   產業別   證交所的公司基本資料回的是代碼，畫面上就出現一個「22」
 *   上市櫃日 回的是「20191007」這種連在一起的八碼
 *
 * 轉換放在讀出的那一刻，不是放在抓取的那一刻——已經落地在快取分頁裡的
 * 幾十列舊資料也要跟著變乾淨，否則得等下一次重抓才會對。
 * ------------------------------------------------------------------ */

/* 證交所與櫃買中心的產業別代碼。兩邊共用同一套編碼。
   查不到的代碼原樣顯示，不要吞掉——那代表這份表該補了，
   顯示成空白只會讓人以為那一檔沒有產業別。 */
var INDUSTRY_CODES_ = {
  '01': '水泥工業', '02': '食品工業', '03': '塑膠工業', '04': '紡織纖維',
  '05': '電機機械', '06': '電器電纜', '07': '化學生技醫療', '08': '玻璃陶瓷',
  '09': '造紙工業', '10': '鋼鐵工業', '11': '橡膠工業', '12': '汽車工業',
  '13': '電子工業', '14': '建材營造', '15': '航運業', '16': '觀光餐旅',
  '17': '金融保險', '18': '貿易百貨', '19': '綜合', '20': '其他',
  '21': '化學工業', '22': '生技醫療業', '23': '油電燃氣業', '24': '半導體業',
  '25': '電腦及週邊設備業', '26': '光電業', '27': '通信網路業',
  '28': '電子零組件業', '29': '電子通路業', '30': '資訊服務業',
  '31': '其他電子業', '32': '文化創意業', '33': '農業科技業',
  '34': '電子商務', '35': '綠能環保', '36': '數位雲端', '37': '運動休閒',
  '38': '居家生活', '80': '管理股票', '97': '存託憑證', '98': '受益證券',
  '99': 'ETF'
};

/** 「22」→「生技醫療業」。本來就是中文的原樣返回。 */
function industryName_(v) {
  var t = String(v == null ? '' : v).trim();
  if (!t) { return ''; }
  if (!/^\d{1,2}$/.test(t)) { return t; }      // 已經是中文
  return INDUSTRY_CODES_[('0' + t).slice(-2)] || t;
}

/** 「20191007」→「2019/10/07」。已經有分隔符號或看不懂的原樣返回。 */
function listedDate_(v) {
  var t = String(v == null ? '' : v).trim();
  if (!t) { return ''; }
  var m = t.match(/^(\d{4})(\d{2})(\d{2})$/);
  if (m) { return m[1] + '/' + m[2] + '/' + m[3]; }
  // 民國年：「1081007」這種七碼
  var r = t.match(/^(\d{3})(\d{2})(\d{2})$/);
  if (r) { return (Number(r[1]) + 1911) + '/' + r[2] + '/' + r[3]; }
  return t.replace(/-/g, '/');
}

/**
 * 個股基本面。先讀落地快取，沒有才現抓。取不到的欄位一律 null，
 * 前端顯示「未提供」，不編數字。
 */
function getFundamentals(code) {
  code = String(code || '').trim();
  var m = loadCodeMap_().byCode[code] || {};

  var f = loadFundCache_()[code];
  if (f) {
    return {
      code: code, name: f.name || m.name || '', market: f.market || m.market || '',
      per: f.per, pbr: f.pbr, yld: f.yld,
      industry: f.industry || '', listed: f.listed || '', chairman: f.chairman || '',
      cached: true,
      source: '本益比、股價淨值比、殖利率來自證交所與櫃買中心公開資料，更新於 ' + (f.updated || '未知') + '。'
    };
  }

  // 追蹤清單以外的冷門股，現抓一次
  var v = getValuationTable_()[code] || {};
  var p = getProfileTable_()[code] || {};
  return {
    code: code, name: m.name || '', market: m.market || '',
    per: v.per || null, pbr: v.pbr || null, yld: v.yld || null,
    industry: industryName_(p.industry || m.industry || ''),
    listed: listedDate_(p.listed || ''), chairman: p.chairman || '',
    cached: false,
    source: '本益比、股價淨值比、殖利率來自證交所與櫃買中心公開資料，為前一交易日數值。'
  };
}
