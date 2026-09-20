// 測試寄信與說明去分類理由（2026/09/16）：一封就是一封、不碰任何狀態；舊文章呈現時也不留分類流程。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const TSMC = '詳細以台積電作為技術面與外部因素干擾的最佳教學教材，說明其季線下方的買點與外部升息事件的影響，' +
  '但在當日節目中並未將台積電列為當日會員實際買進或持有的個股明細，故列入市場教學與觀察範疇。' +
  '台積電季線下方本為正常買點，但因外部升息等變數干擾可能導致短線急跌後再回升，提醒不宜在變數未定前盲目躁進。';
const CLEAN = '詳細以台積電作為技術面與外部因素干擾的最佳教學教材，說明其季線下方的買點與外部升息事件的影響。' +
  '台積電季線下方本為正常買點，但因外部升息等變數干擾可能導致短線急跌後再回升，提醒不宜在變數未定前盲目躁進。';

function load(opt) {
  opt = opt || {};
  const sent = [], sheetRows = [], props = Object.assign({}, opt.props);
  const pad = n => String(n).padStart(2, '0');
  class FDate extends Date { constructor(...a) { a.length ? super(...a) : super(Date.parse(opt.now || '2026-09-16T09:00:00+08:00')); } }
  const ctx = vm.createContext({ console, JSON, Math, String, Number, Object, Array, RegExp, Date: FDate,
    Logger: { log: () => {} },
    Utilities: { formatDate: d => d.getFullYear() + '/' + pad(d.getMonth() + 1) + '/' + pad(d.getDate()) },
    PropertiesService: { getScriptProperties: () => ({
      getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = String(v); } }) },
    MailApp: { sendEmail: m => sent.push(m) } });
  for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs'])
    vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', name), 'utf8'), ctx);
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', DISCLAIMER: '免責',
    fmtDate_: v => { const m = String(v || '').match(/^(\d{4})\/(\d{2})\/(\d{2})/); return m ? m[0] : ''; },
    nowStamp_: () => '2026/09/16 09:00:00',
    unsubscribeUrl_: (email, token) => 'https://example.com/u?e=' + email + '&t=' + token,
    readSheetObjects_: name => name === '每日推播內容' ? (opt.articles || []) : (opt.subscribers || []),
    activeSubscribers_: () => opt.subscribers || [],
    getSheet_: () => ({ appendRow: r => sheetRows.push(r) })
  });
  return { ctx, sent, sheetRows, props };
}
const article = ['文章標題：量縮震盪，靜待CPI！', '', '② 會員操作紀錄與持股明細', '', '②-3 觀望個股（當日未執行買賣）', '', '觀望注意', '',
  '| 股票名稱 | 股票代號 | 價位說明 | 張震口頭說明重點 |', '|---|---|---|---|', '| 台積電 | 2330 | 未說明 | ' + TSMC + ' |'].join('\n');
const day = { '日期': '2026/09/15', '文字稿': article, '寄送狀態': '已寄送' };

// 一、說明去掉分類理由：資料層（publicNarrative_）與呈現層（mdToHtml_ 說明欄）都乾淨，
//     舊文章不必重寫；正常說明一個字都不動。
let env = load({ articles: [day] });
assert.equal(env.ctx.stripMetaClauses_(TSMC), CLEAN);
assert.equal(env.ctx.publicNarrative_(TSMC, { name: '台積電', code: '2330' }), CLEAN);
assert.equal(env.ctx.stripMetaClauses_('季線下方本為買點，故列入觀察範疇。外部變數未定前不宜躁進。'),
             '季線下方本為買點。外部變數未定前不宜躁進。');
const keep = '會員先前在1170賣出，若後續再度跌破1000或來到更低位置時，可留意其止跌回穩與尋求切入的機會。';
assert.equal(env.ctx.stripMetaClauses_(keep), keep);
const html = env.ctx.mdToHtml_(article);
assert(html.includes(CLEAN) && !/並未將|個股明細|故列入|觀察範疇/.test(html), '信件說明欄不留分類流程');

// 二、測試寄信：寄一封、主旨與正式信相同、內容是那一天的每日整理
let r = env.ctx.sendTestMailTo('rainforecast2026@gmail.com', '2026/09/15');
assert.equal(r.ok, true); assert.equal(r.date, '2026/09/15');
assert.equal(env.sent.length, 1);
assert.equal(env.sent[0].to, 'rainforecast2026@gmail.com');
assert.equal(env.sent[0].subject, '[2026/09/15] 張震股市盤中家教班　每日整理');
assert(env.sent[0].htmlBody.includes(CLEAN) && env.sent[0].htmlBody.includes('取消訂閱'));
// 不碰任何狀態：沒有寫寄送紀錄，只留一筆系統狀態
assert(!Object.keys(env.props).some(k => k.indexOf('PUSH_SENT') >= 0 || k.indexOf('STATUS_MAIL') >= 0));
assert.equal(env.sheetRows.length, 1);
assert(/測試寄信/.test(env.sheetRows[0][1]) && /rainforecast2026@gmail\.com/.test(env.sheetRows[0][2]));

// 三、只寄一次：同一天同一個信箱再執行不會再寄；明確要求才重寄
r = env.ctx.sendTestMailTo('rainforecast2026@gmail.com', '2026/09/15');
assert.equal(r.ok, false); assert.equal(env.sent.length, 1);
assert(/已經在 2026\/09\/16 09:00:00 寄給/.test(r.reason));
r = env.ctx.sendTestMailTo('rainforecast2026@gmail.com', '2026/09/15', true);
assert.equal(r.ok, true); assert.equal(env.sent.length, 2);

// 四、沒指定日期＝昨天；昨天沒有內容才退回最近一天
env = load({ articles: [day] });
assert.equal(env.ctx.sendTestMailNow().date, '2026/09/15');
assert.equal(env.sent[0].to, 'rainforecast2026@gmail.com');
env = load({ articles: [{ '日期': '2026/09/11', '文字稿': article }] });
assert.equal(env.ctx.sendTestMailTo('a@b.co', '').date, '2026/09/11');

// 五、擋下明顯的誤用
env = load({ articles: [day] });
assert.throws(() => env.ctx.sendTestMailTo('', '2026/09/15'), /收件信箱/);
assert.throws(() => env.ctx.sendTestMailTo('not-an-email', '2026/09/15'), /收件信箱/);
assert.throws(() => env.ctx.sendTestMailTo('a@b.co', '2026/09/14'), /沒有郵件內容/);
assert.equal(env.sent.length, 0);

// 六、訂閱者的退訂權杖會用他自己的；非訂閱者不借用別人的
env = load({ articles: [day], subscribers: [{ 'Email': 'sub@b.co', '取消訂閱權杖': 'tok-1' }] });
env.ctx.sendTestMailTo('sub@b.co', '2026/09/15');
assert(env.sent[0].htmlBody.includes('t=tok-1'));
env.ctx.sendTestMailTo('other@b.co', '2026/09/15');
assert(!env.sent[1].htmlBody.includes('tok-1'));
console.log('PASS: one-shot test mail sends exactly one unchanged daily mail, and stock descriptions drop classification rationale everywhere.');
