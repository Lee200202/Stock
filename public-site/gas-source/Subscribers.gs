/* ------------------------------------------------------------------ *
 * 後台「訂閱者」分頁（2026/11/24）
 *
 * Email：列出「使用者訂閱清單」，寄出統計從「寄送帳本」逐收件者算（v54 起有逐收件者紀錄）。
 *        後台可取消（狀態改「已取消」，可復原）或永久刪除訂閱列；寄送帳本一律保留不動。
 * LINE： 列出「LINE 訂閱清單」的帳號，標出管理者端（測試身分＝管理者）。
 *        對話紀錄：每則收到的訊息與回覆寫進「LINE 對話紀錄」（Line.gs lineHandleEvent_ 回覆後才寫），留一年。
 *        月統計：每天 linePruneJob_ 結束前，把新增的對話與寄送帳本已接受的推送累加進「LINE 帳號月統計」，永久保留。
 *        寄送帳本只留 60 天，第一次累加會把這 60 天補進去；更早的推送與 11/24 以前的對話無從補回。
 * 這些資料只經後台登入後的 API 回傳，不進公開頁、不寫進 repo。
 * ------------------------------------------------------------------ */
var LINE_CONV_SHEET_ = 'LINE 對話紀錄';
var LINE_STATS_SHEET_ = 'LINE 帳號月統計';
var LINE_CONV_KEEP_DAYS_ = 365;
var LINE_STATS_COLS_ = ['月份', '使用者ID', '每日總覽', '盤中通知', '管理者通知', '其他推送', '收到訊息', '回覆'];

function subMaskEmail_(e) {
  e = String(e || '');
  var at = e.indexOf('@');
  return at > 0 ? e.slice(0, 1) + '***' + e.slice(at) : '***';
}
function subStamp_(v) {
  if (v instanceof Date) { return Utilities.formatDate(v, 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss'); }
  return String(v == null ? '' : v).trim();
}
function subYmd_(v) {
  var t = subStamp_(v);
  if (/^\d{4}[\/-]\d{1,2}[\/-]\d{1,2}/.test(t)) { return t.replace(/-/g, '/').slice(0, 10); }
  var d = t ? new Date(t) : null;   // 試算表日期轉成字串後是「Mon Sep 28 2026 …」，改回台北日期
  return d && !isNaN(d.getTime()) ? Utilities.formatDate(d, 'Asia/Taipei', 'yyyy/MM/dd') : t;
}

/* ---------------- LINE 對話紀錄與月統計 ---------------- */

/** 一則事件寫一到兩列：收到什麼、回了什麼（文字或卡片的替代文字）。 */
function lineConvLog_(uid, ev, msgs, out) {
  var t = String(ev.type || ''), kind = t, text = '';
  if (t === 'message') {
    var m = ev.message || {};
    kind = 'message:' + (m.type || '');
    text = m.type === 'text' ? String(m.text || '') : '（' + (m.type || '訊息') + '）';
  } else if (t === 'postback') {
    text = '（按鈕）' + String(ev.postback && ev.postback.data || '');
  } else if (t === 'follow') { text = '加入好友'; }
  else if (t === 'unfollow') { text = '封鎖或刪除好友'; }
  else { return; }
  var sh = getSheet_(LINE_CONV_SHEET_), now = lineNow_();
  sh.appendRow([now, uid, '收到', kind, lineClip_(text, 500)]);
  if (msgs && msgs.length && (!out.reply || out.reply === 'ok')) {
    var said = msgs.map(function (x) { return x.type === 'text' ? String(x.text || '') : String(x.altText || '（' + x.type + '）'); }).join(' ／ ');
    sh.appendRow([now, uid, '回覆', '', lineClip_(said, 500)]);
  }
}

function lineStatKind_(messageId) {
  var id = String(messageId || '');
  if (/^daily\|/.test(id)) { return '每日總覽'; }
  if (/^sms\|/.test(id)) { return '盤中通知'; }
  if (/^admin/.test(id)) { return '管理者通知'; }
  return '其他推送';
}

/** 每天一次：新增的對話列與已接受的推送 → 月統計；再清一年前的對話。可重跑：進度記在 LINE_ROLLUP_STATE。 */
function lineAccountRollup_() {
  var props = PropertiesService.getScriptProperties();
  var st = {};
  try { st = JSON.parse(props.getProperty('LINE_ROLLUP_STATE') || '{}') || {}; } catch (e) { st = {}; }
  var add = {};   // 月份|uid → {欄位: 次數}
  function bump(month, uid, col) {
    if (!month || !uid) { return; }
    var k = month + '|' + uid;
    add[k] = add[k] || {};
    add[k][col] = (add[k][col] || 0) + 1;
  }
  // 1) 對話：只會在表尾新增、只由這裡從表頭刪，記「已算過幾列」即可。
  var conv = getSheet_(LINE_CONV_SHEET_), last = conv.getLastRow(), done = Number(st.convDone) || 0;
  if (last - 1 > done) {
    conv.getRange(2 + done, 1, last - 1 - done, 3).getValues().forEach(function (r) {
      var month = subYmd_(r[0]).slice(0, 7);
      bump(month, String(r[1]), String(r[2]) === '回覆' ? '回覆' : '收到訊息');
    });
    done = last - 1;
  }
  // 2) 推送：寄送帳本以「接受時間」落在 (上次截止, 這次截止] 計；截止取十分鐘前，避開正在寫入的批次。
  var cutoff = Date.now() - 10 * 60000, from = Number(st.ledgerAt) || 0;
  var led = getSheet_(LINE_LEDGER_SHEET_), ll = led.getLastRow();
  if (ll >= 2) {
    led.getRange(2, 1, ll - 1, LINE_LEDGER_COLS_.length).getValues().forEach(function (r) {
      if (String(r[3]) !== 'accepted') { return; }
      var at = lineParseTaipei_(subStamp_(r[8]));
      if (!at || at <= from || at > cutoff) { return; }
      bump(Utilities.formatDate(new Date(at), 'Asia/Taipei', 'yyyy/MM'), String(r[2]), lineStatKind_(r[0]));
    });
  }
  // 3) 寫回月統計
  var keys = Object.keys(add);
  if (keys.length) {
    withLock_(function () {
      var sh = getSheet_(LINE_STATS_SHEET_), vals = sh.getDataRange().getValues(), at = {};
      for (var i = 1; i < vals.length; i++) { at[subStamp_(vals[i][0]).slice(0, 7) + '|' + vals[i][1]] = i; }
      var fresh = [];
      keys.forEach(function (k) {
        var p = k.split('|'), i = at[k], row;
        if (i === undefined) { row = [p[0], p[1], 0, 0, 0, 0, 0, 0]; fresh.push(row); }
        else { row = vals[i]; }
        Object.keys(add[k]).forEach(function (col) {
          var c = LINE_STATS_COLS_.indexOf(col);
          row[c] = (Number(row[c]) || 0) + add[k][col];
        });
      });
      if (vals.length > 1) { sh.getRange(2, 1, vals.length - 1, LINE_STATS_COLS_.length).setValues(vals.slice(1)); }
      if (fresh.length) { sh.getRange(Math.max(sh.getLastRow(), 1) + 1, 1, fresh.length, LINE_STATS_COLS_.length).setNumberFormat('@').setValues(fresh); }
    });
  }
  // 4) 清一年前的對話（已計入統計）
  var keepFrom = Date.now() - LINE_CONV_KEEP_DAYS_ * 86400000, cut = 0;
  if (done > 0) {
    var times = conv.getRange(2, 1, done, 1).getValues();
    while (cut < times.length && lineParseTaipei_(subStamp_(times[cut][0])) && lineParseTaipei_(subStamp_(times[cut][0])) < keepFrom) { cut++; }
    if (cut > 0) { conv.deleteRows(2, cut); done -= cut; }
  }
  st = { convDone: done, ledgerAt: Math.max(from, cutoff), at: lineNow_() };
  props.setProperty('LINE_ROLLUP_STATE', JSON.stringify(st));
  return { rows: keys.length, pruned: cut };
}

/** LINE 顯示名稱：向 LINE 查好友資料，存在指令碼屬性，七天內不重查；不是好友或查不到記空白。 */
function lineDisplayNames_(uids, budget) {
  var props = PropertiesService.getScriptProperties(), map = {};
  try { map = JSON.parse(props.getProperty('LINE_NAMES') || '{}') || {}; } catch (e) { map = {}; }
  var token = lineProp_('LINE_CHANNEL_ACCESS_TOKEN'), now = Date.now(), changed = false, n = 0;
  uids.forEach(function (uid) {
    var hit = map[uid];
    if (!token || (hit && now - hit.at < 7 * 86400000) || n >= (budget || 25)) { return; }
    n++;
    try {
      var r = UrlFetchApp.fetch('https://api.line.me/v2/bot/profile/' + encodeURIComponent(uid),
        { headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true });
      var name = r.getResponseCode() === 200 ? String(JSON.parse(r.getContentText()).displayName || '') : '';
      map[uid] = { n: lineClip_(name, 40), at: now }; changed = true;
    } catch (e) { /* 網路暫時失敗：下次再查 */ }
  });
  if (changed) {
    var text = JSON.stringify(map);
    if (text.length < 8500) { props.setProperty('LINE_NAMES', text); }
  }
  var out = {};
  uids.forEach(function (u) { out[u] = map[u] ? map[u].n : ''; });
  return out;
}

/* ---------------- 後台 API ---------------- */

function apiAdminSubscribers(key) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  var out = { ok: true, at: nowStamp_() };

  // Email
  var sent = {}, ledgerFrom = '';
  try {
    readSheetObjects_(DELIVERY_SHEET_).forEach(function (r) {
      if (String(r['狀態']) !== 'accepted') { return; }
      var to = String(r['收件者'] || '').toLowerCase(), d = subYmd_(r['日期']);
      if (!to || !d) { return; }
      var s = sent[to] = sent[to] || { total: 0, years: {}, last: '' };
      var y = d.slice(0, 4), kind = String(r['種類']) === 'daily' ? 'daily' : String(r['種類']) === 'sms' ? 'sms' : 'other';
      s.total++;
      s.years[y] = s.years[y] || { daily: 0, sms: 0, other: 0 };
      s.years[y][kind]++;
      if (d > s.last) { s.last = d; }
      if (!ledgerFrom || d < ledgerFrom) { ledgerFrom = d; }
    });
  } catch (e) { out.emailLedgerError = String(e.message || e); }
  out.email = readSheetObjects_('使用者訂閱清單').filter(function (r) {
    return String(r['Email'] || '').indexOf('@') > 0;
  }).map(function (r) {
    var items = String(r['訂閱項目'] || ''), email = String(r['Email']).trim();
    return { email: email, daily: items.indexOf('每日總覽') >= 0, sms: items.indexOf('會員簡訊') >= 0,
      active: String(r['狀態']) !== '已取消', since: subYmd_(r['建立時間']),
      sent: sent[email.toLowerCase()] || { total: 0, years: {}, last: '' } };
  });
  out.emailLedgerFrom = ledgerFrom;

  // LINE
  var stats = {}, statFrom = '';
  try {
    readSheetObjects_(LINE_STATS_SHEET_).forEach(function (r) {
      var uid = String(r['使用者ID'] || ''), month = subStamp_(r['月份']).slice(0, 7);
      if (!uid || !month) { return; }
      var s = stats[uid] = stats[uid] || { push: 0, received: 0, replied: 0, years: {} };
      var y = month.slice(0, 4), ys = s.years[y] = s.years[y] || { daily: 0, sms: 0, admin: 0, other: 0, received: 0, replied: 0 };
      var g = function (k) { return Number(r[k]) || 0; };
      ys.daily += g('每日總覽'); ys.sms += g('盤中通知'); ys.admin += g('管理者通知'); ys.other += g('其他推送');
      ys.received += g('收到訊息'); ys.replied += g('回覆');
      s.push += g('每日總覽') + g('盤中通知') + g('管理者通知') + g('其他推送');
      s.received += g('收到訊息'); s.replied += g('回覆');
      if (!statFrom || month < statFrom) { statFrom = month; }
    });
  } catch (e) { out.lineStatsError = String(e.message || e); }
  var rollup = {};
  try { rollup = JSON.parse(PropertiesService.getScriptProperties().getProperty('LINE_ROLLUP_STATE') || '{}') || {}; } catch (e) { rollup = {}; }
  var subs = [];
  try { subs = lineSubsRead_().rows; } catch (e) { out.lineError = String(e.message || e); }
  var names = {};
  try { names = lineDisplayNames_(subs.map(function (s) { return s.uid; }), 25); } catch (e) { names = {}; }
  out.line = subs.map(function (s) {
    return { uid: s.uid, id: lineMask_(s.uid), name: names[s.uid] || '', admin: !!s.tester, adminMode: !!s.adminMode,
      friend: s.friend, daily: !!s.daily, sms: !!s.sms, since: subYmd_(s.createdAt), lastSeen: subYmd_(s.lastSeen),
      stats: stats[s.uid] || { push: 0, received: 0, replied: 0, years: {} } };
  });
  out.lineStatsFrom = statFrom;
  out.lineRollupAt = rollup.at || '';
  return out;
}

/** 某個 LINE 帳號最近的對話（新的在前）。 */
function apiAdminLineConversation(key, uid, limit) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  uid = String(uid || '');
  if (!/^U[0-9a-f]{32}$/.test(uid)) { return { ok: false, reason: '帳號格式不對。' }; }
  limit = Math.min(Math.max(Number(limit) || 50, 1), 200);
  var sh = getSheet_(LINE_CONV_SHEET_), last = sh.getLastRow(), rows = [];
  for (var end = last; end >= 2 && rows.length < limit; end -= 2000) {
    var start = Math.max(2, end - 1999);
    var vals = sh.getRange(start, 1, end - start + 1, 5).getValues();
    for (var i = vals.length - 1; i >= 0 && rows.length < limit; i--) {
      if (String(vals[i][1]) === uid) { rows.push({ at: subStamp_(vals[i][0]), dir: String(vals[i][2]), text: String(vals[i][4] || '') }); }
    }
  }
  return { ok: true, rows: rows };
}

/* 2026/11/25 後台「LINE 推送紀錄」：每則推送（每日總覽、盤中通知、管理者通知…）的時間、內容、收件人與結果。
   直接讀既有的「LINE 待送訊息」與「LINE 寄送帳本」（推送當下就寫入，所以是即時的）；只讀、不另外寫，也不多用 LINE 額度。 */
var LINE_PUSH_KIND_ = { daily: '每日總覽', sms: '盤中通知', admin: '管理者通知' };
function linePushTexts_(msgs) {
  var out = [];
  (function walk(n) {
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (!n || typeof n !== 'object') { return; }
    if ((n.type === 'text' || n.type === 'span') && typeof n.text === 'string' && n.text.trim()) { out.push(n.text.trim()); }
    Object.keys(n).forEach(function (k) { if (k !== 'action' && n[k] && typeof n[k] === 'object') { walk(n[k]); } });
  })(msgs);
  return out;
}
function apiAdminLinePushLog(key, days) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  days = [1, 7, 30].indexOf(Number(days)) >= 0 ? Number(days) : 7;
  var today = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd');
  var since = days === 1 ? lineParseTaipei_(today + ' 00:00:00') : Date.now() - days * 86400000;
  var t = lineOutboxRead_();
  var rows = t.rows.filter(function (r) { return (r.createdMs || 0) >= since; })
    .sort(function (a, b) { return (b.createdMs || 0) - (a.createdMs || 0); }).slice(0, 80);
  var payload = {};
  if (rows.length && t.ci['內容JSON'] >= 0) {
    var lo = Math.min.apply(null, rows.map(function (r) { return r.row; })), hi = Math.max.apply(null, rows.map(function (r) { return r.row; }));
    t.sh.getRange(lo, t.ci['內容JSON'] + 1, hi - lo + 1, 1).getValues().forEach(function (v, i) { payload[lo + i] = v[0]; });
  }
  return { ok: true, at: nowStamp_(), days: days, items: rows.map(function (r) {
    var msgs = []; try { msgs = JSON.parse(String(payload[r.row] || '[]')) || []; } catch (e) { msgs = []; }
    var texts = linePushTexts_(msgs), first = msgs[0] || {};
    return { id: r.id, kind: r.kind, kindLabel: LINE_PUSH_KIND_[r.kind] || '其他通知', date: r.date, created: subStamp_(r.created),
      updated: subStamp_(r.updated), state: r.state, total: r.total, accepted: r.accepted, failed: r.failed, open: r.open,
      note: r.note, preview: String(first.altText || texts[0] || '').slice(0, 160), text: texts.join('\n').slice(0, 6000) };
  }) };
}
/** 某一則推送的每位收件人：名稱、遮蔽後的帳號、狀態、送達時間、錯誤。 */
function apiAdminLinePushDetail(key, id) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  id = String(id || '');
  if (!id || id.length > 160) { return { ok: false, reason: '訊息編號不對。' }; }
  var led = lineLedgerFor_(id), names = {};
  try { names = lineDisplayNames_(led.rows.map(function (x) { return x.uid; }), 25); } catch (e) { names = {}; }
  return { ok: true, rows: led.rows.map(function (x) {
    var v = led.vals[x.i] || [];
    return { name: names[x.uid] || '', id: lineMask_(x.uid), state: x.state, attempts: x.attempt, error: x.err,
      at: subStamp_(v[8]) || '', updated: subStamp_(v[7]) || '' };
  }) };
}

/** 停止對這個 LINE 帳號推送：每日總覽與盤中通知都關。後台只能關；要再開由本人在 LINE 按訂閱（盤中通知需要本人同意）。 */
function apiAdminLineStop(key, uid) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  uid = String(uid || '');
  if (!/^U[0-9a-f]{32}$/.test(uid)) { return { ok: false, reason: '帳號格式不對。' }; }
  if (!lineSubsRead_().rows.some(function (s) { return s.uid === uid; })) { return { ok: false, reason: '找不到這個 LINE 帳號。' }; }
  var r = lineUpsertSub_(uid, '', function (s) { s.daily = false; s.sms = false; });
  try { getSheet_('系統狀態').appendRow([nowStamp_(), '訂閱管理', '後台停止 LINE 推送 ' + lineMask_(uid), 'admin', 'Apps Script']); } catch (e) {}
  return { ok: true, before: r.before };
}

/** mode：cancel（停止寄送，可復原）／restore（恢復）／delete（刪除訂閱列；寄送帳本保留）。 */
function apiAdminSubscriberRemove(key, email, mode) {
  try { adminAuth_(key); } catch (e) { return { ok: false, reason: String(e.message || e) }; }
  email = String(email || '').trim().toLowerCase();
  if (email.indexOf('@') < 1) { return { ok: false, reason: 'Email 不完整。' }; }
  if (['cancel', 'restore', 'delete'].indexOf(mode) < 0) { return { ok: false, reason: '不支援的操作。' }; }
  var res = withLock_(function () {
    var sh = getSheet_('使用者訂閱清單'), vals = sh.getDataRange().getValues(), hit = [];
    for (var i = 1; i < vals.length; i++) { if (String(vals[i][0]).trim().toLowerCase() === email) { hit.push(i + 1); } }
    if (!hit.length) { return { ok: false, reason: '找不到這個 Email 的訂閱。' }; }
    if (mode === 'delete') { hit.reverse().forEach(function (row) { sh.deleteRow(row); }); }
    else { hit.forEach(function (row) { sh.getRange(row, 6).setValue(mode === 'cancel' ? '已取消' : '生效'); }); }
    return { ok: true, rows: hit.length };
  });
  if (res.ok) {
    var word = { cancel: '取消', restore: '恢復', delete: '刪除' }[mode];
    try { getSheet_('系統狀態').appendRow([nowStamp_(), '訂閱管理', '後台' + word + '訂閱 ' + subMaskEmail_(email), 'admin', 'Apps Script']); } catch (e) {}
    res.mode = mode;
  }
  return res;
}
