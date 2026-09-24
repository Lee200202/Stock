/**
 * 檔案：SheetService.gs
 * 試算表讀寫。所有寫入一律經過 LockService，避免與 GitHub Actions 同時寫入造成資料競爭
 * （規格書 3.5.3 節）。
 */

var TZ = 'Asia/Taipei';
var NOT_MENTIONED = '本支影片未說明';

function fmtDate_(v) {
  if (!v) { return ''; }
  if (Object.prototype.toString.call(v) === '[object Date]') {
    return Utilities.formatDate(v, TZ, 'yyyy/MM/dd');
  }
  return String(v).trim().replace(/-/g, '/');
}

function todayStr_() {
  return Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
}

/** 統一的寫入鎖 */
function withLock_(fn) {
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try { return fn(); } finally { lock.releaseLock(); }
}

/* ------------------------------------------------------------------ *
 * 4.1 逐日績效總覽
 * ------------------------------------------------------------------ */

/** 把當天會員簡訊的解析明細即時併入今日買賣與持股清單 */
function mergeSmsItemsIntoTrades_(d, todayTrades, todayHolds) {
  var smsList = [];
  try { smsList = readSheetObjects_('會員簡訊'); } catch (e) { return; }
  var daySms = smsList.filter(function (r) { return cmDate_(r['發文時間']) === d; });
  if (!daySms.length) { return; }

  daySms.forEach(function (r) {
    var rawDetail = String(r['解析明細'] || '').trim();
    if (!rawDetail || rawDetail === '[]') { return; }
    try {
      var items = JSON.parse(rawDetail);
      if (!Array.isArray(items)) { return; }
      items.forEach(function (x) {
        var name = String(x.name || '').trim();
        var code = String(x.code || '').trim();
        var dir = String(x.dir || x.action || '').trim();
        if (!name) { return; }

        if (dir === '會員持股' || dir === '續抱' || dir === '持有') {
          var existsHold = todayHolds.some(function (h) {
            return String(h['股票名稱'] || '').trim() === name;
          });
          if (!existsHold) {
            todayHolds.push({
              '日期': d,
              '股票名稱': name,
              '代號': code || '代號待確認',
              '方向': '續抱',
              '目前立場': '續抱',
              '說明重點': String(x.reason || x.note || '簡訊通知持股續抱')
            });
          }
        } else if (dir) {
          var existsTrade = todayTrades.some(function (t) {
            return String(t['股票名稱'] || '').trim() === name &&
                   String(t['方向'] || '').indexOf(dir) === 0;
          });
          if (!existsTrade) {
            todayTrades.push({
              '日期': d,
              '股票名稱': name,
              '代號': code || '代號待確認',
              '方向': dir,
              '價位說明': String(x.priceText || x.price || '未說明'),
              '理由摘錄': String(x.reason || x.note || '簡訊通知即時操作')
            });
          }
        }
      });
    } catch (e) {}
  });
}

/** 取得最近一個有資料的交易日總覽（含會員通知即時紀錄） */
function getTodayOverview() {
  var trades = readSheetObjects_('操作紀錄');
  var holds = readSheetObjects_('會員持股');
  var videos = readSheetObjects_('影片清單');
  var smsList = [];
  try { smsList = readSheetObjects_('會員簡訊'); } catch (e) {}

  var dates = trades.map(function (r) { return fmtDate_(r['日期']); })
    .concat(holds.map(function (r) { return fmtDate_(r['日期']); }))
    .concat(smsList.map(function (r) { return cmDate_(r['發文時間']); }))
    .filter(String);
  var latest = dates.sort().pop() || '';

  var todayTrades = trades.filter(function (r) { return fmtDate_(r['日期']) === latest; });
  var todayHolds = holds.filter(function (r) { return fmtDate_(r['日期']) === latest; });
  var todaySms = smsList.filter(function (r) { return cmDate_(r['發文時間']) === latest; });

  // 會員通知即時併入總覽：若最新日有簡訊明細，即時合併至當日買賣與持股
  if (todaySms.length) {
    mergeSmsItemsIntoTrades_(latest, todayTrades, todayHolds);
  }

  var video = videos.filter(function (r) { return fmtDate_(r['發布日期']) === latest; })[0] || null;

  function pick(dir) {
    return todayTrades
      .filter(function (r) { return String(r['方向']).indexOf(dir) === 0; })
      .map(function (r) {
        return {
          name: r['股票名稱'],
          code: r['代號'] || '代號待確認',
          price: r['價位說明'] || '未說明',
          reason: naturalReason_(r['理由摘錄']) || '未說明'
        };
      });
  }

  var status = '';
  if (video && video['處理狀態']) {
    status = video['處理狀態'];
  } else if (todaySms.length) {
    status = '會員通知已即時併入總覽（盤後影片處理中）';
  }
  /* 影片還沒整理完（v54）。只有簡訊進來、影片還在處理時，觀望兩類是空的——
     那不是「當天影片沒有提到」，是還沒整理出來。前端據此改顯示「整理中」。 */
  var videoPending = !(video && String(video['處理狀態'] || '') === '完成') &&
    todayTrades.every(function (r) { return /^(CMONEY-|MANUALENTRY-)/.test(String(r['來源影片ID'] || '')) || !r['來源影片ID']; });
  /* 今天沒有直播（v54）：上游判定「今日無直播」時，觀望兩區不是「整理中」而是「今天沒有節目」。
     只有會員簡訊的日子，網站照常呈現簡訊的買賣與持股；郵件查詢那一天留空、不寄每日總覽。 */
  var noShow = false;
  if (videoPending) {
    try {
      noShow = readSheetObjects_('系統狀態').some(function (r) {
        return String(r['時間'] || '').indexOf(latest) === 0 && String(r['類別'] || '') === '今日無直播';
      });
    } catch (e) { noShow = false; }
  }

  /* 同一檔同一天已有買入或賣出，就不再列進觀望（v54，2026/09/22 聖暉同時在買入與觀望注意）。
     pipeline 寫入時已經處理；這裡是顯示端的第二道，涵蓋盤中簡訊即時併入、pipeline 還沒跑的那段時間。 */
  var tradedCodes = {};
  todayTrades.forEach(function (r) {
    var d = String(r['方向'] || '');
    if (/^[買賣]/.test(d) && r['代號']) { tradedCodes[String(r['代號']).trim()] = true; }
  });
  function pickWatch(dir) {
    return pick(dir).filter(function (x) { return !tradedCodes[String(x.code).trim()]; });
  }

  return {
    date: latest,
    hasData: !!latest,
    status: status,
    failReason: video ? video['失敗原因'] : '',
    buy: pick('買'),
    sell: pick('賣'),
    watchAvoid: pickWatch('觀望不碰'),
    watchWatch: pickWatch('觀望注意'),
    videoPending: videoPending,
    noShow: noShow,
    // 舊欄位保留，內容為兩類合併，避免其他呼叫端壞掉
    watch: pick('觀望不碰').concat(pick('觀望注意')).concat(pick('不碰')),
    // 首頁持股與「持股追蹤」頁保持同一個來源：持股追蹤裡狀態為持有中的那些。
    // 先前這裡只讀當天「會員持股」分頁，張震當天沒複述持股清單時就會空白，
    // 與追蹤頁對不起來。改用追蹤表後兩頁必然一致。
    //
    // 追蹤表只收「明確講過買入」的標的，而且一出現觀望不碰就平倉，
    // 所以這份清單不會再出現方向是觀望不碰的股票。
    holdings: (function () {
      var t = getHoldingsTracker();
      var heldMap = {};
      var out = (t.held || []).map(function (i) {
        heldMap[i.name] = true;
        return {
          name: i.name,
          code: i.valid ? i.code : '代號待確認',
          stance: (i.rounds > 1) ? ('持有中\u3000第 ' + i.rounds + ' 回合') : '持有中',
          note: i.latestReason || i.firstReason || '未說明',
          noteDate: i.latestReasonDate || ''
        };
      });
      // 當日簡訊有續抱但追蹤尚未涵蓋的，補充進持股清單
      todayHolds.forEach(function (h) {
        var hName = String(h['股票名稱'] || '').trim();
        if (hName && !heldMap[hName]) {
          heldMap[hName] = true;
          out.push({
            name: hName,
            code: h['代號'] || '代號待確認',
            stance: '持有中（簡訊續抱）',
            note: naturalReason_(h['說明重點'] || h['理由摘錄'] || '') || '未說明'
          });
        }
      });
      return out;
    })(),
    notMentioned: NOT_MENTIONED
  };
}

/**
 * 在編輯器直接選取執行：測試今日總覽（含會員簡訊即時併入結果）。
 * 在 Apps Script 的「執行紀錄」印出當前最新交易日、狀態、簡訊合併後的買賣與持股。
 */
function testTodayOverview() {
  var ov = getTodayOverview();
  Logger.log('================ 今日總覽測試紀錄 ================');
  Logger.log('最新日期：' + (ov.date || '無資料'));
  Logger.log('總覽狀態：' + (ov.status || '無特殊狀態'));
  Logger.log('是否有資料 (hasData)：' + ov.hasData);
  Logger.log('買進個股 (' + (ov.buy || []).length + ' 檔)：\n' + (ov.buy || []).map(function (i) { return '  - ' + i.name + ' (' + i.code + ') ' + i.price + '：' + i.reason; }).join('\n'));
  Logger.log('賣出個股 (' + (ov.sell || []).length + ' 檔)：\n' + (ov.sell || []).map(function (i) { return '  - ' + i.name + ' (' + i.code + ') ' + i.price + '：' + i.reason; }).join('\n'));
  Logger.log('觀望不碰 (' + (ov.watchAvoid || []).length + ' 檔)：\n' + (ov.watchAvoid || []).map(function (i) { return '  - ' + i.name + ' (' + i.code + ') ' + i.price + '：' + i.reason; }).join('\n'));
  Logger.log('觀望注意 (' + (ov.watchWatch || []).length + ' 檔)：\n' + (ov.watchWatch || []).map(function (i) { return '  - ' + i.name + ' (' + i.code + ') ' + i.price + '：' + i.reason; }).join('\n'));
  Logger.log('會員持股 (' + (ov.holdings || []).length + ' 檔)：\n' + (ov.holdings || []).map(function (i) { return '  - ' + i.name + ' (' + i.code + ') ' + i.stance + '：' + i.note; }).join('\n'));
  Logger.log('==================================================');
  return ov;
}

/**
 * 期間統計：買入標的自買入日收盤起算至最新收盤的報酬。
 * 明確標示計算基準與資料期間，不使用「訊號」「建議」等字眼（規格書 4.1、九節）。
 */
function getPerformanceSummary() {
  var trades = readSheetObjects_('操作紀錄').filter(function (r) {
    return String(r['方向']).indexOf('買') === 0 && r['代號'] && String(r['代號']).indexOf('待確認') < 0;
  });

  if (!trades.length) {
    return { count: 0, avgReturn: null, positiveRatio: null, from: '', to: '', basis: '' };
  }

  var dates = trades.map(function (r) { return fmtDate_(r['日期']); }).sort();
  var returns = [];

  trades.forEach(function (r) {
    var code = String(r['代號']).trim();
    var buyDate = fmtDate_(r['日期']);
    try {
      var candles = getCandles(code, 'day');
      if (!candles || !candles.length) { return; }
      var entry = candles.filter(function (c) { return c.date >= buyDate; })[0];
      var last = candles[candles.length - 1];
      if (!entry || !last || !entry.close) { return; }
      returns.push((last.close - entry.close) / entry.close * 100);
    } catch (err) {
      // 單檔取價失敗不影響整體統計
    }
  });

  if (!returns.length) {
    return { count: trades.length, avgReturn: null, positiveRatio: null, from: dates[0], to: dates[dates.length - 1], basis: '' };
  }

  var avg = returns.reduce(function (a, b) { return a + b; }, 0) / returns.length;
  var pos = returns.filter(function (x) { return x > 0; }).length / returns.length * 100;

  return {
    count: trades.length,
    priced: returns.length,
    avgReturn: Math.round(avg * 100) / 100,
    positiveRatio: Math.round(pos * 10) / 10,
    from: dates[0],
    to: dates[dates.length - 1],
    basis: '以買入日收盤價為基準，計算至最近一個交易日收盤價。'
  };
}

/* ------------------------------------------------------------------ *
 * 持股追蹤
 *
 * 使用者真正想知道的是：這檔他從什麼時候開始講、講了多久、到現在賺賠多少。
 *
 * 這裡曾經有個 bug：過濾條件是 /^(?:00981A|\d{4,6})$/，代號待確認的整列被扔掉，
 * 所以追蹤表永遠是空的，而且看不出原因。現在改成全部收進來，
 * 代號無效的標示為「代號待確認」並附上原因，讓問題浮出檯面而不是消失。
 *
 * 效能：進場價改讀日K快取，不再逐月向證交所抓。
 * ------------------------------------------------------------------ */

/**
 * 從「價位說明」欄解析出明確的成交價。
 *
 * 只認唯一、明確、可當成本的數字。含「約」「附近」「~」「跌到」這類
 * 模糊語一律不採用，寧可退回當日收盤價。
 * 「約 250 元」「120~125」「跌到 88 再買」都不是實際成交價，
 * 拿它們當成本會算出看起來精確但其實是編的報酬率。
 */
var VAGUE_WORDS = ['約', '附近', '左右', '上下', '~', '～',
                   '如果', '若', '漲停', '跌停', '之間', '區間'];

// 這些詞一出現，整句的數字就與成交價無關（財報、指數、技術線）。
// 「均線」特別重要：「等跌到 35 均線再買」裡的 35 是均線天數不是價格，
// 少了這一條就會把 35 當成買進價寫進表裡。
var NON_PRICE_WORDS = ['EPS', '每股', '年報', '財報', '營收', '毛利', '本益比',
                       '殖利率', '指數', '大盤', '成長',
                       '均線', '月線', '季線', '年線', '週線', '半年線'];

// 這些是「數量或金額單位」，只有緊接在數字後面時才代表非股價。
// 不能當成一般關鍵字比對：張震的「張」字會讓每一句含他名字的話都被誤判成成交量，
// 「說明重點」的「點」也會誤觸指數點位規則。必須要求前面真的有數字。
var NON_PRICE_UNITS = ['億', '兆', '萬張', '張', '口', '點', '塊', '萬元', '千萬'];

function hasNonPriceUnit_(s) {
  // 不用動態組 RegExp，字串裡的反斜線在不同層轉義很容易寫錯。
  // 直接掃字元：找到單位詞之後，往前跳過空白，看前一個字元是不是數字。
  var t = String(s || '');
  for (var i = 0; i < NON_PRICE_UNITS.length; i++) {
    var u = NON_PRICE_UNITS[i];
    var at = t.indexOf(u);
    while (at >= 0) {
      var k = at - 1;
      while (k >= 0 && (t.charAt(k) === ' ' || t.charAt(k) === '\u3000')) { k--; }
      if (k >= 0 && t.charAt(k) >= '0' && t.charAt(k) <= '9') { return true; }
      at = t.indexOf(u, at + 1);
    }
  }
  return false;
}

// 概數：「1400 多」「兩百出頭」。這種數字不能當成本，拿去算報酬是編的。
function isVagueNumber_(s) {
  return /\d\s*(多|出頭)/.test(s);
}

/**
 * 價位說明與方向是否互相矛盾。
 * 方向是買入、內容卻只講賣出（例如「255 以上全部賣掉」誤掛在買入那一列），
 * 那個數字描述的是另一個動作，不能拿來當這一筆的成交價。
 */
function contradictsDirection_(text, kind) {
  var s = String(text || '');
  if (kind === 'buy') {
    return /賣|出清|全部出|獲利了結|停損/.test(s) && !/買|承接|進場|加碼/.test(s);
  }
  if (kind === 'sell') {
    return /買|承接|進場|加碼/.test(s) && !/賣|出清|出場|獲利了結|停損/.test(s);
  }
  return false;
}

/* 技術指標裡的數字不是股價。

   實際算錯過一次：「股價拉回至低檔區，且60分K線顯示收斂末端翻揚，具上漲空間。」
   這句話裡 60 是全句唯一的數字，於是被第 4 條規則（整句只有一個數字就當價位）
   當成成交價，一路變成某一回合的出場價 60，算出 −15.49%；
   而那一天的收盤其實是 72.40。

   這些寫法都要先拿掉再判斷：60分K、5分鐘、20MA、9週KD、日K、月K線、
   60MA、5日均線。拿掉之後那句話一個數字都不剩，才是正確答案。 */
var INDICATOR_NUM_RE =
  /\d+(?:\.\d+)?\s*(?:分\s*[KkＫ]|分鐘|分線|日\s*[KkＫ]|週\s*[KkＫ]|月\s*[KkＫ]|季\s*[KkＫ]|[KkＫ]\s*線|MA|ma|日均線|日均|週期)/g;

function stripIndicatorNumbers_(s) {
  return String(s || '').replace(INDICATOR_NUM_RE, ' ');
}

function parsePriceHint_(priceText, reasonText, kind) {
  function scan_(text, src) {
    var s = String(text || '').trim();
    if (!s || s === '未說明') { return null; }
    // 技術指標的數字先拿掉，後面每一條規則都不會再看到它們。
    s = stripIndicatorNumbers_(s);

    // 財報、指數、技術線類的數字直接排除
    for (var j = 0; j < NON_PRICE_WORDS.length; j++) {
      if (s.indexOf(NON_PRICE_WORDS[j]) >= 0) { return null; }
    }
    // 數量、金額單位（241 億、5000 張）不是股價
    if (hasNonPriceUnit_(s)) { return null; }
    // 概數（1400 多）不能當成本
    if (isVagueNumber_(s)) { return null; }
    // 與操作方向矛盾的敘述，講的是另一個動作，不採用
    if (contradictsDirection_(s, kind)) { return null; }

    // 1. 門檻式：「255 以上」「241 以下」「跌破 88」「站上 120」
    //    這一條要排在模糊語檢查之前。「漲到 255 以上就賣」裡有「到」字，
    //    先前會被模糊語規則整句丟掉，連帶把明確的門檻數字也丟了。
    var th = s.match(/(\d+(?:\.\d{1,2})?)\s*(?:元)?\s*(以上|之上|以下|之下)/);
    if (th) {
      var tv = parseFloat(th[1]);
      if (!isNaN(tv) && tv >= 1 && tv <= 10000) {
        return { value: tv, mode: (th[2] === '以上' || th[2] === '之上') ? 'above' : 'below', src: src };
      }
    }
    var th2 = s.match(/(跌破|跌到|回到|拉回到|站上|漲到|突破|衝上)\s*(\d+(?:\.\d{1,2})?)/);
    if (th2) {
      var tv2 = parseFloat(th2[2]);
      if (!isNaN(tv2) && tv2 >= 1 && tv2 <= 10000) {
        var down = ('跌破跌到回到拉回到'.indexOf(th2[1]) >= 0);
        return { value: tv2, mode: down ? 'below' : 'above', src: src };
      }
    }

    // 2. 明確成交價：「在 235 買入」「235 買進」「成本 168」
    var ex = s.match(/(?:在|以|用)?\s*(\d+(?:\.\d{1,2})?)\s*(?:元)?\s*(買到|買進|買入|承接|進場|賣出|賣掉|出場|成本)/);
    if (!ex) {
      ex = s.match(/(?:成本|買在|賣在|進場價|出場價)\s*(\d+(?:\.\d{1,2})?)/);
      if (ex) { ex = [ex[0], ex[1]]; }
    }
    if (ex) {
      var ev = parseFloat(ex[1]);
      if (!isNaN(ev) && ev >= 1 && ev <= 10000) {
        return { value: ev, mode: 'exact', src: src };
      }
    }

    // 3. 模糊語出現就不再往下猜單一數字。「約 90」「1400 多」不能當成本。
    for (var i = 0; i < VAGUE_WORDS.length; i++) {
      if (s.indexOf(VAGUE_WORDS[i]) >= 0) { return null; }
    }

    /* 4. 整句只有一個數字，且看起來像價位。

       這一條只適用於「價位說明」欄。那一欄本來就是為了放價位，
       裡面出現一個孤零零的數字，它是價位的機率很高。

       「說明重點」不適用。那一欄是敘述句，裡面的數字什麼都可能是——
       技術指標（60分K、20MA）、天數（等3天）、名次、百分比。
       用「只有一個數字就當價位」去撈，撈到的多半不是價位，
       而錯誤的成本會直接變成錯誤的報酬，比沒有數字糟得多。
       敘述句裡真正的價位一定會帶著動詞或門檻詞（「在 235 買進」
       「跌破 88」），那些在上面第 1、2 條就抓到了。 */
    if (src !== '價位說明') { return null; }

    var m = s.match(/(?:^|[^\d.])(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)(?![\d.])/g);
    if (!m || m.length !== 1) { return null; }
    var v = parseFloat(String(m[0]).replace(/[^\d.]/g, ''));
    if (isNaN(v) || v < 1 || v > 10000) { return null; }
    return { value: v, mode: 'exact', src: src };
  }

  // 價位說明優先，那一欄本來就是為了放價位。找不到才去說明重點裡撈。
  return scan_(priceText, '價位說明') || scan_(reasonText, '說明重點');
}

/** 舊介面保留，只回數值。其他地方若還在用不會壞掉。 */
function parseStatedPrice_(text) {
  var h = parsePriceHint_(text, '', '');
  return h ? h.value : null;
}

// 條件價往後找觸價日的上限。超過這個交易日數還沒碰到，就當作沒成交。
var FILL_LOOKAHEAD_DAYS = 60;

// 提及日早於日K快取最早一根超過這麼多個日曆日，視為「日K未涵蓋那一天」。
// 五天留給週末與連假：週六的提及取下週一，仍照舊。
var UNCOVERED_GAP_DAYS = 5;

// 沒有往前下限時，快取在提及日之前已有這麼多個交易日仍查無成交，
// 就不是「快取沒涵蓋」，而是這個價位在那段期間真的沒出現過。約半年。
var NO_TOUCH_COVERED_DAYS = 120;

/**
 * 決定某一天的成交價。
 *
 * hint 是 parsePriceHint_ 的結果。處理順序：
 *   1. 沒有 hint            → 當日收盤
 *   2. 落在當日高低之內      → 就是它，當天真的成交得了
 *   3. 門檻價、當日還沒到    → 往後找第一個真的碰到的交易日，用那天當成交日
 *   4. 明確價、當日碰不到    → 往後找第一個涵蓋這個價位的交易日
 *   5. 一直到期限都沒碰到    → 退回當日收盤
 *   6. 離譜到不像這檔的股價  → 判定不是價位，捨棄
 *
 * 第 3 點是這次修正的重點。「255 以上全部賣掉」在講的當天股價可能才 235，
 * 那個 255 是條件不是成交。先前一律拿當日高低去驗，必然驗不過而退回收盤，
 * 等於把張震明確講過的出場價丟掉，報酬就算錯。現在改成往後找觸價日，
 * 後來真的漲到 255 的那一天才是成交日，價格就用 255。
 */
/**
 * 沒有明講價位時，那一筆要用當天的哪個價。
 *
 * 規則（2026/09/20 起）：
 *   買入　→ 當日最低價
 *   賣出　→ 當日最高價
 *   其他　→ 當日收盤
 *
 * 為什麼買賣不同邊：講者是在盤中直播裡喊的，會員實際成交落在當天區間內的
 * 哪一點無從得知。取買入用低、賣出用高，是把「跟著喊單操作」能拿到的
 * 最好結果當成基準；收盤只是區間中的一個點，沒有比較正確，只是比較中性。
 *
 * 要知道這個選擇的效果：**它會讓報酬看起來比用收盤算更好**。
 * 這是刻意的取價立場，不是計算誤差。表格的「進場價來源」「出場價來源」
 * 會寫明是「當日最低價」還是「當日最高價」，看得出這一筆是估的還是明講的。
 *
 * 哪些情況不適用（呼叫端會傳 'close'）：
 *   首次明講會員持有　那天並沒有成交，部位是更早之前建立的
 *   轉為觀望不碰　　　立場轉變，不是賣出動作
 *   觀望注意的參考價　只是佐證，不影響任何成交
 */
function unstatedPrice_(d, mode) {
  if (mode === 'low' && d.low) { return { price: d.low, src: '當日最低價' }; }
  if (mode === 'high' && d.high) { return { price: d.high, src: '當日最高價' }; }
  return { price: d.close, src: '當日收盤' };
}


function priceOnDate_(candles, dateStr, hint, name, label, floorDate, opts) {
  opts = opts || {};
  if (!candles || !candles.length) {
    return { price: '', src: '', rejected: false, date: dateStr };
  }

  var idx = -1;
  for (var i = 0; i < candles.length; i++) {
    if (candles[i].date >= dateStr) { idx = i; break; }
  }
  var d = idx >= 0 ? candles[idx] : null;

  /* 提及日早於日K快取最早一根（2026/09/14）。

     先前直接拿「日期 ≥ 提及日的第一根」當那一天，而那一根可能是幾週之後的K棒：
     快取從 7/27 才有收盤時，7/08 的持有聲明被標成「當日收盤」，實際用的是 7/27 的價，
     進場日、報酬與績效歷史一起偏掉，來源欄卻看不出來。現在照實標示，並標記 uncovered。 */
  if (idx === 0 && d.date > dateStr && daysBetween_(dateStr, d.date) > UNCOVERED_GAP_DAYS) {
    var allHi = Math.max.apply(null, candles.map(function (k) { return k.high; }));
    var allLo = Math.min.apply(null, candles.map(function (k) { return k.low; }));
    if (hint && hint.value && hint.value >= allLo * 0.7 && hint.value <= allHi * 1.3) {
      return { price: hint.value, rejected: false, date: dateStr, unverified: hint.value, uncovered: true,
               src: '影片明講 ' + hint.value + '；日K快取最早只到 ' + d.date + '，' + dateStr + ' 無K線可驗證' };
    }
    return { price: d.close, rejected: !!(hint && hint.value), date: d.date, uncovered: true,
             src: '日K快取最早只到 ' + d.date + '，' + dateStr + ' 無K線，暫取 ' + d.date +
                  ' 收盤（補齊日K後重算）' };
  }

  // 沒有明講價位：依這一筆是買是賣，取當天的最低／最高價（見 unstatedPrice_）。
  if (!hint || !hint.value) {
    if (!d) { return { price: '', src: '', rejected: false, date: dateStr }; }
    var u = unstatedPrice_(d, opts.unstated);
    return { price: u.price, src: u.src, rejected: false, date: d.date };
  }

  var v = hint.value;

  // 日K快取沒涵蓋到那一天：用整段區間當寬鬆的合理範圍，只擋離譜值
  if (!d) {
    var his = candles.map(function (k) { return k.high; });
    var los = candles.map(function (k) { return k.low; });
    var hiAll = Math.max.apply(null, his), loAll = Math.min.apply(null, los);
    if (v >= loAll * 0.7 && v <= hiAll * 1.3) {
      return { price: v, src: '影片明講', rejected: false, date: dateStr };
    }
    // 同樣不留空。取離該日最近的一根K棒收盤當估計值。
    var near = candles[0], bestGap = Infinity;
    candles.forEach(function (k) {
      var gap = Math.abs(new Date(k.date).getTime() - new Date(dateStr).getTime());
      if (gap < bestGap) { bestGap = gap; near = k; }
    });
    Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　明講價 ' + v +
               ' 遠離歷史區間 ' + loAll + '-' + hiAll + '，改用最近一根收盤 ' + near.close);
    return { price: near.close, src: '日K未涵蓋該日，取最近交易日 ' + near.date + ' 收盤',
             rejected: true, date: near.date };
  }

  // 當天就在區間內，直接成交
  if (v >= d.low && v <= d.high) {
    return { price: v, src: '影片明講', rejected: false, date: d.date };
  }

  // 先確認這個數字對這一檔而言合不合理。差太多的直接判定不是股價，
  // 不要拿去往後找，否則「241 億」會在某天真的漲到 241 時被誤採。
  var hiA = Math.max.apply(null, candles.map(function (k) { return k.high; }));
  var loA = Math.min.apply(null, candles.map(function (k) { return k.low; }));
  if (v > hiA * 1.3 || v < loA * 0.7) {
    // 這個數字不是股價（實際踩過：一檔一千多元的股票抓到「8」）。
    // 捨棄它是對的，但不能因此讓進場價整欄空白——那會讓報酬、現價、
    // 累積報酬一起變成破折號，看起來像系統壞掉，比用當日收盤更難理解。
    // 正確做法是退回當日收盤，並在來源欄說明那個數字為什麼沒被採用。
    Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　價位 ' + v +
               ' 遠離這檔的歷史區間 ' + loA + '-' + hiA + '，判定不是股價，改用未明講時的取價');
    var bad = unstatedPrice_(d, opts.unstated);
    return { price: bad.price, src: '取' + bad.src + '（明講的 ' + v + ' 不是這檔的價位）',
             rejected: true, date: d.date };
  }

  // ---- 雙向找觸價日 ----
  //
  // 只往後找是錯的，這是先前把明講價丟掉的主因。
  //
  // 講者講價位有兩種時態，兩種都很常見：
  //   未來式　「漲到 255 以上就賣」「跌到 241 以下可以買」→ 要等，往後找。
  //   過去式　「會員在 2135 買的」「241 那天連買都買不到」→ 已經發生，往前找。
  //
  // 先前只往後找，於是所有回述成本的講法都必然找不到，
  // 然後被當成「條件沒成立」退回當日收盤——把講者明確講過的價格丟掉，
  // 換成一個他從沒提過的數字。實際發生過台積電講 2135 卻用 2465、
  // 鴻海講 241 卻用 250.5，兩筆的報酬都因此算錯。
  //
  // 往後找有 FILL_LOOKAHEAD_DAYS 的上限，因為條件價等太久就失去意義；
  // 往前找不設限，因為回述成本可能是好幾個月前的事，而且那是既成事實，
  // 沒有「過期」的問題。
  function hit_(k) {
    if (hint.mode === 'above') { return k.high >= v; }
    if (hint.mode === 'below') { return k.low <= v; }
    return v >= k.low && v <= k.high;
  }

  /* 往前找要有下限。

     這是實際算錯過報酬的地方：往前找原本一路找到快取的最開頭，於是
     出場價可以「成交」在這一回合買進之前——
       第 2 回合　2026/09/04 買入 71 → 2026/09/09 賣出 60（2026/08/04 觸價）
     那筆賣出被記在買進的一個月前，然後用它算出 −15.49%。
     賣出不可能發生在買進之前，那個數字是憑空生出來的。

     floorDate 由呼叫端給：算出場價時是「這一回合的成交日」，
     算進場價時是「上一回合的出場日」。回述成本仍然找得到（那本來就是
     這一回合開始之後、或上一回合結束之後的事），但跨不回不可能的區間。 */
  var floorIdx = 0;
  if (floorDate) {
    for (var f = 0; f < candles.length; f++) {
      if (candles[f].date >= floorDate) { floorIdx = f; break; }
    }
  }

  var fwd = -1, back = -1;
  // noForward：部位在提及日已經存在（會員持有聲明），成本不可能是之後才成交的價，只看當天與之前。
  var limit = opts.noForward ? Math.min(candles.length, idx + 1)
                             : Math.min(candles.length, idx + 1 + FILL_LOOKAHEAD_DAYS);
  for (var j = idx; j < limit; j++) {
    if (hit_(candles[j])) { fwd = j; break; }
  }
  for (var b = idx - 1; b >= floorIdx; b--) {
    if (hit_(candles[b])) { back = b; break; }
  }

  // 兩邊都找到就取離發話日比較近的那一天。
  var pick = -1;
  if (fwd >= 0 && back >= 0) {
    pick = (fwd - idx) <= (idx - back) ? fwd : back;
  } else if (fwd >= 0) { pick = fwd; }
  else if (back >= 0) { pick = back; }

  if (pick >= 0) {
    var k2 = candles[pick];
    var tense = pick < idx ? '回述' : (pick > idx ? '條件價' : '明講價');
    var verb = (hint.mode === 'above') ? '漲抵' : (hint.mode === 'below') ? '跌抵' : '成交於';
    var why2 = (pick === idx)
      ? '影片明講'
      : tense + '，' + k2.date + ' ' + verb + ' ' + v;
    if (pick !== idx) {
      Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　' + v +
                 ' 當日未觸價（' + d.low + '-' + d.high + '），' +
                 (pick < idx ? '往前' : '往後') + '找到 ' + k2.date + ' 觸價');
    }
    return { price: v, src: why2, rejected: false, date: k2.date,
             filled: (pick !== idx), backward: (pick < idx) };
  }

  /* 找不到觸價日。這裡要分兩種情況，因為證據強度完全不同。

     一、沒有下限（floorDate 是空的）
         「整段快取都沒碰到」很可能只是快取沒涵蓋到那一段。這個價位已經
         通過前面的合理性檢查（落在這一檔歷史區間正負三成內），八成仍是
         這一檔的價位。用當日收盤取代等於把講者明講的數字換成他沒提過的，
         報酬會算錯而且看不出來。所以採用明講價，並在來源欄註明沒有K線佐證。

     二、有下限（算出場價、或上一回合之後的進場價）
         這時搜尋範圍是「這一回合實際存在的那幾天」，而那幾天的K線就在
         快取裡——「沒碰到」不是快取缺漏，是這個價位在這段期間真的沒有成交。
         這種情況再採用明講價就是編造：實際發生過
           2026/09/04 買入 71 → 2026/09/09 賣出 60（2026/08/04 觸價）
         那個 60 是一個月前的價，被當成這一回合的出場價，算出 −15.49%。
         而那一天的收盤是 72.40，這一回合其實小賺。
         有下限時一律退回當日收盤，並在來源欄說明那個數字為什麼沒被採用。 */
  if (floorDate && d) {
    Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　明講價 ' + v +
               ' 在 ' + floorDate + ' 之後到 ' + dateStr + ' 之間未曾成交，改用當日收盤 ' + d.close);
    return { price: d.close, rejected: true, date: d.date,
             src: '明講的 ' + v + ' 在本回合期間未曾成交（' + floorDate +
                  ' 起），改取當日收盤' };
  }

  /* 快取在提及日之前已涵蓋夠長仍查無成交：這個價位在那段期間真的沒出現過，不是快取缺漏。
     2026/09/14 鴻準 8/05 的 73，一整年都沒有成交過卻被採用，已實現報酬算成 −15.5%。 */
  if (d && idx >= NO_TOUCH_COVERED_DAYS) {
    Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　明講價 ' + v + ' 在日K快取 ' +
               candles[0].date + ' 起未曾成交，判定不是成交價，改用當日收盤 ' + d.close);
    return { price: d.close, rejected: true, date: d.date,
             src: '明講的 ' + v + ' 在日K快取 ' + candles[0].date + ' 起未曾成交，改取當日收盤' };
  }
  Logger.log('    ' + name + ' ' + dateStr + ' ' + label + '　明講價 ' + v +
             ' 在日K快取中查無觸價紀錄（快取可能未涵蓋該區間），仍採用明講價');
  return { price: v, src: '影片明講，日K快取查無觸價紀錄', rejected: false,
           date: dateStr, unverified: v };
}

function daysBetween_(a, b) {
  if (!a || !b) { return 0; }
  var d1 = new Date(a), d2 = new Date(b);
  if (isNaN(d1) || isNaN(d2)) { return 0; }
  return Math.max(Math.round((d2 - d1) / 86400000), 0);
}

/* ------------------------------------------------------------------ *
 * 持有回合（round）模型
 *
 * 一檔股票可能買進、賣出、再買回來。用單一組「首次買入 → 最近賣出」
 * 描述不了這件事：第二次買回來之後，進場價還停在第一次那個價格，
 * 報酬就整個算錯，狀態也會在持有中與已出場之間互相蓋掉。
 * 所以改成把每一檔切成數個「回合」，一個回合 ＝ 一次進場到一次出場
 * （或還沒出場）。表格顯示的是「最新那一個回合」，過去的回合另外保留。
 *
 * 開倉有兩種方式：
 *   1. 影片中明講買入。
 *   2. 影片中明講「會員目前持有 X」，也就是寫進「會員持股」分頁的那些。
 *      這一條是後來補的。張震有時候不會重述當初的買入動作，只在盤中
 *      複述一次會員手上有什麼，那句話本身就是一個明確的持有聲明，
 *      信件的「影片中明講之會員目前持有股票」那一段列的就是它。
 *      少了這條路，那些標的會出現在信裡卻不在網站上，兩邊對不起來。
 *
 *      但它跟明講買入不同：那天並沒有成交，所以進場價只能取當日收盤，
 *      而且必須擋掉同日矛盾（同一天又被講成觀望不碰或賣出）。
 *      早期版本就是少了這道防呆，才會讓方向是觀望不碰的股票混進會員持股。
 *      同一天被講成「賣出」時，這一筆持有聲明不予採信。
 *      同一天另有「觀望不碰」則照樣採信（2026/09/14）：擷取規則現在允許已持有的股票
 *      對「還沒有的人」另列一筆觀望，而觀望不碰也涵蓋中性與偏負面的觀察，
 *      兩筆並存是正常的。祥碩就是這樣從「目前持有」消失的。
 *
 * 平倉有三種方式，依序判斷：
 *   1. 明講賣出　　　　　　　　→ 出場價取賣出當日（明講價通過當日高低驗證才採用）
 *   2. 觀望不碰　　　　　　　　→ 暫定視為出場，出場價取當日收盤。
 *      這不是一筆成交，是立場由持有轉為不碰，所以絕不採用明講價，
 *      只用當日收盤代表「若在這天退出，價位大約在這裡」。
 *   3. 逾 STALE_TRADING_DAYS 個交易日未再提及 → 視為出場。出場日＝「每天講了什麼」最後一次
 *      提及的日期，出場價＝那一天的收盤；那天沒有日K（週末的會員簡訊、快取缺漏）
 *      取那天之前最近一個交易日的收盤，絕不拿之後的價格——提及當下還不知道之後的價。
 *   觀望不碰與逾期都只看「這一回合」：同一天另有會員持股聲明時，那次不碰不平倉。
 *
 * 「觀望不碰」的平倉是暫定的，會被後來的事實推翻。
 *
 * 他講「不碰」時，講的對象常常是「還沒進場的人」而不是「手上有的人」。
 * 鴻海實際發生過：8/27 明講會員持有 238，8/31 說「238 搶過了，248 不會推薦」，
 * 9/2 又說「前幾天於 238 買進，目前已漲到 250 幾」。
 * 8/31 那句是在勸人不要追高，不是宣布出場——9/2 那句證明部位從頭到尾都在手上。
 * 舊的規則把 8/31 當成出場、把 9/2 當成新開的第四回合，於是同一筆部位被切成兩段，
 * 第三回合結算出一個沒有發生過的報酬，第四回合的進場價又變成 9/2 的收盤。
 *
 * 所以改成往後看一步再決定：
 *   不碰之後的下一個實質動作是「會員持股」或「賣出」→ 部位還在，這次不碰不平倉。
 *     賣出也算，因為賣得掉就代表手上還有，那句不碰同樣沒有讓部位離開。
 *   下一個實質動作是「買入」→ 那是重新進場，這次不碰確實是出場，照舊平倉。
 *   在 AVOID_REOPEN_TRADING_DAYS 個交易日內沒有任何實質動作 → 照舊平倉。
 * 被推翻的那幾次會記在回合上，出場原因欄看得到，不會無聲無息地消失。
 *
 * 觀望注意不平倉：語氣偏多，代表繼續追蹤，不是退出。
 * 同一天既有買入又有相反訊號時，以買入為準，忽略當天的平倉訊號。
 * 那種同日矛盾幾乎都是擷取誤判，不該讓它把當天剛建立的部位又關掉。
 * ------------------------------------------------------------------ */

/** 把「方向」欄歸類成回合模型認得的動作。順序有意義，不可對調。 */
function classifyDirection_(dir) {
  var d = String(dir || '');
  if (d.indexOf('買') === 0) { return 'buy'; }
  if (d.indexOf('賣') === 0) { return 'sell'; }
  if (d.indexOf('會員持股') >= 0) { return 'hold'; }
  if (d.indexOf('觀望不碰') >= 0 || d.indexOf('不碰') >= 0) { return 'avoid'; }
  if (d.indexOf('觀望注意') >= 0) { return 'note'; }
  if (d.indexOf('觀望') >= 0) { return 'note'; }   // 舊資料只寫「觀望」時當中性，不平倉
  return 'other';
}

/** 同一天多筆時的先後順序：先開倉、再平倉、其他最後。 */
/* buy 與 hold 都是開倉動作，所以排在最前面；hold 排在 buy 之後，
   同一天兩者都出現時以真正的買入為準（hold 會因為已有部位而略過）。 */
var ACTION_ORDER = { buy: 1, hold: 2, sell: 3, avoid: 4, note: 5, other: 6 };

/**
 * 排序。日期優先，同一天再看順序。
 *
 * 同一天的先後，能用「序」就用「序」。
 *
 * ACTION_ORDER 把買入一律排在賣出前面，那是在沒有其他資訊時的合理猜測，
 * 但它會猜錯，而且錯得很嚴重。實際發生過：他昨天早盤賣出晶心科 270 幾、
 * 快中午又買回來 250 幾。照 ACTION_ORDER 會變成「先買 250 再賣 270」——
 * 先買後賣是加碼之後獲利了結，先賣後買是賣掉再撿回來，兩者的持有回合、
 * 進場價、報酬完全不同。更糟的是後面還有一條「同日已有買入就忽略相反訊號」，
 * 於是那筆賣出直接消失，回合永遠不會結束。
 *
 * 現在擷取端會填「序」（同一檔同一天照他描述的先後 1、2、3）。
 * 兩筆都有序而且都是買賣時就照序走，其餘情況維持原本的優先級——
 * 混用是刻意的：會員持股那張分頁沒有序欄，拿 0 去跟買入的 1 比會把
 * 持有聲明排到買入前面，那是另一種錯。
 */
function sortRecords_(list) {
  return list.slice().sort(function (a, b) {
    if (a.date !== b.date) { return a.date < b.date ? -1 : 1; }
    var trade = (a.kind === 'buy' || a.kind === 'sell') &&
                (b.kind === 'buy' || b.kind === 'sell');
    if (trade && a.seq > 0 && b.seq > 0 && a.seq !== b.seq) {
      return a.seq - b.seq;
    }
    return (ACTION_ORDER[a.kind] || 9) - (ACTION_ORDER[b.kind] || 9);
  });
}

// 觀望不碰之後要往後看幾個交易日，才算「沒有下文」。
// 用交易日而不是日曆日，因為連假期間他本來就沒在講盤，
// 用日曆日會讓中秋或農曆年前後的每一次不碰都被當成真的出場。
// 十天與 STALE_TRADING_DAYS 一致：那是這個系統對「還在追蹤中」的一貫定義。
var AVOID_REOPEN_TRADING_DAYS = 10;

// 日曆日的上限，與上面那個一起看。
//
// 為什麼要兩個：交易日的數法完全依賴日K快取，而日K快取是會殘缺的
// （新股還沒補、當天的還沒進來、額度用盡跑到一半）。快取少了一段時，
// 兩個相隔三個月的日期算起來可能只差三個交易日，於是三個月前的一次不碰
// 會被今天的持有聲明推翻——那是明顯錯的。
// 日曆日不依賴任何快取，拿它當第二道界線，快取殘缺時仍然擋得住。
// 十個交易日約兩週，二十一天留了連假與補班的餘裕。
var AVOID_REOPEN_CALENDAR_DAYS = 21;

/** tradingDays 陣列裡，a 與 b 之間隔了幾個交易日。tradingDays 需已排序。 */
function tradingDaysBetween_(tradingDays, a, b) {
  if (!tradingDays || !tradingDays.length) { return 0; }
  var n = 0;
  for (var i = 0; i < tradingDays.length; i++) {
    if (tradingDays[i] > a && tradingDays[i] <= b) { n++; }
  }
  return n;
}

/**
 * 這一次的「觀望不碰」到底算不算出場。
 *
 * 往後找第一個實質動作（買入、賣出、會員持股），依它決定：
 *   賣出或會員持股 → 部位還在手上，這次不碰不算出場。
 *   買入　　　　　 → 是重新進場，所以這次不碰確實是出場。
 *   都沒有（或超過期限）→ 沒有下文，照舊視為出場。
 *
 * 只看第一個，不看更後面的。中間若隔著一次買入，那次買入已經開了新回合，
 * 再後面的持有聲明講的是新回合的部位，不能拿來證明舊回合沒結束。
 */
function avoidClosesRound_(sorted, at, tradingDays) {
  var here = sorted[at];
  // 沒有交易日曆就量不出「隔多久」，這時退回舊行為：不碰就是出場。
  // 量不出來的時候寧可維持原樣，也不要因為量不出來而放寬。
  if (!tradingDays || !tradingDays.length) { return { close: true }; }
  for (var j = at + 1; j < sorted.length; j++) {
    var r = sorted[j];
    if (r.kind !== 'buy' && r.kind !== 'sell' && r.kind !== 'hold') { continue; }
    if (r.kind === 'buy') { return { close: true }; }
    var gap = tradingDaysBetween_(tradingDays, here.date, r.date);
    var cal = daysBetween_(here.date, r.date);
    if (gap > AVOID_REOPEN_TRADING_DAYS ||
        cal > AVOID_REOPEN_CALENDAR_DAYS) { return { close: true }; }
    return { close: false,
             why: r.date + ' ' + (r.kind === 'sell' ? '明講賣出' : '明講會員持有') +
                  '，證明部位並未在 ' + here.date + ' 離開' };
  }
  return { close: true };
}

/**
 * 把一檔的所有紀錄切成數個持有回合。
 * 回傳陣列，最後一個若 close 為 null 就代表現在還持有。
 *
 * tradingDays 是市場交易日清單（由小到大），用來判斷觀望不碰之後多久沒有下文。
 * 傳空陣列也能跑，那時所有的不碰都照舊視為出場，行為與加這一關之前相同。
 */
function buildRounds_(records, tradingDays) {
  // 每一天出現過哪些動作。用來判斷同日矛盾。
  var dayKinds = {};
  records.forEach(function (r) {
    if (!dayKinds[r.date]) { dayKinds[r.date] = {}; }
    dayKinds[r.date][r.kind] = 1;
  });
  function sameDay_(date, kind) { return !!(dayKinds[date] && dayKinds[date][kind]); }

  /* 哪幾天的買賣有明確的先後。

     有序的那幾天，同一天的買與賣不是「矛盾」而是「順序」，
     下面那條忽略相反訊號的守則要讓開，否則先賣後買會被吃掉那筆賣出。 */
  var daySeqKnown = {};
  records.forEach(function (r) {
    if ((r.kind === 'buy' || r.kind === 'sell') && r.seq > 0) {
      daySeqKnown[r.date] = (daySeqKnown[r.date] || 0) + 1;
    }
  });
  function ordered_(date) { return (daySeqKnown[date] || 0) >= 2; }

  var rounds = [], cur = null;
  var sorted = sortRecords_(records);
  sorted.forEach(function (r, at) {
    if (r.kind === 'buy') {
      // 已經有部位時再買，是加碼，不另開回合，進場價維持這一回合的第一筆。
      if (!cur) { cur = { open: r, openKind: 'buy', adds: [], close: null, closeKind: '' }; }
      else { cur.adds.push(r); }
      return;
    }

    if (r.kind === 'hold') {
      // 明講「會員目前持有」。手上已經有部位就只是再確認一次，不動任何東西。
      if (cur) { return; }
      // 同一天又被講成賣出，代表這一筆持有聲明與當天的成交打架，寧可不開倉。
      // 同一天的觀望不碰不再擋（2026/09/14）：已持有的股票對還沒有的人另列觀望，
      // 是擷取規則明訂允許的寫法；舊規則會讓這種日子的持有聲明整筆作廢。
      if (sameDay_(r.date, 'sell')) { return; }
      cur = { open: r, openKind: 'hold', adds: [], close: null, closeKind: '' };
      return;
    }

    if (!cur) { return; }                      // 手上沒部位，賣出或觀望都不構成回合
    // 同日已有買入時忽略相反訊號——但那條守則是在「不知道先後」時的防呆。
    // 這一天若有明確的序（先賣後買、或先買後賣），就照序走，不要忽略。
    if (sameDay_(r.date, 'buy') && !ordered_(r.date)) { return; }
    if (r.kind === 'sell') {
      cur.close = r; cur.closeKind = '賣出';
      rounds.push(cur); cur = null;
    } else if (r.kind === 'avoid') {
      // 同一天明講會員持有：這次不碰是講給還沒有的人聽的，部位還在，不平倉。
      if (sameDay_(r.date, 'hold')) {
        cur.avoidIgnored = (cur.avoidIgnored || []);
        cur.avoidIgnored.push(r.date);
        return;
      }
      var verdict = avoidClosesRound_(sorted, at, tradingDays);
      if (!verdict.close) {
        // 這次不碰被後來的事實推翻。只記日期——理由對每一次都一樣，
        // 每一筆都附一次會讓出場原因欄變成一整片文字牆。
        cur.avoidIgnored = (cur.avoidIgnored || []);
        cur.avoidIgnored.push(r.date);
        return;
      }
      cur.close = r; cur.closeKind = '觀望不碰';
      rounds.push(cur); cur = null;
    }
  });
  if (cur) { rounds.push(cur); }
  return rounds;
}

// 由「會員持股聲明」開倉的部位，必須在其後這麼多個交易日內至少再被提到一次，
// 否則視為擷取錯誤整筆移除。三天是刻意訂得短的：張震每個交易日都在講盤，
// 一檔真的在手上的股票不可能連續三個交易日完全不被提起；
// 反過來說，只出現過孤零零一次、之後就消失的，幾乎都是語音辨識或擷取的雜訊。
var HOLD_CONFIRM_DAYS = 3;

/**
 * 判斷一個由持有聲明開倉的回合，有沒有在期限內被再次提到。
 *
 * 回傳 'confirmed' | 'unconfirmed' | 'pending'
 *
 *   confirmed    期限內有再被提到，是真的持股。
 *   unconfirmed  期限已過但完全沒有再被提到，判定為擷取錯誤，整個回合移除。
 *   pending      期限還沒過完（例如昨天才剛講的），現在還不能judge。
 *                這一段最容易寫錯：若不分辨 pending，剛講完的持股會在
 *                隔天的重算裡被當成「沒有後續」而立刻刪掉，等於永遠留不住。
 *
 * openDate     這個回合的開倉日
 * recs         這一檔的全部紀錄（已含操作紀錄與會員持股）
 * tradingDays  市場交易日清單，由小到大
 */
function holdConfirmState_(openDate, recs, tradingDays) {
  // 開倉日之後的交易日，取前 HOLD_CONFIRM_DAYS 個當作觀察窗。
  var after = tradingDays.filter(function (d) { return d > openDate; });
  var windowDays = after.slice(0, HOLD_CONFIRM_DAYS);

  // 觀察窗還沒湊滿，代表時間根本還沒到，不能判定。
  if (windowDays.length < HOLD_CONFIRM_DAYS) { return 'pending'; }

  var windowEnd = windowDays[windowDays.length - 1];
  var mentioned = recs.some(function (r) {
    return r.date > openDate && r.date <= windowEnd;
  });
  return mentioned ? 'confirmed' : 'unconfirmed';
}

/**
 * dateStr 當天或之前最近一根日K的收盤。沒有就回空值。
 * 逾期出場用：出場價是「最後一次提及那天」的收盤，那天沒有日K時只能往前取，
 * 不能像 priceOnDate_ 那樣往後找——往後的價格在提及當下還不存在。
 */
function closeOnOrBefore_(candles, dateStr) {
  var pick = null;
  (candles || []).forEach(function (k) {
    if (k && k.date <= dateStr && isFinite(Number(k.close)) && Number(k.close) > 0 &&
        (!pick || k.date > pick.date)) { pick = k; }
  });
  return pick ? { price: pick.close, date: pick.date } : { price: '', date: '' };
}

/** 補單檔已出場日的正式收盤，再重算持股與該日起的歷史績效；不使用盤中現價。 */
function repairMissingTrackerExitPrice(code) {
  code = String(code || '').trim();
  if (!/^\d{4}$/.test(code)) { throw new Error('請提供四位台股代號'); }
  var row = readSheetObjects_('持股追蹤').filter(function (r) { return String(r['代號']).trim() === code; })[0];
  if (!row || String(row['狀態']) !== '已出場') { throw new Error(code + ' 目前沒有已出場回合'); }
  var day = fmtDate_(row['最近賣出日']);
  if (!day || day > todayStr_()) { throw new Error(code + ' 的出場日無效'); }
  var existing = getCachedDailyK(code), exact = existing.filter(function (k) { return k.date === day && validDailyK_(k); })[0];
  if (!exact) {
    var token = acquireDailyKLease_(Date.now() + 180000, 0);
    if (!token) { throw new Error('日K整輪補齊正在寫入；請等它完成後重試'); }
    try {
      // 查前後各一天以涵蓋行情商的日期區間邊界；只接受出場日自己的K棒。
      var from = fugleShiftDay_(day.replace(/\//g, '-'), -1);
      var to = fugleShiftDay_(day.replace(/\//g, '-'), 1);
      var fresh = hasFugle_() ? fugleHistorical_(code, from, to)
                : (typeof hasFinMind_ === 'function' && hasFinMind_() ? fetchFinmindDailyK_(code, from, to) : []);
      exact = fresh.filter(function (k) { return k.date === day && validDailyK_(k); })[0];
      if (!exact) { throw new Error(code + ' ' + day + ' 行情來源沒有有效日K；保留原資料，請查停牌或來源缺口'); }
      writeDailyKRows_([{ code: code, rows: mergeDailyK_(existing, fresh), months: null }]);
    } finally { releaseDailyKLease_(token); }
  }
  rebuildHoldingsTrackerJob();
  var result = { code: code, exitDate: day, close: exact.close,
    trackerExit: (readSheetObjects_('持股追蹤').filter(function (r) { return String(r['代號']).trim() === code; })[0] || {})['出場價'] };
  if (Number(result.trackerExit) !== Number(exact.close)) {
    throw new Error('已補日K但持股追蹤出場價未對上；請執行 explainHoldingsTracker("' + code + '") 檢查：' + JSON.stringify(result));
  }
  rebuildPerformanceHistoryJob(day);
  Logger.log(JSON.stringify(result));
  return result;
}

/** Apps Script 編輯器一鍵修復本次力旺 3529 出場價。 */
function repairLiwangExitPriceNow() { return repairMissingTrackerExitPrice('3529'); }

/**
 * 在 Apps Script 編輯器執行，檢查「日K快取」的涵蓋範圍。只讀不寫，也不呼叫外部服務。
 *
 * 用途：績效歷史只採用「收」是正數的列，持股追蹤的交易日曆卻只看「日期」。
 * 兩者涵蓋不同時（例如某一段日期的列存在、收盤卻是空的），進場價會被接到之後的K棒，
 * 績效歷史的天數也會變短。這裡逐月列出列數與有效收盤的列數，並抽樣列出無效的列。
 * 例：auditDailyKCoverage()、auditDailyKCoverage(['5269','9958'])
 */
function auditDailyKCoverage(codes) {
  var vals = getSheet_('日K快取').getDataRange().getValues();
  if (vals.length < 2) { Logger.log('日K快取是空的'); return {}; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  var cCode = head.indexOf('代號'), cDate = head.indexOf('日期'), cClose = head.indexOf('收');
  Logger.log('表頭：' + head.join('、') + '　共 ' + (vals.length - 1) + ' 列');
  if (cCode < 0 || cDate < 0 || cClose < 0) {
    Logger.log('找不到「代號」「日期」或「收」欄，無法檢查');
    return {};
  }
  var months = {}, firstValid = '', lastValid = '', samples = [], perCode = {};
  var want = (codes && codes.length ? codes : ['5269', '9958', '2354']).map(String);
  for (var i = 1; i < vals.length; i++) {
    var code = String(vals[i][cCode] || '').trim();
    var date = fmtDate_(vals[i][cDate]);
    var raw = vals[i][cClose];
    var v = Number(raw);
    var ok = !!(code && date && isFinite(v) && v > 0);
    var m = date ? date.slice(0, 7) : '（無日期）';
    months[m] = months[m] || { rows: 0, valid: 0 };
    months[m].rows++;
    if (ok) {
      months[m].valid++;
      if (!firstValid || date < firstValid) { firstValid = date; }
      if (!lastValid || date > lastValid) { lastValid = date; }
    } else if (samples.length < 8) {
      samples.push('第 ' + (i + 1) + ' 列　代號=' + JSON.stringify(code) + '　日期=' + JSON.stringify(date) +
                   '　收=' + JSON.stringify(raw) + '（' + typeof raw + '）');
    }
    if (want.indexOf(code) >= 0) {
      var p = perCode[code] = perCode[code] || { rows: 0, valid: 0, first: '', last: '' };
      p.rows++;
      if (ok) {
        p.valid++;
        if (!p.first || date < p.first) { p.first = date; }
        if (!p.last || date > p.last) { p.last = date; }
      }
    }
  }
  Logger.log('有效收盤涵蓋：' + (firstValid || '（沒有）') + ' ～ ' + (lastValid || '（沒有）'));
  var fprops = PropertiesService.getScriptProperties();
  Logger.log('Fugle 回溯上限紀錄：舊版 FUGLE_MAX_DAYS=' + (fprops.getProperty('FUGLE_MAX_DAYS') || '（無）') +
             '（已不採信）；新版 FUGLE_RANGE_LIMIT=' + (fprops.getProperty('FUGLE_RANGE_LIMIT') || '（無，代表沒被拒絕過）'));
  Object.keys(months).sort().forEach(function (k) {
    Logger.log('  ' + k + '：' + months[k].rows + ' 列，有效收盤 ' + months[k].valid + ' 列' +
               (months[k].valid < months[k].rows ? '　<<< 有 ' + (months[k].rows - months[k].valid) + ' 列無效' : ''));
  });
  if (samples.length) {
    Logger.log('無效列抽樣：');
    samples.forEach(function (s) { Logger.log('  ' + s); });
  }
  want.forEach(function (c) {
    var p = perCode[c];
    Logger.log('代號 ' + c + '：' + (p ? p.rows + ' 列，有效 ' + p.valid + ' 列，' + (p.first || '—') + ' ～ ' + (p.last || '—')
                                       : '日K快取沒有這一檔'));
  });
  return { firstValid: firstValid, lastValid: lastValid, months: months, perCode: perCode };
}

/**
 * 在 Apps Script 編輯器執行，查一檔為什麼在（或不在）持股追蹤、狀態怎麼判的。
 * 只讀不寫：不改持股追蹤、不補日K、不呼叫任何外部服務。結果印在執行紀錄。
 * 例：explainHoldingsTracker('5269')、explainHoldingsTracker('世紀鋼')
 */
function explainHoldingsTracker(codeOrName) {
  var target = String(codeOrName || '').trim();
  if (!target) { Logger.log('請帶代號或名稱，例如 explainHoldingsTracker(\'5269\')'); return []; }
  var stat = rebuildHoldingsTrackerJob({ dryRun: true, explain: target });
  (stat.trace || []).forEach(function (line) { Logger.log(line); });
  return stat.trace || [];
}

/** 每日 14:50 觸發。重算每一檔的持有回合、進場價、狀態。 */
function rebuildHoldingsTrackerJob(options) {
  var start = Date.now();
  // 觸發器呼叫時會帶事件物件，只認得明確的 dryRun／explain，其餘一律當一般重算。
  var dryRun = !!(options && options.dryRun === true);
  var explain = (options && typeof options.explain === 'string') ? options.explain.trim() : '';
  var trace = [];
  function tr_(g, line) {
    if (!explain) { return; }
    if (g.code !== explain && String(g.name).indexOf(explain) < 0) { return; }
    trace.push(g.name + '（' + (g.valid ? g.code : '代號待確認') + '）　' + line);
  }

  /* 開始之前，先把「完全沒有日K」的那幾檔補起來。

     進場價、現價、報酬三欄都是從日K算出來的，所以一檔沒有K線的股票
     在表格上會是三個空格加一句「取不到進場價，可到後台按補齊日K並重算」。
     而會踩到這個的幾乎都是今天第一次被講到的股票——它才剛寫進操作紀錄，
     分批補日K的游標不一定輪得到它，那句話就會在網站上掛一整天。

     要人去按一個按鈕才會好的東西，本來就該自己好。這裡無條件跑一次，
     沒有缺的時候只是多讀一次已經要讀的那張表，成本接近零；
     真的有缺時通常也只有一兩檔，幾秒鐘就補完。

     包在 try 裡是因為它會對外請求，而對外請求一定會有失敗的一天。
     補不到就補不到，不可以因此讓整個重算掛掉——那樣損失比缺幾個數字大得多。 */
  try {
    var mk = dryRun ? { checked: 0 } : fillMissingDailyK_(60);
    if (mk.checked) {
      Logger.log('自動補缺日K：' + mk.checked + ' 檔沒有資料，補成功 ' +
                 mk.filled + '、仍取不到 ' + mk.failed);
    }
  } catch (e) {
    Logger.log('自動補缺日K略過（不影響重算）：' + e);
  }

  var trades = readSheetObjects_('操作紀錄').filter(function (r) {
    return String(r['股票名稱'] || '').trim();
  });
  if (!trades.length) {
    Logger.log('操作紀錄是空的，無事可做');
    return;
  }

  // 分組。代號無效的用「名稱」當 key，不扔掉。
  var groups = {};
  trades.forEach(function (r) {
    var code = String(r['代號'] || '').trim();
    var name = String(r['股票名稱']).trim();
    var valid = /^(?:00981A|\d{4,6})$/.test(code);
    var key = valid ? code : ('NAME:' + name);

    if (!groups[key]) {
      groups[key] = { code: valid ? code : '', name: name, valid: valid, records: [] };
    }

    var dir = String(r['方向']);
    groups[key].records.push({
      date: fmtDate_(r['日期']),
      reason: naturalReason_(r['理由摘錄']) || '未說明',
      direction: dir || '提及',
      kind: classifyDirection_(dir),
      // 同一天同一檔的動作先後。舊資料沒有這一欄，那時是 0，排序就退回優先級。
      seq: Number(r['序']) || 0,
      price: String(r['價位說明'] || '').trim(),
      // 價位說明與理由摘錄兩邊一起看。張震常常不在價位欄講價，
      // 而是在說明裡帶一句「會員在 235 買入」，那同樣是明確的成本。
      hint: parsePriceHint_(r['價位說明'], r['理由摘錄'], classifyDirection_(dir))
    });
  });

  // 併入「會員持股」。這是張震在影片中明講「會員目前持有 X」的那些，
  // 也就是信件裡「影片中明講之會員目前持有股票」那一段的來源。
  //
  // 它是第二種開倉方式：沒有對應買入紀錄時，第一次被明講持有就開一個回合。
  // 但那天並沒有成交，所以進場價只能取當日收盤，而且同一天若又出現
  // 觀望不碰或賣出，這一筆就不予採信（詳見 buildRounds_）。
  var holdOnlyOpened = {};
  readSheetObjects_('會員持股').forEach(function (r) {
    var code = String(r['代號'] || '').trim();
    var name = String(r['股票名稱'] || '').trim();
    if (!name) { return; }
    var valid = /^(?:00981A|\d{4,6})$/.test(code);
    var key = valid ? code : ('NAME:' + name);
    if (!groups[key]) {
      groups[key] = { code: valid ? code : '', name: name, valid: valid, records: [] };
    }
    groups[key].records.push({
      date: fmtDate_(r['日期']),
      reason: naturalReason_(r['說明重點']) || r['目前立場'] || '會員持股',
      direction: '會員持股',
      kind: 'hold',
      seq: 0,          // 會員持股沒有序欄，排序一律走優先級
      price: '',
      hint: null
    });
  });

  var rows = [];
  var now = todayStr_();
  var stat = { ok: 0, noCode: 0, noPrice: 0, stated: 0, rejected: 0, noOpen: 0,
               autoClosed: 0, avoidClosed: 0, multiRound: 0, dropped: 0,
               holdOpened: 0, holdConflict: 0, refPrice: 0, filled: 0, unverified: 0, backward: 0, noDailyK: 0,
               holdDropped: 0, holdPending: 0 };
  var holdDroppedList = [];
  var costOverrides = readCostOverrides_();   // 管理者修正成本（v54），鍵＝代號|回合開始日

  // 交易日曆：日K快取裡出現過的所有日期。用來判斷「幾個交易日沒再提及」。
  var tradingDays = (function () {
    var set = {};
    readSheetObjects_('日K快取').forEach(function (r) {
      var d = fmtDate_(r['日期']);
      if (d && d <= now && isFinite(Number(r['收'])) && Number(r['收']) > 0) { set[d] = 1; }
    });
    return Object.keys(set).sort();
  })();
  function tradingDaysAfter_(dateStr) {
    var n = 0;
    for (var i = 0; i < tradingDays.length; i++) { if (tradingDays[i] > dateStr) { n++; } }
    return n;
  }
  var STALE_TRADING_DAYS = 10;   // 超過這個交易日數沒再提及，自動視為已出場

  Object.keys(groups).forEach(function (key) {
    var g = groups[key];
    var recs = sortRecords_(g.records);
    var hasBuy = recs.some(function (r) { return r.kind === 'buy'; });
    var hasHold = recs.some(function (r) { return r.kind === 'hold'; });

    // 沒有任何開倉動作就沒有持有。只講過觀望或賣出的，不列入追蹤。
    tr_(g, '紀錄 ' + recs.length + ' 筆：' + recs.map(function (r) {
      return r.date + ' ' + r.direction;
    }).join('、'));
    if (!hasBuy && !hasHold) {
      stat.noOpen++;
      tr_(g, '不列入：沒有任何買入或會員持股紀錄');
      return;
    }

    // 轉譯錯誤剔除：若這一檔從頭到尾都沒有出現觀望／不碰／買入／賣出這類
    // 實質操作字眼，且首次理由也是「未說明」，很可能是語音辨識把某句話
    // 誤判成一檔股票，直接不列入追蹤。
    var firstOpen = recs.filter(function (r) {
      return r.kind === 'buy' || r.kind === 'hold';
    })[0];
    var hasRealAction = recs.some(function (r) {
      var d = String(r.direction || '');
      var t = String(r.reason || '');
      return /買|賣|觀望|不碰|持有|抱|會員持股/.test(d) ||
             /買|賣|觀望|不碰|抱著|加碼|減碼|停損|停利|持有/.test(t);
    });
    var firstReasonBlank = !firstOpen.reason || String(firstOpen.reason).trim() === '' ||
                           String(firstOpen.reason).trim() === '未說明';
    if (!hasRealAction && firstReasonBlank) {
      stat.dropped++;
      tr_(g, '不列入：無任何實質操作字眼且首次理由未說明，研判轉譯錯誤');
      Logger.log('剔除疑似轉譯錯誤：' + g.name + '（' + g.code + '），無任何實質操作字眼且首次理由未說明');
      return;
    }

    /* 由持有聲明開倉的回合要通過三個交易日的確認，期限內沒有被再次提到的判定為擷取錯誤。

       先前是把「整個回合」移除。但回合是從那一筆聲明開始一路吃進後面所有紀錄的：
       七月一筆孤立的持股聲明沒通過確認，九月確認過的持股聲明早就併進那個回合，
       於是跟著一起被刪，整檔從「目前持有」消失（2026/09/14 祥碩）。
       現在只移除那一筆「開倉的聲明」，再用剩下的紀錄重建回合，
       後面的持股聲明就能自己開回合、自己接受確認。每一輪至少移除一筆，必定收斂。 */
    var rounds = [], excludedOpen = 0;
    for (var pass = 0; pass <= recs.length; pass++) {
      rounds = buildRounds_(recs.filter(function (r) { return !r._unconfirmedOpen; }), tradingDays);
      var bad = rounds.filter(function (rd) {
        return rd.openKind === 'hold' && holdConfirmState_(rd.open.date, recs, tradingDays) === 'unconfirmed';
      });
      if (!bad.length) { break; }
      bad.forEach(function (rd) {
        rd.open._unconfirmedOpen = true;
        excludedOpen++;
        stat.holdDropped++;
        holdDroppedList.push(g.name + '（' + (g.valid ? g.code : '代號待確認') +
                             '）　' + rd.open.date + ' 起連續 ' + HOLD_CONFIRM_DAYS +
                             ' 個交易日未再被提及');
        tr_(g, '移除未確認的持有聲明 ' + rd.open.date + '（之後 ' + HOLD_CONFIRM_DAYS + ' 個交易日沒有再被提及），其餘紀錄重建回合');
      });
    }
    if (!rounds.length) {
      // 沒有移除任何聲明卻開不成回合：持有聲明每一次都撞上同日賣出。
      if (!excludedOpen) { stat.holdConflict++; }
      tr_(g, excludedOpen ? '不列入：所有持有聲明都未通過確認' : '不列入：持有聲明每一次都與同日賣出矛盾');
      return;
    }

    var pendingHold = false;
    rounds.forEach(function (rd) {
      if (rd.openKind === 'hold' && holdConfirmState_(rd.open.date, recs, tradingDays) === 'pending') {
        rd.pending = true; pendingHold = true;
      }
    });
    tr_(g, '回合：' + rounds.map(function (rd) {
      return rd.open.date + (rd.openKind === 'hold' ? ' 會員持股' : ' 買入') + ' → ' +
             (rd.close ? rd.close.date + ' ' + rd.closeKind : '持有中') +
             (rd.avoidIgnored && rd.avoidIgnored.length ? '（不計出場的觀望不碰：' + rd.avoidIgnored.join('、') + '）' : '');
    }).join('；'));
    if (pendingHold) { stat.holdPending++; }
    if (rounds.length > 1) { stat.multiRound++; }
    if (rounds[0].openKind === 'hold') {
      stat.holdOpened++;
      holdOnlyOpened[g.name] = 1;
    }

    var lastRound = rounds[rounds.length - 1];

    // 最後一個回合還開著時，套用逾期規則。
    // 出場日與出場價以「最後一次提及」當日為準（含會員持股與觀望注意）。
    if (!lastRound.close) {
      var lastMention = recs[recs.length - 1].date;      // 「每天講了什麼」最近一次的日期
      if (tradingDaysAfter_(lastMention) > STALE_TRADING_DAYS) {
        lastRound.close = { date: lastMention, hint: null };
        lastRound.closeKind = '逾期未再提及';
        stat.autoClosed++;
        tr_(g, '逾期出場：最後一次提及 ' + lastMention + '，之後已過 ' + tradingDaysAfter_(lastMention) +
               ' 個交易日（門檻 ' + STALE_TRADING_DAYS + '）');
      }
    }
    if (lastRound.closeKind === '觀望不碰') { stat.avoidClosed++; }

    var candles = g.valid ? getCachedDailyK(g.code) : null;
    if (!g.valid) { stat.noCode++; }

    // 日K快取沒有這一檔時的備援。
    //
    // 兩種情況會踩到：當天才第一次出現的股票（日K還沒補到），
    // 以及賣掉後又買回來、第二回合進場日就在最近幾天的標的。
    // 先前這種情況 candles 是空的，priceOnDate_ 直接回空字串，
    // 於是進場價、現價、報酬三欄全部空白，表格上看起來像壞掉。
    //
    // 備援順序：先用即時報價，再退回講者明講的價位。
    // 兩者都標明來源，不會被誤認為是從日K算出來的。
    var fallbackPx = 0, fallbackSrc = '';
    if (g.valid && (!candles || !candles.length)) {
      try {
        var qc = getQuoteCache() || {};
        var q = qc[g.code];
        var qp = q ? Number(q.price || q.last || 0) : 0;
        if (qp > 0) {
          fallbackPx = qp;
          fallbackSrc = '日K快取尚無此檔，暫以最新成交價估算';
        }
      } catch (e) { /* 取不到就往下走 */ }
      stat.noDailyK++;
    }

    // 逐回合定價。已結束的回合也要算，才能列出回合明細與已實現報酬。
    rounds.forEach(function (rd, idxRound) {
      rd.entry = ''; rd.entrySrc = ''; rd.exit = ''; rd.exitSrc = '';

      if (!candles || !candles.length) {
        // 沒有日K可算。用講者明講的價位優先，其次即時報價，
        // 總比三欄全空、看起來像壞掉要好。
        var h0 = (rd.openKind === 'hold') ? null : rd.open.hint;
        if (!h0) {
          for (var q0 = 0; q0 < recs.length; q0++) {
            if (recs[q0].date >= rd.open.date && recs[q0].hint && recs[q0].hint.value) {
              h0 = recs[q0].hint; break;
            }
          }
        }
        if (h0 && h0.value) {
          rd.entry = h0.value;
          rd.entrySrc = '張震明講 ' + h0.value + '（日K快取尚無此檔，未經K線驗證）';
        } else if (fallbackPx) {
          rd.entry = fallbackPx;
          rd.entrySrc = fallbackSrc;
        }
        return;
      }

      // 由「會員持股」開倉的回合，那天並沒有成交，所以絕不採用明講價，
      // 一律取當日收盤，代表「這天他說會員手上有，價位大約在這裡」。
      // 由「會員持股」開倉的回合本來沒有價位可用，只能取當日收盤。
      // 但講者後來若真的講到這一檔的價位（例如先說會員持有，隔幾天才說
      // 「2330 以下」），那個價位比當日收盤更能代表他心裡的成本區間，
      // 應該優先採用。只看本回合期間內的紀錄，不會跨到別的回合去。
      var openHint = rd.open.hint, openHintDate = rd.open.date;
      if (rd.openKind === 'hold') {
        openHint = null;
        // 挑後續講到的價位之前先篩一次合理性。
        // 逐字稿裡的數字不一定是價位（月份、成數、名次都可能被抓成數字），
        // 挑到離譜的值不但沒幫助，還會蓋掉本來可用的當日收盤。
        var band = candles && candles.length ? {
          hi: Math.max.apply(null, candles.map(function (k) { return k.high; })),
          lo: Math.min.apply(null, candles.map(function (k) { return k.low; }))
        } : null;
        for (var hi2 = 0; hi2 < recs.length; hi2++) {
          var hr = recs[hi2];
          if (hr.date < rd.open.date) { continue; }
          if (rd.close && hr.date > rd.close.date) { break; }
          if (!hr.hint || !hr.hint.value) { continue; }
          var hv = hr.hint.value;
          if (band && (hv > band.hi * 1.3 || hv < band.lo * 0.7)) {
            Logger.log('    ' + g.name + ' 後續提到的 ' + hv +
                       ' 不在合理區間 ' + band.lo + '-' + band.hi + '，略過');
            continue;
          }
          openHint = hr.hint; openHintDate = hr.date; break;
        }
      }
      /* 進場價的往前下限＝上一回合的出場日。
         回述成本是合理的（「會員在 2135 買的」），但不能回述到上一回合
         還沒結束的時候——那段期間的成交屬於上一回合，不是這一回合的進場。 */
      var buyFloor = (idxRound > 0 && rounds[idxRound - 1] && rounds[idxRound - 1].exitDate)
        ? rounds[idxRound - 1].exitDate
        : ((idxRound > 0 && rounds[idxRound - 1] && rounds[idxRound - 1].close)
            ? rounds[idxRound - 1].close.date : '');
      // 由會員持有聲明開倉的回合，那天部位已經在手上：成本只能是那天或之前的成交，
      // 不往後找（2026/09/14 威剛 7/09 持有，成本卻被接到 8/06 才觸價，進場日跟著變成 8/06）。
      /* 沒明講買價時用當日最低價。
         但「首次明講會員持有」那天並沒有成交——部位是更早之前建立的，
         那一天的最低價跟他的成本沒有關係，所以維持當日收盤。 */
      var r1 = priceOnDate_(candles, rd.open.date, openHint, g.name, '買入', buyFloor,
                            { noForward: rd.openKind === 'hold',
                              unstated: rd.openKind === 'hold' ? 'close' : 'low' });
      rd.entry = r1.price;
      rd.entryDate = r1.date || rd.open.date;
      rd.entryUncovered = !!r1.uncovered;
      if (r1.uncovered) {
        rd.entrySrc = (rd.openKind === 'hold' ? '首次明講會員持有；' : '') + r1.src;
        stat.unverified++;
      } else if (rd.openKind === 'hold') {
        // 先前這裡一律寫「張震明講在 X 買入」，但那天是持有聲明，價位是另一天講到的成本。
        var said = openHint ? openHintDate + ' 提到成本 ' + openHint.value : '';
        if (!openHint) {
          rd.entrySrc = '首次明講會員持有，取當日收盤';
        } else if (r1.src === '影片明講') {
          rd.entrySrc = '首次明講會員持有；' + said + '，落在持有聲明當日的成交區間，採用之';
          stat.stated++;
        } else if (r1.backward) {
          rd.entrySrc = '首次明講會員持有；' + said + '，' + r1.date + ' 曾成交於此，採用之';
          stat.backward++;
        } else if (r1.unverified) {
          rd.entrySrc = '首次明講會員持有；' + said + '，日K快取查無成交紀錄，未經驗證仍採用';
          stat.unverified++;
        } else {
          rd.entrySrc = '首次明講會員持有；' + said + ' 未採用（' + r1.src + '）';
        }
      } else if (r1.src === '影片明講') {
        rd.entrySrc = '張震明講在 ' + rd.entry + ' 買入';
        stat.stated++;
      } else if (r1.filled) {
        // 成交日不等於發話日，要標明是往前回述還是往後等到的。
        if (r1.backward) {
          rd.entrySrc = '張震回述 ' + openHint.value + ' 的成本，' + r1.date + ' 曾成交於此';
          stat.backward++;
        } else {
          rd.entrySrc = '張震說' + (openHint.mode === 'below' ? '跌到 ' : '漲到 ') +
                        openHint.value + ' 買進，' + r1.date + ' 觸價';
          stat.filled++;
        }
      } else {
        rd.entrySrc = r1.src;   // 例如「當日收盤」「未觸價，取當日收盤」
        if (r1.unverified) { stat.unverified++; }
      }
      if (r1.rejected) { stat.rejected++; }

      if (rd.close && rd.closeKind === '逾期未再提及') {
        // 逾期出場不是成交：取「每天講了什麼」最後一次提及日的收盤。
        // 那天沒有日K時取之前最近的交易日，不往後找（priceOnDate_ 會往後找，所以這裡不用它）。
        var sc = closeOnOrBefore_(candles, rd.close.date);
        rd.exit = sc.price;
        rd.exitDate = sc.date || rd.close.date;
        rd.exitSrc = sc.price
          ? '逾 ' + STALE_TRADING_DAYS + ' 個交易日未再提及，取最後一次提及日 ' + rd.close.date +
            (sc.date === rd.close.date ? ' 收盤' : '（當天無日K）之前最近交易日 ' + sc.date + ' 收盤')
          : '最後一次提及日 ' + rd.close.date + ' 以前沒有日K，出場價待補齊日K後重算';
      } else if (rd.closeKind === '觀望不碰') {
        // 立場轉變不是成交。只取該日或之前最近交易日收盤，絕不借用未來K棒。
        // 當日日K稍後補齊時，下一次重算會自然換成當日收盤。
        var avoidClose = closeOnOrBefore_(candles, rd.close.date);
        rd.exit = avoidClose.price;
        rd.exitDate = avoidClose.date || rd.close.date;
        rd.exitSrc = avoidClose.price
          ? (avoidClose.date === rd.close.date ? '轉為觀望不碰，取當日收盤'
              : '轉為觀望不碰；當日無日K，暫取之前最近交易日 ' + avoidClose.date + ' 收盤，待補當日K後重算')
          : '轉為觀望不碰；該日以前無日K，出場價待補齊日K後重算';
      } else if (rd.close) {
        var useHint = (rd.closeKind === '賣出') ? rd.close.hint : null;
        /* 出場價的往前下限＝這一回合實際成交的那一天。
           賣出不可能發生在買進之前，往前找越過那一天就是在編造。 */
        var sellFloor = rd.entryDate || rd.open.date;
        /* 沒明講賣價時用當日最高價。
           但「觀望不碰」不是成交，只是立場轉變，那天並沒有賣出動作，
           用最高價會替一個沒發生的交易灌高報酬，所以它維持當日收盤。 */
        var r2 = priceOnDate_(candles, rd.close.date, useHint, g.name, '賣出', sellFloor,
                              { unstated: rd.closeKind === '賣出' ? 'high' : 'close' });
        rd.exit = r2.price;
        rd.exitDate = r2.date || rd.close.date;
        if (r2.rejected) { stat.rejected++; }
        if (r2.uncovered) {
          rd.exitSrc = r2.src;
        } else if (r2.src === '影片明講') {
          rd.exitSrc = '張震明講在 ' + rd.exit + ' 賣出';
        } else if (r2.filled) {
          rd.exitSrc = r2.backward
            ? ('張震回述 ' + useHint.value + ' 的出場價，' + r2.date + ' 曾成交於此')
            : ('張震說' + (useHint.mode === 'below' ? '跌到 ' : '漲到 ') +
               useHint.value + ' 賣出，' + r2.date + ' 觸價');
          if (r2.backward) { stat.backward++; } else { stat.filled++; }
        } else if (r2.unverified) {
          rd.exitSrc = '張震明講 ' + r2.unverified + '，日K快取查無觸價紀錄';
          stat.unverified++;
        } else {
          rd.exitSrc = r2.src;
        }
      }
      rd.ret = (rd.entry && rd.exit) ? Math.round((rd.exit - rd.entry) / rd.entry * 10000) / 100 : null;
      // 還開著的回合先用最後一根收盤估一個未實現，讓累積報酬涵蓋到現在。
      if (!rd.close && rd.entry && candles.length) {
        var lastC = candles[candles.length - 1].close;
        if (lastC) { rd.ret = Math.round((lastC - rd.entry) / rd.entry * 10000) / 100; }
      }
    });

    /* 管理者修正成本（v54）。

       2026/09/23 管理者：「它是 7 月買進、一直吃到現在，但我要改價格，要跑到 7 月去改。」
       逐日編輯改的是那一天的原始紀錄，改完還要等整條同步；這裡則是在後台「現有持股」直接填成本，
       以代號＋回合開始日對到那一回合，重算時蓋過系統推算的進場價。系統原判寫進來源欄，查得回去。 */
    rounds.forEach(function (rd) {
      var ov = costOverrides[String(g.code) + '|' + rd.open.date];
      if (!ov || !(ov.cost > 0)) { return; }
      rd.entrySrc = '管理者修正成本 ' + ov.cost + '（' + (ov.at || '') + (ov.note ? '，' + ov.note : '') + '）；系統原判：' +
                    (rd.entry ? rd.entry + '，' : '') + (rd.entrySrc || '未取得');
      rd.entry = ov.cost;
      rd.entryOverridden = true;
      if (rd.exit) { rd.ret = Math.round((rd.exit - rd.entry) / rd.entry * 10000) / 100; }
      else if (!rd.close && candles && candles.length && candles[candles.length - 1].close) {
        rd.ret = Math.round((candles[candles.length - 1].close - rd.entry) / rd.entry * 10000) / 100;
      } else if (!rd.close) { rd.ret = null; }
      stat.overridden = (stat.overridden || 0) + 1;
      tr_(g, '回合 ' + rd.open.date + ' 套用管理者修正成本 ' + ov.cost);
    });

    if (lastRound.entry) { stat.ok++; } else if (g.valid) { stat.noPrice++; }

    // 參考價位：本回合進場當天沒講價（進場價只好用收盤），
    // 但後面某一天以「觀望注意」的身分講到了價位時，把那個價位撈出來標註。
    // 這只是佐證，不會改動進場價，因為那天並沒有成交。
    var refPrice = '', refSrc = '';
    if (lastRound.entrySrc === '當日收盤') {
      for (var ri = 0; ri < recs.length; ri++) {
        var r = recs[ri];
        if (r.date < lastRound.open.date) { continue; }
        if (lastRound.close && r.date > lastRound.close.date) { break; }
        if (r.kind !== 'note' || !r.hint) { continue; }
        // 一樣要通過當日高低驗證，避免把營收、億元、指數點位當成股價。
        var rp = priceOnDate_(candles, r.date, r.hint, g.name, '觀望注意',
                                '', { unstated: 'close' });
        if (rp.src === '影片明講' || rp.filled) {
          refPrice = rp.price;
          refSrc = r.date + ' 觀望注意時提到 ' + rp.price;
          stat.refPrice++;
          break;
        }
      }
    }

    /* 提及次數。

       這個數字必須等於下面「逐日說明」的筆數，因為介面上它們是同一件事的
       兩種呈現：一個寫「影片提及 32 次」，一個把那 32 次一條一條列出來。
       先前一個從 recs 數、一個從去重後的 timeline 數，兩邊對不起來，
       而使用者會去數——數完發現只有 28 條，那個 32 就變成一個沒人相信的數字。

       所以改成先組 timeline，再拿它的筆數當提及次數。
       真正的數字在下面算完才填得出來，這裡先佔位。 */
    var mentions = 0;

    // 說明欄用最新一句有意義的說明。張震後續若補充（今天說抱著、今天說賣出等），
    // 要用最新的那句；只有完全沒有後續補充時才退回首次理由。
    var meaningful = recs.filter(function (r) {
      var t = String(r.reason || '').trim();
      return t && t !== '未說明' && t !== '會員持股';
    });
    var latestReason = naturalReason_(meaningful.length ? meaningful[meaningful.length - 1].reason
                                                       : (firstOpen.reason || '未說明'));
    // 這句說明是哪一天講的（v54）。表格上寫「9/22｜…」，讀的人才分得出是新是舊——
    // 2026/09/23 的檢查報告以為「最新說明其實是首次理由」，就是因為看不到日期。
    var latestReasonDate = meaningful.length ? meaningful[meaningful.length - 1].date : firstOpen.date;

    // 逐日說明：列出這一檔「所有」被提到的日期，含買入前的討論與會員持股。
    // 同一天若有多筆（例如同日既講買入又列持股），各留一行，只去除完全重複的。
    var seen = {};
    var timeline = recs
      .filter(function (r) {
        var k = r.date + '|' + (r.direction || '') + '|' + (r.reason || '');
        if (seen[k]) { return false; }
        seen[k] = 1;
        return true;
      })
      .map(function (r) {
        var head = (r.direction || '提及');
        if (r.date === rounds[0].open.date) {
          if (r.kind === 'buy') { head = '首次買入'; }
          else if (r.kind === 'hold' && rounds[0].openKind === 'hold') { head = '首次明講會員持有'; }
        }
        var px = r.price && r.price !== '未說明' ? '（' + r.price + '）' : '';
        return r.date + '\u3000' + head + px + '：' + (naturalReason_(r.reason) || '未說明');
      })
      .join('\n');

    // 一對一：提及次數就是逐日說明的筆數。
    mentions = timeline ? timeline.split('\n').length : 0;

    // 回合明細：每一回合一行，讓「買了又賣、賣了又買」看得出來。
    var roundDetail = rounds.map(function (rd, idx) {
      var openWord = (rd.openKind === 'hold') ? '首次明講會員持有' : '買入';
      var head = '第 ' + (idx + 1) + ' 回合\u3000' + rd.open.date + ' ' + openWord;
      if (rd.entry) { head += ' ' + rd.entry; }
      // 條件價的成交日與發話日不同，要標出來，否則會以為當天就買到了。
      if (rd.entryDate && rd.entryDate !== rd.open.date) {
        head += rd.entryUncovered ? '（日K最早 ' + rd.entryDate + '）' : '（' + rd.entryDate + ' 觸價）';
      }
      if (rd.avoidIgnored && rd.avoidIgnored.length) {
        head += '　中途曾轉觀望不碰 ' + rd.avoidIgnored.length + ' 次但未出場';
      }
      if (!rd.close) { return head + ' \u2192 持有中'; }
      var tail = ' \u2192 ' + rd.close.date + ' ' +
                 (rd.closeKind === '賣出' ? '賣出' :
                  rd.closeKind === '觀望不碰' ? '轉觀望不碰視為出場' : '逾期未再提及視為出場');
      if (rd.exit) { tail += ' ' + rd.exit; }
      if (rd.exitDate && rd.exitDate !== rd.close.date) {
        tail += rd.closeKind === '賣出' ? '（' + rd.exitDate + ' 觸價）' : '（取 ' + rd.exitDate + ' 收盤）';
      }
      if (rd.ret !== null && rd.ret !== undefined) {
        tail += '（' + (rd.ret > 0 ? '+' : '') + rd.ret.toFixed(2) + '%）';
      }
      return head + tail;
    }).join('\n');

    // 累積報酬：把每一個已結束回合的報酬連乘起來，再乘上目前這一回合的未實現。
    // 用連乘而不是相加，因為那才是真的把同一筆錢滾過每一回合的結果；
    // 相加會把「跌五成再漲五成」算成打平，實際上是虧了四分之一。
    var cum = 1, cumParts = [];
    rounds.forEach(function (rd) {
      if (rd.ret === null || rd.ret === undefined) { return; }
      cum *= (1 + rd.ret / 100);
      cumParts.push((rd.ret > 0 ? '+' : '') + rd.ret.toFixed(2) + '%');
    });
    var cumRet = cumParts.length ? Math.round((cum - 1) * 10000) / 100 : null;

    var stillHeld = !lastRound.close;
    // 尚在確認期內的持有聲明要標出來，讓人知道它還沒被證實、隨時可能被移除。
    var pendingNote = lastRound.pending
      ? '持有聲明尚在 ' + HOLD_CONFIRM_DAYS + ' 個交易日確認期內，若期滿仍未再被提及將移除'
      : '';
    /* 期間講過不碰卻沒有出場的，要說明白為什麼還算持有中。

       每一次都附上完整理由會變成一大段：兩次不碰就是兩句「（某日明講會員持有，
       證明部位並未在某日離開）」，塞進表格的一格裡是一整片文字牆，
       實際看到的就是那個樣子。理由對每一次都一樣，講一次就夠；
       日期用月/日就好，年份在旁邊的欄位已經看得到。 */
    if (lastRound.avoidIgnored && lastRound.avoidIgnored.length) {
      var days = lastRound.avoidIgnored.map(function (d) {
        return String(d).slice(5);          // 2026/08/31 → 08/31
      });
      var ig = '期間轉觀望不碰 ' + days.length + ' 次（' + days.join('、') +
               '），之後仍明講會員持有，故不計出場';
      pendingNote = pendingNote ? (pendingNote + '\n' + ig) : ig;
    }
    var exitReason = stillHeld ? pendingNote :
      (lastRound.closeKind === '賣出' ? '明講賣出' :
       lastRound.closeKind === '觀望不碰' ? '轉為觀望不碰，視為出場' :
       '逾 ' + STALE_TRADING_DAYS + ' 個交易日未再提及，視為出場');

    // 進場價來源欄：持有中就顯示進場來源；已出場則同時標明出場價來源。
    var srcCol = lastRound.entrySrc;
    if (!stillHeld && lastRound.exitSrc) { srcCol = lastRound.entrySrc + '；' + lastRound.exitSrc; }

    rows.push([
      g.valid ? g.code : '代號待確認',
      g.name,
      rounds[0].open.date,                       // 首次買入日：整檔的追蹤起點
      lastRound.entry, srcCol,
      lastRound.close ? lastRound.close.date : '',
      lastRound.exit,
      stillHeld ? '持有中' : '已出場',
      mentions, latestReason, timeline, now,
      rounds.length,                             // 回合數
      lastRound.open.date,                       // 本回合進場日
      exitReason,                                // 出場原因
      roundDetail,                               // 回合明細
      refPrice, refSrc,                          // 參考價位（後續觀望注意提到的價）
      rounds[0].openKind === 'hold' ? '會員持股聲明' : '明講買入',   // 首次進場方式
      lastRound.openKind === 'hold' ? '會員持股聲明' : '明講買入',   // 本回合進場方式
      cumRet === null ? '' : cumRet,                              // 累積報酬
      cumParts.join(' × '),                                       // 各回合明細
      /* 回合JSON：績效歷史重算要用的結構化版本。

         「回合明細」那一欄是給人看的文字（第 1 回合　2026/08/20 買入 38.5 → …），
         反解析回結構化資料很脆弱，格式一改就整批算錯。這裡多存一份機器讀的，
         成本是一個欄位，換來的是「某一天到底持有哪幾檔、成本多少」可以精確回答。

         有了它，重算歷史績效就不必把整個追蹤重跑一百多次——那在 Apps Script
         的執行時間上限下根本跑不完。只要這一份加上日K收盤價就夠了。 */
      JSON.stringify(rounds.map(function (rd) {
        return {
          o: rd.open.date,                                  // 進場日（發話日）
          od: rd.entryDate || rd.open.date,                 // 實際成交日（條件價可能晚幾天）
          e: rd.entry || null,                              // 進場價
          c: rd.close ? rd.close.date : '',                 // 出場日
          x: rd.exit || null,                               // 出場價
          k: rd.openKind === 'hold' ? 'hold' : 'buy',
          // 逾期未再提及的出場。判定出場的那天晚於出場日（相隔 10 個交易日），
          // 中間那段的每日績效當時是以持有中記下的，績效歷史重算要從出場日往後蓋掉。
          s: rd.closeKind === '逾期未再提及' ? 1 : 0
        };
      })),
      latestReasonDate                                             // 最新說明日期（v54）
    ]);
  });

  if (explain) {
    rows.forEach(function (row) {
      if (row[0] !== explain && String(row[1]).indexOf(explain) < 0) { return; }
      trace.push(row[1] + '（' + row[0] + '）　結果：' + row[7] + '；本回合進場 ' + row[13] + ' ' + row[3] +
                 '；出場 ' + (row[5] || '—') + ' ' + (row[6] || '') + '；' + (row[14] || '') + '；' + row[4]);
    });
    if (!trace.length) { trace.push('操作紀錄與會員持股裡找不到代號或名稱含「' + explain + '」的列'); }
  }
  stat.trace = trace;
  if (dryRun) { return stat; }

  withLock_(function () {
    var sh = getSheet_('持股追蹤');
    sh.clearContents();
    var headers = ['代號', '股票名稱', '首次買入日', '進場價', '進場價來源',
                   '最近賣出日', '出場價', '狀態', '提及次數', '首次理由', '逐日說明', '更新時間',
                   '回合數', '本回合進場日', '出場原因', '回合明細', '參考價位', '參考價位來源',
                   '首次進場方式', '本回合進場方式', '累積報酬', '各回合報酬', '回合JSON', '最新說明日期'];
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    if (rows.length) { sh.getRange(2, 1, rows.length, headers.length).setValues(rows); }
  });

  CACHE.remove('tracker');
  CACHE.remove('dash_v54');   // 首頁快取（API.gs）與持股追蹤同一個來源，一起清

  Logger.log('持股追蹤重算完成，共 ' + rows.length + ' 檔（耗時 ' +
             Math.round((Date.now() - start) / 1000) + ' 秒）');
  Logger.log('  進場價已算出　' + stat.ok + ' 檔（其中 ' + stat.stated + ' 次用影片明講的價格）');
  if (stat.multiRound) {
    Logger.log('  有兩段以上持有回合（賣出後又買回）　' + stat.multiRound + ' 檔');
  }
  if (stat.avoidClosed) {
    Logger.log('  最後由「觀望不碰」平倉　' + stat.avoidClosed + ' 檔');
  }
  if (stat.autoClosed) {
    Logger.log('  超過 ' + STALE_TRADING_DAYS + ' 個交易日未再提及，自動轉為已出場　' + stat.autoClosed + ' 檔');
  }
  if (stat.filled) {
    Logger.log('  條件價往後找到觸價日才成交　' + stat.filled + ' 次');
    Logger.log('  （例如「255 以上賣掉」，講的當天還沒到，後來漲抵才算成交）');
  }
  if (stat.backward) {
    Logger.log('  往前找到觸價日（講者回述先前成本）　' + stat.backward + ' 次');
    Logger.log('  （例如「會員在 2135 買的」，2135 是過去成交價，不是當天的）');
  }
  if (stat.unverified) {
    Logger.log('  明講價在日K快取中查無觸價紀錄，仍採用明講價　' + stat.unverified + ' 次');
    Logger.log('  （多半是日K快取沒涵蓋到那一段，可執行 backfillDailyKJob() 補齊）');
  }
  if (stat.refPrice) {
    Logger.log('  進場當天未說明價位，改由後續「觀望注意」補上參考價位　' + stat.refPrice + ' 檔');
  }
  if (stat.noOpen) {
    Logger.log('  沒有任何開倉動作（既沒買入、也沒被明講會員持有），未列入　' + stat.noOpen + ' 檔');
  }
  if (stat.holdOpened) {
    Logger.log('  由「影片中明講會員持有」開倉、沒有對應買入紀錄的　' + stat.holdOpened + ' 檔：' +
               Object.keys(holdOnlyOpened).slice(0, 20).join('、'));
    Logger.log('  這些進場價取當日收盤，因為那天並沒有成交。');
  }
  if (stat.holdDropped) {
    Logger.log('  持有聲明未通過 ' + HOLD_CONFIRM_DAYS + ' 個交易日確認，判定為擷取錯誤已移除　' +
               stat.holdDropped + ' 筆：');
    holdDroppedList.slice(0, 30).forEach(function (t) { Logger.log('      ' + t); });
    if (holdDroppedList.length > 30) {
      Logger.log('      ……另有 ' + (holdDroppedList.length - 30) + ' 筆');
    }
  }
  if (stat.holdPending) {
    Logger.log('  持有聲明尚在確認期內（未滿 ' + HOLD_CONFIRM_DAYS + ' 個交易日），暫時保留　' +
               stat.holdPending + ' 檔');
    Logger.log('  這些會標為待確認。等確認期過完，下一次重算才會判定要留還是要移除。');
  }
  if (stat.holdConflict) {
    Logger.log('  有持有聲明但同日又被講成賣出，不予採信　' + stat.holdConflict + ' 檔');
  }
  if (stat.rejected) {
    Logger.log('  明講價未通過當日高低驗證，已退回收盤價　' + stat.rejected + ' 筆');
  }
  if (stat.noDailyK) {
    Logger.log('  日K快取沒有這一檔，已改用明講價或即時報價估算　' + stat.noDailyK + ' 檔');
    Logger.log('  （多半是當天新增或剛買回的標的。執行 backfillDailyKJob() 補齊後會更準）');
  }
  if (stat.noCode) {
    Logger.log('  代號待確認　　' + stat.noCode + ' 檔　<<< 請執行 repairCodesJob()，或到 GitHub 重跑 backfill');
  }
  if (stat.noPrice) {
    Logger.log('  缺進場價　　　' + stat.noPrice + ' 檔　<<< 日K快取還沒補到，請執行 backfillDailyKJob()（會自動續跑到補完）');
  }
  return stat;
}

/**
 * 前端用。讀持股追蹤分頁，配上即時快取算出目前報酬。
 * 全部從試算表讀，不對外請求，所以很快。
 */
/** 持股成本覆寫表 → { '代號|回合開始日': {cost, note, at} }。分頁還沒建立時回空物件。 */
function readCostOverrides_() {
  var out = {};
  try {
    readSheetObjects_('持股成本覆寫').forEach(function (r) {
      var code = String(r['代號'] || '').trim(), d = fmtDate_(r['回合開始日']), cost = Number(r['成本']);
      if (!code || !d || !(cost > 0)) { return; }
      out[code + '|' + d] = { cost: cost, note: String(r['備註'] || '').trim(), at: String(r['修改時間'] || '').trim() };
    });
  } catch (e) { /* 分頁不存在＝沒有任何覆寫 */ }
  return out;
}

function getHoldingsTracker() {
  var hit = CACHE.get('tracker');
  if (hit) { return JSON.parse(hit); }

  var base = readSheetObjects_('持股追蹤');
  if (!base.length) {
    return {
      items: [], summary: null,
      note: '持股追蹤尚未建立。管理者請在 Apps Script 執行 bootstrapAll()。',
      basis: ''
    };
  }

  var quotes = getQuoteCache();
  var today = todayStr_();
  // 補日K時記下來的失敗原因。讀不到就當空的，說明文字自動退回一般版本。
  var missReason = (typeof dailyKMissingReasons_ === 'function')
    ? dailyKMissingReasons_() : {};

  var items = base.map(function (r) {
    var code = String(r['代號']).trim();
    var valid = /^(?:00981A|\d{4,6})$/.test(code);
    var stillHeld = String(r['狀態']) === '持有中';
    var entry = Number(r['進場價']) || null;
    var entrySrc = r['進場價來源'] || '';

    var current = null, curSrc = '';
    if (!valid) {
      current = null;
    } else if (stillHeld) {
      // 持有中：以最新價格計算未實現損益。
      // 即時快取只在盤中更新，取不到就用日K最後一根收盤價。
      var q = quotes[code];
      if (q && q.last != null) {
        current = q.last;
        curSrc = '即時';
      } else {
        var lc = lastCloseOf_(code);
        if (lc) { current = lc.last; curSrc = lc.date + ' 收盤'; }
      }
    } else {
      // 已出場：報酬必須用「賣出當日的價格」算，這是已實現損益，是固定的事實。
      // 絕對不可以用今天的價格代替，否則賣出後股價再漲跌，
      // 已經結束的那筆交易的報酬率會跟著變動，那是錯的。
      current = Number(r['出場價']) || null;
      curSrc = current ? '賣出當日' : '';
    }

    var ret = (entry && current) ? Math.round((current - entry) / entry * 10000) / 100 : null;
    var firstBuy = fmtDate_(r['首次買入日']);
    // 持有天數要用「本回合進場日」算，不是整檔的首次買入日。
    // 賣掉又買回來的標的，用首次買入日會把中間沒持有的那段也算進去。
    var roundStart = fmtDate_(r['本回合進場日']) || firstBuy;
    var endDate = stillHeld ? today : fmtDate_(r['最近賣出日']);

    /* 缺數字的時候要講出真正的原因。

       原本三種情況都只寫「可到後台按補齊日K並重算」，但那句話有時候是錯的：
       這一檔如果根本查不到歷史行情（代號打錯、剛上市、已下市），
       按幾次都不會好，而使用者會一直按。現在重算之前已經會自動補一次，
       所以還缺的必然是補不到，那就把補不到的理由直接寫出來。 */
    var why = '';
    if (!valid) { why = '代號待確認，無法取價'; }
    else if (!entry) {
      var r1 = missReason[code];
      why = r1 ? ('這一檔取不到歷史行情（' + r1 + '），因此算不出進場價')
               : '進場價尚未算出。日K可能還在補，下一次重算會補上';
    }
    else if (!current) {
      var r2 = missReason[code];
      why = r2 ? ('這一檔取不到歷史行情（' + r2 + '），因此沒有現價')
               : '日K快取還沒有這一檔的最新報價，下一次重算會補上';
    }

    return {
      code: code, name: r['股票名稱'], valid: valid,
      // 「首次理由」欄名是歷史遺留：重算時寫進去的其實是最新一句有意義的說明（latestReason）。
      firstBuy: firstBuy, firstReason: r['首次理由'] || '未說明',
      latestReason: r['首次理由'] || '未說明', latestReasonDate: fmtDate_(r['最新說明日期']),
      timeline: r['逐日說明'] || '',
      lastSell: fmtDate_(r['最近賣出日']), stillHeld: stillHeld,
      days: daysBetween_(roundStart, endDate),
      entry: entry, entrySrc: entrySrc, current: current, curSrc: curSrc, ret: ret,
      mentions: Number(r['提及次數']) || 0,
      why: why,
      // 回合模型：買了又賣、賣了又買時，表格顯示的是最新那一回合
      rounds: Number(r['回合數']) || 1,
      roundStart: roundStart,
      exitReason: r['出場原因'] || '',
      roundDetail: r['回合明細'] || '',
      // 進場當天沒講價、後續以「觀望注意」提到的價位。只作佐證，不改進場價。
      refPrice: Number(r['參考價位']) || null,
      refSrc: r['參考價位來源'] || '',
      // 進場方式：明講買入，或第一次被明講「會員目前持有」。
      // 後者沒有成交價，進場價是當日收盤，介面上要標示清楚以免誤解。
      openBy: r['本回合進場方式'] || '明講買入',
      firstOpenBy: r['首次進場方式'] || '明講買入',
      // 多回合時的累積報酬（各回合連乘）與各回合明細
      cumRet: (r['累積報酬'] === '' || r['累積報酬'] == null) ? null : Number(r['累積報酬']),
      roundRets: r['各回合報酬'] || '',
      // 累積與各回合報酬是「重算那一天」的收盤算的；本回合報酬用即時價（v54，Codex 規格 27）。兩個時間點不同，畫面要標出來。
      roundsAsOf: fmtDate_(String(r['更新時間'] || '').slice(0, 10))
    };
  });

  function byReturn(a, b) {
    if ((a.ret === null) !== (b.ret === null)) { return a.ret === null ? 1 : -1; }
    if (a.ret === null) { return b.days - a.days; }
    return b.ret - a.ret;
  }

  // 持有中與已出場必須分開呈現，因為兩者的報酬意義不同：
  // 持有中是「未實現損益」，會隨股價變動；
  // 已出場是「已實現損益」，用買入價與賣出價算，賣出後就固定不再變。
  // 混在一起看會誤以為是同一種數字。
  var held = items.filter(function (i) { return i.stillHeld; }).sort(byReturn);
  var exited = items.filter(function (i) { return !i.stillHeld; }).sort(byReturn);

  function summarize(list) {
    var priced = list.filter(function (i) { return i.ret !== null; });
    if (!priced.length) {
      return { total: list.length, priced: 0, pending: list.length, avgReturn: null, positiveRatio: null };
    }
    var sum = priced.reduce(function (s, i) { return s + i.ret; }, 0);
    return {
      total: list.length,
      priced: priced.length,
      pending: list.length - priced.length,
      avgReturn: Math.round(sum / priced.length * 100) / 100,
      positiveRatio: Math.round(priced.filter(function (i) { return i.ret > 0; }).length / priced.length * 1000) / 10
    };
  }

  var heldSummary = summarize(held);
  var exitedSummary = summarize(exited);

  // 舊欄位保留，內容維持「持有中」的統計，避免其他呼叫端壞掉
  var summary = heldSummary.priced ? {
    total: items.length,
    holding: held.length,
    priced: heldSummary.priced,
    pending: heldSummary.pending,
    avgReturn: heldSummary.avgReturn,
    positiveRatio: heldSummary.positiveRatio
  } : null;

  var out = {
    items: held.concat(exited),
    held: held,
    exited: exited,
    heldSummary: heldSummary,
    exitedSummary: exitedSummary,
    summary: summary,
    note: '',
    heldBasis: '持有中為未實現損益。同一檔可能買了又賣、賣了又買，因此以「回合」計算：一個回合是一次進場到一次出場，表格顯示的是最新那一個回合，進場價與持有天數都從本回合進場日起算。進場價取本回合進場當日的收盤價，影片若明講了價格且該價格落在當日最高與最低之間，才改用明講價。報酬以最新成交價計算，盤後或取不到即時報價時用最後一根日K收盤價，因此會隨股價每日變動。',
    exitedBasis: '已出場為已實現損益，用本回合的進場價與出場價計算，出場之後不再變動，後續股價漲跌與這個數字無關。出場有三種情形：明講賣出時取賣出當日價；轉為「觀望不碰」時視為出場，取當日收盤價，因為那不是一筆成交而是立場由持有轉為不碰；超過 10 個交易日未再被提及時自動視為出場，出場日為最後一次提及的日期，取那一天的收盤價；那天沒有日K時取之前最近一個交易日的收盤。',
    basis: '進場有兩種認定：影片中明講買入，或影片中明講「會員目前持有」而先前沒有對應的買入紀錄。後者那天並沒有成交，進場價取當日收盤，表格會標示為會員持股聲明。同日另有觀望不碰不作廢持股聲明，也不因此平倉；明講賣出仍依交易先後處理。觀望注意不影響持有狀態，觀望不碰視為出場。未計入交易成本與部位大小。'
  };

  CACHE.put('tracker', JSON.stringify(out), 300);
  return out;
}

/** 個股在追蹤表裡的單筆資料 */
function getStockTracker(code) {
  code = String(code || '').trim();
  var all = getHoldingsTracker();
  return all.items.filter(function (i) { return i.code === code; })[0] || null;
}

/* ------------------------------------------------------------------ *
 * 4.4 查詢功能
 * ------------------------------------------------------------------ */

/* 搜尋字 → 要比對的名稱與代號（v54）。
   一、去掉官方簡稱尾巴的「*」與前後空白（「聖暉*」「 2330 」）。
   二、聽錯或唸法不同的寫法（秦城、立即電、四星）用確認過的別名表換成正式名稱與代號，
       表與 pipeline 的 CONFIRMED_NAMES 同一份（由 sync_quality.py 同步），不在這裡另外猜。 */
function searchTerms_(keyword) {
  var kw = String(keyword || '').replace(/[*＊\s]+/g, '').trim();
  var terms = kw ? [kw] : [], code = '';
  var table = (typeof PUBLIC_CONFIRMED_NAMES === 'object' && PUBLIC_CONFIRMED_NAMES) || {};
  if (table[kw]) {
    code = table[kw][0];
    terms.push(String(table[kw][1]).replace(/[*＊]+$/, ''), code);
  }
  return { kw: kw, terms: terms, code: code };
}

function searchStock(keyword) {
  var q = searchTerms_(keyword), kw = q.kw;
  if (!kw) { return { keyword: '', trades: [], holdings: [], found: false }; }

  function hit(r) {
    var name = String(r['股票名稱']).replace(/[*＊]+$/, ''), code = String(r['代號']);
    return q.terms.some(function (t) { return name.indexOf(t) >= 0 || code.indexOf(t) >= 0; });
  }

  /* 「影片中提到的每一次」要與「每天講了什麼」是同一份清單。

     先前這裡只讀操作紀錄，而逐日說明讀的是操作紀錄加會員持股，
     於是同一個面板上兩個區塊列出來的筆數不一樣——上面說提及 32 次，
     下面的表只有 28 列，而且少掉的正好是他明講會員持有的那幾天。
     兩邊都是「他提到這一檔」的紀錄，本來就該是同一份。 */
  var seen = {};
  function dedupe_(x) {
    var k = x.date + '|' + x.direction + '|' + x.reason;
    if (seen[k]) { return false; }
    seen[k] = 1;
    return true;
  }

  var trades = readSheetObjects_('操作紀錄').filter(hit).map(function (r) {
    return {
      date: fmtDate_(r['日期']),
      name: r['股票名稱'],
      code: r['代號'] || '代號待確認',
      direction: r['方向'],
      price: r['價位說明'] || '未說明',
      reason: naturalReason_(r['理由摘錄']) || '未說明',
      videoId: r['來源影片ID'],
      seq: Number(r['序']) || 0
    };
  });

  var holdings = readSheetObjects_('會員持股').filter(hit).map(function (r) {
    return {
      date: fmtDate_(r['日期']),
      name: r['股票名稱'],
      code: r['代號'] || '代號待確認',
      stance: r['目前立場'],
      note: naturalReason_(r['說明重點']),
      videoId: r['來源影片ID']
    };
  }).sort(function (a, b) { return a.date < b.date ? 1 : -1; });

  // 會員持股併進來，方向就寫「會員持股」，價位那一欄它本來就沒有。
  holdings.forEach(function (h) {
    trades.push({
      date: h.date, name: h.name, code: h.code,
      direction: '會員持股', price: '未說明',
      reason: h.note || h.stance || '會員持股',
      videoId: h.videoId, seq: 0
    });
  });

  // 去重之後由新到舊。同一天有多筆時，序小的排前面（早上的動作在上面）。
  trades = trades.filter(dedupe_).sort(function (a, b) {
    if (a.date !== b.date) { return a.date < b.date ? 1 : -1; }
    return (a.seq || 99) - (b.seq || 99);
  });

  var buys = trades.filter(function (t) { return t.direction.indexOf('買') === 0; });
  var sells = trades.filter(function (t) { return t.direction.indexOf('賣') === 0; });

  // 結果只有一檔（輸入名稱或別名時）就回傳它的代號，前端直接開行情圖。
  var codes = {};
  trades.forEach(function (t) { if (/^\d{4,6}[A-Z]?$/.test(String(t.code))) { codes[t.code] = 1; } });
  var only = Object.keys(codes);
  return {
    keyword: kw,
    resolvedCode: q.code || (only.length === 1 ? only[0] : ''),
    aliasOf: q.code ? q.terms[1] : '',
    found: !!(trades.length || holdings.length),
    firstBuy: buys.length ? buys[buys.length - 1].date : '',
    lastSell: sells.length ? sells[0].date : '',
    stillHeld: buys.length > 0 && (!sells.length || sells[0].date < buys[0].date),
    trades: trades,
    holdings: holdings
  };
}

/**
 * 有紀錄的日期清單，新到舊。供「翻到某一天」的日曆標記哪幾天點得下去。
 * 只列出真的有操作紀錄或會員持股的日期，沒資料的日期在日曆上就是不可點，
 * 使用者不必先點下去才發現那天沒東西。
 */
function listRecordDates() {
  var hit = CACHE.get('recordDates');
  if (hit) { return JSON.parse(hit); }

  var set = {};
  readSheetObjects_('操作紀錄').forEach(function (r) {
    var d = fmtDate_(r['日期']); if (d) { set[d] = (set[d] || 0) + 1; }
  });
  readSheetObjects_('會員持股').forEach(function (r) {
    var d = fmtDate_(r['日期']); if (d) { set[d] = (set[d] || 0) + 1; }
  });
  try {
    readSheetObjects_('會員簡訊').forEach(function (r) {
      var d = cmDate_(r['發文時間']); if (d) { set[d] = (set[d] || 0) + 1; }
    });
  } catch (e) {}

  var out = Object.keys(set).sort().reverse().map(function (d) {
    return { date: d, count: set[d] };
  });
  CACHE.put('recordDates', JSON.stringify(out), 300);
  return out;
}

function searchByDate(dateStr) {
  var d = fmtDate_(dateStr);
  var trades = readSheetObjects_('操作紀錄').filter(function (r) { return fmtDate_(r['日期']) === d; });
  var holds = readSheetObjects_('會員持股').filter(function (r) { return fmtDate_(r['日期']) === d; });
  var video = readSheetObjects_('影片清單').filter(function (r) { return fmtDate_(r['發布日期']) === d; })[0];

  // 會員通知即時併入總覽：查詢特定日期時同樣併入該日簡訊紀錄
  mergeSmsItemsIntoTrades_(d, trades, holds);

  return {
    date: d,
    found: !!(trades.length || holds.length),
    videoId: video ? video['影片ID'] : '',
    videoTitle: video ? video['標題'] : '',
    status: video ? video['處理狀態'] : (trades.length || holds.length ? '會員通知已即時併入' : ''),
    buy: trades.filter(function (r) { return String(r['方向']).indexOf('買') === 0; }).map(mapTrade_),
    sell: trades.filter(function (r) { return String(r['方向']).indexOf('賣') === 0; }).map(mapTrade_),
    watchAvoid: trades.filter(function (r) { return String(r['方向']).indexOf('觀望不碰') >= 0 || String(r['方向']).indexOf('不碰') >= 0; }).map(mapTrade_),
    watchWatch: trades.filter(function (r) { return String(r['方向']).indexOf('觀望注意') >= 0; }).map(mapTrade_),
    watch: trades.filter(function (r) { return String(r['方向']).indexOf('買') < 0 && String(r['方向']).indexOf('賣') < 0; }).map(mapTrade_),
    // 同日觀望不碰可能是講另一個情境，不能抹掉已明講的會員持股。
    // 真正明講賣出的同日列才由後續持股回合判定；此處保留逐字稿聲明供讀者核對。
    holdings: (function () {
      return holds
        .map(function (r) {
          return { name: r['股票名稱'], code: r['代號'] || '代號待確認',
                   stance: r['目前立場'], note: naturalReason_(r['說明重點']) };
        });
    })()
  };
}

function mapTrade_(r) {
  return {
    name: r['股票名稱'],
    code: r['代號'] || '代號待確認',
    price: r['價位說明'] || '未說明',
    reason: naturalReason_(r['理由摘錄']) || '未說明'
  };
}

/* ------------------------------------------------------------------ *
 * 4.6 使用者上下文記憶
 * ------------------------------------------------------------------ */

function rememberQuery(email, keyword) {
  if (!email || !keyword) { return; }
  withLock_(function () {
    var sh = getSheet_('使用者上下文記憶');
    var rows = sh.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (String(rows[i][0]).toLowerCase() === String(email).toLowerCase()) {
        var hist = String(rows[i][1] || '').split(',').filter(String);
        hist.unshift(keyword);
        hist = hist.filter(function (v, idx, arr) { return arr.indexOf(v) === idx; }).slice(0, 12);
        sh.getRange(i + 1, 2).setValue(hist.join(','));
        sh.getRange(i + 1, 4).setValue(todayStr_());
        return;
      }
    }
    sh.appendRow([email, keyword, '', todayStr_()]);
  });
}

function getUserContext(email) {
  if (!email) { return { recent: [] }; }
  var rows = readSheetObjects_('使用者上下文記憶').filter(function (r) {
    return String(r['使用者識別']).toLowerCase() === String(email).toLowerCase();
  });
  if (!rows.length) { return { recent: [] }; }
  return { recent: String(rows[0]['歷史互動摘要'] || '').split(',').filter(String) };
}

/* ------------------------------------------------------------------ *
 * 3.5.3 年度封存
 * ------------------------------------------------------------------ */

function yearlyArchiveJob() {
  var now = new Date();
  if (Number(Utilities.formatDate(now, TZ, 'M')) !== 1) { return; }

  var lastYear = Number(Utilities.formatDate(now, TZ, 'yyyy')) - 1;
  ['操作紀錄', '每日推播內容', '影片清單'].forEach(function (name) {
    archiveSheetYear_(name, lastYear);
  });
}

function archiveSheetYear_(sheetName, year) {
  withLock_(function () {
    var ss = getSS_();
    var sh = ss.getSheetByName(sheetName);
    var target = sheetName + '_' + year;
    if (ss.getSheetByName(target)) { return; }

    var values = sh.getDataRange().getValues();
    if (values.length < 2) { return; }
    var headers = values[0];
    var dateCol = headers.indexOf('日期') >= 0 ? headers.indexOf('日期') : headers.indexOf('發布日期');
    if (dateCol < 0) { return; }

    var keep = [], move = [];
    values.slice(1).forEach(function (row) {
      var d = fmtDate_(row[dateCol]);
      if (d && Number(d.slice(0, 4)) === year) { move.push(row); } else { keep.push(row); }
    });
    if (!move.length) { return; }

    var arch = ss.insertSheet(target);
    arch.getRange(1, 1, 1, headers.length).setValues([headers]);
    arch.getRange(2, 1, move.length, headers.length).setValues(move);

    sh.clearContents();
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    if (keep.length) { sh.getRange(2, 1, keep.length, headers.length).setValues(keep); }

    ss.getSheetByName('資料索引').appendRow([target, sheetName, year + '/01/01 至 ' + year + '/12/31', todayStr_()]);
  });
}

/**
 * 乾跑：列出「由會員持股聲明開倉」的每一檔目前的確認狀態，不寫任何資料。
 *
 * 用途是在真的重算之前先看一眼會刪掉哪些，確認不是誤刪。
 * 直接在編輯器執行這一支，看執行紀錄即可，跑完不會動到試算表。
 */
function auditUnconfirmedHoldings() {
  var trades = readSheetObjects_('操作紀錄').filter(function (r) {
    return String(r['股票名稱'] || '').trim();
  });
  var groups = {};
  function push_(name, code, date, dir, reason, seq) {
    if (!name) { return; }
    var valid = /^(?:00981A|\d{4,6})$/.test(String(code || '').trim());
    var key = valid ? String(code).trim() : ('NAME:' + name);
    if (!groups[key]) {
      groups[key] = { code: valid ? String(code).trim() : '', name: name, valid: valid, records: [] };
    }
    groups[key].records.push({
      date: fmtDate_(date), reason: reason || '', direction: dir || '',
      kind: classifyDirection_(dir), seq: Number(seq) || 0, price: '', hint: null
    });
  }
  trades.forEach(function (r) {
    push_(String(r['股票名稱']).trim(), r['代號'], r['日期'], String(r['方向']),
          r['理由摘錄'], r['序']);
  });
  readSheetObjects_('會員持股').forEach(function (r) {
    push_(String(r['股票名稱'] || '').trim(), r['代號'], r['日期'], '會員持股',
          r['說明重點'] || r['目前立場'] || '會員持股');
  });

  var tradingDays = (function () {
    var set = {};
    readSheetObjects_('日K快取').forEach(function (r) {
      var d = fmtDate_(r['日期']); if (d) { set[d] = 1; }
    });
    return Object.keys(set).sort();
  })();

  var out = { confirmed: [], unconfirmed: [], pending: [] };
  Object.keys(groups).forEach(function (key) {
    var g = groups[key];
    var recs = sortRecords_(g.records);
    buildRounds_(recs, tradingDays).forEach(function (rd) {
      if (rd.openKind !== 'hold') { return; }
      var st = holdConfirmState_(rd.open.date, recs, tradingDays);
      var after = recs.filter(function (r) { return r.date > rd.open.date; });
      out[st].push(g.name + '（' + (g.valid ? g.code : '代號待確認') + '）　開倉 ' +
                   rd.open.date + '　之後被提及 ' + after.length + ' 次' +
                   (after.length ? '，最近一次 ' + after[after.length - 1].date : ''));
    });
  });

  Logger.log('===== 會員持股聲明確認狀態（乾跑，未寫入任何資料）=====');
  Logger.log('確認門檻：開倉後 ' + HOLD_CONFIRM_DAYS + ' 個交易日內至少要再被提及一次');
  Logger.log('');
  Logger.log('【會被移除】期限已過且完全沒有後續　' + out.unconfirmed.length + ' 筆');
  out.unconfirmed.forEach(function (t) { Logger.log('    ' + t); });
  Logger.log('');
  Logger.log('【暫時保留】確認期還沒過完　' + out.pending.length + ' 筆');
  out.pending.forEach(function (t) { Logger.log('    ' + t); });
  Logger.log('');
  Logger.log('【會保留】期限內有再被提及　' + out.confirmed.length + ' 筆');
  out.confirmed.slice(0, 40).forEach(function (t) { Logger.log('    ' + t); });
  if (out.confirmed.length > 40) {
    Logger.log('    ……另有 ' + (out.confirmed.length - 40) + ' 筆');
  }
  Logger.log('');
  Logger.log('確認名單沒問題後，執行 rebuildHoldingsTrackerJob() 套用，');
  Logger.log('或用網站的「立即刷新網站內容」一次跑完全站重算。');
  return out;
}


/* ==================================================================== *
 * 逐字稿瀏覽
 * ==================================================================== */

/** 有逐字稿的日期清單，供日曆標記哪幾天點得下去。 */
function listTranscriptDates() {
  var hit = CACHE.get('txDates');
  if (hit) { return JSON.parse(hit); }

  var byDate={},groups={};
  readSheetObjects_('影片清單').forEach(function(r){
    var d=fmtDate_(r['發布日期']);if(d){(groups[d]||(groups[d]=[])).push(r);}
  });
  Object.keys(groups).forEach(function(d){
    var selected=selectTranscriptRow_(groups[d],'',d).row;
    var text=displayTranscriptText_(selected);if(text.length<200){return;}
    byDate[d]={date:d,chars:text.length,title:String(selected['標題']||selected['影片標題']||''),
      videoId:String(selected['影片ID']||''),raw:text===String(selected['原始逐字稿內容']||'')};
  });
  var out = Object.keys(byDate).map(function (k) { return byDate[k]; });
  out.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
  CACHE.put('txDates', JSON.stringify(out), 300);
  return out;
}

/**
 * 取某一天的逐字稿，並排好版。
 *
 * 直接把兩萬字倒出來沒有人看得下去：沒有段落、沒有重點、找不到位置。
 * 排好的版面存在影片清單的「排版稿JSON」（工作流寫入時排一次，這裡每 15 分鐘補排，見 ensureTranscriptLayoutJob）。
 * 沒有版面、或版面與原文指紋對不上時，先用規則分段顯示，不讓讀者等。
 */
function getTranscript(dateStr) {
  var d = fmtDate_(dateStr);

  // 同一天可能有多列。後台投稿會用 MANUAL-日期 當影片ID 另外建一列，
  // 若當天稍後又補上了真正的影片，就會出現一列有內容、一列全空的情況。
  // 先前用 forEach 逐列覆蓋，最後留下的是「最後一列」而不是「有內容那列」，
  // 於是明明資料就在試算表裡，畫面卻說沒有逐字稿。
  // 改成挑內容最長的那一列。
  var selected=selectTranscriptRow_(readSheetObjects_('影片清單'),'',d);
  var row=selected ? selected.row : null;
  if (!row) { return { found: false, date: d }; }

  var v1 = String(row['原始逐字稿內容'] || '');
  var v2 = transcriptDisplayText_(row);
  var isRaw = v2 === v1;
  if (v2.length < 200) {
    return { found: false, date: d,
             reason: '這一天的影片清單裡沒有逐字稿內容（修飾後 ' + String(row['修飾後逐字稿內容'] || '').length +
                     ' 字、原始 ' + v1.length + ' 字）。' +
                     '可能是還沒處理完，或該列的逐字稿欄位是空的。' };
  }

  var base = { found: true, date: d, title: String(row['標題'] || row['影片標題'] || ''),
               videoId: String(row['影片ID'] || ''), chars: v2.replace(/\s/g, '').length, raw: isRaw };
  var stored = storedTranscriptLayout_(row, v2);
  if (stored) {
    var method = storedLayoutMethod_(stored, row['排版稿方式']);
    base.sections = tidySections_(stored);
    base.formatted = method === 'ai';
    base.layoutSource = 'pipeline';
    base.layoutMethod = method;
    return base;
  }
  base.sections = transcriptRuleSections_(transcriptSentences_(v2));
  base.formatted = false;
  base.layoutMethod = 'none';
  return base;
}

/** 網站顯示的那一份原文：潤飾稿與原文字不一樣就用原文；潤飾稿太短而原文夠長也用原文。 */
function transcriptDisplayText_(row) {
  var v1 = String(row['原始逐字稿內容'] || '');
  var v2 = displayTranscriptText_(row);
  if (v2.length < 200 && v1.length >= 200) { v2 = v1; }
  return stripTranscribeEcho_(v2);
}

/* 聽打請求的封套回聲（v54）。與 pipeline.py strip_transcribe_echo、transcript.py 同一組規則：
   只拿掉「請聽打這段影片 h:mm:ss 到 h:mm:ss 的完整逐字稿」與「可能出現的專有名詞：」後的逗號清單。
   2026/09/23 原文第 10 段混進這兩句——那是我們送出的請求被模型照抄回來，不是節目內容。 */
var TX_ECHO_PROMPT_ = /請聽打這段影片\s*\d{1,2}:\d{2}:\d{2}\s*到\s*\d{1,2}:\d{2}:\d{2}\s*的完整逐字稿[。．.]?[ \t]*\n?/g;
var TX_ECHO_VOCAB_ = /可能出現的專有名詞\s*[：:]\s*(?:[^,，、\s。！？\n]{1,16}\s*[,，、]\s*){3,}[^,，、\s。！？\n]{1,16}[。．.]?[ \t]*\n?/g;
function stripTranscribeEcho_(text) {
  return String(text || '').replace(TX_ECHO_PROMPT_, '').replace(TX_ECHO_VOCAB_, '');
}

/** 原文指紋：去掉空白後 SHA-256 的前 16 個十六進位字元，與 pipeline.py 的 transcript_fingerprint 相同。 */
function transcriptFingerprint_(text) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,
    String(text || '').replace(/[\s\x1c-\x1f\x85]/g, ''), Utilities.Charset.UTF_8);
  return bytes.map(function (b) { return ('0' + ((b + 256) % 256).toString(16)).slice(-2); }).join('').slice(0, 16);
}

/** 試算表裡排好的版面。指紋要與目前顯示的原文相同才算數（原文後來改過，舊版面就不能用）。 */
function storedTranscriptLayout_(row, text) {
  var payload = String(row['排版稿JSON'] || '').trim();
  var got = String(row['排版稿指紋'] || '').trim();
  if (!payload || !got) { return null; }
  try {
    var sections = JSON.parse(payload);
    if (!sections || !sections.length) { return null; }
    if (got !== transcriptFingerprint_(text)) { return null; }
    return sections;
  } catch (e) {
    Logger.log('排版稿JSON 解析失敗，改用規則分段：' + e);
    return null;
  }
}

/** 舊版（v29）沒有「排版稿方式」欄：每一段都有小標的是模型排的，有空白小標的是機械分段。 */
function storedLayoutMethod_(sections, method) {
  var m = String(method || '').trim();
  if (m) { return m; }
  return (sections && sections.length && sections.every(function (s) { return String(s.title || '').trim(); })) ? 'ai' : 'rule';
}

/* 中文字旁邊的空白是語音轉文字留下來的（9/17 投稿的原文每個字之間都有空格），
   潤飾稿也常殘留（「我會不 會開直播」）。一邊是中文字就拿掉；英文字之間的空白保留。 */
var TX_CJK_ = '\u3000-\u303f\u3400-\u9fff\uff00-\uffef';
function tidyTranscriptText_(text) {
  return String(text || '').replace(/\r/g, '')
    .replace(new RegExp('([' + TX_CJK_ + '])[ \\t\\u00a0]+(?=[' + TX_CJK_ + '0-9A-Za-z])', 'g'), '$1')
    .replace(new RegExp('([0-9A-Za-z])[ \\t\\u00a0]+(?=[' + TX_CJK_ + '])', 'g'), '$1');
}
function tidySections_(sections) {
  return (sections || []).map(function (s) {
    return { title: String(s.title || ''), paras: (s.paras || []).map(tidyTranscriptText_) };
  });
}

/** 切成句子，每一個字都落在某一句裡；太長的句子（沒有標點的原文）在逗號或每 80 字處切開。與 pipeline 的 _tx_sentences 相同。 */
function transcriptSentences_(text) {
  var out = [];
  tidyTranscriptText_(text).split('\n').forEach(function (line) {
    line = line.trim();
    if (!line) { return; }
    (line.match(/[^。！？!?]+[。！？!?]*[」』”）)]*|[。！？!?]+[」』”）)]*/g) || []).forEach(function (piece) {
      while (piece.length > 100) {
        var cut = Math.max(piece.lastIndexOf('，', 99), piece.lastIndexOf('、', 99));
        cut = cut >= 40 ? cut + 1 : 80;
        out.push(piece.slice(0, cut));
        piece = piece.slice(cut);
      }
      if (piece) { out.push(piece); }
    });
  });
  return out;
}

/** 句子組成段落：約 180～320 字一段，盡量停在句號、問號、驚嘆號。 */
function transcriptParas_(sentences) {
  var out = [], buf = '';
  sentences.forEach(function (s) {
    if (buf && buf.length + s.length > 320) { out.push(buf); buf = ''; }
    buf += s;
    if (buf.length >= 180 && /[。！？!?][」』”）)]*$/.test(buf)) { out.push(buf); buf = ''; }
  });
  if (buf) { out.push(buf); }
  return out;
}

/** 沒有模型可用時的小標：這一段第一句的開頭。 */
function transcriptRuleTitle_(sentences) {
  var head = sentences.slice(0, 2).join('').replace(/[，。！？!?、；;：:「」『』（）()\s]/g, '');
  var trimmed = head.replace(/^(?:好|來|那|啊|哦|對|所以|然後|其實說真的)+/, '') || head;
  return trimmed.length > 10 ? trimmed.slice(0, 10) + '…' : (trimmed || '逐字稿');
}

/** 規則分段：約 1500 字一段，每段用第一句當小標。不呼叫 AI，永遠可用。 */
function transcriptRuleSections_(sentences, size) {
  size = size || 1500;
  var out = [], group = [], n = 0;
  sentences.forEach(function (s) {
    group.push(s); n += s.length;
    // 沒有標點的原始稿找不到句尾，超過 1.3 倍就直接切
    if (n >= size * 1.3 || (n >= size && /[。！？!?][」』”）)]*$/.test(s))) {
      out.push({ title: transcriptRuleTitle_(group), paras: transcriptParas_(group) });
      group = []; n = 0;
    }
  });
  if (group.length) { out.push({ title: transcriptRuleTitle_(group), paras: transcriptParas_(group) }); }
  return out;
}

/** 舊名稱保留給既有呼叫端。 */
function splitTranscriptByRule_(text) {
  return transcriptRuleSections_(transcriptSentences_(text));
}

function displayTranscriptText_(row) {
  var raw=String(row['原始逐字稿內容']||''),polished=String(row['修飾後逐字稿內容']||'');
  // 忽略的符號要涵蓋 pipeline.py _ev_norm 會忽略的全部標點（…、‧、〈〉、〔〕、～、－……）。
  // 少一個，修飾稿只要出現一次刪節號就會被判成「字不一樣」，整天改顯示沒有標點的原文（2026/09/13）。
  function words(s){return s.replace(/[\s，。！？、；：,.!?;:「」『』“”‘’（）()【】\[\]《》<>〈〉〔〕\-—－_＊*…‧﹒•·～~]/g,'');}
  // Old display drafts may have introduced different companies. They cannot be
  // rehabilitated by a second AI formatter; display the original words instead.
  if(raw && words(raw)!==words(polished)){return raw;}
  return polished || raw;
}

var TX_FORMAT_SYSTEM = "你要替一段直播逐字稿分段並下小標。原文已經拆成編號的句子，格式是「[編號] 句子」。\n你只決定兩件事：從第幾句開始是新的一段、這一段的小標。不要輸出原文，不要改寫、摘要或補字。\n\n=== 在哪裡分段 ===\n判斷依據是「講者換了話題」，不是字數到了就切。以下情況換一段：\n1. 換一檔股票或一個族群。從一檔講到另一檔，是最明確的分段點。\n2. 從大盤轉到個股，或從個股回到大盤。\n3. 從講行情轉到講操作或觀念（為什麼要抱、什麼時候賣、怎麼選股）。\n4. 開始回答會員提問，或提問結束回到盤勢。\n5. 題外話（開場寒暄、時事、抱怨、講古、節目宣傳）自成一段。\n6. 時間推進（等一下開盤、收盤前再看）。\n一段通常 8 到 40 句；同一檔講很久時，可以依技術面、基本面、操作建議再切細。\n\n=== 小標 ===\n每一段一個 6 到 12 個字的小標，讓人掃過去就知道這一段在講什麼、要不要細看。\n講到股票就放股票名稱（照原文的寫法），講大盤就寫大盤在做什麼，講觀念就寫是什麼觀念。\n用原文出現過的詞，不下結論、不加原文沒有的評價。\n好的小標：「台積電填息看法」「會員問記憶體」「大盤量縮等升息」「不要用單日漲跌選股」「節目開場閒聊」\n不好的小標：「內容一」「講者說明」「這一段」「其他」（沒有資訊）\n\n=== 輸出 ===\n只輸出 JSON：{\"sections\":[{\"start\":1,\"title\":\"小標\"},{\"start\":14,\"title\":\"小標\"}]}\nstart 是這一段第一句的編號，由小到大排列；第一段的 start 必須是這一批第一句的編號。";   // 由 scripts/sync_quality.py 從 pipeline.py 同步，不要手改

var TX_LAYOUT_CHUNK_ = 4000;
var TX_BAD_TITLE_ = /^(?:這一段|內容[一二三四五六七八九十\d]*|講者說明|其他|段落\d*|第.段)$/;

function transcriptCleanTitle_(title) {
  var t = String(title || '').replace(/[「」『』"'【】\[\]]/g, '').replace(/^[ 　：:，,。]+|[ 　：:，,。]+$/g, '');
  return (!t || TX_BAD_TITLE_.test(t)) ? '' : t.slice(0, 14);
}

/** 模型回的分段位置：只收本批範圍內的編號，由小到大；第一段一律從本批第一句開始。 */
function transcriptBreaks_(reply, first, last) {
  var got = typeof reply === 'string' ? JSON.parse(String(reply).replace(/^```json|^```|```$/gm, '').trim()) : (reply || {});
  var starts = {};
  (got.sections || []).forEach(function (sec) {
    var n = Number(sec.start);
    if (Math.floor(n) === n && n >= first && n <= last && !(n in starts)) { starts[n] = transcriptCleanTitle_(sec.title); }
  });
  var keys = Object.keys(starts).map(Number).sort(function (a, b) { return a - b; });
  if (!keys.length) { throw new Error('模型沒有回可用的分段位置'); }
  if (keys[0] !== first) { starts[first] = starts[keys[0]]; delete starts[keys[0]]; }
  return Object.keys(starts).map(Number).sort(function (a, b) { return a - b; })
    .map(function (k) { return { start: k, title: starts[k] }; });
}

/**
 * 排版：原文切成編號的句子，模型只回「第幾句開始是新的一段」與小標，段落由程式用原句組回去。
 * 模型碰不到原文，所以一個字都不會被改，也不需要還原檢查（2026/09/17 v44）。
 * 回傳 { sections, method }，method 是 ai（全部由模型分段）／mixed（部分）／rule（全部機械分段）。
 */
function buildTranscriptLayout_(text, deadline) {
  var sentences = transcriptSentences_(text);
  var chunks = [], cur = [], n = 0;
  sentences.forEach(function (s, i) {
    if (cur.length && n + s.length > TX_LAYOUT_CHUNK_) { chunks.push({ first: i + 1 - cur.length, group: cur }); cur = []; n = 0; }
    cur.push(s); n += s.length;
  });
  if (cur.length) { chunks.push({ first: sentences.length - cur.length + 1, group: cur }); }

  var sections = [], ai = 0;
  chunks.forEach(function (c, ci) {
    var last = c.first + c.group.length - 1;
    var breaks = null;
    var left = deadline - Date.now();
    if (TX_FORMAT_SYSTEM && left > 45 * 1000) {
      try {
        var prompt = c.group.map(function (s, k) { return '[' + (c.first + k) + '] ' + s; }).join('\n');
        var raw = callGemini_(TX_FORMAT_SYSTEM, prompt,
          { maxOut: 2048, temperature: 0.1, json: true, maxWaitMs: Math.min(CALL_MAX_WAIT_MS, left - 30 * 1000) });
        breaks = transcriptBreaks_(raw, c.first, last);
      } catch (e) {
        Logger.log('逐字稿排版：第 ' + (ci + 1) + ' 批改用規則分段（' + String(e && e.message || e).slice(0, 120) + '）');
      }
    }
    if (!breaks) {
      sections = sections.concat(transcriptRuleSections_(c.group));
      return;
    }
    ai++;
    breaks.forEach(function (b, bi) {
      var end = bi + 1 < breaks.length ? breaks[bi + 1].start : last + 1;
      var part = sentences.slice(b.start - 1, end - 1);
      if (!part.length) { return; }
      var prev = sections[sections.length - 1];
      // 模型切得太碎（不到 150 字）就併進上一段，保留上一段的小標
      if (prev && prev._ai && part.join('').length < 150) {
        prev._sents = prev._sents.concat(part);
        prev.paras = transcriptParas_(prev._sents);
        return;
      }
      sections.push({ title: b.title || transcriptRuleTitle_(part), paras: transcriptParas_(part), _sents: part, _ai: true });
    });
  });
  sections = sections.map(function (s) { return { title: s.title, paras: s.paras }; });
  return { sections: sections.length ? sections : transcriptRuleSections_(sentences),
           method: ai === chunks.length ? 'ai' : (ai ? 'mixed' : 'rule') };
}

/**
 * 排好某一天的逐字稿並存回影片清單（網站顯示的那一列、那一份原文）。
 * opt.force：已經是模型排好的也重排。opt.deadline：這一次最晚做到什麼時候（毫秒）。
 */
function layoutTranscriptDate_(d, opt) {
  opt = opt || {};
  var sh = getSheet_('影片清單');
  var values = sh.getDataRange().getValues();
  if (values.length < 2) { return { ok: false, reason: '影片清單沒有資料' }; }
  var head = values[0].map(function (h) { return String(h).trim(); });
  var objs = values.slice(1).map(function (r, i) {
    var o = { __row: i + 2 };
    head.forEach(function (h, j) { o[h] = r[j]; });
    return o;
  });
  var picked = selectTranscriptRow_(objs, '', d);
  if (!picked) { return { ok: false, reason: d + ' 沒有逐字稿' }; }
  var row = picked.row, text = transcriptDisplayText_(row);
  if (text.length < 200) { return { ok: false, reason: d + ' 的逐字稿不到 200 字' }; }
  var fp = transcriptFingerprint_(text);
  var stored = storedTranscriptLayout_(row, text);
  if (!opt.force && stored && storedLayoutMethod_(stored, row['排版稿方式']) === 'ai') {
    return { ok: true, skipped: true, method: 'ai', sections: stored.length };
  }
  var layout = buildTranscriptLayout_(text, opt.deadline || (Date.now() + 4 * 60 * 1000));
  var payload = JSON.stringify(layout.sections);
  if (payload.length > 45000) { return { ok: false, reason: '版面 ' + payload.length + ' 字元超過儲存格上限' }; }

  ['排版稿JSON', '排版稿指紋', '排版稿方式'].forEach(function (name) {
    if (head.indexOf(name) < 0) {
      head.push(name);
      sh.getRange(1, head.length).setValue(name);
    }
  });
  // 寫入前再確認一次那一列還是同一份原文（排版要一兩分鐘，期間可能有人重新投稿）
  var now = sh.getRange(row.__row, 1, 1, head.length).getValues()[0];
  var nowRow = {};
  head.forEach(function (h, j) { nowRow[h] = now[j]; });
  if (fmtDate_(nowRow['發布日期']) !== d || transcriptFingerprint_(transcriptDisplayText_(nowRow)) !== fp) {
    return { ok: false, reason: d + ' 的原文在排版期間被更新，這一次不寫入，下一棒重排' };
  }
  sh.getRange(row.__row, head.indexOf('排版稿JSON') + 1).setValue(payload);
  sh.getRange(row.__row, head.indexOf('排版稿指紋') + 1).setValue(fp);
  sh.getRange(row.__row, head.indexOf('排版稿方式') + 1).setValue(layout.method);
  CACHE.remove('txDates');
  return { ok: true, method: layout.method, sections: layout.sections.length };
}

/**
 * 自動補排（2026/09/17 v44）。每五分鐘那一棒裡每 15 分鐘做一次，一次排一天。
 *
 * 為什麼需要：先前只有工作流「寫入那一步」會排版。9/17 手動投稿之後那一步沒有排成
 * （中斷、從檢查點續跑、或配額用完都會這樣），逐字稿分頁就一直是一整段「這一段」，沒有任何東西會再去排。
 * 現在補排三種日子（由新到舊，近 21 天）：沒有版面、版面與原文指紋對不上、上次只拿到規則分段。
 * 規則分段的日子每天最多重試 4 次、間隔至少 2 小時，配額用完時不會一直空打。
 */
function ensureTranscriptLayoutJob(opt) {
  opt = opt || {};
  var start = typeof dkExecStart_ === 'function' ? dkExecStart_(Date.now()) : Date.now();
  var deadline = start + 6 * 60 * 1000 - 75 * 1000;
  if (deadline - Date.now() < 90 * 1000) { return { ok: false, reason: '這一次執行剩下的時間不夠排版' }; }

  var props = PropertiesService.getScriptProperties();
  var retry = {};
  try { retry = JSON.parse(props.getProperty('TXLAYOUT_RETRY') || '{}'); } catch (e) { retry = {}; }
  var today = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd');
  if (retry._day !== today) { retry = { _day: today }; }

  var cutoff = Utilities.formatDate(new Date(Date.now() - 21 * 86400000), 'Asia/Taipei', 'yyyy/MM/dd');
  var rows = readSheetObjects_('影片清單'), byDate = {};
  rows.forEach(function (r) { var d = fmtDate_(r['發布日期']); if (d && (opt.all || d >= cutoff)) { (byDate[d] = byDate[d] || []).push(r); } });
  var dates = Object.keys(byDate).sort().reverse();
  for (var i = 0; i < dates.length; i++) {
    var d = dates[i];
    var pick = selectTranscriptRow_(byDate[d], '', d);
    if (!pick) { continue; }
    var text = transcriptDisplayText_(pick.row);
    if (text.length < 200) { continue; }
    var stored = storedTranscriptLayout_(pick.row, text);
    var method = stored ? storedLayoutMethod_(stored, pick.row['排版稿方式']) : '';
    if (method === 'ai') { continue; }
    if (stored) {
      var st = retry[d] || { n: 0, at: 0 };
      if (st.n >= 4 || Date.now() - st.at < 2 * 3600 * 1000) { continue; }
    }
    var result = layoutTranscriptDate_(d, { deadline: deadline });
    if (result.ok && result.method !== 'ai') {
      var s2 = retry[d] || { n: 0, at: 0 };
      retry[d] = { n: s2.n + 1, at: Date.now() };
    }
    props.setProperty('TXLAYOUT_RETRY', JSON.stringify(retry));
    Logger.log('逐字稿自動排版　' + d + '　' + JSON.stringify(result));
    return result.ok ? { ok: true, date: d, method: result.method, sections: result.sections } : result;
  }
  return { ok: true, idle: true };
}

/**
 * 「智慧排版」按鈕：立刻排這一天並存回試算表（不再只放六小時快取），之後誰打開都是排好的。
 * 已經是模型排好的就直接回傳，不重排。
 */
function formatTranscript(dateStr) {
  var d = fmtDate_(dateStr);
  var t = getTranscript(d);
  if (!t.found || t.formatted) { return t; }
  var result = layoutTranscriptDate_(d, { deadline: Date.now() + 4 * 60 * 1000 });
  if (!result.ok) { Logger.log('智慧排版未完成：' + result.reason); }
  return getTranscript(d);
}


/**
 * 診斷：列出影片清單每一天的逐字稿長度。
 * 逐字稿分頁說某天沒有內容、但你確定試算表裡有的時候，用這一支確認實情。
 */
function debugTranscripts() {
  var rows = readSheetObjects_('影片清單');
  Logger.log('影片清單共 ' + rows.length + ' 列');
  Logger.log('欄位名稱：' + Object.keys(rows[0] || {}).join('、'));
  Logger.log('');
  Logger.log('日期　　　　狀態　　原始　修飾後　會不會出現在逐字稿分頁');
  rows.slice(-20).forEach(function (r) {
    var d = fmtDate_(r['發布日期']);
    var v1 = String(r['原始逐字稿內容'] || '').length;
    var v2 = String(r['修飾後逐字稿內容'] || '').length;
    var show = (v2 >= 200 || v1 >= 200) ? '會' : '不會（兩者都不足 200 字）';
    Logger.log(d + '　' + String(r['處理狀態'] || '').slice(0, 4) +
               '　' + v1 + '　' + v2 + '　' + show);
  });
  Logger.log('');
  Logger.log('若某天顯示「會」但網站上看不到，執行 CACHE.remove(\'txDates\') 清快取後重試。');
}

/** 清掉逐字稿相關快取。改過試算表之後想立刻看到新內容時用。 */
function clearTranscriptCache() {
  CACHE.remove('txDates');
  readSheetObjects_('影片清單').forEach(function (r) {
    var d = fmtDate_(r['發布日期']);
    if (d) { CACHE.remove('txfmt_' + d); }
  });
  Logger.log('逐字稿快取已清除');
}
