// v58（2026/09/24）持股追蹤取價與版面：
//   一、表格三欄：股票｜交易軸｜最新說明；狀態、持有天數、提及欄拿掉（網站；信件本來就沒有這三欄）。
//   二、交易軸：左端進場（日期、進場日最低）、右端目前／出場，報酬寫在線上；張震明講的價位是灰字。
//   三、個股面板：取價區間圖畫出來時，圖上已有的價格、日期、報酬不再用卡片重複；報酬寫在線軸上。
//   四、正式站實測的版面修正：跑馬燈不塞佔位字、只有一筆的市場圖不畫空圖、信件名稱不帶「*」、
//       績效圖滾輪還給頁面、後台今日狀態不露灰格、現有持股的輸入欄收起來、步驟直式清單不畫框。
// 實際排版在 scripts/preview_v54.js 以 1440／1280／900／768／390／360 量測與截圖。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, 'apps-script', f), 'utf8');
const js = read('JavaScript.html'), css = read('Stylesheet.html'), admin = read('Admin.html');
const market = read('Market.html'), mail = read('MailService.gs'), sheet = read('SheetService.gs');

// 一、二：交易軸實際跑一次
const tableSrc = js.slice(js.indexOf('  function md_(d) {'), js.indexOf('  /* 管理功能已全部移到後台'));
const ctx = vm.createContext({
  esc: s => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;'),
  fmtPx: v => v == null ? '—' : Number(v).toFixed(2), fmtRet: v => (v > 0 ? '+' : '') + Number(v).toFixed(2) + '%',
  signCls: v => v > 0 ? 'up' : v < 0 ? 'down' : 'flat', statedTag: n => n ? '<span class="stated">' + n + '</span>' : '',
  nameCell: n => n, datedNote: (n, d) => d.slice(5).replace(/^0/, '') + '｜' + n, trackerView: 'held',
  bindRows: () => {}, trackerLoaded: false, $: () => ({ set innerHTML(v) { ctx.out = v; } })
});
vm.runInContext(tableSrc, ctx);
const held = { code: '6770', name: '力積電', valid: true, stillHeld: true, rounds: 2, cumRet: 14.2, roundRets: '+5%×+8%',
  roundStart: '2026/09/23', entry: 72.8, current: 75.2, ret: 3.3, entryNote: '明講 73.5 以下買進', latestReason: '拉回不破月線就續抱', latestReasonDate: '2026/09/22' };
ctx.renderTrackerRows({}, [held]);
const html = ctx.out;
assert(/<th>股票<\/th><th>進場 → 目前<\/th><th>最新說明<\/th>/.test(html), '三欄表頭');
assert(!/持有天數|提及|持有中|狀態/.test(html), '狀態、持有天數、提及不再出現：' + html);
assert(/tx-up/.test(html) && /<span class="tx-ret">\+3\.30%<\/span>/.test(html), '報酬寫在線上、上漲用紅');
assert(/9\/23 進場<\/small><b>72\.80<\/b><span class="stated">明講 73\.5 以下買進<\/span>/.test(html), '左端：日期、進場日最低、明講灰字');
assert(/<small>目前<\/small><b>75\.20<\/b>/.test(html), '右端：目前價');
assert(/累積 \+14\.20%/.test(html) && /第 2 回合/.test(html), '多回合：累積報酬與回合標籤');
ctx.trackerView = 'exited';
ctx.renderTrackerRows({}, [Object.assign({}, held, { stillHeld: false, rounds: 1, lastSell: '2026/09/30', current: 70.1, ret: -3.71,
  exitNote: '明講 80 以上賣出', exitReason: '9/30｜轉為觀望不碰' })]);
assert(/進場 → 出場/.test(ctx.out) && /出場原因/.test(ctx.out) && /tx-down/.test(ctx.out) && /9\/30 出場/.test(ctx.out) && /明講 80 以上賣出/.test(ctx.out));
ctx.renderTrackerRows({}, [Object.assign({}, held, { rounds: 1, ret: null, current: null, why: '日K快取還沒有這一檔的最新報價' })]);
assert(/tx-na/.test(ctx.out) && /tx-why/.test(ctx.out) && />待補</.test(ctx.out), '缺價時寫原因');

// 三、個股面板
assert(/var chart = pxRangeHtml\(t\);\s*if \(chart\) \{/.test(js));
const chartKv = js.slice(js.indexOf('    if (chart) {'), js.indexOf('    } else {', js.indexOf('    if (chart) {')));
assert(!/kv\('(狀態|本回合進場|持有天數|進場價|未實現報酬|影片提及)'/.test(chartKv), '圖上已有的資訊不用卡片重複');
assert(/pxr-move/.test(js) && /pxr-ret/.test(js) && /\.pxr-ret \{/.test(css));

// 四、正式站實測的版面修正
assert(!/本支影片未說明<\/span><\/div>/.test(js) && !/function absentChip/.test(js), '跑馬燈不塞佔位字');
assert(/function bandEmptyText\(\)/.test(js) && /\.mui-marquee\.is-still \{ animation: none;/.test(css));
assert(/OVERVIEW_NOSHOW = !!d\.noShow;\s*renderLedger\(d\);\s*renderBand\(d\);/.test(js), '旗標在畫跑馬燈前設好');
assert(/今天沒有直播節目，當日買賣以上方會員通知為準/.test(js));
assert(/if\(parsed\.length<2\)\{return '<div class="market-chart-empty">開盤不久，走勢累積中/.test(market));
assert(/\.market-card, \.market-card:first-child \{ padding:20px; \}/.test(market), '手機上每張市場卡同寬');
const mctx = vm.createContext({}); vm.runInContext(mail.match(/function mailStockName_\(v\) \{[\s\S]*?\n\}/)[0], mctx);
assert.equal(mctx.mailStockName_('國巨*'), '國巨'); assert.equal(mctx.mailStockName_('聖暉＊ '), '聖暉 '); assert.equal(mctx.mailStockName_('台積電'), '台積電');
assert.equal((mail.match(/mailStockName_\(cells\[0\]\)/g) || []).length, 2, '信件兩種表格都用');
assert(/handleScroll: \{ mouseWheel: false/.test(js) && /grid: \{ vertLines: \{ visible: false \} \}/.test(js), '績效圖滾輪還給頁面、拿掉垂直格線');
assert(/\.astat \{ gap: 12px; background: none; border: 0;/.test(admin), '今日狀態三格不露灰格');
assert(/<div class="he-form" id="hf' \+ i \+ '" hidden>/.test(admin) && /h-edit/.test(admin), '成本輸入欄收起來');
assert(/border: 0; background: none; border-radius: var\(--r-sm\); \} \/\* v58：直式清單不畫框線/.test(admin));
assert(/\.tech-v45 \.tech-steps \{ border-bottom: 0;/.test(css));

// 取價規則的說明文字與後端一致
assert(/進場價取進場日當日最低，灰字是張震明講的價位/.test(js));
assert(/進場價一律取進場日的當日最低價（2026\/09\/24 起）/.test(sheet) && /出場價一律取出場日的當日最高價（2026\/09\/24 起）/.test(sheet));
console.log('PASS: v58 tracker three-column trade axis, detail chart without duplicate cards, live-site layout fixes.');
