// 2026/11/25 後台「LINE 推送紀錄」（不連 Google、不連 LINE）：node scripts/check_line_push_log.js
// 讀既有的待送訊息與寄送帳本：期間篩選、新的在前、內容取自訊息本身（含 Flex 內文字，不含按鈕）、收件人帳號遮蔽、只讀不寫。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const G = path.join(__dirname, '..', 'public-site', 'gas-source');
const src = fs.readFileSync(path.join(G, 'Subscribers.gs'), 'utf8');
const line = fs.readFileSync(path.join(G, 'Line.gs'), 'utf8');
const pick = (s, name) => { const a = s.indexOf('function ' + name + '('); const b = s.indexOf('\nfunction ', a + 10); return s.slice(a, b); };
const now = Date.UTC(2026, 10, 25, 5, 0, 0);   // 台北 13:00
const stamp = ms => new Date(ms + 8 * 3600000).toISOString().replace('T', ' ').slice(0, 19).replace(/-/g, '/');
const flex = [{ type: 'flex', altText: '11/25 每日總覽', contents: { type: 'bubble', body: { type: 'box', contents: [
  { type: 'text', text: '今日重點：台積電' }, { type: 'button', action: { type: 'uri', label: '看網站', uri: 'https://x' } }] } } }];
const outbox = [
  { row: 2, id: 'old', kind: 'daily', createdMs: now - 10 * 86400000, created: stamp(now - 10 * 86400000), state: 'done', total: 3, accepted: 3 },
  { row: 3, id: 'd1125', kind: 'daily', date: '2026/11/25', createdMs: now - 3600000, created: stamp(now - 3600000), updated: stamp(now - 3500000), state: 'done', total: 2, accepted: 1, failed: 1 },
  { row: 4, id: 'a1125', kind: 'admin', createdMs: now - 600000, created: stamp(now - 600000), state: 'sending', total: 1, accepted: 0, open: 1 },
];
const json = { 2: '[]', 3: JSON.stringify(flex), 4: JSON.stringify([{ type: 'text', text: '後台通知內容' }]) };
let writes = 0;
const sh = { getRange: (r, c, n) => ({ getValues: () => Array.from({ length: n }, (_, i) => [json[r + i]]), setValues() { writes++; } }) };
const ctx = {
  Date: class extends Date { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } },
  Utilities: { formatDate: (d) => stamp(d.getTime()).slice(0, 10) },
  adminAuth_: k => { if (k !== 'K') throw new Error('未授權'); },
  lineOutboxRead_: () => ({ sh, ci: { '內容JSON': 14 }, rows: outbox }),
  lineLedgerFor_: id => ({ rows: [{ i: 0, uid: 'U' + 'a'.repeat(32), state: 'accepted', attempt: 1, err: '' }, { i: 1, uid: 'U' + 'b'.repeat(32), state: 'failed', attempt: 3, err: 'HTTP 403' }],
    vals: [['d1125', '', '', '', '', '', '', '2026/11/25 12:10:05', '2026/11/25 12:10:04'], ['d1125', '', '', '', '', '', '', '2026/11/25 12:12:00', '']] }),
  lineDisplayNames_: () => ({ ['U' + 'a'.repeat(32)]: '小明' }),
  nowStamp_: () => '2026/11/25 13:00:00',
};
vm.createContext(ctx);
vm.runInContext(pick(src, 'subStamp_') + pick(line, 'lineParseTaipei_') + pick(line, 'lineMask_'), ctx);
const a = src.indexOf('var LINE_PUSH_KIND_'), b = src.indexOf('/** 停止對這個 LINE 帳號推送');
vm.runInContext(src.slice(a, b), ctx);

assert.strictEqual(ctx.apiAdminLinePushLog('bad', 7).ok, false, '要登入');
const week = JSON.parse(JSON.stringify(ctx.apiAdminLinePushLog('K', 7)));
assert.deepStrictEqual(week.items.map(x => x.id), ['a1125', 'd1125'], '近 7 天、新的在前（10 天前的不列）');
const d = week.items[1];
assert.strictEqual(d.kindLabel, '每日總覽');
assert.strictEqual(d.preview, '11/25 每日總覽');
assert.strictEqual(d.text, '今日重點：台積電', 'Flex 內文字取出、按鈕標籤不算內容');
assert.strictEqual(week.items[0].text, '後台通知內容');
assert.strictEqual(JSON.parse(JSON.stringify(ctx.apiAdminLinePushLog('K', 30))).items.length, 3);
assert.strictEqual(JSON.parse(JSON.stringify(ctx.apiAdminLinePushLog('K', 1))).items.length, 2, '今天');
const det = JSON.parse(JSON.stringify(ctx.apiAdminLinePushDetail('K', 'd1125')));
assert.deepStrictEqual(det.rows.map(r => [r.name, r.state, r.at]), [['小明', 'accepted', '2026/11/25 12:10:04'], ['', 'failed', '']]);
assert(det.rows.every(r => /\*\*\*/.test(r.id) && !/a{20}|b{20}/.test(r.id)), '帳號遮蔽');
assert.strictEqual(det.rows[1].error, 'HTTP 403');
assert.strictEqual(writes, 0, '只讀不寫');
const panel = fs.readFileSync(path.join(G, 'SubscribersPanel.html'), 'utf8');
assert(/apiAdminLinePushLog\(KEY,pushDays\)/.test(panel) && /apiAdminLinePushDetail\(KEY,id\)/.test(panel), '後台分頁接上');
for (const f of [path.join(G, 'SiteBridge.gs'), path.join(__dirname, '..', 'site-api', 'worker.mjs'), path.join(__dirname, '..', 'site-api', 'admin-security.mjs')]) {
  const t = fs.readFileSync(f, 'utf8');
  assert(t.includes('apiAdminLinePushLog') && t.includes('apiAdminLinePushDetail'), path.basename(f) + ' 允許清單');
}
console.log('LINE 推送紀錄：期間、排序、內容、收件人遮蔽、只讀、允許清單 — 通過');
