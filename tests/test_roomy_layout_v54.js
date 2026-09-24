// v54 寬鬆版面（2026/09/24 管理者：「一排放六格太過擠，本網站理念是寬鬆、排版舒適」）
// 資訊卡一排最多三格、步驟列一排最多五格；另外核對版本沿革的「部分已由 v54 取代」與日曆圖例。
// 實際排出來幾格由 scripts/preview_v54.js 在 1440／1280／1024／900／768／390／360 寬度量測。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, 'apps-script', f), 'utf8');
const admin = read('Admin.html'), css = read('Stylesheet.html'), md = read('MarketDetail.html');
const js = read('JavaScript.html'), idx = read('Index.html'), log = read('Changelog.html');

// 一、後台今日狀態：auto-fill 的最小寬度取 240px 與三分之一欄寬較大者 → 最多三格
assert(/\.health \{ display: grid; grid-template-columns: repeat\(auto-fill, minmax\(max\(240px, calc\(\(100% - 32px\) \/ 3\)\), 1fr\)\);/.test(admin), '今日狀態一排最多三格');
assert(!/\.health \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/.test(admin), '手機不再硬排兩格');
assert(/\.sms-merge-note, \.sms-bucket \{ grid-template-columns: repeat\(auto-fill, minmax\(max\(240px, calc\(\(100% - 16px\) \/ 2\)\), 1fr\)\);/.test(admin), '會員簡訊四格改兩格');
assert(/\.steps \{ --step-min: 180; --step-gap: 10; --step-max: 5;/.test(admin), '步驟列至少 180px、最多五格');

// 二、layoutSteps 真的照 --step-max 封頂（抽出函式在 vm 裡跑）
const src = admin.match(/function layoutSteps\(el\)\{[\s\S]*?\n\}/)[0];
function cols(n, w, vars) {
  const el = { children: { length: n }, clientWidth: w, style: { v: {}, setProperty(k, v) { this.v[k] = v; } } };
  const ctx = vm.createContext({
    document: { documentElement: {} },
    getComputedStyle: e => ({ getPropertyValue: k => (e === el ? vars : { '--rd-fs': '1' })[k] || '' }),
    parseFloat, Math
  });
  vm.runInContext(src, ctx);
  ctx.layoutSteps(el);
  return el.style.v['--step-cols'];
}
const v = { '--step-min': '180', '--step-gap': '10', '--step-max': '5' };
assert.equal(cols(15, 1290, v), 5, '十五步寬螢幕 5×3');
assert.equal(cols(10, 1290, v), 5, '十步 5×2');
assert.equal(cols(5, 1290, v), 5, '取稿五步一排剛好');
assert.equal(cols(4, 1290, v), 5, '四步仍以五格寬排，不撐成四個大格');
assert.equal(cols(15, 900, v), 4, '1024 寬（步驟列內寬約 900px）一排四格');
assert.equal(cols(15, 700, v), 3, '768 寬時一排三格');
assert.equal(cols(15, 1290, { '--step-min': '84', '--step-gap': '5' }), 8, '沒有 --step-max 時維持原算法（舊版後台）');

// 三、前台：技術說明步進器三顆一排、即時數字與個股面板最多三格、首頁四格 1180px 以下 2×2
assert(/\.tech-v45 \.tech-steps \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\); gap: 10px;/.test(css), '步進器三顆一排');
assert(/\.live-stats \{ display: grid; grid-template-columns: repeat\(auto-fill, minmax\(max\(130px, calc\(\(100% - 28px\) \/ 3\)\), 1fr\)\);/.test(css), '即時數字一排三格');
assert(/\.kv-grid \{ grid-template-columns: repeat\(auto-fill, minmax\(max\(150px, calc\(\(100% - 24px\) \/ 3\)\), 1fr\)\);\s*gap: 12px; background: none; border: 0; \}/.test(css), '個股面板三格、分開的小卡（不露灰底）');
assert(/@media \(max-width: 1180px\) \{ \.stats \{ grid-template-columns: repeat\(2, 1fr\); \} \}/.test(css), '首頁四格窄時 2×2');
assert(/html\[data-fs="x"\] \.stats/.test(css), '大字級提早 2×2');
assert(/#marketOverlay \.metrics\{display:grid;grid-template-columns:repeat\(auto-fill,minmax\(max\(140px,calc\(\(100% - 72px\) \/ 3\)\),1fr\)\);align-items:start\}/.test(md), '市場詳情數字平均分散、最多三格');
// 新規則在 v54 樣式層裡、晚於原本的 1px 格線與六欄步進器
const layer = css.indexOf('/* ====== v54 樣式層 ====== */');
assert(layer > 0 && css.indexOf('.tech-v45 .tech-steps { grid-template-columns: repeat(3') > layer);
assert(css.indexOf('.tech-v45 .tech-steps { display:grid; grid-template-columns:repeat(6') < layer);

// 四、K 線元件沒載入時不丟例外
assert(/if \(!window\.LightweightCharts\) \{\s*\$\('chart'\)\.innerHTML = '<p class="emptyline">K 線圖元件沒有載入成功/.test(js));
assert(/kv\('影片提及', t\.mentions != null \? t\.mentions \+ ' 次' : '(\\u2014|—)'\)/.test(js), '提及次數缺值不顯示 undefined');

// 五、版本沿革：三條舊紀錄標「部分已由 v54 取代」；C10 標籤不留單字一行；F7 圖例
assert.equal((log.match(/<div class="ver-sup">部分已由 v54 取代：/g) || []).length, 3);
assert(/\.ver-sup \{/.test(css));
assert(/\.tsum-i \.k, \.stat \.k, \.kv-cell \.k, \.live-stats span \{ text-wrap: balance; \}/.test(css));
assert(!/dot-none"><\/span>無<\/div>/.test(idx), '前台圖例不寫「無」');
assert.equal((idx.match(/dot-none"><\/span>沒有紀錄<\/div>/g) || []).length, 4);
console.log('roomy layout v54 ok');
