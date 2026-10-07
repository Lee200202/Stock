/* 新版沒有改變 v136 的判定：同樣的行情、同樣的舊設定，新舊兩版的評估結果逐欄相同。
   舊版程式直接從 git 取（v136r3，6adc404）。取不到舊版（例如淺層 checkout）時：
     一般執行：印出「略過」並以代碼 0 結束；
     發布驗收：加 --require-baseline（或環境變數 REQUIRE_BASELINE=1），取不到就以代碼 2 失敗——缺基準不能算通過。
   只在本機計算。 */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const root = path.resolve(__dirname, '..'), OLD = '6adc404';
const block = html => html.replace(/\r\n/g, '\n').match(/<script>([\s\S]*?)<\/script>/)[1];
function load(readFile) {
  const ctx = {window: {}, console}; vm.createContext(ctx);
  vm.runInContext(block(readFile('BattleConfig.html')), ctx); vm.runInContext(block(readFile('Battle.html')), ctx);
  return ctx.window.MarketBattle;
}
let old;
try { old = load(n => execFileSync('git', ['show', OLD + ':public-site/gas-source/' + n], {cwd: root, encoding: 'utf8', maxBuffer: 1 << 26, stdio: ['ignore', 'pipe', 'ignore']})); }
catch (e) {
  const strict = process.argv.includes('--require-baseline') || process.env.REQUIRE_BASELINE === '1';
  console.log((strict ? '未完成' : '略過') + '：取不到 ' + OLD + ' 的舊版程式（' + String(e.message).split('\n')[0] + '）' + (strict ? '；發布驗收不能缺這一項' : ''));
  process.exit(strict ? 2 : 0);
}
const now = load(n => fs.readFileSync(path.join(root, 'public-site/gas-source', n), 'utf8'));

function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const calendar = []; { let d = new Date('2025-10-02T12:00:00Z'); while (calendar.length < 245) { if (d.getUTCDay() > 0 && d.getUTCDay() < 6) calendar.push(d.toISOString().slice(0, 10).replaceAll('-', '/')); d.setUTCDate(d.getUTCDate() + 1); } }
function stock(seed) {
  const r = rng(seed), bars = []; let price = 20 + r() * 400;
  calendar.slice(-160).forEach(date => {
    const jump = r() < 0.1 ? (r() - 0.45) * 0.14 : (r() - 0.5) * 0.012;
    const open = +(price * (1 + jump)).toFixed(2), close = +Math.max(1, open * (1 + (r() - 0.5) * 0.06)).toFixed(2);
    bars.push({date, open, high: +(Math.max(open, close) * (1 + r() * 0.015)).toFixed(2), low: +(Math.min(open, close) * (1 - r() * 0.015)).toFixed(2), close, volume: Math.round(200 + r() * r() * 12000)});
    price = close;
  });
  const kind = seed % 10;
  if (kind === 7) bars.splice(120, 1);                                               // 缺一天
  if (kind === 8) bars[100].high = bars[100].low - 1;                                // 壞資料
  if (kind === 9) bars[150].volume = null;                                           // 沒有成交量
  return {code: String(1000 + seed), name: '測試' + seed, bars, error: seed % 37 === 0 ? '日 K 讀取失敗' : ''};
}
/* v136 存得出來的設定：八個原有條件、兩種組合方式、兩個方向、估量與自訂均量天數。 */
const F = ['useGap', 'useVolume', 'useTrend', 'useBreakout', 'useBody', 'useMacd', 'useKD', 'useBoll'];
function prefs(seed) {
  const r = rng(seed * 7919), pick = () => F[Math.floor(r() * 8)], n = 1 + Math.floor(r() * 3);
  const groups = Array.from({length: n}, () => Array.from(new Set(Array.from({length: 1 + Math.floor(r() * 3)}, pick))));
  const p = {gapPct: [0.5, 1, 2.5, 4][Math.floor(r() * 4)], volumeMultiple: [1, 1.5, 2.5][Math.floor(r() * 3)], volumeDays: [5, 20, 10, 33][Math.floor(r() * 4)], minLots: [0, 500, 1000][Math.floor(r() * 3)],
    direction: r() < 0.5 ? 'up' : 'down', volumeMode: r() < 0.3 ? 'estimate' : 'actual', trendDays: [5, 10, 20, 60, 120][Math.floor(r() * 5)], breakoutDays: r() < 0.5 ? 20 : 60, closePosition: [0.5, 0.7, 0.9][Math.floor(r() * 3)],
    groupLogic: r() < 0.5 ? 'anyOfAll' : 'allOfAny', groups};
  if (seed % 6 === 0) return {useGap: r() < 0.7, useVolume: r() < 0.7, useTrend: r() < 0.5, useMacd: r() < 0.3, logic: r() < 0.5 ? 'any' : 'all', direction: p.direction, gapPct: p.gapPct};   // 更早的格式
  return p;
}
/* 新版多出來的欄位先拿掉再比：目的、範本、指標版本、每個條件的依據性質、各組與組間的且／或。其餘必須一模一樣。 */
function strip(r) {
  const c = JSON.parse(JSON.stringify(r)); delete c.purpose; delete c.template; delete c.signalVersion; delete c.join;
  // MACD 的說明文字由「最近至多 160 根」改成「260 根」（新版後端給的歷史較長）；給同樣 160 根時數值相同，文字差異不算。
  c.checks.forEach(x => { delete x.origin; if (x.rule) x.rule = x.rule.replace('最近至多 260 根', '最近至多 160 根'); }); c.groups.forEach(g => { delete g.op; delete g.word; }); return c;
}
let compared = 0, passed = 0, pending = 0;
for (let s = 1; s <= 240; s++) {
  const it = stock(s), day = calendar[calendar.length - 1];
  for (let k = 0; k < 6; k++) {
    const p = prefs(s * 10 + k), live = k >= 4;
    let item = it, ctx = {date: day, calendar, volumeUnit: 'lots', afterClose: true};
    if (live) {                                                                      // 盤中：最後一根改成報價
      const last = it.bars[it.bars.length - 1], prev = it.bars[it.bars.length - 2];
      item = {...it, bars: it.bars.slice(0, -1), quote: {date: day, open: last.open, high: last.high, low: last.low, last: last.close, volume: last.volume, prevClose: k === 5 && s % 9 === 0 ? prev.close * 0.9 : prev.close,
        stamp: day.replaceAll('/', '-') + 'T03:10:00.000Z', time: '11:10', stale: s % 13 === 0}};
      ctx = {...ctx, afterClose: false};
    }
    const a = strip(old.evaluate(item, p, ctx)), b = strip(now.evaluate(item, p, ctx));
    assert.deepEqual(b, a, `第 ${s} 檔、設定 ${k}`);
    compared++; if (a.pass) passed++; if (a.status === '資料待核對') pending++;
  }
}
assert.ok(passed > 50 && pending > 50 && passed + pending < compared, `符合 ${passed}、待核對 ${pending}`);
console.log(`ok v136（${OLD}）與現在的版本：${compared} 次評估逐欄相同（其中符合 ${passed}、資料待核對 ${pending}、未符合 ${compared - passed - pending}）`);
