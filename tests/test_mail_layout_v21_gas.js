// 信件與網站版面（2026/09/15）：標題不加「張震：」、疊列表格不必左右滑動且各表欄寬一致、章節圓角卡片、
// 每日總覽長名稱換行、郵件查詢解除舊表格規則。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..'), ctx = vm.createContext({ console });
for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs'])
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', name), 'utf8'), ctx);
ctx.DISCLAIMER = '免責'; ctx.unsubscribeUrl_ = () => 'https://example.com/u';

const article = [
  '① 文章標題：張震：大盤在46188到46620狹幅區間震盪！', '',
  '② 基本資訊', '', '• 節目名稱：張震 股市盤中家教班', '',
  '④ 會員操作紀錄與持股明細', '', '④-1 當日明確說明之買入／賣出紀錄', '',
  '| 股票名稱 | 股票代號 | 方向 | 價位區間／成本說明 | 張震口頭說明與操作理由 |', '|---|---|---|---|---|',
  '| 晶心科 | 6533 | 賣出 | 271以上 | 獲利賣出。 |', '',
  '④-2 影片中明講之「會員目前持有股票」', '',
  '| 股票名稱 | 股票代號 | 張震在本集節目中的說明重點 |', '|---|---|---|',
  '| 鴻準 | 2354 | 續抱觀察。 |', '',
  '④-3 觀望個股（當日未執行買賣）', '', '觀望不碰', '',
  '| 股票名稱 | 股票代號 | 價位說明 | 張震口頭說明重點 |', '|---|---|---|---|',
  '| 主動統一台股增長 | 00981A | 未說明 | 等待賣壓消化。 |', '',
  '觀望注意', '',
  '| 股票名稱 | 股票代號 | 價位說明 | 張震口頭說明重點 |', '|---|---|---|---|',
  '| 裕隆 | 2201 | 30 | 長線存股。 |'
].join('\n');
const html = ctx.mdToHtml_(article);

// 標題卡：不加「張震：」前綴（舊文章裡的前綴在呈現時拿掉）
assert(/class="mc-hero"[\s\S]*<h1[^>]*>大盤在46188到46620狹幅區間震盪！<\/h1>/.test(html), '標題卡');
assert(!/<h1[^>]*>張震/.test(html));
// 疊列表格：不再包橫向捲動容器；四張表同一組欄寬；說明另起一列橫跨三欄
assert(!html.includes('mail-table-scroll'), '手機不必左右滑動');
const tables = html.match(/<table class="mc-table"[\s\S]*?<\/table>/g);
assert.equal(tables.length, 4);
tables.forEach(t => assert(t.includes('<colgroup><col style="width:36%"><col style="width:22%"><col style="width:42%"></colgroup>'), '欄寬一致'));
assert.equal((html.match(/<td colspan="3" bgcolor="[^"]*" class="[^"]*mc-desc/g) || []).length, 4);
// 買賣表：方向做成標籤、價位不拆行；持股表第三格是「持有」；觀望表價位格不換行
assert(/mc-chip tn-sell[^>]*>賣出<\/span> <span style="white-space:nowrap;">271以上<\/span>/.test(tables[0]));
assert(/>狀態<\/th>/.test(tables[1]) && /mc-chip tn-hold[^>]*>持有</.test(tables[1]));
assert(/<td[^>]*white-space:nowrap[^>]*>未說明<\/td>/.test(tables[2]));
assert(/overflow-wrap:anywhere;">主動統一台股增長<\/td>/.test(tables[2]), '長名稱可換行');
// 類別標籤與章節卡片（v22 起舊文章的「基本資訊」呈現時拿掉，會員操作紀錄換成 ②）
assert(/class="mc-tone"[\s\S]*觀望不碰[\s\S]*語氣偏空，暫不進場/.test(html));
assert.equal((html.match(/class="mc-sec"/g) || []).length, 1);
assert(/<h2 class="mc-h2"[^>]*>.*② 會員操作紀錄與持股明細<\/h2>/.test(html) && !html.includes('節目名稱'));
assert(/<b style="color:#12161A;">觀念一<\/b>：/.test(ctx.mdToHtml_('③ 分析師操作邏輯與教學重點\n\n• 觀念一：等拉回')));
// 外框：內容區最寬 680px、內文 14px；信尾有取消訂閱按鈕
const mail = ctx.wrapMail_(html, 'a@b.c', 't');
assert(mail.includes('class="mail-shell"') && mail.includes('max-width:680px') && /class="mail-col"[^>]*font-size:14px/.test(mail));
assert(/取消訂閱<\/a>/.test(mail));
// HTML 跳脫仍有效
assert(!ctx.mdToHtml_('| 股票名稱 | 代號 | 價位 | 說明 |\n|---|---|---|---|\n| <b>x</b> | 1 | <900 | y |').includes('<b>x</b>'));
// 關注股票異動提醒已在 v35 整個拿掉（見 test_mail_v35_gas.js），每日推播只剩一種信
const svc = fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8');
assert(!svc.includes('關注股票異動提醒'));
// 網站：每日總覽名稱欄可換行；郵件查詢解除舊版 td:first-child 規則
const css = fs.readFileSync(path.join(root, 'apps-script/Stylesheet.html'), 'utf8');
assert(/\.tbl-daily td\.sticky \{ white-space: normal; overflow-wrap: anywhere;/.test(css));
assert(/\.mail-content \.mail-shell td \{ white-space: normal; position: static; min-width: 0; \}/.test(css));
assert(/\.mail-content table\.mc-table \{ min-width: 0 !important;/.test(css));
console.log('PASS: v21 mail layout — no title prefix, stacked aligned tables without horizontal scroll, section cards, daily name wrap and site overrides.');
