/**
 * 檔案：Line.gs
 * LINE 官方帳號：加好友、訂閱開關、查詢與每日總覽推送。
 * 規劃：docs/0927-review/LINE-訂閱與對話工作流規劃.md　部署：docs/0927-line/部署與驗證.md
 *
 * 路線
 *   LINE → 轉送服務（Cloudflare Worker＋Queue 或既有 Cloud Run，line-webhook/）先驗簽並快速回 200，
 *        → 背景把原始 body 與 x-line-signature 原樣 POST 到 /exec?action=line
 *        → doPost → lineWebhook_ 用頻道密鑰再驗一次（網站網址是公開的，任何人都能 POST），
 *          依 webhookEventId 去重後處理。
 *   Apps Script 的 doPost 讀不到請求標頭（拿不到 x-line-signature），而 LINE 要求兩秒內回應，
 *   所以不能直接把這個網站當 LINE 的 Webhook。
 *
 * 通知（與 Email 分開記帳；Email 已寄不代表 LINE 已送，反之亦然）
 *   每日總覽　lineDailyTick_：交易日、最早寄送時間到 22:00、有影片、文章存在、品質關卡「完成」才建一筆 daily|日期。
 *   盤中通知　只對明確輸入啟用指令、仍為好友的帳號推送；公開卡片與圖文選單不提供此項。
 *   寄送器　　lineDeliverTick_：按訂閱快照展開收件者，逐人 push 並帶 X-Line-Retry-Key；
 *             2xx／409＝LINE 已接受，逾時／5xx 用同一把 key 重試，其他 4xx 記原因不重試，429 月額度用完就停。
 *             「已接受」不等於使用者已看到，後台不寫「已送達」。
 * 金鑰只放指令碼屬性（後台 LINE 分頁寫入，畫面只顯示有沒有設定），不放 HTML、試算表或原始碼。
 */

var LINE_API_ = 'https://api.line.me';
var LINE_API_DATA_ = 'https://api-data.line.me';
var LINE_SUBS_SHEET_ = 'LINE 訂閱清單';
var LINE_EVENTS_SHEET_ = 'LINE 事件帳本';
var LINE_OUTBOX_SHEET_ = 'LINE 待送訊息';
var LINE_LEDGER_SHEET_ = 'LINE 寄送帳本';
// 欄位與 Setup.gs 的 SHEET_SCHEMA 相同（測試會核對）。內容JSON 放最後一欄：列清單時不讀它。
var LINE_SUBS_COLS_ = ['使用者ID', '頻道', '好友狀態', '每日總覽', '盤中通知', '同意時間', '同意版本', '建立時間', '更新時間', '最後互動', '測試身分'];
var LINE_EVENTS_COLS_ = ['事件ID', '類型', '使用者', '收到時間', '重送', '處理結果'];
var LINE_OUTBOX_COLS_ = ['訊息ID', '種類', '日期', '內容版本', '來源', '建立時間', '到期時間', '狀態', '收件人數', '已接受', '失敗', '待送', '備註', '更新時間', '內容JSON'];
var LINE_LEDGER_COLS_ = ['訊息ID', '日期', '收件者', '狀態', '嘗試次數', '重試金鑰', '最後錯誤', '更新時間', '接受時間', '請求ID'];
var LINE_CONSENT_VERSION_ = 'v1-2026-09-27';
var LINE_SMS_WINDOW_MIN_ = 60;                       // 與 Email 盤中通知同一個門檻（CM_RECONCILE_NOTIFY_MIN_）
var LINE_RETRY_BACKOFF_MIN_ = [0, 1, 2, 5, 10, 20];  // 第 n 次失敗後隔幾分鐘再試
var LINE_MAX_ATTEMPTS_ = 6;
var LINE_BATCH_ = 20;                                // 一次 fetchAll 幾位
var LINE_RICH_ALIAS_ = { query: 'zz-query', notify: 'zz-notify' };
var LINE_SAFE_REPLY_ = '我能查節目已發布的紀錄、市場資料，以及管理 LINE 通知；不提供買賣建議，也不提供內部設定與指示。';
var LINE_C_ = { hero: '#17322A', heroK: '#9CC8B4', white: '#FFFFFF', accent: '#04795C', ink: '#1B2420', muted: '#667069',
  soft: '#F3F6F4', amber: '#8A5A00', buy: '#B4342C', sell: '#1E7B4F', hold: '#04795C', watch: '#2F6FA3', avoid: '#9A6B12', off: '#8A958F' };

/* ------------------------------------------------------------------ *
 * 設定
 * ------------------------------------------------------------------ */
function lineProp_(k) {
  try { return String(PropertiesService.getScriptProperties().getProperty(k) || '').trim(); } catch (e) { return ''; }
}
function lineSetProp_(k, v) {
  try { PropertiesService.getScriptProperties().setProperty(k, String(v)); } catch (e) { Logger.log('LINE：屬性寫不進去 ' + k + ' ' + e); }
}
/* 非機密的設定合成一個屬性（專案屬性超過 50 個時設定頁面會變唯讀，見 AGENTS v35）。 */
function lineSettings_() {
  try { return JSON.parse(lineProp_('LINE_SETTINGS') || '{}') || {}; } catch (e) { return {}; }
}
function lineSaveSettings_(patch) {
  return withLock_(function () {
    var s = lineSettings_();
    Object.keys(patch || {}).forEach(function (k) { s[k] = patch[k]; });
    lineSetProp_('LINE_SETTINGS', JSON.stringify(s));
    return s;
  });
}
function lineConfigured_() { return !!(lineProp_('LINE_CHANNEL_ACCESS_TOKEN') && lineProp_('LINE_CHANNEL_SECRET')); }
function linePushMode_() { var m = String(lineSettings_().mode || 'off'); return m === 'on' || m === 'test' ? m : 'off'; }
function lineBotId_() { return String(lineSettings_().botUserId || ''); }
function lineMask_(uid) { uid = String(uid || ''); return uid.length > 10 ? uid.slice(0, 5) + '***' + uid.slice(-4) : (uid ? '***' : ''); }
function lineClip_(s, n) { s = String(s == null ? '' : s); return s.length > n ? s.slice(0, n) : s; }
function lineNow_() { return typeof nowStamp_ === 'function' ? nowStamp_() : Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss'); }

/** 台北時間字串（或試算表轉成的 Date）→ 毫秒。不依賴指令碼時區。 */
function lineParseTaipei_(v) {
  if (v instanceof Date) { return isNaN(v.getTime()) ? 0 : v.getTime(); }
  var m = String(v || '').match(/(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/);
  if (!m) { return 0; }
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), Number(m[4] || 0) - 8, Number(m[5] || 0), Number(m[6] || 0));
}
function lineStampOf_(ms) {
  var d = new Date(ms + 8 * 3600000), p = function (n) { return ('0' + n).slice(-2); };
  return d.getUTCFullYear() + '/' + p(d.getUTCMonth() + 1) + '/' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
}
/** '2026/09/23' → '09/23（三）' */
function lineDateLabel_(d) {
  var m = String(d || '').match(/(\d{4})[\/\-](\d{1,2})[\/\-](\d{1,2})/);
  if (!m) { return String(d || ''); }
  var wd = '日一二三四五六'.charAt(new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]))).getUTCDay());
  return ('0' + m[2]).slice(-2) + '/' + ('0' + m[3]).slice(-2) + '（' + wd + '）';
}
function lineMd_(d) { var m = String(d || '').match(/\d{4}[\/\-](\d{1,2})[\/\-](\d{1,2})/); return m ? ('0' + m[1]).slice(-2) + '/' + ('0' + m[2]).slice(-2) : ''; }
function lineSiteUrl_(tab) {
  var base = (typeof publicSiteUrl_ === 'function' && publicSiteUrl_()) ? publicSiteUrl_() :
             (typeof PUBLIC_SITE_URL_DEFAULT === 'string' && PUBLIC_SITE_URL_DEFAULT ? PUBLIC_SITE_URL_DEFAULT : 'https://lee200202.github.io/Stock/');
  base = (base || 'https://lee200202.github.io/Stock/').replace(/\/?$/, '/');
  return tab ? base + '?tab=' + encodeURIComponent(tab) : base;
}
function lineStockUrl_(code) {
  var base = (typeof publicSiteUrl_ === 'function' && publicSiteUrl_()) ? publicSiteUrl_() :
             (typeof PUBLIC_SITE_URL_DEFAULT === 'string' && PUBLIC_SITE_URL_DEFAULT ? PUBLIC_SITE_URL_DEFAULT : 'https://lee200202.github.io/Stock/');
  base = (base || 'https://lee200202.github.io/Stock/').replace(/\/?$/, '/');
  return /^\d{4,6}[A-Z]?$/.test(String(code)) ? base + '?stock=' + encodeURIComponent(code) : base;
}
function lineAddFriendUrl_() {
  var s = lineSettings_();
  if (/^https:\/\/(?:line\.me|lin\.ee)\//.test(String(s.addFriendUrl || ''))) { return String(s.addFriendUrl); }
  return s.basicId ? 'https://line.me/R/ti/p/' + encodeURIComponent(String(s.basicId)) : '';
}

/* ------------------------------------------------------------------ *
 * LINE API
 * ------------------------------------------------------------------ */
function lineRequest_(method, path, payload, opts) {
  opts = opts || {};
  var p = { url: (opts.data ? LINE_API_DATA_ : LINE_API_) + path, method: method, muteHttpExceptions: true,
            headers: { Authorization: 'Bearer ' + (opts.token || lineProp_('LINE_CHANNEL_ACCESS_TOKEN')) } };
  if (opts.retryKey) { p.headers['X-Line-Retry-Key'] = opts.retryKey; }
  if (opts.bytes) { p.contentType = opts.contentType || 'image/png'; p.payload = opts.bytes; }
  else if (payload != null) { p.contentType = 'application/json; charset=UTF-8'; p.payload = JSON.stringify(payload); }
  return p;
}
function lineParseRes_(res) {
  var code = res.getResponseCode(), text = String(res.getContentText() || ''), json = null, h = {};
  try { json = text ? JSON.parse(text) : {}; } catch (e) { json = null; }
  try { h = res.getAllHeaders() || {}; } catch (e) { h = {}; }
  var hv = function (name) { for (var k in h) { if (String(k).toLowerCase() === name) { return String(h[k]); } } return ''; };
  return { code: code, json: json, text: text.slice(0, 400), requestId: hv('x-line-request-id'), acceptedId: hv('x-line-accepted-request-id') };
}
function lineApi_(method, path, payload, opts) {
  opts = opts || {};
  if (!opts.token && !lineProp_('LINE_CHANNEL_ACCESS_TOKEN')) { return { code: 0, json: null, text: '尚未設定 LINE 存取權杖' }; }
  var p = lineRequest_(method, path, payload, opts), url = p.url;
  delete p.url;
  try { return lineParseRes_(UrlFetchApp.fetch(url, p)); }
  catch (e) { return { code: -1, json: null, text: lineClip_(String(e && e.message || e), 200) }; }
}
function lineErrText_(r) {
  if (!r) { return ''; }
  var j = r.json || {}, msg = j.message ? String(j.message) : String(r.text || '');
  var det = Array.isArray(j.details) ? j.details.map(function (d) { return (d.property ? d.property + '：' : '') + (d.message || ''); }).join('；') : '';
  var s = (msg + (det ? '（' + det + '）' : '')).trim();
  return lineClip_(s || ('HTTP ' + r.code), 200);
}
function lineReply_(replyToken, messages) {
  var r = lineApi_('post', '/v2/bot/message/reply', { replyToken: replyToken, messages: messages.slice(0, 5) });
  if (r.code === 200) { return 'ok'; }
  // 卡片被 LINE 拒收（400）時 reply token 沒有被用掉，改回一則純文字，使用者至少看得到回應。
  if (r.code === 400) {
    var r2 = lineApi_('post', '/v2/bot/message/reply', { replyToken: replyToken, messages: [lineText_('暫時無法顯示這張卡片，請稍後再試，或直接到網站查看。')] });
    return 'HTTP 400 ' + lineErrText_(r) + (r2.code === 200 ? '（已改回純文字）' : '');
  }
  return 'HTTP ' + r.code + ' ' + lineErrText_(r);
}
function lineSendLoading_(uid) {
  if (!uid) { return; }
  try { lineApi_('post', '/v2/bot/chat/loading/start', { chatId: uid, loadingSeconds: 45 }); } catch (e) {}
}

/* ------------------------------------------------------------------ *
 * Webhook：doPost（Code.gs）→ 這裡
 * ------------------------------------------------------------------ */
/** base64(HMAC-SHA256(頻道密鑰, 原始 body))，與 LINE 的 x-line-signature 相同算法。比對時間固定。 */
function lineSignatureOk_(body, sig, secret) {
  if (typeof body !== 'string' || !sig || !secret) { return false; }
  var expect;
  try { expect = Utilities.base64Encode(Utilities.computeHmacSha256Signature(body, String(secret), Utilities.Charset.UTF_8)); }
  catch (e) { return false; }
  sig = String(sig).trim();
  if (expect.length !== sig.length) { return false; }
  var diff = 0;
  for (var i = 0; i < expect.length; i++) { diff |= expect.charCodeAt(i) ^ sig.charCodeAt(i); }
  return diff === 0;
}

function lineWebhook_(e) {
  var raw = e && e.postData ? String(e.postData.contents || '') : '';
  var env = null;
  try { env = JSON.parse(raw); } catch (x) { env = null; }
  if (!env || typeof env.body !== 'string') { return { ok: false, error: 'bad-envelope' }; }
  var secret = lineProp_('LINE_CHANNEL_SECRET');
  if (!secret) { return { ok: false, error: 'not-configured' }; }
  if (!lineSignatureOk_(env.body, env.sig, secret)) { lineStatAdd_('gasSigFail', 1); return { ok: false, error: 'signature' }; }
  var body = null;
  try { body = JSON.parse(env.body); } catch (x) { body = null; }
  if (!body || typeof body !== 'object') { return { ok: false, error: 'bad-body' }; }
  if (body.type === 'relay-stats') { return lineApplyStats_(body); }
  var dest = String(body.destination || ''), s = lineSettings_();
  // 測試頻道的轉送服務指到正式網站（或反過來）：驗簽已經會擋，這裡再擋一次頻道不符。
  if (s.botUserId && dest && dest !== s.botUserId) { return { ok: false, error: 'destination-mismatch' }; }
  if (!s.botUserId && /^U[0-9a-f]{32}$/.test(dest)) { lineSaveSettings_({ botUserId: dest }); }
  lineStatTouch_();
  var events = Array.isArray(body.events) ? body.events : [];
  var results = events.map(function (ev) {
    try { return lineHandleEvent_(ev, dest || s.botUserId || ''); }
    catch (err) {
      Logger.log('LINE 事件處理失敗：' + (err && err.stack || err));
      return { id: String(ev && ev.webhookEventId || ''), error: lineClip_(String(err && err.message || err), 160) };
    }
  });
  // 任一事件處理失敗，就讓 Cloud Tasks 重試同一批。成功的事件已記帳，重送時會略過。
  // 先前一律回 ok:true，Cloud Tasks 會把半途失敗的事件當成完成，使用者就收不到回覆。
  if (results.some(function (r) { return r.error; })) {
    return { ok: false, error: 'event-failed', handled: results.length,
      failed: results.filter(function (r) { return r.error; }).length };
  }
  return { ok: true, handled: results.length, results: results };
}

/* 轉送服務每分鐘最多回報一次它擋下的驗簽失敗數（同一把頻道密鑰簽名，別人偽造不了）。 */
function lineApplyStats_(b) {
  var at = Number(b.at) || 0;
  if (!at || Math.abs(Date.now() - at) > 10 * 60000) { return { ok: false, error: 'stale-stats' }; }
  var n = Math.max(0, Math.min(100000, Number(b.sigFail) || 0));
  lineStatAdd_('cloudSigFail', n, { lastStatsAt: lineNow_(), relayBuild: lineClip_(b.build || '', 40) });
  return { ok: true, stats: true };
}
function lineStats_() {
  var st = {};
  try { st = JSON.parse(lineProp_('LINE_STATS') || '{}') || {}; } catch (e) { st = {}; }
  var today = todayStr_();
  if (st.day !== today) { st = { day: today, gasSigFail: 0, cloudSigFail: 0, lastWebhookAt: st.lastWebhookAt || '', lastStatsAt: st.lastStatsAt || '', relayBuild: st.relayBuild || '' }; }
  return st;
}
function lineStatAdd_(field, n, extra) {
  try {
    withLock_(function () {
      var st = lineStats_();
      st[field] = (Number(st[field]) || 0) + (n || 0);
      Object.keys(extra || {}).forEach(function (k) { st[k] = extra[k]; });
      lineSetProp_('LINE_STATS', JSON.stringify(st));
    });
  } catch (e) { Logger.log('LINE：統計寫不進去 ' + e); }
}
/** 最後一次收到 Webhook 的時間。每分鐘最多寫一次。 */
function lineStatTouch_() {
  try {
    var c = CacheService.getScriptCache();
    if (c.get('line_touch_webhook')) { return; }
    c.put('line_touch_webhook', '1', 60);
    lineStatAdd_('webhooks', 1, { lastWebhookAt: lineNow_() });
  } catch (e) {}
}

/** 先查持久帳本，再短暫佔用事件；失敗時移除佔用，工作佇列才有機會重跑。 */
function lineClaimEvent_(id) {
  if (!id) { return 'new'; }
  return withLock_(function () {
    var c = CacheService.getScriptCache(), k = 'lineev_' + id;
    var cached = c.get(k);
    if (cached === 'done') { return 'duplicate'; }
    if (cached === 'processing') { return 'busy'; }
    var sh = getSheet_(LINE_EVENTS_SHEET_), last = sh.getLastRow();
    if (last > 1 && sh.getRange(2, 1, last - 1, 1).createTextFinder(id).matchEntireCell(true).findAll().length) {
      c.put(k, 'done', 21600);
      return 'duplicate';
    }
    c.put(k, 'processing', 180);
    return 'new';
  });
}
function lineReleaseEvent_(id, done) {
  if (!id) { return; }
  try {
    var c = CacheService.getScriptCache(), k = 'lineev_' + id;
    if (done) { c.put(k, 'done', 21600); } else { c.remove(k); }
  } catch (e) { Logger.log('LINE 事件佔用狀態更新失敗：' + e); }
}

function lineHandleEvent_(ev, channel) {
  ev = ev || {};
  var id = String(ev.webhookEventId || ''), out = { id: id, type: String(ev.type || '') };
  var claim = lineClaimEvent_(id);
  if (claim === 'duplicate') { out.skipped = 'duplicate'; return out; }
  if (claim === 'busy') { out.error = 'event-busy'; return out; }
  try {
  var src = ev.source || {}, uid = src.type === 'user' ? String(src.userId || '') : '';
  var redelivery = !!(ev.deliveryContext && ev.deliveryContext.isRedelivery);
  var msgs = [];
  if (!uid) { out.skipped = 'not-one-to-one'; }
  else if (ev.type === 'follow') { msgs = lineOnFollow_(uid, channel, ev); }
  else if (ev.type === 'unfollow') { lineOnUnfollow_(uid, channel); out.result = 'blocked'; }
  else if (ev.type === 'postback') {
    lineSendLoading_(uid);
    msgs = lineRoute_(uid, channel, lineParseData_(ev.postback && ev.postback.data));
  }
  else if (ev.type === 'message') {
    var m = ev.message || {};
    if (m.type === 'text') {
      lineSendLoading_(uid);
      msgs = lineRoute_(uid, channel, lineParseText_(m.text, uid));
    } else {
      msgs = [lineText_('目前僅支援文字與股票代號查詢，請直接輸入代號（例如 2330）或點選下方選單！', lineQuickMain_())];
    }
  } else { out.skipped = 'ignored'; }
  if (msgs.length && ev.replyToken) {
    out.reply = lineReply_(ev.replyToken, msgs);
    // 暫時性回覆錯誤不能記成已處理；重送會沿用同一事件 ID，LINE 的 replyToken 也只可成功使用一次。
    if (/^HTTP (?:0|5\d\d)\b/.test(out.reply)) { throw new Error('LINE reply 暫時失敗：' + out.reply); }
  }
  out.result = out.result || (out.skipped ? out.skipped : msgs.length ? 'replied' : 'ok');
  lineLogEvent_(id, out.type, uid, redelivery, out.reply && out.reply !== 'ok' ? '回覆失敗：' + out.reply : out.result);
  lineReleaseEvent_(id, true);
  return out;
  } catch (err) {
    lineReleaseEvent_(id, false);
    throw err;
  }
}

function lineLogEvent_(id, type, uid, redelivery, result) {
  getSheet_(LINE_EVENTS_SHEET_).appendRow([id, type, lineMask_(uid), lineNow_(), redelivery ? '是' : '', lineClip_(result, 200)]);
}

/* ------------------------------------------------------------------ *
 * 使用者說了什麼 → 要做哪一件事（只用確定規則；不送模型）
 * ------------------------------------------------------------------ */
function lineParseData_(data) {
  var o = {};
  String(data || '').split('&').forEach(function (kv) {
    var i = kv.indexOf('=');
    if (i > 0) { try { o[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); } catch (e) { o[kv.slice(0, i)] = kv.slice(i + 1); } }
  });
  return o;
}

var LINE_ADVICE_RE_ = /(?:明天|今天|現在|何時|什麼時候)?(?:能不能|可不可以|可以|該不該|要不要|該|能|要)(?:買|賣|進場|出場|加碼|停損|抱)(?:嗎|呢)?|會(?:漲|跌)(?:嗎|到)?|目標價|進場點|買點在哪|推薦(?:哪|什麼)/;

function lineParseText_(raw, uid) {
  var t = String(raw || '')
    .replace(/[！-～]/g, function (c) { var x = String.fromCharCode(c.charCodeAt(0) - 0xFEE0); return /[0-9A-Za-z]/.test(x) ? x : c; })
    .replace(/\s+/g, '').trim();
  if (!t) { return { a: 'outofscope', kind: 'empty', raw: raw }; }
  if (t.length > 120) { return { a: 'toolong' }; }
  // 套話檢查用原文（英文的「ignore previous」要靠空白）與去空白後的文字各看一次
  if (typeof isPromptProbe_ === 'function' && (isPromptProbe_(String(raw || '')) || isPromptProbe_(t))) { return { a: 'probe' }; }
  var m;
  if ((m = t.match(/^(?:管理者)?綁定(\d{6})$/))) { return { a: 'bind', code: m[1] }; }
  if (/^(?:說明|使用說明|幫助|help|功能|選單|怎麼用|\?|？)$/i.test(t)) { return { a: 'help' }; }

  // 特殊隱藏機關：啟用／關閉盤中通知
  if (/^(?:我要|請幫我|幫我)?(?:啟用|開啟|加訂)(?:盤中即時通知|盤中通知)$/.test(t) || /^(?:我要啟用盤中通知|我要開啟盤中通知|啟用盤中通知|開啟盤中通知)$/.test(t)) {
    return { a: 'secretsms', on: true };
  }
  if (/^(?:關閉|停止|取消)(?:盤中即時通知|盤中通知)$/.test(t)) {
    return { a: 'secretsms', on: false };
  }

  // 訂閱操作與諮詢
  if (/^(?:全部停止|停止通知|停止所有通知|取消訂閱|全部取消|退訂)$/.test(t)) { return { a: 'unsub', k: 'all' }; }
  if (/^(?:停止|取消|關閉)(?:每日總覽|每日)$/.test(t)) { return { a: 'unsub', k: 'daily' }; }
  if (/^(?:停止|取消|關閉)(?:盤中即時通知|盤中通知|即時通知|盤中)$/.test(t)) { return { a: 'unsub', k: 'sms' }; }
  if (/^(?:兩種都要|全部訂閱|都要|兩個都要)$/.test(t)) { return { a: 'sub', k: 'daily' }; }
  if (/^(?:開啟|訂閱|加訂)(?:每日總覽|每日)$/.test(t)) { return { a: 'sub', k: 'daily' }; }
  if (/^(?:訂閱|管理訂閱|通知設定|我的訂閱|訂閱狀態|設定)$/.test(t)) { return { a: 'manage' }; }
  if (/(?:如何|怎麼|怎樣|我要)?(?:訂閱|電子報|email|信箱|信件).*(?:訂閱|電子報|email|信箱|信件)|^(?:我要如何訂閱電子報|如何訂閱電子報|怎麼訂閱電子報|訂閱電子報|電子報)$/i.test(t)) {
    return { a: 'subhelp' };
  }

  // 日常問候與感謝
  if (/^(?:哈[囉嘍羅]|嗨|你好|您好|早安|午安|晚安|嗨嗨|hi|hello|hey)$/i.test(t)) { return { a: 'greeting' }; }
  if (/^(?:謝謝|多謝|感謝|感恩|3q|thx|thanks)$/i.test(t)) { return { a: 'thanks' }; }

  // 接續對話確認（例如詢問「您是指華城嗎？」後回覆「是」或「對」）
  if (/^(?:是|是的|對|對的|好|好的|沒錯)$/.test(t)) { return { a: 'confirm_stock' }; }
  if (/^(?:不是|不對|否|不是的|沒有)$/.test(t)) { return { a: 'deny_stock' }; }

  // 業務核心選單
  if (/^(?:今日整理|今天整理|今日總覽|每日總覽|每日整理|今天|今日|今天有影片嗎|今天有直播嗎|今天有沒有影片|今天有沒有直播)$/.test(t)) { return { a: 'today' }; }
  if (/^(?:最新盤中通知|盤中通知|最新通知|會員通知|盤中|最新簡訊|簡訊)$/.test(t)) { return { a: 'private' }; }
  if (/^(?:持股追蹤|持股|目前持股|會員持股)$/.test(t)) { return { a: 'tracker' }; }
  if (/^(?:市場總覽|市場|大盤|加權指數|台指期|行情)$/.test(t)) { return { a: 'market' }; }
  if (/^(?:查詢|查個股|查股票|個股)$/.test(t)) { return { a: 'askstock' }; }
  var sectorAsk=t.match(/(電機(?:機械)?|半導體|鋼鐵|航運|金融|生技|電子|汽車|營建|光電|食品)(?:類股|產業|股)/);
  if(sectorAsk&&/(?:推薦|提過|提到|點名|買賣|買進|賣出|購入|歷史|紀錄|有哪些)/.test(t)){
    return { a: 'sector', sector: sectorAsk[1] };
  }
  if(/^(?:你也?|張震|分析師)?推薦(?:哪|什麼|幾)(?:些)?股票/.test(t)){
    return { a: 'recommendation' };
  }
  if (LINE_ADVICE_RE_.test(t)) { return { a: 'advice', q: lineStockKeyword_(t.replace(LINE_ADVICE_RE_, '')) }; }
  if ((m = t.match(/^(?:查詢?)?(00\d{3,4}[A-Z]?|\d{4,6}[A-Z]?)(?!\d)/))) { return { a: 'stock', c: m[1] }; }

  // 句子中的股票意圖交由官方名稱／代號表解析；不把整句當成公司名。
  if (/(?:查|找|幫我看|我想看|紀錄|買賣|操作|提到|提過|點名|提及|看法|怎麼看|怎麼說|持股|何時|什麼時候|歷年|代號|價位|時間點)/.test(t) && /[㐀-鿿]|\d{4}/.test(t)) {
    return { a: 'stock', q: t };
  }

  var kw = lineStockKeyword_(t);
  // 單獨輸入公司名仍可查，但一般閒聊不能每句都誤當股票再回「找不到」。
  if (kw.length >= 2 && kw.length <= 12 && /[㐀-鿿]/.test(kw)) {
    var knownStock = lineResolveStock_(kw);
    if (knownStock.code || knownStock.choices) { return { a: 'stock', q: kw }; }
    // 檢查是否有同音/近音相似候選
    var sim = lineFindSimilarStocks_(kw);
    if (sim.length) { return { a: 'stock', q: kw }; }
  }

  // 權限判別：計算與數學運算
  if (/^[\d\s+\-*/xX÷=()（）.%]+$/.test(t) || /(?:1\+1|計算|算一下|幾加幾|多少乘多少)/.test(t)) {
    return { a: 'outofscope', kind: 'calc', raw: t };
  }

  // 其餘超出權限之提問（天氣、生活常識、閒聊等）
  return { a: 'outofscope', kind: 'general', raw: t };
}

function lineStripQuestionWords_(text) {
  var s = String(text || '').replace(/[，。？！、；：\s,?!;:_—~～-]+/g, '');
  if (!s) return '';
  var patterns = [
    /^(?:請問|查詢?|我要查詢?|我想查詢?|幫我查詢?|請幫我查詢?|幫我看|我想看|我想知道|請教|查一下|看一下|聽說|張震說|張震講)+/g,
    /(?:張震說|張震講|張震看|張震是怎麼說的|張震是怎麼看的|張震怎麼說|張震怎麼看|張震|分析師|老師)/g,
    /(?:股票代號|個股代號|代號|股票|個股|這檔股票|這檔|這支|這一支)/g,
    /(?:歷年|這幾年|今年|去年|歷史|過去|最近|最新|上次|之前|今天|今日|現在|目前|近期)/g,
    /(?:有否提過|有沒有提過|有提過嗎|有說過嗎|有講過嗎|有提過|有說過|有講過|有否提及|有沒有提及|有提及嗎|有推薦過嗎|有推薦嗎)/g,
    /(?:何時被提到|何時提到|什麼時候提到|什麼時候講過|何時講過|何時提及|何時買|何時賣|講了?什麼|說了?什麼|提到什麼)/g,
    /(?:提過|提到|提及|說過|講過|點名|推薦|介紹)/g,
    /(?:買賣價位|買進價位|賣出價位|進場價位|出場價位|價位時間點|時間點為何|時間點|買賣點|進場點|出場點|目標價|買賣價格|價位|價格)/g,
    /(?:買賣狀況|買賣紀錄|買賣|買進|買入|賣出|操作|持股|續抱|做多|做空)/g,
    /(?:有否|有沒有|是否有|為何|如何|怎樣|什麼時候|何時|落在哪裡|落在哪|在哪裡|在哪|幾塊|多少|能不能|可不可以|可以嗎|該不該|要不要)/g,
    /(?:走勢如何|走勢|行情|看法|怎麼看|怎麼說|說明|紀錄|資料)/g,
    /(?:是在|是|在|位於)+$/g,
    /(?:呢|嗎|？|\?|！|!|。|，|、|；|：)+$/g
  ];
  patterns.forEach(function (re) { s = s.replace(re, ''); });
  return s.trim();
}

function lineExtractStockFromSentence_(text) {
  text = String(text || '').replace(/[＊*\s]/g, '').trim();
  if (!text) return '';
  var codeMatch = text.match(/(?:^|[^\d])(00\d{3,4}[A-Z]?|\d{4,6}[A-Z]?)(?!\d)/i);
  if (codeMatch) return codeMatch[1];
  var stripped = lineStripQuestionWords_(text);
  if (stripped.length >= 2 && stripped.length <= 8) return stripped;

  var clean = text.replace(/[，。？！、；：\s,?!;:_—~～-]+/g, '');
  var bestCandidate = '', bestScore = 0;
  var codeMap = null;
  try { codeMap = loadCodeMap_() || {}; } catch (e) { codeMap = {}; }
  for (var len = 4; len >= 2; len--) {
    for (var pos = 0; pos <= clean.length - len; pos++) {
      var chunk = clean.slice(pos, pos + len);
      if (/^(?:代號|歷年|有否|提過|買賣|價位|時間|為何|如何|怎樣|紀錄|說明|看法|股票|今天|最近|最新|之前|張震|老師)$/.test(chunk)) continue;
      var sim = lineFindSimilarStocks_(chunk, codeMap);
      if (sim && sim.length && sim[0].score > bestScore) {
        bestScore = sim[0].score;
        bestCandidate = chunk;
      }
    }
    if (bestCandidate) break;
  }
  return bestCandidate || stripped || text;
}

/** 「台積電最近講什麼」→「台積電」、「華成何時被提到」→「華成」 */
function lineStockKeyword_(t) {
  var extracted = lineExtractStockFromSentence_(t);
  if (extracted && extracted.length >= 2 && extracted.length <= 12) {
    return extracted;
  }
  return String(t || '')
    .replace(/^(?:請問|查詢?|我要查詢?|我想查詢?|幫我查詢?|請幫我查詢?|幫我看|我想看|我想知道|請教|查一下|看一下|聽說|張震說|張震講)/, '')
    .replace(/(?:最近|最新|上次|之前|今天|近期)?(?:何時被提到|何時提到|什麼時候提到|什麼時候講過|何時講過|何時提及|何時買|何時賣|講了?什麼|說了?什麼|怎麼說|怎麼看|提到什麼|被提到|被提及|的紀錄|紀錄|的說明|說明|的看法|看法|的操作|怎麼操作|如何操作|的走勢|走勢如何|呢|嗎|？|\?|！|!|。)+$/, '')
    .replace(/(?:何時被提到|何時提到|什麼時候提到|什麼時候講過|何時講過|何時提及|何時買|何時賣|最近|最新|上次|之前)$/, '')
    .trim();
}

function lineRoute_(uid, ch, act) {
  act = act || {};
  if (act.a !== 'tab') { lineTouch_(uid, ch); }
  switch (act.a) {
    case 'sub': return lineSubReply_(uid, ch, act.k, true);
    case 'unsub': return lineSubReply_(uid, ch, act.k, false);
    case 'secretsms': return lineSecretSmsReply_(uid, ch, act.on === true || act.on === '1');
    case 'manage': return [lineManageFlex_(lineFindSub_(uid, ch))];
    case 'subhelp': return lineSubHelpReply_();
    case 'today': return lineTodayReply_();
    case 'sms': return lineSmsReply_();
    case 'private': return [lineText_('目前公開訂閱只提供每日總覽。你可以輸入「訂閱每日總覽」。')];
    case 'tracker': return lineTrackerReply_();
    case 'market': return lineMarketReply_();
    case 'help': return [lineHelpFlex_()];
    case 'greeting': return lineGreetingReply_();
    case 'thanks': return lineThanksReply_();
    case 'confirm_stock': return lineConfirmStockReply_(uid);
    case 'deny_stock': return [lineText_('好的，請直接輸入您想查詢的股票名稱或代號（例如「2330」或「聯發科」），我來為您查詢！', lineQuickMain_())];
    case 'askstock': return [lineText_('請輸入股票代號或名稱，例如「2330」或「台積電」。', lineQuickMain_())];
    case 'stock': return lineStockReply_(act.c || act.q || '', uid);
    case 'sector': return lineSectorReply_(act.sector);
    case 'recommendation': return [lineText_('我不會即時推薦股票。可以查已發布的節目紀錄：輸入公司名稱、代號，或「電機類股提過哪些」；我會列出當時日期與分類，供你回看原文。', lineQuickMain_())];
    case 'advice': return lineAdviceReply_(act.q || '');
    case 'bind': return lineBindReply_(uid, ch, act.code);
    case 'probe': return [lineText_(LINE_SAFE_REPLY_, lineQuickMain_())];
    case 'toolong': return [lineText_('訊息太長了。請輸入股票代號或名稱，或點下方的按鈕。', lineQuickMain_())];
    case 'outofscope': return lineOutOfScopeReply_(act.kind, act.raw);
    case 'tab': return [];
    default: return lineOutOfScopeReply_('general', act.raw);
  }
}

/* ------------------------------------------------------------------ *
 * 訂閱清單（每個好友一列；頻道＝LINE 帳號的 bot userId，測試與正式帳號的資料分得開）
 * ------------------------------------------------------------------ */
function lineOn_(v) { return v === true || /^(?:TRUE|是|開啟|1)$/i.test(String(v == null ? '' : v).trim()); }
function lineSubsRead_() {
  var sh = getSheet_(LINE_SUBS_SHEET_), vals = sh.getDataRange().getValues();
  var head = (vals[0] || []).map(function (h) { return String(h).trim(); }), ci = {};
  LINE_SUBS_COLS_.forEach(function (k) { ci[k] = head.indexOf(k); });
  var rows = [];
  for (var i = 1; i < vals.length; i++) {
    var r = vals[i], uid = String(r[ci['使用者ID']] || '').trim();
    if (!uid) { continue; }
    var g = function (k) { return ci[k] >= 0 ? r[ci[k]] : ''; };
    rows.push({ row: i + 1, uid: uid, channel: String(g('頻道') || '').trim(), friend: String(g('好友狀態') || '').trim() || 'follow',
      daily: lineOn_(g('每日總覽')), sms: lineOn_(g('盤中通知')), consentAt: String(g('同意時間') || ''), consentVer: String(g('同意版本') || ''),
      createdAt: String(g('建立時間') || ''), updatedAt: String(g('更新時間') || ''), lastSeen: String(g('最後互動') || ''),
      tester: String(g('測試身分') || '').trim() === '管理者' });
  }
  return { sh: sh, ci: ci, rows: rows };
}
function lineSubRowValues_(s) {
  var by = { '使用者ID': s.uid, '頻道': s.channel, '好友狀態': s.friend, '每日總覽': s.daily ? '開啟' : '關閉', '盤中通知': s.sms ? '開啟' : '關閉',
    '同意時間': s.consentAt || '', '同意版本': s.consentVer || '', '建立時間': s.createdAt || '', '更新時間': s.updatedAt || '',
    '最後互動': s.lastSeen || '', '測試身分': s.tester ? '管理者' : '' };
  return LINE_SUBS_COLS_.map(function (k) { return by[k]; });
}
function lineFindSub_(uid, ch) {
  var rows = lineSubsRead_().rows.filter(function (x) { return x.uid === uid && (!ch || !x.channel || x.channel === ch); });
  return rows[0] || { uid: uid, channel: ch, friend: 'follow', daily: false, sms: false, tester: false };
}
/** 讀改寫整列都在鎖裡：同一人連按兩個按鈕，兩個選項都要留下來（加訂保留既有選項）。 */
function lineUpsertSub_(uid, ch, fn) {
  return withLock_(function () {
    var t = lineSubsRead_();
    var s = t.rows.filter(function (x) { return x.uid === uid && (!ch || !x.channel || x.channel === ch); })[0];
    var created = !s;
    if (!s) { s = { row: 0, uid: uid, channel: ch, friend: 'follow', daily: false, sms: false, consentAt: '', consentVer: '', createdAt: lineNow_(), updatedAt: '', lastSeen: '', tester: false }; }
    if (!s.channel && ch) { s.channel = ch; }
    var before = { daily: s.daily, sms: s.sms, friend: s.friend };
    fn(s, created);
    s.updatedAt = lineNow_();
    var vals = [lineSubRowValues_(s)];
    if (s.row) { t.sh.getRange(s.row, 1, 1, LINE_SUBS_COLS_.length).setValues(vals); }
    else { var at = Math.max(t.sh.getLastRow(), 1) + 1; t.sh.getRange(at, 1, 1, LINE_SUBS_COLS_.length).setValues(vals); s.row = at; }
    return { sub: s, before: before, created: created };
  });
}
/** 查詢也記「最後互動」，但一小時最多寫一次。 */
function lineTouch_(uid, ch) {
  try {
    var c = CacheService.getScriptCache(), k = 'linetouch_' + uid;
    if (c.get(k)) { return; }
    lineUpsertSub_(uid, ch, function (s) { s.lastSeen = lineNow_(); if (s.friend === 'blocked') { s.friend = 'follow'; } });
    c.put(k, '1', 3600);
  } catch (e) { Logger.log('LINE：最後互動寫不進去 ' + e); }
}

function lineOnFollow_(uid, ch, ev) {
  var r = lineUpsertSub_(uid, ch, function (s) { s.friend = 'follow'; s.lastSeen = lineNow_(); });
  try { CacheService.getScriptCache().put('linetouch_' + uid, '1', 3600); } catch (e) {}
  return [lineWelcomeFlex_(r.sub, !r.created && r.sub.daily)];
}
/** 封鎖或刪除好友：停止推送，但保留原本的選項（解除封鎖時照舊）；也不是 Email 退訂。 */
function lineOnUnfollow_(uid, ch) {
  lineUpsertSub_(uid, ch, function (s) { s.friend = 'blocked'; });
}

var LINE_KIND_NAME_ = { daily: '每日總覽', sms: '盤中即時通知' };
var LINE_KIND_WHEN_ = {
  daily: '每日總覽只在交易日有直播、影片判讀並通過品質檢查後送出一則。',
  sms: '盤中即時通知來自會員簡訊，發出後幾分鐘內送出；沒有直播或休市日仍可能收到。'
};

function lineSubReply_(uid, ch, k, on) {
  // 舊圖文選單的 postback 也會進來；公開入口不得重開盤中推送。
  if (on && (k === 'sms' || k === 'both')) { return [lineText_('目前公開訂閱只提供每日總覽。')]; }
  var keys = k === 'all' ? ['daily', 'sms'] : k === 'daily' || (!on && k === 'sms') ? [k] : [];
  if (!keys.length) { return [lineManageFlex_(lineFindSub_(uid, ch))]; }
  var r = lineUpsertSub_(uid, ch, function (s) {
    keys.forEach(function (x) { s[x] = on; });
    if (!on && (k === 'sms' || k === 'all')) { s.sms = false; }
    if (on) { s.consentAt = lineNow_(); s.consentVer = LINE_CONSENT_VERSION_ + (s.sms && /:sms-keyword$/.test(s.consentVer) ? ':sms-keyword' : ''); s.friend = 'follow'; }
    s.lastSeen = lineNow_();
  });
  return [lineSubConfirm_(r.sub, r.before, keys, on)];
}
function lineSubConfirm_(s, before, keys, on) {
  var names = function (list) { return list.map(function (x) { return LINE_KIND_NAME_[x]; }).join('、'); };
  var changed = keys.filter(function (x) { return !!before[x] !== on; });
  var same = keys.filter(function (x) { return !!before[x] === on; });
  var lines = [], quick = [];
  if (on) {
    if (changed.length) { lines.push('已開啟：' + names(changed) + '。'); }
    if (same.length) { lines.push(names(same) + '原本就已開啟。'); }
    keys.forEach(function (x) { lines.push(LINE_KIND_WHEN_[x]); });
    lines.push('輸入「管理訂閱」可以查看或停用；Email 訂閱不受影響。');
    ['daily'].forEach(function (x) { if (!s[x]) { quick.push(linePb_('開啟' + LINE_KIND_NAME_[x], 'a=sub&k=' + x, '開啟' + LINE_KIND_NAME_[x])); } });
    quick.push(linePb_('今日整理', 'a=today', '今日整理'));
  } else {
    if (changed.length) { lines.push('已停止：' + names(changed) + '。'); }
    if (same.length) { lines.push(names(same) + '原本就沒有開啟。'); }
    var still = ['daily'].filter(function (x) { return s[x]; });
    lines.push(still.length ? names(still) + '仍維持開啟。' : '目前沒有開啟任何 LINE 通知。');
    lines.push('Email 訂閱不受影響；之後可以在「管理訂閱」重新開啟。');
    keys.forEach(function (x) { if (x === 'daily' && !s[x]) { quick.push(linePb_('重新開啟' + LINE_KIND_NAME_[x], 'a=sub&k=' + x, '開啟' + LINE_KIND_NAME_[x])); } });
  }
  quick.push(linePb_('管理訂閱', 'a=manage', '管理訂閱'));
  return lineText_(lines.join('\n'), quick);
}

/** 管理者綁定測試身分：後台產生六位數碼（十分鐘有效），在 LINE 輸入「綁定 123456」。 */
function lineBindReply_(uid, ch, code) {
  var ok = withLock_(function () {
    var b = null;
    try { b = JSON.parse(lineProp_('LINE_ADMIN_BIND') || 'null'); } catch (e) { b = null; }
    if (!b || !b.code || b.until < Date.now()) { return false; }
    if (String(code) !== String(b.code)) {
      b.tries = (b.tries || 0) + 1;
      if (b.tries >= 5) { b = { code: '', until: 0 }; }
      lineSetProp_('LINE_ADMIN_BIND', JSON.stringify(b));
      return false;
    }
    lineSetProp_('LINE_ADMIN_BIND', JSON.stringify({ code: '', until: 0 }));
    return true;
  });
  if (!ok) { return [lineText_('綁定碼不正確或已過期。請到後台 LINE 分頁重新產生。')]; }
  lineUpsertSub_(uid, ch, function (s) { s.tester = true; s.lastSeen = lineNow_(); });
  return [lineText_('已綁定為測試帳號。推送設為「只送測試帳號」時，只有綁定的帳號會收到；後台的「送一則測試」也只送到這裡。')];
}

/* ------------------------------------------------------------------ *
 * 查詢：只讀網站已發布的資料；查不到就說查不到，不以模型補寫
 * ------------------------------------------------------------------ */
function lineDailyRow_(d) {
  var row = readSheetObjects_('每日推播內容').filter(function (r) { return fmtDate_(r['日期']) === d; })[0];
  return row ? { article: String(row['文字稿'] || row['內文'] || row['文章'] || ''), sent: String(row['寄送狀態'] || '') } : null;
}
/** 文章「① 盤勢總覽重點整理」章節的前幾點（已驗證的條列，原文照用，不改寫）。 */
function lineArticlePoints_(article, n) {
  var md = typeof normalizeArticleSections_ === 'function' ? normalizeArticleSections_(String(article || '')) : String(article || '');
  var lines = md.split(/\n/), inMacro = false, out = [];
  for (var i = 0; i < lines.length && out.length < n; i++) {
    var l = lines[i].replace(/^[#*\s]+|\*+$/g, '').trim();
    if (/^[①②③④⑤⑥]/.test(l)) { inMacro = /^①\s*盤勢/.test(l); continue; }
    if (!inMacro) { continue; }
    var m = l.match(/^(?:[•・\-]|\d+[.、])\s*(.+)$/);
    if (m && m[1].trim().length >= 6) { out.push(m[1].trim()); }
  }
  return out;
}
function lineDailyInfo_(d, article) {
  var info = { date: d, title: (typeof dailyPreheader_ === 'function' ? dailyPreheader_(article) : '') || '', points: lineArticlePoints_(article, 2),
               counts: { buy: 0, sell: 0, hold: 0, watch: 0 } };
  try {
    var r = searchByDate(d);
    if (r) { info.counts = { buy: (r.buy || []).length, sell: (r.sell || []).length, hold: (r.holdings || []).length, watch: (r.watchAvoid || []).length + (r.watchWatch || []).length }; }
  } catch (e) { Logger.log('LINE：當日筆數讀不到 ' + e); }
  return info;
}

/** 今天的每日整理狀態：休市／沒有直播／已發布／還在判讀。每日推送與「今日整理」查詢共用同一套條件。 */
function lineTodayState_() {
  var today = todayStr_(), closed = whyClosed_(new Date());
  if (closed) { return { state: 'closed', date: today, why: closed }; }
  var noShow = noVideoToday_(today);
  if (noShow === '今日無直播') { return { state: 'noshow', date: today }; }
  if (typeof cmContentSyncPending_ === 'function' && cmContentSyncPending_(today)) {
    return { state: 'pending', date: today, why: '簡訊與逐字稿說明尚在合併' };
  }
  var row = lineDailyRow_(today), g = (typeof gateState_ === 'function' ? gateState_() : null) || {};
  if (row && row.article && !noShow && g.date === today && g.status === '完成') {
    return { state: 'ready', date: today, article: row.article };
  }
  return { state: 'pending', date: today, why: noShow || (!row || !row.article ? '影片與文章還在整理' : '等品質檢查完成') };
}
function lineTodayReply_() {
  var st = lineTodayState_(), mail = lineSiteUrl_('mail'), quick = lineQuickMain_();
  if (st.state === 'ready') { return [lineDailyFlex_(lineDailyInfo_(st.date, st.article), '今日整理')]; }
  if (st.state === 'closed') {
    return [lineText_('今天' + (st.why === '週末不開盤' ? '是週末' : '台股休市') + '，沒有每日整理。', quick)];
  }
  if (st.state === 'noshow') { return [lineText_('今天沒有直播，沒有每日整理。', quick)]; }
  var hhmm = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'HHmm'));
  return [lineText_('還在判讀：今天的影片整理完成、通過品質檢查後才會發布（' + st.why + '）。' +
    (hhmm < 1200 ? '通常在中午過後。' : '') + (mail ? '\n之前的整理可以到網站「郵件查詢」看：' + mail : ''), quick)];
}

function lineSmsReply_() {
  var d = null;
  try { d = memberSmsData_(1); } catch (e) { d = null; }
  var a = d && d.items && d.items[0];
  if (!a) { return [lineText_('目前沒有可核對的會員通知。', lineQuickMain_())]; }
  var msgs = [];
  if (String(a.time).slice(0, 10) !== todayStr_()) { msgs.push(lineText_('今天還沒有會員通知，以下是最近一則（' + lineDateLabel_(a.time) + '）。')); }
  msgs.push(lineSmsFlex_({ id: a.id, time: a.time, text: a.text, url: a.url }, (Number(a.revisions) || 0) > 0));
  return msgs;
}

function lineTrackerReply_() {
  var t = null;
  try { t = getHoldingsTracker(); } catch (e) { t = null; }
  if (!t) { return [lineText_('持股追蹤暫時讀不到，請稍後再試。', lineQuickMain_())]; }
  var held = (t.held || []).slice().sort(function (a, b) {
    var x = String(a.latestReasonDate || a.entryDate || ''), y = String(b.latestReasonDate || b.entryDate || '');
    return x === y ? 0 : (x < y ? 1 : -1);
  });
  if (!held.length) { return [lineText_('網站目前沒有持有中的回合。', lineQuickMain_())]; }
  return [lineTrackerFlex_(held, t)];
}

function lineMarketReply_() {
  var cards = lineMarketCards_();
  if (!cards.length) { return [lineText_('目前沒有可核對的市場資料快取，請稍後再試或到網站查看。', lineQuickMain_())]; }
  return [lineMarketFlex_(cards)];
}
/** 只讀網站既有快取（盤中每五分鐘由排程更新），不為了查詢臨時打行情 API。 */
function lineMarketCards_() {
  var out = [];
  var live = null;
  try { live = JSON.parse(CACHE.get('market_live_index') || 'null'); } catch (e) { live = null; }
  var pay = function (k) { try { var p = marketPayload_('市場總覽快取', k, false); return p && p.data ? p.data : null; } catch (e) { return null; } };
  var taiex = live && Number(live.value) > 0 ? live : pay('taiex');
  var tx = pay('tx');
  [[taiex, '加權指數'], [tx, '台指期']].forEach(function (x) {
    var c = x[0];
    if (!c || !(Number(c.value) > 0)) { return; }
    out.push({ label: String(c.label || x[1]), value: Number(c.value), change: Number(c.change), percent: Number(c.percent),
               time: String(c.time || c.at || ''), source: String(c.source || ''), unit: String(c.unit || '') });
  });
  return out;
}

function lineResolveStock_(q) {
  q = String(q || '').replace(/[*＊\s]/g, '').replace(/[－–]/g, '-');
  if (!q) { return { none: true, q: q }; }
  var map = {};
  try { map = loadCodeMap_() || {}; } catch (e) { map = {}; }
  var byCode = map.byCode || {}, byName = map.byName || {}, table = (typeof PUBLIC_CONFIRMED_NAMES === 'object' && PUBLIC_CONFIRMED_NAMES) || {};
  var nameOf = function (c) { return typeof narrativeName_ === 'function' ? narrativeName_((byCode[c] || {}).name || '') : String((byCode[c] || {}).name || ''); };
  var code = '';
  if (/^\d{4,6}[A-Z]?$/.test(q)) { code = q; }
  else if (table[q]) { code = String(table[q][0]); }
  else if (byName[q]) { code = byName[q]; }
  else if (byName[q + '*']) { code = byName[q + '*']; }
  if (code) { return { code: code, name: nameOf(code) || (table[q] ? narrativeName_(table[q][1]) : '') }; }
  // 自然語句先找明確代號，再用最長正式名稱／已確認別名。不能把「我要查詢…買賣狀況」整句送去模糊比對。
  var embedded = q.match(/(?:^|[^0-9])(00\d{3,4}[A-Z]?|\d{4,6}[A-Z]?)(?!\d)/i);
  if (embedded && byCode[embedded[1]]) {
    code = embedded[1];
    return { code: code, name: nameOf(code) };
  }
  var names = Object.keys(byName).concat(Object.keys(table));
  var mentions = names.map(function (n) {
    var stem = n.replace(/[*＊]+$/, '');
    var normalized = stem.toUpperCase().replace(/[-＿_]/g, '');
    var haystack = q.toUpperCase().replace(/[-＿_]/g, '');
    return stem.length >= 2 && haystack.indexOf(normalized) >= 0 ? { name: n, length: normalized.length,
      code: String(byName[n] || (table[n] && table[n][0]) || '') } : null;
  }).filter(function (x) { return x && x.code && byCode[x.code]; }).sort(function (a, b) { return b.length - a.length; });
  if (mentions.length && (mentions[0].length >= 3 || /查|找|紀錄|買賣|操作|提到|提過|點名|提及|看法|怎麼看|怎麼說|持股|代號|價位|時間/.test(q))) {
    var best = mentions[0];
    return { code: best.code, name: nameOf(best.code) };
  }
  var hits = Object.keys(byName).filter(function (n) { return n.replace(/[*＊]+$/, '').indexOf(q) >= 0; });
  if (hits.length === 1) { return { code: byName[hits[0]], name: nameOf(byName[hits[0]]) }; }
  if (hits.length > 1) {
    return { q: q, more: hits.length > 10, choices: hits.slice(0, 10).map(function (n) { return { code: byName[n], name: nameOf(byName[n]) }; }) };
  }
  // 若包含自然語句問句或贅詞，嘗試抽取核心股票關鍵詞後再比對一次
  var stripped = lineStockKeyword_(q);
  if (stripped && stripped !== q && stripped.length >= 2) {
    var subRes = lineResolveStock_(stripped);
    if (!subRes.none) { return subRes; }
    return { none: true, q: (subRes.q || stripped) };
  }
  return { none: true, q: (stripped && stripped.length >= 2 ? stripped : q) };
}

/**
 * 尋找可能打錯字或同音/近音的股票候選
 * 例如：華成 → 華城（1519）、台績電 → 台積電（2330）
 */
function lineFindSimilarStocks_(q, passedMap) {
  q = String(q || '').replace(/[*＊\s]/g, '').replace(/[－–]/g, '-').trim();
  if (!q || q.length < 2 || q.length > 8) { return []; }
  var map = (passedMap && passedMap.byCode) ? passedMap : {};
  if (!map.byCode) { try { map = loadCodeMap_() || {}; } catch (e) { map = {}; } }
  var byCode = map.byCode || {}, byName = map.byName || {};
  var table = (typeof PUBLIC_CONFIRMED_NAMES === 'object' && PUBLIC_CONFIRMED_NAMES) || {};
  var nameOf = function (c) {
    return typeof narrativeName_ === 'function' ? narrativeName_((byCode[c] || {}).name || '') : String((byCode[c] || {}).name || '');
  };

  var SOUND_MAP = {
    '成': ['城'], '城': ['成'],
    '績': ['積'], '積': ['績'],
    '連': ['聯'], '聯': ['連'],
    '紅': ['鴻'], '弘': ['鴻'], '鴻': ['紅', '弘'],
    '光': ['廣'], '廣': ['光'],
    '技': ['際'], '際': ['技'],
    '偉': ['緯'], '緯': ['偉'],
    '闖': ['創'], '創': ['闖', '億'],
    '揚': ['陽'], '陽': ['揚'],
    '碩': ['享', '祥'], '享': ['碩', '祥'], '祥': ['碩', '享'],
    '銳': ['瑞', '譜'], '瑞': ['銳', '譜'], '譜': ['銳', '瑞'],
    '澤': ['嘉', '家'], '嘉': ['澤', '家', '加', '佳'], '家': ['澤', '嘉'],
    '晶': ['金', '精'], '金': ['晶', '精'], '精': ['晶', '金'],
    '湖': ['服', '福'], '川': ['穿'],
    '立': ['力', '利'], '力': ['立', '利'], '利': ['立', '力'],
    '達': ['答'], '答': ['達'],
    '巨': ['具'], '具': ['巨'],
    '泰': ['態'], '態': ['泰'],
    '宇': ['雨', '語', '玉'], '玉': ['宇', '雨', '裕'], '裕': ['玉', '雨', '宇'],
    '毅': ['意', '益', '億', '易', '義', '一'], '意': ['毅', '億', '益', '易', '義'],
    '源': ['原', '員'], '原': ['源', '員'],
    '柱': ['準', '竹'], '準': ['柱', '準']
  };

  var candidates = {}, candidateList = [];
  var addCandidate = function (code, score) {
    if (!code || !byCode[code] || candidates[code]) { return; }
    var nm = nameOf(code) || (byCode[code] && byCode[code].name) || code;
    // 候選排序只用代號表。逐檔 searchStock 會把兩張完整紀錄表重讀數次，
    // 打錯字時因此可能超過 LINE reply token 的有效時間；確認股票後才讀一次紀錄。
    candidates[code] = true;
    candidateList.push({ code: code, name: nm, score: score });
  };

  // 1. 同音替換比對
  for (var i = 0; i < q.length; i++) {
    var ch = q.charAt(i);
    var alts = SOUND_MAP[ch] || [];
    for (var a = 0; a < alts.length; a++) {
      var variant = q.slice(0, i) + alts[a] + q.slice(i + 1);
      if (byName[variant]) { addCandidate(byName[variant], 100); }
      if (byName[variant + '*']) { addCandidate(byName[variant + '*'], 98); }
      if (table[variant]) { addCandidate(table[variant][0], 105); }
    }
  }

  // 同音替換已找到高分候選時直接回傳，省下對 2000 檔股票逐一迴圈字串比對的時間
  if (candidateList.length && candidateList[0].score >= 100) {
    candidateList.sort(function (a, b) { return b.score - a.score; });
    return candidateList.slice(0, 4);
  }

  // 2. 字元重疊比對
  var allNames = Object.keys(byName);
  for (var j = 0; j < allNames.length; j++) {
    var rawName = allNames[j];
    var normName = rawName.replace(/[*＊]+$/, '').replace(/-KY$/i, '');
    if (normName === q) { continue; }
    if (Math.abs(normName.length - q.length) <= 1) {
      var matches = 0;
      for (var k = 0; k < q.length; k++) {
        if (normName.indexOf(q.charAt(k)) >= 0) { matches++; }
      }
      if (q.length === 2 && matches === 1 && normName.charAt(0) === q.charAt(0)) {
        addCandidate(byName[rawName], 40);
      } else if (q.length >= 3 && matches >= 2) {
        addCandidate(byName[rawName], 60 + matches * 10);
      }
    }
  }

  candidateList.sort(function (a, b) { return b.score - a.score; });
  return candidateList.slice(0, 4);
}

function lineStockRecords_(code) {
  var res = null;
  try { res = searchStock(code); } catch (e) { res = null; }
  return (res && res.trades || []).filter(function (t) { return String(t.code) === String(code); });
}

function lineStockReply_(q, uid) {
  var r = lineResolveStock_(q);
  if (r.choices) {
    return [lineText_('「' + lineClip_(r.q, 12) + '」對到好幾檔' + (r.more ? '（只列前十檔）' : '') + '，請選一檔：',
      r.choices.map(function (c) { return linePb_(c.name + ' ' + c.code, 'a=stock&c=' + c.code, c.name + '（' + c.code + '）'); }))];
  }
  if (r.none) {
    var similar = lineFindSimilarStocks_(r.q);
    if (similar.length) {
      if (similar.length === 1) {
        var best = similar[0];
        if (uid) { try { CacheService.getScriptCache().put('linesuggest_' + uid, best.code, 300); } catch (e) {} }
        return [lineText_('找不到「' + lineClip_(r.q, 12) + '」，請問您是指「' + best.name + '（' + best.code + '）」嗎？', [
          linePb_('查詢 ' + best.name, 'a=stock&c=' + best.code, '查詢 ' + best.name + '（' + best.code + '）'),
          linePb_('查其他股票', 'a=askstock', '查其他股票'),
          linePb_('今日整理', 'a=today', '今日整理')
        ])];
      }
      return [lineText_('找不到「' + lineClip_(r.q, 12) + '」，您是否想查詢下列可能股票？',
        similar.slice(0, 4).map(function (c) {
          return linePb_(c.name + ' ' + c.code, 'a=stock&c=' + c.code, c.name + '（' + c.code + '）');
        }).concat([linePb_('查其他股票', 'a=askstock', '查其他股票')]))];
    }
    return [lineText_('找不到「' + lineClip_(r.q, 12) + '」這檔股票。請輸入代號（例如 2330）或完整名稱。', lineQuickMain_())];
  }
  var list = lineStockRecords_(r.code), label = (r.name || r.code) + '（' + r.code + '）';
  if (!list.length) { return [lineText_(label + '目前沒有可核對的已發布紀錄。', lineQuickMain_())]; }
  return [lineStockFlex_(r, list)];
}

/** 產業問句只統計已發布逐日紀錄；「曾提及」不能被改寫成現在推薦。 */
function lineSectorReply_(sector) {
  var aliases={電機:'05',電機機械:'05',半導體:'24',鋼鐵:'10',航運:'15',金融:'17',生技:'22',電子:'13',汽車:'12',營建:'14',光電:'26',食品:'02'};
  var code=aliases[String(sector||'')];
  if(!code){return [lineText_('請告訴我想查的產業，例如「電機類股提過哪些」。',lineQuickMain_())];}
  var map={};try{map=loadCodeMap_().byCode||{};}catch(e){}
  var list=[];
  [['操作紀錄','方向'],['會員持股','目前立場']].forEach(function(spec){
    try{readSheetObjects_(spec[0]).forEach(function(row){
      var c=String(row['代號']||'').trim(),ind=String((map[c]||{}).industry||'').trim();
      var inSector=ind===code||ind===((typeof INDUSTRY_CODES_==='object'&&INDUSTRY_CODES_[code])||'');
      if(code==='13'&&/^(?:2[4-9]|3[01])$/.test(ind)){inSector=true;}
      if(!inSector){return;}
      list.push({code:c,name:String(row['股票名稱']||(map[c]||{}).name||c),date:fmtDate_(row['日期']),
        direction:spec[0]==='會員持股'?'會員持股':String(row[spec[1]]||'')});
    });}catch(e){Logger.log('LINE 產業紀錄讀取失敗：'+e);}
  });
  list.sort(function(a,b){return a.date===b.date?0:a.date>b.date?-1:1;});
  var seen={},latest=[];
  list.forEach(function(x){if(x.code&&!seen[x.code]){seen[x.code]=1;latest.push(x);}});
  if(!latest.length){return [lineText_('目前的已發布紀錄中，查不到「'+sector+'」產業的可核對個股；這不代表整個市場沒有相關股票。你也可以輸入單一代號查詢。',lineQuickMain_())];}
  var shown=latest.slice(0,8),lines=['已發布紀錄曾提及的'+sector+'個股（按最近提及排序）：'];
  shown.forEach(function(x){lines.push('• '+x.name+'（'+x.code+'）｜'+lineMd_(x.date)+'｜'+lineDirLabel_(x.direction).label);});
  if(latest.length>shown.length){lines.push('另有 '+(latest.length-shown.length)+' 檔；請輸入個股名稱查看完整日期、價位與說明。');}
  lines.push('這是當時的節目分類，不等於目前推薦或持有；產業歸屬以股票對照表為準。');
  return [lineText_(lines.join('\n'),shown.slice(0,3).map(function(x){return linePb_('查'+x.name,'a=stock&c='+x.code,'查'+x.name);} ))];
}

function lineConfirmStockReply_(uid) {
  var code = '';
  if (uid) {
    try {
      code = CacheService.getScriptCache().get('linesuggest_' + uid) || '';
      if (code) { CacheService.getScriptCache().remove('linesuggest_' + uid); }
    } catch (e) { code = ''; }
  }
  if (code) { return lineStockReply_(code, uid); }
  return [lineText_('請問您想查詢哪一檔股票呢？請輸入股票代號（例如 2330）或完整名稱。', lineQuickMain_())];
}

function lineOutOfScopeReply_(kind, raw) {
  if (kind === 'calc') {
    return [lineText_('抱歉，本服務主要專注於張震分析師節目之「每日總覽重點」、「個股歷史提及紀錄」與「通知訂閱」，計算與數學運算不在我的服務範圍內喔。\n\n您可以試試：\n• 輸入股票代號或名稱（例如「2330」或「台積電」）\n• 點選下方「今日整理」查看最新分析\n• 點選「市場總覽」查看加權指數與台指期行情', lineQuickMain_())];
  }
  return [lineText_('抱歉，此問題不在我的工作與服務範圍內喔。我專責提供張震分析師的節目觀點、個股歷史提及紀錄與訂閱服務。\n\n歡迎輸入股票代號（例如「2330」）或點選下方按鈕查詢！', lineQuickMain_())];
}

function lineSecretSmsReply_(uid, ch, on) {
  if (on) {
    lineUpsertSub_(uid, ch, function (s) {
      s.sms = true;
      s.consentAt = lineNow_();
      s.consentVer = LINE_CONSENT_VERSION_ + ':sms-keyword';
      s.friend = 'follow';
      s.lastSeen = lineNow_();
    });
    return [lineText_('已開啟盤中通知。只有來源入庫、內容完成核對且發文未超過 60 分鐘時才會推送；是否送達仍受 LINE 額度與來源狀態影響。輸入「關閉盤中通知」可隨時停止；每日總覽的訂閱狀態不變。', [
      linePb_('今日整理', 'a=today', '今日整理'),
      linePb_('管理訂閱', 'a=manage', '管理訂閱'),
      linePb_('關閉盤中通知', 'a=secretsms&on=0', '關閉盤中通知')
    ])];
  } else {
    lineUpsertSub_(uid, ch, function (s) {
      s.sms = false;
      s.lastSeen = lineNow_();
    });
    return [lineText_('已為您關閉「盤中即時通知」。日後將不再接收盤中即時推送。\n（每日總覽若有開啟不受影響）', [
      linePb_('管理訂閱', 'a=manage', '管理訂閱'),
      linePb_('今日整理', 'a=today', '今日整理')
    ])];
  }
}

function lineGreetingReply_() {
  return [lineText_('您好！我是逐日追蹤機器人。\n\n您可以輸入股票代號或名稱（例如「2330」或「台積電」）查詢分析師提及紀錄，或點選下方按鈕查看最新資訊！', lineQuickMain_())];
}

function lineThanksReply_() {
  return [lineText_('不客氣！祝您投資順利。若需要查詢其他股票或最新盤勢，隨時輸入代號或點選下方選單即可。', lineQuickMain_())];
}

function lineSubHelpReply_() {
  var mail = lineSiteUrl_('mail');
  var lines = [
    '訂閱通知說明：',
    '• LINE 每日總覽：可在本聊天室點選「管理訂閱」開啟，每個交易日直播整理完成後主動推送。',
    '• Email 電子報（每日整理）：請前往官方網站的「訂閱通知」頁面輸入您的 Email 即可完成訂閱。'
  ];
  if (mail) { lines.push('網站網址：' + mail); }
  return [lineText_(lines.join('\n'), [
    linePb_('管理訂閱', 'a=manage', '管理訂閱'),
    linePb_('今日整理', 'a=today', '今日整理'),
    mail ? lineUri_('前往網站', mail) : linePb_('查個股', 'a=askstock', '查個股')
  ].filter(Boolean))];
}
/** 「明天能不能買？」：說明做不到，再附上最近一次提及的紀錄（有的話）。 */
function lineAdviceReply_(q) {
  var head = '我能整理已發布的節目紀錄，不能替你決定買賣。';
  var r = q ? lineResolveStock_(q) : { none: true };
  if (r.code) {
    var list = lineStockRecords_(r.code);
    if (list.length) {
      return [lineText_(head + '最近一次提及' + (r.name || r.code) + '是 ' + lineMd_(list[0].date) + '，當時歸類為「' + lineDirLabel_(list[0].direction).label + '」。'),
              lineStockFlex_(r, list)];
    }
    return [lineText_(head + (r.name || r.code) + '目前沒有可核對的紀錄。', lineQuickMain_())];
  }
  return [lineText_(head + '可以輸入股票代號或名稱，查它最近一次被提到的紀錄。', lineQuickMain_())];
}
function lineDirLabel_(dir) {
  var d = String(dir || '');
  if (/^買/.test(d)) { return { label: '當日買入', color: LINE_C_.buy }; }
  if (/^賣/.test(d)) { return { label: '當日賣出', color: LINE_C_.sell }; }
  if (/觀望不碰|不碰/.test(d)) { return { label: '觀望不碰', color: LINE_C_.avoid }; }
  if (/觀望注意/.test(d)) { return { label: '觀望注意', color: LINE_C_.watch }; }
  if (/會員持股|持有|續抱/.test(d)) { return { label: '會員持有', color: LINE_C_.hold }; }
  return { label: d || '未分類', color: LINE_C_.muted };
}

/* ------------------------------------------------------------------ *
 * 訊息元件（Flex）
 * 版面：頂部深綠標題卡（日期與狀態）→ 主體最多三個重點 → 底部一個主要動作。
 * 每張都有能獨立看懂的 altText；長名稱與長說明一律 wrap，手機窄螢幕不橫向捲動。
 * ------------------------------------------------------------------ */
function lineText_(text, quick) {
  var m = { type: 'text', text: lineClip_(String(text || ' '), 4900) };
  if (quick && quick.length) { m.quickReply = { items: quick.slice(0, 13).map(function (a) { return { type: 'action', action: a }; }) }; }
  return m;
}
function linePb_(label, data, display, extra) {
  var a = { type: 'postback', label: lineClip_(label, 20), data: data };
  if (display) { a.displayText = lineClip_(display, 300); }
  Object.keys(extra || {}).forEach(function (k) { a[k] = extra[k]; });
  return a;
}
function lineUri_(label, uri) { return { type: 'uri', label: lineClip_(label, 20), uri: uri }; }
function lineQuickMain_() {
  return [linePb_('今日整理', 'a=today', '今日整理'), linePb_('查個股', 'a=askstock', '查個股', { inputOption: 'openKeyboard' }),
          linePb_('持股追蹤', 'a=tracker', '持股追蹤'),
          linePb_('市場總覽', 'a=market', '市場總覽'), linePb_('管理訂閱', 'a=manage', '管理訂閱')];
}
function fxText_(text, o) {
  var t = { type: 'text', text: lineClip_(String(text == null || text === '' ? ' ' : text), 1800), wrap: true };
  Object.keys(o || {}).forEach(function (k) { t[k] = o[k]; });
  return t;
}
function fxBox_(layout, contents, o) {
  var b = { type: 'box', layout: layout, contents: (contents || []).filter(Boolean) };
  Object.keys(o || {}).forEach(function (k) { b[k] = o[k]; });
  return b;
}
function fxBtn_(action, style) {
  return { type: 'button', style: style || 'primary', height: 'sm', action: action, color: (style || 'primary') === 'primary' ? LINE_C_.accent : undefined };
}
function fxHeader_(kicker, title, bg) {
  return fxBox_('vertical', [
    fxText_(kicker, { size: 'xs', color: LINE_C_.heroK, weight: 'bold' }),
    title ? fxText_(title, { size: 'md', color: LINE_C_.white, weight: 'bold', margin: 'sm', maxLines: 3 }) : null
  ], { backgroundColor: bg || LINE_C_.hero, paddingAll: '16px' });
}
function fxFooter_(buttons) {
  var bs = (buttons || []).filter(Boolean).map(function (b) { var x = JSON.parse(JSON.stringify(b)); if (!x.color) { delete x.color; } return x; });
  return bs.length ? fxBox_('vertical', bs, { spacing: 'sm', paddingAll: '12px' }) : null;
}
function fxBubble_(header, bodyContents, buttons) {
  var b = { type: 'bubble', size: 'mega', header: header, body: fxBox_('vertical', bodyContents, { spacing: 'md', paddingAll: '16px' }) };
  var f = fxFooter_(buttons);
  if (f) { b.footer = f; }
  return b;
}
function lineFlex_(alt, bubble) { return { type: 'flex', altText: lineClip_(alt, 400), contents: bubble }; }
function fxNote_(text) { return fxText_(text, { size: 'xxs', color: LINE_C_.muted, margin: 'md' }); }
function fxBullet_(text, maxLines) {
  return fxBox_('baseline', [fxText_('•', { size: 'sm', color: LINE_C_.accent, flex: 0 }), fxText_(text, { size: 'sm', color: LINE_C_.ink, margin: 'sm', maxLines: maxLines || 3, flex: 1 })], { spacing: 'none' });
}

function lineWelcomeFlex_(s, back) {
  if (back) {
    var on = ['daily'].filter(function (x) { return s[x]; }).map(function (x) { return LINE_KIND_NAME_[x]; });
    return lineFlex_('歡迎回來，目前開啟：' + on.join('、'), fxBubble_(fxHeader_('歡迎回來', '你之前開啟的 LINE 通知會照舊送出'), [
      fxText_('目前開啟：' + on.join('、') + '。', { size: 'sm', color: LINE_C_.ink }),
      fxNote_('要調整或停止，點下方「管理訂閱」，或輸入「停止通知」。')
    ], [fxBtn_(linePb_('管理訂閱', 'a=manage', '管理訂閱')), fxBtn_(linePb_('今日整理', 'a=today', '今日整理'), 'link')]));
  }
  var item = function (k) {
    return fxBox_('vertical', [fxText_(LINE_KIND_NAME_[k], { size: 'sm', weight: 'bold', color: LINE_C_.ink }), fxText_(LINE_KIND_WHEN_[k], { size: 'xs', color: LINE_C_.muted, margin: 'xs' })],
      { backgroundColor: LINE_C_.soft, cornerRadius: '10px', paddingAll: '12px' });
  };
  return lineFlex_('歡迎使用逐日追蹤。通知尚未開啟，可選擇訂閱每日總覽。', fxBubble_(
    fxHeader_('歡迎', '歡迎使用逐日追蹤'), [
      fxText_('你可以查節目已發布的個股紀錄與市場資料。通知目前都還沒開啟。', { size: 'sm', color: LINE_C_.ink }),
      item('daily'),
      fxNote_('加好友不代表同意接收推送。訂閱後可隨時在聊天室停止。')
    ], [fxBtn_(linePb_('訂閱每日總覽', 'a=sub&k=daily', '訂閱每日總覽')),
        fxBtn_(linePb_('先看今日整理', 'a=today', '今日整理'), 'link')]));
}

function lineLastAccepted_(uid) {
  var out = {};
  try {
    var sh = getSheet_(LINE_LEDGER_SHEET_), last = sh.getLastRow();
    if (last < 2) { return out; }
    var from = Math.max(2, last - 999), vals = sh.getRange(from, 1, last - from + 1, LINE_LEDGER_COLS_.length).getValues();
    for (var i = vals.length - 1; i >= 0; i--) {
      var v = vals[i];
      if (String(v[2]) !== uid || String(v[3]) !== 'accepted') { continue; }
      var k = String(v[0]).indexOf('daily|') === 0 ? 'daily' : 'sms';
      if (!out[k]) { out[k] = v[8] instanceof Date ? lineStampOf_(v[8].getTime()) : String(v[8] || v[7] || ''); }
      if (out.daily && out.sms) { break; }
    }
  } catch (e) {}
  return out;
}
function lineManageFlex_(s) {
  var last = s && s.uid ? lineLastAccepted_(s.uid) : {};
  var kinds = ['daily'];
  if (s && s.sms) { kinds.push('sms'); }
  var rows = kinds.map(function (k) {
    var on = !!(s && s[k]);
    return fxBox_('vertical', [
      fxBox_('horizontal', [fxText_(LINE_KIND_NAME_[k], { size: 'sm', weight: 'bold', color: LINE_C_.ink, flex: 1 }),
        fxText_(on ? '已開啟' : '未開啟', { size: 'xs', weight: 'bold', color: on ? LINE_C_.accent : LINE_C_.off, align: 'end', flex: 0 })]),
      fxText_(LINE_KIND_WHEN_[k], { size: 'xs', color: LINE_C_.muted, margin: 'xs' }),
      last[k] ? fxText_('最近一次 LINE 已接受：' + String(last[k]).slice(5, 16), { size: 'xxs', color: LINE_C_.muted, margin: 'xs' }) : null
    ], { backgroundColor: LINE_C_.soft, cornerRadius: '10px', paddingAll: '12px' });
  });
  var btns = kinds.map(function (k) {
    return s && s[k] ? fxBtn_(linePb_('停止' + LINE_KIND_NAME_[k], 'a=unsub&k=' + k, '停止' + LINE_KIND_NAME_[k]), 'secondary')
                     : fxBtn_(linePb_('開啟' + LINE_KIND_NAME_[k], 'a=sub&k=' + k, '開啟' + LINE_KIND_NAME_[k]));
  });
  if (s && (s.daily || s.sms)) { btns.push(fxBtn_(linePb_('全部停止', 'a=unsub&k=all', '全部停止'), 'link')); }
  var on = kinds.filter(function (k) { return s && s[k]; }).map(function (k) { return LINE_KIND_NAME_[k]; });
  return lineFlex_('LINE 通知設定：' + (on.length ? '已開啟' + on.join('、') : '目前沒有開啟任何通知'), fxBubble_(
    fxHeader_('管理訂閱', 'LINE 通知設定'), rows.concat([fxNote_('這裡只管 LINE 通知；Email 訂閱請到網站「訂閱通知」。')]), btns));
}

function lineHelpFlex_() {
  var rows = [['今日整理', '今天的盤勢摘要與個股數'], ['輸入代號、名稱或一句話', '例如：我要查詢世芯KY買賣狀況'],
              ['持股追蹤', '網站目前持有中的回合'], ['市場總覽', '加權指數與台指期的最新快取'], ['管理訂閱', '開啟或停止 LINE 通知']];
  return lineFlex_('使用說明：可以輸入今日整理、股票代號或查詢句子、持股追蹤、市場總覽、管理訂閱。', fxBubble_(
    fxHeader_('使用說明', '可以這樣問'),
    rows.map(function (r) { return fxBox_('vertical', [fxText_(r[0], { size: 'sm', weight: 'bold', color: LINE_C_.ink }), fxText_(r[1], { size: 'xs', color: LINE_C_.muted })]); })
      .concat([fxNote_('這裡只整理節目已發布的紀錄，不提供買賣建議。電腦版 LINE 沒有下方選單，直接輸入上面的文字即可。')]),
    [fxBtn_(linePb_('今日整理', 'a=today', '今日整理')), fxBtn_(linePb_('管理訂閱', 'a=manage', '管理訂閱'), 'secondary')]));
}

/** 每日總覽卡片（推送與查詢共用）。內容全部來自當次已驗證的文章與紀錄。 */
function lineDailyFlex_(info, kicker) {
  var dl = lineDateLabel_(info.date), title = info.title || (dl + ' 盤勢與操作重點');
  var c = info.counts || {};
  var num = function (n, label, color) {
    return fxBox_('vertical', [fxText_(String(n || 0), { size: 'xl', weight: 'bold', color: color, align: 'center' }),
      fxText_(label, { size: 'xxs', color: LINE_C_.muted, align: 'center' })], { flex: 1 });
  };
  var body = [];
  if ((info.points || []).length) {
    body.push(fxText_('盤勢', { size: 'xs', weight: 'bold', color: LINE_C_.muted }));
    info.points.slice(0, 2).forEach(function (p) { body.push(fxBullet_(p, 3)); });
    body.push({ type: 'separator', margin: 'md' });
  }
  body.push(fxBox_('horizontal', [num(c.buy, '買入', LINE_C_.buy), num(c.sell, '賣出', LINE_C_.sell), num(c.hold, '持有', LINE_C_.hold), num(c.watch, '觀望', LINE_C_.watch)], { margin: 'md' }));
  body.push(fxNote_('整理已驗證的節目內容，不是買賣建議。'));
  var url = lineSiteUrl_('mail');
  return lineFlex_('每日總覽 ' + dl + '｜' + title, fxBubble_(fxHeader_((kicker || '每日總覽') + '｜' + dl, title), body,
    [url ? fxBtn_(lineUri_('查看完整整理', url)) : null, fxBtn_(linePb_('管理通知', 'a=manage', '管理訂閱'), 'link')]));
}

/** 盤中即時通知卡片：原文前兩行照登（保留否定詞與數字），完整內容連回原文與網站。 */
function lineSmsFlex_(a, revised) {
  var time = String(a.time || ''), clock = (time.match(/(\d{1,2}:\d{2})/) || [])[1] || '';
  var lines = String(a.text || '').split(/\n+/).map(function (s) { return s.trim(); }).filter(Boolean);
  var body = lines.slice(0, 2).map(function (l) { return fxText_(lineClip_(l, 500), { size: 'sm', color: LINE_C_.ink }); });
  if (!body.length) { body.push(fxText_('（這則通知沒有文字內容）', { size: 'sm', color: LINE_C_.muted })); }
  if (lines.length > 2) { body.push(fxText_('原文共 ' + lines.length + ' 行，完整內容請看原文。', { size: 'xxs', color: LINE_C_.muted })); }
  body.push(fxNote_('原文照登，未經改寫。股票與價位稍後整理到網站的會員通知頁。'));
  var src = /^https:\/\//.test(String(a.url || '')) ? String(a.url) : '', site = lineSiteUrl_('mail');
  var kicker = (revised ? '內容已修訂' : '盤中即時通知') + (clock ? '｜' + clock : '');
  return lineFlex_((revised ? '會員通知內容已修訂 ' : '盤中即時通知 ') + clock + '｜' + lineClip_(lines[0] || '', 80),
    fxBubble_(fxHeader_(kicker, lineDateLabel_(time) + ' 會員通知', revised ? LINE_C_.amber : LINE_C_.hero), body,
      [src ? fxBtn_(lineUri_('查看原文', src)) : null, site ? fxBtn_(lineUri_('查看後續整理', site), src ? 'secondary' : 'primary') : null]));
}

function lineStockFlex_(r, list) {
  var t = list[0], lab = lineDirLabel_(t.direction), name = r.name || t.name || r.code;
  var px = typeof displayPrice_ === 'function' ? displayPrice_(t.price, '', t.reason) : String(t.price || '');
  var reason = typeof publicNarrative_ === 'function' ? publicNarrative_(t.reason, { name: name, code: r.code }) : String(t.reason || '');
  var body = [
    fxBox_('horizontal', [fxText_('當時分類', { size: 'xs', color: LINE_C_.muted, flex: 0 }),
      fxText_(lab.label, { size: 'sm', weight: 'bold', color: lab.color, margin: 'md', flex: 1 })], { spacing: 'none' }),
    px && px !== '未說明' ? fxBox_('horizontal', [fxText_('價位', { size: 'xs', color: LINE_C_.muted, flex: 0 }),
      fxText_(px, { size: 'sm', weight: 'bold', color: LINE_C_.ink, margin: 'md', flex: 1 })]) : null,
    reason ? fxText_(lineClip_(reason, 300), { size: 'sm', color: LINE_C_.ink, maxLines: 5 }) : null
  ];
  var seen = {}; seen[t.date + t.direction] = 1;
  var earlier = list.slice(1).filter(function (x) { var k = x.date + x.direction; if (seen[k]) { return false; } seen[k] = 1; return true; }).slice(0, 3);
  if (earlier.length) {
    body.push({ type: 'separator', margin: 'md' });
    body.push(fxText_('歷次提及', { size: 'xs', weight: 'bold', color: LINE_C_.muted }));
    [t].concat(earlier).forEach(function (x) {
      var direction = lineDirLabel_(x.direction);
      body.push(fxBox_('horizontal', [
        fxText_('●', { size: 'sm', color: direction.color, flex: 0 }),
        fxText_(lineDateLabel_(x.date), { size: 'xs', color: LINE_C_.muted, margin: 'sm', flex: 0 }),
        fxText_(direction.label, { size: 'xs', weight: 'bold', color: direction.color, margin: 'md', flex: 1 })
      ], { spacing: 'none' }));
    });
  }
  body.push(fxNote_('這是節目當天的紀錄，不代表現在的買賣建議。'));
  var url = lineStockUrl_(r.code);
  return lineFlex_(name + '（' + r.code + '）最近一次提及 ' + lineMd_(t.date) + '：' + lab.label,
    fxBubble_(fxHeader_('最近一次提及｜' + lineDateLabel_(t.date), name + '（' + r.code + '）'), body,
      [url ? fxBtn_(lineUri_('看完整紀錄與圖表', url)) : null, fxBtn_(linePb_('查另一檔', 'a=askstock', '查個股', { inputOption: 'openKeyboard' }), 'link')]));
}

function lineTrackerFlex_(held, t) {
  var show = held.slice(0, 8);
  var body = show.map(function (h) {
    var code = h.valid === false ? '代號待確認' : h.code;
    return fxBox_('vertical', [fxText_((h.name || '') + '（' + code + '）', { size: 'sm', weight: 'bold', color: LINE_C_.ink }),
      fxText_('進場 ' + (lineMd_(h.entryDate || h.roundStart) || '—') + '　最近提及 ' + (lineMd_(h.latestReasonDate) || '—'), { size: 'xs', color: LINE_C_.muted })]);
  });
  if (held.length > show.length) { body.push(fxText_('另有 ' + (held.length - show.length) + ' 檔，完整清單請看網站。', { size: 'xs', color: LINE_C_.muted })); }
  var asOf = (show[0] && show[0].roundsAsOf) || '';
  body.push(fxNote_('只顯示網站已發布的持股回合，不代表你個人的持股。' + (asOf ? '回合資料更新：' + asOf + '。' : '')));
  var url = lineSiteUrl_('tracker');
  return lineFlex_('持股追蹤：目前持有 ' + held.length + ' 檔', fxBubble_(fxHeader_('持股追蹤', '目前持有 ' + held.length + ' 檔'), body,
    [url ? fxBtn_(lineUri_('網站持股追蹤', url)) : null, fxBtn_(linePb_('查個股', 'a=askstock', '查個股', { inputOption: 'openKeyboard' }), 'link')]));
}

function lineNum_(v, dp) {
  var n = Number(v);
  if (!isFinite(n)) { return '—'; }
  var s = Math.abs(n).toFixed(dp), parts = s.split('.');
  parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (n < 0 ? '-' : '') + parts.join('.');
}
function lineMarketFlex_(cards) {
  var nowMs = Date.now(), body = [];
  cards.forEach(function (c) {
    var ms = lineParseTaipei_(c.time), age = ms ? Math.round((nowMs - ms) / 60000) : null;
    var up = c.change > 0, down = c.change < 0;
    var color = up ? LINE_C_.buy : down ? LINE_C_.sell : LINE_C_.muted;
    var chg = isFinite(c.change) ? (up ? '+' : '') + lineNum_(c.change, 2) + (isFinite(c.percent) ? '（' + (c.percent > 0 ? '+' : '') + lineNum_(c.percent, 2) + '%）' : '') : '';
    body.push(fxBox_('vertical', [
      fxBox_('horizontal', [fxText_(c.label, { size: 'sm', weight: 'bold', color: LINE_C_.ink, flex: 1 }),
        fxText_(lineNum_(c.value, 2), { size: 'md', weight: 'bold', color: LINE_C_.ink, align: 'end', flex: 0 })]),
      chg ? fxText_(chg, { size: 'xs', color: color, align: 'end' }) : null,
      fxText_('資料時間 ' + (c.time ? String(c.time).slice(5, 16) : '未標示') + (age !== null && age > 20 ? '（' + (age >= 1440 ? '非今日資料' : '約 ' + age + ' 分鐘前') + '）' : '') +
        (c.source ? '　' + lineClip_(c.source, 30) : ''), { size: 'xxs', color: LINE_C_.muted })
    ], { backgroundColor: LINE_C_.soft, cornerRadius: '10px', paddingAll: '12px' }));
  });
  body.push(fxNote_('來自網站最近一次快取，可能延遲；盤中每五分鐘更新一次，不是即時成交。'));
  var url = lineSiteUrl_('market');
  return lineFlex_('市場總覽：' + cards.map(function (c) { return c.label + ' ' + lineNum_(c.value, 2); }).join('、'),
    fxBubble_(fxHeader_('市場總覽', '網站最新快取'), body, [url ? fxBtn_(lineUri_('網站市場總覽', url)) : null]));
}

/* ------------------------------------------------------------------ *
 * 待送訊息與寄送帳本
 * ------------------------------------------------------------------ */
function lineOutboxRead_() {
  var sh = getSheet_(LINE_OUTBOX_SHEET_), last = sh.getLastRow(), n = LINE_OUTBOX_COLS_.length - 1;   // 不讀內容JSON
  var head = sh.getRange(1, 1, 1, LINE_OUTBOX_COLS_.length).getValues()[0].map(function (h) { return String(h).trim(); });
  var ci = {}; LINE_OUTBOX_COLS_.forEach(function (k) { ci[k] = head.indexOf(k); });
  var rows = [];
  if (last >= 2) {
    sh.getRange(2, 1, last - 1, n).getValues().forEach(function (v, i) {
      var id = String(v[ci['訊息ID']] || '').trim();
      if (!id) { return; }
      rows.push({ row: i + 2, id: id, kind: String(v[ci['種類']] || ''), date: fmtDate_(v[ci['日期']]), version: String(v[ci['內容版本']] || ''),
        createdMs: lineParseTaipei_(v[ci['建立時間']]), expiresMs: lineParseTaipei_(v[ci['到期時間']]), state: String(v[ci['狀態']] || ''),
        total: Number(v[ci['收件人數']]) || 0, accepted: Number(v[ci['已接受']]) || 0, failed: Number(v[ci['失敗']]) || 0,
        open: Number(v[ci['待送']]) || 0, note: String(v[ci['備註']] || ''), updated: v[ci['更新時間']], created: v[ci['建立時間']] });
    });
  }
  return { sh: sh, ci: ci, rows: rows };
}
/** 建一筆待送。同一個訊息ID只建一次（同一則簡訊重新解析、五分鐘排程重疊都不會多建）。 */
function lineQueue_(m) {
  return withLock_(function () {
    var t = lineOutboxRead_();
    if (t.rows.some(function (r) { return r.id === m.id; })) { return { created: false, id: m.id }; }
    var by = { '訊息ID': m.id, '種類': m.kind, '日期': m.date, '內容版本': m.version || '', '來源': m.source || '', '建立時間': lineNow_(),
      '到期時間': m.expiresAt ? lineStampOf_(m.expiresAt) : '', '狀態': m.state || 'queued', '收件人數': '', '已接受': 0, '失敗': 0, '待送': '',
      '備註': m.note || '', '更新時間': lineNow_(), '內容JSON': JSON.stringify(m.messages || []) };
    var at = Math.max(t.sh.getLastRow(), 1) + 1;
    t.sh.getRange(at, 1, 1, LINE_OUTBOX_COLS_.length).setValues([LINE_OUTBOX_COLS_.map(function (k) { return by[k]; })]);
    if ((m.state || 'queued') === 'queued') { lineSetProp_('LINE_OUTBOX_OPEN', '1'); }
    return { created: true, id: m.id };
  });
}
function lineOutboxSet_(t, msg, patch) {
  Object.keys(patch).forEach(function (k) { msg[k] = patch[k]; });
  var vals = [[msg.state, msg.total, msg.accepted, msg.failed, msg.open, lineClip_(msg.note || '', 200), lineNow_()]];
  t.sh.getRange(msg.row, t.ci['狀態'] + 1, 1, 7).setValues(vals);   // 狀態～更新時間 七欄相鄰
}
function lineOutboxPayload_(t, msg) {
  try { return JSON.parse(String(t.sh.getRange(msg.row, t.ci['內容JSON'] + 1).getValue() || '[]')); } catch (e) { return []; }
}

/** 某一則的帳本。展開時一次附加一整段，所以同一則的列是連在一起的；用 TextFinder 找出那一段，不整張讀。 */
function lineLedgerFor_(id) {
  var sh = getSheet_(LINE_LEDGER_SHEET_), last = sh.getLastRow(), out = { sh: sh, id: id, lo: 0, vals: [], rows: [] };
  if (last < 2) { return out; }
  var hits = sh.getRange(2, 1, last - 1, 1).createTextFinder(id).matchEntireCell(true).findAll();
  if (!hits.length) { return out; }
  var nums = hits.map(function (h) { return h.getRow(); }), lo = Math.min.apply(null, nums), hi = Math.max.apply(null, nums);
  out.lo = lo;
  out.vals = sh.getRange(lo, 1, hi - lo + 1, LINE_LEDGER_COLS_.length).getValues();
  out.vals.forEach(function (v, i) {
    if (String(v[0]) !== id) { return; }
    out.rows.push({ i: i, uid: String(v[2]), state: String(v[3] || 'pending'), attempt: Number(v[4]) || 0, key: String(v[5] || ''),
      err: String(v[6] || ''), updatedMs: lineParseTaipei_(v[7]) });
  });
  return out;
}
function lineLedgerSet_(led, x, patch) {
  Object.keys(patch).forEach(function (k) { x[k] = patch[k]; });
  var v = led.vals[x.i];
  v[3] = x.state; v[4] = x.attempt; v[6] = lineClip_(x.err || '', 200); v[7] = lineNow_();
  if (patch.acceptedAt) { v[8] = patch.acceptedAt; }
  if (patch.requestId) { v[9] = patch.requestId; }
}
function lineLedgerFlush_(led) {
  if (led.lo && led.vals.length) { led.sh.getRange(led.lo, 1, led.vals.length, LINE_LEDGER_COLS_.length).setValues(led.vals); }
}
function lineLedgerCreate_(msg, subs) {
  return withLock_(function () {
    var sh = getSheet_(LINE_LEDGER_SHEET_), at = Math.max(sh.getLastRow(), 1) + 1, now = lineNow_();
    var vals = subs.map(function (s) { return [msg.id, msg.date, s.uid, 'pending', 0, Utilities.getUuid(), '', now, '', '']; });
    sh.getRange(at, 1, vals.length, LINE_LEDGER_COLS_.length).setValues(vals);
    return lineLedgerFor_(msg.id);
  });
}

/** 收件人快照：好友、這個頻道、這一項開啟；測試模式只送綁定的測試帳號。 */
function lineRecipients_(kind, mode) {
  var bot = lineBotId_();
  // 測試與正式頻道共用一份試算表時，尚未確認 botUserId 絕不可跨頻道發送。
  if (!bot) { return []; }
  return lineSubsRead_().rows.filter(function (s) {
    return s.friend !== 'blocked' && s.channel === bot && (kind === 'daily' ? s.daily : s.sms && /:sms-keyword$/.test(s.consentVer)) && (mode !== 'test' || s.tester);
  });
}

function lineQuota_(force) {
  var c = CacheService.getScriptCache();
  if (!force) { try { var hit = JSON.parse(c.get('line_quota_v1') || 'null'); if (hit) { return hit; } } catch (e) {} }
  var q = lineApi_('get', '/v2/bot/message/quota'), u = lineApi_('get', '/v2/bot/message/quota/consumption');
  var out = { ok: q.code === 200 && u.code === 200, type: q.json && q.json.type || '', limit: q.json && q.json.value != null ? Number(q.json.value) : null,
              used: u.json && u.json.totalUsage != null ? Number(u.json.totalUsage) : null, at: lineNow_() };
  out.remaining = out.type === 'limited' && out.limit != null && out.used != null ? Math.max(0, out.limit - out.used) : null;
  if (!out.ok) { out.error = lineErrText_(q.code !== 200 ? q : u); }
  try { c.put('line_quota_v1', JSON.stringify(out), 300); } catch (e) {}
  return out;
}

/** LINE 的回應 → 帳本狀態。依官方重試規則：2xx、409 不重試；逾時與 5xx 用同一把 key 重試；其他 4xx 不重試。 */
function lineClassify_(res, attempt) {
  var code = res.code, msg = lineErrText_(res);
  if (code >= 200 && code < 300) { return { state: 'accepted', err: '', requestId: res.requestId }; }
  if (code === 409) { return { state: 'accepted', err: '先前已接受（409）', requestId: res.acceptedId || res.requestId }; }
  if (code === 429) { return { state: 'retryable', err: msg, stop: /monthly limit/i.test(msg) ? 'quota' : 'rate', noCount: true }; }
  if (code === 401) { return { state: 'retryable', err: '存取權杖無效或過期：' + msg, stop: 'auth', noCount: true }; }
  if (code <= 0 || code >= 500) {
    return attempt >= LINE_MAX_ATTEMPTS_ ? { state: 'failed', err: '重試 ' + attempt + ' 次仍失敗：' + msg } : { state: 'retryable', err: msg };
  }
  return { state: 'failed', err: 'HTTP ' + code + ' ' + msg };
}
function lineRetryDue_(x, now) {
  var wait = LINE_RETRY_BACKOFF_MIN_[Math.min(x.attempt, LINE_RETRY_BACKOFF_MIN_.length - 1)] * 60000;
  return !x.updatedMs || now - x.updatedMs >= wait;
}

function lineLease_(ms) {
  var p = PropertiesService.getScriptProperties(), token = Utilities.getUuid();
  var got = withLock_(function () {
    var cur = null;
    try { cur = JSON.parse(p.getProperty('LINE_SEND_LEASE') || 'null'); } catch (e) { cur = null; }
    if (cur && cur.until > Date.now()) { return false; }
    p.setProperty('LINE_SEND_LEASE', JSON.stringify({ token: token, until: Date.now() + ms }));
    return true;
  });
  return got ? token : '';
}
function lineLeaseRelease_(token) {
  try {
    withLock_(function () {
      var p = PropertiesService.getScriptProperties(), cur = null;
      try { cur = JSON.parse(p.getProperty('LINE_SEND_LEASE') || 'null'); } catch (e) { cur = null; }
      if (cur && cur.token === token) { p.deleteProperty('LINE_SEND_LEASE'); }
    });
  } catch (e) {}
}

/**
 * 寄送器。五分鐘總排程（寄信那一段）與新簡訊進來時各叫一次；同一時間只有一支在寄（租約）。
 * opts：{ only: 訊息ID, budgetMs, force }
 */
function lineDeliverTick_(opts) {
  opts = opts || {};
  var out = { messages: 0, accepted: 0, failed: 0, retry: 0, stoppedBy: '' };
  if (!lineConfigured_()) { out.stoppedBy = 'not-configured'; return out; }
  var mode = linePushMode_();
  if (mode === 'off') { out.stoppedBy = 'off'; return out; }
  if (!opts.force && lineProp_('LINE_OUTBOX_OPEN') !== '1') { return out; }   // 沒有待送就不讀表，一棒不到一秒
  var lease = lineLease_(330000);
  if (!lease) { out.stoppedBy = 'busy'; return out; }
  var start = Date.now(), budget = opts.budgetMs || 90000, ctx = { start: start, budget: budget, quota: null };
  try {
    var t = lineOutboxRead_();
    var open = t.rows.filter(function (r) { return (r.state === 'queued' || r.state === 'sending') && (!opts.only || r.id === opts.only); })
      .sort(function (a, b) { return a.createdMs - b.createdMs; });
    for (var i = 0; i < open.length; i++) {
      if (Date.now() - start > budget) { out.stoppedBy = 'time'; break; }
      var r = lineDeliverOne_(t, open[i], mode, ctx);
      out.messages++; out.accepted += r.accepted || 0; out.failed += r.failed || 0; out.retry += r.retry || 0;
      if (r.stop) { out.stoppedBy = r.stop; break; }
    }
    var still = lineOutboxRead_().rows.some(function (r) { return r.state === 'queued' || r.state === 'sending'; });
    lineSetProp_('LINE_OUTBOX_OPEN', still ? '1' : '0');
  } finally { lineLeaseRelease_(lease); }
  if (out.stoppedBy === 'quota' || out.stoppedBy === 'auth') { lineAlertOnce_(out.stoppedBy); }
  return out;
}

function lineDeliverOne_(t, msg, mode, ctx) {
  var now = Date.now(), res = { accepted: 0, failed: 0, retry: 0 };
  // 盤中訊息僅寄給以明確文字指令同意的好友；舊訂閱旗標不會自動復活。
  var expired = !!(msg.expiresMs && now > msg.expiresMs);
  var led;
  if (msg.state === 'queued') {
    if (expired) { lineOutboxSet_(t, msg, { state: 'expired', note: msg.kind === 'sms' ? '發文超過 60 分鐘仍未開始，不補送' : '當日未開始，不補送' }); return res; }
    // 若上一棒在「建立逐人帳本」之後被 Apps Script 強制中止，先沿用那份快照。
    // 再建一次會複製收件者與 retry key，造成同一人收到兩則。
    led = lineLedgerFor_(msg.id);
    var subs = led.rows.length ? [] : lineRecipients_(msg.kind, mode);
    if (!led.rows.length && !subs.length) {
      lineOutboxSet_(t, msg, { state: 'done', total: 0, open: 0, note: mode === 'test' ? '測試模式：沒有開啟這項通知的測試帳號' : '沒有開啟這項通知的好友' });
      return res;
    }
    if (!led.rows.length) { led = lineLedgerCreate_(msg, subs); }
    lineOutboxSet_(t, msg, { state: 'sending', total: led.rows.length, open: led.rows.length, note: mode === 'test' ? '測試模式' : '' });
  } else {
    led = lineLedgerFor_(msg.id);
  }
  if (expired) {
    led.rows.forEach(function (x) {
      if (x.state === 'pending' || x.state === 'retryable') { lineLedgerSet_(led, x, { state: 'expired', err: msg.kind === 'sms' ? '發文超過 60 分鐘，不補送' : '當日未送出，不補送' }); }
      else if (x.state === 'sending') { lineLedgerSet_(led, x, { state: 'unknown', err: '送出後沒有收到回應，LINE 可能已接受' }); }
    });
    lineLedgerFlush_(led);
    lineFinishMessage_(t, msg, led, true);
    return res;
  }
  var payload = lineOutboxPayload_(t, msg);
  if (!payload.length) { lineOutboxSet_(t, msg, { state: 'failed', note: '內容JSON 讀不到' }); return res; }
  var due = led.rows.filter(function (x) {
    return x.state === 'pending' || (x.state === 'retryable' && lineRetryDue_(x, now)) || (x.state === 'sending' && now - x.updatedMs > 10 * 60000);
  });
  for (var k = 0; k < due.length && !res.stop; k += LINE_BATCH_) {
    if (Date.now() - ctx.start > ctx.budget) { res.stop = 'time'; break; }
    if (!ctx.quota) { ctx.quota = lineQuota_(false); }
    var chunk = due.slice(k, k + LINE_BATCH_);
    if (ctx.quota.remaining !== null && ctx.quota.remaining !== undefined) {
      if (ctx.quota.remaining <= 0) { res.stop = 'quota'; break; }
      chunk = chunk.slice(0, ctx.quota.remaining);
    }
    chunk.forEach(function (x) { lineLedgerSet_(led, x, { state: 'sending' }); });
    lineLedgerFlush_(led);
    var reqs = chunk.map(function (x) { return lineRequest_('post', '/v2/bot/message/push', { to: x.uid, messages: payload }, { retryKey: x.key }); });
    var resps;
    try { resps = UrlFetchApp.fetchAll(reqs).map(lineParseRes_); }
    catch (e) { resps = chunk.map(function () { return { code: -1, json: null, text: lineClip_(String(e && e.message || e), 200) }; }); }
    chunk.forEach(function (x, j) {
      var c = lineClassify_(resps[j] || { code: -1, text: '沒有回應' }, x.attempt + 1);
      var patch = { state: c.state, err: c.err, attempt: c.noCount ? x.attempt : x.attempt + 1 };
      if (c.state === 'accepted') { patch.acceptedAt = lineNow_(); patch.requestId = c.requestId || ''; res.accepted++; if (ctx.quota.remaining != null) { ctx.quota.remaining--; } }
      else if (c.state === 'failed') { res.failed++; }
      else { res.retry++; }
      if (c.stop && !res.stop) { res.stop = c.stop; }
      lineLedgerSet_(led, x, patch);
    });
    lineLedgerFlush_(led);
  }
  try { if (ctx.quota && ctx.quota.remaining != null) { CacheService.getScriptCache().put('line_quota_v1', JSON.stringify(ctx.quota), 300); } } catch (e) {}
  lineFinishMessage_(t, msg, led, false, res.stop);
  return res;
}

function lineFinishMessage_(t, msg, led, expired, stop) {
  var n = { accepted: 0, failed: 0, expired: 0, unknown: 0, open: 0 };
  led.rows.forEach(function (x) {
    if (x.state === 'accepted') { n.accepted++; } else if (x.state === 'failed') { n.failed++; }
    else if (x.state === 'expired') { n.expired++; } else if (x.state === 'unknown') { n.unknown++; } else { n.open++; }
  });
  var note = [];
  if (n.expired) { note.push((msg.kind === 'sms' ? '逾時未補送 ' : '當日未送出 ') + n.expired); }
  if (n.unknown) { note.push('不明 ' + n.unknown); }
  if (n.failed) { var e = led.rows.filter(function (x) { return x.state === 'failed'; })[0]; note.push('失敗原因：' + lineClip_(e && e.err, 80)); }
  if (stop === 'quota') { note.push('月訊息額度用完，暫停'); }
  if (stop === 'auth') { note.push('存取權杖無效，暫停'); }
  if (n.open && stop === 'rate') { note.push('LINE 限速，下一輪續送'); }
  var state = n.open ? 'sending' : (expired || n.expired ? 'expired' : 'done');
  lineOutboxSet_(t, msg, { state: state, total: led.rows.length, accepted: n.accepted, failed: n.failed + n.expired + n.unknown, open: n.open,
                           note: (msg.note === '測試模式' ? '測試模式　' : '') + note.join('；') });
}

function lineAlertOnce_(why) {
  try {
    var c = CacheService.getScriptCache(), k = 'line_alert_' + why + '_' + todayStr_();
    if (c.get(k)) { return; }
    c.put(k, '1', 21600);
    var text = why === 'quota' ? 'LINE 月訊息額度用完，推送暫停；待送的訊息保留在「LINE 待送訊息」，額度恢復或升級方案後續送（盤中通知超過 60 分鐘不補送）。'
                               : 'LINE 存取權杖無效或過期，推送暫停。請到後台 LINE 分頁重新貼上權杖。';
    getSheet_('系統狀態').appendRow([lineNow_(), 'LINE', text, 'line', 'Apps Script']);
    if (typeof notifyAdmin_ === 'function') { notifyAdmin_('LINE 推送暫停　' + todayStr_(), text); }
  } catch (e) {}
}

function lineQueueSms_(a, revised) {
  if (!a || !a.id || !lineConfigured_() || linePushMode_() === 'off') { return { skipped: true }; }
  var recipients = lineRecipients_('sms', linePushMode_());
  if (!recipients.length) { return { skipped: 'no-explicit-sms-subscribers' }; }
  var ver = typeof deliveryVersion_ === 'function' ? deliveryVersion_(a.text) : '';
  var id = 'sms|' + a.id + (revised ? '|rev|' + ver : '');
  var posted = lineParseTaipei_(a.time) || Date.now();
  var expires = posted + LINE_SMS_WINDOW_MIN_ * 60000;
  var late = Date.now() > expires;
  var q = lineQueue_({ id: id, kind: 'sms', date: fmtDate_(String(a.time || '').slice(0, 10)) || todayStr_(), version: ver,
    source: '會員簡訊 ' + a.id + (revised ? '（修訂）' : ''), expiresAt: expires, state: late ? 'expired' : 'queued',
    note: late ? '發文超過 60 分鐘才進來，不補送' : '', messages: [lineSmsFlex_(a, !!revised)] });
  if (q.created && !late) {
    try { lineDeliverTick_({ only: id, budgetMs: 45000 }); }
    catch (e) { Logger.log('LINE：盤中通知立即寄送失敗，下一棒續送 ' + e); }
  }
  return q;
}

/** 每日總覽：與 Email 同一套放行條件，但各自記帳；Email 寄過不代表 LINE 寄過。 */
function lineDailyTick_() {
  if (!lineConfigured_() || linePushMode_() === 'off') { return { skipped: 'off' }; }
  var today = todayStr_();
  if (whyClosed_(new Date())) { return { skipped: 'closed' }; }
  var hhmm = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'HHmm'));
  var startHm = typeof dailyPushStartTime_ === 'function' ? Number(dailyPushStartTime_(today)) : 1200;
  if (hhmm < startHm || hhmm > 2200) { return { skipped: 'window' }; }
  var cache = CacheService.getScriptCache();
  if (cache.get('line_daily_' + today)) { return { skipped: 'queued' }; }
  var st = lineTodayState_();
  if (st.state !== 'ready') { return { skipped: st.state + (st.why ? '：' + st.why : '') }; }
  var q = lineQueue_({ id: 'daily|' + today, kind: 'daily', date: today, version: typeof deliveryVersion_ === 'function' ? deliveryVersion_(st.article) : '',
    source: '每日推播內容 ' + today, expiresAt: lineParseTaipei_(today + ' 23:59:59'), messages: [lineDailyFlex_(lineDailyInfo_(today, st.article), '每日總覽')] });
  cache.put('line_daily_' + today, '1', 21600);
  if (q.created) { lineDeliverTick_({ budgetMs: 90000 }); }
  return q;
}

/** 每天一次：事件帳本留 30 天，待送訊息與寄送帳本留 60 天。三張都是依時間附加，從最上面刪到第一列還在期限內為止。 */
function linePruneJob_() {
  if (!lineConfigured_()) { return; }
  var cut = function (days) { return Date.now() - days * 86400000; };
  // [分頁, 時間欄（0 起算）, 保留天數]：事件看收到時間、待送看建立時間、帳本看訊息日期（更新時間會因重試而變）
  [[LINE_EVENTS_SHEET_, 3, 30], [LINE_OUTBOX_SHEET_, 5, 60], [LINE_LEDGER_SHEET_, 1, 60]].forEach(function (x) {
    try {
      var sh = getSheet_(x[0]), last = sh.getLastRow();
      if (last < 3) { return; }
      var col = sh.getRange(2, x[1] + 1, last - 1, 1).getValues(), n = 0;
      while (n < col.length && lineParseTaipei_(col[n][0]) && lineParseTaipei_(col[n][0]) < cut(x[2])) { n++; }
      if (n > 0) { sh.deleteRows(2, n); }
    } catch (e) { Logger.log('LINE：清理 ' + x[0] + ' 失敗 ' + e); }
  });
}

/* ------------------------------------------------------------------ *
 * 網站與後台
 * ------------------------------------------------------------------ */
/** 網站「訂閱通知」頁的 LINE 卡片。後台打開「網站顯示加入好友」之前不顯示。 */
function apiGetLineEntry() {
  try {
    var c = CacheService.getScriptCache(), hit = c.get('line_entry_v1');
    if (hit) { return JSON.parse(hit); }
    var s = lineSettings_(), url = lineAddFriendUrl_();
    var out = { enabled: !!(lineConfigured_() && url && s.siteEntry === true), url: url,
                qr: /^https:\/\/qr-official\.line\.me\//.test(String(s.qrUrl || '')) ? String(s.qrUrl) : '', name: lineClip_(s.botName || '', 40) };
    if (!out.enabled) { out.url = ''; out.qr = ''; }
    c.put('line_entry_v1', JSON.stringify(out), 600);
    return out;
  } catch (e) { return { enabled: false }; }
}

function lineCounts_() {
  var bot = lineBotId_(), n = { friends: 0, blocked: 0, daily: 0, sms: 0, testers: 0, other: 0 };
  try {
    lineSubsRead_().rows.forEach(function (s) {
      if (!bot || s.channel !== bot) { n.other++; return; }
      if (s.friend === 'blocked') { n.blocked++; return; }
      n.friends++;
      if (s.daily) { n.daily++; }
      if (s.sms) { n.sms++; }
      if (s.tester) { n.testers++; }
    });
  } catch (e) { n.error = String(e.message || e); }
  return n;
}
function lineStatusData_() {
  var s = lineSettings_(), st = lineStats_(), out = {
    configured: { token: !!lineProp_('LINE_CHANNEL_ACCESS_TOKEN'), secret: !!lineProp_('LINE_CHANNEL_SECRET') },
    bot: { userId: lineMask_(s.botUserId), basicId: s.basicId || '', name: s.botName || '', checkedAt: s.botCheckedAt || '' },
    mode: linePushMode_(), siteEntry: s.siteEntry === true, relayUrl: s.relayUrl || '', addFriendUrl: lineAddFriendUrl_(), qrUrl: s.qrUrl || '',
    gasTarget: (typeof publicWebAppUrl_ === 'function' ? publicWebAppUrl_() : ''),
    callbackUrl: s.relayUrl ? String(s.relayUrl).replace(/\/+$/, '') + '/callback' : '',
    counts: lineCounts_(), stats: st, quota: null, menu: lineRichMenuHealth_(), outbox: { queued: 0, sending: 0, done: 0, expired: 0, failed: 0, oldestOpen: '' }, recent: []
  };
  if (out.configured.token) { try { out.quota = lineQuota_(false); } catch (e) { out.quota = { ok: false, error: String(e.message || e) }; } }
  try {
    var rows = lineOutboxRead_().rows;
    rows.forEach(function (r) {
      out.outbox[r.state] = (out.outbox[r.state] || 0) + 1;
      if ((r.state === 'queued' || r.state === 'sending') && (!out.outbox.oldestOpen || String(r.created) < out.outbox.oldestOpen)) {
        out.outbox.oldestOpen = r.createdMs ? lineStampOf_(r.createdMs) : String(r.created);
      }
    });
    out.recent = rows.slice(-15).reverse().map(lineOutboxView_);
  } catch (e) { out.outboxError = String(e.message || e); }
  return out;
}
function lineOutboxView_(r) {
  return { id: r.id, kind: r.kind, date: r.date, state: r.state, total: r.total, accepted: r.accepted, failed: r.failed, open: r.open,
           note: r.note, created: r.createdMs ? lineStampOf_(r.createdMs) : '', expires: r.expiresMs ? lineStampOf_(r.expiresMs) : '' };
}

function apiAdminLineStatus(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  try { return { ok: true, data: lineStatusData_() }; } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

/**
 * 儲存設定。權杖與密鑰只寫不讀（畫面只顯示有沒有設定）；空白代表不改。
 * 換權杖時先用它查 /v2/bot/info，查得到才存，並記下這個帳號的 bot userId 與 Basic ID。
 */
function apiAdminLineSaveConfig(key, cfg) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  cfg = cfg || {};
  var patch = {}, token = String(cfg.token || '').trim(), secret = String(cfg.secret || '').trim();
  if (token) {
    if (!/^[A-Za-z0-9+\/=._\-]{40,}$/.test(token)) { return { ok: false, reason: '存取權杖格式不對（應為 LINE Developers 發的長期權杖）。' }; }
    var info = lineApi_('get', '/v2/bot/info', null, { token: token });
    if (info.code !== 200 || !info.json || !info.json.userId) { return { ok: false, reason: '用這個權杖查不到 LINE 帳號：' + lineErrText_(info) }; }
    lineSetProp_('LINE_CHANNEL_ACCESS_TOKEN', token);
    patch.botUserId = String(info.json.userId); patch.basicId = String(info.json.basicId || ''); patch.botName = String(info.json.displayName || '');
    patch.botCheckedAt = lineNow_();
    try { CacheService.getScriptCache().remove('line_quota_v1'); } catch (e) {}
  }
  if (secret) {
    if (!/^[0-9a-f]{32}$/i.test(secret)) { return { ok: false, reason: '頻道密鑰格式不對（32 個十六進位字元）。' }; }
    lineSetProp_('LINE_CHANNEL_SECRET', secret);
  }
  if (cfg.mode === 'off' || cfg.mode === 'test' || cfg.mode === 'on') { patch.mode = cfg.mode; }
  if (cfg.relayUrl !== undefined) {
    var ru = String(cfg.relayUrl || '').trim().replace(/\/+$/, '');
    if (ru && !/^https:\/\/[A-Za-z0-9.\-]+(?::\d+)?$/.test(ru)) { return { ok: false, reason: '轉送服務網址要是 https:// 開頭的服務網址（不含路徑），例如 https://line-webhook-xxxx.a.run.app' }; }
    patch.relayUrl = ru;
  }
  if (cfg.addFriendUrl !== undefined) {
    var af = String(cfg.addFriendUrl || '').trim();
    if (af && !/^https:\/\/(?:line\.me|lin\.ee)\//.test(af)) { return { ok: false, reason: '加入好友網址要是 https://lin.ee/… 或 https://line.me/… 。' }; }
    patch.addFriendUrl = af;
  }
  if (cfg.qrUrl !== undefined) {
    var qr = String(cfg.qrUrl || '').trim();
    if (qr && !/^https:\/\/qr-official\.line\.me\//.test(qr)) { return { ok: false, reason: 'QR 圖片網址要是 LINE 官方帳號管理後台提供的 https://qr-official.line.me/… 。' }; }
    patch.qrUrl = qr;
  }
  if (cfg.siteEntry !== undefined) { patch.siteEntry = cfg.siteEntry === true; }
  if (Object.keys(patch).length) { lineSaveSettings_(patch); }
  try { CacheService.getScriptCache().remove('line_entry_v1'); } catch (e) {}
  if (patch.mode) { getSheet_('系統狀態').appendRow([lineNow_(), 'LINE', '推送模式改為「' + ({ off: '關閉', test: '只送測試帳號', on: '正式推送' })[patch.mode] + '」', 'line', '後台']); }
  return { ok: true, data: lineStatusData_() };
}

/** 依訊息ID、文章ID或日期查：來源 → 待送 → 每位收件者（遮罩）的狀態。只讀。 */
function apiAdminLineLookup(key, q) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  q = String(q || '').trim();
  if (!q) { return { ok: false, reason: '請輸入訊息ID、文章ID或日期（例如 2026/09/23）。' }; }
  var d = fmtDate_(q), rows = lineOutboxRead_().rows.filter(function (r) { return r.id === q || r.id.indexOf(q) >= 0 || r.date === d; }).slice(-10).reverse();
  return { ok: true, items: rows.map(function (r) {
    var led = lineLedgerFor_(r.id), v = lineOutboxView_(r);
    v.recipients = led.rows.slice(0, 50).map(function (x) {
      var raw = led.vals[x.i];
      return { user: lineMask_(x.uid), state: x.state, attempt: x.attempt, err: x.err,
               acceptedAt: raw[8] instanceof Date ? lineStampOf_(raw[8].getTime()) : String(raw[8] || '') };
    });
    v.canRetry = (r.state === 'done' || r.state === 'sending') && (!r.expiresMs || Date.now() < r.expiresMs) &&
      led.rows.some(function (x) { return x.state === 'failed' || x.state === 'unknown'; });
    return v;
  }) };
}

/** 只重試還沒被 LINE 接受的收件者（失敗、不明），同一把 retry key；過了期限（盤中 60 分鐘、每日當天）不重試。 */
function apiAdminLineRetry(key, id) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  var t = lineOutboxRead_(), msg = t.rows.filter(function (r) { return r.id === String(id || ''); })[0];
  if (!msg) { return { ok: false, reason: '找不到這則訊息。' }; }
  if (msg.expiresMs && Date.now() > msg.expiresMs) { return { ok: false, reason: '已超過送達期限（盤中通知 60 分鐘、每日總覽當天），不補送。' }; }
  var led = lineLedgerFor_(msg.id), n = 0;
  led.rows.forEach(function (x) { if (x.state === 'failed' || x.state === 'unknown') { lineLedgerSet_(led, x, { state: 'retryable', attempt: 0, err: '管理者重試' }); n++; } });
  if (!n) { return { ok: false, reason: '沒有需要重試的收件者（已接受的不會重送）。' }; }
  lineLedgerFlush_(led);
  lineOutboxSet_(t, msg, { state: 'sending' });
  lineSetProp_('LINE_OUTBOX_OPEN', '1');
  var r = lineDeliverTick_({ only: msg.id, budgetMs: 60000, force: true });
  return { ok: true, retried: n, result: r };
}

/** 產生十分鐘有效的綁定碼：管理者在 LINE 輸入「綁定 123456」，那個帳號就成為測試帳號。 */
function apiAdminLineBindCode(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  var code = String(100000 + (parseInt(Utilities.getUuid().replace(/\D/g, '').slice(0, 9), 10) % 900000));
  lineSetProp_('LINE_ADMIN_BIND', JSON.stringify({ code: code, until: Date.now() + 10 * 60000, tries: 0 }));
  return { ok: true, code: code, minutes: 10 };
}
function apiAdminLineClearTesters(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  var n = 0;
  lineSubsRead_().rows.filter(function (s) { return s.tester; }).forEach(function (s) { lineUpsertSub_(s.uid, s.channel, function (x) { x.tester = false; }); n++; });
  return { ok: true, cleared: n };
}

/** 送一則測試到綁定的測試帳號（不寫寄送帳本；會計入當月推送量）。kind：daily／sms。 */
function apiAdminLineTestPush(key, kind) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  if (kind !== 'daily') { return { ok: false, reason: 'LINE 測試推送目前只提供每日總覽。' }; }
  if (!lineConfigured_()) { return { ok: false, reason: '還沒設定存取權杖與頻道密鑰。' }; }
  var bot = lineBotId_();
  var to = lineSubsRead_().rows.filter(function (s) { return bot && s.tester && s.friend !== 'blocked' && s.channel === bot; });
  if (!to.length) { return { ok: false, reason: '沒有綁定的測試帳號。先按「產生綁定碼」，在 LINE 輸入「綁定 六位數」。' }; }
  var msg = lineSampleMessage_(kind);
  if (!msg) { return { ok: false, reason: kind === 'sms' ? '會員簡訊分頁沒有任何一則可測。' : '每日推播內容沒有任何一篇可測。' }; }
  var results = to.map(function (s) {
    var r = lineApi_('post', '/v2/bot/message/push', { to: s.uid, messages: [msg] }, { retryKey: Utilities.getUuid() });
    return { user: lineMask_(s.uid), ok: r.code === 200, error: r.code === 200 ? '' : 'HTTP ' + r.code + ' ' + lineErrText_(r) };
  });
  return { ok: results.some(function (r) { return r.ok; }), results: results };
}
function lineSampleMessage_(kind) {
  if (kind === 'sms') {
    var d = null; try { d = memberSmsData_(1); } catch (e) {}
    var a = d && d.items && d.items[0];
    return a ? lineSmsFlex_({ id: a.id, time: a.time, text: a.text, url: a.url }, false) : null;
  }
  var rows = readSheetObjects_('每日推播內容').filter(function (r) { return String(r['文字稿'] || '').length > 20; });
  var r = rows.sort(function (a, b) { return fmtDate_(a['日期']) < fmtDate_(b['日期']) ? 1 : -1; })[0];
  return r ? lineDailyFlex_(lineDailyInfo_(fmtDate_(r['日期']), String(r['文字稿'])), '每日總覽（測試）') : null;
}

/** 部署驗證：每一種卡片與兩個圖文選單都交給 LINE 的驗證 API 檢查格式（不會送出任何訊息）。 */
function lineValidateAll_() {
  var fakeSub = { uid: 'U00000000000000000000000000000000', daily: true, sms: false };
  var samples = [
    ['歡迎', lineWelcomeFlex_(fakeSub, false)], ['歡迎回來', lineWelcomeFlex_(fakeSub, true)], ['管理訂閱', lineManageFlex_(fakeSub)],
    ['開啟確認', lineSubConfirm_({ daily: true, sms: false }, { daily: false, sms: false }, ['daily'], true)],
    ['停止確認', lineSubConfirm_({ daily: false, sms: false }, { daily: true, sms: false }, ['daily'], false)],
    ['使用說明', lineHelpFlex_()],
    ['每日總覽', lineDailyFlex_({ date: '2026/09/23', title: '記憶體報價止跌，法人回補後的操作重點整理', points: ['加權指數量縮整理，季線附近有撐。', '記憶體族群報價止跌，法人連三天回補。'], counts: { buy: 1, sell: 1, hold: 3, watch: 4 } }, '每日總覽')],
    ['個股', lineStockFlex_({ code: '6770', name: '力積電' }, [{ date: '2026/09/23', direction: '買入', price: '73.5以下', reason: '記憶體報價止跌，法人連三天回補。' }, { date: '2026/09/18', direction: '觀望注意', price: '', reason: '' }])],
    ['持股追蹤', lineTrackerFlex_([{ name: '力積電', code: '6770', entryDate: '2026/09/23', latestReasonDate: '2026/09/25', roundsAsOf: '2026/09/25' }], {})],
    ['市場總覽', lineMarketFlex_([{ label: '加權指數', value: 23456.78, change: -123.4, percent: -0.52, time: '2026/09/26 13:30:00', source: 'Yahoo Finance', unit: '點' }])]
  ];
  var out = samples.map(function (s) {
    var r = lineApi_('post', '/v2/bot/message/validate/push', { messages: [s[1]] });
    return { name: s[0], ok: r.code === 200, error: r.code === 200 ? '' : 'HTTP ' + r.code + ' ' + lineErrText_(r) };
  });
  lineRichMenuDefs_().forEach(function (m) {
    var r = lineApi_('post', '/v2/bot/richmenu/validate', m.def);
    out.push({ name: '圖文選單 ' + m.def.name, ok: r.code === 200, error: r.code === 200 ? '' : 'HTTP ' + r.code + ' ' + lineErrText_(r) });
  });
  return out;
}
function apiAdminLineValidate(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  if (!lineProp_('LINE_CHANNEL_ACCESS_TOKEN')) { return { ok: false, reason: '還沒設定存取權杖。' }; }
  var items = lineValidateAll_();
  return { ok: items.every(function (x) { return x.ok; }), items: items };
}

/* ------------------------------------------------------------------ *
 * 圖文選單：兩頁（查資料／通知），上方分頁用 richmenuswitch 切換。
 * 圖片由轉送服務 /static/ 提供（line-webhook/static，scripts/make_line_richmenu.py 產生）。
 * 電腦版 LINE 不顯示圖文選單，所有功能都能用文字指令完成。
 * ------------------------------------------------------------------ */
function lineRichMenuDefs_() {
  var W = 2500, TAB = 220, CW = 1250, CH = 733;
  var tabs = function (page) {
    return [
      { bounds: { x: 0, y: 0, width: CW, height: TAB }, action: { type: 'richmenuswitch', richMenuAliasId: LINE_RICH_ALIAS_.query, data: 'a=tab&p=query' } },
      { bounds: { x: CW, y: 0, width: CW, height: TAB }, action: { type: 'richmenuswitch', richMenuAliasId: LINE_RICH_ALIAS_.notify, data: 'a=tab&p=notify' } }
    ];
  };
  var cell = function (i, action) { return { bounds: { x: (i % 2) * CW, y: TAB + Math.floor(i / 2) * CH, width: CW, height: CH }, action: action }; };
  var site = (typeof publicSiteUrl_ === 'function' && publicSiteUrl_()) ? publicSiteUrl_() :
             (typeof PUBLIC_SITE_URL_DEFAULT === 'string' && PUBLIC_SITE_URL_DEFAULT ? PUBLIC_SITE_URL_DEFAULT : 'https://lee200202.github.io/Stock/');
  site = (site || 'https://lee200202.github.io/Stock/').replace(/\/?$/, '/');
  return [
    { alias: LINE_RICH_ALIAS_.query, image: 'richmenu-query.png', def: { size: { width: W, height: 1686 }, selected: true, name: 'zz-query-v1', chatBarText: '查資料',
      areas: tabs('query').concat([cell(0, linePb_('今日整理', 'a=today', '今日整理')), cell(1, linePb_('查個股', 'a=askstock', '查個股', { inputOption: 'openKeyboard' })),
        cell(2, linePb_('持股追蹤', 'a=tracker', '持股追蹤')), cell(3, linePb_('市場總覽', 'a=market', '市場總覽'))]) } },
    { alias: LINE_RICH_ALIAS_.notify, image: 'richmenu-notify.png', sha256: 'fe8cf11f982bfc1911a9c4a6bc5550d5f8e19863732d80c45abd23599ca8d8dd', def: { size: { width: W, height: 1686 }, selected: true, name: 'zz-notify-v2', chatBarText: '通知',
      areas: tabs('notify').concat([cell(0, linePb_('管理訂閱', 'a=manage', '管理訂閱')), cell(1, linePb_('今日整理', 'a=today', '今日整理')),
        cell(2, linePb_('使用說明', 'a=help', '使用說明')), cell(3, site ? lineUri_('開啟網站', site) : linePb_('今日整理', 'a=today', '今日整理'))]) } }
  ];
}
/** 只核對 LINE 上的通知選單別名與版本；個別好友覆蓋選單仍須用手機驗收。 */
function lineRichMenuHealth_() {
  var expected = lineRichMenuDefs_()[1].def.name;
  if (!lineProp_('LINE_CHANNEL_ACCESS_TOKEN')) { return { ok: false, expected: expected, reason: '未設定存取權杖' }; }
  var alias = lineApi_('get', '/v2/bot/richmenu/alias/' + LINE_RICH_ALIAS_.notify);
  var id = alias.json && alias.json.richMenuId;
  if (alias.code !== 200 || !id) { return { ok: false, expected: expected, reason: '通知選單別名尚未建立或讀取失敗：' + lineErrText_(alias) }; }
  var menu = lineApi_('get', '/v2/bot/richmenu/' + encodeURIComponent(id));
  var actual = menu.json && menu.json.name;
  if (menu.code !== 200 || !actual) { return { ok: false, expected: expected, reason: '通知選單讀取失敗：' + lineErrText_(menu) }; }
  return { ok: actual === expected, expected: expected, actual: actual,
           reason: actual === expected ? '別名已指向新版；仍需用手機核對圖片' : '別名仍指向舊版，請重新建立圖文選單' };
}
function lineImageSha256_(bytes) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes)
    .map(function (b) { return ('0' + (b & 255).toString(16)).slice(-2); }).join('');
}
function lineSetupRichMenus_() {
  var relay = String(lineSettings_().relayUrl || '').replace(/\/+$/, '');
  if (!relay) { return { ok: false, reason: '先在後台填「轉送服務網址」（圖文選單圖片由它提供）。' }; }
  var log = [], defs = lineRichMenuDefs_();
  // 圖片先全數驗證，避免來源還是舊版時已經建立半套新選單。
  var images = {};
  for (var k = 0; k < defs.length; k++) {
    var source = defs[k], img;
    try { img = UrlFetchApp.fetch(relay + '/static/' + source.image, { muteHttpExceptions: true }); } catch (e) { img = null; }
    if (!img || img.getResponseCode() !== 200) { return { ok: false, reason: '讀不到圖片 ' + relay + '/static/' + source.image + '（轉送服務部署了嗎？）' }; }
    images[source.alias] = img.getBlob().getBytes();
    if (source.sha256 && lineImageSha256_(images[source.alias]) !== source.sha256) {
      return { ok: false, reason: source.image + ' 不是目前版本。先從 line-webhook/ 部署轉送服務，再按「建立圖文選單」。' };
    }
  }
  // 新選單連圖片都驗過後才切換別名；舊選單在切換失敗時仍可用。
  var list = lineApi_('get', '/v2/bot/richmenu/list');
  var oldMenus = ((list.json && list.json.richmenus) || []).filter(function (m) { return /^zz-(?:query|notify)-/.test(String(m.name || '')); });
  var ids = {};
  for (var i = 0; i < defs.length; i++) {
    var m = defs[i];
    var c = lineApi_('post', '/v2/bot/richmenu', m.def);
    if (c.code !== 200 || !c.json || !c.json.richMenuId) { return { ok: false, reason: '建立 ' + m.def.name + ' 失敗：' + lineErrText_(c), log: log }; }
    ids[m.alias] = c.json.richMenuId;
    var up = lineApi_('post', '/v2/bot/richmenu/' + ids[m.alias] + '/content', null, { data: true, bytes: images[m.alias], contentType: 'image/png' });
    if (up.code !== 200) { return { ok: false, reason: '上傳圖片失敗：' + lineErrText_(up), log: log }; }
    log.push('建立 ' + m.def.name + ' 並上傳圖片');
  }
  for (var j = 0; j < defs.length; j++) {
    var alias = defs[j].alias, existing = lineApi_('get', '/v2/bot/richmenu/alias/' + alias);
    var path = existing.code === 200 ? '/v2/bot/richmenu/alias/' + alias : '/v2/bot/richmenu/alias';
    var body = existing.code === 200 ? { richMenuId: ids[alias] } : { richMenuAliasId: alias, richMenuId: ids[alias] };
    var a = lineApi_('post', path, body);
    if (a.code !== 200) { return { ok: false, reason: '切換別名 ' + alias + ' 失敗：' + lineErrText_(a), log: log }; }
  }
  var dflt = lineApi_('post', '/v2/bot/user/all/richmenu/' + ids[LINE_RICH_ALIAS_.query]);
  if (dflt.code !== 200) { return { ok: false, reason: '設為預設選單失敗：' + lineErrText_(dflt), log: log }; }
  log.push('「查資料」設為所有好友的預設選單');
  oldMenus.forEach(function (m) {
    if (m.richMenuId !== ids[LINE_RICH_ALIAS_.query] && m.richMenuId !== ids[LINE_RICH_ALIAS_.notify]) {
      var gone = lineApi_('delete', '/v2/bot/richmenu/' + m.richMenuId);
      if (gone.code === 200) { log.push('清理舊選單 ' + m.name); }
    }
  });
  lineSaveSettings_({ richMenuAt: lineNow_() });
  return { ok: true, log: log };
}
function apiAdminLineSetupRichMenu(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  if (!lineProp_('LINE_CHANNEL_ACCESS_TOKEN')) { return { ok: false, reason: '還沒設定存取權杖。' }; }
  return lineSetupRichMenus_();
}

/* ------------------------------------------------------------------ *
 * 在編輯器執行（部署步驟用，只印不改；金鑰只印有沒有設定）
 * ------------------------------------------------------------------ */
function lineSetupCheck() {
  var d = lineStatusData_(), L = [];
  L.push('LINE 設定檢查　' + lineNow_());
  L.push('存取權杖：' + (d.configured.token ? '已設定' : '未設定') + '　頻道密鑰：' + (d.configured.secret ? '已設定' : '未設定'));
  L.push('LINE 帳號：' + (d.bot.name || '（未查）') + '　Basic ID ' + (d.bot.basicId || '—') + '　bot ' + (d.bot.userId || '—'));
  L.push('推送模式：' + ({ off: '關閉', test: '只送測試帳號', on: '正式推送' })[d.mode] + '　網站顯示加入好友：' + (d.siteEntry ? '是' : '否'));
  L.push('轉送服務：' + (d.relayUrl || '未填') + '　→ LINE Developers 的 Webhook URL 填：' + (d.callbackUrl || '（先填轉送服務網址）'));
  L.push('通知圖文選單：' + (d.menu.ok ? d.menu.actual + '（別名已更新，圖片仍需手機核對）' : d.menu.reason));
  L.push('轉送服務的環境變數 GAS_WEBAPP_URL 填：' + (d.gasTarget || '（沒有正式 /exec 網址，先執行 setWebAppUrl()）'));
  L.push('好友 ' + d.counts.friends + '（封鎖 ' + d.counts.blocked + '）　每日總覽 ' + d.counts.daily + '　盤中通知 ' + d.counts.sms + '　測試帳號 ' + d.counts.testers);
  L.push('最後收到 Webhook：' + (d.stats.lastWebhookAt || '尚無') + '　今日驗簽失敗：網站 ' + (d.stats.gasSigFail || 0) + '、轉送服務 ' + (d.stats.cloudSigFail || 0));
  if (d.quota) { L.push('本月額度：' + (d.quota.ok ? (d.quota.type === 'limited' ? '已用 ' + d.quota.used + '／' + d.quota.limit : '無上限（' + d.quota.type + '）') : '讀不到：' + (d.quota.error || ''))); }
  L.push('待送：queued ' + (d.outbox.queued || 0) + '、sending ' + (d.outbox.sending || 0) + (d.outbox.oldestOpen ? '（最舊 ' + d.outbox.oldestOpen + '）' : ''));
  var trig = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); });
  L.push('五分鐘總排程 everyFiveMinJob：' + (trig.indexOf('everyFiveMinJob') >= 0 ? '有' : '沒有（推送與續送靠它）') + '　每分鐘 cmoneyPollJob：' + (trig.indexOf('cmoneyPollJob') >= 0 ? '有' : '沒有'));
  Logger.log(L.join('\n'));
  return d;
}
function lineValidateTemplates() {
  var items = lineValidateAll_();
  Logger.log(items.map(function (x) { return (x.ok ? '✓ ' : '✗ ') + x.name + (x.error ? '　' + x.error : ''); }).join('\n'));
  return items;
}
function lineSetupRichMenus() {
  var r = lineSetupRichMenus_();
  Logger.log((r.ok ? '完成　' : '失敗　' + r.reason + '\n') + (r.log || []).join('\n'));
  return r;
}
