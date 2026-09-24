/** 前端及 HTTP 公開 API；保留 google.script.run 的既有函式名稱。 */

/** 純 JSON 回應，供 GitHub Actions 這類程式呼叫端判讀 */
function jsonOut_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/**
 * 後台／前台「管理現有訂閱」用：讀出這個信箱目前訂了什麼。
 *
 * 這是依要求做的：直接在介面上查看與勾選，不寄信、不另開頁面。
 * 取捨要寫清楚——少了信箱驗證這一層，任何人輸入別人的 Email 就看得到、
 * 也改得動那個人的訂閱內容。若之後要補回驗證，最小的作法是
 * 送出前先寄一組六位數驗證碼，填對才往下走，不必再回到寄連結那套。
 */
function apiLookupSubscription(email) {
  email = String(email || '').trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return { ok: false, message: '請填寫正確的 Email 格式。' };
  }
  var hit = findSubscription_(email);
  if (!hit) {
    return { ok: false, message: '這個信箱目前沒有生效中的訂閱。要新增請用上面的訂閱表單。' };
  }
  var items = String(hit.row['訂閱項目'] || '');
  return {
    ok: true,
    email: String(hit.row['Email'] || ''),
    daily: items.indexOf('每日總覽') >= 0,
    sms: items.indexOf('會員簡訊') >= 0,
    createdAt: fmtDate_(hit.row['建立時間'])
  };
}

/**
 * 儲存管理頁上的勾選結果。
 *
 * 兩項都沒勾就等於退訂——那是使用者明確的意思，不要留一筆什麼都不寄的
 * 「生效中」訂閱，那種列會一直出現在訂閱人數裡卻永遠不寄東西。
 */
function apiUpdateSubscription(email, payload) {
  return withLock_(function () {
  var hit = findSubscription_(email);
  if (!hit) { return { ok: false, message: '這個信箱目前沒有生效中的訂閱。' }; }
  payload = payload || {};

  var items = [];
  if (payload.daily) { items.push('每日總覽'); }
  if (payload.sms) { items.push('會員簡訊'); }

  if (!items.length) {
    writeSubscriptionFields_(hit, {'狀態':'已取消'});
    return { ok: true, message: '兩項都沒有勾選，已停止接收所有信件。之後可以回網站重新訂閱。' };
  }

  // 關注股票提醒已拿掉：順手清空舊的代號欄，免得日後誰又依它寄信。
  writeSubscriptionFields_(hit, {'訂閱項目':items.join('、'), '關注股票代號':'', '狀態':'生效中'});

  return { ok: true, message: '已儲存：' + items.join('、').replace('會員簡訊', '盤中即時通知') + '。' };
  });
}

/** 管理介面上的「停止接收所有信件」。與信裡那個連結做的是同一件事。 */
function apiStopAllMail(email) {
  return withLock_(function () {
  var hit = findSubscription_(email);
  if (!hit) { return { ok: false, message: '這個信箱目前沒有生效中的訂閱。' }; }
  writeSubscriptionFields_(hit, {'狀態':'已取消'});
  return { ok: true, message: '已停止接收所有信件。之後可以回上面的表單重新訂閱。' };
  });
}

/** 取消訂閱結果頁 */
/* 退訂確認頁（v54，Codex 規格 75–77）。輕量獨立模板：不載入整站樣式與外部字型，手機首屏看得到按鈕。
   回首頁一律用正式網址（publicWebAppUrl_），不再用 getUrl()（排程或編輯器裡會拿到 /dev）。 */
function renderUnsubscribePage_(o) {
  var t = HtmlService.createTemplateFromFile('Unsubscribed');
  t.email = String(o.email || '');
  t.token = String(o.token || '');
  t.type = (o.type === 'daily' || o.type === 'sms') ? o.type : '';
  t.typeLabel = t.type ? UNSUB_SCOPE_LABEL_[t.type] : '';
  t.homeUrl = publicWebAppUrl_();
  t.appTitle = APP_TITLE;
  return t.evaluate()
    .setTitle('取消訂閱　' + APP_TITLE)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** 確認頁的按鈕。scope：'daily'／'sms' 只停一類，'all' 停止所有通知。不需要登入，靠 token 驗證。 */
function apiUnsubscribeConfirm(email, token, scope) {
  try { return unsubscribeByToken(email, token, scope === 'all' ? '' : scope); }
  catch (e) { return { ok: false, state: 'error', message: '暫時無法處理（' + String(e.message || e).slice(0, 80) + '），請稍後再按一次。' }; }
}

/* ------------------------------------------------------------------ *
 * 以下為前端 google.script.run 可呼叫的公開介面
 * 全部只回傳純資料物件，不回傳任何金鑰或內部設定
 * ------------------------------------------------------------------ */

/**
 * 首頁需要的一次性資料：今日總覽。
 *
 * v54 兩處改動（首次載入實測空白 6～10 秒）：
 *   一、拿掉 stats（getPerformanceSummary）。它對操作紀錄裡「每一筆買入」各抓一次日K，
 *       首頁每載入一次就跑一遍，而前端從來沒有讀這個欄位——算完就丟掉。
 *       首頁四格統計讀的是持股追蹤的 summary，與持股追蹤頁同一個來源。函式保留給編輯器用。
 *   二、整包結果快取 90 秒。盤中會員簡訊要能很快出現在首頁，所以不放更久；
 *       多位訪客同時打開時只算一次。重算持股追蹤時會一併清掉。
 */
var DASH_CACHE_KEY_ = 'dash_v54';

function apiGetDashboard() {
  var hit = CACHE.get(DASH_CACHE_KEY_);
  if (hit) { try { return JSON.parse(hit); } catch (e) {} }
  // 唯讀：同一個請求內同一張表只讀一次（v54，Codex 規格 67）。
  var out = {
    today: withSheetSnapshot_(function () { return getTodayOverview(); }),
    updatedAt: Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm')
  };
  try {
    var json = JSON.stringify(out);
    if (json.length < 95000) { CACHE.put(DASH_CACHE_KEY_, json, 90); }
  } catch (e) {}
  return out;
}

/** 依股票代號或名稱查詢（規格書 4.4 節） */
function apiSearchStock(keyword, email) {
  var res = searchStock(keyword);
  if (email) { rememberQuery(email, keyword); }
  return res;
}

/* 使用紀錄。前端批次送上來，這裡只負責寫進試算表。

   刻意不接受 Email：訪客識別是瀏覽器本地產生的隨機碼，
   清掉瀏覽器資料就換一個新的，這邊無從還原成任何真人身分。
   要記的是「大家在看什麼」，不是「誰在看」。 */
function apiLogUsage(who, events) {
  return apiLogUse(who, events);
}

/** 依日期查詢（規格書 4.4 節） */
function apiSearchByDate(dateStr) {
  return searchByDate(dateStr);
}

/**
 * 有紀錄的日期清單（新到舊）。
 * 「翻到某一天」的日曆用它決定哪幾天點得下去，
 * 使用者就不必先點下去才發現那天沒東西。
 */
/** 有逐字稿的日期清單 */
function apiListTranscriptDates() {
  try { return listTranscriptDates(); } catch (e) { return []; }
}

/** 某一天的逐字稿（規則分段，不花額度） */
function apiGetTranscript(dateStr) {
  try { return getTranscript(dateStr); }
  catch (e) { return { found: false, reason: String(e.message || e) }; }
}

/** 某一天的逐字稿（AI 排版，使用者按下才呼叫） */
function apiFormatTranscript(dateStr) {
  try { return formatTranscript(dateStr); }
  catch (e) { return { found: false, reason: String(e.message || e) }; }
}

function apiListRecordDates() {
  try { return listRecordDates(); } catch (e) { return []; }
}

/** 個股即時報價（共用快取，規格書 4.2 節） */
function apiGetQuote(code) {
  return getRealtimeQuote(code);
}

/** 個股歷史 K 線，period: 'hour' | 'day' | 'week' | 'month' */
/** 個股 K 線：日K、週K、60 分 K 一次取回（v41）。 */
function apiGetCandlesBundle(code) {
  return getCandlesBundle(code);
}

function apiGetCandles(code, period) {
  return getCandles(code, period || 'day');
}

/** 個股現爬已移除：證交所逐月現爬太慢會讓前端空轉，改為資料庫沒有就明確告知。 */
function apiFetchStockOnDemand(code) {
  return { ok: false, reason: '已停用現爬' };
}

/** 持股追蹤：每一檔從第一次提到買入至今的持有天數與報酬 */
function apiGetHoldingsTracker() {
  return getHoldingsTracker();
}

/**
 * 個股面板拆成四個獨立呼叫。
 *
 * 先前是一次回傳全部，四件事序列執行，基本面冷快取要抓兩千筆，
 * 整個面板就卡在「讀取中」，連左上角的股票名稱都出不來。
 * 拆開之後每一塊到了就顯示，名稱與報價幾乎立刻出現。
 */

/** 最快的一塊：名稱與報價。名稱來自對照表，就算報價掛掉也一定有名字。 */
function apiGetStockHeader(code) {
  code = String(code || '').trim();
  var m = loadCodeMap_().byCode[code] || {};
  var out = { code: code, name: m.name || '', market: m.market || '', quote: null };
  try { out.quote = getRealtimeQuote(code); } catch (e) { out.quote = null; }
  if (out.quote && out.quote.ok && out.quote.name) { out.name = out.quote.name; }
  if (!out.name) { out.name = code; }
  return out;
}

function apiGetStockTracker(code) {
  try { return getStockTracker(code); } catch (e) { return null; }
}

function apiGetStockRecord(code) {
  try { return searchStock(code); } catch (e) { return null; }
}

/* 技術說明的即時數字（v54，H7／Codex 62）：紀錄的規模，不是投資績效。快取 30 分鐘。
   會員簡訊只讀文章ID那一欄（AGENTS：不整張讀）。 */
function apiGetTechStats() {
  try {
    var hit = CACHE.get('tech_stats_v54');
    if (hit) { try { return JSON.parse(hit); } catch (e) {} }
    return withSheetSnapshot_(function () {
      var trades = readSheetObjects_('操作紀錄'), holds = readSheetObjects_('會員持股');
      var days = {}, codes = {};
      trades.concat(holds).forEach(function (r) {
        var d = fmtDate_(r['日期']); if (d) { days[d] = 1; }
        var c = String(r['代號'] || '').trim(); if (/^\d{4,6}[A-Z]?$/.test(c)) { codes[c] = 1; }
      });
      var dl = Object.keys(days).sort();
      var sms = 0;
      try { var sh = getSheet_('會員簡訊'); sms = Math.max(0, sh.getLastRow() - 1); } catch (e) {}
      var manual = 0; try { manual = readSheetObjects_('人工補登').length; } catch (e) {}
      var mails = 0; try { mails = listMailDates().length; } catch (e) {}
      var out = { ok: true, days: dl.length, records: trades.length + holds.length, codes: Object.keys(codes).length,
                  sms: sms, manual: manual, mails: mails, since: dl[0] || '', asOf: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm') };
      try { CACHE.put('tech_stats_v54', JSON.stringify(out), 1800); } catch (e) {}
      return out;
    });
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

/* 個股面板的摘要（v54，Codex 規格 68）：名稱與報價、持有回合、提到的紀錄三塊合成一個請求，
   同一個請求內共用試算表快照（操作紀錄、會員持股、持股追蹤各只讀一次）。每一塊各自 try，
   一塊壞了其他照常回傳，前端逐塊顯示錯誤與重試。基本面冷快取慢，仍是另一個請求，不拖住首屏。 */
function apiGetStockSummary(code) {
  code = String(code || '').trim();
  return withSheetSnapshot_(function () {
    var out = { code: code, header: null, tracker: null, record: null, errors: {} };
    try { out.header = apiGetStockHeader(code); } catch (e) { out.errors.header = String(e.message || e); }
    try { out.tracker = getStockTracker(code); } catch (e) { out.errors.tracker = String(e.message || e); }
    try { out.record = searchStock(code); } catch (e) { out.errors.record = String(e.message || e); }
    return out;
  });
}

function apiGetStockFundamentals(code) {
  try { return getFundamentals(code); } catch (e) { return null; }
}

/** 小時K 的累積範圍，前端據此說明資料從哪天開始 */
function apiGetHourlyMeta() {
  return getHourlyMeta();
}

/** 為總覽表格補上現價。讀即時快取，不對外請求。 */
function apiGetQuotesFor(codes) {
  try { return getQuotesFor(codes || []); } catch (e) { return {}; }
}

/** 郵件查詢：列出所有已產生郵件內容的日期（新到舊）。 */
function apiListMailDates() {
  try { return listMailDates(); } catch (e) { return []; }
}

/** 郵件查詢：取某一天的郵件完整內容（文章＋結構化對照表，HTML）。 */
function apiGetMailContent(dateStr) {
  try { return getMailContent(dateStr); } catch (e) { return null; }
}

/**
 * 立即刷新網站既有內容（受保護）。
 * 這會重算全站顯示，屬管理操作，必須帶對管理密鑰才會執行。
 * 密鑰存在 Script Properties 的 ADMIN_KEY，只有站方知道。
 * 前端從管理入口呼叫並附上密鑰；一般訪客沒有密鑰，按不動。
 */
function apiRefreshSiteNow(adminKey) {
  var key = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if (!key) {
    return { ok: false, reason: '尚未設定 ADMIN_KEY，請先到 Apps Script 指令碼屬性設定一組管理密鑰。' };
  }
  if (String(adminKey || '') !== key) {
    return { ok: false, reason: '管理密鑰不正確，沒有權限執行刷新。' };
  }
  try {
    var summary = refreshSiteNow();
    return { ok: true, summary: summary };
  } catch (e) {
    return { ok: false, reason: String(e).slice(0, 200) };
  }
}

/** 績效走勢，供折線圖 */
function apiGetPerformanceSeries() {
  return getPerformanceSeries();
}

/** 日K快取涵蓋範圍 */
function apiGetKCacheMeta() {
  return getKCacheMeta();
}

/** 股票代號名稱自動建議 */
function apiSuggestCodes(keyword) {
  return suggestCodes(keyword);
}

/** 建立訂閱 */
/** 會員通知分頁：有簡訊的日期，給日曆用。 */
function apiListSmsDates() {
  return smsDates_();
}

/** 會員通知分頁的資料。date 有給就只回那一天。含原文與解析出來的每一列。 */
function apiGetMemberSms(limit, date) {
  return memberSmsData_(limit, date);
}

function apiSubscribe(payload) {
  return createSubscription(payload);
}

/** 使用者上下文：回傳常查詢股票（規格書 4.6 節） */
function apiGetUserContext(email) {
  return getUserContext(email);
}

/**
 * 站內助手。可協助訂閱、查詢紀錄、說明功能。
 * 後端呼叫 Gemini，前端看不到金鑰。模型只負責理解，寫入由後端驗證後決定。
 */
function apiAsk(question, sessionId, model, userKey) {
  return askAssistant(question, sessionId, model, userKey);
}

/** 驗證使用者貼上的金鑰，回傳這把金鑰能用哪些模型 */
function apiValidateKey(userKey) {
  return validateKey(userKey);
}

/** 寄送取消訂閱連結到指定信箱 */
function apiSendUnsubscribeLink(email) {
  return sendUnsubscribeLink(email);
}

/** 可用的模型選單 */
function apiListModels() {
  return listModels();
}

/** 清空這一段對話的記憶 */
function apiResetSession(sessionId) {
  return resetSession(sessionId);
}

/**
 * 取消刷新。
 *
 * 與全面重整的取消是同一個道理：不去停 GitHub 那個 job，而是插一支旗子，
 * 下一步進來時看到就回「別打了」。已經做完的步驟保留著，之後用
 * 「從這一步重跑」挑一個接著做即可。
 */
function apiAdminCancelRefresh(key) {
  var real = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if (!real || String(key) !== real) { return { ok: false, reason: '管理密鑰不正確' }; }
  var st = chainState_();
  if (!st) { return { ok: false, reason: '目前沒有進行中的刷新。' }; }
  st.cancelled = true;
  st.status = '已取消';
  st.updatedAt = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss');
  PropertiesService.getScriptProperties().setProperty(CHAIN_KEY_, JSON.stringify(st));
  return { ok: true,
           message: '已取消，停在「' + (st.name || st.step) + '」。' +
                    'GitHub 那邊最多再做完手上這一步就會收工。' +
                    '已完成的步驟保留著，用「從這一步重跑」挑一個接著做。' };
}

/** 後台用：刷新鏈跑到哪一步了。 */
function apiAdminChainState(key) {
  var real = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if (!real || String(key) !== real) { return { ok: false, reason: '管理密鑰不正確' }; }
  var raw = PropertiesService.getScriptProperties().getProperty(CHAIN_KEY_);
  var st = null;
  try { st = raw ? JSON.parse(raw) : null; } catch (e) { st = null; }
  var order = (st && st.order) || REFRESH_ORDER_;
  return { ok: true, state: st, order: order,
           names: order.map(function (k) { return REFRESH_STEPS_[k].name; }) };
}

/** 執行已驗證的單一步驟，保留待補資料、失敗與完成狀態。
 * @param {string} adminKey 管理密鑰。
 * @param {string} step 刷新步驟代號。
 * @param {string} dateStr 目標日期。
 * @param {Object} params 附加請求參數。
 * @return {Object} 可續跑的結構化結果。
 */
function apiRefreshStep_(adminKey, step, dateStr, params) {
  var real = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if (!real) { return { ok: false, error: '尚未設定 ADMIN_KEY' }; }
  if (String(adminKey) !== real) { return { ok: false, error: '管理密鑰不正確' }; }

  /* 被按了取消就立刻收工。

     整條鏈是 GitHub 一步一步打進來的，Apps Script 這邊沒有辦法主動叫停對方，
     只能在下一步進來時回一句「別打了」。呼叫端看到 cancelled 就結束整條鏈，
     所以最多再多做一步就會停，不必到 Actions 去按停止。 */
  if (chainCancelled_()) {
    return { ok: true, cancelled: true, done: true, step: step,
             result: '已取消，不再往下做' };
  }

  // 日期只有稽核與複審那一步用得到，其餘步驟收下但不理會。
  // 沒帶就是今天，這也是每日流程的常態。
  var d = dateStr || '';

  if (step === 'all') {
    return runRefreshAllChunk_(adminKey, d, params || {});
  }

  var t = REFRESH_STEPS_[step];
  if (!t) {
    return { ok: false, error: '不認得的步驟：' + step,
             valid: REFRESH_ORDER_.join(', ') };
  }
  // 記下「現在在哪一步」。
  //
  // 刷新是由 GitHub 逐步打進來的，Apps Script 這邊每次只知道自己被要求做哪一步，
  // 但把它寫下來之後，後台就能問出整條鏈跑到哪，而不是只能顯示一個
  // 「GitHub 執行中」——那句話對正在等的人沒有任何資訊。
  markChainStep_(step, t.name, '執行中');

  try {
    var r = t.fn(d, params);
    if (r && typeof r === 'object' && (r.ok === false || r.pending || r.skipped)) {
      var issue = r.error || r.reason || '相依資料尚未備齊';
      markChainStep_(step, t.name, r.pending || r.skipped ? '待補資料' : '失敗', issue);
      return {ok:false,step:step,name:t.name,done:false,pending:!!(r.pending || r.skipped),
              blockedBy:r.blockedBy || '',error:issue};
    }
    // 分批的步驟會回一個物件，裡面帶 done 與進度。
    // 呼叫端看到 done=false 就再打一次，直到 true 為止。
    if (t.chunked && r && typeof r === 'object') {
      var note = r.note || ('本批成功 ' + (r.ok || 0) + ' 檔，進度 ' +
                 (r.processed || 0) + '/' + (r.total || 0) +
                 (r.failed ? '，失敗 ' + r.failed : ''));

      /* 把「第幾批／共幾批」寫回狀態，後台的進度條才看得出它在動。

         補日K有三十幾檔、稽核與複審有一百多天，這一格會亮好幾分鐘，
         先前畫面上只有一句「執行中」——與真的卡住完全分不出來，
         而使用者唯一能做的判斷就是「等了很久，是不是壞了」。 */
      CHAIN_SUB_ = { step: step,
                     sub: { done: Number(r.processed) || 0,
                            total: Number(r.total) || 0,
                            label: r.label || '' },
                     note: note };
      markChainStep_(step, t.name, r.done ? '完成' : '執行中');

      return { ok: true, step: step, name: t.name, chunked: true,
               done: !!r.done, processed: r.processed, total: r.total,
               result: note };
    }
    markChainStep_(step, t.name, '完成');
    return { ok: true, step: step, name: t.name, done: true, result: r };
  } catch (e) {
    markChainStep_(step, t.name, '失敗', String(e && e.message || e).slice(0, 200));
    return { ok: false, step: step, name: t.name,
             error: String(e && e.message || e).slice(0, 300) };
  }
}
