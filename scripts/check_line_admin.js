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

// ---- r2：測試雲端（只限管理者） ----
assert.equal(ctx.lineParseText_('測試雲端', 'U1').a, 'admintest');
assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'admintest' }, true)[0].text, /只開放給管理者/);
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
  assert.match(ctx.lineRoute_('Uuser', 'bot', { a: 'adminprogress' }, true)[0].text, /只開放給管理者/);

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
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].text, 'outofscope');   // 模型失敗照舊回覆
  ctx.callGemini_ = () => '{"intent":"stock","date":"","stock":"台積電"}';
  assert.equal(ctx.lineRoute_('Uadmin', 'bot', vague, true)[0].text, 'stock:台積電');
})();
console.log('11/10 管理者一般問句：規則判斷日期與意圖、Gemini 備援、非管理者不呼叫模型通過');
