/* 戰情行情端到端模擬（v136）。不連任何外部服務；沿用 check_quote_flow.js 的假時鐘、假試算表與假報價來源。

   node scripts/check_battle_data.js

   一、精簡格式沒有少東西：同一時刻分別用原格式與精簡格式讀同一批股票，還原後逐欄相同
       （160 根日K 的日期／開／高／低／收／量、報價的每一個欄位、行事曆、清單、其餘欄位）。
   二、一次可讀的檔數：原格式 24、精簡格式 60；超過會被拒絕，不會悄悄截斷。
   三、盤中：報價排程更新之後，戰情頁拿到的是五分鐘內的成交價與累計量，條件判定為「盤中條件符合／未符合」，不是待核對。
   四、收盤後、日K落地前：用收盤結算後的報價判定，時間標「收盤」。
   五、日K落地後：改用日K，判定為「盤後條件符合」，數值就是日K 的收盤與全日量。
   六、批次來源不通時：沒更新到的股票照實標「報價已過期」，不拿舊價判成符合。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const flow = require('./check_quote_flow.js');
const {makeWorld, taipei, CODES, HELD, DAY, base, closeOf} = flow;

// 前台的計算核心與還原函式（Battle.html 第一段）加上設定（BattleConfig.html 第一段）
const read = n => fs.readFileSync('public-site/gas-source/' + n, 'utf8');
const front = {window: {}, console};
vm.createContext(front);
vm.runInContext(read('BattleConfig.html').match(/<script>([\s\S]*?)<\/script>/)[1], front);
vm.runInContext(read('Battle.html').match(/<script>([\s\S]*?)<\/script>/)[1], front);
const plain = v => JSON.parse(JSON.stringify(v));
// 還原函式跑在另一個執行環境，回來的物件先轉成普通資料再比（比的是內容，不是物件來自哪裡）。
const {evaluate} = front.window.MarketBattle, expand = item => plain(front.window.MarketBattle.expand(item));
const report = [];

/** 跑到某個時刻：照排程每五分鐘一棒報價；13:31 之後另跑收盤結算；kAt 之後日K落地。 */
function runUntil(w, untilHm, {kAt = '99:99'} = {}) {
  let landed = false;
  for (let t = taipei(DAY, '09:00:10'); ; t += 300000) {
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    if (hm > untilHm) break;
    w.clock.now = Math.max(w.clock.now, t);
    if (!landed && hm >= kAt) { w.landDailyK(DAY); landed = true; }
    if (hm <= '13:40') w.ctx.refreshQuoteCacheJob();
    if (hm >= '13:31') w.ctx.settleClosingQuotesTick_();
  }
  w.clock.now = Math.max(w.clock.now, taipei(DAY, untilHm + ':30'));
}
/** 前台實際的讀法：第一個回應帶清單與行事曆，其餘用精簡、省略重複的批次；回傳 {context, items}。 */
function loadAll(w) {
  const first = plain(w.ctx.apiGetBattleData(null, {v: 2, lite: false}));
  const items = first.items.map(expand), have = new Set(items.map(i => i.code)), rest = first.universe.map(u => u.code).filter(c => !have.has(c));
  let requests = 1;
  for (let n = 0; n < rest.length; n += first.batchMax) {
    const part = plain(w.ctx.apiGetBattleData(rest.slice(n, n + first.batchMax), {v: 2, lite: true}));
    assert.equal(part.lite, true); assert.equal(part.calendarKey, first.calendarKey); assert.equal(part.universeKey, first.universeKey);
    assert.equal(part.date, first.date);
    items.push(...part.items.map(expand)); requests++;
  }
  return {context: first, items, requests};
}

/* ---- 一、二：格式與批次 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  w.seedHistory();
  runUntil(w, '10:02');
  const codes = CODES.slice(0, 24);
  const v1 = plain(w.ctx.apiGetBattleData(codes)), v2 = plain(w.ctx.apiGetBattleData(codes, {v: 2}));
  assert.equal(v1.format, undefined); assert.equal(v2.format, 2);
  assert.ok(v1.items.every(i => i.bars.length === 130 && i.quote && i.b === undefined));
  assert.deepEqual(v2.items.map(expand), v1.items, '精簡格式還原後要和原格式逐欄相同');
  for (const k of Object.keys(v1)) if (k !== 'items') assert.deepEqual(v2[k], v1[k], '欄位 ' + k + ' 不同');
  assert.deepEqual(Object.keys(v2).filter(k => !(k in v1)).sort(), ['batchMax', 'calendarKey', 'format', 'universeKey']);
  // 邊界值：沒有成交量（null）、小數、整數都原樣回來。
  const odd = [{date: '2026/01/02', open: 12.35, high: 12.4, low: 12.3, close: 12.35, volume: null}, {date: '2026/10/06', open: 2575, high: 2590, low: 2565, close: 2585, volume: 18343.125}];
  assert.deepEqual(plain(expand({code: 'x', name: 'y', error: '', b: plain(w.ctx.battleCompactBars_(odd)), q: null})).bars, odd);
  assert.deepEqual(expand(v1.items[0]), v1.items[0], '原格式的資料原樣通過');
  // 省略版只少清單與行事曆，其餘相同，而且帶著同樣的指紋。
  const lite = plain(w.ctx.apiGetBattleData(codes, {v: 2, lite: true}));
  assert.deepEqual(Object.keys(v2).filter(k => !(k in lite)).sort(), ['calendar', 'universe']);
  assert.deepEqual(lite.items, v2.items); assert.equal(lite.calendarKey, v2.calendarKey); assert.equal(lite.universeKey, v2.universeKey);
  const bytes = o => Buffer.byteLength(JSON.stringify(o));
  const perBarV1 = bytes(v1.items.map(i => i.bars)) / (24 * 130), perBarV2 = bytes(v2.items.map(i => i.b)) / (24 * 130);
  assert.ok(perBarV2 < perBarV1 * 0.6, `精簡後每根 ${perBarV2.toFixed(0)} 位元組，原本 ${perBarV1.toFixed(0)}`);
  // 批次上限
  assert.throws(() => w.ctx.apiGetBattleData(CODES.slice(0, 25)), /一次最多讀取 24 檔/);
  assert.equal(w.ctx.apiGetBattleData(CODES.slice(0, 60), {v: 2}).items.length, 60);
  assert.throws(() => w.ctx.apiGetBattleData(CODES.slice(0, 61), {v: 2}), /一次最多讀取 60 檔/);
  assert.equal(w.ctx.apiGetBattleData(null).items.length, 24); assert.equal(w.ctx.apiGetBattleData(null, {v: 2}).items.length, 60);
  const sixty = bytes(plain(w.ctx.apiGetBattleData(CODES.slice(0, 60), {v: 2, lite: true}))), twentyFour = bytes(v1);
  report.push(`一、精簡格式還原後與原格式逐欄相同（24 檔 × 130 根日K、報價與其餘欄位）；每根日K ${perBarV1.toFixed(0)} → ${perBarV2.toFixed(0)} 位元組`);
  report.push(`二、一次可讀：原格式 24 檔、精簡格式 60 檔，超過即拒絕；精簡 60 檔 ${(sixty / 1024).toFixed(0)}KB，原格式 24 檔 ${(twentyFour / 1024).toFixed(0)}KB`);
}

/* ---- 三、四、五：一個交易日裡三個時點的戰情判定 ---- */
{
  const w = makeWorld({direct: false, relay: true});
  w.seedHistory();
  // 用「開盤跳空」以外、盤中數字會動的條件來看：收盤站上 20 日均線 或 成交量達 5 日均量的 0.5 倍以上
  const prefs = {groupLogic: 'anyOfAll', groups: [['useTrend'], ['useVolume']], volumeDays: 5, volumeMultiple: 1, minLots: 1};

  runUntil(w, '10:02');
  let {context, items, requests} = loadAll(w);
  assert.equal(items.length, CODES.length); assert.equal(requests, 4, `240 檔應該 4 次請求，實際 ${requests}`);
  assert.equal(context.afterClose, false); assert.equal(context.date, DAY);
  let results = items.map(i => evaluate(i, prefs, context));
  assert.ok(items.every(i => i.quote && i.quote.stale === false && i.quote.date === DAY), '盤中每一檔都要是新鮮的當日報價');
  const ages = items.map(i => (w.clock.now - taipei(DAY, String(i.quote.stamp).slice(11))) / 60000);
  assert.ok(Math.max(...ages) <= 5.1, `盤中報價最舊 ${Math.max(...ages).toFixed(1)} 分鐘`);
  assert.ok(results.every(r => r.status === '盤中條件符合' || r.status === '未符合設定'), '盤中不應該有待核對：' + JSON.stringify(results.find(r => r.status === '資料待核對')));
  assert.ok(results.every(r => r.complete === false && r.phase === '盤中／盤後待日 K' && /^10-06 10:0\d:\d\d$/.test(r.quoteTime)));
  const sample = results.find(r => r.code === CODES[3]), q = items.find(i => i.code === CODES[3]).quote;
  assert.equal(sample.current.close, q.last); assert.equal(sample.current.volume, q.volume);          // 判定用的就是那一筆報價
  assert.equal(sample.volumeMA, 1000); assert.equal(sample.volumeRatio, q.volume / 1000);
  const intradayPass = results.filter(r => r.pass).length;
  report.push(`三、盤中 10:02：240 檔 ${requests} 次請求；報價最舊 ${Math.max(...ages).toFixed(1)} 分鐘、全部新鮮；判定「盤中條件符合」${intradayPass} 檔、未符合 ${240 - intradayPass} 檔、待核對 0 檔`);

  // 同一個世界繼續走到收盤後、日K還沒落地
  runUntil2(w, '10:07', '13:41');
  ({context, items} = loadAll(w));
  results = items.map(i => evaluate(i, prefs, context));
  assert.equal(context.afterClose, false);
  assert.ok(items.every(i => i.quote.stale === false && i.quote.last === closeOf(i.code) && i.quote.time === '10-06 收盤'), '收盤後、日K落地前，報價要是收盤價並標收盤');
  assert.ok(results.every(r => r.complete === false && r.current.close === closeOf(r.code) && r.quoteTime === '10-06 收盤'));
  assert.ok(results.every(r => r.status === '盤中條件符合' || r.status === '未符合設定'));
  report.push(`四、13:41（日K未落地）：240 檔報價皆為收盤價、標「收盤」；仍以報價判定，待核對 0 檔`);

  // 日K落地、過 14:00
  w.landDailyK(DAY);
  w.clock.now = taipei(DAY, '14:50:00');
  w.ctx.settleClosingQuotesTick_();
  ({context, items} = loadAll(w));
  results = items.map(i => evaluate(i, prefs, context));
  assert.equal(context.afterClose, true);
  assert.ok(items.every(i => i.bars[i.bars.length - 1].date === DAY && i.bars.length === 131));
  assert.ok(results.every(r => r.complete === true && r.phase === '已落地日 K' && r.current.close === closeOf(r.code) && r.current.volume === 1000 + Number(r.code) % 50));
  assert.ok(results.every(r => r.status === '盤後條件符合' || r.status === '未符合設定'));
  assert.ok(results.every(r => r.quoteTime === '10-06 收盤'));
  const closePass = results.filter(r => r.pass).length;
  assert.ok(closePass > 0);
  report.push(`五、14:50（日K已落地）：240 檔改用日K 判定，「盤後條件符合」${closePass} 檔；收盤與全日量與日K 相同`);
}

/* ---- 六：批次來源整天不通時，不拿舊價判成符合 ---- */
{
  const w = makeWorld({direct: false, relay: 'missing'});
  w.seedHistory();
  runUntil(w, '12:32');
  const {context, items} = loadAll(w);
  const results = items.map(i => evaluate(i, {groups: [['useTrend']]}, context));
  const held = results.filter(r => HELD.includes(r.code)), others = results.filter(r => !HELD.includes(r.code));
  assert.ok(held.every(r => r.status !== '資料待核對'), '持有中的每一棒都更新，應該判得出來');
  const blocked = others.filter(r => r.status === '資料待核對');
  assert.ok(blocked.length > 150, `沒更新到的股票應該大多是待核對，實際 ${blocked.length}`);
  assert.ok(blocked.every(r => r.pass === false && r.issues.some(x => x === '報價已過期' || x === '缺少當日開高低收或成交量')));
  assert.ok(others.filter(r => r.pass).every(r => { const i = items.find(x => x.code === r.code); return i.quote && i.quote.stale === false; }), '判成符合的一定是新鮮報價');
  report.push(`六、批次來源整天不通（12:32）：持有 ${held.length} 檔照常判定；其餘 ${blocked.length} 檔標「待核對」，沒有任何一檔用過期報價判成符合`);
}

/** 從某個時刻接著跑到另一個時刻（同一個世界）。 */
function runUntil2(w, fromHm, untilHm) {
  for (let t = taipei(DAY, fromHm + ':10'); ; t += 300000) {
    const hm = new Date(t + 8 * 3600000).toISOString().slice(11, 16);
    if (hm > untilHm) break;
    w.clock.now = Math.max(w.clock.now, t);
    if (hm <= '13:40') w.ctx.refreshQuoteCacheJob();
    if (hm >= '13:31') w.ctx.settleClosingQuotesTick_();
  }
  w.clock.now = Math.max(w.clock.now, taipei(DAY, untilHm + ':30'));
}

report.forEach(line => console.log('ok ' + line));
console.log(report.length + ' battle data checks passed');
