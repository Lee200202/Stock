/**
 * 檔案：QuoteService.gs
 *
 * 報價來源分工：
 *   Fugle      即時報價、當日與歷史 60 分 K；Yahoo 在獨立分頁補歷史空缺。
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
  var j = fugleFetch_('/intraday/quote/' + encodeURIComponent(code));
  var last = (j.lastPrice != null) ? Number(j.lastPrice)
    : (j.closePrice != null) ? Number(j.closePrice) : null;
  if (last == null) { return null; }

  return {
    date: String(j.date || "").replace(/-/g,"/"),
    code: String(j.symbol || code),
    name: j.name || '',
    last: last,
    prevClose: j.previousClose != null ? Number(j.previousClose) : null,
    open: j.openPrice != null ? Number(j.openPrice) : null,
    high: j.highPrice != null ? Number(j.highPrice) : null,
    low: j.lowPrice != null ? Number(j.lowPrice) : null,
    volume: Number(j.total && j.total.tradeVolume) || 0,   // 即時報價：張
    change: j.change != null ? Number(j.change) : null,
    changePct: j.changePercent != null ? Number(j.changePercent) : null
  };
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
var QUOTE_JOB_BUDGET_MS_ = 180000;   // 3 分鐘。everyFiveMinJob 還有別的事要做，六分鐘上限要留餘裕。

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

var QUOTE_STALE_MIN_ = 10;        // 盤中容許的快取年齡（分鐘）

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

/* 這一列還能不能當現價用。
   盤中限 QUOTE_STALE_MIN_ 分鐘；盤後只要是今天的就算數——那本來就是收盤價。 */
function quoteFresh_(stamp) {
  return quoteAgeMin_(stamp) <= (isTradingNow_() ? QUOTE_STALE_MIN_ : 1440);
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
  var taken = 0, i;

  for (i = 0; i < codes.length; i++) {
    if (Date.now() > deadline) { break; }
    var code = codes[(cursor + i) % codes.length];

    var q = null;
    if (hasFugle_()) {
      try { q = fugleQuote_(code); } catch (e) { q = null; }
    }
    if (!q) {
      try { q = misQuote_(code); } catch (e) { q = null; }
    }
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
      stamp, q.open || "", q.high || "", q.low || "", q.date || ""
    ];
    taken++;
  }

  pr.setProperty(QUOTE_CURSOR_KEY_, String((cursor + i) % codes.length));

  if (!taken) {
    Logger.log('即時快取：這一棒 ' + codes.length + ' 檔都沒取到報價，保留舊值');
    return;
  }

  var tracked = {};
  codes.forEach(function (c) { tracked[c] = 1; });

  withLock_(function () {
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
    sh.clearContents();
    sh.getRange(1, 1, 1, 12).setValues([['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']]);
    sh.getRange(2, 1, rows.length, 12).setValues(rows);
  });

  CACHE.remove('qcache');
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

  if (Object.prototype.toString.call(v) === '[object Date]') {
    return Utilities.formatDate(v, TZ, 'MM-dd HH:mm:ss');
  }

  var t = String(v).trim();

  // 已經被序列化成 ISO 的（舊快取、或別處直接塞了 toISOString）
  if (/^\d{4}-\d{2}-\d{2}T/.test(t)) {
    var d = new Date(t);
    if (!isNaN(d.getTime())) { return Utilities.formatDate(d, TZ, 'MM-dd HH:mm:ss'); }
  }

  // 「2026/09/04 13:38」→「09-04 13:38:00」
  var m = t.match(/^(\d{4})[\/-](\d{1,2})[\/-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    return pad2_(m[2]) + '-' + pad2_(m[3]) + ' ' +
           pad2_(m[4]) + ':' + m[5] + ':' + (m[6] || '00');
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
  if (hit) { return JSON.parse(hit); }

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

  CACHE.put('qcache', JSON.stringify(out), 120);
  return out;
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
function getQuotesFor(codes, refreshStale) {
  var cache = getQuoteCache();
  var out = {}, missing = [];

  (codes || []).forEach(function (c) {
    c = String(c).trim();
    if (!/^(?:00981A|\d{4,6})$/.test(c)) { return; }
    var hit = cache[c];
    if (!hit) { missing.push(c); return; }
    if (quoteFresh_(hit.stamp)) { out[c] = hit; return; }
    if (refreshStale) { missing.push(c); } else { out[c] = hit; }
  });

  if (!missing.length) { return out; }

  // 盤中才值得為了即時性逐檔對外請求。盤後直接用日K快取的最後收盤價，
  // 那本來就是正確答案，而且不花任何請求。
  if (!isTradingNow_()) {
    missing.forEach(function (c) {
      var k = getCachedDailyK(c);
      if (!k.length) { return; }
      var last = k[k.length - 1];
      var prev = k.length > 1 ? k[k.length - 2] : null;
      var chg = prev ? last.close - prev.close : null;
      out[c] = {
        name: '', last: last.close, prevClose: prev ? prev.close : null,
        change: chg != null ? Math.round(chg * 100) / 100 : null,
        changePct: (chg != null && prev.close) ? Math.round(chg / prev.close * 10000) / 100 : null,
        volume: sharesToLots_(last.volume), time: qTime_(last.date + ' 收盤')
      };
    });
    return out;
  }

  // 盤中：逐檔補抓。Fugle 日內行情 60 次/分，所以每次最多 50 檔，留餘裕。
  var fetched = 0;
  for (var i = 0; i < missing.length && fetched < 50; i++) {
    var c = missing[i];
    var q = null;
    if (hasFugle_()) { try { q = fugleQuote_(c); } catch (e) { q = null; } }
    if (!q) { try { q = misQuote_(c); } catch (e) { q = null; } }

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
      volume: q.volume, time: '即時'
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
 * 證交所 MIS 備援
 * 沒有 Fugle 金鑰、或 Fugle 出錯時走這裡。
 * 有每 5 秒 3 次的限制，所以只當備援，不當主力。
 * ------------------------------------------------------------------ */

function throttleMis_() {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var now = Date.now();
    var raw = CACHE.get('mis_calls');
    var calls = raw ? JSON.parse(raw) : [];
    calls = calls.filter(function (t) { return now - t < 5000; });
    if (calls.length >= 3) {
      Utilities.sleep(Math.max(5000 - (now - calls[0]) + 120, 200));
      now = Date.now();
      calls = calls.filter(function (t) { return now - t < 5000; });
    }
    calls.push(now);
    CACHE.put('mis_calls', JSON.stringify(calls), 30);
  } finally {
    lock.releaseLock();
  }
}

function misQuote_(code) {
  var map = loadCodeMap_().byCode;
  // 對照表市場別可能分類錯或缺（KY 股、剛上市櫃、上市轉上櫃等），
  // 前綴一錯 MIS 就回空。所以先猜一個，抓不到再換另一個市場前綴試一次。
  var first = (map[code] && map[code].market === '上櫃') ? 'otc' : 'tse';
  var second = first === 'otc' ? 'tse' : 'otc';
  return misOnce_(code, first, map) || misOnce_(code, second, map);
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
  if (isNaN(last)) { last = parseFloat(m.b ? m.b.split('_')[0] : NaN); }
  if (isNaN(last)) { last = parseFloat(m.y); }
  if (isNaN(last)) { return null; }

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
  var q = getQuotesFor([code], true)[code];
  if (!q) { return { code: code, ok: false, message: '目前取不到這檔股票的報價。' }; }

  var map = loadCodeMap_().byCode;
  return {
    code: code, ok: true,
    name: q.name || (map[code] ? map[code].name : code),
    last: q.last, prevClose: q.prevClose,
    change: q.change, changePct: q.changePct,
    volume: q.volume, time: q.time,
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

  for (var i = 0; i < codes.length && out.length < 12; i++) {
    var c = codes[i];
    if ((c.indexOf(kw) === 0 || m[c].name.indexOf(kw) >= 0) && !out.some(function (o) { return o.code === c; })) {
      out.push({ code: c, name: m[c].name, market: m[c].market });
    }
  }
  return out;
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
