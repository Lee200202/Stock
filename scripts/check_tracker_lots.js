// v227（管理者 2026/11/13）：分批買進與部分賣出的報酬。純函式取自 SheetService.gs，不連試算表。
// 決定：整檔報酬＝各次買進報酬的平均；部分賣出照講的比例、沒講當一半；部分賣出由每一筆等比例賣掉。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const dir = path.join(__dirname, '..', 'public-site', 'gas-source');
const src = fs.readFileSync(path.join(dir, 'SheetService.gs'), 'utf8');
const cache = fs.readFileSync(path.join(dir, 'Cachebuilder.gs'), 'utf8');
function grab(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0, end = src.indexOf('{', start);
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}' && --depth === 0) break;
  }
  return src.slice(start, end + 1);
}
const digits = /var CN_DIGIT_ = \{[^}]*\};/.exec(src)[0];
const ctx = vm.createContext({});
vm.runInContext([digits].concat(['saleFraction_', 'lotsReturn_', 'lotReturn_', 'roundLotsInput_', 'roundHasLots_',
  'isPartialSaleRecord_', 'sortRecords_', 'buildRounds_'].map(grab)).join('\n'), ctx);
const run = code => JSON.parse(JSON.stringify(vm.runInContext(code, ctx)));
const J = JSON.stringify;

// 比例：照講的，沒講當一半
assert.strictEqual(run("saleFraction_('先賣出一半')"), 0.5);
assert.strictEqual(run("saleFraction_('先賣三分之一')"), 1 / 3);
assert.strictEqual(run("saleFraction_('減碼三成')"), 0.3);
assert.strictEqual(run("saleFraction_('部分賣出')"), 0.5);
// 哪些賣出是部分賣出（講了比例才算；漲跌幅、成本不算）
const partial = { '先賣出一半': true, '先賣三分之一': true, '賣出三成': true, '賣 30%': true, '減碼': true, '一半先賣掉': true,
  '全部賣出': false, '賣出': false, '停利賣出，獲利30%': false, '賣出完成': false, '賣出後漲了三成': false, '今天賣出，成本 50': false };
for (const [t, want] of Object.entries(partial)) {
  assert.strictEqual(run(`isPartialSaleRecord_(${JSON.stringify({ kind: 'sell', reason: t })})`), want, t);
}
// 只買一次：和舊公式完全一樣
assert.strictEqual(run(`lotsReturn_(${J([{ d: '2026/10/01', p: 100 }])},[],110,null)`), 10);
// 兩次買進 +20% 與 +10% → 平均 +15%
assert.strictEqual(run(`lotsReturn_(${J([{ d: '2026/10/01', p: 100 }, { d: '2026/10/05', p: 120 / 1.1 }])},[],120,null)`), 15);
// 賣一半在 +10%、剩下在 +20% 出清 → +15%
assert.strictEqual(run(`lotsReturn_(${J([{ d: '2026/10/01', p: 100 }])},${J([{ d: '2026/10/03', p: 110, f: 0.5 }])},120,null)`), 15);
// 部分賣出只賣當時已經買的；之後才買的那筆全部留著
assert.strictEqual(run(`lotsReturn_(${J([{ d: '2026/10/01', p: 100 }, { d: '2026/10/05', p: 100 }])},${J([{ d: '2026/10/03', p: 110, f: 0.5 }])},120,null)`), 17.5);
// 績效歷史用 upto：還沒買的那筆當天不算
assert.strictEqual(run(`lotsReturn_(${J([{ d: '2026/10/01', p: 100 }, { d: '2026/10/05', p: 200 }])},[],110,'2026/10/02')`), 10);
// 四次買進（勤誠的情形）：各筆平均
const four = [100, 80, 90, 125].map((p, i) => ({ d: '2026/10/0' + (i + 1), p }));
assert.strictEqual(run(`lotsReturn_(${J(four)},[],100,null)`), Math.round(((0 + 25 + 100 / 9 * 1 + -20) / 4) * 100) / 100);

// 回合：之後的買進記成加碼、部分賣出記成部分賣出，出清才結束回合
const recs = [
  { date: '2026/10/01', kind: 'buy', reason: '買進', seq: 0 },
  { date: '2026/10/05', kind: 'buy', reason: '再買進', seq: 0 },
  { date: '2026/10/07', kind: 'sell', reason: '先賣出一半', seq: 0 },
  { date: '2026/10/09', kind: 'buy', reason: '再加碼', seq: 0 },
  { date: '2026/10/12', kind: 'sell', reason: '全部賣出出清', seq: 0 }
];
const rounds = run(`buildRounds_(${J(recs)}, [])`);
assert.strictEqual(rounds.length, 1);
assert.deepStrictEqual(rounds[0].adds.map(a => a.date), ['2026/10/05', '2026/10/09']);
assert.deepStrictEqual(rounds[0].partialSells.map(a => a.date), ['2026/10/07']);
assert.strictEqual(rounds[0].close.date, '2026/10/12');

// 回合JSON → 輸入（低進高出試算取 lo／hi）
const rj = { o: '2026/10/01', od: '2026/10/01', e: 100, el: 98, l: [{ d: '2026/10/05', p: 110, lo: 108 }], ps: [{ d: '2026/10/07', p: 115, hi: 117, f: 0.5 }] };
assert.deepStrictEqual(run(`roundLotsInput_(${J(rj)}, true)`), { lots: [{ d: '2026/10/01', p: 98 }, { d: '2026/10/05', p: 108 }], partials: [{ d: '2026/10/07', p: 117, f: 0.5 }] });
assert.strictEqual(run(`roundHasLots_(${J({ e: 1 })})`), false);
assert.strictEqual(run(`roundHasLots_(${J(rj)})`), true);

// 接線：重算寫回合JSON 的 l／ps、即時報酬與績效歷史都走同一個函式、圖表清單帶出每一次買賣
assert(/l: \(rd\.lots \|\| \[\]\)/.test(src) && /ps: \(rd\.partials \|\| \[\]\)/.test(src), '回合JSON 要寫 l／ps');
assert(/roundHasLots_\(lastRj\)/.test(src), '即時報酬要用分批');
assert(/roundHasLots_\(rd\)[\s\S]{0,200}lotsReturn_\(li\.lots, li\.partials, px, d\)/.test(cache), '績效歷史要用分批');
assert(/adds: \(Array\.isArray\(x\.l\)/.test(src) && /partialSells: \(Array\.isArray\(x\.ps\)/.test(src), '圖表要有每一次買賣');
console.log('v227 分批買進與部分賣出：比例、各筆平均、部分賣出只賣已買的、回合判定、回合JSON 與三處接線通過');

// 前台（JavaScript.html 的 trackerLotsReturn_）與後端 lotsReturn_ 結果必須一致：隨機 300 組
const page = fs.readFileSync(path.join(dir, 'JavaScript.html'), 'utf8');
const fstart = page.indexOf('function trackerLotsReturn_(');
let fd = 0, fend = page.indexOf('{', fstart);
for (; fend < page.length; fend++) { if (page[fend] === '{') fd++; else if (page[fend] === '}' && --fd === 0) break; }
vm.runInContext(page.slice(fstart, fend + 1), ctx);
let seed = 7;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (let n = 0; n < 300; n++) {
  const lots = [], parts = [];
  const k = 1 + Math.floor(rnd() * 4);
  for (let i = 0; i < k; i++) lots.push({ d: '2026/10/' + String(10 + i * 3), p: Math.round((50 + rnd() * 100) * 100) / 100 });
  if (rnd() < 0.6) parts.push({ d: '2026/10/' + String(11 + Math.floor(rnd() * 10)), p: Math.round((50 + rnd() * 100) * 100) / 100, f: [0.5, 1 / 3, 0.3][Math.floor(rnd() * 3)] });
  const px = Math.round((50 + rnd() * 100) * 100) / 100;
  const back = run(`lotsReturn_(${J(lots)}, ${J(parts)}, ${px}, null)`);
  const front = run(`trackerLotsReturn_(${J(lots.map(l => ({ date: l.d, price: l.p })))}, ${J(parts.map(x => ({ date: x.d, price: x.p, fraction: x.f })))}, ${px})`);
  assert.strictEqual(front, back, 'front/back differ: ' + J({ lots, parts, px }));
}
assert(/trackerHasLots_\(lr\)/.test(page) && /m\.r && m\.r\.adds/.test(page) && /trackerHasLots_\(r\)/.test(page), '面板即時報酬、走勢圖標記、低進高出都要用分批');
console.log('前台與後端分批報酬 300 組隨機一致；面板、走勢圖、低進高出接線通過');

// v227r2：清單持有中列的盤中報酬也要用各筆（以前每次報價更新都改回第一筆的 (現價-進場)/進場）
assert(/trackerLotsAttr_\(i\)/.test(page) && /el\.dataset\.lots\?trackerLotsLive_\(el,v\.last\)/.test(page), '清單盤中報酬要用分批');
assert(/price: Number\(l\.p\) \|\| null/.test(src) && /price: Number\(x\.p\) \|\| null/.test(src), '清單回合資料要帶中點取價');
console.log('v227r2 清單盤中報酬接線通過');

// v227r3：清單與面板寫平均成本（按股數平均）；沒有部分賣出時「現價 ÷ 平均成本 − 1」＝合併報酬
const cstart = page.indexOf('function trackerLotsCost_(');
let cd = 0, cend = page.indexOf('{', cstart);
for (; cend < page.length; cend++) { if (page[cend] === '{') cd++; else if (page[cend] === '}' && --cd === 0) break; }
vm.runInContext(page.slice(cstart, cend + 1), ctx);
for (let n = 0; n < 200; n++) {
  const k = 1 + Math.floor(rnd() * 4), lots = [];
  for (let i = 0; i < k; i++) lots.push({ date: '2026/10/1' + i, price: Math.round((50 + rnd() * 100) * 100) / 100 });
  const px = Math.round((50 + rnd() * 100) * 100) / 100;
  const cost = run(`trackerLotsCost_(${J(lots)}, [])`);
  const viaCost = Math.round((px / cost - 1) * 10000) / 100;
  assert(Math.abs(viaCost - run(`trackerLotsReturn_(${J(lots)}, [], ${px})`)) <= 0.05, 'cost vs return ' + J({ lots, px, cost }));
}
assert(/平均成本 ' \+ pxShort\(m\.avgCost\)/.test(page) && /entryLabel/.test(page), '面板與清單要寫平均成本');
console.log('v227r3 平均成本與合併報酬一致（200 組）');
