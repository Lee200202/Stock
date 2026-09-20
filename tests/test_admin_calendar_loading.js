// 後台日曆讀取中（2026/09/15）：日期資料到之前整月灰字、不能點（與前台一致），資料到了才依規則可點。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const admin = fs.readFileSync(path.join(root, 'apps-script/Admin.html'), 'utf8');
const start = admin.indexOf('function makeAdminCal(opts) {');
const end = admin.slice(start).search(/\r?\n\}\r?\n/);
assert(start > 0 && end > 0, '找得到 makeAdminCal');
const src = admin.slice(start, start + end) + '\n}\n';

function makeDom() {
  const els = {};
  const el = id => els[id] || (els[id] = {
    id, innerHTML: '', textContent: '', attrs: {}, onclick: null,
    setAttribute(k, v) { this.attrs[k] = String(v); }, removeAttribute(k) { delete this.attrs[k]; },
    querySelectorAll(sel) {
      assert.equal(sel, '.cal-c.pickable');
      // 同一次渲染回同一組按鈕，才看得到日曆綁上去的點擊。
      if (this._html !== this.innerHTML) {
        this._html = this.innerHTML;
        this._list = (this.innerHTML.match(/<button[^>]*class="[^"]*\bpickable\b[^"]*"[^>]*>/g) || []).map(tag => ({
          dataset: { date: (tag.match(/data-date="([^"]+)"/) || [])[1] }, handlers: [],
          addEventListener(t, fn) { this.handlers.push(fn); } }));
      }
      return this._list;
    }
  });
  return { els, el };
}
function env(clockIso) {
  const dom = makeDom();
  const now = Date.parse(clockIso);
  class FDate extends Date { constructor(...a) { a.length ? super(...a) : super(now); } static now() { return now; } }
  const ctx = vm.createContext({ Date: FDate, document: { getElementById: dom.el },
    esc: s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])) });
  vm.runInContext(src, ctx);
  return { ctx, dom };
}
const buttons = html => html.match(/<button[^>]*>/g) || [];
const ids = { grid: 'g', title: 't', prev: 'p', next: 'n' };

// 一、剛建立（資料還沒回來）：整月 disabled、沒有 data-date、沒有可點的格子；今天虛線框；aria-busy
let { ctx, dom } = env('2026-09-15T04:00:00+08:00');
let picked = [];
let cal = ctx.makeAdminCal(Object.assign({ onPick: d => picked.push(d) }, ids));
let html = dom.el('g').innerHTML;
assert.equal(dom.el('t').textContent, '2026 年 9 月');
assert.equal(buttons(html).length, 30);
assert(buttons(html).every(b => / disabled>$/.test(b) && /\bnone loading\b/.test(b) && !/data-date|pickable/.test(b)), '讀取中每一格都不能點');
assert.equal(buttons(html).filter(b => /\btoday\b/.test(b)).length, 1);
assert(/<button[^>]*today[^>]*><span class="cal-n">15</.test(html));
assert.equal(dom.el('g').querySelectorAll('.cal-c.pickable').length, 0);
assert.equal(dom.el('g').attrs['aria-busy'], 'true');
assert.equal(cal.ready(), false);

// 讀取中換月：仍然全部不能點
dom.el('p').onclick();
assert.equal(dom.el('t').textContent, '2026 年 8 月');
assert(buttons(dom.el('g').innerHTML).every(b => / disabled>$/.test(b)));
// 讀取中先 select：不顯示選取，也不能點
cal.select('2026/09/10');
assert(!/\bsel\b/.test(dom.el('g').innerHTML));

// 二、資料到了：翻回今天的月份；今天以前可點、未來不能點；loading 樣式消失
cal.setData(['2026/09/10', '2026/09/11'], '2026/09/15');
html = dom.el('g').innerHTML;
assert.equal(dom.el('t').textContent, '2026 年 9 月');
assert.equal(cal.ready(), true); assert.equal(dom.el('g').attrs['aria-busy'], 'false');
assert(!/\bloading\b/.test(html));
assert.equal(buttons(html).filter(b => /pickable/.test(b)).length, 15);
assert(/data-date="2026\/09\/15"/.test(html) && !/data-date="2026\/09\/16"/.test(html));
assert(/class="cal-c day has pickable sel"[^>]* data-date="2026\/09\/10"/.test(html), '讀取中選的日子，資料到了才顯示選取');
const btn = dom.el('g').querySelectorAll('.cal-c.pickable').find(b => b.dataset.date === '2026/09/11');
btn.handlers[0]();
assert.deepEqual(picked, ['2026/09/11']);

// 三、只能選有資料的日子（資料修正、會員簡訊篩選）：讀取中一樣全擋，資料到了只剩有紀錄的
({ ctx, dom } = env('2026-09-15T04:00:00+08:00'));
cal = ctx.makeAdminCal(Object.assign({ onlyWithData: true }, ids));
assert.equal(dom.el('g').querySelectorAll('.cal-c.pickable').length, 0);
cal.setData(['2026/09/10'], '2026/09/15');
assert.deepEqual(dom.el('g').querySelectorAll('.cal-c.pickable').map(b => b.dataset.date), ['2026/09/10']);

// 四、讀取失敗：顯示原因，換月後訊息仍在（不退回灰色月曆）；重建日曆時換月按鈕只綁最新一份
({ ctx, dom } = env('2026-09-15T04:00:00+08:00'));
cal = ctx.makeAdminCal(ids);
cal.fail('連線失敗，切走再切回來可重試。');
assert(/連線失敗/.test(dom.el('g').innerHTML) && !/<button/.test(dom.el('g').innerHTML));
dom.el('n').onclick();
assert(/連線失敗/.test(dom.el('g').innerHTML));
const first = dom.el('n').onclick;
const cal2 = ctx.makeAdminCal(ids);
assert.notEqual(dom.el('n').onclick, first, '換月按鈕被新的一份取代，不疊兩份');
cal2.setData(['2026/09/10'], '2026/09/15');
dom.el('n').onclick();
assert.equal(dom.el('t').textContent, '2026 年 10 月', '只換一個月');

// 五、樣式：讀取中的格子不吃後台 disabled 的 .4 透明度；會員簡訊日曆讀取失敗也會顯示原因
assert(/\.panel \.mailcal button\.cal-c\.loading:disabled \{ opacity: 1; cursor: default; \}/.test(admin));
assert(/smsSinceCal\.fail\('連線失敗，切走再切回來可重試。'\); smsFilterCal\.fail\(/.test(admin));
assert(!/addEventListener\('click', function \(\) \{ shift\(/.test(src));
console.log('PASS: admin calendars stay disabled while dates load, match front loading look, and enable by rule once data arrives.');
