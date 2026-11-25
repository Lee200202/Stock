/**
 * 主資料分卷（2026/11/25 管理者定案：主試算表爆量時整本換新，舊表保留只供讀取）。
 *
 * 11/25 主試算表被 Google 回「This document is too large to continue editing」，當天逐字稿寫不進去、資料卡住。
 * 之後的處理順序：
 *   一、先清空白欄：每張分頁表頭右邊整欄空白、超過表頭寬度的欄刪掉（不動任何資料），再重寫一次。
 *   二、還是寫不進去 → 整本換到事先建好的「備用卷」：
 *       - 設定、訂閱、持股、每日內容、LINE 帳本等分頁整張複製到新卷（逐張讀回核對）；
 *       - 日K快取、小時K、使用紀錄三張累積表不複製：舊資料留在舊卷，新資料寫在新卷；
 *         讀取時舊卷在前、新卷在後合併，欄位依新卷（使用中）的表頭按欄名對齊，同一筆以新卷為準。
 *       - 舊卷保留不刪、不再寫入；SPREADSHEET_ID 指向新卷，GitHub 端向 Apps Script 查詢目前使用中的一卷。
 * 備用卷由 Apps Script 以擁有者帳號建立在擁有者的雲端硬碟，分享給既有服務帳號編輯；ID 只存在指令碼屬性，不寫進 repo。
 * 屬性 MAIN_VOLUMES：{ chain:[{id,until,reason}]（舊卷，舊到新）, standby:{id,at,volume}, switching:{from,to,done:[]}, log:[] }。
 */
var MAIN_SPLIT_TABS_ = { '日K快取': ['代號', '日期'], '小時K': ['日期', '時段', '代號'], '使用紀錄': null };
var MAIN_VOLUME_PROP_ = 'MAIN_VOLUMES';
var MAIN_VOLUME_FOLDER_PROP_ = 'MAIN_VOLUME_FOLDER_ID';
var MAIN_VOLUME_COL_SPARE_ = 4;        // 清空白欄時表頭右邊多留幾欄，日後補欄位（ensureHeaders_）才有位置
var _MAIN_ARCHIVES_ = null;            // 同一次執行內開過的舊卷
var MAIN_SWITCH_TRIED_ = false;        // 同一次執行只自動處理一次，避免反覆

function mainVolumeState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(MAIN_VOLUME_PROP_);
  var s = {};
  try { s = JSON.parse(raw || '{}') || {}; } catch (e) { s = {}; }
  s.chain = Array.isArray(s.chain) ? s.chain : [];
  s.log = Array.isArray(s.log) ? s.log : [];
  return s;
}
function mainVolumeSave_(s) {
  s.log = (s.log || []).slice(-20);
  PropertiesService.getScriptProperties().setProperty(MAIN_VOLUME_PROP_, JSON.stringify(s));
}
function mainVolumeIsSplit_(name) { return Object.prototype.hasOwnProperty.call(MAIN_SPLIT_TABS_, name); }
function mainVolumeMask_(id) { id = String(id || ''); return id ? id.slice(0, 4) + '…' + id.slice(-4) : ''; }
function mainVolumeTooLarge_(e) {
  var m = String((e && (e.message || e)) || '');
  // 只認 Google 試算表本身的容量錯誤；不能連 UrlFetch 的「payload too large」也算進來（會誤觸換卷）。
  return /too large to continue editing|above the limit of [\d,]+ cells|文件(?:太大|過大).{0,8}無法繼續編輯|儲存格.{0,12}超過.{0,24}上限|超過.{0,24}儲存格.{0,6}上限/i.test(m);
}

/** 舊卷（舊到新）。只有累積表的讀取會用到。 */
function mainArchives_() {
  if (_MAIN_ARCHIVES_) { return _MAIN_ARCHIVES_; }
  _MAIN_ARCHIVES_ = mainVolumeState_().chain.map(function (v) { return SpreadsheetApp.openById(v.id); });
  return _MAIN_ARCHIVES_;
}
/** 讀某張分頁要讀哪幾卷：累積表是 [舊卷…, 使用中]，其他分頁只讀使用中。 */
function mainVolumeSheets_(name, activeSheet) {
  var out = [];
  if (mainVolumeIsSplit_(name)) {
    mainArchives_().forEach(function (book) { var sh = book.getSheetByName(name); if (sh) { out.push(sh); } });
  }
  out.push(activeSheet || getSheet_(name));
  return out;
}
function mainVolumeTrimHead_(row) { return (row || []).map(function (h) { return String(h).trim(); }); }

/**
 * 合併讀取。回傳 { values:[表頭, 列…], display:{欄名:[每列顯示值]} }，與 getDataRange().getValues() 同形。
 * 沒有舊卷時就是原本的單卷讀取（行為完全不變）。
 * 有舊卷時：欄位以使用中一卷的表頭為準按欄名對齊（舊卷缺的欄補空白），整列空白略過；
 * 有鍵的累積表（日K快取：代號＋日期；小時K：日期＋時段＋代號）同一筆以較新的一卷為準。
 */
function readVolumeValues_(name, displayCols, opts) {
  opts = opts || {};
  var sheets = opts.sheets || mainVolumeSheets_(name);
  displayCols = displayCols || [];
  if (sheets.length === 1 && !opts.merge) {
    var sh = sheets[0], vals = sh.getDataRange().getValues(), disp = {};
    var h1 = mainVolumeTrimHead_(vals[0]);
    displayCols.forEach(function (c) {
      var j = h1.indexOf(c);
      disp[c] = (j >= 0 && vals.length > 1) ? sh.getRange(2, j + 1, vals.length - 1, 1).getDisplayValues().map(function (r) { return r[0]; }) : [];
    });
    return { values: vals, display: disp };
  }
  var active = sheets[sheets.length - 1];
  var head = mainVolumeTrimHead_(active.getRange(1, 1, 1, Math.max(1, active.getLastColumn())).getValues()[0]);
  var keyCols = MAIN_SPLIT_TABS_[name] || null;
  var want = displayCols.slice();
  (keyCols || []).forEach(function (c) { if (want.indexOf(c) < 0) { want.push(c); } });
  var rows = [], shown = {}, at = {};
  displayCols.forEach(function (c) { shown[c] = []; });
  sheets.forEach(function (sh) {
    var v = sh.getDataRange().getValues();
    if (v.length < 2) { return; }
    var h = mainVolumeTrimHead_(v[0]);
    var map = head.map(function (c) { return h.indexOf(c); });
    var dsp = {};
    want.forEach(function (c) {
      var j = h.indexOf(c);
      dsp[c] = j >= 0 ? sh.getRange(2, j + 1, v.length - 1, 1).getDisplayValues() : null;
    });
    for (var i = 1; i < v.length; i++) {
      var src = v[i];
      if (!src.some(function (x) { return x !== '' && x !== null; })) { continue; }
      var row = map.map(function (j) { return j >= 0 ? src[j] : ''; });
      var key = keyCols ? keyCols.map(function (c) { return dsp[c] ? String(dsp[c][i - 1][0]).trim() : ''; }).join('|') : null;
      var pos = rows.length;
      if (key !== null && Object.prototype.hasOwnProperty.call(at, key)) { pos = at[key]; }
      rows[pos] = row;
      if (key !== null) { at[key] = pos; }
      displayCols.forEach(function (c) { shown[c][pos] = dsp[c] ? dsp[c][i - 1][0] : ''; });
    }
  });
  return { values: [head].concat(rows), display: shown };
}

/** 使用紀錄這類只讀最後 N 列的地方：使用中一卷不足 N 列時，從最新的舊卷尾端補足（欄位依使用中表頭對齊）。 */
function readVolumeTail_(name, n, width) {
  var sheets = mainVolumeSheets_(name), out = [], total = 0;
  sheets.forEach(function (sh) { total += Math.max(0, sh.getLastRow() - 1); });
  for (var k = sheets.length - 1; k >= 0 && out.length < n; k--) {
    var sh = sheets[k], last = sh.getLastRow(), take = Math.min(n - out.length, Math.max(0, last - 1));
    if (!take) { continue; }
    var lastCol = Math.max(1, sh.getLastColumn());
    var h = mainVolumeTrimHead_(sh.getRange(1, 1, 1, lastCol).getValues()[0]);
    var vals = sh.getRange(last - take + 1, 1, take, lastCol).getValues();
    if (k !== sheets.length - 1) {
      var head = mainVolumeTrimHead_(sheets[sheets.length - 1].getRange(1, 1, 1, width).getValues()[0]);
      var map = head.map(function (c) { return h.indexOf(c); });
      vals = vals.map(function (r) { return map.map(function (j) { return j >= 0 ? r[j] : ''; }); });
    } else {
      vals = vals.map(function (r) { var x = r.slice(0, width); while (x.length < width) { x.push(''); } return x; });
    }
    out = vals.concat(out);
  }
  out.total = total;   // 各卷合計列數（呼叫端判斷是否只讀了尾端）
  return out;
}

/* ---------- 備用卷 ---------- */

/** 只沿用原本就能編輯主表的服務帳號（主表或任一舊卷的編輯者），不接受新的帳號。 */
function mainVolumeServiceOk_(email) {
  email = String(email || '');
  if (!/^[^@]+@[^@]+\.iam\.gserviceaccount\.com$/.test(email)) { return false; }
  var ids = [PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID')].concat(mainVolumeState_().chain.map(function (v) { return v.id; }));
  return ids.some(function (id) {
    try { return SpreadsheetApp.openById(id).getEditors().some(function (u) { return u.getEmail() === email; }); } catch (e) { return false; }
  });
}
/** 確認某一卷已分享給服務帳號編輯（沒有就補上）。 */
function mainVolumeShare_(id, email) {
  if (!id || !email || !mainVolumeServiceOk_(email)) { return false; }
  var book = SpreadsheetApp.openById(id);
  if (!book.getEditors().some(function (u) { return u.getEmail() === email; })) { book.addEditor(email); }
  return true;
}
function mainVolumeFolder_() {
  var p = PropertiesService.getScriptProperties(), id = p.getProperty(MAIN_VOLUME_FOLDER_PROP_);
  if (id) { try { return DriveApp.getFolderById(id); } catch (e) { /* 被移走或刪掉就重建 */ } }
  var f = DriveApp.createFolder('盤勢有據－主資料分卷');
  p.setProperty(MAIN_VOLUME_FOLDER_PROP_, f.getId());
  return f;
}
/** 建立（或沿用）備用卷，並把每張分頁的表頭對齊到使用中的一卷。serviceEmail 有給就分享給它編輯。 */
function mainVolumeStandby_(serviceEmail) {
  var s = mainVolumeState_(), book = null, created = false;
  if (s.standby && s.standby.id) { try { book = SpreadsheetApp.openById(s.standby.id); } catch (e) { book = null; } }
  if (!book) {
    var src = getSS_(), n = s.chain.length + 2;
    book = SpreadsheetApp.create('盤勢有據－主資料 第 ' + n + ' 卷（備用）');
    DriveApp.getFileById(book.getId()).moveTo(mainVolumeFolder_());
    book.setSpreadsheetTimeZone(src.getSpreadsheetTimeZone() || 'Asia/Taipei');
    s.standby = { id: book.getId(), at: nowStamp_(), volume: n };
    mainVolumeSave_(s);
    created = true;
  }
  var shared = serviceEmail ? mainVolumeShare_(book.getId(), serviceEmail) : false;
  var align = mainVolumeAlign_(getSS_(), book);
  return { ok: true, created: created, shared: shared, standby: mainVolumeMask_(book.getId()), volume: s.standby.volume, align: align };
}
/** 讓 dst 的每張分頁都有 src 同名分頁與同一列表頭。dst 已有資料列的分頁表頭不同時只回報、不改。 */
function mainVolumeAlign_(src, dst) {
  var names = {}, made = [], fixed = [], mismatch = [];
  src.getSheets().forEach(function (sh) {
    var name = sh.getName(); names[name] = 1;
    var width = Math.max(1, sh.getLastColumn());
    var head = sh.getRange(1, 1, 1, width).getValues()[0];
    while (head.length && String(head[head.length - 1]).trim() === '') { head.pop(); }
    if (!head.length) { head = ['']; }
    var d = dst.getSheetByName(name);
    if (!d) {
      d = dst.insertSheet(name);
      made.push(name);
    }
    var dw = Math.max(1, d.getLastColumn()), dh = mainVolumeTrimHead_(d.getRange(1, 1, 1, dw).getValues()[0]);
    while (dh.length && dh[dh.length - 1] === '') { dh.pop(); }
    var want = mainVolumeTrimHead_(head);
    if (dh.join('\u0001') === want.join('\u0001')) { return; }
    if (d.getLastRow() > 1) { mismatch.push(name); return; }
    if (d.getMaxColumns() < head.length) { d.insertColumnsAfter(d.getMaxColumns(), head.length - d.getMaxColumns()); }
    d.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold').setBackground('#EDF0EE');
    d.setFrozenRows(1);
    var extra = d.getMaxColumns() - (head.length + MAIN_VOLUME_COL_SPARE_);
    if (extra > 0) { d.deleteColumns(head.length + MAIN_VOLUME_COL_SPARE_ + 1, extra); }
    if (d.getMaxRows() > 200) { d.deleteRows(201, d.getMaxRows() - 200); }
    if (made.indexOf(name) < 0) { fixed.push(name); }
  });
  dst.getSheets().forEach(function (d) {   // 建立時 Google 自動附的空白分頁
    if (!names[d.getName()] && d.getLastRow() === 0 && dst.getSheets().length > 1) { dst.deleteSheet(d); }
  });
  return { tabs: Object.keys(names).length, created: made.length, fixed: fixed, mismatch: mismatch };
}

/* ---------- 一、清空白欄（不動資料） ---------- */

/** 每張分頁超過「最後一欄有資料＋保留欄」的整欄空白刪掉。preview 只回報可省多少格。 */
function mainVolumeTrim_(preview) {
  var ss = getSS_(), saved = 0, tabs = [];
  ss.getSheets().forEach(function (sh) {
    var keep = Math.max(1, sh.getLastColumn()) + MAIN_VOLUME_COL_SPARE_, extra = sh.getMaxColumns() - keep;
    if (extra <= 0) { return; }
    var cells = extra * sh.getMaxRows();
    saved += cells; tabs.push({ name: sh.getName(), columns: extra, cells: cells });
    if (!preview) { sh.deleteColumns(keep + 1, extra); }
  });
  tabs.sort(function (a, b) { return b.cells - a.cells; });
  return { ok: true, preview: !!preview, cells: saved, tabs: tabs.slice(0, 12) };
}

/* ---------- 二、整本換新 ---------- */

/** 整本換到備用卷。可分次執行：每次在時間預算內複製幾張，全部讀回核對後才切換 SPREADSHEET_ID。 */
function mainVolumeSwitch_(reason, budgetMs) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) { return { ok: false, busy: true, error: '分卷切換正在進行，稍後再查' }; }
  try {
    var p = PropertiesService.getScriptProperties(), s = mainVolumeState_(), fromId = p.getProperty('SPREADSHEET_ID');
    if (!s.standby || !s.standby.id) { mainVolumeStandby_(''); s = mainVolumeState_(); }
    var src = SpreadsheetApp.openById(fromId), dst = SpreadsheetApp.openById(s.standby.id);
    if (!s.switching || s.switching.from !== fromId || s.switching.to !== dst.getId()) {
      mainVolumeAlign_(src, dst);
      s.switching = { from: fromId, to: dst.getId(), started: nowStamp_(), reason: String(reason || '').slice(0, 200), done: [] };
      mainVolumeSave_(s);
    }
    var deadline = Date.now() + (Number(budgetMs) || 240000), pending = [];
    src.getSheets().forEach(function (sh) {
      var name = sh.getName();
      if (mainVolumeIsSplit_(name) || s.switching.done.indexOf(name) >= 0) { return; }
      if (Date.now() > deadline) { pending.push(name); return; }
      mainVolumeCopyTab_(sh, dst);
      s.switching.done.push(name);
      mainVolumeSave_(s);
    });
    if (pending.length) { return { ok: true, switched: false, copied: s.switching.done.length, pending: pending.length }; }
    // 全部核對完才切換
    var stamp = nowStamp_();
    s.chain.push({ id: fromId, until: stamp, reason: s.switching.reason });
    p.setProperty('SPREADSHEET_ID', dst.getId());
    s.log.push({ at: stamp, from: mainVolumeMask_(fromId), to: mainVolumeMask_(dst.getId()), tabs: s.switching.done.length, reason: s.switching.reason });
    var volume = s.standby.volume;
    s.standby = null; s.switching = null;
    mainVolumeSave_(s);
    _SS_OPEN_ = {}; _MAIN_ARCHIVES_ = null;
    try { DriveApp.getFileById(dst.getId()).setName('盤勢有據－主資料 第 ' + volume + ' 卷（使用中，' + stamp.slice(0, 10) + ' 起）'); } catch (e) {}
    try { DriveApp.getFileById(fromId).setName('盤勢有據－主資料 舊卷（唯讀，至 ' + stamp.slice(0, 10) + '）'); } catch (e) {}
    try { getSheet_('系統狀態').appendRow([stamp, '警告', '主試算表寫不進去，已整本換到第 ' + volume + ' 卷；舊卷保留只供讀取。原因：' + String(reason || '').slice(0, 120), 'main-volume', 'Apps Script']); } catch (e) {}
    mainVolumeAlert_('主資料試算表已換到第 ' + volume + ' 卷（' + stamp + '）。原本的試算表寫不進去（' + String(reason || '').slice(0, 80) +
      '），設定與每日資料已複製並核對；日K、小時K、使用紀錄的舊資料留在舊卷，網站照常合併讀取。');
    return { ok: true, switched: true, volume: volume, tabs: s.log[s.log.length - 1].tabs };
  } finally { lock.releaseLock(); }
}
/** 一張分頁整張複製到新卷，讀回核對（列數、欄數與所有顯示值的指紋）後才算完成。 */
function mainVolumeCopyTab_(sh, dst) {
  var name = sh.getName(), old = dst.getSheetByName(name);
  var copy = sh.copyTo(dst);
  if (old) { dst.deleteSheet(old); }
  copy.setName(name);
  var lastRow = sh.getLastRow(), lastCol = sh.getLastColumn();
  var spareRows = copy.getMaxRows() - Math.max(lastRow + 1000, 200);
  if (spareRows > 0 && name !== '盤中快照') { copy.deleteRows(copy.getMaxRows() - spareRows + 1, spareRows); }
  var spareCols = copy.getMaxColumns() - (Math.max(1, lastCol) + MAIN_VOLUME_COL_SPARE_);
  if (spareCols > 0) { copy.deleteColumns(copy.getMaxColumns() - spareCols + 1, spareCols); }
  if (lastRow && lastCol) {
    var a = mainVolumeDigest_(sh.getRange(1, 1, lastRow, lastCol).getDisplayValues());
    var b = mainVolumeDigest_(copy.getRange(1, 1, lastRow, lastCol).getDisplayValues());
    if (a !== b || copy.getLastRow() !== lastRow) { throw new Error('分頁「' + name + '」複製後讀回不一致，未切換'); }
  }
}
function mainVolumeDigest_(rows) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, rows.map(function (r) { return r.join('\u0001'); }).join('\u0002'), Utilities.Charset.UTF_8);
  return bytes.map(function (b) { return ('0' + (b & 255).toString(16)).slice(-2); }).join('');
}
function mainVolumeAlert_(text) {
  try {
    if (typeof lineAdminUids_ !== 'function' || typeof lineApi_ !== 'function') { return; }
    lineAdminUids_(true).forEach(function (uid) {
      lineApi_('post', '/v2/bot/message/push', { to: uid, messages: [{ type: 'text', text: text }] }, { retryKey: Utilities.getUuid() });
    });
  } catch (e) { Logger.log('分卷通知送不出去：' + e); }
}

/**
 * 寫入撞到「檔案太大」時的自動處理（opsTimed_ 與 GitHub 端都走這裡）。
 * 先清空白欄；清完 retry 由呼叫端做。回傳 { step:'trim'|'switch', ... }。
 */
function mainVolumeRelieve_(reason, alreadyTrimmed) {
  if (!alreadyTrimmed) {
    try {
      var t = mainVolumeTrim_(false);
      if (t.cells > 0) { return { ok: true, step: 'trim', cells: t.cells }; }
    } catch (e) { Logger.log('清空白欄失敗，改換新卷：' + e); }
  }
  var r = { ok: true, switched: false };
  for (var i = 0; i < 3 && r.ok && !r.switched; i++) { r = mainVolumeSwitch_(reason, 120000); }
  r.step = 'switch';
  return r;
}

/** GitHub 端與後台用的狀態（ID 一律遮蔽，只回給持有管理密鑰的請求）。 */
function mainVolumeStatus_() {
  var s = mainVolumeState_();
  return { ok: true, active: mainVolumeMask_(PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID')),
    archives: s.chain.map(function (v) { return { id: mainVolumeMask_(v.id), until: v.until }; }),
    standby: s.standby ? { id: mainVolumeMask_(s.standby.id), at: s.standby.at, volume: s.standby.volume } : null,
    switching: s.switching ? { done: s.switching.done.length, started: s.switching.started } : null,
    log: s.log.slice(-5), splitTabs: Object.keys(MAIN_SPLIT_TABS_) };
}

/**
 * GitHub 端入口（doGet action=backup-archive，op=main-*，持管理密鑰）。source 是 GitHub secret 的 SPREADSHEET_ID：
 * 必須是使用中或舊卷之一，才回目前使用中的完整 ID（GitHub 不必改 secret 就能跟著換卷）。
 */
function mainVolumeRequest_(op, opts, source) {
  var p = PropertiesService.getScriptProperties(), s = mainVolumeState_(), active = p.getProperty('SPREADSHEET_ID');
  var known = [active].concat(s.chain.map(function (v) { return v.id; }));
  if (!source || known.indexOf(source) < 0) { throw new Error('來源試算表不在分卷清單內'); }
  opts = opts || {};
  if (op === 'main-route') {
    // 換卷後第一次查詢時，確認新卷與備用卷都分享給這個服務帳號（只看分享，不重新對齊表頭）
    if (opts.serviceEmail) {
      try { mainVolumeShare_(active, opts.serviceEmail); } catch (e) {}
      if (s.standby && s.standby.id) { try { mainVolumeShare_(s.standby.id, opts.serviceEmail); } catch (e) {} }
    }
    return { ok: true, active: active, archives: s.chain.map(function (v) { return v.id; }),
      standby: s.standby ? s.standby.id : '', switching: !!s.switching };
  }
  if (op === 'main-status') { return mainVolumeStatus_(); }
  if (op === 'main-standby') { return mainVolumeStandby_(opts.serviceEmail || ''); }
  if (op === 'main-trim-preview') { return mainVolumeTrim_(true); }
  if (op === 'main-relieve') {
    if (source !== active) { return { ok: true, step: 'already', active: active }; }   // 別的工作已經換過卷
    var r = mainVolumeRelieve_(String(opts.reason || 'GitHub 寫入失敗'), opts.trimmed === true);
    r.active = p.getProperty('SPREADSHEET_ID');
    if (opts.serviceEmail && r.active !== active) { try { mainVolumeShare_(r.active, opts.serviceEmail); } catch (e) {} }
    return r;
  }
  if (op === 'main-selftest') { return mainVolumeSelftest_(opts); }
  throw new Error('不認得的分卷動作');
}

/**
 * 實際驗收（不影響正式資料）：以「主表＝舊卷、備用卷＝新卷」的組合跑一次合併讀取。
 * 在備用卷的累積表寫一列測試資料 → 合併讀取應得到主表全部列＋這一列、欄位對齊 → 刪掉測試列。
 */
function mainVolumeSelftest_(opts) {
  var s = mainVolumeState_();
  if (!s.standby || !s.standby.id) { throw new Error('還沒有備用卷'); }
  var main = getSS_(), book = SpreadsheetApp.openById(s.standby.id), out = { ok: true, tabs: [] };
  out.align = mainVolumeAlign_(main, book);
  // 測試列只填一個文字標記欄，不寫日期（日期字串會被試算表轉成日期值，比對不準）
  [['使用紀錄', '頁面'], ['日K快取', '代號']].forEach(function (pair) {
    var name = pair[0], markCol = pair[1];
    var oldSh = main.getSheetByName(name), newSh = book.getSheetByName(name);
    if (!oldSh || !newSh) { out.ok = false; out.tabs.push({ name: name, error: '缺分頁' }); return; }
    var head = mainVolumeTrimHead_(newSh.getRange(1, 1, 1, newSh.getLastColumn()).getValues()[0]);
    var mc = head.indexOf(markCol), mark = 'VOLTEST' + String(Date.now()).slice(-6);
    if (mc < 0) { out.ok = false; out.tabs.push({ name: name, error: '缺「' + markCol + '」欄' }); return; }
    var expect = readVolumeValues_(name, [], { sheets: [oldSh], merge: true }).values.length - 1;
    var test = head.map(function (h, j) { return j === mc ? mark : ''; });
    var at = newSh.getLastRow() + 1;
    newSh.getRange(at, 1, 1, head.length).setValues([test]);
    try {
      var merged = readVolumeValues_(name, [], { sheets: [oldSh, newSh] }).values;
      var row = { name: name, oldRows: expect, mergedRows: merged.length - 1, headAligned: merged[0].join('|') === head.join('|'),
        lastIsTest: String(merged[merged.length - 1][mc]) === mark };
      row.ok = row.headAligned && row.lastIsTest && row.mergedRows === expect + 1;
      if (!row.ok) { out.ok = false; }
      out.tabs.push(row);
    } finally { newSh.deleteRow(at); }
  });
  out.cleaned = book.getSheetByName('使用紀錄').getLastRow() <= 1 && book.getSheetByName('日K快取').getLastRow() <= 1;
  return out;
}
