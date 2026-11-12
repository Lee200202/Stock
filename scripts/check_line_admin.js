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
const ORIG = { lineAdminReviewReply_: ctx.lineAdminReviewReply_, lineAdminPendingReply_: ctx.lineAdminPendingReply_, lineAdminPendingFlex_: ctx.lineAdminPendingFlex_ };   // 後面的測試會換掉，需要時還原

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
assert.equal(m1.contents.contents.length, 5);            // 2026/11/11：摘要＋每一項一張（明確錯誤、待確認兩項、邊界）
{
  const cards = m1.contents.contents.slice(1);
  const kick = cards.map(c => c.header.contents[0].text);
  assert.deepEqual([...kick], ['明確錯誤・已修正', '待確認・待你裁示', '待確認・已修正', '邊界・只記錄']);   // 依分級排序、標題寫處理狀態
  assert.deepEqual([...cards.map(c => c.header.backgroundColor)], ['#9B1C1C', '#D0645A', '#D0645A', '#4A9A6A']);   // 顏色＝分級
  assert.equal(ctx.lineParseData_(cards[1].footer.contents[0].action.data).a, 'rulepick');                          // 待你裁示可直接裁示
  assert.equal(cards[3].footer, undefined);                                                                          // 只記錄的邊界沒有按鈕
  const sevBtns = m1.contents.contents[0].footer.contents.filter(b => b.action.type === 'postback').map(b => ctx.lineParseData_(b.action.data));
  assert.deepEqual([...sevBtns.map(b => b.s)], ['明確錯誤', '待確認', '邊界']);                                      // 有項目的分級才有「看…」按鈕
  const onlyPending = ctx.lineAdminReviewFlex_(data, '待確認');
  assert.equal(onlyPending.contents.contents.length, 3);
  assert.match(onlyPending.altText, /11\/06 待確認 2 項/);
  assert.equal(onlyPending.contents.contents[0].header.backgroundColor, '#D0645A');
}
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
assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'adminpending' }, true)[0].text, /目前沒有提供這項功能/);   // 2026/11/11：使用者端不透露管理者功能
assert.doesNotMatch(ctx.lineRoute_('Uuser', 'bot', { a: 'adminpending' }, true)[0].text, /管理者/);
assert.equal(ctx.lineRoute_('Uuser', 'bot', { a: 'adminrule', rule: r, raw: 'x' }, true)[0].text, 'outofscope');
assert.match(ctx.lineRoute_('Ubound', 'bot', { a: 'ruleok', k: 'R1' }, true)[0].text, /先輸入「管理者登入」/);   // 綁定過的帳號才提示切換
// 管理者模式的快速回覆換成管理者那一組，回到使用者後恢復
ctx.lineRoute_('Uadmin', 'bot', { a: 'tab' }, true);
assert(ctx.lineQuickMain_().some(a => a.label === '待裁示'));
ctx.lineRoute_('Uuser', 'bot', { a: 'tab' }, true);
assert(!ctx.lineQuickMain_().some(a => a.label === '待裁示'));
// 沒綁定的人「管理者登入」：2026/11/11 起不透露管理者功能與綁定方式（綁定說明只在網站後台）
ctx.lineFindSub_ = uid => subs.find(s => s.uid === uid) || { uid, tester: false };
assert.match(ctx.lineAdminOnReply_('Uuser', 'bot')[0].text, /目前沒有提供這項功能/);
assert.doesNotMatch(ctx.lineAdminOnReply_('Uuser', 'bot')[0].text, /管理者|綁定/);

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

// ---- r2：測試雲端（只限管理者） ----
assert.equal(ctx.lineParseText_('測試雲端', 'U1').a, 'admintest');
assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'admintest' }, true)[0].text, /目前沒有提供這項功能/);
called = null;
assert.match(ctx.lineRoute_('Uadmin', 'bot', { a: 'admintest' }, true)[0].text, /雲端觸發成功/);
assert.match(JSON.parse(called.o.payload).text, /測試/);
ctx.UrlFetchApp = { fetch: () => ({ getResponseCode: () => 401 }) };
assert.match(ctx.lineAdminTestReply_()[0].text, /HTTP 401/);
console.log('r2 測試雲端通過');

// ---- v220：一般句子的裁示、身分確認、進度回報（2026/11/09 勤誠「需是買進或會員持股，應根據簡訊方向判定」被當成查個股） ----
(() => {
  const L = s => ctx.lineAdminLooseRuling_(s);
  const q = L('勤誠需是買進或會員持股，應根據簡訊方向判定，請稍作調整');
  assert.equal(q.lead, '勤誠'); assert.deepEqual([...q.cats], ['買入', '會員持股']); assert.equal(q.notice, true); assert.equal(q.month, 0);
  const q2 = L('11/06 3008 應改為觀望注意，原稿說等量縮');
  assert.equal(q2.month, 11); assert.equal(q2.day, 6); assert.equal(q2.code, '3008'); assert.deepEqual([...q2.cats], ['觀望注意']);
  assert.equal(L('勤誠的說明要修正，列為會員持股才對').lead, '勤誠');
  assert.deepEqual([...L('8210 依簡訊應列買入，570元以下').cats], ['買入']);
  assert.equal(L('8210 依簡訊應列買入，570元以下').code, '8210');        // 570 元不是代號
  // 問句、沒有裁示語氣、沒有分類詞都不是裁示
  for (const s of ['台積電應該買進嗎', '請問勤誠是不是會員持股', '我覺得勤誠可以買進', '會員持股', '持股追蹤', '勤誠今天怎麼說', '應該要注意', '今天有影片嗎']) {
    assert.equal(L(s), null, s);
  }
  assert.equal(ctx.lineParseText_('勤誠需是買進或會員持股，應根據簡訊方向判定，請稍作調整', 'U1').a, 'adminfree');
  assert.equal(ctx.lineParseText_('勤誠需是買進或會員持股，應根據簡訊方向判定', 'U1', true).a !== 'adminfree', true);   // noRule 時照一般解析
  assert.equal(ctx.lineParseText_('裁示進度', 'U1').a, 'adminprogress');
  assert.equal(ctx.lineParseText_('2330 會員持股—紀錄上還沒出場', 'U1').a, 'adminrule');   // 舊格式照舊

  // 套到哪一檔、哪一天：差異明細裡還沒處理完的列優先（11/09 勤誠修正未完成）
  const det = [
    { 日期: '2026/11/09', 代號: '8210', 名稱: '勤誠', 嚴重度: '明確錯誤', 處理: '待管理者' },
    { 日期: '2026/11/05', 代號: '8210', 名稱: '勤誠', 嚴重度: '明確錯誤', 處理: '已修正' },
    { 日期: '2026/11/09', 代號: '—', 名稱: '公開文字', 嚴重度: '明確錯誤', 處理: '待管理者' },
    { 日期: '2026/09/01', 代號: '2330', 名稱: '台積電', 嚴重度: '待確認', 處理: '待管理者' },
  ];
  const open = ctx.lineAdminOpenRows_(det, '2026/11/09', 30);
  assert.deepEqual([...open.map(r => r.代號)], ['8210']);
  const resolve = s => (s === '勤誠' || s === '8210' ? { code: '8210', name: '勤誠' } : null);
  let t = ctx.lineAdminPickTarget_(q, open, null, resolve, '2026/11/10');
  assert.deepEqual({ ...t }, { date: '2026/11/09', code: '8210', name: '勤誠' });   // 隔天才裁示也套到 11/09
  t = ctx.lineAdminPickTarget_(L('2026/11/05 勤誠應改成會員持股'), open, null, resolve, '2026/11/10');
  assert.equal(t.date, '2026/11/05');
  t = ctx.lineAdminPickTarget_(L('應改為觀望注意，原稿說等量縮回穩'), [], null, resolve, '2026/11/10');
  assert.equal(t.error, 'stock');
  t = ctx.lineAdminPickTarget_(L('應改為觀望注意，原稿說等量縮回穩'), [], { date: '2026/11/06', code: '3008', name: '大立光' }, resolve, '2026/11/10');
  assert.deepEqual({ ...t }, { date: '2026/11/06', code: '3008', name: '大立光' });   // 先按「裁示這一檔」

  // 身分：管理者模式→確認卡；綁定但登出→身分確認；沒綁定→一般回覆
  ctx.lineResolveStock_ = s => resolve(s) || {};
  ctx.dailyReviewBook_ = () => null;
  ctx.todayStr_ = () => '2026/11/09';
  ctx.lineMd_ = ctx.lineMd_ || (d => d);
  cacheStore.clear();
  const act = ctx.lineParseText_('勤誠需是買進或會員持股，應根據簡訊方向判定，請稍作調整', 'Uadmin');
  const asAdmin = ctx.lineRoute_('Uadmin', 'bot', act, true);
  assert.match(asAdmin[0].altText, /確認裁示（管理者）.*勤誠 8210 → 買入／會員持股/);
  assert.match(JSON.stringify(asAdmin[0]), /依當天會員通知（簡訊）的方向判定/);
  assert.match(JSON.stringify(asAdmin[0]), /管理者身分/);
  const asBound = ctx.lineRoute_('Ubound', 'bot', act, true);
  assert.match(asBound[0].altText, /你目前是使用者身分/);
  const btn = asBound[0].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data).a);
  assert.deepEqual([...btn], ['ruleresume', 'rulequery']);
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  ctx.lineStockReply_ = q => [{ type: 'text', text: 'stock:' + q }];
  gem = 0;   // 2026/11/11：管理者那一句會先走第一關（Gemini）；這裡只驗使用者不呼叫
  const asUser = ctx.lineRoute_('Uuser', 'bot', act, true);
  assert.doesNotMatch(JSON.stringify(asUser), /裁示|管理者/);              // 一般使用者不透露管理者功能
  // 「切換管理者並裁示」→ 開管理者模式，接著出確認卡
  let modeSet = null;
  ctx.lineUpsertSub_ = (uid, ch, fn) => { const s = subs.find(x => x.uid === uid); fn(s); modeSet = [uid, s.adminMode]; return { sub: s }; };
  const resumed = ctx.lineRoute_('Ubound', 'bot', { a: 'ruleresume' }, true);
  assert.deepEqual(modeSet, ['Ubound', true]);
  assert.match(resumed[0].text, /已切換為管理者身分/);
  assert.match(resumed[1].altText, /確認裁示（管理者）/);
  subs.find(x => x.uid === 'Ubound').adminMode = false; ctx.lineAdminUids_(true);
  // 「當作一般查詢」→ 照一般解析回覆
  ctx.lineRoute_('Ubound', 'bot', act, true);
  assert.doesNotMatch(JSON.stringify(ctx.lineRoute_('Ubound', 'bot', { a: 'rulequery' }, true)), /確認裁示/);
  assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'adminprogress' }, true)[0].text, /目前沒有提供這項功能/);

  // 修正未完成的卡片也能「裁示這一檔」
  const fx = ctx.lineAdminReviewFlex_({ ...data, day: '2026/11/09', rows: [{ ...det[0], 網站分類: '觀望注意＋會員持股', 複核分類: '會員持股' }] });
  const fail = fx.contents.contents[1];
  assert.equal(ctx.lineParseData_(fail.footer.contents[0].action.data).a, 'rulepick');
  assert.equal(ctx.lineParseData_(fail.footer.contents[0].action.data).c, '8210');

  // 進度回報：接手回報一次、結果回報一次；舊版只寫時間戳記的列視為已回報結果
  const rp = ctx.lineAdminReportPlan_([
    { 裁示ID: 'A', 狀態: '處理中', LINE回報: '' }, { 裁示ID: 'B', 狀態: '套用中', LINE回報: '接手 2026/11/09 14:00:00' },
    { 裁示ID: 'C', 狀態: '已完成', LINE回報: '接手 2026/11/09 14:00:00' }, { 裁示ID: 'D', 狀態: '失敗', LINE回報: '2026/11/08 22:00:00' },
    { 裁示ID: 'E', 狀態: '已完成', LINE回報: '' }, { 裁示ID: 'F', 狀態: '待處理', LINE回報: '' }]);
  assert.deepEqual([...rp.started.map(r => r.裁示ID)], ['A']);
  assert.deepEqual([...rp.final.map(r => r.裁示ID)], ['C', 'E']);
  const st = ctx.lineAdminStartedFlex_([{ 日期: '2026/11/09', 代號: '8210', 名稱: '勤誠', 裁示分類: '買入／會員持股', 管理者說明: 'x', 狀態: '處理中' }]);
  assert.match(st.altText, /勤誠 雲端已接手/);
  assert.equal(st.contents.body.contents[0].contents.length, 2);           // 兩個分類兩個色塊
})();
console.log('v220 一般句子裁示、身分確認、進度回報通過');

// ---- 2026/11/10：「股票 分類—說明」為主格式、部署後推一次驗收卡 ----
(() => {
  let r2 = ctx.lineAdminParseRuling_('勤誠 買入—簡訊明寫請買進，依簡訊方向判定');
  assert.equal(r2.name, '勤誠'); assert.equal(r2.cat, '買入'); assert.match(r2.text, /依簡訊方向/);
  r2 = ctx.lineAdminParseRuling_('11/09 8210 買入—簡訊明寫請買進');
  assert.equal(r2.code, '8210'); assert.equal(r2.day, 9);
  const card = ctx.lineAdminFormatFlex_();
  assert.match(card.altText, /股票 分類—說明/);
  assert.match(JSON.stringify(card), /格式：股票 分類—說明/);
  // 卡片上的每個範例都要真的解析得出來（不可以示範一個系統讀不懂的格式）
  const examples = JSON.stringify(card).match(/"text":"[^"]*"/g).map(s => s.slice(8, -1))
    .filter(s => /^(?:\d{1,2}\/\d{1,2}\s)?[^\s—「」：]+\s[^\s—]+—/.test(s));
  assert.ok(examples.length >= 4, examples.join('|'));
  for (const ex of examples) { assert.ok(ctx.lineAdminParseRuling_(ex), '範例解析不到：' + ex); }
  const props = new Map();
  let queued = 0, delivered = 0;
  ctx.PropertiesService = { getScriptProperties: () => ({ getProperty: k => (props.has(k) ? props.get(k) : null), setProperty: (k, v) => props.set(k, v) }) };
  ctx.todayStr_ = () => '2026/11/10';
  ctx.lineAdminUids_ = () => ['Uadmin'];
  ctx.lineQueue_ = job => { queued++; assert.match(JSON.stringify(job.messages), /管理者・裁示驗收/); return { created: true, id: job.id }; };
  ctx.lineDeliverTick_ = () => { delivered++; };
  ctx.Utilities = { formatDate: () => '2240' };
  assert.equal(ctx.lineAdminVerifyTick_(), 'queued');
  assert.equal(ctx.lineAdminVerifyTick_(), 'sent');                         // 同一版只推一次
  assert.equal(queued, 1); assert.equal(delivered, 1);
  props.clear(); ctx.Utilities = { formatDate: () => '0230' };
  assert.equal(ctx.lineAdminVerifyTick_(), 'window');                       // 半夜不推
  ctx.Utilities = { formatDate: () => '1000' }; ctx.lineAdminUids_ = () => [];
  assert.equal(ctx.lineAdminVerifyTick_(), 'no-admin');                     // 沒有開著管理者模式的人就不推
})();
console.log('11/10 股票 分類—說明格式、驗收卡只推一次通過');

// ---- 2026/11/10：管理者用一般問句查（規則先、Gemini 後；非管理者不呼叫模型） ----
(() => {
  ctx.todayStr_ = () => '2026/11/10';
  const qi = s => ctx.lineAdminQueryIntent_(s, '2026/11/10');
  assert.deepEqual({ ...qi('11月9號待裁示的有哪些') }, { intent: 'pending', day: '2026/11/09' });
  assert.deepEqual({ ...qi('昨天的稽核結果') }, { intent: 'review', day: '2026/11/09' });
  assert.deepEqual({ ...qi('11/6 改了哪些') }, { intent: 'review', day: '2026/11/06' });
  assert.deepEqual({ ...qi('前天還有待確認的嗎') }, { intent: 'pending', day: '2026/11/08' });
  assert.equal(qi('裁示進度如何').intent, 'progress');
  assert.equal(qi('台積電'), null);
  assert.equal(ctx.lineAdminSpokenDate_('12/30 待裁示', '2026/01/05'), '2025/12/30');   // 一月問十二月是去年
  const act = ctx.lineParseText_('11月9號待裁示的有哪些', 'Ua', false);
  assert.equal(act.a, 'adminquery'); assert.equal(act.q.day, '2026/11/09');

  const calls = [];
  ctx.lineAdminPendingReply_ = d => { calls.push(['pending', d]); return [{ type: 'text', text: 'pending ' + d }]; };
  ctx.lineAdminReviewReply_ = d => { calls.push(['review', d]); return [{ type: 'text', text: 'review ' + d }]; };
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  ctx.lineTouch_ = () => {};
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', act, true)[0].text, 'pending 2026/11/09');
  // 非管理者：不透露管理者功能，改回一般解析
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  ctx.lineStockReply_ = q => [{ type: 'text', text: 'stock:' + q }];
  ctx.lineFindSub_ = () => ({});
  let geminiCalls = 0;
  ctx.callGemini_ = () => { geminiCalls++; return '{"intent":"review","date":"2026/11/09","stock":""}'; };
  assert.doesNotMatch(JSON.stringify(ctx.lineRoute_('Uuser', 'bot', act, true)), /pending|裁示|管理者/);
  // 規則抓不到的管理者句子 → Gemini 判斷意圖與日期，答案由既有函式產生
  const vague = ctx.lineParseText_('那天被抓出來的問題後來都處理好了沒', 'Uadmin', false);
  assert.equal(vague.a, 'outofscope');
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].text, 'review 2026/11/09');
  assert.equal(geminiCalls, 1);
  assert.equal(ctx.lineRoute_('Uuser', 'bot', vague, true)[0].text, 'outofscope');
  assert.equal(geminiCalls, 1);                                                      // 非管理者不呼叫模型
  ctx.callGemini_ = () => { throw new Error('QUOTA_DAILY'); };
  // 2026/11/11：管理者模式下模型失敗不再回使用者端的「不在服務範圍」，改出送雲端確認卡
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/10', rows: [], summary: {} });
  assert.match(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].altText, /送給雲端・管理者提問/);
  ctx.lineResolveStock_ = q => /台積電|2330/.test(q) ? { code: '2330', name: '台積電' } : { none: true, q };
  ctx.callGemini_ = () => '{"intent":"stock","date":"","stock":"台積電"}';
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].text, 'stock:台積電');
})();
console.log('11/10 管理者一般問句：規則判斷日期與意圖、Gemini 備援、非管理者不呼叫模型通過');

// ---- 2026/11/11：管理者指示（回答複核問題、補充規則）送雲端 ----
(() => {
  const L = s => ctx.lineAdminLooksLikeInstruction_(s);
  const answer = '追買不算外洩，外洩只看價格；行程其實就是8210勤誠，集中股票講的是宜鼎，請寫進週五檢討';
  assert.ok(L(answer));
  assert.ok(L('11/11 那兩件事：追買這個不算外洩，請寫進週五的流程'));
  for (const s of ['台積電', '請幫我查台積電', '今天有影片嗎', '那天被抓出來的問題後來都處理好了沒', '11月9號待裁示的有哪些']) assert.ok(!L(s), s);
  // 解析：不是逐檔裁示、不是查詢句 → 一般句子；管理者路由成指示確認卡
  const act = ctx.lineParseText_(answer, 'Uadmin', false);
  assert.notEqual(act.a, 'adminrule'); assert.notEqual(act.a, 'adminquery');
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', rows: [], summary: {} });
  ctx.Utilities = { formatDate: () => '20261111123000' };
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  let gem = 0; ctx.callGemini_ = () => { gem++; return '{"intent":"other"}'; };
  const out = ctx.lineRoute_('Uadmin', 'bot', act, true);
  assert.match(out[0].altText, /送給雲端・管理者指示/);
  const btns = out[0].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data).a);
  assert.deepEqual([...btns], ['ruleok', 'rulequery', 'ruleno']);
  const draft = JSON.parse(store.get('line_rule_draft_Uadmin'));
  assert.equal(draft.cat, '指示'); assert.equal(draft.code, ''); assert.equal(draft.date, '2026/11/11'); assert.match(draft.text, /追買/);
  // 使用者：不透露、不呼叫模型
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  ctx.lineStockReply_ = q => [{ type: 'text', text: 'stock:' + q }];
  gem = 0;   // 2026/11/11：管理者那一句會先走第一關（Gemini）；這裡只驗使用者不呼叫
  const asUser = ctx.lineRoute_('Uuser', 'bot', act, true);
  assert.doesNotMatch(JSON.stringify(asUser), /指示|管理者/);
  assert.equal(gem, 0);
  // 管理者查股照舊
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', { a: 'stock', q: '台積電' }, true)[0].text, 'stock:台積電');
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', { a: 'stock', q: '請幫我查台積電' }, true)[0].text, 'stock:請幫我查台積電');
  // Gemini 判定為指示的短句也走確認卡
  ctx.callGemini_ = () => '{"intent":"instruction","date":"","stock":""}';
  const vague = { a: 'outofscope', kind: 'general', raw: '那個詞我覺得沒問題' };
  assert.match(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].altText, /管理者指示/);
})();
console.log('11/11 管理者指示：一般句子送雲端確認卡、查股照舊、使用者不透露不呼叫模型通過');

// ---- 2026/11/11（管理者）：管理者模式只接管理者的話，不落到使用者端的「找不到這檔股票」 ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', rows: [], summary: {} });
  ctx.lineResolveStock_ = q => /台積電|2330/.test(q) ? { code: '2330', name: '台積電' } : { none: true, q };
  const pend = [];
  ctx.lineAdminPendingReply_ = d => { pend.push(d); return [{ type: 'text', text: 'pending ' + d }]; };
  // 「需要管理者裁示（語音寫成才是）」的問句由規則直接列待裁示
  for (const s of ['今天有沒有需要管理者才是的事情請明確列出', '今天有沒有需要管理者裁示的事情', '今日有要我決定的嗎', '今天有沒有需要我決定的事情']) {
    const a = ctx.lineParseText_(s, 'Uadmin', false);
    assert.equal(a.a, 'adminquery', s); assert.equal(a.q.intent, 'pending', s);
    assert.equal(ctx.lineRoute_('Uadmin', 'bot', a, true)[0].text, 'pending 2026/11/11', s);
  }
  // 查不到的股票、模型判不出來的句子 → 送雲端確認卡（提問），不回「找不到」
  let gem = 0; ctx.callGemini_ = () => { gem++; return '{"intent":"stock","date":"","stock":"今日"}'; };
  const ask = ctx.lineRoute_('Uadmin', 'bot', { a: 'outofscope', kind: 'general', raw: '今天雲端回報的結果你看過了嗎' }, true);
  assert.match(ask[0].altText, /送給雲端・管理者提問/); assert.equal(gem, 1);
  const st = ctx.lineRoute_('Uadmin', 'bot', { a: 'stock', q: '今日雲端到底改了什麼內容' }, true);
  assert.match(st[0].altText, /送給雲端/);
  assert.doesNotMatch(JSON.stringify(st), /找不到/);
  // 查得到的股票照舊查；打招呼不送雲端
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', { a: 'stock', q: '台積電' }, true)[0].text, 'stock:台積電');
  assert.ok(!ctx.lineAdminSendable_('謝謝！')); assert.ok(ctx.lineAdminSendable_('今天的結果呢'));
  // 提問的回報卡顯示「回答」並保留較長的雲端說明
  const bubble = JSON.stringify(ctx.lineAdminRuleBubble_({ '狀態': '已記錄', '日期': '2026/11/11', '名稱': '管理者提問', '代號': '', '裁示分類': '指示',
    '管理者說明': '今天有沒有要我決定的', '結果': '甲'.repeat(350) }));
  assert.match(bubble, /回答/); assert.match(bubble, /甲{350}/);
  // 使用者：同一句照一般查詢，不出管理者卡
  ctx.lineFindSub_ = () => ({});
  assert.doesNotMatch(JSON.stringify(ctx.lineRoute_('Uuser', 'bot', { a: 'stock', q: '今日雲端到底改了什麼內容' }, true)), /送給雲端|管理者/);
})();
console.log('11/11 管理者模式只接管理者的話：待裁示問句直接列出、其餘送雲端確認、查股與使用者端照舊通過');

// ---- 2026/11/11（管理者）：第一關 Claude Sonnet 理解管理者的話；查詢直接回答、修改才送 Opus ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: { 狀態: '已完成', 比對檔數: '9' },
    rows: [{ 日期: '2026/11/11', 代號: '2317', 名稱: '鴻海', 網站分類: '觀望不碰', 複核分類: '觀望注意', 差異類型: '分類', 嚴重度: '邊界', 處理: '只記錄' }] });
  ctx.lineAdminBook_ = () => null;
  ctx.lineResolveStock_ = q => /台積電|2330/.test(q) ? { code: '2330', name: '台積電' } : { none: true, q };
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  const props = { ANTHROPIC_API_KEY: 'test-key' };
  const prevProp = ctx.lineProp_; ctx.lineProp_ = k => props[k] || '';
  let answer = null, status = 200, sent = [];
  ctx.UrlFetchApp = { fetch: (url, opt) => {
    sent.push({ url, opt, body: JSON.parse(opt.payload) });
    return { getResponseCode: () => status, getContentText: () => JSON.stringify(
      { stop_reason: 'end_turn', content: [{ type: 'thinking', thinking: '' }, { type: 'text', text: JSON.stringify(answer) }] }) };
  } };
  const say = (text, a) => { answer = { intent: 'unclear', date: '', severity: '', stock: '', reply: '', summary: '', ...a }; sent = [];
    return ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_(text, 'Uadmin', false), true); };
  const calls = [];
  ctx.lineAdminReviewReply_ = (d, sev) => { calls.push(['review', d, sev]); return [{ type: 'text', text: 'review ' + d + ' ' + sev }]; };
  ctx.lineAdminPendingReply_ = d => { calls.push(['pending', d]); return [{ type: 'text', text: 'pending ' + d }]; };

  // 1. 「待確認的明細」→ 那一級的明細（規則原本會給待裁示清單）
  let out = say('我想查看待確認的明細是什麼', { intent: 'review', severity: '待確認' });
  assert.equal(out[0].text, 'review  待確認');
  const req = sent[0];
  assert.equal(req.url, 'https://api.anthropic.com/v1/messages');
  assert.equal(req.body.model, 'claude-sonnet-5-5');
  assert.equal(req.opt.headers['anthropic-version'], '2026-10-21');
  assert.equal(req.opt.headers['x-api-key'], 'test-key');
  assert.equal(req.body.output_config.format.type, 'json_schema');
  assert.equal(req.body.output_config.effort, 'low');
  assert.match(req.body.messages[0].content, /鴻海/);                      // 複核資料有帶上
  assert.match(req.body.messages[0].content, /我想查看待確認的明細是什麼/);
  // 2. 能直接回答的問題：用第一關的答案，不送雲端
  out = say('鴻海為什麼是邊界', { intent: 'answer', reply: '11/11 鴻海 2317：網站觀望不碰，複核判觀望注意，兩者都可接受，所以只記錄。' });
  assert.match(out[0].text, /鴻海 2317/);
  assert.equal(store.has('line_rule_draft_Uadmin'), false);
  // 3. 要改東西：確認卡帶「我的理解」，按確認送出才交給 Opus；雲端看到的是原話＋理解
  out = say('鴻海其實應該是注意，請改', { intent: 'change', date: '2026/11/11', summary: '把 11/11 鴻海 2317 從觀望不碰改成觀望注意。' });
  assert.match(out[0].altText, /送給雲端・管理者指示/);
  assert.match(JSON.stringify(out[0]), /我的理解/);
  let draft = JSON.parse(store.get('line_rule_draft_Uadmin'));
  assert.equal(draft.cat, '指示'); assert.equal(draft.name, '管理者指示'); assert.equal(draft.date, '2026/11/11');
  assert.match(draft.text, /^鴻海其實應該是注意，請改\n〔第一關理解，原話為準〕把 11\/11 鴻海/);
  // 4. 要查原稿才答得出來：提問卡
  out = say('今天原稿有講到宜鼎的成本嗎', { intent: 'ask_cloud', summary: '查 11/11 原稿宜鼎的成本價。' });
  assert.match(out[0].altText, /送給雲端・管理者提問/);
  // 5. 第一關說是查股，且查得到 → 查股；查不到 → 交回規則（送雲端確認卡），不回「找不到」
  ctx.lineStockReply_ = q => [{ type: 'text', text: 'stock:' + q }];
  assert.equal(say('幫我看一下台積電最近被提到的紀錄', { intent: 'stock', stock: '台積電' })[0].text, 'stock:台積電');
  assert.doesNotMatch(JSON.stringify(say('那個今日的東西幫我看一下好了', { intent: 'stock', stock: '今日' })), /找不到/);
  // 6. 單純輸入股票不呼叫第一關
  sent = []; assert.equal(ctx.lineRoute_('Uadmin', 'bot', { a: 'stock', q: '台積電' }, true)[0].text, 'stock:台積電'); assert.equal(sent.length, 0);
  // 7. API 失敗或回覆不合格 → 照規則（不卡住）
  status = 500;
  assert.equal(say('今天有沒有需要管理者裁示的事情', {})[0].text, 'pending 2026/11/11');
  status = 200; answer = { intent: 'nonsense' };
  assert.equal(ctx.lineAdminAiParse_({ stop_reason: 'end_turn', content: [{ type: 'text', text: '{"intent":"nonsense"}' }] }), null);
  assert.equal(ctx.lineAdminAiParse_({ stop_reason: 'refusal', content: [] }), null);
  // 8. 沒設金鑰 → 不呼叫
  delete props.ANTHROPIC_API_KEY; sent = [];
  say('今天有沒有需要管理者裁示的事情', { intent: 'answer', reply: 'x' }); assert.equal(sent.length, 0);
  // 9. 一般使用者永遠不呼叫第一關
  props.ANTHROPIC_API_KEY = 'test-key'; sent = []; ctx.lineFindSub_ = () => ({});
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  ctx.lineRoute_('Uuser', 'bot', ctx.lineParseText_('鴻海為什麼是邊界', 'Uuser', false), true);
  assert.equal(sent.length, 0);
  ctx.lineProp_ = prevProp; delete ctx.UrlFetchApp;
})();
console.log('11/11 第一關 Claude Sonnet：分級明細、直接回答、修改確認卡帶理解、提問送雲端、失敗回規則、使用者不呼叫通過');

// ---- 2026/11/11（管理者）：不付 API 費用時第一關用 Gemini；管理者圖文選單只連到管理者帳號 ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: {}, rows: [] });
  ctx.lineAdminBook_ = () => null;
  const prevProp = ctx.lineProp_; const props = { LINE_CHANNEL_ACCESS_TOKEN: 't' }; ctx.lineProp_ = k => props[k] || '';
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  // 1. 沒設 ANTHROPIC_API_KEY：第一關改用 Gemini，同一份提示，回覆照樣分流
  let gemArgs = null; ctx.callGemini_ = (sys, user, opt) => { gemArgs = { sys, user, opt };
    return JSON.stringify({ intent: 'review', date: '', severity: '邊界', stock: '', reply: '', summary: '' }); };
  const calls = []; ctx.lineAdminReviewReply_ = (d, sev) => { calls.push(sev); return [{ type: 'text', text: 'review ' + sev }]; };
  const api = []; ctx.lineApi_ = (m, path) => { api.push(m + ' ' + path); return { code: 200, json: {} }; };
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('我想看邊界的那幾項', 'Uadmin', false), true)[0].text, 'review 邊界');
  assert.match(gemArgs.sys, /第一關/); assert.match(gemArgs.sys, /intent、date、severity/); assert.equal(gemArgs.opt.json, true);
  // 2. 管理者選單：定義六格、熱區不超出 2500×1686
  const def = ctx.lineAdminMenuDef_();
  assert.equal(def.areas.length, 6); assert.equal(def.chatBarText, '管理者');
  for (const a of def.areas) { assert.ok(a.bounds.x + a.bounds.width <= 2500 && a.bounds.y + a.bounds.height <= 1686); }
  assert.deepEqual([...def.areas.map(a => a.action.data ? ctx.lineParseData_(a.action.data).a : a.action.text)],
    ['adminreview', 'adminpending', 'adminprogress', 'askstock', 'adminformat', '取消管理者']);
  // 3. 管理者模式的訊息會補連結選單（已有就不重複）；回到使用者解除
  props.LINE_ADMIN_MENU = JSON.stringify({ id: 'RM1', name: 'zz-admin-v1' });
  api.length = 0; store.clear();
  ctx.lineRoute_('Uadmin', 'bot', { a: 'adminprogress' }, true);
  assert.ok(api.includes('post /v2/bot/user/Uadmin/richmenu/RM1'), '進入管理者要連結管理者選單');
  api.length = 0; ctx.lineRoute_('Uadmin', 'bot', { a: 'adminprogress' }, true);
  assert.ok(!api.some(x => /\/v2\/bot\/user\/Uadmin\/richmenu/.test(x)), '六小時內不重複連結');
  api.length = 0; ctx.lineAdminMenuLink_('Uadmin', false);
  assert.ok(api.includes('delete /v2/bot/user/Uadmin/richmenu'), '回到使用者要解除');
  // 一般使用者不連結
  api.length = 0; ctx.lineFindSub_ = () => ({}); ctx.lineRoute_('Uuser', 'bot', { a: 'help' }, true);
  assert.ok(!api.some(x => /richmenu/.test(x)));
  ctx.lineProp_ = prevProp;
})();
console.log('11/11 免費第一關（Gemini）與管理者圖文選單：分流、六格熱區、進入連結、回到使用者解除通過');

// ---- 2026/11/11（管理者）：單一股票只回一張卡；每日複核問題用選項卡；使用者端嚴格不顯示管理者功能 ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  const prevProp = ctx.lineProp_; ctx.lineProp_ = () => '';
  ctx.callGemini_ = () => { throw new Error('offline'); };          // 第一關失敗 → 規則
  ctx.lineAdminReviewReply_ = ORIG.lineAdminReviewReply_; ctx.lineAdminPendingReply_ = ORIG.lineAdminPendingReply_;   // 前面測試換掉的回到原函式
  const rows = [
    { 日期: '2026/11/11', 代號: '2317', 名稱: '鴻海', 網站分類: '觀望不碰', 複核分類: '觀望注意', 差異類型: '分類', 嚴重度: '邊界', 處理: '只記錄', 判斷理由: '23幾是買點但250有壓力' },
    { 日期: '2026/11/11', 代號: '③', 名稱: '③教學重點', 差異類型: '章節過短', 嚴重度: '待確認', 處理: '已修正' },
    { 日期: '2026/11/11', 代號: '3661', 名稱: '世芯-KY', 差異類型: '漏收', 嚴重度: '邊界', 處理: '只記錄' }];
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: { 狀態: '已完成' }, rows });
  // 1. 「鴻海為什麼是邊界」不再是整天的稽核：規則不接，第一關失敗時只回鴻海那一張
  assert.equal(ctx.lineAdminQueryIntent_('鴻海為什麼是邊界', '2026/11/11'), null);
  let out = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('鴻海為什麼是邊界', 'Uadmin', false), true);
  assert.equal(out.length, 1); assert.equal(out[0].contents.type, 'bubble');
  assert.match(out[0].altText, /鴻海 2317：邊界・只記錄/);
  assert.equal(out[0].contents.header.backgroundColor, '#4A9A6A');
  // 第一關回 review＋stock 也只回那一檔；查不到就說沒提到
  assert.equal(ctx.lineAdminReviewReply_('', '', '世芯-KY')[0].contents.type, 'bubble');
  assert.match(ctx.lineAdminReviewReply_('', '', '台積電')[0].text, /沒有提到「台積電」/);
  // 2. 每日複核問題：選項卡 → 選 → 確認卡 → 確認送出寫裁示並把題目改已回答
  const qrows = [{ _row: 2, 問題ID: 'Q1', 日期: '2026/11/11', 問題: '「行程」指的是哪一檔？', 背景: '原稿：行程這一檔…', 選項: '["勤誠 8210","其他股票"]', 狀態: '待回答', LINE推送: '' }];
  ctx.lineAdminQuestions_ = st => qrows.filter(r => !st || r.狀態 === st);
  const card = ctx.lineAdminQuestionsFlex_(qrows);
  const btns = card.contents.footer.contents.map(b => ctx.lineParseData_(b.action.data));
  assert.deepEqual([...btns.map(b => b.a)], ['qpick', 'qpick', 'qcustom']);
  assert.equal(card.contents.header.backgroundColor, '#D0645A');
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  out = ctx.lineRoute_('Uadmin', 'bot', { a: 'qpick', q: 'Q1', o: '0' }, true);
  assert.match(out[0].altText, /回答複核問題：勤誠 8210/);
  let draft = JSON.parse(store.get('line_rule_draft_Uadmin'));
  assert.equal(draft.qid, 'Q1'); assert.equal(draft.name, '管理者回答'); assert.equal(draft.cat, '指示');
  assert.match(draft.text, /回答複核問題 Q1：「行程」指的是哪一檔？\n管理者回答：勤誠 8210/);
  const marked = []; ctx.lineAdminQuestionMark_ = (q, a, id) => { marked.push([q, a, id]); return true; };
  const wrote = []; ctx.lineAdminBook_ = () => ({}); ctx.lineAdminRuleSheet_ = () => ({ getLastRow: () => 1, getRange: () => ({ setValues: v => wrote.push(v) }) });
  ctx.withLock_ = f => f(); ctx.lineSetProp_ = () => {}; ctx.lineAdminFire_ = () => ({ ok: true });
  ctx.lineRoute_('Uadmin', 'bot', { a: 'ruleok', k: draft.id }, true);
  assert.deepEqual(marked[0].slice(0, 2), ['Q1', '勤誠 8210']); assert.equal(wrote.length, 1);
  // 自訂回答：下一句就是回答（先給確認卡）
  ctx.lineRoute_('Uadmin', 'bot', { a: 'qcustom', q: 'Q1' }, true);
  out = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('行程是勤誠沒錯，但要寫成勤誠不要寫行程', 'Uadmin', false), true);
  assert.match(out[0].altText, /回答複核問題：行程是勤誠沒錯/);
  assert.equal(JSON.parse(store.get('line_rule_draft_Uadmin')).qid, 'Q1');
  // 待裁示會先列要你決定的題目
  ctx.lineAdminPendingFlex_ = () => null;
  assert.match(ctx.lineAdminPendingReply_('')[0].altText, /要你決定 1 件事/);
  // 3. 使用者端：管理者按鈕、管理者指令一律「沒有提供這項功能」，不出現「管理者」字樣
  ctx.lineFindSub_ = () => ({ tester: false });
  for (const act of [{ a: 'qpick', q: 'Q1', o: '0' }, { a: 'qcustom', q: 'Q1' }, { a: 'adminsev', s: '邊界' }, ctx.lineParseText_('今日稽核', 'Uuser', false)]) {
    const r = ctx.lineRoute_('Uuser', 'bot', act, true);
    assert.match(r[0].text, /目前沒有提供這項功能/); assert.doesNotMatch(JSON.stringify(r), /管理者|稽核|裁示/);
  }
  ctx.lineProp_ = prevProp;
})();
console.log('11/11 單一股票一張卡、每日複核問題選項卡（選、自訂、確認、標已回答）、使用者端不透露管理者功能通過');

// ---- 2026/11/11（管理者）：打「1」選選項；管理者模式絕不回使用者端的「不在服務範圍」 ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  const prevProp = ctx.lineProp_; ctx.lineProp_ = () => '';
  ctx.callGemini_ = () => { throw new Error('offline'); };
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: {}, rows: [] });
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  const q = { _row: 2, 問題ID: 'Q202611111753280', 日期: '2026/11/11', 問題: '鴻海 2317 網站列觀望不碰、複核判觀望注意（邊界，只記錄）。要改嗎？',
    選項: '["維持觀望不碰（250壓力為主）","改成觀望注意（23幾是買點）","兩者都可，不用改"]', 狀態: '待回答', LINE推送: '2026/11/11 18:00:00' };
  ctx.lineAdminQuestions_ = st => [q].filter(r => !st || r.狀態 === st);
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: '抱歉，此問題不在我的工作與服務範圍內喔。' }];
  for (const [text, want] of [['1', '維持觀望不碰（250壓力為主）'], ['選1', '維持觀望不碰（250壓力為主）'], ['我選1', '維持觀望不碰（250壓力為主）'],
                              ['①', '維持觀望不碰（250壓力為主）'], ['1 維持觀望不碰', '維持觀望不碰（250壓力為主）'], ['維持觀望不碰', '維持觀望不碰（250壓力為主）'],
                              ['2', '改成觀望注意（23幾是買點）'], ['第三個', '兩者都可，不用改']]) {
    store.clear();
    const out = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_(text, 'Uadmin', false), true);
    assert.match(out[0].altText || '', /回答複核問題/, text);
    assert.equal(JSON.parse(store.get('line_rule_draft_Uadmin')).said, want, text);
  }
  // 沒有待回答題目時打「1」：留在管理者端，不回使用者端的「不在服務範圍」
  q.狀態 = '已回答';
  for (const text of ['1', '嗯', '好像怪怪的']) {
    const out = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_(text, 'Uadmin', false), true);
    assert.doesNotMatch(JSON.stringify(out), /不在我的工作與服務範圍/, text);
    if (text.length < 4) assert.match(out[0].text, /還不確定你要做什麼/, text);   // 長一點的句子會出送雲端確認卡，也算管理者端
  }
  // 使用者端照舊
  ctx.lineFindSub_ = () => ({ tester: false });
  assert.match(ctx.lineRoute_('Uuser', 'bot', ctx.lineParseText_('嗯', 'Uuser', false), true)[0].text, /不在我的工作與服務範圍/);
  ctx.lineProp_ = prevProp;
})();
console.log('11/11 打數字或選項文字即選擇、管理者模式不落到使用者端回覆通過');

// ---- 2026/11/11（管理者）：Claude 改在雲端執行並回傳（雲端對話） ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  const props = { CLAUDE_RULING_FIRE_URL: 'https://api.anthropic.com/v1/claude_code/routines/trig_x/fire' };
  const prevProp = ctx.lineProp_; ctx.lineProp_ = k => props[k] || ''; ctx.lineSetProp_ = (k, v) => { props[k] = String(v); };
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: {}, rows: [] });
  ctx.lineAdminQuestions_ = () => [];
  let gem = 0; ctx.callGemini_ = () => { gem++; return '{}'; };
  const sheet = []; ctx.lineAdminBook_ = () => ({});
  ctx.lineAdminRuleSheet_ = () => ({ getLastRow: () => sheet.length + 1, getRange: (r, c) => ({ setValues: v => sheet.push(v[0]), setValue: v => { sheet[r - 2][c - 1] = v; } }) });
  ctx.withLock_ = f => f();
  const fired = []; ctx.lineAdminFire_ = ids => { fired.push(...ids); return { ok: true }; };
  const triggers = []; ctx.ScriptApp = { getProjectTriggers: () => triggers.map(h => ({ getHandlerFunction: () => h })), deleteTrigger: t => triggers.splice(triggers.indexOf(t.getHandlerFunction()), 1),
    newTrigger: h => ({ timeBased: () => ({ after: () => ({ create: () => triggers.push(h) }) }) }) };
  // 1. 規則接不住的句子 → 先出「送給雲端前確認」卡（錯字修正）→ 按送出才寫「對話」列、觸發雲端
  const chatStore = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (chatStore.has(k) ? chatStore.get(k) : null), put: (k, v) => chatStore.set(k, String(v)), remove: k => chatStore.delete(k) }) };
  ctx.callGemini_ = () => { gem++; return JSON.stringify({ text: '今天待確認的四項為什麼都已修正了？誰改的', clear: true, note: '' }); };
  const pre = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('今天待確認的四項為什麼都已修正了？誰改的', 'Uadmin', false), true);
  assert.match(pre[0].altText, /送給雲端前確認/);
  assert.equal(fired.length, 0);                                                   // 確認前不觸發雲端
  const sendBtn = pre[0].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data));
  assert.deepEqual([...sendBtn.map(b => b.a)], ['chatsend', 'chatretype']);         // 沒改字：送出／重新輸入
  const out = ctx.lineRoute_('Uadmin', 'bot', sendBtn[0], true);
  assert.match(out[0].text, /已交給雲端 Claude：「今天待確認的四項/);
  const row = Object.fromEntries(ctx.LINE_ADMIN_RULE_COLS_.map((k, i) => [k, sheet[0][i]]));
  assert.equal(row.裁示分類, '對話'); assert.equal(row.名稱, '管理者對話'); assert.equal(row.狀態, '待處理'); assert.match(row.管理者說明, /待確認的四項/);
  assert.deepEqual([...fired], [row.裁示ID]); assert.match(row.裁示ID, /^C/);
  assert.deepEqual([...triggers], ['lineAdminFastTick_']);                         // 一分鐘後回來看
  // 2. 回報：對話列不推「接手」；已記錄 → 文字回答；待確認 → 確認卡
  const plan = ctx.lineAdminReportPlan_([{ 裁示ID: 'C1', 裁示分類: '對話', 狀態: '處理中', LINE回報: '' }, { 裁示ID: 'R1', 裁示分類: '指示', 狀態: '處理中', LINE回報: '' }]);
  assert.deepEqual([...plan.started.map(r => r.裁示ID)], ['R1']);
  const msgs = ctx.lineAdminChatResultMsgs_([
    { 裁示ID: 'C1', 裁示分類: '對話', 管理者說明: '待確認為什麼都已修正', 狀態: '已記錄', 結果: '11/11 第 87–90 列在 11:46 由每日複核修正分支套用。' },
    { 裁示ID: 'C2', 裁示分類: '對話', 管理者說明: '鴻海改成注意', 狀態: '待確認', 結果: '把 11/11 鴻海 2317 從觀望不碰改成觀望注意，依據「23幾次買點」。' }]);
  assert.match(msgs[0].text, /雲端 Claude 回覆「待確認為什麼都已修正」：\n\n11\/11 第 87–90 列/);
  const btns = msgs[1].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data));
  assert.deepEqual([...btns.map(b => b.a)], ['chatok', 'chatno']); assert.equal(btns[0].r, 'C2');
  // 3. 確認送出 → 轉成指示、觸發雲端；對話列改已轉交
  const rows = [{ _row: 2, 裁示ID: 'C2', 裁示分類: '對話', 日期: '2026/11/11', 管理者說明: '鴻海改成注意', 狀態: '待確認', 結果: '把鴻海改成觀望注意' }];
  ctx.lineAdminReadTab_ = () => rows;
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  const states = []; ctx.lineAdminChatSetState_ = (r, st) => { states.push(st); r.狀態 = st; };
  fired.length = 0; sheet.length = 0;
  const ok = ctx.lineRoute_('Uadmin', 'bot', { a: 'chatok', r: 'C2' }, true);
  assert.match(ok[0].text, /已送出裁示/);
  assert.deepEqual([...states], ['已轉交']);
  const r2 = Object.fromEntries(ctx.LINE_ADMIN_RULE_COLS_.map((k, i) => [k, sheet[0][i]]));
  assert.equal(r2.裁示分類, '指示'); assert.match(r2.管理者說明, /鴻海改成注意\n〔雲端理解，管理者已確認，原話為準〕把鴻海改成觀望注意/);
  assert.equal(fired.length, 1);
  assert.match(ctx.lineRoute_('Uadmin', 'bot', { a: 'chatok', r: 'C2' }, true)[0].text, /已經已轉交/);     // 不能重複送
  // 取消
  rows[0].狀態 = '待確認'; states.length = 0;
  assert.match(ctx.lineRoute_('Uadmin', 'bot', { a: 'chatno', r: 'C2' }, true)[0].text, /已取消/); assert.deepEqual([...states], ['已取消']);
  // 4. 一分鐘追蹤：還有待處理就再排，沒有就收掉
  ctx.lineAdminRulingTick_ = () => ({});
  triggers.length = 0; props.LINE_RULE_OPEN = '1'; props.LINE_CHAT_TRIES = '0';
  ctx.lineAdminFastTick_(); assert.deepEqual([...triggers], ['lineAdminFastTick_']);
  props.LINE_RULE_OPEN = ''; ctx.lineAdminFastTick_(); assert.deepEqual([...triggers], []);
  // 5. 即時的照舊：按鈕指令、選項數字、單股卡不送雲端；使用者端不送雲端
  fired.length = 0;
  ctx.lineAdminReviewReply_ = () => [{ type: 'text', text: 'review' }];
  ctx.lineRoute_('Uadmin', 'bot', { a: 'adminreview' }, true);
  ctx.lineFindSub_ = () => ({ tester: false });
  ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  ctx.lineRoute_('Uuser', 'bot', ctx.lineParseText_('今天待確認的四項為什麼都已修正了？誰改的', 'Uuser', false), true);
  assert.equal(fired.length, 0);
  ctx.lineProp_ = prevProp; delete ctx.ScriptApp;
})();
console.log('11/11 雲端對話：送雲端即時回覆、結果文字／確認卡、確認轉指示、取消、一分鐘追蹤、即時與使用者端不送通過');

// ---- 2026/11/11（管理者）：送雲端前先確認文字（錯字、同音字） ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  const props = { CLAUDE_RULING_FIRE_URL: 'x' };
  const prevProp = ctx.lineProp_; ctx.lineProp_ = k => props[k] || ''; ctx.lineSetProp_ = () => {};
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: {}, rows: [{ 代號: '2317', 名稱: '鴻海' }] });
  ctx.lineAdminQuestions_ = () => [];
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  const sheet = []; ctx.lineAdminBook_ = () => ({});
  ctx.lineAdminRuleSheet_ = () => ({ getLastRow: () => sheet.length + 1, getRange: () => ({ setValues: v => sheet.push(v[0]) }) });
  ctx.withLock_ = f => f(); ctx.lineAdminFire_ = () => ({ ok: true }); ctx.ScriptApp = { getProjectTriggers: () => [], newTrigger: () => ({ timeBased: () => ({ after: () => ({ create() {} }) }) }) };
  // 規則先修常見語音錯字
  assert.equal(ctx.lineAdminFixCommonTypos_('今天有沒有需要管理者才是的事，複合錯的帶確認'), '今天有沒有需要管理者裁示的事，複核錯的待確認');
  // 「天帶」：Gemini 判斷看不懂並給最可能的意思 → 確認卡顯示「我理解成」與提醒，三顆按鈕
  let sys = ''; ctx.callGemini_ = (s, u) => { sys = s; return JSON.stringify({ text: '今天待確認的有哪些？', clear: false, note: '補成完整問句' }); };
  const pre = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('天帶', 'Uadmin', false), true);
  assert.match(sys, /鴻海/); assert.match(sys, /待確認/);                          // 帶入今天的股名與常用詞
  const card = JSON.stringify(pre[0]);
  assert.match(card, /你輸入/); assert.match(card, /天帶/); assert.match(card, /我理解成/); assert.match(card, /今天待確認的有哪些？/); assert.match(card, /不太確定意思/);
  const btns = pre[0].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data));
  assert.deepEqual([...btns.map(b => b.a + (b.v || ''))], ['chatsend1', 'chatsend0', 'chatretype']);
  // 照這句送出 → 雲端收到修正後的句子並附原輸入
  ctx.lineRoute_('Uadmin', 'bot', btns[0], true);
  const row = Object.fromEntries(ctx.LINE_ADMIN_RULE_COLS_.map((k, i) => [k, sheet[0][i]]));
  assert.equal(row.管理者說明, '今天待確認的有哪些？（原輸入：天帶）');
  assert.match(ctx.lineRoute_('Uadmin', 'bot', btns[0], true)[0].text, /已逾時或已送出過/);   // 不重複送
  // 重新輸入：清掉草稿，不送
  ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('天帶', 'Uadmin', false), true);
  assert.match(ctx.lineRoute_('Uadmin', 'bot', { a: 'chatretype' }, true)[0].text, /請重新輸入/);
  assert.equal(store.has('line_chat_draft_Uadmin'), false); assert.equal(sheet.length, 1);
  // Gemini 失敗：仍出確認卡（規則修過的句子）
  ctx.callGemini_ = () => { throw new Error('quota'); };
  assert.match(ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('鴻海的帶確認是什麼', 'Uadmin', false), true)[0].altText, /送給雲端前確認：鴻海的待確認是什麼/);
  // 使用者端不受影響
  ctx.lineFindSub_ = () => ({ tester: false }); ctx.lineOutOfScopeReply_ = () => [{ type: 'text', text: 'outofscope' }];
  assert.doesNotMatch(JSON.stringify(ctx.lineRoute_('Uuser', 'bot', ctx.lineParseText_('天帶', 'Uuser', false), true)), /送給雲端/);
  ctx.lineProp_ = prevProp; delete ctx.ScriptApp;
})();
console.log('11/11 送雲端前確認文字：規則修錯字、Gemini 補全、確認卡三選一、送出附原輸入、重新輸入不送、失敗仍可確認通過');

// ---- 2026/11/11（管理者）：很短的語音殘句（「天帶」）模型原樣奉還時，規則猜最像的常用問句；猜不到就不送雲端 ----
(() => {
  ctx.todayStr_ = () => '2026/11/11';
  ctx.lineAdminActive_ = uid => uid === 'Uadmin';
  const prevProp = ctx.lineProp_; ctx.lineProp_ = k => (k === 'CLAUDE_RULING_FIRE_URL' ? 'x' : '');
  ctx.lineAdminReviewData_ = () => ({ day: '2026/11/11', summary: {}, rows: [] });
  ctx.lineAdminQuestions_ = () => []; ctx.lineAdminBook_ = () => ({});
  const store = new Map();
  ctx.CacheService = { getScriptCache: () => ({ get: k => (store.has(k) ? store.get(k) : null), put: (k, v) => store.set(k, String(v)), remove: k => store.delete(k) }) };
  for (const [t, want] of [['天帶', '今天待確認的明細是什麼？'], ['帶才是', '今天有哪些要我裁示的？'], ['季核', '今天的稽核結果是什麼？'], ['便捷', '今天的邊界有哪些？']]) {
    assert.equal(ctx.lineAdminGuessShort_(t), want, t);
  }
  assert.equal(ctx.lineAdminGuessShort_('哈'), '');
  // 模型原樣奉還「天帶」→ 卡片「我理解成：今天待確認的明細是什麼？」，有「照這句送出」
  ctx.callGemini_ = () => JSON.stringify({ text: '天帶', clear: false, note: '' });
  let pre = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('天帶', 'Uadmin', false), true);
  assert.match(JSON.stringify(pre[0]), /我理解成.*今天待確認的明細是什麼？/);
  assert.equal(ctx.lineParseData_(pre[0].contents.footer.contents[0].action.data).v, '1');
  // 模型失敗也一樣
  ctx.callGemini_ = () => { throw new Error('quota'); };
  pre = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('天帶', 'Uadmin', false), true);
  assert.match(JSON.stringify(pre[0]), /今天待確認的明細是什麼？/);
  // 看不懂也猜不到：不給「送出」，只有常用按鈕與重新輸入
  ctx.callGemini_ = () => JSON.stringify({ text: '嗯哼喔', clear: false, note: '' });
  pre = ctx.lineRoute_('Uadmin', 'bot', ctx.lineParseText_('嗯哼喔', 'Uadmin', false), true);
  const acts = pre[0].contents.footer.contents.map(b => ctx.lineParseData_(b.action.data).a);
  assert.ok(!acts.includes('chatsend')); assert.deepEqual([...acts], ['adminsev', 'adminpending', 'chatretype']);
  assert.match(JSON.stringify(pre[0]), /先不送雲端/);
  ctx.lineProp_ = prevProp;
})();
console.log('11/11 短語音殘句：規則猜常用問句、模型失敗也能猜、猜不到不送雲端通過');

// ---- 2026/11/11（管理者）：雲端回答用卡片呈現，一點一張 ----
(() => {
  const note = JSON.stringify({ summary: '今天沒有要你確認的東西；待確認 4 項都已修正。', items: [
    { type: '待確認', title: '③教學重點第1點', status: '已修正', detail: '只寫不追紅盤、選黑K佈局，已補做法與例子。', ref: '差異明細第87列' },
    { type: '邊界', title: '世芯-KY 3661', status: '只記錄', detail: '網站未列，複核認為可列觀望不碰。', ref: '差異明細第92列', code: '3661', name: '世芯-KY', date: '2026/11/11' },
    { type: '邊界', title: '鴻海 2317', status: '待你決定', detail: '觀望不碰或注意都可。', code: '2317', name: '鴻海' },
    { type: '問題', title: '鴻海要改嗎？', status: '已處理', detail: '你選維持觀望不碰。', qid: 'Q202611111753280' },
    { type: '候選', title: '提示詞候選 31–40', status: '待管理者', detail: '週五檢討前要看。' },
    { type: '怪類型', title: '其他', detail: 'x' }] });
  const cards = ctx.lineAdminParseCards_(note);
  assert.equal(cards.items.length, 6);
  assert.equal(ctx.lineAdminParseCards_('一般文字回答'), null);
  assert.equal(ctx.lineAdminParseCards_('{壞掉'), null);
  const [msg] = ctx.lineAdminChatResultMsgs_([{ 裁示ID: 'C1', 裁示分類: '對話', 管理者說明: '今天待確認', 日期: '2026/11/11', 狀態: '已記錄', 結果: note }]);
  assert.equal(msg.contents.type, 'carousel'); assert.equal(msg.contents.contents.length, 7);   // 摘要＋六點
  const b = msg.contents.contents;
  assert.match(JSON.stringify(b[0]), /今天沒有要你確認的東西/); assert.match(JSON.stringify(b[0]), /邊界 2/);
  assert.equal(b[1].header.backgroundColor, '#D0645A'); assert.equal(b[2].header.backgroundColor, '#4A9A6A');
  assert.equal(b[5].header.backgroundColor, '#4A9A6A'); assert.equal(b[6].header.backgroundColor, '#4A9A6A');   // 不認得的類型當資訊
  assert.match(b[1].header.contents[0].text, /待確認・已修正/);
  assert.equal(b[2].footer, undefined);                                                       // 只記錄 → 不給裁示鈕
  assert.equal(ctx.lineParseData_(b[3].footer.contents[0].action.data).a, 'rulepick');         // 待你決定 → 可裁示
  assert.equal(ctx.lineParseData_(b[4].footer.contents[0].action.data).a, 'qshow');
  assert(Buffer.byteLength(JSON.stringify(msg.contents)) < 50000);
  // 文字回答照舊；待確認用摘要寫進確認卡
  assert.match(ctx.lineAdminChatResultMsgs_([{ 裁示ID: 'C2', 管理者說明: 'x', 狀態: '已記錄', 結果: '純文字' }])[0].text, /純文字/);
  const prop = ctx.lineAdminChatResultMsgs_([{ 裁示ID: 'C3', 管理者說明: '鴻海改注意', 狀態: '待確認', 結果: JSON.stringify({ summary: '把鴻海改成觀望注意' }) }])[0];
  assert.match(JSON.stringify(prop), /把鴻海改成觀望注意/); assert.match(JSON.stringify(prop), /chatok/);
})();
console.log('11/11 雲端回答卡片：摘要卡＋每點一張、類型顏色、裁示／看題按鈕、文字與待確認照舊通過');

// ---- 2026/11/11（管理者）：卡片內文完整呈現（不截成「…」）、卡片用 giga 寬度 ----
(() => {
  const longQuote = '下面這邊有兩個缺口，第一個缺口補一半，第二個缺口要等它回測站穩再說，世芯-KY 兩個缺口至少補一個才買，' + '台積電缺口在下面'.repeat(12);
  const longWhy = '原稿明講兩個缺口的位置與條件，網站只寫了第一個；' + '依裁示第一節第3條判讀，'.repeat(10);
  const rows = Array.from({ length: 10 }, (_, i) => ({ 日期: '2026/11/11', 代號: String(2300 + i), 名稱: '股' + i, 網站分類: '觀望注意', 複核分類: '觀望不碰',
    差異類型: '分類', 嚴重度: i % 2 ? '邊界' : '待確認', 處理: '待管理者', 原文依據: longQuote, 判斷理由: longWhy, 修正前: '網站原本的說明'.repeat(8) }));
  const m = ctx.lineAdminReviewFlex_({ day: '2026/11/11', rows, summary: { 狀態: '已完成', 比對檔數: '10' } });
  const s = JSON.stringify(m.contents);
  assert.ok(s.includes(longQuote), '原句要完整'); assert.ok(s.includes(longWhy), '判斷要完整');
  assert.doesNotMatch(s.replace(/"altText":"[^"]*"/g, ''), /…/);
  assert.ok(m.contents.contents.every(b => b.size === 'giga'), '管理者卡片用 giga');
  assert(Buffer.byteLength(s) < 50000, 'LINE 一則上限 50KB：' + Buffer.byteLength(s));
  // 雲端卡片：內文、標題、依據都完整
  const detail = '下面這邊有兩個缺口，' + '補一半再說，'.repeat(40);
  const [msg] = ctx.lineAdminChatResultMsgs_([{ 裁示ID: 'C1', 管理者說明: '今天世芯-KY 為什麼是邊界？我想看完整的原句與判斷理由', 日期: '2026/11/11', 狀態: '已記錄',
    結果: JSON.stringify({ summary: '總結'.repeat(80), items: [{ type: '邊界', title: '世芯-KY 3661 兩個缺口的條件', status: '待你決定', detail, ref: '差異明細第92列：' + longQuote }] }) }]);
  const ms = JSON.stringify(msg.contents);
  assert.ok(ms.includes(detail) && ms.includes(longQuote) && ms.includes('總結'.repeat(80)) && ms.includes('我想看完整的原句與判斷理由'));
  assert.ok(msg.contents.contents.every(b => b.size === 'giga'));
  // 待裁示、題目、確認卡也完整
  const p = JSON.stringify(ORIG.lineAdminPendingFlex_({ day: '2026/11/11', rows }).contents);
  assert.ok(p.includes(longQuote));
  const q = JSON.stringify(ctx.lineAdminQuestionBubble_({ 問題ID: 'Q1', 日期: '2026/11/11', 問題: '問題'.repeat(120), 背景: '背景'.repeat(150), 選項: '["a","b"]' }));
  assert.ok(q.includes('問題'.repeat(120)) && q.includes('背景'.repeat(150)));
})();
console.log('11/11 卡片內文完整、giga 寬度、50KB 以內通過');

// ---- 2026/11/11（管理者）：顏色收斂成紅→綠四段（兩深兩淺） ----
(() => {
  const used = new Set(Object.values(ctx.LINE_ADMIN_SEV_COLOR_).concat(Object.values(ctx.LINE_ADMIN_ITEM_TYPES_)));
  assert.deepEqual([...used].sort(), ['#1E5E3E', '#4A9A6A', '#9B1C1C', '#D0645A']);
  assert.deepEqual({ ...ctx.LINE_ADMIN_SEV_COLOR_ }, { 明確錯誤: '#9B1C1C', 待確認: '#D0645A', 邊界: '#4A9A6A', 複核錯: '#1E5E3E' });
  // 白字標題對比至少 3:1（標題是粗體大字）
  const lum = h => { const c = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255).map(v => v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4); return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]; };
  for (const h of used) assert.ok(1.05 / (lum(h) + 0.05) >= 3, h + ' 對白字對比不足');
})();
console.log('11/11 四段紅綠漸層、白字對比通過');

// ---- 2026/11/12（管理者）：直播畫面辨識出現在今日稽核卡 ----
(() => {
  const screen = [
    { 日期: '2026/11/12', 時間: '0:17:05', 講者原話: '你看這一檔，底部爆量', 畫面股名: '勤誠', 代號: '8210', 把握: '清楚', 說明: 'K線圖左上角', 狀態: '已上網站', 處理說明: '補上勤誠觀望注意' },
    { 日期: '2026/11/12', 時間: '0:33:40', 講者原話: '再告訴你們一檔', 畫面股名: '', 代號: '', 把握: '沒有顯示', 說明: '只有講者', 狀態: '待你決定', 處理說明: '' },
    { 日期: '2026/11/12', 時間: '0:41:00', 講者原話: '記憶體只看這一支', 畫面股名: '宜鼎', 代號: '5289', 把握: '模糊', 說明: '報價視窗', 狀態: '待你決定', 處理說明: '' }];
  const data = { day: '2026/11/12', summary: { 狀態: '已完成', 比對檔數: '8' }, rows: [], screen };
  const m = ctx.lineAdminReviewFlex_(data);
  const b = m.contents.contents;
  assert.equal(b.length, 4);                                          // 摘要＋三處
  assert.match(JSON.stringify(b[0]), /畫面辨識 3 處/);
  assert.equal(b[1].header.backgroundColor, '#1E5E3E');                // 已上網站＝深綠
  assert.equal(b[2].header.backgroundColor, '#D0645A');                // 畫面沒顯示＝淺紅
  assert.match(b[1].header.contents[1].text, /0:17:05　勤誠 8210/);
  assert.equal(b[2].footer, undefined);                               // 沒有代號不給裁示鈕
  assert.equal(ctx.lineParseData_(b[3].footer.contents[0].action.data).c, '5289');
  assert.ok(b.every(x => x.size === 'giga'));
  // 只看某一級不放畫面辨識；整天「無」只寫在摘要
  assert.equal(ctx.lineAdminReviewFlex_(data, '待確認').contents.type, 'bubble');
  const none = ctx.lineAdminReviewFlex_({ ...data, screen: [{ 日期: '2026/11/12', 時間: '', 狀態: '無' }] });
  assert.match(JSON.stringify(none), /今天沒有需要看畫面才知道股名的地方/);
  const fail = ctx.lineAdminReviewFlex_({ ...data, screen: [{ 日期: '2026/11/12', 時間: '', 狀態: '失敗', 說明: 'Gemini 額度用完' }] });
  assert.match(JSON.stringify(fail), /畫面辨識沒有完成：Gemini 額度用完/);
  // 上限：差異 10 項＋畫面辨識 5 處仍不超過 12 張
  const many = Array.from({ length: 10 }, (_, i) => ({ 日期: '2026/11/12', 代號: String(1000 + i), 名稱: '股' + i, 嚴重度: '邊界', 處理: '只記錄' }));
  const big = ctx.lineAdminReviewFlex_({ ...data, rows: many, screen: screen.concat(screen).slice(0, 5) });
  assert.ok(big.contents.contents.length <= 12);
})();
console.log('11/12 畫面辨識卡：顏色依狀態、摘要一句、裁示鈕、無／失敗、12 張上限通過');
