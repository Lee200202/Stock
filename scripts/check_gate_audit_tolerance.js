// 品質關卡（MailService.gs）的稽核容錯：2026/11/03 模型回覆少了 missing 陣列，整輪 AI 複審被跳過、自動放行。
// 驗：格式不對會重送一次；明確的格式變體可接受；稽核未完成只記警告、接著進內容複審；額度用完仍往外拋。
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'MailService.gs'), 'utf8');

function pick(name) {
  const i = src.indexOf('function ' + name + '(');
  assert(i >= 0, '找不到 ' + name);
  let depth = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') depth++;
    if (src[k] === '}' && --depth === 0) return src.slice(i, k + 1);
  }
}
const DIGEST = src.match(/var DIGEST_MISSING_SYSTEM_ =[\s\S]*?;\r?\n/)[0];

function load(replies) {
  const calls = [];
  const env = {
    calls,
    Logger: { log() {} },
    callGemini_(sys, user, opts) { calls.push(opts); const r = replies.shift(); if (r instanceof Error) throw r; return r; },
    fmtDate_: (d) => d,
    readSheetObjects_: () => [{ '發布日期': '2026/11/03', raw: 'x'.repeat(500) }],
    rawTranscript_: (r) => r.raw,
    searchByDate: () => ({ buy: [], sell: [], watchAvoid: [], watchWatch: [], holdings: [] }),
    isIndustryName_: () => false,
    isQuotaExhausted_: (e) => /quota/.test(String(e && e.message || e)),
  };
  const code = DIGEST + pick('gateJsonResponse_') + pick('digestAuditVerdict_') +
    '\nreturn { gateJsonResponse_, digestAuditVerdict_ };';
  const fn = new Function(...Object.keys(env), code);
  return Object.assign(env, fn(...Object.values(env)));
}

// 1) 第一次少了 missing，重送一次後正常
let g = load(['{"consistent":false}', '{"missing":[],"pending":[]}']);
assert.deepStrictEqual(g.digestAuditVerdict_('2026/11/03').missing, []);
assert.strictEqual(g.calls.length, 2, '形狀不對要重送一次');

// 2) 明確的變體：整個回覆是陣列；或 consistent=true 沒附清單
g = load(['[]']);
assert.deepStrictEqual(g.digestAuditVerdict_('2026/11/03').missing, []);
assert.strictEqual(g.calls.length, 1);
g = load(['{"consistent":true}']);
assert.strictEqual(g.digestAuditVerdict_('2026/11/03').consistent, true);

// 3) 兩次都不對：照舊丟「稽核缺少 missing 陣列」，由關卡決定怎麼處理
g = load(['{"foo":1}', '{"bar":2}']);
assert.throws(() => g.digestAuditVerdict_('2026/11/03'), /稽核缺少 missing 陣列/);

// 4) 關卡：稽核未完成只記警告、接著複審；額度用完仍往外拋
const advance = pick('gateAdvance_');
function gate(auditImpl) {
  const env = {
    autoFixAuditMissing: auditImpl,
    isQuotaExhausted_: (e) => /quota/.test(String(e && e.message || e)),
    reviewDayRecords_: () => ({ checked: 3, changed: 0, dropped: 0, renamed: 0, unsure: 0, note: '' }),
    stepArticle_: () => {},
  };
  return new Function(...Object.keys(env), advance + '\nreturn gateAdvance_;')(...Object.values(env));
}
let st = { date: '2026/11/03', phase: '稽核', changed: 0, log: [] };
let msg = gate(() => { throw new Error('完整性稽核失敗，未放行：Error: 稽核缺少 missing 陣列，未放行'); })(st);
assert.strictEqual(st.phase, '複審', '稽核未完成要接著複審');
assert(/繼續內容複審/.test(msg));
assert(/稽核未完成/.test(st.auditStat.note));
st = { date: '2026/11/03', phase: '稽核', changed: 0, log: [] };
gate(() => ({ added: 0, skipped: 1, pending: 2, decisions: [] }))(st);
assert.strictEqual(st.phase, '複審', '待確認不擋整輪複審');
st = { date: '2026/11/03', phase: '稽核', changed: 0, log: [] };
assert.throws(() => gate(() => { throw new Error('quota exhausted'); })(st), /quota/);
assert.strictEqual(st.phase, '稽核', '額度用完不推進');

console.log('品質關卡稽核容錯：7 項通過');
