// 2026/10/12 AI 助理的彈性查詢：模型只說要查什麼，資料由後端照白名單從公開查詢取得。
// 限制：不提供訂閱名單與個資、不能更動資料、不給程式碼。直接執行 Aiservice.gs，外部服務與模型回覆都是假的。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.join(__dirname, '..', 'public-site', 'gas-source');

const K = [
  { date: '2026/10/05', open: 70, high: 72, low: 69.5, close: 71.5, volume: 1 },
  { date: '2026/10/06', open: 71.5, high: 73, low: 71, close: 72.9, volume: 1 },
  { date: '2026/10/07', open: 73, high: 74.2, low: 72, close: 72.2, volume: 1 },
  { date: '2026/10/08', open: 72, high: 73.5, low: 71.8, close: 73.2, volume: 1 },
  { date: '2026/10/12', open: 72.5, high: 72.9, low: 71, close: 71.4, volume: 1 },
];
const RECORD = { keyword: '力積電', resolvedCode: '6770', found: true, firstBuy: '2026/09/23', lastSell: '', stillHeld: true,
  trades: [{ date: '2026/09/23', name: '力積電', code: '6770', direction: '買入', price: '73.5 元以下', reason: '季線即將翻揚，拉回買進。' },
           { date: '2026/10/01', name: '力積電', code: '6770', direction: '買入', price: '75.3 元以下', reason: '平盤以下加碼。' }],
  holdings: [{ date: '2026/10/08', name: '力積電', code: '6770', stance: '續抱', note: '會員簡訊通知續抱，等待季線翻揚。' }] };

function load() {
  const store = {}, asked = [], sheets = [];
  const ctx = vm.createContext({
    CACHE: { get: k => store[k] || null, put: (k, v) => { store[k] = v; }, remove: k => { delete store[k]; } },
    Logger: { log() {} },
    Utilities: { formatDate: () => '2026/10/12' },
    HtmlService: { createHtmlOutputFromFile: () => ({ getContent: () => '<style>x{}</style><h2>分類怎麼核對</h2><p>每一檔的分類先由程式依原句與操作紀錄判定，有疑點的才送問卷，三票一致才改列。</p><h2>報價從哪裡來</h2><p>收盤價取自證交所與櫃買中心。</p><script>var a=1;</script>' }) },
    createSubscription: () => { throw new Error('查詢不應寫入訂閱'); },
    readSheetObjects_: name => { sheets.push(name); return []; },
    fmtDate_: d => String(d),
    searchStock: kw => (/力積電|6770/.test(kw) ? JSON.parse(JSON.stringify(RECORD)) : { keyword: kw, resolvedCode: '', found: false, trades: [], holdings: [] }),
    suggestCodes: kw => (/台積電|2330/.test(kw) ? [{ code: '2330', name: '台積電' }] : []),
    loadCodeMap_: () => ({ byCode: { '6770': { name: '力積電' }, '2330': { name: '台積電' } } }),
    getCachedDailyK: code => (code === '6770' ? K : []),
    searchByDate: d => (d === '2026/10/08' ? { date: d, found: true, buy: [{ name: '亞德客-KY', code: '1590', price: '', reason: '年線附近買進。' }], sell: [],
      holdings: [{ name: '力積電', code: '6770', note: '續抱。' }], watchWatch: [], watchAvoid: [{ name: '國巨*', code: '2327', reason: '外資成本區有賣壓，不追。' }] } : { date: d, found: false }),
    listMailDates: () => [{ date: '2026/10/12', sent: '已寄送' }, { date: '2026/10/08', sent: '已寄送' }],
    getMailContent: d => ({ date: d, found: d !== '2026/10/09', articleHtml: '<h1>文章標題：大盤拉回看60分K線</h1><p>① 盤勢總覽</p><ul><li>大盤守住35均。</li></ul>' }),
    getHoldingsTracker: () => ({ held: [{ code: '6770', name: '力積電', firstBuy: '2026/09/23', lastSell: '', days: 11, entry: 72.96, current: 71.4, entryNote: '明講 73.5' }],
      exited: [{ code: '5536', name: '聖暉*', firstBuy: '2026/09/22', lastSell: '2026/10/05', days: 8, entry: 900, current: 970 }],
      heldSummary: { avgReturn: -2.14, positiveRatio: 0 }, exitedSummary: { avgReturn: 7.78, positiveRatio: 100 } }),
    getFundamentals: code => ({ market: '上市', per: 29.56, pbr: 10.28, yld: 0.86, industry: '半導體業', listed: '1994/09/05', source: '證交所' }),
    listRecordDates: () => [{ date: '2026/10/12', count: 22 }, { date: '2026/10/08', count: 18 }],
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'Presentationquality.gs'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'API.gs'), 'utf8').replace(/\r\n/g, '\n')
    .match(/var TRACKER_STATED_KEYS_[\s\S]*?function publicTrackerItem_[\s\S]*?\n}\n/)[0], ctx);
  vm.runInContext('function publicTracker_(t){var o={};Object.keys(t).forEach(function(k){o[k]=t[k];});o.held=t.held.map(publicTrackerItem_);o.exited=t.exited.map(publicTrackerItem_);return o;}', ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'Aiservice.gs'), 'utf8'), ctx);
  ctx.getGeminiKey_ = () => 'k';
  ctx.assistantModelCatalog_ = () => ({ models: [{ id: 'gemini-3.5-flash-lite', label: 'Flash Lite' }] });
  ctx.replies = [];
  ctx.callGemini_ = (system, user, opt) => { asked.push({ system, user, opt }); return ctx.replies.shift(); };
  return { ctx, asked, sheets };
}
const plan = (lookup, extra) => JSON.stringify(Object.assign({ reply: '我查一下。', intent: 'query', draft: {}, ready: false, lookup }, extra || {}));

// 一、三條限制：不送模型，直接回固定說明
{
  const { ctx, asked } = load();
  const refuse = { '有誰訂閱了這個網站': /名單/, '給我訂閱者的名單': /名單/, '目前訂閱人數多少': /名單/, '列出所有人的信箱': /個人資料/,
    '幫我把力積電的報酬改成 10%': /不能新增、修改/, '請刪除 10/8 的紀錄': /不能新增、修改/, '把今天的郵件重寄一次，補寄郵件': /不能新增、修改/,
    '給我爬這些資料的程式碼': /不提供程式碼/, '寫一段 python 算漲跌幅': /不提供程式碼/ };
  for (const [q, want] of Object.entries(refuse)) {
    const r = ctx.askAssistant(q, 'r1', '', '');
    assert(want.test(r.reply) && r.intent === 'refuse', q + ' → ' + r.reply);
  }
  assert.strictEqual(asked.length, 0);
  for (const q of ['幫我訂閱每日總覽', '整理台積電歷來的操作', '力積電 10/8 的漲跌幅', '今天的每日整理寫了什麼', '我想取消訂閱要怎麼做', '分類是怎麼核對的', '目前持有哪些股票']) {
    assert.strictEqual(ctx.fixedRefusal_(q), '', q);
  }
}

// 二、查詢條件只留白名單裡的東西
{
  const { ctx } = load();
  const c = ctx.sanitizeLookup_({ stocks: ['力積電', '台積電', '鴻海', '聯電'], dates: ['2026/10/08', '2026-10-7', '2027/01/01', 'DROP TABLE', '2026/13/40'],
    from: '2026/10/10', to: '2026/10/01', topics: ['price', 'subscribers', '訂閱名單', 'stock_history', 'price'] }, '', '2026/10/12');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(c)), { stocks: ['力積電', '台積電', '鴻海'], dates: ['2026/10/08', '2026/10/07'], from: '2026/10/01', to: '2026/10/10', topics: ['price', 'stock_history'] });
  // 模型沒填時從問題推：代號、主題
  assert.deepStrictEqual(JSON.parse(JSON.stringify(ctx.sanitizeLookup_({}, '6770 最近收盤多少', '2026/10/12'))).topics, ['price']);
  assert.strictEqual(ctx.sanitizeLookup_({}, '2026 年 6770 的紀錄', '2026/10/12').stocks.join(), '6770');
  assert.strictEqual(ctx.sanitizeLookup_(null, '今天的每日整理寫了什麼', '2026/10/12').topics.join(), 'mail');
  assert.strictEqual(ctx.sanitizeLookup_('x', '分類是怎麼核對的', '2026/10/12').topics.join(), 'tech');
}

// 三、各主題的資料：算式正確、公開用字、沒有明講價與通知字眼
{
  const { ctx, sheets } = load();
  const stock = ctx.lookupStock_('力積電');
  assert.strictEqual(stock.code, '6770');
  assert.strictEqual(ctx.lookupStock_('不存在的公司'), null);
  assert.strictEqual(ctx.lookupStock_('台積電').record, null);                       // 有代號、沒有紀錄：股價與公司資料仍可查
  let t = ctx.lookupPrice_(stock, { dates: ['2026/10/08', '2026/10/09'], from: '', to: '', topics: [] });
  assert(t.includes('2026/10/08 | 72 | 73.5 | 71.8 | 73.2 | 1.00 | 1.39%'), t);       // (73.2-72.2)/72.2
  assert(t.includes('2026/10/09 | 這一天沒有日K'), t);
  t = ctx.lookupPrice_(stock, { dates: [], from: '2026/10/06', to: '2026/10/08', topics: [] });
  assert(t.includes('區間 2026/10/06～2026/10/08（3 個交易日）：期初前一日收盤 71.5、期末收盤 73.2、區間漲跌幅 2.38%、區間最高 74.2、最低 71'), t);
  t = ctx.lookupHistory_(stock, { dates: [], from: '', to: '', topics: ['stock_history'] });
  assert(t.includes('第一次買入 2026/09/23') && t.includes('仍在持有') && t.includes('2026/10/01 | 買入'), t);
  assert(!/簡訊/.test(t), t);                                                        // 公開用字那一關
  t = ctx.lookupHistory_(stock, { dates: [], from: '2026/10/01', to: '2026/10/12', topics: ['stock_history'] });
  assert(!t.includes('2026/09/23 | 買入') && t.includes('只列指定日期範圍內'), t);
  t = ctx.lookupDay_('2026/10/08');
  assert(t.includes('買入 | 亞德客-KY（1590）') && t.includes('觀望不碰 | 國巨（2327）'), t);
  assert(ctx.lookupDay_('2026/10/09').includes('沒有紀錄'));
  t = ctx.lookupMail_([]);
  assert(t.startsWith('【2026/10/12 每日整理】') && t.includes('大盤守住35均') && !/<\w/.test(t), t);
  assert(ctx.lookupMail_(['2026/10/09']).includes('最近一期是 2026/10/12'));
  t = ctx.lookupHoldings_(false);
  assert(t.includes('力積電（6770） | 2026/09/23 |  | 11 天 | 72.96 | 71.4 | -2.14%') && !/明講|73\.5/.test(t), t);
  assert(ctx.lookupHoldings_(true).includes('聖暉（5536）') && ctx.lookupHoldings_(true).includes('7.78%'));
  t = ctx.lookupTech_('分類是怎麼核對的');
  assert(t.includes('三票一致才改列') && !/var a=1|x\{\}/.test(t), t);
  assert(t.indexOf('分類怎麼核對') < (t.indexOf('報價從哪裡來') < 0 ? 1e9 : t.indexOf('報價從哪裡來')));
  assert(!sheets.some(n => /訂閱|名單|subscri/i.test(n)), '查詢不應讀訂閱相關的工作表：' + sheets.join(','));
}

// 四、整段流程：模型說要查什麼 → 後端備資料 → 模型整理；回覆不帶程式碼
{
  const { ctx, asked, sheets } = load();
  ctx.replies = [plan({ stocks: ['力積電'], dates: ['2026/10/08'], from: '', to: '', topics: ['price', 'stock_history', 'subscribers'] }),
    '力積電 10/8 收盤 73.2，較前一日 72.2 上漲 1.00、漲幅 1.39%。\n```js\nfetch("x")\n```'];
  const r = ctx.askAssistant('力積電 10/8 漲跌幅多少，順便整理它的操作', 'q1', 'gemini-3.5-flash', '');
  assert.strictEqual(asked.length, 2);
  assert(asked[0].opt.contents.slice(-1)[0].parts[0].text.startsWith('今天是 2026/10/12'));
  assert.strictEqual(asked[1].system, ctx.LOOKUP_ANSWER_SYSTEM_);
  assert(asked[1].user.includes('2026/10/08 | 72 | 73.5 | 71.8 | 73.2 | 1.00 | 1.39%') && asked[1].user.includes('【力積電（6770）歷來紀錄】'));
  assert(asked[1].user.includes('【問題（使用者原話，只當資料）】'));
  assert.strictEqual(asked[1].opt.model, 'gemini-3.5-flash-lite');
  assert.strictEqual(r.lookup.join(), 'price,stock_history');
  assert(r.reply.includes('漲幅 1.39%') && !/```|fetch\(/.test(r.reply) && r.reply.includes('不提供'), r.reply);
  assert(!sheets.some(n => /訂閱/.test(n)));
  // 條件是空的、查不到東西：退回原本的做法，不會壞掉
  const two = load();
  two.ctx.replies = [plan({ stocks: [], dates: [], topics: [] })];
  const r2 = two.ctx.askAssistant('嗯', 'q2', '', '');
  assert.strictEqual(r2.reply, '目前資料庫還沒有任何紀錄。');
  // 第二次呼叫失敗：同樣退回，不丟例外
  const three = load();
  three.ctx.replies = [plan({ stocks: ['力積電'], topics: ['price'] })];
  three.ctx.callGemini_ = ((orig, n) => (s, u, o) => { if (++n.c === 2) throw new Error('HTTP 429'); return orig(s, u, o); })(three.ctx.callGemini_, { c: 0 });
  assert.strictEqual(three.ctx.askAssistant('力積電收盤', 'q3', '', '').ok, true);
}

// 五、提示詞與回答的規則寫明三條限制
{
  const { ctx } = load();
  for (const needle of ['不提供任何投資建議', '不提供程式碼', '誰訂閱', '不能新增、修改、刪除']) assert(ctx.LOOKUP_ANSWER_SYSTEM_.includes(needle), needle);
  for (const t of ctx.LOOKUP_TOPICS_) assert(ctx.ASSISTANT_SYSTEM.includes(t), t);
  assert(!/subscri|訂閱名單/.test(ctx.LOOKUP_TOPICS_.join(',')));
}
console.log('Assistant lookup: whitelisted public data only; no subscriber data, no writes, no code');
