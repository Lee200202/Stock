/**
 * 檔案：CacheBuilder.gs
 *
 * 這個檔案存在的理由：網站慢，而且持股追蹤是空的。兩件事同源。
 *
 * 慢，是因為每次載入都要向證交所逐月抓日K。
 * 追蹤空白，是因為操作紀錄裡的代號有一堆「代號待確認」，被過濾器濾光了。
 *
 * 解法是把所有慢的、會變的東西全部落地到試算表，線上只讀不抓：
 *   股票對照表   代號與名稱的權威來源，每週更新
 *   日K快取     逐月抓一次就存著，不重抓
 *   即時快取     盤中每 5 分鐘更新，前端直接讀
 *   每日績效     每天收盤記一筆，供折線圖使用
 *
 * 所有排程都有時間預算，跑不完就記住進度、下次接著跑，不會逾時中斷。
 */

var BUDGET_MS = 4.5 * 60 * 1000;   // GAS 單次上限 6 分鐘，留 1.5 分鐘餘裕
// 日K 往前補幾個月。
//
// 24 → 6 → 12。兩年的資料在圖上會被壓成細線看不出型態，抓取也慢；
// 六個月雖然快，但實測顯示一年才夠看出完整的波段結構。
// 十二個月是兩者的平衡點，配合分批補齊之後抓取時間也不再是問題。
//
// 改這個值時，QuoteService 的 TARGET_DAYS 要一起改，否則抓取端會把
// 區間截短，結果是這裡要一年、實際只拿到半年，而且不會有任何錯誤訊息。
var KLINE_MONTHS = 12;

function budgetLeft_(start) { return Date.now() - start < BUDGET_MS; }

/**
 * 追蹤宇宙：所有曾出現在操作紀錄或會員持股、且代號合格的股票。
 * 快取表與報價快取都以這份清單為範圍，代號待確認的自然不在內。
 */
function trackedCodes_() {
  var set = {};
  ['操作紀錄', '會員持股'].forEach(function (name) {
    readSheetObjects_(name).forEach(function (r) {
      var c = String(r['代號'] || '').trim();
      if (/^(?:00981A|\d{4,6})$/.test(c)) { set[c] = 1; }
    });
  });
  return Object.keys(set);
}

/* ================================================================== *
 * 對外抓取
 *
 * 政府開放資料的端點會改版。櫃買中心的 openapi 路徑就改過，
 * 改版後回傳的是 HTML 錯誤頁，而 JSON.parse 只會吐一句
 * 「Unexpected token '<'」，完全看不出是端點死了。
 *
 * 所以這裡做兩件事：先驗證回應是不是真的 JSON 或 CSV，
 * 以及每種資料都準備多個來源，一個死了自動換下一個。
 * ================================================================== */

function fetchJson_(url) {
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
  var code = res.getResponseCode();
  if (code !== 200) { throw new Error('HTTP ' + code); }

  var text = res.getContentText();
  var head = text.slice(0, 300).trim();
  if (head.charAt(0) === '<') {
    throw new Error('回傳的是網頁不是 JSON，這個端點多半已改版或失效');
  }
  if (!head) { throw new Error('回傳空內容'); }
  return JSON.parse(text);
}

function fetchCsv_(url) {
  var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
  var code = res.getResponseCode();
  if (code !== 200) { throw new Error('HTTP ' + code); }

  var text = res.getContentText('UTF-8');
  if (text.slice(0, 300).trim().charAt(0) === '<') {
    throw new Error('回傳的是網頁不是 CSV，這個端點多半已改版或失效');
  }

  var rows = Utilities.parseCsv(text);
  if (rows.length < 2) { throw new Error('CSV 沒有資料列'); }

  var headers = rows[0].map(function (h) { return String(h).replace(/^\uFEFF/, '').trim(); });
  return rows.slice(1).map(function (r) {
    var o = {};
    headers.forEach(function (h, i) { o[h] = r[i]; });
    return o;
  });
}

/** 從多個可能的欄位名稱中取第一個有值的。政府資料欄位名不一致，這個省很多事。 */
function pickField_(row, names) {
  for (var i = 0; i < names.length; i++) {
    var v = row[names[i]];
    if (v !== undefined && v !== null && String(v).trim()) { return String(v).trim(); }
  }
  return '';
}

/**
 * 上市與上櫃的代號名稱來源，依序嘗試。
 * 第一個成功就停，全部失敗才報錯。
 */
var LISTED_SOURCES = [
  {
    label: '證交所 OpenAPI 上市公司基本資料',
    type: 'json',
    url: 'https://openapi.twse.com.tw/v1/opendata/t187ap03_L',
    code: ['公司代號'], name: ['公司簡稱', '公司名稱'], industry: ['產業別']
  },
  {
    label: '公開資訊觀測站 上市公司基本資料 CSV',
    type: 'csv',
    url: 'https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv',
    code: ['公司代號'], name: ['公司簡稱', '公司名稱'], industry: ['產業別']
  }
];

var OTC_SOURCES = [
  {
    label: '公開資訊觀測站 上櫃公司基本資料 CSV',
    type: 'csv',
    url: 'https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv',
    code: ['公司代號'], name: ['公司簡稱', '公司名稱'], industry: ['產業別']
  },
  {
    label: '櫃買 OpenAPI 本益比表',
    type: 'json',
    url: 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis',
    code: ['SecuritiesCompanyCode', 'Code'],
    name: ['CompanyName', 'CompanyAbbreviation', 'Name'],
    industry: ['SecuritiesIndustryCode']
  },
  {
    label: '櫃買 OpenAPI 上櫃公司基本資料',
    type: 'json',
    url: 'https://www.tpex.org.tw/openapi/v1/opendata_t187ap03_O',
    code: ['SecuritiesCompanyCode', '公司代號'],
    name: ['CompanyAbbreviation', 'CompanyName', '公司簡稱'],
    industry: ['SecuritiesIndustryCode', '產業別']
  }
];

/** 依序嘗試來源，回傳 { rows, label }。全部失敗回 null。 */
function trySources_(sources, market) {
  for (var i = 0; i < sources.length; i++) {
    var s = sources[i];
    try {
      var raw = (s.type === 'csv') ? fetchCsv_(s.url) : fetchJson_(s.url);
      if (!raw || !raw.length) { throw new Error('回傳 0 筆'); }

      var out = [];
      raw.forEach(function (r) {
        var c = pickField_(r, s.code);
        var n = pickField_(r, s.name);
        if (/^(?:00981A|\d{4,6})$/.test(c) && n) {
          out.push({ code: c, name: n, market: market, industry: pickField_(r, s.industry) });
        }
      });

      if (!out.length) { throw new Error('解析後 0 筆，欄位名稱可能改了'); }
      Logger.log('  ' + market + '：' + s.label + ' 成功，' + out.length + ' 檔');
      return { rows: out, label: s.label };
    } catch (e) {
      Logger.log('  ' + market + '：' + s.label + ' 失敗（' + e.message + '），換下一個來源');
    }
  }
  return null;
}

/* ================================================================== *
 * 一、股票對照表
 * 代號與名稱一對一。來源是證交所與櫃買中心的公開清單，不是模型的記憶。
 * ================================================================== */

function rebuildCodeMapJob() {
  Logger.log('開始建立股票對照表');

  var listed = trySources_(LISTED_SOURCES, '上市');
  var otc = trySources_(OTC_SOURCES, '上櫃');

  if (!listed && !otc) {
    throw new Error('上市與上櫃的所有來源都失敗，對照表未更動。請執行 probeEndpoints() 看每個端點的實際狀況。');
  }

  var map = {};
  [listed, otc].forEach(function (src) {
    if (!src) { return; }
    src.rows.forEach(function (r) {
      map[r.code] = { name: r.name, market: r.market, industry: r.industry };
    });
  });

  var codes = Object.keys(map);
  var now = todayStr_();
  var rows = codes.sort().map(function (c) {
    return [c, map[c].name, map[c].market, map[c].industry, now];
  });

  withLock_(function () {
    var sh = getSheet_('股票對照表');
    sh.clearContents();
    sh.getRange(1, 1, 1, 5).setValues([['代號', '名稱', '市場', '產業', '更新時間']]);
    sh.getRange(2, 1, rows.length, 5).setValues(rows);
  });

  CACHE.remove('codemap-v6');

  Logger.log('');
  Logger.log('股票對照表更新完成：共 ' + rows.length + ' 檔');
  if (!listed) { Logger.log('警告：上市清單全部來源都失敗，對照表只有上櫃的部分。'); }
  if (!otc) { Logger.log('警告：上櫃清單全部來源都失敗，對照表只有上市的部分。'); }
  return rows.length;
}

/**
 * 端點健檢。任何一個對外來源掛掉時執行這一支，
 * 它會逐一測試並印出每個端點的實際回應，讓你知道是誰死了。
 */
function probeEndpoints() {
  var out = [];

  function probe(label, url, type) {
    try {
      var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true, followRedirects: true });
      var code = res.getResponseCode();
      var text = res.getContentText();
      var head = text.slice(0, 120).replace(/\s+/g, ' ').trim();

      if (code !== 200) { out.push('  ' + label + '\n    HTTP ' + code + '　<<< 失效'); return; }
      if (head.charAt(0) === '<') {
        out.push('  ' + label + '\n    回傳 HTML 網頁　<<< 端點已改版或失效\n    開頭：' + head.slice(0, 60));
        return;
      }
      var n;
      if (type === 'csv') {
        n = Utilities.parseCsv(text).length - 1;
      } else {
        var j = JSON.parse(text);
        // STOCK_DAY 回傳的是物件不是陣列，資料在 .data 裡。
        // 先前直接取 .length 所以印出 undefined，那是探針寫錯不是端點有問題。
        n = Array.isArray(j) ? j.length
          : (j.data && j.data.length) ? j.data.length
          : (j.aaData && j.aaData.length) ? j.aaData.length
          : Object.keys(j).length + ' 個欄位';
      }
      out.push('  ' + label + '\n    正常，' + n + ' 筆\n    開頭：' + head.slice(0, 80));
    } catch (e) {
      out.push('  ' + label + '\n    例外：' + e.message + '　<<< 有問題');
    }
  }

  out.push('=== 上市代號名稱來源 ===');
  LISTED_SOURCES.forEach(function (s) { probe(s.label, s.url, s.type); });

  out.push('');
  out.push('=== 上櫃代號名稱來源 ===');
  OTC_SOURCES.forEach(function (s) { probe(s.label, s.url, s.type); });

  out.push('');
  out.push('=== 估值與日K ===');
  probe('證交所 上市本益比', 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL', 'json');
  probe('櫃買 上櫃本益比', 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis', 'json');
  probe('證交所 上市月日K（以 2330 測）',
        'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=20260701&stockNo=2330&response=json', 'json');
  out.push('  櫃買 上櫃月日K　舊站已於 2025/05/31 停用，不再使用。上櫃日K 走 Fugle。');

  out.push('');
  out.push('=== Fugle ===');
  out.push(hasFugle_() ? '  FUGLE_API_KEY 已設定（內容不顯示）' : '  FUGLE_API_KEY 尚未設定');
  if (hasFugle_()) {
    try {
      var q = fugleQuote_('2330');
      out.push('  即時報價（2330）：' + (q ? '正常，現價 ' + q.last : '回傳空值'));
    } catch (e) {
      out.push('  即時報價（2330）：失敗（' + e.message + '）');
    }
    try {
      var h = fugleHistorical_('3105', '2026-06-01', '2026-07-17');
      out.push('  歷史日K（3105 穩懋，上櫃）：' + (h.length ? '正常，' + h.length + ' 根' : '回傳 0 根'));
    } catch (e) {
      out.push('  歷史日K（3105 穩懋，上櫃）：失敗（' + e.message + '）　<<< 上櫃日K 就靠這條');
    }
    try {
      var h6 = fugleHourly_('2330');
      out.push('  當日 60 分 K（2330）：' + (h6.length ? '正常，' + h6.length + ' 根' : '回傳 0 根，可能是非交易時段'));
    } catch (e) {
      out.push('  當日 60 分 K（2330）：失敗（' + e.message + '）');
    }
  }

  out.push('');
  out.push('=== FinMind ===');
  var fmToken = (typeof getFinMindToken_ === 'function') ? getFinMindToken_() : '';
  out.push(fmToken ? '  FINMIND_API_TOKEN 已設定（內容不顯示）' : '  FINMIND_API_TOKEN 尚未設定（可至 finmindtrade.com 免費註冊申請，每小時 600 次請求）');
  try {
    var probeUrl = 'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=2330&start_date=2026-09-18&end_date=2026-09-18' +
                   (fmToken ? ('&token=' + encodeURIComponent(fmToken)) : '');
    var fmRes = UrlFetchApp.fetch(probeUrl, { muteHttpExceptions: true });
    var fmCode = fmRes.getResponseCode();
    var fmText = fmRes.getContentText() || '';
    if (fmCode === 200) {
      var fmObj = JSON.parse(fmText);
      out.push('  日K探針（2330）：正常，' + ((fmObj.data && fmObj.data.length) ? fmObj.data.length + ' 筆' : '回傳 0 筆（' + (fmObj.msg || '') + '）'));
    } else {
      out.push('  日K探針（2330）：HTTP ' + fmCode + '，內容：' + fmText.slice(0, 80));
    }
  } catch (fmErr) {
    out.push('  日K探針（2330）：例外 ' + fmErr.message);
  }

  Logger.log(out.join('\n'));
  return out.join('\n');
}

/**
 * 詳細診斷單一股票的各 API 抓取情況（Fugle、FinMind、TWSE）。
 * 可在 Apps Script 編輯器直接執行 debugDailyKFetch('2330')，
 * 或透過網址 ?action=debugDailyK&code=2330 呼叫。
 */
function debugDailyKFetch(code) {
  code = String(code || '2330').trim();
  var logs = [];
  logs.push('=== 診斷日K抓取：' + code + ' ===');
  var today = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
  var from = Utilities.formatDate(new Date(Date.now() - 30 * 86400000), TZ, 'yyyy/MM/dd');
  var to = today;

  // 1. 檢查 Fugle
  logs.push('\n[1] Fugle:');
  var fugleKey = (typeof getFugleKey_ === 'function') ? getFugleKey_() : '';
  if (!fugleKey) {
    logs.push('  尚未設定 FUGLE_API_KEY');
  } else {
    try {
      var fq = fugleQuote_(code);
      logs.push('  即時報價：成功，現價 ' + (fq ? fq.last : 'null'));
    } catch (e) {
      logs.push('  即時報價：失敗（' + e.message + '）');
    }
    try {
      var fh = fugleHistorical_(code, from, to);
      logs.push('  歷史日K：成功，取得 ' + fh.length + ' 根 K 線');
    } catch (e) {
      logs.push('  歷史日K：失敗（' + e.message + '）');
    }
  }

  // 2. 檢查 FinMind
  logs.push('\n[2] FinMind:');
  var fmToken = (typeof getFinMindToken_ === 'function') ? getFinMindToken_() : '';
  logs.push('  Token: ' + (fmToken ? '已設定（' + fmToken.slice(0, 6) + '...）' : '未設定（使用公開限制配額）'));
  try {
    var fmDet = fetchFinmindDailyKDetailed_(code, from, to);
    if (fmDet.rows.length) {
      logs.push('  歷史日K：成功！取得 ' + fmDet.rows.length + ' 根 K 線（最近一根：' + JSON.stringify(fmDet.rows[fmDet.rows.length - 1]) + '）');
    } else {
      logs.push('  歷史日K：回傳 0 根，HTTP ' + fmDet.httpCode + '，原因：' + (fmDet.error || '無資料'));
    }
  } catch (e) {
    logs.push('  歷史日K：例外（' + e.message + '）');
  }

  // 3. 檢查 證交所 TWSE
  logs.push('\n[3] 證交所 (TWSE):');
  var ym = today.slice(0, 7);
  var twDet = fetchTwseMonthDetailed_(code, ym);
  if (twDet.rows.length) {
    logs.push('  當月日K：成功，取得 ' + twDet.rows.length + ' 根 K 線');
  } else {
    logs.push('  當月日K：取得 0 根，原因：' + (twDet.error || '查無資料/連線問題'));
  }

  var output = logs.join('\n');
  Logger.log(output);
  return output;
}

/** 讀對照表。空的時候自動先建一次，不要讓呼叫端莫名其妙地炸。 */
function loadCodeMap_() {
  var hit = CACHE.get('codemap-v6');
  if (hit) { return JSON.parse(hit); }

  var rows = readSheetObjects_('股票對照表');

  if (!rows.length) {
    Logger.log('股票對照表是空的，自動建立一次');
    try {
      rebuildCodeMapJob();
      rows = readSheetObjects_('股票對照表');
    } catch (e) {
      Logger.log('自動建立失敗：' + e.message);
      return { byCode: {}, byName: {} };
    }
  }

  var byCode = {}, byName = {};
  rows.forEach(function (r) {
    var c = String(r['代號']).trim();
    var n = String(r['名稱']).trim();
    if (!c || !n) { return; }
    byCode[c] = { name: n, market: r['市場'] || '', industry: r['產業'] || '' };
    byName[n] = c;
  });

  byCode['00981A'] = {name:'主動統一台股增長',market:'上市',industry:'ETF'};
  byName['主動統一台股增長'] = '00981A';
  var out = { byCode: byCode, byName: byName };
  var payload = JSON.stringify(out);
  if (payload.length < 95000) { CACHE.put('codemap-v6', payload, 21600); }
  return out;
}

/**
 * 對照表是不是上市與上櫃都載到了。
 *
 * 只有兩邊都在的時候，才敢說「這個代號不在表裡就是不存在」。
 * 某一邊的來源掛掉時那句話會冤枉一半的股票——先前就發生過：
 * 上櫃清單掛了，美琪瑪（4721）這種本來正確的資料因為不在殘缺的表裡，
 * 被模糊比對改成美利達 9914，把對的改成錯的。
 *
 * 用「有沒有這兩種市場」判斷，不用筆數判斷。筆數會隨著上市家數變動，
 * 訂一個門檻遲早會失準；市場欄位不會。
 */
function codeMapFull_(map) {
  if (!map || !map.byCode) { return false; }
  var listed = false, otc = false;
  for (var c in map.byCode) {
    var mk = String((map.byCode[c] || {}).market || '');
    if (mk === '上市') { listed = true; } else if (mk === '上櫃') { otc = true; }
    if (listed && otc) { return true; }
  }
  return false;
}

/* ================================================================== *
 * 二、代號修復
 *
 * 這一支處理你點出的兩種情形：
 *   有代號但名稱不對或空白  ->  用代號查對照表，補正名稱
 *   有名稱但代號待確認      ->  用名稱查對照表，補上代號
 *
 * 同音錯字（四星科 對 事欣科）修不了。那需要拼音比對，
 * 已經做在 pipeline.py 的 resolve_code，GAS 這邊沒有拼音函式庫。
 * 這種只能重跑 backfill 讓上游修，或人工填。
 * ================================================================== */

/** 字面相似度。GAS 沒有 difflib，這裡用最長共同子序列長度除以較長者長度。 */
function similarity_(a, b) {
  a = String(a); b = String(b);
  if (!a || !b) { return 0; }
  if (a === b) { return 1; }
  var m = a.length, n = b.length;
  var prev = new Array(n + 1).fill(0), cur;
  for (var i = 1; i <= m; i++) {
    cur = new Array(n + 1).fill(0);
    for (var j = 1; j <= n; j++) {
      cur[j] = (a.charAt(i - 1) === b.charAt(j - 1)) ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev = cur;
  }
  return prev[n] / Math.max(m, n);
}

var REPAIR_CUTOFF = 0.75;

function repairOne_(name, code, map) {
  name = String(name || '').trim();
  code = String(code || '').trim();

  // 代號合格，直接以對照表的正式名稱為準
  if (/^(?:00981A|\d{4,6})$/.test(code) && map.byCode[code]) {
    var official = map.byCode[code].name;
    return (official !== name)
      ? { code: code, name: official, changed: true, how: '依代號補正名稱' }
      : { code: code, name: name, changed: false, how: '' };
  }

  if (!name) { return { code: code, name: name, changed: false, how: '無名稱可比對' }; }

  // 名稱完全相同
  if (map.byName[name]) {
    return { code: map.byName[name], name: name, changed: true, how: '名稱完全相同' };
  }

  // 字面相似
  var bestC = '', bestS = 0;
  Object.keys(map.byCode).forEach(function (c) {
    var s = similarity_(name, map.byCode[c].name);
    if (s > bestS) { bestS = s; bestC = c; }
  });
  if (bestS >= REPAIR_CUTOFF) {
    return { code: bestC, name: map.byCode[bestC].name, changed: true,
             how: '字面相似 ' + bestS.toFixed(2) };
  }

  return { code: code || '代號待確認', name: name, changed: false,
           how: '無法確定（最高相似 ' + bestS.toFixed(2) + '）' };
}

/**
 * 列出所有代號待確認的項目，直接指出在試算表的哪一列。
 *
 * 存在理由：先前的 log 只說「代號待確認 4 檔」，卻沒說是哪四檔、
 * 在哪一列、該怎麼改。這支把路指到底。
 */
function listUnresolved() {
  var map = loadCodeMap_();
  var out = [];
  var total = 0;

  [{ sheet: '操作紀錄', nameCol: 2, codeCol: 3 },
   { sheet: '會員持股', nameCol: 2, codeCol: 3 }].forEach(function (cfg) {

    var values = getSheet_(cfg.sheet).getDataRange().getValues();
    var hits = [];

    for (var i = 1; i < values.length; i++) {
      var name = String(values[i][cfg.nameCol - 1] || '').trim();
      var code = String(values[i][cfg.codeCol - 1] || '').trim();
      if (!name) { continue; }
      if (/^(?:00981A|\d{4,6})$/.test(code)) { continue; }

      // 找出最接近的三個候選，讓人工判斷有依據
      var cands = [];
      Object.keys(map.byCode).forEach(function (c) {
        var s = similarity_(name, map.byCode[c].name);
        if (s > 0.2) { cands.push({ code: c, name: map.byCode[c].name, s: s }); }
      });
      cands.sort(function (a, b) { return b.s - a.s; });

      hits.push({
        row: i + 1, name: name, code: code,
        cands: cands.slice(0, 3)
      });
      total++;
    }

    if (!hits.length) { return; }

    out.push('');
    out.push('=== ' + cfg.sheet + ' ===');
    hits.forEach(function (h) {
      out.push('  第 ' + h.row + ' 列　「' + h.name + '」　目前代號：' + (h.code || '空白'));
      if (h.cands.length) {
        out.push('    最接近的候選：' + h.cands.map(function (c) {
          return c.name + ' ' + c.code + '（字面 ' + c.s.toFixed(2) + '）';
        }).join('、'));
      } else {
        out.push('    找不到任何相近的公司，這可能是語音辨識錯得太離譜，需要看影片確認');
      }
    });
  });

  if (!total) {
    Logger.log('沒有代號待確認的項目，全部都對上了。');
    return '沒有代號待確認的項目。';
  }

  var head = [
    '共 ' + total + ' 筆代號待確認。',
    '',
    '這些多半是語音同音錯字，例如「四星科」其實是「事欣科 4916」。',
    'GAS 只能做字面比對，修不了同音字，所以它們卡在這裡。',
    '',
    '三個選擇，任選一個：',
    '',
    '  一、到 GitHub 重跑（推薦，會自動修）',
    '      Actions -> daily-transcript-pipeline -> Run workflow -> backfill 填 true',
    '      pipeline.py 有 pypinyin 拼音比對，四星科 對 事欣科 相似度 0.88，抓得到。',
    '      跑完回來執行 rebuildHoldingsTrackerJob()。',
    '',
    '  二、直接在試算表手動改（最快）',
    '      打開「操作紀錄」分頁，照下面的列號把「代號」欄填成正確的四位數字，',
    '      「股票名稱」欄也一併改成正式簡稱。改完執行 rebuildHoldingsTrackerJob()。',
    '',
    '  三、不管它',
    '      這些會照樣列在持股追蹤裡，只是報酬欄顯示「代號待確認，無法取價」。',
    '      不影響其他標的。'
  ];

  var text = head.concat(out).join('\n');
  Logger.log(text);
  return text;
}


// 產業、族群、概念、集團、技術名詞清單。這些不是個股，卻常被寫成「代號待確認」
// 留在資料裡。用 purgeIndustryRows() 可以立刻把它們從操作紀錄與會員持股移除。
var INDUSTRY_NAMES = [
  '記憶體', '面板', '被動元件', '散熱', '重電', '軍工', '無人機', '機器人',
  '矽光子', '光通訊', '高速傳輸', '散熱模組', '伺服器', '半導體', '封測',
  '晶圓代工', 'IC設計', 'IC 設計', '第三代半導體', '碳化矽', '氮化鎵',
  '銅箔基板', 'PCB', 'ABF', 'CoWoS', 'HBM', 'AI', 'AI伺服器', 'AI 伺服器',
  '電動車', '儲能', '太陽能', '風電', '生技', '重電股', '航太', '資安',
  '元宇宙', '低軌衛星', '衛星', '折疊機', '先進封裝', '玻璃基板',
  '權值股', '中小型股', '傳產', '電子股', '金融股', '航運股', '生技股', '觀光股',
  '台塑集團', '遠東集團', '鴻海集團', '大盤', '加權指數', '台股', '美股', '期貨', '選擇權'
];

function isIndustryName_(name) {
  var n = String(name || '').replace(/\s+/g, '').replace(/載版/g, '載板');
  if (!n) { return false; }
  if (INDUSTRY_NAMES.indexOf(n) >= 0) { return true; }
  if (/(集團|族群|概念股|概念|類股|板塊|產業|供應鏈|相關股)$/.test(n)) { return true; }
  if (n.length >= 3 && /股$/.test(n)) { return true; }
  if (!/[\u4e00-\u9fff]/.test(n)) { return true; }   // 純英數技術縮寫
  return false;
}

/**
 * 立刻把產業/族群名稱（如記憶體、台塑集團、ABF）從操作紀錄與會員持股整列移除。
 * 手動執行即可，不需要跑 pipeline。移除後記得跑 rebuildHoldingsTrackerJob()。
 */
function purgeIndustryRows() {
  var total = 0;
  ['操作紀錄', '會員持股'].forEach(function (sheet) {
    var sh = getSheet_(sheet);
    var vals = sh.getDataRange().getValues();
    if (vals.length < 2) { return; }
    var nm = vals[0].indexOf('股票名稱');
    if (nm < 0) { nm = 1; }
    var removed = 0;
    withLock_(function () {
      for (var i = vals.length - 1; i >= 1; i--) {
        if (isIndustryName_(vals[i][nm])) { sh.deleteRow(i + 1); removed++; }
      }
    });
    total += removed;
    Logger.log(sheet + ' 移除 ' + removed + ' 列產業/族群');
  });
  CACHE.remove('tracker');
  Logger.log('共移除 ' + total + ' 列。請接著執行 rebuildHoldingsTrackerJob()。');
  return total;
}


/**
 * 一鍵刷新網站既有內容。
 *
 * 用途：當你不想等下一個交易日、想「立刻」把網站上顯示的內容用最新資料與
 * 最新邏輯重算一遍時，手動執行這一個函式就好，不必逐一去按每個重算工作。
 *
 * 它「不抓新影片、不呼叫任何 AI」，只把試算表裡「已經有的」資料重新算成
 * 網站要顯示的衍生內容。做的事依正確順序如下：
 *   1. purgeIndustryRows      清掉記憶體、AB載板等產業/族群列
 *   2. repairCodesJob         把股票名稱重跑一次代號比對
 *   3. rebuildFundamentalsJob 更新基本面快取
 *   4. backfillDailyKJob      補齊日K快取（個股圖表、進出場價要用；沒補完會自動續跑）
 *   5. rebuildHoldingsTrackerJob 重算持股追蹤與逐日說明（首頁與追蹤頁的來源）
 *   6. snapshotPerformanceJob 記錄一筆當下的整體績效
 *
 * 順序有意義：先把資料清乾淨、代號補好、日K備妥，最後才重算追蹤與績效，
 * 這樣算出來的進出場價、報酬才會用到最新的快取。
 *
 * 每一步都各自 try，單一步驟失敗不會中斷整體，最後回傳每一步的結果摘要。
 * 執行位置：Apps Script 編輯器選這個函式按執行，或從網站的管理入口呼叫。
 */
function refreshSiteNow() {
  var t0 = Date.now();
  var steps = [
    ['清除產業/族群列', function () { return purgeIndustryRows() + ' 列'; }],
    ['重跑代號比對',   function () { repairCodesJob(); return 'ok'; }],
    ['更新基本面快取', function () { rebuildFundamentalsJob(); return 'ok'; }],
    ['補齊日K快取',    function () {
      // 同一次執行後面還有兩件事，只給 2 分鐘；沒補完會自動排續跑（backfillDailyKStatus() 看進度）
      return backfillDailyKJob({ deadline: Date.now() + 2 * 60 * 1000, source: 'refreshSiteNow' }).note;
    }],
    ['重算持股追蹤',   function () { rebuildHoldingsTrackerJob(); return 'ok'; }],
    ['記錄當下績效',   function () { snapshotPerformanceJob(); return 'ok'; }]
  ];

  var report = [];
  steps.forEach(function (pair) {
    var name = pair[0], fn = pair[1];
    try {
      var r = fn();
      report.push('✓ ' + name + '：' + r);
      Logger.log('✓ ' + name + '：' + r);
    } catch (e) {
      report.push('✗ ' + name + '：' + String(e).slice(0, 120));
      Logger.log('✗ ' + name + ' 失敗：' + e);
    }
  });

  var secs = Math.round((Date.now() - t0) / 1000);
  var summary = '網站內容已刷新（耗時約 ' + secs + ' 秒）\n' + report.join('\n');
  Logger.log(summary);

  // 若有設定通知信箱，寄一封結果通知，讓你知道刷新完成與各步驟狀態。
  try {
    var to = (typeof statusEmails_ === 'function') ? statusEmails_() : [];
    if (to && to.length) {
      MailApp.sendEmail({
        to: to.join(','),
        subject: '網站內容已手動刷新',
        body: summary
      });
    }
  } catch (e) { /* 通知失敗不影響刷新本身 */ }

  return summary;
}


function repairCodesJob() {
  var map = loadCodeMap_();
  if (!Object.keys(map.byCode).length) {
    Logger.log('股票對照表是空的，請先執行 rebuildCodeMapJob()');
    return;
  }

  var stat = { fixed: 0, still: 0, ok: 0 };
  var unresolved = {};

  [{ sheet: '操作紀錄', nameCol: 2, codeCol: 3 },
   { sheet: '會員持股', nameCol: 2, codeCol: 3 }].forEach(function (cfg) {

    var sh = getSheet_(cfg.sheet);
    var values = sh.getDataRange().getValues();
    if (values.length < 2) { return; }

    var names = [], codes = [], dirty = false;
    for (var i = 1; i < values.length; i++) {
      var oldName = String(values[i][cfg.nameCol - 1] || '');
      var oldCode = String(values[i][cfg.codeCol - 1] || '');
      var r = repairOne_(oldName, oldCode, map);

      names.push([r.name]);
      codes.push([r.code]);

      if (r.changed) {
        stat.fixed++; dirty = true;
        Logger.log('  ' + cfg.sheet + ' 第 ' + (i + 1) + ' 列　' +
                   oldName + '（' + oldCode + '）-> ' + r.name + '（' + r.code + '）　' + r.how);
      } else if (!/^(?:00981A|\d{4,6})$/.test(r.code)) {
        stat.still++;
        unresolved[oldName] = r.how;
      } else {
        stat.ok++;
      }
    }

    if (dirty) {
      withLock_(function () {
        sh.getRange(2, cfg.nameCol, names.length, 1).setValues(names);
        sh.getRange(2, cfg.codeCol, codes.length, 1).setValues(codes);
      });
    }
  });

  Logger.log('');
  Logger.log('代號修復完成：修正 ' + stat.fixed + ' 筆，本來就正確 ' + stat.ok + ' 筆，仍待確認 ' + stat.still + ' 筆');

  var names = Object.keys(unresolved);
  if (names.length) {
    Logger.log('');
    Logger.log('以下仍無法確定，多半是語音同音錯字。GAS 沒有拼音比對，');
    Logger.log('請在 GitHub 重跑一次 backfill（pipeline.py 有拼音比對），或人工填入代號：');
    names.slice(0, 20).forEach(function (n) { Logger.log('  ' + n + '　' + unresolved[n]); });
  }
  return stat;
}

/* ================================================================== *
 * 三、日K 落地快取
 *
 * 原本每次要用就向證交所逐月抓，而且視窗寫死 8 個月，
 * 從 2026/07 回推剛好停在 2025/12，所以更早的資料一片空白。
 * 現在改成抓一次就存進試算表，並把視窗拉到 24 個月。
 * 有時間預算，一次跑不完下次接著跑。
 * ================================================================== */

/** 快取裡已經有哪些 代號|年月 */
function cachedKeys_() {
  var rows = getSheet_('日K快取').getDataRange().getValues();
  var have = {};
  for (var i = 1; i < rows.length; i++) {
    var c = String(rows[i][0]).trim();
    var d = fmtDate_(rows[i][1]);
    if (c && d) { have[c + '|' + d.slice(0, 7)] = 1; }
  }
  return have;
}

/**
 * 補日K快取（可續跑）。
 *
 * 優先用 Fugle 的歷史行情：一檔一次請求就拿到完整一年，上市上櫃通吃。
 * 沒有 Fugle 金鑰才退回證交所逐月抓（抓不到上櫃）。
 *
 * 為什麼要改成可續跑
 * ------------------
 * 追蹤宇宙已經三百多檔。Fugle 歷史行情每分鐘 60 次，光是排隊就要五分鐘以上，
 * 單次執行（6 分鐘、扣掉餘裕 4.5 分鐘）不可能跑完。舊版的問題是：
 *   1. 每次都從第一檔重來，時間到就停，排在後面的代號永遠輪不到；
 *   2. 資料等全部抓完才一次寫回，中途被砍就全部白抓；
 *   3. 每天 14:35 的排程也一樣，後半段的代號其實從來沒更新過。
 *
 * 現在的做法：
 *   一「輪」＝把整個追蹤宇宙（代號排序後）從頭到尾補一遍，區間在開輪時固定。
 *   進度記在指令碼屬性 dailyKBackfillState：做到哪一檔（lastCode）、成功與失敗數。
 *   每補完 DK_FLUSH_CODES 檔就寫回試算表並推進進度——先寫資料、後記進度，
 *   所以被砍的最壞情況是那一批重抓一次，不會漏也不會記成已完成。
 *   時間到了就排一個一次性觸發器 backfillDailyKContinueJob，一分鐘後自動接著補，
 *   直到整輪完成才停。按一次 backfillDailyKJob() 就會自己跑完。
 *
 * 誰會推進同一輪：
 *   backfillDailyKJob()          編輯器手動、每日 14:35 排程
 *   backfillDailyKContinueJob()  上面兩者時間不夠時自動排的續跑
 *   backfillDailyKChunk_()       網站／GitHub 的 step=dailyk（每次約 45 秒一小批）
 *   全面重整的「補齊日K」步驟
 * 它們共用同一份進度，所以 GitHub 那輪沒補完，14:35 的排程會從停下的地方接著做。
 * 同一時間只准一個在寫（dailyKBackfillLease），另一個會等或回報「正在補」。
 *
 * 開新一輪還是接著做：
 *   上一輪還沒完成、而且是今天開的 → 接著做
 *   上一輪已完成，或是前一天開的     → 開新一輪（區間改用今天）
 *   續跑觸發器只接著做，不會自己開新一輪。
 *
 * 看進度：backfillDailyKStatus()。從頭重來：resetDailyKCursor()。
 */
var DK_STATE_PROP = 'dailyKBackfillState';
var DK_LEASE_PROP = 'dailyKBackfillLease';
var DK_CONTINUE_HANDLER = 'backfillDailyKContinueJob';
var DK_CONTINUE_DELAY_MS = 60 * 1000;
var DK_FLUSH_CODES = 25;        // 每幾檔寫回一次試算表並推進進度
var DK_MAX_RETRY = 3;           // 同一檔連續遇到暫時性錯誤幾次後，記失敗並跳過
var DK_MAX_ROUNDS = 80;         // 一輪最多續跑幾次，防止異常時無限排觸發器
var DK_HEAD = ['代號', '日期', '開', '高', '低', '收', '量'];

function dailyKState_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty(DK_STATE_PROP) || 'null'); }
  catch (e) { return null; }
}

function setDailyKState_(st) {
  PropertiesService.getScriptProperties().setProperty(DK_STATE_PROP, JSON.stringify(st));
}

/** 取得寫入權。拿不到回 null。waitMs 內每 5 秒再試一次。 */
function acquireDailyKLease_(untilMs, waitMs) {
  var props = PropertiesService.getScriptProperties();
  var token = Utilities.getUuid();
  var giveUp = Date.now() + (waitMs || 0);
  while (true) {
    var lock = LockService.getScriptLock();
    if (lock.tryLock(3000)) {
      try {
        var lease = null;
        try { lease = JSON.parse(props.getProperty(DK_LEASE_PROP) || 'null'); } catch (e) { lease = null; }
        if (!lease || !(lease.until > Date.now())) {
          props.setProperty(DK_LEASE_PROP, JSON.stringify({ token: token, until: untilMs }));
          return token;
        }
      } finally { lock.releaseLock(); }
    }
    if (Date.now() + 5000 > giveUp) { return null; }
    Utilities.sleep(5000);
  }
}

function releaseDailyKLease_(token) {
  var props = PropertiesService.getScriptProperties();
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) { return; }          // 拿不到就讓租約自然到期
  try {
    var lease = null;
    try { lease = JSON.parse(props.getProperty(DK_LEASE_PROP) || 'null'); } catch (e) { lease = null; }
    if (lease && lease.token === token) { props.deleteProperty(DK_LEASE_PROP); }
  } finally { lock.releaseLock(); }
}

/** 本輪要補的日期區間：今天往回 KLINE_MONTHS 個月再加一天。

    加一天是因為富果歷史行情單次查詢要「小於 1 年」，恰好一年會回 400（2026/09/16 v40 查證文件）。
    差這一天的那一根本來就在一年前，K 線與進出場價都用不到；換來的是每一檔一次請求就拿完。 */
function dailyKRange_() {
  var now = new Date();
  var to = Utilities.formatDate(now, TZ, 'yyyy-MM-dd');
  var d = new Date(now.getTime());
  d.setMonth(d.getMonth() - KLINE_MONTHS);
  d.setDate(d.getDate() + 1);
  return { from: Utilities.formatDate(d, TZ, 'yyyy-MM-dd'), to: to };
}

/* 單次執行的時間（2026/09/16 v40）。

   Apps Script 每一次執行最多 6 分鐘，時間到會被直接中止——這是平台上限，程式取消不了。
   先前補日K固定只抓 4 分鐘（4.5 分鐘預算再扣 30 秒），剩下將近兩分鐘沒有用到。
   現在以「這一次執行真正開始的時間」算到 6 分鐘，只保留收尾需要的時間：
   基本 45 秒（最後一批寫回試算表、記進度、排續跑），再加上這次實際量到最慢那一次寫入耗時的兩倍。
   抓取速度只受富果每分鐘的次數限制（見 Quoteservice.gs 的 fugleHistPace_），做不完就由續跑觸發器接著做。 */
var GAS_EXEC_LIMIT_MS_ = 6 * 60 * 1000;
var DK_RESERVE_MS_ = 45 * 1000;
var DK_FLUSH_EVERY_MS_ = 60 * 1000;   // 除了每 25 檔，最多一分鐘也寫回一次，萬一被中止最多重抓一分鐘
var EXEC_STARTED_AT_ = Date.now();    // 每一次執行都會重新載入程式碼，所以這就是這次執行的開始時間

function dkExecStart_(now) {
  var age = now - EXEC_STARTED_AT_;
  // 同一次執行裡不可能超過 6 分鐘；超過代表不是同一次（例如測試環境重複呼叫），以現在為準
  return (age >= 0 && age < GAS_EXEC_LIMIT_MS_) ? EXEC_STARTED_AT_ : now;
}

/*
 * 錯誤分三種，處理方式不同：
 *   fatal      金鑰無效（401/403）、今日 UrlFetch 額度用完 → 整輪停下，不排續跑，後面每一檔都會一樣失敗
 *   transient  429、5xx、逾時、網路 → 這一檔先不推進，下一次續跑再試，連續 DK_MAX_RETRY 次才記失敗
 *   其他       404、回傳 0 根、400 → 這一檔記失敗，繼續下一檔
 */
function dailyKErrorKind_(e) {
  var code = e && e.httpCode;
  var msg = String(e && e.message || e);
  if (/too many times|Service invoked too many|額度/i.test(msg)) { return 'fatal'; }
  if (code === 401 || code === 403 || /尚未設定 FUGLE_API_KEY|FinMind HTTP 40[13]/.test(msg)) { return 'fatal'; }
  if (code === 429 || (code >= 500 && code < 600) || /FinMind HTTP (429|5)/.test(msg)) { return 'transient'; }
  if (!code && !/回傳 0 根|查無|分段次數異常/.test(msg)) { return 'transient'; }
  return 'permanent';
}

/** 證交所路線：一檔要抓哪些月份。最近兩個月一律重抓（當月還在長、上個月可能只抓到一半），更早的缺才抓。 */
function dailyKTwseFetch_(code, from, to, have, deadline, repair) {
  var months = [];
  var y = Number(from.slice(0, 4)), m = Number(from.slice(5, 7));
  var ty = Number(to.slice(0, 4)), tm = Number(to.slice(5, 7));
  while (y < ty || (y === ty && m <= tm)) {
    months.push(y + '/' + (m < 10 ? '0' : '') + m);
    m++; if (m > 12) { m = 1; y++; }
  }
  var previous=getCachedDailyK(code),needed={};
  var today=Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd');
  missingKDateRanges_(previous,from,to,today,Number(Utilities.formatDate(new Date(),TZ,'HHmm'))>=1630).forEach(function(r){
    var d=new Date(r.from+'T00:00:00Z');while(d.toISOString().slice(0,10)<=r.to){needed[d.toISOString().slice(0,7).replace('-','/')]=true;d.setUTCDate(d.getUTCDate()+1);}
  });
  var out = { rows: [], months: {}, complete: true, lastError: '' };
  for (var i = 0; i < months.length; i++) {
    var ym = months[i];
    if (!repair&&!needed[ym]) { continue; }
    if (Date.now() > deadline) { out.complete = false; return out; }
    var got = fetchTwseMonth_(code, ym);
    if (got && !got.length && got.error) {
      out.lastError = got.error;
    }
    // fetchTwseMonth_ 失敗時回空陣列。只有真的拿到資料的月份才列入「要替換」，
    // 否則一次連線失敗就會把快取裡好好的那個月刪掉。
    if (got.length) {
      out.months[ym] = 1;
      got.forEach(function (r) { out.rows.push(r); });
    }
    Utilities.sleep(600);
  }
  if(!repair){out.rows=mergeDailyK_(previous,out.rows).filter(function(r){return out.months[r.date.slice(0,7)];});}
  return out;
}

/**
 * 把一批結果寫進日K快取。先附加新列、再刪同代號的舊列（由下往上），全程持鎖。
 * items: [{ code, rows:[{date,open,high,low,close,volume}], months: null | {'yyyy/MM':1} }]
 * months 為 null 表示整檔替換（Fugle 一次拿完整區間）；有值則只替換那些月份（證交所逐月）。
 */
function writeDailyKRows_(items) {
  if (!items.length) { return; }
  withLock_(function () {
    var sh = getSheet_('日K快取');
    var last = sh.getLastRow();
    if (last < 1) {
      sh.getRange(1, 1, 1, 7).setValues([DK_HEAD]);
      last = 1;
    }
    var head = sh.getRange(1, 1, 1, 7).getValues()[0].map(function (h) { return String(h).trim(); });
    if (head.join('|') !== DK_HEAD.join('|')) {
      throw new Error('日K快取的表頭不是「' + DK_HEAD.join('、') + '」（實際：' + head.join('、') +
                      '），為避免欄位錯位不寫入');
    }

    var byCode = {};
    items.forEach(function (it) { byCode[it.code] = it; });
    var remove = [];
    if (last >= 2) {
      var vals = sh.getRange(2, 1, last - 1, 2).getValues();
      for (var i = 0; i < vals.length; i++) {
        var it = byCode[String(vals[i][0]).trim()];
        if (!it) { continue; }
        if (it.months) {
          var d = fmtDate_(vals[i][1]);
          if (!d || !it.months[d.slice(0, 7)]) { continue; }
        }
        remove.push(i + 2);
      }
    }

    var out = [];
    items.forEach(function (it) {
      it.rows.forEach(function (r) {
        out.push([it.code, r.date, r.open, r.high, r.low, r.close, r.volume]);
      });
    });
    // 先附加：新列在所有舊列之後，刪舊列時列號不會位移；
    // 也避免「整張只剩表頭」時 deleteRows 不允許刪光所有未凍結列。
    var first = last + 1;
    var need = first + out.length - 1 - sh.getMaxRows();
    if (need > 0) { sh.insertRowsAfter(sh.getMaxRows(), need); }
    sh.getRange(first, 1, out.length, 7).setValues(out);

    var blocks = [];
    remove.forEach(function (row) {
      var b = blocks.length ? blocks[blocks.length - 1] : null;
      if (b && row === b.start + b.count) { b.count++; }
      else { blocks.push({ start: row, count: 1 }); }
    });
    blocks.reverse().forEach(function (b) { sh.deleteRows(b.start, b.count); });
  });
  _DK_INDEX = null;
  CACHE.remove('kcache_meta');
  kcDrop_('dk_', items.map(function (it) { return it.code; }));
  kcDrop_('dk2_', items.map(function (it) { return it.code; }));
}

/**
 * 補一段。所有入口都走這裡。
 * opts:
 *   deadline      抓取迴圈的截止時間（毫秒時間戳）。預設本次執行開始後 BUDGET_MS 再扣 30 秒寫入時間
 *   waitMs        別人正在補時最多等多久（預設不等）
 *   continueOnly  只接著做未完成的一輪，不開新一輪（續跑觸發器用）
 *   source        記在進度裡，看得出這一輪是誰開的
 * 回傳 { done, busy, fatal, stop, processed, total, ok, failed, skipped, note, failedList }
 */
function dailyKBackfillRound_(opts) {
  opts = opts || {};
  var t0 = Date.now();
  // 呼叫端給了截止時間（網站分批 45 秒、refreshSiteNow 2 分鐘）就照它；否則用滿這一次執行的 6 分鐘
  var deadline = typeof opts.deadline === 'number' ? opts.deadline
               : dkExecStart_(t0) + GAS_EXEC_LIMIT_MS_ - DK_RESERVE_MS_;

  var token = acquireDailyKLease_(deadline + 3 * 60 * 1000, opts.waitMs || 0);
  if (!token) {
    var cur = dailyKState_();
    var p = cur ? dailyKProgress_(cur) : { processed: 0, total: 0 };
    return { done: false, busy: true, fatal: false, stop: 'busy', processed: p.processed, total: p.total,
             ok: 0, failed: 0, skipped: 0, failedList: [],
             note: '另一個補日K正在寫入（進度 ' + p.processed + '/' + p.total + '），這次不重複執行' };
  }

  try {
    var codes = trackedCodes_();
    codes.sort();
    var today = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
    var st = dailyKState_();
    var unfinished = st && !st.finishedAt;

    if (opts.continueOnly && !unfinished) {
      return dailyKResult_(st, codes, { done: true, stop: 'none', note: '沒有進行中的補日K，不需續跑' });
    }
    // 舊版把簡訊解析狀態當成日K的前置條件，重試三次後永久封住續跑。
    // 兩者寫不同資料，解除舊狀態後照游標續補，不重抓已完成代號。
    if (st && st.abort && st.smsRetryAbort) {
      st.abort = false;
      st.smsRetryAbort = 0;
      st.lastError = '';
      setDailyKState_(st);
    }
    if (!codes.length) {
      return dailyKResult_(st, codes, { done: true, stop: 'none', note: '沒有需要快取的代號' });
    }
    if (!unfinished || (!opts.continueOnly && st.day !== today)) {
      if (unfinished) {
        Logger.log('前一輪（' + st.day + ' 開始）停在 ' + (st.lastCode || '開頭') + '，已過日，改開新一輪。');
      }
      var range = dailyKRange_();
      st = { runId: Utilities.getUuid().slice(0, 8), day: today, from: range.from, to: range.to,
             lastCode: '', ok: 0, failed: 0, skipped: 0, failedList: [], rounds: 0,
             retryCode: '', retryN: 0, source: opts.source || '', startedAt: nowStampDk_(),
             updatedAt: '', finishedAt: '', lastError: '' };
      Logger.log('開始新一輪補日K：' + codes.length + ' 檔，區間 ' + st.from + ' ～ ' + st.to +
                 (st.source ? '（' + st.source + '）' : ''));
    }

    st.rounds = (st.rounds || 0) + 1;
    if (st.rounds > DK_MAX_ROUNDS) {
      st.lastError = '這一輪已續跑 ' + DK_MAX_ROUNDS + ' 次仍未完成，先停下；請看 backfillDailyKStatus() 的失敗原因，或 resetDailyKCursor() 重來';
      setDailyKState_(st);
      return dailyKResult_(st, codes, { done: false, fatal: true, stop: 'rounds', note: st.lastError });
    }
    st.lastError = '';

    var useFugle = hasFugle_();
    var useFinMind = !useFugle && (typeof hasFinMind_ === 'function') && hasFinMind_();
    var ghosts = dailyKGhosts_();
    var cmap = {}, have = null;
    if (!useFugle && !useFinMind) {
      try { cmap = (loadCodeMap_() || {}).byCode || {}; } catch (e) { cmap = {}; }
      have = cachedKeys_();
    }

    var pending = codes.filter(function (c) { return c > st.lastCode; });
    var batch = [], stop = '', roundOk = 0, roundFailed = 0, roundSkipped = 0;
    var flushCost = 0, lastFlushAt = Date.now();

    var flush = function () {
      if (!batch.length) { return; }
      var f0 = Date.now();
      writeDailyKRows_(batch.filter(function (it) { return it.rows && it.rows.length; }));
      batch.forEach(function (it) {
        if (it.rows && it.rows.length) { st.ok++; roundOk++; }
        else if (it.skip) { st.skipped++; roundSkipped++; }
        else {
          st.failed++; roundFailed++;
          if (st.failedList.length < 30) { st.failedList.push(it.code + '（' + (it.err || '0 根') + '）'); }
        }
        if (st.retryCode === it.code) { st.retryCode = ''; st.retryN = 0; }
        st.lastCode = it.code;
      });
      batch = [];
      st.updatedAt = nowStampDk_();
      setDailyKState_(st);
      lastFlushAt = Date.now();
      flushCost = Math.max(flushCost, lastFlushAt - f0);
    };

    for (var i = 0; i < pending.length; i++) {
      // 寫入越來越慢時（試算表變大），收尾保留的時間跟著加大；下一次請求前可能還要等一個控速間隔，也算進去
      if (Date.now() + (useFugle ? FUGLE_HIST_GAP_MS_ : 0) > deadline - 2 * flushCost) { stop = 'time'; break; }
      var code = pending[i];
      var item = { code: code, rows: null, months: null, err: '', skip: '' };

      if (ghosts[code]) {
        item.skip = '查無此股';
      } else if (!useFugle && !useFinMind && cmap[code] && cmap[code].market === '上櫃') {
        item.skip = '上櫃股需要 FUGLE_API_KEY 或 FINMIND_API_TOKEN';
      } else {
        try {
          if (useFugle) {
            var beforeRows=getCachedDailyK(code),repair=/repairDailyKCache/.test(st.source||'');
            var fresh=repair?fugleHistorical_(code,st.from,st.to):fetchMissingDailyK_(code,st.from,st.to,beforeRows,deadline);
            if(fresh.pending){
              if(fresh.length){writeDailyKRows_([{code:code,rows:mergeDailyK_(beforeRows,fresh),months:null}]);}
              stop='time';break; // 部分缺口已落地，但這個代號尚未完成，不能移動游標。
            }
            if(!fresh.length){item.skip='日K已齊或缺口暫無成交';}
            else{item.rows=repair?fresh:mergeDailyK_(beforeRows,fresh);}
          } else if (useFinMind) {
            var beforeRows=getCachedDailyK(code),repair=/repairDailyKCache/.test(st.source||'');
            var fresh=repair?fetchFinmindDailyK_(code,st.from,st.to):fetchMissingDailyKFinMind_(code,st.from,st.to,beforeRows,deadline);
            if(fresh.pending){
              if(fresh.length){writeDailyKRows_([{code:code,rows:mergeDailyK_(beforeRows,fresh),months:null}]);}
              stop='time';break;
            }
            if(!fresh.length){item.skip='日K已齊或缺口暫無成交';}
            else{item.rows=repair?fresh:mergeDailyK_(beforeRows,fresh);}
            Utilities.sleep(150);
          } else {
            var tw = dailyKTwseFetch_(code, st.from, st.to, have, deadline, /repairDailyKCache/.test(st.source||''));
            if (!tw.complete) { stop = 'time'; break; }   // 月份沒抓完不寫半套，下次整檔重來
            item.rows = tw.rows;
            item.months = tw.months;
            if (!item.rows.length && tw.lastError) {
              item.err = tw.lastError;
            }
          }
        } catch (e) {
          var kind = dailyKErrorKind_(e);
          var msg = String(e && e.message || e).slice(0, 60);
          if (kind === 'fatal') {
            stop = 'fatal';
            st.lastError = code + '：' + msg;
            break;
          }
          if (kind === 'transient') {
            if (st.retryCode === code) { st.retryN++; } else { st.retryCode = code; st.retryN = 1; }
            if (st.retryN < DK_MAX_RETRY) {
              stop = 'transient';
              st.lastError = code + '：' + msg + '（第 ' + st.retryN + ' 次，稍後重試）';
              break;
            }
          }
          item.err = msg;
        }
        // 不再固定睡 1.1 秒：節奏由 fugleHistPace_ 在每一次請求前控制
      }

      batch.push(item);
      if (batch.length >= DK_FLUSH_CODES || Date.now() - lastFlushAt >= DK_FLUSH_EVERY_MS_) { flush(); }
    }
    flush();

    var left = codes.filter(function (c) { return c > st.lastCode; }).length;
    var done = left === 0 && stop !== 'fatal';
    if (done) { st.finishedAt = nowStampDk_(); }
    st.updatedAt = nowStampDk_();
    setDailyKState_(st);

    var p2 = dailyKProgress_(st, codes);
    var note = '本次成功 ' + roundOk + '、失敗 ' + roundFailed + (roundSkipped ? '、略過 ' + roundSkipped : '') +
               '，進度 ' + p2.processed + '/' + p2.total +
               (done ? '，整輪完成（累計成功 ' + st.ok + '、失敗 ' + st.failed + '）' : '') +
               (stop === 'transient' ? '，' + st.lastError : '') +
               (stop === 'fatal' ? '，停止：' + st.lastError : '');
    var res = dailyKResult_(st, codes, { done: done, fatal: stop === 'fatal', stop: stop || 'none',
                                         ok: roundOk, failed: roundFailed, skipped: roundSkipped, note: note });
    res.finished = done;   // 這一次真的把整輪做完（不是「沒有進行中的一輪」那種提早返回）
    return res;
  } finally {
    releaseDailyKLease_(token);
  }
}

function nowStampDk_() { return Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss'); }

function dailyKProgress_(st, codes) {
  codes = codes || trackedCodes_();
  var last = st && st.lastCode || '';
  var processed = st && st.finishedAt ? codes.length
                : codes.filter(function (c) { return c <= last && last !== ''; }).length;
  return { processed: processed, total: codes.length };
}

function dailyKResult_(st, codes, r) {
  var p = dailyKProgress_(st, codes);
  return { done: !!r.done, busy: false, fatal: !!r.fatal, stop: r.stop || 'none',
           processed: p.processed, total: p.total,
           ok: r.ok || 0, failed: r.failed || 0, skipped: r.skipped || 0,
           failedList: st && st.failedList ? st.failedList.slice(0, 5) : [],
           note: r.note || '' };
}

function scheduleDailyKContinue_(delayMs) {
  clearDailyKContinue_();
  try {
    ScriptApp.newTrigger(DK_CONTINUE_HANDLER).timeBased().after(delayMs || DK_CONTINUE_DELAY_MS).create();
    return true;
  } catch (e) {
    // 觸發器上限 20 個。排不進去不能連這一段都不做，進度照樣保留，改由手動或明天的排程接著補。
    Logger.log('  ※ 無法排定自動續跑（' + String(e && e.message || e).slice(0, 80) + '）。' +
               '多半是觸發器已滿 20 個；這一段照常執行、進度保留，請再手動執行 backfillDailyKJob() 接著補。');
    return false;
  }
}

/* 保險：開始補之前先排一個 7 分鐘後的續跑。
   正常結束時 afterDailyKRound_ 會把它換成 1 分鐘後（或完成時刪掉）；
   萬一這次執行被 Google 在 6 分鐘處硬砍、來不及排下一棒，這個保險還在，整輪不會就此停住。 */
var DK_SAFETY_DELAY_MS = 7 * 60 * 1000;

function clearDailyKContinue_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === DK_CONTINUE_HANDLER) { ScriptApp.deleteTrigger(t); }
  });
}

/** 依結果決定要不要排續跑，並把結果印到執行紀錄。 */
function afterDailyKRound_(r) {
  Logger.log('補日K：' + r.note);
  if (r.done) {
    clearDailyKContinue_();
    if (r.failedList && r.failedList.length) { Logger.log('  取不到的（前幾檔）：' + r.failedList.join('、')); }
    if (r.finished && scheduleAfterDailyKDone_()) {
      Logger.log('  整輪完成。約一分鐘後自動重算持股追蹤並記錄當日績效（' + DK_AFTER_HANDLER_ + '）。');
    } else {
      Logger.log('  整輪完成。請接著執行 rebuildHoldingsTrackerJob()，需要時再 rebuildPerformanceHistoryJob()。');
    }
  } else if (r.fatal) {
    clearDailyKContinue_();
    Logger.log('  無法繼續，已停止續跑。修正原因後再執行 backfillDailyKJob() 會從停下的地方接著補。');
  } else if (scheduleDailyKContinue_()) {
    Logger.log('  已排定約 ' + Math.round(DK_CONTINUE_DELAY_MS / 1000) + ' 秒後自動續跑（' + DK_CONTINUE_HANDLER +
               '），不必再手動按。進度可用 backfillDailyKStatus() 查看。');
  }
}

/* 補日K整輪完成之後的銜接（2026/09/16 v40）。

   原本每天 14:35 補日K、14:50 重算持股追蹤、15:05 記錄績效，是三個各自固定時刻的觸發器，
   隱含「補日K 15 分鐘內一定做完」的假設。一輪要重抓所有追蹤中的股票一整年，
   做不完時追蹤與績效就用了還沒補完的日K。現在由補日K做完整輪後自己排一次性觸發器接著做，
   順序保證是補日K → 重算持股追蹤 → 記錄績效。14:50、15:05 的排程保留，當作收盤後的第一版。 */
var DK_AFTER_HANDLER_ = 'afterDailyKDoneJob';

function scheduleAfterDailyKDone_() {
  try {
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (t.getHandlerFunction() === DK_AFTER_HANDLER_) { ScriptApp.deleteTrigger(t); }
    });
    ScriptApp.newTrigger(DK_AFTER_HANDLER_).timeBased().after(60 * 1000).create();
    return true;
  } catch (e) {
    Logger.log('  ※ 無法排定補日K之後的重算（' + String(e && e.message || e).slice(0, 80) + '），請手動執行 afterDailyKDoneJob()。');
    return false;
  }
}

/** 補日K整輪完成後：重算持股追蹤，成功才記錄當日績效（追蹤失敗時記下的績效會是舊的）。 */
function afterDailyKDoneJob() {
  try {
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (t.getHandlerFunction() === DK_AFTER_HANDLER_) { ScriptApp.deleteTrigger(t); }
    });
  } catch (e) { /* 刪不掉一次性觸發器不影響這次重算 */ }
  try {
    rebuildHoldingsTrackerJob();
  } catch (e) {
    Logger.log('補日K之後重算持股追蹤失敗，這次不記錄績效：' + e);
    return { tracker: false, perf: false };
  }
  try {
    snapshotPerformanceJob();
  } catch (e) {
    Logger.log('補日K之後記錄績效失敗：' + e);
    return { tracker: true, perf: false };
  }
  // 日K剛整批換新：先把每一檔的讀取快取寫好，訪客打開個股面板不必等整張表讀取
  try { warmKCaches_(); } catch (e) { /* 預熱失敗只影響速度 */ }
  return { tracker: true, perf: true };
}

/* 每日補日K的排程時間（2026/09/16 v40）：16:45。

   為什麼不是收盤後馬上抓：
     13:30 收盤，14:00～14:30 還有盤後定價交易，證交所公布的當日成交股數含盤後定價、零股、鉅額，
     14:30 之後才是最終數字；富果文件寫明歷史行情「每交易日盤後 16:30 前完成更新」。
     原本 14:35 抓，當天那一根可能還沒有、或量還不完整，要到隔天那一輪才會被改正。
   13:45 做的是另一件事：把盤中五分鐘快照聚合成當天的 60 分 K（aggregateSnapshotJob），14:05 再以富果的 60 分 K 取代。 */
var DK_DAILY_HOUR_ = 16;
var DK_DAILY_MINUTE_ = 45;

/**
 * 在編輯器執行一次：把每日補日K的觸發器改到 16:45。只動 backfillDailyKJob 這一個，其他觸發器不碰
 * （installTriggers 會先刪除全部觸發器再重建，既有專案不建議為了這一項重跑）。
 */
function rescheduleDailyKTrigger() {
  var removed = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'backfillDailyKJob') { ScriptApp.deleteTrigger(t); removed++; }
  });
  ScriptApp.newTrigger('backfillDailyKJob').timeBased().atHour(DK_DAILY_HOUR_).nearMinute(DK_DAILY_MINUTE_).everyDays(1).create();
  Logger.log('每日補日K已改為 ' + DK_DAILY_HOUR_ + ':' + ('0' + DK_DAILY_MINUTE_).slice(-2) +
             '（移除舊的 ' + removed + ' 個）。補完整輪後會自動接著重算持股追蹤與記錄績效。');
  return { removed: removed, hour: DK_DAILY_HOUR_, minute: DK_DAILY_MINUTE_ };
}

/**
 * 編輯器手動執行或每日排程。時間不夠會自己排續跑，直到整輪完成。
 * 觸發器呼叫時第一個參數是事件物件，所以只有帶 deadline 的物件才當作選項。
 */
function backfillDailyKJob(opts) {
  // 觸發器傳進來的是事件物件（authMode、triggerUid…），沒有 deadline 也沒有文字的 source，不會被誤認成選項。
  var o = opts && (typeof opts.deadline === 'number' || typeof opts.source === 'string') ? opts : {};
  scheduleDailyKContinue_(DK_SAFETY_DELAY_MS);
  var r = dailyKBackfillRound_({ deadline: o.deadline, source: o.source || '排程或編輯器' });
  afterDailyKRound_(r);
  return r;
}

/** 續跑用的一次性觸發器入口。只接著做未完成的一輪。 */
function backfillDailyKContinueJob() {
  scheduleDailyKContinue_(DK_SAFETY_DELAY_MS);
  var r = dailyKBackfillRound_({ continueOnly: true, source: '自動續跑' });
  afterDailyKRound_(r);
  return r;
}

/** 在編輯器執行：印出目前這一輪補到哪裡。只讀。 */
function backfillDailyKStatus() {
  var st = dailyKState_();
  if (!st) { Logger.log('目前沒有補日K的進度紀錄。'); return null; }
  var codes = trackedCodes_(); codes.sort();
  var p = dailyKProgress_(st, codes);
  var lease = null;
  try { lease = JSON.parse(PropertiesService.getScriptProperties().getProperty(DK_LEASE_PROP) || 'null'); } catch (e) { lease = null; }
  var pendingTrigger = ScriptApp.getProjectTriggers().some(function (t) {
    return t.getHandlerFunction() === DK_CONTINUE_HANDLER;
  });
  Logger.log('補日K　' + (st.finishedAt ? '已完成（' + st.finishedAt + '）' : '進行中') +
             '　進度 ' + p.processed + '/' + p.total + '　做到 ' + (st.lastCode || '（尚未開始）'));
  Logger.log('  區間 ' + st.from + ' ～ ' + st.to + '　開始 ' + st.startedAt + '　最後更新 ' + (st.updatedAt || '—') +
             '　續跑 ' + st.rounds + ' 次' + (st.source ? '　來源：' + st.source : ''));
  Logger.log('  成功 ' + st.ok + '、失敗 ' + st.failed + '、略過 ' + (st.skipped || 0));
  if (st.failedList && st.failedList.length) { Logger.log('  失敗（前 30 檔）：' + st.failedList.join('、')); }
  if (st.smsRetryAbort) { Logger.log('  簡訊重試計數：' + st.smsRetryAbort + (st.abort ? '（已中止續跑）' : '')); }
  if (st.lastError) { Logger.log('  最近的問題：' + st.lastError); }
  Logger.log('  寫入中：' + (lease && lease.until > Date.now() ? '是（' + new Date(lease.until) + ' 前）' : '否') +
             '　已排續跑：' + (pendingTrigger ? '是' : '否'));
  return { state: st, progress: p, running: !!(lease && lease.until > Date.now()), pendingTrigger: pendingTrigger };
}

/** 純附加。成本只跟這一批的筆數有關，與表的大小無關。 */
function appendKCache_(buffer) {
  if (!buffer.length) { return; }
  withLock_(function () {
    var sh = getSheet_('日K快取');
    sh.getRange(sh.getLastRow() + 1, 1, buffer.length, 7).setValues(buffer);
  });
}

/**
 * 保留供未來排程補資料用；個股查詢的即時現爬已停用（證交所逐月太慢會空轉）。
 * 沒有 Fugle 金鑰時只走證交所，涵蓋上市。
 */
function fetchCodeOnDemand(code) {
  code = String(code || '').trim();
  if (!/^(?:00981A|\d{4,6})$/.test(code)) { return { ok: false, reason: '代號格式不正確' }; }

  // 已經有就直接回，不重抓
  if (getCachedDailyK(code).length) { return { ok: true, cached: true, bars: getCachedDailyK(code).length }; }

  var rows = [];
  try {
    if (hasFugle_()) {
      var rng = dailyKRange_();
      rows = fugleHistorical_(code, rng.from, rng.to);
    } else {
      // 證交所逐月，往前抓最近 6 個月即可讓面板有圖
      var now = new Date();
      for (var m = 0; m < 6; m++) {
        var dt = new Date(now.getFullYear(), now.getMonth() - m, 1);
        var ym = Utilities.formatDate(dt, TZ, 'yyyy/MM');
        fetchTwseMonth_(code, ym).forEach(function (r) { rows.push(r); });
        Utilities.sleep(400);
      }
    }
  } catch (e) {
    return { ok: false, reason: String(e).slice(0, 120) };
  }

  rows = rows.filter(function (r) { return r.close; });
  if (!rows.length) {
    return { ok: false, reason: '外部來源查無這一檔的日K（可能是上櫃股且未設定 Fugle 金鑰，或代號不存在）' };
  }

  var buffer = rows.map(function (r) {
    return [code, r.date, r.open, r.high, r.low, r.close, r.volume];
  });
  appendKCache_(buffer);
  kcDrop_('dk_', [code]);
  kcDrop_('dk2_', [code]);
  _DK_INDEX = null;   // 讓索引重建，含這一檔
  return { ok: true, cached: false, bars: rows.length };
}

/** 單月上市日K */
function fetchTwseMonth_(code, ym) {
  var url = 'https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date=' +
    ym.replace('/', '') + '01&stockNo=' + code + '&response=json';
  var out = [];
  try {
    var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    var status = (res && typeof res.getResponseCode === 'function') ? res.getResponseCode() : 200;
    var text = (res && typeof res.getContentText === 'function') ? res.getContentText() : '';
    if (status !== 200) {
      out.error = 'TWSE HTTP ' + status;
      return out;
    }
    var trimmed = text.trim();
    if (trimmed.charAt(0) === '<') {
      out.error = 'TWSE 回傳 HTML 網頁（IP 可能遭阻擋）';
      return out;
    }
    var j = JSON.parse(trimmed);
    if (j.stat && j.stat !== 'OK') {
      out.error = 'TWSE 回應：' + j.stat;
    }
    (j.data || []).forEach(function (r) {
      var p = String(r[0]).split('/');
      out.push({
        date: (Number(p[0]) + 1911) + '/' + p[1] + '/' + p[2],
        volume: kNum_(r[1]), open: kNum_(r[3]), high: kNum_(r[4]),
        low: kNum_(r[5]), close: kNum_(r[6])
      });
    });
    var err = out.error;
    out = out.filter(function (r) { return r.close; });
    if (err) { out.error = err; }
  } catch (e) {
    out.error = 'TWSE 例外：' + e.message;
  }
  return out;
}

/** 單月上市日K（含詳細診斷資訊） */
function fetchTwseMonthDetailed_(code, ym) {
  var got = fetchTwseMonth_(code, ym);
  return { rows: got || [], error: (got && got.error) || '' };
}

/** FinMind 歷史日K詳細抓取（含診斷資訊） */
function fetchFinmindDailyKDetailed_(code, from, to) {
  var token = (typeof getFinMindToken_ === 'function') ? getFinMindToken_() : '';
  var sDate = String(from || '').replace(/\//g, '-');
  var eDate = String(to || '').replace(/\//g, '-');
  var url = 'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id=' +
            encodeURIComponent(code) + '&start_date=' + sDate + '&end_date=' + eDate +
            (token ? ('&token=' + encodeURIComponent(token)) : '');
  var out = { rows: [], error: '', httpCode: 200 };
  try {
    var res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
    var status = res.getResponseCode();
    out.httpCode = status;
    var content = res.getContentText() || '';
    if (status !== 200) {
      out.error = 'FinMind HTTP ' + status + (content ? ('：' + content.slice(0, 80)) : '');
      return out;
    }
    var j = JSON.parse(content);
    if (j.status !== 200 && j.msg !== 'success') {
      out.error = 'FinMind 回應：' + (j.msg || j.status);
      return out;
    }
    (j.data || []).forEach(function (r) {
      if (r.close && r.date) {
        out.rows.push({
          date: String(r.date).replace(/-/g, '/'),
          volume: kNum_(r.Trading_Volume),
          open: kNum_(r.open),
          high: kNum_(r.max),
          low: kNum_(r.min),
          close: kNum_(r.close)
        });
      }
    });
    out.rows = out.rows.filter(function (r) { return r.close; });
  } catch (e) {
    out.error = 'FinMind 例外：' + e.message;
  }
  return out;
}

/** FinMind 歷史日K（支援上市與上櫃） */
function fetchFinmindDailyK_(code, from, to) {
  var det = fetchFinmindDailyKDetailed_(code, from, to);
  if (det.error && !det.rows.length) {
    var err = new Error(det.error);
    err.httpCode = det.httpCode;
    throw err;
  }
  return det.rows;
}

/** FinMind 智慧補缺口日K */
function fetchMissingDailyKFinMind_(code, from, to, rows, deadline) {
  var now = new Date(), today = Utilities.formatDate(now, TZ, 'yyyy/MM/dd'), closed = Number(Utilities.formatDate(now, TZ, 'HHmm')) >= 1630;
  var ranges = missingKDateRanges_(rows, from, to, today, closed);
  if (!ranges.length) { return []; }
  if (deadline && Date.now() > deadline - 15000) {
    var pendingRes = [];
    pendingRes.pending = true;
    return pendingRes;
  }
  var minFrom = ranges[0].from, maxTo = ranges[ranges.length - 1].to;
  var key = 'fm_empty_' + code + '_' + minFrom + '_' + maxTo;
  if (CACHE.get(key)) { return []; }
  var fetched = fetchFinmindDailyK_(code, minFrom, maxTo);
  if (!fetched.length) { CACHE.put(key, '1', 21600); }
  return fetched;
}

// 櫃買的 st43_result.php 已隨舊站 wwwov.tpex.org.tw 於 2025/05/31 停用，
// 現在回傳 404 網頁。上櫃日K 一律走 Fugle，故此處不再保留該來源。

function kNum_(v) {
  var n = parseFloat(String(v).replace(/,/g, ''));
  return isNaN(n) ? 0 : n;
}

/** 從快取讀日K。線上一律走這裡，不對外請求。 */
/*
 * 日K快取讀取。
 *
 * 這裡原本每呼叫一次就把整張「日K快取」讀進來再過濾出一檔，
 * 而那張表是 70 幾檔 × 每檔數百根，一次就是上萬列。
 * 個股面板一開、或追蹤表要補收盤價時會連續呼叫數十次，
 * 等於把上萬列重讀數十次，執行時間直接爆掉，圖表就轉不出來。
 *
 * 改成整張表只讀一次、依代號建索引，之後同一次執行都吃記憶體裡的索引。
 */
var _DK_INDEX = null;

function loadAllDailyK_() {
  if (_DK_INDEX) { return _DK_INDEX; }

  var map = {};
  readSheetObjects_('日K快取').forEach(function (r) {
    var c = String(r['代號']).trim();
    if (!c) { return; }
    var d = fmtDate_(r['日期']);
    var close = Number(r['收']);
    if (!d || !close) { return; }
    if (!map[c]) { map[c] = []; }
    map[c].push({
      date: d, open: Number(r['開']), high: Number(r['高']),
      low: Number(r['低']), close: close, volume: Number(r['量'])
    });
  });

  Object.keys(map).forEach(function (c) {
    map[c].sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
  });

  _DK_INDEX = map;
  return map;
}

/** 某一檔的最後一根收盤價。取不到即時報價時用它補。 */
function lastCloseOf_(code) {
  var k = getCachedDailyK(code);
  if (!k.length) { return null; }
  var last = k[k.length - 1];
  var prev = k.length > 1 ? k[k.length - 2] : null;
  return {
    last: last.close,
    prevClose: prev ? prev.close : null,
    date: last.date,
    volume: last.volume
  };
}

function getCachedDailyK(code) {
  code = String(code || '').trim();
  var hit = null;
  try { hit = CACHE.get('dk2_' + code); } catch (e) { hit = null; }
  if (hit) { try { return kcDecode_(hit); } catch (e) { /* 壞掉的快取當作沒有 */ } }
  // 整張讀一次，所有代號一起寫進快取（見 Quoteservice.gs 的 kcPutAll_ 說明）
  var index = loadAllDailyK_();
  kcPutAll_('dk2_', index, code);
  return index[code] || [];
}

/** 預先把日K與 60 分 K 的逐檔快取整批寫好。補日K整輪完成後、以及每 5.5 小時（快取 6 小時到期前）執行。 */
function warmKCaches_() {
  var out = { daily: 0, hourly: 0 };
  try { out.daily = kcPutAll_('dk2_', loadAllDailyK_()); } catch (e) { Logger.log('預熱日K快取失敗：' + e); }
  try { out.hourly = kcPutAll_('hk2_', loadAllHourly_()); } catch (e) { Logger.log('預熱 60 分 K 快取失敗：' + e); }
  Logger.log('K 線快取預熱：日K ' + out.daily + ' 檔、60 分 K ' + out.hourly + ' 檔');
  return out;
}

/** 日K快取的涵蓋範圍，供技術說明與前端揭露 */
function getKCacheMeta() {
  var hit = CACHE.get('kcache_meta');
  if (hit) { return JSON.parse(hit); }

  var rows = readSheetObjects_('日K快取');
  var dates = rows.map(function (r) { return fmtDate_(r['日期']); }).filter(String).sort();
  var codes = {};
  rows.forEach(function (r) { codes[String(r['代號'])] = 1; });

  var meta = {
    bars: rows.length,
    codes: Object.keys(codes).length,
    since: dates.length ? dates[0] : '',
    until: dates.length ? dates[dates.length - 1] : ''
  };
  CACHE.put('kcache_meta', JSON.stringify(meta), 1800);
  return meta;
}

/* ================================================================== *
 * 日K快取全面核對與修復（2026/09/16 v39）
 *
 * 起因：checkVolumeUnit 核對的結果，1506 的日K快取與證交所成交股數逐日完全相同（股），
 * 2385 卻是四捨五入後的張（7151／官方 7,150,763 股），而且 7 月只有 07/27 之後。
 * 同一張表兩種單位——v41 查出原因：舊的 repairDailyKVolume 把「每日量中位數 ≥ 1,000,000」的股票
 * 整欄除以 1000 四捨五入寫回，成交量大的（2385 一天四、五百萬股）被改成張，小的（1506 一天十萬股）維持股；
 * 2385 從 07/27 開始，正是當年只抓 46 天那個問題留下的資料，之後一直沒被重抓成功。
 *
 * 四捨五入過的張乘回 1000 也不是正確的股數（7151 × 1000 ≠ 7,150,763），所以不做換算，
 * 一律重新向富果抓完整區間（v38 起寫入端固定存股）。
 *
 * 核對的標準答案：證交所 MI_INDEX（上市全部股票）與櫃買 dailyQuotes（上櫃全部股票）
 * 某一天的收盤行情，一天各一個請求就涵蓋所有代號，不必逐檔逐月去問。
 * ================================================================== */

/** 某一天全市場的官方收盤行情：代號 → { volume（股）, open, high, low, close, market }。 */
function officialDailyAll_(dateStr) {
  var out = {}, got = { twse: false, tpex: false };
  var num = function (v) { return kNum_(String(v == null ? '' : v).replace(/<[^>]*>/g, '')); };
  var getJson = function (url) {
    try { return JSON.parse(UrlFetchApp.fetch(url, { muteHttpExceptions: true }).getContentText()); }
    catch (e) { return null; }
  };

  // 上市：MI_INDEX 的「每日收盤行情」表（欄位：證券代號、成交股數、開盤價、最高價、最低價、收盤價）
  var j1 = getJson('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=' +
                   dateStr.replace(/\//g, '') + '&type=ALLBUT0999&response=json');
  if (j1 && j1.stat === 'OK') {
    (j1.tables || []).forEach(function (t) {
      var f = t.fields || [];
      var ic = f.indexOf('證券代號'), iv = f.indexOf('成交股數');
      var io = f.indexOf('開盤價'), ih = f.indexOf('最高價'), il = f.indexOf('最低價'), ix = f.indexOf('收盤價');
      if (ic < 0 || iv < 0 || ix < 0) { return; }
      (t.data || []).forEach(function (r) {
        var code = String(r[ic]).trim();
        if (!code) { return; }
        out[code] = { market: '上市', volume: num(r[iv]), open: num(r[io]), high: num(r[ih]), low: num(r[il]), close: num(r[ix]) };
      });
      if ((t.data || []).length) { got.twse = true; }
    });
  }

  // 上櫃：dailyQuotes 的「上櫃股票行情」表（欄位：代號、成交股數、開盤、最高、最低、收盤）
  var j2 = getJson('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=' +
                   encodeURIComponent(dateStr) + '&type=EW&response=json');
  if (j2 && String(j2.stat).toLowerCase() === 'ok') {
    (j2.tables || []).forEach(function (t) {
      var f = t.fields || [];
      var ic = f.indexOf('代號'), iv = f.indexOf('成交股數');
      var io = f.indexOf('開盤'), ih = f.indexOf('最高'), il = f.indexOf('最低'), ix = f.indexOf('收盤');
      if (ic < 0 || iv < 0 || ix < 0) { return; }
      (t.data || []).forEach(function (r) {
        var code = String(r[ic]).trim();
        if (!code || out[code]) { return; }
        out[code] = { market: '上櫃', volume: num(r[iv]), open: num(r[io]), high: num(r[ih]), low: num(r[il]), close: num(r[ix]) };
      });
      if ((t.data || []).length) { got.tpex = true; }
    });
  }
  return { rows: out, twse: got.twse, tpex: got.tpex };
}

/** yyyy/MM/dd 加減天數。 */
function dkShiftDate_(ymd, days) {
  var p = String(ymd).split('/');
  var d = new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]) + days);
  return d.getFullYear() + '/' + ('0' + (d.getMonth() + 1)).slice(-2) + '/' + ('0' + d.getDate()).slice(-2);
}

/**
 * 在編輯器執行：日K快取逐檔核對。只讀，不寫任何表。
 *
 * auditDailyKCache()              取快取裡最新的交易日（官方還沒公布就往前一天）
 * auditDailyKCache('2026/07/31')  指定某一天
 *
 * 每一檔檢查四件事：
 *   量與開高低收   與官方完全相同才算對；量剛好是官方股數 ÷ 1000 四捨五入 → 舊版存成張
 *   過期           最後一根早於核對日
 *   涵蓋不足       最早一根晚於「今天往回 KLINE_MONTHS 個月」超過 14 天（新上市櫃的股票會被列出，可忽略）
 *   缺那一天       官方有成交、快取沒有那一天
 * 上櫃股的量若與櫃買差在 2% 以內、開高低收相同，只列為「量接近」不要求重抓：
 * 富果與櫃買對零股、鉅額是否計入可能不同，上市股已逐日核對過是完全相同。
 */
function auditDailyKCache(dateStr) {
  var index = loadAllDailyK_();
  var codes = trackedCodes_().slice().sort();
  var out = ['========================================',
             '  日K快取核對（官方：證交所 MI_INDEX、櫃買 dailyQuotes）',
             '========================================'];

  var dates = {};
  Object.keys(index).forEach(function (c) { index[c].forEach(function (r) { dates[r.date] = 1; }); });
  var candidates = dateStr ? [String(dateStr).replace(/-/g, '/')] : Object.keys(dates).sort().reverse().slice(0, 3);
  var auditDate = '', official = null;
  for (var i = 0; i < candidates.length; i++) {
    var o = officialDailyAll_(candidates[i]);
    if (o.twse) { auditDate = candidates[i]; official = o; break; }
    Utilities.sleep(500);
  }
  if (!official) {
    out.push('取不到證交所 ' + candidates.join('、') + ' 的收盤行情（可能尚未公布或連線失敗），請稍後再試或指定日期。');
    Logger.log(out.join('\n'));
    return { ok: false, date: '', refetch: [], matched: [], notes: [] };
  }

  var expectFrom = dailyKRange_().from.replace(/-/g, '/');
  var result = { ok: true, date: auditDate, twse: official.twse, tpex: official.tpex,
                 matched: [], refetch: [], notes: [] };
  var same = function (a, b) { return Math.abs(Number(a) - Number(b)) < 1e-6; };

  codes.forEach(function (code) {
    var rows = index[code] || [];
    var off = official.rows[code];
    var issues = [], notes = [];
    if (!rows.length) {
      issues.push('快取沒有這一檔');
    } else {
      var first = rows[0].date, last = rows[rows.length - 1].date;
      if (last < auditDate) { issues.push('過期：最後一根 ' + last); }
      if (first > dkShiftDate_(expectFrom, 14)) { issues.push('涵蓋不足：最早 ' + first + '（應從 ' + expectFrom + ' 起）'); }
      var row = null;
      for (var k = rows.length - 1; k >= 0; k--) { if (rows[k].date === auditDate) { row = rows[k]; break; } if (rows[k].date < auditDate) { break; } }
      if (off && off.close) {
        if (!row) {
          if (last >= auditDate) { issues.push('缺 ' + auditDate); }
        } else {
          var ohlc = same(row.open, off.open) && same(row.high, off.high) && same(row.low, off.low) && same(row.close, off.close);
          if (!ohlc) {
            issues.push('開高低收不符（快取 ' + [row.open, row.high, row.low, row.close].join('/') +
                        '，官方 ' + [off.open, off.high, off.low, off.close].join('/') + '）');
          }
          if (!same(row.volume, off.volume)) {
            if (off.volume >= 1000 && Math.abs(row.volume - Math.round(off.volume / 1000)) <= 1) {
              issues.push('量存成張（快取 ' + row.volume + '，官方 ' + off.volume + ' 股）');
            } else if (off.market === '上櫃' && ohlc && off.volume && Math.abs(row.volume / off.volume - 1) <= 0.02) {
              notes.push('量接近（快取 ' + row.volume + ' 股，櫃買 ' + off.volume + ' 股，差 ' +
                         ((row.volume / off.volume - 1) * 100).toFixed(2) + '%）');
            } else {
              issues.push('量不符（快取 ' + row.volume + '，官方 ' + off.volume + ' 股' +
                          (off.volume ? '，' + (row.volume / off.volume).toFixed(4) + ' 倍' : '') + '）');
            }
          }
        }
      } else if (!off) {
        notes.push('官方 ' + auditDate + ' 查無這一檔（興櫃、下市櫃、暫停交易或代號有誤）');
      }
    }
    if (issues.length) { result.refetch.push({ code: code, issues: issues }); }
    else { result.matched.push(code); }
    if (notes.length) { result.notes.push({ code: code, notes: notes }); }
  });

  out.push('核對日 ' + auditDate + '　上市行情' + (official.twse ? '有' : '無') + '、上櫃行情' + (official.tpex ? '有' : '無') +
           '　追蹤 ' + codes.length + ' 檔：完全正確 ' + result.matched.length + '、需要重抓 ' + result.refetch.length + '。');
  result.refetch.forEach(function (x) { out.push('  ✗ ' + x.code + '　' + x.issues.join('；')); });
  result.notes.forEach(function (x) { out.push('  ・ ' + x.code + '　' + x.notes.join('；')); });
  if (result.refetch.length) {
    out.push('');
    out.push('修法：執行 repairDailyKCache()，會把所有追蹤中的股票重新抓完整區間（量一律存股），沒做完會自動續跑；');
    out.push('用 backfillDailyKStatus() 看進度，完成後再執行一次 auditDailyKCache() 確認。');
  }
  Logger.log(out.join('\n'));
  return result;
}

/**
 * 在編輯器執行：日K快取整批重抓。
 *
 * 做法就是每天 14:35 那一輪補日K，只是先把進度歸零、從第一檔重新開始，
 * 確保每一檔都用 v38 之後的程式重寫一次（量存股、區間 KLINE_MONTHS 個月）。
 * 一次執行只做約 4 分鐘，剩下的由續跑觸發器接著做；觸發器建不起來時（專案觸發器滿 20 個）
 * backfillDailyKStatus() 會顯示，再手動執行 backfillDailyKJob() 接著補即可，同一天不會從頭重來。
 */
function repairDailyKCache() {
  // 先把上一輪停在哪、失敗原因印出來再重設：2385 這種一直沒被重抓的，原因就在這裡。
  Logger.log('—— 重設前的補日K狀態 ——');
  try { backfillDailyKStatus(); } catch (e) { Logger.log('讀不到補日K狀態：' + e); }
  if (!resetDailyKCursor()) {
    Logger.log('補日K正在寫入中，這次沒有重設。等它這一段結束（幾分鐘）再執行一次 repairDailyKCache()。');
    return { started: false };
  }
  var r = backfillDailyKJob({ source: '日K快取修復（repairDailyKCache）' });
  Logger.log('日K快取修復已開始：' + (r && r.note ? r.note : '') +
             '\n用 backfillDailyKStatus() 看進度；整輪完成後執行 auditDailyKCache() 確認每一檔都正確。');
  return { started: true, round: r };
}

/* ================================================================== *
 * 四、每日績效快照
 * 每天收盤記一筆，供績效走勢折線圖使用。這是往前補不了的資料。
 * ================================================================== */

/**
 * 回補績效走勢。
 *
 * 每日績效原本只有排程跑過的那幾天，起點是排程安裝日。
 * 但日K快取裡有每一檔從進場日至今的每日收盤價，所以「那一天的整體平均報酬」
 * 是算得出來的，不需要當時真的有跑排程。
 *
 * 算法：對每一個交易日 d，取所有在 d 之前已經進場的標的，
 * 各自算 (d 當天收盤 - 進場價) / 進場價，再取平均。
 * 已出場的標的在出場日之後不列入。
 */
function backfillPerformanceJob() {
  var base = readSheetObjects_('持股追蹤').filter(function (r) {
    return /^(?:00981A|\d{4,6})$/.test(String(r['代號']).trim()) && Number(r['進場價']);
  });
  if (!base.length) {
    Logger.log('持股追蹤沒有可用的進場價，請先執行 backfillDailyKJob()（會自動續跑到補完）與 rebuildHoldingsTrackerJob()');
    return;
  }

  // 每一檔的日K，順便收集所有出現過的交易日
  var kmap = {}, dateSet = {};
  base.forEach(function (r) {
    var code = String(r['代號']).trim();
    var k = {};
    getCachedDailyK(code).forEach(function (c) {
      k[c.date] = c.close;
      dateSet[c.date] = 1;
    });
    kmap[code] = k;
  });

  var firstBuy = base.map(function (r) { return fmtDate_(r['首次買入日']); }).sort()[0];
  var dates = Object.keys(dateSet).filter(function (d) { return d >= firstBuy && (typeof PERFORMANCE_START_DATE!=='string'||d>=PERFORMANCE_START_DATE); }).sort();

  if (!dates.length) {
    Logger.log('日K快取沒有涵蓋到首次買入日 ' + firstBuy + '，無法回補');
    return;
  }

  Logger.log('回補區間 ' + dates[0] + ' 到 ' + dates[dates.length - 1] + '，共 ' + dates.length + ' 個交易日');

  var rows = [];
  dates.forEach(function (d) {
    var rets = [], held = 0, entered = 0;

    base.forEach(function (r) {
      var code = String(r['代號']).trim();
      var buy = fmtDate_(r['首次買入日']);
      var sell = fmtDate_(r['最近賣出日']);
      var entry = Number(r['進場價']);

      if (buy > d) { return; }                       // 那天還沒進場
      entered++;                                     // 到 d 為止已進場（累積追蹤檔數）
      if (sell && sell < d) { return; }              // 那天已經出場
      held++;                                        // 那天仍持有（不論有無收盤價）

      var close = kmap[code][d];
      if (!close) { return; }                        // 有持有但那天沒有收盤價，不列入報酬平均
      rets.push((close - entry) / entry * 100);
    });

    if (!held) { return; }
    var avg = rets.length ? rets.reduce(function (a, b) { return a + b; }, 0) / rets.length : 0;
    var pos = rets.length ? rets.filter(function (x) { return x > 0; }).length / rets.length * 100 : 0;

    rows.push([d, entered, held,
               rets.length ? Math.round(avg * 100) / 100 : '',
               rets.length ? Math.round(pos * 10) / 10 : '']);
  });

  if (!rows.length) { Logger.log('沒有可回補的資料'); return; }

  withLock_(function () {
    var sh = getSheet_('每日績效');
    sh.clearContents();
    sh.getRange(1, 1, 1, 5).setValues([['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例']]);
    sh.getRange(2, 1, rows.length, 5).setValues(rows);
  });

  CACHE.remove('perf_series');
  CACHE.remove('perf_series_v54_'+(typeof PERFORMANCE_START_DATE==='string'?PERFORMANCE_START_DATE:''));

  var first = rows[0], last = rows[rows.length - 1];
  Logger.log('');
  Logger.log('回補完成，共 ' + rows.length + ' 天');
  Logger.log('  ' + first[0] + '　' + first[1] + ' 檔　平均報酬 ' + first[3] + '%');
  Logger.log('  ' + last[0] + '　' + last[1] + ' 檔　平均報酬 ' + last[3] + '%');
  Logger.log('現在績效走勢分頁的折線圖就有東西了，不再只有一個點。');
}

/* ------------------------------------------------------------------ *
 * 績效走勢保底（v54）
 *
 * 2026/09/23 走勢停在 9/18，兩個原因疊在一起：
 *   一、snapshotPerformanceJob 只由 afterDailyKDoneJob 呼叫，而後者只在「補日K整輪完成」才排。
 *       補日K沒跑完的日子（來源失敗、時間到、續跑鏈斷掉），當天就不會記績效，也沒有人被通知。
 *   二、getPerformanceSeries 用日K快取的日期當交易日曆，補日K落後時，新的點也會被濾掉。
 * 第二點在 getPerformanceSeries 改看休市表；這一支處理第一點：
 *   每個交易日 17:30、19:30 各檢查一次，最後一筆早於今天就重算持股追蹤再記一筆，不等補日K。
 *   中間漏掉超過一個交易日時，另外從最後一筆那天起重算歷史，把能算的日子補回來。
 * 補日K完成之後的完整重算照舊，會覆寫同一天。
 * ------------------------------------------------------------------ */
function isTradingDateStr_(d) {
  var m = String(d || '').match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (!m) { return false; }
  // 取當天中午，避免跨時區把日期推到前一天或後一天。
  var noon = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0);
  if (typeof whyClosed_ === 'function') { return !whyClosed_(noon); }
  var wd = noon.getDay();            // 沒有休市表時（例如離線測試）至少擋掉週末
  return wd !== 0 && wd !== 6;
}

function latestTradingDayStr_() {
  var d = new Date();
  for (var i = 0; i < 20; i++) {
    var s = Utilities.formatDate(d, TZ, 'yyyy/MM/dd');
    if (isTradingDateStr_(s)) { return s; }
    d = new Date(d.getTime() - 86400000);
  }
  return todayStr_();
}

/** after（不含）到 until（含）之間有幾個交易日。 */
function tradingDaysAfter_(after, until) {
  if (!after || !until || after >= until) { return 0; }
  var m = after.match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
  if (!m) { return 0; }
  var d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0), n = 0;
  for (var i = 0; i < 400; i++) {
    d = new Date(d.getTime() + 86400000);
    var s = Utilities.formatDate(d, TZ, 'yyyy/MM/dd');
    if (s > until) { break; }
    if (isTradingDateStr_(s)) { n++; }
  }
  return n;
}

function ensurePerformanceContinuityJob_() {
  if (!isTradingDayToday_()) { return; }
  var today = todayStr_();
  var last = readSheetObjects_('每日績效').map(function (r) { return fmtDate_(r['日期']); })
    .filter(String).sort().pop() || '';
  if (last >= today) { return; }
  var gap = tradingDaysAfter_(last, today);
  Logger.log('績效保底：最後一筆 ' + (last || '無') + '，距今天 ' + gap + ' 個交易日，先重算持股追蹤再記一筆');
  try { rebuildHoldingsTrackerJob(); }
  catch (e) { Logger.log('績效保底：重算持股追蹤失敗，今天先不記（' + e + '）'); return; }
  if (last && gap > 1) {
    try { rebuildPerformanceHistoryJob(last); }
    catch (e2) { Logger.log('績效保底：補歷史失敗（' + e2 + '），仍記今天這一筆'); }
  }
  snapshotPerformanceJob();
  try {
    getSheet_('系統狀態').appendRow([nowStamp_(), '績效保底',
      '每日績效最後一筆原為 ' + (last || '無') + '（落後 ' + gap + ' 個交易日），已補記 ' + today +
      (gap > 1 ? '，並從 ' + last + ' 起重算歷史' : ''), 'performance', 'Apps Script']);
  } catch (e3) {}
}

function snapshotPerformanceJob() {
  if(typeof PERFORMANCE_START_DATE==='string'&&todayStr_()<PERFORMANCE_START_DATE){return;}
  var t = getHoldingsTracker();
  if (!t.summary) { Logger.log('尚無可統計的持股，略過'); return; }

  var today = todayStr_();
  withLock_(function () {
    var sh = getSheet_('每日績效');
    var values = sh.getDataRange().getValues();

    var row = [today, t.summary.total, t.summary.holding, t.summary.avgReturn, t.summary.positiveRatio];

    for (var i = 1; i < values.length; i++) {
      if (fmtDate_(values[i][0]) === today) {
        sh.getRange(i + 1, 1, 1, 5).setValues([row]);   // 同一天重跑就覆蓋
        return;
      }
    }
    sh.appendRow(row);
  });

  CACHE.remove('perf_series');
  CACHE.remove('perf_series_v54_'+(typeof PERFORMANCE_START_DATE==='string'?PERFORMANCE_START_DATE:''));
  Logger.log('已記錄 ' + today + ' 的績效：平均報酬 ' + t.summary.avgReturn + '%，正報酬比例 ' + t.summary.positiveRatio + '%');
}

/* ==================================================================== *
 * 績效歷史重算
 *
 * 「每日績效」是逐日快照：snapshotPerformanceJob 每天下午跑一次，
 * 寫下當下的平均報酬與正報酬比例。那是設計，不是缺陷——它記的是
 * 「那一天你看到的樣子」。
 *
 * 但資料本身被修正之後（例如會員簡訊優先套用進去、回合被重新切分），
 * 過去那些點就變成「用錯的資料算出來的歷史」，留著會誤導。
 * 這一支把整條曲線依現在的資料重算一次。
 *
 * 為什麼不是把追蹤重跑一百多次
 * ----------------------------
 * rebuildHoldingsTrackerJob 是重活，跑一次要幾十秒。一百多個交易日
 * 就是一個小時以上，Apps Script 的單次執行上限是六分鐘，根本跑不完。
 *
 * 改用「持股追蹤的回合JSON ＋ 日K收盤價」直接算：
 *   某一天 D 持有哪幾檔　→ 回合的進場日 ≤ D 且（沒出場 或 出場日 > D）
 *   那一檔在 D 的報酬　　→（D 當天收盤 − 進場價）÷ 進場價
 * 兩份資料都已經在試算表裡，不必呼叫任何外部服務，也不花模型額度。
 *
 * 已出場的回合不列入某一天的統計，與 snapshotPerformanceJob 一致——
 * 它記的是「持有中」那一組的平均，已實現的那組另外看。
 * ==================================================================== */
function rebuildPerformanceHistoryJob(fromDate) {
  var start = Date.now();

  // 交易日曆與收盤價：兩者都從日K快取來，一次讀完。
  var closes = {};          // closes[code][date] = 收盤價
  var tradingDays = {};
  readSheetObjects_('日K快取').forEach(function (r) {
    var d = fmtDate_(r['日期']);
    var c = String(r['代號'] || '').trim();
    var v = Number(r['收']);
    if (!d || !c || !isFinite(v) || v <= 0) { return; }
    (closes[c] = closes[c] || {})[d] = v;
    tradingDays[d] = 1;
  });
  var days = Object.keys(tradingDays).sort();
  if (!days.length) {
    /* 沒有交易日曆＝沒有東西可以重算，這是「不必做」，不是「做失敗」。

       原本回 ok:false，於是上游把它當成一步失敗，整條刷新鏈停在這裡、
       後台工單標成失敗、GitHub Actions 以 exit 1 收場——而實際情況只是
       日K快取還沒有資料。後台工單本來就刻意跳過補齊日K（那一步要跑十幾分鐘、
       與這次的改動無關），所以「快取還沒填」在這條路上是預期中的狀態，
       每日排程會把它補上。把預期中的狀態報成失敗，會讓真正的失敗被淹沒。 */
    Logger.log('日K快取是空的，沒有交易日曆可用，略過績效歷史重算。');
    return { ok: true, skipped: true, days: 0, codes: 0,
             reason: '日K快取還沒有資料，沒有交易日曆可用' };
  }
  /* 本站真正開始有紀錄的那一天。

     日K快取往回涵蓋很長一段歷史，所以 days 會包含本站還沒開始記錄的日子。
     那些日子不該出現在績效走勢上——圖上畫出一條從四月就開始的線，
     看起來像那時候就在追蹤，實際上一筆紀錄都沒有。

     它會發生是因為進場日可以被「回述成本」往前推：講者說「會員在 2135 買的」，
     程式往回找到日K快取裡某一天曾經成交在 2135，就把進場日訂在那天——
     而那天可能遠早於本站的第一筆紀錄。回合本身這樣判是合理的
     （成本確實是那時候的），但據此把持有狀態一路往前延伸到沒有資料的日子，
     畫出來的走勢就是憑空的。

     起點取「操作紀錄」與「會員持股」裡最早的日期，也就是本站第一次有東西可記的那天。
     兩張表都空的時候不設限，維持原本行為——那種情況下本來就沒有東西可畫。 */
  var dataFloor = typeof PERFORMANCE_START_DATE==='string' ? PERFORMANCE_START_DATE : '';
  ['操作紀錄', '會員持股'].forEach(function (name) {
    readSheetObjects_(name).forEach(function (r) {
      var d = fmtDate_(r['日期']);
      if (d && (!dataFloor || d < dataFloor)) { dataFloor = d; }
    });
  });
  if(typeof PERFORMANCE_START_DATE==='string'&&(!dataFloor||dataFloor<PERFORMANCE_START_DATE)){dataFloor=PERFORMANCE_START_DATE;}
  if (dataFloor) {
    var before = days.length;
    days = days.filter(function (d) { return d >= dataFloor; });
    if (days.length !== before) {
      Logger.log('績效歷史：本站最早的紀錄是 ' + dataFloor + '，' +
                 (before - days.length) + ' 個更早的交易日不列入（那時沒有任何資料）。');
    }
  } else {
    Logger.log('績效歷史：操作紀錄與會員持股都是空的，不設資料起點。');
  }

  var from = fmtDate_(fromDate || '');
  var requestedFrom = from;

  // 每一檔的回合。沒有回合JSON 的列跳過（那是舊版寫的，重算一次追蹤就會有）。
  var holdingsByCode = [];
  var noJson = 0;
  readSheetObjects_('持股追蹤').forEach(function (r) {
    var code = String(r['代號'] || '').trim();
    if (!/^(?:00981A|\d{4,6})$/.test(code)) { return; }
    var raw = String(r['回合JSON'] || '').trim();
    if (!raw) { noJson++; return; }
    var rounds;
    try { rounds = JSON.parse(raw); } catch (e) { noJson++; return; }
    if (!rounds || !rounds.length) { return; }
    holdingsByCode.push({ code: code, name: String(r['股票名稱'] || ''), rounds: rounds });
  });

  /* 逾期未再提及的出場，要從出場日往後重算（2026/09/14）。

     判定出場的那天比出場日晚 10 個交易日。中間那段的每日績效，當時是以「持有中」記下的；
     刷新流程只從投稿日期往後重算，那段就一直留著舊值，與持股追蹤的出場日對不起來。
     起點往前延伸到這些出場日，之前的歷史點照舊保留。 */
  if (from) {
    holdingsByCode.forEach(function (h) {
      h.rounds.forEach(function (rd) {
        if (rd.s && rd.c && rd.c < from) { from = rd.c; }
      });
    });
    if (from !== requestedFrom) {
      Logger.log('績效歷史：有逾期出場的回合，重算起點由 ' + requestedFrom + ' 提前到 ' + from);
    }
    days = days.filter(function (d) { return d >= from; });
  }

  if (!holdingsByCode.length) {
    Logger.log('持股追蹤裡沒有可用的回合JSON。請先執行一次 rebuildHoldingsTrackerJob()，' +
               '它會把這一欄補上，再回來重算歷史。');
    return { ok: false, reason: '缺少回合JSON，請先重算持股追蹤' };
  }
  if (noJson) {
    Logger.log('※ 有 ' + noJson + ' 檔沒有回合JSON（舊版寫的列），這次不列入統計。');
  }

  /* 某一檔在 D 這一天的未實現報酬。沒持有、或算不出價就回 null。 */
  function retOn(h, d) {
    for (var i = 0; i < h.rounds.length; i++) {
      var rd = h.rounds[i];
      var open = String(rd.od || rd.o || '');
      if (!open || open > d) { continue; }
      if (rd.c && rd.c <= d) { continue; }        // 那一天之前已經出場
      var entry = Number(rd.e);
      if (!isFinite(entry) || entry <= 0) { return { held: true, ret: null }; }
      var px = closes[h.code] && closes[h.code][d];
      if (!isFinite(px) || px <= 0) { return { held: true, ret: null }; }
      return { held: true, ret: Math.round((px - entry) / entry * 10000) / 100 };
    }
    return { held: false, ret: null };
  }

  var out = [];
  days.forEach(function (d) {
    var total = 0, priced = [];
    holdingsByCode.forEach(function (h) {
      var v = retOn(h, d);
      if (!v.held) { return; }
      total++;
      if (v.ret !== null) { priced.push(v.ret); }
    });
    if (!total) { return; }                        // 那一天沒有任何持倉，不畫點
    /* 有持倉、但一檔都算不出報酬（沒有進場價或那天沒有收盤）＝那一天的績效是
       「不知道」，不是「0%」。寫出去的話 avg 與 pos 會是空字串，而讀取端的
       Number('') 是 0，圖上就變成一條平在 0 的線——看起來像那段期間績效掛零，
       實際上是根本沒有資料。這正是走勢圖從一月就開始有線的原因。 */
    if (!priced.length) { return; }
    var avg = null, pos = null;
    if (priced.length) {
      var sum = priced.reduce(function (s, x) { return s + x; }, 0);
      avg = Math.round(sum / priced.length * 100) / 100;
      pos = Math.round(priced.filter(function (x) { return x > 0; }).length
                       / priced.length * 1000) / 10;
    }
    out.push([d, total, total, avg === null ? '' : avg, pos === null ? '' : pos]);
  });

  withLock_(function () {
    var sh = getSheet_('每日績效');
    var headers = ['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例'];
    if (from) {
      /* 指定起點時只換掉那一段，起點之前的歷史點原樣保留。
         全表重寫會把「還沒有回合JSON 的那段更早期歷史」一起清掉。 */
      var vals = sh.getDataRange().getValues();
      var keep = [];
      for (var i = 1; i < vals.length; i++) {
        var dd = fmtDate_(vals[i][0]);
        // 資料起點之前的舊列不保留：那是本站還沒有任何紀錄的日子，
        // 留著就是把「沒有資料」畫成一條線。
        if (dd && dd < from && (!dataFloor || dd >= dataFloor)) { keep.push(vals[i].slice(0, 5)); }
      }
      out = keep.concat(out);
      out.sort(function (a, b) { return String(a[0]) < String(b[0]) ? -1 : 1; });
    }
    sh.clearContents();
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    if (out.length) { sh.getRange(2, 1, out.length, headers.length).setValues(out); }
  });

  CACHE.remove('perf_series');
  CACHE.remove('perf_series_v54_'+(typeof PERFORMANCE_START_DATE==='string'?PERFORMANCE_START_DATE:''));
  Logger.log('績效歷史重算完成：' + out.length + ' 個交易日，' +
             holdingsByCode.length + ' 檔納入計算（耗時 ' +
             Math.round((Date.now() - start) / 1000) + ' 秒）');
  return { ok: true, days: out.length, codes: holdingsByCode.length };
}


/** 空白、非數字一律回 null。不能用 Number()——Number('') 是 0，會把「不知道」變成 0。 */
function numOrNull_(v) {
  if (v === '' || v === null || v === undefined) { return null; }
  var n = Number(v);
  return isFinite(n) ? n : null;
}


/** 績效走勢，供折線圖 */
function getPerformanceSeries() {
  var floor=typeof PERFORMANCE_START_DATE==='string'?PERFORMANCE_START_DATE:'';
  var cacheKey='perf_series_v54_'+floor;
  var hit = CACHE.get(cacheKey);
  if (hit) { return JSON.parse(hit); }

  /* 交易日曆：日K快取裡出現過的所有日期。

     用它把非交易日剔掉。排程原本就只在交易日跑，但實際上有兩條路會塞進
     不該有的日期：回補程式往前補的時候是按日曆天推的，以及有人在編輯器裡
     手動執行過一次。那幾點畫在折線圖上就是週六日突然多一個轉折，
     而那一天根本沒有收盤價，等於憑空多一段走勢。

     快取整個讀不到時不過濾——那時全部都會被當成非交易日剔掉，
     圖會整片空白，比多幾個點糟得多。 */
  /* v54：交易日改看休市日表，不再看日K快取出現過哪些日期。

     2026/09/23 走勢停在 9/18 的原因之一：補日K那幾天沒跑完，日K快取裡沒有 9/21 之後的日期，
     於是就算那幾天有記下績效，也會在這裡被當成「非交易日」濾掉。
     休市日表（whyClosed_）不會跟著補日K落後；週末與國定休市照樣剔除，原本要擋的東西一樣擋得住。 */

  /* 同一天只留一筆，留最後寫進去的那一筆。

     snapshotPerformanceJob 自己會覆蓋同一天，所以正常不會重複；
     重複來自回補與手動補跑——那兩條路都是直接 appendRow。
     重複的兩筆日期一樣、數字不一樣，畫在折線圖上是同一個 x 有兩個 y，
     圖表函式庫會直接把後面那一段畫成一條垂直線。 */
  var byDate = {};
  var order = [];
  readSheetObjects_('每日績效').forEach(function (r) {
    var d = fmtDate_(r['日期']);
    if (!d || (floor&&d<floor)) { return; }
    if (!isTradingDateStr_(d)) { return; }
    if (!byDate[d]) { order.push(d); }
    byDate[d] = {
      date: d,
      total: Number(r['追蹤檔數']) || 0,
      holding: Number(r['持有檔數']) || 0,
      /* 空白代表「那一天算不出來」，不是 0。
         Number('') 會回 0，把「不知道」變成「零報酬」——這是走勢圖出現
         假的平線的直接原因。回 null，由前端略過這個點。 */
      avgReturn: numOrNull_(r['平均報酬']),
      positiveRatio: numOrNull_(r['正報酬比例'])
    };
  });

  var rows = order.sort().map(function (d) { return byDate[d]; });
  var dropped = readSheetObjects_('每日績效').length - rows.length;

  var lastDate = rows.length ? rows[rows.length - 1].date : '';
  var latestDay = latestTradingDayStr_();
  var out = {
    series: rows,
    since: rows.length ? rows[0].date : '',
    days: rows.length,
    lastDate: lastDate,
    latestTradingDay: latestDay,
    // 收盤後才會記當天；盤中看到「最後一點是昨天」是正常的，只有落後超過一個交易日才標示待補。
    behind: lastDate ? tradingDaysAfter_(lastDate, latestDay) : 0,
    note: '僅呈現 '+(floor||'本站開始紀錄日')+' 起有來源依據的績效；更早日期視為無資料。' +
          (dropped > 0 ? '（已略過 ' + dropped + ' 筆重複或非交易日的紀錄）' : '')
  };
  CACHE.put(cacheKey, JSON.stringify(out), 1800);
  return out;
}



/* ------------------------------------------------------------------ *
 * 補「完全沒有日K」的那幾檔
 *
 * 為什麼要跟分批補日K分開
 * ----------------------
 * backfillDailyKChunk_ 是照代號排序、用游標一批一批往下走的，設計目的是
 * 把整個追蹤宇宙的一年份K線刷新一遍。它沒有優先順序——排在後面的代號
 * 要等前面全部跑完才輪得到。
 *
 * 但「完全沒有資料」與「資料有點舊」是兩件事，代價差很多：
 *   資料舊　  K線圖少了最後幾根，圖還是畫得出來，報酬只是差一點。
 *   完全沒有　進場價、現價、報酬三欄全空，網站上顯示
 *             「取不到進場價。可到後台按補齊日K並重算」，看起來像壞掉。
 *
 * 而且會踩到的幾乎都是「今天第一次被講到的股票」——它剛被寫進操作紀錄，
 * 排序上不知道落在哪裡，很可能這一輪根本輪不到，於是那句話會掛在網站上
 * 一整天，甚至更久。使用者看到的是一檔沒有任何數字的股票。
 *
 * 這一支只做一件事：找出「一列都沒有」的代號，馬上把它們補起來。
 * 通常是零到三檔，幾秒鐘就好，所以可以放在每次重算持股追蹤之前無條件跑。
 * 已經有資料的一概不碰，不會跟分批補日K重複做工。
 * ------------------------------------------------------------------ */

// 補不到的原因記在這裡，讓網站上的說明能講出真正的理由，
// 而不是叫使用者去按一個按不好的按鈕。
var DK_MISSING_PROP = "dailyKMissingReason";

/* 確認過「不是台股代號」的那些。
 *
 * 語音轉文字聽錯數字、或模型自己補了一個代號時，會產生根本不存在的號碼
 * （實際發生過：茂聯 2155、和生堂 3182）。這種代號每天都會被算成
 * 「完全沒有日K」，於是每天去行情商敲一次、每天收一個 404、每天洗兩行紀錄，
 * 而且永遠不會好。
 *
 * 記下來就不再重試。判定條件是兩個同時成立，不是只看 404：
 *   1. 這個代號不在股票對照表裡（上市與上櫃都沒有）
 *   2. 行情商回 404
 * 只有 404 不夠——真的存在的股票也可能因為對方一時的問題回 404。
 * 而只要它哪天出現在對照表裡（例如新上市），下一輪就會自動從這份清單移除。 */
var DK_GHOST_PROP = "dailyKGhostCodes";

/** 讀確認過不存在的代號。回傳 { 代號: 1 }。 */
function dailyKGhosts_() {
  try {
    var raw = PropertiesService.getScriptProperties().getProperty(DK_GHOST_PROP);
    if (!raw) { return {}; }
    var out = {};
    JSON.parse(raw).forEach(function (c) { out[String(c)] = 1; });
    return out;
  } catch (e) { return {}; }
}

/** 寫回確認過不存在的代號。 */
function setDailyKGhosts_(obj) {
  var list = Object.keys(obj || {});
  var pr = PropertiesService.getScriptProperties();
  if (!list.length) { pr.deleteProperty(DK_GHOST_PROP); return; }
  try { pr.setProperty(DK_GHOST_PROP, JSON.stringify(list)); } catch (e) { /* 忽略 */ }
}

/** 在編輯器直接執行：清掉這份清單，讓那些代號下一輪重新試一次。 */
function resetDailyKGhosts() {
  PropertiesService.getScriptProperties().deleteProperty(DK_GHOST_PROP);
  Logger.log("已清空「查無此股」清單，下一次補日K會重新試一遍。");
}

/** 追蹤宇宙裡，日K快取一列都沒有的代號。 */
function missingDailyKCodes_() {
  var have = {};
  var rows = getSheet_("日K快取").getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    var c = String(rows[i][0]).trim();
    if (c) { have[c] = 1; }
  }
  return trackedCodes_().filter(function (c) { return !have[c]; });
}

/**
 * 把完全沒有日K的那幾檔補起來。回傳 { checked, filled, failed, list }。
 *
 * maxSec 是自己給自己的時間上限，預設 60 秒。這一支會被放在別人的流程中間，
 * 不能因為某一檔一直取不到就把呼叫端的額度吃光。
 */
function fillMissingDailyK_(maxSec) {
  var start = Date.now();
  var budget = (maxSec || 60) * 1000;

  var todo = missingDailyKCodes_();
  if (!todo.length) {
    PropertiesService.getScriptProperties().deleteProperty(DK_MISSING_PROP);
    return { checked: 0, filled: 0, failed: 0, list: [] };
  }

  Logger.log("有 " + todo.length + " 檔完全沒有日K，先補這幾檔：" + todo.join("、"));

  var rng = dailyKRange_();   // 一年少一天：富果單次查詢要小於一年
  var from = rng.from, to = rng.to;

  var useFugle = hasFugle_();
  var all = [], ok = [], why = {};

  // 對照表用來判斷「這個代號到底存不存在」。讀不到就當作空的，
  // 那樣不會有任何代號被判成幽靈，只是回到原本每天重試的行為。
  var cmap = {};
  try { cmap = (loadCodeMap_() || {}).byCode || {}; } catch (e) { cmap = {}; }
  var ghosts = dailyKGhosts_();
  var ghostDirty = false;

  for (var i = 0; i < todo.length; i++) {
    if (Date.now() - start > budget) {
      Logger.log("  時間到，剩下的 " + (todo.length - i) + " 檔留給下一次");
      break;
    }
    var code = todo[i];

    // 已經確認過不是台股代號的，連敲都不用敲。
    // 但只要它出現在對照表裡（例如真的新上市了），就把它從清單移除再試一次。
    if (ghosts[code]) {
      if (cmap[code]) {
        delete ghosts[code];
        ghostDirty = true;
        Logger.log("  " + code + "　已出現在對照表，從「查無此股」清單移除，重新嘗試");
      } else {
        why[code] = "查無此股：這個代號不在上市櫃清單，行情商也沒有這一檔";
        Logger.log("  " + code + "　跳過：確認過不是台股代號，不再重試");
        continue;
      }
    }

    try {
      var rows;
      if (useFugle) {
        rows = fugleHistorical_(code, from, to);
      } else {
        // 沒有 Fugle 金鑰時只抓最近三個月就好。這一支的目的是「讓進場價算得出來」，
        // 不是把歷史補齊，而證交所逐月抓十二個月會遠遠超過這裡的時間預算。
        rows = [];
        var cur = new Date();
        cur.setMonth(cur.getMonth() - 2);
        for (var mm = 0; mm < 3; mm++) {
          try {
            rows = rows.concat(
              fetchTwseMonth_(code, Utilities.formatDate(cur, TZ, "yyyyMM")) || []);
          } catch (e2) { /* 單月失敗不影響其他月 */ }
          cur.setMonth(cur.getMonth() + 1);
          Utilities.sleep(600);
        }
      }
      if (!rows || !rows.length) { throw new Error("查無這一檔的歷史行情"); }
      rows.forEach(function (r) {
        all.push([code, r.date, r.open, r.high, r.low, r.close, r.volume]);
      });
      ok.push(code);
      Logger.log("  " + code + "　補進 " + rows.length + " 根");
    } catch (e) {
      why[code] = String(e && e.message || e).slice(0, 60);
      Logger.log("  " + code + "　補不到：" + why[code]);

      /* 兩個條件同時成立才判定「這個代號不存在」：對照表裡沒有，而且
         行情商回 404。少了任何一個都可能冤枉真的存在的股票——
         404 有可能是對方一時的問題，不在對照表也可能只是清單還沒更新。 */
      var is404 = (e && e.httpCode === 404) || why[code].indexOf("HTTP 404") >= 0;
      if (is404 && !cmap[code]) {
        ghosts[code] = 1;
        ghostDirty = true;
        Logger.log("  " + code + "　既不在上市櫃清單、行情商也查無，之後不再重試" +
                   "（要重試請執行 resetDailyKGhosts()）");
      }
    }
    // 節奏由 fugleHistPace_ 控制，不再固定睡 1.1 秒
  }

  if (all.length) {
    withLock_(function () {
      var sh = getSheet_("日K快取");
      // 補日K的那一輪可能剛好在這段期間把同一檔寫進去了。已經有列的代號就不再附加，
      // 否則同一檔同一天會出現兩列。
      var lr = sh.getLastRow(), present = {};
      if (lr >= 2) {
        sh.getRange(2, 1, lr - 1, 1).getValues().forEach(function (v) { present[String(v[0]).trim()] = 1; });
      }
      var add = all.filter(function (row) { return !present[row[0]]; });
      if (!add.length) { return; }
      var need = lr + add.length - sh.getMaxRows();
      if (need > 0) { sh.insertRowsAfter(sh.getMaxRows(), need); }
      sh.getRange(lr + 1, 1, add.length, 7).setValues(add);
    });
    _DK_INDEX = null;
    CACHE.remove("kcache_meta");
    kcDrop_("dk_", ok);
    kcDrop_("dk2_", ok);
  }

  if (ghostDirty) { setDailyKGhosts_(ghosts); }

  // 補不到的原因留給前端解釋用。全部補成功就把它清掉。
  var pr = PropertiesService.getScriptProperties();
  if (Object.keys(why).length) {
    try { pr.setProperty(DK_MISSING_PROP, JSON.stringify(why)); } catch (e) { /* 忽略 */ }
  } else {
    pr.deleteProperty(DK_MISSING_PROP);
  }

  return { checked: todo.length, filled: ok.length,
           failed: Object.keys(why).length, list: ok };
}

/** 補不到日K的原因表。給前端組說明文字用，讀不到就回空物件。 */
function dailyKMissingReasons_() {
  try {
    var raw = PropertiesService.getScriptProperties().getProperty(DK_MISSING_PROP);
    return raw ? JSON.parse(raw) : {};
  } catch (e) { return {}; }
}

/** 手動跑一次，看它會補哪幾檔。 */
function fillMissingDailyK() {
  var r = fillMissingDailyK_(120);
  Logger.log("完全沒有日K的 " + r.checked + " 檔，補成功 " + r.filled +
             "、仍取不到 " + r.failed);
  return r;
}
/* ------------------------------------------------------------------ *
 * 分批補日K（網站／GitHub 的 step=dailyk）
 *
 * 透過網頁請求呼叫時，Google 前端會比 6 分鐘更早切斷連線，所以每次只做約 45 秒。
 * 與 backfillDailyKJob 走同一支 dailyKBackfillRound_、共用同一份進度：
 * 這裡沒補完的，14:35 的排程或手動執行會接著做，反之亦然。
 *
 * 舊版用「第幾檔」的數字游標，代號清單一增減，游標就對到別檔，
 * 會跳過或重做；現在記的是「做到哪個代號」，清單變動也不會錯位。
 * ------------------------------------------------------------------ */

var DK_CURSOR_PROP = 'dailyKCursor';
var DK_FINAL_AFTER_ = '16:30';   // 富果歷史行情每交易日盤後 16:30 前完成更新   // 舊版數字游標，只在 resetDailyKCursor 時順手清掉

/**
 * 跑一批。回傳 { done, processed, total, ok, failed, failedList, note }。
 * 金鑰無效或今日額度用完時回 { ok:false, error }，呼叫端會標成失敗而不是一直重打。
 */
function backfillDailyKChunk_(maxSec) {
  var sec = Math.max(25, maxSec || 45);
  var start = Date.now();
  /* 今天 16:30 之後開始的一輪已經完整做完：那就是今天的最終資料，不必再從第一檔重抓一遍。
     GitHub 在晚上寫入紀錄時，刷新鏈的「補齊日K」原本每次都會重開一整輪（幾分鐘、上百次請求）。
     當天才第一次出現、還沒有日K的股票，由重算持股追蹤開頭的 fillMissingDailyK_ 補上。 */
  var cur = dailyKState_();
  var today = Utilities.formatDate(new Date(start), TZ, 'yyyy/MM/dd');
  if (cur && cur.finishedAt && cur.day === today && String(cur.startedAt || '').slice(11, 16) >= DK_FINAL_AFTER_) {
    var p0 = dailyKProgress_(cur);
    return { done: true, processed: p0.processed, total: p0.total, ok: 0, failed: 0, failedList: [],
             note: '補日K：今天 ' + String(cur.startedAt).slice(11, 16) + ' 開始的完整一輪已於 ' +
                   String(cur.finishedAt).slice(11, 19) + ' 完成，不重抓' };
  }
  var r = dailyKBackfillRound_({
    deadline: start + (sec - 15) * 1000,           // 留 15 秒寫回試算表並送出 JSON
    waitMs: Math.min(20000, (sec - 25) * 1000),    // 排程正在補時等一下，不要立刻空轉回去
    source: '分批請求（step=dailyk）'
  });
  Logger.log('補日K（分批）：' + r.note);
  if (r.fatal) {
    return { ok: false, done: false, error: '補日K停止：' + r.note,
             processed: r.processed, total: r.total };
  }
  return { done: r.done, processed: r.processed, total: r.total,
           ok: r.ok, failed: r.failed, failedList: r.failedList,
           note: '補日K：' + r.note };
}

/** 在編輯器執行：清掉補日K的進度與排定的續跑，下一次從第一檔重新開始。 */
function resetDailyKCursor() {
  var props = PropertiesService.getScriptProperties();
  var lease = null;
  try { lease = JSON.parse(props.getProperty(DK_LEASE_PROP) || 'null'); } catch (e) { lease = null; }
  if (lease && lease.until > Date.now()) {
    // 正在寫的那一段結束時會把記憶體裡的進度寫回去，這時清掉等於沒清，還會跟它搶。
    Logger.log('補日K正在寫入中（' + new Date(lease.until) + ' 前），請等這一段結束再重設。');
    return false;
  }
  props.deleteProperty(DK_STATE_PROP);
  props.deleteProperty(DK_LEASE_PROP);
  props.deleteProperty(DK_CURSOR_PROP);
  clearDailyKContinue_();
  Logger.log('已重設補日K進度並取消排定的續跑；下一次補日K會從第一檔重新開始。');
  return true;
}


/* ==================================================================
 * 盤中快照與小時K
 *
 * 這兩張分頁一直是空的。原因不是連不上，是從來沒有人寫進去——
 * Setup 建了表、前端也寫了「60 分 K 由本站自行累積的盤中快照聚合而成」，
 * 但寫入的那一支與收盤聚合的那一支都不存在。
 *
 * 補上：盤中每五分鐘記一次現價與累計量，收盤後把同一小時的聚合成一根 K 棒。
 * 證交所不提供歷史盤中資料，所以這種資料只能從今天開始往後累積，
 * 往前一天都補不了——這也是為什麼它值得每天做。
 * ================================================================== */

/** 盤中每 5 分鐘記一次。只在交易時段跑，其餘時間立刻返回。 */
function snapshotIntradayJob() {
  if (!isTradingNow_()) { return; }

  var codes = trackedCodes_();
  if (!codes.length) { return; }

  var now = new Date();
  var d = Utilities.formatDate(now, TZ, 'yyyy/MM/dd');
  var t = Utilities.formatDate(now, TZ, 'HH:mm');

  /* 讀既有的即時快取，不另外對外請求。

     refreshQuoteCacheJob 本來就每幾分鐘更新一次它，兩邊各抓一次
     只是把同一份資料抓兩遍，還多一份被對方限流的風險。 */
  var q = getQuoteCache();
  var rows = [];
  codes.forEach(function (c) {
    var v = q[c];
    if (!v || v.last == null) { return; }
    rows.push([d, t, c, v.last, v.volume || 0]);
  });
  if (!rows.length) { return; }

  withLock_(function () {
    var sh = getSheet_('盤中快照');
    var first = sh.getLastRow() + 1;
    sh.getRange(first, 2, rows.length, 1).setNumberFormat('@');   // "09:05" 不要被轉成時間值
    sh.getRange(first, 1, rows.length, 5).setValues(rows);
  });
}


/**
 * 收盤後把當天的盤中快照聚合成小時K，然後清掉快照。
 *
 * 快照是暫存：一天幾十檔乘上五分鐘一筆就是好幾千列，留著會讓整份試算表
 * 越來越慢，而聚合完之後那些原始點就沒有用了。
 */
/** 盤中快照整張讀出來，時間欄以畫面上的文字為準（見 Quoteservice.gs 的 hourSlot_）。 */
function readSnapshotRows_() {
  var sh = getSheet_('盤中快照');
  var last = sh.getLastRow(), width = sh.getLastColumn();
  if (last < 2 || width < 1) { return []; }
  var head = sh.getRange(1, 1, 1, width).getValues()[0].map(function (h) { return String(h).trim(); });
  var col = function (n) { return head.indexOf(n); };
  if (col('時間') < 0) { throw new Error('盤中快照缺「時間」欄'); }
  var values = sh.getRange(2, 1, last - 1, width).getValues();
  var shown = sh.getRange(2, col('時間') + 1, last - 1, 1).getDisplayValues();
  return values.map(function (row, i) {
    return {
      date: fmtDate_(row[col('日期')]),
      time: hourSlot_(row[col('時間')], shown[i][0]),   // HH:mm
      code: String(row[col('代號')] || '').trim(),
      price: Number(row[col('成交價')]) || 0,
      cumVolume: Number(row[col('累計成交量')]) || 0
    };
  });
}

/* 名字不能叫 aggregateHourlyJob。
   Quoteservice.gs 已經有一支同名的（向 Fugle 要 60 分 K），
   而 Apps Script 所有 .gs 共用一個全域範圍——後載入的會把前一個蓋掉，
   於是其中一件事永遠不會發生，而且看不出來。 */
function aggregateSnapshotJob() {
  var d = todayStr_();
  var snaps;
  try { snaps = readSnapshotRows_(); } catch (e) { return; }
  if (!snaps.length) { return; }

  // 代號 → 時段 → 這一小時內的每一筆
  var byCode = {};
  snaps.forEach(function (r) {
    if (r.date !== d || !r.code || !r.time || !r.price) { return; }
    var slot = r.time.slice(0, 2) + ':00';
    if (!byCode[r.code]) { byCode[r.code] = {}; }
    if (!byCode[r.code][slot]) { byCode[r.code][slot] = []; }
    byCode[r.code][slot].push({ t: r.time, p: r.price, v: r.cumVolume });
  });

  var out = [];
  Object.keys(byCode).sort().forEach(function (code) {
    /* 成交量（2026/09/16 v38 修正）。快照記的是「當日累計成交量」（張），
       這一小時的量＝這一小時最後一筆的累計 − 上一小時最後一筆的累計；第一個小時減 0。
       先前減的是「這一小時第一筆」，於是兩個快照之間跨整點的成交整段不見——
       09:00 那一根連開盤集合競價的量都沒算進去，各小時加總也對不回當日累計。
       累計量偶爾會因為報價沒取到而是 0，取「到目前為止的最大值」當累計，避免算出負數。 */
    var prevCum = 0;
    Object.keys(byCode[code]).sort().forEach(function (slot) {
      var pts = byCode[code][slot].sort(function (a, b) { return a.t < b.t ? -1 : a.t > b.t ? 1 : 0; });
      if (!pts.length) { return; }
      var his = pts.map(function (x) { return x.p; });
      var cum = Math.max.apply(null, [prevCum].concat(pts.map(function (x) { return x.v; })));
      var vol = cum - prevCum;
      prevCum = cum;
      out.push([d, slot, code,
                pts[0].p, Math.max.apply(null, his), Math.min.apply(null, his),
                pts[pts.length - 1].p, vol]);
    });
  });

  if (!out.length) { return; }

  withLock_(function () {
    var hk = getSheet_('小時K');
    // 同一天重跑就先清掉當天的，避免每跑一次就多一份
    var vals = hk.getDataRange().getValues();
    for (var i = vals.length - 1; i >= 1; i--) {
      if (fmtDate_(vals[i][0]) === d) { hk.deleteRow(i + 1); }
    }
    var first = hk.getLastRow() + 1;
    hk.getRange(first, 2, out.length, 1).setNumberFormat('@');   // 時段寫成純文字
    hk.getRange(first, 1, out.length, 8).setValues(out);

    // 聚合完就清空快照。它是暫存，留著只會讓試算表越來越慢。
    var sn = getSheet_('盤中快照');
    if (sn.getLastRow() > 1) {
      sn.getRange(2, 1, sn.getLastRow() - 1, sn.getLastColumn()).clearContent();
    }
  });

  _HK_INDEX = null;
  kcDrop_('hk2_', Object.keys(byCode));
  Logger.log('小時K：' + d + ' 聚合出 ' + out.length + ' 根');
}


/* ==================================================================
 * 使用紀錄
 *
 * 記什麼：哪一個訪客、在哪一頁、做了什麼、停留多久。
 * 不記什麼：IP、Email（除非訪客自己在訂閱欄填了）、任何可以直接指向
 * 某一個人的東西。訪客識別是瀏覽器本地產生的一串隨機碼，
 * 清掉瀏覽器資料就換一個新的，我們這邊無從還原成任何真人身分。
 *
 * 為什麼記在試算表而不是外部分析服務：這個網站的所有資料本來就都在
 * 同一份試算表裡，多接一個外部服務等於把訪客的瀏覽行為送到第三方，
 * 那是完全不必要的擴散。
 * ================================================================== */

var USAGE_SHEET = '使用紀錄';

/* 一次最多收幾筆。前端是批次送的，開著分頁一整天也只會累積幾十筆，
   但這裡仍然要有上限——這支是公開的端點，沒有上限等於讓任何人
   一次寫幾萬列進來。 */
var USAGE_MAX_BATCH = 40;

/**
 * 前端送來的一批使用紀錄。
 *
 * events：[{ page, action, target, seconds }]
 * who：瀏覽器本地產生的隨機識別碼
 */
function apiLogUse(who, events) {
  try {
    if (!events || !events.length) { return { ok: true, n: 0 }; }

    var id = String(who || '').slice(0, 40).replace(/[^\w-]/g, '');
    if (!id) { return { ok: false, reason: 'no id' }; }

    var now = nowStamp_();
    var rows = [];
    events.slice(0, USAGE_MAX_BATCH).forEach(function (e) {
      if (!e) { return; }
      /* 「進入分頁」不記。

         它與「停留」是同一件事的兩半，而「停留」那一筆本來就帶著頁面名稱
         與秒數——進入的那一筆只是多一列沒有資訊量的紀錄，
         而且每切一次分頁就多一列，是使用紀錄裡數量最多、價值最低的一種。

         擋在後端而不是只改前端：已經開著頁面的瀏覽器、以及快取住的舊 HTML，
         都還會繼續送這個動作。前端改完要等快取過期才會生效，後端擋掉是立刻的。
         前端那邊也一併不再送，省一次往返與一次寫入。 */
      if (String(e.action || '') === '進入分頁') { return; }
      rows.push([
        now, id,
        String(e.page || '').slice(0, 40),
        String(e.action || '').slice(0, 40),
        String(e.target || '').slice(0, 80),
        Math.max(0, Math.min(86400, Number(e.seconds) || 0)),
        String(e.device || '').slice(0, 20)
      ]);
    });
    if (!rows.length) { return { ok: true, n: 0 }; }

    withLock_(function () {
      var sh = getSheet_(USAGE_SHEET);
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, 7).setValues(rows);
    });
    return { ok: true, n: rows.length };
  } catch (e) {
    // 記不下來不該讓使用者看到任何東西壞掉。這是旁支，不是主線。
    return { ok: false, reason: String(e && e.message || e).slice(0, 80) };
  }
}

/**
 * 使用紀錄留太久會讓試算表變慢，而它的價值本來就是短期的
 * （這一週大家在看什麼），不是永久檔案。留 90 天。
 */
function pruneUsageLogJob() {
  var keepDays = 90;
  var cut = Utilities.formatDate(
    new Date(Date.now() - keepDays * 86400000), TZ, 'yyyy/MM/dd');
  var sh;
  try { sh = getSheet_(USAGE_SHEET); } catch (e) { return; }
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return; }

  var n = 0;
  withLock_(function () {
    for (var i = vals.length - 1; i >= 1; i--) {
      var d = String(vals[i][0] || '').slice(0, 10).replace(/-/g, '/');
      if (d && d < cut) { sh.deleteRow(i + 1); n++; }
    }
  });
  if (n) { Logger.log('使用紀錄：清掉 ' + n + ' 列超過 ' + keepDays + ' 天的'); }
}

/** 只補指定區間的空缺；已有完整OHLCV不重抓，來源回空不補造上市前資料。 */
function missingKDateRanges_(rows,from,to,today,closed,noClamp) {
  var valid={};(rows||[]).forEach(function(r){
    if(validDailyK_(r)){valid[r.date.slice(0,10).replace(/-/g,'/')]=true;}
  });
  var known=Object.keys(valid).sort(),start=String(from).replace(/-/g,'/'),end=String(to).replace(/-/g,'/');
  var ranges=[],first='',last='';
  function flush(){if(first){ranges.push({from:first.replace(/\//g,'-'),to:last.replace(/\//g,'-')});first='';last='';}}
  for(var d=new Date(start.replace(/\//g,'-')+'T00:00:00Z');isFinite(d.getTime());d.setUTCDate(d.getUTCDate()+1)){
    var key=d.toISOString().slice(0,10).replace(/-/g,'/');if(key>end){break;}
    if(valid[key]){flush();continue;}
    if(d.getUTCDay()===0||d.getUTCDay()===6||(key===today&&!closed)){continue;}
    if(typeof isMarketHoliday_==='function'&&isMarketHoliday_(new Date(key.replace(/\//g,'-')+'T12:00:00+08:00'))){continue;}
    if(!first){first=key;}last=key;
  }flush();return ranges;
}
function mergeDailyK_(oldRows,newRows) {
  var by={};(oldRows||[]).forEach(function(r){by[r.date]=r;});
  (newRows||[]).forEach(function(r){if(validDailyK_(r)&&!validDailyK_(by[r.date])){by[r.date]=r;}});
  return Object.keys(by).sort().map(function(k){return by[k];});
}
function validDailyK_(r){return !!r&&[r.open,r.high,r.low,r.close].every(function(v){return v!=null&&isFinite(v)&&v>0;})&&r.high>=Math.max(r.open,r.close,r.low)&&r.low<=Math.min(r.open,r.close,r.high)&&r.volume!=null&&isFinite(r.volume)&&r.volume>=0;}
/* 「這一檔在某天以前沒有日K」（v54）。
   補日K每天把近 12 個月的缺口問一次；上市不滿一年的股票，上市前那一整段每天都回空，
   先前只記在六小時的快取裡，隔天又問一次。這裡把「第一根有效日K之前問過而且確定是空的」記成下限，
   之後的每一輪直接從下限開始算缺口。只記在第一根有效日K之前的區間，中間的缺口（停牌、來源漏資料）照樣每輪重試。
   修復模式（repairDailyKCache）不看這個下限。重來用 resetDailyKFloor()。 */
var DK_FLOOR_PROP_ = 'dailyKFloorV54';
function dailyKFloors_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty(DK_FLOOR_PROP_) || '{}'); } catch (e) { return {}; }
}
function setDailyKFloor_(code, day) {
  try {
    var m = dailyKFloors_();
    if (m[code] === day) { return; }
    m[code] = day;
    var keys = Object.keys(m);
    while (keys.length > 600) { delete m[keys.shift()]; }   // 單一屬性值有大小上限
    PropertiesService.getScriptProperties().setProperty(DK_FLOOR_PROP_, JSON.stringify(m));
  } catch (e) { Logger.log('日K下限記不住（不影響補日K）：' + e); }
}
function resetDailyKFloor() {
  PropertiesService.getScriptProperties().deleteProperty(DK_FLOOR_PROP_);
  Logger.log('已清除日K下限，下一輪補日K會重新確認每一檔最早的資料。');
}

function fetchMissingDailyK_(code,from,to,rows,deadline) {
  var now=new Date(),today=Utilities.formatDate(now,TZ,'yyyy/MM/dd'),closed=Number(Utilities.formatDate(now,TZ,'HHmm'))>=1630;
  var floor=dailyKFloors_()[code]||'';
  var startFrom=floor&&floor.replace(/\//g,'-')>String(from)?floor.replace(/\//g,'-'):from;
  var firstValid=(rows||[]).filter(validDailyK_).map(function(r){return String(r.date).slice(0,10).replace(/-/g,'/');}).sort()[0]||'';
  var ranges=missingKDateRanges_(rows,startFrom,to,today,closed),out=[];
  for(var i=0;i<ranges.length;i++){
    if(deadline&&Date.now()>deadline-15000){out.pending=true;break;}
    var r=ranges[i],key='dk_empty_'+code+'_'+r.from+'_'+r.to;
    if(CACHE.get(key)){continue;}
    var fetched=fugleHistorical_(code,r.from,r.to);
    if(!fetched.length){
      CACHE.put(key,'1',21600);
      // 第一根有效日K之前的整段都是空的＝上市前：記成下限，之後不再問這一段。
      if(firstValid&&r.to.replace(/-/g,'/')<firstValid){setDailyKFloor_(code,firstValid);}
    }
    out=out.concat(fetched);
  }return out;
}
