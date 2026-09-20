// v36（2026/09/16）：訂閱確認信、訂閱管理信與盤中即時通知同一套版面；網站 ?tab=subscribe 直接開訂閱分頁。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

const EXEC = 'https://script.google.com/macros/s/AKfycbTEST/exec';

function load(opt) {
  opt = opt || {};
  const sent = [], props = Object.assign({}, opt.props);
  const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN, Infinity,
    Logger: { log: () => {} },
    ScriptApp: { getService: () => ({ getUrl: () => 'https://script.google.com/macros/s/1Sid/dev' }) },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = v; } }) },
    Utilities: { formatDate: () => '2026/09/16', getUuid: () => 'u-u' },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {} }) },
    MailApp: { sendEmail: m => sent.push(m) }
  });
  for (const n of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs', 'Cmoney.gs'])
    vm.runInContext(read('apps-script/' + n), ctx);
  Object.assign(ctx, {
    DISCLAIMER: '免責', APP_TITLE: '張震股市盤中家教班　逐日追蹤',
    fmtDate_: v => String(v || '').slice(0, 10),
    readSheetObjects_: () => opt.rows || []
  });
  return { ctx, sent };
}

const tag = (html, cls) => (html.match(new RegExp('<[a-z0-9]+ class="' + cls + '"[^>]*>')) || [])[0];
function checkColors(html) {
  for (const m of html.matchAll(/<(p|h1|h2|a|div class="mc-hero-k"|div class="mail-bullet")[^>]*style="([^"]*)"/g))
    assert(/(^|;)color:#/.test(m[2]), '缺文字顏色：' + m[0].slice(0, 80));
  assert(!/<style|<ul|<li/.test(html), '不用 <style>，也不再用舊版的項目清單');
  assert(!/border:1px solid #101A16/.test(html), '舊版黑框方形按鈕不應再出現');
}

// 盤中即時通知當基準：標題卡、卡片、卡片標題、主要按鈕的樣式要一字不差
let env = load({ props: { WEBAPP_URL: EXEC } });
const instant = env.ctx.cmMailBody_({ time: '2026/09/16 11:00:12', url: 'https://cm/1', text: '原文' }, false);
const pillStyle = h => (h.match(/<a href="[^"]*" (style="[^"]*background:#04795C;[^"]*")>/) || [])[1];

// ---------------------------------------------------------------- 一、訂閱確認信
env.ctx.sendWelcomeMail_('lin@example.com', 'tok1', true, [], true, false);
let m = env.sent.pop();
assert.strictEqual(m.subject, '張震股市盤中家教班　訂閱完成');
let h = m.htmlBody;
assert(/class="mc-brand"/.test(h), '品牌列（wrapMail_）');
assert.strictEqual(tag(h, 'mc-hero'), tag(instant, 'mc-hero'), '標題卡與盤中即時通知同一個樣式');
assert.strictEqual(tag(h, 'mc-sec'), tag(instant, 'mc-sec'), '卡片與盤中即時通知同一個樣式');
assert.strictEqual(tag(h, 'mc-h2'), tag(instant, 'mc-h2'));
assert.strictEqual(pillStyle(h), pillStyle(instant), '主要按鈕同一個樣式');
assert(/class="mc-hero-k"[^>]*>訂閱完成</.test(h));
assert(/<h1[^>]*>接下來的信會寄到這個信箱<\/h1>/.test(h));
assert(h.includes('<a href="mailto:lin@example.com" style="color:#9CC8B4;text-decoration:none;">lin@example.com</a>'),
  '信箱在深色標題卡裡要自己寫顏色，不能變成收件匣的藍色連結');
for (const t of ['你訂閱的內容', '關於這些信件', '想調整或停掉'])
  assert(new RegExp('<h2 class="mc-h2"[^>]*>.*?' + t + '</h2>').test(h), '缺卡片：' + t);
// 兩種通知都列出，狀態各自正確；順序與網站表單相同（每日總覽在前）
const daily = h.indexOf('每日總覽'), sms = h.indexOf('盤中即時通知');
assert(daily > 0 && sms > daily);
assert(/每日總覽<span[^>]*background:#BEE2CC;[^>]*>已訂閱<\/span>/.test(h));
assert(/盤中即時通知<span[^>]*background:#E8ECEA;[^>]*>未訂閱<\/span>/.test(h));
assert(h.includes('交易日 12:00 起'));
// 到網站管理訂閱：正式網址＋直接開訂閱分頁
assert(h.includes('href="' + EXEC + '?tab=subscribe"') && />到網站管理訂閱<\/a>/.test(h));
// 信尾：免責＋取消訂閱（/exec）
assert(h.includes(EXEC + '?action=unsubscribe&email=lin%40example.com&token=tok1'));
assert.strictEqual((h.match(/>取消訂閱<\/a>/g) || []).length, 1, '取消訂閱只在信尾出現一次');
checkColors(h);

// 設定更新：標籤與主旨換字，兩項都開
env.ctx.sendWelcomeMail_('lin@example.com', 'tok1', true, [], false, true);
m = env.sent.pop();
assert.strictEqual(m.subject, '張震股市盤中家教班　訂閱設定已更新');
assert(/class="mc-hero-k"[^>]*>訂閱設定已更新</.test(m.htmlBody));
assert.strictEqual((m.htmlBody.match(/>已訂閱<\/span>/g) || []).length, 2);
assert(!/未訂閱/.test(m.htmlBody));

// 信箱含引號與角括號：屬性與內文都要跳脫
env.ctx.sendWelcomeMail_('a"b<x>@e.co', 't', true, [], true, false);
m = env.sent.pop();
assert(m.htmlBody.includes('href="mailto:a&quot;b&lt;x&gt;@e.co"'), '屬性裡的引號要跳脫，不能提前結束 href');
assert(!/href="mailto:a"/.test(m.htmlBody));
assert(!m.htmlBody.includes('<x>'), '角括號一律跳脫（內文裡的引號本身無害）');

// 還沒有正式網址：不放「到網站管理訂閱」，也不能把 /dev 發出去當網站連結
env = load();
env.ctx.sendWelcomeMail_('lin@example.com', 't', false, [], true, true);
m = env.sent.pop();
assert(!/到網站管理訂閱/.test(m.htmlBody) && !/1Sid\/dev\?tab=/.test(m.htmlBody));

// 走 createSubscription 的完整路徑也寄出新版
env = load({ props: { WEBAPP_URL: EXEC } });
const rows = [['Email', '訂閱項目', '關注股票代號', '建立時間', '取消訂閱權杖', '狀態']];
Object.assign(env.ctx, { withLock_: fn => fn(), todayStr_: () => '2026/09/16',
  getSheet_: () => ({ getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
    getRange: (r, c) => ({ setValue: v => { rows[r - 1][c - 1] = v; } }), appendRow: r => rows.push(r) }) });
assert(env.ctx.createSubscription({ email: 'new@example.com', daily: true, sms: true }).ok);
assert.strictEqual(env.sent[0].subject, '張震股市盤中家教班　訂閱完成');
assert(/class="mc-hero"/.test(env.sent[0].htmlBody));

// ---------------------------------------------------------------- 二、訂閱管理信
env = load({ props: { WEBAPP_URL: EXEC },
  rows: [{ Email: 'Lin@Example.com', '訂閱項目': '會員簡訊', '建立時間': '2026/09/10 10:00', '取消訂閱權杖': 'tk', '狀態': '生效中' }] });
let r = env.ctx.sendUnsubscribeLink('lin@example.com');
assert(r.ok);
m = env.sent.pop();
assert.strictEqual(m.subject, '張震股市盤中家教班　你目前的訂閱');
h = m.htmlBody;
assert.strictEqual(tag(h, 'mc-hero'), tag(instant, 'mc-hero'));
assert.strictEqual(tag(h, 'mc-sec'), tag(instant, 'mc-sec'));
assert(/class="mc-hero-k"[^>]*>訂閱管理</.test(h) && /<h1[^>]*>你目前的訂閱<\/h1>/.test(h));
assert(/lin@example\.com<\/a>　建立於 2026\/09\/10/.test(h));
assert(/每日總覽<span[^>]*>未訂閱<\/span>/.test(h) && /盤中即時通知<span[^>]*>已訂閱<\/span>/.test(h));
assert(h.includes('href="' + EXEC + '?tab=subscribe"'));
assert(h.includes(EXEC + '?action=unsubscribe&email=lin%40example.com&token=tk'));
assert(/忽略這封信即可/.test(h));
checkColors(h);
// 沒有訂閱：回覆一樣、不寄信（不透露信箱狀態）
env = load({ rows: [] });
assert.strictEqual(env.ctx.sendUnsubscribeLink('nobody@example.com').message, r.message);
assert.strictEqual(env.sent.length, 0);

// ---------------------------------------------------------------- 三、?tab=subscribe 直接開分頁
const code = read('apps-script/Code.gs');
const line = code.match(/t\.initialTab = (\/\^\([^/]+\)\$\/)\.test/);
assert(line, 'doGet 要把 tab 參數交給頁面');
const allow = eval(line[1]);
assert(allow.test('subscribe') && allow.test('tech'));
for (const bad of ['', 'admin', 'subscribe"><script>', 'stock', 'Subscribe']) assert(!allow.test(bad), '不該接受 ' + bad);
const index = read('apps-script/Index.html');
assert(index.includes('<body data-tab="<?= initialTab ?>">'), '用會跳脫的 <?= ?> 輸出');
// 每個允許的名字在分頁列上都真的有按鈕
for (const name of line[1].match(/\(([^)]+)\)/)[1].split('|'))
  assert(index.includes('data-tab="' + name + '"'), '分頁列沒有 ' + name);
const js = read('apps-script/JavaScript.html');
const start = js.indexOf('/* 從信件的「到網站管理訂閱」');
const iife = js.slice(js.indexOf('(function () {', start), js.indexOf('})();', start) + 5);
function runInit(bodyTab, buttons) {
  const calls = [];
  const doc = { body: { getAttribute: () => bodyTab },
    querySelector: sel => { const k = (sel.match(/data-tab="([^"]*)"/) || [])[1]; return buttons.includes(k) ? { k } : null; } };
  vm.runInNewContext(iife, { document: doc, activateTab: (n, b) => calls.push([n, b.k]) });
  return calls;
}
assert.deepStrictEqual(runInit('subscribe', ['overview', 'subscribe']), [['subscribe', 'subscribe']]);
assert.deepStrictEqual(runInit('', ['overview', 'subscribe']), [], '沒帶參數維持原本的每日總覽');
assert.deepStrictEqual(runInit('overview', ['overview']), [], '本來就在每日總覽，不重複切換');
assert.deepStrictEqual(runInit('ghost', ['overview']), [], '分頁列上沒有的名字不切');

console.log('PASS: v36 subscription mails share the instant-notification design (hero, cards, pills, footer) and ?tab=subscribe opens the subscribe tab.');
