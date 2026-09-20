// 文章章節（2026/09/15 v22）：標題不編號、拿掉基本資訊、盤勢總覽起算 ①；舊文章呈現時照章名換號。
// 郵件查詢：每一個寫死 px 的字都有對應的 CSS，乘上版面設定的字體大小（字距、行距同步）。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..'), ctx = vm.createContext({ console });
for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs'])
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', name), 'utf8'), ctx);

const d = '2026/09/15';
const s = {
  market: [
    { kind: 'level', text: '大盤在46188到46620之間震盪，量縮震盪，市場靜待CPI公布再決定方向。', headline: '量縮震盪，市場靜待CPI公布再決定方向', _evidence_verified: true },
    { kind: 'view', text: '等拉回再買：追高容易套牢，已有部位續抱。', _evidence_verified: true }],
  buy: [{ name: '晶心科', code: '6533', price: '271以上', reason: '分批買進。' }],
  sell: [],
  holdings: [{ name: '鴻準', code: '2354', note: '續抱觀察。' }],
  watch_avoid: [{ name: '勤誠', code: '8210', price: '', reason: '還不能買，等賣壓消化。' }],
  watch_watch: [{ name: '裕隆', code: '2201', price: '30', reason: '長線存股。' }],
  _past: [{ _date: '2026/09/12', name: '華城', reason: '前一交易日賣出。' }]
};

// 一、GAS 產生的結構：標題一行、①②（②-1～②-3）③，沒有基本資訊與 ⑤⑥；v44 起也沒有 ④ 風險揭露（改在信尾）
const article = ctx.enforceArticleRecords_('① 文章標題：舊\n② 基本資訊\n• 節目名稱：x\n⑥ 舊', s, d);
const heads = article.split('\n').filter(l => /^(文章標題：|[①②③④⑤⑥])/.test(l))
  .map(l => l.startsWith('文章標題：') ? '文章標題：' : l.split(' ')[0]);
assert.deepEqual(heads, ['文章標題：', '①', '②', '②-1', '②-2', '②-3', '③']);
assert(article.startsWith('文章標題：量縮震盪，市場靜待CPI公布再決定方向！'));
assert(!/基本資訊|節目名稱|播出平台|主要講者|④|⑤|⑥|風險揭露/.test(article));
assert(article.indexOf('補記（記在其他日期') < article.indexOf('②-2'), '補記放在 ②-2 之前');

// 二、呈現：標題卡不帶編號與「文章標題」字樣；三張章節卡依序 ①～③；子節 ②-1～②-3
const text = h => h.replace(/<[^>]+>/g, '').trim();
const html = ctx.mdToHtml_(article);
const h1 = html.match(/<h1[^>]*>([\s\S]*?)<\/h1>/);
assert.equal(text(h1[1]), '量縮震盪，市場靜待CPI公布再決定方向！');
assert(!html.includes('文章標題'));
assert.deepEqual((html.match(/<h2 class="mc-h2"[\s\S]*?<\/h2>/g) || []).map(text),
  ['① 盤勢總覽重點整理', '② 會員操作紀錄與持股明細', '③ 分析師操作邏輯與教學重點']);
assert.deepEqual((html.match(/<h3 class="mc-h3"[\s\S]*?<\/h3>/g) || []).map(h => text(h).slice(0, 3)), ['②-1', '②-2', '②-3']);
assert.equal((html.match(/class="mc-sec"/g) || []).length, 3);

// 三、試算表裡的舊六章文章：呈現結果與新結構逐字相同（基本資訊、風險揭露整章拿掉，含它底下的內容）
const RISK = '\n\n④ 風險揭露與重要提醒\n\n• 本文章內容僅為整理節目中之公開資訊與觀點，不構成任何形式之投資建議或獲利保證。\n• 實際投資操作須自行評估風險與財務狀況，必要時請諮詢專業投資顧問。';
assert.equal(ctx.mdToHtml_(article + RISK), html, 'v22～v43 的四章文章：④ 風險揭露呈現時拿掉');
const legacy = (article + RISK)
  .replace(/^④ 風險揭露/m, '⑥ 風險揭露').replace(/^③ 分析師/m, '⑤ 分析師')
  .replace(/^② 會員操作紀錄/m, '④ 會員操作紀錄').replace(/^②-/gm, '④-')
  .replace(/^① 盤勢總覽/m, '③ 盤勢總覽')
  .replace(/^文章標題：([^\n]*)/, '① 文章標題：張震：$1\n\n② 基本資訊\n\n• 節目名稱：張震 股市盤中家教班\n• 播出日期：' + d +
    '\n\n| 欄位 | 內容 |\n|---|---|\n| 主要講者 | 張震 |');
assert(legacy.includes('④-1') && legacy.includes('⑥ 風險揭露') && legacy.includes('② 基本資訊'));
assert.equal(ctx.mdToHtml_(legacy), html, '舊文章換號後與新文章呈現一致');

// 四、模型版舊文章：「## ① 文章標題」空章名＋下一行標題、粗體或 # 章名、基本資訊裡有節目簡述
const aiLegacy = ['## ① 文章標題', '文章標題：換手太明顯！', '', '**② 基本資訊**', '節目簡述：本集談量縮。', '',
  '### ③ 盤勢總覽重點整理', '• 量縮。', '', '④ 會員操作紀錄與持股明細', '④-1 當日明確說明之買入／賣出紀錄', '本支影片未說明當日具體買賣紀錄。'].join('\n');
const aiHtml = ctx.mdToHtml_(aiLegacy);
assert.equal((aiHtml.match(/class="mc-hero"/g) || []).length, 1);
assert(!/文章標題|節目簡述|基本資訊/.test(aiHtml));
assert.deepEqual((aiHtml.match(/<h2 class="mc-h2"[\s\S]*?<\/h2>/g) || []).map(text), ['① 盤勢總覽重點整理', '② 會員操作紀錄與持股明細']);
assert(/<h3 class="mc-h3"[^>]*>②-1 當日/.test(aiHtml));

// 五、不認得的章名不換號；沒有所屬章標題的子節不亂換
assert(/<h2 class="mc-h2"[\s\S]*① 關注股票異動提醒<\/h2>/.test(ctx.mdToHtml_('① 關注股票異動提醒\n下列股票')));
assert(/>④-1 孤兒子節<\/h3>/.test(ctx.mdToHtml_('④-1 孤兒子節')));
assert.equal(ctx.normalizeArticleSections_('③ 盤勢總覽重點整理\n③-1 不是這章的子節'), '① 盤勢總覽重點整理\n①-1 不是這章的子節');
assert.equal(ctx.normalizeArticleSections_('⑤ 分析師操作邏輯與教學重點\n④-1 前一章的子節'), '③ 分析師操作邏輯與教學重點\n④-1 前一章的子節');

// 六、網站版面設定：信件 HTML 裡每個寫死 px 字級的元素，Stylesheet 都有同 px × --rd-fs 的 !important 規則
const css = fs.readFileSync(path.join(root, 'apps-script/Stylesheet.html'), 'utf8');
const rich = ctx.mdToHtml_(article + '\n\n## 其他標題\n\n#### 小標\n\n一般段落。');
const cssPx = sel => {
  const esc = sel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(esc + '(?![\\w-])[^{}]*\\{[^}]*font-size:\\s*calc\\(([\\d.]+)px \\* var\\(--rd-fs\\)\\)\\s*!important', 'g');
  const out = []; let m; while ((m = re.exec(css))) { out.push(Number(m[1])); } return out;
};
const known = ['mc-hero-k', 'mc-h2', 'mc-h3', 'mc-p', 'mail-bullet', 'mc-th', 'mc-name', 'mc-code', 'mc-mid', 'mc-desc', 'mc-chip', 'mc-tone-k', 'mc-table'];
let checked = 0;
(rich.match(/<\w+\b[^>]*>/g) || []).forEach(tag => {
  const fs_ = tag.match(/style="[^"]*font-size:([\d.]+)px/);
  if (!fs_) { return; }
  const cls = ((tag.match(/class="([^"]*)"/) || [])[1] || '').split(/\s+/);
  const key = /^<h1\b/.test(tag) ? '.mc-hero h1' : '.' + (known.find(k => cls.includes(k)) || '');
  assert(key !== '.', '沒有對應 class 的固定字級：' + tag.slice(0, 120));
  assert(cssPx(key).includes(Number(fs_[1])), key + ' 缺少 calc(' + fs_[1] + 'px * var(--rd-fs)) 規則');
  checked++;
});
assert(checked > 30, '實際檢查了表格、標籤、條列與標題');
// 股票名稱、代號、狀態（持有標籤）都在檢查範圍內
assert(/class="tn-[a-z]+ mc-name"/.test(rich) && /class="tn-[a-z]+ mc-code"/.test(rich) && /mc-chip tn-hold/.test(rich));
// 字距、行距也跟著設定
['mc-name', 'mc-code', 'mc-mid', 'mc-desc', 'mc-th', 'mc-h2', 'mc-h3'].forEach(k =>
  assert(new RegExp('\\.' + k + '(?![\\w-])[^{}]*\\{[^}]*letter-spacing:\\s*var\\(--rd-ls\\)\\s*!important').test(css), k + ' 字距'));
['mc-desc', 'mail-bullet'].forEach(k =>
  assert(new RegExp('\\.' + k + '(?![\\w-])[^{}]*\\{[^}]*line-height:\\s*var\\(--rd-lh\\)\\s*!important').test(css), k + ' 行距'));
// 信件本身（收件匣）仍是 inline px，標準字級與網站相同
assert(/mc-name" style="[^"]*font-size:15px/.test(rich));
console.log('PASS: v22 article sections (untitled heading, no basic info, ①–③ with legacy renumbering, v44 no risk chapter) and mail text following site reading settings.');
