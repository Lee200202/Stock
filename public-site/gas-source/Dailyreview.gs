/* 每日寄信前複核（2026/11/02，管理者核定）：雲端排程與 Apps Script 透過「複核紀錄」試算表溝通，不用管理密鑰。
   - 排程開始複核時把「每日摘要」今天那一列設成「複核中」；寄信與 LINE 每日總覽看到就等，最晚 13:00 照原內容寄出。
   - 排程把日報寫進「通知」分頁（狀態「待寄」、預定寄送時間）；這裡每 5 分鐘把到時間的用管理者通知寄出。
   試算表由 setupDailyReviewLog() 在擁有者的雲端硬碟建立並共用給服務帳戶；只需在編輯器執行一次。
   說明書：docs/daily-review/每日寄信前複核排程說明書.md、作業步驟：docs/daily-review/每日複核作業.md */

var DAILY_REVIEW_TABS_ = {
  '每日摘要': ['日期', '狀態', '開始時間', '完成時間', '主流程狀態', '郵件狀態', '比對檔數', '明確錯誤', '待確認',
              '邊界', '複核錯', '已修正', '判讀雜湊', '執行', '備註'],
  '差異明細': ['日期', '代號', '名稱', '網站分類', '複核分類', '可接受集合', '差異類型', '嚴重度', '原文依據',
              '判斷理由', '處理', '修正前', '修正後', '執行', '是否轉成規則或測試'],
  '通知': ['建立時間', '日期', '類型', '預定寄送', '主旨', '內容', '狀態', '寄出時間'],
  '提示詞候選': ['日期', '差異類型', '近兩週次數', '反例原句', '建議修改', '要新增的測試', '狀態']
};
var DAILY_REVIEW_HOLD_UNTIL_ = 1300;   // 複核中最晚讓郵件等到這個時刻

/** 只需執行一次：建立「盤勢有據_每日複核紀錄」，共用給主資料表裡的服務帳戶，記下試算表 ID。已建立就只補分頁與共用。 */
function setupDailyReviewLog() {
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('REVIEW_LOG_ID');
  var book = id ? SpreadsheetApp.openById(id) : SpreadsheetApp.create('盤勢有據_每日複核紀錄');
  Object.keys(DAILY_REVIEW_TABS_).forEach(function (name, i) {
    var sh = book.getSheetByName(name);
    if (!sh) { sh = (i === 0 && book.getSheets().length === 1 && book.getSheets()[0].getLastRow() === 0)
      ? book.getSheets()[0].setName(name) : book.insertSheet(name); }
    if (sh.getLastRow() === 0) {
      var head = DAILY_REVIEW_TABS_[name];
      sh.getRange(1, 1, 1, head.length).setValues([head]).setFontWeight('bold').setBackground('#DDE7F0');
      sh.setFrozenRows(1);
    }
  });
  // 服務帳戶：主資料試算表的編輯者裡結尾是 gserviceaccount.com 的那幾個
  var accounts = getSS_().getEditors().map(function (u) { return u.getEmail(); })
    .filter(function (e) { return /gserviceaccount\.com$/.test(e); });
  accounts.forEach(function (e) { book.addEditor(e); });
  props.setProperty('REVIEW_LOG_ID', book.getId());
  if (!props.getProperty('REVIEW_NOTIFY_TO')) { props.setProperty('REVIEW_NOTIFY_TO', 'rainforecast2026@gmail.com'); }
  var out = { id: book.getId(), url: book.getUrl(), sharedWith: accounts, notifyTo: dailyReviewNotifyTo_() };
  Logger.log(JSON.stringify(out));
  return out;
}

/** 五分鐘排程呼叫：還沒有 REVIEW_LOG_ID 時建立一次，並把試算表連結與 ID 寄給管理者（雲端排程要設成環境變數）。 */
function dailyReviewEnsureLog_() {
  if (PropertiesService.getScriptProperties().getProperty('REVIEW_LOG_ID')) { return; }
  var out = setupDailyReviewLog();
  var body = ['每日寄信前複核的紀錄試算表已建立在你的雲端硬碟。', '',
    '連結：' + out.url,
    '試算表 ID（雲端排程環境變數 REVIEW_LOG_ID）：' + out.id,
    '已共用給服務帳戶：' + (out.sharedWith.join('、') || '（找不到，請手動共用）'),
    '日報收件人：' + out.notifyTo.join('、')].join('\n');
  if (typeof notifyAdmin_ === 'function') { notifyAdmin_('每日複核紀錄表已建立', body); }
  var extra = (out.notifyTo || []).filter(function (e) { return (typeof statusEmails_ === 'function' ? statusEmails_() : []).indexOf(e) < 0; });
  if (extra.length) { MailApp.sendEmail({ to: extra.join(','), subject: '每日複核紀錄表已建立', body: body }); }
}

function dailyReviewBook_() {
  var id = PropertiesService.getScriptProperties().getProperty('REVIEW_LOG_ID');
  return id ? SpreadsheetApp.openById(id) : null;
}

/** 日報收件人：REVIEW_NOTIFY_TO（逗號分隔）加上管理者狀態信箱，不重複。 */
function dailyReviewNotifyTo_() {
  var extra = String(PropertiesService.getScriptProperties().getProperty('REVIEW_NOTIFY_TO') || '')
    .split(',').map(function (s) { return s.trim(); }).filter(String);
  var base = typeof statusEmails_ === 'function' ? statusEmails_() : [];
  return base.concat(extra).filter(function (e, i, all) { return all.indexOf(e) === i; });
}

/** 今天在「複核中」且還沒到 13:00：回傳暫緩原因；否則回空字串。讀取結果快取 60 秒，讀不到紀錄表就不擋。 */
function dailyReviewHold_(today) {
  var hhmm = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'HHmm'));
  if (hhmm >= DAILY_REVIEW_HOLD_UNTIL_) { return ''; }
  var cache = CacheService.getScriptCache(), key = 'review_hold_' + String(today).replace(/\D/g, '');
  var cached = cache.get(key);
  if (cached !== null) { return cached === '-' ? '' : cached; }
  var why = '';
  try {
    var book = dailyReviewBook_();
    var sh = book && book.getSheetByName('每日摘要');
    if (sh && sh.getLastRow() > 1) {
      var rows = sh.getRange(2, 1, sh.getLastRow() - 1, 2).getDisplayValues();
      for (var i = rows.length - 1; i >= 0; i--) {
        if (String(rows[i][0]).trim().replace(/-/g, '/') === today && String(rows[i][1]).trim() === '複核中') {
          why = '每日寄信前複核進行中（最晚 13:00 照原內容寄出）'; break;
        }
      }
    }
  } catch (e) { Logger.log('複核紀錄讀取失敗，不暫緩寄信：' + e); }
  cache.put(key, why || '-', 60);
  return why;
}

/** 每 5 分鐘：把「通知」分頁到時間的待寄日報用管理者通知寄出，標記已寄。 */
function dailyReviewNoticeTick_() {
  var book = dailyReviewBook_();
  var sh = book && book.getSheetByName('通知');
  if (!sh || sh.getLastRow() < 2) { return; }
  var to = dailyReviewNotifyTo_();
  if (!to.length) { return; }
  var now = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss');
  var head = DAILY_REVIEW_TABS_['通知'];
  var values = sh.getRange(2, 1, sh.getLastRow() - 1, head.length).getDisplayValues();
  values.forEach(function (r, i) {
    if (String(r[6]).trim() !== '待寄' || String(r[3]).trim() > now) { return; }
    var mail = { to: to.join(','), subject: String(r[4]), body: String(r[5]) };
    try { if (typeof adminNoticeHtml_ === 'function') { mail.htmlBody = adminNoticeHtml_(mail.subject, mail.body); } } catch (e) { delete mail.htmlBody; }
    MailApp.sendEmail(mail);
    sh.getRange(i + 2, 7, 1, 2).setValues([['已寄', now]]);
  });
}
