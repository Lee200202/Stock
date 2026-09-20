// v35（2026/09/16）：退訂連結要指向 /exec、關注股票賣出提醒整個拿掉、盤中即時通知信件與每日整理同一套版面。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, f), 'utf8');

const EXEC = 'https://script.google.com/macros/s/AKfycbDEPLOYMENT/exec';
const DEV = 'https://script.google.com/macros/s/1ScriptIdXYZ/dev';

function load(opt) {
  opt = opt || {};
  const props = Object.assign({}, opt.props), writes = [], sent = [];
  const ctx = vm.createContext({ console, JSON, Math, Date, String, Number, Object, Array, RegExp, isNaN, Infinity,
    Logger: { log: () => {} },
    ScriptApp: { getService: () => ({ getUrl: () => opt.live }) },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null),
      setProperty: (k, v) => { writes.push(k); props[k] = String(v); } }) },
    Utilities: { formatDate: () => '2026/09/16' },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {} }) },
    MailApp: { sendEmail: m => sent.push(m) }
  });
  for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs', 'Cmoney.gs'])
    vm.runInContext(read('apps-script/' + name), ctx);
  ctx.DISCLAIMER = '免責';
  return { ctx, props, writes, sent };
}

// ---------------------------------------------------------------- 一、退訂連結
// 排程寄信時 getUrl() 回的是 /dev：有記下 /exec 就一定用 /exec
let env = load({ live: DEV, props: { WEBAPP_URL: EXEC } });
let u = env.ctx.unsubscribeUrl_('a+b@x.co', 't/1');
assert.strictEqual(u, EXEC + '?action=unsubscribe&email=a%2Bb%40x.co&token=t%2F1');
assert(!/\/dev\?/.test(u), '退訂連結不能是 /dev');
// 存的值不是 /exec（手滑貼錯）就不採用，改看當下網址
env = load({ live: EXEC, props: { WEBAPP_URL: DEV } });
assert.strictEqual(env.ctx.publicWebAppUrl_(), EXEC);
// 兩邊都沒有 /exec：回空字串，退訂連結退回可拿到的網址（並留紀錄），不會丟例外
env = load({ live: DEV });
assert.strictEqual(env.ctx.publicWebAppUrl_(), '');
assert(env.ctx.unsubscribeUrl_('a@b.co', 't').startsWith(DEV + '?action=unsubscribe'));

// Config.gs 寫死的正式網址（管理者 2026/09/16 提供；專案屬性超過 50 個，設定頁面唯讀加不進去）
const GIVEN = 'https://script.google.com/macros/s/AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw/exec';
const cfgSrc = read('apps-script/Config.gs');
assert(cfgSrc.includes("var WEBAPP_URL_DEFAULT = '" + GIVEN + "';"), 'Config.gs 的網址要與管理者給的一字不差');
// 屬性沒設、排程裡只拿得到 /dev：用 Config.gs 那一份
env = load({ live: DEV });
env.ctx.WEBAPP_URL_DEFAULT = GIVEN;
assert.strictEqual(env.ctx.unsubscribeUrl_('a@b.co', 't'), GIVEN + '?action=unsubscribe&email=a%40b.co&token=t');
assert(env.ctx.cmMailBody_({ text: 'x', url: 'https://u' }, false).includes('href="' + GIVEN + '"'));
// 屬性有值時以屬性為準（換部署後 doGet 自動記下的新網址要能蓋過舊的寫死值）
env = load({ live: DEV, props: { WEBAPP_URL: EXEC } });
env.ctx.WEBAPP_URL_DEFAULT = GIVEN;
assert.strictEqual(env.ctx.publicWebAppUrl_(), EXEC);
// 寫死值不是 script.google.com 的 /exec 就不採用
env = load({ live: DEV });
env.ctx.WEBAPP_URL_DEFAULT = DEV;
assert.strictEqual(env.ctx.publicWebAppUrl_(), '');

// setWebAppUrl：在編輯器執行，經由屬性服務寫入並讀回確認；/dev 或亂填直接拒絕
const setupSrc = read('apps-script/Setup.gs');
const setFn = setupSrc.slice(setupSrc.indexOf('function setWebAppUrl('), setupSrc.indexOf('function showDeployInfo('));
env = load({ live: DEV });
vm.runInContext(setFn, env.ctx);
env.ctx.WEBAPP_URL_DEFAULT = GIVEN;
let res = env.ctx.setWebAppUrl();
assert.strictEqual(env.props.WEBAPP_URL, GIVEN);
assert.strictEqual(res.after, GIVEN);
res = env.ctx.setWebAppUrl(EXEC);
assert.strictEqual(res.before, GIVEN);
assert.strictEqual(env.props.WEBAPP_URL, EXEC);
assert.throws(() => env.ctx.setWebAppUrl(DEV), /exec/);
assert.throws(() => env.ctx.setWebAppUrl('https://evil.example.com/exec'), /exec/);
assert.strictEqual(env.props.WEBAPP_URL, EXEC, '被拒絕時不能動到原本的值');
// showDeployInfo 與 whoAmI 都要印出正式網址現況
const fnBody = name => { const a = setupSrc.indexOf('function ' + name + '('); return setupSrc.slice(a, setupSrc.indexOf('\nfunction ', a + 10)); };
assert(/webAppUrlReport_\(\)/.test(fnBody('showDeployInfo')) && /webAppUrlReport_\(\)/.test(fnBody('whoAmI')));
env = load({ live: DEV, props: { WEBAPP_URL: EXEC } });
vm.runInContext(fnBody('webAppUrlReport_'), env.ctx);
env.ctx.WEBAPP_URL_DEFAULT = GIVEN;
let rep = env.ctx.webAppUrlReport_().join('\n');
assert(rep.includes('實際使用　' + EXEC) && /兩份不一樣/.test(rep));
env = load({ live: DEV });
vm.runInContext(fnBody('webAppUrlReport_'), env.ctx);
env.ctx.WEBAPP_URL_DEFAULT = GIVEN;
rep = env.ctx.webAppUrlReport_().join('\n');
assert(rep.includes('實際使用　' + GIVEN) && /setWebAppUrl\(\)/.test(rep));

// doGet 從 /exec 打開時記下正式網址；/dev 不記；沒變就不重寫
env = load({ live: EXEC });
env.ctx.rememberWebAppUrl_();
assert.strictEqual(env.props.WEBAPP_URL, EXEC);
env.ctx.rememberWebAppUrl_();
assert.strictEqual(env.writes.length, 1, '網址沒變就不該每次都寫屬性');
env = load({ live: DEV });
env.ctx.rememberWebAppUrl_();
assert(!('WEBAPP_URL' in env.props), '從 /dev 打開不能蓋掉正式網址');
// getUrl 丟例外也不能讓網頁掛掉
env = load({ live: EXEC });
env.ctx.ScriptApp = { getService: () => { throw new Error('boom'); } };
env.ctx.rememberWebAppUrl_();

const code = read('apps-script/Code.gs');
const doGet = code.slice(code.indexOf('function doGet('), code.indexOf("if (params.action === 'unsubscribe')"));
assert(/rememberWebAppUrl_\(\);/.test(doGet), 'doGet 一進來（處理退訂之前）就要記下正式網址');
const svc = read('apps-script/MailService.gs');
const unsub = svc.slice(svc.indexOf('function unsubscribeUrl_('), svc.indexOf('function unsubscribeUrl_(') + 700);
assert(/publicWebAppUrl_\(\)/.test(unsub));
assert(/WEBAPP_URL/.test(read('apps-script/Setup.gs')), 'showDeployInfo 要列出正式網址的狀態');

// ---------------------------------------------------------------- 二、關注股票賣出提醒拿掉
const index = read('apps-script/Index.html'), js = read('apps-script/JavaScript.html');
for (const id of ['subAlert', 'watchInput', 'watchSuggest', 'watchTags', 'mgWatch', 'mgWatchHint', 'mgStocks'])
  assert(!index.includes('id="' + id + '"') && !js.includes("$('" + id + "')"), '殘留 ' + id);
assert(!index.includes('關注股票被明講賣出時提醒'));
assert(!/codes:\s*watch/.test(js) && !/alert:\s*\$\('subAlert'\)/.test(js));
assert(!/d\.alert|d\.codes/.test(js), 'AI 訂閱草稿卡片不再顯示賣出提醒與關注股票');
assert(!svc.includes('關注股票異動提醒'), '每日推播、確認信、管理信都不再提關注股票');
const pushAt = svc.indexOf('function dailyPushJob');
const push = svc.slice(pushAt, svc.indexOf('\nfunction ', pushAt + 10));
assert(push.length > 500 && !/readSheetObjects_\('操作紀錄'\)/.test(push), '每日推播不必再讀操作紀錄');

// 建立訂閱：只給代號不再算數；寫進表格的代號欄一律空白
env = load({ live: EXEC });
const rows = [['Email', '訂閱項目', '關注股票代號', '建立時間', '取消訂閱權杖', '狀態']];
Object.assign(env.ctx, {
  withLock_: fn => fn(), todayStr_: () => '2026/09/16',
  getSheet_: () => ({ getDataRange: () => ({ getValues: () => rows.map(r => r.slice()) }),
                      getRange: (r, c) => ({ setValue: v => { rows[r - 1][c - 1] = v; } }),
                      appendRow: r => rows.push(r) }),
  Utilities: { getUuid: () => 'aaaa-bbbb' },
  loadCodeMap_: () => ({ byCode: {} })
});
let r = env.ctx.createSubscription({ email: 'x@y.co', codes: ['2330'], alert: true });
assert.strictEqual(r.ok, false);
assert(!/關注股票/.test(r.message));
r = env.ctx.createSubscription({ email: 'x@y.co', sms: true, codes: ['2330'] });
assert.strictEqual(r.ok, true);
assert.strictEqual(rows[1][2], '', '代號欄寫空白');
assert.strictEqual(rows[1][1], '會員簡訊');
assert(!/關注股票/.test(env.sent.map(m => m.htmlBody).join('')), '確認信不提關注股票');

// 管理頁儲存：清掉舊的代號欄
const api = read('apps-script/API.gs');
const upd = api.slice(api.indexOf('function apiUpdateSubscription'), api.indexOf('function apiUpdateSubscription') + 1600);
assert(/'關注股票代號':''/.test(upd) && !/payload\.codes/.test(upd));
const lookup = api.slice(api.indexOf('function apiLookupSubscription'), api.indexOf('function apiUpdateSubscription'));
assert(!/stocks:/.test(lookup));

// AI 訂閱助理：草稿只剩 email／daily／sms；兩項都問過、至少一項要才送出
const ai = read('apps-script/Aiservice.gs');
assert(ai.includes('"draft": { "email": "", "daily": null, "sms": null }'));
assert(!/codes\s+關注股票代號|alert\s+是否在關注股票/.test(ai));
const aiCtx = vm.createContext({ String, RegExp, Object, Array, JSON });
vm.runInContext(ai.slice(ai.indexOf('function sanitizeDraft_'), ai.indexOf('function draftMissing_')) +
  ai.slice(ai.indexOf('function draftMissing_'), ai.indexOf('}', ai.indexOf('return miss;')) + 1), aiCtx);
const d1 = aiCtx.sanitizeDraft_({ email: 'x@y.co', daily: true, codes: ['2330'], alert: true }, {});
assert.deepStrictEqual(Object.keys(d1).sort(), ['daily', 'email', 'sms']);
assert.strictEqual(aiCtx.draftReady_(d1), false, '盤中即時通知還沒問，不能先送出');
assert.deepStrictEqual(Array.from(aiCtx.draftMissing_(d1)), ['是否要盤中即時通知']);
const d2 = aiCtx.sanitizeDraft_({ sms: false }, d1);
assert.strictEqual(aiCtx.draftReady_(d2), true);
assert.strictEqual(aiCtx.draftReady_(aiCtx.sanitizeDraft_({ email: 'x@y.co', daily: false, sms: false }, {})), false);

// ---------------------------------------------------------------- 三、盤中即時通知信件
env = load({ live: DEV, props: { WEBAPP_URL: EXEC } });
const art = { id: '1', time: '2026/09/16 13:24:05', url: 'https://www.cmoney.tw/forum/article/1?a=1&b=2',
              text: '張震-1:今天台指期結算\n\n<script>alert(1)</script> 不加碼也不賣股' };
let body = env.ctx.cmMailBody_(art, false);
assert(/class="mc-hero" style="background:#17322A;/.test(body), '標題卡與每日整理同色');
assert(/class="mc-hero-k"[^>]*>盤中即時通知</.test(body));
assert(/<h1[^>]*color:#FFFFFF;[^>]*>13:24 張震發了一則會員簡訊<\/h1>/.test(body), '大字是發文時間');
assert(/class="mc-sec" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;/.test(body), '原文放在同一種圓角卡片');
assert(/<h2 class="mc-h2"[^>]*>.*簡訊原文<\/h2>/.test(body));
assert(!body.includes('<script>') && body.includes('&lt;script&gt;'), '原文一律跳脫');
assert.strictEqual((body.match(/font-size:15px;line-height:1.85;color:#12161A;/g) || []).length, 2, '空行不產生空段落');
assert(body.includes('href="https://www.cmoney.tw/forum/article/1?a=1&amp;b=2"') && />看原文<\/a>/.test(body));
assert(body.includes('href="' + EXEC + '"') && />到網站看整理<\/a>/.test(body), '網站連結用 /exec');
assert(!/<style|style="[^"]*"[^"]*font-family:"/.test(body), '只用 inline style，屬性內沒有雙引號');
assert(!/內容已修訂/.test(body));
// 每一段文字都有明確顏色（深色模式收件匣不會反成白底白字）
for (const m of body.matchAll(/<(p|h1|h2|a|div class="mc-hero-k")[^>]*style="([^"]*)"/g))
  assert(/(^|;)color:#/.test(m[2]), '缺文字顏色：' + m[0].slice(0, 60));

body = env.ctx.cmMailBody_(art, true);
assert(/class="mc-hero-k"[^>]*color:#F7DDA5;[^>]*>內容已修訂</.test(body));
assert(/他剛修改了這一篇/.test(body));

// 還沒記下正式網址：不放「到網站看整理」，不能把 /dev 發給訂閱者
env = load({ live: DEV });
body = env.ctx.cmMailBody_(art, false);
assert(!/到網站看整理/.test(body) && !body.includes(DEV));
// 沒有網址、沒有時間也不壞
body = env.ctx.cmMailBody_({ text: '只有文字' }, false);
assert(/>張震發了一則會員簡訊<\/h1>/.test(body) && !/看原文/.test(body));

// 包上信殼：品牌列＋內容＋信尾取消訂閱（/exec）
env = load({ live: DEV, props: { WEBAPP_URL: EXEC } });
const full = env.ctx.wrapMail_(env.ctx.cmMailBody_(art, false), 'a@b.co', 't1');
assert(/class="mc-brand"/.test(full) && full.includes(EXEC + '?action=unsubscribe&email=a%40b.co&token=t1'));

// 主旨：與每日整理同一個格式，帶上時間
const cm = read('apps-script/Cmoney.gs');
assert(cm.includes("'] 張震股市盤中家教班　' +"));
assert(/String\(a\.time\)\.slice\(11, 16\)/.test(cm.slice(cm.indexOf('function cmNotifyNew_'))));

console.log('PASS: v35 unsubscribe links use the recorded /exec URL, watch-stock alerts are gone end to end, and the instant mail shares the daily mail design.');
