// 2026/11/25 主資料分卷（不連 Google）：node scripts/check_main_volume.js
// 一、11/25 的失敗型態（主表每次寫入都回 This document is too large to continue editing）：排程不再卡住，
//     先清空白欄、仍不行就整本換到備用卷並重跑，資料寫進新卷；設定類分頁整張帶過去，累積表舊資料留在舊卷。
// 二、合併讀取：舊卷在前、新卷在後，欄位依使用中表頭按欄名對齊，同一筆以新卷為準。
// 三、不相干的「too large」（UrlFetch payload）不觸發換卷。
// 四、只清空白欄就寫得進去時不換卷。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const crypto = require('crypto');
const assert = require('assert');

const GAS = path.join(__dirname, '..', 'public-site', 'gas-source');
const mainSrc = fs.readFileSync(path.join(GAS, 'Mainvolume.gs'), 'utf8');
const setup = fs.readFileSync(path.join(GAS, 'Setup.gs'), 'utf8').replace(/\r\n/g, '\n');
const opsTimed = setup.slice(setup.indexOf('function opsTimed_('), setup.indexOf('function opsRuntimeAdd_('));
const rawReader = setup.slice(setup.indexOf('function readSheetObjectsRaw_('), setup.indexOf('/** v88'));
assert(opsTimed.includes('mainVolumeRelieve_'), 'opsTimed_ 沒有接上分卷處理');
assert(rawReader.includes('readVolumeValues_'), 'readSheetObjectsRaw_ 沒有接上合併讀取');

// vm 內建立的陣列與外面不同 realm，先轉成一般 JSON 再比
const eq = (a, b, m) => assert.deepStrictEqual(JSON.parse(JSON.stringify(a)), JSON.parse(JSON.stringify(b)), m);
const TOO_LARGE = 'This document is too large to continue editing.';

function makeSheet(book, name, rows, maxCols) {
  const sh = {
    _rows: rows.map(r => r.slice()), _maxRows: Math.max(rows.length + 50, 100), _maxCols: maxCols || 26, _name: name, _book: book,
    getName: () => sh._name, setName(n) { sh._name = n; return sh; },
    getLastRow: () => { for (let i = sh._rows.length - 1; i >= 0; i--) { if (sh._rows[i].some(x => x !== '' && x != null)) return i + 1; } return 0; },
    getLastColumn: () => sh._rows.reduce((m, r) => { let k = r.length; while (k > 0 && (r[k - 1] === '' || r[k - 1] == null)) k--; return Math.max(m, k); }, 0),
    getMaxRows: () => sh._maxRows, getMaxColumns: () => sh._maxCols,
    getRange(r, c, nr, nc) { return range(sh, r, c, nr || 1, nc || 1); },
    getDataRange() { return range(sh, 1, 1, Math.max(1, sh.getLastRow()), Math.max(1, sh.getLastColumn())); },
    deleteRows(at, n) { sh._book._edit(); sh._rows.splice(at - 1, n); sh._maxRows -= n; },
    deleteRow(at) { sh.deleteRows(at, 1); },
    deleteColumns(at, n) { sh._book._trim(n * sh._maxRows); sh._rows.forEach(r => r.splice(at - 1, n)); sh._maxCols -= n; },
    insertColumnsAfter(at, n) { sh._book._edit(); sh._maxCols += n; },
    setFrozenRows() {},
    copyTo(dst) { const c = makeSheet(dst, name + ' 的副本', sh._rows, sh._maxCols); c._maxRows = sh._maxRows; dst._sheets.push(c); return c; },
  };
  return sh;
}
function range(sh, r, c, nr, nc) {
  const cell = (i, j) => { const row = sh._rows[r - 1 + i] || []; const v = row[c - 1 + j]; return v == null ? '' : v; };
  const rg = {
    getValues: () => Array.from({ length: nr }, (_, i) => Array.from({ length: nc }, (_, j) => cell(i, j))),
    getDisplayValues: () => rg.getValues().map(row => row.map(v => String(v))),
    setValues(vals) {
      sh._book._edit();
      vals.forEach((row, i) => { while (sh._rows.length < r + i) sh._rows.push([]); const t = sh._rows[r - 1 + i]; row.forEach((v, j) => { t[c - 1 + j] = v; }); });
      return rg;
    },
    setFontWeight: () => rg, setBackground: () => rg, setNumberFormat: () => rg,
  };
  return rg;
}
function makeBook(id, tooLarge) {
  const b = {
    _id: id, _sheets: [], _tooLarge: !!tooLarge, _trimmed: 0, _editors: ['bot@proj.iam.gserviceaccount.com'], _fixAfterTrim: 0,
    _edit() { if (b._tooLarge) throw new Error(TOO_LARGE); },
    _trim(cells) { b._trimmed += cells; if (b._fixAfterTrim && b._trimmed >= b._fixAfterTrim) b._tooLarge = false; },
    getId: () => id, getUrl: () => 'https://docs/' + id, getSheets: () => b._sheets.slice(),
    getSheetByName: n => b._sheets.find(s => s.getName() === n) || null,
    insertSheet(n) { const s = makeSheet(b, n, []); b._sheets.push(s); return s; },
    deleteSheet(s) { b._sheets.splice(b._sheets.indexOf(s), 1); },
    getEditors: () => b._editors.map(e => ({ getEmail: () => e })), addEditor(e) { b._editors.push(e); },
    getSpreadsheetTimeZone: () => 'Asia/Taipei', setSpreadsheetTimeZone() {},
  };
  return b;
}

function world() {
  const books = {};
  const props = {};
  const alerts = [];
  const ctx = {
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = v; }, setProperties: o => Object.assign(props, o) }) },
    LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock() {} }) },
    SpreadsheetApp: { openById: id => { if (!books[id]) throw new Error('no ' + id); return books[id]; },
      create: () => { const id = 'BOOK' + (Object.keys(books).length + 1); books[id] = makeBook(id); books[id].insertSheet('工作表1'); return books[id]; } },
    DriveApp: { createFolder: () => ({ getId: () => 'F' }), getFolderById: () => ({ getId: () => 'F' }), getFileById: () => ({ moveTo() {}, setName() {} }) },
    Utilities: { computeDigest: (a, s) => Array.from(crypto.createHash('sha256').update(s, 'utf8').digest()).map(x => (x > 127 ? x - 256 : x)),
      DigestAlgorithm: { SHA_256: 1 }, Charset: { UTF_8: 1 }, getUuid: () => 'u' },
    Logger: { log() {} },
    nowStamp_: () => '2026/11/25 12:00:00',
    lineAdminUids_: () => ['Uadmin'], lineApi_: (m, p, body) => { alerts.push(body.messages[0].text); return { code: 200 }; },
    OPS_STAGE_SAMPLE_: null, opsRuntimeAdd_() {}, whyClosed_: () => '',
  };
  vm.createContext(ctx);
  vm.runInContext(`var _SS_OPEN_ = { id: '', ss: null };
    function getSS_() { var id = PropertiesService.getScriptProperties().getProperty('SPREADSHEET_ID'); if (_SS_OPEN_.ss && _SS_OPEN_.id === id) return _SS_OPEN_.ss; _SS_OPEN_ = { id: id, ss: SpreadsheetApp.openById(id) }; return _SS_OPEN_.ss; }
    function getSheet_(n) { var ss = getSS_(); return ss.getSheetByName(n) || ss.insertSheet(n); }
    var SHEET_SNAPSHOT_ = null;`, ctx);
  vm.runInContext(mainSrc, ctx);
  vm.runInContext(opsTimed, ctx);
  vm.runInContext(rawReader, ctx);
  return { ctx, books, props, alerts };
}

const DK = ['代號', '日期', '開', '高', '低', '收', '量'];
function seedMain(w, tooLarge) {
  const main = makeBook('MAIN', tooLarge);
  w.books.MAIN = main;
  main._sheets.push(makeSheet(main, '每日推播內容', [['日期', '文字稿'], ['2026/11/24', '前一天的內容']]));
  main._sheets.push(makeSheet(main, 'LINE 寄送帳本', [['訊息ID', '收件者', '狀態'], ['m1', 'U1', 'accepted']]));
  main._sheets.push(makeSheet(main, '日K快取', [DK, ['2330', '2026/11/24', 1, 2, 1, 2, 9], ['2317', '2026/11/24', 1, 1, 1, 1, 5]]));
  main._sheets.push(makeSheet(main, '使用紀錄', [['時間', '訪客識別', '頁面'], ['t1', 'v1', '/'], ['t2', 'v2', '/stock']]));
  w.props.SPREADSHEET_ID = 'MAIN';
  return main;
}

// ---------- 一、11/25 失敗型態：寫不進去 → 清空白欄 → 換卷 → 重跑成功 ----------
{
  const w = world();
  const main = seedMain(w, false);
  const r0 = w.ctx.mainVolumeStandby_('bot@proj.iam.gserviceaccount.com');
  assert.strictEqual(r0.created, true);
  assert.strictEqual(r0.shared, true, '備用卷要分享給既有服務帳號');
  const standbyId = JSON.parse(w.props.MAIN_VOLUMES).standby.id;
  const sb = w.books[standbyId];
  eq(sb.getSheets().map(s => s.getName()).sort(), main.getSheets().map(s => s.getName()).sort(), '備用卷分頁與主表一致（自動附的空白分頁已刪）');
  eq(sb.getSheetByName('日K快取').getRange(1, 1, 1, 7).getValues()[0], DK, '表頭對齊');
  main._tooLarge = true;                                   // 11/25 12:00 起主表每次寫入都被拒
  let ran = 0;
  const job = function () { ran++; getSheetWrite(); return 'done'; };
  function getSheetWrite() {
    const sh = w.ctx.getSheet_('每日推播內容');
    sh.getRange(sh.getLastRow() + 1, 1, 1, 2).setValues([['2026/11/25', '今天的內容']]);
    const k = w.ctx.getSheet_('日K快取');
    k.getRange(k.getLastRow() + 1, 1, 1, 7).setValues([['2330', '2026/11/25', 2, 3, 2, 3, 8]]);
  }
  w.ctx.job = job;
  const out = vm.runInContext("opsTimed_('dailyJob', job, [{ triggerUid: 't' }])", w.ctx);
  assert.strictEqual(out, 'done', '排程重跑後完成，不再卡住');
  assert.strictEqual(ran, 3, '原本一次＋清空白欄後一次＋換卷後一次');
  assert(main._trimmed > 0, '先清空白欄');
  assert.strictEqual(w.props.SPREADSHEET_ID, standbyId, 'SPREADSHEET_ID 換到新卷');
  const st = JSON.parse(w.props.MAIN_VOLUMES);
  eq(st.chain.map(v => v.id), ['MAIN'], '舊卷留在清單，只供讀取');
  assert.strictEqual(st.standby, null);
  const daily = sb.getSheetByName('每日推播內容').getDataRange().getValues();
  eq(daily.slice(1).map(r => r[1]), ['前一天的內容', '今天的內容'], '設定類分頁整張帶過去，今天的資料寫進新卷');
  eq(sb.getSheetByName('LINE 寄送帳本').getDataRange().getValues(), main.getSheetByName('LINE 寄送帳本').getDataRange().getValues(), '寄送帳本原樣保留');
  assert.strictEqual(sb.getSheetByName('日K快取').getLastRow(), 2, '累積表不複製：新卷只有表頭＋今天新寫的一列');
  assert.strictEqual(main.getSheetByName('日K快取').getLastRow(), 3, '舊卷資料原封不動');
  w.ctx._MAIN_ARCHIVES_ = null;
  const merged = w.ctx.readVolumeValues_('日K快取').values;
  eq(merged.slice(1).map(r => r[0] + '@' + r[1]), ['2330@2026/11/24', '2317@2026/11/24', '2330@2026/11/25'], '讀取時舊卷在前、新卷在後');
  assert.strictEqual(w.ctx.readSheetObjectsRaw_('日K快取').length, 3, 'readSheetObjects_ 也讀到兩卷');
  assert.strictEqual(w.ctx.readSheetObjectsRaw_('每日推播內容').length, 2, '非累積表只讀使用中一卷（不會重複）');
  assert.strictEqual(w.alerts.length, 1, 'LINE 通知管理者一次');
  assert(/第 2 卷/.test(w.alerts[0]));
  const usage = w.ctx.readVolumeTail_('使用紀錄', 30000, 3);
  assert.strictEqual(usage.length, 2, '使用紀錄尾端從舊卷補足');
  assert.strictEqual(usage.total, 2);
}

// ---------- 二、合併讀取：欄序不同按欄名對齊、同一筆以新卷為準 ----------
{
  const w = world();
  const old = makeBook('OLD'); w.books.OLD = old;
  old._sheets.push(makeSheet(old, '小時K', [['日期', '時段', '代號', '開', '高', '低', '收', '量'], ['2026/11/24', '09:00', '2330', 1, 1, 1, 1, 1], ['2026/11/24', '10:00', '2330', 2, 2, 2, 2, 2]]));
  const cur = makeBook('CUR'); w.books.CUR = cur;
  cur._sheets.push(makeSheet(cur, '小時K', [['代號', '日期', '時段', '開', '高', '低', '收', '量', '來源'], ['2330', '2026/11/24', '10:00', 9, 9, 9, 9, 9, 'fix'], ['2330', '2026/11/25', '09:00', 3, 3, 3, 3, 3, 'x']]));
  w.props.SPREADSHEET_ID = 'CUR';
  w.props.MAIN_VOLUMES = JSON.stringify({ chain: [{ id: 'OLD' }] });
  const m = w.ctx.readVolumeValues_('小時K', ['時段']);
  eq(m.values[0], ['代號', '日期', '時段', '開', '高', '低', '收', '量', '來源'], '表頭以使用中一卷為準');
  eq(m.values.slice(1).map(r => [r[0], r[1], r[2], r[6], r[8]]),
    [['2330', '2026/11/24', '09:00', 1, ''], ['2330', '2026/11/24', '10:00', 9, 'fix'], ['2330', '2026/11/25', '09:00', 3, 'x']],
    '舊卷缺的欄補空白；同一根（日期＋時段＋代號）以新卷為準');
  eq(m.display['時段'], ['09:00', '10:00', '09:00'], '顯示值跟著列對齊');
}

// ---------- 三、不相干的 too large 不換卷 ----------
{
  const w = world();
  seedMain(w, false);
  w.ctx.bad = () => { throw new Error('Request payload too large'); };
  assert.throws(() => vm.runInContext("opsTimed_('x', bad, [])", w.ctx), /payload too large/);
  assert.strictEqual(w.props.SPREADSHEET_ID, 'MAIN');
  assert.strictEqual(w.ctx.mainVolumeTooLarge_(new Error('This action would increase the number of cells in the workbook above the limit of 10000000 cells.')), true);
  assert.strictEqual(w.ctx.mainVolumeTooLarge_(new Error('這份文件太大，無法繼續編輯。')), true);
}

// ---------- 四、清空白欄就寫得進去：不換卷 ----------
{
  const w = world();
  const main = seedMain(w, true);
  main._fixAfterTrim = 1;
  w.ctx.job = () => { const sh = w.ctx.getSheet_('每日推播內容'); sh.getRange(3, 1, 1, 2).setValues([['2026/11/25', 'ok']]); return 'ok'; };
  assert.strictEqual(vm.runInContext("opsTimed_('dailyJob', job, [])", w.ctx), 'ok');
  assert.strictEqual(w.props.SPREADSHEET_ID, 'MAIN', '清完空白欄就好時不換卷');
  assert(!w.props.MAIN_VOLUMES || JSON.parse(w.props.MAIN_VOLUMES).chain.length === 0);
}

// ---------- 五、GitHub 入口：來源必須是已知的一卷 ----------
{
  const w = world();
  seedMain(w, false);
  assert.throws(() => w.ctx.mainVolumeRequest_('main-route', {}, 'SOMEONE'), /不在分卷清單/);
  const r = w.ctx.mainVolumeRequest_('main-route', {}, 'MAIN');
  assert.strictEqual(r.active, 'MAIN');
}
console.log('主資料分卷：11/25 寫不進去會先清空白欄、再整本換新並重跑成功；兩卷合併讀取對齊；不相干錯誤不換卷 — 全部通過');
