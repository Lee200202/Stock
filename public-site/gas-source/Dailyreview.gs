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
  '提示詞候選': ['日期', '差異類型', '近兩週次數', '反例原句', '建議修改', '要新增的測試', '狀態'],
  // 2026/11/08：LINE 管理者裁示（LineAdmin.gs 寫入；雲端排程「管理者裁示」與 line-ruling-apply.yml 更新狀態）
  '管理者裁示': ['建立時間', '裁示ID', '日期', '代號', '名稱', '裁示分類', '管理者說明', '來源', '管理者', '狀態',
              '分支', '結果', '更新時間', 'LINE回報']
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
        var st = String(rows[i][1]).trim();
        if (String(rows[i][0]).trim().replace(/-/g, '/') === today && (st === '複核中' || st === '修正中')) {
          why = st === '修正中' ? '複核修正套用中（最晚 13:00 照當時內容寄出）' : '每日寄信前複核進行中（最晚 13:00 照原內容寄出）';
          break;
        }
      }
    }
  } catch (e) { Logger.log('複核紀錄讀取失敗，不暫緩寄信：' + e); }
  cache.put(key, why || '-', 60);
  return why;
}

/** 管理者 2026/11/03：複核不再單獨寄信，一律在 12:30 起的「爬取狀態」信裡用卡片呈現（statusReportJob 呼叫
    dailyReviewStatusSection_）。保留這個名稱給五分鐘排程呼叫，但不寄任何信。 */
function dailyReviewNoticeTick_() {}

var DAILY_REVIEW_MAIL_AT_ = '12:30:00';
// 同一個色相由深到淺，深＝嚴重。
var DAILY_REVIEW_SEV_ = [
  { key: '明確錯誤', bg: '#0A4A3C', fg: '#FFFFFF' },
  { key: '待確認', bg: '#2F7A63', fg: '#FFFFFF' },
  { key: '邊界', bg: '#9CC8B4', fg: '#12161A' },
  { key: '複核錯', bg: '#DCEDE5', fg: '#12161A' }
];

/** 今天的複核卡片區塊（12:30 起才有）。回傳 { html, state, markRows }；沒有東西時回 null。讀不到紀錄表就不影響狀態信。 */
function dailyReviewStatusSection_(today) {
  var now = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss');
  if (now < today + ' ' + DAILY_REVIEW_MAIL_AT_) { return null; }
  var book;
  try { book = dailyReviewBook_(); } catch (e) { return null; }
  if (!book) { return null; }
  var read = function (name) {
    var sh = book.getSheetByName(name);
    if (!sh || sh.getLastRow() < 2) { return []; }
    var v = sh.getRange(1, 1, sh.getLastRow(), sh.getLastColumn()).getDisplayValues();
    return v.slice(1).map(function (r, i) {
      var o = { _row: i + 2 };
      v[0].forEach(function (h, j) { o[h] = r[j]; });
      return o;
    }).filter(function (o) { return String(o['日期']).trim().replace(/-/g, '/') === today; });
  };
  var summary = read('每日摘要').pop();
  if (!summary) { return null; }
  var rows = read('差異明細');
  var notices = read('通知').filter(function (o) { return String(o['狀態']).trim() === '待寄'; });
  var html = dailyReviewCardsHtml_(summary, rows, book.getUrl());
  return { html: html, state: [summary['狀態'], summary['已修正'], rows.length,
           rows.map(function (r) { return r['處理']; }).join(',')].join('/'),
           mark: function () {
             var sh = book.getSheetByName('通知');
             notices.forEach(function (o) { sh.getRange(o._row, 7, 1, 2).setValues([['已併入狀態信', now]]); });
           } };
}

/** 純函式（可離線測）：每日摘要一列＋差異明細當天各列 → 卡片 HTML。字少、卡片多：嚴重度數字、今日網站修改、明確錯誤、待你裁示。 */
function dailyReviewCardsHtml_(summary, rows, logUrl) {
  var e = typeof esc_ === 'function' ? esc_ : function (s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); };
  var cut = function (s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };
  var state = String(summary['狀態'] || '').trim();
  if (state === '未執行') {
    return '<p style="margin:4px 0 10px;font-size:16px;color:#26312C;">今天未複核：' + e(summary['備註'] || '沒有影片') + '</p>';
  }
  var count = function (k) { return Number(summary[k]) || 0; };
  // 二乘二（管理者 2026/11/03：四格排一列太擠）：上排明確錯誤、待確認，下排邊界、複核錯
  var tile = function (s) {
    return '<td width="50%" style="padding:5px;"><div style="background:' + s.bg + ';border-radius:16px;padding:14px 10px;text-align:center;">' +
      '<div style="font-size:32px;line-height:1.1;font-weight:700;color:' + s.fg + ';">' + count(s.key) + '</div>' +
      '<div style="font-size:14px;letter-spacing:.06em;color:' + s.fg + ';margin-top:4px;">' + s.key + '</div></div></td>';
  };
  var sev = DAILY_REVIEW_SEV_;
  var tiles = tile(sev[0]) + tile(sev[1]) + '</tr><tr>' + tile(sev[2]) + tile(sev[3]);
  var stateLabel = { '已完成': '已完成', '修正中': '修正套用中', '修正失敗': '修正套用失敗', '複核中': '複核進行中' }[state] || state;
  var html = '<p style="margin:0 0 8px;font-size:15px;color:#4A5A52;">比對 ' + e(summary['比對檔數'] || 0) + ' 檔・已修正 ' +
    '<b style="color:#0A4A3C;">' + count('已修正') + '</b> 項・' + e(stateLabel) + '</p>' +
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:0 0 12px;"><tr>' + tiles + '</tr></table>';

  var color = {};
  DAILY_REVIEW_SEV_.forEach(function (s) { color[s.key] = s.bg; });
  var card = function (title, chip, accent, lines) {
    return '<div style="border:1px solid #D9DFDA;border-left:5px solid ' + accent + ';border-radius:14px;background:#F6F8F6;padding:12px 14px;margin:0 0 10px;">' +
      '<div style="font-size:17px;font-weight:700;color:#12161A;">' + e(title) +
      (chip ? '<span style="display:inline-block;margin-left:8px;padding:1px 10px;border-radius:999px;background:' + accent +
        ';color:' + (accent === '#9CC8B4' || accent === '#DCEDE5' ? '#12161A' : '#FFFFFF') + ';font-size:12.5px;font-weight:600;vertical-align:2px;">' + e(chip) + '</span>' : '') +
      '</div>' +
      '<ul style="margin:6px 0 0;padding-left:20px;font-size:15px;line-height:1.7;color:#26312C;">' +
      lines.filter(function (l) { return l[1]; }).map(function (l) {
        return '<li><b style="color:#12161A;">' + e(l[0]) + '</b>' + e(l[1]) + '</li>';
      }).join('') + '</ul></div>';
  };
  var name = function (r) { return (r['名稱'] || '') + (r['代號'] ? ' ' + r['代號'] : ''); };
  var sub = function (title, inner) {
    return inner ? '<div style="font-size:15px;font-weight:700;color:#0A4A3C;letter-spacing:.08em;margin:14px 0 8px;">' + title + '</div>' + inner : '';
  };

  var fixed = rows.filter(function (r) { return r['處理'] === '已修正'; });
  html += sub('今日網站修改', fixed.map(function (r) {
    var cls = r['網站分類'] && r['複核分類'] && r['網站分類'] !== r['複核分類'] ? r['網站分類'] + ' → ' + r['複核分類'] : '';
    return card(name(r), r['差異類型'], '#0A4A3C', [['分類：', cls], ['改成：', cut(r['修正後'], 70)]]);
  }).join(''));

  var detail = function (r) {
    var some = function (x) { return !!x; };
    return [['網站：', cut([r['網站分類'], r['修正前']].filter(some).join('｜'), 70)],
            ['判斷：', cut([r['複核分類'], r['判斷理由']].filter(some).join('｜'), 90)],
            ['原句：', r['原文依據'] ? '「' + cut(r['原文依據'], 60) + '」' : ''],
            ['處理：', r['處理']]];
  };
  html += sub('明確錯誤', rows.filter(function (r) { return r['嚴重度'] === '明確錯誤'; }).map(function (r) {
    return card(name(r), r['差異類型'], color['明確錯誤'], detail(r));
  }).join(''));
  // 已經修正的待確認（例如管理者當天就裁示了）只出現在「今日網站修改」，不再列成待裁示
  html += sub('待你裁示', rows.filter(function (r) { return r['嚴重度'] === '待確認' && r['處理'] !== '已修正'; }).map(function (r) {
    return card(name(r), r['差異類型'], color['待確認'], detail(r));
  }).join(''));
  var minor = rows.filter(function (r) { return r['嚴重度'] === '邊界' || r['嚴重度'] === '複核錯'; });
  if (minor.length) {
    html += '<p style="margin:10px 0 4px;font-size:14.5px;color:#4A5A52;">邊界／複核錯（不需處理）：' +
      e(minor.map(function (r) { return r['名稱'] + '（' + r['嚴重度'] + '）'; }).join('、')) + '</p>';
  }
  if (!rows.length) {
    html += '<p style="margin:4px 0 8px;font-size:15.5px;color:#26312C;">網站內容與獨立判讀一致，今天沒有修改。</p>';
  }
  if (logUrl) {
    html += '<p style="margin:10px 0 4px;font-size:14px;"><a href="' + e(logUrl) + '" style="color:#04795C;">完整紀錄（每日複核紀錄試算表）</a></p>';
  }
  return html;
}
