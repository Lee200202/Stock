// 十年日K（v200）前端合併與指標核對：歷史檔清理、與試算表日K合併、分割口徑、週K月K與 Apps Script aggregate_ 逐根相同、
// 均線／MACD／KD／布林／RSI 在約 2,450 根上與逐根重算的獨立寫法一致、長區間起點。
process.env.TZ = 'Asia/Taipei';   // Apps Script 專案時區；aggregate_ 用本地時間的星期幾
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const js = fs.readFileSync('public-site/gas-source/JavaScript.html', 'utf8');
const gs = fs.readFileSync('public-site/gas-source/Quoteservice.gs', 'utf8');
const tide = fs.readFileSync('public-site/gas-source/TideDesign.html', 'utf8');
const cut = (src, from, to) => { const a = src.indexOf(from), b = src.indexOf(to, a); assert(a >= 0 && b > a, '找不到 ' + from); return src.slice(a, b); };

const ctx = { console, Math, Date, Object, Array, String, Number, isFinite, css: () => '#000', macdMap: {} };
vm.createContext(ctx);
vm.runInContext(cut(js, '  function pad2(n)', '\n', 0) + '\n', ctx);
vm.runInContext(cut(js, '  var HISTORY_BASE', '  function loadCandles'), ctx);
vm.runInContext(cut(js, '  function toTime(d)', '  /* 成交量（張）的顯示'), ctx);
vm.runInContext(cut(js, '  function sma(arr, n)', '  function drawMaLegend'), ctx);
vm.runInContext(cut(js, '  function rangeStartIndex(data, months)', '  /* 資料少的股票'), ctx);
vm.runInContext('var TideRsi = ' + cut(tide, 'function rsi(rows,n){', '  window.TideMath') + ';' +
  'function num(value){return value!==null&&value!==undefined&&value!==""&&Number.isFinite(Number(value))?Number(value):null;}', ctx);
const tz = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' });
const gas = { Utilities: { formatDate: d => tz.format(d).replace(/-/g, '/') }, TZ: 'Asia/Taipei', Math, Date, Object };
vm.createContext(gas);
vm.runInContext(cut(gs, 'function aggregate_(daily, unit)', '/* ----'), gas);
let n = 0; const ok = (cond, msg) => { assert(cond, msg); n++; };

// 1. 模擬十年交易日（跳過週末、偶爾停牌一週），開高低收合理、量為整數股
let seed = 7; const rnd = () => (seed = (seed * 48271) % 2147483647) / 2147483647;
const days = []; let px = 80;
for (let t = Date.UTC(2016, 9, 24); t <= Date.UTC(2026, 9, 21); t += 86400000) {
  const d = new Date(t), wd = d.getUTCDay();
  if (wd === 0 || wd === 6 || rnd() < 0.03) continue;
  const o = px * (1 + (rnd() - 0.5) * 0.02), c = o * (1 + (rnd() - 0.5) * 0.05);
  const h = Math.max(o, c) * (1 + rnd() * 0.01), l = Math.min(o, c) * (1 - rnd() * 0.01);
  px = c;
  days.push([d.toISOString().slice(0, 10), +o.toFixed(2), +h.toFixed(2), +l.toFixed(2), +c.toFixed(2), Math.floor(rnd() * 5e6) + 1]);
}
ok(days.length > 2300, '十年日K根數 ' + days.length);

// 2. 歷史檔清理：代號不符整份不用；日期不遞增、高低不合理、非數字的列略過；量股→張
ok(vm.runInContext('cleanHistory', ctx)({ code: '9999', rows: days }, '2330') === null, '代號不符要退回');
const dirty = days.slice(0, 5).concat([[days[4][0], 1, 1, 1, 1, 1], ['2026-13-01', 1, 1, 1, 1, 1], [days[5][0], 10, 9, 11, 10, 1], [days[6][0], 'x', 1, 1, 1, 1]]);
const cleaned = ctx.cleanHistory({ code: '2330', rows: dirty, adjustments: [{ date: '2020-01-02', factor: 4 }, { date: 'bad', factor: 2 }] }, '2330');
ok(cleaned.rows.length === 5 && cleaned.rows[0].volume === days[0][5] / 1000 && cleaned.rows[0].date === days[0][0].replace(/-/g, '/'), '清理後只留合理的列、量換成張');
ok(cleaned.adjustments.length === 1 && cleaned.adjustments[0].date === '2020/01/02', '分割紀錄格式不對的略過');

// 3. 合併：重疊日期以歷史為準；歷史之後的日期接試算表；歷史缺的較早日期依之後的分割換算；盤中預覽保留
const hist = ctx.cleanHistory({ code: '2330', rows: days.slice(0, -2), adjustments: [{ date: days[100][0], factor: 4 }] }, '2330');
const slash = s => s.replace(/-/g, '/');
const gasDay = days.slice(-250).map(r => ({ date: slash(r[0]), open: r[1] + 1, high: r[2] + 1, low: r[3] + 1, close: r[4] + 1, volume: r[5] / 1000 }));
gasDay.push({ date: '2026/10/22', open: 1, high: 2, low: 1, close: 2, volume: 3, _provisional: true });
const lost = { date: slash(days[50][0]).replace(/\d\d$/, m => m), open: 400, high: 404, low: 396, close: 400, volume: 1 };
const merged = ctx.mergeHistoryDay(gasDay.concat([{ date: '2017/01/01', open: 400, high: 404, low: 396, close: 400, volume: 1 }]), hist);
const byDate = {}; merged.forEach(r => { byDate[r.date] = r; });
ok(byDate[slash(days[days.length - 3][0])].close === days[days.length - 3][4], '重疊日期用歷史檔的價');
ok(byDate[slash(days[days.length - 1][0])].close === days[days.length - 1][4] + 1, '歷史之後的日期用試算表的價');
ok(byDate['2017/01/01'].close === 100 && byDate['2017/01/01'].high === 101, '分割前的試算表列除以分割倍數');
ok(merged[merged.length - 1]._provisional && merged.every((r, i) => !i || merged[i - 1].date < r.date), '盤中預覽保留、日期嚴格遞增');
ok(ctx.splitFactorAfter([{ date: '2020/01/02', factor: 4 }, { date: '2023/05/05', factor: 0.5 }], '2019/12/31') === 2 &&
  ctx.splitFactorAfter([{ date: '2020/01/02', factor: 4 }], '2020/01/02') === 1, '進場價換算：只算進場之後的分割');
void lost;

// 4. 週K、月K：與 Apps Script aggregate_ 逐根相同（後端量為股、加總後才除以 1000）
const sharesRows = days.map(r => ({ date: slash(r[0]), open: r[1], high: r[2], low: r[3], close: r[4], volume: r[5] }));
const lotsRows = sharesRows.map(r => Object.assign({}, r, { volume: r.volume / 1000 }));
for (const unit of ['week', 'month']) {
  const want = gas.aggregate_(sharesRows, unit).map(b => Object.assign({}, b, { volume: b.volume / 1000 }));
  const got = ctx.aggregateDaily(lotsRows.concat([{ date: '2026/10/22', open: 1, high: 9, low: 1, close: 2, volume: 9, _provisional: true }]), unit);
  ok(got.length === want.length, unit + ' 根數 ' + got.length + ' vs ' + want.length);
  got.forEach((b, i) => { const w = want[i]; ['date', 'open', 'high', 'low', 'close', 'volume'].forEach(f => assert.strictEqual(b[f], w[f], unit + ' 第 ' + i + ' 根 ' + f + '：' + b[f] + ' vs ' + w[f])); });
  n++;
}
const yearEnd = ctx.aggregateDaily([{ date: '2024/12/31', open: 1, high: 1, low: 1, close: 1, volume: 1 }, { date: '2025/01/03', open: 2, high: 2, low: 2, close: 2, volume: 1 }], 'week');
ok(yearEnd.length === 1 && yearEnd[0].date === '2025/01/03', '跨年那一週算同一根');

// 5. 指標：與逐根重算的寫法比較（2,450 根）
const bars = lotsRows;
const maxDiff = (a, b) => Math.max(...a.map((x, i) => Math.abs(x - b[i])));
for (const len of [5, 20, 60, 240, 1200]) {
  const got = ctx.sma(bars, len).map(p => p.value);
  const want = bars.slice(len - 1).map((_, i) => bars.slice(i, i + len).reduce((s, r) => s + r.close, 0) / len);
  ok(got.length === want.length && maxDiff(got, want) < 1e-9, 'MA' + len + ' 誤差 ' + maxDiff(got, want));
}
const emaRef = (v, len) => { const k = 2 / (len + 1); const out = []; v.forEach((x, i) => out.push(i ? x * k + out[i - 1] * (1 - k) : x)); return out; };
const closes = bars.map(r => r.close), dif = emaRef(closes, 12).map((x, i) => x - emaRef(closes, 26)[i]), dea = emaRef(dif, 9);
const md = ctx.computeMacd(bars);
ok(md.m.length === bars.length && maxDiff(md.m.map(p => p.value), dif) < 1e-9 && maxDiff(md.s.map(p => p.value), dea) < 1e-9 &&
  maxDiff(md.h.map(p => p.value), dif.map((x, i) => (x - dea[i]) * 2)) < 1e-9, 'MACD 12/26/9 每根一致、根數與 K 線相同');
let K = 50, D = 50; const kRef = [], dRef = [];
bars.forEach((r, i) => { if (i < 8) return; const w = bars.slice(i - 8, i + 1), hi = Math.max(...w.map(x => x.high)), lo = Math.min(...w.map(x => x.low));
  const rsv = hi === lo ? 50 : (r.close - lo) / (hi - lo) * 100; K = K * 2 / 3 + rsv / 3; D = D * 2 / 3 + K / 3; kRef.push(K); dRef.push(D); });
const kd = ctx.computeKD(bars, 9);
ok(kd.k.length === bars.length && !('value' in kd.k[7]) && maxDiff(kd.k.slice(8).map(p => p.value), kRef) < 1e-9 && maxDiff(kd.d.slice(8).map(p => p.value), dRef) < 1e-9, 'KD 9,3,3 一致、前 8 根留空白點');
const boll = ctx.computeBoll(bars, 20, 2);
const mid = bars.slice(19).map((_, i) => bars.slice(i, i + 20).reduce((s, r) => s + r.close, 0) / 20);
const sd = bars.slice(19).map((_, i) => Math.sqrt(bars.slice(i, i + 20).reduce((s, r) => s + (r.close - mid[i]) ** 2, 0) / 20));
ok(maxDiff(boll[1].map(p => p.value), mid) < 1e-9 && maxDiff(boll[0].map(p => p.value), mid.map((m, i) => m + 2 * sd[i])) < 1e-9, '布林 20,2 一致');
let G = 0, L = 0; const rsiRef = [];
for (let i = 1; i < bars.length; i++) { const dlt = bars[i].close - bars[i - 1].close, g = Math.max(0, dlt), l = Math.max(0, -dlt);
  if (i <= 14) { G += g / 14; L += l / 14; } else { G = (G * 13 + g) / 14; L = (L * 13 + l) / 14; } if (i >= 14) rsiRef.push(L === 0 ? 100 : 100 - 100 / (1 + G / L)); }
ok(maxDiff(ctx.TideRsi(bars, 14).map(p => p.value), rsiRef) < 1e-9, 'RSI 14（Wilder）一致');

// 6. 長區間起點：最後一根往回 N 個月之後的第一根
const last = bars[bars.length - 1].date;
for (const m of [36, 60, 120]) {
  const i = ctx.rangeStartIndex(bars, m), y = +last.slice(0, 4) - m / 12, cutDate = y + last.slice(4);
  ok(bars[i].date >= cutDate && (i === 0 || bars[i - 1].date < cutDate), m + ' 個月起點 ' + bars[i].date);
}
console.log('完整歷史日K合併與指標：' + n + ' 項通過');
