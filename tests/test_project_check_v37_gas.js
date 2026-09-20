// v37（2026/09/16）：Config.gs 沒載入時 doGet 說得出哪裡壞；checkProjectFiles 逐檔檢查貼齊與版本。
// 起因：更新 v36 後網站出現「ReferenceError: APP_TITLE is not defined (第 141 行，檔案名稱：Code)」。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const dir = path.resolve(__dirname, '..', 'apps-script');
const read = f => fs.readFileSync(path.join(dir, f), 'utf8');
const GS = fs.readdirSync(dir).filter(f => f.endsWith('.gs')).sort();
const HTML = fs.readdirSync(dir).filter(f => f.endsWith('.html')).map(f => f.slice(0, -5)).sort();

// 模擬一個 Apps Script 專案：files 是 {檔名: 內容}，html 是 {名稱: 內容}
function project(files, html) {
  const stub = () => new Proxy(function () {}, { get: (t, k) => (k === Symbol.toPrimitive ? () => '' : k === 'then' ? undefined : stub()), apply: () => stub() });
  const out = {};
  const chain = o => Object.assign(o, { setTitle: v => { out.title = v; return o; }, addMetaTag: () => o, setXFrameOptionsMode: () => o, setMimeType: () => o });
  const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN, parseInt, parseFloat,
    encodeURIComponent, decodeURIComponent, Error, Infinity,
    Logger: { log: m => { out.log = String(m); } },
    SpreadsheetApp: stub(), PropertiesService: stub(), CacheService: stub(), LockService: stub(), UrlFetchApp: stub(),
    Utilities: stub(), MailApp: stub(), Session: stub(), DriveApp: stub(), GmailApp: stub(),
    ScriptApp: { getService: () => ({ getUrl: () => 'https://script.google.com/macros/s/X/exec' }), getScriptId: () => 'x', getProjectTriggers: () => [] },
    ContentService: { createTextOutput: t => chain({ kind: 'json', text: t }), MimeType: { JSON: 'json' } },
    HtmlService: {
      XFrameOptionsMode: { ALLOWALL: 1 },
      createHtmlOutput: h => chain({ kind: 'html', html: h }),
      createHtmlOutputFromFile: n => ({ getContent: () => html[n] || '' }),
      createTemplateFromFile: n => {
        if (!(n in html)) { throw new Error('找不到檔案 ' + n); }
        const t = { getRawContent: () => html[n], evaluate: () => chain({ kind: 'page', template: n, t }) };
        return t;
      }
    }
  });
  const loadErrors = [];
  for (const [name, src] of Object.entries(files)) {
    try { vm.runInContext(src, ctx, { filename: name }); } catch (e) { loadErrors.push(name + ': ' + e.message); }
  }
  assert.deepStrictEqual(loadErrors, [], '載入不應出錯');
  return { ctx, out };
}
const allGs = () => Object.fromEntries(GS.map(f => [f, read(f)]));
const allHtml = () => Object.fromEntries(HTML.map(n => [n, read(n + '.html')]));

// ---------------------------------------------------------------- 一、檢查表與實際檔案一致
const setup = read('Setup.gs');
const tbl = project({ 'Setup.gs': setup }, {}).ctx;   // 只讀表，不呼叫
const listedGs = Array.from(tbl.PROJECT_FILES_, f => f.file).sort();
assert.deepStrictEqual(listedGs, GS, '每個 .gs 都要在 PROJECT_FILES_ 上（新增檔案要一起加）');
assert.deepStrictEqual(Array.from(tbl.PROJECT_HTML_, h => h.file).sort(), HTML, '每個 .html 都要在 PROJECT_HTML_ 上');
for (const f of tbl.PROJECT_FILES_) {
  const src = read(f.file);
  for (const n of f.names)
    assert(new RegExp('^(?:function ' + n + '\\s*\\(|var ' + n + '\\b)', 'm').test(src), f.file + ' 沒有宣告 ' + n);
  if (f.marker) {
    // marker 可以指向函式或頂層 var（提示詞字串）；checkProjectFiles 執行時用 String(全域[名稱]) 比對，兩種都成立。
    const at = src.search(new RegExp('^(?:function ' + f.marker[0] + '\\s*\\(|var ' + f.marker[0] + '\\b)', 'm'));
    assert(at >= 0, f.file + ' 沒有 ' + f.marker[0]);
    const ends = ['\nfunction ', '\nvar '].map(s => src.indexOf(s, at + 10)).filter(i => i >= 0);
    const body = src.slice(at, ends.length ? Math.min(...ends) : src.length);
    assert(body.includes(f.marker[1]), f.file + ' 的 ' + f.marker[0] + ' 裡沒有「' + f.marker[1] + '」');
  }
}
for (const h of tbl.PROJECT_HTML_) if (h.marker) assert(read(h.file + '.html').includes(h.marker), h.file + '.html 沒有 marker');
assert.strictEqual(tbl.PROJECT_BUILD_, read('Config.gs').match(/var GAS_BUILD = '([^']+)'/)[1], 'PROJECT_BUILD_ 要等於 GAS_BUILD');

// ---------------------------------------------------------------- 二、完整專案：檢查全數通過、網站正常
let p = project(allGs(), allHtml());
let r = p.ctx.checkProjectFiles();
assert.strictEqual(r.ok, true, '完整專案不應有問題：' + JSON.stringify(r.problems));
assert(/全部正常/.test(p.out.log));
let page = p.ctx.doGet({ parameter: {} });
assert.strictEqual(page.kind, 'page');
assert.strictEqual(page.t.appTitle, '張震股市盤中家教班　逐日追蹤');

// ---------------------------------------------------------------- 三、Config.gs 被貼成別的內容（這次的狀況）
let files = allGs();
files['Config.gs'] = read('DB.gs').replace(/function (\w+)/g, 'function __copy_$1');   // 貼成別的檔案，名稱改掉避免覆蓋
p = project(files, allHtml());
page = p.ctx.doGet({ parameter: {} });                        // 以前這裡丟 ReferenceError: APP_TITLE is not defined
assert.strictEqual(page.kind, 'html');
assert(/網站暫時無法顯示/.test(page.html) && /Config\.gs/.test(page.html) && /APP_TITLE/.test(page.html) && /checkProjectFiles/.test(page.html));
assert(!/<script/i.test(page.html));
page = p.ctx.doGet({ parameter: { action: 'unsubscribe', email: 'a@b.co', token: 't' } });
assert.strictEqual(page.kind, 'html', '退訂連結也不能變成一頁錯誤');
for (const action of ['ping', 'refresh']) {
  const res = p.ctx.doGet({ parameter: { action } });
  assert.strictEqual(res.kind, 'json');
  const j = JSON.parse(res.text);
  assert.strictEqual(j.ok, false); assert.strictEqual(j.error, 'config-missing');
  assert(j.missing.includes('APP_TITLE') && j.missing.includes('GAS_BUILD'));
}
r = p.ctx.checkProjectFiles();
assert.strictEqual(r.ok, false);
const cfg = r.problems.find(x => x.file === 'Config.gs');
assert(cfg && /整個檔案沒有載入/.test(cfg.reason) && /APP_TITLE/.test(cfg.reason));
assert(/✗ Config\.gs/.test(p.out.log) && /整份貼上/.test(p.out.log));

// Config.gs 整個不見也一樣
files = allGs(); delete files['Config.gs'];
p = project(files, allHtml());
assert.strictEqual(p.ctx.doGet({ parameter: {} }).kind, 'html');
assert(p.ctx.checkProjectFiles().problems.some(x => x.file === 'Config.gs'));

// ---------------------------------------------------------------- 四、舊版檔案、版本不符、少了 HTML
files = allGs();
files['Code.gs'] = files['Code.gs'].replace(/t\.initialTab = [\s\S]*?: '';/, '');     // 舊版 doGet
files['Config.gs'] = files['Config.gs'].replace(/var GAS_BUILD = '[^']+'/, "var GAS_BUILD = '2026-09-16-quality-v34'");
// 用檢查表上現在的 marker 模擬舊版 JavaScript.html（版本往前推進時 marker 會換，這裡跟著表走）
const jsMarker = Array.from(tbl.PROJECT_HTML_).find(h => h.file === 'JavaScript').marker;
const html = allHtml(); delete html['Tech']; html['JavaScript'] = html['JavaScript'].split(jsMarker).join('');
p = project(files, html);
r = p.ctx.checkProjectFiles();
const by = f => r.problems.filter(x => x.file === f).map(x => x.reason).join(' / ');
assert(/舊版.*initialTab/.test(by('Code.gs')), by('Code.gs'));
assert(by('Config.gs').includes('quality-v34') && by('Config.gs').includes(tbl.PROJECT_BUILD_), by('Config.gs'));
assert(/找不到這個 HTML 檔/.test(by('Tech.html')));
assert(/舊版/.test(by('JavaScript.html')));
assert.strictEqual(r.problems.length, 4, JSON.stringify(r.problems));

// 只少了其中幾個名稱（貼到一半）
files = allGs();
files['MailService.gs'] = files['MailService.gs'].replace(/^function escAttr_\(/m, 'function __gone(');
p = project(files, allHtml());
assert(/缺 escAttr_/.test(p.ctx.checkProjectFiles().problems.map(x => x.reason).join()));

console.log('PASS: v37 doGet explains a missing Config.gs (page + JSON) and checkProjectFiles pinpoints missing, stale or mismatched files.');
