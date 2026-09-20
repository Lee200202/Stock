// v44（2026/09/17）：信裡「④ 風險揭露與重要提醒」與信尾免責聲明重複（管理者回報截圖）。
// 現在只留一處：信尾「不想再收到這封信？」正上方；文章不再寫 ④，舊文章呈現時拿掉；網站郵件查詢接同一段。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const ctx = vm.createContext({ console });
for (const name of ['Presentationquality.gs', 'Evidencequality.gs', 'Articlequality.gs', 'MailService.gs'])
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script', name), 'utf8'), ctx);
ctx.DISCLAIMER = '本站內容僅整理影片中明確講述的內容，不構成任何投資建議，投資決策與盈虧由使用者自行負責。';
ctx.unsubscribeUrl_ = () => 'https://example.com/unsub';
const count = (s, needle) => s.split(needle).length - 1;

const oldArticle = [
  '文章標題：量縮震盪，市場靜待CPI公布再決定方向！', '',
  '① 盤勢總覽重點整理', '', '• 大盤量縮震盪。', '',
  '② 會員操作紀錄與持股明細', '', '②-1 當日明確說明之買入／賣出紀錄', '', '本支影片未說明當日具體買賣紀錄。', '',
  '③ 分析師操作邏輯與教學重點', '', '• 不要追高。', '',
  '④ 風險揭露與重要提醒', '',
  '• 本文章內容僅為整理節目中之公開資訊與觀點，不構成任何形式之投資建議或獲利保證。',
  '• 實際投資操作須自行評估風險與財務狀況，必要時請諮詢專業投資顧問。'
].join('\n');

// 一、信：風險揭露只出現一次，而且在「不想再收到這封信？」上面；原本那行免責聲明不再重複
const body = ctx.mdToHtml_(oldArticle);
assert(!body.includes('風險揭露'), '試算表裡的舊文章：④ 整章在呈現時拿掉');
assert(!body.includes('不構成任何形式之投資建議'), '連同底下的條列');
const mail = ctx.wrapMail_(body, 'a@b.c', 't');
assert.strictEqual(count(mail, '風險揭露與重要提醒'), 1);
assert.strictEqual(count(mail, '不構成任何形式之投資建議或獲利保證'), 1);
assert.strictEqual(count(mail, '必要時請諮詢專業投資顧問'), 1);
assert(mail.indexOf('風險揭露與重要提醒') < mail.indexOf('不想再收到這封信？'), '放在退訂說明正上方');
assert(mail.indexOf('風險揭露與重要提醒') > mail.indexOf('③ 分析師操作邏輯與教學重點'), '在文章之後');
assert(!mail.includes(ctx.DISCLAIMER), '信尾不再另外放一行意思相同的免責聲明');
assert(/class="mc-sec mc-foot"[\s\S]*mc-risk[\s\S]*不想再收到這封信？[\s\S]*取消訂閱/.test(mail), '同一張信尾卡片');

// 二、盤中即時通知、歡迎信等也走 wrapMail_，信尾同樣只有一次
const plain = ctx.wrapMail_('<p>內文</p>', 'a@b.c', 't');
assert.strictEqual(count(plain, '風險揭露與重要提醒'), 1);

// 三、網站郵件查詢（沒有信尾）：文章後面接同一段；寄測試信用 articleHtml，不會接兩次
ctx.fmtDate_ = x => x;
ctx.readSheetObjects_ = () => [{ '日期': '2026/09/17', '文字稿': oldArticle, '寄送狀態': '已寄送' }];
const content = ctx.getMailContent('2026/09/17');
assert.strictEqual(content.found, true);
assert.strictEqual(count(content.html, '風險揭露與重要提醒'), 1, '網站看得到一次');
assert.strictEqual(count(content.articleHtml, '風險揭露與重要提醒'), 0);
assert.strictEqual(count(ctx.wrapMail_(content.articleHtml, 'a@b.c', 't'), '風險揭露與重要提醒'), 1, '測試信也只有一次');
const src = fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8');
assert(/htmlBody: wrapMail_\(content\.articleHtml,/.test(src), '測試寄信用不含風險揭露的 articleHtml');

// 四、新產生的文章沒有 ④（Apps Script 重建與 pipeline 同一結構）
const rebuilt = ctx.enforceArticleRecords_('', { market: [], buy: [], sell: [], holdings: [], watch_avoid: [], watch_watch: [] }, '2026/09/17');
assert(!/④|風險揭露/.test(rebuilt));
console.log('PASS: v44 risk disclosure appears once, above the unsubscribe note; chapter ④ removed from new and stored articles; site view shows it once.');
