// 2026/10/12 AI 助理：只用 Flash Lite、不給使用者選模型；訂閱只有每日總覽，回覆不出現盤中通知。
// 直接執行 Aiservice.gs（外部服務與模型回覆用假的），不連網、不寫任何資料。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.join(__dirname, '..', 'public-site', 'gas-source');

function load(models) {
  const store = {};
  const saved = [];
  const asked = [];
  const ctx = vm.createContext({
    CACHE: { get: k => store[k] || null, put: (k, v) => { store[k] = v; }, remove: k => { delete store[k]; } },
    Logger: { log() {} },
    Utilities: { formatDate: () => '2026/10/12' },
    createSubscription: p => { saved.push(p); return { ok: true }; },
    readSheetObjects_: () => [], fmtDate_: d => String(d),
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'Presentationquality.gs'), 'utf8'), ctx);
  vm.runInContext(fs.readFileSync(path.join(root, 'Aiservice.gs'), 'utf8'), ctx);
  ctx.getGeminiKey_ = () => 'k';
  ctx.assistantModelCatalog_ = () => ({ models: models });
  ctx.replies = [];
  ctx.callGemini_ = (system, user, opt) => { asked.push({ system, opt }); return ctx.replies.shift(); };
  return { ctx, saved, asked };
}
const reply = o => JSON.stringify(Object.assign({ reply: '', intent: 'smalltalk', draft: {}, ready: false }, o));
const BOTH = [{ id: 'gemini-3.5-flash', label: 'Flash' }, { id: 'gemini-3.5-flash-lite', label: 'Flash Lite' }];

// 一、模型：只認 Flash Lite；前端傳什麼都不採用
{
  const { ctx, asked } = load(BOTH);
  assert(ctx.isLiteModel_('gemini-3.5-flash-lite') && ctx.isLiteModel_('gemini-4-flash-lite-preview'));
  for (const id of ['gemini-3.5-flash', 'gemini-3.5-pro', 'gemini-3.5-flash-lite-image', 'gemini-3.5-flash-lite-tts']) assert(!ctx.isLiteModel_(id), id);
  ctx.replies = [reply({ reply: '這個網站逐日整理節目內容。', intent: 'help' })];
  let r = ctx.askAssistant('這個網站有哪些功能', 's1', 'gemini-3.5-flash', '');
  assert.strictEqual(r.model, 'gemini-3.5-flash-lite');
  assert.strictEqual(asked[0].opt.model, 'gemini-3.5-flash-lite');
  // 只有 Flash、沒有 Lite 可用時不退回 Flash
  const only = load([{ id: 'gemini-3.5-flash', label: 'Flash' }]);
  r = only.ctx.askAssistant('你好', 's2', 'gemini-3.5-flash', '');
  assert.strictEqual(r.ok, false);
  assert.strictEqual(only.asked.length, 0);
}

// 二、官方清單的候選只留 Flash Lite
{
  const { ctx } = load(BOTH);
  const props = {};
  const src = fs.readFileSync(path.join(root, 'Aiservice.gs'), 'utf8');
  const fresh = vm.createContext({
    Logger: { log() {} }, CACHE: {},
    Utilities: { computeDigest: () => [1, 2, 3, 4, 5, 6, 7, 8], DigestAlgorithm: { SHA_256: 1 }, formatDate: () => '2026-10' },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] || null, setProperty: (k, v) => { props[k] = v; } }) },
    UrlFetchApp: { fetch: () => ({ getResponseCode: () => 200, getContentText: () => JSON.stringify({ models: [
      'gemini-3.5-flash', 'gemini-3.5-flash-lite', 'gemini-3.5-pro', 'gemini-3.0-flash-lite', 'gemini-3.5-flash-lite-tts'
    ].map(n => ({ name: 'models/' + n, displayName: n, supportedGenerationMethods: ['generateContent'] })) }) }) },
  });
  vm.runInContext(src, fresh);
  const probed = [];
  fresh.callGemini_ = (s, u, opt) => { probed.push(opt.model); return 'OK'; };
  const out = fresh.assistantModelCatalog_('k');
  assert.deepStrictEqual(probed.sort(), ['gemini-3.0-flash-lite', 'gemini-3.5-flash-lite']);
  assert.strictEqual(out.models[0].id, 'gemini-3.5-flash-lite');
  assert(Object.keys(props)[0].startsWith('ASSISTANT_LITE_MODELS_'));
  assert(ctx);
}

// 三、訂閱只有每日總覽：模型就算回 sms:true、使用者就算說要，也不寫入
{
  const { ctx, saved } = load(BOTH);
  assert(!/盤中(?:即時)?通知|即時通知|簡訊|sms/i.test(ctx.ASSISTANT_SYSTEM), '提示詞不應再提盤中通知');   // 節目名稱裡的「盤中家教班」不算
  ctx.replies = [reply({ reply: '好的。', intent: 'subscribe', draft: { email: 'a@example.com', daily: true, sms: true }, ready: true })];
  let r = ctx.askAssistant('幫我訂閱每日總覽和盤中即時通知，信箱 a@example.com', 's3', '', '');
  assert.strictEqual(r.reply, '準備訂閱 a@example.com：每日總覽。若正確，請回覆「確認訂閱」。');
  assert.strictEqual(r.draft.sms, false);
  assert.strictEqual(saved.length, 0);                       // 還沒有明講確認
  r = ctx.askAssistant('確認訂閱', 's3', '', '');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(saved)), [{ email: 'a@example.com', daily: true, sms: false }]);
  assert(r.done);
  // 只給了信箱、還沒說要不要：補問的是每日總覽，不是別的
  const two = load(BOTH);
  two.ctx.replies = [reply({ reply: '收到信箱。', intent: 'subscribe', draft: { email: 'b@example.com', daily: null } })];
  r = two.ctx.askAssistant('我的信箱是 b@example.com', 's4', '', '');
  assert(/確認要訂閱每日總覽/.test(r.reply) && !/盤中|簡訊/.test(r.reply), r.reply);
  assert.strictEqual(two.ctx.draftReady_({ email: 'b@example.com', daily: false, sms: true }), false);
}

// 四、回覆的最後一關：不出現盤中通知這個訂閱種類
{
  const { ctx } = load(BOTH);
  assert.strictEqual(ctx.guardReply_('本站沒有關注特定股票的功能，僅提供每日總覽與盤中即時通知。請提供您的 Email。'),
    '本站沒有關注特定股票的功能，僅提供每日總覽。請提供您的 Email。');
  assert.strictEqual(ctx.guardReply_('目前可以訂閱每日總覽、盤中通知。'), '目前可以訂閱每日總覽。');
  assert.strictEqual(ctx.guardReply_('台積電最近一次被提到是 2026/10/12，列在會員持股。'), '台積電最近一次被提到是 2026/10/12，列在會員持股。');
  ctx.replies = [reply({ reply: '本站沒有關注特定股票的功能，僅提供每日總覽與盤中即時通知。請提供您的 Email。', intent: 'help' })];
  const r = ctx.askAssistant('我想追蹤 2330 和 4916', 's5', '', '');
  assert(!/盤中|簡訊|即時通知/.test(r.reply), r.reply);
}

// 五、前台：沒有模型選單，提示語不再引導「追蹤股票」
{
  const index = fs.readFileSync(path.join(root, 'Index.html'), 'utf8');
  const js = fs.readFileSync(path.join(root, 'JavaScript.html'), 'utf8');
  assert(!/id="modelSel"/.test(index) && !/modelSel/.test(js));
  assert(!/我想追蹤 2330/.test(js));
  assert(/apiAsk\(q, sessionId, '', userKey\)/.test(js));
}
console.log('Assistant: Flash Lite only, no model picker, daily digest is the only subscription');
