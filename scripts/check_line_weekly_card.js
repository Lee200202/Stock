// 2026/11/14：每週檢討結果卡（LineAdmin.gs lineAdminWeeklyFlex_）。不連 LINE；檢查卡片結構符合 LINE Flex 的限制。
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const dir = path.join(__dirname, '..', 'public-site', 'gas-source');
const src = ['Line.gs', 'LineAdmin.gs'].map(f => fs.readFileSync(path.join(dir, f), 'utf8')).join('\n');
function grab(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0, end = src.indexOf('{', start);
  for (; end < src.length; end++) { if (src[end] === '{') depth++; else if (src[end] === '}' && --depth === 0) break; }
  return src.slice(start, end + 1);
}
const vars = ['LINE_C_', 'LINE_ADMIN_SCALE_', 'LINE_ADMIN_NOTICE_ICON_', 'LINE_ADMIN_NOTICE_COLOR_'].map(v => {
  const m = new RegExp('var ' + v + ' = \\{[\\s\\S]*?\\};').exec(src); assert(m, v); return m[0];
});
const ctx = vm.createContext({});
vm.runInContext(vars.join('\n') + '\n' + ['lineClip_', 'lineMd_', 'fxText_', 'fxBox_', 'fxBtn_', 'fxHeader_', 'fxFooter_', 'fxBubble_', 'fxNote_', 'fxBullet_',
  'lineAdminBubble_', 'lineAdminChip_', 'lineAdminLine_', 'lineAdminUri_', 'lineAdminStatusRow_', 'lineAdminWeeklyFlex_'].map(grab).join('\n'), ctx);
const card = {
  type: 'weekly', date: '2026/11/13', merged: false, headline: '重播遇到 Google 服務暫時故障，未合併', gate: '通過', offline: '通過',
  changes: ['講者明講引出下一檔時，本股說明的可引用範圍停在那裡', '紀錄上未出場部位——問卷缺答不算反對列持股'],
  days: [{ date: '2026/11/09', role: '本週', status: 'infra', note: 'Google 服務暫時故障，重跑後仍無法完成' },
         { date: '2026/11/10', role: '本週', status: 'pass', note: '通過' },
         { date: '2026/10/27', role: '保留日', status: 'fail', note: '新增不符：世芯-KY 說明帶內部字樣' }],
  requests: 361, live: { total: 39, ok: 38, problems: [{ date: '2026/11/09', name: '世芯-KY', code: '3661', type: '說明', why: '待核' }] },
  asks: [{ question: 'q', context: 'c', options: ['a', 'b'] }], reportUrl: 'https://github.com/Lee200202/Stock/blob/main/docs/weekly-review/2026-11-13/報告.md',
  runUrl: 'https://github.com/Lee200202/Stock/actions/runs/1'
};
const msg = JSON.parse(JSON.stringify(vm.runInContext('lineAdminWeeklyFlex_(' + JSON.stringify(card) + ')', ctx)));
assert.strictEqual(msg.type, 'flex');
assert(msg.altText.length > 0 && msg.altText.length <= 400);
assert.strictEqual(msg.contents.type, 'carousel');
assert(msg.contents.contents.length >= 3 && msg.contents.contents.length <= 12);
assert(Buffer.byteLength(JSON.stringify(msg.contents)) < 50000, 'carousel 要小於 50KB');
const allowedBox = new Set(['type', 'layout', 'contents', 'spacing', 'margin', 'paddingAll', 'paddingStart', 'paddingEnd', 'paddingTop', 'paddingBottom',
  'backgroundColor', 'cornerRadius', 'flex', 'borderWidth', 'borderColor', 'width', 'height', 'justifyContent', 'alignItems', 'action', 'offsetTop', 'position']);
const allowedText = new Set(['type', 'text', 'wrap', 'size', 'color', 'weight', 'align', 'gravity', 'flex', 'margin', 'maxLines', 'style', 'decoration', 'action']);
(function walk(n) {
  if (!n || typeof n !== 'object') return;
  if (Array.isArray(n)) { n.forEach(walk); return; }
  if (n.type === 'box') { Object.keys(n).forEach(k => assert(allowedBox.has(k), 'box 不支援的屬性：' + k)); assert(['vertical', 'horizontal', 'baseline'].includes(n.layout)); }
  if (n.type === 'text') { Object.keys(n).forEach(k => assert(allowedText.has(k), 'text 不支援的屬性：' + k)); assert(String(n.text).length > 0); }
  if (n.type === 'button') { assert(n.action && n.action.label && n.action.label.length <= 20, '按鈕文字 20 字內'); if (n.action.type === 'uri') assert(/^https:\/\//.test(n.action.uri)); }
  Object.values(n).forEach(walk);
})(msg.contents);
const text = JSON.stringify(msg);
assert(text.includes('⚠') && text.includes('✗') && text.includes('重播 1/3') && text.includes('有 1 件要你決定'));
assert(/out\.notice = lineAdminNoticeTick_\(\)/.test(src), '五分鐘排程要推通知');
console.log('每週檢討結果卡：輪播 ' + msg.contents.contents.length + ' 張、' + Buffer.byteLength(JSON.stringify(msg.contents)) + ' bytes，屬性與長度符合 LINE Flex 限制');
