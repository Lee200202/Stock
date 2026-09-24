/**
 * 檔案：MailService.gs
 * 對應規格書 4.5 節（訂閱、取消訂閱權杖）與二、1.4 節（失敗告警）。
 */

/**
 * 交易日判斷。不是交易日時，下游的推播、狀態信、失敗告警一律直接返回，
 * 不寄任何信、不做任何檢查。
 *
 * 兩種不開盤：
 *   週末　　　週六日本來就不開盤。
 *   國定假日　落在平日的休市日（春節、清明、端午、中秋、雙十…）。
 *             這一段原本沒有，於是休市的平日仍會照跑、照判「今日無影片」、
 *             照寄狀態信——那些信是假的，看久了會把真正的異常一起忽略掉。
 *             日期清單在 Holidays.gs，由 scripts/update_holidays.py
 *             從證交所資料產生，每年更新一次。
 */
function isTradingDayToday_() {
  return !whyClosed_(new Date());
}

/* ------------------------------------------------------------------ *
 * 訂閱與取消訂閱
 * ------------------------------------------------------------------ */

function createSubscription(payload) {
  var email = String(payload && payload.email || '').trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return { ok: false, message: '請填寫可收信的 Email。' };
  }

  var daily = !!(payload.daily);
  var sms = !!(payload.sms);
  // 「關注股票被明講賣出時提醒」已拿掉（2026/09/16）。欄位保留、一律寫空白，舊資料不必搬。
  var codes = [];
  if (!daily && !sms) {
    return { ok: false, message: '請至少選擇一項通知。' };
  }

  /* 訂閱項目存成逗號串起來的清單。既有的判斷都是用 indexOf 找字串
     （indexOf('每日總覽') >= 0），所以加一個項目不會動到原本的邏輯。 */
  var items = [];
  if (daily) { items.push('每日總覽'); }
  if (sms) { items.push('會員簡訊'); }
  var itemStr = items.join(',');

  var result = withLock_(function () {
    var sh = getSheet_('使用者訂閱清單');
    var rows = sh.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (String(rows[i][0]).toLowerCase() === email.toLowerCase()) {
        // 上方表單是「加訂」入口：只勾盤中通知時，不能把原有每日總覽退掉。
        // 要關閉某項，應到管理訂閱頁明確取消勾選。
        var oldItems = String(rows[i][1] || '');
        var effectiveDaily = daily || (oldItems.indexOf('每日總覽') >= 0 && String(rows[i][5]) !== '已取消');
        var effectiveSms = sms || (oldItems.indexOf('會員簡訊') >= 0 && String(rows[i][5]) !== '已取消');
        var merged = (effectiveDaily ? ['每日總覽'] : []).concat(effectiveSms ? ['會員簡訊'] : []).join(',');
        sh.getRange(i + 1, 2, 1, 2).setValues([[merged, '']]);
        sh.getRange(i + 1, 6).setValue('生效');
        return { ok: true, isNew: false, token: String(rows[i][4]), daily: effectiveDaily, sms: effectiveSms,
          message: '訂閱設定已更新，原有通知保留。' };
      }
    }
    var token = Utilities.getUuid().replace(/-/g, '');
    sh.appendRow([email, itemStr, codes.join(','), todayStr_(), token, '生效']);
    return { ok: true, isNew: true, token: token, daily: daily, sms: sms,
      message: '訂閱完成，確認信已寄出。' };
  });

  try {
    sendWelcomeMail_(email, result.token, result.daily, codes, result.isNew, result.sms);
  } catch (e) {
    // 訂閱本身已成功，確認信寄不出去不該讓整筆失敗
    return { ok: true, message: '訂閱已建立，但確認信寄送失敗，請確認信箱是否正確。' };
  }
  return { ok: true, message: result.message };
}

/* 訂閱相關信件的共用元件（2026/09/16 v36）。

   訂閱確認信與訂閱管理信原本是一整頁灰字清單加一顆黑框方形按鈕，
   和盤中即時通知、每日整理放在同一個收件匣裡，看起來像是另一個網站寄的。
   現在四種信同一套：品牌列 → 深綠標題卡 → 白底圓角卡片（綠色短條標題）→ 膠囊按鈕 → 信尾卡片。
   樣式數值與 Cmoney.gs 的 cmMailBody_、mdToHtml_ 的標題卡相同；改一邊要一起改。
   一律 inline style、屬性內不用雙引號、每段文字都寫明顏色（深色模式收件匣不會反白）。 */
function mailHero_(kicker, title, subHtml, kickerColor) {
  return '<div class="mc-hero" style="background:#17322A;border-radius:14px;padding:16px 18px;margin:0 0 12px;">' +
    '<div class="mc-hero-k" style="font-size:11px;letter-spacing:0.18em;font-weight:700;color:' +
      (kickerColor || '#9CC8B4') + ';">' + esc_(kicker) + '</div>' +
    '<h1 style="font-size:19px;line-height:1.5;margin:6px 0 0;font-weight:700;color:#FFFFFF;">' + esc_(title) + '</h1>' +
    (subHtml ? '<div style="font-size:12.5px;line-height:1.6;color:#9CC8B4;margin:2px 0 0;">' + subHtml + '</div>' : '') +
  '</div>';
}

function mailSection_(heading, innerHtml) {
  return '<div class="mc-sec" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;' +
      'padding:14px 12px 8px;margin:0 0 10px;">' +
    '<h2 class="mc-h2" style="font-size:16px;line-height:1.45;margin:0 0 10px;font-weight:700;color:#12161A;">' +
      '<span style="display:inline-block;width:4px;height:15px;border-radius:2px;background:#04795C;' +
        'margin-right:8px;vertical-align:-2px;"></span>' + esc_(heading) + '</h2>' +
    innerHtml +
  '</div>';
}

function mailPill_(href, text, primary) {
  return '<a href="' + escAttr_(href) + '" target="_blank" rel="noopener noreferrer" style="display:inline-block;margin:0 8px 8px 0;border-radius:999px;' +
    'padding:9px 18px;font-size:13.5px;font-weight:600;text-decoration:none;cursor:pointer;-webkit-text-size-adjust:none;touch-action:manipulation;' +
    (primary ? 'background:#04795C;color:#FFFFFF;border:1px solid #04795C;'
             : 'background:#FFFFFF;color:#12161A;border:1px solid #C3CBC6;') + '">' + esc_(text) + '</a>';
}

function mailBullet_(text) {
  return '<div class="mail-bullet" style="margin:9px 0;padding-left:14px;text-indent:-12px;font-size:14px;' +
    'line-height:1.75;color:#26312C;"><span style="color:#04795C;font-weight:700;">•</span> ' + esc_(text) + '</div>';
}

function mailNote_(text) {
  return '<p style="margin:6px 0 4px;font-size:12.5px;line-height:1.7;color:#667069;">' + esc_(text) + '</p>';
}

/* 兩種通知各一張小卡，已訂閱的是綠色條＋「已訂閱」，沒訂的是灰色條＋「未訂閱」。
   兩種都列出來：「設定已更新」時一眼看得出現在是哪幾項開著，不必回想當初勾了什麼。
   順序與網站的訂閱表單相同（每日總覽在前）。 */
var SUB_KINDS_ = [
  { key: 'daily', name: '每日總覽', when: '交易日 12:00 起',
    desc: '當天內容一備妥就寄出：影片中明講的買入、賣出、觀望不碰、觀望注意與會員持股整理。' },
  { key: 'sms', name: '盤中即時通知', when: '盤中，通常在開盤後到中午之間',
    desc: '他在盤中發給會員的操作簡訊一出現就轉寄給你。信裡放原文，不改寫也不摘要；結構化的股票與價位稍後會整理到網站的「會員通知」分頁。' }
];

function subscriptionItemsHtml_(on) {
  return SUB_KINDS_.map(function (k) {
    var yes = !!on[k.key];
    return '<div style="border-left:3px solid ' + (yes ? '#04795C' : '#C3CBC6') + ';background:' +
        (yes ? '#F3F6F4' : '#FFFFFF') + ';border-radius:0 10px 10px 0;padding:10px 14px 8px;margin:0 0 10px;">' +
      '<div style="font-size:15px;line-height:1.5;font-weight:700;color:' + (yes ? '#12161A' : '#667069') + ';">' +
        esc_(k.name) +
        '<span style="display:inline-block;margin-left:8px;border-radius:999px;padding:0 8px;font-size:11.5px;' +
          'line-height:20px;font-weight:700;vertical-align:1px;background:' + (yes ? '#BEE2CC' : '#E8ECEA') +
          ';color:' + (yes ? '#04583F' : '#5F6E67') + ';">' + (yes ? '已訂閱' : '未訂閱') + '</span></div>' +
      '<div style="font-size:12.5px;line-height:1.6;font-weight:600;color:' + (yes ? '#04795C' : '#8A958F') +
        ';margin:2px 0 4px;">' + esc_(k.when) + '</div>' +
      '<div style="font-size:13.5px;line-height:1.75;color:' + (yes ? '#26312C' : '#8A958F') + ';">' + esc_(k.desc) + '</div>' +
    '</div>';
  }).join('');
}

/** 信件裡「到網站管理訂閱」的網址：直接打開訂閱分頁。還沒有正式網址就不放按鈕。 */
function subscribePageUrl_() {
  var base = publicWebAppUrl_();
  return base ? base + '?tab=subscribe' : '';
}

/* 收件人信箱放在深色標題卡裡。Gmail 會自動把信箱變成藍色底線連結，壓在深綠底上幾乎看不見，
   所以自己包一個 mailto 連結並寫明顏色，收件匣就不會再套它的藍色。 */
function mailAddressHtml_(email) {
  return '<a href="mailto:' + escAttr_(email) + '" style="color:#9CC8B4;text-decoration:none;">' + esc_(email) + '</a>';
}

/** 訂閱確認信：訂了什麼、什麼時候收、怎麼調整或停掉。 */
function sendWelcomeMail_(email, token, daily, codes, isNew, sms) {
  var site = subscribePageUrl_();
  var body =
    mailHero_(isNew ? '訂閱完成' : '訂閱設定已更新', '接下來的信會寄到這個信箱', mailAddressHtml_(email)) +

    mailSection_('你訂閱的內容', subscriptionItemsHtml_({ daily: daily, sms: sms })) +

    mailSection_('關於這些信件',
      mailBullet_('內容只整理影片中明確講述的部分。影片沒提到的會標示「本支影片未說明」，不會用其他日期的資料回補，也不會推測。') +
      mailBullet_('沒有直播的日子不寄每日總覽；會員簡訊的盤中即時通知照常寄出。') +
      mailBullet_('這些是紀錄整理，不是投資建議。')) +

    mailSection_('想調整或停掉',
      '<p style="margin:0 0 12px;font-size:14px;line-height:1.75;color:#26312C;">' +
        '到網站的「訂閱通知」分頁，在「管理現有訂閱」填入這個 Email，就能逐項勾選要保留的內容。' +
        '再用訂閱表單勾選新的通知，會<b>加進</b>原本的訂閱，不會取消已訂的項目；要取消請用「管理現有訂閱」或信尾的取消訂閱。</p>' +
      (site ? mailPill_(site, '到網站管理訂閱', true) : '') +
      mailNote_('不想再收信，按最下面的「取消訂閱」，在確認頁選擇要停止的通知即可，不需要登入。'));

  MailApp.sendEmail({
    to: email,
    // 主旨與每日整理、盤中即時通知同一個格式
    subject: '張震股市盤中家教班　' + (isNew ? '訂閱完成' : '訂閱設定已更新'),
    htmlBody: wrapMail_(body, email, token),
    body: mailPlainText_(wrapMail_(body, email, token))
  });
}

/* 退訂（v54）。scope：'daily' 只停每日總覽、'sms' 只停盤中即時通知、其他＝停止所有通知。
   只停一類時另一類保留；兩類都停了才把狀態改成已取消。token 驗證不變，不需要登入。 */
var UNSUB_SCOPE_ITEM_ = { daily: '每日總覽', sms: '會員簡訊' };
var UNSUB_SCOPE_LABEL_ = { daily: '每日總覽', sms: '盤中即時通知' };
function unsubscribeByToken(email, token, scope) {
  email = String(email || '').trim();
  token = String(token || '').trim();
  if (!email || !token) { return { ok: false, state: 'invalid', message: '連結不完整，請直接從信件底部點擊。' }; }

  return withLock_(function () {
    var sh = getSheet_('使用者訂閱清單');
    var rows = sh.getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (String(rows[i][0]).toLowerCase() === email.toLowerCase() && String(rows[i][4]) === token) {
        if (String(rows[i][5]) === '已取消') {
          return { ok: true, state: 'already', message: '這個信箱先前已經取消訂閱，不會再收到信。' };
        }
        var item = UNSUB_SCOPE_ITEM_[scope];
        if (item) {
          var left = String(rows[i][1] || '').split(/[,，、]/).map(function (x) { return x.trim(); })
            .filter(function (x) { return x && x !== item; });
          if (left.length) {
            sh.getRange(i + 1, 2).setValue(left.join(','));
            return { ok: true, state: 'partial', message: '已停止「' + UNSUB_SCOPE_LABEL_[scope] + '」。' +
                     '其他通知照常寄送；想全部停止，可以再按一次信尾的取消訂閱。' };
          }
        }
        sh.getRange(i + 1, 6).setValue('已取消');
        return { ok: true, state: 'all', message: '已停止所有通知，不會再收到每日總覽與盤中即時通知。' };
      }
    }
    return { ok: false, state: 'invalid', message: '找不到這筆訂閱，或連結已失效（例如已經重新訂閱過）。可以到網站「訂閱通知」重新設定。' };
  });
}

/**
 * 寄送訂閱管理信。信裡列出目前訂了什麼，並附一個可以逐項勾選的管理連結。
 *
 * 刻意不做成「在網站上輸入 Email 就直接顯示或修改」。那樣任何人都能打別人的
 * 信箱，看到人家訂了什麼、甚至幫人家退訂。改成寄一封含連結的信到該信箱，
 * 點了才看得到內容，等於用「收得到那個信箱的信」當驗證。
 * 回覆訊息也刻意不透露該信箱有沒有訂閱過，避免變成查詢工具。
 */
function sendUnsubscribeLink(email) {
  email = String(email || '').trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return { ok: false, message: '請填寫正確的 Email 格式。' };
  }

  var hit = readSheetObjects_('使用者訂閱清單').filter(function (r) {
    return String(r['Email']).toLowerCase() === email.toLowerCase()
      && String(r['狀態']) !== '已取消';
  })[0];

  // 無論有沒有訂閱，回覆都一樣，不透露該信箱的狀態
  var reply = { ok: true, message: '若這個信箱有生效中的訂閱，管理頁的連結已經寄過去了。'
                + '點開就能逐項勾選要保留什麼，不必重填表單。' };
  if (!hit) { return reply; }

  var token = String(hit['取消訂閱權杖']);
  var itemsStr = String(hit['訂閱項目'] || '');
  var since = fmtDate_(hit['建立時間']);
  var site = subscribePageUrl_();

  // 版面與訂閱確認信、盤中即時通知同一套（見 mailHero_ 上面的說明）
  var body =
    mailHero_('訂閱管理', '你目前的訂閱', mailAddressHtml_(email) + (since ? '　建立於 ' + esc_(since) : '')) +

    mailSection_('訂閱內容', subscriptionItemsHtml_({
      daily: itemsStr.indexOf('每日總覽') >= 0,
      sms: itemsStr.indexOf('會員簡訊') >= 0
    })) +

    mailSection_('想調整內容',
      '<p style="margin:0 0 12px;font-size:14px;line-height:1.75;color:#26312C;">' +
        '到網站的「訂閱」分頁，在「管理現有訂閱」填入這個 Email，就會列出目前訂了什麼，' +
        '逐項勾選要保留的內容再儲存即可。</p>' +
      (site ? mailPill_(site, '到網站管理訂閱', true) : '') +
      mailNote_('若這不是你本人要求的，忽略這封信即可，訂閱不會有任何變動。不想再收信，按最下面的「取消訂閱」。'));

  MailApp.sendEmail({
    to: email,
    subject: '張震股市盤中家教班　你目前的訂閱',
    htmlBody: wrapMail_(body, email, token),
    body: mailPlainText_(wrapMail_(body, email, token))
  });
  return reply;
}

function activeSubscribers_() {
  return readSheetObjects_('使用者訂閱清單').filter(function (r) {
    return String(r['狀態']) !== '已取消' && String(r['Email']).indexOf('@') > 0;
  });
}

/* 網站對外的正式網址（/exec）。

   先前退訂連結直接用 ScriptApp.getService().getUrl()。那一支在網頁被訪客打開時回的是 /exec，
   但從編輯器或排程觸發器呼叫時回的是 /dev——而盤中即時通知正是排程在寄的。
   /dev 只有專案擁有者登入時開得起來，其他人點下去看到的是
   「很抱歉，目前無法開啟這個檔案。請檢查網址並再試一次。」（2026/09/16 管理者回報）。

   /dev 的網址用的是專案 ID、/exec 用的是部署 ID，兩者不能互換字尾。
   所以正式網址要記下來：任何訪客從 /exec 打開網站時 doGet 會順手存進指令碼屬性 WEBAPP_URL，
   排程寄信時讀那一份。管理者也可以手動設定。 */
/* 正式部署網址的格式（v54，Codex 規格 74）：https、script.google.com、/macros/s/<部署ID>/exec
   （Workspace 網域版是 /a/macros/<網域>/s/<部署ID>/exec）。只驗字尾 /exec 時，任意主機或貼錯的網址都會被當成正式網址。 */
var EXEC_URL_RE_ = /^https:\/\/script\.google\.com\/(?:a\/macros\/[A-Za-z0-9.-]+|macros)\/s\/[A-Za-z0-9_-]{10,}\/exec$/;
function isExecUrl_(u) { return EXEC_URL_RE_.test(String(u || '').trim()); }

function publicWebAppUrl_() {
  var saved = '';
  try { saved = String(PropertiesService.getScriptProperties().getProperty('WEBAPP_URL') || '').trim(); } catch (e) {}
  if (isExecUrl_(saved)) { return saved; }
  // 屬性還沒寫進去時用 Config.gs 寫死的那一份（專案屬性超過 50 個時，專案設定頁面只能看不能改）。
  var fixed = (typeof WEBAPP_URL_DEFAULT === 'string') ? WEBAPP_URL_DEFAULT.trim() : '';
  if (isExecUrl_(fixed)) { return fixed; }
  var live = '';
  try { live = String(ScriptApp.getService().getUrl() || ''); } catch (e) {}
  if (isExecUrl_(live)) { return live; }
  return '';
}

/** doGet 從 /exec 被打開時記下正式網址；只在變了的時候寫，不是每次都寫。 */
function rememberWebAppUrl_() {
  try {
    var live = String(ScriptApp.getService().getUrl() || '');
    if (!isExecUrl_(live)) { return; }
    var props = PropertiesService.getScriptProperties();
    if (props.getProperty('WEBAPP_URL') !== live) { props.setProperty('WEBAPP_URL', live); }
  } catch (e) {
    Logger.log('記錄正式網址失敗（不影響網頁）：' + e);
  }
}

/* 退訂連結（v54，Codex 規格 73–77）。

   一、不再退回 ScriptApp.getService().getUrl()：排程裡它回 /dev，只有專案擁有者開得起來——
       「電腦（登入擁有者）開得了、手機開不了」正是這個樣子（2026/09/23 管理者回報，是否就是這次的原因仍待核對那封信的實際網址）。
       取不到正式網址就回空字串，寄信那一端看到空字串會暫停寄送（見 deliverMessage_），不寄出打不開的退訂按鈕。
   二、kind＝daily／sms 時帶 type，確認頁可以只停這一類；不帶 type 的是訂閱確認信、管理信。
   三、連結本身不再直接取消：打開是一張確認頁，按下按鈕才停止（信件掃描或預先載入不會誤觸）。 */
function unsubscribeUrl_(email, token, kind) {
  var base = publicWebAppUrl_();
  if (!base) {
    Logger.log('退訂連結：沒有可用的正式網址（/exec），不產生連結。請打開一次網站，或在編輯器執行 setWebAppUrl()。');
    return '';
  }
  return base + '?action=unsubscribe&email=' + encodeURIComponent(email) + '&token=' + encodeURIComponent(token) +
    (kind === 'daily' || kind === 'sms' ? '&type=' + kind : '');
}

/* ------------------------------------------------------------------ *
 * 郵件查詢（供網站分頁與 AI 助手唯讀查詢）
 * ------------------------------------------------------------------ */

/** 列出所有已產生郵件內容的日期，新到舊。 */
function listMailDates() {
  var rows = readSheetObjects_('每日推播內容');
  var out = [];
  rows.forEach(function (r) {
    var d = fmtDate_(r['日期']);
    var article = String(r['文字稿'] || r['內文'] || r['文章'] || '');
    // 有文章內容的才列入，避免列出空殼
    if (d && article.length > 20) {
      out.push({ date: d, sent: String(r['寄送狀態'] || '未寄送') });
    }
  });
  out.sort(function (a, b) { return a.date < b.date ? 1 : -1; });   // 新到舊
  return out;
}

/**
 * 取某一天的郵件完整內容（HTML）。
 * 內容＝張震格式文章 + 結構化對照表，與當天實際寄出的每日整理相同。
 */
/* 每日總覽的預覽摘要：收件匣列表標題後面那一行灰字，用文章標題（去掉舊的「張震：」前綴）。
   正式寄送（dailyPushJob）與測試信（sendTestMailTo）共用，兩邊看到的一樣。 */
function dailyPreheader_(article) {
  return String((String(article || '').match(/^文章標題\s*[：:]\s*(.+)$/m) || [])[1] || '').replace(/^(?:張震|張正)\s*[：:]\s*/, '');
}

function getMailContent(dateStr) {
  var d = fmtDate_(dateStr);
  var rows = readSheetObjects_('每日推播內容');
  var row = rows.filter(function (r) { return fmtDate_(r['日期']) === d; })[0];
  if (!row) { return { date: d, found: false }; }

  var article = String(row['文字稿'] || row['內文'] || row['文章'] || '');
  if (!article) { return { date: d, found: false }; }

  // 只呈現文章本身。
  //
  // 這裡原本會再附一份「網站同一份結構化紀錄」的對照表。拿掉的理由：
  // 那份表格與網站的每日總覽是同一批資料，讀信的人要逐檔核對時本來就會去看網站，
  // 附在信裡只是把同樣的內容再講一次，讓信變得又長又重複，
  // 手機上還要往下滑很久才看得完。文章本身已經完整涵蓋當天所有個股。
  // 網站上沒有信尾，風險揭露接在文章後面，與信裡同一段（2026/09/17 v44）。
  // 寄信要用 articleHtml：信尾的 wrapMail_ 已經有這一段，接兩次就又重複了。
  var articleHtml = mdToHtml_(article);
  var html = articleHtml +
    '<div class="mc-sec mc-foot" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;' +
         'padding:14px 16px 2px;margin:12px 0 0;">' + mailRiskHtml_() + '</div>';

  return {
    date: d,
    found: true,
    sent: String(row['寄送狀態'] || '未寄送'),
    subject: '[' + d + '] 張震股市盤中家教班　每日整理',
    html: html,
    articleHtml: articleHtml,
    preheader: dailyPreheader_(article)
  };
}


/* ------------------------------------------------------------------ *
 * 測試寄信（管理者要求，2026/09/16）
 *
 * 在正式名單以外先看一封真的信。刻意不碰任何狀態：不讀訂閱名單決定收件者、
 * 不寫「寄送狀態」、不記「這一天寄過了」，所以跑完不會影響當天的正式推播，
 * 也不會讓某一天在網站上變成已寄送。內容與訂閱者、網站郵件查詢完全同一份。
 *
 * 同一天同一個信箱只寄一次：管理者說只測試一次，手滑再執行一次不該又寄一封。
 * 真的要再寄，第三個參數給 true。
 * ------------------------------------------------------------------ */
function sendTestMailTo(email, dateStr, force) {
  var to = String(email || '').trim();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
    throw new Error('請給收件信箱，例如 sendTestMailTo("someone@example.com")');
  }
  var d = fmtDate_(dateStr || '');
  if (!d) {
    // 預設拿昨天的（昨天的內容已經產生完了）；昨天沒有就用最近一天有內容的。
    var y = new Date();
    y.setDate(y.getDate() - 1);
    var yesterday = Utilities.formatDate(y, TZ, 'yyyy/MM/dd');
    d = getMailContent(yesterday).found ? yesterday : String((listMailDates()[0] || {}).date || '');
  }
  if (!d) { throw new Error('每日推播內容分頁還沒有任何一天的文章。'); }
  var content = getMailContent(d);
  if (!content.found) { throw new Error(d + ' 沒有郵件內容；換一天，或先在後台「重寫郵件內容」。'); }

  var props = PropertiesService.getScriptProperties();
  var key = 'TEST_MAIL_' + d + '_' + to.toLowerCase();
  var already = props.getProperty(key);
  if (already && force !== true) {
    return { ok: false, date: d, to: to, sentAt: already,
             reason: d + ' 的測試信已經在 ' + already + ' 寄給 ' + to + '，沒有再寄一封。' +
                     '真的要重寄：sendTestMailTo("' + to + '", "' + d + '", true)' };
  }

  // 收件者剛好在訂閱名單裡就用他自己的退訂權杖；不在名單裡時退訂連結按了會顯示無效，
  // 這是對的——測試信的收件者本來就沒有訂閱可退。
  var sub = activeSubscribers_().filter(function (s) {
    return String(s['Email']).toLowerCase() === to.toLowerCase();
  })[0];
  // 與正式的每日總覽同一套：種類 daily（退訂確認頁可只停這一類）、預覽摘要、純文字 body（v54）
  var testHtml = wrapMail_(content.articleHtml, to, sub ? String(sub['取消訂閱權杖']) : '', 'daily', content.preheader);
  MailApp.sendEmail({
    to: to,
    subject: content.subject,
    htmlBody: testHtml,
    body: mailPlainText_(testHtml)
  });
  props.setProperty(key, nowStamp_());
  try {
    getSheet_('系統狀態').appendRow([nowStamp_(), '測試寄信', d + ' 的每日整理已寄一封到 ' + to +
      '（測試，未更動訂閱名單與寄送狀態）', 'test', 'Apps Script']);
  } catch (e) { Logger.log('測試寄信：系統狀態寫入失敗 ' + e); }
  return { ok: true, date: d, to: to, subject: content.subject,
           note: '已寄出一封，內容是 ' + d + ' 的每日整理；訂閱名單、寄送狀態都沒有變動。' };
}

/** 編輯器裡選這一支直接執行：把昨天的每日整理寄一封到測試信箱。 */
function sendTestMailNow() {
  return sendTestMailTo('rainforecast2026@gmail.com', '');
}

/* 這一天沒寄出去的原因。

   dailyPushJob 有六個「直接 return」的出口，先前每一個都是靜默的——
   信沒來的時候，信箱只是安靜地沒有東西，執行紀錄裡也什麼都沒有，
   完全無從判斷是哪一關擋下來的。實際發生過一整天沒有信而查不出原因。

   現在每一個出口都寫進這裡，whyNoMail() 讀得到，狀態報告信也帶得上。 */
var PUSH_WHY_KEY = 'dailyPushLastReason';

function pushWhy_(d, why) {
  try {
    PropertiesService.getScriptProperties().setProperty(
      PUSH_WHY_KEY, JSON.stringify({ date: d, why: why, at: nowStamp_() }));
  } catch (e) { /* 記不住不影響寄信 */ }
  return why;
}

/* 同一天只寄第一封每日整理。

   先前唯一的防線是「每日推播內容」那一列的寄送狀態，但那一列會被換掉：
   GitHub 整天重跑會先刪掉那一列、再新增一列「待寄送」；管理者把當天內容刪掉重跑也一樣。
   狀態一回到待寄送，12:00–22:00 每五分鐘一棒的 dailyPushJob、品質關卡之後那一棒、
   後台投稿收尾的「推播信」，任何一個都會再寄一次——後來每更新一次就多一封（2026/09/11）。

   所以另外把「已寄出的日期」記在指令碼屬性，不跟著試算表的列走：
   第一封寄出的那一刻就記下，之後這一天不論狀態被改成什麼、列被刪掉幾次，都不再寄。
   網站與郵件查詢讀的仍是最新的文章，只是不再寄第二封。 */
var PUSH_SENT_KEY = 'dailyPushSentDates';
var PUSH_SENT_KEEP = 60;   // 只留最近 60 個寄送日；指令碼屬性單一值有大小上限

function pushSentDates_() {
  try {
    var raw = PropertiesService.getScriptProperties().getProperty(PUSH_SENT_KEY);
    var m = raw ? JSON.parse(raw) : {};
    return (m && typeof m === 'object') ? m : {};
  } catch (e) { return {}; }
}

/** 這一天第一封寄出的時間；沒寄過是空字串。 */
function pushSentAt_(d) { return String(pushSentDates_()[d] || ''); }

function markPushSent_(d) {
  try {
    var m = pushSentDates_();
    if (m[d]) { return; }
    m[d] = nowStamp_();
    var keys = Object.keys(m).sort();              // yyyy/MM/dd 字串排序就是時間順序
    while (keys.length > PUSH_SENT_KEEP) { delete m[keys.shift()]; }
    PropertiesService.getScriptProperties().setProperty(PUSH_SENT_KEY, JSON.stringify(m));
  } catch (e) {
    // 記不住時還有試算表的「已寄送」狀態擋著，不因此中斷寄信。
    Logger.log('寄送紀錄寫入失敗：' + e);
  }
}

/**
 * 今天的信為什麼還沒寄。在編輯器直接執行就會印出來。
 *
 * 這一支不做任何事，只回答問題。信沒來的時候第一個該跑的就是它。
 */
function whyNoMail() {
  var d = todayStr_();
  var out = ['========================================',
             '  今天（' + d + '）的推播狀態',
             '========================================'];

  out.push('是否為平日：' + (isTradingDayToday_() ? '是' : '否（週末不推播）'));

  var rows = getSheet_('每日推播內容').getDataRange().getValues();
  var found = null;
  for (var i = 1; i < rows.length; i++) {
    if (fmtDate_(rows[i][0]) === d) {
      found = { row: i + 1, article: String(rows[i][1] || ''), sent: String(rows[i][2] || '') };
      break;
    }
  }
  if (!found) {
    out.push('每日推播內容：今天還沒有那一列 → 上游還沒處理完，或處理失敗。');
    out.push('　到 GitHub Actions 看今天那一輪的輸出，或在後台投稿逐字稿。');
  } else {
    out.push('每日推播內容：第 ' + found.row + ' 列');
    out.push('　文章長度：' + found.article.length + ' 字' +
             (found.article ? '' : ' → 文章還沒產生，撰稿那一步沒跑完'));
    out.push('　寄送狀態：' + (found.sent || '（空白，代表還沒寄）'));
    if (String(found.sent) === '寄送中') {
      out.push('　「寄送中」代表有一次執行搶到鎖之後被中斷了。');
      out.push('　那個狀態不會自己恢復，執行 resetTodayPush() 清掉再讓它重寄。');
    }
  }
  if (pushSentAt_(d)) {
    out.push('今天的第一封已在 ' + pushSentAt_(d) + ' 寄出；之後的更新只換網站與郵件查詢的文章，不會再寄。');
  }

  var g = gateState_();
  if (!g || g.date !== d) {
    out.push('品質關卡：今天還沒開始');
  } else {
    out.push('品質關卡：' + g.status + '　目前在「' + (g.phase || '?') + '」' +
             '　第 ' + (g.tries || 1) + ' 次　更新於 ' + (g.updatedAt || '?'));
    if (g.lastError) { out.push('　最後的錯誤：' + String(g.lastError).slice(0, 160)); }
  }

  var subs = activeSubscribers_();
  var daily = subs.filter(function (x) {
    return String(x['訂閱項目']).indexOf('每日總覽') >= 0;
  });
  out.push('訂閱者：' + subs.length + ' 位，其中訂了每日總覽的 ' + daily.length + ' 位' +
           (daily.length ? '' : ' → 沒有人訂，寄了也沒有收件者'));

  try {
    out.push('今日剩餘寄信額度：' + MailApp.getRemainingDailyQuota());
  } catch (e) { out.push('查不到寄信額度：' + e); }

  try {
    var raw = PropertiesService.getScriptProperties().getProperty(PUSH_WHY_KEY);
    if (raw) {
      var w = JSON.parse(raw);
      out.push('上一次退出的原因：' + w.why + '（' + w.date + ' ' + w.at + '）');
    }
  } catch (e) { /* 沒有就算了 */ }

  out.push('');
  out.push('要立刻補寄：執行 forceSendToday()。');
  // 換行用 '\n'。先前寫成 chr(10)（Python 的寫法），JavaScript 沒有這個函式，
  // 在編輯器執行 whyNoMail() 會直接丟 ReferenceError: chr is not defined（2026/09/11）。
  Logger.log(out.join('\n'));
  return out.join('\n');
}

/** 把今天的寄送狀態清空，讓下一棒重新嘗試。卡在「寄送中」時用。 */
function resetTodayPush() {
  var d = todayStr_();
  var sh = getSheet_('每日推播內容');
  var rows = sh.getDataRange().getValues();
  for (var i = 1; i < rows.length; i++) {
    if (fmtDate_(rows[i][0]) === d) {
      sh.getRange(i + 1, 3).setValue('');
      Logger.log(d + ' 的寄送狀態已清空，下一棒會重新嘗試（最慢五分鐘）。');
      return true;
    }
  }
  Logger.log(d + ' 在每日推播內容裡沒有那一列。');
  return false;
}

/** 跳過品質關卡直接寄今天的信。關卡壞掉而當天又必須有信時用。 */
function forceSendToday() {
  var d = todayStr_();
  var st = gateState_() || {};
  st.date = d; st.status = '完成'; st.phase = '完成'; st.updatedAt = nowStamp_();
  st.log = (st.log || []).concat(['— 人工放行，未經複審 —']);
  setGateState_(st);
  resetTodayPush();
  dailyPushJob();
  return whyNoMail();
}


/* ------------------------------------------------------------------ *
 * 逐收件者寄送帳本（v54，Codex 規格 86–89）
 *
 * 先前每日總覽是「第一封寄出就把整天標成已寄」，後面某一位失敗（額度、暫時錯誤）就停下，
 * 之後每一棒都看到「已寄送」而不會再補；盤中通知則是額度不足時只寄前 N 位、其餘只記 log。
 * 現在每一則信（messageId＝種類｜日期或文章ID）對每一位收件者各記一列：
 *   pending   還沒輪到（時間或額度不夠）→ 下一棒續送
 *   sending   正要寄。程序若在寄出後、寫回前被切斷，這一列會停在 sending；
 *             超過 15 分鐘仍是 sending 就改成 unknown（結果不明），不自動再寄，避免重複。
 *   accepted  MailApp 已接受（不等於已送達收件匣、更不等於已讀）
 *   retryable 這次失敗、下一棒再試；同一人試滿 3 次改 failed
 *   failed    放棄，列在後台
 * 已 accepted 的人不會因為同一天文章更新而再收一次：messageId 不含內容版本，版本另記一欄。
 * MailApp 沒有冪等鍵，所以這不是 exactly-once；寄出與寫回之間的窄縫以 unknown 呈現，交給管理者判斷。
 * ------------------------------------------------------------------ */
var DELIVERY_SHEET_ = '寄送帳本';
var DELIVERY_COLS_ = ['訊息ID', '種類', '日期', '內容版本', '收件者', '狀態', '嘗試次數', '最後錯誤', '更新時間', '服務接受時間'];
var DELIVERY_PENDING_KEY_ = 'deliveryPendingV54';
var DELIVERY_DONE_ = { accepted: 1, failed: 1, unknown: 1, cancelled: 1 };

function deliveryVersion_(text) {
  try {
    var b = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(text || ''), Utilities.Charset.UTF_8);
    return b.slice(0, 5).map(function (x) { return ('0' + ((x + 256) % 256).toString(16)).slice(-2); }).join('');
  } catch (e) { return String(String(text || '').length); }
}

/** 讀出某一則信的帳本。回傳的物件在記憶體裡改，flush() 才寫回（一次批次寫，不逐格寫）。 */
function deliveryLedger_(messageId, meta) {
  var sh = getSheet_(DELIVERY_SHEET_);
  var vals = sh.getDataRange().getValues();
  var head = (vals[0] || []).map(String);
  if (head.join('|') !== DELIVERY_COLS_.join('|') && vals.length <= 1) { head = DELIVERY_COLS_.slice(); }
  var ci = {}; DELIVERY_COLS_.forEach(function (k) { ci[k] = head.indexOf(k); });
  var byEmail = {}, now = Date.now();
  for (var i = 1; i < vals.length; i++) {
    if (String(vals[i][ci['訊息ID']]) !== messageId) { continue; }
    var em = String(vals[i][ci['收件者']]).trim().toLowerCase();
    var stamp = String(vals[i][ci['更新時間']] || '');
    var st = String(vals[i][ci['狀態']] || 'pending');
    var t = stamp ? new Date(stamp.replace(/-/g, '/')).getTime() : 0;
    if (st === 'sending' && (!t || now - t > 15 * 60 * 1000)) { st = 'unknown'; }
    byEmail[em] = { row: i + 1, state: st, attempt: Number(vals[i][ci['嘗試次數']]) || 0,
                    err: String(vals[i][ci['最後錯誤']] || ''), acceptedAt: String(vals[i][ci['服務接受時間']] || ''),
                    dirty: st !== String(vals[i][ci['狀態']] || 'pending') };
  }
  function rowOf(em, x) {
    var r = DELIVERY_COLS_.map(function () { return ''; });
    r[0] = messageId; r[1] = meta.kind; r[2] = meta.date; r[3] = meta.version || '';
    r[4] = em; r[5] = x.state; r[6] = x.attempt || 0; r[7] = String(x.err || '').slice(0, 200);
    r[8] = nowStamp_(); r[9] = x.acceptedAt || '';
    return r;
  }
  return {
    state: function (em) { var x = byEmail[String(em).toLowerCase()]; return x ? x.state : 'pending'; },
    attempt: function (em) { var x = byEmail[String(em).toLowerCase()]; return x ? x.attempt : 0; },
    set: function (em, patch) {
      em = String(em).toLowerCase();
      var x = byEmail[em] || (byEmail[em] = { row: 0, state: 'pending', attempt: 0 });
      Object.keys(patch).forEach(function (k) { x[k] = patch[k]; });
      x.dirty = true;
    },
    flush: function () {
      var appends = [];
      Object.keys(byEmail).forEach(function (em) {
        var x = byEmail[em];
        if (!x.dirty) { return; }
        x.dirty = false;
        if (x.row) { sh.getRange(x.row, 1, 1, DELIVERY_COLS_.length).setValues([rowOf(em, x)]); }
        else { appends.push([em, rowOf(em, x)]); }
      });
      if (appends.length) {
        var start = Math.max(sh.getLastRow(), 1) + 1;
        sh.getRange(start, 1, appends.length, DELIVERY_COLS_.length).setValues(appends.map(function (a) { return a[1]; }));
        appends.forEach(function (a, k) { byEmail[a[0]].row = start + k; });
      }
    },
    summary: function (emails) {
      var s = { total: emails.length, accepted: 0, failed: 0, unknown: 0, retryable: 0, pending: 0, sending: 0 };
      emails.forEach(function (em) { var st = this.state(em); s[st] = (s[st] || 0) + 1; }, this);
      s.open = s.pending + s.retryable + s.sending;
      return s;
    }
  };
}

/** 記住「還有沒寄完的信」，重試那一棒才知道要不要讀帳本（沒有待寄時不讀整張表）。 */
function deliveryPendingSet_(messageId, info) {
  try {
    var p = PropertiesService.getScriptProperties(), m = JSON.parse(p.getProperty(DELIVERY_PENDING_KEY_) || '{}');
    if (info) { m[messageId] = info; } else { delete m[messageId]; }
    p.setProperty(DELIVERY_PENDING_KEY_, JSON.stringify(m));
  } catch (e) { Logger.log('待寄清單記不住：' + e); }
}
function deliveryPendingAll_() {
  try { return JSON.parse(PropertiesService.getScriptProperties().getProperty(DELIVERY_PENDING_KEY_) || '{}'); }
  catch (e) { return {}; }
}

/**
 * 把一則信寄給一批收件者。o：{messageId, kind, date, version, subject, html(email, token), quota, budgetMs}
 * 回傳帳本摘要。只寄 pending／retryable；accepted、failed、unknown 一律跳過。
 * 每十位一批：先把這一批標成 sending 寫回，再逐一寄，寄完再寫回結果。
 */
function deliverMessage_(subs, o) {
  var led = deliveryLedger_(o.messageId, o);
  var start = Date.now(), stopped = '', fresh = 0;
  // 沒有正式網址就不寄（v54，Codex 規格 74）：寄出去的信退訂按鈕會打不開。全部留在 pending，網址設定好之後續送。
  if (!publicWebAppUrl_()) { stopped = 'no-url'; }
  var quota = (o.quota == null) ? null : Number(o.quota);
  var emails = subs.map(function (s) { return String(s['Email']).trim(); });
  var todo = subs.filter(function (s) { return !DELIVERY_DONE_[led.state(String(s['Email']).trim())]; });
  if (stopped === 'no-url') {
    Logger.log('寄送暫停：沒有正式網址（/exec），' + o.messageId + ' 全部留待續送。');
    try {
      var nc = CacheService.getScriptCache();
      if (!nc.get('nourl_' + o.messageId)) {
        nc.put('nourl_' + o.messageId, '1', 21600);
        getSheet_('系統狀態').appendRow([nowStamp_(), '寄信', '寄送暫停：沒有正式網址（/exec），' + o.messageId +
          ' 全部留待續送。請在編輯器執行 setWebAppUrl()。', 'mail', 'Apps Script']);
      }
    } catch (e) {}
  }
  for (var k = 0; k < todo.length && !stopped; k += 10) {
    if (Date.now() - start > (o.budgetMs || 240000)) { stopped = 'time'; break; }
    if (quota !== null && quota <= 0) { stopped = 'quota'; break; }
    var chunk = todo.slice(k, k + 10);
    if (quota !== null) { chunk = chunk.slice(0, Math.max(0, quota)); }
    chunk.forEach(function (s) { led.set(String(s['Email']).trim(), { state: 'sending' }); });
    led.flush();
    chunk.forEach(function (s) {
      var em = String(s['Email']).trim();
      if (stopped) { led.set(em, { state: 'pending' }); return; }
      var att = led.attempt(em) + 1;
      try {
        var htmlBody = o.html(em, String(s['取消訂閱權杖'] || ''));
        MailApp.sendEmail({ to: em, subject: o.subject, htmlBody: htmlBody, body: mailPlainText_(htmlBody) });
        led.set(em, { state: 'accepted', attempt: att, err: '', acceptedAt: nowStamp_() });
        fresh++;
        if (quota !== null) { quota--; }
      } catch (e) {
        var msg = String(e && e.message || e);
        var quotaErr = /quota|too many times|limit|上限/i.test(msg);
        led.set(em, { state: (!quotaErr && att >= 3) ? 'failed' : 'retryable', attempt: att, err: msg.slice(0, 200) });
        if (quotaErr) { stopped = 'quota'; }
      }
    });
    led.flush();
  }
  var sum = led.summary(emails);
  sum.stoppedBy = stopped;
  sum.fresh = fresh;
  if (sum.open > 0) { deliveryPendingSet_(o.messageId, { kind: o.kind, date: o.date, at: Date.now() }); }
  else { deliveryPendingSet_(o.messageId, null); }
  return sum;
}

/** 後台用：某一天各則信的投遞摘要（服務接受／失敗／不明／待續送）。只讀。 */
function deliverySummaryForDate_(d) {
  var out = {};
  try {
    readSheetObjects_(DELIVERY_SHEET_).forEach(function (r) {
      if (fmtDate_(r['日期']) !== d) { return; }
      var id = String(r['訊息ID']), st = String(r['狀態'] || 'pending');
      var x = out[id] || (out[id] = { kind: String(r['種類']), total: 0, accepted: 0, failed: 0, unknown: 0, open: 0, lastError: '', firstAcceptedAt: '', lastAcceptedAt: '' });
      x.total++;
      if (st === 'accepted') {
        x.accepted++;
        var at = String(r['服務接受時間'] || r['更新時間'] || '');
        if (at && (!x.firstAcceptedAt || at < x.firstAcceptedAt)) { x.firstAcceptedAt = at; }
        if (at && at > x.lastAcceptedAt) { x.lastAcceptedAt = at; }
      } else if (st === 'failed') { x.failed++; }
      else if (st === 'unknown' || st === 'sending') { x.unknown++; } else { x.open++; }
      if (r['最後錯誤']) { x.lastError = String(r['最後錯誤']).slice(0, 80); }
    });
  } catch (e) {}
  return out;
}

/** 今天有沒有節目（v54）。上游判定「今日無直播」寫進系統狀態；影片清單今天沒有可用逐字稿也算沒有。 */
function noVideoToday_(today) {
  try {
    if (readSheetObjects_('系統狀態').some(function (r) {
      return String(r['時間'] || '').indexOf(today) === 0 && String(r['類別'] || '') === '今日無直播'; })) {
      // 停播判定後若管理者補貼真實原稿並完成整理，以完成的影片列為準。
      var completed=readSheetObjects_('影片清單').some(function(r){
        return fmtDate_(r['發布日期'])===today && String(r['處理狀態']||'')==='完成' &&
          String(r['原始逐字稿內容']||'').trim().length>200;
      });
      return completed?'':'今日無直播';
    }
  } catch (e) {}
  return '';
}

/** 當日最早寄送時刻；只影響未寄的今日整理，預設 12:00。 */
function dailyPushStartTime_(date) {
  var saved = String(PropertiesService.getScriptProperties().getProperty('DAILY_PUSH_START_TODAY') || '');
  var parts = saved.split('|');
  var value = parts[0] === String(date).replace(/\D/g, '') ? parts[1] : '1200';
  return /^(1[2-9]|20|21)[0-5][0-9]$/.test(value) ? value : '1200';
}

function dailyPushJob() {
  var today = todayStr_();
  if (!isTradingDayToday_()) {
    return pushWhy_(today, '週末不推播');
  }
  var startHm = dailyPushStartTime_(today);
  if (Number(Utilities.formatDate(new Date(), TZ, 'HHmm')) < startHm) {
    return pushWhy_(today, '未到今日設定的最早寄送時間 ' + startHm.slice(0, 2) + ':' + startHm.slice(2));
  }
  var sh = getSheet_('每日推播內容');
  var rows = sh.getDataRange().getValues();

  var rowIdx = -1, sent = '', article = '';
  for (var i = 1; i < rows.length; i++) {
    if (fmtDate_(rows[i][0]) === today) {
      rowIdx = i + 1; sent = rows[i][2]; article = String(rows[i][1] || ''); break;
    }
  }
  if (rowIdx < 0) {
    return pushWhy_(today, '每日推播內容還沒有今天這一列（上游還沒處理完或失敗）');
  }
  // 同一天只寄第一封：列被重建、狀態被改回待寄送也不再寄（理由見 PUSH_SENT_KEY）。
  var sentAt = pushSentAt_(today);
  // 部分寄送（v54）：已經有人收到，但名單還沒寄完——繼續寄給還沒收到的人，已收到的人帳本會擋下。
  if (sentAt && String(sent) === '部分寄送') { sentAt = ''; }
  if (sentAt) {
    if (String(sent).indexOf('已寄送') !== 0) {
      // 狀態欄順手改回來，後台與郵件查詢看到的才一致。
      withLock_(function () { sh.getRange(rowIdx, 3).setValue('已寄送').setNote(''); });
    }
    return pushWhy_(today, '已寄送（' + sentAt + ' 已寄出第一封，之後的更新不再寄）');
  }
  if (String(sent).indexOf('已寄送') === 0) {
    markPushSent_(today);   // 這個機制上線前就寄過的日子，也補記進去
    return pushWhy_(today, '已寄送');
  }
  if (String(sent) === '寄送中') {
    /* 卡在「寄送中」。

       這個狀態是搶鎖之後、真正寄出之前設的，用來防止三個時段各寄一封。
       但如果那一次執行在中間被中斷（超時、額度、例外），狀態就永遠留在
       「寄送中」，之後每一棒都被它擋掉——當天再也不會有信，而且沒有任何
       錯誤訊息。先前就是這樣一整天沒有信。

       所以加一個逾時：超過十五分鐘還在寄送中，就當作那一次死了，清掉重來。
       重複寄的風險遠小於整天沒有信，而且十五分鐘足夠任何一次正常的寄送。 */
    var stampCell = String(sh.getRange(rowIdx, 3).getNote() || '');
    var t = stampCell ? new Date(stampCell.replace(/-/g, '/')).getTime() : 0;
    if (!t || (Date.now() - t) > 15 * 60 * 1000) {
      Logger.log(today + ' 卡在「寄送中」超過 15 分鐘，判定該次執行已中斷，清掉重來。');
      sh.getRange(rowIdx, 3).setValue('').setNote('');
      sent = '';
    } else {
      return pushWhy_(today, '另一棒正在寄送中');
    }
  }
  if (!article) {
    return pushWhy_(today, '文章還沒產生（撰稿那一步沒跑完）');
  }
  /* 沒有影片的日子不寄每日總覽（v54，管理者 2026/09/23）。會員簡訊的盤中即時通知照常寄，
     網站郵件查詢那一天留空。文章若被逐日編輯或其他路徑生出來，也在這裡擋下。 */
  var noShow = noVideoToday_(today);
  if (noShow) {
    return pushWhy_(today, noShow + '：不寄每日總覽（盤中即時通知照常寄出）');
  }

  // 寄信之前必須先過品質關卡（完整性稽核 → 內容複審 → 重寫每日整理）。
  // 關卡會改動當天的紀錄，也會重寫這篇文章，所以順序不能顛倒：
  // 先寄再複審的話，信裡是舊的、網站是新的，而信寄出去就改不了了。
  //
  // 關卡沒過就先退出，由關卡跑完後自己再呼叫一次這一支。
  // 這一段必須在搶鎖之前，否則狀態會被標成「寄送中」然後空等。
  if (!gateReady_(today)) {
    var g = gateState_() || {};
    return pushWhy_(today, '等品質關卡（' + (g.status || '?') + '　' +
                           (g.phase || '?') + '　第 ' + (g.tries || 1) + ' 次）');
  }

  // 關卡可能重寫過文章，重讀一次，確保寄出去的是複審後的版本。
  article = String(sh.getRange(rowIdx, 2).getValue() || '');
  if (!article) {
    return pushWhy_(today, '複審後文章變成空的（撰稿失敗）');
  }

  // 防重複寄送的關鍵：12:00 起每五分鐘一棒，前後兩棒可能同時讀到「待寄送」
  // 而各自開始寄，造成一天寄好幾封。這裡用鎖搶占：搶到鎖的 job 先把狀態改成
  // 「寄送中」再放鎖，其餘 job 一看到不是空白就直接退出。先標記、後寄信，
  // 確保同一天只有一個 job 會真正寄出。
  var claimed = false;
  withLock_(function () {
    if (pushSentAt_(today) && String(sh.getRange(rowIdx, 3).getValue()) !== '部分寄送') { return; }   // 等鎖的這段時間，另一棒可能已經寄完
    var cur = String(sh.getRange(rowIdx, 3).getValue());
    if (cur === '' || cur === '待寄送' || cur === '未寄送' || cur === '部分寄送') {
      // 時間戳寫在儲存格附註裡，上面的逾時判斷讀的就是它。
      // 寫在附註而不是值裡面，是因為值那一欄有固定的幾個狀態字串，
      // 塞時間進去會讓所有比對狀態的地方都要跟著改。
      sh.getRange(rowIdx, 3).setValue('寄送中').setNote(nowStamp_());
      claimed = true;
    }
  });
  if (!claimed) {
    return pushWhy_(today, '另一棒搶先寄了');
  }

  // 信件內容只有文章本身，不再附結構化對照表（理由見 getMailContent）。
  // 網站的郵件查詢分頁讀的是同一份文章，所以兩邊看到的內容完全一致。
  var body = mdToHtml_(article);
  var dailyPre = dailyPreheader_(article);
  var subs = activeSubscribers_().filter(function (s) { return String(s['訂閱項目']).indexOf('每日總覽') >= 0; });
  var quota = null;
  try { quota = MailApp.getRemainingDailyQuota(); } catch (e) { quota = null; }
  var sum;
  try {
    sum = deliverMessage_(subs, {
      messageId: 'daily|' + today, kind: 'daily', date: today, version: deliveryVersion_(article),
      subject: '[' + today + '] 張震股市盤中家教班　每日整理',
      html: function (email, token) { return wrapMail_(body, email, token, 'daily', dailyPre); },
      quota: quota, budgetMs: 200000
    });
  } catch (err) {
    // 帳本本身讀寫失敗（試算表暫時錯誤）：狀態退回，讓下一棒重來；帳本裡已 accepted 的人不會重收。
    withLock_(function () { sh.getRange(rowIdx, 3).setValue('部分寄送').setNote(''); });
    pushWhy_(today, '寄送帳本讀寫失敗，下一棒續送：' + String(err && err.message || err).slice(0, 120));
    throw err;
  }
  var done = sum.open === 0;
  if (sum.accepted > 0 || done) { markPushSent_(today); }
  var label = done
    ? (sum.failed || sum.unknown ? '已寄送（' + sum.accepted + '/' + sum.total + ' 服務接受，失敗 ' + sum.failed + '、不明 ' + sum.unknown + '）' : '已寄送')
    : '部分寄送';
  withLock_(function () { sh.getRange(rowIdx, 3).setValue(label).setNote(''); });
  pushWhy_(today, (done ? '寄送完成　' : '部分寄送，下一棒續送　') + '服務接受 ' + sum.accepted + '/' + sum.total +
           (sum.open ? '，待續送 ' + sum.open : '') + (sum.stoppedBy ? '（' + (sum.stoppedBy === 'quota' ? '額度用完' : '時間到') + '）' : '') +
           '　' + nowStamp_());
}

/* ------------------------------------------------------------------ *
 * 每日推播內文（結構化，與網站同源）
 * ------------------------------------------------------------------ */
function esc_(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/** 放進雙引號屬性（href）裡的值：esc_ 之外再跳脫引號。信箱格式檢查只看 @ 與點，擋不住引號。 */
function escAttr_(s) {
  return esc_(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/**
 * 結構化對照表 HTML。
 *
 * 目前沒有任何寄信路徑會用到它：每日推播與郵件查詢都已改成只呈現文章本身。
 * 保留這支函式是為了兩件事：
 *   1. 稽核時想手動比對某天的結構化紀錄，在編輯器裡直接呼叫就有。
 *   2. 日後若想做一封「對照版」的信，不必重寫。
 * 若確定永遠不再需要，整段刪掉也不會影響任何功能。
 */
function dailyDigestHtml_(dateStr) {
  var d = searchByDate(dateStr);
  if (!d.found) {
    return '<p>今天的影片中沒有明確的買入、賣出、觀望或會員持股紀錄。</p>';
  }
  function tradeTbl(list) {
    return '<table style="width:100%;border-collapse:collapse;margin:6px 0 16px;">' +
      '<tr><th align="left">股票名稱</th><th align="left">代號</th>' +
      '<th align="left">價位說明</th><th align="left">說明重點</th></tr>' +
      list.map(function (r) {
        return '<tr><td>' + esc_(r.name) + '</td><td>' + esc_(r.code) + '</td><td>' +
          esc_(r.price || '未說明') + '</td><td>' + esc_(r.reason || '未說明') + '</td></tr>';
      }).join('') + '</table>';
  }
  function holdTbl(list) {
    return '<table style="width:100%;border-collapse:collapse;margin:6px 0 16px;">' +
      '<tr><th align="left">股票名稱</th><th align="left">代號</th>' +
      '<th align="left">目前立場</th><th align="left">說明重點</th></tr>' +
      list.map(function (r) {
        return '<tr><td>' + esc_(r.name) + '</td><td>' + esc_(r.code) + '</td><td>' +
          esc_(r.stance || '未說明') + '</td><td>' + esc_(r.note || '未說明') + '</td></tr>';
      }).join('') + '</table>';
  }
  var html = '<p>以下為 ' + d.date + ' 影片中明確講到的內容，與網站呈現同一份資料。</p>';
  html += '<h3>今日買入</h3>' + (d.buy.length ? tradeTbl(d.buy) : '<p>無</p>');
  html += '<h3>今日賣出</h3>' + (d.sell.length ? tradeTbl(d.sell) : '<p>無</p>');
  html += '<h3>觀望不碰（語氣偏空）</h3>' + (d.watchAvoid && d.watchAvoid.length ? tradeTbl(d.watchAvoid) : '<p>無</p>');
  html += '<h3>觀望注意（語氣偏多）</h3>' + (d.watchWatch && d.watchWatch.length ? tradeTbl(d.watchWatch) : '<p>無</p>');
  html += '<h3>會員目前持有</h3>' + (d.holdings.length ? holdTbl(d.holdings) : '<p>無</p>');
  return html;
}

/* ------------------------------------------------------------------ *
 * 完整性稽核：修飾後逐字稿 vs 結構化紀錄
 * 逐字稿是真實來源；找出逐字稿點名、但沒進紀錄（因此網站與推播都會漏）的個股。
 * ------------------------------------------------------------------ */
var DIGEST_AUDIT_SYSTEM =
  '你是一致性稽核員。給你「修飾後逐字稿」（張震實際講的內容，視為真實來源）與' +
  '「網站結構化紀錄」（買入／賣出／觀望不碰／觀望注意／會員持股）。\n' +
  '任務：找出逐字稿中有明確「指名的單一上市櫃公司」，其操作或立場卻沒有出現在結構化紀錄裡的。\n' +
  '規則：\n' +
  '1. 只看「單一一家可掛牌交易的公司」。凡是產業、族群、概念、技術、材料、集團、' +
  '   市場泛稱，一律不算漏，不要列入。\n' +
  '   明確舉例（這些都不是個股，絕對不可列入 missing）：記憶體、面板、載板、AB載板、' +
  '   ABF、CoWoS、HBM、石英元件、光通訊、被動元件、散熱、重電、機器人、矽光子、' +
  '   航運股、金融股、AI、AI伺服器、電動車、儲能、太陽能、生技、台塑集團、權值股、大盤。\n' +
  '2. 判斷準則：這個詞是「一整類公司的統稱」還是「某一家特定公司」？' +
  '   是統稱就是產業，不算漏；是特定公司才可能算漏。拿不準時一律當產業、不列入。\n' +
  '3. 買賣限「今天」明講執行；未買賣但被點名的個股，語氣偏空應在 watchAvoid（觀望不碰），\n' +
  '   語氣偏多或中性應在 watchWatch（觀望注意）。\n' +
  '4. 以名稱或代號比對，已在結構化紀錄裡的不算漏。名稱照逐字稿實際講的填。\n' +
  '5. 純粹回顧以前的操作，或拿舊單當教學範例，不算漏。\n' +
  '   例如「這支我已經沒了，我都賺錢賣」「當初跌停我沒賣，等上來到黑K棒上緣才賣」，\n' +
  '   那是完結的舊事，既不是今天的動作也不是現在的立場，不要列入 missing。\n' +
  '6. 只是報個價、講成交量、回答問題時順口帶到而沒有任何立場的，也不算漏。\n' +
  '7. 集團順帶點名不算漏。他講「台塑集團」「鴻海集團」這種整體時會念到成分股的名字，\n' +
  '   例如台塑、台化、南亞。除非他單獨對其中某一檔說了要怎麼做，否則不要列入。\n' +
  '8. 族群舉例不算漏。他講「航運」「記憶體」「重電」時常拿幾檔當例子，\n' +
  '   例如長榮、陽明。重點是整個族群的方向，不是那一檔的個別立場，不要列入。\n' +
  '9. 拿來當對照或比喻的不算漏。例如「這檔跟大立光一樣都是高價股」，\n' +
  '   大立光只是參照物，不是他在談的標的，不要列入。\n' +
  '只回傳 JSON，不要多餘文字：\n' +
  '{"consistent": true/false, "missing": [{"name":"","is_stock":true,"where":"買入/賣出/觀望不碰/觀望注意/持有","evidence":"逐字稿中的一句話"}]}\n' +
  '每一筆 missing 都必須自問 is_stock：只有你確信它是單一上市櫃公司才填 true，' +
  '否則填 false。consistent 為 true 當且僅當沒有任何 is_stock 為 true 的漏抓。';

function digestAuditVerdict_(dateStr) {
  try {
    var d = fmtDate_(dateStr);
    var video = readSheetObjects_('影片清單').filter(function (r) { return fmtDate_(r['發布日期']) === d; })[0];
    var v2 = video ? rawTranscript_(video) : '';
    if (v2.length < 200) { return { consistent: true, skipped: '無修飾後逐字稿可比對' }; }

    var s = searchByDate(d);
    var struct = { buy: s.buy, sell: s.sell, watchAvoid: s.watchAvoid,
                   watchWatch: s.watchWatch, holdings: s.holdings };

    var raw = callGemini_(PIPE_EXTRACT_SYSTEM + '\n本輪只輸出相容稽核格式：{"missing":[{"name":"原文名稱","where":"會員持股或觀望注意或觀望不碰","evidence":"原句","is_stock":true}]}。只列目前結構化資料缺少且有證據者。買賣日期須等於影片日期才可列；歷史買賣交由上游重跑。',
      '網站結構化紀錄:\n' + JSON.stringify(struct) + '\n\n修飾後逐字稿:\n' + v2,
      { maxOut: 1200, json: true });
    var j = JSON.parse(String(raw).replace(/```json|```/g, '').trim());
    j.missing = j.missing || [];
    // 雙重過濾：AI 自評 is_stock 必須為 true，且名稱本身不能是已知產業/族群。
    // 這樣像「石英元件」「AB載板」「三藝數（誤植）」這種就不會被當漏抓一直報。
    j.missing = j.missing.filter(function (m) {
      if (m.is_stock === false) { return false; }
      if (typeof isIndustryName_ === 'function' && isIndustryName_(m.name)) { return false; }
      return true;
    });
    j.consistent = !j.missing.length;
    return j;
  } catch (e) {
    // 額度用完要往外拋。回 consistent:true 等於騙上層「今天沒有漏抓」，
    // 那會讓全面重整安靜地跳過一整天的稽核，而且沒有人知道。
    if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
    throw new Error('完整性稽核失敗，未放行：' + String(e));
  }
}

/** 手動稽核任一天，回傳並記錄結果。用來確認新版是否已改善。 */
function auditDigest(dateStr) {
  var v = digestAuditVerdict_(dateStr || todayStr_());
  Logger.log(JSON.stringify(v, null, 2));
  return v;
}

/* ------------------------------------------------------------------ *
 * 爬取狀態報告
 * 收件者放在指令碼屬性 STATUS_EMAILS，以逗號分隔。
 * ------------------------------------------------------------------ */
function statusEmails_() {
  var raw = PropertiesService.getScriptProperties().getProperty('STATUS_EMAILS') || '';
  var list = raw.split(',').map(function (s) { return s.trim(); }).filter(String);
  if (!list.length) {
    var owner = PropertiesService.getScriptProperties().getProperty('OWNER_EMAIL');
    if (owner) { list = [owner]; }
  }
  return list;
}

/* 今天已經回報過「正常」了。

   這一支在 13:10 與 15:50 各觸發一次，本來是為了「早上沒抓到、下午補一次」。
   但改成人工貼逐字稿之後，下午重跑一次工單是常態，而重跑期間
   影片處理狀態會回到「處理中」——於是 13:10 剛寄過一封「正常」，
   15:50 又寄一封「需要注意」，內容還比前一封難看（修飾後逐字稿 0 字，
   因為那一刻正在重新潤飾）。實際收到的就是這個順序。

   一天之內同一件事回報兩次、而且後面那次是誤報，比不報還糟：
   看的人會開始忽略這封信，真的出事那天就沒有人注意。
   所以只要當天已經回報過正常，後面就不再寄。真的有新問題時，
   15:30 的 failureAlertJob 仍然會通知，那一支才是專門報失敗的。 */
var STATUS_OK_KEY_ = 'statusReportOkDate';

/** 13:10 與 15:50 觸發。把今天的爬取結果整理成一封信。 */
function statusReportJob() {
  if (!isTradingDayToday_()) { return; }   // 週末不寄狀態信
  var to = statusEmails_();
  if (!to.length) { return; }

  var today = todayStr_();
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty(STATUS_OK_KEY_) === today) {
    Logger.log(today + ' 稍早已經回報過「正常」，這一棒不再重複寄狀態信。');
    return;
  }
  var logs = readSheetObjects_('系統狀態').filter(function (r) {
    return String(r['時間'] || '').indexOf(today) === 0;
  });
  var video = readSheetObjects_('影片清單').filter(function (r) { return fmtDate_(r['發布日期']) === today; })[0];
  var trades = readSheetObjects_('操作紀錄').filter(function (r) { return fmtDate_(r['日期']) === today; });
  var holds = readSheetObjects_('會員持股').filter(function (r) { return fmtDate_(r['日期']) === today; });
  var push = readSheetObjects_('每日推播內容').filter(function (r) { return fmtDate_(r['日期']) === today; })[0];

  var fails = logs.filter(function (r) { return String(r['類別']) === '失敗'; });
  // 輪詢次數：內部輪詢循環每敲一次門會寫一列「輪詢」，每次 job 啟動另寫一列「開始」。
  // 兩者都代表一次實際嘗試，都要算進去，否則會像先前那樣嚴重低估。
  var runs = logs.filter(function (r) {
    var k = String(r['類別']);
    return k === '開始' || k === '輪詢';
  });

  var v1 = video ? String(video['原始逐字稿內容'] || '').length : 0;
  var v2 = video ? String(video['修飾後逐字稿內容'] || '').length : 0;
  var status = video ? String(video['處理狀態'] || '') : '尚無紀錄';

  /* 今天有開盤，但他沒有節目。

     休市日這封信本來就不會寄（上面的 isTradingDayToday_）。這裡擋的是另一種：
     開盤日他請假或臨時停播——2026/09/24 就是，他在 9/23 的節目裡講
     「明天我放假，我們下次見面是下個禮拜二」。
     那種日子沒有影片是正常的，先前卻會寄一封「需要注意」，看起來像壞掉。

     判定由上游 transcript.py 做（頻道上連一支今天的影片都沒有，
     不是排定中、不是直播中、也不是已結束），寫進系統狀態，這裡只讀結論。 */
  var noShow = status !== '完成' && logs.some(function (r) { return String(r['類別'] || '') === '今日無直播'; });
  var plannedNoShow = !noShow && !video && logs.some(function (r) { return String(r['類別'] || '') === '預告停播'; });
  if (noShow) { props.setProperty(STATUS_OK_KEY_, today); }

  var ok = (status === '完成' && v2 > 200 && (trades.length || holds.length));
  if (ok) {
    // 先記下來再寄。順序不能反：寄信可能因為配額失敗，
    // 那時寧可少寄一封，也不要因為沒記到而讓下一棒又寄一封「需要注意」。
    props.setProperty(STATUS_OK_KEY_, today);
  }
  var head = noShow ? '今日無直播' : plannedNoShow ? '預告停播，待當日頻道確認'
    : ok ? '正常' : (status === '等待中' ? '進行中' : '需要注意');

  var html = '<p><b>' + today + ' 爬取狀態：' + head + '</b></p>' +
    '<table style="border-collapse:collapse;">' +
    '<tr><td>本日輪詢次數</td><td>' + runs.length + ' 次</td></tr>' +
    '<tr><td>影片處理狀態</td><td>' + esc_(status) + '</td></tr>' +
    '<tr><td>原始逐字稿</td><td>' + v1 + ' 字</td></tr>' +
    '<tr><td>修飾後逐字稿</td><td>' + v2 + ' 字</td></tr>' +
    '<tr><td>操作紀錄</td><td>' + trades.length + ' 筆</td></tr>' +
    '<tr><td>會員持股</td><td>' + holds.length + ' 筆</td></tr>' +
    '<tr><td>每日推播</td><td>' + (push ? esc_(push['寄送狀態'] || '未寄送') : '尚未產生') + '</td></tr>' +
    '<tr><td>失敗次數</td><td>' + fails.length + ' 次</td></tr>' +
    '</table>';

  if (fails.length) {
    html += '<p>最近一次失敗訊息：' + esc_(fails[fails.length - 1]['說明']) + '</p>';
  }
  if (noShow) {
    html += '<p>今天有開盤，但頻道上沒有這一集：不是排定中、不是直播中，也不是已結束。' +
      '上游已判定今天沒有節目並停止取稿，剩下的排程不會再空轉。</p>' +
      '<p>訂閱者<b>不會</b>收到每日整理——沒有產生文章，推播那一步本來就不會執行，' +
      '不需要另外處理。行情與持股追蹤照常更新。</p>' +
      '<p class=\"m\">如果他其實有播、只是標題或時間不一樣，' +
      '到後台「投稿逐字稿」把原稿貼進來，後面的流程會自己接上。</p>';
  } else if (plannedNoShow && !video) {
    html += '<p>前一集原文已預告請假，當日尚未發現影片。系統已停止密集輪詢，' +
      '後續排程會單次查片；節目時段後仍無影片才正式判定今日無直播。</p>';
  } else if (!video) {
    if (runs.length === 0) {
      html += '<p style="color:#b00;">目前輪詢次數為 0，代表這個時段 GitHub Actions 一次都還沒被觸發' +
        '（排程延遲或被跳過）。若持續為 0，請到 Actions 分頁手動 Run workflow 一次。</p>';
    } else {
      html += '<p>已輪詢 ' + runs.length + ' 次，尚未偵測到今日影片。直播結束到 VOD 生成需要時間，' +
        '通常 13:00 到 14:00 之間才會抓到，屬正常等待。</p>';
    }
  }

  // 品質關卡的結果。
  //
  // 這裡刻意「不」再呼叫一次 digestAuditVerdict_。那一支是稽核的第一段，
  // 設計成高召回，寧可多列也不要漏，它的輸出本來就不該直接給人看——
  // 2026/08/24 那天它列出台塑、台化、陽明、大立光等七檔，實際上多半是
  // 講集團、講族群、拿來對照時順帶點名，不是他對那一檔的立場。
  // 把那份清單原樣寫進信裡，等於每天請人去追一批本來就不該收的東西。
  //
  // 改成讀品質關卡判定之後的結論：哪幾檔收了、哪幾檔為什麼不收、
  // 哪幾檔要人看。順便省下一次 Gemini 呼叫（狀態信一天寄三次）。
  var gate = (typeof gateState_ === 'function') ? gateState_() : null;
  if (gate && gate.date === today && (gate.audit || gate.auditStat)) {
    var gs = gate.auditStat || { added: 0, skipped: 0, pending: 0 };
    html += '<p><b>漏抓判定：</b>收 ' + (gs.added || 0) + ' 筆、不收 ' +
            (gs.skipped || 0) + ' 筆、待你確認 ' + (gs.pending || 0) + ' 筆</p>';
    var det = (gate.audit || []).filter(function (x) { return x.verdict !== '收'; });
    if (det.length) {
      html += '<ul>' + det.map(function (x) {
        return '<li>' + esc_(x.name) + '　' + esc_(x.verdict) + '：' + esc_(x.why) + '</li>';
      }).join('') + '</ul>';
      html += '<p class=\"m\">「不收」的不必處理，那是判定過的結論。' +
              '「待確認」的已寫進試算表的修正建議分頁。</p>';
    }
  } else if (video) {
    html += '<p>品質關卡尚未執行，稽核與複審的結果會在推播前產生。</p>';
  }

  // 同一天成功的狀態信只寄一次。
  //
  // 狀態信裝了三個觸發器（12:30、13:10、15:50），是為了在流程還沒完成時
  // 能持續回報進度。但一旦當天已經處理完成，後面兩次寄的內容幾乎一樣，
  // 收信的人一天就收到三封講同一件事的信，久了就不會再點開——
  // 真正需要注意的那一封也跟著被忽略，那才是最糟的結果。
  //
  // 規則：成功狀態一天只寄第一封；有異常則照舊每次都寄，
  // 因為異常需要盡快知道，而且內容會隨著處理進度改變。
  var props = PropertiesService.getScriptProperties();
  var okKey = 'STATUS_MAIL_OK_' + today;
  // 「成功」沿用上面顯示正常的完整條件：完成狀態、逐字稿內容有效，且至少
  // 有操作或持股資料。只看處理狀態會把空白輸出也誤認為成功並停止後續通知。
  var isOk = ok && !fails.length;
  var mail = {
    to: to.join(','),
    subject: '[狀態] ' + today + ' 爬取' + head,
    htmlBody: html
  };

  if (!isOk) {
    // 失敗、等待中與需要注意仍照舊：每個排程時點都寄，讓最新進度能持續回報。
    MailApp.sendEmail(mail);
    return;
  }

  // 成功信必須具備 exactly-once 的併發保護。舊流程先查旗標、寄信、再寫旗標，
  // 兩個觸發器重疊時可能同時查到空白而各寄一封。現在整段放進 ScriptLock；
  // 寄信若失敗不會寫旗標，下一個排程仍能重試。
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    if (props.getProperty(okKey)) {
      Logger.log(today + ' 已寄過成功狀態信，本次略過');
      return;
    }
    MailApp.sendEmail(mail);
    props.setProperty(okKey, Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss'));

    // 順手清掉七天前的旗標，免得指令碼屬性無限累積。
    var keep = {};
    for (var k = 0; k < 7; k++) {
      var d0 = new Date(); d0.setDate(d0.getDate() - k);
      keep['STATUS_MAIL_OK_' + Utilities.formatDate(d0, TZ, 'yyyy/MM/dd')] = 1;
    }
    var all = props.getProperties();
    Object.keys(all).forEach(function (key) {
      if (key.indexOf('STATUS_MAIL_OK_') === 0 && !keep[key]) {
        props.deleteProperty(key);
      }
    });
  } finally {
    lock.releaseLock();
  }
}

/* ------------------------------------------------------------------ *
 * AI 修正建議：提出、核准、套用
 * AI 不直接寫試算表。它把建議寫進「修正建議」分頁，
 * 由你確認後執行 applyAuditFixes() 才會真的動資料，全程留痕。
 * ------------------------------------------------------------------ */

/** 稽核發現漏抓時，把建議寫進「修正建議」分頁（狀態：待確認）。 */
function proposeAuditFixes(dateStr) {
  var d = fmtDate_(dateStr || todayStr_());
  var v = digestAuditVerdict_(d);
  if (!v || !v.missing || !v.missing.length) {
    Logger.log(d + ' 稽核沒有發現漏抓，不需要修正');
    return { proposed: 0 };
  }
  var now = new Date();
  var rows = v.missing.map(function (m) {
    var where = String(m.where || '觀望');
    var sheet = where.indexOf('持有') >= 0 ? '會員持股' : '操作紀錄';
    return [now, d, '新增', sheet, m.name || '', '',
            JSON.stringify({ where: where, evidence: m.evidence || '' }), '待確認', ''];
  });
  withLock_(function () {
    var sh = getSheet_('修正建議');
    sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
  });
  Logger.log(d + ' 已提出 ' + rows.length + ' 筆修正建議，請檢視「修正建議」分頁');
  return { proposed: rows.length };
}

/**
 * 套用「修正建議」分頁中狀態為「待確認」的列。
 * 你可以先在分頁裡把不同意的那幾列狀態改成「駁回」，再執行這一支。
 */
function applyAuditFixes() {
  var sh = getSheet_('修正建議');
  var values = sh.getDataRange().getValues();
  var applied = 0;

  for (var i = 1; i < values.length; i++) {
    var row = values[i];
    if (String(row[7]) !== '待確認') { continue; }

    var dateStr = fmtDate_(row[1]);
    var action = String(row[2]);
    var sheetName = String(row[3]);
    var name = String(row[4]);
    var code = String(row[5] || '');
    var payload = {};
    try { payload = JSON.parse(row[6] || '{}'); } catch (e) { payload = {}; }

    if (action === '新增' && sheetName === '操作紀錄') {
      var dir = String(payload.where || '').indexOf('買') >= 0 ? '買入'
              : String(payload.where || '').indexOf('賣') >= 0 ? '賣出'
              : String(payload.where || '').indexOf('注意') >= 0 ? '觀望注意' : '觀望不碰';
      // 欄位順序必須完全對齊：日期、股票名稱、代號、方向、價位說明、理由摘錄、來源影片ID
      getSheet_('操作紀錄').appendRow([dateStr, name, code, dir, '未說明',
                                       payload.evidence || '人工核准補登', '人工補登']);
      applied++;
    } else if (action === '新增' && sheetName === '會員持股') {
      // 欄位順序：日期、股票名稱、代號、目前立場、說明重點、來源影片ID
      getSheet_('會員持股').appendRow([dateStr, name, code, '未說明',
                                       payload.evidence || '人工核准補登', '人工補登']);
      applied++;
    } else {
      continue;
    }
    sh.getRange(i + 1, 8, 1, 2).setValues([['已套用', new Date()]]);
  }

  if (applied) {
    CACHE.remove('tracker');
    Logger.log('已套用 ' + applied + ' 筆修正，建議接著執行 rebuildHoldingsTrackerJob()');
  } else {
    Logger.log('沒有待確認的修正建議');
  }
  return { applied: applied };
}

/**
 * 稽核落差不再寄信。
 *
 * 這封信原本每天都來，內容是「逐字稿點名了這幾檔但表格裡沒有，請到 GitHub
 * 勾 fill_blanks 重跑，完成後執行 rebuildHoldingsTrackerJob()」。
 * 問題是那件事程式自己就做得到，卻寫成一份要人動手的指示，
 * 結果信天天來、事沒人做，隔天稽核再發現一次、再寄一次。
 *
 * 現在補漏已經是每日品質關卡的第一段（見本檔最下方），寄信之前就跑完了。
 * 這一支保留成只寫執行紀錄，方便事後回頭查那一天判到了什麼，
 * 也讓任何還指向它的舊程式碼不會壞掉。
 */
function alertDigestGap_(dateStr, verdict) {
  var list = (verdict && verdict.missing || []).map(function (m) {
    return m.name + '（' + m.where + '）';
  }).join('、');
  Logger.log('稽核落差 ' + dateStr + '：' + (list || '無') + '　（由品質關卡自動處理，不寄信）');
}

/* ------------------------------------------------------------------ *
 * 失敗告警
 *
 * 15:30 觸發，也就是 GitHub 輪詢全部結束之後。
 * 這個時間點很重要：輪詢期間狀態會是「等待中」，那是正常的，
 * 若在輪詢中間檢查，每天都會收到一封假告警。
 * ------------------------------------------------------------------ */

function failureAlertJob() {
  if (!isTradingDayToday_()) { return; }   // 週末不寄失敗告警
  var owner = PropertiesService.getScriptProperties().getProperty('OWNER_EMAIL')
              || statusEmails_().join(',');
  if (!owner) { return; }

  var today = todayStr_();
  var videos = readSheetObjects_('影片清單').filter(function (r) { return fmtDate_(r['發布日期']) === today; });

  var subject, body;

  if (!videos.length) {
    subject = '[' + today + '] 今日無新影片紀錄';
    body = '<p>試算表「影片清單」今天沒有任何紀錄，GitHub Actions 可能完全沒有執行。</p>' +
           '<p>請到 Actions 分頁確認排程是否被停用。儲存庫閒置 60 天以上，GitHub 會自動停用排程。</p>';
  } else {
    var v = videos[0];
    var status = String(v['處理狀態']);

    if (status === '完成') { return; }

    /* 人工投稿不寄告警。

       管理者是自己在後台按下送出的，那一頁上就有進度條、看進度、
       取消工單與錯誤訊息，他從頭到尾看得見。再寄一封「狀態：處理中，
       只是重複他已經知道的事。 */
    if (String(v['影片ID'] || '').indexOf('MANUAL-') === 0) { return; }

    /* 處理中不是失敗。

       15:30 這個時間點是照自動排程抓的，但人工在下午重跑一次工單很正常，
       這時狀態就是處理中。把一個正在正常進行的工作報成失敗，
       會讓真正的失敗變得沒有人看。 */
    if (status === '處理中') { return; }

    // 輪詢已經結束還停在等待中，代表 VOD 一直沒好，這才值得通知
    if (status === '等待中') {
      subject = '[' + today + '] 逐字稿始終未就緒';
      body = '<p>影片ID：' + v['影片ID'] + '</p>' +
             '<p>標題：' + v['標題'] + '</p>' +
             '<p>排程從 11:20 敲到收工都沒有取得逐字稿。</p>' +
             '<p>最後一次的訊息：' + (v['失敗原因'] || '未記錄') + '</p>' +
             '<p>可能原因：直播還沒結束、YouTube 回放還在處理，或 Gemini 額度用完。</p>' +
             '<p>影片網址：https://www.youtube.com/watch?v=' + v['影片ID'] + '</p>' +
             '<p>想手動補跑：Actions → Run workflow → final 填 true（會使用 30 分鐘長逾時）。</p>';
    } else if (status === '今日無影片') {
      return;
    } else {
      subject = '[' + today + '] 資料處理狀態：' + status;
      body = '<p>影片ID：' + v['影片ID'] + '</p>' +
             '<p>標題：' + v['標題'] + '</p>' +
             '<p>狀態：' + status + '</p>' +
             '<p>失敗原因：' + (v['失敗原因'] || '未記錄') + '</p>' +
             '<p>影片網址：https://www.youtube.com/watch?v=' + v['影片ID'] + '</p>' +
             '<p>到後台的「投稿逐字稿」按「看進度」可以看到卡在哪一步與實際錯誤訊息。</p>';
    }
  }

  MailApp.sendEmail({ to: owner, subject: subject, htmlBody: mailShell_(body) });
}

/* ------------------------------------------------------------------ *
 * 郵件外框
 * ------------------------------------------------------------------ */

/* 郵件外框。

   先前是 max-width:680px + margin:0 auto，也就是一條置中的窄欄。
   在手機上剛好，但在桌機的收件匣裡兩側各留了一大片空白，
   而這封信最重要的內容是表格——表格被壓在 680px 裡，欄位就得換行，
   換行之後每一列高度不一，一整張表看起來就散掉了。

   現在改成滿版，寬度交給收件匣自己決定（Gmail 本來就有自己的外距）。 */
/* 信件外框（2026/09/15 版面重整）。

   Email 不是網頁：有些收件匣或轉寄會拿掉 <style>（Gmail 支援部分 <style> 與 media query，但不能全靠它）、Outlook 不認 flex 與 grid，
   所以版面一律用 table 排、樣式一律寫在 inline style。
   內容區最寬 680px（電腦讀起來不吃力），手機上自然縮到螢幕寬，左右各留 8px。
   底色用與網站相同的灰綠（#E8EAE7），內容一塊塊放在白底圓角卡片裡；
   Gmail 深色模式會自動反轉這組淺色，文字與底色的對比仍然足夠。 */
/* 字體名稱用單引號。這一串是寫進 style="..." 裡的，先前用雙引號 —— 屬性值在第一個
   "PingFang TC" 的引號就結束了，後面的 font-size、line-height、color 全部變成無效屬性，
   整封信的基準字體與字級其實從來沒有生效過，各家收件匣都用自己的預設字體在排（2026/09/16）。
   同樣的寫法也讓代號欄的 style 整段壞掉，Gmail 深色模式下那一格就掉了底色，變成一塊黑方塊。 */
var MAIL_FONT_ = "-apple-system,BlinkMacSystemFont,'PingFang TC','Noto Sans TC','Microsoft JhengHei',sans-serif";
/* 代號欄先前用等寬字體堆疊（"SF Mono","IBM Plex Mono",…）。收件匣會重寫 inline style，
   帶引號的字體名是整份信裡唯一長這樣的宣告，而 Gmail 深色模式下也只有這一格掉了底色，
   露出卡片本身的底 —— 手機上就是代號那一塊變成黑（淺色模式下則是白）方塊（2026/09/16）。
   代號是四到六位數字，本來就不需要等寬字體；改用與全信相同的字體，並把顏色寫明。 */
var MAIL_CODE_COLOR_ = '#3E4944';

function mailShell_(inner) {
  return '<div class="mail-shell" style="margin:0;padding:0;background:#E8EAE7;">' +
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" ' +
      'style="width:100%;border-collapse:collapse;background:#E8EAE7;">' +
    '<tr><td align="center" style="padding:10px 6px 18px;">' +
      '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" ' +
        'style="width:100%;max-width:680px;border-collapse:collapse;">' +
      '<tr><td class="mail-col" style="font-family:' + MAIL_FONT_ + ';font-size:14px;line-height:1.7;' +
        'letter-spacing:0.01em;color:#12161A;text-align:left;">' + inner + '</td></tr>' +
      '</table>' +
    '</td></tr></table></div>';
}

/* 風險揭露與重要提醒（2026/09/17 v44，管理者要求）。

   原本文章第 ④ 章寫一次、信尾「不想再收到這封信？」上面的免責聲明又寫一次，兩張卡片講同一件事。
   現在只留一處：放在信尾，退訂說明的正上方。文章不再有 ④（pipeline 與 Articlequality 都不寫），
   試算表裡的舊文章在 normalizeArticleSections_ 呈現時把那一章拿掉。
   網站「郵件查詢」沒有信尾，getMailContent 在文章後面接同一段，讀起來與信一致。 */
var MAIL_RISK_LINES_ = [
  '本信內容僅為整理節目中之公開資訊與觀點，不構成任何形式之投資建議或獲利保證。',
  '實際投資操作須自行評估風險與財務狀況，必要時請諮詢專業投資顧問。'
];
function mailRiskHtml_() {
  return '<div class="mc-risk" style="margin:0 0 12px;">' +
      '<div class="mc-risk-h" style="font-size:13px;font-weight:700;color:#12161A;margin:0 0 4px;">風險揭露與重要提醒</div>' +
      MAIL_RISK_LINES_.map(function (line) {
        return '<div class="mc-risk-li" style="font-size:12px;line-height:1.7;color:#667069;margin:0;">• ' + line + '</div>';
      }).join('') +
    '</div>';
}

/* 信尾：風險揭露與取消訂閱放在同一張圓角卡片裡。
   退訂難找的信會被檢舉為垃圾信、傷到整個寄件網域，所以按鈕做成按鈕的樣子，手指點得到。 */
/* 純文字版本（v54，Codex 規格 85）：只看文字的收件軟體、螢幕閱讀器與垃圾信判斷都會讀它。
   由同一份 HTML 轉出來，不另外寫一份，內容不會兩邊不一致。連結保留成「文字（網址）」。 */
function mailPlainText_(html) {
  var t = String(html || '')
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
    .replace(/<a\b[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/gi, function (_, href, txt) {
      var label = txt.replace(/<[^>]+>/g, '').trim();
      return label && href && href.indexOf('mailto:') !== 0 ? label + '（' + href + '）' : label;
    })
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/(p|div|h[1-6]|li|tr|table|section)>/gi, '\n')
    .replace(/<(li)\b[^>]*>/gi, '・')
    .replace(/<\/t[dh]>/gi, '　')
    .replace(/<[^>]+>/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  return t.split('\n').map(function (l) { return l.replace(/[ \t　]+$/g, '').replace(/^[ \t]+/, ''); })
    .join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/* 預覽摘要（preheader）：收件匣列表在主旨旁邊顯示的那一行。不放它時收件匣會抓信的第一段字（品牌列），沒有資訊量。
   隱藏的 div 不寫 font-size，網站郵件查詢的字級規則（v22）不必為它另開一條。 */
function mailPreheader_(text) {
  var t = String(text || '').replace(/\s+/g, ' ').trim().slice(0, 90);
  return t ? '<div class="mc-pre" style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;">' + esc_(t) + '</div>' : '';
}

function wrapMail_(inner, email, token, kind, pre) {
  var head =
    '<div class="mc-brand" style="padding:2px 4px 10px;font-size:12px;letter-spacing:0.14em;' +
         'font-weight:700;color:#4A5A52;">張震 股市盤中家教班</div>';
  var unsub = unsubscribeUrl_(email, token, kind);
  var foot =
    '<div class="mc-sec mc-foot" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;' +
         'padding:14px 16px;margin:12px 0 0;">' +
      mailRiskHtml_() +
      '<div style="border-top:1px solid #E4E8E6;margin:0 0 12px;"></div>' +
      '<div style="font-size:13px;font-weight:700;color:#12161A;margin:0 0 2px;">不想再收到這封信？</div>' +
      '<div style="font-size:12px;line-height:1.7;color:#667069;margin:0 0 10px;">' +
        (kind === 'daily' || kind === 'sms'
          ? '按下後會開一張確認頁：可以只停止這一類（' + UNSUB_SCOPE_LABEL_[kind] + '），或停止所有通知。不需要登入。'
          : '按下後會開一張確認頁，確認後停止所有通知。不需要登入。') + '</div>' +
      (unsub
        ? '<a href="' + unsub + '" target="_blank" rel="noopener noreferrer" ' +
           'style="display:inline-block;border:1px solid #C3CBC6;border-radius:999px;background:#FFFFFF;' +
           'padding:12px 20px;min-height:44px;box-sizing:border-box;font-size:14px;color:#12161A;text-decoration:none;font-weight:600;cursor:pointer;-webkit-text-size-adjust:none;touch-action:manipulation;">取消訂閱</a>'
        : '<div style="font-size:12px;color:#8A6410;">退訂連結暫時無法產生，請回覆這封信告知要停止哪一種通知。</div>') +
    '</div>';
  return mailShell_(mailPreheader_(pre) + head + inner + foot);
}

/* 表格底色。

   買、賣、持有、觀望不碰、觀望注意在信裡原本全是同一種灰白，
   要逐格讀完字才知道這一列是哪一類——而那正是這封信最該一眼看出來的事。

   顏色寫死在 inline style 裡，因為收件匣一律把 <style> 整段丟掉；
   同時掛上 class，網站的深色模式再用 CSS 蓋回去（見 Stylesheet 的 .tn-*）。
   bar 是第一格的左側色條。塗滿的淺底色在小螢幕上辨識度有限，
   一條實心的色條才是遠看就分得出來的那個東西。 */
/* 紅漲綠跌，不是綠買紅賣。

   這一組顏色原本是照西方股市的習慣配的：買綠、賣紅。台股是相反的——
   紅是漲、綠是跌，而且網站上一直都是這樣（Stylesheet 的 --rise 是紅、
   --fall 是綠，.dir-buy 用紅、.dir-sell 用綠）。
   於是同一筆資料在網站上是紅的、在信裡是綠的，兩邊互相打架，
   而讀的人不會去想「這封信是不是換了一套配色」，只會看錯方向。
   現在買入配紅、賣出配綠，色碼直接對齊網站的 --rise / --fall。 */
var MAIL_TONES_ = {
  buy:   { key: 'buy',   bg: '#FBDFDF', hbg: '#F5C4C4', line: '#EBAAAA', bar: '#C41E28' },
  sell:  { key: 'sell',  bg: '#DCF0E4', hbg: '#BEE2CC', line: '#9ED3B0', bar: '#04795C' },
  hold:  { key: 'hold',  bg: '#DEEAFA', hbg: '#C3D9F4', line: '#A8C6EC', bar: '#1F5296' },
  watch: { key: 'watch', bg: '#FCEDCD', hbg: '#F7DDA5', line: '#EACF8C', bar: '#9C6A0F' },
  avoid: { key: 'avoid', bg: '#E8ECEA', hbg: '#D8DEDA', line: '#C4CCC7', bar: '#5F6E67' }
};

/** 這段文字屬於哪一類。判不出來、或同時包含兩類，就回 null 交給逐列判定。 */
function toneOf_(text) {
  var t = String(text || '');
  if (t.indexOf('觀望不碰') >= 0 && t.indexOf('觀望注意') >= 0) { return null; }
  if (t.indexOf('觀望不碰') >= 0) { return MAIL_TONES_.avoid; }
  if (t.indexOf('觀望注意') >= 0) { return MAIL_TONES_.watch; }
  // ②-1 的標題同時有買入與賣出，那張表是混的，只能逐列判。
  var buy = t.indexOf('買入') >= 0 || t.indexOf('買進') >= 0;
  var sell = t.indexOf('賣出') >= 0;
  if (buy && sell) { return null; }
  if (sell) { return MAIL_TONES_.sell; }
  if (buy) { return MAIL_TONES_.buy; }
  if (t.indexOf('持有') >= 0 || t.indexOf('持股') >= 0) { return MAIL_TONES_.hold; }
  return null;
}

/* 表頭去括號。

   「目前立場（續抱／加碼觀察／分批調節等）」「觀望類型（觀望不碰／觀望注意）」——
   括號裡那一串是寫給模型看的欄位說明，不是要給讀者看的。
   它讓表頭長到一定得換行，一整排表頭的高度就跟著散掉。 */
function cleanHead_(c) {
  var t = String(c).trim();
  var cut = t.replace(/[（(][^（()）]*[）)]\s*$/, '').trim();
  return cut || t;
}

/* 章節標題的語氣說明，和網站每日總覽的表頭說明相同。 */
var MAIL_TONE_LABEL_ = { avoid: '語氣偏空，暫不進場', watch: '語氣偏多，留意追蹤' };

/* 章節編號（2026/09/15 v22，管理者要求）：標題不寫編號、拿掉「基本資訊」、盤勢總覽起算 ①。

   新文章已經是「文章標題：…」＋ ① 盤勢總覽 ② 會員操作紀錄（②-1～②-3）③ 教學。
   舊文章的「風險揭露」一章（④ 或 ⑥）呈現時拿掉，改在信尾呈現一次（2026/09/17 v44）。
   試算表裡的舊文章仍是 ① 文章標題 ② 基本資訊 ③～⑥，呈現時在這裡換成新結構，
   網站郵件查詢翻到舊日期也一致，不必逐天重寫。

   換號依據是章名，不是把數字往前平移兩格：舊文章若少了一章或順序亂掉，
   平移會把章名配錯號；照章名配，最壞情況只是某一章沒有換號。
   子節跟著它所屬的那一章換（④-1 → ②-1），只換在那一章標題之後出現的子節。 */
var ARTICLE_SECTION_NO_ = [
  [/^盤勢總覽/, '①'], [/^會員操作紀錄/, '②'], [/^分析師操作邏輯/, '③']
];
function normalizeArticleSections_(md) {
  var out = [], skip = false, sub = {};
  String(md).split('\n').forEach(function (raw) {
    var line = raw.trim().replace(/^#{1,6}\s*/, '').replace(/^\*\*(.*?)\*\*$/, '$1').trim();
    if (/^\|/.test(line)) { if (!skip) { out.push(raw); } return; }

    var title = line.match(/^(?:[①②③④⑤⑥]\s*)?文章標題\s*[：:]\s*(.*)$/);
    if (title) { skip = false; out.push('文章標題：' + title[1]); return; }

    var head = line.match(/^([①②③④⑤⑥])(?![-－])\s*(.*)$/);
    if (head) {
      skip = false;
      var name = head[2];
      if (/^文章標題\s*$/.test(name)) { return; }                 // 模型版的「① 文章標題」空章名，下一行才是標題
      if (/^基本資訊/.test(name)) { skip = true; return; }         // 整章拿掉，到下一章為止
      if (/^風險揭露/.test(name)) { skip = true; return; }         // v44：改在信尾呈現一次（mailRiskHtml_）
      for (var i = 0; i < ARTICLE_SECTION_NO_.length; i++) {
        if (ARTICLE_SECTION_NO_[i][0].test(name)) {
          sub = {}; sub[head[1]] = ARTICLE_SECTION_NO_[i][1];
          out.push(ARTICLE_SECTION_NO_[i][1] + ' ' + name);
          return;
        }
      }
      sub = {};
      out.push(line);
      return;
    }
    if (skip) { return; }

    var child = line.match(/^([①②③④⑤⑥])([-－]\d[\s\S]*)$/);
    if (child && sub[child[1]]) { out.push(sub[child[1]] + child[2]); return; }
    out.push(raw);
  });
  return out.join('\n');
}

/** 極簡 Markdown 轉 HTML，只處理標題、表格、清單、段落。

    版面（2026/09/15）：
      文章標題　　深色標題卡，不寫編號、不加「張震：」前綴（管理者要求）。
      ①②③④　　每一章是一張白底圓角卡片，章名前一條色條（舊文章先經 normalizeArticleSections_ 換號）。
      股票表格　改成「疊列表格」：第一列是 股票名稱｜代號｜價位，說明另起一列橫跨整張表。
                四欄並排時說明欄在手機上被擠成兩三個字一行、整張表要兩指往右拖才看得完；
                疊列之後手機不必左右滑動，而且每一張表都用同一組欄寬，持股、觀望各表左右對齊。
      字級　　　內文 14px、表格 13.5px、章名 16px，篇幅比先前短約三分之一。
      網站　　　每個元素掛 class（mc-name、mc-code、mc-mid、mc-desc、mc-chip⋯），
                郵件查詢分頁用 CSS 乘上版面設定的字體大小／字距／行距（見 Stylesheet）。 */
function mdToHtml_(md) {
  // Transcript-derived text is content, never executable HTML. Escaping before
  // parsing still preserves Markdown delimiters and numeric comparisons such as <900.
  var lines = normalizeArticleSections_(md).replace(/[&<>"']/g, function (ch) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];
  }).split('\n');
  var html = [], inTable = false, inSection = false, sectionNo = '';
  var lastHead = '';   // 表格前一行的文字，用來判定整張表屬於哪一類
  var tone = null;     // 這張表的底色
  var drops = [];      // 要整欄拿掉的欄位索引（由大到小，才能安全地逐一 splice）
  var dirCol = -1;     // 方向欄，逐列覆寫底色時優先看它
  var heads = null;    // 這張表的表頭（已經去掉括號與常數欄）
  var body = [];       // 這張表的資料列，收齊之後才輸出

  function dropCols(arr) {
    for (var i = 0; i < drops.length; i++) {
      if (arr.length > drops[i]) { arr.splice(drops[i], 1); }
    }
    return arr;
  }

  function openSection(no) {
    closeSection();
    sectionNo = no;
    inSection = true;
    html.push('<div class="mc-sec" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;' +
              'padding:14px 12px 8px;margin:0 0 10px;">');
  }
  function closeSection() {
    if (!inSection) { return; }
    closeTable();
    html.push('</div>');
    inSection = false;
  }

  function chip(text, t) {
    return '<span class="mc-chip tn-' + (t ? t.key : 'none') + '" style="display:inline-block;white-space:nowrap;' +
           'border-radius:999px;padding:0 8px;font-size:12px;line-height:20px;font-weight:700;' +
           'background:' + (t ? t.hbg : '#E4E8E5') + ';color:' + (t ? t.bar : '#3E4944') + ';">' + text + '</span>';
  }

  /* 整張表收齊之後才輸出（要合併同一檔就得先看得到後面的列）。 */
  function closeTable() {
    if (!inTable) { return; }
    inTable = false;

    var rows = mergeDupRows_(body, tone);
    var edge = tone ? tone.line : '#D6DCD8';
    var hbg = tone ? tone.hbg : '#E4E8E5';
    var hcls = 'tn-h ' + (tone ? 'tn-' + tone.key : 'tn-none');

    // 欄位角色：第一欄名稱、代號欄、最後一欄說明，其餘（方向、價位）併進第三格。
    var codeIdx = -1;
    heads.forEach(function (h, i) { if (codeIdx < 0 && /代號/.test(h)) { codeIdx = i; } });
    if (codeIdx < 0 && heads.length > 2) { codeIdx = 1; }
    var descIdx = heads.length - 1;
    var mids = [];
    heads.forEach(function (h, i) { if (i !== 0 && i !== codeIdx && i !== descIdx) { mids.push(i); } });
    var midHead = mids.length ? mids.map(function (i) { return /價位|成本/.test(heads[i]) ? '價位' : heads[i]; }).join('／')
                              : (tone && tone.key === 'hold' ? '狀態' : '價位');

    var th = function (text) {
      return '<th bgcolor="' + hbg + '" class="' + hcls + ' mc-th" style="background:' + hbg + ';padding:7px 12px;text-align:left;' +
             'font-size:12px;line-height:1.5;font-weight:600;color:#3E4944;white-space:nowrap;">' + text + '</th>';
    };
    /* 觀望兩表不再有價位欄（v54，管理者 2026/09/23）：「觀望不碰、觀望注意的價位說明呈現出來看不出重點」，
       而且九成是「未說明」。改成兩欄——左邊名稱與代號上下兩行，右邊整格給說明；
       真的有已證實價位的，在說明最前面放一個「價位 597」小標籤。買入／賣出／持股表保留價位欄。 */
    var watchLayout = !!(tone && (tone.key === 'watch' || tone.key === 'avoid')) && dirCol < 0;
    html.push('<table class="mc-table" role="presentation" width="100%" cellpadding="0" cellspacing="0" ' +
              'style="width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0;margin:6px 0 12px;' +
              'border:1px solid ' + edge + ';border-radius:12px;overflow:hidden;font-family:' + MAIL_FONT_ +
              ';font-size:13.5px;line-height:1.7;">' +
              /* 名稱欄收窄到約四個字（v54）：手機寬 360px 時 30% 約 100px。說明另起一列橫跨，不跟名稱搶寬度。 */
              (watchLayout
                ? '<colgroup><col style="width:26%"><col style="width:74%"></colgroup>' +
                  '<thead><tr>' + th(heads[0] || '股票名稱') + th(heads[descIdx] || '說明重點') + '</tr></thead><tbody>'
                : '<colgroup><col style="width:30%"><col style="width:22%"><col style="width:48%"></colgroup>' +
                  '<thead><tr>' + th(heads[0] || '股票名稱') + th('代號') + th(midHead) + '</tr></thead><tbody>'));

    rows.forEach(function (cells) {
      var rt = rowTone(cells);
      var bg = rt ? rt.bg : '#FFFFFF';
      var ln = rt ? rt.line : '#E4E8E6';
      var bar = rt ? rt.bar : '#C3CBC6';
      var cls = rt ? 'tn-' + rt.key : 'tn-none';
      var cell = function (inner, extra, role) {
        // 底色同時寫成 bgcolor 屬性：收件匣改寫或丟掉 style 時格子仍有底色，不會露出卡片的底。
        return '<td bgcolor="' + bg + '" class="' + cls + ' ' + role + '" style="background:' + bg + ';padding:9px 12px 2px;border-top:1px solid ' + ln + ';' +
               'vertical-align:top;' + (extra || '') + '">' + inner + '</td>';
      };
      var vals = mids.map(function (i) { return { i: i, v: String(cells[i] || '').trim() }; })
        .filter(function (x) { return x.v; });
      var mid = vals.map(function (x) {
        if (x.i === dirCol) { return chip(x.v, toneOf_(x.v)); }
        // 和方向標籤排在同一格時，短價位（271以上、1580以下）包一層不換行；只有一個值時交給整格的 nowrap。
        return vals.length > 1 && x.v.replace(/&[a-z]+;|&#\d+;/g, 'x').length <= 10
          ? '<span style="white-space:nowrap;">' + x.v + '</span>' : x.v;
      }).join(' ');
      if (!mids.length) { mid = rt && rt.key === 'hold' ? chip('持有', rt) : ''; }
      if (watchLayout) {
        var px = vals.map(function (x) { return x.v; }).join(' ');
        var wdesc = descIdx > 0 ? stripMetaClauses_(String(cells[descIdx] || '')) : '';
        var pxTag = px && !/^未說明$/.test(px) ? chip('價位 ' + px, rt || tone) + ' ' : '';
        html.push('<tr>' +
          '<td bgcolor="' + bg + '" class="' + cls + ' mc-name" style="background:' + bg + ';padding:10px 10px 11px 12px;border-top:1px solid ' + ln + ';' +
            'border-left:4px solid ' + bar + ';vertical-align:top;font-weight:700;font-size:15px;line-height:1.5;color:#12161A;overflow-wrap:anywhere;">' +
            (cells[0] || '') +
            (codeIdx >= 0 && cells[codeIdx] ? '<br><span class="mc-code" style="font-weight:400;font-size:13.5px;letter-spacing:0.04em;color:' +
              MAIL_CODE_COLOR_ + ';white-space:nowrap;">' + cells[codeIdx] + '</span>' : '') + '</td>' +
          '<td bgcolor="' + bg + '" class="' + cls + ' mc-desc mc-side" style="background:' + bg + ';padding:10px 12px 11px;border-top:1px solid ' + ln + ';' +
            'vertical-align:top;font-size:13.5px;line-height:1.75;color:#26312C;overflow-wrap:anywhere;">' + pxTag + (wdesc || '未說明') + '</td>' +
          '</tr>');
        return;
      }
      var plain = mid.replace(/<[^>]+>/g, '');
      var midStyle = plain.length <= 8 && mid.indexOf('mc-chip') < 0
        ? 'white-space:nowrap;word-break:normal;overflow-wrap:normal;' : 'overflow-wrap:anywhere;';
      html.push('<tr>' +
        cell(cells[0] || '', 'border-left:4px solid ' + bar + ';font-weight:700;font-size:15px;line-height:1.5;color:#12161A;overflow-wrap:anywhere;', 'mc-name') +
        cell(codeIdx >= 0 ? (cells[codeIdx] || '') : '', 'font-size:13.5px;letter-spacing:0.04em;color:' +
             MAIL_CODE_COLOR_ + ';white-space:nowrap;', 'mc-code') +
        cell(mid || '—', 'font-size:13.5px;color:#26312C;' + midStyle, 'mc-mid') +
        '</tr>');
      // 說明欄再過一次內部判斷字眼的過濾：已經寫進試算表的舊文章（例如 2026/09/15 台積電那句
      // 「並未將台積電列為當日會員實際買進或持有的個股明細，故列入市場教學與觀察範疇」）
      // 不必重寫也能乾淨呈現。新資料在 pipeline 寫入時就已經清過。
      var desc = descIdx > 0 ? stripMetaClauses_(String(cells[descIdx] || '')) : '';
      if (desc) {
        html.push('<tr><td colspan="3" bgcolor="' + bg + '" class="' + cls + ' mc-desc" style="background:' + bg + ';padding:2px 12px 12px;' +
                  'border-left:4px solid ' + bar + ';vertical-align:top;font-size:13.5px;line-height:1.75;color:#26312C;' +
                  'overflow-wrap:anywhere;">' + desc + '</td></tr>');
      }
    });

    html.push('</tbody></table>');
    heads = null; body = [];
  }

  /* 這一列的底色。方向欄優先；沒有方向欄就找哪一格剛好就是類別字眼；都判不出來才用整張表的底色。 */
  function rowTone(cells) {
    if (dirCol >= 0 && cells[dirCol]) {
      var t = toneOf_(cells[dirCol]);
      if (t) { return t; }
    }
    for (var i = 0; i < cells.length; i++) {
      var c = String(cells[i]).trim();
      if (c === '買入' || c === '賣出' || c === '觀望不碰' || c === '觀望注意') {
        return toneOf_(c);
      }
    }
    return tone;
  }

  lines.forEach(function (raw) {
    var line = raw.trim();
    if (!line) { closeTable(); return; }

    if (/^\|/.test(line)) {
      if (/^\|[\s\-|:]+\|$/.test(line)) { return; }
      var cells = line.split('|').slice(1, -1).map(function (c) { return c.trim(); });

      if (!inTable) {
        heads = cells.map(cleanHead_);
        /* 「觀望類型」「目前立場」「動作類型」每一列都填同一個字，整欄拿掉；類別改用整列底色表示。 */
        drops = [];
        ['觀望類型', '目前立場', '動作類型'].forEach(function (h) {
          var i = heads.indexOf(h);
          if (i >= 0) { drops.push(i); }
        });
        drops.sort(function (a, b) { return b - a; });
        dirCol = heads.indexOf('方向');
        drops.forEach(function (i) { if (dirCol > i) { dirCol--; } });
        dropCols(heads);
        tone = toneOf_(lastHead);
        body = [];
        inTable = true;
      } else {
        body.push(dropCols(cells.slice()));
      }
      return;
    }

    closeTable();
    lastHead = line;

    // 文章標題：深色標題卡，不寫編號。舊文章裡的「張震：」前綴在呈現時一併拿掉。
    var title = line.match(/^文章標題\s*[：:]\s*(.*)$/);
    if (title) {
      closeSection();
      var t = title[1].replace(/^(?:張震|張正)\s*[：:]\s*/, '');
      // text-wrap:balance：支援的收件匣會把標題平均折行，最後一個字不會單獨掉到下一行（v54，Codex 規格 84）；不支援的照舊，不硬截。
      html.push('<div class="mc-hero" style="background:#17322A;border-radius:14px;padding:16px 18px;margin:0 0 12px;">' +
                '<div class="mc-hero-k" style="font-size:11px;letter-spacing:0.18em;font-weight:700;color:#9CC8B4;">每日整理</div>' +
                '<h1 style="font-size:19px;line-height:1.5;margin:6px 0 0;font-weight:700;color:#FFFFFF;text-wrap:balance;">' + t + '</h1></div>');
      return;
    }

    // The canonical article uses circled section numbers instead of Markdown #.
    if (/^[①②③④⑤⑥](?:\s|$)/.test(line)) {
      openSection(line.charAt(0));
      html.push('<h2 class="mc-h2" style="font-size:16px;line-height:1.45;margin:0 0 8px;font-weight:700;color:#12161A;">' +
                '<span style="display:inline-block;width:4px;height:15px;border-radius:2px;background:#04795C;' +
                'margin-right:8px;vertical-align:-2px;"></span>' + line + '</h2>');
      return;
    }
    if (/^[①②③④⑤⑥][-－]\d/.test(line)) {
      html.push('<h3 class="mc-h3" style="font-size:14.5px;line-height:1.5;margin:14px 0 6px;font-weight:700;color:#12161A;">' +
                line + '</h3>');
      return;
    }

    var m = line.match(/^(#{1,4})\s+(.*)$/);
    if (m) {
      var lv = Math.min(4, m[1].length + 1);
      lastHead = m[2];
      html.push('<h' + lv + ' class="' + (lv <= 2 ? 'mc-h2' : 'mc-h3') + '" style="font-size:' + (lv <= 2 ? 16 : 14.5) + 'px;line-height:1.5;margin:14px 0 6px;font-weight:700;">' +
                m[2] + '</h' + lv + '>');
      return;
    }

    // 「觀望不碰」「觀望注意」這種單獨一行的類別名：做成色塊標籤，接下來那張表就是這一類。
    var toneLine = toneOf_(line);
    if (toneLine && line.length <= 6) {
      html.push('<div class="mc-tone" style="margin:12px 0 4px;">' + chip(line, toneLine) +
                (MAIL_TONE_LABEL_[toneLine.key] ? '<span class="mc-tone-k" style="font-size:12px;color:#667069;margin-left:8px;">' +
                  MAIL_TONE_LABEL_[toneLine.key] + '</span>' : '') + '</div>');
      return;
    }

    if (/^[-•・]\s*/.test(line) && !/^[-•・]\s*$/.test(line)) {
      var text = stripConceptParens_(line.replace(/^[-•・]\s*/, ''));
      // 「觀念標題：說明」「節目名稱：……」把冒號前的標籤加粗，掃讀時一眼抓到重點。
      var kv = text.match(/^([^：:。，,]{2,16})[：:]\s*([\s\S]+)$/);
      if (kv) { text = '<b style="color:#12161A;">' + kv[1] + '</b>：' + kv[2]; }
      html.push('<div class="mail-bullet" style="margin:9px 0;padding-left:14px;text-indent:-12px;font-size:14px;' +
                'line-height:1.75;color:#26312C;"><span style="color:#04795C;font-weight:700;">•</span> ' + text + '</div>');
      return;
    }
    html.push('<p class="mc-p" style="margin:9px 0;font-size:14px;line-height:1.75;color:#26312C;">' + stripConceptParens_(line) + '</p>');
  });

  closeTable();
  closeSection();
  return html.join('\n');
}

/* 已經寫進試算表的文章裡，同一檔重複的那幾列在呈現時就併起來。

   規則改好之後只對之後新產生的內容有效，而郵件內容是撰稿當下存起來的一份文字，
   不會跟著試算表變。過去幾個月的文章裡那些重複列，只有在這裡處理才看得到效果。

   只動觀望與持股那幾張表：那三類「他那一天對這一檔的立場」只有一個，
   同一檔出現兩列一定是重複。買賣不動——同一天分批買、分批賣是真的會發生，
   兩列各有各的價位，併掉會把「他做了兩次」抹平成一次。

   鑰匙是前兩格（股票名稱、股票代號）。不能拿整列當鑰匙：重複的那兩列
   正是因為說明不同才會被留成兩列，比整列永遠比不出它們是同一檔。 */
function mergeDupRows_(rows, tone) {
  if (!rows.length) { return rows; }
  if (!tone || ['hold', 'watch', 'avoid'].indexOf(tone.key) < 0) { return rows; }

  var out = [], index = {};
  rows.forEach(function (cells) {
    var k = String(cells[0] || '').trim() + '|' + String(cells[1] || '').trim();
    if (!k.replace('|', '')) { out.push(cells); return; }

    var hit = index[k];
    if (!hit) { index[k] = cells; out.push(cells); return; }

    // 從第三格起逐格併。最後一格是說明，其餘多半是價位這種短欄位。
    for (var i = 2; i < cells.length; i++) {
      hit[i] = (i === cells.length - 1)
        ? mergeText_(hit[i], cells[i])
        : mergePrice_(hit[i], cells[i]);
    }
  });
  return out;
}

/* mergeText_ 與 mergePrice_ 定義在 Adminservice.gs。Apps Script 的檔案共用
   同一個全域範圍，所以這裡直接叫得到，不必再抄一份——抄一份的東西遲早會長歪，
   而資料層併起來的句子與呈現層併起來的句子必須一模一樣，否則同一天的內容
   會因為「有沒有重跑過全面重整」而長得不一樣。 */


/* 「觀念一：（避開高基期與技術弱勢股）」→「觀念一：避開高基期與技術弱勢股」

   那一對括號是提示語裡的佔位符號被照抄出來的，不是內容的一部分。
   規則刻意寫得很窄：整段剛好被一對括號從頭包到尾才拿掉，
   句子中間本來就有括號的（例如「跌破季線（觸發程式化賣單）」）完全不動。

   放在這裡而不是只改提示語，是因為已經寫進試算表的舊文章也要跟著變乾淨。 */
function stripConceptParens_(line) {
  return String(line).replace(
    /^(\s*(?:\d+[.、)]\s*)?觀念[一二三四五六七八九十百零\d]+\s*[：:]\s*)[（(]\s*(.+?)\s*[）)]\s*$/,
    '$1$2');
}


/* ==================================================================== *
 * 漏抓判定：這一檔到底該不該進表格（等於該不該出現在網站上）
 *
 * 為什麼需要一道獨立的判定
 * ------------------------
 * 前面那道稽核是「找候選」，它刻意設計成高召回：寧可多列，不要漏。
 * 那份清單本來就不該直接給人看，也不該直接寫進表格。
 *
 * 2026/08/24 那天它列出台塑、台化、陽明、日電貿、所羅門、大立光、新象，
 * 七檔全部標成觀望不碰。逐字稿實際上多半是這幾種情況：
 *   講「台塑集團怎麼樣」時順帶念到台塑、台化的名字
 *   講航運族群時拿陽明當例子
 *   拿大立光當高價股的對照
 *   所羅門是回顧早就出清的舊單
 * 這些都不是「他今天對這一檔的立場」，補進去只會讓網站顯示不存在的操作。
 *
 * 所以這一關要回答的是一個很窄的問題：
 *   逐字稿裡，他對這一檔有沒有給出「今天當下、可以照著做」的立場？
 * 有才收，沒有就不收。拿不準的不猜，留給人看。
 * ==================================================================== */

var AUDIT_FIX_SYSTEM =
  '你是漏抓判定員。稽核程式列出了一批「可能被漏掉的股票」，那份清單刻意寧可多列，\n' +
  '所以裡面有不少根本不該收。你的工作是逐筆判定：這一檔該不該進表格。\n' +
  '進表格就等於出現在網站與推播信上，所以判錯會讓讀者看到不存在的操作。\n' +
  '\n' +
  '第一步，判斷這個名稱是不是「在台灣上市或上櫃的單一公司」。\n' +
  '不是的話（產業、族群、概念、題材、指數、集團、外國公司、聽錯的碎片），\n' +
  'is_stock 回 false，其餘欄位留空。\n' +
  '\n' +
  '第二步，判斷該不該收，填 should_publish。\n' +
  '\n' +
  '唯一的收錄標準：逐字稿裡他對「這一檔」給出了今天當下的、可以照著做的立場。\n' +
  '被提到不等於有立場。下面六種情形一律 should_publish=false，\n' +
  '並在 why_not 填對應的代號：\n' +
  '\n' +
  '  group    集團順帶點名。他講的是「台塑集團」「鴻海集團」這種整體，\n' +
  '           過程中念到成分股的名字，但沒有單獨對那一檔說要怎麼做。\n' +
  '  sector   族群舉例。他講「航運」「記憶體」「重電」時拿某幾檔當例子，\n' +
  '           重點是整個族群的方向，不是那一檔的個別立場。\n' +
  '  compare  對照或比喻。拿某一檔的價格、成交量、走勢當作說明別件事的參照，\n' +
  '           例如「這檔跟大立光一樣都是高價股」。\n' +
  '  past     回顧舊單。講的是已經結束的事，例如「這支我已經沒了，我都賺錢賣」\n' +
  '           「當初跌停我沒賣，等上來到黑K棒上緣才賣」。那是教學不是今天的紀錄。\n' +
  '  quote    只有報價。只講了價格、成交量、漲跌，沒有任何要不要碰的結論。\n' +
  '  unclear  節錄裡根本找不到他談這一檔，或內容零碎到看不出在講什麼。\n' +
  '\n' +
  '真的有立場才 should_publish=true，why_not 留空。\n' +
  '判斷立場時以整段話的結論為準，不要被前半句的鋪陳帶走：\n' +
  '他很常先講這檔多好，最後才說現在不要買，那種情況結論是不要買。\n' +
  '\n' +
  '第三步，should_publish=true 時填方向，只能是這五個之一：\n' +
  '  買入　　　明講今天、剛剛、這個盤中實際執行的買進。\n' +
  '  賣出　　　明講今天、剛剛、這個盤中實際執行的賣出。\n' +
  '  觀望不碰　結論是現在不要進場。包含不要買、不用買、別追、來不及、\n' +
  '            已經漲上去了、會整理一段時間、風險高、轉弱、破線。\n' +
  '  觀望注意　結論是值得留意或等回檔再看。包含看好、留意、追蹤、\n' +
  '            等回檔進場、跌到某價位可以買。\n' +
  '  會員持股　明講現在還抱著、還沒賣、續抱。\n' +
  '\n' +
  '買入與賣出的門檻特別高：逐字稿必須看得到「今天、剛剛、這個盤中」實際執行了動作。\n' +
  '看不到今天執行就不可以填買入或賣出，依結論改填觀望兩類。\n' +
  '這一關寧可少收，不要收錯：分不清是今天還是以前，就填觀望類。\n' +
  '\n' +
  'price 填他明講的價位或條件（例如「255以上」「168」），沒講就填「未說明」。\n' +
  '營收、成交量、指數點位、財報數字都不是價位，不要填。\n' +
  'reason 用 25 字以內寫他為什麼這樣講，要照逐字稿的意思，不要自己加解讀。\n' +
  '\n' +
  'confident 表示你有沒有把握。節錄太零碎、看不出是今天還是以前、\n' +
  '或看不出結論時回 false。confident=false 的那一筆不會進表格，\n' +
  '會留在「修正建議」分頁等人判斷，所以不確定時務必回 false，不要猜。\n' +
  '\n' +
  '只回傳 JSON，index 對應輸入編號：\n' +
  '{"results":[{"index":1,"is_stock":true,"should_publish":false,"why_not":"sector",' +
  '"direction":"","price":"","reason":"","confident":true}]}';

// why_not 代號翻成人看得懂的話。執行紀錄與狀態信都用這一份。
var AUDIT_SKIP_LABEL = {
  group:   '集團順帶點名，沒有單獨立場',
  sector:  '族群舉例，重點不是這一檔',
  compare: '拿來當對照或比喻',
  past:    '回顧已經結束的舊單',
  quote:   '只有報價，沒有結論',
  unclear: '節錄看不出在講什麼'
};

/**
 * 判定某一天稽核列出的候選，該收的補登、不該收的丟掉、拿不準的留給人。
 * 回傳 { added, skipped, pending, decisions }
 * decisions 是逐筆的判定明細，狀態信顯示的就是它。
 */
function autoFixAuditMissing(dateStr) {
  var d = fmtDate_(dateStr || todayStr_());
  var v = digestAuditVerdict_(d);
  if (!v || !v.missing || !v.missing.length) {
    Logger.log(d + ' 稽核沒有發現漏抓');
    return { added: 0, skipped: 0, pending: 0 };
  }

  var items = v.missing.slice(0, 25);   // 一次最多 25 筆，避免請求過大

  // 判定要看得到上下文才判得準。
  //
  // 先前只送稽核給的那一句 evidence，而那一句是稽核自己挑出來當證據的，
  // 本來就偏向「看起來像漏抓」的那一句。拿它去問「該不該收」，等於請模型
  // 用一句斷章取義的話做判斷，集團順帶點名與真的給立場長得幾乎一樣。
  // 改成回逐字稿抓那一檔前後各數百字，模型才看得出他是在講整個集團、
  // 拿它當族群例子，還是真的單獨對這一檔說了要怎麼做。
  var v2 = '';
  try {
    readSheetObjects_('影片清單').forEach(function (r) {
      if (fmtDate_(r['發布日期']) !== d) { return; }
      var t = rawTranscript_(r);
      if (t.length > v2.length) { v2 = t; }
    });
  } catch (e) { /* 讀不到就退回只用 evidence */ }

  var payload = items.map(function (m, i) {
    var snip = '';
    if (v2 && typeof reviewSnips_ === 'function') {
      snip = reviewSnips_(v2, String(m.name || ''), '');
    }
    return (i + 1) + '. 名稱：' + (m.name || '') +
           '\n   稽核推測的方向：' + (m.where || '未知') +
           '\n   稽核挑出的那一句：' + String(m.evidence || '').slice(0, 200) +
           '\n   逐字稿上下文：' + (snip || '（逐字稿中找不到這個名稱）');
  }).join('\n\n');

  var res;
  try {
    var raw = callGemini_(PIPE_EXTRACT_SYSTEM + '\n本輪只輸出相容補登格式：{"results":[{"index":1,"is_stock":true,"should_publish":true,"confident":true,"direction":"會員持股","price":"未說明","reason":"","evidence":["原句"],"when":"today"}]}。買賣無明確當日成交句時 confident=false，需回上游重跑來源影片修正日期。', '影片日期：' + d + '\n' + payload,
                          { maxOut: 4096, temperature: 0, json: true });
    res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim());
  } catch (e) {
    // 額度用完要往外拋，讓上層停下來。改走人工建議會再打一次模型，
    // 那一次同樣會失敗，只是把錯誤換了個樣子。
    if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
    Logger.log('自動補登判斷失敗，改走人工建議：' + e);
    return proposeAuditFixes(d);
  }

  var vid = '';
  readSheetObjects_('影片清單').forEach(function (r) {
    if (fmtDate_(r['發布日期']) === d) { vid = String(r['影片ID'] || ''); }
  });

  var trades = [], holds = [], pending = [], skipped = 0;
  // 每一筆的判定結果。狀態信要顯示的是這一份，不是稽核那份高召回的候選清單。
  var decisions = [];

  (res.results || []).forEach(function (r) {
    var n = Number(r.index) - 1;
    if (!(n >= 0 && n < items.length)) { return; }
    var m = items[n];

    if (!r.is_stock) {
      skipped++;
      decisions.push({ name: m.name, verdict: '不收', why: '判定不是個股' });
      Logger.log('  不收　' + m.name + '（判定不是個股）');
      return;
    }
    // 是個股，但這一次提到沒有給出當下的立場：集團順帶點名、族群舉例、
    // 拿來當對照、回顧舊單、只有報價。補進去會讓網站顯示不存在的操作，
    // 比漏掉還糟，所以一律不收。
    if (r.should_publish === false) {
      skipped++;
      var why = AUDIT_SKIP_LABEL[String(r.why_not || '')] || '沒有當下的立場';
      decisions.push({ name: m.name, verdict: '不收', why: why });
      Logger.log('  不收　' + m.name + '（' + why + '）');
      return;
    }
    if (r.confident !== true || !validEvidence_(r.evidence, v2) ||
        ((r.direction === '買入' || r.direction === '賣出') &&
         (r.when !== 'today' || !/今天|今日|剛剛/.test(r.evidence.join(' ')) || /昨天|昨日|先前|那一天|當天/.test(r.evidence.join(' '))))) {
      pending.push(m);
      decisions.push({ name: m.name, verdict: '待確認', why: '模型沒有把握，留給人判斷' });
      return;
    }

    var dir = String(r.direction || '').trim();
    if (dir === '會員持股') {
      holds.push([d, m.name, '', '持有', String(r.reason || '').slice(0, 60), vid]);
    } else if (['買入', '賣出', '觀望不碰', '觀望注意'].indexOf(dir) >= 0) {
      trades.push([d, m.name, '', dir, String(r.price || '未說明'),
                   String(r.reason || '').slice(0, 60), vid]);
    } else {
      pending.push(m);
      decisions.push({ name: m.name, verdict: '待確認', why: '方向判不出來' });
      return;
    }
    decisions.push({ name: m.name, verdict: '收', why: dir });
    Logger.log('  補登　' + m.name + ' → ' + dir);
  });

  withLock_(function () {
    if (trades.length) {
      var sh = getSheet_('操作紀錄');
      sh.getRange(sh.getLastRow() + 1, 1, trades.length, 7).setValues(trades);
    }
    if (holds.length) {
      var hs = getSheet_('會員持股');
      hs.getRange(hs.getLastRow() + 1, 1, holds.length, 6).setValues(holds);
    }
  });

  // 沒把握的才寫進修正建議等人看
  if (pending.length) {
    var now = new Date();
    var rows = pending.map(function (m) {
      var where = String(m.where || '觀望');
      return [now, d, '新增', where.indexOf('持有') >= 0 ? '會員持股' : '操作紀錄',
              m.name || '', '', JSON.stringify({ where: where, evidence: m.evidence || '' }),
              '待確認', ''];
    });
    withLock_(function () {
      var sh = getSheet_('修正建議');
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    });
  }

  var added = trades.length + holds.length;
  Logger.log(d + ' 判定結果：收 ' + added + ' 筆、不收 ' + skipped +
             ' 筆、留待人工 ' + pending.length + ' 筆');

  if (added) { CACHE.remove('tracker'); }
  return { added: added, skipped: skipped, pending: pending.length,
           decisions: decisions };
}

/**
 * 品質關卡的收尾保險。每日 15:20 觸發。
 *
 * 正常情況下關卡在推播窗口一開（12:00 起）就跑完了，這一支什麼都不用做。
 * 它存在是為了兩種例外：逐字稿在下午才到手，以及關卡中途被中斷。
 * 兩種情況都只要把關卡補跑一次即可，跑完它自己會接回推播。
 *
 * 函式名稱維持 auditAutoFixJob 沒有改，這樣既有的觸發器不必重裝。
 * 若之後重跑 installTriggers()，安裝的仍是同一支。
 *
 * 這一支不再寄任何信。判不出來的那幾筆會留在「修正建議」分頁，
 * 需要時自己去看；不需要一封信天天提醒同一件事。
 */
function auditAutoFixJob() {
  if (!isTradingDayToday_()) { return; }
  var d = todayStr_();

  var st = gateState_();
  if (st && st.date === d && st.status === '完成') {
    Logger.log(d + ' 品質關卡已完成，不必補跑');
    return;
  }

  // 沒有當天的文章就代表逐字稿還沒處理完，關卡沒有東西可以看。
  var hasArticle = false;
  try {
    readSheetObjects_('每日推播內容').forEach(function (r) {
      if (fmtDate_(r['日期']) === d && String(r['文字稿'] || '')) { hasArticle = true; }
    });
  } catch (e) { /* 讀不到就當作沒有 */ }
  if (!hasArticle) { Logger.log(d + ' 還沒有每日整理，品質關卡略過'); return; }

  Logger.log(d + ' 品質關卡尚未完成，補跑一次');
  scheduleQualityGate_(d, (st && st.date === d ? (Number(st.tries) || 0) : 0) + 1);
}


/* ==================================================================== *
 * 每日品質關卡
 *
 * 這一段取代了先前那封「[稽核] 推播與網站可能漏抓個股」的信。
 *
 * 那封信的問題不在內容，而在於它把工作丟回給人：信裡寫著「到 GitHub
 * 勾 fill_blanks 重跑，完成後執行 rebuildHoldingsTrackerJob()」，
 * 而那件事其實程式自己就做得到。結果是信每天都來，事情沒人做，
 * 隔天稽核再發現一次、再寄一次。
 *
 * 現在改成：逐字稿處理完之後，寄信之前，自動跑完三件事。
 *
 *   1. 完整性稽核　　逐字稿點名了、結構化紀錄卻沒有的，判定後自動補登。
 *   2. 內容複審　　　每一筆回頭問「這一次提到該不該記」。純粹回顧舊單的
 *                    整列刪掉；買賣看不到今天執行的退回觀望類。
 *   3. 重寫每日整理　紀錄變了，信件內容必須跟著重生，否則信與網站對不起來。
 *
 * 跑完才寄信，所以訂閱者收到的就是複審後的版本，不會先寄一封錯的再補救。
 *
 * 為什麼要用觸發器跑，而不是直接在 dailyPushJob 裡做完
 * ----------------------------------------------------
 * 三件事加起來要呼叫四到五次模型，最慢可能三四分鐘，而 Apps Script
 * 單次執行只有 6 分鐘，再加上寄信給所有訂閱者，很容易被腰斬。
 * 被腰斬不是拋例外而是整個中斷，狀態會卡在「寄送中」永遠不再寄。
 *
 * 所以 dailyPushJob 看到關卡還沒過，就排一棒去跑關卡然後直接返回；
 * 關卡自己在一次完整的額度裡跑完，跑完再回頭呼叫一次 dailyPushJob。
 * 對使用者而言就是備妥之後晚幾分鐘寄出，而不是整整慢一輪。
 * ==================================================================== */

var GATE_KEY = 'dailyGateState';

// 關卡的三段。phase 走到「完成」就結束。
var GATE_PHASES = ['稽核', '複審', '重寫', '完成'];

// 連續失敗這麼多次之後就放行，直接寄未複審的內容。
// 理由：信沒寄出去是使用者看得見的損失，關卡失敗只是品質沒有變好。
// 兩者相權，不能讓一個壞掉的關卡把整天的推播擋死。
var GATE_MAX_TRIES = 2;

// 超過這麼久沒有更新，就當作那一棒被中斷了，可以重排。
var GATE_STALE_MIN = 12;

function gateState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(GATE_KEY);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (e) { return null; }
}

function setGateState_(st) {
  PropertiesService.getScriptProperties().setProperty(GATE_KEY, JSON.stringify(st || {}));
}

function newGateState_(d, tries) {
  return { date: d, status: '處理中', phase: GATE_PHASES[0], changed: 0,
           tries: tries || 1, log: [], startedAt: nowStamp_(), updatedAt: nowStamp_() };
}

function gateStale_(st) {
  if (!st || !st.updatedAt) { return true; }
  var t = new Date(String(st.updatedAt).replace(/-/g, '/')).getTime();
  return !isFinite(t) || (Date.now() - t) > GATE_STALE_MIN * 60 * 1000;
}

function cleanupGateTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runQualityGate_') { ScriptApp.deleteTrigger(t); }
  });
}

function scheduleQualityGate_(d, tries) {
  cleanupGateTriggers_();

  // 同一天重試時接著上次卡住的那一段做，不要從頭來過。
  // 從頭來過等於再付一次稽核與複審的模型費用，而那兩段已經做完了。
  var st = gateState_();
  if (st && st.date === d && st.phase && st.phase !== '完成') {
    st.status = '處理中';
    st.tries = tries || ((Number(st.tries) || 0) + 1);
    st.updatedAt = nowStamp_();
    setGateState_(st);
  } else {
    setGateState_(newGateState_(d, tries));
  }

  /* 建觸發器會失敗，而且是這個專案實際踩過的坑：Apps Script 每個指令碼的
     觸發器上限是 20 個，裝滿之後 create() 直接拋例外。
     這裡絕對不能讓例外往外拋——呼叫端是 dailyPushJob，拋出去等於當天完全
     不寄信，而且畫面上不會有任何徵兆。回報失敗，讓呼叫端決定要不要放行。 */
  try {
    ScriptApp.newTrigger('runQualityGate_').timeBased().after(3000).create();
  } catch (e) {
    var m = String(e && e.message || e);
    Logger.log('排不出品質關卡的觸發器：' + m);
    var bad = gateState_() || {};
    bad.status = '失敗';
    bad.lastError = '排不出觸發器：' + m.slice(0, 160) +
                    '（多半是觸發器額度已滿，執行 cleanupAllTriggers() 或重跑 installTriggers()）';
    setGateState_(bad);
    return false;
  }
  Logger.log('已排入 ' + d + ' 的品質關卡（第 ' + (tries || 1) + ' 次）');
  return true;
}

/**
 * 做目前這一段，做完把 phase 推到下一段，回傳這一段的說明文字。
 * 觸發器路徑與網頁請求路徑共用同一支，兩邊行為才會完全一致。
 */
function gateAdvance_(st) {
  var d = st.date;

  // phase 一律在做完之後才往前推。做到一半失敗時 phase 留在原地，
  // 重試才會從失敗的那一段接著做，而不是跳過它假裝做完了。
  if (st.phase === '稽核') {
    var a = autoFixAuditMissing(d);
    if (a.pending) { throw new Error('稽核仍有 ' + a.pending + ' 筆待確認，請核對原文'); }
    st.changed += (Number(a.added) || 0);
    // 判定明細留在狀態裡，狀態信直接讀這一份。
    // 這樣信裡呈現的是「判過之後的結論」，不是稽核那份高召回的候選清單。
    st.audit = (a.decisions || []).slice(0, 30);
    st.auditStat = { added: a.added, skipped: a.skipped, pending: a.pending };
    st.phase = '複審';
    return '完整性稽核：收 ' + a.added + '、不收 ' + a.skipped +
           '、留待人工 ' + a.pending;
  }

  if (st.phase === '複審') {
    var rv = reviewDayRecords_(d);
    if (rv.unsure || rv.note && /中斷|沒有.*逐字稿|超過安全上限/.test(rv.note)) {
      throw new Error('品質複審未通過：' + rv.unsure + ' 筆待確認；' + (rv.note || '請重跑来源影片'));
    }
    st.changed += (Number(rv.changed) || 0) + (Number(rv.dropped) || 0) + (Number(rv.renamed) || 0);
    st.phase = '重寫';
    return '內容複審：檢視 ' + rv.checked + '、改分類 ' + rv.changed +
           '、刪除 ' + rv.dropped + '、沒把握不動 ' + rv.unsure +
           (rv.note ? '（' + rv.note + '）' : '');
  }

  if (st.phase === '重寫') {
    if (!st.changed) { st.phase = '完成'; return '紀錄沒有變動，不必重寫每日整理'; }
    try {
      // 撰稿本身就會先補齊代號，所以這裡直接呼叫即可。
      stepArticle_({ date: d, videoId: '', id: 'GATE-' + d, step: '撰稿', done: 0, total: 1 });
    } catch (e) {
      var m = String(e && e.message || e);
      // 這一天被複審清空了，本來就沒有東西可以寫，不算失敗。
      if (m.indexOf('無法撰稿') >= 0) {
        st.phase = '完成';
        return '複審後這一天已經沒有紀錄，未重寫每日整理';
      }
      // 其他失敗多半是配額。這裡一定要往外拋，讓整個關卡算失敗並重試一次，
      // 否則信裡是舊文章、網站是複審後的紀錄，兩邊對不起來而且信改不回來。
      throw e;
    }
    st.phase = '完成';
    return '每日整理已依複審後的紀錄重寫，信件與網站同源';
  }

  st.phase = '完成';
  return '';
}

/**
 * 觸發器路徑：一次把三段做完，額度不夠就交棒。跑完接回推播。
 */
function runQualityGate_() {
  cleanupGateTriggers_();

  var st = gateState_();
  if (!st || st.status !== '處理中') { return; }
  var deadline = stepDeadline_();

  try {
    while (st.phase !== '完成') {
      // 單段最慢的是重寫（撰稿），可能要一分半。留 100 秒緩衝。
      if (timeLeft_(deadline) < 100000) {
        st.updatedAt = nowStamp_();
        setGateState_(st);
        // 排不出下一棒就別假裝還在跑，標成失敗讓 dailyPushJob 決定要不要放行。
        try {
          ScriptApp.newTrigger('runQualityGate_').timeBased().after(3000).create();
        } catch (e0) {
          st.status = '失敗';
          st.lastError = '排不出下一棒：' + String(e0 && e0.message || e0).slice(0, 160);
          setGateState_(st);
          Logger.log(st.lastError);
        }
        return;
      }
      var msg = gateAdvance_(st);
      if (msg) { st.log.push(msg); Logger.log('　' + msg); }
      st.updatedAt = nowStamp_();
      setGateState_(st);
    }
    st.status = '完成';
  } catch (e) {
    st.status = '失敗';
    st.lastError = String(e && e.message || e);
    st.log.push('✗ ' + st.phase + '：' + st.lastError.slice(0, 150));
  }

  st.updatedAt = nowStamp_();
  setGateState_(st);
  CACHE.remove('tracker');
  Logger.log('品質關卡 ' + st.date + '　' + st.status + '\n' + (st.log || []).join('\n'));

  // 關卡過了就立刻接回推播，不必等下一個推播時段。
  // 失敗也接回去：dailyPushJob 自己會判斷要不要放行，
  // 由它一處決定，比在這裡多寫一套判斷可靠。
  //
  // 用觸發器再排一棒，而不是直接呼叫。關卡本身可能已經用掉四分鐘，
  // 直接接著寄信給所有訂閱者有機會撞上單次執行上限——被撞到不是拋例外，
  // 是整個執行被腰斬，狀態會卡在「寄送中」而且再也不會寄出去。
  // 換一棒就是換一次完整的時間額度，代價只是晚五秒。
  try { scheduleDailyPush_(); }
  catch (e2) { Logger.log('排推播失敗，改為直接寄：' + e2);
               try { dailyPushJob(); } catch (e3) { Logger.log('推播失敗：' + e3); } }
}

function scheduleDailyPush_() {
  cleanupPushTriggers_();
  ScriptApp.newTrigger('pushAfterGate_').timeBased().after(5000).create();
}

function cleanupPushTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'pushAfterGate_') { ScriptApp.deleteTrigger(t); }
  });
}

/**
 * 關卡跑完之後的那一棒推播。
 *
 * 一次性觸發器不會自己消失，所以進來第一件事是把自己刪掉，
 * 否則每天累積一個，遲早撞到帳號的觸發器數量上限，
 * 那時所有背景作業都會排不出下一棒而集體卡住。
 */
function pushAfterGate_() {
  cleanupPushTriggers_();
  dailyPushJob();
}

/**
 * 判斷今天的關卡過了沒有。沒過就排一棒去跑，並回 false 讓推播先退出。
 *
 * 這裡是唯一決定「要不要放行」的地方。連續失敗 GATE_MAX_TRIES 次就放行，
 * 寧可寄一封沒複審過的信，也不要整天沒有信。
 */
function gateReady_(d) {
  var st = gateState_();

  if (st && st.date === d) {
    if (st.status === '完成') { return true; }

    // 還在跑就讓它跑完。真的卡住（很久沒更新）才重排。
    if (st.status === '處理中' && !gateStale_(st)) { return false; }

    if ((Number(st.tries) || 0) >= GATE_MAX_TRIES) {
      Logger.log(d + ' 品質關卡已連續 ' + st.tries + ' 次未完成，' +
                 '保留待寄送，修正品質問題後重跑。');
      return false;
    }
    // 排得出來就等它跑完；排不出來就直接放行。
    // 寧可寄一封沒複審過的信，也不要因為排程問題整天沒有信。
    if (scheduleQualityGate_(d, (Number(st.tries) || 0) + 1)) { return false; }
    Logger.log(d + ' 排不出品質關卡，保留待寄送。');
    return false;
  }

  if (scheduleQualityGate_(d, 1)) { return false; }
  Logger.log(d + ' 排不出品質關卡，保留待寄送。');
  return false;
}

/**
 * 網頁請求路徑：一次只做一段就回報，由呼叫端重複打到 done 為止。
 *
 * 給 pipeline.py 跑完之後的 ?action=refresh&step=gate 用。
 * 一次做一段，每個請求都遠在時間上限之內，不會被切斷連線。
 */
function qualityGateChunk_(dateStr) {
  var d = fmtDate_(dateStr) || todayStr_();
  var st = gateState_();
  if (!st || st.date !== d || st.status === '完成' || st.phase === '完成') {
    // 明確要求重跑（例如 pipeline 跑完之後）就從頭來一次。
    st = newGateState_(d, 1);
  } else {
    // 上次做到一半沒完成，接著做，不重複付前面幾段的費用。
    st.status = '處理中';
  }

  var msg = gateAdvance_(st);
  if (msg) { st.log.push(msg); Logger.log('　' + msg); }
  if (st.phase === '完成') { st.status = '完成'; CACHE.remove('tracker'); }
  st.updatedAt = nowStamp_();
  setGateState_(st);

  return { done: st.phase === '完成', phase: st.phase,
           note: msg || '完成', processed: GATE_PHASES.indexOf(st.phase),
           total: GATE_PHASES.length - 1 };
}

/* 只推進指定的那一段。

   qualityGateChunk_ 是「呼叫一次推進一段」，三段做完要打三次，
   但畫面上只有一格「稽核與複審」——那一格會亮三到六分鐘，
   期間分不出它在補漏、在複審、還是在重寫整理，卡住時也看不出卡在哪一段。

   拆成三格不多花任何一次模型呼叫：本來就是分三次請求做完的，
   只是把已經存在的分段講出來。

   wanted 是這一格負責的那一段。已經做過就直接放行——單獨重跑後面某一步時
   會遇到這種情況，重開狀態的話會把稽核與複審再跑一次，白付兩次模型費用。 */
function gatePhaseChunk_(dateStr, wanted) {
  var d = fmtDate_(dateStr) || todayStr_();
  var want = GATE_PHASES.indexOf(wanted);
  if (want < 0) { return qualityGateChunk_(dateStr); }   // 認不得就跑整套

  var st = gateState_();
  var stale = !st || st.date !== d;
  var finished = st && (st.status === '完成' || st.phase === '完成');

  if (stale) {
    st = newGateState_(d, 1);
  } else if (finished) {
    // 整套已經跑完。第一段負責開新的一輪，後兩段沒事可做。
    if (want === 0) { st = newGateState_(d, 1); }
    else { return { done: true, processed: 1, total: 1, label: wanted,
                    note: wanted + '：整套已完成，這一段不重跑' }; }
  } else {
    st.status = '處理中';
  }

  if (GATE_PHASES.indexOf(st.phase) > want) {
    return { done: true, processed: 1, total: 1, label: wanted,
             note: wanted + '：先前已完成' };
  }

  var msg = gateAdvance_(st);
  if (msg) { st.log.push(msg); Logger.log('　' + msg); }
  if (st.phase === '完成') { st.status = '完成'; CACHE.remove('tracker'); }
  st.updatedAt = nowStamp_();
  setGateState_(st);

  // 還沒推進到下一段就代表這一段沒做完（失敗重試中），回 done=false 讓驅動端再打一次。
  var now = GATE_PHASES.indexOf(st.phase);
  return { done: now > want, processed: now > want ? 1 : 0, total: 1,
           label: wanted, note: msg || (wanted + '：完成') };
}


/** 後台或編輯器要看關卡進度時用。 */
function apiAdminGateState(key) {
  try {
    adminAuth_(key);
    var st = gateState_();
    if (st) {
      st.note = (st.log || []).join('\n');
      var at = GATE_PHASES.indexOf(st.phase);
      st.pct = st.status === '完成' ? 100
             : Math.round(Math.max(0, at) / (GATE_PHASES.length - 1) * 100);
    }
    return { ok: true, state: st };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/** 編輯器裡直接跑某一天的關卡（同步跑完，不排觸發器）。 */
function runGateNow(dateStr) {
  var d = fmtDate_(dateStr) || todayStr_();
  var st = newGateState_(d, 1);
  while (st.phase !== '完成') {
    var msg = gateAdvance_(st);
    if (msg) { st.log.push(msg); Logger.log('　' + msg); }
  }
  st.status = '完成';
  st.updatedAt = nowStamp_();
  setGateState_(st);
  CACHE.remove('tracker');
  Logger.log('品質關卡 ' + d + ' 完成\n' + st.log.join('\n'));
  return st;
}
