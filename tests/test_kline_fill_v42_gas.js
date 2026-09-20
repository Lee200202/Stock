// v42（2026/09/16）：管理者回報寬螢幕上一年 53 根週K 左半邊空白、滾輪不能放大、MACD 左邊一大段空白。
// 取消「靠右留白、鎖住縮放」模式；MACD 從第一根起算；圖表再拉高。
const fs = require('fs'), path = require('path'), assert = require('assert');
const read = f => fs.readFileSync(path.join(path.resolve(__dirname, '..', 'apps-script'), f), 'utf8');
const js = read('JavaScript.html'), css = read('Stylesheet.html');

// 高度：K 線 520、成交量 220、MACD／KD 220；手機 420／180／180
assert(/#chart \{ height: 520px; \}/.test(css));
assert(/#volChart \{ height: 220px; border-top: none; \}/.test(css));
assert(/#macdChart, #kdChart \{ height: 220px; border-top: none; \}/.test(css));
const mobile = css.slice(css.indexOf('@media (max-width: 600px) {\n  #chart') >= 0 ? css.indexOf('@media (max-width: 600px) {\n  #chart') : css.indexOf('@media (max-width: 600px) {\r\n  #chart'));
assert(/#chart \{ height: 420px; \}\s*#volChart \{ height: 180px; \}\s*#macdChart, #kdChart \{ height: 180px; \}/.test(mobile.slice(0, 200)), '手機高度');

// 不再有靠右留白模式；每次套區間都開著滾輪縮放
assert(!/kShortMode|KBAR_MAX_PX/.test(js));
const at = js.indexOf('function applyChartRange(');
const body = js.slice(at, js.indexOf('\n  }\n', at) >= 0 ? js.indexOf('\n  }\n', at) : js.indexOf('\r\n  }\r\n', at));
assert(/var range = \{ from: from - 0\.5, to: n - 0\.5 \};/.test(body), '一律填滿');
assert(/handleScale: \{ mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true \}/.test(body));
assert(/handleScroll: \{ mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false \}/.test(body));
assert(/timeScale: \{ fixLeftEdge: true, fixRightEdge: true \}/.test(body), '邊界鎖在資料兩端，拖不出空白');

// MACD 從第一根起算（EMA 以第一根的值當起點）
const ema = js.slice(js.indexOf('function ema('), js.indexOf('function computeMacd('));
assert(/prev = prev == null \? v : v \* k \+ prev \* \(1 - k\);/.test(ema));
assert(!/sum \/ n/.test(ema), '不再用前 n 根簡單平均起算');

console.log('PASS: v42 charts always fill the width with wheel zoom (no right-aligned blank mode), MACD from the first bar, taller panes (520/220/220/220, mobile 420/180/180).');
