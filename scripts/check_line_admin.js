// 執行方式：node scripts/check_line_admin.js
// 驗證 LINE 管理者模式（LineAdmin.gs）：裁示解析、日期期限、卡片、權限與收件人，不連線 LINE、不讀試算表。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const dir = path.join(__dirname, '..', 'public-site', 'gas-source');
const cacheStore = new Map();
const ctx = vm.createContext({
  console, Date, JSON, Math, Number, String, Object, Array, RegExp, encodeURIComponent, decodeURIComponent,
  CacheService: { getScriptCache: () => ({
    get: k => (cacheStore.has(k) ? cacheStore.get(k) : null), put: (k, v) => cacheStore.set(k, String(v)), remove: k => cacheStore.delete(k) }) },
  Utilities: { formatDate: () => '20261108153000' },
  Logger: { log() {} },
});
for (const f of ['Line.gs', 'LineAdmin.gs']) {
  vm.runInContext(fs.readFileSync(path.join(dir, f), 'utf8'), ctx, { filename: f });
}

// ---- 裁示解析 ----
const p = s => ctx.lineAdminParseRuling_(s);
let r = p('觀望注意—原稿說等拉回月線再看，不是不碰');
assert.equal(r.cat, '觀望注意'); assert.equal(r.code, ''); assert.equal(r.month, 0);
assert.match(r.text, /等拉回月線/);
r = p('11/06 2330 會員持股—紀錄上還沒出場');
assert.equal(r.month, 11); assert.equal(r.day, 6); assert.equal(r.code, '2330'); assert.equal(r.cat, '會員持股');
r = p('２０２６／１１／０５　３００８　觀望注意－等量縮回穩');
assert.equal(r.year, 2026); assert.equal(r.code, '3008'); assert.equal(r.cat, '觀望注意');
r = p('台積電 觀望不碰：本股自己被否定，明講不碰');
assert.equal(r.name, '台積電'); assert.equal(r.cat, '觀望不碰');
r = p('2317 刪除—只是舉例');
assert.equal(r.cat, '不列');
r = p('6488 注意-等回測再看');
assert.equal(r.cat, '觀望注意');
// 不是裁示：一般查詢、沒有說明、說明太短、只有分類詞
for (const s of ['台積電', '2330', '今天有影片嗎', '觀望注意', '觀望注意—好', '我要查詢世芯KY買賣狀況', '開啟盤中通知']) {
  assert.equal(p(s), null, s);
}
// lineParseText_ 會把裁示交給管理者路由；一般指令不受影響
assert.equal(ctx.lineParseText_('觀望注意—原稿說等拉回月線再看', 'U1').a, 'adminrule');
assert.equal(ctx.lineParseText_('管理者登入', 'U1').a, 'adminon');
assert.equal(ctx.lineParseText_('取消管理者', 'U1').a, 'adminoff');
assert.equal(ctx.lineParseText_('回到使用者', 'U1').a, 'adminoff');
assert.equal(ctx.lineParseText_('今日稽核', 'U1').a, 'adminreview');
assert.equal(ctx.lineParseText_('待裁示', 'U1').a, 'adminpending');
assert.equal(ctx.lineParseText_('管理者綁定123456', 'U1').a, 'bind');
assert.equal(ctx.lineParseText_('2330', 'U1').a, 'stock');

// ---- 日期期限 ----
const d = (m, dd, y) => ctx.lineAdminRuleDate_({ month: m, day: dd, year: y || 0 }, '2026/11/08');
assert.equal(ctx.lineAdminRuleDate_({ month: 0 }, '2026/11/08').date, '2026/11/08');
assert.equal(d(11, 6).date, '2026/11/06');
assert.equal(d(10, 9).date, '2026/10/09');                 // 30 天內
assert.match(d(10, 1).error, /最近 30 天/);
assert.match(d(11, 9, 2026).error, /未來/);
assert.equal(d(12, 30).error.includes('30 天'), true);      // 沒寫年份的 12/30 視為去年，超過期限
assert.match(d(2, 30).error, /看不懂/);

// ---- 卡片 ----
const rows = [
  { 日期: '2026/11/06', 代號: '2330', 名稱: '台積電', 網站分類: '會員持股', 複核分類: '會員持股', 差異類型: '說明缺漏', 嚴重度: '明確錯誤', 處理: '已修正', 修正後: '補上缺口與安全區條件', 原文依據: '缺口在下面' },
  { 日期: '2026/11/06', 代號: '8299', 名稱: '群聯', 網站分類: '觀望不碰', 複核分類: '觀望注意', 差異類型: '分類', 嚴重度: '待確認', 處理: '待管理者', 判斷理由: '本股正面等回穩', 原文依據: '群聯等回測再看' },
  { 日期: '2026/11/06', 代號: '3008', 名稱: '大立光', 網站分類: '', 複核分類: '觀望注意', 差異類型: '漏收', 嚴重度: '待確認', 處理: '已修正' },
  { 日期: '2026/11/06', 代號: '1590', 名稱: '亞德客-KY', 網站分類: '觀望注意', 複核分類: '會員持股', 差異類型: '分類', 嚴重度: '邊界', 處理: '只記錄' },
];
const data = { day: '2026/11/06', url: 'https://docs.google.com/spreadsheets/d/x', rows,
  summary: { 日期: '2026/11/06', 狀態: '已完成', 比對檔數: '18', 明確錯誤: '1', 待確認: '2', 邊界: '1', 複核錯: '0', 已修正: '2' } };
const m1 = ctx.lineAdminReviewFlex_(data);
assert.equal(m1.type, 'flex');
assert.equal(m1.contents.type, 'carousel');
assert.equal(m1.contents.contents.length, 3);            // 摘要＋兩檔已修改（台積電、已裁示的大立光）
assert.match(m1.altText, /已修正 2 項，待你裁示 1 項/);
const m2 = ctx.lineAdminPendingFlex_(data);
assert.equal(m2.contents.contents.length, 2);            // 格式說明＋群聯
const pick = m2.contents.contents[1].footer.contents[0].action;
assert.equal(pick.type, 'postback');
assert.equal(ctx.lineParseData_(pick.data).a, 'rulepick');
assert.equal(ctx.lineParseData_(pick.data).c, '8299');
assert.equal(ctx.lineParseData_(pick.data).d, '2026/11/06');
assert.equal(ctx.lineAdminPendingFlex_({ ...data, rows: rows.slice(0, 1) }), null);   // 沒有待裁示就不送第二則
const none = ctx.lineAdminReviewFlex_({ ...data, rows: [] });
assert.match(JSON.stringify(none), /網站內容與獨立判讀一致/);
const notRun = ctx.lineAdminReviewFlex_({ ...data, summary: { 狀態: '未執行', 備註: '今日無影片' } });
assert.match(notRun.altText, /未複核：今日無影片/);
// 超過十張只放十張＋「還有」
const many = Array.from({ length: 14 }, (_, i) => ({ ...rows[1], 代號: String(1000 + i), 名稱: '股' + i }));
const big = ctx.lineAdminPendingFlex_({ ...data, rows: many });
assert.equal(big.contents.contents.length, 12);
assert.match(JSON.stringify(big.contents.contents[11]), /另外 4 項/);
for (const msg of [m1, m2, big]) { assert(Buffer.byteLength(JSON.stringify(msg.contents)) < 50000, 'LINE 一則上限 50KB'); }
const confirm = ctx.lineAdminConfirmFlex_({ id: 'R1', date: '2026/11/06', code: '8299', name: '群聯', cat: '觀望注意', text: '本股正面等回穩' });
const acts = confirm.contents.footer.contents.map(b => ctx.lineParseData_(b.action.data).a);
assert.deepEqual([...acts], ['ruleok', 'ruleno']);
const result = ctx.lineAdminResultFlex_([{ 日期: '2026/11/06', 代號: '8299', 名稱: '群聯', 裁示分類: '觀望注意', 管理者說明: 'x', 狀態: '已完成', 結果: '已套用' },
  { 日期: '2026/11/06', 代號: '3008', 名稱: '大立光', 裁示分類: '觀望注意', 管理者說明: 'y', 狀態: '失敗', 結果: '原稿找不到引句' }]);
assert.match(result.altText, /完成 1 項，未完成或只記錄 1 項/);

// ---- 權限：沒開管理者模式的人不能用管理者指令，也不能下裁示 ----
let subs = [{ uid: 'Uadmin', channel: 'bot', friend: 'follow', tester: true, adminMode: true, daily: true, sms: false },
            { uid: 'Ubound', channel: 'bot', friend: 'follow', tester: true, adminMode: false, daily: false, sms: false },
            { uid: 'Uuser', channel: 'bot', friend: 'follow', tester: false, adminMode: true, daily: true, sms: false }];
ctx.lineSubsRead_ = () => ({ rows: subs });
ctx.lineBotId_ = () => 'bot';
ctx.lineTouch_ = () => {};
ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
cacheStore.clear();
assert.deepEqual([...ctx.lineAdminUids_(true)], ['Uadmin']);   // 沒綁定的人即使欄位被改成開啟也不算
assert.deepEqual([...ctx.lineRecipients_('admin', 'on').map(s => s.uid)], ['Uadmin']);
assert.deepEqual([...ctx.lineRecipients_('daily', 'on').map(s => s.uid)], ['Uadmin', 'Uuser']);   // 管理者照常收每日總覽
assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'adminpending' }, true)[0].text, /只開放給管理者/);
assert.equal(ctx.lineRoute_('Uuser', 'bot', { a: 'adminrule', rule: r, raw: 'x' }, true)[0].text, 'outofscope');
assert.match(ctx.lineRoute_('Ubound', 'bot', { a: 'ruleok', k: 'R1' }, true)[0].text, /只開放給管理者/);
// 管理者模式的快速回覆換成管理者那一組，回到使用者後恢復
ctx.lineRoute_('Uadmin', 'bot', { a: 'tab' }, true);
assert(ctx.lineQuickMain_().some(a => a.label === '待裁示'));
ctx.lineRoute_('Uuser', 'bot', { a: 'tab' }, true);
assert(!ctx.lineQuickMain_().some(a => a.label === '待裁示'));
// 沒綁定的人「管理者登入」只看到請先綁定
ctx.lineFindSub_ = uid => subs.find(s => s.uid === uid) || { uid, tester: false };
assert.match(ctx.lineAdminOnReply_('Uuser', 'bot')[0].text, /管理者綁定 123456/);

// ---- 觸發網址只接受 Anthropic 的 routine fire 端點 ----
const props = {};
ctx.lineProp_ = k => props[k] || '';
ctx.lineSetProp_ = (k, v) => { props[k] = v; };
assert.equal(ctx.lineAdminFire_(['R1']).reason, 'unset');
props.CLAUDE_RULING_FIRE_URL = 'https://evil.example.com/fire'; props.CLAUDE_RULING_FIRE_TOKEN = 't';
assert.equal(ctx.lineAdminFire_(['R1']).reason, 'unset');
let called = null;
ctx.UrlFetchApp = { fetch: (url, o) => { called = { url, o }; return { getResponseCode: () => 200 }; } };
props.CLAUDE_RULING_FIRE_URL = 'https://api.anthropic.com/v1/claude_code/routines/trig_01ABC/fire';
assert.equal(ctx.lineAdminFire_(['R1', 'R2']).ok, true);
assert.equal(called.o.headers['anthropic-beta'], 'experimental-cc-routine-2026-04-01');
assert.match(JSON.parse(called.o.payload).text, /R1,R2/);

console.log('LINE 管理者模式：解析、期限、卡片、權限、收件人與觸發檢查通過');

// ---- r1：隨時查看時跳過「未執行」的日子 ----
(() => {
  const sheet = (vals) => ({ getLastRow: () => vals.length, getLastColumn: () => vals[0].length,
    getRange: () => ({ getDisplayValues: () => vals }) });
  const sums = [['日期', '狀態'], ['2026/11/06', '已完成'], ['2026/11/07', '未執行'], ['2026/11/08', '未執行']];
  const detail = [['日期', '代號', '嚴重度', '處理'], ['2026/11/06', '8299', '待確認', '待管理者']];
  ctx.dailyReviewBook_ = () => ({ getSheetByName: n => (n === '每日摘要' ? sheet(sums) : n === '差異明細' ? sheet(detail) : null), getUrl: () => 'u' });
  const d = ctx.lineAdminReviewData_('2026/11/08', true);
  assert.equal(d.day, '2026/11/06');
  assert.equal(d.rows.length, 1);
  assert.equal(ctx.lineAdminReviewData_('2026/11/08', false).summary['狀態'], '未執行');   // 自動推送仍看當天
})();
console.log('r1 隨時查看跳過未執行通過');
