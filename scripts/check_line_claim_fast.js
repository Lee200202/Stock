// LINE 互動去重（Line.gs lineClaimEvent_）：首次送達只走快取，不搶全站寫入鎖、不搜尋事件帳本；
// 重送與重試照舊走完整核對（2026/11/03：去重一段佔 0.8～1.3 秒）。
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Line.gs'), 'utf8');

function pick(name) {
  const i = src.indexOf('function ' + name + '(');
  assert(i >= 0, '找不到 ' + name);
  let depth = 0;
  for (let k = src.indexOf('{', i); k < src.length; k++) {
    if (src[k] === '{') depth++;
    if (src[k] === '}' && --depth === 0) return src.slice(i, k + 1);
  }
}

function world(ledgerIds) {
  const store = new Map();
  const counts = { lock: 0, sheet: 0 };
  const env = {
    CacheService: { getScriptCache: () => ({ get: (k) => store.has(k) ? store.get(k) : null, put: (k, v) => store.set(k, v), remove: (k) => store.delete(k) }) },
    withLock_: (fn) => { counts.lock++; return fn(); },
    getSheet_: () => {
      counts.sheet++;
      return { getLastRow: () => ledgerIds.length + 1,
               getRange: () => ({ createTextFinder: (id) => ({ matchEntireCell: () => ({ findAll: () => ledgerIds.filter((x) => x === id) }) }) }) };
    },
    LINE_EVENTS_SHEET_: 'LINE 事件帳本',
  };
  const fn = new Function(...Object.keys(env), pick('lineClaimEvent_') + '\nreturn lineClaimEvent_;')(...Object.values(env));
  return { claim: fn, counts, store };
}

// 1) 首次送達：不鎖、不讀帳本；同一事件再來一次（處理中）是 busy
let w = world([]);
assert.strictEqual(w.claim('ev1', true), 'new');
assert.deepStrictEqual(w.counts, { lock: 0, sheet: 0 }, '首次送達不鎖、不讀帳本');
assert.strictEqual(w.claim('ev1', true), 'busy');
// 2) 已處理完（快取 done）：首次送達路徑也認得是重複
w.store.set('lineev_ev1', 'done');
assert.strictEqual(w.claim('ev1', true), 'duplicate');
// 3) 重送或重試：走完整核對，帳本裡有就算重複
w = world(['ev9']);
assert.strictEqual(w.claim('ev9', false), 'duplicate');
assert.strictEqual(w.counts.lock, 1);
assert.strictEqual(w.counts.sheet, 1);
assert.strictEqual(world([]).claim('ev2', false), 'new');
// 4) 呼叫端：只有「LINE 沒標重送、轉送第一次」才算首次送達
const handle = pick('lineHandleEvent_');
assert(/firstDelivery = !\(ev\.deliveryContext && ev\.deliveryContext\.isRedelivery\) && !\(Number\(timing\.attempt\) > 1\)/.test(handle));
assert(/lineClaimEvent_\(id, firstDelivery\)/.test(handle));

console.log('LINE 去重快速路徑：8 項通過');
