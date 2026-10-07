/* 戰情條件庫（v137）的規則測試：進場／出場分開、每個條件的正例與反例、事件與狀態的差別、
   資料不足不當成不成立、不使用未來資料、範本與登錄表的一致性。只在本機計算，不連網、不寫入。
   指標數值本身的正確性由 scripts/check_battle_indicators.py 用獨立的參考實作核對；這裡核對的是規則。 */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const read = n => fs.readFileSync(path.join(root, 'public-site/gas-source', n), 'utf8');
const ctx = {window: {}, console}; vm.createContext(ctx);
vm.runInContext(read('BattleConfig.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
vm.runInContext(read('Battle.html').match(/<script>([\s\S]*?)<\/script>/)[1], ctx);
const {evaluate, cleanPrefs, indicators: I} = ctx.window.MarketBattle, cfg = ctx.window.BATTLE_CONFIG;
let count = 0;
// 計算在另一個執行環境裡跑，陣列與物件不是同一個型別：比內容。
const eq = (a, b, m) => assert.deepEqual(JSON.parse(JSON.stringify(a === undefined ? null : a)), JSON.parse(JSON.stringify(b === undefined ? null : b)), m);
function test(name, fn) { fn(); count++; console.log('OK ' + name); }

const calendar = []; { let d = new Date('2025-06-02T12:00:00Z'); while (calendar.length < 420) { if (d.getUTCDay() > 0 && d.getUTCDay() < 6) calendar.push(d.toISOString().slice(0, 10).replaceAll('-', '/')); d.setUTCDate(d.getUTCDate() + 1); } }
const B = (open, high, low, close, volume = 1000) => ({open, high, low, close, volume});
const flat = (n, p = 100, v = 1000) => Array.from({length: n}, () => B(p, p + 1, p - 1, p, v));
/* 每根的開盤等於收盤（十字），高低各加減 0.5：轉折的位置只由收盤決定，好控制。 */
const dots = (closes, v = 1000) => closes.map(c => B(c, c + 0.5, c - 0.5, c, v));
const item = bars => {
  // 測試資料本身要是合理的 K 棒，否則會被當成壞資料而整檔「資料不足」，測不到想測的規則。
  bars.forEach((b, i) => assert.ok(b.high >= Math.max(b.open, b.close) && b.low <= Math.min(b.open, b.close) && b.volume >= 0, '第 ' + i + ' 根不是合理的 K 棒 ' + JSON.stringify(b)));
  return {code: '1234', name: '測試', bars: bars.map((b, i) => ({date: calendar[i], ...b}))};
};
/* 從行事曆第 from 天開始排的日 K（測試「日 K 與行事曆起點不同」用）。 */
const item2 = (bars, from) => ({code: '1234', name: '測試', bars: bars.map((b, i) => ({date: calendar[from + i], ...b}))});
const dayCtx = (n, more) => ({date: calendar[n - 1], calendar, volumeUnit: 'lots', afterClose: true, ...more});
/* 單一條件的結果：把它單獨放進一組來算。 */
function sig(id, bars, params = {}, more = {}) {
  const r = evaluate(item(bars), {groups: [[id]], cond: {[id]: params}, ...more.prefs}, dayCtx(bars.length, more.ctx));
  const c = r.checks.find(x => x.key === id);
  assert.ok(c, id + ' 沒有算出來');
  return {pass: c.pass, value: c.value, why: c.why, r};
}
/* 把最後一根改成盤中報價（日 K 還沒落地）。 */
function live(bars) {
  const last = bars[bars.length - 1], it = item(bars.slice(0, -1));
  it.quote = {date: calendar[bars.length - 1], open: last.open, high: last.high, low: last.low, last: last.close, volume: last.volume, prevClose: bars[bars.length - 2].close, stale: false};
  return it;
}
const D = n => calendar[n];

/* ───── 登錄表與範本 ───── */
test('registry: every condition has a calculator, an origin label, valid sources and no forbidden wording', () => {
  assert.equal(cfg.conditions.length, 57);
  eq(JSON.parse(JSON.stringify(cfg.legacyFlags)), ['useGap', 'useVolume', 'useTrend', 'useBreakout', 'useBody', 'useMacd', 'useKD', 'useBoll']);
  const ids = new Set();
  cfg.conditions.forEach((c, n) => {
    assert.ok(!ids.has(c.id), c.id + ' 重複'); ids.add(c.id);
    assert.ok(cfg.origins[c.origin], c.id + ' 沒有依據性質');
    assert.ok(['entry', 'exit', 'both'].includes(c.side) && cfg.categories.some(x => x[0] === c.cat), c.id);
    c.refs.forEach(r => assert.ok(cfg.sources[r] && /^https:\/\//.test(cfg.sources[r][1]), c.id + ' 來源 ' + r));
    if (n >= 8) assert.ok(I.ids.includes(c.id), c.id + ' 沒有計算函式');
    // 張震觀點的條件一定要有節目紀錄的出處
    if (c.origin === 'zhang') assert.ok(c.refs.some(r => /^Z/.test(r)), c.id + ' 缺少節目紀錄出處');
  });
  I.ids.forEach(id => assert.ok(ids.has(id), id + ' 有計算函式但沒有登錄'));
  // 名稱不暗示身分、保證或勝率；說明文字可以出現「不保證」「無法知道是誰」這類否定句，但不能有下面這些說法。
  const names = cfg.conditions.map(c => c.label).concat(cfg.templates.map(t => t.name)).join('｜');
  ['主力', '保證', '必買', '勝率', '法人', '官方', '必漲', '穩賺', '出貨', '鎖碼'].forEach(w => assert.ok(!names.includes(w), '名稱不該出現「' + w + '」'));
  const text = JSON.stringify([cfg.conditions, cfg.templates, cfg.unsupported, cfg.origins]);
  ['主力必買', '保證突破', '高勝率', '法人進場', '法人已', '官方選股', '主力出貨', '主力鎖碼', '必漲', '穩賺'].forEach(w => assert.ok(!text.includes(w), '不該出現「' + w + '」'));
});
test('templates: only use registered conditions and valid parameter values; a template is recognised until anything is changed', () => {
  const names = new Set();
  cfg.templates.forEach(t => {
    assert.ok(!names.has(t.name)); names.add(t.name);
    assert.ok(['research', 'zhang'].includes(t.origin) && ['entry', 'exit'].includes(t.side));
    t.groups.flat().forEach(k => { assert.ok(cfg.byId[k], t.id + ' 用了不存在的條件 ' + k); assert.ok(cfg.byId[k].side === 'both' || cfg.byId[k].side === t.side, t.id + ' 用了另一個目的的條件 ' + k); });
    Object.keys(t.params).forEach(id => assert.ok(t.groups.flat().includes(id), t.id + ' 設了沒用到的條件參數 ' + id));
    const prefs = {purpose: t.side, cond: t.params};
    const s = {direction: t.direction, groupLogic: t.groupLogic, groups: t.groups};
    if (t.side === 'exit') prefs.exit = s; else Object.assign(prefs, s);
    const p = cleanPrefs(prefs);
    // 參數值都是登錄表允許的：整理之後原樣保留
    Object.keys(t.params).forEach(id => Object.keys(t.params[id]).forEach(k => assert.equal(String(p.cond[id][k]), String(t.params[id][k]), t.id + ' ' + id + '.' + k)));
    assert.equal(p.template[t.side], t.id, t.id + ' 套用後應該認得出來');
    // 改掉一個參數或拿掉一個條件，就不再是這個範本
    const id = Object.keys(t.params)[0];
    if (id) { const k = Object.keys(t.params[id])[0], q = cfg.byId[id].params.find(x => x.key === k); const other = q.options ? q.options.find(o => String(o[0]) !== String(t.params[id][k]))[0] : (t.params[id][k] === q.max ? q.min : q.max);
      assert.equal(cleanPrefs({...prefs, cond: {...t.params, [id]: {...t.params[id], [k]: other}}}).template[t.side], '', t.id + ' 改了參數仍被當成範本'); }
    const cut = JSON.parse(JSON.stringify(prefs)); (t.side === 'exit' ? cut.exit : cut).groups = [[t.groups[0][0], 'useKD']];
    assert.notEqual(cleanPrefs(cut).template[t.side], t.id);
  });
  assert.ok(cfg.templates.filter(t => t.side === 'entry').length >= 12 && cfg.templates.filter(t => t.side === 'exit').length >= 10);
});
test('saved v136 settings keep their meaning: entry groups, 1x volume multiple; exit starts from its own default', () => {
  const v136 = {gapPct: 3, volumeMultiple: 1, volumeDays: 10, volumeCustom: true, minLots: 500, closePosition: 0.7, trendDays: 60, breakoutDays: 20, direction: 'down', volumeMode: 'estimate',
    groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'anyOfAll', useGap: true, useVolume: true, useTrend: true, useBreakout: false, useBody: false, useMacd: false, useKD: false, useBoll: false};
  const p = cleanPrefs(v136);
  assert.equal(p.purpose, 'entry'); assert.equal(p.direction, 'down'); assert.equal(p.volumeMultiple, 1); assert.equal(p.volumeDays, 10); assert.equal(p.trendDays, 60);
  eq(JSON.parse(JSON.stringify(p.groups)), [['useGap', 'useVolume'], ['useTrend']]);
  eq(p.exit, {direction: 'down', groups: [['supportBreak'], ['maLost']], ops: ['and', 'and'], join: 'or', groupLogic: 'anyOfAll'});
  eq([p.ops, p.join, p.groupLogic], [['and', 'and'], 'or', 'anyOfAll']);              // 舊的「且→或」換成每組「且」、組間「或」
  assert.equal(cleanPrefs({}).volumeMultiple, 1);                                   // 預設仍是 1 倍，沒有被悄悄改掉
  assert.equal(cleanPrefs({}).cond.volSpike.mult, 2);                               // 研究用的 2 倍是另一個條件自己的參數
  // 參數不在允許範圍：用預設，不照單全收
  const bad = cleanPrefs({cond: {trendUp: {maDays: 33}, notExtended: {pct: 999}, kdCross: {zone: 'x', within: '5'}}});
  assert.equal(bad.cond.trendUp.maDays, 60); assert.equal(bad.cond.notExtended.pct, 10); assert.equal(bad.cond.kdCross.zone, 20); assert.equal(bad.cond.kdCross.within, 5);
});

/* ───── 進場與出場分開 ───── */
test('entry and exit are two separate rule sets; direction is a third, independent choice', () => {
  // 20 根平盤後跌破前低：進場（預設：開盤跳空 且 爆量）不符合；出場（預設：跌破前低 或 跌破均線）符合。同一檔同一天，兩種目的結果不同。
  const bars = flat(30).concat([B(100, 100.5, 96, 96.5, 1500)]);
  const entry = evaluate(item(bars), {}, dayCtx(31)), exit = evaluate(item(bars), {purpose: 'exit'}, dayCtx(31));
  assert.equal(entry.purpose, 'entry'); assert.equal(entry.pass, false);
  assert.equal(exit.purpose, 'exit'); assert.equal(exit.pass, true); assert.equal(exit.status, '盤後條件符合');
  eq(exit.groups.map(g => g.keys.join('+')), ['supportBreak', 'maLost']);
  assert.equal(exit.formula, '跌破前低 或 跌破均線');
  // 出場的結果不會動到進場那一套
  const p = cleanPrefs({purpose: 'exit', exit: {groups: [['macdDead']], groupLogic: 'allOfAny', direction: 'up'}});
  eq(JSON.parse(JSON.stringify(p.groups)), [['useGap', 'useVolume']]); assert.equal(p.direction, 'up'); assert.equal(p.exit.direction, 'up');
  assert.equal(p.macdDead, true); assert.equal(p.useGap, false);                    // 開關反映的是目前這個目的
  // 方向只影響原有的七項：出場目的、方向向上時，「開盤跳空」看的仍是向上缺口（高檔跳空轉弱的範本就是這樣用）
  const gapUp = flat(30).concat([B(104, 107, 103.5, 104.2, 3000)]);
  const r = evaluate(item(gapUp), {purpose: 'exit', exit: {groups: [['useGap']], direction: 'up'}}, dayCtx(31));
  assert.equal(r.checks[0].label, '向上開盤缺口'); assert.equal(r.pass, true);
  assert.equal(evaluate(item(gapUp), {purpose: 'exit', exit: {groups: [['useGap']], direction: 'down'}}, dayCtx(31)).pass, false);
});

/* ───── 事件與狀態 ───── */
test('a cross is an event: it expires after the chosen window even though the state still holds', () => {
  const base = flat(30).map(b => ({...b}));
  const up = n => dots(Array.from({length: n}, (_, i) => 102 + 2 * i));
  // 第 30 根起漲：5 日均線當根上穿 20 日均線
  assert.equal(sig('maCross', base.concat(up(1)), {pair: '5-20', within: 1}).pass, true);
  const later = base.concat(up(5));                                                  // 交叉在 4 根之前
  assert.equal(sig('maCross', later, {pair: '5-20', within: 3}).pass, false);       // 超過 3 根：事件過期
  assert.equal(sig('maCross', later, {pair: '5-20', within: 5}).pass, true);
  assert.match(sig('maCross', later, {pair: '5-20', within: 5}).value, new RegExp(D(30).replaceAll('/', '\\/')));   // 寫出交叉是哪一天
  const S = I.series(item(later).bars); assert.ok(I.sma(S, 5)[S.t] > I.sma(S, 20)[S.t]);                             // 狀態（5 日 > 20 日）一直成立
  // 交叉之後又跌回去：不算
  const back = base.concat(up(1), dots([80]));
  const x = sig('maCross', back, {pair: '5-20', within: 3}); assert.equal(x.pass, false); assert.match(x.value, /之後又回到另一側/);
  // 資料不夠算 20 日均線的前一根：資料不足，不是不成立
  assert.equal(sig('maCross', flat(20), {pair: '5-20', within: 1}).pass, null);
});
test('trend state needs a rising average, not just price above it; too little history is unknown', () => {
  assert.equal(sig('trendUp', dots(Array.from({length: 40}, (_, i) => 100 + i)), {maDays: 20}).pass, true);
  // 一路跌、最後一根彈到均線上：價在均線上，但均線仍在下彎 → 不成立
  const falling = Array.from({length: 39}, (_, i) => 300 - 5 * i); falling.push(falling[38] + 50);
  const r = sig('trendUp', dots(falling), {maDays: 20}); assert.equal(r.pass, false);
  const S = I.series(item(dots(falling)).bars); assert.ok(S.C[S.t] > I.sma(S, 20)[S.t] && I.sma(S, 20)[S.t] < I.sma(S, 20)[S.t - 5]);
  assert.equal(sig('trendUp', dots(Array.from({length: 24}, (_, i) => 100 + i)), {maDays: 20}).pass, null);         // 少一根就算不出 5 根前的均線
  assert.equal(sig('trendUp', dots(Array.from({length: 25}, (_, i) => 100 + i)), {maDays: 20}).pass, true);
  assert.equal(sig('maAlign', dots(Array.from({length: 70}, (_, i) => 100 + i))).pass, true);
  assert.equal(sig('maAlign', flat(70)).pass, false);                                // 三條一樣高不算排列
  assert.equal(sig('maAlign', flat(59)).pass, null);
});

/* ───── 突破、回測、支撐 ───── */
const breakout = () => flat(40).concat([B(100, 105.5, 99.5, 105, 3000)]);            // 第 40 根收盤突破前 20 根高點 101
test('breakout then retest: the breakout price is fixed at the event; needs a retest that holds and a close above the retest high', () => {
  const ok = breakout().concat([B(105, 105, 101.2, 103, 800), B(103, 106.5, 102.5, 106, 1200)]);
  const r = sig('breakRetest', ok, {lookback: 10, dry: 'on'}); assert.equal(r.pass, true); assert.match(r.value, new RegExp(D(40).replaceAll('/', '\\/') + ' 突破 101；'));
  // 沒有回到突破價附近：只是續漲，不是回測
  assert.equal(sig('breakRetest', breakout().concat([B(105, 106, 104, 105.5, 800), B(105.5, 108, 105, 107, 1200)]), {lookback: 10}).pass, false);
  // 回測時收盤跌破突破價：失敗
  const lost = sig('breakRetest', breakout().concat([B(105, 105, 98, 99, 800), B(99, 107, 99, 106, 1200)]), {lookback: 10}); assert.equal(lost.pass, false); assert.match(lost.value, /跌回突破價下方/);
  // 回測量沒有縮：要求量縮時不成立，不要求時成立
  const loud = breakout().concat([B(105, 105, 101.2, 103, 3500), B(103, 106.5, 102.5, 106, 1200)]);
  assert.equal(sig('breakRetest', loud, {lookback: 10, dry: 'on'}).pass, false); assert.equal(sig('breakRetest', loud, {lookback: 10, dry: 'off'}).pass, true);
  // 回測了但還沒越過回測那一根的高點
  assert.equal(sig('breakRetest', breakout().concat([B(105, 105, 101.2, 103, 800), B(103, 104.9, 102.5, 104.5, 1200)]), {lookback: 10}).pass, false);
  // 之後再多幾根，突破價仍是 101，不會被後來的高點換掉
  const more = ok.concat([B(106, 107, 105, 106.5, 900)]); assert.match(sig('breakRetest', more, {lookback: 10}).value, / 突破 101；/);
  // 最近 10 根都要能算出「前 20 根高點」：30 根不夠（差一根），31 根才夠
  const edge = n => flat(n).concat([B(100, 106, 100, 105, 3000), B(105, 105, 101.2, 103, 800), B(103, 107, 102, 106)]);
  assert.equal(sig('breakRetest', edge(27), {lookback: 10}).pass, null); assert.equal(sig('breakRetest', edge(28), {lookback: 10}).pass, true);
  // 突破失敗（出場）：同一個突破，之後收盤跌回 101 之下
  assert.equal(sig('breakoutFail', breakout().concat([B(105, 106, 104, 105), B(105, 105, 99.5, 100)]), {lookback: 10}).pass, true);
  assert.equal(sig('breakoutFail', breakout().concat([B(105, 106, 104, 105), B(105, 105, 101.5, 102)]), {lookback: 10}).pass, false);
  assert.equal(sig('breakoutFail', flat(45), {lookback: 10}).pass, false);           // 根本沒有突破
});
const risingTo = last => { const c = Array.from({length: 40}, (_, i) => 100 + 0.5 * i); const bars = dots(c); bars.push(last); return bars; };
const fallingTo = last => { const c = Array.from({length: 40}, (_, i) => 120 - 0.5 * i); const bars = dots(c); bars.push(last); return bars; };
test('pullback to a rising average is an entry study; a bounce into a falling average is an exit study — never both', () => {
  // 均線向上：20 日均線 115.05，當根低點 115.5 回到均線 1% 內、收 116 站在均線上
  const pull = risingTo(B(119.5, 120, 115.5, 116));
  assert.equal(sig('maPullback', pull, {maDays: 20, tolPct: 1}).pass, true);
  assert.equal(sig('seasonBounceFail', pull, {maDays: 20, tolPct: 1}).pass, false);
  // 沒有回測（離均線還很遠）
  assert.equal(sig('maPullback', risingTo(B(119.5, 120.5, 119.5, 120)), {maDays: 20, tolPct: 1}).pass, false);
  // 均線向下：反彈碰到均線又收低
  const bounce = fallingTo(B(100.5, 104.6, 100, 100.2));
  assert.equal(sig('seasonBounceFail', bounce, {maDays: 20, tolPct: 1}).pass, true);
  assert.equal(sig('maPullback', bounce, {maDays: 20, tolPct: 1}).pass, false);
  // 均線向下、價格彈上均線並站穩：既不是向上回測，也不是反彈失敗
  const reclaim = fallingTo(B(100.5, 106.5, 100.4, 106));
  assert.equal(sig('maPullback', reclaim, {maDays: 20, tolPct: 1}).pass, false); assert.match(sig('maPullback', reclaim, {maDays: 20}).value, /未向上/);
  assert.equal(sig('seasonBounceFail', reclaim, {maDays: 20, tolPct: 1}).pass, false);
  // 季線（60 日）資料不足：不退而求其次
  assert.equal(sig('maPullback', pull, {maDays: 60, tolPct: 1}).pass, null);
});
test('false breakdown: intraday break of the prior low that closes back above; a close below is a real break', () => {
  const back = flat(25).concat([B(100, 100.5, 97, 99.5, 600)]), down = flat(25).concat([B(100, 100.5, 97, 98.5, 600)]);
  assert.equal(sig('falseBreak', back, {dry: 'off'}).pass, true);
  assert.equal(sig('falseBreak', down, {dry: 'off'}).pass, false);                   // 收盤沒有站回：續跌
  assert.equal(sig('supportBreak', down, {days: 20, buffer: 'off'}).pass, true);     // 這才是跌破前低
  assert.equal(sig('supportBreak', back, {days: 20, buffer: 'off'}).pass, false);
  // 緩衝：ATR 約 2.11，0.2 ATR 約 0.42，門檻 98.58。收 98.7 沒有緩衝時算跌破，有緩衝時還沒破；收 98.5 兩種都破。
  const near = flat(25).concat([B(100, 100.5, 97, 98.7, 600)]);
  assert.equal(sig('supportBreak', near, {days: 20, buffer: 'off'}).pass, true); assert.equal(sig('supportBreak', near, {days: 20, buffer: 'on'}).pass, false);
  assert.equal(sig('supportBreak', down, {days: 20, buffer: 'on'}).pass, true);
  // 要求量縮：量比均量大就不算
  assert.equal(sig('falseBreak', flat(25).concat([B(100, 100.5, 97, 99.5, 1500)]), {dry: 'on'}).pass, false);
  assert.equal(sig('falseBreak', back, {dry: 'on'}).pass, true);
  // 盤中量還沒走完：要求量縮時先不下結論
  const r = evaluate(live(back), {groups: [['falseBreak']], cond: {falseBreak: {dry: 'on'}}}, dayCtx(26, {afterClose: false}));
  const c = r.checks.find(x => x.key === 'falseBreak'); assert.equal(c.pass, null); assert.match(c.why, /盤中成交量尚未完成/); assert.equal(r.pass, false); assert.equal(r.status, '資料待核對');
  assert.equal(evaluate(live(back), {groups: [['falseBreak']], cond: {falseBreak: {dry: 'off'}}}, dayCtx(26, {afterClose: false})).status, '盤中條件符合');
});

/* ───── 缺口 ───── */
test('gaps: opening gap vs a gap that held all day; three degrees of a gap being lost', () => {
  const held = flat(30).concat([B(104, 107, 103.5, 106)]);
  assert.equal(sig('gapHold', held).pass, true);
  const filled = flat(30).concat([B(104, 107, 100.9, 106)]);                         // 開盤有缺口，但低點回到昨日高點 101 之下
  assert.equal(sig('gapHold', filled).pass, false); assert.match(sig('gapHold', filled).value, /已回補/);
  assert.equal(evaluate(item(filled), {groups: [['useGap']]}, dayCtx(31)).checks[0].pass, true);   // 「開盤跳空」仍成立：兩個條件看的不是同一件事
  assert.equal(sig('gapHold', flat(30).concat([B(102, 105, 101.5, 104)])).pass, false);            // 只開高 2%，沒到 2.5% 門檻
  assert.equal(sig('gapDown', flat(30).concat([B(96, 97, 94, 95)])).pass, true);
  assert.equal(sig('gapDown', flat(30).concat([B(99.5, 100, 94, 95)])).pass, false);               // 沒有低於昨日低點
  const after = last => held.concat([B(106, 107, 105, 106), last]);                  // 缺口 101–103.5
  const touch = after(B(106, 106, 103, 104)), enter = after(B(106, 106, 102, 103)), full = after(B(106, 106, 100, 100.5));
  eq(['touch', 'enter', 'full'].map(m => sig('gapLost', touch, {mode: m, lookback: 10}).pass), [true, false, false]);
  eq(['touch', 'enter', 'full'].map(m => sig('gapLost', enter, {mode: m, lookback: 10}).pass), [true, true, false]);
  eq(['touch', 'enter', 'full'].map(m => sig('gapLost', full, {mode: m, lookback: 10}).pass), [true, true, true]);
  assert.match(sig('gapLost', enter, {mode: 'enter'}).value, / 向上缺口 101–103\.5；/);
  // 當天就回補的缺口不算「守住的缺口」，之後也不會拿來判失守
  assert.equal(sig('gapLost', filled.concat([B(106, 107, 105, 106), B(106, 106, 100, 100.5)]), {mode: 'enter'}).pass, false);
});

/* ───── 背離 ───── */
const DIV = 120;                                                                    // 前段長度：MACD 需要 130 根以上才判定（暖機）
const divClose = Array.from({length: DIV}, (_, i) => 100.5 + 0.5 * (DIV - 1 - i)).concat([97, 93, 90, 94, 97, 99, 100, 99, 97, 94, 91, 89.5, 88.5, 90, 92, 94, 96, 101]);
test('bullish divergence: needs two confirmed pivots (no back-filling), is voided by a new low, and can require price confirmation', () => {
  const bars = dots(divClose), S = I.series(item(bars).bars), pv = I.pivots(S, true), h = I.macd(S).hist;
  eq(pv.slice(-2), [DIV + 2, DIV + 12]);   // 兩個低點
  assert.ok(S.L[DIV + 12] < S.L[DIV + 2] && h[DIV + 12] > h[DIV + 2], '這組資料應該是價格更低、柱體更高');
  const full = sig('bullDiverge', bars, {indicator: 'macd', confirm: 'price'});      // 最後一根收 101，站上兩低點間的高點 100.5
  assert.equal(full.pass, true); assert.match(full.value, /背離；確認價 100\.5，收盤已站上/);
  // 少一根（收 96）：背離成立但價格還沒確認
  assert.equal(sig('bullDiverge', bars.slice(0, -1), {indicator: 'macd', confirm: 'price'}).pass, false);
  assert.equal(sig('bullDiverge', bars.slice(0, -1), {indicator: 'macd', confirm: 'none'}).pass, true);
  // 第二個低點右邊只走了 2 根：轉折還沒確認，不能先說背離（不回填）
  const early = sig('bullDiverge', bars.slice(0, DIV + 15), {indicator: 'macd', confirm: 'none'}); assert.equal(early.pass, false);
  assert.equal(sig('bullDiverge', bars.slice(0, DIV + 16), {indicator: 'macd', confirm: 'none'}).pass, true);             // 第 3 根走完才成立
  // 背離之後又破底：作廢
  const lower = dots(divClose.slice(0, DIV + 17).concat([87])); assert.equal(sig('bullDiverge', lower, {indicator: 'macd', confirm: 'none'}).pass, false);
  assert.match(sig('bullDiverge', lower, {indicator: 'macd', confirm: 'none'}).value, /作廢/);
  // 頂背離是鏡像：價格更高、柱體更低，收盤跌破兩高點間的低點才確認
  const mirror = dots(divClose.map(c => 260 - c));
  assert.equal(sig('bearDiverge', mirror, {indicator: 'macd', confirm: 'price'}).pass, true);
  assert.equal(sig('bearDiverge', mirror.slice(0, -1), {indicator: 'macd', confirm: 'price'}).pass, false);
  assert.equal(sig('bearDiverge', mirror.slice(0, DIV + 15), {indicator: 'macd', confirm: 'none'}).pass, false);
  assert.equal(sig('bullDiverge', mirror, {indicator: 'macd', confirm: 'none'}).pass, false);                       // 方向不會弄反
  const short = sig('bullDiverge', dots(divClose.slice(30)), {indicator: 'macd'});                                  // 108 根：MACD 暖機不足，不拿還沒收斂的數字判背離
  assert.equal(short.pass, null); assert.match(short.why, /需要至少 130 根連續日 K/);
});

/* ───── 量：低檔大量、量縮、表態 ───── */
const bigVol = (tail, head = 140) => flat(20, head).concat(flat(118), [B(100, 104, 100, 103, 2500)], tail);   // 第 138 根是大量 K（低點 100）
test('big volume at a low base, then quiet bars that hold its low, then a breakout — in that order', () => {
  const quiet = [B(103, 103.5, 101, 102, 500), B(102, 103, 101.5, 102.5, 450)], go = B(102.5, 105, 102, 104.5, 1800);
  const ok = sig('bigVolDryUp', bigVol(quiet.concat([go])), {mult: 2, dry: 0.6, mode: 'ABC'});
  assert.equal(ok.pass, true); assert.match(ok.value, new RegExp('^' + D(138).replaceAll('/', '\\/') + ' 大量 K（低 100）'));
  // 同樣的大量 K 出現在高位階（之前沒有更高的價格）：不是低檔大量
  const high = sig('bigVolDryUp', bigVol(quiet.concat([go]), 100), {mult: 2, dry: 0.6, mode: 'ABC'}); assert.equal(high.pass, false); assert.match(high.value, /沒有低位階的大量 K/);
  // 量縮但跌破大量 K 低點
  const broke = sig('bigVolDryUp', bigVol([quiet[0], B(102, 103, 99.5, 102.5, 450), go]), {mode: 'ABC'}); assert.equal(broke.pass, false); assert.match(broke.value, /跌破大量 K 低點/);
  // 沒有量縮
  assert.equal(sig('bigVolDryUp', bigVol([B(103, 103.5, 101, 102, 2000), quiet[1], go]), {mode: 'ABC'}).pass, false);
  // 還沒突破整理區
  assert.equal(sig('bigVolDryUp', bigVol(quiet.concat([B(102.5, 103.4, 102, 103, 1800)])), {mode: 'ABC'}).pass, false);
  // 只看到量縮守低（不等突破）：當根也要量縮
  assert.equal(sig('bigVolDryUp', bigVol(quiet.concat([go])), {mode: 'AB'}).pass, false);
  assert.equal(sig('bigVolDryUp', bigVol(quiet.concat([B(102.5, 103, 102, 102.8, 700)])), {mode: 'AB'}).pass, true);
  // 多一根之後，觀察點仍是同一根大量 K
  assert.match(sig('bigVolDryUp', bigVol(quiet.concat([go, B(104.5, 106, 104, 105.5, 900)])), {mode: 'ABC'}).value, new RegExp('^' + D(138).replaceAll('/', '\\/')));
  // 盤中、只看量縮：成交量還沒走完，不下結論
  const bars = bigVol(quiet.concat([B(102.5, 103, 102, 102.8, 300)]));
  const r = evaluate(live(bars), {groups: [['bigVolDryUp']], cond: {bigVolDryUp: {mode: 'AB'}}}, dayCtx(bars.length, {afterClose: false}));
  assert.equal(r.checks.find(c => c.key === 'bigVolDryUp').pass, null);
  // 日 K 不足以判斷位階（需要大量 K 之前 120 根）：資料不足
  assert.equal(sig('bigVolDryUp', flat(100).concat([B(100, 104, 100, 103, 2500)], quiet, [go]), {mode: 'ABC'}).pass, null);
});
test('first move after a volume dry-up; "beats the previous five" can mean price, volume or both', () => {
  const base = v => flat(20).concat(flat(5, 100, v));
  const move = B(100, 103, 100, 102.5, 900);
  assert.equal(sig('dryUpFirstMove', base(400).concat([move]), {dry: 0.6, mode: 'both'}).pass, true);
  assert.equal(sig('dryUpFirstMove', base(800).concat([move]), {dry: 0.6, mode: 'both'}).pass, false);             // 前 5 根沒有量縮
  const thin = B(100, 103, 100, 102.5, 300);                                         // 過高但量沒有超過前 5 根
  assert.equal(sig('dryUpFirstMove', base(400).concat([thin]), {dry: 0.6, mode: 'both'}).pass, false);
  assert.equal(sig('dryUpFirstMove', base(400).concat([thin]), {dry: 0.6, mode: 'price'}).pass, true);
  assert.equal(sig('dryUpFirstMove', base(400).concat([B(100, 100.8, 99.5, 100.6, 900)]), {dry: 0.6, mode: 'volume'}).pass, true);
  assert.equal(sig('dryUpFirstMove', base(400).concat([B(102.5, 103, 100, 100.5, 900)]), {dry: 0.6, mode: 'volume'}).pass, false);  // 收黑
  assert.equal(sig('dryUpFirstMove', flat(25), {}).pass, null);
});
const anchored = tail => flat(40).concat([B(100, 106, 99.5, 105, 5000)], flat(26, 105), tail);   // 第 40 根量是之前 20 根均量的 5 倍，低點 99.5
test('a big-volume bar is a fixed event: its date and low never change; a newer event replaces it openly, and it expires', () => {
  const hold = anchored([B(105, 105, 101, 101.5), B(101.5, 102, 100.2, 100.8), B(100.8, 102, 99.9, 101.5)]);
  const lost = anchored([B(105, 105, 101, 101.5), B(101.5, 102, 100.2, 100.8), B(100.8, 101, 98, 98.5)]);
  assert.equal(sig('bigVolHold', hold).pass, true); assert.equal(sig('bigVolLowLost', hold, {within: 3}).pass, false);
  assert.equal(sig('bigVolHold', lost).pass, false); assert.equal(sig('bigVolLowLost', lost, {within: 1}).pass, true);
  assert.match(sig('bigVolLowLost', lost, {within: 1}).value, new RegExp('^' + D(40).replaceAll('/', '\\/') + ' 大量 K（量 5 倍）低點 99\\.5'));
  // 跌破是事件：3 根之後還在低點下方，選「當根」就不再列
  const stale = lost.concat([B(98.5, 99, 97, 98), B(98, 98.5, 97, 97.5), B(97.5, 98, 96.5, 97)]);
  assert.equal(sig('bigVolLowLost', stale, {within: 1}).pass, false); assert.equal(sig('bigVolLowLost', stale, {within: 5}).pass, true);
  assert.equal(sig('bigVolHold', anchored([B(105, 106, 104, 105), B(105, 106, 104, 105), B(105, 106, 104, 105)])).pass, false);   // 根本沒有回測
  assert.equal(sig('bigVolHold', flat(50)).pass, null);
  // 事件由那一天自己的資料決定：之後每多一根，日期與低點都不變，直到 60 根到期
  const ev = I.events.bigVolume, at = bars => { const S = I.series(item(bars).bars), e = ev(S, 2); return e ? [S.D[e.at], e.low] : e; };
  let grow = anchored([]);
  for (let k = 0; k < 33; k++) { grow = grow.concat([B(105, 106, 104, 105)]); eq(at(grow), [D(40), 99.5], '第 ' + grow.length + ' 根'); }
  assert.equal(grow.length, 100); eq(at(grow.concat([B(105, 106, 104, 105)])), [D(40), 99.5]);      // 評估日是第 100 根：事件還在 60 根內
  eq(at(grow.concat(flat(2, 105))), null);                                                           // 再過一根就到期：沒有事件，不會悄悄改抓第二大的量
  assert.equal(sig('bigVolHold', grow.concat(flat(2, 105))).pass, false);
  // 視窗裡量更大、但沒有達到「自己之前均量的倍數」的 K 不是事件；達標的新事件才取代舊的，卡片寫出新日期
  const louder = anchored([B(105, 106, 104, 105, 1900)]);                                            // 1900 不到當時均量（約 1200）的 2 倍
  eq(at(louder.concat(flat(1, 105))), [D(40), 99.5]);
  const newer = anchored([B(105, 108, 103, 107, 6000), B(107, 108, 106, 107), B(107, 108, 106, 107), B(107, 108, 103.5, 107)]);
  eq(at(newer), [D(67), 103]); assert.match(sig('bigVolHold', newer).value, new RegExp('^' + D(67).replaceAll('/', '\\/')));
  // 同一天、給不同長度的歷史：事件相同（不是視窗掃描）
  const longer = flat(80).concat(anchored([B(105, 105, 101, 101.5)]));
  eq(at(longer), [D(120), 99.5]); eq(at(anchored([B(105, 105, 101, 101.5)])), [D(40), 99.5]);
  // 倍數是參數：門檻調到 6 倍，5 倍那一根就不是事件
  assert.equal(sig('bigVolHold', anchored([B(105, 105, 101, 101.5), B(101.5, 102, 100.2, 100.8), B(100.8, 102, 99.9, 101.5)]), {mult: 6}).pass, false);
});
test('volume conditions keep liquidity, expansion and dry-up apart; dry-up waits for the close', () => {
  const spike = flat(30).concat([B(100, 102, 99, 101, 2000)]);
  assert.equal(sig('volSpike', spike, {mult: 2}).pass, true); assert.equal(sig('volSpike', spike, {mult: 2.1}).pass, false);
  assert.equal(sig('liquidity', spike, {lots: 1000}).pass, true); assert.equal(sig('liquidity', spike, {lots: 1001}).pass, false);   // 均量不含當日的 2000
  assert.equal(sig('liquidity', flat(30, 100, 300).concat([B(100, 102, 99, 101, 5000)]), {lots: 1000}).pass, false);                 // 今天爆量不等於平日有量
  const dry = flat(30).concat([B(100, 101, 99, 100, 500)]);
  assert.equal(sig('volDryUp', dry, {ratio: 0.6}).pass, true);
  const r = evaluate(live(dry), {groups: [['volDryUp']]}, dayCtx(31, {afterClose: false}));          // 盤中累計 500 張不代表全日量縮
  assert.equal(r.checks.find(c => c.key === 'volDryUp').pass, null); assert.equal(r.pass, false);
  // 均量基準是這個條件自己的參數：前 5 日均量 4000、前 20 日均量 1750，同一根 5000 張對兩種基準答案不同
  const five = flat(25).concat(flat(5, 100, 4000), [B(100, 102, 99, 101, 5000)]);
  assert.equal(sig('volSpike', five, {mult: 2, days: 5}).pass, false); assert.equal(sig('volSpike', five, {mult: 2, days: 20}).pass, true);
  // 「爆量與最低量」卡片裡的均量基準、「開盤跳空」卡片裡的幅度，只管它們自己那一張，不會牽動別的條件
  assert.equal(sig('volSpike', five, {mult: 2, days: 20}, {prefs: {volumeDays: 5}}).pass, true);
  const small = flat(30).concat([B(102, 105, 101.5, 104)]);                            // 開高 2%
  assert.equal(sig('gapHold', small, {minPct: 2.5}).pass, false); assert.equal(sig('gapHold', small, {minPct: 1.5}).pass, true);
  assert.equal(sig('gapHold', small, {minPct: 2.5}, {prefs: {gapPct: 1}}).pass, false);
});

/* ───── 均線糾結、布林壓縮、K 棒 ───── */
test('moving-average squeeze: needs every average in the chosen set; a missing long average is unknown, not silently dropped', () => {
  const bars = flat(85).concat([B(100, 104, 100, 103, 2500)]);
  assert.equal(sig('maSqueezeBreak', bars, {set: '5-20-60', pct: 5, minBars: 10}).pass, true);
  assert.equal(sig('maSqueezeBreak', bars, {set: '5-20-60-120', pct: 5, minBars: 10}).pass, null);                 // 86 根算不出 120 日均線
  assert.equal(sig('maSqueezeBreak', flat(145).concat([B(100, 104, 100, 103)]), {set: '5-20-60-120', pct: 5, minBars: 10}).pass, true);
  assert.equal(sig('maSqueezeBreak', flat(85).concat([B(100, 101, 99.5, 100.8)]), {set: '5-20-60'}).pass, false);   // 糾結但沒有突破
  const trend = dots(Array.from({length: 86}, (_, i) => 100 + i));                   // 一路上漲：均線早就發散
  assert.equal(sig('maSqueezeBreak', trend, {set: '5-20-60', pct: 5, minBars: 10}).pass, false);
  // 糾結突破發生在高位階：範本「低位階均線糾結突破」不成立（低位階那一項擋下）
  const t = cfg.templates.find(x => x.id === 'JE02'), high = flat(145).concat([B(100, 104, 100, 103, 2500)]);
  const r = evaluate(item(high), {groups: t.groups, groupLogic: t.groupLogic, cond: t.params}, dayCtx(high.length));
  assert.equal(r.template, 'JE02'); assert.equal(r.checks.find(c => c.key === 'maSqueezeBreak').pass, true); assert.equal(r.checks.find(c => c.key === 'lowBase').pass, false); assert.equal(r.pass, false);
});
test('Bollinger squeeze then breakout; wide bands with the same close do not qualify', () => {
  const wide = Array.from({length: 60}, (_, i) => 100 + (i % 2 ? 3 : 0)), tight = Array.from({length: 20}, (_, i) => 100 + (i % 2 ? 0.2 : 0));
  assert.equal(sig('bollSqueeze', dots(wide.concat(tight, [104])), {lookback: 60, pct: 20}).pass, true);
  const noisy = Array.from({length: 80}, (_, i) => 100 + (i % 2 ? 3 : 0));
  const r = sig('bollSqueeze', dots(noisy.concat([110])), {lookback: 60, pct: 20}); assert.equal(r.pass, false);
  assert.equal(sig('bollSqueeze', dots(wide.concat(tight, [100.1])), {lookback: 60, pct: 20}).pass, false);         // 壓縮但沒有突破上軌
  assert.equal(sig('bollSqueeze', dots(wide.concat(tight, [104])), {lookback: 120, pct: 20}).pass, null);
});
test('reversal candles need a prior move and a confirming close', () => {
  const down = dots(Array.from({length: 10}, (_, i) => 110 - i));                    // 110 → 101
  const engulf = down.concat([B(101, 101.5, 99.5, 100), B(99.8, 102, 99.5, 101.5)]);
  assert.equal(sig('bullReversal', engulf.concat([B(101.5, 103.5, 101, 103)])).pass, true);
  assert.equal(sig('bullReversal', engulf.concat([B(101.5, 102, 101, 101.8)])).pass, false);                        // 沒有越過型態高點 102
  const hammer = down.concat([B(101, 101.5, 99.5, 100), B(100, 100.3, 97, 100.2)]);
  assert.match(sig('bullReversal', hammer.concat([B(100.2, 101, 100, 100.8)])).value, /錘子/); assert.equal(sig('bullReversal', hammer.concat([B(100.2, 101, 100, 100.8)])).pass, true);
  const noDrop = dots(Array.from({length: 10}, (_, i) => 92 + i)).concat([B(101, 101.5, 99.5, 100), B(99.8, 102, 99.5, 101.5), B(101.5, 103.5, 101, 103)]);
  assert.equal(sig('bullReversal', noDrop).pass, false);                             // 之前是上漲，不是下跌後的反轉
  const up = dots(Array.from({length: 10}, (_, i) => 90 + i));                       // 90 → 99
  const bear = up.concat([B(99, 100.5, 98.5, 100), B(100.2, 100.5, 98, 98.5)]);
  assert.equal(sig('bearReversal', bear.concat([B(98.5, 99, 97, 97.5)])).pass, true);
  assert.equal(sig('bearReversal', bear.concat([B(98.5, 99, 98.2, 98.4)])).pass, false);
  const star = up.concat([B(99, 100.5, 98.5, 100), B(100, 103, 99.9, 100.2)]);
  assert.match(sig('bearReversal', star.concat([B(100.2, 100.5, 99, 99.5)])).value, /射擊之星/);
  assert.equal(sig('weakClose', flat(5).concat([B(100, 104, 99, 100.5)]), {maxPos: 0.4}).pass, true);               // 收在區間 30%
  assert.equal(sig('upperShadow', flat(5).concat([B(100, 104, 99, 100.5)]), {minPct: 40}).pass, true);              // 上影 3.5／5
  assert.equal(sig('upperShadow', flat(5).concat([B(100, 100, 100, 100)]), {minPct: 40}).pass, null);               // 一價到底：沒有區間可算
});

/* ───── 範本：高檔與低檔、組合邏輯 ───── */
const useTemplate = (id, more) => { const t = cfg.templates.find(x => x.id === id), s = {direction: t.direction, groupLogic: t.groupLogic, groups: t.groups}; return t.side === 'exit' ? {purpose: 'exit', exit: s, cond: t.params, ...more} : {...s, cond: t.params, ...more}; };
test('the same big-volume gap day reads differently at a low base and at a high base', () => {
  // 高位階：之前一路漲到區間頂，當天跳空開高、爆量、留長上影收低
  const rise = dots(Array.from({length: 130}, (_, i) => 60 + 0.4 * i));              // 60 → 111.6
  const top = rise.concat([B(115.5, 119, 113.6, 114.2, 4000)]);                       // 開高 3.5%，上影 3.5／5.4，收在區間 11%
  const jx04 = evaluate(item(top), useTemplate('JX04'), dayCtx(top.length));
  assert.equal(jx04.purpose, 'exit'); assert.equal(jx04.template, 'JX04'); assert.equal(jx04.pass, true);
  assert.equal(jx04.formula, '高位階 且 向上開盤缺口 且 量能擴張 且 （收在當日低檔 或 長上影線）');
  const o04 = evaluate(item(top), useTemplate('O04'), dayCtx(top.length)); assert.equal(o04.pass, true);
  // 同一根 K 放在低位階（之前從高處跌下來盤整）：高位階那一項不成立，範本不列
  const low = flat(20, 160).concat(flat(110, 111.6), [B(115.5, 119, 113.6, 114.2, 4000)]);
  const again = evaluate(item(low), useTemplate('JX04'), dayCtx(low.length));
  assert.equal(again.checks.find(c => c.key === 'highBase').pass, false); assert.equal(again.pass, false);
  // 高位階的強勢長紅（收在最高、沒有上影）：有量有缺口，但沒有轉弱跡象，不列為出場觀察
  const strong = rise.concat([B(115.5, 119, 115, 118.8, 4000)]);
  assert.equal(evaluate(item(strong), useTemplate('JX04'), dayCtx(strong.length)).pass, false);
  assert.equal(evaluate(item(strong), useTemplate('O04'), dayCtx(strong.length)).pass, false);
  // 進場的「爆量開盤跳空續強」對這根強勢長紅另有判斷：離 20 日均線超過 10% 才擋
  const i06 = evaluate(item(strong), useTemplate('I06'), dayCtx(strong.length));
  assert.equal(i06.purpose, 'entry'); assert.equal(i06.checks.find(c => c.key === 'gapHold').pass, true);
  assert.equal(i06.checks.find(c => c.key === 'notExtended').pass, true); assert.equal(i06.pass, true);
});
test('an unknown branch never counts as met; a met branch still lists the stock', () => {
  const bars = flat(40).concat([B(100, 103, 100, 102)]);                             // 41 根：算不出 120 日均線集合
  const any = evaluate(item(bars), {groupLogic: 'anyOfAll', groups: [['maSqueezeBreak'], ['prevHighBreak']]}, dayCtx(41));
  eq(any.groups.map(g => g.pass), [null, true]); assert.equal(any.pass, true);
  assert.ok(any.issues.some(x => /均線糾結後突破所需資料不足/.test(x)));
  const all = evaluate(item(bars), {groupLogic: 'anyOfAll', groups: [['maSqueezeBreak', 'prevHighBreak']]}, dayCtx(41));
  assert.equal(all.groups[0].pass, null); assert.equal(all.pass, false); assert.equal(all.status, '資料待核對');
  // 空的條件組不會讓全部股票都符合
  const none = evaluate(item(bars), {groups: []}, dayCtx(41)); assert.equal(none.pass, false);
  assert.equal(evaluate(item(bars), {purpose: 'exit', exit: {groups: []}}, dayCtx(41)).pass, false);
});
test('a reference price that disagrees with the last daily close (ex-dividend, split) blocks every signal', () => {
  const bars = flat(30).concat([B(104, 107, 103.5, 106, 3000)]), it = live(bars);
  assert.equal(evaluate(it, {groups: [['gapHold']]}, dayCtx(31, {afterClose: false})).pass, true);
  it.quote.prevClose = 96;                                                           // 昨收 100，但交易所參考價 96：可能是除權息
  const r = evaluate(it, {groups: [['gapHold']]}, dayCtx(31, {afterClose: false}));
  assert.equal(r.pass, false); assert.equal(r.status, '資料待核對'); assert.ok(r.issues.some(x => /除權息/.test(x)));
});

/* ───── 隨機走勢：每個條件對照照定義直接寫的算式、不看未來、盤中與盤後一致 ───── */
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function walk(seed, n, drift = 0, lots = 1) {      // lots：成交量的倍率，小一點才驗得到流動性不足的情況
  const r = rng(seed), g = () => (r() + r() + r() + r() - 2) * 1.2, bars = []; let price = 100;
  for (let i = 0; i < n; i++) {
    const jump = r() < 0.08 ? (r() - 0.5) * 0.12 : (r() - 0.5) * 0.012;              // 偶爾有跳空
    const open = +(price * (1 + jump)).toFixed(2), close = +Math.max(5, open * (1 + drift + g() * 0.02)).toFixed(2);
    const high = +(Math.max(open, close) * (1 + r() * 0.012)).toFixed(2), low = +(Math.min(open, close) * (1 - r() * 0.012)).toFixed(2);
    bars.push(B(open, high, low, close, Math.round((300 + r() * r() * 9000) * lots))); price = close;
  }
  return bars;
}
const WALKS = [walk(11, 400), walk(12, 400, 0.002), walk(13, 400, -0.002, 0.4), walk(14, 400, 0.0005, 0.4)];
const ALL = {groups: [['useGap']], cond: {}};
/* 第 t 天的評估：只給到第 t 天的日 K。 */
const evalAt = (bars, t, prefs, more) => evaluate(item(bars.slice(0, t + 1)), prefs || ALL, {...dayCtx(t + 1), all: true, ...more});
test('random walks: 34 conditions match formulas written straight from their definitions, on every day', () => {
  const mx = (a, f, t) => Math.max(...a.slice(f, t + 1)), mn = (a, f, t) => Math.min(...a.slice(f, t + 1)), mean = (a, f, t) => a.slice(f, t + 1).reduce((s, v) => s + v, 0) / (t - f + 1);
  const cross = (a, b, t, within, up, extra) => {                                    // 最近 within 根內穿越，且現在仍在那一側
    const A = i => typeof a === 'number' ? a : a[i], Bv = i => typeof b === 'number' ? b : b[i];
    if (!(up ? A(t) > Bv(t) : A(t) < Bv(t))) return false;
    for (let j = t - within + 1; j <= t; j++) if ((up ? A(j - 1) <= Bv(j - 1) && A(j) > Bv(j) : A(j - 1) >= Bv(j - 1) && A(j) < Bv(j)) && (!extra || extra(j))) return true;
    return false;
  };
  const P = cleanPrefs({}).cond, fired = {}, compared = {};
  /* 每個條件：用預設參數，照定義算出該不該成立。S 是當天評估用的那一段日 K（最多 260 根）。 */
  const expect = {
    trendUp: (S, t) => { const m = I.sma(S, 60); return S.C[t] > m[t] && m[t] > m[t - 5]; },
    maCross: (S, t) => cross(I.sma(S, 5), I.sma(S, 20), t, 3, true),
    maAlign: (S, t) => I.sma(S, 5)[t] > I.sma(S, 20)[t] && I.sma(S, 20)[t] > I.sma(S, 60)[t],
    prevHighBreak: (S, t) => S.C[t] > S.H[t - 1],
    notExtended: (S, t) => S.C[t] / I.sma(S, 20)[t] - 1 <= 0.10,
    lowBase: (S, t) => (S.C[t] - mn(S.L, t - 120, t - 1)) / (mx(S.H, t - 120, t - 1) - mn(S.L, t - 120, t - 1)) <= 0.5,
    highBase: (S, t) => (S.C[t] - mn(S.L, t - 120, t - 1)) / (mx(S.H, t - 120, t - 1) - mn(S.L, t - 120, t - 1)) >= 0.8,
    macdCross: (S, t) => cross(I.macd(S).dif, I.macd(S).dea, t, 3, true),
    macdZero: (S, t) => cross(I.macd(S).dif, 0, t, 3, true),
    macdConverge: (S, t) => { const h = I.macd(S).hist; return h[t] < 0 && h[t] > h[t - 1] && h[t - 1] > h[t - 2] && h[t - 2] > h[t - 3]; },
    rsiRecover: (S, t) => cross(I.rsi(S, 14), 50, t, 3, true),
    kdCross: (S, t) => { const v = I.kd(S); return cross(v.k, v.d, t, 3, true, j => Math.min(v.k[j - 1], v.d[j - 1]) < 20); },
    adxTrend: (S, t) => { const d = I.dmi(S, 14); return d.adx[t] >= 25 && d.adx[t] > d.adx[t - 1] && d.adx[t - 1] > d.adx[t - 2] && d.pdi[t] > d.mdi[t]; },
    rocUp: (S, t) => cross(I.roc(S, 12), 0, t, 3, true),
    cciUp: (S, t) => cross(I.cci(S, 20), 100, t, 3, true),
    mfiUp: (S, t) => cross(I.mfi(S, 14), 50, t, 3, true),
    bollSqueeze: (S, t) => { const b = I.boll(S, 20, 2); let below = 0; for (let j = t - 120; j <= t - 1; j++) if (b.width[j] <= b.width[t - 1] * (1 + 1e-9)) below++; return below / 120 <= 0.2 && S.C[t] > b.up[t]; },
    gapHold: (S, t) => S.O[t] > S.H[t - 1] && (S.O[t] / S.C[t - 1] - 1) * 100 >= 2.5 && S.L[t] > S.H[t - 1],
    volSpike: (S, t) => S.V[t] >= 2 * mean(S.V, t - 20, t - 1),
    volDryUp: (S, t) => S.V[t] <= 0.6 * mean(S.V, t - 20, t - 1),
    liquidity: (S, t) => mean(S.V, t - 20, t - 1) >= 1000,
    obvHigh: (S, t) => I.obv(S)[t] > mx(I.obv(S), t - 20, t - 1),
    cmfPositive: (S, t) => { const c = I.cmf(S, 20); return c[t] > 0 && c[t - 1] > 0 && c[t - 2] > 0; },
    supportBreak: (S, t) => S.C[t] < mn(S.L, t - 20, t - 1),
    maLost: (S, t) => cross(S.C, I.sma(S, 20), t, 3, false),
    maDeadCross: (S, t) => cross(I.sma(S, 5), I.sma(S, 20), t, 3, false),
    macdDead: (S, t) => cross(I.macd(S).dif, I.macd(S).dea, t, 3, false),
    rsiFall: (S, t) => cross(I.rsi(S, 14), 70, t, 3, false),
    kdDead: (S, t) => { const v = I.kd(S); return cross(v.k, v.d, t, 3, false, j => Math.max(v.k[j - 1], v.d[j - 1]) > 80); },
    gapDown: (S, t) => S.O[t] < S.L[t - 1] && (S.O[t] / S.C[t - 1] - 1) * 100 <= -2.5,
    chandelier: (S, t) => S.C[t] < mx(S.H, t - 21, t) - 3 * I.atr(S, 22)[t],
    weakClose: (S, t) => (S.C[t] - S.L[t]) / (S.H[t] - S.L[t]) <= 0.4,
    upperShadow: (S, t) => (S.H[t] - Math.max(S.O[t], S.C[t])) / (S.H[t] - S.L[t]) >= 0.4,
    obvDiverge: (S, t) => { const o = I.obv(S); for (let j = t - 9; j <= t; j++) if (S.H[j] > mx(S.H, j - 20, j - 1) && o[j] <= mx(o, j - 20, j - 1)) return true; return false; }
  };
  assert.equal(Object.keys(expect).length, 34);
  WALKS.forEach(bars => {
    for (let t = 170; t < bars.length; t++) {
      const r = evalAt(bars, t), S = I.series(item(bars.slice(Math.max(0, t - 259), t + 1)).bars);     // 評估用手上全部的連續日 K，最多 260 根
      assert.equal(S.n, Math.min(260, t + 1));
      Object.keys(expect).forEach(id => {
        const c = r.checks.find(x => x.key === id), want = expect[id](S, S.t);
        assert.equal(c.pass, want, `${id} 第 ${t} 天：前台 ${c.pass}，照定義 ${want}（${c.value}）`);
        fired[id] = (fired[id] || 0) + (want ? 1 : 0); compared[id] = (compared[id] || 0) + 1;
      });
      assert.equal(JSON.stringify(P), JSON.stringify(cleanPrefs(ALL).cond));
    }
  });
  // 每個條件都真的發生過，也不是永遠成立
  Object.keys(expect).forEach(id => assert.ok(fired[id] > 0 && fired[id] < compared[id], `${id} 成立 ${fired[id]}／${compared[id]} 次`));
  console.log('   每個條件比對 ' + compared.trendUp + ' 個交易日；成立次數 ' + Object.keys(expect).map(id => id + ' ' + fired[id]).join('、'));
});
test('every condition fires at least once on random data and none is stuck', () => {
  const seen = {};
  WALKS.concat([walk(21, 400, 0.001), walk(22, 400, -0.001, 0.4), walk(23, 400), walk(24, 400, 0, 0.4)]).forEach(bars => {
    for (let t = 170; t < bars.length; t++) evalAt(bars, t).checks.forEach(c => { const s = seen[c.flag] || (seen[c.flag] = {yes: 0, no: 0, unknown: 0}); s[c.pass === true ? 'yes' : c.pass === false ? 'no' : 'unknown']++; });
  });
  cfg.conditions.forEach(c => { const s = seen[c.id]; assert.ok(s && s.yes > 0 && s.no > 0, c.id + ' ' + JSON.stringify(s)); assert.equal(s.unknown, 0, c.id + ' 資料齊全時不該有資料不足'); });
});
test('no look-ahead: a day evaluated with only its own history equals the same day evaluated with later bars present', () => {
  let compared = 0;
  WALKS.slice(0, 2).forEach(bars => {
    for (let t = 170; t < bars.length - 30; t += 3) {
      const a = evalAt(bars, t), b = evaluate(item(bars), ALL, {...dayCtx(t + 1), all: true});      // b 的資料裡有第 t 天之後的日 K
      a.checks.forEach((c, n) => { assert.equal(b.checks[n].key, c.key); assert.equal(b.checks[n].pass, c.pass, c.key + ' 第 ' + t + ' 天'); eq(b.checks[n].value, c.value, c.key + ' 第 ' + t + ' 天'); compared++; });
    }
  });
  assert.ok(compared > 5000);
});
test('intraday quote and the landed daily bar give the same answers, except where a finished day\'s volume is required', () => {
  const wait = new Set(['volDryUp']);                                                // 這些要等收盤；其餘兩邊必須相同
  let same = 0;
  WALKS.slice(0, 2).forEach(bars => {
    for (let t = 170; t < bars.length; t += 7) {
      const part = bars.slice(0, t + 1), a = evalAt(bars, t), b = evaluate(live(part), ALL, {...dayCtx(t + 1), afterClose: false, all: true});
      assert.equal(b.complete, false);
      a.checks.forEach((c, n) => {
        if (wait.has(c.key)) { assert.equal(b.checks[n].pass, null, c.key); return; }
        assert.equal(b.checks[n].pass, c.pass, c.key + ' 第 ' + t + ' 天'); same++;
      });
    }
  });
  assert.ok(same > 3000);
});
test('holes in the daily bars make every indicator unknown instead of computing across the gap', () => {
  const bars = WALKS[0].slice(0, 200), it = item(bars); it.bars.splice(150, 1);       // 少了一天
  const r = evaluate(it, {groups: [['trendUp']]}, {...dayCtx(200), all: true});
  r.checks.slice(8).forEach(c => assert.equal(c.pass, null, c.key)); assert.equal(r.pass, false); assert.equal(r.status, '資料待核對');
});
test('a confirmed market closure is not a trading day; a shared gap that is not confirmed stays a gap', () => {
  eq(cfg.confirmedClosed, ['2026/07/10']); assert.ok(!ctx.window.MarketBattle.closedDays, '前台不該自己用缺 K 推測休市');
  const n = 150, cal = calendar.slice(0, n), X = cal[90];
  const mk = (code, skip) => ({code, name: code, bars: walk(Number(code), n).map((b, i) => ({date: cal[i], ...b})).filter(b => !skip.includes(b.date))});
  const a = mk('31', [X]), c = {date: cal[n - 1], calendar: cal, volumeUnit: 'lots', afterClose: true}, p = {groups: [['useMacd', 'trendUp']], cond: {trendUp: {maDays: 60}}};
  // 行事曆還把那一天當交易日（沒有核對）：缺口照實保留，需要連續日 K 的指標都是資料不足
  const raw = evaluate(a, p, c); eq(raw.checks.filter(k => k.enabled).map(k => k.pass), [null, null]); assert.equal(raw.status, '資料待核對');
  // 核對過、行事曆拿掉那一天之後才算得出來；結果和「那一天本來就不在行事曆裡」完全相同
  const clean = {...c, calendar: cal.filter(d => d !== X)}, ok = evaluate(a, p, clean);
  assert.ok(ok.checks.filter(k => k.enabled).every(k => k.pass !== null));
  // 個別停牌（行事曆有、這一檔沒有）仍是資料不足
  assert.ok(evaluate(mk('33', [X, cal[120]]), p, clean).checks.filter(k => k.enabled).every(k => k.pass === null));
});

/* ───── v138：且／或可以各組不同 ───── */
test('each group has its own and/or and the groups are joined by another: (A and B) or (C or D), (A or B) and C, A and B and C', () => {
  // 事件日：跳空成立（A）、量 1500 不到 2.5 倍（B 不成立）、收在 20 日均線上（C）、沒有突破 60 日高點以外的條件……用四個原有條件組合
  const it = item(flat(60).concat([B(103, 107, 102, 106, 1500)])), c = dayCtx(61), base = {volumeMultiple: 2.5};
  const run = more => evaluate(it, {...base, ...more}, c);
  let r = run({groups: [['useGap', 'useVolume'], ['useTrend', 'useKD']], ops: ['and', 'or'], join: 'or'});     // （A 且 B）或（C 或 D）
  eq(r.groups.map(g => [g.op, g.word, g.pass]), [['and', '且', false], ['or', '或', true]]); assert.equal(r.pass, true); assert.equal(r.join, 'or');
  assert.equal(r.formula, '（向上開盤缺口 且 成交量倍數） 或 （20 日均線 或 KD 相對位置）');
  r = run({groups: [['useGap', 'useVolume'], ['useTrend', 'useKD']], ops: ['and', 'or'], join: 'and'});        // （A 且 B）且（C 或 D）
  assert.equal(r.pass, false); assert.equal(r.formula, '（向上開盤缺口 且 成交量倍數） 且 （20 日均線 或 KD 相對位置）');
  r = run({groups: [['useGap', 'useVolume'], ['useTrend']], ops: ['or', 'and'], join: 'and'});                 // （A 或 B）且 C
  eq(r.groups.map(g => g.pass), [true, true]); assert.equal(r.pass, true);
  r = run({groups: [['useGap'], ['useVolume'], ['useTrend']], ops: ['and', 'and', 'and'], join: 'and'});       // A 且 B 且 C
  assert.equal(r.pass, false); assert.equal(r.formula, '向上開盤缺口 且 成交量倍數 且 20 日均線');
  // 舊的兩種固定組合是特例，結果與公式文字都不變
  eq(cleanPrefs({groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'allOfAny'}).ops, ['or', 'or']);
  assert.equal(run({groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'allOfAny'}).formula, '（向上開盤缺口 或 成交量倍數） 且 20 日均線');
  assert.equal(run({groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'anyOfAll'}).formula, '（向上開盤缺口 且 成交量倍數） 或 20 日均線');
  // 空掉的組連同它的且／或一起整理掉，其餘各組的且／或不會錯位
  const p = cleanPrefs({groups: [['useGap'], [], ['useVolume', 'useTrend'], ['bogus']], ops: ['and', 'or', 'or', 'and'], join: 'and'});
  eq([p.groups, p.ops, p.join], [[['useGap'], ['useVolume', 'useTrend']], ['and', 'or'], 'and']); eq(p.unknown.entry, ['bogus']);
});

/* ───── v138：暖機與歷史長度 ───── */
test('warm-up: indicators that carry a seed refuse to judge on short history; 160 vs 260 vs full history is measured, not assumed', () => {
  eq(I.warm, {macd: 130, rsi: 120, adx: 135}); assert.equal(I.maxHistory, 259);
  // 129 根不判、130 根才判（MACD）；119／120（RSI）；134／135（ADX）
  const w = walk(77, 300), cut = (id, n, params) => sig(id, w.slice(0, n), params).pass;
  assert.equal(cut('macdCross', 129), null); assert.notEqual(cut('macdCross', 130), null);
  assert.equal(cut('rsiRecover', 119), null); assert.notEqual(cut('rsiRecover', 120), null);
  assert.equal(cut('adxTrend', 134), null); assert.notEqual(cut('adxTrend', 135), null);
  assert.match(sig('macdDead', w.slice(0, 100)).why, /需要至少 130 根連續日 K/);
  /* 同一天，給 160 根、260 根、全部（最多 400 根）三種長度的歷史，比指標的值與訊號。
     數字印出來並設上限：值的差異要小到不影響判讀，訊號不一致的次數要是 0。 */
  const ids = ['macdCross', 'macdDead', 'macdZero', 'macdConverge', 'rsiRecover', 'rsiFall', 'adxTrend', 'kdCross', 'kdDead'];
  const worst = {hist: 0, rsi: 0, adx: 0, k: 0}, flips = {'160↔260': 0, '260↔全部': 0}; let days = 0, signals = 0;
  const P = cleanPrefs({}).cond, env = {complete: true};
  [walk(41, 400), walk(42, 400, 0.002), walk(43, 400, -0.002), walk(44, 400, 0.0005), walk(45, 400), walk(46, 400, -0.001)].forEach(bars => {
    const all = item(bars).bars;
    for (let t = 260; t < all.length; t++) {
      const S = [160, 260, t + 1].map(n => I.series(all.slice(t + 1 - n, t + 1))), price = all[t].close;
      const v = S.map(x => ({hist: I.macd(x).hist[x.t] / price * 100, rsi: I.rsi(x, 14)[x.t], adx: I.dmi(x, 14).adx[x.t], k: I.kd(x).k[x.t]}));
      Object.keys(worst).forEach(k => { worst[k] = Math.max(worst[k], Math.abs(v[0][k] - v[2][k])); });
      ids.forEach(id => { const r = S.map(x => I.run(id, x, P[id], env).pass); signals++; if (r[0] !== r[1]) flips['160↔260']++; if (r[1] !== r[2]) flips['260↔全部']++; });
      days++;
    }
  });
  console.log(`   ${days} 個交易日 × ${ids.length} 個條件 = ${signals} 次判定；160 根與全部歷史相比，最大差異：MACD 柱體 ${worst.hist.toExponential(1)}%（占股價）、RSI ${worst.rsi.toExponential(1)}、ADX ${worst.adx.toExponential(1)}、K 值 ${worst.k.toExponential(1)}；` +
    `訊號不一致：160↔260 根 ${flips['160↔260']} 次、260 根↔全部 ${flips['260↔全部']} 次`);
  assert.ok(worst.hist < 1e-3 && worst.rsi < 0.02 && worst.adx < 0.05 && worst.k < 1e-6, JSON.stringify(worst));
  assert.equal(flips['260↔全部'], 0); assert.ok(flips['160↔260'] <= 2, '160 根與 260 根的訊號差異超出預期：' + flips['160↔260']);
});
test('more bars than the calendar covers: use the part the calendar can vouch for instead of turning the whole stock unknown', () => {
  // 正式站實際發生的情形（2026/10/07）：後端給 250 根日 K，行事曆只往回涵蓋 244 個交易日
  const bars = walk(88, 250), it = item(bars), c = dayCtx(250), p = {groups: [['macdCross', 'trendUp', 'useMacd']], cond: {trendUp: {maDays: 60}}};
  const full = evaluate(it, p, c), short = evaluate(it, p, {...c, calendar: calendar.slice(6, 250)});      // 行事曆少了最前面 6 天
  assert.ok(full.checks.filter(k => k.enabled).every(k => k.pass !== null));
  assert.ok(short.checks.filter(k => k.enabled).every(k => k.pass !== null), '行事曆比日 K 短時不該整檔資料不足');
  // 用的是行事曆涵蓋的那 243 根：和直接只給那一段日 K 的結果相同
  const same = evaluate(item2(bars.slice(6), 6), p, {...c, calendar: calendar.slice(6, 250)});
  eq(short.checks.map(k => [k.key, k.pass, k.value]), same.checks.map(k => [k.key, k.pass, k.value]));
  // 行事曆涵蓋的那一段裡如果有缺日，仍然是資料不足
  const holed = item(bars); holed.bars.splice(200, 1);
  assert.ok(evaluate(holed, p, {...c, calendar: calendar.slice(6, 250)}).checks.filter(k => k.enabled).every(k => k.pass === null));
});
test('fixed events keep their date and level from one day to the next unless an explicit rule replaces or expires them', () => {
  let checked = 0, replaced = 0, expired = 0;
  [walk(51, 400), walk(52, 400, 0.002), walk(53, 400, -0.002, 0.4)].forEach(bars => {
    const all = item(bars).bars; let prev = null, prevBreak = null;
    for (let t = 200; t < all.length; t++) {
      const S = I.series(all.slice(Math.max(0, t - 259), t + 1)), e = I.events.bigVolume(S, 2), b = I.events.breakout(S, 10);
      const now = e ? {date: S.D[e.at], low: e.low, high: e.high} : null, nowBreak = b ? {date: S.D[b.at], level: b.level} : null;
      if (prev) {
        const age = t - all.findIndex(x => x.date === prev.date);
        if (now && now.date === prev.date) { eq(now, prev); checked++; }                          // 同一個事件：價位一模一樣
        else if (now) { assert.ok(now.date > prev.date || age > 60, '事件不能換成更早的日期'); replaced++; }   // 被更新的事件取代，或舊的到期後接到更早的…不允許
        else { assert.ok(age > 60, `事件 ${prev.date} 才 ${age} 根就不見了`); expired++; }       // 只有滿 60 根才會消失
      }
      if (prevBreak && nowBreak && nowBreak.date === prevBreak.date) eq(nowBreak, prevBreak);     // 突破價在事件當下就固定
      prev = now; prevBreak = nowBreak;
    }
  });
  assert.ok(checked > 300 && replaced > 5 && expired >= 0, JSON.stringify({checked, replaced, expired}));
});

/* ───── v138：安全回退與不認得的條件 ───── */
test('settings a build does not understand stop the screen instead of silently loosening it; the old storage slot stays safe for old code', () => {
  const it = item(flat(60).concat([B(103, 107, 102, 106, 1500)])), c = dayCtx(61);
  // 這一版不認得的條件（例如從更新的版本退回來）：不把它拿掉後照剩下的條件篩
  const p = cleanPrefs({groups: [['useGap', 'someFutureCondition']]});
  eq(p.groups, [['useGap']]); eq(p.unknown, {entry: ['someFutureCondition'], exit: []});
  const blocked = evaluate(it, {groups: [['useGap', 'someFutureCondition']]}, c);
  assert.equal(blocked.checks[0].pass, true); assert.equal(blocked.pass, false); assert.equal(blocked.status, '資料待核對');
  assert.ok(blocked.issues.some(x => /不認得的條件（someFutureCondition），已停用篩選/.test(x)));
  // 存起來再讀回來仍然停用（不會因為存了一次就忘記）；使用者明確清掉之後才恢復
  const saved = JSON.parse(JSON.stringify(p)); eq(cleanPrefs(saved).unknown.entry, ['someFutureCondition']);
  assert.equal(evaluate(it, saved, c).pass, false);
  assert.equal(evaluate(it, {...saved, unknown: {entry: [], exit: []}}, c).pass, true);
  // 另一個目的不受影響
  assert.equal(evaluate(it, {...saved, purpose: 'exit'}, c).issues.some(x => /不認得/.test(x)), false);
  // 留給舊版（v136）讀的內容：舊版看得懂就原樣；看不懂就給舊版的預設，不給少了條件的版本
  const safe = x => JSON.parse(JSON.stringify(cfg.legacySafe(x)));
  eq(safe({groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'allOfAny', direction: 'down', gapPct: 3, volumeDays: 10, volumeCustom: true}),
     {gapPct: 3, volumeMultiple: 1, volumeDays: 10, volumeCustom: true, minLots: 1000, closePosition: 0.7, trendDays: 20, breakoutDays: 20, volumeMode: 'actual', direction: 'down', groups: [['useGap', 'useVolume'], ['useTrend']], groupLogic: 'allOfAny'});
  const narrowed = safe({groups: [['lowBase', 'maSqueezeBreak', 'useVolume']], gapPct: 3});                    // 三個條件用「且」：舊版只認得最後一個
  eq([narrowed.groups, narrowed.groupLogic, narrowed.gapPct], [[['useGap', 'useVolume']], 'anyOfAll', 3]);
  eq(safe({groups: [['useGap', 'useVolume'], ['useTrend', 'useKD']], ops: ['and', 'or'], join: 'or'}).groups, [['useGap', 'useVolume']]);   // 混合的且／或舊版表達不了
});

/* ───── v138：常用條件 ───── */
test('favourites: at most ten, replace by name, restore the whole set-up including numbers, and never apply one with unknown conditions', () => {
  const t = cfg.templates.find(x => x.id === 'JE02'), mine = {groups: t.groups, ops: ['and'], join: 'or', cond: {...t.params, volSpike: {mult: 3, days: 5}}, gapPct: 4, volumeDays: 33, volumeCustom: true};
  const f = cfg.favoriteFrom(mine, '  低位階   糾結突破  ');
  assert.equal(f.name, '低位階 糾結突破'); assert.equal(f.purpose, 'entry');
  eq(f.setup, {direction: 'up', groups: [['lowBase', 'maSqueezeBreak', 'volSpike']], ops: ['and'], join: 'or'});
  eq(Object.keys(f.cond).sort(), ['lowBase', 'maSqueezeBreak', 'volSpike']); eq(f.cond.volSpike, {days: 5, mult: 3}); eq([f.top.gapPct, f.top.volumeDays, f.top.volumeCustom], [4, 33, true]);
  // 套用：從別的設定（出場、預設值）套回來，整套一模一樣；另一個目的不動
  const other = {purpose: 'exit', exit: {groups: [['macdDead']], ops: ['and'], join: 'or', direction: 'down'}, cond: {volSpike: {mult: 9}}};
  const back = cfg.applyFavorite(other, f);
  assert.equal(back.purpose, 'entry'); eq([back.groups, back.ops, back.join], [f.setup.groups, ['and'], 'or']); eq(back.cond.volSpike, {days: 5, mult: 3});
  eq([back.gapPct, back.volumeDays], [4, 33]); eq(back.exit.groups, [['macdDead']]);
  assert.ok(cfg.sameFavorite(cfg.favoriteFrom(back, 'x'), f)); assert.ok(!cfg.sameFavorite(cfg.favoriteFrom({...back, gapPct: 5}, 'x'), f));
  // 出場的常用條件
  const ex = cfg.favoriteFrom({purpose: 'exit', exit: {groups: [['highBase'], ['weakClose', 'upperShadow']], ops: ['and', 'or'], join: 'and', direction: 'up'}}, '高檔轉弱');
  eq(ex.setup, {direction: 'up', groups: [['highBase'], ['weakClose', 'upperShadow']], ops: ['and', 'or'], join: 'and'});
  const applied = cfg.applyFavorite(mine, ex); assert.equal(applied.purpose, 'exit'); eq(applied.exit.ops, ['and', 'or']); eq(applied.groups, f.setup.groups);
  // 清單整理：最多十組（留最後存的）、同名只留一個、沒有名稱或沒有條件的丟掉、亂七八糟的資料不會壞
  const many = Array.from({length: 13}, (_, i) => ({...f, name: '第 ' + i + ' 組'}));
  eq(cfg.cleanFavorites(many).map(x => x.name), many.slice(3).map(x => x.name)); assert.equal(cfg.maxFavorites, 10);
  eq(cfg.cleanFavorites([f, {...ex, name: f.name}]).map(x => [x.name, x.purpose]), [[f.name, 'exit']]);
  eq(cfg.cleanFavorites([null, 5, 'x', {}, {name: 'a'}, {name: '', setup: f.setup}, {name: 'b', setup: {groups: []}}, {name: 'c', setup: {groups: 'no'}}]), []);
  eq(cfg.cleanFavorites('nope'), []); eq(cfg.cleanFavorites(undefined), []);
  // 存進去再讀出來（經過 JSON）完全相同
  eq(cfg.cleanFavorites(JSON.parse(JSON.stringify([f, ex]))), [f, ex]);
  // 參數超出範圍的存檔：讀回來是合法值，不照單全收
  assert.equal(cfg.cleanFavorites([{...f, cond: {...f.cond, volSpike: {mult: 999, days: 7}}}])[0].cond.volSpike.mult, 2);
  // 含有這一版不認得的條件：整組保留但不能套用，也不會被拿掉條件後當成同一組
  const future = {...f, name: '新版存的', setup: {...f.setup, groups: [['lowBase', 'fromTheFuture']]}};
  const kept = cfg.cleanFavorites([future])[0]; eq(kept.blocked, ['fromTheFuture']); eq(kept.setup.groups, [['lowBase', 'fromTheFuture']]);
  eq(cfg.applyFavorite(mine, kept).groups, cleanPrefs(mine).groups);                  // 套用它什麼都不會變
});
test('cost: a full watch-list evaluates well within an interaction budget', () => {
  const items = Array.from({length: 240}, (_, n) => item(walk(100 + n, 260))), c = dayCtx(260);
  const heavy = useTemplate('JE02');
  const time = prefs => { const t0 = process.hrtime.bigint(); items.forEach(it => evaluate(it, prefs, c)); return Number(process.hrtime.bigint() - t0) / 1e6; };
  time({}); const base = time({}), tpl = time(heavy), all = (() => { const t0 = process.hrtime.bigint(); items.slice(0, 24).forEach(it => evaluate(it, {}, {...c, all: true})); return Number(process.hrtime.bigint() - t0) / 1e6; })();
  console.log(`   240 檔、每檔 260 根：預設條件 ${base.toFixed(0)} ms、範本 JE02 ${tpl.toFixed(0)} ms；一頁 24 檔全部條件 ${all.toFixed(0)} ms`);
  // 這裡的執行環境比瀏覽器慢；v136 同樣的重算要 4 秒左右。守住的是「不要退回去」，瀏覽器裡的實際時間由 check_battle_signals_ui.py 量。
  assert.ok(base < 2000 && tpl < 2000, '240 檔重算超過 2000 ms');
});
console.log(count + ' battle signal checks passed');
