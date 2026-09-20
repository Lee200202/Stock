// 手機收件匣深色模式（2026/09/16）：代號那一格不再掉底色變成黑／白方塊；每一格底色與文字顏色都寫明。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..'), ctx = vm.createContext({ console });
for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs'])
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', name), 'utf8'), ctx);
ctx.DISCLAIMER = '免責'; ctx.unsubscribeUrl_ = () => 'https://example.com/u';

const signals = {
  market: [{ kind: 'level', text: '大盤在46188到46620之間震盪。', headline: '量縮震盪，靜待CPI', _evidence_verified: true },
           { kind: 'view', text: '等拉回再買：追高容易套牢。', _evidence_verified: true }],
  buy: [], sell: [{ name: '晶心科', code: '6533', price: '271以上', reason: '獲利賣出。' }],
  holdings: [{ name: '鴻準', code: '2354', note: '成本在238附近，籌碼安定，抱著等待後續表現。' },
             { name: '世芯-KY', code: '3661', note: '低點3410連續三天不破低，續抱看好後市。' }],
  watch_avoid: [{ name: '主動統一台股增長', code: '00981A', price: '', reason: '等待賣壓消化。' }],
  watch_watch: [{ name: '裕隆', code: '2201', price: '30', reason: '長線存股。' }]
};
const html = ctx.mdToHtml_(ctx.enforceArticleRecords_('', signals, '2026/09/16'));
const mail = ctx.wrapMail_(html, 'a@b.c', 't');

// 一、代號欄：不再有等寬字體堆疊（整封信唯一帶引號的字體宣告，也是唯一掉底色的那一格）
assert(!/SF Mono|IBM Plex Mono|monospace/.test(mail), '信件不再宣告等寬字體');
assert(!/MAIL_MONO_/.test(fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8')));
assert(/mc-code"[^>]*font-size:13\.5px;letter-spacing:0\.04em;color:#3E4944;white-space:nowrap;"/.test(html), '代號字級、字距與顏色寫明');
assert(/>2354</.test(html) && /<td[^>]*mc-code[^>]*>3661<\/td>/.test(html));

// 二、每一個資料格都同時有 bgcolor 屬性與 style 底色，且文字顏色寫明（深色模式才不會兩者各自反轉）
const cells = html.match(/<t[dh][^>]*class="tn-[^"]*"[^>]*>/g) || [];
assert(cells.length >= 12, '涵蓋表頭與各類表格的格子');
cells.forEach(tag => {
  const bgcolor = (tag.match(/bgcolor="(#[0-9A-Fa-f]{6})"/) || [])[1];
  const style = (tag.match(/style="([^"]*)"/) || [])[1] || '';
  const bg = (style.match(/background:(#[0-9A-Fa-f]{6})/) || [])[1];
  assert(bgcolor && bg && bgcolor.toUpperCase() === bg.toUpperCase(), 'bgcolor 與 style 底色一致：' + tag.slice(0, 90));
  assert(/(^|;)color:#[0-9A-Fa-f]{6}/.test(style), '文字顏色寫明：' + tag.slice(0, 90));
});
// 外框那一格也要寫明顏色
assert(/class="mail-col"[^>]*color:#12161A/.test(mail));

// 三、手機上的行距與段距：條列、段落之間留 9px，說明欄下緣 12px
assert(/class="mail-bullet" style="margin:9px 0;/.test(html));
assert(/class="mc-p" style="margin:9px 0;/.test(ctx.mdToHtml_('① 盤勢總覽重點整理\n\n一段說明。')));
assert(/mc-desc" style="background:#[0-9A-Fa-f]{6};padding:2px 12px 12px;/.test(html));

// 四、網站郵件查詢的字級規則跟著代號改到 13.5px（版面設定仍然生效）
const css = fs.readFileSync(path.join(root, 'apps-script/Stylesheet.html'), 'utf8');
assert(/td\.mc-code \{ font-size: calc\(13\.5px \* var\(--rd-fs\)\) !important;/.test(css));
assert(/td\.mc-desc \{ padding-top: 0 !important; padding-bottom: 12px !important; \}/.test(css));

// 五、字體名稱不能用雙引號：style="..." 會在第一個引號就結束，後面的 font-size、line-height、
//     color 全部失效（整封信的基準字體因此從來沒生效過），代號欄那一格的 style 也整段壞掉。
const svc = fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8');
assert(/var MAIL_FONT_ = "[^"]*';/.test(svc) === false, '字體常數格式');
assert(!/var MAIL_FONT_ =[^\n]*[^=]"[A-Za-z]/.test(svc.replace(/var MAIL_FONT_ = "/, 'var MAIL_FONT_ = X')), '字體堆疊裡不用雙引號');
const full = ctx.wrapMail_(html, 'a@b.c', 't');
(full.match(/style="[^"]*"/g) || []).forEach(attr => {
  assert(!/font-family:\s*(?:;|"$)/.test(attr), '字體宣告被截斷：' + attr.slice(0, 80));
});
assert(!/style="[^"]*font-family:"/.test(full), 'style 屬性沒有在字體名稱處提前結束');
assert(/font-family:[^";]*sans-serif;font-size:14px;line-height:1\.7;/.test(full), '外框的字體與字級一起生效');
assert(/<table class="mc-table"[^>]*font-family:[^";]*sans-serif;font-size:13\.5px/.test(full), '表格自己宣告字體（巢狀表格不一定繼承）');

// 六、網站深色主題：名稱與價位交還 --ink、代號用 --muted，不被信件的 inline 深色文字蓋住
assert(/td\.mc-name, \.mail-content table\.mc-table td\.mc-mid \{ color: var\(--ink\) !important; \}/.test(css));
assert(/td\.mc-code \{ color: var\(--muted\) !important; \}/.test(css));
console.log('PASS: mail cells keep their background and explicit colors in dark mode; stock codes no longer render as a black/white box.');
