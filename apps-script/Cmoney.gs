/* ==================================================================== *
 * 會員簡訊：盤中即時通知的擷取與發佈
 *
 * 這是什麼
 * --------
 * 張震給 VIP 會員的盤中操作簡訊，會由 CMoney 股市爆料同學會上一個轉發帳號
 * （預設「我是三峽人」，會員編號 1230411）以「僅供參考！」為標題轉貼出來。
 * 內容長這樣：
 *
 *   張震-1:手中持有1519華城，請於775元以上全數獲利賣出，資金保留下來。
 *   張震6GJ-1:全國會員請將手中璟德於257元以上全數獲利賣出，
 *             資金轉為65.5元以下市價買進2354鴻準！
 *
 * 「張震-1」「張震6GJ-2」「震1」是投顧內部對不同會員等級的廣播序號，
 * 不是內容的一部分，但它是判斷「這一篇是不是張震的簡訊」最可靠的記號。
 *
 * 這一份與影片那條線的關係
 * ------------------------
 * 影片是收盤前後才有的完整解盤，簡訊是盤中當下的指令，兩者是不同的東西：
 *   影片　　說明為什麼，事後整理，一天一次
 *   簡訊　　只說做什麼與什麼價位，即時，一天零到三則
 * 所以簡訊不覆蓋影片的紀錄，而是各自成一列、用「來源影片ID」分辨
 * （簡訊寫成 CMONEY-<文章ID>，與人工補登的 MANUALENTRY- 同一個作法）。
 * 只有持股追蹤那邊會優先採用簡訊講的價位——那是他當下講的委託價，
 * 比事後從逐字稿推出來的準。
 *
 * 平常輪詢與歷史補抓
 * ------------------
 * 每分鐘輪詢只讀文章清單 API 第一頁，一次取得新文章與當日文章最新版全文；
 * 內容以字數與 SHA-256 比對。歷史補抓由 GitHub Actions 沿 weight 游標找到底，
 * 會先從公開會員頁讀取訪客 JWT，權杖失效會自動重取。文章頁仍是依編號補抓的備援，時間優先讀
 * article:published_time，不能驗證就隔離，絕不以執行時間代替。
 *
 * 流量預算
 * --------
 * Apps Script 的 UrlFetch 每天只能收 100MB。清單 API 第一頁實測約 30KB，
 * 每分鐘抓一次、平日 09:00–15:00 約 11MB；權杖更新才額外讀一次會員頁。
 * 所以這裡有一道每日流量上限（CMONEY_MAX_MB，預設 60MB），
 * 超過就當天停抓。報價快取與日K 是整個網站的骨幹，不能被這一支餓死。
 * ==================================================================== */

var CM_SHEET = '會員簡訊';
var CM_SEEN_PROP = 'cmSeenIds';          // 看過的文章ID（最近 N 個）
var CM_STAT_PROP = 'cmFetchStat';        // 當日抓取次數與位元組估計
var CM_LAST_PROP = 'cmLastPoll';         // 上一次真的去抓的時間
var CM_WATCH_PROP = 'cmWatchedArticle';  // 今日持續追蹤的文章與內容指紋
var CM_LIVE_TOKEN_PROP = 'cmLiveGuestToken';

var CM_SEEN_KEEP = 300;                  // 記住最近幾個文章ID
var CM_MAX_PER_TICK = 3;                 // 一棒最多抓幾篇新文章（避免超時）
var CM_PAGE_KB = 128;                    // 會員頁 gzip 後的實測大小
var CM_ART_KB = 119;                     // 文章頁 gzip 後的實測大小

/* 判斷「這一篇是不是張震的簡訊」。
 *
 * 記號的實際樣子（29 篇歷史文章全部涵蓋）：
 *   張震-1:  張震-2:  張震-3:  張震:  張震1:  張震2:
 *   張震6GJ-1:  張震6GJ-2:
 *   震1:  震2:
 * 全形冒號也要收，他兩種都用（張震-1：今日盤面…）。
 *
 * 一定要有冒號。少了它，「大震盪」「震盪一兩天」這種一般用字會被誤判——
 * 那不是指令，收進來會生出一整列不存在的操作。
 * 序號可有可無（張震:手中持有威剛者…），但冒號不行。 */
var CM_MARK = /(?:張震|震)\s*(?:6GJ)?\s*[-－—─]?\s*[0-9０-９]{0,2}\s*[:：]/;


/* ------------------------------------------------------------------ *
 * 設定
 * ------------------------------------------------------------------ */

function cmProp_(k, dflt) {
  var v = PropertiesService.getScriptProperties().getProperty(k);
  return (v === null || v === '') ? dflt : v;
}

/** 沒設定會員編號就整支不啟用。這是這一項功能的總開關。 */
function cmMemberId_() { return String(cmProp_('CMONEY_MEMBER_ID', '')).trim(); }

function cmEnabled_() { return /^\d+$/.test(cmMemberId_()); }

/** 會員簡訊是正式資料來源；解析成功即寫入操作紀錄。 */
function cmWriteTrades_() {
  return true;
}

function cmUserUrl_() {
  return 'https://www.cmoney.tw/forum/user/' + cmMemberId_();
}

function cmArticleUrl_(id) {
  return 'https://www.cmoney.tw/forum/article/' + id;
}


/* ------------------------------------------------------------------ *
 * 流量預算
 *
 * 每天記抓了幾次。位元組用實測值估，不去問回應的實際大小——
 * UrlFetchApp 拿到的是解壓後的內容，長度與計入額度的位元組不是同一件事。
 * 估得保守一點，寧可提早停。
 * ------------------------------------------------------------------ */

function cmStat_() {
  var raw = cmProp_(CM_STAT_PROP, '');
  var today = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
  var st;
  try { st = raw ? JSON.parse(raw) : null; } catch (e) { st = null; }
  if (!st || st.date !== today) { st = { date: today, page: 0, art: 0, kb: 0 }; }
  return st;
}

function cmSaveStat_(st) {
  try { PropertiesService.getScriptProperties().setProperty(CM_STAT_PROP, JSON.stringify(st)); }
  catch (e) { /* 記不起來就當作沒記，不影響抓取 */ }
}

function cmOverBudget_(st) {
  var maxMb = Number(cmProp_('CMONEY_MAX_MB', '60')) || 60;
  return st.kb >= maxMb * 1024;
}


/* ------------------------------------------------------------------ *
 * 抓取
 * ------------------------------------------------------------------ */

/**
 * 抓一頁回來。失敗回空字串，不往外拋——這一支跑在每分鐘的排程上，
 * 對方偶爾回 5xx 是常態，不該讓整棒中斷。
 */
function cmFetch_(url) {
  try {
    var res = UrlFetchApp.fetch(url, {
      muteHttpExceptions: true,
      followRedirects: true,
      headers: {
        // 不帶 UA 有機會拿到簡化版頁面。這一組是一般瀏覽器的樣子。
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'zh-TW,zh;q=0.9'
      }
    });
    if (res.getResponseCode() !== 200) {
      Logger.log('會員簡訊：' + url + ' 回 HTTP ' + res.getResponseCode());
      return '';
    }
    return res.getContentText();
  } catch (e) {
    Logger.log('會員簡訊：抓不到 ' + url + '（' + e + '）');
    return '';
  }
}

/**
 * 會員頁上的文章ID，新到舊。
 *
 * 只認 /forum/article/<數字> 這個連結樣式——它是網址，不是版面，
 * 改版時最不容易變的東西。
 */
function cmListArticleIds_(html) {
  var out = [], seen = {};
  var re = /\/forum\/article\/(\d{6,})/g, m;
  while ((m = re.exec(html)) !== null) {
    if (!seen[m[1]]) { seen[m[1]] = 1; out.push(m[1]); }
  }
  return out;
}

/** 從 HTML 取 meta。property 與 name 兩種寫法都收，屬性順序也不固定。 */
function cmMeta_(html) {
  var meta = {};
  var pats = [
    /<meta[^>]*?(?:property|name)="([^"]+)"[^>]*?content="([^"]*)"/gi,
    /<meta[^>]*?content="([^"]*)"[^>]*?(?:property|name)="([^"]+)"/gi
  ];
  for (var k = 0; k < 2; k++) {
    var re = pats[k], m;
    while ((m = re.exec(html)) !== null) {
      var key = k === 0 ? m[1] : m[2];
      var val = k === 0 ? m[2] : m[1];
      if (meta[key] === undefined) { meta[key] = cmUnescape_(val); }
    }
  }
  return meta;
}

function cmUnescape_(s) {
  return String(s || '')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, String.fromCharCode(39))
    .replace(/&apos;/g, String.fromCharCode(39))
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
}

/**
 * 文章全文。
 *
 * og:description 只有 80 字，一則有三段指令的簡訊會被切掉兩段，
 * 所以正文要從內文區塊取。取不到就退回 og:description——
 * 寧可短也不要整篇漏掉，而且短的那一版仍然看得出是不是張震的簡訊。
 */
function cmBody_(html, meta) {
  var i = html.indexOf('articleContent__text');
  if (i >= 0) {
    // 內文區塊到下一個 articleContent__ 之間就是正文，再往後是圖片與留言。
    var j = html.indexOf('articleContent__', i + 20);
    var seg = html.slice(i, j > i ? j : i + 20000);
    var lines = [], re = /<span[^>]*>([\s\S]*?)<\/span>/g, m;
    while ((m = re.exec(seg)) !== null) {
      var t = cmUnescape_(String(m[1]).replace(/<[^>]+>/g, '')).trim();
      if (t) { lines.push(t); }
    }
    var body = lines.join('\n').trim();
    if (body.length >= 10) { return body; }
  }
  return String(meta['og:description'] || meta['description'] || '').trim();
}

/**
 * 嚴格解析發文時間。解析失敗一定回空字串，絕不拿「現在」代替。
 *
 * 舊版用現在時間兜底，歷史文章只要少一個 meta，就會整批被塞到最近一天。
 * 這比漏一篇更嚴重：日期一錯，日曆、操作順序與持股回合都會一起錯。
 *
 * CMoney 在一週內的畫面有時只顯示「星期三 09:41」，所以 HTML 備援也支援
 * 今天、昨天與星期幾。星期幾一律解成「不晚於參考時間的最近那一天」；若同一
 * 星期但顯示時間還沒到，代表上週，不會誤放到未來。
 */
function cmTime_(meta, html, referenceDate) {
  var raw = String((meta || {})['article:published_time'] || '').trim();
  var m = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})T(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    return cmMakeTime_(m[1], m[2], m[3], m[4], m[5], m[6] || 0);
  }

  var source = String(html || '');
  // 文章頁後半還有「推薦文章」，裡面也會出現星期幾。只讀目前文章區，
  // 並先移除程式與樣式，避免拿到推薦卡或內嵌狀態裡的日期。
  var navAt = source.indexOf('nav__articleItemTime');
  if (navAt > 0) { source = source.slice(0, navAt); }
  source = source.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
                 .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ');
  var text = cmUnescape_(source.replace(/<[^>]+>/g, ' '))
    .replace(/\s+/g, ' ');
  m = text.match(/(20\d{2})[\/\-.年](\d{1,2})[\/\-.月](\d{1,2})日?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (m) {
    return cmMakeTime_(m[1], m[2], m[3], m[4], m[5], m[6] || 0);
  }

  var ref = referenceDate instanceof Date ? new Date(referenceDate.getTime()) : new Date();
  if (isNaN(ref.getTime())) { ref = new Date(); }
  m = text.match(/(今天|昨日|昨天|星期[一二三四五六日天])\s*(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  if (!m) { return ''; }

  var target = new Date(ref.getTime());
  if (m[1] === '昨日' || m[1] === '昨天') {
    target.setDate(target.getDate() - 1);
  } else if (m[1].indexOf('星期') === 0) {
    var map = { '日': 0, '天': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6 };
    var want = map[m[1].slice(-1)];
    if (want === undefined) { return ''; }
    var back = (target.getDay() - want + 7) % 7;
    target.setDate(target.getDate() - back);
    if (back === 0) {
      var shownMinutes = Number(m[2]) * 60 + Number(m[3]);
      var refMinutes = ref.getHours() * 60 + ref.getMinutes();
      if (shownMinutes > refMinutes) { target.setDate(target.getDate() - 7); }
    }
  }
  return cmMakeTime_(target.getFullYear(), target.getMonth() + 1, target.getDate(),
                     m[2], m[3], m[4] || 0);
}

function cmMakeTime_(year, month, day, hour, minute, second) {
  var y = Number(year), mo = Number(month), d = Number(day);
  var h = Number(hour), mi = Number(minute), s = Number(second || 0);
  var dt = new Date(y, mo - 1, d, h, mi, s);
  if (isNaN(dt.getTime()) || dt.getFullYear() !== y || dt.getMonth() !== mo - 1 ||
      dt.getDate() !== d || h < 0 || h > 23 || mi < 0 || mi > 59 || s < 0 || s > 59) {
    return '';
  }
  return Utilities.formatDate(dt, TZ, 'yyyy/MM/dd HH:mm:ss');
}

/** 抓一篇文章，回 { id, url, title, time, text } 或 null。 */
function cmFetchArticle_(id) {
  var html = cmFetch_(cmArticleUrl_(id));
  if (!html) { return null; }
  var meta = cmMeta_(html);
  return {
    id: String(id),
    url: cmArticleUrl_(id),
    title: String(meta['og:title'] || '').trim(),
    time: cmTime_(meta, html, new Date()),
    text: cmBody_(html, meta)
  };
}


/* ------------------------------------------------------------------ *
 * 看過的文章
 * ------------------------------------------------------------------ */

function cmSeen_() {
  var raw = cmProp_(CM_SEEN_PROP, '');
  var out = {};
  if (!raw) { return out; }
  try {
    JSON.parse(raw).forEach(function (x) { out[String(x)] = 1; });
  } catch (e) { /* 壞掉就當作沒看過，最多重抓一次 */ }
  return out;
}

function cmMarkSeen_(seen, id) {
  seen[String(id)] = 1;
  // 只留最近的一批。文章ID 是遞增的，數字大的就是新的。
  var list = Object.keys(seen).sort(function (a, b) { return Number(b) - Number(a); })
    .slice(0, CM_SEEN_KEEP);
  var next = {};
  list.forEach(function (x) { next[x] = 1; });
  try {
    PropertiesService.getScriptProperties()
      .setProperty(CM_SEEN_PROP, JSON.stringify(list));
  } catch (e) { /* 寫不進去下一棒會重抓，不會漏 */ }
  return next;
}


/* ------------------------------------------------------------------ *
 * 寫進試算表
 * ------------------------------------------------------------------ */

/** 這一篇已經收過了嗎。用文章ID 比對，不看內容。 */
function cmAlreadySaved_(id) {
  try {
    return !!cmSheetIds_()[String(id).trim()];
  } catch (e) { /* 讀不到就當作沒有，最多重複一列 */ }
  return false;
}

/** 會員簡訊分頁已有的文章ID。只讀文章ID那一欄——整張讀會連原文全文一起讀，分頁大了很慢。 */
function cmSheetIds_() {
  var sh = getSheet_(CM_SHEET), last = sh.getLastRow(), out = {};
  if (last < 2) { return out; }
  var head = sh.getRange(1, 1, 1, Math.max(1, sh.getLastColumn())).getValues()[0]
    .map(function (h) { return String(h).trim(); });
  var col = head.indexOf('文章ID');
  if (col < 0) { col = 0; }
  sh.getRange(2, col + 1, last - 1, 1).getValues().forEach(function (r) {
    var id = String(r[0]).trim();
    if (id) { out[id] = 1; }
  });
  return out;
}

/* 今天這幾篇的內容指紋。只讀文章ID與內容指紋兩欄。

   修訂偵測本身（cmApplyArticleRevision_）要整張讀會員簡訊才找得到那一列，
   而那正是 9/14 輪詢停掉的嫌疑：整張讀在文章累積之後會越來越慢。
   稽核那一棒每十分鐘跑一次，不能每次都整張讀，所以先用兩欄比指紋，
   真的變了才走那條貴的路。 */
function cmFingerprints_(ids) {
  var sh = getSheet_(CM_SHEET), last = sh.getLastRow(), out = {};
  if (last < 2) { return out; }
  var head = sh.getRange(1, 1, 1, Math.max(1, sh.getLastColumn())).getValues()[0]
    .map(function (h) { return String(h).trim(); });
  var idCol = head.indexOf('文章ID'), hashCol = head.indexOf('內容指紋');
  if (idCol < 0 || hashCol < 0) { return out; }
  var idVals = sh.getRange(2, idCol + 1, last - 1, 1).getValues();
  var hashVals = sh.getRange(2, hashCol + 1, last - 1, 1).getValues();
  for (var i = 0; i < idVals.length; i++) {
    var id = String(idVals[i][0]).trim();
    if (id && (!ids || ids[id])) { out[id] = String(hashVals[i][0] || ''); }
  }
  return out;
}

function cmSaveMessage_(a) {
  if (!a || !cmDate_(a.time)) {
    cmNote_('文章 ' + String(a && a.id || '（無編號）') +
            ' 缺少可驗證的發文日期，已隔離，沒有寫入會員簡訊');
    return false;
  }
  if (cmAlreadySaved_(a.id)) { return false; }
  withLock_(function () {
    var sh = getSheet_(CM_SHEET);
    var head = sh.getRange(1, 1, 1, Math.max(8, sh.getLastColumn())).getValues()[0];
    ['解析明細', '內容指紋', '最後偵測', '修訂次數', '解析版本', '判定時間'].forEach(function (name) {
      if (head.indexOf(name) < 0) {
        head.push(name);
        // 先確認欄數夠。分頁若是用 12 欄建的，直接 getRange(1, 13) 會丟
        // 「範圍的座標或尺寸無效」，而那個錯誤看起來跟欄位完全無關。
        if (sh.getMaxColumns() < head.length) {
          sh.insertColumnsAfter(sh.getMaxColumns(), head.length - sh.getMaxColumns());
        }
        sh.getRange(1, head.length).setValue(name);
      }
    });
    /* 解析明細留空白，不要寫 '[]'。

       這一個字串是「1519 華城 775 以上全數獲利賣出」被判成無可收錄的直接原因：
       '[]' 是合法 JSON 的空陣列，於是併入那一步把它讀成「解析過、沒有個股」，
       一次 Gemini 都沒呼叫就把該列標成「已解析（無可收錄，稽核通過）」。
       空白才代表「還沒判定過」，而「判定過但真的沒有個股」由解析版本欄位認定。 */
    sh.appendRow([
      a.id, a.time, a.title, a.text.slice(0, 20000),
      '待解析', nowStamp_(), '未通知', a.url, '', cmFingerprint_(a.text), nowStamp_(), 0, '', ''
    ]);
  });
  return true;
}

function cmFingerprint_(text) {
  var normalized = String(text || '').replace(/\s+/g, ' ').trim();
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, normalized,
                                      Utilities.Charset.UTF_8);
  return bytes.map(function (b) { var n = b < 0 ? b + 256 : b; return ('0' + n.toString(16)).slice(-2); }).join('');
}

function cmRememberWatch_(a) {
  var d = cmDate_(a && a.time);
  if (!a || !a.id || !d || d !== todayStr_()) { return; }
  var pr = PropertiesService.getScriptProperties(), old = null;
  try { old = JSON.parse(pr.getProperty(CM_WATCH_PROP) || 'null'); } catch (e) {}
  if (old && old.time && String(old.time) > String(a.time)) { return; }
  pr.setProperty(CM_WATCH_PROP, JSON.stringify({
    id: String(a.id), date: d, time: String(a.time), fingerprint: cmFingerprint_(a.text),
    length: String(a.text || '').length
  }));
}

/** 同一篇文章盤中被編輯時，覆蓋原文並讓 GitHub 重新解析。 */
function cmApplyArticleRevision_(a) {
  var sh = getSheet_(CM_SHEET), vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return false; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  ['解析明細', '內容指紋', '最後偵測', '修訂次數', '解析版本', '判定時間'].forEach(function (name) {
    if (head.indexOf(name) < 0) {
      head.push(name);
      if (sh.getMaxColumns() < head.length) {
        sh.insertColumnsAfter(sh.getMaxColumns(), head.length - sh.getMaxColumns());
      }
      sh.getRange(1, head.length).setValue(name);
    }
  });
  var iId = head.indexOf('文章ID'), iText = head.indexOf('原文'), iTitle = head.indexOf('標題');
  var iTime = head.indexOf('發文時間'), iState = head.indexOf('解析狀態');
  var iDetail = head.indexOf('解析明細'), iHash = head.indexOf('內容指紋');
  var iSeen = head.indexOf('最後偵測'), iRev = head.indexOf('修訂次數');
  var iVer = head.indexOf('解析版本'), iAt = head.indexOf('判定時間');
  var hash = cmFingerprint_(a.text);
  for (var r = vals.length - 1; r >= 1; r--) {
    if (String(vals[r][iId]).trim() !== String(a.id)) { continue; }
    var oldHash = String(vals[r][iHash] || '') || cmFingerprint_(vals[r][iText]);
    sh.getRange(r + 1, iSeen + 1).setValue(nowStamp_());
    if (oldHash === hash && String(vals[r][iText] || '').length === String(a.text || '').length) { return false; }
    sh.getRange(r + 1, iText + 1).setValue(String(a.text || '').slice(0, 20000));
    if (iTitle >= 0) { sh.getRange(r + 1, iTitle + 1).setValue(a.title || vals[r][iTitle]); }
    if (iTime >= 0 && cmDate_(a.time)) { sh.getRange(r + 1, iTime + 1).setValue(a.time); }
    sh.getRange(r + 1, iState + 1).setValue('待解析（文章已修訂）');
    /* 內容變了，舊的 AI 判定就不再對應這篇原文。清掉明細與版本戳記，
       讓它回到「空白待解析」那一桶重新判定；不清的話會被當成
       「已經判定過」而沿用舊明細，修訂後的新指令就收錄不到。 */
    if (iDetail >= 0) { sh.getRange(r + 1, iDetail + 1).setValue(''); }
    if (iVer >= 0) { sh.getRange(r + 1, iVer + 1).setValue(''); }
    if (iAt >= 0) { sh.getRange(r + 1, iAt + 1).setValue(''); }
    sh.getRange(r + 1, iHash + 1).setValue(hash);
    sh.getRange(r + 1, iRev + 1).setValue((Number(vals[r][iRev]) || 0) + 1);
    cmNote_('文章 ' + a.id + ' 內容已修訂，已保留舊衍生紀錄並等待成功解析後原子取代');
    return true;
  }
  return false;
}

/* 自動派工：盤中偵測到新文章或文章被修訂時，派一個「只更新當天」的工單。

   這裡有兩個先前造成事故的細節，都必須維持現狀，不要再改回去：

   1. sms_mode 一定是 today，不可以是 parse。
      parse 在 pipeline.py 那邊會落到預設範圍，也就是掃過全部歷史。
      自動派工每十幾分鐘就會發生一次，用全歷史範圍等於每十幾分鐘
      把整批舊文章重新丟給 Gemini 判一次，免費層的每日額度撐不到中午，
      接著當天的逐字稿擷取就會拿到「Gemini 配額用盡（extract）：HTTP 429」。
      當天的簡訊本來就只有幾篇，today 才是這裡該做的事。

   2. refresh_site 不帶。全站重算是另一條鏈，不該被一則簡訊觸發。 */
function cmDispatchGithubParse_(since) {
  try {
    /* 已經有一個工單在跑就不要重複派——但「在跑」必須有時效。

       這個判斷沒有時效的話會變成永久的門閂：GitHub 那邊若因為任何原因
       沒能回報最後狀態（job 被砍、網路斷在最後一步、Actions 佇列塞住），
       後台的狀態就永遠停在「處理中」，於是從那一刻起所有新簡訊都不再派工，
       而且完全沒有徵兆——畫面上看起來只是「今天沒有新的通知」。
       超過四十分鐘沒有更新就視為卡死，讓新的工單接手。
       這個門檻與後台手動派工那邊用的是同一個。 */
    var active = getSmsParseState_();
    if (active && active.status === '處理中' && active.execution === 'github') {
      var age = Date.now() - new Date(active.updatedAt || 0).getTime();
      if (!(age > 40 * 60 * 1000)) {
        return { ok: true, queuedBehindActive: true };
      }
      cmNote_('前一個會員簡訊工單已逾四十分鐘沒有更新，視為中斷，改派新的工單。');
    }
    // 配額冷卻期間不自動派工。派了也只會拿回 429，而且照樣計入用量。
    var cd = smsCooldownLeft_();
    if (cd) {
      return { ok: true, cooldown: cd, skipped: '配額冷卻中，暫不自動派工' };
    }
    var jobId = cmJobId_();
    setSmsParseState_({ jobId: jobId, mode: 'today', execution: 'github', status: '處理中',
      step: '準備', index: 0, total: SMS_MERGE_STEPS_.length - 1, done: 0, pct: 2,
      since: since || '', steps: SMS_MERGE_STEPS_,
      through: todayStr_(), auditReady: false, startedAt: nowStamp_(),
      note: '偵測到新內容，正在派送 GitHub Actions 更新當天資料。' });
    var out = dispatchGithub_({ parse_sms: 'true', sms_mode: 'today', sms_since: since || '',
                                sms_job_id: jobId, sms_member_id: cmMemberId_() });
    if (out && out.ok) {
      var running = getSmsParseState_() || {};
      running.runId = out.runId || '';
      running.runUrl = out.runUrl || '';
      running.note = '偵測到新內容，GitHub Actions 已接受解析工作。';
      setSmsParseState_(running);
      PropertiesService.getScriptProperties().setProperty('cmLastGithubParseDispatch', String(Date.now()));
    } else {
      var failed = getSmsParseState_() || {};
      failed.status = '失敗'; failed.note = out && out.reason || 'GitHub 派工失敗';
      setSmsParseState_(failed);
    }
    return out;
  } catch (e) {
    cmNote_('GitHub 解析派工失敗：' + String(e).slice(0, 120));
    return { ok: false };
  }
}

/* 每五分鐘補看一次，負責接回短暫派工失敗的情況。

   這一支是「會員簡訊一直在跑」的來源，所以三道閘門缺一不可：

   1. 只看今天。先前 cmCountPendingSms_('') 看的是全部歷史的待解析列，
      而那些列因為每一輪都在 429 後回滾、狀態永遠停在待解析，
      於是這裡每十分鐘就再派一次工，每一次都跑十四分鐘、每一次都失敗，
      工作清單上就是一整排「會員簡訊 SMS-…」。
   2. 冷卻期間不派。
   3. 一天的自動派工次數有上限。真正壞掉的時候，讓它安靜下來等人處理，
      而不是用整天的 Actions 額度去重複同一個失敗。 */
var CM_AUTO_DISPATCH_PROP_ = 'cmAutoDispatchCount';
var CM_AUTO_DISPATCH_MAX_ = 6;

function cmAutoDispatchLeft_() {
  var today = todayStr_();
  var raw = cmProp_(CM_AUTO_DISPATCH_PROP_, '');
  var st = { day: today, n: 0 };
  try { var p = JSON.parse(raw); if (p && p.day === today) { st = p; } } catch (e) {}
  return { state: st, left: Math.max(0, CM_AUTO_DISPATCH_MAX_ - Number(st.n || 0)) };
}

function cmBumpAutoDispatch_() {
  var cur = cmAutoDispatchLeft_().state;
  cur.n = Number(cur.n || 0) + 1;
  PropertiesService.getScriptProperties()
    .setProperty(CM_AUTO_DISPATCH_PROP_, JSON.stringify(cur));
}

function cmDispatchPendingGithubJob_() {
  // 只看今天。過去資料的空白由後台那顆「併入過去資料」按鈕負責，
  // 不要讓自動排程去跑全歷史。
  if (!cmCountPendingSms_(todayStr_())) { return; }
  if (smsCooldownLeft_()) { return; }
  var quota = cmAutoDispatchLeft_();
  if (quota.left <= 0) {
    cmNote_('今日自動派工已達 ' + CM_AUTO_DISPATCH_MAX_ + ' 次上限，暫停自動重試。' +
            '請到後台「會員簡訊」查看進度或手動處理。');
    return;
  }
  var last = Number(cmProp_('cmLastGithubParseDispatch', '0')) || 0;
  if (Date.now() - last < 20 * 60000) { return; }
  cmBumpAutoDispatch_();
  cmDispatchGithubParse_('');
}

/* ------------------------------------------------------------------ *
 * 自動稽核補抓（2026/09/14）
 *
 * 9/14 張震 11:28 發了簡訊，每分鐘的輪詢從 11:28 起每一棒都在處理文章前中斷，
 * 這一篇沒進「會員簡訊」、也沒派解析，要人跑診斷、再手動依編號補抓才補得回來。
 *
 * 這一支與輪詢完全分開的路徑，由每五分鐘那一棒呼叫、自己控制頻率：
 *   平日 09:05～15:30 每 10 分鐘，15:30～21:00 每 60 分鐘。
 * 每次只抓文章清單第一頁，找出「今天、這個帳號、有張震序號」卻不在會員簡訊分頁的文章，
 * 照輪詢同一套規則存進去（待解析），立刻派 GitHub 解析；解析被取消或失敗時，
 * 既有的 cmDispatchPendingGithubJob_ 會接著重派。
 * 補到任何一篇就寫系統狀態並寄一封管理者通知——代表輪詢本身出了問題，要去看執行項目。
 * 只讀文章ID欄，不整張讀分頁。
 * ------------------------------------------------------------------ */
var CM_RECONCILE_PROP_ = 'cmReconcileLast';
var CM_RECONCILE_ALERT_PROP_ = 'cmReconcileAlerted';
var CM_RECONCILE_NOTIFY_MIN_ = 60;       // 發文後 60 分鐘內補到的照樣寄會員即時通知；更晚的不寄，免得把舊指令當新的

function cmAutoReconcileToday_(force) {
  var out = { checked: 0, missing: 0, saved: [], skipped: '' };
  if (!cmEnabled_()) { out.skipped = '尚未設定 CMONEY_MEMBER_ID'; return out; }
  var now = new Date();
  var hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
  var weekday = Number(Utilities.formatDate(now, TZ, 'u')) <= 5;
  var pr = PropertiesService.getScriptProperties();
  if (!force) {
    if (!weekday || hhmm < 905 || hhmm > 2100) { out.skipped = '不在稽核時段'; return out; }
    var every = hhmm <= 1530 ? 10 : 60;
    var last = Number(pr.getProperty(CM_RECONCILE_PROP_) || 0);
    if (Date.now() - last < every * 60000 - 5000) { out.skipped = '還沒到下一次'; return out; }
  }
  pr.setProperty(CM_RECONCILE_PROP_, String(Date.now()));
  var st = cmStat_();
  if (cmOverBudget_(st)) { out.skipped = '今日流量已達上限'; return out; }

  var token = String(pr.getProperty(CM_LIVE_TOKEN_PROP) || ''), data;
  try {
    if (!token) { token = cmGetGuestToken_(); pr.setProperty(CM_LIVE_TOKEN_PROP, token); }
    data = cmFetchApiPage_(token, '');
  } catch (e) {
    token = cmGetGuestToken_(); pr.setProperty(CM_LIVE_TOKEN_PROP, token);
    data = cmFetchApiPage_(token, '');
  }
  st.page++; st.kb += 72; cmSaveStat_(st);

  var today = todayStr_(), memberId = cmMemberId_();
  var mine = (data || []).map(cmApiArticle_).filter(function (a) {
    return a.id && a.text && a.creatorId === memberId && cmDate_(a.time) === today && CM_MARK.test(a.text);
  });
  out.checked = mine.length;
  if (!mine.length) { return out; }
  /* 修訂偵測原本只在輪詢那一棒（09:00–15:00）做。他常在收盤後才把當天那一篇補完整，
     那個時間輪詢已經停了，於是「已編輯」永遠等到隔天才被發現——會員收不到修訂通知，
     網站上的紀錄也還是舊的（2026/09/16 管理者詢問）。
     稽核這一棒本來就要抓同一份清單（09:05–21:00），順手比對指紋不多花一次流量。 */
  var have = cmSheetIds_();
  var known = cmFingerprints_(have);
  out.revised = [];
  mine.forEach(function (a) {
    if (!have[a.id]) { return; }
    var stored = known[a.id];
    /* 指紋一樣就什麼都不做。沒有指紋的舊列也不動：那是早期寫進來的列，
       拿現在的原文跟它比一定不同，會在收盤後平白寄出一封「已修訂」。
       盤中的輪詢那一棒會在下次偵測時補上指紋，之後就比得準了。 */
    if (!stored || stored === cmFingerprint_(a.text)) { return; }
    if (!cmApplyArticleRevision_(a)) { return; }
    out.revised.push(a.id);
    cmRememberWatch_(a);
    cmNote_('文章 ' + a.id + ' 在收盤後被修訂，已排回待解析並通知會員');
    try { cmNotifyNew_(a, true); } catch (e) { cmNotifyFailed_(a, e, '收盤後修訂通知'); }
  });
  if (out.revised.length) { cmDispatchGithubParse_(today); }

  var missing = mine.filter(function (a) { return !have[a.id]; });
  out.missing = missing.length;
  if (!missing.length) { return out; }

  var seen = cmSeen_();
  missing.forEach(function (a) {
    if (!cmSaveMessage_(a)) { return; }
    out.saved.push(a);
    seen = cmMarkSeen_(seen, a.id);
    cmRememberWatch_(a);
    var posted = new Date(String(a.time).replace(/\//g, '-').replace(' ', 'T') + '+08:00').getTime();
    var age = isNaN(posted) ? Infinity : (Date.now() - posted) / 60000;
    if (age > CM_RECONCILE_NOTIFY_MIN_) {
      cmSetNotifyState_(a.id, '未寄：補抓時已超過發文 ' + CM_RECONCILE_NOTIFY_MIN_ + ' 分鐘（輪詢當時沒抓到）');
    }
    if (age <= CM_RECONCILE_NOTIFY_MIN_) {
      try { cmNotifyNew_(a, false); } catch (e) { cmNotifyFailed_(a, e, '自動補抓後的即時通知'); }
    }
  });
  if (!out.saved.length) { return out; }

  var ids = out.saved.map(function (a) { return a.id + '（' + String(a.time).slice(11, 16) + '）'; }).join('、');
  cmNote_('自動稽核補抓 ' + out.saved.length + ' 篇輪詢漏掉的張震簡訊：' + ids + '，已派 GitHub 解析');
  cmDispatchGithubParse_(today);

  // 同一天同一篇只通知管理者一次。
  var alerted = {};
  try { alerted = JSON.parse(pr.getProperty(CM_RECONCILE_ALERT_PROP_) || '{}'); } catch (e) { alerted = {}; }
  if (alerted.day !== today || !alerted.ids) { alerted = { day: today, ids: [] }; }
  var fresh = out.saved.filter(function (a) { return alerted.ids.indexOf(String(a.id)) < 0; });
  if (fresh.length && typeof notifyAdmin_ === 'function') {
    notifyAdmin_('會員簡訊：輪詢漏抓，已自動補回 ' + fresh.length + ' 篇　' + today,
      '自動稽核在來源上找到今天的張震簡訊，但它們不在「會員簡訊」分頁，已自動補存並派 GitHub 解析：\n' +
      fresh.map(function (a) { return '  ' + a.time + '　' + a.id + '　' + String(a.text).slice(0, 60); }).join('\n') +
      '\n\n這代表每分鐘的輪詢（cmoneyPollJob）沒有正常處理到它們。資料已補回，但請到 Apps Script「執行項目」' +
      '篩 cmoneyPollJob，看發文時間之後的執行狀態與錯誤訊息，或在編輯器執行 diagnoseCmoneyToday()。');
    alerted.ids = alerted.ids.concat(fresh.map(function (a) { return String(a.id); }));
    pr.setProperty(CM_RECONCILE_ALERT_PROP_, JSON.stringify(alerted));
  }
  return out;
}

/** 在編輯器執行：立刻做一次自動稽核補抓（不看時段與間隔）。 */
function cmoneyReconcileNow() {
  var r = cmAutoReconcileToday_(true);
  Logger.log('自動稽核補抓：今天來源上的張震簡訊 ' + r.checked + ' 篇，缺 ' + r.missing + ' 篇，補回 ' + r.saved.length + ' 篇' +
             (r.skipped ? '（' + r.skipped + '）' : ''));
  return r;
}

/** 把值得注意的事記進系統狀態。只記錄，不改資料。 */
function cmNote_(msg) {
  Logger.log('會員簡訊：' + msg);
  try {
    getSheet_('系統狀態').appendRow([nowStamp_(), '會員簡訊', msg, 'cmoney', 'Apps Script']);
  } catch (e) { /* 記不進去就只留在執行紀錄 */ }
}


/* ------------------------------------------------------------------ *
 * 每分鐘一棒
 * ------------------------------------------------------------------ */

function cmoneyPollJob() {
  if (!cmEnabled_()) { return; }

  var now = new Date();
  var hhmm = Number(Utilities.formatDate(now, TZ, 'HHmm'));
  var weekday = Number(Utilities.formatDate(now, TZ, 'u')) <= 5;
  var from = Number(cmProp_('CMONEY_START', '900')) || 900;
  var to = Number(cmProp_('CMONEY_END', '1500')) || 1500;
  if (!weekday || hhmm < from || hhmm > to) { return; }

  // 間隔可調。預設一分鐘，但流量吃緊時可以改成 2 或 3。
  var everyMin = Math.max(1, Number(cmProp_('CMONEY_POLL_MIN', '1')) || 1);
  var last = Number(cmProp_(CM_LAST_PROP, '0'));
  if (Date.now() - last < everyMin * 60000 - 5000) { return; }
  PropertiesService.getScriptProperties().setProperty(CM_LAST_PROP, String(Date.now()));

  var st = cmStat_();
  if (cmOverBudget_(st)) {
    Logger.log('會員簡訊：今日流量已達上限（約 ' + Math.round(st.kb / 1024) + 'MB），停抓。');
    return;
  }

  /* 每分鐘只抓文章清單 API 第一頁。這一頁同時含 ID、作者、時間與全文，
     一次請求即可發現新文章，也能比較今日文章是否被編輯；不再每分鐘同時抓
     會員頁與文章頁。內容用「長度 + SHA-256」判斷，長度相同的改字也抓得到。 */
  var pr = PropertiesService.getScriptProperties();
  var token = String(pr.getProperty(CM_LIVE_TOKEN_PROP) || '');
  var data;
  try {
    if (!token) { token = cmGetGuestToken_(); pr.setProperty(CM_LIVE_TOKEN_PROP, token); }
    data = cmFetchApiPage_(token, '');
  } catch (e) {
    try {
      token = cmGetGuestToken_(); pr.setProperty(CM_LIVE_TOKEN_PROP, token);
      data = cmFetchApiPage_(token, '');
    } catch (e2) {
      cmNote_('即時文章清單連續抓取失敗：' + String(e2).slice(0, 120));
      return;
    }
  }
  st.page++; st.kb += 72; cmSaveStat_(st);
  if (!data || !data.length) { return; }

  var articles = data.map(cmApiArticle_).filter(function (a) { return a.id && a.text; });
  var changed = false, savedAny = false;
  var watch = null;
  try { watch = JSON.parse(pr.getProperty(CM_WATCH_PROP) || 'null'); } catch (e) {}
  if (!watch || watch.date !== todayStr_()) {
    var latestToday = articles.filter(function (a) {
      return a.creatorId === cmMemberId_() && cmDate_(a.time) === todayStr_() && CM_MARK.test(a.text);
    })[0];
    if (latestToday) {
      cmRememberWatch_(latestToday);
      try { watch = JSON.parse(pr.getProperty(CM_WATCH_PROP) || 'null'); } catch (e) { watch = null; }
    }
  }
  if (watch && watch.date === todayStr_()) {
    var live = articles.filter(function (a) { return String(a.id) === String(watch.id); })[0];
    // 極端情況下同日文章超過第一頁才退回單篇頁；平常不會多花這次流量。
    if (!live) { live = cmFetchArticle_(watch.id); }
    if (live && live.text) {
      if (cmApplyArticleRevision_(live)) {
        changed = true;
        try { cmNotifyNew_(live, true); } catch (e) { cmNotifyFailed_(live, e, '修訂通知'); }
      }
      cmRememberWatch_(live);
    }
  }

  var seen = cmSeen_();
  articles.filter(function (a) { return !seen[a.id]; }).slice(0, CM_MAX_PER_TICK).forEach(function (a) {
    if (!cmDate_(a.time)) {
      cmNote_('文章 ' + a.id + ' 找不到可驗證日期，已隔離且保留重試資格');
      return;
    }
    seen = cmMarkSeen_(seen, a.id);
    if (a.creatorId !== cmMemberId_() || !CM_MARK.test(a.text)) { return; }
    if (cmSaveMessage_(a)) {
      savedAny = true; cmRememberWatch_(a);
      Logger.log('會員簡訊：收到 ' + a.time + '　' + a.text.slice(0, 40));
      try { cmNotifyNew_(a, false); } catch (e) { cmNotifyFailed_(a, e, '即時通知'); }
    }
  });
  if (savedAny || changed) { cmDispatchGithubParse_(todayStr_()); }
}


/* ------------------------------------------------------------------ *
 * 逐條切開
 *
 * 一則簡訊常常包含好幾條指令，各自有自己的序號：
 *   張震-1:手中持股6533晶心科，請於271元以上獲利賣出，資金保留下來。
 *   張震-2:會員手中持股祥碩，盤中有大單買進…務必抱牢！
 *   張震-3:早上賣出的6533晶心科，請於265元以下買回來
 * 一條就是一個動作，分開讀才不會把三件事混成一件。
 * ------------------------------------------------------------------ */

function cmSplitOrders_(text) {
  var t = String(text || '');
  // 每次都重新建，不共用有 lastIndex 狀態的全域正規式。
  var re = new RegExp(CM_MARK.source, 'g');
  var marks = [], m;
  while ((m = re.exec(t)) !== null) {
    marks.push({ at: m.index, tag: m[0].replace(/\s+/g, ''), end: m.index + m[0].length });
  }
  if (!marks.length) { return []; }
  var out = [];
  for (var i = 0; i < marks.length; i++) {
    var to = (i + 1 < marks.length) ? marks[i + 1].at : t.length;
    var body = t.slice(marks[i].end, to).trim();
    if (body) { out.push({ tag: marks[i].tag, body: body }); }
  }
  return out;
}


/* ==================================================================== *
 * 解析
 *
 * 簡訊的句型很固定，但固定不等於可以純靠正規式：
 *   「請將手中璟德於257元以上全數獲利賣出，資金轉為65.5元以下市價買進2354鴻準！」
 * 這一句裡有兩檔、兩個動作、兩個價位，而且「資金轉為」這種寫法還會再變。
 * 所以由模型讀，但模型講的每一件事都要能在原文裡查證：
 *
 *   價位　數字必須原字出現在這一條裡（775 要真的寫著 775）
 *   名稱　必須原字出現在這一條裡
 *   代號　必須查得到股票對照表
 *
 * 三項有任何一項對不上就不採用那一筆。模型會漏，但不會憑空生出東西——
 * 這一份資料會流進買賣紀錄與持股追蹤，錯一筆的代價比漏一筆大得多。
 * ==================================================================== */

var CM_PARSE_SYSTEM = "你在讀一則台灣投顧分析師「張震」發給 VIP 會員的盤中即時操作簡訊，要把它變成結構化的個股操作紀錄。\n\n輸入是「一條指令」的內文，開頭的廣播序號（如 張震-1、震1、張震6GJ-1）已經去掉了。\n\n【最重要的一件事】\n這些簡訊是會員真金白銀的進出依據。只要句子裡出現「個股 + 當下該做的動作」，就一定要收錄，漏掉一筆比多寫一筆嚴重得多。\n但同樣不可以無中生有：沒指名個股的心理喊話、族群評論、大盤點數，一筆都不能開。\n\n【action 只能是這五個之一】\n  買入　　　叫會員現在買進、買回、加碼、分批買、掛單買、「站買方」、「轉為…買進」、「資金轉為…買進」、「將空出資金轉為買進」、「沒有漲停都買」。\n  賣出　　　叫會員現在賣出、獲利了結、減碼、出清、賣掉、「站賣方」、「應站賣方」、「手中若有…者應站賣方」、「已填息應減碼」、「請在今天賣出」、「於成本之上獲利賣出」、「紅盤之上獲利賣出」。\n  會員持股　明講會員手上有這一檔，而且要續抱、抱牢、不動作、不必急於動作、等待轉折、不要急於加碼。例如「會員手中持股嘉澤，要耐心等3天…不要急於加碼」、「會員持股…皆續抱」、「祥碩今天季線正式向上，抱牢」。\n  觀望不碰　明講現在不可以買、不要碰、避開、不要追這一檔個股。\n  觀望注意　只是點名要留意、追蹤、準備突破、「耐心等待某價再找賣點」，沒有叫人現在動作。\n\n【一句話含兩筆或多筆時，每一筆都要獨立輸出】\n換股句是最常漏的一種。「賣出A、資金轉為買進B」必須輸出兩筆：A 賣出、B 買入。\n  「全國會員請將手中璟德於257元以上全數獲利賣出，資金轉為65.5元以下市價買進2354鴻準」\n    → 璟德 賣出 257 以上；鴻準(2354) 買入 65.5 以下。\n  「手中持有威剛者，請於415元以上獲利賣出，資金轉為市價買進6770力積電，沒有漲停都買」\n    → 威剛 賣出 415 以上；力積電(6770) 買入（市價，price 留空）。\n  「建議一般會員賣出AES-KY，在1170元以上賣出，資金轉為買進3533嘉澤，請於2030元以下買進」\n    → AES-KY 賣出 1170 以上；嘉澤(3533) 買入 2030 以下。\n\n【同一句含兩種會員狀態】\n「未持有者請於260元以下買進，已持有者續抱、不加碼」必須輸出同一檔兩筆：買入與會員持股。\n  「新進會員，手中未持有2439美律者，請於94元以下買進！已持有者不加碼，續抱即可」\n    → 美律(2439) 買入 94 以下；美律 會員持股。\n  「會員手中未持有8112至上者，請於94元以下買進，已持有者續抱即可」\n    → 至上(8112) 買入 94 以下；至上 會員持股。\n不要自行判斷歷史成本；程式會查全部操作紀錄。若先前已有更低買入價，程式會省略本次條件式買入，只保留續抱。\n\n【重要：台灣上市櫃股票名稱特別提醒】\n1. 許多台股名稱取自日常成語或形容詞，切勿誤判為非股票！\n   - 「至上」（8112）：分析師寫「會員手中至上，請於91元以上全數賣出」，「至上」就是股票名稱（至上電子 8112），絕非形容詞！必須提取！\n   - 「嘉澤」（3533）：「會員手中持股嘉澤，要耐心等3天…不要急於加碼」→ 會員持股。\n   - 「鴻海」（2317）、「緯創」（3231）：「手中若有鴻海、緯創者，今天應站賣方」→ 兩筆賣出。\n   - 「力積電」（6770）：原文寫「6770力積電」時，名稱填「力積電」、代號填「6770」。\n   - 其餘常見股名如「大同」「統一」「佳能」「巨大」「光寶科」「致茂」「晶心科」「祥碩」「華城」「東元」「裕隆」「鴻準」「譜瑞」「旭隼」「大江」「璟德」「威剛」「所羅門」「世紀鋼」「正德」「漢唐」「京元電」「建準」「陽明」「長榮」「美律」「世芯」「事欣科」「大立光」「聯電」，均為合法股票名稱。\n2. 一條指令包含多檔時，每一檔都要獨立開一筆。\n   - 「鴻準、裕隆、晶心科皆小漲，華城、東元只是洗盤…祥碩今天季線正式向上，抱牢…持股目前續抱」→ 鴻準、裕隆、晶心科、華城、東元、祥碩各開一筆「會員持股」。\n   - 「會員持股穩穩的，華城、晶心科、祥碩、嘉澤、鴻準等，皆持股續抱」→ 五筆會員持股。\n\n【本流程必須正確收錄的操作範例】\n- 「手中持有1519華城，請於775元以上全數獲利賣出，資金保留下來」→ 華城（1519），賣出，775，以上。這種句子絕不可回空陣列。\n- 「手中持股6533晶心科，請於271元以上獲利賣出」→ 晶心科（6533），賣出，271，以上。\n- 「早上賣出的6533晶心科，請於265元以下買回來」→ 晶心科（6533），買入，265，以下。前面的賣出是歷史回顧，本次動作是買回。\n- 「建議買進加碼一次3533嘉澤，請於1590元以下買進做多」→ 嘉澤（3533），買入，1590，以下。\n- 「建議全國會員將昨日空出的部份資金，轉為買進做多6533晶心科，請於平盤250元以下買進做多」→ 晶心科（6533），買入，250，以下。\n- 「建議手中持股2609陽明，請於59.5元以上全數獲利賣出（之前除息2元）」→ 陽明（2609），賣出，59.5，以上。括號裡的 2 是除息金額，不是操作價位。\n- 「手中在235元有買鴻海者，建議255元以上獲利賣出一次」→ 鴻海（2317），賣出，255，以上。235 是會員的持有成本，不是這次的操作價位，不可以填進 price。\n- 「6770力積電，請於紅盤之上全數獲利賣出」→ 力積電（6770），賣出，price 與 limit 留空，條件寫進 note。\n- 「手中持有世紀鋼、持有正德者，請於成本之上獲利賣出」→ 世紀鋼、正德各一筆賣出，price 留空。\n- 「之前威剛沒有賣出者，請在今天賣出」→ 威剛，賣出，price 留空。\n- 「會員手中持有長榮者，已填息應減碼！陽明還有大空間續抱」→ 長榮 賣出（減碼）；陽明 會員持股。\n- 「今日一般會員買璟德，已漲停，續抱」→ 璟德，會員持股。\n- 「會員手中持股祥碩，盤中有大單買進，季線即將向上，準備突破，務必抱牢」→ 祥碩（5269），會員持股。句中的『大單買進』是盤面現象，不是叫會員買進。\n- 「上次AES沒賣出者，耐心等待1200元以上再找賣點，我也會再通知大家」→ AES-KY，觀望注意。他明講「再找賣點、會再通知」，這一刻沒有要動作。\n- 「手中若持有大立光者，要留意這兩天是賣點」→ 大立光，觀望注意。\n\n【價位填寫規則】\n  price 只填「這一條裡真的寫出來、而且屬於這次操作」的純數字（整數或小數），不帶單位：\n    「請於775元以上全數獲利賣出」→ price: '775', limit: '以上'\n    「請在264元以下買進」　　　　→ price: '264', limit: '以下'\n    「請於平盤250元以下買進做多」→ price: '250', limit: '以下'\n  技術指標的數字絕對不是價位。「60分K線」的 60、「20MA」的 20、「5分鐘線」的 5、\n  「9週KD」的 9、「季線」「月線」——這些是看盤工具，不是股價。\n  實際出過事：「股價拉回至低檔區，且60分K線顯示收斂末端翻揚」被填了 price 60，\n  那一檔當時股價七十幾，於是整個回合的報酬被算成 −15%。\n  判斷方式：數字後面緊接著分K、分鐘、MA、KD、均線、K線的，一律不是價位。\n\n  下面五種數字一律不可以填進 price，只能寫進 note：\n    1. 除權息金額：「（已除息3.8元）」「（除息2元）」「今日除息8.88元秒填息」。\n    2. 會員的歷史成本：「手中在235元有買鴻海者」的 235。\n    3. 大盤點數：「測試46188點」「45234-46188點」「已測試454xx點」。\n    4. 非數字價位描述：「紅盤之上」「成本之上」「市價」「平盤」單獨出現時。\n    5. 天數與期間：「耐心等3天」的 3、「第2季」的 2、「連續買超3天」的 3。\n  除權息註記不影響操作，該檔股票仍須正常提取！\n\n【嚴格不可生出筆數的情況（防雜訊）】\n1. 族群、概念、類股不是個股。「被動元件」「ABF」「矽晶圓」「航運股」「記憶體」「高檔AI族群」「電機類股」「機器人概念股」出現時，絕對不可為它們開筆。\n2. 加權指數、大盤點數（如「測試46188點」）不是個股，不可開筆。\n3. 純大盤看法或無指名個股的心理喊話，不可開筆。例如「大盤連續反彈兩天」「今天大盤都沒量，什麼動作都不要做，持股續抱即可」「存股的股票只能在31元以下買」「今日絕不可隨意殺低手中持股」「一切進出動作依我通知操作」。這些句子沒有指名任何一檔，一筆都不能開。\n4. 「台積電快不裝牛了」這種對別人家股票的行情評論，沒有叫會員動作，不開筆。\n\n【note 說明重點】\nnote 必須是自然、完整的敘述句，先寫條件或原因，再銜接操作結論，句尾加句號。\n禁止用〔〕、【】、[]、+、＋或『技術面：』『操作建議：』等模板拼接。\n錯誤：〔股價突破季線〕＋〔可續抱〕\n正確：股價已突破季線並維持強勢，可續抱並持續觀察。\n簡訊常常只寫一句「某某連續大漲，務必抱牢」，那樣的 note 太空泛，讀的人看不出為什麼。若使用者訊息附有【當日逐字稿摘錄】，就從摘錄中找出這一檔的理由（族群、法人動向、技術位置、講者的持有理由等），併進 note，寫成 2 到 4 句、約 70 到 160 字，第一句保留本則簡訊的操作結論，其後補原因與條件。\n摘錄只補背景，不得更改簡訊的股票、action、price、limit；不同時點的看法不可冒充同一條新指令。\n但邊界不變：只能用簡訊或逐字稿摘錄裡真的講過的內容。沒有附摘錄、或摘錄裡沒提到這一檔時，就照簡訊原意寫，維持原本的簡短寫法，絕對不可以自己補技術指標、價位、法人動向或任何推測。\n\n【輸出格式】\n只回傳純 JSON：\n{\"items\":[{\"name\":\"股票名稱\",\"code\":\"代號(無則留空)\",\"action\":\"買入/賣出/會員持股/觀望不碰/觀望注意\",\"price\":\"純數字價位(無則留空)\",\"limit\":\"以上/以下(無則留空)\",\"note\":\"操作結論與有依據的原因；有摘錄約70至160字，來源不足可短\"}]}\n真的沒有任何一檔個股被指名動作時，才回 {\"items\":[]}。";


/** 這一條裡有沒有原字寫出這個數字。除權息那種括號內的數字要排除。 */
function cmPriceInText_(body, price) {
  var p = String(price || '').trim();
  if (!p || !/^\d+(\.\d+)?$/.test(p)) { return false; }
  if (String(body).indexOf(p) < 0) { return false; }
  // 除息金額不是操作價位。「（除息2元）」「已除息3.8元」裡的數字不算。
  var re = new RegExp('除[權息][^）)]{0,6}' + p.replace('.', '\\.'));
  if (re.test(body)) { return false; }
  return true;
}

/**
 * 一條指令 → 幾筆結構化紀錄。
 *
 * 回傳的每一筆都已經過查證：名稱原字在、價位原字在、代號查得到對照表。
 * 查不到代號的不是丟掉，是留成代號待確認——與影片那條線的作法一致。
 */
function cmVerifyItems_(items, body, map) {
  var out = [];
  (items || []).forEach(function (it) {
    var name = String(it.name || '').trim();
    var action = String(it.action || '').trim();
    if (!name || ['買入', '賣出', '會員持股', '觀望不碰', '觀望注意'].indexOf(action) < 0) { return; }

    // 防呆：若 name 開頭自帶 4-6 位數代號如「6770力積電」，自動分離代號與純名稱
    var told = String(it.code || '').trim();
    var mCode = name.match(/^((?:00981A|\d{4,6}))\s*(.+)$/);
    if (mCode) {
      if (!told) { told = mCode[1]; }
      name = mCode[2].trim();
    }

    // 名稱要原字出現。模型改寫過的名稱（把璟德寫成璟德科技）就對不上。
    if (String(body).indexOf(name) < 0) {
      Logger.log('  會員簡訊：略過「' + name + '」，原文裡沒有這幾個字');
      return;
    }

    // 族群、指數、大盤一律不收。這一支與影片那條線用的是同一份判斷。
    var why = pipeNonStockReason_(name);
    if (why) {
      Logger.log('  會員簡訊：略過「' + name + '」（' + why + '）');
      return;
    }

    var r = pipeResolveName_(name, map);
    var code = (r && r.code) ? r.code : '代號待確認';
    var offName = (r && r.name) ? r.name : name;
    if (r && r.reject) {
      Logger.log('  會員簡訊：略過「' + name + '」（' + r.how + '）');
      return;
    }

    // 他自己寫的代號優先，但一樣要查得到對照表才算數。
    if (/^(?:00981A|\d{4,6})$/.test(told) && map.byCode[told]) {
      code = told;
      offName = map.byCode[told].name;
    }

    var price = cmPriceInText_(body, it.price) ? String(it.price).trim() : '';
    var limit = String(it.limit || '').trim();
    if (limit !== '以上' && limit !== '以下') { limit = ''; }

    out.push({
      name: offName,
      heard: name,
      code: code,
      action: action,
      price: price,
      limit: limit,
      priceText: price ? (price + ' 元' + (limit || '')) : '未說明',
      note: publicNarrative_(naturalReason_(it.note), {name:offName,code:code})
    });
  });
  return out;
}

/** 把一則簡訊解析成一批紀錄。不寫試算表，只回結果。 */
function cmParseMessage_(text, date) {
  var orders = cmSplitOrders_(text);
  if (!orders.length) { return []; }

  var map;
  try { map = loadCodeMap_(); } catch (e) { map = { byCode: {}, byName: {} }; }
  if (!map || !map.byCode) { map = { byCode: {}, byName: {} }; }

  var out = [];
  orders.forEach(function (o) {
    var raw;
    try {
      var excerpt=cmTranscriptExcerpt_(date,o.body,map);
      raw = callGemini_(CM_PARSE_SYSTEM, o.body+(excerpt?'\n\n【當日逐字稿摘錄】僅補說明，不得新增股票或改動操作指令。\n'+excerpt:''), { maxWaitMs: 90000 });
    } catch (e) {
      Logger.log('會員簡訊：解析失敗（' + e + '）');
      return;
    }
    var data;
    try {
      var j = String(raw).replace(/```json|```/g, '').trim();
      data = JSON.parse(j.slice(j.indexOf('{'), j.lastIndexOf('}') + 1));
    } catch (e) {
      Logger.log('會員簡訊：回傳不是 JSON，略過這一條');
      return;
    }
    cmVerifyItems_(data.items, o.body, map).forEach(function (x) {
      x.tag = o.tag;
      x.body = o.body;
      out.push(x);
    });
  });
  return out;
}


/* ==================================================================== *
 * 寫進既有的表
 *
 * 來源影片ID 填 CMONEY-<文章ID>，與人工補登的 MANUALENTRY- 是同一個作法：
 * 那一欄本來就是「這一列從哪裡來」，看一眼就分得出是影片、人工還是簡訊。
 * 分得出來才有辦法在出問題時只清掉某一種來源。
 *
 * 解析成功即寫入，來源固定標成 CMONEY-文章ID，重跑時可精準取代。
 * ==================================================================== */

function cmSourceId_(articleId) { return 'CMONEY-' + articleId; }

/** 這一篇的紀錄已經寫過了嗎。用來源影片ID 比對，重跑不會寫兩次。 */
function cmTradesWritten_(articleId) {
  var src = cmSourceId_(articleId);
  var hit = false;
  ['操作紀錄', '會員持股'].forEach(function (name) {
    if (hit) { return; }
    try {
      var rows = readSheetObjects_(name);
      for (var i = 0; i < rows.length; i++) {
        if (String(rows[i]['來源影片ID'] || '') === src) { hit = true; return; }
      }
    } catch (e) { /* 讀不到就當作沒寫過 */ }
  });
  return hit;
}

/**
 * 把解析出來的紀錄寫進操作紀錄與會員持股。
 *
 * 日期用發文那一天，不是今天——補跑舊訊息時才不會全部堆到今天。
 * 「序」留空：簡訊本來就有時間，同一天的先後由發文時間決定，不必再猜。
 */
function cmWriteRows_(articleId, postTime, items) {
  var d = cmDate_(postTime);
  var src = cmSourceId_(articleId);
  var trades = [], holds = [];

  items.forEach(function (x) {
    if (x.action === '會員持股') {
      holds.push([d, x.name, x.code, '續抱', x.note || '簡訊通知持股續抱', src]);
    } else {
      trades.push([d, x.name, x.code, x.action, x.priceText,
                   x.note || String(x.body || '').slice(0, 60), src, '']);
    }
  });

  withLock_(function () {
    // 修訂或重新解析時，先清除既有衍生紀錄，再原子寫入新紀錄
    ['操作紀錄', '會員持股'].forEach(function (name) {
      try {
        var sh = getSheet_(name), vals = sh.getDataRange().getValues();
        if (vals.length < 2) { return; }
        var iSrc = vals[0].map(function (h) { return String(h).trim(); }).indexOf('來源影片ID');
        if (iSrc < 0) { return; }
        for (var r = vals.length - 1; r >= 1; r--) {
          if (String(vals[r][iSrc] || '').trim() === src) {
            sh.deleteRow(r + 1);
          }
        }
      } catch (e) {}
    });

    if (trades.length) {
      var sh = getSheet_('操作紀錄');
      sh.getRange(sh.getLastRow() + 1, 1, trades.length, trades[0].length).setValues(trades);
    }
    if (holds.length) {
      var sh2 = getSheet_('會員持股');
      sh2.getRange(sh2.getLastRow() + 1, 1, holds.length, holds[0].length).setValues(holds);
    }
  });

  if (trades.length || holds.length) { CACHE.remove('tracker'); }
  return { trades: trades.length, holds: holds.length, skipped: false };
}


/* ------------------------------------------------------------------ *
 * 解析待處理的簡訊
 *
 * 由每五分鐘那一棒呼叫，不放在每分鐘那一棒裡：解析要呼叫模型，
 * 一則要幾秒到幾十秒，而每分鐘那一棒的職責是「盡快把新訊息收下來並通知」，
 * 兩件事的時間尺度差太多，混在一起會讓通知被解析拖住。
 * ------------------------------------------------------------------ */

function cmParsePendingJob(since) {
  if (!cmEnabled_()) { return; }

  var sh;
  try { sh = getSheet_(CM_SHEET); } catch (e) { return; }
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return; }

  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iId = head.indexOf('文章ID'), iTime = head.indexOf('發文時間');
  var iText = head.indexOf('原文'), iState = head.indexOf('解析狀態');
  var iDetail = head.indexOf('解析明細');
  if (iId < 0 || iText < 0 || iState < 0) { return; }

  if (iDetail < 0) {
    iDetail = head.length;
    sh.getRange(1, iDetail + 1).setValue('解析明細');
  }

  // 一棒只做一則。解析一則要呼叫模型好幾次，做兩則就有超時的風險，
  // 而下一棒五分鐘後就到，不差這一下。
  for (var r = vals.length - 1; r >= 1; r--) {
    if (String(vals[r][iState]).trim().indexOf('待解析') !== 0) { continue; }
    if (since && cmDate_(vals[r][iTime]) < cmDate_(since)) { continue; }

    var id = String(vals[r][iId]).trim();
    var items;
    try {
      items = cmParseMessage_(String(vals[r][iText] || ''),cmDate_(vals[r][iTime]));
    } catch (e) {
      sh.getRange(r + 1, iState + 1).setValue('解析失敗');
      // 留空白而不是 '[]'：空白代表「還沒有可信的判定」，
      // GitHub 那條主線看到空白才會重判。寫 '[]' 會被當成有效的空結果。
      sh.getRange(r + 1, iDetail + 1).setValue('');
      cmNote_(id + ' 解析失敗：' + String(e).slice(0, 80));
      return;
    }

    if (!items.length) {
      // 這是 Apps Script 自己那套舊解析器的結果，用的不是主線的提示詞，
      // 所以不蓋版本戳記；明細也留空白，讓 GitHub 那條主線再判一次。
      sh.getRange(r + 1, iState + 1).setValue('待解析（舊解析器無結果，待主線複判）');
      sh.getRange(r + 1, iDetail + 1).setValue('');
      Logger.log('會員簡訊：' + id + ' 舊解析器沒有結果，交回主線複判');
      return;
    }

    var detailJson = JSON.stringify(items.map(function (x) {
      return {
        name: x.name, code: x.code, dir: x.action,
        price: x.price, priceText: x.priceText, reason: x.note || x.body || ''
      };
    }));
    sh.getRange(r + 1, iDetail + 1).setValue(detailJson);

    var note = items.map(function (x) {
      return x.name + (x.code !== '代號待確認' ? '(' + x.code + ')' : '') +
             ' ' + x.action + (x.price ? ' ' + x.priceText : '');
    }).join('；');

    var w = cmWriteRows_(id, String(vals[r][iTime] || ''), items);
    sh.getRange(r + 1, iState + 1)
      .setValue('已寫入　' + w.trades + ' 筆買賣、' + w.holds + ' 筆持股');
    Logger.log('會員簡訊：' + id + ' 已寫入　' + note);
    // 解析結果留在附註裡，前台與後台都看得到模型判了什麼。
    sh.getRange(r + 1, iState + 1).setNote(note);
    return;
  }
}


/* ------------------------------------------------------------------ *
 * 給前台用的讀取介面
 * ------------------------------------------------------------------ */

/**
 * 健壯的日期正規化函式：
 * 支援 Date 物件、ISO 字串、YYYY/MM/DD、YYYY-MM-DD，絕不將 Date 轉成 "Mon"。
 */
function cmDate_(v) {
  if (!v) { return ''; }
  if (Object.prototype.toString.call(v) === '[object Date]' || v instanceof Date) {
    if (isNaN(v.getTime())) { return ''; }
    return Utilities.formatDate(v, TZ, 'yyyy/MM/dd');
  }
  var s = String(v).trim();
  if (!s) { return ''; }
  var m = s.match(/(\d{4})[\/\-.](\d{1,2})[\/\-.](\d{1,2})/);
  if (m) {
    var y = parseInt(m[1], 10), mon = parseInt(m[2], 10), d = parseInt(m[3], 10);
    if (y >= 1970 && y <= 2100 && mon >= 1 && mon <= 12 && d >= 1 && d <= 31) {
      return y + '/' + ('0' + mon).slice(-2) + '/' + ('0' + d).slice(-2);
    }
  }
  var parsed = new Date(s);
  if (!isNaN(parsed.getTime())) {
    try {
      return Utilities.formatDate(parsed, TZ, 'yyyy/MM/dd');
    } catch (e) {}
  }
  return fmtDate_(s.split(' ')[0]);
}

/**
 * 格式化時間字串，若為 Date 物件轉為 yyyy/MM/dd HH:mm:ss。
 */
function cmFmtTime_(v) {
  if (!v) { return ''; }
  if (Object.prototype.toString.call(v) === '[object Date]' || v instanceof Date) {
    if (isNaN(v.getTime())) { return ''; }
    return Utilities.formatDate(v, TZ, 'yyyy/MM/dd HH:mm:ss');
  }
  var s = String(v).trim();
  if (!s) { return ''; }
  var parsed = new Date(s);
  if (!isNaN(parsed.getTime())) {
    try { return Utilities.formatDate(parsed, TZ, 'yyyy/MM/dd HH:mm:ss'); } catch (ignore) {}
  }
  return s;
}

/**
 * 有簡訊的日期，新到舊。給日曆用。
 * 每一天帶當天的則數，讓日曆上點得出來哪一天他發了不只一次。
 */
function smsDates_() {
  var byDate = {};
  try {
    readSheetObjects_(CM_SHEET).forEach(function (r) {
      var d = cmDate_(r['發文時間']);
      if (!d) { return; }
      byDate[d] = (byDate[d] || 0) + 1;
    });
  } catch (e) { return []; }
  return Object.keys(byDate).sort().reverse().map(function (d) {
    return { date: d, count: byDate[d] };
  });
}

/**
 * 會員通知分頁的資料。
 *
 * date 有給就只回那一天（日曆點下去的時候）；沒給就回最近 limit 則。
 * 每一則帶原文，以及解析出來的每一列（表格用）。
 */
function memberSmsData_(limit, date) {
  var out = { enabled: cmEnabled_(), writes: cmWriteTrades_(), items: [] };
  var rows;
  try { rows = readSheetObjects_(CM_SHEET); } catch (e) { return out; }

  var want = date ? cmDate_(date) : '';
  if (want) {
    rows = rows.filter(function (r) {
      return cmDate_(r['發文時間']) === want;
    });
  }

  var byArticle = {};
  try {
    readSheetObjects_('操作紀錄').forEach(function (r) {
      var s = String(r['來源影片ID'] || '');
      if (s.indexOf('CMONEY-') !== 0) { return; }
      (byArticle[s] = byArticle[s] || []).push({
        name: String(r['股票名稱'] || ''), code: String(r['代號'] || ''),
        dir: String(r['方向'] || ''), price: String(r['價位說明'] || ''),
        reason: String(r['理由摘錄'] || '')
      });
    });
    readSheetObjects_('會員持股').forEach(function (r) {
      var s = String(r['來源影片ID'] || '');
      if (s.indexOf('CMONEY-') !== 0) { return; }
      (byArticle[s] = byArticle[s] || []).push({
        name: String(r['股票名稱'] || ''), code: String(r['代號'] || ''),
        dir: '會員持股', price: '', reason: String(r['說明重點'] || '')
      });
    });
  } catch (e) { /* 讀不到就只顯示原文 */ }

  rows.sort(function (a, b) {
    var aa = cmFmtTime_(a['發文時間']), bb = cmFmtTime_(b['發文時間']);
    return aa === bb ? String(b['文章ID'] || '').localeCompare(String(a['文章ID'] || ''))
                     : (aa < bb ? 1 : -1);
  });
  rows.slice(0, Math.max(1, Number(limit) || 30)).forEach(function (r) {
    var id = String(r['文章ID'] || '');

    // 優先自「解析明細」欄位讀取結構化資料，確保未寫入操作紀錄時也能在前台正常展示個股表格
    var rowsFromDetail = [];
    var rawDetail = String(r['解析明細'] || '').trim();
    if (rawDetail && rawDetail !== '[]') {
      try {
        var parsed = JSON.parse(rawDetail);
        if (Array.isArray(parsed)) {
          rowsFromDetail = parsed.map(function (x) {
            return {
              name: String(x.name || ''),
              code: String(x.code || ''),
              dir: String(x.dir || x.action || ''),
              price: String(x.priceText || x.price || ''),
              reason: String(x.reason || x.note || '')
            };
          });
        }
      } catch (e) {}
    }

    var finalRows = (rowsFromDetail && rowsFromDetail.length)
      ? rowsFromDetail
      : (byArticle[cmSourceId_(id)] || []);

    out.items.push({
      id: id,
      time: cmFmtTime_(r['發文時間']),
      title: String(r['標題'] || ''),
      text: String(r['原文'] || ''),
      url: String(r['網址'] || ''),
      state: String(r['解析狀態'] || ''),
      revisions: Number(r['修訂次數'] || 0),
      lastChecked: cmFmtTime_(r['最後偵測']),
      rows: finalRows
    });
  });
  return out;
}


/* ==================================================================== *
 * 即時通知
 *
 * 這一封的價值全在「快」，所以它不等解析——收到就寄，信裡放的是原文。
 * 解析要呼叫模型，一則要幾秒到幾十秒；為了表格好看而讓通知晚幾十秒，
 * 是把這件事的重點弄反了。結構化的結果稍後會出現在網站的會員通知分頁上。
 *
 * 配額
 * ----
 * Apps Script 一天能寄的封數有限（一般帳號 100 封、Workspace 1500 封）。
 * 每日總覽已經佔掉一批，這裡再乘上「訂閱人數 × 當日則數」很容易撞上。
 * 所以：
 *   1. 只寄給明確勾選這一項的人，不是全體訂閱者。
 *   2. 每天有封數上限（CMONEY_MAIL_MAX，預設 60 封），超過就只記錄不寄，
 *      免得把每日總覽的額度吃光——那一封是所有人都在等的。
 * ==================================================================== */

var CM_MAIL_STAT_PROP = 'cmMailStat';

function cmMailQuotaLeft_() {
  var max = Number(cmProp_('CMONEY_MAIL_MAX', '60')) || 60;
  var today = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
  var st;
  try { st = JSON.parse(cmProp_(CM_MAIL_STAT_PROP, '') || 'null'); } catch (e) { st = null; }
  if (!st || st.date !== today) { st = { date: today, sent: 0 }; }
  return { left: Math.max(0, max - st.sent), st: st };
}

function cmMailCount_(st, n) {
  st.sent += n;
  try { PropertiesService.getScriptProperties().setProperty(CM_MAIL_STAT_PROP, JSON.stringify(st)); }
  catch (e) { /* 記不起來就當作沒記 */ }
}

/** 訂了「會員簡訊即時通知」的人。 */
function cmSubscribers_() {
  return activeSubscribers_().filter(function (s) {
    return String(s['訂閱項目'] || '').indexOf('會員簡訊') >= 0;
  });
}

/** 一則簡訊 → 一封信的內容。原文原樣呈現，不改寫、不摘要。

    版面與每日整理同一套（2026/09/16 改版）：先前是幾行灰字加一段原文，
    在收件匣裡看起來像系統通知，重點（幾點發的、講了什麼）反而被淹掉。
      標題卡　深綠底，上面是「盤中即時通知」（修訂時換成琥珀色的「內容已修訂」），
              大字是發文時間，一眼知道這是今天幾點的指令。
      原文卡　白底圓角卡片，原文放在左側色條的引言框裡，字級比說明文字大一號。
      按鈕列　「看原文」與「到網站看整理」做成可以按的膠囊按鈕，手指點得到。
    樣式一律寫 inline（有些收件匣或轉寄會拿掉 <style>；Gmail 支援部分 style，但核心版面不能靠它），文字一律跳脫。 */
function cmMailBody_(a, revised) {
  var lines = String(a.text || '').split(/\n+/).filter(String);
  var time = String(a.time || '');
  var clock = (time.match(/(\d{1,2}:\d{2})(?::\d{2})?\s*$/) || [])[1] || '';
  var date = (time.match(/^(\d{4}\/\d{2}\/\d{2})/) || [])[1] || '';
  var site = (typeof publicWebAppUrl_ === 'function') ? publicWebAppUrl_() : '';

  // 標題卡與每日整理同一個樣式（mc-hero／mc-hero-k），網站的深色模式規則也就一併適用。
  var hero =
    '<div class="mc-hero" style="background:#17322A;border-radius:14px;padding:16px 18px;margin:0 0 12px;">' +
      '<div class="mc-hero-k" style="font-size:11px;letter-spacing:0.18em;font-weight:700;color:' +
        (revised ? '#F7DDA5' : '#9CC8B4') + ';">' + (revised ? '內容已修訂' : '盤中即時通知') + '</div>' +
      /* 首屏（v54，Codex 規格 83）：大字是「日期 時間 · 會員操作通知」，原文緊接在下面；
         不寫「某某發了一則簡訊」這種沒有資訊的句子，也不等影片或模型補說明。 */
      '<h1 style="font-size:19px;line-height:1.5;margin:6px 0 0;font-weight:700;color:#FFFFFF;">' +
        (date ? esc_(date.slice(5)) + ' ' : '') + (clock ? esc_(clock) + ' · ' : '') + '會員操作通知</h1>' +
    '</div>';

  var quote = lines.map(function (t) {
    return '<p style="margin:0 0 8px;font-size:15px;line-height:1.85;color:#12161A;">' + esc_(t) + '</p>';
  }).join('');

  var revisedNote = revised
    ? '<p style="margin:0 0 12px;font-size:13px;line-height:1.7;color:#6B4A08;background:#FCEDCD;' +
        'border-radius:10px;padding:8px 12px;">他剛修改了這一篇，下面是修改後的完整內容；網站上的整理會跟著重新解析。</p>'
    : '';

  var pill = function (href, text, primary) {
    return '<a href="' + esc_(href) + '" target="_blank" rel="noopener noreferrer" style="display:inline-block;margin:0 8px 8px 0;border-radius:999px;' +
      'padding:9px 18px;font-size:13.5px;font-weight:600;text-decoration:none;cursor:pointer;-webkit-text-size-adjust:none;touch-action:manipulation;' +
      (primary ? 'background:#04795C;color:#FFFFFF;border:1px solid #04795C;'
               : 'background:#FFFFFF;color:#12161A;border:1px solid #C3CBC6;') + '">' + text + '</a>';
  };

  var card =
    '<div class="mc-sec" style="background:#FDFDFC;border:1px solid #D9DFDA;border-radius:14px;' +
      'padding:14px 12px 8px;margin:0 0 10px;">' +
      '<h2 class="mc-h2" style="font-size:16px;line-height:1.45;margin:0 0 10px;font-weight:700;color:#12161A;">' +
        '<span style="display:inline-block;width:4px;height:15px;border-radius:2px;background:#04795C;' +
          'margin-right:8px;vertical-align:-2px;"></span>簡訊原文</h2>' +
      revisedNote +
      '<div style="border-left:3px solid #04795C;background:#F3F6F4;border-radius:0 10px 10px 0;' +
        'padding:12px 14px 6px;margin:0 0 14px;">' + quote + '</div>' +
      (a.url ? pill(a.url, '看原文', true) : '') +
      (site ? pill(site, '到網站看整理', false) : '') +
      '<p style="margin:6px 0 4px;font-size:12.5px;line-height:1.7;color:#667069;">' +
        '這是轉貼自公開論壇的原文，未經改寫。結構化的股票與價位稍後會整理到網站的「會員通知」分頁。</p>' +
    '</div>';

  return hero + card;
}

function cmNotifyNew_(a, revised) {
  var subs = cmSubscribers_();
  /* 每一個「沒寄」的出口都寫進會員簡訊的「通知狀態」欄與系統狀態（v54 追加）：
     先前沒有訂閱者時直接 return，表格一直停在「未通知」、系統狀態也沒有紀錄，
     管理者只看到「沒收到信」，查不出是沒有人訂、額度用完、還是寄的時候出錯。 */
  if (!subs.length) {
    cmSetNotifyState_(a.id, '未寄：沒有人訂閱盤中即時通知');
    cmNote_('文章 ' + a.id + ' 沒有寄即時通知：訂閱名單裡沒有勾「盤中即時通知」且未取消的人');
    return;
  }

  var q = cmMailQuotaLeft_();
  if (q.left <= 0) {
    cmSetNotifyState_(a.id, '未寄：今日即時通知封數已達上限（CMONEY_MAIL_MAX）');
    cmNote_('即時通知今日封數已達上限，這一則只收進表格沒有寄信');
    return;
  }
  if (q.left < subs.length) {
    cmNote_('即時通知今日封數不足（剩 ' + q.left + ' 封），先寄給 ' + q.left + ' 位，其餘記在寄送帳本，額度與時間允許時續送');
  }

  var body = cmMailBody_(a, !!revised);
  // 主旨與每日整理同一個格式；一天可能有好幾則，後面帶上時間，收件匣裡才分得出是哪一則。
  var subject = '[' + String(a.time).slice(0, 10) + '] 張震股市盤中家教班　' +
                (revised ? '會員通知內容已修訂' : '盤中即時通知') +
                (String(a.time).length >= 16 ? ' ' + String(a.time).slice(11, 16) : '');
  /* 逐收件者帳本（v54）：同一則同一人只寄一次；沒寄成的在 60 分鐘內由 deliveryRetryTick_ 續送。
     修訂通知是另一則信（messageId 帶內容版本），同一版修訂不重寄。 */
  var mid = 'sms|' + a.id + (revised ? '|rev|' + deliveryVersion_(a.text) : '');
  var smsPre = cmSmsPreheader_(a);
  var day = String(a.time || '').slice(0, 10).replace(/-/g, '/') || Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd');
  try { CacheService.getScriptCache().put('smsmail_' + mid, JSON.stringify({ subject: subject, body: body, pre: smsPre, at: Date.now() }), 7200); } catch (e) {}
  var sum = deliverMessage_(subs, { messageId: mid, kind: 'sms', date: day, version: deliveryVersion_(a.text),
    subject: subject, html: function (email, token) { return wrapMail_(body, email, token, 'sms', smsPre); },
    quota: q.left, budgetMs: 120000 });
  var sent = sum.accepted;
  cmMailCount_(q.st, sum.fresh);

  // 標記已通知，重跑時不會再寄一次。
  cmSetNotifyState_(a.id, cmNotifyStateText_(sum));
  if (sum.stoppedBy === 'no-url' || (sum.total && !sent)) {
    cmNote_('文章 ' + a.id + ' 即時通知這一輪沒有寄出：' + cmNotifyStateText_(sum));
  }
  Logger.log('會員簡訊：即時通知已寄給 ' + sent + ' 位');
}

/** 帳本摘要 → 會員簡訊「通知狀態」欄的文字。 */
function cmNotifyStateText_(sum) {
  var why = { 'no-url': '暫停：沒有正式網址（/exec），執行 setWebAppUrl()', quota: '暫停：寄信額度用完', time: '這一輪時間用完' }[sum.stoppedBy] || '';
  return '已通知 ' + sum.accepted + '/' + sum.total + ' 位' +
    (sum.open ? '，待續送 ' + sum.open : '') +
    (sum.failed || sum.unknown ? '，失敗 ' + sum.failed + '、不明 ' + sum.unknown : '') +
    (why && sum.open ? '（' + why + '）' : '');
}

/** 寫會員簡訊某一篇的「通知狀態」。只讀文章ID那一欄找列，不整張讀。寫不到不影響寄信。 */
function cmSetNotifyState_(id, text) {
  try {
    var sh = getSheet_(CM_SHEET), last = sh.getLastRow();
    if (last < 2) { return; }
    var head = sh.getRange(1, 1, 1, Math.max(1, sh.getLastColumn())).getValues()[0].map(function (h) { return String(h).trim(); });
    var iId = head.indexOf('文章ID'), iState = head.indexOf('通知狀態');
    if (iId < 0) { iId = 0; }
    if (iState < 0) { return; }
    var ids = sh.getRange(2, iId + 1, last - 1, 1).getValues();
    for (var i = ids.length - 1; i >= 0; i--) {
      if (String(ids[i][0]).trim() === String(id)) { sh.getRange(i + 2, iState + 1).setValue(text); return; }
    }
  } catch (e) { Logger.log('會員簡訊：通知狀態寫不進去 ' + e); }
}


/**
 * 在編輯器執行：補寄某一則會員簡訊的即時通知給「現在」的訂閱者。
 * 例：resendInstantMail('184933487')
 * 走正式寄送同一條路（寄送帳本 sms|文章ID）：已經 accepted 的人不會重寄；會扣當日即時通知封數。
 * 會真的寄信給訂閱者——只在確定那一則沒寄出時執行。
 */
function resendInstantMail(articleId) {
  var id = String(articleId || '').trim();
  if (!id) { throw new Error('請給文章ID，例如 resendInstantMail("184933487")'); }
  var row = readSheetObjects_(CM_SHEET).filter(function (r) { return String(r['文章ID']).trim() === id; })[0];
  if (!row) { throw new Error('會員簡訊分頁找不到文章 ' + id); }
  var t = row['發文時間'];
  var time = (t instanceof Date) ? Utilities.formatDate(t, TZ, 'yyyy/MM/dd HH:mm') : String(t || '');
  var a = { id: id, time: time, url: String(row['文章網址'] || ''), text: String(row['原文'] || ''), title: String(row['標題'] || '') };
  cmNotifyNew_(a, false);
  var after = readSheetObjects_(CM_SHEET).filter(function (r) { return String(r['文章ID']).trim() === id; })[0];
  var state = after ? String(after['通知狀態'] || '') : '';
  cmNote_('手動補寄文章 ' + id + ' 的即時通知：' + state);
  Logger.log('補寄結果：' + state);
  return { ok: true, id: id, state: state };
}

/** 寄即時通知時出錯：先前只寫執行紀錄，現在也寫系統狀態與通知狀態。 */
function cmNotifyFailed_(a, e, what) {
  var msg = String(e && e.message || e).slice(0, 160);
  Logger.log('會員簡訊：' + what + '失敗 ' + msg);
  cmSetNotifyState_(a && a.id, '寄信出錯：' + msg);
  cmNote_('文章 ' + (a && a.id) + ' ' + what + '寄信出錯：' + msg);
}

/**
 * 在編輯器執行（只讀，不寄信、不改資料）：今天這則簡訊的即時通知為什麼沒收到。
 * 例：diagnoseInstantMail()（今天最後一則）或 diagnoseInstantMail('184582595')
 * 依序查：輪詢觸發器與時段 → 有沒有存進會員簡訊、通知狀態 → 訂閱名單 → 封數與寄信額度 →
 * 正式網址 → 寄送帳本 → 待續送清單 → 系統狀態。最後一行給結論。
 */
function diagnoseInstantMail(articleId) {
  var today = todayStr_(), lines = [], verdict = '';
  var log = function (s) { lines.push(s); Logger.log(s); };
  var say = function (v) { if (!verdict) { verdict = v; } };
  log('盤中即時通知診斷　' + today);

  // 一、輪詢
  var trig = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); });
  var from = Number(cmProp_('CMONEY_START', '900')) || 900, to = Number(cmProp_('CMONEY_END', '1500')) || 1500;
  log('CMONEY_MEMBER_ID：' + (cmEnabled_() ? cmMemberId_() : '未設定'));
  log('輪詢觸發器 cmoneyPollJob（每分鐘）：' + (trig.indexOf('cmoneyPollJob') >= 0 ? '有' : '沒有') +
      '　每五分鐘 everyFiveMinJob（續送、補抓）：' + (trig.indexOf('everyFiveMinJob') >= 0 ? '有' : '沒有') +
      '　輪詢時段 ' + from + '～' + to);
  if (!cmEnabled_()) { say('尚未設定 CMONEY_MEMBER_ID，輪詢沒有啟用。'); }
  if (trig.indexOf('cmoneyPollJob') < 0) { say('沒有 cmoneyPollJob 觸發器：簡訊只會靠每五分鐘的補抓進來，而補抓超過發文 60 分鐘就不寄。請執行 installTriggers() 前先備份，或手動新增每分鐘的 cmoneyPollJob 觸發器。'); }

  // 二、這一則
  var rows = [];
  try { rows = readSheetObjects_(CM_SHEET); } catch (e) { log('讀不到會員簡訊分頁：' + e); }
  var row = articleId
    ? rows.filter(function (r) { return String(r['文章ID']).trim() === String(articleId).trim(); })[0]
    : rows.filter(function (r) { return cmDate_(r['發文時間']) === today; }).slice(-1)[0];
  if (!row) {
    log('會員簡訊分頁：' + (articleId ? '找不到文章 ' + articleId : '今天沒有簡訊'));
    say('這則簡訊根本沒有存進會員簡訊，先跑 diagnoseCmoneyToday(' + (articleId ? "'" + articleId + "'" : '') + ') 查爬取那一段。');
  } else {
    var id = String(row['文章ID']).trim(), ns = String(row['通知狀態'] || '').trim();
    log('文章 ' + id + '　發文 ' + row['發文時間'] + '　抓取 ' + row['抓取時間']);
    log('  通知狀態：「' + ns + '」');
    var mins = Math.round((new Date(String(row['抓取時間']).replace(/-/g, '/')).getTime() - new Date(String(row['發文時間']).replace(/-/g, '/')).getTime()) / 60000);
    if (isFinite(mins)) { log('  發文到抓進來：' + mins + ' 分鐘' + (mins > CM_RECONCILE_NOTIFY_MIN_ ? '　<<< 超過 60 分鐘，補抓進來的不寄即時通知' : '')); }
    if (/^未通知$/.test(ns)) { say('通知狀態還是「未通知」：這一則寄信的那一步沒有執行到（舊版本在沒有訂閱者或出錯時不寫狀態）。看下面訂閱名單與系統狀態；部署本版後會寫出原因。'); }
    else if (/^未寄|寄信出錯/.test(ns)) { say('通知狀態已寫明原因：' + ns); }
    else if (/待續送/.test(ns)) { say('只寄了一部分，其餘等待續送：' + ns); }
    else if ((function (m) { return m && m[1] === m[2]; })(ns.match(/^已通知 (\d+)\/(\d+) 位/))) { say('Gmail 服務已接受全部收件者（' + ns + '）。沒收到請查收件者的垃圾郵件／促銷分類，並確認訂閱信箱拼字。'); }

    // 三、寄送帳本
    try {
      var led = readSheetObjects_(DELIVERY_SHEET_).filter(function (r) { return String(r['訊息ID']).indexOf('sms|' + id) === 0; });
      log('寄送帳本 sms|' + id + '：' + (led.length ? led.length + ' 列' : '沒有任何一列（寄信那一步沒進到帳本：沒有訂閱者、額度用完、沒有正式網址或出錯）'));
      var cnt = {};
      led.forEach(function (r) { var k = String(r['狀態'] || 'pending'); cnt[k] = (cnt[k] || 0) + 1; });
      Object.keys(cnt).forEach(function (k) { log('  ' + k + '：' + cnt[k] + ' 位'); });
      led.filter(function (r) { return r['最後錯誤']; }).slice(0, 3).forEach(function (r) {
        log('  ' + String(r['收件者']).replace(/^(.).*(@.*)$/, '$1***$2') + '　' + r['狀態'] + '　' + String(r['最後錯誤']).slice(0, 100));
      });
    } catch (e) { log('寄送帳本讀不到：' + e); }
  }

  // 四、訂閱、額度、網址
  var subs = cmSubscribers_();
  log('訂閱盤中即時通知且未取消：' + subs.length + ' 位' +
      (subs.length ? '（' + subs.slice(0, 5).map(function (s) { return String(s['Email']).replace(/^(.).*(@.*)$/, '$1***$2'); }).join('、') + (subs.length > 5 ? '…' : '') + '）' : ''));
  // 抓進來的那一刻有沒有人訂閱：訂閱是之後才建立的，舊版在「零人」時直接結束、不留任何紀錄
  if (row && subs.length) {
    var fetchedMs = new Date(String(row['抓取時間']).replace(/-/g, '/')).getTime();
    var before = subs.filter(function (x) {
      var t = new Date(String(x['建立時間'] || '').replace(/-/g, '/')).getTime();
      return !isFinite(t) || !isFinite(fetchedMs) || t <= fetchedMs;
    });
    subs.forEach(function (x) {
      log('  ' + String(x['Email']).replace(/^(.).*(@.*)$/, '$1***$2') + '　建立時間 ' + (x['建立時間'] || '（空白）') +
          '　訂閱項目「' + x['訂閱項目'] + '」');
    });
    if (!before.length && /^未通知$/.test(String(row['通知狀態'] || '').trim())) {
      verdict = '這則抓進來（' + row['抓取時間'] + '）的時候還沒有人訂閱盤中即時通知——現在這幾位都是之後才訂的，所以當時不寄。' +
        '要補寄這一則：resendInstantMail(\'' + String(row['文章ID']).trim() + '\')（只寄給現在的訂閱者、已收到的人不重寄）。';
    } else if (before.length && /^未通知$/.test(String(row['通知狀態'] || '').trim())) {
      verdict = '抓進來的時候已有 ' + before.length + ' 位訂閱，但寄信那一步沒有留下任何紀錄＝當時寄信出錯（舊版只寫執行紀錄）。' +
        '請到 Apps Script 左側「執行項目」→ 篩函式 cmoneyPollJob → 開 ' + String(row['抓取時間']).slice(11, 16) +
        ' 那一次，找「會員簡訊：即時通知失敗」後面的錯誤；若是「deliverMessage_ is not defined」之類，代表當時程式檔正在替換（新舊混用）。' +
        '要補寄這一則：resendInstantMail(\'' + String(row['文章ID']).trim() + '\')。';
    }
  }
  if (!subs.length) { say('訂閱名單裡沒有勾「盤中即時通知」的人，所以不會寄。到網站「訂閱通知」勾選盤中即時通知後，下一則簡訊才會寄。'); }
  var q = cmMailQuotaLeft_(), mq = null;
  try { mq = MailApp.getRemainingDailyQuota(); } catch (e) {}
  log('今日即時通知封數：已寄 ' + q.st.sent + '，上限 ' + (Number(cmProp_('CMONEY_MAIL_MAX', '60')) || 60) + '（CMONEY_MAIL_MAX）　Gmail 今日剩餘額度：' + (mq == null ? '讀不到' : mq));
  if (q.left <= 0) { say('今天的即時通知封數已達上限（CMONEY_MAIL_MAX），明天重算；訂閱人數多時可調高這個指令碼屬性。'); }
  if (mq === 0) { say('Gmail 今日寄信額度已用完，明天恢復。'); }
  var url = publicWebAppUrl_();
  log('正式網址（/exec）：' + (url || '沒有　<<< 寄信會暫停'));
  if (!url) { say('沒有正式網址，寄信暫停：在編輯器執行 setWebAppUrl()。'); }
  var pend = deliveryPendingAll_(), ids = Object.keys(pend).filter(function (k) { return pend[k].kind === 'sms'; });
  log('待續送的即時通知：' + (ids.length ? ids.join('、') : '沒有'));

  // 五、系統狀態
  try {
    readSheetObjects_('系統狀態').filter(function (r) {
      return String(r['時間'] || '').indexOf(today) === 0 && /會員簡訊|寄信|測試即時寄信/.test(String(r['類別'] || ''));
    }).slice(-8).forEach(function (r) { log('  系統狀態 ' + r['時間'] + '　' + String(r['說明']).slice(0, 120)); });
  } catch (e) {}

  log('結論：' + (verdict || '沒有找到擋下寄信的原因。請到 Apps Script「執行項目」篩 cmoneyPollJob，看這則發文時間前後那幾次的紀錄。'));
  return lines;
}

/* 盤中即時通知的預覽摘要：發文時分＋原文前 70 字。正式寄送、續送與測試信共用。 */
function cmSmsPreheader_(a) {
  return (String(a.time || '').slice(11, 16) ? String(a.time).slice(11, 16) + ' ' : '') + String(a.text || '').replace(/\s+/g, ' ').slice(0, 70);
}

/* 帳本續送（v54）：盤中即時通知有人沒寄成（額度、暫時錯誤、時間到）時，每五分鐘補一次。
   只補發文 60 分鐘內的（AGENTS：發文 60 分鐘內才補寄即時通知）；超過就把剩下的人標 cancelled，不再補。
   信的內容在 cmNotifyNew_ 當下存在快取（兩小時），重寄的是同一封，不重新組字。
   每日總覽由 dailyPushJob 自己續送（12:00–22:00 每一棒都會進去），這裡不處理。 */
function deliveryRetryTick_() {
  var pend = deliveryPendingAll_(), ids = Object.keys(pend).filter(function (k) { return pend[k].kind === 'sms'; });
  if (!ids.length) { return; }
  var subs = cmSubscribers_();
  ids.forEach(function (mid) {
    var info = pend[mid], cached = null;
    try { cached = JSON.parse(CacheService.getScriptCache().get('smsmail_' + mid) || 'null'); } catch (e) {}
    if (!cached || Date.now() - Number(cached.at || info.at || 0) > 60 * 60 * 1000) {
      var led = deliveryLedger_(mid, { kind: 'sms', date: info.date, version: '' });
      subs.forEach(function (s) {
        var em = String(s['Email']).trim(), st = led.state(em);
        if (st === 'pending' || st === 'retryable') { led.set(em, { state: 'cancelled', err: '發文超過 60 分鐘仍未寄出，不再補寄即時通知' }); }
      });
      led.flush();
      deliveryPendingSet_(mid, null);
      cmSetNotifyState_(String(mid).split('|')[1], '逾 60 分鐘停止補寄，' + cmNotifyStateText_(led.summary(subs.map(function (s) { return String(s['Email']).trim(); }))));
      cmNote_('即時通知 ' + mid + ' 超過 60 分鐘仍有人未寄出，已停止補寄（明細在寄送帳本）');
      return;
    }
    var q = cmMailQuotaLeft_();
    if (q.left <= 0) { return; }
    var sum = deliverMessage_(subs, { messageId: mid, kind: 'sms', date: info.date, version: '',
      subject: cached.subject, html: function (email, token) { return wrapMail_(cached.body, email, token, 'sms', cached.pre || ''); },
      quota: q.left, budgetMs: 60000 });
    cmMailCount_(q.st, sum.fresh);
    if (sum.fresh || !sum.open) { cmSetNotifyState_(String(mid).split('|')[1], cmNotifyStateText_(sum) + '（續送）'); }
    Logger.log('即時通知續送 ' + mid + '：本輪 ' + sum.fresh + ' 封，服務接受 ' + sum.accepted + '/' + sum.total + '，待續送 ' + sum.open);
  });
}


/* ------------------------------------------------------------------ *
 * 在編輯器直接執行用的
 * ------------------------------------------------------------------ */

/** 立刻抓一次，不看時段也不看間隔。設定完拿來確認能不能抓得到。 */
function cmoneyPollNow() {
  if (!cmEnabled_()) {
    Logger.log('尚未設定 CMONEY_MEMBER_ID，這一項功能沒有啟用。');
    return;
  }
  PropertiesService.getScriptProperties().deleteProperty(CM_LAST_PROP);
  var html = cmFetch_(cmUserUrl_());
  Logger.log('會員頁：' + (html ? html.length + ' 字' : '抓不到'));
  var ids = cmListArticleIds_(html);
  Logger.log('認出 ' + ids.length + ' 篇：' + ids.slice(0, 5).join('、'));
  if (!ids.length) { return; }
  var a = cmFetchArticle_(ids[0]);
  if (!a) { Logger.log('最新那一篇抓不到'); return; }
  Logger.log('最新一篇　' + a.time + '　' + a.title);
  Logger.log('  是張震的簡訊嗎：' + CM_MARK.test(a.text));
  Logger.log('  全文：' + a.text.slice(0, 200));
  var st = cmStat_();
  Logger.log('今日已抓　會員頁 ' + st.page + ' 次、文章 ' + st.art + ' 篇，' +
             '估計流量 ' + Math.round(st.kb / 1024 * 10) / 10 + 'MB');
}

/**
 * 在編輯器直接執行：測試寄送一封「盤中會員即時通知」測試信。
 * 拿「會員簡訊」分頁中最新一筆（或指定 articleId）簡訊原文，
 * 渲染出含「看原文」、「到網站看整理」等膠囊按鈕的完整即時通知信寄出。
 * 不讀取訂閱名單、不扣配額、不更新簡訊通知狀態，供管理者測試手機排版與連結跳轉。
 *
 * @param {string} [email] 收件信箱，預設為 rainforecast2026@gmail.com
 * @param {string} [articleId] 文章ID（選填，留空取最新一筆）
 * @param {boolean} [force] 是否略過重複寄送檢查（預設 true）
 */
function sendTestInstantMailTo(email, articleId, force) {
  var to = String(email || '').trim();
  if (!to) {
    try {
      to = Session.getActiveUser().getEmail() || '';
    } catch (e) {}
    if (!to) { to = 'rainforecast2026@gmail.com'; }
  }
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to)) {
    throw new Error('請給正確收件信箱，例如 sendTestInstantMailTo("someone@example.com")');
  }

  var rows = [];
  try { rows = readSheetObjects_(CM_SHEET); } catch (e) {
    Logger.log('讀取「' + CM_SHEET + '」分頁失敗：' + e);
  }

  var targetRow = null;
  if (rows && rows.length) {
    if (articleId) {
      targetRow = rows.filter(function (r) { return String(r['文章ID']).trim() === String(articleId).trim(); })[0];
    } else {
      targetRow = rows[rows.length - 1];
    }
  }

  var a = null;
  if (targetRow) {
    a = {
      id: String(targetRow['文章ID'] || 'TEST_001'),
      time: String(targetRow['發文時間'] || Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm')),
      url: String(targetRow['文章網址'] || 'https://www.cmoney.tw/forum/article/184582595'),
      text: String(targetRow['原文'] || '張震-1:全國會員請將手中璟德於257元以上全數獲利賣出，資金轉為65.5元以下市價買進2354鴻準！')
    };
  } else {
    a = {
      id: 'TEST_SAMPLE',
      time: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm'),
      url: 'https://www.cmoney.tw/forum/article/184582595',
      text: '張震-1:【測試簡訊】全國會員請將手中璟德於257元以上全數獲利賣出，資金轉為65.5元以下市價買進2354鴻準！'
    };
  }

  var props = PropertiesService.getScriptProperties();
  var key = 'TEST_INSTANT_MAIL_' + a.id + '_' + to.toLowerCase();
  var already = props.getProperty(key);
  if (already && force !== true) {
    return {
      ok: false, id: a.id, to: to, sentAt: already,
      reason: '文章 ' + a.id + ' 的測試信已在 ' + already + ' 寄給 ' + to + '。' +
              '若要強制重寄：sendTestInstantMailTo("' + to + '", "' + a.id + '", true)'
    };
  }

  var body = cmMailBody_(a, false);
  var subject = '[' + String(a.time).slice(0, 10) + '] 張震股市盤中家教班　[測試] 盤中即時通知' +
                (String(a.time).length >= 16 ? ' ' + String(a.time).slice(11, 16) : '');

  var sub = activeSubscribers_().filter(function (s) {
    return String(s['Email']).toLowerCase() === to.toLowerCase();
  })[0];

  // 與正式的盤中即時通知同一套：種類 sms、預覽摘要、純文字 body（v54）
  var testHtml = wrapMail_(body, to, sub ? String(sub['取消訂閱權杖']) : '', 'sms', cmSmsPreheader_(a));
  MailApp.sendEmail({
    to: to,
    subject: subject,
    htmlBody: testHtml,
    body: mailPlainText_(testHtml)
  });

  props.setProperty(key, nowStamp_());
  try {
    getSheet_('系統狀態').appendRow([nowStamp_(), '測試即時寄信', '文章 ' + a.id + ' 即時通知已寄至 ' + to +
      '（測試，未更動訂閱名單與簡訊通知狀態）', 'test', 'Apps Script']);
  } catch (e) { Logger.log('測試即時寄信：系統狀態寫入失敗 ' + e); }

  Logger.log('已寄出盤中即時通知測試信給 ' + to + '（文章 ' + a.id + '）');
  return { ok: true, id: a.id, to: to, subject: subject, note: '測試信已成功寄出！' };
}

/** 編輯器選取直接執行：寄出最新一則會員簡訊的即時通知測試信到測試信箱 */
function sendTestInstantMailNow() {
  return sendTestInstantMailTo('rainforecast2026@gmail.com', '', true);
}

/**
 * 在編輯器直接執行：強制重新解析最新一則會員簡訊。
 * 當盤中文章被修訂後若尚未提取新個股，可執行此函式立即重新解析並寫回。
 */
function reparseLatestSmsNow() {
  var sh = getSheet_(CM_SHEET);
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) {
    Logger.log('會員簡訊分頁無資料');
    return { ok: false, reason: '無資料' };
  }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iId = head.indexOf('文章ID'), iTime = head.indexOf('發文時間');
  var iText = head.indexOf('原文'), iState = head.indexOf('解析狀態');
  var iDetail = head.indexOf('解析明細'), iVer = head.indexOf('解析版本');
  var iAt = head.indexOf('判定時間');

  var lastRow = vals.length;
  var artId = String(vals[lastRow - 1][iId] || '').trim();
  var rawText = String(vals[lastRow - 1][iText] || '');
  var postTime = String(vals[lastRow - 1][iTime] || '');

  sh.getRange(lastRow, iState + 1).setValue('待解析（文章已修訂）');
  if (iDetail >= 0) { sh.getRange(lastRow, iDetail + 1).setValue(''); }
  if (iVer >= 0) { sh.getRange(lastRow, iVer + 1).setValue(''); }
  if (iAt >= 0) { sh.getRange(lastRow, iAt + 1).setValue(''); }

  Logger.log('已將文章 ' + artId + ' 標記為待解析，開始執行解析…');
  Logger.log('原文內容：\n' + rawText);

  var items = [];
  try {
    items = cmParseMessage_(rawText, cmDate_(postTime));
  } catch (e) {
    Logger.log('解析失敗：' + e);
    sh.getRange(lastRow, iState + 1).setValue('解析失敗');
    throw e;
  }

  Logger.log('成功解析出 ' + items.length + ' 筆個股指令：');
  items.forEach(function (x) {
    Logger.log('  - ' + x.name + ' (' + x.code + ') ' + x.action + ' ' + (x.priceText || '') + '：' + (x.note || ''));
  });

  if (items.length) {
    var detailJson = JSON.stringify(items.map(function (x) {
      return {
        name: x.name, code: x.code, dir: x.action,
        price: x.price, priceText: x.priceText, reason: x.note || x.body || ''
      };
    }));
    if (iDetail >= 0) { sh.getRange(lastRow, iDetail + 1).setValue(detailJson); }

    var w = cmWriteRows_(artId, postTime, items);
    var stateText = '已寫入 ' + w.trades + ' 筆買賣、' + w.holds + ' 筆持股';
    sh.getRange(lastRow, iState + 1).setValue(stateText);
    Logger.log('寫入完成！最新狀態：' + stateText);
  } else {
    sh.getRange(lastRow, iState + 1).setValue('已解析（AI 判定無可收錄個股）');
    Logger.log('AI 判定無可收錄個股。');
  }

  try { cmDispatchGithubParse_(); } catch (e) {}

  return { ok: true, articleId: artId, itemsCount: items.length };
}

/**
 * 在編輯器執行：今天的會員簡訊為什麼沒進來。只讀，不寫入、不標記已看過、不派工。
 * 例：diagnoseCmoneyToday()；指定文章：diagnoseCmoneyToday('184582595')
 *
 * 從來源一路查到解析：來源上有沒有這一篇 → 輪詢條件（設定、觸發器、時段、流量）→
 * 有沒有存進「會員簡訊」、解析狀態 → 解析派工（進行中狀態、配額冷卻、今日自動重派次數）→
 * 系統狀態裡今天的會員簡訊紀錄。最後一行給結論與處理方式。
 */
function diagnoseCmoneyToday(articleId) {
  var today = todayStr_(), lines = [], verdict = '';
  var log = function (s) { lines.push(s); Logger.log(s); };
  var memberId = cmMemberId_();
  log('會員簡訊診斷　' + today + '　帳號 ' + (memberId || '（未設定 CMONEY_MEMBER_ID）'));
  if (!memberId) { log('結論：尚未設定 CMONEY_MEMBER_ID，輪詢根本沒有啟用。'); return lines; }

  // 一、輪詢條件
  var hasTrigger = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'cmoneyPollJob'; });
  var from = Number(cmProp_('CMONEY_START', '900')) || 900, to = Number(cmProp_('CMONEY_END', '1500')) || 1500;
  var st = cmStat_(), last = Number(cmProp_(CM_LAST_PROP, '0'));
  log('輪詢觸發器 cmoneyPollJob：' + (hasTrigger ? '有' : '沒有　<<< 需執行 installTriggers()') +
      '　時段 ' + from + '～' + to + '　每 ' + (Number(cmProp_('CMONEY_POLL_MIN', '1')) || 1) + ' 分鐘');
  log('今日已抓　清單 ' + st.page + ' 次、文章 ' + st.art + ' 篇、約 ' + Math.round(st.kb / 1024 * 10) / 10 + 'MB' +
      (cmOverBudget_(st) ? '　<<< 已達流量上限，後面停抓' : '') +
      '　最後一次輪詢 ' + (last ? Utilities.formatDate(new Date(last), TZ, 'HH:mm:ss') : '（今天沒有）'));

  // 二、來源上的文章（一次清單請求，不寫任何狀態）
  var sourceToday = [];
  try {
    var data = cmFetchApiPage_(cmGetGuestToken_(), '') || [];
    var pageToday = data.map(cmApiArticle_).filter(function (a) { return a.id && cmDate_(a.time) === today; });
    sourceToday = pageToday.filter(function (a) { return a.creatorId === memberId && CM_MARK.test(a.text); });
    log('來源上今天的張震簡訊：' + (sourceToday.length ? sourceToday.map(function (a) { return a.id + '（' + a.time + '）'; }).join('、') : '第一頁沒有'));
    // 同一頁今天的每一篇（不論是誰發的）輪詢有沒有看過。輪詢每一棒依序處理沒看過的文章：
    // 某個時間點之後的全都沒看過，代表從那之後每一棒都在處理到它們之前就中斷了。
    var seenNow = cmSeen_();
    log('第一頁今天的文章（輪詢是否看過）：');
    pageToday.slice().sort(function (a, b) { return a.time < b.time ? -1 : 1; }).forEach(function (a) {
      log('  ' + a.time.slice(11) + '　' + a.id + '　' + (seenNow[a.id] ? '看過' : '沒看過') +
          (a.creatorId === memberId && CM_MARK.test(a.text) ? '　（張震簡訊）' : ''));
    });
    var unseen = pageToday.filter(function (a) { return !seenNow[a.id] && a.time.slice(11) <= String(to).replace(/(\d{2})$/, ':$1:59'); });
    var seenLater = pageToday.filter(function (a) { return seenNow[a.id]; })
      .some(function (a) { return unseen.some(function (u) { return a.time > u.time; }); });
    if (unseen.length && !seenLater) {
      var first = unseen.map(function (a) { return a.time; }).sort()[0];
      log('  <<< 時段內 ' + first.slice(11) + ' 之後的文章輪詢全都沒看過：從那時起每一棒都在處理文章之前中斷，' +
          '請到 Apps Script「執行項目」篩 cmoneyPollJob，看這之後的執行狀態與錯誤訊息');
    }
  } catch (e) {
    log('來源清單抓取失敗：' + String(e).slice(0, 160) + '　<<< 輪詢也會卡在這一步');
    verdict = verdict || '來源清單抓不到（訪客權杖或 API 改版），先修抓取。';
  }
  var targets = articleId ? [String(articleId)] : sourceToday.map(function (a) { return String(a.id); });

  // 三、會員簡訊分頁
  var rows = [];
  try { rows = readSheetObjects_(CM_SHEET); } catch (e) { log('讀不到「' + CM_SHEET + '」分頁：' + e); }
  // 輪詢每一棒在有今日追蹤文章時會整張讀這個分頁（含原文全文），分頁越大每一棒越慢。
  var chars = rows.reduce(function (n, r) { return n + String(r['原文'] || '').length; }, 0);
  log('「' + CM_SHEET + '」分頁：' + rows.length + ' 列，原文合計約 ' + Math.round(chars / 10000) + ' 萬字' +
      (rows.length > 1500 || chars > 5000000 ? '　<<< 分頁很大，輪詢每一棒整張讀取可能逾時' : ''));
  var seen = cmSeen_();
  targets.forEach(function (id) {
    var row = rows.filter(function (r) { return String(r['文章ID']).trim() === id; })[0];
    if (!row) {
      log('文章 ' + id + '：沒有存進會員簡訊（' + (seen[id] ? '輪詢已看過卻沒存' : '輪詢還沒看過') + '）');
      verdict = verdict || (seen[id]
        ? '輪詢看過這一篇卻沒存：多半是當時抓到的內容還沒有「張震-1:」標記或作者不符。請到後台「會員簡訊」→「二、依編號補抓」貼 ' + id + '。'
        : '輪詢沒有看到這一篇：檢查上面觸發器、時段與流量；補救請到後台「會員簡訊」→「二、依編號補抓」貼 ' + id + '。');
    } else {
      var state = String(row['解析狀態'] || '').trim();
      log('文章 ' + id + '：已存進會員簡訊　解析狀態「' + state + '」　抓取時間 ' + row['抓取時間']);
      if (state.indexOf('待解析') === 0) {
        verdict = verdict || '簡訊已經抓到，卡在解析：看下面的派工狀態，並到 GitHub Actions 查「會員簡訊 SMS-…」那幾次是否被取消（Cancelled）。' +
                            '補救請到後台「會員簡訊」→「三、重新解析」取消勾選「全部」、起始日選今天，按「在 GitHub 重新解析」。';
      }
    }
  });

  // 四、解析派工
  var job = getSmsParseState_(), cd = smsCooldownLeft_(), auto = cmAutoDispatchLeft_();
  var lastDispatch = Number(cmProp_('cmLastGithubParseDispatch', '0')) || 0;
  log('解析工單：' + (job ? (job.jobId + '　' + job.status + '　' + (job.step || '') + '　更新 ' + (job.updatedAt || '—') +
      (job.runUrl ? '　' + job.runUrl : '') + (job.note ? '　' + String(job.note).slice(0, 80) : '')) : '（沒有紀錄）'));
  if (job && job.status === '處理中') {
    var age = Math.round((Date.now() - new Date(job.updatedAt || 0).getTime()) / 60000);
    log('  已 ' + age + ' 分鐘沒有更新' + (age > 40 ? '　<<< 視為中斷，下一次會改派新工單' : '　（40 分鐘內新簡訊只會排在它後面）'));
  }
  log('配額冷卻：' + (cd ? '冷卻中，還有 ' + cd.minutes + ' 分鐘（到 ' + cd.until + '）　<<< 冷卻期間不自動派工' : '沒有') +
      '　今日自動重派剩 ' + auto.left + '/' + CM_AUTO_DISPATCH_MAX_ + ' 次' +
      '　最後派工 ' + (lastDispatch ? Utilities.formatDate(new Date(lastDispatch), TZ, 'MM/dd HH:mm') : '（沒有）'));
  if (cd) { verdict = verdict || 'Gemini 配額冷卻中，解析暫停；冷卻結束後會自動派工；額度恢復後也可在後台「三、重新解析」手動送出。'; }
  if (!auto.left) { verdict = verdict || '今天自動重派已達上限，不會再自動送出；請到後台「會員簡訊」→「三、重新解析」手動送出（起始日選今天）。'; }

  // 五、系統狀態裡今天的會員簡訊紀錄
  try {
    var notes = readSheetObjects_('系統狀態').filter(function (r) {
      return String(r['類別']) === '會員簡訊' && String(fmtDate_(r['時間']) || String(r['時間']).slice(0, 10)).indexOf(today) === 0;
    }).slice(-8);
    log('系統狀態（今天）：' + (notes.length ? '' : '沒有會員簡訊相關紀錄'));
    notes.forEach(function (r) { log('  ' + r['時間'] + '　' + String(r['說明']).slice(0, 120)); });
  } catch (e) { /* 沒有這張表就略過 */ }

  log('結論：' + (verdict || (targets.length ? '這幾篇都已存進會員簡訊且不在待解析，請到後台「會員簡訊」看解析結果是否為「無可收錄」。' :
                              '來源第一頁沒有今天的張震簡訊，可帶文章編號再查一次：diagnoseCmoneyToday(\'文章編號\')')));
  return lines;
}

/* ==================================================================== *
 * 更新過去的資料
 *
 * 下面兩支同步函式保留給編輯器診斷。正式後台改用後面的背景工單：
 * 自動沿 API 游標翻頁、逐篇辨識張震、嚴格驗日期、寫入並做日曆式逐日稽核。
 * 依編號仍可補任意舊文章；重新解析則由 Apps Script 每棒處理一則並保存進度。
 * ==================================================================== */

/**
 * 重新掃一次會員頁，把還沒收過的都收下來。
 *
 * 與每分鐘那一棒的差別只有兩個：不看時段、不看間隔。
 * 一樣走完整的判斷（是不是張震的簡訊、有沒有收過），所以重跑安全。
 */
function cmBackfillRecent_(maxArticles) {
  var out = { scanned: 0, fetched: 0, saved: 0, skipped: 0, note: '' };
  if (!cmEnabled_()) { out.note = '尚未設定 CMONEY_MEMBER_ID'; return out; }

  var html = cmFetch_(cmUserUrl_());
  var st = cmStat_(); st.page++; st.kb += CM_PAGE_KB; cmSaveStat_(st);
  if (!html) { out.note = '會員頁抓不到'; return out; }

  var ids = cmListArticleIds_(html);
  out.scanned = ids.length;
  if (!ids.length) { out.note = '認不出任何文章連結，可能是對方改版'; return out; }

  var cap = Math.max(1, Number(maxArticles) || 12);
  var seen = cmSeen_();
  ids.slice(0, cap).forEach(function (id) {
    if (cmAlreadySaved_(id)) { out.skipped++; return; }
    var a = cmFetchArticle_(id);
    var s2 = cmStat_(); s2.art++; s2.kb += CM_ART_KB; cmSaveStat_(s2);
    out.fetched++;
    if (!a || !a.text || !cmDate_(a.time)) { return; }
    seen = cmMarkSeen_(seen, id);
    if (!CM_MARK.test(a.text)) { return; }
    if (cmSaveMessage_(a)) { out.saved++; }
  });
  return out;
}

/**
 * 依文章編號或網址補抓。一行一個，兩種寫法都收：
 *   https://www.cmoney.tw/forum/article/183929007
 *   183929007
 *
 * 不檢查那一篇是不是這個帳號發的——貼進來就是你要的。
 * 但仍然要通過「是不是張震的簡訊」那一關，避免貼錯連結生出一堆雜訊。
 */
function cmBackfillIds_(text, maxArticles) {
  var out = { asked: 0, fetched: 0, saved: 0, skipped: 0, notMark: 0, failed: [] };
  var ids = [], seenId = {};
  String(text || '').split(/[\s,;、，]+/).forEach(function (t) {
    var m = String(t).match(/(\d{6,})\s*$/);
    if (m && !seenId[m[1]]) { seenId[m[1]] = 1; ids.push(m[1]); }
  });
  out.asked = ids.length;
  if (!ids.length) { return out; }

  var cap = Math.max(1, Number(maxArticles) || 20);
  var seen = cmSeen_();
  ids.slice(0, cap).forEach(function (id) {
    if (cmAlreadySaved_(id)) { out.skipped++; return; }
    var a = cmFetchArticle_(id);
    var s2 = cmStat_(); s2.art++; s2.kb += CM_ART_KB; cmSaveStat_(s2);
    out.fetched++;
    if (!a || !a.text) { out.failed.push(id); return; }
    if (!cmDate_(a.time)) { out.failed.push(id + '（日期不明）'); return; }
    seen = cmMarkSeen_(seen, id);
    if (!CM_MARK.test(a.text)) { out.notMark++; return; }
    if (cmSaveMessage_(a)) { out.saved++; }
  });
  return out;
}

/**
 * 把已經收下來的重新排進解析佇列。
 *
 * 只改「解析狀態」，不重抓網頁也不動已經寫進操作紀錄的列——
 * 那些列由 cmTradesWritten_ 擋著，重跑不會寫兩次。
 * 真的要重寫那幾列的話，先在試算表把來源影片ID 是 CMONEY- 的刪掉再跑。
 */
function cmReparse_(since) {
  var out = { queued: 0, total: 0 };
  var sh;
  try { sh = getSheet_(CM_SHEET); } catch (e) { return out; }
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return out; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iTime = head.indexOf('發文時間'), iState = head.indexOf('解析狀態');
  var iDetail = head.indexOf('解析明細');
  if (iState < 0) { return out; }

  var from = since ? cmDate_(since) : '';
  var edits = [];
  for (var r = 1; r < vals.length; r++) {
    out.total++;
    var d = cmDate_(vals[r][iTime]);
    if (from && d && d < from) { continue; }
    if (String(vals[r][iState]).trim() === '待解析') { continue; }
    edits.push(r + 1);
  }
  if (!edits.length) { return out; }
  var iVer = head.indexOf('解析版本'), iAt = head.indexOf('判定時間');
  withLock_(function () {
    edits.forEach(function (rn) {
      sh.getRange(rn, iState + 1).setValue('待解析').setNote('');
      // 明細留空白，並清掉版本戳記。兩者一起清才算真的排回待解析：
      // 只清狀態不清版本的話，主線會判定「已經用現行提示詞判過」而跳過，
      // 按了重新解析卻什麼都沒重跑。
      if (iDetail >= 0) { sh.getRange(rn, iDetail + 1).setValue(''); }
      if (iVer >= 0) { sh.getRange(rn, iVer + 1).setValue(''); }
      if (iAt >= 0) { sh.getRange(rn, iAt + 1).setValue(''); }
    });
  });
  out.queued = edits.length;
  return out;
}

/* 會員簡訊的步驟表。

   「同步衍生資料」整格拿掉了。它做的是全站重算（清產業列、稽核複審、
   代號比對、更新基本面、補齊日K、重算持股追蹤、記錄績效），與
   「這一篇簡訊收錄了哪些個股」完全是兩件事，卻被綁在同一條鏈上：
   更新一筆簡訊要等一次全站重算，中間任何一步失敗還會讓已經寫好的
   簡訊資料被判成失敗。補齊日K更是其中最慢的一步，一次可以跑十幾分鐘。
   要更新網站時，到「網站內容」分頁按刷新即可。

   併入過去資料那一支從一格擴成十三格，而且全部排在逐日稽核之後：
   先把「哪一天有幾篇、幾篇還沒收錄個股」講清楚，再去動資料。
   這幾格要與 pipeline.py 的 SMS_MERGE_STEPS 一字不差。
   實際顯示時以 GitHub 回報的 steps 為準，這裡只是後台的預設值。 */
var SMS_MERGE_STEPS_ = ['準備', '盤點簡訊', '逐日稽核', '分類已解析與空白',
                        '建立收錄佇列', '載入代號對照表', '讀取歷史買價',
                        'AI 收錄個股', '規則稽核與代號比對', '條件式買賣去重',
                        '寫入操作紀錄', '寫入會員持股', '原子取代舊列',
                        '回寫解析狀態', '完成'];
var SMS_STEPS_ = ['準備', '探索文章', '辨識張震', '篩選日期'].concat(SMS_MERGE_STEPS_.slice(1));
var SMS_REPARSE_STEPS_ = SMS_MERGE_STEPS_;

/* 配額暫停後的冷卻。

   免費層的 Gemini 是「每日請求數」制，用完要等太平洋時間午夜才重置。
   在那之前每一次呼叫都只會拿回 429，而且仍然計入用量。
   先前後台沒有這道閘門，於是配額用盡那天按鈕被連按了八次，
   每一次都跑十四分鐘、每一次都在同一篇文章上撞牆，一列也沒寫進去。
   現在派工前先看冷卻時間，還沒到就直接擋下並告訴使用者什麼時候可以按。 */
var SMS_COOLDOWN_PROP_ = 'smsQuotaCooldownUntil';

function smsCooldownLeft_() {
  var raw = PropertiesService.getScriptProperties().getProperty(SMS_COOLDOWN_PROP_);
  if (!raw) { return null; }
  // pipeline.py 送過來的一律是台北時間，所以時區明著寫死 +08:00，
  // 不要靠執行環境的本地時區去猜——猜錯八小時，冷卻就形同虛設。
  var m = String(raw).match(/^(\d{4})[\/-](\d{2})[\/-](\d{2})[ T](\d{2}):(\d{2})/);
  if (!m) { return null; }
  var until = new Date(m[1] + '-' + m[2] + '-' + m[3] + 'T' + m[4] + ':' + m[5] + ':00+08:00');
  if (isNaN(until.getTime()) || until.getTime() <= Date.now()) {
    PropertiesService.getScriptProperties().deleteProperty(SMS_COOLDOWN_PROP_);
    return null;
  }
  return { until: String(raw), minutes: Math.ceil((until.getTime() - Date.now()) / 60000) };
}

function smsSetCooldown_(untilText) {
  if (!untilText) { return; }
  PropertiesService.getScriptProperties().setProperty(SMS_COOLDOWN_PROP_, String(untilText));
}

/** 後台用：使用者確認要無視冷卻時，手動清掉。 */
function apiAdminClearSmsCooldown(key) {
  adminAuth_(key);
  PropertiesService.getScriptProperties().deleteProperty(SMS_COOLDOWN_PROP_);
  return { ok: true, message: '已清除配額冷卻，可再次派工。若額度其實還沒恢復，仍會再次暫停。' };
}
var SMS_STATE_PROP_ = 'smsParseState';
var CM_AUDIT_SHEET_ = '會員簡訊稽核';
var CM_API_URL_ = 'https://www.cmoney.tw/api/mach/api/Article/GetChannelsArticleByWeight';
// 端點即使要求 50 篇也固定最多回 20 篇；用真實上限判斷是否到底，
// 否則第一頁 20 < 50 會被誤認為已經抓完。
var CM_API_PAGE_SIZE_ = 20;
var CM_API_MAX_PAGES_ = 5000;            // 最多 10 萬篇；到頂會明確失敗，不假裝完整

function getSmsParseState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(SMS_STATE_PROP_);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (e) { return null; }
}

function setSmsParseState_(st) {
  st.updatedAt = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss');
  try {
    PropertiesService.getScriptProperties().setProperty(SMS_STATE_PROP_, JSON.stringify(st));
  } catch (e) {}
}

function cmPublicJobState_(st) {
  if (!st) { return null; }
  var out = JSON.parse(JSON.stringify(st));
  delete out.apiToken;
  delete out.ids;
  return out;
}

/** 後台用：查詢會員簡訊作業進度與逐日稽核。 */
function apiAdminSmsState(key) {
  adminAuth_(key);
  var st = getSmsParseState_();
  // 步驟清單以 GitHub 回報的為準。兩邊版本不同步時，後台照樣畫得出
  // 正確的格數，不會出現「進度條停在第 8 格但實際已經在第 12 步」。
  var steps = (st && st.steps && st.steps.length) ? st.steps :
              (st && st.mode === 'reparse' ? SMS_REPARSE_STEPS_ :
               (st && ['recent', 'all', 'ids'].indexOf(st.mode) >= 0) ? SMS_STEPS_ :
               SMS_MERGE_STEPS_);
  return {
    ok: true,
    state: cmPublicJobState_(st),
    steps: steps,
    cooldown: smsCooldownLeft_(),
    contentSync: cmContentSyncStatus_(),
    audit: st && st.auditReady ? cmBuildSmsAudit_(st) : null
  };
}

function cmTodayMinus_(days) {
  var d = new Date();
  d.setHours(12, 0, 0, 0);
  d.setDate(d.getDate() - Number(days || 0));
  return Utilities.formatDate(d, TZ, 'yyyy/MM/dd');
}

function cmJobId_() {
  return 'SMS-' + Utilities.formatDate(new Date(), TZ, 'yyyyMMdd-HHmmss') + '-' +
         Math.floor(Math.random() * 900 + 100);
}

function cmParseIds_(text) {
  var ids = [], seen = {};
  String(text || '').split(/[\s,;、，]+/).forEach(function (part) {
    var m = part.match(/(\d{6,})(?:\D*)$/);
    if (m && !seen[m[1]]) { seen[m[1]] = 1; ids.push(m[1]); }
  });
  return ids.slice(0, 100);
}

function cmScheduleAdminWorker_() {
  try {
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (t.getHandlerFunction() === 'cmAdminWorker_') { ScriptApp.deleteTrigger(t); }
    });
    ScriptApp.newTrigger('cmAdminWorker_').timeBased().after(8000).create();
  } catch (e) {
    Logger.log('會員簡訊作業：排下一棒失敗，五分鐘看門狗會接手：' + e);
  }
}

function cmStartAdminJob_(mode, payload) {
  payload = payload || {};
  var cur = getSmsParseState_();
  if (cur && cur.status === '處理中') {
    return { ok: false, reason: '已有會員簡訊作業在進行（' + cur.step + '）。' };
  }
  if (!cmEnabled_()) {
    return { ok: false, reason: '尚未設定 CMONEY_MEMBER_ID，會員簡訊沒有啟用。' };
  }
  var active = getSmsParseState_();
  if (active && active.status === '處理中') {
    return { ok: false, reason: '已有會員簡訊作業在 GitHub Actions 執行（' + active.step + '）。' };
  }

  var ids = mode === 'ids' ? cmParseIds_(payload.text) : [];
  if (mode === 'ids' && !ids.length) {
    return { ok: false, reason: '沒有讀到文章編號。可貼網址或純數字，一行一個。' };
  }
  if (['recent', 'all', 'ids', 'reparse'].indexOf(mode) < 0) {
    return { ok: false, reason: '不認得的作業模式：' + mode };
  }

  var since = '';
  if (mode === 'recent') { since = cmTodayMinus_(6); }
  if (mode === 'reparse' && payload.since) { since = cmDate_(payload.since); }
  var st = {
    jobId: cmJobId_(), mode: mode, status: '處理中', phase: 'prepare',
    step: '準備', index: 0,
    total: (mode === 'reparse' ? SMS_REPARSE_STEPS_ : SMS_STEPS_).length - 1,
    done: 0, pct: 2,
    since: since, through: todayStr_(), ids: ids, idIndex: 0,
    page: 0, score: '', scanned: 0, zhang: 0, inRange: 0,
    saved: 0, existed: 0, excluded: 0, errors: 0, auditReady: false,
    note: mode === 'recent' ? '準備自動探索最近七天的所有文章。' :
          mode === 'all' ? '準備自動探索這個帳號的全部文章。' :
          mode === 'ids' ? '準備逐一抓取 ' + ids.length + ' 個文章編號。' :
          '準備重新解析資料。',
    startedAt: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss')
  };

  if (mode === 'reparse') {
    var queued = cmReparse_(since);
    var reparseRows = readSheetObjects_(CM_SHEET).filter(function (r) {
      var d = cmDate_(r['發文時間']);
      return d && (!since || d >= since) && d <= st.through;
    });
    st.scanned = reparseRows.length;
    st.zhang = reparseRows.length;
    st.existed = reparseRows.length;
    st.parseTotal = cmCountPendingSms_(since);
    st.phase = 'parse'; st.step = '建立解析佇列'; st.index = 1; st.pct = 12;
    st.note = '已將 ' + queued.queued + ' 則排回待解析，共 ' + st.parseTotal + ' 則等待處理。';
  } else {
    // 先觸發自動建表，避免第一棒才因缺分頁而停住。
    var auditSheet = getSheet_(CM_AUDIT_SHEET_);
    // 起始列要在第一棒前就持久化。若第一棒「寫完列、還沒存狀態」時被平台
    // 硬切，重跑仍能把那批列納入稽核，不會從下一批才開始算。
    st.auditStartRow = auditSheet.getLastRow() + 1;
    st.auditEndRow = st.auditStartRow - 1;
    st.phase = mode === 'ids' ? 'ids' : 'discover';
  }
  setSmsParseState_(st);
  cmScheduleAdminWorker_();
  return { ok: true, jobId: st.jobId, message: st.note };
}

/** 會員簡訊長作業的統一入口。Apps Script 只負責授權與派工，重活由 GitHub 執行。 */
function apiAdminStartSmsJob(key, mode, payload) {
  adminAuth_(key);
  mode = String(mode || ''); payload = payload || {};
  if (!cmEnabled_()) {
    return { ok: false, reason: '尚未設定 CMONEY_MEMBER_ID，會員簡訊沒有啟用。' };
  }
  if (['today', 'blanks', 'recent', 'all', 'ids', 'reparse', 'merge'].indexOf(mode) < 0) {
    return { ok: false, reason: '不認得的作業模式：' + mode };
  }
  var ids = mode === 'ids' ? cmParseIds_(payload.text) : [];
  if (mode === 'ids' && !ids.length) {
    return { ok: false, reason: '沒有讀到文章編號。可貼網址或純數字，一行一個。' };
  }

  /* 配額冷卻只擋「自動派工」，不擋人。

     這一段先前也擋了手動按鈕，結果變成一個解不開的死結：額度用完之後
     去 GitHub 換了新的金鑰，回來按「接續跑」卻還是被擋住——擋的理由是
     一個已經不成立的舊狀態（那是上一把金鑰的額度，不是新的那一把）。
     而且冷卻要等到太平洋午夜才自己過期，等於整天都動不了。

     人按下按鈕是一個明確的意思表示：他知道發生過什麼，而且多半剛做過
     某件事（換金鑰、加金鑰、改型號）想立刻驗證。這種時候該做的是照做，
     不是替他判斷「你現在按也沒用」——真的沒用的話，跑一次就知道了，
     而且新版撞到每日額度會立刻停，不會浪費時間。

     所以手動入口一律放行，並把冷卻狀態一起清掉：留著它只會讓下一次
     自動派工也被這個舊狀態擋住。自動派工那一條（cmDispatchGithubParse_）
     仍然看冷卻，那裡沒有人在判斷，需要一個煞車。 */
  var cd = smsCooldownLeft_();
  if (cd) {
    PropertiesService.getScriptProperties().deleteProperty(SMS_COOLDOWN_PROP_);
    cmNote_('手動派工：清除配額冷卻（原訂 ' + cd.until + ' 解除）。' +
            '若額度其實還沒恢復，這一輪會再次暫停，已完成的資料不受影響。');
  }

  // 同一時間只讓一個 GitHub 工單在跑。先前沒擋，於是按鈕被連按八次，
  // 八個 job 排隊、每個跑十四分鐘，全部撞在同一個配額上。
  var running = getSmsParseState_();
  if (running && running.status === '處理中' && running.execution === 'github') {
    var age = Date.now() - new Date(running.updatedAt || 0).getTime();
    if (!(age > 40 * 60 * 1000)) {          // 超過四十分鐘沒更新才視為卡死
      return { ok: false, reason: '已有一個會員簡訊工單在 GitHub Actions 執行（' +
                                  (running.step || '處理中') + '，' + (running.pct || 0) +
                                  '%）。請等它跑完，或先按「取消本次工作」。' };
    }
  }

  var previous = getSmsParseState_() || {};
  var jobId = cmJobId_();
  var since = mode === 'recent' ? cmTodayMinus_(6) :
              mode === 'reparse' && payload.since ? cmDate_(payload.since) : '';
  var resume = previous.status === '配額暫停' && previous.mode === mode &&
               String(previous.since || '') === String(since || '');
  setSmsParseState_({ jobId: jobId, mode: mode, execution: 'github', status: '處理中', step: '準備',
    index: 0, total: 8, done: 0, pct: 2, since: since, through: todayStr_(),
    auditReady: false, startedAt: nowStamp_(), resume: resume,
    note: resume ? '已建立續跑工單，將沿用逐篇檢查點並從未完成文章接續。' :
                   '已建立工單，正在派送 GitHub Actions。' });
  // refresh_site 刻意不帶。會員簡訊這條鏈只負責把簡訊變成操作紀錄與
  // 會員持股；全站重算（含補齊日K、重算追蹤、記錄績效）是另一條鏈，
  // 綁在一起會讓更新一筆簡訊要等一次全站重算，而且中間任何一步失敗
  // 都會讓已經寫好的簡訊資料被判成失敗。要更新網站請到「網站內容」按刷新。
  var d = dispatchGithub_({
    parse_sms: 'true', sms_mode: mode,
    sms_since: since, sms_ids: ids.join(','), sms_job_id: jobId,
    sms_resume: resume ? 'true' : 'false',
    sms_member_id: cmMemberId_()
  });
  if (!d.ok) {
    var failed = getSmsParseState_() || {};
    failed.status = '失敗'; failed.note = d.reason; setSmsParseState_(failed);
    return d;
  }
  var accepted = getSmsParseState_() || {};
  accepted.runId = d.runId || '';
  accepted.runUrl = d.runUrl || '';
  accepted.note = 'GitHub Actions 已接受工作，正在等待執行。';
  setSmsParseState_(accepted);
  PropertiesService.getScriptProperties().setProperty('cmLastGithubParseDispatch', String(Date.now()));
  var msg = {
    today:   '已交給 GitHub Actions 更新當天的會員簡訊。只處理今天，配額用量最小。',
    blanks:  '已交給 GitHub Actions 補齊過去資料中收錄個股仍空白的文章。已判定過的一律不重跑。',
    merge:   '已交給 GitHub Actions 將未寫入通知併入過去資料：沿用已判定明細，並用 AI 補齊仍空白的收錄個股。',
    reparse: '已交給 GitHub Actions 強制重新判定選定範圍。這是最耗配額的模式。'
  }[mode] || '已交給 GitHub Actions 執行抓取、解析、寫入與逐日稽核。';
  return { ok: true, jobId: jobId, runId: d.runId || '', runUrl: d.runUrl, message: msg };
}

/** 舊前端相容入口。 */
function apiAdminStartSmsParse(key, since) {
  return apiAdminStartSmsJob(key, 'reparse', { since: since || '' });
}

function apiAdminCancelSmsJob(key, runId) {
  adminAuth_(key);
  var st = getSmsParseState_();
  if (!st || st.status !== '處理中') { return { ok: false, reason: '沒有進行中的會員簡訊作業。' }; }
  var target = String(runId || st.runId || '');
  if (!target && st.jobId) {
    var found = latestRunInfo_(st.jobId);
    target = found ? String(found.id || '') : '';
    if (found && found.status === 'completed') {
      st.status = '已取消';
      st.note = 'GitHub 工作已經結束，已清除後台殘留的處理中狀態；已完成資料保留。';
      st.auditReady = true; st.runId = target; st.runUrl = found.url || st.runUrl || '';
      setSmsParseState_(st);
      return { ok: true, message: st.note };
    }
  }
  if (!target) {
    st.status = '已取消';
    st.note = '舊工單沒有可追蹤的 GitHub run ID，已清除後台殘留狀態；已完成資料保留。';
    st.auditReady = true;
    setSmsParseState_(st);
    return { ok: true, message: st.note };
  }
  var stopped = cancelGithubRun_(target);
  if (!stopped.ok) { return stopped; }
  st.status = '已取消';
  st.note = stopped.alreadyFinished ?
    'GitHub 工作已經結束，已清除後台殘留狀態；已完成資料保留。' :
    '已要求 GitHub Actions 停止；已完成的寫入與稽核軌跡保留。';
  st.auditReady = true; st.runId = target;
  setSmsParseState_(st);
  return { ok: true, message: stopped.message + ' ' + st.note };
}

function cmGuestToken_(html) {
  var source = String(html || '');
  var pats = [
    /tokens\s*:\s*\{\s*at\s*:\s*"([^"]+)"/,
    /"tokens"\s*:\s*\{\s*"at"\s*:\s*"([^"]+)"/
  ];
  for (var i = 0; i < pats.length; i++) {
    var m = source.match(pats[i]);
    if (m && m[1]) { return m[1]; }
  }
  throw new Error('公開會員頁抓得到，但找不到訪客權杖；來源網站可能已改版。');
}

function cmGetGuestToken_() {
  var html = cmFetch_(cmUserUrl_());
  if (!html) { throw new Error('抓不到公開會員頁。'); }
  return cmGuestToken_(html);
}

function cmFetchApiPage_(token, score) {
  var url = CM_API_URL_ + '?count=' + CM_API_PAGE_SIZE_;
  if (score !== '' && score !== null && score !== undefined) {
    url += '&startScore=' + encodeURIComponent(String(score));
  }
  var res = UrlFetchApp.fetch(url, {
    method: 'post', muteHttpExceptions: true, contentType: 'application/json',
    payload: JSON.stringify({ items: ['Member-All.' + cmMemberId_()] }),
    headers: {
      'Authorization': 'Bearer ' + token,
      'X-Version': '3.0',
      'Accept': 'application/json',
      'Referer': cmUserUrl_(),
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
    }
  });
  var code = res.getResponseCode();
  if (code !== 200) { throw new Error('文章清單 API 回 HTTP ' + code); }
  var data;
  try { data = JSON.parse(res.getContentText()); }
  catch (e) { throw new Error('文章清單 API 回傳的不是 JSON。'); }
  if (!Array.isArray(data)) { throw new Error('文章清單 API 格式已改變。'); }
  return data;
}

function cmApiArticle_(raw) {
  var c = raw && raw.content || {};
  var ms = Number(raw && raw.createTime);
  var time = ms > 946684800000 && ms < 4102444800000 ?
    Utilities.formatDate(new Date(ms), TZ, 'yyyy/MM/dd HH:mm:ss') : '';
  return {
    id: String(raw && raw.id || ''),
    creatorId: String(c.creatorId || ''),
    title: String(c.title || '').trim(),
    text: String(c.text || '').trim(),
    time: time,
    url: cmArticleUrl_(raw && raw.id || ''),
    score: raw && raw.weight !== undefined ? String(raw.weight) : ''
  };
}

function cmAuditExistingIds_(jobId) {
  var out = {};
  var sh = getSheet_(CM_AUDIT_SHEET_);
  var last = sh.getLastRow();
  if (last < 2) { return out; }
  // 只需防「上一棒寫完但還沒存游標就被硬切」造成的末端重複；讀最後 250 列
  // 即可，避免全部抓取跑到幾千篇後每一頁都重讀整張稽核表。
  var first = Math.max(2, last - 249);
  var vals = sh.getRange(first, 1, last - first + 1, 2).getValues();
  for (var r = 0; r < vals.length; r++) {
    if (String(vals[r][0]) === String(jobId)) { out[String(vals[r][1])] = 1; }
  }
  return out;
}

function cmAppendAudit_(rows, st) {
  if (!rows.length) { return; }
  var sh = getSheet_(CM_AUDIT_SHEET_);
  var seen = cmAuditExistingIds_(st.jobId);
  rows = rows.filter(function (row) { return row[1] && !seen[String(row[1])]; });
  if (!rows.length) {
    // 代表上一棒其實已寫成功，只是還沒來得及保存游標。把尾端重新納入範圍。
    st.auditEndRow = Math.max(Number(st.auditEndRow || 0), sh.getLastRow());
    return;
  }
  var start = sh.getLastRow() + 1;
  sh.getRange(start, 1, rows.length, 12).setValues(rows);
  if (!st.auditStartRow) { st.auditStartRow = start; }
  st.auditEndRow = start + rows.length - 1;
}

function cmAuditRow_(st, a, fetchError) {
  var now = Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss');
  if (fetchError) {
    return [st.jobId, String(a.id || ''), '', '', '', '', '無法判定', '未判定',
            '抓取失敗', String(fetchError).slice(0, 500), a.url || cmArticleUrl_(a.id), now];
  }
  // 自動探索時 API 必須明確證明作者就是設定帳號；依編號補抓只有文章 HTML，
  // 沒有 creatorId，該模式才允許改由張震內文標記作身分驗證。
  var belongs = a.creatorId ? a.creatorId === cmMemberId_() : st.mode === 'ids';
  var isZhang = belongs && CM_MARK.test(String(a.text || ''));
  var date = cmDate_(a.time);
  var inRange = !!date && date <= st.through && (!st.since || date >= st.since);
  var status = !a.text ? '內容錯誤' : !isZhang ? '已排除' : !date ? '日期錯誤' :
               !inRange ? '範圍外' : '待寫入';
  var note = !belongs ? '作者不是設定的來源帳號' : !a.text ? '文章沒有可讀正文' :
             !isZhang ? '正文沒有張震廣播標記' : !date ? '找不到可驗證的發文時間，已隔離' :
             !inRange ? '張震文章，但不在本次日期範圍' : '通過身分與日期檢查';
  return [st.jobId, a.id, a.time, date, a.title, isZhang ? a.text.slice(0, 20000) : '',
          isZhang ? '是' : '否', inRange ? '範圍內' : '範圍外', status, note,
          a.url, now];
}

function cmConsumeArticles_(st, articles) {
  var rows = [];
  articles.forEach(function (raw) {
    // API 會把已刪除文章以 tombstone 回傳；那不是一篇可稽核的來源文章，
    // 不能因為沒有正文與日期就灌成一批假錯誤。
    if (raw && raw.content && raw.content.articleState) { st.excluded++; return; }
    var a = raw.id !== undefined && raw.content !== undefined ? cmApiArticle_(raw) : raw;
    var row = cmAuditRow_(st, a, '');
    rows.push(row);
    st.scanned++;
    if (row[6] === '是') { st.zhang++; }
    else { st.excluded++; }
    if (row[7] === '範圍內' && row[6] === '是') { st.inRange++; }
    if (row[8] === '日期錯誤' || row[8] === '內容錯誤') { st.errors++; }
  });
  cmAppendAudit_(rows, st);
}

function cmDiscoverChunk_(st) {
  if (!st.apiToken) { st.apiToken = cmGetGuestToken_(); }
  var stop = false;
  for (var turn = 0; turn < 4 && !stop; turn++) {
    var data;
    try { data = cmFetchApiPage_(st.apiToken, st.score); }
    catch (first) {
      // 訪客權杖會輪替。失敗一次就重新讀公開頁面，再試同一頁。
      st.apiToken = cmGetGuestToken_();
      data = cmFetchApiPage_(st.apiToken, st.score);
    }
    if (!data.length) { stop = true; break; }
    cmConsumeArticles_(st, data);
    st.page++;
    var last = data[data.length - 1];
    var next = last && last.weight !== undefined ? String(last.weight) : '';
    if (!next || next === st.score) {
      throw new Error('文章分頁游標沒有前進，已停止，避免重複抓取。');
    }
    st.score = next;

    if (st.mode === 'recent') {
      var validDates = data.map(function (x) { return cmApiArticle_(x).time.slice(0, 10); })
        .filter(function (d) { return !!d; });
      if (validDates.length && validDates.every(function (d) { return d < st.since; })) { stop = true; }
    }
    if (data.length < CM_API_PAGE_SIZE_) { stop = true; }
    if (st.page >= CM_API_MAX_PAGES_) {
      throw new Error('已探索 ' + (CM_API_MAX_PAGES_ * CM_API_PAGE_SIZE_) +
                      ' 篇仍未到底。請提高 CM_API_MAX_PAGES_ 後接續，系統沒有把結果冒充完整。');
    }
  }
  st.step = '探索文章'; st.index = 1;
  st.pct = Math.min(42, 8 + st.page * 3);
  st.note = '已探索 ' + st.page + ' 頁、' + st.scanned + ' 篇；先做張震判定，再比對日期。';
  if (stop) { st.phase = 'identity'; st.pct = 45; }
}

function cmIdsChunk_(st) {
  var list = st.ids || [];
  var rows = [];
  for (var n = 0; n < 5 && st.idIndex < list.length; n++, st.idIndex++) {
    var id = list[st.idIndex], a = null, err = '';
    try { a = cmFetchArticle_(id); } catch (e) { err = String(e); }
    if (!a || !a.text) { err = err || '文章頁抓不到或沒有正文'; a = a || { id: id, url: cmArticleUrl_(id) }; }
    var row = cmAuditRow_(st, a, err);
    rows.push(row); st.scanned++;
    if (row[6] === '是') { st.zhang++; } else if (!err) { st.excluded++; }
    if (row[7] === '範圍內' && row[6] === '是') { st.inRange++; }
    if (err || row[8] === '日期錯誤' || row[8] === '內容錯誤') { st.errors++; }
  }
  cmAppendAudit_(rows, st);
  st.step = '探索文章'; st.index = 1;
  st.pct = Math.min(43, 8 + Math.round(st.idIndex / Math.max(1, list.length) * 35));
  st.note = '已抓取 ' + st.idIndex + '/' + list.length + ' 篇，逐篇保留判定結果。';
  if (st.idIndex >= list.length) { st.phase = 'identity'; st.pct = 45; }
}

function cmAuditRowsForJob_(jobId, st) {
  var sh = getSheet_(CM_AUDIT_SHEET_);
  var first = Number(st && st.auditStartRow) || 2;
  var last = Number(st && st.auditEndRow) || sh.getLastRow();
  if (last < first) { return []; }
  var vals = sh.getRange(first, 1, last - first + 1, 12).getValues(), out = [];
  for (var r = 0; r < vals.length; r++) {
    if (String(vals[r][0]) === String(jobId)) { out.push({ row: first + r, values: vals[r] }); }
  }
  return out;
}

function cmSavedArticle_(id) {
  var sh = getSheet_(CM_SHEET), vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return null; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iId = head.indexOf('文章ID'), iTime = head.indexOf('發文時間');
  for (var r = 1; r < vals.length; r++) {
    if (String(vals[r][iId]).trim() === String(id)) {
      return { sheet: sh, row: r + 1, timeCol: iTime + 1, time: vals[r][iTime] };
    }
  }
  return null;
}

/** 權威發文時間與舊資料不同時，同步校正原文列及其衍生的操作／持股日期。 */
function cmRepairSavedDate_(id, correctTime, saved) {
  var correctDate = cmDate_(correctTime);
  if (!saved || !correctDate || cmDate_(saved.time) === correctDate) { return 0; }
  var source = cmSourceId_(id), changed = 0;
  withLock_(function () {
    saved.sheet.getRange(saved.row, saved.timeCol).setValue(correctTime);
    changed++;
    ['操作紀錄', '會員持股'].forEach(function (name) {
      var sh = getSheet_(name), vals = sh.getDataRange().getValues();
      if (vals.length < 2) { return; }
      var head = vals[0].map(function (h) { return String(h).trim(); });
      var iDate = head.indexOf('日期'), iSrc = head.indexOf('來源影片ID');
      if (iDate < 0 || iSrc < 0) { return; }
      for (var r = 1; r < vals.length; r++) {
        if (String(vals[r][iSrc]).trim() === source && cmDate_(vals[r][iDate]) !== correctDate) {
          sh.getRange(r + 1, iDate + 1).setValue(correctDate);
          changed++;
        }
      }
    });
  });
  return changed;
}

function cmRecountAudit_(st) {
  var rows = cmAuditRowsForJob_(st.jobId, st);
  st.scanned = rows.length; st.zhang = 0; st.inRange = 0; st.excluded = 0; st.errors = 0;
  st.saved = 0; st.existed = 0; st.repaired = 0; st.writeFailed = 0;
  rows.forEach(function (x) {
    var v = x.values;
    var status = String(v[8]);
    if (String(v[6]) === '是') { st.zhang++; } else { st.excluded++; }
    if (String(v[6]) === '是' && String(v[7]) === '範圍內') { st.inRange++; }
    if (status === '已收錄') { st.saved++; }
    if (status === '已存在') { st.existed++; }
    if (status === '已校正日期') { st.existed++; st.repaired++; }
    if (status === '寫入失敗') { st.writeFailed++; }
    if (status.indexOf('錯誤') >= 0 || status.indexOf('失敗') >= 0) { st.errors++; }
  });
}

function cmSaveAuditChunk_(st) {
  var sh = getSheet_(CM_AUDIT_SHEET_);
  var first = Number(st.saveCursorRow || st.auditStartRow || 2);
  var end = Number(st.auditEndRow || sh.getLastRow());
  var count = Math.max(0, Math.min(200, end - first + 1));
  var values = count ? sh.getRange(first, 1, count, 12).getValues() : [];
  var handled = 0, scannedRows = 0;
  for (var i = 0; i < values.length; i++) {
    var v = values[i];
    scannedRows++;
    if (String(v[0]) !== String(st.jobId)) { continue; }
    if (String(v[8]) !== '待寫入') { continue; }
    var article = { id: String(v[1]), time: String(v[2]), title: String(v[4]),
                    text: String(v[5]), url: String(v[10]) };
    var saved = cmSavedArticle_(article.id);
    var existed = !!saved;
    var repaired = existed ? cmRepairSavedDate_(article.id, article.time, saved) : 0;
    var ok = existed || cmSaveMessage_(article);
    if (existed) { st.existed++; if (repaired) { st.repaired = Number(st.repaired || 0) + 1; } }
    else if (ok) { st.saved++; }
    else { st.errors++; st.writeFailed = Number(st.writeFailed || 0) + 1; }
    var rowNum = first + i;
    sh.getRange(rowNum, 9).setValue(repaired ? '已校正日期' : existed ? '已存在' : ok ? '已收錄' : '寫入失敗');
    sh.getRange(rowNum, 10).setValue(repaired ? '依來源時間校正會員簡訊與衍生資料，共 ' + repaired + ' 列' : existed ? '正式資料已有同一文章ID' :
      ok ? '已寫入會員簡訊，等待 GitHub Actions 解析' : '未寫入；請查看系統狀態');
    sh.getRange(rowNum, 12).setValue(nowStamp_());
    handled++;
    if (handled >= 12) { break; }
  }
  st.saveCursorRow = first + scannedRows;
  var left = Math.max(0, st.inRange - st.saved - st.existed - Number(st.writeFailed || 0));
  st.step = '寫入資料'; st.index = 4;
  st.pct = left ? Math.min(82, 68 + Math.round((st.inRange - left) / Math.max(1, st.inRange) * 14)) : 84;
  st.note = '新收 ' + st.saved + ' 篇、原已存在 ' + st.existed + ' 篇' +
            (st.repaired ? '（校正日期 ' + st.repaired + ' 篇）' : '') +
            (left ? '，尚餘 ' + left + ' 篇。' : '。');
  if (!left || st.saveCursorRow > end) { st.phase = 'audit'; }
}

function cmCountPendingSms_(since) {
  var rows = readSheetObjects_(CM_SHEET), from = cmDate_(since || '');
  return rows.filter(function (r) {
    var d = cmDate_(r['發文時間']);
    return String(r['解析狀態']).trim().indexOf('待解析') === 0 && (!from || d >= from);
  }).length;
}

function cmParseChunk_(st) {
  var before = cmCountPendingSms_(st.since);
  if (!before) {
    st.phase = 'audit'; st.step = '逐日稽核'; st.index = 3; st.pct = 92;
    st.note = '重新解析完成，正在做逐日完整性檢查。';
    return;
  }
  cmParsePendingJob(st.since);
  var left = cmCountPendingSms_(st.since);
  var total = Math.max(Number(st.parseTotal) || 0, before);
  st.parseTotal = total; st.done = total - left;
  st.step = '重新解析'; st.index = 2;
  st.pct = 20 + Math.round(st.done / Math.max(1, total) * 70);
  st.note = 'Apps Script 已解析 ' + st.done + '/' + total + ' 則，尚餘 ' + left + ' 則。';
}

function cmDateRange_(start, end) {
  var out = [];
  function parse(s) {
    var m = String(s || '').match(/^(\d{4})\/(\d{2})\/(\d{2})$/);
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0) : null;
  }
  var a = parse(start), b = parse(end);
  if (!a || !b || a > b) { return out; }
  for (var d = a; d <= b && out.length < 5000; d.setDate(d.getDate() + 1)) {
    out.push(Utilities.formatDate(d, TZ, 'yyyy/MM/dd'));
  }
  return out;
}

function cmBuildSmsAudit_(st) {
  var by = {}, errors = [], min = '', max = '';
  var basis = 'job', scopeSince = '';
  // 只有抓取模式會為本次 job 建立「會員簡訊稽核」列。
  // merge/reparse 直接處理既有會員簡訊，若錯走 job 分支，日曆會整月空白。
  if (st.jobId && ['recent', 'all', 'ids'].indexOf(st.mode) >= 0) {
    cmAuditRowsForJob_(st.jobId, st).forEach(function (x) {
      var v = x.values, d = cmDate_(v[3]);
      var rowStatus = String(v[8]);
      if (rowStatus.indexOf('錯誤') >= 0 || rowStatus.indexOf('失敗') >= 0) {
        errors.push({ id: String(v[1]), status: rowStatus, note: String(v[9]) });
      }
      if (!d) {
        return;
      }
      if (!by[d]) { by[d] = { date: d, articles: 0, zhang: 0, saved: 0, errors: 0 }; }
      by[d].articles++;
      if (String(v[6]) === '是') { by[d].zhang++; }
      if (['已收錄', '已存在', '已更新', '已校正日期'].indexOf(String(v[8])) >= 0) { by[d].saved++; }
      if (rowStatus.indexOf('失敗') >= 0 || rowStatus.indexOf('錯誤') >= 0) { by[d].errors++; }
      if (!min || d < min) { min = d; }
      if (!max || d > max) { max = d; }
    });
  } else {
    /* 不是抓取作業（新簡訊自動派的「只更新今天」、補空白、合併、重新解析）時，依會員簡訊分頁逐日核對。

       範圍只在管理者手動「重新解析」指定起始日時才縮小。自動派工帶的 since＝今天
       只代表「這一輪處理哪一天」，不是稽核範圍——先前直接拿它當範圍，
       新簡訊一進來、自動工單一跑完，後台逐日稽核就只剩今天一格，
       而且上面的掃描／張震／新收是那個自動工單自己的計數，全是 0，
       看起來像整個抓取壞掉（2026/09/15）。 */
    basis = 'sheet';
    scopeSince = st.mode === 'reparse' ? String(st.since || '') : '';
    readSheetObjects_(CM_SHEET).forEach(function (r) {
      var d = cmDate_(r['發文時間']);
      var parseState = String(r['解析狀態'] || '');
      if (!d) {
        errors.push({ id: String(r['文章ID'] || ''), status: parseState || '日期錯誤',
                      note: '尚無可驗證日期，系統會按文章 ID 回查來源；回查前不歸入任何一天。' });
        return;
      }
      if ((scopeSince && d < scopeSince) || d > st.through) { return; }
      if (!by[d]) { by[d] = { date: d, articles: 0, zhang: 0, saved: 0, errors: 0 }; }
      by[d].articles++; by[d].zhang++; by[d].saved++;
      if (parseState.indexOf('失敗') >= 0 || parseState.indexOf('日期錯誤') >= 0 ||
          parseState.indexOf('待解析') >= 0) {
        by[d].errors++;
        errors.push({ id: String(r['文章ID'] || ''), status: parseState,
                      note: parseState.indexOf('日期錯誤') >= 0 ? '系統將按文章 ID 回查來源日期；無法驗證時維持隔離。' :
                            '尚未完成，按原作業按鈕可從檢查點接續。' });
      }
      if (!min || d < min) { min = d; }
      if (!max || d > max) { max = d; }
    });
  }
  var start = (basis === 'sheet' ? scopeSince : st.since) || min, end = st.through || max;
  var days = cmDateRange_(start, end).map(function (d) {
    var x = by[d] || { date: d, articles: 0, zhang: 0, saved: 0, errors: 0 };
    x.status = x.errors ? 'error' : x.zhang && x.saved < x.zhang ? 'missing' :
               x.zhang ? 'ok' : x.articles ? 'none' : 'empty';
    return x;
  });
  var dayErrors = days.reduce(function (n, d) { return n + Number(d.errors || 0); }, 0);
  if (basis === 'sheet') {
    // 分頁核對沒有「掃描、新收」這種作業計數，回報分頁本身的數字，不拿自動工單的 0 充數。
    return { basis: basis, start: start, end: end, days: days, errors: errors.slice(0, 50),
             summary: { articles: days.reduce(function (n, d) { return n + Number(d.articles || 0); }, 0),
                        activeDays: days.filter(function (d) { return d.articles > 0; }).length,
                        errors: Math.max(dayErrors, errors.length) } };
  }
  return { basis: basis, start: start, end: end, days: days, errors: errors.slice(0, 50),
           summary: { scanned: st.scanned || 0, zhang: st.zhang || 0,
                      saved: st.saved || 0, existed: st.existed || 0,
                      repaired: st.repaired || 0,
                      errors: Math.max(Number(st.errors || 0), dayErrors) } };
}

function cmAdminWorker_() {
  // 清掉已觸發或重複排到的同名一次性觸發器；五分鐘看門狗仍會兜底。
  try {
    ScriptApp.getProjectTriggers().forEach(function (t) {
      if (t.getHandlerFunction() === 'cmAdminWorker_') { ScriptApp.deleteTrigger(t); }
    });
  } catch (e) {}
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) { return; }
  try {
    var st = getSmsParseState_();
    if (!st || st.status !== '處理中') { return; }
    if (st.phase === 'prepare') { st.phase = st.mode === 'ids' ? 'ids' : 'discover'; }
    if (st.phase === 'discover') { cmDiscoverChunk_(st); }
    else if (st.phase === 'ids') { cmIdsChunk_(st); }
    else if (st.phase === 'identity') {
      cmRecountAudit_(st);
      st.step = '辨識張震'; st.index = 2; st.pct = 52; st.phase = 'filter';
      st.note = '已先逐篇判斷：' + st.scanned + ' 篇中有 ' + st.zhang + ' 篇屬於張震。';
    } else if (st.phase === 'filter') {
      st.step = '篩選日期'; st.index = 3; st.pct = 62; st.phase = 'save';
      st.saveCursorRow = Number(st.auditStartRow || 2);
      st.note = st.since ? '再依 ' + st.since + ' 至 ' + st.through + ' 篩出 ' + st.inRange + ' 篇。' :
                           '日期有效且不在未來的張震文章共 ' + st.inRange + ' 篇。';
    } else if (st.phase === 'save') { cmSaveAuditChunk_(st); }
    else if (st.phase === 'parse') { cmParseChunk_(st); }
    else if (st.phase === 'audit') {
      if (st.mode !== 'reparse') { cmRecountAudit_(st); }
      st.step = '逐日稽核'; st.index = st.mode === 'reparse' ? 3 : 5; st.pct = 96; st.auditReady = true;
      st.phase = 'done'; st.note = '已完成逐日核對；空白日、非張震文章日與錯誤日分開呈現。';
    } else if (st.phase === 'done') {
      st.status = '完成'; st.step = '完成'; st.index = st.mode === 'reparse' ? 4 : 6; st.pct = 100;
      st.note = (st.mode === 'reparse' ? '重新解析完成。' :
        '掃描 ' + st.scanned + ' 篇，張震 ' + st.zhang + ' 篇；新收 ' + st.saved +
        ' 篇、已有 ' + st.existed + ' 篇' + (st.repaired ? '，校正日期 ' + st.repaired + ' 篇' : '') + '。') +
        (st.errors ? ' 有 ' + st.errors + ' 篇需查看稽核明細。' : ' 無日期歸檔錯誤。');
    }
    setSmsParseState_(st);
    if (st.status === '處理中') { cmScheduleAdminWorker_(); }
  } catch (e) {
    var failed = getSmsParseState_() || {};
    failed.status = '失敗'; failed.lastError = String(e && e.message || e);
    failed.note = '作業停止：' + failed.lastError; failed.auditReady = true;
    setSmsParseState_(failed);
    cmNote_('後台補抓失敗：' + failed.lastError);
  } finally {
    lock.releaseLock();
  }
}

/** 五分鐘總排程的兜底：一次性觸發器被平台中斷時，從同一階段接回。 */
function cmAdminWatchdog_() {
  var st = getSmsParseState_();
  if (!st || st.status !== '處理中') { return; }
  if (st.execution === 'github') { return; }
  var updated = new Date(String(st.updatedAt || '').replace(/\//g, '-'));
  if (!isNaN(updated.getTime()) && Date.now() - updated.getTime() < 3 * 60 * 1000) { return; }
  cmAdminWorker_();
}

/**
 * 後台的「會員簡訊：更新過去資料」。
 *
 * 舊入口保留給尚未重新部署的前端。所有模式都改為建立背景工單。
 */
function apiAdminSmsBackfill(key, mode, payload) {
  var r = apiAdminStartSmsJob(key, mode, payload || {});
  return { ok: r.ok, message: r.message || r.reason, reason: r.reason || '',
           jobId: r.jobId || '', runId: r.runId || '', runUrl: r.runUrl || '' };
}

/**
 * 徹底移除一則會員簡訊及其衍生關聯資料：
 * 1. 從「會員簡訊」分頁刪除該列
 * 2. 從「操作紀錄」刪除該文章對應的買賣紀錄（來源影片ID 為 CMONEY-<文章ID>）
 * 3. 從「會員持股」刪除該文章對應的持股紀錄（來源影片ID 為 CMONEY-<文章ID>）
 * 4. 從已讀清單 cmSeenIds 移除該文章ID，讓後續若有需要時仍可重新補抓
 * 5. 清除快取，避免殘留幽靈紀錄
 */
function cmDeleteArticles_(articleIds) {
  var ids = (articleIds || []).map(function(x) { return String(x || '').trim(); })
    .filter(function(x, i, all) { return /^\d{6,}$/.test(x) && all.indexOf(x) === i; });
  if (!ids.length) { return { ok: false, message: '未指定有效文章ID。' }; }
  var wanted = {};
  ids.forEach(function(id) { wanted[id] = true; });
  var sources = {};
  ids.forEach(function(id) { sources[cmSourceId_(id)] = true; });
  var res = { ok: true, requested: ids.length, foundIds: [], smsDeleted: 0,
              tradesDeleted: 0, holdsDeleted: 0 };

  withLock_(function () {
    var shSms = getSheet_(CM_SHEET);
    var vals = shSms.getDataRange().getValues();
    for (var r = vals.length - 1; r >= 1; r--) {
      var id = String(vals[r][0] || '').trim();
      if (wanted[id]) {
        shSms.deleteRow(r + 1);
        res.smsDeleted++;
        if (res.foundIds.indexOf(id) < 0) { res.foundIds.push(id); }
      }
    }

    [['操作紀錄', 'tradesDeleted'], ['會員持股', 'holdsDeleted']].forEach(function(pair) {
      var sh = getSheet_(pair[0]);
      var rows = sh.getDataRange().getValues();
      if (!rows.length) { return; }
      var iSrc = rows[0].map(function(h) { return String(h).trim(); }).indexOf('來源影片ID');
      if (iSrc < 0) { return; }
      for (var x = rows.length - 1; x >= 1; x--) {
        if (sources[String(rows[x][iSrc] || '').trim()]) {
          sh.deleteRow(x + 1);
          res[pair[1]]++;
        }
      }
    });

    var seen = cmSeen_();
    ids.forEach(function(id) { delete seen[id]; });
    PropertiesService.getScriptProperties().setProperty(CM_SEEN_PROP, JSON.stringify(seen));
    try { CACHE.remove('tracker'); } catch (ignore) {}
  });
  return res;
}

function cmDeleteArticle_(articleId) {
  return cmDeleteArticles_([articleId]);
}

/** 後台用：查詢已收錄的會員簡訊清單供管理與移除 */
function apiAdminListSms(key, limit, date) {
  adminAuth_(key);
  var out = { ok: true, items: [] };
  var sh;
  try { sh = getSheet_(CM_SHEET); } catch (e) { return out; }
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return out; }

  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iId = head.indexOf('文章ID'), iTime = head.indexOf('發文時間');
  var iTitle = head.indexOf('標題'), iText = head.indexOf('原文');
  var iState = head.indexOf('解析狀態'), iDetail = head.indexOf('解析明細');
  var iUrl = head.indexOf('網址');

  var want = date ? cmDate_(date) : '';
  var max = Math.max(1, Number(limit) || 40);

  for (var r = 1; r < vals.length; r++) {
    var postTime = vals[r][iTime];
    var d = cmDate_(postTime);
    if (want && d !== want) { continue; }

    var text = String(vals[r][iText] || '');
    var detail = (iDetail >= 0) ? String(vals[r][iDetail] || '') : '';
    var stocksSummary = '';
    if (detail && detail !== '[]') {
      try {
        var parsed = JSON.parse(detail);
        if (Array.isArray(parsed)) {
          stocksSummary = parsed.map(function (x) {
            return (x.name || '') + ' ' + (x.dir || x.action || '') + (x.priceText ? ' ' + x.priceText : '');
          }).join('、');
        }
      } catch (e) {}
    }

    out.items.push({
      id: String(vals[r][iId] || '').trim(),
      time: cmFmtTime_(postTime),
      date: d,
      title: String(vals[r][iTitle] || ''),
      textSnippet: text.slice(0, 90) + (text.length > 90 ? '...' : ''),
      state: String(vals[r][iState] || ''),
      stocks: stocksSummary,
      url: (iUrl >= 0) ? String(vals[r][iUrl] || '') : ''
    });

  }
  out.items.sort(function(a, b) { return String(b.time || '').localeCompare(String(a.time || '')); });
  out.items = out.items.slice(0, max);
  return out;
}

/** 會員簡訊兩個日期選擇器共用：只回傳已收錄日期與今天。 */
function apiAdminSmsDays(key) {
  adminAuth_(key);
  var dates = {};
  try {
    var vals = getSheet_(CM_SHEET).getDataRange().getValues();
    if (vals.length > 1) {
      var head = vals[0].map(function(h) { return String(h).trim(); });
      var iTime = head.indexOf('發文時間');
      for (var r = 1; r < vals.length; r++) {
        var d = iTime >= 0 ? cmDate_(vals[r][iTime]) : '';
        if (d) { dates[d] = true; }
      }
    }
  } catch (ignore) {}
  return { ok: true, dates: Object.keys(dates).sort(), today: todayStr_() };
}

/** 後台用：移除指定的會員簡訊收錄訊息 */
function apiAdminDeleteSms(key, articleId) {
  adminAuth_(key);
  var r = cmDeleteArticle_(articleId);
  if (!r.smsDeleted) {
    return { ok: false, reason: '找不到文章 ID 為 ' + articleId + ' 的收錄紀錄。' };
  }
  return {
    ok: true,
    message: '成功移除簡訊（ID: ' + articleId + '）！已刪除會員簡訊 1 列' +
             (r.tradesDeleted ? '、操作紀錄 ' + r.tradesDeleted + ' 列' : '') +
             (r.holdsDeleted ? '、會員持股 ' + r.holdsDeleted + ' 列' : '') + '。'
  };
}

/** 後台用：把清單中已勾選的文章一次移除，整批只讀取各分頁一次。 */
function apiAdminDeleteSmsBatch(key, articleIds) {
  adminAuth_(key);
  var ids = Array.isArray(articleIds) ? articleIds : [];
  if (ids.length > 100) { return { ok: false, reason: '一次最多移除 100 篇。' }; }
  var r = cmDeleteArticles_(ids);
  if (!r.ok) { return { ok: false, reason: r.message }; }
  if (!r.smsDeleted) { return { ok: false, reason: '勾選的文章都找不到，資料可能已被其他作業移除。' }; }
  return { ok: true,
    message: '已移除 ' + r.smsDeleted + ' 篇會員簡訊' +
      (r.tradesDeleted ? '、操作紀錄 ' + r.tradesDeleted + ' 列' : '') +
      (r.holdsDeleted ? '、會員持股 ' + r.holdsDeleted + ' 列' : '') + '。',
    removedIds: r.foundIds };
}

/** 把最新一則重新解析一次並印出結果，不寫試算表。調提示詞時用。 */
function cmoneyDryRun() {
  var rows = readSheetObjects_(CM_SHEET);
  if (!rows.length) { Logger.log('會員簡訊分頁是空的'); return; }
  var last = rows[rows.length - 1];
  Logger.log('原文：' + String(last['原文']).slice(0, 300));
  var items = cmParseMessage_(String(last['原文'] || ''));
  Logger.log('解析出 ' + items.length + ' 筆：');
  items.forEach(function (x) {
    Logger.log('  ' + x.tag + '　' + x.name + '（' + x.code + '）　' + x.action +
               '　' + x.priceText + '　' + x.note);
  });
}

/** v46：簡訊解析完成與郵件查詢更新是兩件事，狀態分開回報。 */
function cmContentSyncStatus_(){
  var rows=getSheet_('簡訊內容同步').getDataRange().getDisplayValues().slice(1);
  return rows.filter(function(r){return r[0];}).slice(-5).map(function(r){return {date:r[0],status:r[2],updated:r[3],note:r[4]};});
}
function cmSyncContentTick_(){
  if(Date.now()-dkExecStart_(Date.now())>120000){return;}
  var lock=LockService.getScriptLock();if(!lock.tryLock(500)){return;}
  try{
    var sh=getSheet_('簡訊內容同步'),rows=sh.getDataRange().getDisplayValues();
    var available={};getSheet_('每日推播內容').getDataRange().getValues().slice(1).forEach(function(r){if(r[1]){available[fmtDate_(r[0])]=true;}});
    for(var i=rows.length-1;i>0;i--){
      var r=rows[i];if(r[2]==='完成'||!r[0]){continue;}
      if(!available[r[0]]){continue;} // 沒有原文章時等逐字稿，不能拿簡訊生出半封日報。
      try{
        stepArticle_({date:r[0],_codesDone:true,_nested:true});
        // Python 可能在重建途中又送來同日新版本；舊結果不能把新工作標完成。
        if(String(sh.getRange(i+1,2).getDisplayValue())===String(r[1])){
          sh.getRange(i+1,3,1,3).setValues([['完成',Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd HH:mm:ss'),'郵件查詢內容已更新；已寄出的信不重寄']]);
        }
      }catch(e){
        if(String(sh.getRange(i+1,2).getDisplayValue())===String(r[1])){
          sh.getRange(i+1,3,1,3).setValues([['等待續跑',Utilities.formatDate(new Date(),TZ,'yyyy/MM/dd HH:mm:ss'),String(e.message||e).slice(0,250)]]);
        }
      }
      return; // 每一棒最多一天，保留推播與行情的執行時間。
    }
  }finally{lock.releaseLock();}
}

var CM_TX_RAW_CACHE_={};
function cmTranscriptExcerpt_(date,body,map){
  if(!date){return '';}
  if(!Object.prototype.hasOwnProperty.call(CM_TX_RAW_CACHE_,date)){
    var selected=selectTranscriptRow_(readSheetObjects_('影片清單'),'',date);
    CM_TX_RAW_CACHE_[date]=selected&&selected.row['逐字稿來源']!=='手動保留'?String(selected.row['原始逐字稿內容']||'').replace(/\s+/g,''):'';
  }
  var raw=CM_TX_RAW_CACHE_[date];if(raw.length<200){return '';}
  var selected={},all=[];
  Object.keys(map.byCode||{}).forEach(function(code){
    var name=map.byCode[code];if(typeof name!=='string'){return;}
    var forms=[name,name.replace(/(?:[-＊*]?KY|[-＊*])$/,'')];
    Object.keys(PUBLIC_CONFIRMED_NAMES).forEach(function(alias){if(PUBLIC_CONFIRMED_NAMES[alias][0]===code){forms.push(alias);}});
    forms.forEach(function(n){if(n.length>=2){all.push(n);}});
    if(forms.some(function(n){return body.indexOf(n)>=0;})){forms.forEach(function(n){selected[n]=true;});}
  });
  if(!Object.keys(selected).length){return '';}
  var escape=function(x){return x.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');};
  var re=new RegExp(all.sort(function(a,b){return b.length-a.length;}).map(escape).join('|'),'g'),matches=[],m;
  while((m=re.exec(raw))){matches.push({text:m[0],at:m.index});}
  var out=[];matches.forEach(function(x,i){if(selected[x.text]){out.push(raw.slice(x.at,Math.min(x.at+480,i+1<matches.length?matches[i+1].at:raw.length)));}});
  return out.join('\n').slice(0,2800);
}
