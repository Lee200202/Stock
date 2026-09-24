/* ==================================================================== *
 * 後台：手動投稿逐字稿
 *
 * 存在的理由
 * ----------
 * 自動取稿那條路（排程用 Gemini 聽 YouTube）有可能當天就是拿不到：
 * 直播臨時沒開、回放遲遲沒處理好、Gemini 額度用完。那時整條線就卡住，
 * 當天的資料補不回來。
 *
 * 這裡提供第二條路：管理者自己把逐字稿貼進這個後台送出，後面的潤飾、
 * 擷取、分類、代號比對、股價更新全部照原本的流程跑完。兩條路產出的資料
 * 格式完全一樣，寫進同樣的分頁，所以網站不知道也不在意是哪條路進來的。
 *
 * 手動優先：這裡一送出就把「逐字稿來源」標成「手動」，
 * 自動取稿看到之後對那一天完全停手，不覆蓋、不重抓。
 *
 * 為什麼要做成非同步
 * ------------------
 * Apps Script 的網頁應用程式單次執行有時間上限（一般帳號 6 分鐘）。
 * 一份兩萬多字的逐字稿要切成好幾段送去潤飾，加上擷取與全站重算，
 * 遠遠超過那個上限。所以送出只做兩件事：把原文存起來、建立一張工單，
 * 然後立刻回覆前端。真正的工作由觸發器一步一步做，每次只做一小段，
 * 做完把進度寫回工單並排下一棒。前端輪詢工單狀態，畫出進度條。
 *
 * 這樣的好處是就算中途某一步失敗，工單會停在那一步並記下原因，
 * 可以從那一步續跑，不必整份重來。
 * ==================================================================== */

var ADMIN_JOB_SHEET = '後台工單';
var ADMIN_VIDEO_SHEET = '今日影片候選';

// 潤飾時每一段的字元數。切太大會超過單次執行上限，切太小則段落之間
// 失去上下文、語意接不起來。一萬字左右在兩者之間，實測一段約 60 到 90 秒。
var ADMIN_CHUNK_SIZE = 9000;

// 工單的步驟。順序有意義，processAdminJob_ 依這個順序往下走。
// 步驟順序與 pipeline.py 一致。稽核、代號、校價這三道是資料品質的來源，
// 少了它們，同一份逐字稿走後台進來的結果會明顯比走上游差。
//
// 重算刻意拆成三段。整包 refreshSiteNow 要跑清產業、代號比對、基本面、
// 補日K、持股追蹤、績效六件事，加起來遠超過單次執行上限，
// 一旦超時執行就被腰斬，工單永遠停在「重算／處理中」而不會前進——
// 那正是先前卡住的原因。拆開之後每一段都在額度內跑得完。
var ADMIN_STEPS = ['潤飾', '擷取', '稽核', '代號', '校價', '寫入', '撰稿',
                   '重算A', '重算B', '重算C', '完成'];


/** 設定今日未寄整理信的最早寄送時間，實際寄送仍需文章與品質關卡就緒。 */
function apiAdminSetDailyPushStart(key, hhmm) {
  try {
    adminAuth_(key);
    var match = /^(1[2-9]|20|21):([0-5][0-9])$/.exec(String(hhmm || ''));
    if (!match || Number(match[2]) % 5 !== 0) { throw new Error('請選擇 12:00–21:55 之間、每五分鐘一格的時間。'); }
    var today = todayStr_();
    var push = readSheetObjects_('每日推播內容').filter(function (r) { return fmtDate_(r['日期']) === today; })[0];
    if (pushSentAt_(today) || (push && /已寄|寄送中|部分寄送/.test(String(push['寄送狀態'] || '')))) {
      throw new Error('今日郵件已開始寄送，不能再變更最早寄送時間。');
    }
    var value = match[1] + match[2];
    PropertiesService.getScriptProperties().setProperty('DAILY_PUSH_START_TODAY', today.replace(/\D/g, '') + '|' + value);
    return { ok: true, today: today, time: match[1] + ':' + match[2] };
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

/* ------------------------------------------------------------------ *
 * 權限
 * ------------------------------------------------------------------ */

/**
 * 驗證管理密鑰。
 * 密鑰是 Script Properties 裡的 ADMIN_KEY，與「立即刷新網站」用的是同一組。
 * 忘記或沒設過時，在編輯器執行 showDeployInfo() 會印出來（沒有就自動產生一組）。
 */
function adminAuth_(key) {
  var real = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
  if (!real) {
    throw new Error('尚未設定 ADMIN_KEY。請在 Apps Script 編輯器執行 showDeployInfo()，' +
                    '它會自動產生一組並印出來。');
  }
  if (String(key || '') !== real) {
    throw new Error('管理密鑰不正確。');
  }
  return true;
}


/* ------------------------------------------------------------------ *
 * 每 10 分鐘抓一次今天的影片
 *
 * 讓管理者不必自己去 YouTube 找網址。從台灣時間 11:30 開始每 10 分鐘
 * 查一次頻道的發布清單，找到當天的盤中家教班影片就記下來，
 * 後台一開就直接看到，按一下就帶入。
 *
 * 只在平日的 11:30 到 17:00 之間動作。這個區間之外查也不會有結果，
 * 白白消耗配額，也讓執行紀錄變得難讀。
 * ------------------------------------------------------------------ */

function pollTodayVideoJob() {
  var now = new Date();
  var tz = 'Asia/Taipei';
  var dow = Number(Utilities.formatDate(now, tz, 'u'));   // 1 一 ~ 7 日
  var hhmm = Number(Utilities.formatDate(now, tz, 'HHmm'));

  if (dow >= 6) { return; }                       // 週末不開盤
  if (hhmm < 1130 || hhmm > 1700) { return; }     // 區間外不查

  var chanId = PropertiesService.getScriptProperties().getProperty('YOUTUBE_CHANNEL_ID');
  if (!chanId) {
    Logger.log('尚未設定 YOUTUBE_CHANNEL_ID，略過今日影片偵測');
    return;
  }

  var today = Utilities.formatDate(now, tz, 'yyyy/MM/dd');
  var found = fetchTodayVideo_(chanId, today);
  if (!found) { return; }

  // 已經記過同一支就不重複寫，避免每 10 分鐘就多一列。
  var sh = getSheet_(ADMIN_VIDEO_SHEET);
  var vals = sh.getDataRange().getValues();
  for (var i = 1; i < vals.length; i++) {
    if (String(vals[i][1]) === found.id) { return; }
  }
  sh.appendRow([today, found.id, found.title,
                'https://www.youtube.com/watch?v=' + found.id,
                Utilities.formatDate(now, tz, 'yyyy/MM/dd HH:mm:ss')]);
  Logger.log('偵測到今日影片：' + found.title + '（' + found.id + '）');
}

/** 讀頻道 RSS，找出指定日期、標題符合的那一支。 */
function fetchTodayVideo_(chanId, dateStr) {
  var url = 'https://www.youtube.com/feeds/videos.xml?channel_id=' + encodeURIComponent(chanId);
  var res;
  try {
    res = UrlFetchApp.fetch(url, { muteHttpExceptions: true });
  } catch (e) {
    Logger.log('讀取頻道 RSS 失敗：' + e);
    return null;
  }
  if (res.getResponseCode() !== 200) { return null; }

  var xml;
  try { xml = XmlService.parse(res.getContentText()); }
  catch (e) { return null; }

  var ns = XmlService.getNamespace('http://www.w3.org/2005/Atom');
  var yt = XmlService.getNamespace('yt', 'http://www.youtube.com/xml/schemas/2015');
  var entries = xml.getRootElement().getChildren('entry', ns);

  for (var i = 0; i < entries.length; i++) {
    var e = entries[i];
    var title = e.getChildText('title', ns) || '';
    var vid = e.getChildText('videoId', yt) || '';
    var pub = e.getChildText('published', ns) || '';
    if (!vid || !pub) { continue; }

    var d = Utilities.formatDate(new Date(pub), 'Asia/Taipei', 'yyyy/MM/dd');
    if (d !== dateStr) { continue; }
    // 標題關鍵字：盤中家教班。避免抓到頻道的其他類型影片。
    if (title.indexOf('盤中') < 0 && title.indexOf('家教') < 0) { continue; }
    return { id: vid, title: title, date: d };
  }
  return null;
}


/* ------------------------------------------------------------------ *
 * 前端 API
 * ------------------------------------------------------------------ */

/** 登入。成功時順便把後台首頁需要的資料一次帶回去，省一次往返。 */
function apiAdminLogin(key) {
  try {
    adminAuth_(key);
    return { ok: true, data: adminState_() };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/** 後台狀態：今天的影片候選、今天是否已有資料、目前有沒有工單在跑。 */
function apiAdminState(key) {
  try {
    adminAuth_(key);
    return { ok: true, data: adminState_() };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function adminState_() {
  var today = todayStr_();

  // 今日影片候選（由 pollTodayVideoJob 每 10 分鐘寫入）
  var cands = [];
  try {
    readSheetObjects_(ADMIN_VIDEO_SHEET).forEach(function (r) {
      if (fmtDate_(r['日期']) === today) {
        cands.push({ id: r['影片ID'], title: r['標題'], url: r['網址'],
                     at: String(r['偵測時間'] || '') });
      }
    });
  } catch (e) { /* 分頁還沒建立時視為沒有候選 */ }

  // 今天是否已經有資料，以及「哪幾天已經有逐字稿」。
  // 後者給投稿日期的日曆用：有標記的那幾天代表投過了，
  // 補登時一眼看得出還缺哪一天，不必先點下去才知道。
  var hasData = false, videoStatus = '';
  var postedDates = [];
  try {
    readSheetObjects_('影片清單').forEach(function (r) {
      var d = fmtDate_(r['發布日期']);
      if (d && String(r['原始逐字稿內容'] || '').trim()) { postedDates.push(d); }
      if (d === today) {
        videoStatus = String(r['處理狀態'] || '');
        if (videoStatus === '完成') { hasData = true; }
      }
    });
  } catch (e) { /* 忽略 */ }

  var trades = 0;
  try {
    readSheetObjects_('操作紀錄').forEach(function (r) {
      if (fmtDate_(r['日期']) === today) { trades++; }
    });
  } catch (e) { /* 忽略 */ }

  // Apps Script 執行紀錄的網址。
  //
  // 後台的作業分兩種：派給 GitHub 的（投稿、只刷新畫面）看得到 Actions 日誌，
  // 在 Apps Script 這邊跑的（全面重整、重寫郵件、重新分類、預覽）沒有 Actions
  // 可看，它們的每一行輸出都在 Apps Script 的執行紀錄裡。
  // 把網址一起送給前端，兩種作業就都能一鍵點到自己的日誌，不必記哪個在哪裡看。
  var execUrl = '';
  try {
    execUrl = 'https://script.google.com/home/projects/' +
              ScriptApp.getScriptId() + '/executions';
  } catch (e) { /* 取不到就不給連結，不影響其他功能 */ }

  return {
    today: today,
    candidates: cands,
    hasData: hasData,
    postedDates: postedDates,
    videoStatus: videoStatus,
    todayTrades: trades,
    job: displayJob_(),
    execUrl: execUrl,
    chunkSize: ADMIN_CHUNK_SIZE
  };
}

/**
 * 送出逐字稿。
 *
 * 只做「存原文、建工單、排第一棒」三件事就回覆，因為真正的處理遠超過
 * 網頁應用程式的單次執行上限。前端拿到 jobId 之後改用輪詢看進度。
 */
function apiAdminSubmit(key, payload) {
  var lock=LockService.getScriptLock();
  if(!lock.tryLock(1000)){return {ok:false,reason:'目前有其他投稿或整併正在寫入，請稍後送出'};}
  try{return submitTranscriptLocked_(key,payload);}finally{lock.releaseLock();}
}

function submitTranscriptLocked_(key, payload) {
  try {
    adminAuth_(key);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }

  payload = payload || {};
  var text = String(payload.transcript || '').trim();
  var dateStr = fmtDate_(payload.date) || todayStr_();

  // 影片網址改成選填。
  //
  // 手動投稿的重點是逐字稿，網址只是用來對應影片。偵測不到今日影片時，
  // 硬要求管理者先去 YouTube 找網址才能貼逐字稿，是把系統的不便轉嫁給人。
  // 沒有網址就用日期產一個代號，之後偵測到真正的影片再對上即可。
  var videoId = extractVideoId_(payload.videoUrl);
  if (!videoId) {
    videoId = 'MANUAL-' + dateStr.replace(/\//g, '');
  }
  if (text.length < 500) {
    return { ok: false, reason: '逐字稿只有 ' + text.length + ' 字，太短了。' +
                                '請確認整份都貼進來了（正常一場約兩萬字）。' };
  }

  // ---- 跨日鎖 ----
  // 預設只允許當天。開放補登時要另外帶 allowBackfill，前端會要求再次確認。
  var today = todayStr_();
  if (dateStr !== today) {
    if (!payload.allowBackfill) {
      return { ok: false, reason: '只能投稿今天（' + today + '）的內容。' +
                                  '要補登其他日期請先在介面上解鎖補登模式。' };
    }
    if (dateStr > today) {
      return { ok: false, reason: '不能投稿未來日期。' };
    }
  }

  // ---- 已有工單在跑就不要再開一張 ----
  var cur = currentJob_();
  if (cur && ['處理中','等待日K','等待續跑'].indexOf(cur.status)>=0) {
    return { ok: false, reason: '目前已有一張工單在處理（' + cur.videoId + '，' +
                                cur.step + '）。請等它跑完或先取消。' };
  }

  // ---- 覆蓋確認 ----
  var exists = hasTranscriptRecordsOnDate_(dateStr);
  if (exists && !payload.allowOverwrite) {
    return { ok: false, reason: '這支影片（' + videoId + '）已經處理完成過了。' +
                                '要重跑請勾選「覆蓋既有資料」。' };
  }

  if (text.length>49000) { return {ok:false,reason:'原文超過單格49000字上限，請移除多餘空白或擴充儲存後再投稿；本輪不截斷原文'}; }
  requireTranscriptHeaders_();
  // ---- 原文先落地 ----
  // 先寫進「影片清單」再開始處理。萬一後面每一步都失敗，至少原文還在，
  // 不必請管理者重貼一次兩萬字。
  saveRawTranscript_(videoId, dateStr, payload.title || ('手動投稿 ' + dateStr), text);

  var jobId = 'J' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss');
  getSheet_(ADMIN_JOB_SHEET).appendRow([
    jobId, dateStr, videoId, '處理中', '排程中', 0, 6, '等待 GitHub 接手',
    nowStamp_(), nowStamp_(), '手動投稿', transcriptSha256_(text)
  ]);

  // 交給 GitHub Actions 執行，不在這裡跑。
  //
  // Apps Script 單次執行有 6 分鐘上限，而潤飾兩萬多字加上擷取、稽核、
  // 代號比對、價位校對、撰稿，遠遠超過那個上限。先前用「分段 + 觸發器接力」
  // 硬撐，只要某一棒超時就整個中斷、進度停在原地而且沒有錯誤訊息，
  // 排查非常困難。GitHub Actions 沒有這個限制，執行紀錄也看得到每一行輸出。
  var d = dispatchGithub_({ admin_job: 'true', refresh_site: 'true' });
  if (!d.ok) {
    updateJob_({ status: '失敗', note: '派工失敗：' + d.reason });
    return { ok: false, reason: '逐字稿已存好，但派工給 GitHub 失敗：' + d.reason };
  }

  return { ok: true, jobId: jobId,
           message: '已收下 ' + text.length + ' 字，交給 GitHub Actions 處理。',
           runUrl: d.runUrl };
}

/** 工單進度。前端每幾秒問一次。 */
function apiAdminJobStatus(key) {
  try {
    adminAuth_(key);
    // 顯示用：後台工單與自動工單取較新的一張（v54）。業務判斷仍用 currentJob_。
    var j = displayJob_();
    // 失敗時把「該怎麼辦」一起帶回去，前端才不必自己判讀錯誤訊息。
    if (j && j.status === '失敗') { j.advice = troubleshoot_(j.note); }
    return { ok: true, job: j };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 立即刷新網站內容（同步版，一次跑完）。
 *
 * 後台的按鈕已經改用分段版的 apiAdminStartRefresh，因為同步跑一到兩分鐘
 * 常常超過網頁請求的等待上限，前端顯示連線失敗但後端其實還在跑，很容易誤判。
 * 這一支保留給編輯器手動呼叫，或給外部程式（例如 GitHub 跑完後）用，
 * 那些情境沒有網頁等待的問題。
 */
function apiAdminRefresh(key) {
  try {
    adminAuth_(key);
    return { ok: true, summary: refreshSiteNow() };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 續跑卡住的工單。
 *
 * 為什麼需要：某一步若因為超時而被中斷（不是拋例外，是整個執行被腰斬），
 * updateJob_ 與 scheduleNextStep_ 都沒機會執行，工單就停在原地不動，
 * 而且不會有任何錯誤訊息。重算已經拆成三小段大幅降低發生機率，
 * 但外部服務偶爾很慢時仍可能發生，所以留一個手動續跑的入口。
 * 它會從目前停住的那一步重新開始，前面完成的步驟不必重做。
 */
/**
 * 續跑卡住的工單。
 *
 * 這一支原本排一個 Apps Script 觸發器（scheduleNextStep_），但那是已經退役的
 * 那條路——投稿是用 dispatchGithub_ 交給 GitHub Actions 跑的，兩邊根本不是
 * 同一套。實際的症狀就是使用者看到的：按了續跑，工單狀態一動也不動，
 * 試算表上還寫著「等待 GitHub 接手」，而 GitHub 那邊從頭到尾沒有被叫醒。
 *
 * 更糟的是那個觸發器真的跑起來時：processAdminJob_ 認得的步驟名稱是舊的
 * （潤飾／擷取／稽核／代號／校價／寫入／撰稿／重算ABC），而 GitHub 那條路
 * 寫進工單的是新的（讀取原文／稽核補漏／查驗幻覺／代號比對／名稱釐清／
 * 刷新網站……）。名稱對不上就掉進 else，而 else 會把工單標成「完成」——
 * 一張卡住的工單按下續跑，結果變成假完成。
 *
 * 現在續跑就是「再派一次工給 GitHub」，與送出走同一條路。
 * 上游本來就是接續的：已經寫好的逐字稿、已經完成的步驟都不會重做。
 * 只有在完全沒有 GitHub 設定時，才退回觸發器那條路。
 */
function apiAdminResumeJob(key) {
  try {
    adminAuth_(key);
    var j = currentJob_();
    if (!j) { return { ok: false, reason: '沒有工單。' }; }
    if (j.status === '完成') { return { ok: false, reason: '這張工單已經完成了。' }; }

    PropertiesService.getScriptProperties().deleteProperty('txResume:'+j.id);
    updateJob_({ status: '處理中',
                 note: '手動續跑：正在重新派工給 GitHub（原本停在「' + j.step + '」）' });

    var d = dispatchGithub_({ admin_job: 'true', refresh_site: 'true' });
    if (d.ok) {
      updateJob_({ note: '手動續跑：已派工給 GitHub Actions，從「' + j.step + '」接續' });
      return { ok: true, step: j.step, where: 'github', runUrl: d.runUrl,
               message: '已重新派工給 GitHub Actions，從「' + j.step + '」接續。' +
                        '已經完成的步驟不會重做，約一到三分鐘後進度會開始變動。' };
    }

    // 退路：沒有 GITHUB_REPO / GITHUB_TOKEN 時才走觸發器。
    // 那條路只認得舊的步驟名稱，所以先講清楚它可能接不上。
    try {
      scheduleNextStep_();
    } catch (e2) {
      updateJob_({ status: '失敗',
                   note: '續跑失敗：GitHub 派工失敗（' + d.reason.slice(0, 80) +
                         '），改排觸發器也失敗（' + String(e2 && e2.message || e2).slice(0, 80) + '）' });
      return { ok: false,
               reason: 'GitHub 派工失敗：' + d.reason +
                       '　改用 Apps Script 觸發器也排不出來：' +
                       String(e2 && e2.message || e2) +
                       '　請先確認 GITHUB_REPO 與 GITHUB_TOKEN 有設好。' };
    }
    updateJob_({ note: '手動續跑：沒有 GitHub 派工設定，改用 Apps Script 觸發器（' +
                       d.reason.slice(0, 60) + '）' });
    return { ok: true, step: j.step, where: 'appsscript',
             message: '沒有 GitHub 派工設定（' + d.reason.slice(0, 60) +
                      '），改用 Apps Script 觸發器接手。這條路只認得舊版的步驟名稱，' +
                      '接不上的話請設定 GITHUB_REPO 與 GITHUB_TOKEN 後再試。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/** 取消目前工單。卡住時用。 */
function apiAdminCancelJob(key) {
  try {
    adminAuth_(key);

    // 先把已經排好的下一棒刪掉。
    // 只改狀態不刪觸發器的話，那一棒仍會被喚起，雖然它會因為狀態不是
    // 「處理中」而立刻返回，但執行紀錄會多出一次看不出用途的呼叫，
    // 也白佔一個觸發器名額。
    cleanupAdminTriggers_();

    var sh = getSheet_(ADMIN_JOB_SHEET);
    var vals = sh.getDataRange().getValues();
    var n = 0, at = '';
    for (var i = vals.length - 1; i >= 1; i--) {
      if (['處理中','等待日K','等待續跑'].indexOf(String(vals[i][3]))>=0) {
        sh.getRange(i + 1, 4).setValue('已取消');
        sh.getRange(i + 1, 10).setValue(nowStamp_());
        if (!at) { at = String(vals[i][4] || ''); }
        n++;
      }
    }

    if (!n) { return { ok: false, reason: '目前沒有進行中的工單可以取消。' }; }
    // GitHub 上那一次執行不會因為這裡改了狀態就停下來——取消的是「這張工單」，
    // 不是「那次執行」。講清楚，免得以為按了就一定沒事了。
    return { ok: true, cancelled: n,
             message: '工單已取消（停在「' + at + '」）。已完成的步驟保留著，' +
                      '重新送出同一份逐字稿即可接續。' +
                      '　注意：GitHub Actions 上正在跑的那一次不會因此中止，' +
                      '要立刻停掉請到 Actions 頁面按 Cancel run。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}


/* ------------------------------------------------------------------ *
 * 工單處理
 * ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ *
 * 進度卡要顯示的工單（v54）
 *
 * currentJob_ 只看「後台工單」，而且十幾處拿它做業務判斷（投稿前檢查、續跑、看門狗），
 * 那些不能改。這裡是另一支、只給畫面用：取「後台工單」與「自動工單」較新的那一張。
 *
 * 2026/09/23 管理者回報：後台投稿時十五格會依序亮起，自動取稿進 pipeline 時完全不動，
 * 停在上一次投稿的狀態——因為自動流程從來不開工單。現在 pipeline 會開自動工單。
 * ------------------------------------------------------------------ */
/* ------------------------------------------------------------------ *
 * 今日狀態（v54，I14）：後台首頁一眼看出今天自動化跑到哪、哪裡要處理。
 *
 * 健康指標：節目、逐字稿、當日紀錄、會員簡訊、每日推播、績效、日K、失敗次數。
 * 時間軸：系統狀態分頁今天的每一列（輪詢只算次數，不逐列列出），加上今天的後台／自動工單。
 * 只讀，不改任何資料。會員簡訊只讀「發文時間」一欄（AGENTS：會員簡訊分頁不整張讀）。
 * ------------------------------------------------------------------ */
function apiAdminTodayStatus(key) {
  try {
    adminAuth_(key);
    var today = todayStr_(), hm = Number(Utilities.formatDate(new Date(), TZ, 'HHmm'));
    var trading = isTradingDayToday_();
    var logs = readSheetObjects_('系統狀態').filter(function (r) { return String(r['時間'] || '').indexOf(today) === 0; });
    var video = readSheetObjects_('影片清單').filter(function (r) { return fmtDate_(r['發布日期']) === today; })[0];
    var trades = readSheetObjects_('操作紀錄').filter(function (r) { return fmtDate_(r['日期']) === today; });
    var holds = readSheetObjects_('會員持股').filter(function (r) { return fmtDate_(r['日期']) === today; });
    var push = readSheetObjects_('每日推播內容').filter(function (r) { return fmtDate_(r['日期']) === today; })[0];
    var perfLast = readSheetObjects_('每日績效').map(function (r) { return fmtDate_(r['日期']); }).filter(String).sort().pop() || '';
    var sms = 0;
    try {
      var sh = getSheet_('會員簡訊'), head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0].map(String);
      var c = head.indexOf('發文時間') + 1, n = sh.getLastRow() - 1;
      if (c > 0 && n > 0) {
        sms = sh.getRange(2, c, n, 1).getDisplayValues().filter(function (v) {
          return String(v[0]).replace(/-/g, '/').indexOf(today) === 0; }).length;
      }
    } catch (e) {}
    var noShow = logs.some(function (r) { return String(r['類別'] || '') === '今日無直播'; });
    var plannedNoShow = logs.some(function (r) { return String(r['類別'] || '') === '預告停播'; });
    var fails = logs.filter(function (r) { return String(r['類別']) === '失敗'; });
    var polls = logs.filter(function (r) { var k = String(r['類別']); return k === '開始' || k === '輪詢'; }).length;
    var vStatus = video ? String(video['處理狀態'] || '') : '';
    var v1 = video ? String(video['原始逐字稿內容'] || '').length : 0, v2 = video ? String(video['修飾後逐字稿內容'] || '').length : 0;
    if (vStatus === '完成' && v1 > 200) { noShow = false; plannedNoShow = false; }
    var dk = null; try { dk = dailyKState_(); } catch (e) {}
    var behind = perfLast ? tradingDaysAfter_(perfLast, latestTradingDayStr_()) : 0;

    var items = [];
    function add(label, value, tone, hint) { items.push({ label: label, value: value, tone: tone, hint: hint || '' }); }
    if (!trading) { add('今天', '休市', 'idle', '休市日不取稿、不寄信，行情照常更新。'); }
    else if (noShow) { add('今日節目', '今日無直播', 'idle', '上游確認頻道今天沒有這一集，已停止取稿；訂閱者不會收到每日整理。'); }
    else if (plannedNoShow && !video) { add('今日節目', '預告停播', 'idle', '前一集已預告請假；已停止密集輪詢，後續排程會單次查片，12:30 後確認。'); }
    else if (vStatus === '完成') { add('今日節目', '整理完成', 'ok'); }
    else if (video) { add('今日節目', vStatus || '處理中', 'warn', '影片已抓到，正在判讀；進度看下方「處理進度」。'); }
    else { add('今日節目', '尚未偵測到影片', hm >= 1400 ? 'warn' : 'idle',
               hm >= 1400 ? '已過 14:00 仍沒有影片：到「投稿逐字稿」手動貼上，或按「開始取稿」再試一次。'
                          : '通常 11:30–13:30 抓到。已輪詢 ' + polls + ' 次。'); }
    if (trading && !noShow) {
      add('逐字稿', video ? (v1 + ' 字／修飾 ' + v2 + ' 字') : '—', v2 > 200 ? 'ok' : video ? 'warn' : 'idle');
      add('今日紀錄', trades.length + ' 筆操作、' + holds.length + ' 筆持股', (trades.length || holds.length) ? 'ok' : 'idle');
      add('每日推播', push ? String(push['寄送狀態'] || '未寄送') : '尚未產生', push && /已寄/.test(String(push['寄送狀態'])) ? 'ok' : 'idle');
    }
    add('會員簡訊', sms + ' 則', sms ? 'ok' : 'idle', sms ? '' : '今天還沒有會員簡訊；平日 09:05 起每 10 分鐘自動檢查。');
    /* 郵件投遞健康（v54，Codex 規格 88）：讀寄送帳本。「服務接受」是 MailApp 收下了，不等於已送到收件匣。 */
    var dsum = {};
    try { dsum = deliverySummaryForDate_(today); } catch (e) { dsum = {}; }
    var dkeys = Object.keys(dsum), tot = { total: 0, accepted: 0, failed: 0, unknown: 0, open: 0 }, lastErr = '';
    dkeys.forEach(function (k) { ['total', 'accepted', 'failed', 'unknown', 'open'].forEach(function (f) { tot[f] += dsum[k][f]; });
      if (dsum[k].lastError) { lastErr = dsum[k].lastError; } });
    var quotaLeft = null;
    try { quotaLeft = MailApp.getRemainingDailyQuota(); } catch (e) { quotaLeft = null; }
    if (dkeys.length) {
      add('郵件投遞', '服務接受 ' + tot.accepted + '/' + tot.total + '（' + dkeys.length + ' 則信）',
          tot.failed || tot.unknown ? 'err' : tot.open ? 'warn' : 'ok',
          (tot.open ? '待續送 ' + tot.open + ' 位，下一棒自動補寄。' : '') +
          (tot.failed ? '失敗 ' + tot.failed + ' 位。' : '') + (tot.unknown ? '結果不明 ' + tot.unknown + ' 位（寄出與記錄之間被中斷，不自動重寄）。' : '') +
          (lastErr ? '最近錯誤：' + lastErr + '。' : '') + (quotaLeft !== null ? '今天剩餘寄信額度 ' + quotaLeft + ' 封。' : ''));
    } else {
      add('郵件投遞', '今天還沒有寄信', 'idle', quotaLeft !== null ? '今天剩餘寄信額度 ' + quotaLeft + ' 封。' : '');
    }
    add('績效最後一筆', perfLast || '尚無', behind > 1 ? 'warn' : 'ok',
        behind > 1 ? '落後 ' + (behind - 1) + ' 個交易日；17:30／19:30 會自動補記，也可到「維護工具」按開始刷新。' : '');
    if (dk) {
      add('補日K', dk.finishedAt ? '已完成（' + String(dk.finishedAt).slice(5, 16) + '）' : '進行中：做到 ' + (dk.lastCode || '開頭'),
          dk.finishedAt ? 'ok' : 'warn', dk.lastError ? '最近的問題：' + String(dk.lastError).slice(0, 80) : '');
    }
    add('今天失敗', fails.length + ' 次', fails.length ? 'err' : 'ok',
        fails.length ? '最近一次：' + String(fails[fails.length - 1]['說明'] || '').slice(0, 90) : '');

    var timeline = logs.filter(function (r) { var k = String(r['類別']); return k !== '輪詢' && k !== '開始'; })
      .map(function (r) {
        return { time: String(r['時間'] || '').slice(11, 16), kind: String(r['類別'] || ''),
                 text: String(r['說明'] || '').slice(0, 120), where: String(r['執行環境'] || '') };
      });
    [ADMIN_JOB_SHEET, AUTO_JOB_SHEET].forEach(function (name) {
      try {
        readSheetObjects_(name).filter(function (r) { return String(r['開始時間'] || '').indexOf(today) === 0; })
          .forEach(function (r) {
            timeline.push({ time: String(r['開始時間']).slice(11, 16), kind: name === AUTO_JOB_SHEET ? '自動工單' : '後台工單',
                            text: String(r['狀態'] || '') + '：' + String(r['步驟'] || '') + (r['備註'] ? '｜' + String(r['備註']).slice(0, 80) : ''),
                            where: String(r['來源'] || '') });
          });
      } catch (e) {}
    });
    timeline.sort(function (a, b) { return a.time < b.time ? -1 : a.time > b.time ? 1 : 0; });
    if (polls) { timeline.unshift({ time: '', kind: '輪詢', text: '今天取稿輪詢 ' + polls + ' 次（不逐筆列出）', where: '' }); }
    var triggerNames = [], triggerError = '';
    try { triggerNames = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); }); }
    catch (e) { triggerError = String(e.message || e).slice(0, 120); }
    var heartbeatAt = Number(PropertiesService.getScriptProperties().getProperty('OPS_HEARTBEAT_AT') || 0);
    var heartbeatAge = heartbeatAt ? Math.round((Date.now() - heartbeatAt) / 60000) : null;
    var activeHours = trading && hm >= 900 && hm <= 2200;
    var missingTriggers = triggerError ? [] : ['everyFiveMinJob', 'cmoneyPollJob', 'backfillDailyKJob', 'rebuildHoldingsTrackerJob', 'snapshotPerformanceJob']
      .filter(function (name) { return triggerNames.indexOf(name) < 0; });
    var alerts = [];
    if (missingTriggers.length) { alerts.push('缺少排程：' + missingTriggers.join('、')); }
    if (triggerError) { alerts.push('無法讀取排程：' + triggerError); }
    if (activeHours && (heartbeatAge === null || heartbeatAge > 20)) { alerts.push('五分鐘排程超過 20 分鐘沒有啟動紀錄'); }
    if (tot.failed || tot.unknown) { alerts.push('郵件有失敗或結果不明的收件者，請查看寄送帳本'); }
    if (fails.length) { alerts.push('今日系統狀態記錄 ' + fails.length + ' 次失敗'); }
    return { ok: true, today: today, now: Utilities.formatDate(new Date(), TZ, 'HH:mm'), items: items,
      timeline: timeline.slice(-40), ops: { trading: trading, noShow: noShow, plannedNoShow: plannedNoShow && !video, videoStatus: vStatus,
        rawChars: v1, polishedChars: v2, mailStatus: push ? String(push['寄送狀態'] || '') : '',
        deliveries: tot, mailKinds: dkeys.length, mailQuotaLeft: quotaLeft, smsCount: sms,
        perfLast: perfLast, dailyK: dk ? { finishedAt: dk.finishedAt || '', lastCode: dk.lastCode || '', lastError: dk.lastError || '' } : null,
        heartbeatAge: heartbeatAge, heartbeatAt: heartbeatAt ? Utilities.formatDate(new Date(heartbeatAt), TZ, 'HH:mm') : '',
        mailStart: dailyPushStartTime_(today).replace(/^(..)(..)$/, '$1:$2'),
        missingTriggers: missingTriggers, triggerError: triggerError, alerts: alerts, failures: fails.length } };
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

/* ------------------------------------------------------------------ *
 * 行情完整性（v54，Codex 規格 96–98）
 *
 * 逐檔檢查日K快取：追蹤中的每一檔，在「近 12 個月（補日K的區間）」裡，
 * 依休市表應該有日K的交易日，有幾天缺。不看「有幾根」——有 240 根不代表中間沒漏。
 *   上市前下限：補日K已確認「這一天以前沒有資料」的，下限之前不算缺（見 Cachebuilder 的 dailyKFloors_）。
 *   今天：16:30 前今天還沒收盤，不算缺。
 *   停牌、無成交：沒有外部資料可以分辨，照樣列成缺口，原因寫「可能停牌或來源缺資料」，不自動補零。
 * 只讀一次日K快取，不打任何外部 API；補缺口仍由既有的補日K（只補缺口）處理。
 * ------------------------------------------------------------------ */
function apiAdminKCoverage(key) {
  try {
    adminAuth_(key);
    var codes = trackedCodes_(), want = {};
    codes.forEach(function (c) { want[String(c)] = true; });
    var vals = getSheet_('日K快取').getDataRange().getValues();
    var head = (vals[0] || []).map(function (h) { return String(h).trim(); });
    var cCode = head.indexOf('代號'), cDate = head.indexOf('日期'), cClose = head.indexOf('收');
    var have = {};
    for (var i = 1; i < vals.length; i++) {
      var code = String(vals[i][cCode] || '').trim();
      if (!want[code]) { continue; }
      var v = Number(vals[i][cClose]);
      if (!(isFinite(v) && v > 0)) { continue; }
      var d = fmtDate_(vals[i][cDate]);
      if (d) { (have[code] = have[code] || {})[d] = 1; }
    }
    var range = dailyKRange_(), from = range.from.replace(/-/g, '/'), to = todayStr_();
    var closed = Number(Utilities.formatDate(new Date(), TZ, 'HHmm')) >= 1630;
    var floors = dailyKFloors_();
    // 應有交易日（依休市表），近 12 個月
    var days = [], cur = new Date(from.replace(/\//g, '-') + 'T12:00:00+08:00');
    for (var k = 0; k < 400; k++) {
      var ds = Utilities.formatDate(cur, TZ, 'yyyy/MM/dd');
      if (ds > to) { break; }
      if (isTradingDateStr_(ds) && (ds < to || closed)) { days.push(ds); }
      cur = new Date(cur.getTime() + 86400000);
    }
    var rows = [], affected = 0, missingTotal = 0, oldest = '';
    codes.forEach(function (c) {
      var h = have[c] || {}, got = Object.keys(h).sort();
      var start = floors[c] && floors[c] > from ? floors[c] : (got.length && got[0] > from ? got[0] : from);
      var miss = days.filter(function (d) { return d >= start && !h[d]; });
      if (!got.length) { miss = days.slice(); }
      if (miss.length) {
        affected++; missingTotal += miss.length;
        if (!oldest || miss[0] < oldest) { oldest = miss[0]; }
      }
      rows.push({ code: c, bars: got.length, first: got[0] || '', last: got[got.length - 1] || '', floor: floors[c] || '',
                  missing: miss.length, sample: miss.slice(0, 6), recentMissing: miss.filter(function (d) { return d >= days[Math.max(0, days.length - 5)]; }).length,
                  reason: !got.length ? '日K快取沒有這一檔' : miss.length ? (miss.length <= 3 ? '零星缺日（可能停牌或來源缺資料）' : '缺口待補') : '' });
    });
    rows.sort(function (a, b) { return b.missing - a.missing || (a.code < b.code ? -1 : 1); });
    var dk = null; try { dk = dailyKState_(); } catch (e) {}
    return { ok: true, from: from, to: to, tradingDays: days.length, codes: codes.length, affected: affected,
             missingTotal: missingTotal, oldest: oldest, rows: rows.slice(0, 200),
             backfill: dk ? { finishedAt: dk.finishedAt || '', lastCode: dk.lastCode || '', failed: dk.failed || 0, updatedAt: dk.updatedAt || '' } : null,
             note: '週K、月K由同一份日K聚合，日K齊了週月就齊；本週、本月尚未結束的那一根會隨日K更新。60 分K另存小時K分頁，這裡不列。' };
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

/* ------------------------------------------------------------------ *
 * 現有持股：直接改成本（v54，I12）
 *
 * 列出持股追蹤裡「持有中」的每一檔（本回合開始日、系統推算的成本與來源、是否已被改過），
 * 管理者填新的成本後寫進「持股成本覆寫」，排一輪同步（持股追蹤 → 歷史績效 → 目前績效），
 * 不必回到開倉那一天去改原始紀錄，也不會重寫郵件。
 * ------------------------------------------------------------------ */
function apiAdminHeldList(key) {
  try {
    adminAuth_(key);
    var ov = readCostOverrides_();
    var t = getHoldingsTracker();
    var held = (t.held || (t.items || []).filter(function (i) { return i.stillHeld; }));
    return { ok: true, items: held.map(function (i) {
      var o = ov[String(i.code) + '|' + (i.roundStart || i.firstBuy)] || null;
      return { code: i.code, name: i.name, valid: !!i.valid, roundStart: i.roundStart || i.firstBuy,
               entry: i.entry, entrySrc: i.entrySrc || '', current: i.current, ret: i.ret,
               override: o ? { cost: o.cost, note: o.note, at: o.at } : null };
    }) };
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

function apiAdminSetHoldingCost(key, code, roundStart, cost, note) {
  try {
    adminAuth_(key);
    code = String(code || '').trim();
    var d = fmtDate_(roundStart), c = Number(String(cost || '').replace(/[,，\s]/g, ''));
    if (!/^\d{4,6}[A-Z]?$/.test(code)) { return { ok: false, reason: '代號格式不對：' + code }; }
    if (!d) { return { ok: false, reason: '缺少回合開始日。' }; }
    var clear = cost === '' || cost === null || cost === undefined;
    if (!clear && !(c > 0 && c < 100000)) { return { ok: false, reason: '成本要是大於 0 的數字（收到：' + cost + '）。' }; }
    var held = (getHoldingsTracker().held || []).filter(function (i) {
      return String(i.code) === code && (i.roundStart || i.firstBuy) === d;
    })[0];
    if (!held && !clear) { return { ok: false, reason: code + ' 在 ' + d + ' 開始的回合目前不是持有中，請重新整理清單。' }; }
    withLock_(function () {
      var sh = getSheet_('持股成本覆寫');
      var vals = sh.getDataRange().getValues(), head = vals[0].map(String);
      var ci = function (k) { return head.indexOf(k); };
      for (var r = vals.length - 1; r >= 1; r--) {
        if (String(vals[r][ci('代號')]).trim() === code && fmtDate_(vals[r][ci('回合開始日')]) === d) { sh.deleteRow(r + 1); }
      }
      if (!clear) {
        var row = head.map(function () { return ''; });
        row[ci('代號')] = code; row[ci('股票名稱')] = held ? held.name : '';
        row[ci('回合開始日')] = d; row[ci('成本')] = c; row[ci('備註')] = String(note || '').slice(0, 200);
        row[ci('修改時間')] = nowStamp_();
        row[ci('修改前成本')] = held && held.entry != null ? held.entry : '';
        row[ci('修改前來源')] = held ? String(held.entrySrc || '') : '';
        sh.appendRow(row);
      }
    });
    var q = queueCostSync_(d);
    return { ok: true, message: (clear ? '已還原 ' + code + ' 的系統成本' : '已把 ' + code + (held ? ' ' + held.name : '') + ' 的成本改成 ' + c) +
             '。持股追蹤與績效會從 ' + q.date + ' 起重算，約 1–3 分鐘；進度在下方。' };
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

var AUTO_JOB_SHEET = '自動工單';
var AUTO_JOB_STALE_MIN_ = 40;   // 自動工單超過這麼久沒回報，多半是那一輪被 GitHub 中止

/** 試算表時間欄一律轉成台北時間字串（先前前端印出「Tue Sep 22 2026 12:02:59 GMT+0800」）。 */
function stampText_(v) {
  if (Object.prototype.toString.call(v) === '[object Date]') {
    return Utilities.formatDate(v, TZ, 'yyyy/MM/dd HH:mm:ss');
  }
  return String(v || '');
}

function stampMs_(v) {
  if (Object.prototype.toString.call(v) === '[object Date]') { return v.getTime(); }
  var t = Date.parse(String(v || '').replace(/-/g, '/'));
  return isFinite(t) ? t : 0;
}

function latestJobOf_(sheetName, kind) {
  var vals;
  try { vals = getSheet_(sheetName).getDataRange().getValues(); } catch (e) { return null; }
  if (!vals || vals.length < 2) { return null; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  function at(row, name, fallback) {
    var i = head.indexOf(name);
    if (i < 0) { i = fallback; }
    return (i >= 0 && i < row.length) ? row[i] : '';
  }
  for (var r = vals.length - 1; r >= 1; r--) {
    var row = vals[r];
    if (!row[0]) { continue; }
    return {
      id: String(at(row, '工單ID', 0)), date: fmtDate_(at(row, '日期', 1)), videoId: String(at(row, '影片ID', 2)),
      status: String(at(row, '狀態', 3)), step: String(at(row, '步驟', 4)),
      done: Number(at(row, '已完成', 5)) || 0, total: Number(at(row, '總數', 6)) || 0,
      note: String(at(row, '備註', 7) || ''),
      startedAt: stampText_(at(row, '開始時間', 8)), updatedAt: stampText_(at(row, '更新時間', 9)),
      source: String(at(row, '來源', 10) || (kind === 'auto' ? '自動取稿' : '後台投稿')),
      runUrl: String(at(row, '執行網址', -1) || ''), build: String(at(row, '程式版本', -1) || ''),
      kind: kind, _ms: stampMs_(at(row, '更新時間', 9)) || stampMs_(at(row, '開始時間', 8))
    };
  }
  return null;
}

/** 進度卡要顯示哪一張：兩張表裡最近更新的那一張。 */
function displayJob_() {
  var a = latestJobOf_(ADMIN_JOB_SHEET, 'admin'), b = latestJobOf_(AUTO_JOB_SHEET, 'auto');
  var pick = !a ? b : !b ? a : (b._ms > a._ms ? b : a);
  if (!pick) { return null; }
  if (pick.kind === 'auto' && pick.status === '處理中' && pick._ms &&
      Date.now() - pick._ms > AUTO_JOB_STALE_MIN_ * 60000) {
    pick.status = '中斷';
    pick.note = '超過 ' + AUTO_JOB_STALE_MIN_ + ' 分鐘沒有回報進度，這一輪多半被 GitHub 中止；' +
                '下一次排程會從檢查點接續，不必重新投稿。' + (pick.note ? '（最後回報：' + pick.note + '）' : '');
  }
  delete pick._ms;
  return pick;
}

function currentJob_() {
  var vals;
  try { vals = getSheet_(ADMIN_JOB_SHEET).getDataRange().getValues(); }
  catch (e) { return null; }
  for (var i = vals.length - 1; i >= 1; i--) {
    if (!vals[i][0]) { continue; }
    return {
      id: vals[i][0], date: fmtDate_(vals[i][1]), videoId: vals[i][2],
      status: vals[i][3], step: vals[i][4],
      done: Number(vals[i][5]) || 0, total: Number(vals[i][6]) || 0,
      note: String(vals[i][7] || ''),
      startedAt: String(vals[i][8] || ''), updatedAt: String(vals[i][9] || ''),
      source: String(vals[i][10] || '')
    };
  }
  return null;
}

function updateJob_(patch) {
  var sh = getSheet_(ADMIN_JOB_SHEET);
  var vals = sh.getDataRange().getValues();
  for (var i = vals.length - 1; i >= 1; i--) {
    if (!vals[i][0]) { continue; }
    var row = i + 1;
    if (patch.status !== undefined) { sh.getRange(row, 4).setValue(patch.status); }
    if (patch.step !== undefined) { sh.getRange(row, 5).setValue(patch.step); }
    if (patch.done !== undefined) { sh.getRange(row, 6).setValue(patch.done); }
    if (patch.note !== undefined) { sh.getRange(row, 8).setValue(patch.note); }
    sh.getRange(row, 10).setValue(nowStamp_());
    return;
  }
}

/**
 * 排下一棒。
 *
 * 用一次性觸發器而不是迴圈，是為了讓每一步都在自己的執行時間額度裡跑，
 * 不會因為前面幾步用掉太多時間而讓後面的步驟被腰斬。
 */
function scheduleNextStep_() {
  cleanupAdminTriggers_();
  ScriptApp.newTrigger('processAdminJob_').timeBased().after(3000).create();
}

function cleanupAdminTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'processAdminJob_') { ScriptApp.deleteTrigger(t); }
  });
}

/**
 * 做一步，然後排下一棒。這支是由觸發器呼叫的，不是前端直接呼叫。
 *
 * 每次進來只做一小段工作就結束，把進度寫回工單。這是為了避開單次執行
 * 時間上限：兩萬多字的潤飾若一次做完必定超時，切成每次一段就都在額度內。
 */
/* GitHub 那條路會寫進工單的步驟名稱。
   這些步驟 Apps Script 這邊沒有對應的實作——有幾支（擷取、稽核）已經明確停用，
   一叫就拋「舊版 Apps Script 擷取流程已停用」，而那個例外會把工單標成失敗。
   實際發生過：一張正常在 GitHub 上跑的工單，因為有一個殘留的觸發器被喚醒，
   掉進 stepExtract_ 拋錯，工單就從「處理中」變成「失敗　步驟：擷取（0/15）」，
   而 GitHub 那邊其實還在跑，或只是需要重新派一次工。 */
var GITHUB_JOB_STEPS_ = ['排程中', '讀取原文', '潤飾', '擷取', '稽核補漏', '查驗幻覺',
                         '代號比對', '名稱釐清', '合併重複', '價位校對', '日期歸屬',
                         '撰稿', '寫入', '刷新網站'];

function isGithubJob_(job) {
  return !!job && GITHUB_JOB_STEPS_.indexOf(String(job.step || '')) >= 0;
}

function processAdminJob_() {
  cleanupAdminTriggers_();

  var job = currentJob_();
  if (!job || job.status !== '處理中') { return; }

  /* 投稿工單一律不在這裡跑，不論步驟名稱是什麼。

     apiAdminSubmit 是用 dispatchGithub_ 交給 GitHub Actions 的，
     Apps Script 這條分段接力的路已經退役。留著這支函式只是因為
     可能還有殘留的觸發器指著它。

     先前這裡只擋「GitHub 那套步驟名」，擋不住舊名字（稽核、代號、校價、
     重算A／B／C）。實際發生過：殘留觸發器把工單接走，跑完舊的 stepArticle_，
     把步驟寫成「重算A」——那個名字兩邊的進度條都沒有，畫面上就變成
     「重算A（0/15）」而格子退回開頭；同時 GitHub 那邊也還在跑，
     兩條路搶著寫同一張試算表。

     所以現在是全部不碰。真的卡住由看門狗重新派工給 GitHub，
     那才是這張工單唯一該走的路。 */
  Logger.log('投稿工單由 GitHub Actions 執行，Apps Script 不接手（步驟：' +
             job.step + '）。殘留的觸發器已清除。');
  if (!isGithubJob_(job)) {
    updateJob_({ note: '偵測到舊版步驟「' + job.step + '」，那是已退役的 Apps Script 流程。' +
                       '這張工單會由 GitHub Actions 接續；卡住超過十分鐘會自動重新派工。' });
  }
  return;

  /* eslint-disable no-unreachable */
  try {
    if (job.step === '潤飾') {
      stepPolish_(job);
    } else if (job.step === '擷取') {
      stepExtract_(job);
    } else if (job.step === '稽核') {
      stepAudit_(job);
    } else if (job.step === '代號') {
      stepResolveCodes_(job);
    } else if (job.step === '校價') {
      stepFixPrices_(job);
    } else if (job.step === '寫入') {
      stepWrite_(job);
    } else if (job.step === '撰稿') {
      stepArticle_(job);
    } else if (job.step === '重算A') {
      stepRefreshA_(job);
    } else if (job.step === '重算B') {
      stepRefreshB_(job);
    } else if (job.step === '重算C') {
      stepRefreshC_(job);
    } else if (job.step === '完成') {
      updateJob_({ status: '完成', step: '完成' });
      return;
    } else {
      /* 認不得的步驟名稱＝這張工單不是這條路開的。
         GitHub 那條路寫進工單的是新的步驟名（讀取原文／稽核補漏／查驗幻覺／
         代號比對／名稱釐清／刷新網站……），這裡認得的是舊的那一套。
         先前掉到 else 會被標成「完成」——一張還在 GitHub 上跑、或卡住的工單
         就這樣變成假完成，畫面全綠而資料根本沒進去。
         現在原樣留著，讓「續跑」去重新派工給 GitHub，那才是它該走的路。 */
      updateJob_({ note: '這一步（' + job.step + '）是由 GitHub Actions 執行的，' +
                         'Apps Script 這條路不接手。請按「續跑卡住的工單」重新派工。' });
      return;
    }
  } catch (e) {
    var msg = String(e && e.message || e);
    Logger.log('工單失敗：' + msg);
    updateJob_({ status: '失敗', note: msg.slice(0, 400) });

    // 信件要講清楚「是什麼錯、代表什麼、該怎麼辦」。
    // 只寫「HTTP 429」看不出是每分鐘請求數爆掉、當日額度用完、還是金鑰失效，
    // 而這三種的處理方式完全不同：等一下重跑、等到明天、換金鑰。
    var advice = '';
    if (msg.indexOf('429') >= 0) {
      advice =
        '這是 Gemini 的配額或速率上限。判讀方式：\n' +
        '  訊息含 per minute 或 rate → 每分鐘請求數爆掉，等一兩分鐘後按「續跑卡住的工單」即可。\n' +
        '  訊息含 per day 或 quota   → 當日額度用完，要等到隔天，或改用付費金鑰。\n' +
        '  訊息含 API key            → 金鑰有問題，到指令碼屬性檢查 GEMINI_API_KEY。\n' +
        '系統已把連續呼叫改成批次並加大退避，正常情況不該再頻繁出現。';
    } else if (msg.indexOf('MAX_TOKENS') >= 0) {
      advice = '輸出超過長度上限。撰稿步驟本來就會自動改用精簡指示重生一次，\n' +
               '若仍失敗代表當天內容特別多，可考慮把逐字稿分兩次投稿。';
    } else if (msg.indexOf('過期') >= 0) {
      advice = '中途的暫存結果已逾時。請回後台重新送出這份逐字稿。';
    } else if (msg.indexOf('股票對照表') >= 0) {
      advice = '請先在編輯器執行 rebuildCodeMapJob() 建立股票對照表，再重跑工單。';
    } else {
      advice = '可到後台按「續跑卡住的工單」從這一步重試。若重複失敗，\n' +
               '請看 Apps Script 的執行紀錄取得完整堆疊。';
    }

    notifyAdmin_('後台工單失敗　' + job.date + '　卡在「' + job.step + '」',
      '工單編號：' + job.id + '\n' +
      '影片：' + job.videoId + '\n' +
      '日期：' + job.date + '\n' +
      '失敗步驟：' + job.step + '\n' +
      '進度：' + job.done + '/' + job.total + '\n' +
      '開始時間：' + job.startedAt + '\n\n' +
      '===== 錯誤原文 =====\n' + msg + '\n\n' +
      '===== 這代表什麼 =====\n' + advice + '\n\n' +
      '前一步的備註：' + (job.note || '（無）'));
    return;
  }

  // 排下一棒必須包起來。
  //
  // 這行原本在 try 外面，於是它自己失敗時沒有任何人接住：
  // Apps Script 每個帳號的觸發器有數量上限，投稿、整理、刷新三個作業
  // 各自會建觸發器，累積到上限時 create() 會拋錯，
  // 而工單狀態仍是「處理中」——畫面就永遠停在那一步，也沒有錯誤訊息。
  // 實際踩到的症狀就是卡在重算C（0/3）不動。
  var next = currentJob_();
  if (next && next.status === '處理中') {
    try {
      scheduleNextStep_();
    } catch (e2) {
      var m2 = String(e2 && e2.message || e2);
      Logger.log('排下一棒失敗：' + m2);
      updateJob_({ status: '失敗',
                   note: '無法排下一個步驟：' + m2.slice(0, 200) +
                         '（多半是觸發器數量已達上限，執行 cleanupAllTriggers() 後按續跑）' });
      notifyAdmin_('後台工單無法繼續　' + next.date,
        '工單 ' + next.id + ' 在「' + next.step + '」之後無法排下一個步驟。\n\n' +
        '錯誤：' + m2 + '\n\n' + troubleshoot_(m2));
    }
  }
}

/**
 * 清掉所有一次性觸發器。
 *
 * Apps Script 對每個帳號的觸發器數量有上限，而三個背景作業都會建立
 * 一次性觸發器。正常情況它們用完會自己刪掉，但只要有一次執行被中斷，
 * 就會留下孤兒觸發器，累積到上限之後所有作業都排不出下一棒。
 * 卡住而且沒有錯誤訊息時，先執行這一支再按續跑。
 */
function cleanupAllTriggers_() {
  var names = ['processAdminJob_', 'runPastCleanup_', 'runSiteRefresh_',
               'runFullFix_', 'runQualityGate_', 'pushAfterGate_'];
  var n = 0;
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (names.indexOf(t.getHandlerFunction()) >= 0) {
      ScriptApp.deleteTrigger(t); n++;
    }
  });
  Logger.log('已清除 ' + n + ' 個背景觸發器');
  return n;
}

function cleanupAllTriggers() { return cleanupAllTriggers_(); }

/**
 * 看門狗。每五分鐘檢查一次，把「狀態是處理中、但很久沒有更新」的作業重新排程。
 *
 * 為什麼需要：單次執行超時不是拋例外而是整個被中斷，
 * 進度沒寫回、下一棒也沒排，作業就停在原地。前面已經把每一步都改成
 * 自己看時間來降低機率，但外部服務偶爾異常慢時仍可能發生。
 * 有了看門狗，這種情況會自動接回去，不必人工介入。
 */
function watchdogJob() {
  var STALE_MIN = 12;
  var now = new Date().getTime();

  function stale_(ts) {
    if (!ts) { return false; }
    var t = new Date(String(ts).replace(/-/g, '/')).getTime();
    return isFinite(t) && (now - t) > STALE_MIN * 60 * 1000;
  }

  var job = currentJob_();
  if (job && job.status === '處理中' && stale_(job.updatedAt)) {
    Logger.log('看門狗：工單 ' + job.id + ' 停在「' + job.step + '」超過 ' +
               STALE_MIN + ' 分鐘，重新排程');
    try { adminJobWatchdogJob(); }
    catch (e) { Logger.log('看門狗重新派工失敗：' + e); }
    return;
  }

  var st = cleanState_();
  if (st && st.status === '處理中' && stale_(st.updatedAt)) {
    Logger.log('看門狗：整理過去資料停在「' + st.phase + '」，重新排程');
    try { scheduleCleanup_(); } catch (e) { Logger.log(e); }
    return;
  }

  var rs = refreshState_();
  if (rs && rs.status === '處理中' && stale_(rs.updatedAt)) {
    Logger.log('看門狗：立即刷新停在「' + rs.step + '」，重新排程');
    try { scheduleRefresh_(); } catch (e) { Logger.log(e); }
    return;
  }

  var ff = fullFixState_();
  if (ff && ff.status === '處理中' && stale_(ff.updatedAt)) {
    /* 由 GitHub 驅動的那一種，看門狗不能接手。

       接手的後果不是「幫忙救回來」，而是把整件事搬到 Apps Script 上跑：
       它會建一個觸發器，之後每一棒都在 Apps Script 執行，於是 Actions 日誌
       裡什麼都看不到，觸發器也被佔著。實際發生過——GitHub 那邊因為部署沒更新
       而失敗，看門狗看到狀態沒動就默默接手，看起來像「怎麼跑到 script 去了」。

       GitHub 那邊斷掉的正確處理是人按一次「接著跑」，或重跑一次工作流程，
       兩者都會從同一個游標接續。所以這裡只記錄，不動作。 */
    if (ff.where === 'github') {
      Logger.log('看門狗：全面重整由 GitHub 驅動且已停止更新（' + ff.step +
                 '）。不接手，請到後台按「接著跑」或重跑工作流程。');
      return;
    }
    Logger.log('看門狗：全面重整停在「' + ff.step + '」，重新排程');
    try { scheduleFullFix_(); } catch (e) { Logger.log(e); }
    return;
  }

  // 品質關卡卡住的代價特別大：它擋在推播前面，沒跑完當天就不會寄信。
  // 有看門狗接回去，最多晚幾分鐘，不會整天沒有信。
  var gt = (typeof gateState_ === 'function') ? gateState_() : null;
  if (gt && gt.status === '處理中' && stale_(gt.updatedAt)) {
    Logger.log('看門狗：品質關卡停在「' + gt.phase + '」，重新排程');
    try { ScriptApp.newTrigger('runQualityGate_').timeBased().after(3000).create(); }
    catch (e) { Logger.log(e); }
  }
}


/* ---------------- 步驟一：潤飾 ---------------- */

/** 依字元數切段，盡量切在段落或句子邊界，避免把一句話從中間剖開。 */
function splitForPolish_(text) {
  var out = [], s = String(text || '');
  while (s.length > ADMIN_CHUNK_SIZE) {
    var cut = s.lastIndexOf('\n', ADMIN_CHUNK_SIZE);
    if (cut < ADMIN_CHUNK_SIZE * 0.5) { cut = s.lastIndexOf('。', ADMIN_CHUNK_SIZE); }
    if (cut < ADMIN_CHUNK_SIZE * 0.5) { cut = ADMIN_CHUNK_SIZE; }
    out.push(s.slice(0, cut));
    s = s.slice(cut);
  }
  if (s.trim()) { out.push(s); }
  return out;
}

function stepPolish_(job) {
  var row = findVideoRow_(job.videoId);
  if (!row) { throw new Error('找不到影片清單裡的這一列，原文可能沒存進去。'); }

  var raw = String(row.data['原始逐字稿內容'] || '');
  var chunks = splitForPolish_(raw);
  var idx = job.done;                       // 這次要做第幾段

  if (idx >= chunks.length) {
    updateJob_({ step: '擷取', done: 0, total: 1 });
    return;
  }

  var polished = callGemini_(PIPE_POLISH_SYSTEM, chunks[idx], { maxOut: 8192, temperature: 0.2 });

  // 逐段累加寫回。中途失敗時已完成的段落不會白做。
  var prev = String(row.data['修飾後逐字稿內容'] || '');
  var merged = idx === 0 ? polished : (prev + '\n' + polished);
  setVideoCell_(row.row, '修飾後逐字稿內容', merged.slice(0, 49000));
  // 逐字稿分頁的日期清單有五分鐘快取。寫入後主動清掉，
  // 這樣管理者投稿完立刻切過去就看得到，不必等快取自然過期。
  CACHE.remove('txDates');
  CACHE.remove('txfmt_' + job.date);

  var ratio = Math.round(polished.length / chunks[idx].length * 100);
  Logger.log('潤飾 ' + (idx + 1) + '/' + chunks.length + '：' +
             chunks[idx].length + ' → ' + polished.length + ' 字（' + ratio + '%）');

  updateJob_({ done: idx + 1, total: chunks.length,
               note: '潤飾 ' + (idx + 1) + '/' + chunks.length + '，本段 ' + ratio + '%' });

  if (idx + 1 >= chunks.length) { updateJob_({ step: '擷取', done: 0, total: 1 }); }
}


/* ---------------- 步驟二：擷取 ---------------- */

function stepExtract_(job) {
  // Current submissions are dispatched to GitHub. Do not let an old trigger
  // silently use a legacy writer that cannot preserve the evidence/date schema.
  throw new Error('舊版 Apps Script 擷取流程已停用，請由後台重新送出並在 GitHub 執行 evidence-v1 工單');
  /* legacy
  var row = findVideoRow_(job.videoId);
  var v2 = String(row.data['修飾後逐字稿內容'] || '');
  if (v2.length < 300) { throw new Error('修飾後逐字稿太短，潤飾可能沒成功。'); }

  // 逐字稿可能超過單次輸入上限，取前段與後段。盤中直播的個股討論
  // 前後都有，只取前段會漏掉收盤前的結論。
  var input = v2.length > 60000 ? (v2.slice(0, 40000) + '\n……\n' + v2.slice(-20000)) : v2;

  var raw = callGemini_(PIPE_EXTRACT_SYSTEM, input,
                        { maxOut: 8192, temperature: 0.1, json: true });
  var data;
  try {
    data = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim());
  } catch (e) {
    throw new Error('擷取結果不是合法 JSON：' + String(raw).slice(0, 120));
  }

  CacheService.getScriptCache().put('adminExtract', JSON.stringify(data), 1800);
  var n = (data.buy || []).length + (data.sell || []).length +
          (data.watch_avoid || []).length + (data.watch_watch || []).length;
  updateJob_({ step: '稽核', done: 0, total: 1,
               note: '擷取到 ' + n + ' 筆操作、' + (data.holdings || []).length + ' 筆持股' }); */
}


/* ---------------- 步驟三：完整性稽核 ---------------- */

/**
 * 拿修飾後逐字稿回頭比對擷取結果，補回漏掉的個股。
 *
 * 為什麼需要這一道：擷取是一次性的推論，長逐字稿裡總會漏掉幾檔，
 * 尤其是講得比較快或穿插在其他話題中間的。上游一直有這一步
 * （執行紀錄裡的「完整性稽核補回 N 檔」就是它），後台第一版漏掉了。
 */
function stepAudit_(job) {
  var cached = CacheService.getScriptCache().get('adminExtract');
  if (!cached) { throw new Error('擷取結果過期了，請重新送出。'); }
  var data = JSON.parse(cached);

  var row = findVideoRow_(job.videoId);
  var v2 = String(row.data['修飾後逐字稿內容'] || '');
  var input = v2.length > 50000 ? (v2.slice(0, 35000) + '\n……\n' + v2.slice(-15000)) : v2;

  var raw;
  try {
    raw = callGemini_(PIPE_AUDIT_SYSTEM,
      '已擷取的結構化紀錄：\n' + JSON.stringify(data) + '\n\n修飾後逐字稿：\n' + input,
      { maxOut: 4096, temperature: 0.1, json: true });
  } catch (e) {
    // 稽核是補強不是必要，失敗就帶著現有結果往下走，不要讓整張工單掛掉。
    updateJob_({ step: '代號', done: 0, total: 1, note: '稽核略過：' + String(e).slice(0, 60) });
    return;
  }

  var add = { missing: [] };
  try { add = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim()); }
  catch (e) { /* 格式錯就當作沒有漏抓 */ }

  var have = {};
  ['buy', 'sell', 'watch_avoid', 'watch_watch'].forEach(function (k) {
    (data[k] || []).forEach(function (x) { have[String(x.name || '').trim()] = 1; });
  });
  (data.holdings || []).forEach(function (x) { have[String(x.name || '').trim()] = 1; });

  var bucket = { '買入': 'buy', '賣出': 'sell', '觀望不碰': 'watch_avoid', '觀望注意': 'watch_watch' };
  var added = 0;
  (add.missing || []).forEach(function (m) {
    var nm = String(m.name || '').trim();
    if (!nm || have[nm]) { return; }
    var dir = String(m.direction || '').trim();
    if (dir === '會員持股') {
      data.holdings = data.holdings || [];
      data.holdings.push({ name: nm, stance: '持有', reason: m.reason || '會員持股' });
    } else {
      var k = bucket[dir];
      if (!k) { return; }
      data[k] = data[k] || [];
      data[k].push({ name: nm, price: m.price || '未說明', reason: m.reason || '未說明' });
    }
    have[nm] = 1; added++;
  });

  CacheService.getScriptCache().put('adminExtract', JSON.stringify(data), 1800);
  updateJob_({ step: '代號', done: 0, total: 1,
               note: added ? ('稽核補回 ' + added + ' 檔漏抓的個股') : '稽核未發現漏抓' });
}


/* ---------------- 步驟四：代號比對 ---------------- */

/**
 * 為每一檔配上代號，並剔除非個股與外國股票。
 *
 * 這一步必須在寫入之前做完，不能等寫進去之後靠 repairCodesJob 補。
 * 原因是 repairCodesJob 只做字面比對，它自己的執行紀錄就寫著
 * 「GAS 沒有拼音比對，請在 GitHub 重跑」——同音錯字（加哲、利望、四星）
 * 它一律解不出來，只會留成「代號待確認」顯示在網站上。
 */
/* ------------------------------------------------------------------ *
 * 幻覺檢查：名稱必須真的在逐字稿裡
 *
 * 這是唯一一道不靠提示語、不靠模型自律的防線。
 *
 * 提示語已經寫了「不指名就不要生」，但那是「請它不要」，不是保證。
 * 實際發生過：他講「這個禮拜的第三個動作……我請會員買 250 的，賣 275 左右，
 * 然後 250 幾 26 買回來」——整段沒有講任何股票名稱，模型卻生出了「位速 3508」。
 * 位速這兩個字在整份逐字稿裡出現零次。那一列每一欄都有值，股價與 K 線也
 * 畫得出來，看起來完全正常，只是整列都是憑空產生的。
 *
 * 「這個字串有沒有出現在這兩萬字裡」是事實不是判斷。沒出現就丟掉。
 * 名稱與代號有一個對得上就放行——他有時候只講代號、有時候只講名字。
 * ------------------------------------------------------------------ */

function inTranscript_(needle, hay) {
  var n = String(needle || "").replace(/\s/g, "");
  if (n.length < 2) { return false; }
  return hay.indexOf(n) >= 0;
}

/** 名稱與代號都查無此字的整筆丟掉。回傳被丟掉的筆數。 */
function verifyNames_(data, v2) {
  var hay = String(v2 || "").replace(/\s/g, "");
  if (hay.length < 500) {
    // 沒有可比對的逐字稿時不驗。全部丟掉會讓整天沒有資料，比放行糟得多。
    Logger.log("  幻覺檢查略過（沒有可比對的逐字稿）");
    return 0;
  }
  var dropped = 0;
  ["buy", "sell", "watch_avoid", "watch_watch", "holdings"].forEach(function (k) {
    data[k] = (data[k] || []).filter(function (x) {
      var nm = String(x.name || "").trim();
      var cd = String(x.code || "").trim();
      if (inTranscript_(nm, hay) || inTranscript_(cd, hay)) { return true; }
      dropped++;
      Logger.log("  幻覺剔除　" + nm + "（" + (cd || "無代號") +
                 "）：這個名稱與代號在逐字稿裡都沒有出現");
      return false;
    });
  });
  return dropped;
}

function stepResolveCodes_(job) {
  var cached = CacheService.getScriptCache().get('adminExtract');
  if (!cached) { throw new Error('擷取結果過期了，請重新送出。'); }
  var data = JSON.parse(cached);

  /* 幻覺檢查要排在代號比對之前。
     比對會把名稱換成官方簡稱（加哲→嘉澤），換過之後就對不到逐字稿了。 */
  var v2 = '';
  try {
    readSheetObjects_('影片清單').forEach(function (r) {
      if (String(r['影片ID']) !== job.videoId) { return; }
      var t = String(r['修飾後逐字稿內容'] || r['原始逐字稿內容'] || '');
      if (t.length > v2.length) { v2 = t; }
    });
  } catch (e) { /* 讀不到就等於不驗 */ }
  var ghosts = verifyNames_(data, v2);

  var map = loadCodeMap_();
  if (!Object.keys(map.byCode).length) {
    throw new Error('股票對照表是空的，請先執行 rebuildCodeMapJob()。');
  }

  var stat = { hit: 0, homophone: 0, rejected: 0, pending: 0 };
  var log = [];
  var pendingItems = [];      // 規則判不出來的，集中起來一次送同音判定
  var pendingMap = {};        // 名稱 -> 該名稱的 pending 資訊

  // ---- 第一輪：純規則 ----
  // 命中、剔除都在這裡定案，只有真的判不出來的才進入下一輪。
  function pass1_(list) {
    if (!list) { return []; }
    var keep = [];
    list.forEach(function (x) {
      var nm = String(x.name || '').trim();
      if (!nm) { return; }
      var r = pipeResolveName_(nm, map);

      if (r.reject) {
        stat.rejected++;
        log.push('剔除　' + nm + '（' + r.how + '）');
        return;
      }
      if (r.pending) {
        if (!pendingMap[nm]) {
          pendingMap[nm] = r;
          pendingItems.push(r);
        }
        x.__pending = nm;
        keep.push(x);
        return;
      }
      x.name = r.name; x.code = r.code;
      stat.hit++;
      keep.push(x);
    });
    return keep;
  }

  ['buy', 'sell', 'watch_avoid', 'watch_watch', 'holdings'].forEach(function (k) {
    data[k] = pass1_(data[k]);
  });

  // ---- 第二輪：一次批次同音判定 ----
  // 重點是「一次」。先前每檔各發一次請求，二三十檔就是二三十次連續呼叫，
  // 必然撞上每分鐘配額，實際就發生過整批 429 讓工單失敗。
  var resolved = {};
  if (pendingItems.length) {
    Logger.log('  規則判不出來 ' + pendingItems.length + ' 檔，送批次同音判定');
    resolved = pipeHomophoneBatch_(pendingItems, map);
  }

  ['buy', 'sell', 'watch_avoid', 'watch_watch', 'holdings'].forEach(function (k) {
    data[k] = (data[k] || []).filter(function (x) {
      if (!x.__pending) { return true; }
      var nm = x.__pending;
      delete x.__pending;
      var h = resolved[nm];
      if (h) {
        x.name = h.name; x.code = h.code;
        stat.homophone++;
        log.push('同音　' + nm + ' → ' + h.code + ' ' + h.name);
        return true;
      }
      // 判不出來就保留名稱、代號留空，不硬配也不丟掉。
      x.code = '';
      stat.pending++;
      log.push('待確認　' + nm + '（' + (pendingMap[nm] || {}).how + '）');
      return true;
    });
  });

  CacheService.getScriptCache().put('adminExtract', JSON.stringify(data), 3600);
  log.slice(0, 40).forEach(function (t) { Logger.log('  ' + t); });

  updateJob_({ step: '校價', done: 0, total: 1,
               note: '代號命中 ' + stat.hit + '、同音修正 ' + stat.homophone +
                     '、剔除 ' + stat.rejected + '、待確認 ' + stat.pending +
                     (ghosts ? '、逐字稿查無此名剔除 ' + ghosts : '') });
}


/* ---------------- 步驟五：價位校對 ---------------- */

/**
 * 修正價位說明的三類系統性錯誤：方向矛盾、非股價數字、概數當精確價。
 * 先用純規則快篩，只有可疑的列才送模型，所以大多數的列是零成本通過的。
 */
function stepFixPrices_(job) {
  var cached = CacheService.getScriptCache().get('adminExtract');
  if (!cached) { throw new Error('擷取結果過期了，請重新送出。'); }
  var data = JSON.parse(cached);

  var row = findVideoRow_(job.videoId);
  var v2 = String(row.data['修飾後逐字稿內容'] || '');
  var dirs = { buy: '買入', sell: '賣出', watch_avoid: '觀望不碰', watch_watch: '觀望注意' };

  // ---- 規則快篩 ----
  var suspects = [];
  Object.keys(dirs).forEach(function (k) {
    (data[k] || []).forEach(function (x, i) {
      var why = pipePriceSuspect_(dirs[k], x.price);
      if (why) { suspects.push({ k: k, i: i, name: x.name, dir: dirs[k], price: x.price, why: why }); }
    });
  });

  if (!suspects.length) {
    updateJob_({ step: '寫入', done: 0, total: 1, note: '價位說明全部通過規則快篩，無需校對' });
    return;
  }

  // ---- 一次批次送出 ----
  // 同樣不能逐檔發請求。一天可疑的可能有十幾筆，連發必然撞配額。
  var payload = suspects.map(function (sp, n) {
    var snip = '';
    var at = v2.indexOf(sp.name);
    if (at >= 0) { snip = v2.slice(Math.max(0, at - 300), at + 450).replace(/\n+/g, ' '); }
    return (n + 1) + '. 股票：' + sp.name + '｜方向：' + sp.dir +
           '｜目前價位說明：' + sp.price + '｜疑點：' + sp.why +
           '\n   逐字稿片段：' + (snip || '（找不到相關段落）');
  }).join('\n\n');

  var raw;
  try {
    raw = callGemini_(PIPE_PRICE_FIX_BATCH_SYSTEM, payload,
                      { maxOut: 4096, temperature: 0, json: true });
  } catch (e) {
    // 校對失敗不該讓整張工單掛掉。至少把確定不是股價的清掉，
    // 否則「241億以下」會一直留著被下游當成價位解析。
    var cleared0 = 0;
    suspects.forEach(function (sp) {
      if (pipeHasNonPriceUnit_(String(sp.price || ''))) {
        data[sp.k][sp.i].price = '未說明'; cleared0++;
      }
    });
    CacheService.getScriptCache().put('adminExtract', JSON.stringify(data), 3600);
    updateJob_({ step: '寫入', done: 0, total: 1,
                 note: '價位校對略過（' + String(e).slice(0, 80) + '），已清掉 ' + cleared0 + ' 筆非股價數字' });
    return;
  }

  var res;
  try { res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim()); }
  catch (e) { res = { results: [] }; }

  var fixed = 0, cleared = 0;
  (res.results || []).forEach(function (r) {
    var n = Number(r.index) - 1;
    if (!(n >= 0 && n < suspects.length)) { return; }
    var sp = suspects[n];
    var note = String(r.note || '').trim() || '未說明';
    if (note === sp.price) { return; }
    Logger.log('  校價　' + sp.name + ' [' + sp.dir + ']　「' + sp.price + '」→「' + note +
               '」　' + (r.reason || ''));
    data[sp.k][sp.i].price = note;
    if (note === '未說明') { cleared++; } else { fixed++; }
  });

  CacheService.getScriptCache().put('adminExtract', JSON.stringify(data), 3600);
  updateJob_({ step: '寫入', done: 0, total: 1,
               note: '價位校對：可疑 ' + suspects.length + ' 筆，改寫 ' + fixed +
                     '、清為未說明 ' + cleared });
}


/* ---------------- 步驟六：寫入 ---------------- */

function stepWrite_(job) {
  var cached = CacheService.getScriptCache().get('adminExtract');
  if (!cached) { throw new Error('擷取結果過期了，請重新送出。'); }
  var data = JSON.parse(cached);

  var d = job.date, vid = job.videoId;

  // 先把這一天這支影片的舊資料清掉，再寫新的。
  // 不這樣做的話，重跑一次就會變成兩份重複的紀錄。
  clearDayRows_('操作紀錄', d, vid);
  clearDayRows_('會員持股', d, vid);

  /* 前一個交易日。用在把「昨天的操作」改記到昨天。

     用日K快取的日期而不是「日期減一」：週一的前一天是上週五，遇到連假
     還要再往前。減一天會落在沒有開盤的日子，那一天永遠不會有其他資料，
     這一筆就會孤零零掛在一個空白的日期上。 */
  function prevTradingDay_(d) {
    var best = '';
    try {
      readSheetObjects_('日K快取').forEach(function (r) {
        var k = fmtDate_(r['日期']);
        if (k && k < d && k > best) { best = k; }
      });
    } catch (e) { /* 讀不到就回空字串 */ }
    return best;
  }
  var prevDay = null;      // 用到才算，多數天沒有昨天的操作

  var rows = [];

  /* 沒有數字的價位不是價位。

     模型偶爾會把「最近」「前幾天」「突破均線」填進 price 欄，那些字串會
     一路流到網站的價位欄，讀的人會以為系統抓到了什麼價。
     這條規則與上游 pipeline.py 的 clean_price_field 完全一致，
     兩條路進來的資料才不會一邊乾淨一邊髒。 */
  function cleanPrice_(v) {
    var t = String(v == null ? '' : v).trim();
    if (!t || t === '未說明') { return '未說明'; }
    return /\d/.test(t) ? t : '未說明';
  }

  var movedDays = {};

  function push_(list, dir) {
    (list || []).forEach(function (x) {
      var nm = String(x.name || '').trim();
      if (!nm) { return; }

      /* 這一筆算哪一天。

         他很常在今天的直播裡回頭講「昨天收盤後我做了什麼」，因為昨天的直播
         只播到一半。那些買賣發生在昨天，記在今天的話，昨天那一天在網站上
         完全看不到，今天卻多出兩筆從沒發生過的當日進出。
         只有買賣會改派；觀望與持股講的是「現在的看法」，那屬於今天。 */
      var rowDate = d;
      if ((dir === '買入' || dir === '賣出') &&
          String(x.when || 'today').toLowerCase() === 'prev') {
        if (prevDay === null) { prevDay = prevTradingDay_(d); }
        if (prevDay) {
          rowDate = prevDay;
          movedDays[prevDay] = 1;
          Logger.log('  日期歸屬　' + nm + '（' + dir + '）是昨天的操作，改記在 ' + prevDay);
        }
      }

      // 同一天同一檔的動作先後。沒填就給 1，排序時會退回原本的優先級。
      var sq = Number(x.seq) || 1;

      // 代號在「代號」步驟就已經解析好了，這裡直接帶上。
      // 留空等 repairCodesJob 補是不行的，那支只做字面比對，解不出同音錯字。
      rows.push([rowDate, nm, String(x.code || ''), dir, cleanPrice_(x.price),
                 publicNarrative_(naturalReason_(x.reason),x) || '未說明', vid, sq]);
    });
  }
  push_(data.buy, '買入');
  push_(data.sell, '賣出');
  push_(data.watch_avoid, '觀望不碰');
  push_(data.watch_watch, '觀望注意');

  // 改派到別天的那幾列，上面的 clearDayRows_ 清不到（它只清這一天）。
  // 不先清就會每重跑一次多一份重複，而且看起來像他真的又做了一次。
  Object.keys(movedDays).forEach(function (md) { clearDayRows_('操作紀錄', md, vid); });

  if (rows.length) {
    var sh = getSheet_('操作紀錄');
    sh.getRange(sh.getLastRow() + 1, 1, rows.length, 8).setValues(rows);
  }

  var hrows = (data.holdings || []).map(function (x) {
    return [d, String(x.name || '').trim(), String(x.code || ''), String(x.stance || '持有'),
            publicNarrative_(naturalReason_(x.note || x.reason),x) || '會員持股', vid];
  }).filter(function (r) { return r[1]; });
  if (hrows.length) {
    var hs = getSheet_('會員持股');
    hs.getRange(hs.getLastRow() + 1, 1, hrows.length, 6).setValues(hrows);
  }

  var vrow = findVideoRow_(vid);
  if (vrow) { setVideoCell_(vrow.row, '處理狀態', '完成'); }

  var movedN = Object.keys(movedDays).length;
  updateJob_({ step: '撰稿', done: 0, total: 1,
               note: '寫入操作紀錄 ' + rows.length + ' 筆、會員持股 ' + hrows.length + ' 筆' +
                     (movedN ? '（其中有 ' + movedN + ' 天是昨天的操作，已改記到當天）' : '') });
}


/* ---------------- 步驟四：撰稿 ---------------- */

/**
 * 產生每日整理文章並寫進「每日推播內容」。
 *
 * 這一步不能省。網站的「郵件查詢」分頁與每日推播信讀的都是這張分頁，
 * 少了它，手動投稿的那幾天在郵件查詢裡會是空的，訂閱者也收不到信——
 * 資料明明都進去了，使用者卻看不到，那是最難察覺的一種壞掉。
 *
 * 送進模型的內容必須與 pipeline.py 的 build_article 完全一致，包含那句
 * 「唯一資料來源、禁止列入清單以外的股票、禁止改動代號」的框限。
 * 那句話不是客套，它是防幻覺的主要機制：少了它，模型會自己補上
 * 逐字稿裡出現過但沒被擷取的股票，或把代號改成它記憶中的版本，
 * 於是信件內容與網站對不起來。
 */
function stepArticle_(job) {
  var d=job.date;
  if(!job._codesDone){resolveBlankCodes_(d);}
  var article='';
  var rows=getSheet_('每日推播內容').getDataRange().getValues();
  for(var i=1;i<rows.length;i++){if(fmtDate_(rows[i][0])===d){article=String(rows[i][1]||'');break;}}
  /* 沒有影片的日子不生每日整理（v54，管理者 2026/09/23）：那一天只有會員簡訊時，
     郵件查詢留空、不寄每日總覽；盤中即時通知另外照常寄。逐日編輯改那一天的簡訊列也不會生出半封日報。 */
  if(!article){
    var hasVideo=readSheetObjects_('影片清單').some(function(r){
      return fmtDate_(r['發布日期'])===d&&String(r['修飾後逐字稿內容']||r['原始逐字稿內容']||'').length>200;});
    if(!hasVideo){articleProgress_('略過','這一天沒有影片逐字稿，郵件內容留空（會員簡訊照常呈現在網站）');return;}
  }
  var signals=attachArticleEvidence_(buildSignalsFromSheet_(d),d);
  // The Python assessment already produced verified prose. Refresh changes only
  // the final sheet-based records and audited context, without using more quota.
  article=enforceArticleRecords_(article,signals,d);
  saveDailyArticle_(d,article);
  articleProgress_('寫入',article.length+' 字；標題＋固定四章，資料與郵件已同步');
}


/**
 * 寫進「每日推播內容」。同一天已經有列就覆蓋，避免重跑後出現兩份。
 *
 * 抽成共用函式，是因為「有操作的日子」與「沒有個股操作的日子」都要寫這一列。
 * 少了它，郵件查詢在那一天會是空的，而 dailyPushJob 會一直認為那天還沒處理完，
 * 於是那天的信再也不會寄，且不會有任何錯誤訊息——信箱只是安靜地沒有東西。
 */
function saveDailyArticle_(d, article) {
  var sh = getSheet_('每日推播內容');
  var vals = sh.getDataRange().getValues();
  var row = -1;
  for (var i = 1; i < vals.length; i++) {
    if (fmtDate_(vals[i][0]) === d) { row = i + 1; break; }
  }
  if (row > 0) {
    sh.getRange(row, 2).setValue(String(article).slice(0, 49000));
    // 寄送狀態保持原樣：已經寄過的不要因為重跑而變成未寄送再寄一次。
    if (!String(vals[row - 1][2] || '')) { sh.getRange(row, 3).setValue('未寄送'); }
  } else {
    sh.appendRow([d, String(article).slice(0, 49000), '未寄送']);
  }
}


/**
 * 這支影片的紀錄實際落在哪幾天（排除 exceptDate）。
 *
 * 用來分辨「資料掉了」與「資料被改記到別天」。後者是正常行為：
 * 他很常在今天的直播裡回頭講「昨天收盤後我做了什麼」，那幾筆會被
 * stepWrite_ 改記到前一個交易日；整份逐字稿都在回顧昨天時，
 * 今天這一天就真的一列都沒有，但資料完好。
 */
function videoRowDates_(vid, exceptDate) {
  var days = {};
  if (!vid) { return []; }
  ['操作紀錄', '會員持股'].forEach(function (name) {
    try {
      readSheetObjects_(name).forEach(function (r) {
        if (String(r['來源影片ID'] || '').trim() !== String(vid)) { return; }
        var k = fmtDate_(r['日期']);
        if (k && k !== exceptDate) { days[k] = 1; }
      });
    } catch (e) { /* 讀不到就當成沒有 */ }
  });
  return Object.keys(days).sort();
}


/** 這一天（或這支影片）有沒有逐字稿。有逐字稿就代表資料沒掉，只是沒有個股操作。 */
function dayHasTranscript_(d, vid) {
  try {
    var rows = readSheetObjects_('影片清單');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      // 欄位名稱以 Setup.gs 的 SHEET_SCHEMA 為準：日期欄是「發布日期」，
      // 逐字稿兩欄都帶「內容」兩個字。名稱寫錯會一律讀到空字串，
      // 於是每一天都被判成「連逐字稿都不在」，反而比舊版更常誤判。
      var sameVid = vid && String(r['影片ID'] || '').trim() === String(vid);
      var sameDay = fmtDate_(r['發布日期']) === d;
      if (!sameVid && !sameDay) { continue; }
      if (String(r['修飾後逐字稿內容'] || '').trim() ||
          String(r['原始逐字稿內容'] || '').trim()) {
        return true;
      }
    }
  } catch (e) { /* 讀不到就保守回 false，讓呼叫端照舊拋錯 */ }
  return false;
}

/**
 * 把試算表裡代號還空著的列補齊。
 *
 * 走的順序與擷取時相同：先規則比對，判不出來的集中送一次同音判定，
 * 判定為題材、族群、外國股票的整列刪掉。
 * 這一步之所以要獨立出來，是因為手動修過試算表之後也要能再跑一次。
 */
function resolveBlankCodes_(dateStr) {
  var map;
  try { map = loadCodeMap_(); } catch (e) { return { fixed: 0, dropped: 0, pending: 0 }; }
  if (!map || !Object.keys(map.byCode).length) { return { fixed: 0, dropped: 0, pending: 0 }; }

  var stat = { fixed: 0, dropped: 0, pending: 0, ghost: 0 };
  var full = codeMapFull_(map);

  ['操作紀錄', '會員持股'].forEach(function (sheetName) {
    var sh = getSheet_(sheetName);
    var vals = sh.getDataRange().getValues();
    if (vals.length < 2) { return; }
    var head = vals[0];
    var cDate = head.indexOf('日期'), cName = head.indexOf('股票名稱'), cCode = head.indexOf('代號');
    if (cName < 0 || cCode < 0) { return; }

    var pending = [], pendRows = {}, dropRows = [];

    for (var i = vals.length - 1; i >= 1; i--) {
      if (dateStr && fmtDate_(vals[i][cDate]) !== dateStr) { continue; }
      var code = String(vals[i][cCode] || '').trim();
      var nm = String(vals[i][cName] || '').trim();
      if (!nm) { continue; }

      /* 有代號不等於代號是對的。

         這裡原本是「格式是四到六位數就跳過」，於是模型幻覺出來的代號
         永遠不會被檢查。實際發生過兩筆：茂聯（2155）、和生堂（3182），
         這兩個號碼在上市、上櫃、興櫃、連 ISIN 全清單裡都查不到，
         那兩列的價格與報酬永遠是空的，補日K 每天還去敲一次收兩個 404。

         現在改成查對照表。查不到就把代號丟掉、改用名稱重判——
         那兩筆一重判就還原了：茂聯→貿聯-KY 3665、和生堂→禾伸堂 3026，
         都是拼音一模一樣的同音錯字。對照表殘缺時不動，理由見 codeMapFull_。 */
      var ghost = false;
      if (/^(?:00981A|\d{4,6})$/.test(code)) {
        if (map.byCode[code]) { continue; }            // 對照表裡有，正常
        if (!full) { continue; }                       // 對照表殘缺，不敢動
        ghost = true;
        stat.ghost++;
        Logger.log('  代號查無　' + sheetName + ' ' + nm + '（' + code +
                   '）不在上市櫃清單，改用名稱重判');
      }

      var r = pipeResolveName_(nm, map);
      if (r.reject) {
        /* 假代號那一列刻意不刪。模型敢給一個具體代號，代表它認為這是個股；
           號碼錯了不是「這不是股票」的證據。標成待確認會出現在後台，
           人可以在逐日編輯裡把名稱與代號改對；刪掉就什麼都不剩了。 */
        if (ghost) {
          sh.getRange(i + 1, cCode + 1).setValue('代號待確認');
          stat.pending++;
          Logger.log('  代號查無且名稱也對不上　' + nm + '（' + r.how + '）→ 待確認');
        } else {
          dropRows.push(i + 1); stat.dropped++;
          Logger.log('  撰稿前剔除　' + sheetName + ' ' + nm + '（' + r.how + '）');
        }
      } else if (r.pending) {
        if (!pendRows[nm]) { pendRows[nm] = []; pending.push(r); }
        pendRows[nm].push(i + 1);
      } else {
        sh.getRange(i + 1, cCode + 1).setValue(r.code);
        sh.getRange(i + 1, cName + 1).setValue(r.name);
        stat.fixed++;
      }
    }

    if (pending.length) {
      var got = pipeHomophoneBatch_(pending, map);
      Object.keys(pendRows).forEach(function (nm) {
        var h = got[nm];
        if (h) {
          pendRows[nm].forEach(function (rn) {
            sh.getRange(rn, cCode + 1).setValue(h.code);
            sh.getRange(rn, cName + 1).setValue(h.name);
          });
          stat.fixed += pendRows[nm].length;
          Logger.log('  撰稿前同音修正　' + nm + ' → ' + h.code + ' ' + h.name);
        } else {
          // 真的判不出來。留著資料但把代號標成待確認，
          // 信裡至少看得出來這一檔還沒對上，而不是一片空白。
          pendRows[nm].forEach(function (rn) {
            sh.getRange(rn, cCode + 1).setValue('代號待確認');
          });
          stat.pending += pendRows[nm].length;
        }
      });
    }

    dropRows.sort(function (a, b) { return b - a; });
    dropRows.forEach(function (rn) { sh.deleteRow(rn); });
  });

  Logger.log('  撰稿前代號補齊：修正 ' + stat.fixed + '、剔除 ' + stat.dropped +
             '、仍待確認 ' + stat.pending +
             (stat.ghost ? '（其中 ' + stat.ghost + ' 列的代號查無此股，已改用名稱重判）' : ''));
  return stat;
}

/**
 * 從試算表重建擷取結果的結構。
 * 欄位名稱刻意與 pipeline.py 的擷取輸出一致（buy / sell / watch_avoid /
 * watch_watch / holdings，每檔含 name、code、price、reason），
 * 這樣送進模型的 JSON 與上游長得一模一樣，產出的文章風格才會一致。
 */
function buildSignalsFromSheet_(dateStr) {
  var out = { buy: [], sell: [], watch_avoid: [], watch_watch: [], holdings: [] };

  readSheetObjects_('操作紀錄').forEach(function (r) {
    if (fmtDate_(r['日期']) !== dateStr) { return; }
    var dir = String(r['方向'] || '');
    var item = {
      name: String(r['股票名稱'] || '').trim(),
      code: String(r['代號'] || '').trim(),
      price: String(r['價位說明'] || '未說明'),
      reason: String(r['理由摘錄'] || '未說明')
    };
    if (!item.name) { return; }
    if (dir.indexOf('買') === 0) { out.buy.push(item); }
    else if (dir.indexOf('賣') === 0) { out.sell.push(item); }
    else if (dir.indexOf('不碰') >= 0) { out.watch_avoid.push(item); }
    else { out.watch_watch.push(item); }
  });

  readSheetObjects_('會員持股').forEach(function (r) {
    if (fmtDate_(r['日期']) !== dateStr) { return; }
    var nm = String(r['股票名稱'] || '').trim();
    if (!nm) { return; }
    out.holdings.push({
      name: nm,
      code: String(r['代號'] || '').trim(),
      stance: String(r['目前立場'] || '持有'),
      note: String(r['說明重點'] || '')
    });
  });

  return out;
}


/* ---------------- 步驟八到十：重算 ---------------- */

/*
 * 為什麼重算要拆開、而且每一小段都要自己看時間
 *
 * refreshSiteNow 一口氣做六件事：清產業列、代號比對、基本面、補日K、
 * 持股追蹤、績效。其中補日K與基本面是對外抓資料，檔數多的時候特別慢。
 * 整包跑下來遠超過單次執行的時間上限，一旦超時，執行會被直接腰斬——
 * 不是拋例外，是整個中斷。於是 updateJob_ 沒機會執行、scheduleNextStep_
 * 也沒機會排下一棒，工單就永遠停在那一步不動，而且沒有任何錯誤訊息。
 *
 * 所以每一小段開始前先記下時間，做完一件事就看一次還剩多少額度，
 * 不夠就把控制權交回去、排下一棒、下次接著做。這樣不論外部服務多慢，
 * 工單都會持續前進，不會再出現「卡在重算不動」。
 */

// 單次執行留給實際工作的時間。一般帳號上限 6 分鐘，留 90 秒給收尾與寫回工單。
var STEP_BUDGET_MS = 4.5 * 60 * 1000;

function stepDeadline_() { return Date.now() + STEP_BUDGET_MS; }
function timeLeft_(deadline) { return deadline - Date.now(); }

/**
 * 依序執行數件事，每件之前先確認還有足夠時間。
 * 時間不夠就停下來，回傳還沒做完的索引，交給下一棒接手。
 */
function runWithBudget_(job, tasks, startAt, nextStep) {
  var deadline = stepDeadline_();
  var out = [];
  var i = startAt || 0;

  for (; i < tasks.length; i++) {
    // 每件工作預估最耗時的情況，剩餘時間不足就先停。
    if (timeLeft_(deadline) < 60 * 1000) {
      updateJob_({ done: i, total: tasks.length,
                   note: out.join('；') + '（時間不足，下一棒接著做）' });
      return { done: false, next: i };
    }
    var t = tasks[i];
    try {
      var r = t.fn();
      out.push(t.name + (r ? '：' + r : ' 完成'));
      Logger.log('  ✓ ' + t.name + (r ? '：' + r : ''));
    } catch (e) {
      out.push(t.name + ' 失敗：' + String(e).slice(0, 80));
      Logger.log('  ✗ ' + t.name + '：' + e);
    }
    updateJob_({ done: i + 1, total: tasks.length, note: out.join('；') });
  }
  return { done: true, note: out.join('；') };
}

function stepRefreshA_(job) {
  var tasks = [
    { name: '清除產業列', fn: function () { return purgeIndustryRows() + ' 列'; } },
    { name: '代號比對', fn: function () { repairCodesJob(); return ''; } }
  ];
  var r = runWithBudget_(job, tasks, job.done, '重算A');
  if (!r.done) { return; }                 // 留在同一步，下一棒接著做
  updateJob_({ step: '重算B', done: 0, total: 2, note: r.note });
}

function stepRefreshB_(job) {
  var tasks = [
    { name: '更新基本面', fn: function () { rebuildFundamentalsJob(); return ''; } },
    { name: '補齊日K', fn: function () { backfillDailyKJob(); return ''; } }
  ];
  var r = runWithBudget_(job, tasks, job.done, '重算B');
  if (!r.done) { return; }
  updateJob_({ step: '重算C', done: 0, total: 3, note: r.note });
}

function stepRefreshC_(job) {
  var tasks = [
    { name: '重算持股追蹤', fn: function () { rebuildHoldingsTrackerJob(); return ''; } },
    { name: '記錄績效', fn: function () { snapshotPerformanceJob(); return ''; } },
    { name: '推播信', fn: function () {
        // 手動投稿常發生在排程推播時段之後，沒有這一步文章寫好了卻要等隔天。
        // dailyPushJob 自己會檢查「已寄送」，重跑或撞排程都不會重複寄。
        if (job.date !== todayStr_()) { return '補登日期不自動寄信'; }
        dailyPushJob(); return '已嘗試寄出';
      } }
  ];
  var r = runWithBudget_(job, tasks, job.done, '重算C');
  if (!r.done) { return; }

  updateJob_({ status: '完成', step: '完成', done: 3, total: 3, note: r.note });
  notifyAdmin_('後台投稿處理完成　' + job.date,
               job.date + ' 的逐字稿已處理完成。\n\n' + r.note +
               '\n\n工單：' + job.id + '\n影片：' + job.videoId);
}


/* ------------------------------------------------------------------ *
 * 小工具
 * ------------------------------------------------------------------ */

function extractVideoId_(url) {
  var s = String(url || '').trim();
  if (!s) { return ''; }
  var m = s.match(/[?&]v=([A-Za-z0-9_-]{11})/) ||
          s.match(/youtu\.be\/([A-Za-z0-9_-]{11})/) ||
          s.match(/\/live\/([A-Za-z0-9_-]{11})/) ||
          s.match(/\/embed\/([A-Za-z0-9_-]{11})/);
  if (m) { return m[1]; }
  if (/^[A-Za-z0-9_-]{11}$/.test(s)) { return s; }   // 直接貼 ID 也接受
  return '';
}

function nowStamp_() {
  return Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss');
}

function findVideoRow_(videoId) {
  var sh = getSheet_('影片清單');
  var vals = sh.getDataRange().getValues();
  var head = vals[0];
  for (var i = 1; i < vals.length; i++) {
    if (String(vals[i][head.indexOf('影片ID')]) === videoId) {
      var o = {};
      head.forEach(function (h, j) { o[h] = vals[i][j]; });
      return { row: i + 1, data: o, head: head };
    }
  }
  return null;
}

function setVideoCell_(row, colName, value) {
  var sh = getSheet_('影片清單');
  var head = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
  var c = head.indexOf(colName);
  if (c < 0) { return; }
  sh.getRange(row, c + 1).setValue(value);
}

/**
 * 把管理者貼上的原文寫進影片清單。
 *
 * 找列的順序是「先比影片ID、再比日期」，這個順序很重要。
 *
 * 先前只比影片ID。後台投稿在偵測不到影片時會用 MANUAL-日期 當代號，
 * 而當天真正的影片是另一組 ID，於是比對不到就新增一列——
 * 同一天因此出現兩列：一列有逐字稿、一列全空。
 * 讀取端用 forEach 逐列覆蓋，最後留下的往往是空的那一列，
 * 結果就是明明貼過逐字稿，逐字稿分頁卻說那天沒有內容。
 *
 * 改成日期也算命中之後，就會更新到既有那一列，不再產生重複。
 */
function saveRawTranscript_(videoId, dateStr, title, text) {
  var sh = getSheet_('影片清單');
  var vals = sh.getDataRange().getValues();
  var head = vals[0];
  var cId = head.indexOf('影片ID');
  var cDate = head.indexOf('發布日期');

  var records=vals.slice(1).map(function(v){var r={};head.forEach(function(h,j){r[h]=v[j];});return r;});
  var chosen=selectTranscriptRow_(records,videoId,dateStr);
  var target=chosen ? chosen.index : -1;
  var byId=chosen && String(chosen.row['影片ID']||'')===videoId ? target : -1;
  var stamp=new Date().toISOString(), hash=transcriptSha256_(text);
  if (target > 0) {
    setVideoCell_(target, '原始逐字稿內容', text);
    setVideoCell_(target, '原文更新時間', stamp);
    setVideoCell_(target, '原文SHA256', hash);
    setVideoCell_(target, '修飾後逐字稿內容', '');
    setVideoCell_(target, '處理狀態', '處理中');
    // 標成手動之後，transcript.py 的自動取稿就對這一天完全停手。
    // 不標也不會被覆蓋（那邊把「有原文但沒標來源」當成手動），
    // 但寫清楚比較看得出是誰放進去的。欄位不存在時 setVideoCell_ 自己會跳過。
    setVideoCell_(target, '逐字稿來源', '手動');
    CACHE.remove('txDates');
    CACHE.remove('txfmt_' + dateStr);
    setVideoCell_(target, '失敗原因', '');
    // 只有這一列原本就沒有影片ID時才補上，不要把真正的影片ID
    // 蓋成 MANUAL-日期，那會讓這一列跟 YouTube 上的影片對不起來。
    if (byId < 0 && cId >= 0 && !String(vals[target - 1][cId]).trim()) {
      setVideoCell_(target, '影片ID', videoId);
    }
    Logger.log('原文寫入影片清單第 ' + target + ' 列（' +
               (byId > 0 ? '比對影片ID' : '比對日期') + '）');
    return;
  }

  // 依實際表頭組出這一列，不寫死欄數。
  //
  // 寫死的話有兩種壞法：表頭少一欄（舊試算表還沒長出「逐字稿來源」）時
  // appendRow 會因為值比欄多而整個丟例外，投稿當場失敗；表頭多一欄時
  // 又會把值填到錯的位置。用表頭名稱對應就兩種都不會發生。
  //
  // 「逐字稿來源」填手動，自動取稿看到就對這一天完全停手。
  var newRow = {
    '影片ID': videoId, '發布日期': dateStr, '標題': title, '處理狀態': '處理中',
    '失敗原因': '', '原始逐字稿內容': text, '修飾後逐字稿內容': '',
    '原文更新時間': stamp, '原文SHA256': hash, '來源別名': videoId,
    '逐字稿來源': '手動'
  };
  sh.appendRow(head.map(function (h) {
    return Object.prototype.hasOwnProperty.call(newRow, h) ? newRow[h] : '';
  }));
  CACHE.remove('txDates');
  Logger.log('影片清單新增一列：' + dateStr + '　' + videoId);
}

/** 刪掉某一天、某支影片的既有列。重跑時避免produce重複資料。 */
function clearDayRows_(sheetName, dateStr, videoId) {
  var sh = getSheet_(sheetName);
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return; }
  var head = vals[0];
  var cDate = head.indexOf('日期'), cVid = head.indexOf('來源影片ID');
  for (var i = vals.length - 1; i >= 1; i--) {
    var d = fmtDate_(vals[i][cDate]);
    var v = cVid >= 0 ? String(vals[i][cVid]) : '';
    if (d === dateStr && (!videoId || !v || v === videoId)) {
      sh.deleteRow(i + 1);
    }
  }
}

function notifyAdmin_(subject, body) {
  try {
    var to = (typeof statusEmails_ === 'function') ? statusEmails_() : [];
    if (to && to.length) { MailApp.sendEmail({ to: to.join(','), subject: subject, body: body }); }
  } catch (e) { /* 通知失敗不影響流程 */ }
}


/* ==================================================================== *
 * 整理過去資料
 *
 * 新的規則（題材詞剔除、批次同音判定、價位校對、持有回合）只會套用在
 * 之後新進的資料上。先前已經寫進試算表的那些，不會自己變好。
 * 這一段就是拿新規則回頭把舊資料掃一遍。
 *
 * 做三件事：
 *   1. 名稱與代號　剔除題材、族群、外國股票、單字；同音錯字補上正確代號。
 *   2. 價位說明　　修正方向矛盾、非股價數字、概數。
 *   3. 重算持股追蹤　讓回合、累積報酬、進場價全部依新規則重算。
 *
 * 為什麼要能續跑：資料量可能好幾百列，加上同音判定與價位校對各要呼叫模型，
 * 一次跑不完是常態。所以每一輪只做一段，把進度記在指令碼屬性裡，
 * 排下一棒接著做。中途失敗也不會前功盡棄。
 * ==================================================================== */

var CLEAN_KEY = 'pastCleanupState';

function cleanState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(CLEAN_KEY);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (e) { return null; }
}

function setCleanState_(st) {
  PropertiesService.getScriptProperties()
    .setProperty(CLEAN_KEY, JSON.stringify(st || {}));
}

/**
 * 乾跑：只列出會改動什麼，不寫任何資料。
 * 建議先跑這支確認名單，再決定要不要真的執行。
 */
function previewPastCleanup() {
  var map = loadCodeMap_();
  if (!Object.keys(map.byCode).length) {
    Logger.log('股票對照表是空的，請先執行 rebuildCodeMapJob()');
    return;
  }

  var willDrop = [], willFix = [], stillPending = [], priceIssues = [];

  [['操作紀錄', '方向', '價位說明'], ['會員持股', '', '']].forEach(function (cfg) {
    readSheetObjects_(cfg[0]).forEach(function (r) {
      var nm = String(r['股票名稱'] || '').trim();
      if (!nm) { return; }
      var res = pipeResolveName_(nm, map);
      if (res.reject) {
        willDrop.push(cfg[0] + '　' + fmtDate_(r['日期']) + '　' + nm + '（' + res.how + '）');
      } else if (res.pending) {
        stillPending.push(cfg[0] + '　' + nm + '（' + res.how + '）');
      } else if (String(r['代號'] || '').trim() !== res.code) {
        willFix.push(cfg[0] + '　' + nm + ' → ' + res.code + ' ' + res.name);
      }
      if (cfg[1]) {
        var why = pipePriceSuspect_(String(r[cfg[1]] || ''), String(r[cfg[2]] || ''));
        if (why) {
          priceIssues.push(fmtDate_(r['日期']) + '　' + nm + '　「' +
                           String(r[cfg[2]]).slice(0, 24) + '」　' + why);
        }
      }
    });
  });

  Logger.log('===== 整理過去資料　乾跑（未寫入任何資料）=====');
  Logger.log('');
  Logger.log('【會刪除】判定不是個股　' + willDrop.length + ' 列');
  willDrop.slice(0, 40).forEach(function (t) { Logger.log('    ' + t); });
  if (willDrop.length > 40) { Logger.log('    ……另有 ' + (willDrop.length - 40) + ' 列'); }
  Logger.log('');
  Logger.log('【會補正代號】　' + willFix.length + ' 列');
  willFix.slice(0, 30).forEach(function (t) { Logger.log('    ' + t); });
  Logger.log('');
  Logger.log('【待同音判定】規則判不出來，執行時會送模型　' + stillPending.length + ' 個名稱');
  stillPending.slice(0, 30).forEach(function (t) { Logger.log('    ' + t); });
  Logger.log('');
  Logger.log('【價位說明有疑點】　' + priceIssues.length + ' 列');
  priceIssues.slice(0, 30).forEach(function (t) { Logger.log('    ' + t); });
  Logger.log('');
  Logger.log('確認名單沒問題後，到後台按「整理過去資料」，或執行 startPastCleanup()。');
  return { drop: willDrop.length, fix: willFix.length,
           pending: stillPending.length, price: priceIssues.length };
}

/** 開始整理。會排一次性觸發器逐段進行。 */
function startPastCleanup() {
  setCleanState_({ phase: '名稱', sheet: 0, row: 1, status: '處理中',
                   note: '準備開始', startedAt: nowStamp_(), updatedAt: nowStamp_(),
                   dropped: 0, fixed: 0, homophone: 0, priceFixed: 0 });
  scheduleCleanup_();
  return '已開始整理過去資料，可在後台看進度。';
}

function scheduleCleanup_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runPastCleanup_') { ScriptApp.deleteTrigger(t); }
  });
  ScriptApp.newTrigger('runPastCleanup_').timeBased().after(3000).create();
}

/**
 * 執行一段。這支由觸發器呼叫。
 * 每段都自己看時間，額度不夠就把進度存起來、排下一棒，避免超時被腰斬。
 */
function runPastCleanup_() {
  var st = cleanState_();
  if (!st || st.status !== '處理中') { return; }
  var deadline = stepDeadline_();

  try {
    if (st.phase === '名稱') {
      cleanNamesPhase_(st, deadline);
    } else if (st.phase === '價位') {
      cleanPricePhase_(st, deadline);
    } else if (st.phase === '重算') {
      rebuildHoldingsTrackerJob();
      st.phase = '完成'; st.status = '完成';
      st.note = '整理完成：刪除 ' + st.dropped + ' 列、補正代號 ' + st.fixed +
                '、同音修正 ' + st.homophone + '、價位修正 ' + st.priceFixed;
      st.updatedAt = nowStamp_();
      setCleanState_(st);
      notifyAdmin_('過去資料整理完成', st.note);
      return;
    }
  } catch (e) {
    st.status = '失敗';
    st.note = String(e && e.message || e).slice(0, 300);
    st.updatedAt = nowStamp_();
    setCleanState_(st);
    notifyAdmin_('過去資料整理失敗', '階段：' + st.phase + '\n原因：' + st.note);
    return;
  }

  st.updatedAt = nowStamp_();
  setCleanState_(st);
  if (st.status === '處理中') { scheduleCleanup_(); }
}

/** 名稱與代號。逐列判定，剔除的整列刪掉，判不出來的集中一次送同音判定。 */
function cleanNamesPhase_(st, deadline) {
  var SHEETS = ['操作紀錄', '會員持股'];
  var map = loadCodeMap_();
  if (!Object.keys(map.byCode).length) { throw new Error('股票對照表是空的'); }

  var name = SHEETS[st.sheet];
  var sh = getSheet_(name);
  var vals = sh.getDataRange().getValues();
  var head = vals[0];
  var cName = head.indexOf('股票名稱'), cCode = head.indexOf('代號');

  var pending = [], pendingRows = {};
  var dropRows = [];

  // 由後往前掃，這樣刪列時前面的列號不會位移。
  var i = vals.length - 1;
  if (st.row > 1 && st.row < vals.length) { i = st.row; }

  for (; i >= 1; i--) {
    if (timeLeft_(deadline) < 45000) {
      st.row = i; st.note = name + ' 掃到第 ' + i + ' 列';
      return;                       // 留在同一階段，下一棒接著做
    }
    var nm = String(vals[i][cName] || '').trim();
    if (!nm) { continue; }
    var res = pipeResolveName_(nm, map);

    if (res.reject) {
      dropRows.push(i + 1);
      st.dropped++;
      Logger.log('  刪除　' + name + ' 第 ' + (i + 1) + ' 列　' + nm + '（' + res.how + '）');
    } else if (res.pending) {
      if (!pendingRows[nm]) { pendingRows[nm] = []; pending.push(res); }
      pendingRows[nm].push(i + 1);
    } else if (String(vals[i][cCode] || '').trim() !== res.code) {
      sh.getRange(i + 1, cCode + 1).setValue(res.code);
      sh.getRange(i + 1, cName + 1).setValue(res.name);
      st.fixed++;
    }
  }

  // 一次批次同音判定，不要逐檔發請求（那會撞每分鐘配額）
  if (pending.length) {
    var resolved = pipeHomophoneBatch_(pending, map);
    Object.keys(resolved).forEach(function (k) {
      var h = resolved[k];
      (pendingRows[k] || []).forEach(function (rn) {
        sh.getRange(rn, cCode + 1).setValue(h.code);
        sh.getRange(rn, cName + 1).setValue(h.name);
      });
      st.homophone += (pendingRows[k] || []).length;
      Logger.log('  同音　' + k + ' → ' + h.code + ' ' + h.name +
                 '（' + (pendingRows[k] || []).length + ' 列）');
    });
  }

  // 刪列放最後，避免中途改變列號
  dropRows.sort(function (a, b) { return b - a; });
  dropRows.forEach(function (rn) { sh.deleteRow(rn); });

  st.sheet++;
  st.row = 1;
  if (st.sheet >= SHEETS.length) { st.phase = '價位'; st.sheet = 0; }
  st.note = name + ' 完成';
}

/** 價位說明校對。只處理操作紀錄，且一次批次送出。 */
/**
 * 價位校對。since 有值時只看那一天（含）之後的列，供全面重整的範圍限定用；
 * 留空就是全部歷史，與加這個參數之前的行為完全相同。
 */
function cleanPricePhase_(st, deadline, since) {
  var sh = getSheet_('操作紀錄');
  var vals = sh.getDataRange().getValues();
  var head = vals[0];
  var cDir = head.indexOf('方向'), cPrice = head.indexOf('價位說明');
  var cName = head.indexOf('股票名稱'), cDate = head.indexOf('日期');

  var suspects = [];
  for (var i = 1; i < vals.length; i++) {
    // 範圍限定時，早於起始日的列不看。過濾放在規則快篩之前，
    // 這樣連快篩都不用跑，省下來的是一整輪的字串比對。
    var rowDate = fmtDate_(vals[i][cDate]);
    if (since && rowDate && rowDate < since) { continue; }
    var why = pipePriceSuspect_(String(vals[i][cDir] || ''), String(vals[i][cPrice] || ''));
    if (why) {
      suspects.push({ row: i + 1, name: String(vals[i][cName] || ''),
                      dir: String(vals[i][cDir] || ''), price: String(vals[i][cPrice] || ''),
                      why: why, date: rowDate });
    }
  }

  if (!suspects.length) {
    st.phase = '重算'; st.note = '價位說明沒有需要修正的';
    return;
  }

  /* 開工之前先看時間夠不夠。

     這一段原本收了 deadline 卻整支沒用過。後果是：不管進來時只剩多少額度，
     它都照樣送一次模型呼叫（最壞情況含退避要兩分半）再寫二十幾格試算表。
     時間不夠時就會被 Apps Script 的 6 分鐘上限砍掉——那不是拋例外而是整個
     執行被中止，所以呼叫端只看得到連線讀取逾時，完全看不出真正的原因。

     現在時間不夠就直接交棒，phase 留在原地，下一棒接著做。
     可疑的列是每次重新掃出來的，所以中途停掉不會漏掉任何一列。 */
  if (deadline && timeLeft_(deadline) < 170000) {
    st.note = '本棒剩餘時間不足以再跑一批價位校對（尚有 ' + suspects.length + ' 筆），交棒';
    return;   // phase 不動，維持在「價位」
  }

  // 一次最多處理 25 筆，剩下的下一棒繼續。批次是為了不撞每分鐘配額，
  // 分批則是為了不讓單一請求太大而被截斷。
  var batch = suspects.slice(0, 25);
  var payload = batch.map(function (sp, n) {
    return (n + 1) + '. 股票：' + sp.name + '｜方向：' + sp.dir +
           '｜目前價位說明：' + sp.price + '｜疑點：' + sp.why +
           '\n   逐字稿片段：（無，請僅依方向與常識判斷，不確定就回未說明）';
  }).join('\n\n');

  var raw;
  try {
    raw = callGemini_(PIPE_PRICE_FIX_BATCH_SYSTEM, payload,
                      { maxOut: 4096, temperature: 0, json: true });
  } catch (e) {
    // 呼叫失敗時，至少把確定不是股價的清掉
    var n0 = 0;
    batch.forEach(function (sp) {
      if (pipeHasNonPriceUnit_(sp.price)) {
        sh.getRange(sp.row, cPrice + 1).setValue('未說明'); n0++;
      }
    });
    st.priceFixed += n0;
    st.note = '價位校對呼叫失敗（' + String(e).slice(0, 60) + '），已清掉 ' + n0 + ' 筆非股價數字';
    st.phase = '重算';
    return;
  }

  var res;
  try { res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim()); }
  catch (e) { res = { results: [] }; }

  (res.results || []).forEach(function (r) {
    var n = Number(r.index) - 1;
    if (!(n >= 0 && n < batch.length)) { return; }
    var sp = batch[n];
    var note = String(r.note || '').trim() || '未說明';
    if (note === sp.price) { return; }
    sh.getRange(sp.row, cPrice + 1).setValue(note);
    st.priceFixed++;
    Logger.log('  校價　' + sp.date + ' ' + sp.name + '　「' + sp.price + '」→「' + note + '」');
  });

  // 還有剩就留在這一階段，下一棒繼續；沒有了就進重算。
  st.note = '價位校對已處理 ' + batch.length + ' 筆，累計 ' + st.priceFixed;
  if (suspects.length <= 25) { st.phase = '重算'; }
}


/* ---------------- 前端 API ---------------- */

function apiAdminPreviewCleanup(key) {
  try {
    adminAuth_(key);
    return { ok: true, data: previewPastCleanup() };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminStartCleanup(key) {
  try {
    adminAuth_(key);
    var cur = cleanState_();
    if (cur && cur.status === '處理中') {
      return { ok: false, reason: '整理已經在進行中（' + cur.phase + '）。' };
    }
    startPastCleanup();
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminCleanupState(key) {
  try {
    adminAuth_(key);
    var st = cleanState_();
    if (st && st.status === '失敗') { st.advice = troubleshoot_(st.note); }
    return { ok: true, state: st };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}


/* ==================================================================== *
 * 錯誤說明
 *
 * 錯誤訊息只講「發生什麼」是不夠的，人在現場需要知道的是「該做什麼」。
 * 這支把常見的失敗形態翻成具體的下一步，後台與通知信共用同一份說法，
 * 免得兩邊講得不一樣。
 * ==================================================================== */
function troubleshoot_(msg) {
  var t = String(msg || '');

  // 舊版 Apps Script 步驟被叫到。這代表有一個殘留的觸發器把 GitHub 的工單
  // 拉進了已經停用的實作，不是逐字稿或資料有問題。
  if (t.indexOf('舊版 Apps Script') >= 0) {
    return '這是殘留的觸發器造成的，不是資料有問題。\n' +
           '這張工單本來就該由 GitHub Actions 執行，但有一個舊的 Apps Script\n' +
           '觸發器把它拉進了已經停用的實作，一叫就拋錯。\n' +
           '怎麼辦：直接按「續跑卡住的工單」，它會重新派工給 GitHub，\n' +
           '已完成的步驟不會重做。要根治的話，在編輯器執行一次\n' +
           'cleanupAdminTriggers_() 清掉殘留的觸發器，\n' +
           '再執行 installAdminJobWatchdog() 裝上自動續跑。';
  }
  var m = String(msg || '');

  if (m.indexOf('429') >= 0) {
    var kind = /per minute|rate limit|RATE/i.test(m) ? 'minute'
             : /per day|daily|quota exceeded/i.test(m) ? 'day' : '';
    if (kind === 'minute') {
      return '這是「每分鐘請求數」上限。\n' +
             '怎麼辦：等一到兩分鐘，按「續跑」即可，前面完成的步驟不必重做。\n' +
             '若反覆發生，代表同時有多個工作在跑，請先讓其中一個跑完再跑下一個。';
    }
    if (kind === 'day') {
      return '這是「每日額度」用完了。\n' +
             '怎麼辦：今天無法再呼叫 AI，請明天再續跑；或到 Google AI Studio\n' +
             '把金鑰升級成付費方案，再到 Apps Script 專案設定更新 GEMINI_API_KEY。';
    }
    return '這是 Gemini 的配額或速率上限，但訊息沒講清楚是哪一種。\n' +
           '怎麼辦：先等兩分鐘按「續跑」；若立刻又失敗，多半是每日額度用完，請明天再試。';
  }

  if (/API key|API_KEY_INVALID|PERMISSION_DENIED|401|403/i.test(m)) {
    return '金鑰無效或沒有權限。\n' +
           '怎麼辦：到 Apps Script 左側「專案設定」→「指令碼屬性」，\n' +
           '檢查 GEMINI_API_KEY 是否正確、前後有沒有多餘空格。\n' +
           '金鑰可在 Google AI Studio 重新產生。';
  }
  if (m.indexOf('MAX_TOKENS') >= 0) {
    return '輸出超過長度上限被截斷。\n' +
           '怎麼辦：撰稿步驟本來就會自動改用精簡指示重生一次，若仍失敗，\n' +
           '代表當天內容特別多，可把逐字稿分成上下半場分兩次投稿。';
  }
  if (m.indexOf('過期') >= 0 || m.indexOf('快取') >= 0) {
    return '中途的暫存結果已逾時（暫存只保留一小時）。\n' +
           '怎麼辦：回後台把同一份逐字稿重新送出一次即可。';
  }
  if (m.indexOf('股票對照表') >= 0) {
    return '股票對照表是空的，無法比對代號。\n' +
           '怎麼辦：在 Apps Script 編輯器執行 rebuildCodeMapJob()，\n' +
           '跑完再回後台按「續跑」。';
  }
  if (/Timed out|timeout|逾時|Exceeded maximum execution/i.test(m)) {
    return '這一步花太久被系統中斷。\n' +
           '怎麼辦：按「續跑」，它會從中斷的地方接著做，不會從頭來過。\n' +
           '若同一步反覆逾時，多半是外部報價來源很慢，可稍後再試。';
  }
  if (/Service invoked too many times|Limit Exceeded/i.test(m)) {
    return '今天呼叫 Google 服務（寄信或試算表）的次數達到上限。\n' +
           '怎麼辦：請明天再續跑。資料不會遺失。';
  }
  if (m.indexOf('找不到影片清單') >= 0) {
    return '找不到這支影片的原文。\n' +
           '怎麼辦：回後台重新送出逐字稿，系統會重新建立這一列。';
  }
  if (m.indexOf('結構化紀錄') >= 0) {
    return '撰稿要讀回「操作紀錄」與「會員持股」裡這一天的列，但一列都沒有。\n' +
           '新版已經會自動分辨三種情況，只有最後一種才會停在這裡：\n' +
           '  1. 買賣被改記到前一個交易日（他在回顧昨天的操作）→ 自動改寫那幾天，不再失敗\n' +
           '  2. 當天本來就沒有指名個股（純大盤看法）→ 寫一篇說明並繼續，不再失敗\n' +
           '  3. 連逐字稿都不在 → 資料真的沒寫進去，才是這個錯誤\n' +
           '怎麼辦：先確認 Apps Script 已部署到含 sms-scope-v6 的新版本\n' +
           '（瀏覽器開 部署網址?action=ping，看 features 有沒有這個字）。\n' +
           '若已是新版仍出現，代表擷取那一步沒有產出：多半是 Gemini 當日額度用完，\n' +
           '請到「執行紀錄」確認有沒有 429，明天再續跑，或加 GEMINI_API_KEY_2。';
  }
  return '沒有對應的已知處理方式。\n' +
         '怎麼辦：先按「續跑」試一次。若重複失敗，到 Apps Script 左側「執行紀錄」\n' +
         '找最近一次失敗，把完整訊息記下來再排查。';
}


/* ==================================================================== *
 * 立即刷新網站（分段執行）
 *
 * 原本是一次同步跑完六件事，前端要乾等一到兩分鐘，而且常常超過網頁請求
 * 的等待上限而顯示連線失敗——實際上後端還在跑，只是前端不知道。
 * 改成與投稿工單一樣的分段模式：每段自己看時間，做完寫回進度，排下一棒。
 * 前端輪詢就能畫出進度條，也能如實顯示是卡在哪一步。
 * ==================================================================== */

var REFRESH_KEY = 'siteRefreshState';

var REFRESH_TASKS = [
  { name: '清除產業列', fn: function () { return purgeIndustryRows() + ' 列'; } },
  { name: '代號比對', fn: function () { repairCodesJob(); return ''; } },
  { name: '更新基本面', fn: function () { rebuildFundamentalsJob(); return ''; } },
  { name: '補齊日K', fn: function (deadline) {
      var dk = dailyKBackfillRound_({ deadline: deadline - 60 * 1000, waitMs: 30 * 1000,
                                      source: '立即刷新網站' });
      if (dk.fatal) { throw new Error(dk.note); }
      return dk.done ? dk.note : { pending: true, note: dk.note };
    } },
  { name: '重算持股追蹤', fn: function () { rebuildHoldingsTrackerJob(); return ''; } },
  { name: '記錄績效', fn: function () { snapshotPerformanceJob(); return ''; } }
];

function refreshState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(REFRESH_KEY);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (e) { return null; }
}

function setRefreshState_(st) {
  PropertiesService.getScriptProperties().setProperty(REFRESH_KEY, JSON.stringify(st || {}));
}

function scheduleRefresh_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runSiteRefresh_') { ScriptApp.deleteTrigger(t); }
  });
  ScriptApp.newTrigger('runSiteRefresh_').timeBased().after(2000).create();
}

function runSiteRefresh_() {
  var st = refreshState_();
  if (!st || st.status !== '處理中') { return; }
  var deadline = stepDeadline_();

  while (st.index < REFRESH_TASKS.length) {
    if (timeLeft_(deadline) < 60000) { break; }     // 額度不夠，交給下一棒
    var t = REFRESH_TASKS[st.index];
    st.step = t.name;
    try {
      var r = t.fn(deadline);
      if (r && typeof r === 'object' && r.pending) {
        // 還沒做完（例如補日K）：停在同一步，下一棒接著做
        st.step = t.name + '（' + r.note + '）';
        st.updatedAt = nowStamp_();
        setRefreshState_(st);
        scheduleRefresh_();
        return;
      }
      st.log.push('✓ ' + t.name + (r ? '：' + r : ''));
    } catch (e) {
      st.log.push('✗ ' + t.name + '：' + String(e).slice(0, 120));
      st.lastError = String(e && e.message || e);
    }
    st.index++;
    st.updatedAt = nowStamp_();
    setRefreshState_(st);
  }

  if (st.index >= REFRESH_TASKS.length) {
    st.status = '完成';
    st.step = '完成';
    st.updatedAt = nowStamp_();
    setRefreshState_(st);
    return;
  }
  setRefreshState_(st);
  scheduleRefresh_();
}

function apiAdminStartRefresh(key) {
  try {
    adminAuth_(key);
    var cur = refreshState_();
    if (cur && cur.status === '處理中') {
      return { ok: false, reason: '刷新已經在進行中（' + cur.step + '）。' };
    }
    setRefreshState_({ status: '處理中', index: 0, step: REFRESH_TASKS[0].name,
                       total: REFRESH_TASKS.length, log: [],
                       startedAt: nowStamp_(), updatedAt: nowStamp_() });
    scheduleRefresh_();
    return { ok: true, total: REFRESH_TASKS.length };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminRefreshState(key) {
  try {
    adminAuth_(key);
    var st = refreshState_();
    if (st && st.lastError) { st.advice = troubleshoot_(st.lastError); }
    return { ok: true, state: st };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}


/**
 * 重新產生某一天的郵件內容。
 *
 * 使用時機：手動修過試算表（補代號、改名稱、調價位）之後。
 * 那些修改只會反映在網站上，因為網站是即時讀試算表的；
 * 但郵件內容是撰稿當下產生並存起來的一份文字，不會自己跟著變。
 * 這一支重跑撰稿，讓信件與網站重新對齊。
 *
 * 會先做一次代號補齊，所以手動改完直接按這個就好。
 */
/* ==================================================================== *
 * 資料修正的小工單
 *
 * 為什麼需要
 * ----------
 * 「重寫郵件內容」與「重新分類觀望類」都是同步執行的：按下去之後，
 * 瀏覽器就那樣等著，一分鐘內沒有任何回饋。而它們裡面其實有明確的段落——
 * 補代號、讀資料、呼叫模型、寫回試算表——只是沒有人把段落講出來。
 *
 * 對使用者來說，「等一分鐘」與「當掉了」在畫面上長得一模一樣，
 * 於是唯一能做的就是再按一次，而再按一次會讓同一天的文章被寫兩遍。
 * 這一組東西只做一件事：把段落寫進指令碼屬性，讓頁面另外開一條輪詢去讀。
 *
 * 為什麼不用觸發器
 * ----------------
 * 全面重整那種要跑幾十分鐘的工作才需要觸發器接力。這兩支一分鐘內結束，
 * 搬到背景反而要多處理「誰在跑、跑到哪、失敗了誰通知」，得不償失。
 * 同步執行照樣可以回報進度：指令碼屬性的寫入是立刻生效的，
 * 輪詢那一邊是另一次執行，讀得到這一次執行中途寫下的東西。
 *
 * 取消能取消到什麼程度
 * --------------------
 * 只能在段落與段落之間停下。已經送出去的那一次模型呼叫不會被收回，
 * 已經寫進試算表的那幾列也不會回頭刪掉——那是另一件事（資料還原），
 * 不該混在「我不想等了」這個動作裡。介面上要把這件事講清楚。
 * ==================================================================== */

var FIXJOB_PROP_ = 'ADMIN_FIXJOB';

var FIXJOB_STEPS_ = {
  mail:  ['補齊空白代號', '讀取當天紀錄', '呼叫模型撰稿', '寫入每日推播', '完成'],
  recls: ['讀取操作紀錄', '挑出觀望類', '送 AI 判定', '寫回分類', '更新快取', '完成']
};

var FIXJOB_LABEL_ = { mail: '重寫郵件內容', recls: '重新分類觀望類' };

function fixJobState_() {
  try {
    return JSON.parse(PropertiesService.getScriptProperties()
                        .getProperty(FIXJOB_PROP_) || 'null');
  } catch (e) { return null; }
}

function setFixJob_(st) {
  st.updatedAt = nowStamp_();
  PropertiesService.getScriptProperties()
    .setProperty(FIXJOB_PROP_, JSON.stringify(st));
  return st;
}

function fixJobBegin_(kind, note) {
  return setFixJob_({
    kind: kind, label: FIXJOB_LABEL_[kind] || kind,
    names: FIXJOB_STEPS_[kind] || [], index: 0, status: '處理中',
    note: note || '', error: '', cancel: false, startedAt: nowStamp_()
  });
}

/* 每一步都重新讀一次狀態再寫回去，不是拿著一份在手上改。
   因為中途可能有另一次執行（按下取消的那一次）動過 cancel，
   拿舊的覆蓋回去會把取消指令吃掉。 */
function fixJobStep_(i, note) {
  var st = fixJobState_();
  if (!st) { return null; }
  st.index = i;
  if (note !== undefined) { st.note = note; }
  return setFixJob_(st);
}

function fixJobEnd_(status, note, err) {
  var st = fixJobState_() || { names: [], index: 0 };
  st.status = status;
  st.index = (status === '完成') ? Math.max(0, (st.names || []).length - 1) : st.index;
  if (note) { st.note = note; }
  st.error = err ? String(err).slice(0, 400) : '';
  st.cancel = false;
  return setFixJob_(st);
}

/** 段落之間的取消檢查點。被按過取消就拋出來，由外層統一收尾。 */
function fixJobAbort_() {
  var st = fixJobState_();
  if (st && st.cancel) { throw new Error('FIXJOB_CANCELLED'); }
}

/* 撰稿的內部段落。stepArticle_ 只管喊一聲「我到哪了」，
   要不要記、記到哪裡，是這一層決定的——工單那條路（投稿）本來就有
   自己的進度，不需要重複記一份。 */
var ARTICLE_PROGRESS_ = null;

function articleProgress_(phase, note) {
  if (!ARTICLE_PROGRESS_) { return; }
  try { ARTICLE_PROGRESS_(phase, note); } catch (e) {}
}

/** 頁面輪詢用。沒有紀錄時回 null，前端就不畫進度區。 */
function apiAdminFixState(key) {
  try {
    adminAuth_(key);
    return { ok: true, state: fixJobState_() };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/** 按下「取消」。只立旗子，真正停下來是在下一個段落交界。 */
function apiAdminCancelFix(key) {
  try {
    adminAuth_(key);
    var st = fixJobState_();
    if (!st) { return { ok: false, reason: '目前沒有進行中的資料修正工作。' }; }
    if (st.status !== '處理中') {
      return { ok: false, reason: '這一輪已經結束了（' + st.status + '），沒有東西可以取消。' };
    }
    st.cancel = true;
    st.note = '已送出取消，會在目前這一段做完後停下。';
    setFixJob_(st);
    return { ok: true, message: '已送出取消。正在進行的那一段會做完，' +
                                '接下來的段落不會再跑；已經寫進試算表的內容不會回頭刪除。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminRebuildMail(key, dateStr) {
  var d = fmtDate_(dateStr) || todayStr_();
  try {
    adminAuth_(key);
    fixJobBegin_('mail', d + '：正在比對官方清單，把空白的代號補起來');

    var fix = resolveBlankCodes_(d);
    var fixNote = '代號修正 ' + fix.fixed + '、剔除 ' + fix.dropped +
                  '、仍待確認 ' + fix.pending;
    fixJobStep_(0, fixNote);
    fixJobAbort_();

    /* 撰稿裡面的三段由 stepArticle_ 自己喊，這裡只負責把它對到格子上。
       中間也順便當取消檢查點：模型呼叫是這支最久的一段，
       在它之前停得下來，就不會白燒一次額度。 */
    var PHASE = { '素材': 1, '撰稿': 2, '寫入': 3 };
    ARTICLE_PROGRESS_ = function (phase, note) {
      if (phase === '撰稿') { fixJobAbort_(); }
      fixJobStep_(PHASE[phase], note || '');
    };
    try {
      stepArticle_({ date: d, videoId: '', id: 'MAIL-' + d, step: '撰稿',
                     done: 0, total: 1, _codesDone: true });
    } finally {
      ARTICLE_PROGRESS_ = null;
    }

    var msg = d + ' 的郵件內容已重新產生。' + fixNote + '。' +
              (d === todayStr_() ? '若當天的信已寄出，需要重寄才會看到新內容。' : '');
    fixJobEnd_('完成', msg);
    return { ok: true, message: msg };
  } catch (e) {
    var m = String(e && e.message || e);
    if (m.indexOf('FIXJOB_CANCELLED') >= 0) {
      fixJobEnd_('已取消', d + ' 的重寫在段落之間停下。已經補好的代號會留著，文章沒有被覆蓋。');
      return { ok: false, cancelled: true,
               reason: '已取消。已經補好的代號留著，那一天的文章沒有被覆蓋，' +
                       '要重跑直接再按一次即可。' };
    }
    fixJobEnd_('失敗', '', m);
    return { ok: false, reason: m, advice: troubleshoot_(m) };
  }
}

/**
 * 重新分類某一天（或全部）的觀望類紀錄。
 *
 * 對應上游的 reclassify 模式。判斷依據是理由摘錄的結論：
 * 結論是現在不要進場就歸觀望不碰，否則歸觀望注意。
 * 分類規則調整後，用這一支把舊資料一起套用新標準。
 */
var PIPE_RECLASSIFY_SYSTEM =
  '你要判斷講者對每一檔股票的結論是「現在不要進場」還是「值得留意」。\n' +
  '\n' +
  '只看一件事：講者對「現在進場」的態度。\n' +
  '\n' +
  '判斷規則：以整段話的結論為準，不要被前半句的鋪陳帶走。\n' +
  '講者很常先講這檔公司多好、適合什麼人，最後才說現在不要買。\n' +
  '那種情況結論是不要買，一律歸 avoid。\n' +
  '  「適合退休、想穩定賺錢的投資人，現在已漲上去，不用買了」→ avoid\n' +
  '  「作為範本，急跌後急彈不要買，會整理兩個月」→ avoid\n' +
  '\n' +
  '只要出現不要買、不用買、別追、來不及、漲上去了、會整理這類字眼，\n' +
  '若最後仍是禁買歸 avoid；若明確給同一股拉回後買點，則依條件歸 watch。\n' +
  '語氣偏負面也歸 avoid，不必等到明講不准碰：\n' +
  '  拿它當追高受傷的例子（昨天大漲今天大跌、漲幾塊隔天跌更多、追高容易套牢）→ avoid\n' +
  '  外資買一天賣一天、散戶跟著追會被套 → avoid\n' +
  '  昨天去買今天就跌、動作越多勝率越低 → avoid\n' +
  '說明前面描述的是追高受傷或下跌風險時，後面就算接了「等待機會」「逢低布局」，結論仍是 avoid。\n' +
  '看好、會漲、好股票、等回檔進場、跌到某價位可以買，歸 watch。\n' +
  '只有中性盤整或法人反覆換手、沒有明確正面依據歸 avoid。明確本股低檔買點歸 watch，不能因不要追高就忽略買點。\n' +
  '拿它當反例也歸 avoid：「總比你去買環球金好」「買的人全部賠錢」「不准買」。\n' +
  '族群點名並講本股業績好、不用擔心、會過季線（「業績很好不必擔心」）歸 watch，不因句中有「觀察」兩字改判中性。\n' +
  '「現在還不能買／還不行，要等 ETF 賣完才考慮」當下結論是不進場，歸 avoid；只給買點（900以下是買點）而沒說現在不能買才歸 watch。\n' +
  '漲多缺口還沒補、切勿追高、拿它當負面示範（跌停、跟跌）歸 avoid，不因句中有「買」「等」改判 watch。\n' +
  '正面評價即使沒講買點也歸 watch：資產或長線價值高、淨值高、法人持續買超、體質穩健、適合切入、可持續留意，\n' +
  '只要整段沒有負面說法（追高、套牢、轉弱、賣超、風險高……）。\n' +
  '  「淨值高、資產價值非常高，具備長線價值，若拉回可持續注意」→ watch\n' +
  '  「外資持續買進，相對穩健，適合資金較少者切入」→ watch\n' +
  '  「資產價值高，但追高容易套牢」→ avoid（有負面說法）\n' +
  '反話與「賣壓竭盡」是看多，歸 watch，不因字面有賣、破底、賣壓、跌改判 avoid（2026/09/17 祥碩、勤誠判錯）：\n' +
  '  「假破底後站上季線，目標價看2000元，拉回或打底後可注意」→ watch（假破底＝沒量跌破新低，是看多）\n' +
  '  「等待賣壓減輕、散戶在低點賣完後，將是真正可以賺錢準備大漲的時機」→ watch\n' +
  '  「你們想賣的趕快賣，我的會員不准賣」→ watch（不准賣是續抱，不是不准買）\n' +
  '  「只要ETF賣在低點的股票不會跌了」→ watch（負面示範的是 ETF 的操作，不是這一檔）\n' +
  '但同一段明講「現在還不能買／還不行」時仍歸 avoid。\n' +
  '限制後段解除時依後段判斷（v54）：先說「還不能買，要等 ETF 賣完」，後面明講「ETF 已經賣完了，現在可以在 880 買」→ watch；\n' +
  '順序反過來（先說可以、後面又說還不能買）仍歸 avoid。\n' +
  '「拉回可以布局」「整理三個月、像某股啟動前，拉回承接」是低檔買點，歸 watch，不因「整理」「拉回」改判 avoid。\n' +
  '散戶賣、外資賣、ETF 換股不是會員賣出，也不是本股偏空；「散戶在賣、大戶增加」是看多理由。\n' +
  '\n' +
  '要分清楚「現在不要買」與「現在還沒買」，字面很像，意思相反：\n' +
  '  現在不要買　他叫你別碰（不准碰、追高、還沒跌完、一定會殺破）→ avoid\n' +
  '  現在還沒買　他自己想買、只是在等時機 → watch\n' +
  '「候選名單」「口袋名單」「我以後要買」「等它洗完我就買」「可以抄起來」\n' +
  '「列出來給你看」這幾種說法一律 watch，那是他最看好的一批。\n' +
  '這一批的說明天生同時帶正負詞——「列入追蹤名單，仍在整理，暫不進場」——\n' +
  '不要被「暫不」帶走：他講的是時機，不是否定這一檔。\n' +
  '實際判錯過（2026/09/10）：聖暉、信紘科、辛耘、牧德四檔候選名單全判成\n' +
  'avoid，於是他最想買的名單出現在「語氣偏空，暫不進場」那張表裡。\n' +
  '\n' +
  '只回傳 JSON，index 對應輸入編號：\n' +
  '{"results":[{"index":1,"cls":"avoid 或 watch"}]}';

function apiAdminReclassify(key, dateStr) {
  try {
    adminAuth_(key);
    var d = fmtDate_(dateStr) || '';
    fixJobBegin_('recls', (d || '全部歷史') + '：正在讀取操作紀錄');

    var sh = getSheet_('操作紀錄');
    var vals = sh.getDataRange().getValues();
    var head = vals[0];
    var cDate = head.indexOf('日期'), cDir = head.indexOf('方向');
    var cName = head.indexOf('股票名稱'), cReason = head.indexOf('理由摘錄');

    fixJobStep_(1, '共 ' + (vals.length - 1) + ' 列，正在挑出觀望類');

    var items = [];
    for (var i = 1; i < vals.length; i++) {
      var dir = String(vals[i][cDir] || '');
      if (dir.indexOf('觀望') < 0) { continue; }
      if (d && fmtDate_(vals[i][cDate]) !== d) { continue; }
      items.push({ row: i + 1, name: String(vals[i][cName] || ''),
                   dir: dir, reason: String(vals[i][cReason] || '') });
    }
    if (!items.length) {
      var none = '沒有觀望類紀錄需要重新分類。';
      fixJobEnd_('完成', none);
      return { ok: true, message: none };
    }

    // 一次最多 40 筆，避免請求過大而被截斷
    var batch = items.slice(0, 40);
    fixJobStep_(2, '這一批 ' + batch.length + ' 筆' +
                   (items.length > 40 ? '（總共 ' + items.length + ' 筆，要分批）' : '') +
                   '，正在送 AI 判定，約一分鐘');
    fixJobAbort_();
    var payload = batch.map(function (x, n) {
      return (n + 1) + '. ' + x.name + '：' + (x.reason || '（無說明）');
    }).join('\n');

    var raw = callGemini_(PIPE_RECLASSIFY_SYSTEM, payload,
                          { maxOut: 2048, temperature: 0, json: true });
    var res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim());
    fixJobStep_(3, 'AI 回覆 ' + ((res.results || []).length) + ' 筆判定，正在寫回試算表');

    var changed = 0;
    (res.results || []).forEach(function (r) {
      var n = Number(r.index) - 1;
      if (!(n >= 0 && n < batch.length)) { return; }
      var want = r.cls === 'avoid' ? '觀望不碰' : '觀望注意';
      if (batch[n].dir === want) { return; }
      sh.getRange(batch[n].row, cDir + 1).setValue(want);
      changed++;
      Logger.log('  重新分類　' + batch[n].name + '　' + batch[n].dir + ' → ' + want);
    });

    fixJobStep_(4, '改動 ' + changed + ' 筆，正在清除持股追蹤快取');
    CACHE.remove('tracker');

    var msg = '檢視 ' + batch.length + ' 筆，改動 ' + changed + ' 筆。' +
              (items.length > 40 ? '（還有 ' + (items.length - 40) + ' 筆，請再執行一次）' : '') +
              ' 改完記得按「立即刷新網站」。';
    fixJobEnd_('完成', msg);
    return { ok: true, message: msg, remaining: Math.max(0, items.length - 40) };
  } catch (e) {
    var m = String(e && e.message || e);
    if (m.indexOf('FIXJOB_CANCELLED') >= 0) {
      fixJobEnd_('已取消', '在送 AI 判定之前停下，這一輪沒有改動任何一列。');
      return { ok: false, cancelled: true,
               reason: '已取消。停在送 AI 之前，沒有改動任何一列，也沒有花掉模型額度。' };
    }
    fixJobEnd_('失敗', '', m);
    return { ok: false, reason: m, advice: troubleshoot_(m) };
  }
}



/* ==================================================================== *
 * 人工補登
 *
 * 為什麼需要
 * ----------
 * 有些內容是機器再怎麼強也擷取不到的，因為講者刻意不講：
 *
 *   「這是一檔非電子股，20 幾塊錢，盤底八個月，每股淨值 60.5 元，
 *     月K線 MACD 兩年半來第一次翻揚。我下半週會把股票名稱公開，
 *     我現在圖遮起來。」
 *
 * 整段話有型態、有價位、有淨值、有結論，唯獨沒有名字。擷取端的規則是
 * 「不指名就不要生」——那條規則是對的，從漲跌幅去猜是哪一檔只會猜錯，
 * 所以這一段必然被丟掉。可是它是那天最重要的內容。
 *
 * 後來管理者自己知道了那是哪一檔（他隔天講了，或是從圖認出來），
 * 這時候需要一條「人把答案補進去」的路，而且補進去之後要走完全一樣的流程，
 * 不能變成一筆長得不一樣的資料。
 *
 * 怎麼接進既有流程
 * ----------------
 * 補登只做三件事：驗代號、請模型把那段話整理成表格用的格式、寫進操作紀錄。
 * 寫進去之後它就是一般的一列，後面的重寫郵件、重算追蹤、刷新網站
 * 全部沿用既有的按鈕與函式，一行新流程都沒有。
 *
 * 模型在這裡的角色很小：分類由人指定（人比模型清楚他當時在講什麼），
 * 模型只負責把口語濃縮成一句 40 字的重點、把價位挑出來。
 * 這是刻意的——人工補登的前提就是人比較準，讓模型有權推翻人就沒有意義了。
 *
 * 為什麼要留一張「人工補登」分頁
 * ------------------------------
 * 操作紀錄那一列看起來與 AI 擷取的沒有兩樣，事後分不出哪一列是人補的。
 * 而全面重整會拿逐字稿重新判定每一列，人補的那一列在逐字稿裡找不到名字
 * （講者根本沒講），複審會判定「找不到對應段落」而不動它——這是對的，
 * 但只有在看得到原始依據時才說得清楚。這張分頁留的就是那個依據：
 * 誰在什麼時候、根據哪一段話、補了什麼。
 * ==================================================================== */

var MANUAL_SHEET = "人工補登";

// 人工補登的來源標記。與手動投稿的 MANUAL- 刻意分開：
// 手動投稿那些仍然是 AI 從整份逐字稿擷取出來的，該複審就要複審；
// 這裡的是人指定的結論，複審不該去改它。
var MANUAL_ENTRY_PREFIX = "MANUALENTRY-";

/* 會員簡訊寫進操作紀錄與會員持股時，來源影片ID 用這個開頭。

   複審要認得它，理由與人工補登一模一樣：複審的判斷依據是「這一檔在逐字稿裡
   怎麼被講」，而簡訊是盤中另外發出的即時指令，內容根本不在逐字稿裡。
   交給複審的話它每一次都會回報「找不到節錄」，然後把一筆正確的紀錄刪掉或降級——
   而簡訊的優先權本來就高於收盤後的逐字稿，讓模型拿逐字稿去推翻它是反的。 */
var SMS_ENTRY_PREFIX = "CMONEY-";


/** 這一列是不是「不該交給逐字稿複審」的來源（人工補登或會員簡訊）。 */
function isNonTranscriptSource_(vid) {
  var v = String(vid || '');
  return v.indexOf(MANUAL_ENTRY_PREFIX) === 0 || v.indexOf(SMS_ENTRY_PREFIX) === 0;
}

var MANUAL_ENTRY_SYSTEM =
  '你要把一段直播逐字稿的節錄，整理成一列表格資料。\\n' +
  '\\n' +
  '管理者已經告訴你這一段講的是哪一檔股票，以及該歸哪一類。\\n' +
  '這兩件事你不要質疑也不要更改——他看過整支影片，你只看到一段節錄。\\n' +
  '你的工作只有兩件：把價位挑出來、把口語濃縮成一句重點。\\n' +
  '\\n' +
  'price：\\n' +
  '  只放數字，或帶著數字的短句（28 到 29、238、255 以上）。\\n' +
  '  必須是他真的講出來的操作價位：買進價、承接價、進場價、賣出價、\\n' +
  '  或「跌到多少可以買」這種門檻。\\n' +
  '  不是目標價、不是每股淨值、不是漲跌幅、不是成交量、不是指數點位。\\n' +
  '  節錄裡沒有這種數字就填「未說明」。留「未說明」永遠好過填一個編的數字。\\n' +
  '  「20 幾塊」「30 出頭」這種概數也填「未說明」，但要在 reason 裡保留他的講法。\\n' +
  '\\n' +
  'reason：\\n' +
  '  用你自己的話寫成一句自然完整的重點，通常 55 字以內。\\n' +
  '  禁止使用〔〕、【】、[]、+、＋或欄位標籤拼接，句尾使用正常標點。\\n' +
  '  不是把節錄剪一段貼上——他是口語，有大量語助詞、重複與跳接。\\n' +
  '  只留他真正的判斷：型態、價位、理由、態度。\\n' +
  '  不要加他沒講的東西，不要保留「好，注意」「有沒有看到」這類口頭禪，\\n' +
  '  不要加引號，不要寫成「他說……」，直接寫結論。\\n' +
  '  例：「盤底八個月、每股淨值 60.5 元，月K線 MACD 兩年半來首次翻揚，視為存股標的。」\\n' +
  '\\n' +
  'confident：節錄太零碎、看不出他在講什麼時回 false，並在 why 說明缺什麼。\\n' +
  'false 不會讓補登失敗，只是提醒管理者再看一眼。\\n' +
  '\\n' +
  '只回傳 JSON，不要有其他文字：\\n' +
  '{"price":"","reason":"","confident":true,"why":""}';


/**
 * 找出某一天某一檔的所有列，兩張分頁都找。
 *
 * 不能借用 reviewCollect_：它會刻意跳過人工補登的列（那些不該被複審碰），
 * 拿它來找重複的話，補第二次時永遠找不到第一次，勾了覆蓋也只是又新增一列，
 * 同一檔就會在同一天出現兩次——而且兩次的內容還不一樣。
 */
function manualFindRows_(d, code) {
  var out = [];
  [['操作紀錄', '方向'], ['會員持股', '目前立場']].forEach(function (pair) {
    var sn = pair[0], dirName = pair[1];
    var vals;
    try { vals = getSheet_(sn).getDataRange().getValues(); }
    catch (e) { return; }
    if (vals.length < 2) { return; }
    var head = vals[0];
    var cDate = head.indexOf('日期');
    var cCode = head.indexOf('代號');
    var cName = head.indexOf('股票名稱');
    var cDir = head.indexOf(dirName);
    if (cDate < 0 || cCode < 0) { return; }
    for (var i = 1; i < vals.length; i++) {
      if (fmtDate_(vals[i][cDate]) !== d) { continue; }
      if (String(vals[i][cCode] || '').trim() !== code) { continue; }
      out.push({ sheet: sn, row: i + 1,
                 name: String(vals[i][cName] || '').trim(),
                 cls: cDir >= 0 ? String(vals[i][cDir] || '') : sn });
    }
  });
  return out;
}


/**
 * 補登要用的日曆資料。
 *
 * 前台的日曆只讓「有資料」的日子點得下去，補登這裡剛好相反——
 * 需要補的往往正是還沒有資料的那一天，所以每一天都要點得下去，
 * 有標記的只是提醒「這天已經有內容了」。
 */
function apiAdminManualDays(key) {
  try {
    adminAuth_(key);
    var rec = {}, manual = {};
    readSheetObjects_("操作紀錄").forEach(function (r) {
      var d = fmtDate_(r["日期"]); if (d) { rec[d] = 1; }
    });
    readSheetObjects_("會員持股").forEach(function (r) {
      var d = fmtDate_(r["日期"]); if (d) { rec[d] = 1; }
    });
    try {
      readSheetObjects_(MANUAL_SHEET).forEach(function (r) {
        var d = fmtDate_(r["日期"]); if (d) { manual[d] = 1; }
      });
    } catch (e) { /* 還沒補登過就沒有這張分頁 */ }

    return { ok: true, today: todayStr_(),
             dates: Object.keys(rec).sort(),
             manual: Object.keys(manual).sort() };
  } catch (e) {
    return { ok: false, reason: String(e && e.message || e) };
  }
}



/* ==================================================================== *
 * 逐日編輯：改一列、刪一列
 *
 * 為什麼要有這個
 * --------------
 * 前面所有的機制都是「讓機器判得更準」，但機器一定有判不準的時候，
 * 而管理者是唯一看過整支影片的人。少了直接改一列的能力，發現錯了只有
 * 兩條路：去 Google 試算表手動改（然後郵件內容不會跟著變，兩邊對不起來），
 * 或是按一次全面重整賭它這次會判對。兩條都不好。
 *
 * 列號會位移，所以不能只靠列號
 * ----------------------------
 * 前端拿到的是「第 12 列」，但在他讀完清單到按下儲存之間，可能有另一個人
 * 刪了第 5 列——這時第 12 列已經是別人的資料了，照著寫下去會改到不相干的股票，
 * 而且沒有任何錯誤訊息，兩邊都以為成功了。
 *
 * 所以每一次寫入之前都要驗身分：那一列的日期、代號、名稱要與前端記得的一樣。
 * 對不上就整個範圍重找一次；找得到就用找到的列，找不到就明白拒絕，
 * 請對方重新整理。寧可拒絕一次，也不要改錯一列。
 * ==================================================================== */

/** 某一天的所有紀錄，兩張分頁一起回。 */
function apiAdminDayRows(key, dateStr) {
  try {
    adminAuth_(key);
    var d = fmtDate_(dateStr);
    if (!d) { return { ok: false, reason: '日期不正確。' }; }

    var out = [];
    [['操作紀錄', '方向', '價位說明', '理由摘錄'],
     ['會員持股', '目前立場', '', '說明重點']].forEach(function (spec) {
      var sn = spec[0], dirName = spec[1], priceName = spec[2], reasonName = spec[3];
      var vals;
      try { vals = getSheet_(sn).getDataRange().getValues(); }
      catch (e) { return; }
      if (vals.length < 2) { return; }

      var head = vals[0];
      var c = {
        date: head.indexOf('日期'), name: head.indexOf('股票名稱'),
        code: head.indexOf('代號'), dir: head.indexOf(dirName),
        price: priceName ? head.indexOf(priceName) : -1,
        reason: head.indexOf(reasonName), vid: head.indexOf('來源影片ID')
      };
      if (c.date < 0 || c.name < 0) { return; }

      for (var i = 1; i < vals.length; i++) {
        if (fmtDate_(vals[i][c.date]) !== d) { continue; }
        var nm = String(vals[i][c.name] || '').trim();
        if (!nm) { continue; }
        var vid = c.vid >= 0 ? String(vals[i][c.vid] || '') : '';
        out.push({
          sheet: sn, row: i + 1,
          name: nm,
          code: String(vals[i][c.code] || '').trim(),
          cls: sn === '會員持股' ? '會員持股'
                                : String(vals[i][c.dir] || '').trim(),
          stance: sn === '會員持股' ? String(vals[i][c.dir] || '').trim() : '',
          price: c.price >= 0 ? String(vals[i][c.price] || '未說明') : '',
          reason: String(vals[i][c.reason] || ''),
          vid: vid,
          manual: vid.indexOf(MANUAL_ENTRY_PREFIX) === 0
        });
      }
    });

    // 同一天內排序：買賣在前，觀望在後，會員持股最後。看的人最在意的是有沒有動作。
    var ORDER = { '買入': 1, '賣出': 2, '觀望不碰': 3, '觀望注意': 4, '會員持股': 5 };
    out.sort(function (a, b) {
      var oa = ORDER[a.cls] || 9, ob = ORDER[b.cls] || 9;
      if (oa !== ob) { return oa - ob; }
      return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
    });

    return { ok: true, date: d, rows: out };
  } catch (e) {
    return { ok: false, reason: String(e && e.message || e) };
  }
}


/**
 * 確認前端記得的那一列還是同一列。
 *
 * 回傳實際的列號，找不到回 0。先看原本那一列，對得上就用它（最常見的情形，
 * 一次讀取就結束）；對不上才整張表掃一遍找日期、代號、名稱都相同的那一列。
 */
function verifyRow_(sheetName, row, date, code, name) {
  var vals = getSheet_(sheetName).getDataRange().getValues();
  if (vals.length < 2) { return 0; }
  var head = vals[0];
  var cDate = head.indexOf('日期'), cName = head.indexOf('股票名稱'),
      cCode = head.indexOf('代號');
  if (cDate < 0 || cName < 0) { return 0; }

  function same_(i) {
    if (i < 1 || i >= vals.length) { return false; }
    if (fmtDate_(vals[i][cDate]) !== date) { return false; }
    if (String(vals[i][cName] || '').trim() !== name) { return false; }
    if (cCode >= 0 && String(vals[i][cCode] || '').trim() !== String(code || '')) { return false; }
    return true;
  }

  if (same_(row - 1)) { return row; }
  for (var i = 1; i < vals.length; i++) {
    if (same_(i)) { return i + 1; }
  }
  return 0;
}


/**
 * 改一列。
 *
 * payload：sheet / row / date / code / name（用來驗身分）
 *          cls / price / reason（要改成什麼）
 *
 * 分類改到跨分頁時（操作紀錄 ↔ 會員持股）會搬家：新的那張寫一列、舊的那列刪掉。
 * 兩張表的欄位不一樣，不搬家的話「會員持股」那一列會多出一個沒有欄位可放的價位。
 */
function apiAdminUpdateRow(key, payload) {
  try {
    adminAuth_(key);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }

  payload = payload || {};
  var sheetName = String(payload.sheet || '').trim();
  var d = fmtDate_(payload.date);
  // name/code 是「這一列原本長什麼樣」，拿去認人；newName/newCode 才是要寫進去的。
  // 兩者分開是為了能改名——不分開的話，改名之後 verifyRow_ 會拿新名字去找舊那一列，
  // 永遠找不到，畫面上就是「找不到那一列了」。
  var name = String(payload.name || '').trim();
  var code = String(payload.code || '').trim();
  var newName = String(payload.newName == null ? name : payload.newName).trim();
  var newCode = String(payload.newCode == null ? code : payload.newCode).trim();
  var cls = String(payload.cls || '').trim();
  var price = String(payload.price || '').trim();
  var reason = String(payload.reason || '').trim();

  var VALID = ['買入', '賣出', '觀望不碰', '觀望注意', '會員持股'];
  if (['操作紀錄', '會員持股'].indexOf(sheetName) < 0) {
    return { ok: false, reason: '分頁不正確。' };
  }
  if (!d || !name) { return { ok: false, reason: '缺少日期或股票名稱。' }; }
  if (!newName) { return { ok: false, reason: '股票名稱不能空白。' }; }
  if (VALID.indexOf(cls) < 0) {
    return { ok: false, reason: '分類要是這五個之一：' + VALID.join('、') };
  }
  if (!reason) { return { ok: false, reason: '理由摘錄不能空白。' }; }

  /* 代號只收四到六位數字，或留白，或「代號待確認」。

     這個欄位不是自由文字：下游整條路都靠它——持股追蹤、報價、日K、
     基本面全部用代號當鑰匙。打錯一碼不會有人發現，只會安靜地抓到別家公司的價格。
     留白與「代號待確認」要放行，因為那是「還沒查到」的合法狀態。 */
  if (newCode && newCode !== '代號待確認' && !/^(?:00981A|\d{4,6})$/.test(newCode)) {
    return { ok: false, reason: '代號要是 4 到 6 位數字，或留白／填「代號待確認」。' };
  }

  /* 改成別家公司的代號時，順手核對名稱對不對。

     擋下來的是「名稱改了代號忘了改」這種半套修正——那會產出一列
     名稱是甲、代號是乙的紀錄，比原本錯得更難查。
     對照表查不到的代號就放行：新上市或對照表還沒更新都會這樣，
     這裡不該比對照表更嚴格。 */
  if (newCode && /^(?:00981A|\d{4,6})$/.test(newCode)) {
    var official = '';
    try { official = ((loadCodeMap_().byCode || {})[newCode] || {}).name || ''; }
    catch (e) { official = ''; }
    if (official && official !== newName && !_sameStockName_(official, newName)) {
      return { ok: false,
               reason: '代號 ' + newCode + ' 在官方清單裡叫「' + official + '」，' +
                       '與你填的「' + newName + '」對不起來。' +
                       '確定要用這個代號的話，名稱也請一起改成「' + official + '」。' };
    }
  }

  // 沒有數字的價位不是價位。與寫入端、上游同一條規則。
  if (!price || !/\d/.test(price)) { price = '未說明'; }

  var row = verifyRow_(sheetName, Number(payload.row) || 0, d, code, name);
  if (!row) {
    return { ok: false, stale: true,
             reason: '找不到那一列了（可能已被其他人或全面重整改過）。' +
                     '請重新整理這一天的清單再改一次。' };
  }

  var target = (cls === '會員持股') ? '會員持股' : '操作紀錄';
  var moved = (target !== sheetName);

  withLock_(function () {
    var sh = getSheet_(sheetName);
    var head = sh.getDataRange().getValues()[0];
    var vid = '';
    var cVid = head.indexOf('來源影片ID');
    if (cVid >= 0) { vid = String(sh.getRange(row, cVid + 1).getValue() || ''); }

    if (!moved) {
      var cDir = head.indexOf(sheetName === '會員持股' ? '目前立場' : '方向');
      var cReason = head.indexOf(sheetName === '會員持股' ? '說明重點' : '理由摘錄');
      var cPrice = head.indexOf('價位說明');
      var cName = head.indexOf('股票名稱');
      var cCode = head.indexOf('代號');
      if (cName >= 0) { sh.getRange(row, cName + 1).setValue(newName); }
      if (cCode >= 0) { sh.getRange(row, cCode + 1).setValue(newCode); }
      if (cDir >= 0 && sheetName === '操作紀錄') { sh.getRange(row, cDir + 1).setValue(cls); }
      if (cPrice >= 0) { sh.getRange(row, cPrice + 1).setValue(price); }
      if (cReason >= 0) { sh.getRange(row, cReason + 1).setValue(reason); }
      return;
    }

    // 搬家。先寫新的再刪舊的——順序反過來的話，寫入失敗就兩邊都沒有了。
    if (target === '會員持股') {
      var hs = getSheet_('會員持股');
      hs.getRange(hs.getLastRow() + 1, 1, 1, 6)
        .setValues([[d, newName, newCode, '持有', reason, vid]]);
    } else {
      var ts = getSheet_('操作紀錄');
      ts.getRange(ts.getLastRow() + 1, 1, 1, 7)
        .setValues([[d, newName, newCode, cls, price, reason, vid]]);
    }
    sh.deleteRow(row);
  });

  CACHE.remove('tracker');
  var renamed = (newName !== name || newCode !== code);
  Logger.log('後台改列　' + d + '　' + name + '（' + code + '）' +
             (renamed ? ' → ' + newName + '（' + newCode + '）' : '') +
             ' → ' + cls +
             (moved ? '（從「' + sheetName + '」搬到「' + target + '」）' : '') +
             '　' + price + '　' + reason);

  return { ok: true, sync: queueDayEditSync_(d), moved: moved, renamed: renamed, sheet: target,
           message: d + ' 的 ' + name + ' 已更新' +
                    (renamed ? '，改名為「' + newName +
                               (newCode ? '（' + newCode + '）' : '') + '」' : '') +
                    (moved ? '，並從「' + sheetName + '」移到「' + target + '」' : '') + '。' };
}


/* 兩個名稱算不算同一檔。

   官方簡稱與口語稱呼常常差一兩個字（「台積電」與「臺積電」、
   「中鋼」與「中鋼公司」），差這麼一點就把人擋下來只會讓人放棄改。
   真正要擋的是「名稱是甲、代號是乙」那種完全對不起來的情況。 */
function _sameStockName_(a, b) {
  var na = String(a || '').replace(/[\s　*]/g, '');
  var nb = String(b || '').replace(/[\s　*]/g, '');
  if (!na || !nb) { return false; }
  na = na.replace(/臺/g, '台');
  nb = nb.replace(/臺/g, '台');
  return na === nb || na.indexOf(nb) >= 0 || nb.indexOf(na) >= 0;
}


/** 刪一列。同樣先驗身分，對不上就拒絕。 */
function apiAdminDeleteRow(key, payload) {
  try {
    adminAuth_(key);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }

  payload = payload || {};
  var sheetName = String(payload.sheet || '').trim();
  var d = fmtDate_(payload.date);
  var name = String(payload.name || '').trim();
  var code = String(payload.code || '').trim();

  if (['操作紀錄', '會員持股'].indexOf(sheetName) < 0) {
    return { ok: false, reason: '分頁不正確。' };
  }
  if (!d || !name) { return { ok: false, reason: '缺少日期或股票名稱。' }; }

  var row = verifyRow_(sheetName, Number(payload.row) || 0, d, code, name);
  if (!row) {
    return { ok: false, stale: true,
             reason: '找不到那一列了（可能已經被刪掉或改過）。請重新整理這一天的清單。' };
  }

  withLock_(function () { getSheet_(sheetName).deleteRow(row); });

  // 刪除留痕。這是唯一會讓資料消失的後台動作，而且沒有復原鍵，
  // 所以至少要留下「誰在什麼時候刪了什麼」，事後對不上時查得到。
  try {
    withLock_(function () {
      var sh = getSheet_('修正建議');
      sh.getRange(sh.getLastRow() + 1, 1, 1, 9).setValues([[
        new Date(), d, '刪除', sheetName, name, code,
        '後台逐日編輯手動刪除', '已套用（人工）', new Date()
      ]]);
    });
  } catch (e) { Logger.log('刪除留痕失敗（不影響結果）：' + e); }

  CACHE.remove('tracker');
  Logger.log('後台刪列　' + d + '　' + name + '（' + code + '）　來自「' + sheetName + '」');

  return { ok: true, sync: queueDayEditSync_(d), message: d + ' 的 ' + name + ' 已從「' + sheetName + '」刪除。' };
}


/**
 * 人工補登一檔。
 *
 * payload：
 *   date      YYYY-MM-DD 或 YYYY/MM/DD
 *   name      股票名稱（必填）
 *   code      代號（選填，填了會與名稱互相驗證）
 *   cls       買入 / 賣出 / 觀望不碰 / 觀望注意 / 會員持股
 *   quote     逐字稿節錄（必填，模型只看得到這一段）
 *   note      管理者補充（選填，會併進節錄一起給模型）
 *   price     管理者自己填的價位（選填，填了就以它為準，不問模型）
 *   confirm   名稱與代號對不上時，再送一次並帶 true 表示確定要用
 *   replace   同一天同一檔已經有紀錄時，帶 true 表示要覆蓋
 */
function apiAdminManualEntry(key, payload) {
  try {
    adminAuth_(key);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }

  payload = payload || {};
  var d = fmtDate_(payload.date);
  var name = String(payload.name || "").trim();
  var code = String(payload.code || "").trim();
  var cls = String(payload.cls || "").trim();
  var quote = String(payload.quote || "").trim();
  var note = String(payload.note || "").trim();

  var VALID = ["買入", "賣出", "觀望不碰", "觀望注意", "會員持股"];

  if (!d) { return { ok: false, reason: "請選日期。" }; }
  if (d > todayStr_()) { return { ok: false, reason: "不能補登未來的日期。" }; }
  if (!name) { return { ok: false, reason: "請填股票名稱。" }; }
  if (VALID.indexOf(cls) < 0) {
    return { ok: false, reason: "分類要是這五個之一：" + VALID.join("、") };
  }
  // 節錄是模型唯一看得到的東西，也是這一列日後唯一的依據。
  // 太短的節錄整理不出重點，也留不下可查證的軌跡。
  if (quote.length < 20) {
    return { ok: false, reason: "逐字稿節錄只有 " + quote.length +
                                " 字，太短了。請把講到這一檔的那一段整段貼進來。" };
  }

  /* ---- 代號與名稱互相驗證 ---- */
  var map = loadCodeMap_();
  if (!Object.keys(map.byCode).length) {
    return { ok: false, reason: "股票對照表是空的，請先執行 rebuildCodeMapJob()。" };
  }

  var official = "";
  if (code) {
    if (!/^(?:00981A|\d{4,6})$/.test(code)) {
      return { ok: false, reason: "代號要是 4 到 6 位數字。" };
    }
    var hit = map.byCode[code];
    if (!hit) {
      return { ok: false, reason: code + " 不在股票對照表裡。可能是打錯，" +
                                  "或對照表還沒更新（可執行 rebuildCodeMapJob()）。" };
    }
    official = hit.name;
    // 名稱與代號對不上時停下來問一次。這正是 2402／錩新 那種錯的來源，
    // 人工補登更不該讓它悄悄發生——按錯一個數字就整列掛到別家公司身上。
    if (name !== official && !payload.confirm) {
      return { ok: false, needConfirm: true,
               reason: "代號 " + code + " 的官方簡稱是「" + official +
                       "」，與你填的「" + name + "」不一樣。" +
                       "確定是這一檔的話請再按一次，系統會以官方簡稱「" +
                       official + "」寫入。" };
    }
    name = official;
  } else {
    var rr = pipeResolveName_(name, map);
    if (rr.reject) {
      return { ok: false, reason: "「" + name + "」判定不是個股（" + rr.how +
                                  "）。若確定是，請直接填代號。" };
    }
    if (rr.code) { code = rr.code; name = rr.name; }
    else {
      return { ok: false, reason: "比對不出「" + name + "」的代號（" + rr.how +
                                  "）。請直接填代號，那是最可靠的。" };
    }
  }

  /* ---- 同一天同一檔已經有了就先問 ---- */
  var sheetName = (cls === "會員持股") ? "會員持股" : "操作紀錄";
  var dup = manualFindRows_(d, code);
  if (dup.length && !payload.replace) {
    return { ok: false, needReplace: true,
             reason: d + " 已經有 " + name + " 的紀錄了（" +
                     dup.map(function (x) { return x.cls; }).join("、") +
                     "）。要用補登的內容取代它，請勾選「覆蓋同一天既有的這一檔」。" };
  }

  /* ---- 請模型整理格式 ---- */
  var price = String(payload.price || "").trim();
  var reason = "", confident = true, why = "";
  try {
    var user = "股票：" + name + "（" + code + "）\\n" +
               "日期：" + d + "\\n" +
               "管理者指定的分類：" + cls + "\\n" +
               (note ? "管理者補充：" + note + "\\n" : "") +
               "\\n逐字稿節錄：\\n" + quote;
    var raw = callGemini_(MANUAL_ENTRY_SYSTEM, user,
                          { maxOut: 1024, temperature: 0, json: true });
    var res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, "").trim());
    if (!price) { price = String(res.price || "").trim(); }
    reason = naturalReason_(String(res.reason || "").trim());
    confident = res.confident !== false;
    why = String(res.why || "").trim();
  } catch (e) {
    // 模型掛掉不該讓補登失敗——那樣人就白貼了。
    // 退而求其次：用節錄的前 60 字當理由，並在回覆裡講清楚是退場方案。
    reason = quote.slice(0, 60);
    confident = false;
    why = "模型整理失敗（" + String(e && e.message || e).slice(0, 60) +
          "），理由欄暫時放節錄原文，請自行修飾";
  }

  // 沒有數字的價位不是價位。與上游的 clean_price_field 同一條規則。
  if (!price || !/\d/.test(price)) { price = "未說明"; }
  if (!reason) { reason = note || "人工補登"; }

  /* ---- 寫入 ---- */
  var vid = MANUAL_ENTRY_PREFIX + d.replace(/\//g, "");

  withLock_(function () {
    // 覆蓋時先刪掉同一天這一檔的舊列。只刪這一檔，不動當天其他資料。
    if (payload.replace && dup.length) {
      var byS = { "操作紀錄": [], "會員持股": [] };
      dup.forEach(function (x) { byS[x.sheet].push(x.row); });
      ["操作紀錄", "會員持股"].forEach(function (sn) {
        if (!byS[sn].length) { return; }
        var sh0 = getSheet_(sn);
        byS[sn].sort(function (a, b) { return b - a; })
               .forEach(function (rn) { sh0.deleteRow(rn); });
      });
    }

    if (sheetName === "會員持股") {
      var hs = getSheet_("會員持股");
      hs.getRange(hs.getLastRow() + 1, 1, 1, 6)
        .setValues([[d, name, code, "持有", reason, vid]]);
    } else {
      var ts = getSheet_("操作紀錄");
      ts.getRange(ts.getLastRow() + 1, 1, 1, 7)
        .setValues([[d, name, code, cls, price, reason, vid]]);
    }

    var ms = getSheet_(MANUAL_SHEET);
    ms.getRange(ms.getLastRow() + 1, 1, 1, 9).setValues([[
      nowStamp_(), d, name, code, cls, price, reason,
      quote.slice(0, 4000), confident ? "已寫入" : "已寫入（模型沒把握）"
    ]]);
  });

  CACHE.remove("tracker");
  Logger.log("人工補登　" + d + "　" + name + "（" + code + "）" + cls +
             "　" + price + "　" + reason);

  return { ok: true, sync: queueDayEditSync_(d), date: d, name: name, code: code, cls: cls,
           price: price, reason: reason, sheet: sheetName,
           confident: confident, why: why,
           message: d + " 的 " + name + "（" + code + "）已寫進" + sheetName + "。" };
}

/* ==================================================================== *
 * 內容複審：這一筆到底該不該進表格
 *
 * 為什麼需要
 * ----------
 * 擷取與稽核都只回答「有沒有提到這一檔」，不回答「這一次提到該不該記」。
 * 但一小時的直播裡有大量內容是拿以前的單子講道理：
 *
 *   漢唐　　「我跌停我有賣嗎？沒有啊。我是等他上來到黑K棒上緣才賣的」
 *   索羅門　「這支股票我已經沒了，我都賺錢賣」
 *
 * 那是教學，是已經完結的舊事，既不是今天的動作也不是現在的立場。
 * 這種內容進了表格，網站會顯示他在操作一檔早就出清的股票，
 * 持股追蹤還會據此開一個不存在的回合，而且錯得很難察覺。
 *
 * 另一個同樣常見的錯誤是把「講到買賣兩個字」當成「今天執行了買賣」。
 * 買入與賣出是持股追蹤唯一的開倉與平倉依據，判錯的代價比觀望類大得多，
 * 所以這一關對買賣要求特別嚴：逐字稿看不到今天執行，就退回觀望兩類。
 *
 * 掛錯公司
 * --------
 * 第三種錯是整列掛在錯的公司身上。上游的代號比對有一條硬規則：
 * 代號格式合法就直接採用。那條規則本身是對的，但它假設了代號與名稱
 * 指的是同一檔，而語音轉文字會把兩者都聽錯。實際發生過：
 *
 *   逐字稿　「隱藏版光訊叫做 2402 的錩新……錩新今天漲兩塊」
 *   表格裡　「毅嘉（2402）」
 *
 * 毅嘉這兩個字在整份逐字稿裡一次都沒出現過。這種錯每一欄都有值、
 * 股價與 K 線也都畫得出來，只是整列都是別人的資料，比空白難發現得多。
 *
 * 複審是唯一能回頭修它的地方，因為只有這裡同時看得到「表格上寫什麼」
 * 與「逐字稿實際講什麼」。判定改名稱之後，程式會拿模型回報的名稱去比對
 * 官方清單，名稱與代號一起改掉。
 *
 * 這件事只在「目前的名稱在整份逐字稿裡一次都沒出現」時才做——那是
 * 可以用程式驗證的硬條件，不必相信模型的判斷。名稱只要出現過一次，
 * 就算模型說要改也不動它。
 *
 * 安全設計
 * --------
 * 這一關會刪資料，而且會在無人看著的情況下跑過整段歷史，所以做了三層保護：
 *   1. 模型回 confident=false 的一律不動，維持原狀。
 *   2. 逐字稿裡找不到對應段落的，節錄直接寫「找不到」，模型會回 false。
 *   3. 單日刪除量超過七成時整批不刪，只記錄，避免模型異常時把一天清空。
 * 每一筆實際刪除都會寫進「修正建議」分頁留痕，看得到刪了什麼、為什麼刪。
 * ==================================================================== */

// 一次送多少筆給模型。太多會讓輸出撞上長度上限，20 筆的輸出約 1500 字。
var REVIEW_BATCH = 20;

// 單日刪除比例上限。超過就整批不刪，只寫紀錄。
var REVIEW_MAX_DROP_RATIO = 0.7;

/**
 * 從逐字稿裡取這一檔附近的節錄，最多兩段。
 * 找不到就回空字串，那會讓模型回 confident=false，這一筆就不會被動到。
 */
function reviewSnips_(v2, name, code) {
  // Full source is required: a holding/candidate may only be stated at the end.
  return String(v2 || '');
  /* legacy excerpting disabled
  var keys = [];
  if (name) { keys.push(name); }
  if (code && /^(?:00981A|\d{4,6})$/.test(String(code))) { keys.push(String(code)); }
  // 名稱可能在代號比對時被改過（加哲→嘉澤），逐字稿裡就找不到原字了。
  // 退一步用前兩個字找。再找不到就據實回報找不到，不要硬湊一段無關的話。
  if (name && name.length >= 3) { keys.push(name.slice(0, 2)); }

  for (var k = 0; k < keys.length; k++) {
    var out = [], from = 0;
    while (out.length < 2) {
      var at = v2.indexOf(keys[k], from);
      if (at < 0) { break; }
      out.push(v2.slice(Math.max(0, at - 350), at + 500).replace(/\s+/g, ' '));
      from = at + 500;
    }
    if (out.length) { return out.join('\n   ……\n   '); }
  }
  return ''; */
}

/** 收集某一天某張分頁的所有列。兩張分頁的欄位差異在這裡吸收掉。 */
function reviewCollect_(sheetName, d) {
  var out = [];
  var sh, vals;
  try { sh = getSheet_(sheetName); vals = sh.getDataRange().getValues(); }
  catch (e) { return out; }
  if (vals.length < 2) { return out; }

  var head = vals[0];
  var isTrade = sheetName === '操作紀錄';
  var cDate = head.indexOf('日期');
  var cName = head.indexOf('股票名稱');
  var cCode = head.indexOf('代號');
  var cDir = isTrade ? head.indexOf('方向') : head.indexOf('目前立場');
  var cPrice = isTrade ? head.indexOf('價位說明') : -1;
  var cReason = isTrade ? head.indexOf('理由摘錄') : head.indexOf('說明重點');
  var cVid = head.indexOf('來源影片ID');
  if (cDate < 0 || cName < 0 || cDir < 0) { return out; }

  for (var i = 1; i < vals.length; i++) {
    if (fmtDate_(vals[i][cDate]) !== d) { continue; }
    var nm = String(vals[i][cName] || '').trim();
    if (!nm) { continue; }
    /* 人工補登與會員簡訊的列都不交給複審。

       複審的判斷依據是「這一檔在逐字稿裡怎麼被講」，而這兩種來源的內容
       本來就不在逐字稿裡：人工補登存在的理由正是那一天講者刻意沒講股名；
       會員簡訊是盤中另外發出的即時指令，收盤後的逐字稿不會重複一次。
       兩者「找不到節錄」都是必然，不是異常。交給複審的話它只會一直回報
       找不到，每一次全面重整都重問一次、每一次都白花一次模型呼叫。

       更重要的是優先權方向相反。人工補登的前提是人比模型清楚；
       會員簡訊的優先權高於收盤後的逐字稿（它有明確價位與時間）。
       讓模型拿逐字稿去推翻這兩種來源，等於把正確的資料刪掉。 */
    if (cVid >= 0 && isNonTranscriptSource_(vals[i][cVid])) { continue; }
    out.push({
      sheet: sheetName,
      row: i + 1,
      dirCol: cDir + 1,
      nameCol: cName + 1,
      codeCol: cCode + 1,
      name: nm,
      code: String(vals[i][cCode] || '').trim(),
      cls: isTrade ? String(vals[i][cDir] || '') : '會員持股',
      price: cPrice >= 0 ? String(vals[i][cPrice] || '未說明') : '未說明',
      reason: String(vals[i][cReason] || ''),
      vid: cVid >= 0 ? String(vals[i][cVid] || '') : ''
    });
  }
  return out;
}

/** 把複審動過的那幾筆寫進「修正建議」留痕。純紀錄，不需要人核准。 */
function reviewTrail_(rows) {
  if (!rows.length) { return; }
  try {
    withLock_(function () {
      var sh = getSheet_('修正建議');
      sh.getRange(sh.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
    });
  } catch (e) { Logger.log('複審留痕失敗（不影響結果）：' + e); }
}

/**
 * 複審某一天的所有紀錄。
 * 回傳 { checked, changed, dropped, unsure, note }
 */
function reviewDayRecords_(dateStr) {
  var d = fmtDate_(dateStr);
  var stat = { checked: 0, changed: 0, renamed: 0, dropped: 0, unsure: 0, note: '' };
  if (!d) { stat.note = '日期不正確'; return stat; }

  // 逐字稿是唯一的判斷依據。沒有逐字稿就什麼都不動，
  // 否則等於讓模型憑空決定要刪哪一筆。
  var v2 = '', sources = {};
  try {
    readSheetObjects_('影片清單').forEach(function (r) {
      sources[String(r['影片ID'] || '')] = { date: fmtDate_(r['發布日期']), text: rawTranscript_(r) };
      if (fmtDate_(r['發布日期']) !== d) { return; }
      var t = rawTranscript_(r);
      if (t.length > v2.length) { v2 = t; }
    });
  } catch (e) { /* 讀不到就當作沒有逐字稿 */ }

  var items = reviewCollect_('操作紀錄', d).concat(reviewCollect_('會員持股', d));
  if (!items.length) { stat.note = '這一天沒有紀錄'; return stat; }

  // ---- 逐批送模型 ----
  var decisions = [];
  for (var b = 0; b < items.length; b += REVIEW_BATCH) {
    var batch = items.slice(b, b + REVIEW_BATCH);
    var usedSources = {};
    var payload = batch.map(function (x, n) {
      var source = sources[x.vid];
      if (!source || source.text.length < 300) { throw new Error('缺少來源原稿：' + x.vid); }
      usedSources[x.vid] = source;
      return (n + 1) + '. 股票：' + x.name + (x.code ? '（' + x.code + '）' : '') +
             '\n   來源影片ID：' + x.vid + '；來源影片日期：' + source.date + '；記錄日期：' + d +
             '\n   目前分類：' + x.cls +
             '\n   價位說明：' + x.price +
             '\n   理由摘錄：' + (x.reason || '（無）') +
             '\n   逐字稿：見下方同一來源ID完整原稿';
    }).join('\n\n');
    payload += '\n\n來源原稿：' + JSON.stringify(usedSources);

    var res;
    try {
      var raw = callGemini_(PIPE_REVIEW_SYSTEM, payload,
                            { maxOut: 4096, temperature: 0, json: true });
      res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim());
    } catch (e) {
      /* 當日額度用完要往外拋，讓上層停下整段重整。
         在這裡吞掉的話，外面看到的是「這一天複審中斷」，於是繼續跑下一天，
         而下一天必然也是同一個錯——一百多天就這樣一天一個錯地跑完，
         什麼都沒做成，額度也沒有恢復。 */
      if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
      // 其他失敗不該把資料弄壞，也不該讓整條流程掛掉。
      // 停在這一批，前面已經判好的照舊套用。
      throw new Error('內容複審中斷，未套用本日修改：' + String(e));
    }

    var seen = {};
    (res.results || []).forEach(function (r) {
      var n = Number(r.index) - 1;
      if (!(n >= 0 && n < batch.length)) { return; }
      if (seen[n]) { throw new Error('複審輸出重複編號'); }
      seen[n] = true;
      decisions.push({ it: batch[n], r: r });
    });
    if (Object.keys(seen).length !== batch.length) { throw new Error('複審漏回覆，未套用本日修改'); }
    stat.checked += batch.length;
  }

  // ---- 先算出要做什麼，全部確定之後才動資料 ----
  var VALID = ['買入', '賣出', '觀望不碰', '觀望注意', '會員持股'];
  var drops = [], edits = [], moves = [], renames = [], trail = [];
  var now = new Date();
  var codeMap = null;   // 要改名稱時才載入，平常不付這個成本

  decisions.forEach(function (dz) {
    var it = dz.it, r = dz.r;
    if (r.confident !== true || !validEvidence_(r.evidence, sources[it.vid].text)) { stat.unsure++; return; }

    var verdict = String(r.verdict || '').trim();
    var why = String(r.why || '').slice(0, 60);

    if (verdict === '刪除') {
      drops.push(it);
      trail.push([now, d, '刪除', it.sheet, it.name, it.code,
                  '內容複審：' + (why || '非當下態度'), '已套用（複審）', now]);
      return;
    }

    if (verdict === '改名稱') {
      var heard = String(r.stock_name || '').trim();
      if (!heard || heard === it.name) { return; }

      /* 硬條件：目前這個名稱在整份逐字稿裡一次都沒出現過。

         這一條不是防呆，是這件事唯一可靠的依據。模型說「掛錯公司了」是
         判斷，會錯；「毅嘉這兩個字在兩萬字裡出現零次」是事實，不會錯。
         只認事實，模型的判斷只用來提供候選名稱。

         名稱有出現過就一定不改，即使模型很有把握——那種情形多半是
         同一段話提到了兩檔，而它挑了另一檔。 */
      if (sources[it.vid].text.indexOf(it.name) >= 0) {
        Logger.log('　複審想改名稱但不採納　' + d + '　' + it.name +
                   ' → ' + heard + '（' + it.name + ' 在逐字稿裡出現過，不動）');
        return;
      }
      // 模型講的那個名稱必須真的在逐字稿裡，否則它是憑空想出來的。
      if (sources[it.vid].text.indexOf(heard) < 0) {
        Logger.log('　複審想改名稱但不採納　' + d + '　' + it.name +
                   ' → ' + heard + '（' + heard + ' 也不在逐字稿裡）');
        return;
      }

      if (!codeMap) { codeMap = loadCodeMap_(); }
      var rr = pipeResolveName_(heard, codeMap);
      if (rr.reject) {
        Logger.log('　複審想改名稱但不採納　' + d + '　' + heard +
                   ' 判定不是個股（' + rr.how + '）');
        return;
      }
      // 比不出代號時仍然改名稱，代號留空給後續的代號比對去補。
      // 名稱錯的傷害比代號空白大：代號空白看得出來，名稱錯看不出來。
      renames.push({ it: it, name: rr.name || heard, code: rr.code || '',
                     why: why, how: rr.how });
      return;
    }

    if (verdict !== '改分類') { return; }

    var dir = String(r.direction || '').trim();
    if (VALID.indexOf(dir) < 0 || dir === it.cls) { return; }

    if (it.sheet === '操作紀錄' && dir !== '會員持股') {
      edits.push({ it: it, dir: dir, why: why });
    } else {
      moves.push({ it: it, dir: dir, why: why });
    }
  });

  // 單日刪太多就整批不刪。模型異常時這一層能把損害擋在門外。
  // 門檻放在 3 筆，是因為「整天被清空」才是最需要擋的情況，而那種天數往往不多。
  if (drops.length && items.length >= 3 &&
      drops.length / items.length > REVIEW_MAX_DROP_RATIO) {
    Logger.log('　複審異常：' + d + ' 判定刪除 ' + drops.length + '/' + items.length +
               ' 筆，超過安全上限，本日整批不刪，只留紀錄。');
    stat.note = (stat.note ? stat.note + '；' : '') +
                '判定刪除 ' + drops.length + '/' + items.length + ' 筆超過安全上限，未執行刪除';
    trail.forEach(function (t) {
      t[6] = '（超過單日刪除上限，未執行）' + t[6];
      t[7] = '待確認';
    });
    reviewTrail_(trail);
    drops = []; trail = [];
  }

  if (stat.unsure) { throw new Error('內容複審有 ' + stat.unsure + ' 筆證據不足，未套用本日修改'); }
  // ---- 動資料 ----
  // 順序有意義：先改儲存格（用的是原始列號），最後才刪列。
  // 刪列會讓後面的列往上移，先刪就會改到別人。
  edits.forEach(function (e) {
    getSheet_('操作紀錄').getRange(e.it.row, e.it.dirCol).setValue(e.dir);
    stat.changed++;
    Logger.log('　複審改分類　' + d + '　' + e.it.name + '　' + e.it.cls + ' → ' + e.dir +
               (e.why ? '（' + e.why + '）' : ''));
  });

  renames.forEach(function (m) {
    var sh = getSheet_(m.it.sheet);
    sh.getRange(m.it.row, m.it.nameCol).setValue(m.name);
    if (m.it.codeCol > 0) { sh.getRange(m.it.row, m.it.codeCol).setValue(m.code); }
    stat.renamed++;
    Logger.log('　複審改名稱　' + d + '　' + m.it.name + '（' + m.it.code + '）→ ' +
               m.name + '（' + (m.code || '代號待補') + '）　' + m.how +
               (m.why ? '　' + m.why : ''));
    trail.push([now, d, '改名稱', m.it.sheet, m.it.name, m.it.code,
                '內容複審：逐字稿裡沒有這個名稱，實際講的是 ' + m.name +
                '（' + (m.code || '代號待補') + '）',
                '已套用（複審）', now]);
  });

  var addTrade = [], addHold = [];
  moves.forEach(function (m) {
    var it = m.it;
    if (m.dir === '會員持股') {
      addHold.push([d, it.name, it.code, '持有',
                    (m.why || it.reason || '會員持股').slice(0, 60), it.vid]);
    } else {
      addTrade.push([d, it.name, it.code, m.dir, it.price,
                     (m.why || it.reason || '未說明').slice(0, 60), it.vid]);
    }
    stat.changed++;
    Logger.log('　複審換表　' + d + '　' + it.name + '　' + it.cls + ' → ' + m.dir);
  });

  var delRows = { '操作紀錄': [], '會員持股': [] };
  drops.forEach(function (it) {
    delRows[it.sheet].push(it.row);
    stat.dropped++;
    Logger.log('　複審刪除　' + d + '　' + it.name + '（' + it.cls + '）');
  });
  moves.forEach(function (m) { delRows[m.it.sheet].push(m.it.row); });

  withLock_(function () {
    ['操作紀錄', '會員持股'].forEach(function (sn) {
      if (!delRows[sn].length) { return; }
      var sh = getSheet_(sn);
      delRows[sn].sort(function (a, b) { return b - a; })
                 .forEach(function (rn) { sh.deleteRow(rn); });
    });
    if (addTrade.length) {
      var ts = getSheet_('操作紀錄');
      ts.getRange(ts.getLastRow() + 1, 1, addTrade.length, 7).setValues(addTrade);
    }
    if (addHold.length) {
      var hs = getSheet_('會員持股');
      hs.getRange(hs.getLastRow() + 1, 1, addHold.length, 6).setValues(addHold);
    }
  });

  reviewTrail_(trail);
  if (stat.changed || stat.dropped || stat.renamed) { CACHE.remove('tracker'); }

  Logger.log('內容複審 ' + d + '：檢視 ' + stat.checked + '、改分類 ' + stat.changed +
             '、改名稱 ' + stat.renamed +
             '、刪除 ' + stat.dropped + '、沒把握不動 ' + stat.unsure +
             (stat.note ? '（' + stat.note + '）' : ''));
  return stat;
}

/**
 * 複審單日。給後台或編輯器手動呼叫，不帶日期就是今天。
 */
function apiAdminReview(key, dateStr) {
  try {
    adminAuth_(key);
    var d = fmtDate_(dateStr) || todayStr_();
    var r = reviewDayRecords_(d);
    return { ok: true, stat: r,
             message: d + ' 複審完成：檢視 ' + r.checked + '、改分類 ' + r.changed +
                      '、刪除 ' + r.dropped + '、沒把握不動 ' + r.unsure +
                      (r.note ? '（' + r.note + '）' : '') };
  } catch (e) {
    var m = String(e && e.message || e);
    return { ok: false, reason: m, advice: troubleshoot_(m) };
  }
}

/** 編輯器裡直接跑某一天的複審，不必帶密鑰。 */
function reviewDay(dateStr) {
  return reviewDayRecords_(fmtDate_(dateStr) || todayStr_());
}


/**
 * 全面重整：一次把過去所有資料套用最新規則，跑完網站與郵件都會是新的。
 *
 * 這是「規則改了，想讓歷史資料一起變好」時該按的那一個按鈕。
 * 它把原本要分開按的幾件事串成一條龍，並沿用同一套分段機制，
 * 所以不會超時，也可以關掉頁面回來再看。
 *
 * 順序有意義：
 *   1. 名稱與代號　先把題材、族群、外國股票剔除，同音錯字補上代號。
 *      這一步要最先做，後面每一步都依賴代號是對的。
 *   2. 稽核與複審　把每日品質關卡整套跑過歷史上的每一天。
 *      先稽核補漏：當初漏掉的個股補回來，補之前先過漏抓判定，
 *      集團順帶點名、族群舉例、對照比喻、回顧舊單都不收。
 *      再內容複審：誤收的整列刪掉，買賣看不到今天執行就退回觀望類。
 *      一加一減都做才算真的套用新規則，只做一半等於只修一半。
 *      排在代號之後，是因為名稱要先正確才找得到逐字稿裡的段落。
 *   3. 價位校對　　修正方向矛盾、非股價數字、概數。
 *   4. 補齊日K　　沒有 K 線就算不出進場價與報酬，必須在重算之前。
 *   5. 重算追蹤　　回合、進場價、累積報酬全部依新規則重來。
 *   6. 重寫郵件　　被改過的那幾天加上最近幾天，信件內容跟著更新，與網站對齊。
 */
var FULLFIX_KEY = 'fullFixState';

// 「重寫說明」排在重寫郵件之前，因為信件是從這些說明產生的——
// 順序反過來的話，信裡還是舊的口語句子，網站卻已經是研究報告的寫法，
// 兩邊對不起來而且要等到下一次重整才會修好。
/* 全面重整的步驟。

   「合併重複」是後來加的：同一天同一檔在同一類裡出現兩列，
   規則改好之後只對新資料有效，過去幾個月的重複列要有東西去清。
   它不呼叫模型，判斷依據是「同一天、同一檔、同一類」，那是事實不是判斷，
   所以很快、也不花任何額度。

   排在稽核與複審之後：稽核會補進新的列，補完才知道有沒有跟既有的撞在一起。 */
var FULLFIX_STEPS = ['名稱與代號', '稽核補漏', '內容複審', '合併重複', '價位校對',
                     '重寫說明', '補齊日K', '重算追蹤', '重寫郵件', '完成'];

/* ------------------------------------------------------------------ *
 * 重整範圍
 *
 * 為什麼需要
 * ----------
 * 全面重整一天要呼叫三到四次模型，一百多個交易日就是四五百次，
 * 免費層的每日額度撐不完，實際上要跨好幾天、按好幾次「接著跑」。
 * 但多數時候規則只改了一點，需要重跑的其實只有最近幾天——
 * 為了那幾天把一百多天全部重判一次，額度與時間都是白花的。
 *
 * 範圍怎麼算
 * ----------
 * 用「有紀錄的交易日」往回數，不是用日曆天往回推。
 * 連假、颱風假、他請假沒開播，這些日子都沒有紀錄；用日曆天往回推七天，
 * 遇到中秋連假就只剩三四天真的有東西，範圍會忽大忽小。
 * 用有紀錄的日子數，「近一周」永遠就是最近七個有內容的交易日。
 *
 * 哪幾步會被範圍限制
 * ------------------
 * 只有「按日期逐天處理」的那幾步：稽核與複審、價位校對、重寫郵件。
 * 另外三步不受限制，而且不該受限制：
 *   名稱與代號　只掃代號空白的列，本來就很少，也不呼叫模型（除非要同音判定）
 *   補齊日K　　 K 線是整段區間一起抓的，切一段沒有意義
 *   重算追蹤　　持有回合是從第一天算到今天的，只算最近七天會把回合切斷，
 *               算出來的報酬是錯的。這一步永遠必須看全部歷史。
 * ------------------------------------------------------------------ */

var FULLFIX_SCOPES = {
  week:    { label: '最近七日', days: 7 },
  '7days': { label: '最近七日', days: 7 },
  month:   { label: '近一個月', days: 22 },
  all:     { label: '全部', days: 0 }
};

function fullFixScope_(v) {
  var k = String(v || 'week').trim();
  if (k === '7days') { k = 'week'; }
  return FULLFIX_SCOPES[k] ? k : 'week';
}

/**
 * 這個範圍要從哪一天（含）開始。回空字串代表不設限。
 *
 * days 是「有紀錄的交易日」數量，不是日曆天。
 */
function fullFixSince_(scope, allDays) {
  var n = (FULLFIX_SCOPES[fullFixScope_(scope)] || {}).days || 0;
  if (!n) { return ''; }
  var sorted = (allDays || []).slice().sort();
  if (sorted.length <= n) { return ''; }
  return sorted[sorted.length - n];
}

/** 所有有紀錄的日期，由小到大。 */
function fullFixAllDays_() {
  var days = {};
  ['操作紀錄', '會員持股'].forEach(function (sn) {
    try {
      readSheetObjects_(sn).forEach(function (r) {
        var d = fmtDate_(r['日期']);
        if (d) { days[d] = 1; }
      });
    } catch (e) { /* 分頁不存在就跳過 */ }
  });
  return Object.keys(days).sort();
}

function fullFixState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(FULLFIX_KEY);
  if (!raw) { return null; }
  try { return JSON.parse(raw); } catch (e) { return null; }
}
function setFullFixState_(st) {
  // 指令碼屬性單一值有 9KB 上限，超過就寫不進去而拋錯，
  // 而這支是在每一天處理完之後呼叫的——寫不進去等於進度全丟，
  // 看門狗會從頭再跑一次，一百多天的模型額度就白花了。
  //
  // 會長大的只有三樣：日期清單、變動日清單、執行紀錄。
  // 日期清單用完就設成 null，執行紀錄只留最後 40 則（前面的已經印進 Logger）。
  if (st && st.log && st.log.length > 40) {
    st.log = st.log.slice(-40);
  }
  PropertiesService.getScriptProperties().setProperty(FULLFIX_KEY, JSON.stringify(st || {}));
}
/* 由 GitHub 逐棒呼叫時設成 true。
   那條路每一棒都是一次獨立的網頁請求，各自享有完整的執行額度，
   下一棒由 GitHub 決定什麼時候打，所以不需要、也不該建觸發器。 */
var FULLFIX_HTTP_ = false;

function scheduleFullFix_() {
  if (FULLFIX_HTTP_) { return; }
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runFullFix_') { ScriptApp.deleteTrigger(t); }
  });
  ScriptApp.newTrigger('runFullFix_').timeBased().after(2000).create();
}

/**
 * 做一棒全面重整，然後把目前狀態回報給呼叫端。給 GitHub 那條路用。
 *
 * 與觸發器那條路跑的是同一支 runFullFix_，差別只在不建下一棒的觸發器——
 * 兩條路走同一段程式，結果就不可能不一致。
 *
 * 沒有進行中的重整時會自己開一個；上次做到一半失敗的則接著做，不從頭來過。
 */
function runFullFixChunk_() {
  var st = fullFixState_();
  // 被取消就立刻收工。GitHub 那邊的迴圈看到 done 就停，
  // 不必去 Actions 按停止，也不會再多打一棒。
  if (st && st.status === '已取消') {
    return { done: true, processed: Number(st.index) || 0,
             total: Number(st.total) || (FULLFIX_STEPS.length - 1),
             note: '已被取消，停在「' + st.step + '」' };
  }
  var fresh = !st || st.status === '完成' ||
              (Number(st.index) || 0) >= FULLFIX_STEPS.length - 1;
  if (fresh) {
    st = { status: '處理中', step: FULLFIX_STEPS[0], index: 0,
           total: FULLFIX_STEPS.length - 1, done: 0, log: [],
           startedAt: nowStamp_(), updatedAt: nowStamp_() };
  } else {
    st.status = '處理中';   // 上次沒做完的，接著做
  }
  setFullFixState_(st);

  /* HTTP 那條路的單棒預算要比觸發器那條路短。

     觸發器被時間上限砍掉只是那一棒沒做完，看門狗會接回去；
     網頁請求被砍掉則是連線就那樣掛著，呼叫端要等到自己的讀取逾時才知道，
     而那段等待完全是白等的。所以這裡把牆往前挪，寧可早一點收手交棒。 */
  FULLFIX_HTTP_ = true;
  var savedBudget = STEP_BUDGET_MS;
  STEP_BUDGET_MS = 3.2 * 60 * 1000;
  try { runFullFix_(); }
  finally { FULLFIX_HTTP_ = false; STEP_BUDGET_MS = savedBudget; }

  var now = fullFixState_() || {};
  var idx = Number(now.index) || 0;
  var tot = Number(now.total) || (FULLFIX_STEPS.length - 1);
  var sub = (now.sub && Number(now.sub.total) > 0)
            ? '　' + now.sub.done + '/' + now.sub.total : '';
  return {
    done: now.status !== '處理中',
    processed: idx, total: tot,
    note: now.status === '完成' ? '全部完成'
        : now.status === '暫停' ? 'QUOTA_PAUSED　Gemini 今日額度已用完，進度已保留'
        : now.status === '已取消' ? '已被取消'
        : now.status === '失敗' ? ('中止：' + String(now.lastError || '').slice(0, 120))
        : (now.step || '') + sub
  };
}

/**
 * 把一次全面重整送出去跑。
 *
 * 優先交給 GitHub Actions：它會逐棒打 ?action=refresh&step=fullfix，
 * 每一棒都是一次獨立的網頁請求，各自享有完整的執行額度，一個觸發器都不用，
 * 而且每一行輸出都留在 Actions 日誌裡。
 *
 * 觸發器那條路留著當退路：沒設 GITHUB_REPO / GITHUB_TOKEN，或派工失敗時走它。
 * 先前只有這一條路，而觸發器額度滿的時候它會直接失敗，於是按鈕按下去只跳
 * 「這個指令碼包含過多觸發條件」，沒有第二條路可走。
 */
function startFullFixRun_() {
  function mark_(where) {
    // 記下是誰在驅動。看門狗靠這個判斷該不該接手：
    // GitHub 驅動的斷掉要人按「接著跑」，不能讓看門狗默默搬到 Apps Script。
    var st = fullFixState_() || {};
    st.where = where;
    setFullFixState_(st);
  }

  /* 範圍也一起派過去。

     真正決定範圍的是 Apps Script 的狀態（GitHub 只是逐棒敲門，
     每一棒做什麼是這邊決定的），所以這個輸入對執行結果沒有影響。
     送它過去純粹是為了讓 Actions 的執行清單上看得出這一輪跑的是
     近一周還是全部歷史——兩者的耗時差十倍，事後回頭看時分不出來很麻煩。 */
  var scope = fullFixScope_((fullFixState_() || {}).scope);
  var d = dispatchGithub_({ full_fix: 'true', full_fix_scope: scope });
  if (d.ok) {
    mark_('github');
    return { ok: true, where: 'github', runUrl: d.runUrl, scope: scope,
             message: '已交給 GitHub Actions 逐棒執行（範圍：' +
                      FULLFIX_SCOPES[scope].label + '）。' };
  }

  Logger.log('派工給 GitHub 失敗，改用 Apps Script 觸發器接力：' + d.reason);
  try {
    scheduleFullFix_();
  } catch (e2) {
    var st = fullFixState_() || {};
    st.status = '失敗';
    st.lastError = '排不出觸發器：' + String(e2 && e2.message || e2).slice(0, 160);
    setFullFixState_(st);
    return { ok: false,
             reason: 'GitHub 派工失敗（' + d.reason.slice(0, 80) + '），改用觸發器也失敗（' +
                     String(e2 && e2.message || e2).slice(0, 100) +
                     '）。請先設定 GITHUB_REPO 與 GITHUB_TOKEN，' +
                     '或執行 cleanupAllTriggers() 清掉孤兒觸發器後重試。' };
  }
  mark_('appsscript');
  return { ok: true, where: 'appsscript',
           message: '沒有 GitHub 派工設定（' + d.reason.slice(0, 50) +
                    '），改由 Apps Script 在背景分段執行。' };
}

/**
 * 從頭開始一次全面重整。
 *
 * scope：week（近一周）／month（近一個月）／all（全部歷史）。
 * 沒給就是 all，與加這個參數之前的行為相同。
 */
function apiAdminStartFullFix(key, scope) {
  try {
    adminAuth_(key);
    var cur = fullFixState_();
    if (cur && cur.status === '處理中') {
      return { ok: false, reason: '全面重整已在進行中（' + cur.step + '）。' };
    }
    var sc = fullFixScope_(scope);
    var all = fullFixAllDays_();
    var since = fullFixSince_(sc, all);
    var nDays = since ? all.filter(function (d) { return d >= since; }).length : all.length;

    setFullFixState_({ status: '處理中', step: FULLFIX_STEPS[0], index: 0,
                       total: FULLFIX_STEPS.length - 1, done: 0, log: [],
                       scope: sc,
                       startedAt: nowStamp_(), updatedAt: nowStamp_() });
    var r = startFullFixRun_();
    if (r.ok) {
      r.scope = sc;
      r.days = nDays;
      r.message = FULLFIX_SCOPES[sc].label + '：要處理 ' + nDays + ' 個交易日' +
                  (since ? '（' + since + ' 起）' : '') + '。' + r.message;
    }
    return r;
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 從停住的地方接著跑。
 *
 * 已完成的步驟不會重來：st.index 指著下一步，而「稽核與複審」的日期游標
 * 與「重寫郵件」的待寫清單也都存在狀態裡，所以連做到一半的那一步都是接著做，
 * 不是整步重來。額度用完隔天要繼續、或某一步失敗修好之後要繼續，都按這個。
 */
function apiAdminResumeFullFix(key) {
  try {
    adminAuth_(key);
    var st = fullFixState_();
    if (!st) { return { ok: false, reason: '沒有可以接續的重整紀錄，請按「開始全面重整」。' }; }
    if (st.status === '處理中') { return { ok: false, reason: '正在跑了（' + st.step + '）。' }; }
    if (st.status === '完成') { return { ok: false, reason: '上一次已經全部跑完了。' }; }

    var from = FULLFIX_STEPS[Number(st.index) || 0];
    st.status = '處理中';
    st.lastError = '';
    st.updatedAt = nowStamp_();
    setFullFixState_(st);

    var r = startFullFixRun_();
    if (r.ok) { r.message = '從「' + from + '」接著跑。' + r.message; }
    return r;
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 只重跑指定的那一步。
 *
 * 用在「某一步失敗、修好之後只想重跑它」。跳到那一步之後會照順序把後面的
 * 也跑完，因為後面幾步依賴前面的結果（代號沒補好，追蹤就算不準）。
 * 想只跑一步就跑完，跑完那一步之後按「取消」即可。
 */
function apiAdminRetryFullFixStep(key, index) {
  try {
    adminAuth_(key);
    var i = Number(index);
    if (!(i >= 0 && i < FULLFIX_STEPS.length - 1)) {
      return { ok: false, reason: '步驟編號不正確。' };
    }
    var st = fullFixState_();
    if (st && st.status === '處理中') {
      return { ok: false, reason: '正在跑了（' + st.step + '）。要換步驟請先按「取消」。' };
    }
    st = st || { log: [] };
    st.status = '處理中';
    st.index = i;
    st.done = i;
    st.step = FULLFIX_STEPS[i];
    st.total = FULLFIX_STEPS.length - 1;
    st.lastError = '';
    st.log = (st.log || []).concat(['— 從「' + FULLFIX_STEPS[i] + '」單獨重跑 —']);
    // 重跑某一步就把那一步自己的游標清掉，否則它會以為上次做到一半、跳過前面的日期。
    // 清掉日期清單讓它依目前的範圍重建。st.scope 留著不動，
    // 所以單獨重跑某一步用的是當初選的範圍，不會突然變成全部歷史。
    // 這兩步共用同一份日期清單與同一組統計，重跑任一步都要整個重建
    if (FULLFIX_STEPS[i] === '稽核補漏' || FULLFIX_STEPS[i] === '內容複審') {
      st.reviewDays = null;
    }
    if (FULLFIX_STEPS[i] === '重寫郵件') { st.mailQueue = null; }
    if (FULLFIX_STEPS[i] === '重寫說明') { st.rewriteDone = null; }
    st.updatedAt = nowStamp_();
    setFullFixState_(st);

    var r = startFullFixRun_();
    if (r.ok) { r.message = '從「' + FULLFIX_STEPS[i] + '」重跑。' + r.message; }
    return r;
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 取消全面重整。
 *
 * 已完成的步驟保留著，游標也留著，所以之後按「接著跑」會從停住的地方繼續。
 * GitHub 那邊的迴圈下一棒問到「已取消」就會自己收工，不必去 Actions 按停止。
 */
function apiAdminCancelFullFix(key) {
  try {
    adminAuth_(key);
    var st = fullFixState_();
    if (!st || st.status !== '處理中') {
      return { ok: false, reason: '目前沒有進行中的全面重整。' };
    }
    st.status = '已取消';
    st.updatedAt = nowStamp_();
    st.log = (st.log || []).concat(['— 手動取消，停在「' + st.step + '」 —']);
    setFullFixState_(st);

    // 觸發器那條路要把已經排好的下一棒刪掉，否則它還會醒來一次。
    try {
      ScriptApp.getProjectTriggers().forEach(function (t) {
        if (t.getHandlerFunction() === 'runFullFix_') { ScriptApp.deleteTrigger(t); }
      });
    } catch (e2) { Logger.log('清除全面重整觸發器失敗：' + e2); }

    return { ok: true,
             message: '已取消，停在「' + st.step + '」。已完成的步驟與進度都保留著，' +
                      '按「接著跑」會從這裡繼續。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminFullFixState(key) {
  try {
    adminAuth_(key);
    var st = fullFixState_();
    if (st) {
      st.note = (st.log || []).join('\n');
      // 進度百分比。大步驟之間再按小步驟內插（例如複審跑到第幾天），
      // 這樣長步驟進行中進度條也會動，不會停在同一格讓人以為當掉了。
      var total = Number(st.total) || (FULLFIX_STEPS.length - 1);
      var frac = (st.sub && Number(st.sub.total) > 0)
        ? Math.min(1, Number(st.sub.done) / Number(st.sub.total)) : 0;
      st.pct = Math.min(100, Math.round(((Number(st.done) || 0) + frac) / total * 100));
      if (st.status === '完成') { st.pct = 100; }
      /* 可以從哪裡接下去。前端拿它來做兩件事：
         在按「開始全面重整」時警告會從頭重跑，以及在進度區寫清楚接續點。
         沒有這一段的話，明天回來看到的只有「暫停」兩個字，
         很容易就按了「開始」而不是「接著跑」，前面幾十天全部重做一次。 */
      if (st.status !== '完成' && Number(st.index) >= 0) {
        st.resumeAt = FULLFIX_STEPS[Number(st.index) || 0] || '';
        st.scopeLabel = (FULLFIX_SCOPES[fullFixScope_(st.scope)] || {}).label || '';
        if (st.sub && Number(st.sub.total) > 0) {
          st.resumeAt += '　第 ' + st.sub.done + '/' + st.sub.total + ' 天';
        }
      }
      if (st.status === '失敗' && st.lastError) { st.advice = troubleshoot_(st.lastError); }
    }
    return { ok: true, state: st };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 稽核與複審這一步的實作：把每日品質關卡整套跑過歷史上的每一天。
 *
 * 為什麼一天要跑兩段而不是只跑複審
 * --------------------------------
 * 新規則同時改了兩個方向，只做一半等於只修一半：
 *   稽核補漏會「加」——舊資料當初漏掉的個股，現在補得回來。
 *   內容複審會「減」——當初誤收的回顧舊單、未執行的買賣，現在拿掉。
 * 只跑複審，過去漏掉的永遠漏著；只跑稽核，過去誤收的永遠留著。
 *
 * 順序也有意義：稽核先跑，複審後跑。這樣稽核剛補進來的那幾筆，
 * 會在同一天的複審裡再被看一次，等於自動有了第二道確認。
 * 反過來先複審再稽核，補進來的就沒人檢查了。
 *
 * 為什麼要記下「哪幾天真的變了」
 * ------------------------------
 * 郵件內容是撰稿當下產生並存起來的一份文字，不會跟著試算表變。
 * 網站的郵件查詢分頁讀的就是那一份，所以只要某一天的紀錄被改過，
 * 那一天的信與網站就對不起來了。後面「重寫郵件」那一步會用這份清單，
 * 只重寫真的變過的那幾天——不是全部重寫（浪費），也不是只寫最近七天（會漏）。
 */
/* 要處理哪幾天。兩段共用同一份清單與同一組統計，只建一次。 */
function fullFixDays_(st) {
  if (st.reviewDays) { return st.reviewDays; }

  var days = {};
  ['操作紀錄', '會員持股'].forEach(function (sn) {
    try {
      readSheetObjects_(sn).forEach(function (r) {
        var d = fmtDate_(r['日期']);
        if (d) { days[d] = 1; }
      });
    } catch (e) { /* 分頁不存在就跳過 */ }
  });
  var allDays = Object.keys(days).sort();

  // 範圍只切按日期逐天處理的那幾步；補齊日K與重算追蹤永遠看全部。
  var since = fullFixSince_(st.scope, allDays);
  st.reviewDays = since
    ? allDays.filter(function (d) { return d >= since; })
    : allDays;
  st.reviewTotal = st.reviewDays.length;
  st.auditIdx = 0;
  st.reviewIdx = 0;
  st.reviewStat = { added: 0, skipped: 0, changed: 0, renamed: 0, dropped: 0, unsure: 0 };
  st.changedDays = [];

  Logger.log('逐日處理：共 ' + st.reviewTotal + ' 個交易日' +
             (st.reviewTotal < allDays.length
               ? '（範圍：' + (FULLFIX_SCOPES[fullFixScope_(st.scope)] || {}).label +
                 '，全部歷史共 ' + allDays.length + ' 天）'
               : '（全部歷史）'));
  return st.reviewDays;
}

/** 這一天被動過就記下來，後面的重寫郵件只重寫這幾天。
 *
 * changedDays 是在 fullFixDays_ 裡建起來的，而那一支只有走到「稽核補漏」
 * 才會呼叫；從「合併重複」單獨重跑時它是 undefined，而重寫郵件那一步
 * 又會把它設成 null。少了這兩行，那兩種情況下記一天就會拋例外，
 * 整個重整停在半路。 */
function markChangedDay_(st, d) {
  if (!st) { return; }
  if (!st.changedDays) { st.changedDays = []; }
  if (st.changedDays.indexOf(d) < 0) { st.changedDays.push(d); }
}


/* 第一段：稽核補漏。
 *
 * 逐日對照逐字稿，把當初漏掉的個股補回來。補之前先過漏抓判定——
 * 集團順帶點名、族群舉例、對照比喻、回顧舊單都不收。
 *
 * 與內容複審拆成兩段，是因為擠在一格的時候，那一格會亮很久而畫面上
 * 只看得到「稽核與複審　第 12 天」——分不出它在補漏還是在複審。
 * 拆開不多花任何一次模型呼叫：本來每一天就是各叫一次。
 */
function auditAllPhase_(st, deadline) {
  var days = fullFixDays_(st);

  while (st.auditIdx < days.length) {
    // 一天最多兩次模型呼叫（稽核、漏抓判定），最慢約 50 秒。
    // 留 70 秒緩衝，寧可早一點交棒，也不要在半途被時間上限腰斬——
    // 那會讓游標沒寫回去，同一天被重跑一次，白花一次額度。
    if (timeLeft_(deadline) < 70000) { return false; }

    var d = days[st.auditIdx];
    try {
      var a = autoFixAuditMissing(d);
      st.reviewStat.added += (Number(a.added) || 0);
      st.reviewStat.skipped += (Number(a.skipped) || 0);
      if (Number(a.added) || 0) { markChangedDay_(st, d); }
    } catch (e) {
      // 當日額度用完就整個停下來。繼續跑只會每一天都失敗一次，
      // 把 GitHub 那個 job 的時間耗光，而且什麼都沒做成。
      if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
      Logger.log('稽核 ' + d + ' 失敗（跳過這一天）：' + String(e).slice(0, 120));
    }

    st.auditIdx++;
    st.step = '稽核補漏　' + st.auditIdx + '/' + st.reviewTotal + ' 天（' + d + '）';
    st.sub = { done: st.auditIdx, total: st.reviewTotal, label: d };
    st.updatedAt = nowStamp_();
    setFullFixState_(st);
  }

  st.sub = null;
  return true;
}


/* 第二段：內容複審。
 *
 * 逐日回頭看那一天已經收進表格的每一列，判斷分類對不對、名稱對不對、
 * 有沒有根本不該收的。排在稽核補漏之後，所以它看得到補進來的那幾列。
 */
function reviewAllPhase_(st, deadline) {
  var days = fullFixDays_(st);

  while (st.reviewIdx < days.length) {
    // 一天最多兩批複審，最慢約 60 秒。留 80 秒緩衝。
    if (timeLeft_(deadline) < 80000) { return false; }

    var d = days[st.reviewIdx];
    try {
      var rv = reviewDayRecords_(d);
      st.reviewStat.changed += (Number(rv.changed) || 0);
      st.reviewStat.renamed += (Number(rv.renamed) || 0);
      st.reviewStat.dropped += (Number(rv.dropped) || 0);
      st.reviewStat.unsure += (Number(rv.unsure) || 0);
      // 改名稱也算「這一天被動過」。少算的話那一天不會進 changedDays，
      // 後面的重寫郵件就不會重寫它——名稱在網站上改好了，信裡卻還是舊的。
      if ((Number(rv.changed) || 0) + (Number(rv.dropped) || 0) +
          (Number(rv.renamed) || 0)) {
        markChangedDay_(st, d);
      }
    } catch (e) {
      if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
      // 某一天失敗不影響其他天（額度用完除外，那個上面已經丟出去了）。
      Logger.log('複審 ' + d + ' 失敗（跳過這一天）：' + String(e).slice(0, 120));
    }

    st.reviewIdx++;
    st.step = '內容複審　' + st.reviewIdx + '/' + st.reviewTotal + ' 天（' + d + '）';
    st.sub = { done: st.reviewIdx, total: st.reviewTotal, label: d };
    st.updatedAt = nowStamp_();
    setFullFixState_(st);
  }

  st.sub = null;
  return true;
}


/* ------------------------------------------------------------------ *
 * 合併重複（可回溯）
 *
 * 症狀：觀望注意那張表裡，陽明（2609）連續出現兩列——一列寫「股價在震盪後
 * 有機會飆漲」，另一列寫「大戶持股比重持續增加，待航運股整理結束後可留意」。
 * 兩列都是真的，他確實在節目的不同段落各講了一次；擷取時當成兩件事，
 * 到了表格上就變成同一檔出現兩次，看起來像資料壞掉。
 *
 * 「他那一天對這一檔的立場」只有一個，所以觀望與持股本來就不該有第二列。
 * 兩次說明是同一個立場的兩個理由，該併成一句。
 *
 * 買賣不一樣：同一天分批買、分批賣是真的會發生，兩列各有各的價位，
 * 合併會把「他做了兩次」抹平成一次。所以買賣只在價位說明也一模一樣時才併——
 * 那種情況合理的解釋只剩重複擷取。
 *
 * 這一步完全不呼叫模型：判斷依據是「同一天、同一檔、同一類」，那是事實。
 * 所以它很快，而且不花任何額度。
 * ------------------------------------------------------------------ */

var MERGE_BLANK_ = ['', '未說明', '本支影片未說明'];

/** 兩段說明併成一段。互相包含就留長的，否則接起來。 */
function mergeText_(a, b) {
  a = String(a || '').trim();
  b = String(b || '').trim();
  if (!a || MERGE_BLANK_.indexOf(a) >= 0) { return b || a; }
  if (!b || MERGE_BLANK_.indexOf(b) >= 0) { return a; }
  if (a.indexOf(b) >= 0) { return a; }
  if (b.indexOf(a) >= 0) { return b; }
  return a.replace(/[。；;]+$/, '') + '；' + b;
}

/** 兩個價位說明併成一個。有講的優先，兩個都有講就都留著。 */
function mergePrice_(a, b) {
  a = String(a || '').trim();
  b = String(b || '').trim();
  if (!a || MERGE_BLANK_.indexOf(a) >= 0) { return b || a || '未說明'; }
  if (!b || MERGE_BLANK_.indexOf(b) >= 0 || a.indexOf(b) >= 0) { return a; }
  if (b.indexOf(a) >= 0) { return b; }
  return a + '、' + b;
}

/**
 * 把試算表裡重複的列合併掉。
 *
 * since 為空字串代表不限日期。回傳 { merged, scanned }。
 */
function mergeDuplicateRows_(since) {
  var out = { merged: 0, scanned: 0 };

  [
    { sheet: '操作紀錄', dir: '方向', price: '價位說明', text: '理由摘錄' },
    { sheet: '會員持股', dir: '', price: '', text: '說明重點' }
  ].forEach(function (cfg) {
    var sh;
    try { sh = getSheet_(cfg.sheet); } catch (e) { return; }
    var values = sh.getDataRange().getValues();
    if (values.length < 3) { return; }

    var head = values[0].map(function (h) { return String(h).trim(); });
    var col = {};
    head.forEach(function (h, i) { col[h] = i; });
    if (col['日期'] == null || col['股票名稱'] == null) { return; }

    var iDate = col['日期'], iName = col['股票名稱'], iCode = col['代號'];
    var iDir = cfg.dir ? col[cfg.dir] : null;
    var iPrice = cfg.price ? col[cfg.price] : null;
    var iText = col[cfg.text];
    var iStance = col['目前立場'];
    if (iText == null) { return; }

    var seen = {};        // 鑰匙 → 第一次出現的列號（1 起算）
    var kill = [];        // 要刪掉的列號
    var edits = [];       // [列號, 欄號, 值]

    /* 同一天同一檔，影片與會員簡訊各寫了一列的情況。
       買賣的鑰匙本來帶價位（不同價位是不同的一筆操作），所以這兩列不會被
       合併——於是持股追蹤那邊會看到同一天買了兩次。
       但它們其實是同一筆：簡訊是盤中的委託價，影片是收盤後的說明。
       這時把價位從鑰匙裡拿掉，讓它們併成一列，並且採用簡訊那一個價位——
       那是他當下講的數字，比事後從逐字稿推出來的準。 */
    var iSrc = col['來源影片ID'];
    var smsKey = {};
    if (iSrc != null && iDir != null) {
      for (var q = 1; q < values.length; q++) {
        if (String(values[q][iSrc] || '').indexOf('CMONEY-') !== 0) { continue; }
        var qd = fmtDate_(values[q][iDate]);
        var qdir = String(values[q][iDir] || '').trim();
        if (qdir !== '買入' && qdir !== '賣出') { continue; }
        var qc = iCode == null ? '' : String(values[q][iCode] || '').trim();
        var qn = String(values[q][iName] || '').trim();
        var qi = (qc && qc !== '代號待確認') ? qc : qn;
        if (qd && qi) { smsKey[[qd, qdir, qi].join('|')] = 1; }
      }
    }

    for (var r = 1; r < values.length; r++) {
      var row = values[r];
      var d = fmtDate_(row[iDate]);
      if (!d) { continue; }
      if (since && d < since) { continue; }
      out.scanned++;

      var code = iCode == null ? '' : String(row[iCode] || '').trim();
      var name = String(row[iName] || '').trim();
      var ident = (code && code !== '代號待確認') ? code : name;
      if (!ident) { continue; }

      var dir = iDir == null ? '持股' : String(row[iDir] || '').trim();
      // 買賣要連價位一起當鑰匙，不同價位就是不同的一筆操作
      var trade = (dir === '買入' || dir === '賣出');
      var price = iPrice == null ? '' : String(row[iPrice] || '').trim();
      var base = [d, dir, ident].join('|');
      // 這一天這一檔有簡訊寫過買賣，就不把價位放進鑰匙，讓兩列併起來。
      var pooled = trade && smsKey[base];
      var k = base + ((trade && !pooled) ? '|' + price : '');

      var first = seen[k];

      /* 觀望兩類之間的衝突。

         同一天同一檔同時出現在觀望不碰與觀望注意——他用「甲不要追、要注意乙」
         這種對比句時，乙的段落前半講優點、後半講現在還不能買，兩段分開讀
         會得到相反的結論。表格上就是一列看好、一列看壞，讀的人不知道信哪個。

         收斂成觀望不碰。不是因為它比較可能對，是兩種錯的代價不對稱：
         把「不要碰」顯示成「留意」，讀的人可能照著進場；反過來只是錯過一個想法。
         兩邊的理由都留著——看好的那一段通常正是條件（過季線補缺口再說）。 */
      if (first == null && !trade && (dir === '觀望不碰' || dir === '觀望注意')) {
        var other = (dir === '觀望注意') ? '觀望不碰' : '觀望注意';
        var ok = seen[[d, other, ident].join('|')];
        if (ok != null) {
          var keepRow = values[ok];
          var keepDir = String(keepRow[iDir] || '').trim();
          if (keepDir === '觀望不碰') {
            // 已經有一列觀望不碰，把這一列的理由併過去再刪掉
            keepRow[iText] = mergeText_(keepRow[iText], row[iText]);
            edits.push([ok + 1, iText + 1, keepRow[iText]]);
            if (iPrice != null) {
              keepRow[iPrice] = mergePrice_(keepRow[iPrice], row[iPrice]);
              edits.push([ok + 1, iPrice + 1, keepRow[iPrice]]);
            }
          } else {
            // 先出現的是觀望注意。把它就地改成觀望不碰，再把這一列併進去。
            keepRow[iDir] = '觀望不碰';
            edits.push([ok + 1, iDir + 1, '觀望不碰']);
            keepRow[iText] = mergeText_(keepRow[iText], row[iText]);
            edits.push([ok + 1, iText + 1, keepRow[iText]]);
            if (iPrice != null) {
              keepRow[iPrice] = mergePrice_(keepRow[iPrice], row[iPrice]);
              edits.push([ok + 1, iPrice + 1, keepRow[iPrice]]);
            }
            // 鑰匙跟著改，後面若還有同一檔才比對得到
            seen[[d, '觀望不碰', ident].join('|')] = ok;
          }
          kill.push(r + 1);
          out.merged++;
          continue;
        }
      }

      if (first == null) { seen[k] = r; continue; }

      // 合併進第一次出現的那一列
      var keep = values[first];
      keep[iText] = mergeText_(keep[iText], row[iText]);
      edits.push([first + 1, iText + 1, keep[iText]]);

      if (iPrice != null && !trade) {
        keep[iPrice] = mergePrice_(keep[iPrice], row[iPrice]);
        edits.push([first + 1, iPrice + 1, keep[iPrice]]);
      }
      /* 影片與簡訊併成一列時，價位採用簡訊那一個。
         留下來的可能是任一邊，所以兩種方向都要處理：
         這一列是簡訊就把它的價位覆蓋上去；留下來的是簡訊就不動。 */
      if (pooled && iPrice != null && iSrc != null) {
        var thisIsSms = String(row[iSrc] || '').indexOf('CMONEY-') === 0;
        var keepIsSms = String(keep[iSrc] || '').indexOf('CMONEY-') === 0;
        if (thisIsSms && !keepIsSms && price) {
          keep[iPrice] = price;
          edits.push([first + 1, iPrice + 1, price]);
          keep[iSrc] = row[iSrc];
          edits.push([first + 1, iSrc + 1, row[iSrc]]);
          Logger.log('  價位以會員簡訊為準　' + d + '　' + ident + '　' + price);
        }
      }
      if (iStance != null) {
        var cur = String(keep[iStance] || '').trim();
        if (MERGE_BLANK_.indexOf(cur) >= 0) {
          keep[iStance] = row[iStance];
          edits.push([first + 1, iStance + 1, keep[iStance]]);
        }
      }
      kill.push(r + 1);
      out.merged++;
    }

    if (!kill.length) { return; }

    withLock_(function () {
      edits.forEach(function (e) { sh.getRange(e[0], e[1]).setValue(e[2]); });
      // 由大到小刪，否則刪掉一列之後後面的列號全部往上移一格
      kill.sort(function (a, b) { return b - a; })
          .forEach(function (rn) { sh.deleteRow(rn); });
    });
    Logger.log('合併重複　' + cfg.sheet + '：刪掉 ' + kill.length + ' 列');
  });

  if (out.merged) { CACHE.remove('tracker'); }
  return out;
}

/* ------------------------------------------------------------------ *
 * 成交歸位（可回溯）
 *
 * 症狀：華城（1519）那一天，逐字稿寫著「那我今天賣掉華城，創新高賣股票」，
 * 表格上卻是觀望注意，說明欄還寫著「股價創新高且帶量，先行獲利了結」。
 * 一筆真的發生的賣出，在網站上變成「留意追蹤」。
 *
 * 這種錯特別安靜。買入與賣出是持股追蹤唯一的開倉與平倉依據——賣出被記成
 * 觀望，那一檔的回合就不會平倉，報酬會一直掛在未實現，而畫面上每一欄都有值。
 *
 * 模型為什麼會這樣判：他賣完之後通常會接著講接下來怎麼做
 * （「華城也是在等季線，我看到它衝高我先賣一次，有下來我再接」），
 * 那句讀起來像「現在先觀察」。擷取提示詞裡「同一天同一檔只能有一個立場」
 * 那一條本來是用來收斂觀望兩類的衝突，卻被拿去把成交降級了。
 *
 * 提示詞那邊已經把適用範圍寫清楚，這裡再加一道不靠模型的檢查，
 * 而且它能回頭處理過去的資料——提示詞只對之後新進來的有效。
 *
 * 判斷方式與 pipeline.py 的 promote_executed_trades 完全一致，
 * 兩邊必須同進退，否則同一天用後台重整與用上游重跑會得到不同結果。
 * ------------------------------------------------------------------ */

// 第一人稱、今天真的成交的句型。「賣」這個字本身不算——
// 「聯電可不可以賣」「你們要不要賣」都有賣字，但都不是他成交了。
var EXEC_SELL_RE_ = /我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中|這個盤中)?(?:先|就)?賣掉|我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中)?(?:先)?賣了|我(?:們)?(?:今天|今日)?(?:先)?賣一次|我(?:們)?(?:今天|今日)?出清|獲利了結|停利出場/;
var EXEC_BUY_RE_ = /我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中|這個盤中)?(?:先|就)?買(?:進|回來|了)|就是我買的|我(?:們)?(?:今天|今日)?(?:先)?接回來|我(?:今天)?加碼買/;
var TODAY_RE_ = /今天|今日|剛剛|剛才|這個盤中|早盤|早上|開盤/;
// 回顧詞。有這些就不算今天的成交，這裡一律不碰，交給複審處理。
var PAST_RE_ = /以前|當初|上次|上一次|之前|那時|當時|去年|前年|幾年前|個月前|上禮拜|上個禮拜|上週|昨天|前一天|除權息前/;

/** 以標點把逐字稿切成短句。切一次給同一天的每一列共用。 */
function splitClauses_(t) {
  return String(t || '').split(/[。！？；，、\n\r]+/)
    .filter(function (c) { return c.trim(); });
}

/**
 * 這一檔在逐字稿裡有沒有一句是「我今天賣掉／買了它」。
 * 回傳 '賣出'、'買入' 或空字串。
 *
 * 看兩層：
 *   1. 名稱與動作在同一句（「我今天賣掉華城」）——句子自己就講完了，
 *      隔壁那一句講誰都不影響。
 *   2. 名稱與動作分在兩句（「那一支股票叫做力積電，早上這邊就是我買的」）——
 *      這時才看前後各一句，而且視窗裡不能出現當天其他任何一檔的名稱。
 *      出現了就代表視窗橫跨兩檔，分不清動作是誰的，一律不動。
 */
function executedToday_(script, name, others) {
  name = String(name || '').trim();
  if (!script || name.length < 2) { return ''; }

  var rest = (others || []).map(function (o) { return String(o || '').trim(); })
    .filter(function (o) { return o && o !== name && o.length >= 2; });

  function verdict(win, alone) {
    if (!alone) {
      for (var j = 0; j < rest.length; j++) {
        if (win.indexOf(rest[j]) >= 0) { return ''; }
      }
    }
    if (!TODAY_RE_.test(win) || PAST_RE_.test(win)) { return ''; }
    if (EXEC_SELL_RE_.test(win)) { return '賣出'; }
    if (EXEC_BUY_RE_.test(win)) { return '買入'; }
    return '';
  }

  // script 可以是整份逐字稿，也可以是已經切好的短句陣列。
  // 全面重整會一天切一次、給那一天的每一列共用——不然一天幾十列，
  // 每一列都把五萬字重切一遍，全部歷史那一輪會直接撞上執行時間上限。
  var clauses = (typeof script === 'string') ? splitClauses_(script) : script;

  for (var i = 0; i < clauses.length; i++) {
    if (clauses[i].indexOf(name) < 0) { continue; }
    var got = verdict(clauses[i], true);
    if (got) { return got; }
    got = verdict(clauses.slice(Math.max(0, i - 1), i + 2).join('，'), false);
    if (got) { return got; }
  }
  return '';
}

/**
 * 把「今天明講成交、卻被歸到觀望」的列改回買入或賣出。
 *
 * since 為空字串代表不限日期。st 有給的話，被改動的那幾天會記進去，
 * 全面重整最後會重寫那幾天的郵件。回傳 { moved, scanned, days }。
 */
function promoteExecutedRows_(since, st) {
  var out = { moved: 0, scanned: 0, days: 0 };

  // 逐字稿是唯一的判斷依據。同一天有兩份就取長的那一份。
  var script = {};
  try {
    readSheetObjects_('影片清單').forEach(function (r) {
      var d = fmtDate_(r['發布日期']);
      if (!d) { return; }
      var t = String(r['修飾後逐字稿內容'] || '');
      if (t.length > String(script[d] || '').length) { script[d] = t; }
    });
  } catch (e) {
    Logger.log('成交歸位：讀不到影片清單，略過（' + e + '）');
    return out;
  }

  var sh;
  try { sh = getSheet_('操作紀錄'); } catch (e) { return out; }
  var values = sh.getDataRange().getValues();
  if (values.length < 2) { return out; }

  var head = values[0].map(function (h) { return String(h).trim(); });
  var iDate = head.indexOf('日期');
  var iName = head.indexOf('股票名稱');
  var iDir = head.indexOf('方向');
  if (iDate < 0 || iName < 0 || iDir < 0) { return out; }

  // 先把每一天出現過的名稱收齊，跨檔判定要用。
  var namesOfDay = {};
  for (var r0 = 1; r0 < values.length; r0++) {
    var d0 = fmtDate_(values[r0][iDate]);
    var n0 = String(values[r0][iName] || '').trim();
    if (!d0 || !n0) { continue; }
    (namesOfDay[d0] = namesOfDay[d0] || []).push(n0);
  }

  var edits = [], touched = {}, clauseCache = {};
  for (var r = 1; r < values.length; r++) {
    var d = fmtDate_(values[r][iDate]);
    if (!d) { continue; }
    if (since && d < since) { continue; }
    var dir = String(values[r][iDir] || '').trim();
    if (dir !== '觀望不碰' && dir !== '觀望注意') { continue; }
    out.scanned++;

    var v2 = script[d] || '';
    if (v2.length < 300) { continue; }        // 沒有逐字稿就什麼都不判
    if (!clauseCache[d]) { clauseCache[d] = splitClauses_(v2); }

    var name = String(values[r][iName] || '').trim();
    var got = executedToday_(clauseCache[d], name, namesOfDay[d] || []);
    if (!got) { continue; }

    edits.push([r + 1, iDir + 1, got]);
    touched[d] = 1;
    out.moved++;
    Logger.log('成交歸位　' + d + '　' + name + '　' + dir + ' → ' + got);
  }

  if (!edits.length) { return out; }

  withLock_(function () {
    edits.forEach(function (e) { sh.getRange(e[0], e[1]).setValue(e[2]); });
  });
  Object.keys(touched).forEach(function (d) {
    out.days++;
    if (st) { markChangedDay_(st, d); }
  });
  CACHE.remove('tracker');
  return out;
}

/** 在編輯器直接執行：把所有「明講成交卻停在觀望」的列改回買賣。 */
function promoteExecutedTrades() {
  var r = promoteExecutedRows_('', null);
  Logger.log('掃過 ' + r.scanned + ' 列觀望，改回買賣 ' + r.moved + ' 列，影響 ' + r.days + ' 天');
  return r;
}


/** 在編輯器直接執行：看看目前有多少重複，並就地合併。 */
function mergeDuplicates() {
  var r = mergeDuplicateRows_('');
  Logger.log('掃過 ' + r.scanned + ' 列，合併 ' + r.merged + ' 列');
  return r;
}


/* ------------------------------------------------------------------ *
 * 說明重寫（可回溯）
 *
 * 擷取端現在會把逐字稿改寫成研究報告的句子，但那只對「之後新進來的資料」
 * 有效。過去幾個月的紀錄仍然是口語原句剪貼——「隱藏版光訊叫做2402的錩新，
 * 昨天漲3塊1，今天漲兩塊4，好，注意」那種，讀起來像沒整理過。
 *
 * 這一步把既有的理由摘錄與說明重點重寫一次。它只改文字，不改分類、
 * 不改價位、不改日期，所以判錯的代價很小——最壞的情況是某一句寫得不夠好，
 * 而不是資料錯掉。也因此不需要 confident 那一層保護。
 *
 * 只重寫「看起來還沒改寫過」的：太長、含口頭禪、含情緒字眼的那些。
 * 已經是研究報告句子的不送模型，那是純粹的浪費。
 * ------------------------------------------------------------------ */

// 一批送多少筆。每筆輸入約 60 字、輸出通常 55 字內，30 筆仍可穩定放入回應。
var REWRITE_BATCH = 30;

/** 將舊版 AI 的「〔現況〕＋〔建議〕」模板轉為自然完整句，並清理內部判斷依據與改列註記。 */
function naturalReason_(text) {
  var t = String(text || '').replace(/\s+/g, ' ').trim();
  if (!t || t === '未說明' || t === '會員持股') { return t; }
  var pair = t.match(/^[\u3014\u3010\uFF3B\[]\s*(.*?)\s*[\u3015\u3011\uFF3D\]]\s*[+＋]\s*[\u3014\u3010\uFF3B\[]\s*(.*?)\s*[\u3015\u3011\uFF3D\]]\s*[。．.]?$/);
  if (pair) {
    var left = pair[1].replace(/[。；;，,：:]\s*$/, '').trim();
    var right = pair[2].replace(/^[。；;，,：:]\s*/, '').trim();
    t = left + (left && right ? '，' : '') + right;
  } else if (/[\u3015\u3011\uFF3D\]]\s*[+＋]\s*[\u3014\u3010\uFF3B\[]/.test(t)) {
    t = t.replace(/[\u3015\u3011\uFF3D\]]\s*[+＋]\s*[\u3014\u3010\uFF3B\[]/g, '，')
         .replace(/^[\u3014\u3010\uFF3B\[]+|[\u3015\u3011\uFF3D\]]+$/g, '').trim();
  }
  var changed = true;
  while (changed) {
    changed = false;
    var m1 = t.match(/（[^（）]*?(?:日期未明|改列|原判|原為|依原文語氣|依上下文|歷史回顧)[^（）]*?）/);
    if (m1) {
      t = t.substring(0, m1.index) + t.substring(m1.index + m1[0].length);
      changed = true;
      continue;
    }
    var m2 = t.match(/\([^()]*?(?:日期未明|改列|原判|原為|依原文語氣|依上下文|歷史回顧)[^()]*?\)/);
    if (m2) {
      t = t.substring(0, m2.index) + t.substring(m2.index + m2[0].length);
      changed = true;
    }
  }
  t = t.replace(/[，,、]?\s*因(?:(?!故改列).)*?故改列.*?(?=[。！？!?（(]|$)/g, '');
  // 公開說明不寫人名（2026/09/13），與 pipeline.py 的 strip_speaker_names 同一條規則：
  // 只拿掉句首或連接詞後面當主詞的「張正」「張震」。
  t = t.replace(/(^|[，,。；;：:、「『（(\s]|雖然|但是|但|而且|而|並且|並|且|因為|所以)(?:張震|張正|講者)(?:老師)?(?:本人)?(?:的(?=會員))?/g, '$1');
  t = t.replace(/\s*，\s*/g, '，').trim();
  t = t.replace(/[，,；;：:]\s*[。.]/g, '。');
  t = t.replace(/[，,；;：:]+$/g, '').trim();
  t = t.replace(/^[，,；;：:]+/g, '').trim();
  t = t.replace(/[。.]+$/g, '').trim();
  if (t && !/[。！？!?]$/.test(t)) { t += '。'; }
  return t;
}

/** 這一句需不需要重寫。純規則，不花模型額度。 */
function needsRewrite_(text) {
  var t = String(text || '').trim();
  if (!t || t === '未說明' || t === '會員持股') { return false; }

  // 已經很短又沒有口語痕跡的，八成是改寫過的，不動。
  var ORAL = ['好，注意', '有沒有看到', '你看', '對不對', '娘咧', '我告訴你',
              '知道嗎', '是不是', '來，', '欸', '啦', '喔', '嘛', '呢',
              '有鬼', '沒用', '不敢買', '很爽', '厲害'];
  for (var i = 0; i < ORAL.length; i++) {
    if (t.indexOf(ORAL[i]) >= 0) { return true; }
  }
  // 引號包起來的整段原話，一定是剪貼的。
  if (/^[「『"]/.test(t) || t.indexOf('」') >= 0) { return true; }
  if (/[\u3015\u3011\uFF3D\]]\s*[+＋]\s*[\u3014\u3010\uFF3B\[]/.test(t)) { return true; }
  // 必要價位與條件可稍長；超過 68 字才視為明顯未濃縮。
  if (t.length > 68) { return true; }
  return false;
}

/**
 * 重寫一批。與價位校對同一個模式：規則快篩挑出可疑的，只有那些送模型。
 *
 * since 有值時只看那一天（含）之後的列，供全面重整的範圍限定用。
 */
function rewriteReasonPhase_(st, deadline, since) {
  var TARGETS = [['操作紀錄', '理由摘錄'], ['會員持股', '說明重點']];
  var todo = [];

  TARGETS.forEach(function (spec) {
    var sn = spec[0], col = spec[1];
    var vals;
    try { vals = getSheet_(sn).getDataRange().getValues(); }
    catch (e) { return; }
    if (vals.length < 2) { return; }
    var head = vals[0];
    var cDate = head.indexOf('日期'), cName = head.indexOf('股票名稱');
    var cDir = head.indexOf(sn === '會員持股' ? '目前立場' : '方向');
    var cTxt = head.indexOf(col);
    if (cTxt < 0 || cDate < 0) { return; }

    for (var i = 1; i < vals.length; i++) {
      var d = fmtDate_(vals[i][cDate]);
      if (since && d && d < since) { continue; }
      var txt = String(vals[i][cTxt] || '');
      if (!needsRewrite_(txt)) { continue; }
      todo.push({ sheet: sn, row: i + 1, col: cTxt + 1, date: d,
                  name: String(vals[i][cName] || ''),
                  dir: cDir >= 0 ? String(vals[i][cDir] || '') : sn,
                  text: txt });
    }
  });

  st.rewriteTotal = todo.length;
  if (!todo.length) {
    st.note = '沒有需要重寫的說明';
    return true;
  }

  // 時間不夠就交棒。可疑的列是每次重新掃出來的，中途停掉不會漏掉任何一列。
  if (deadline && timeLeft_(deadline) < 150000) {
    st.note = '本棒剩餘時間不足以再跑一批說明重寫（尚有 ' + todo.length + ' 筆），交棒';
    return false;
  }

  var batch = todo.slice(0, REWRITE_BATCH);
  var payload = batch.map(function (x, n) {
    return (n + 1) + '. ' + x.name + '｜' + x.dir + '｜' + x.text;
  }).join('\n');

  var res;
  try {
    var raw = callGemini_(PIPE_REWRITE_SYSTEM, payload,
                          { maxOut: 4096, temperature: 0, json: true });
    res = JSON.parse(String(raw).replace(/^```json|^```|```$/gm, '').trim());
  } catch (e) {
    if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) { throw e; }
    st.note = '說明重寫呼叫失敗（' + String(e).slice(0, 60) + '），這一步略過';
    return true;
  }

  var done = 0;
  (res.results || []).forEach(function (r) {
    var n = Number(r.index) - 1;
    if (!(n >= 0 && n < batch.length)) { return; }
    var t = naturalReason_(String(r.text || '').trim());
    if (!t || t === batch[n].text) { return; }
    // 改寫不可以把內容改沒了。長度掉到剩三分之一以下時多半是模型偷懶，
    // 只回了半句，那種寧可維持原樣。
    if (t.length * 3 < batch[n].text.length) { return; }
    getSheet_(batch[n].sheet).getRange(batch[n].row, batch[n].col).setValue(t);
    done++;
    Logger.log('　重寫　' + batch[n].date + ' ' + batch[n].name +
               '　「' + batch[n].text.slice(0, 24) + '」→「' + t.slice(0, 24) + '」');
  });

  st.rewriteDone = (st.rewriteDone || 0) + done;
  st.note = '說明重寫已處理 ' + batch.length + ' 筆，累計改寫 ' + st.rewriteDone;
  // 子進度：還剩幾筆要送。一批 30 筆，幾百筆時這一格會亮很久，
  // 沒有數字的話與卡住分不出來。
  st.sub = { done: st.rewriteDone, total: st.rewriteDone + (todo.length - batch.length),
             label: '筆' };
  if (done) { CACHE.remove('tracker'); }

  // 還有剩就回 false，下一棒繼續。
  return todo.length <= REWRITE_BATCH;
}

function runFullFix_() {
  var st = fullFixState_();
  if (!st || st.status !== '處理中') { return; }
  var deadline = stepDeadline_();

  function log_(t) { st.log.push(t); Logger.log('  ' + t); }

  try {
    while (st.index < FULLFIX_STEPS.length - 1) {
      if (timeLeft_(deadline) < 70000) { break; }     // 額度不夠就交棒
      st.step = FULLFIX_STEPS[st.index];

      if (st.step === '名稱與代號') {
        var a = resolveBlankCodes_('');            // 空字串代表不限日期
        log_('名稱與代號：修正 ' + a.fixed + '、剔除 ' + a.dropped + '、待確認 ' + a.pending);

      } else if (st.step === '稽核補漏') {
        // 額度不夠就把游標留著交棒，下一輪從同一天接著做，不重跑已經處理過的日期。
        if (!auditAllPhase_(st, deadline)) {
          setFullFixState_(st); scheduleFullFix_(); return;
        }
        log_('稽核補漏：' + st.reviewTotal + ' 天　補登 ' + st.reviewStat.added +
             '、判定不收 ' + st.reviewStat.skipped);

      } else if (st.step === '內容複審') {
        if (!reviewAllPhase_(st, deadline)) {
          setFullFixState_(st); scheduleFullFix_(); return;
        }
        log_('內容複審：' + st.reviewTotal + ' 天　改分類 ' + st.reviewStat.changed +
             '、改名稱 ' + (st.reviewStat.renamed || 0) +
             '、刪除 ' + st.reviewStat.dropped +
             '、沒把握不動 ' + st.reviewStat.unsure);
        log_('兩段合計 ' + (st.changedDays || []).length +
             ' 天的紀錄有變動，稍後會重寫那幾天的郵件');
        st.reviewDays = null;

      } else if (st.step === '合併重複') {
        var since_ = fullFixSince_(st.scope, fullFixAllDays_());
        /* 成交歸位排在合併之前：歸位之後那一檔可能與既有的賣出列撞在一起，
           讓緊接著的合併去收就好，不必在歸位那邊另外處理。 */
        var pm = promoteExecutedRows_(since_, st);
        if (pm.moved) {
          log_('成交歸位：' + pm.moved + ' 列從觀望改回買賣（影響 ' + pm.days + ' 天）');
        }
        var mg = mergeDuplicateRows_(since_);
        st.sub = { done: mg.merged + pm.moved, total: 0, label: '' };
        log_('合併重複：掃過 ' + mg.scanned + ' 列，合併 ' + mg.merged + ' 列');

      } else if (st.step === '價位校對') {
        var stc = { phase: '價位', priceFixed: 0 };
        cleanPricePhase_(stc, deadline, fullFixSince_(st.scope, fullFixAllDays_()));
        st.sub = { done: stc.priceFixed || 0, total: 0, label: stc.note || '' };
        log_('價位校對：修正 ' + stc.priceFixed + ' 筆' +
             (stc.phase === '價位' ? '（還有剩，下一輪繼續）' : ''));
        if (stc.phase === '價位') { setFullFixState_(st); scheduleFullFix_(); return; }

      } else if (st.step === '重寫說明') {
        // 只改文字，不動分類、價位、日期，所以判錯的代價很小。
        // 一批 30 筆，跑不完就交棒，下一棒重新掃一次還沒改的。
        if (!rewriteReasonPhase_(st, deadline,
                                 fullFixSince_(st.scope, fullFixAllDays_()))) {
          log_('重寫說明：' + (st.note || '處理中'));
          setFullFixState_(st); scheduleFullFix_(); return;
        }
        log_('重寫說明：' + (st.note || '完成') +
             '，累計改寫 ' + (st.rewriteDone || 0) + ' 筆');
        st.rewriteDone = null; st.rewriteTotal = null;

      } else if (st.step === '補齊日K') {
        // 三百多檔一棒補不完。沒補完就留在這一步交棒，下一棒從停下的代號接著補，
        // 不能像以前一樣做一段就記「已補齊」往下走——那樣重算追蹤用的是半套日K。
        var dk = dailyKBackfillRound_({ deadline: deadline - 70 * 1000, waitMs: 30 * 1000,
                                        source: '全面重整' });
        if (dk.fatal) { throw new Error('補齊日K 無法繼續：' + dk.note); }
        if (!dk.done) {
          log_('補齊日K：' + dk.note + '（下一棒接著補）');
          setFullFixState_(st); scheduleFullFix_(); return;
        }
        log_('日K已補齊：' + dk.note);

      } else if (st.step === '重算追蹤') {
        rebuildHoldingsTrackerJob();
        log_('持股追蹤已重算');

      } else if (st.step === '重寫郵件') {
        // 重寫兩種日子：紀錄真的被改過的那幾天，加上最近七個交易日。
        //
        // 為什麼不是只寫最近七天：郵件內容是撰稿當下存起來的一份文字，
        // 不會跟著試算表變，而網站的郵件查詢分頁讀的就是那一份。
        // 前面的稽核與複審若動了三個月前的某一天，那天的信就會與網站對不起來，
        // 而且是永久對不起來——只寫最近七天永遠補不到它。
        //
        // 為什麼不是全部重寫：沒被動過的那些天，重寫的結果會與現在完全一樣，
        // 純粹多花一次模型額度。
        var days = {};
        readSheetObjects_('操作紀錄').forEach(function (r) {
          var d = fmtDate_(r['日期']); if (d) { days[d] = 1; }
        });
        // 「最近幾天」跟著範圍走：選近一周就補最近七天，選全部就仍然是七天
        // （全部歷史時，沒被動過的舊日子重寫的結果與現在一樣，是純粹的浪費）。
        var recentN = (FULLFIX_SCOPES[fullFixScope_(st.scope)] || {}).days || 7;
        var recent = Object.keys(days).sort().reverse().slice(0, recentN);
        if (!st.mailQueue) {
          var q = {};
          (st.changedDays || []).forEach(function (d) { q[d] = 1; });
          recent.forEach(function (d) { q[d] = 1; });
          st.mailQueue = Object.keys(q).sort().reverse();
          st.mailDone = 0;
          log_('待重寫郵件 ' + st.mailQueue.length + ' 天（有變動 ' +
               (st.changedDays || []).length + ' 天、最近 ' + recent.length + ' 天，去重後）');
          st.changedDays = null;   // 已經併進 mailQueue，留著只是佔空間
        }
        // 撰稿一天約一分鐘，天數多時一次跑不完是常態，交棒接著做。
        while (st.mailDone < st.mailQueue.length) {
          if (timeLeft_(deadline) < 90000) {
            st.step = '重寫郵件　' + st.mailDone + '/' + st.mailQueue.length + ' 天';
            st.sub = { done: st.mailDone, total: st.mailQueue.length, label: '' };
            st.updatedAt = nowStamp_();
            setFullFixState_(st); scheduleFullFix_(); return;
          }
          var md = st.mailQueue[st.mailDone];
          try { stepArticle_({ date: md, videoId: '', id: 'FIX', step: '撰稿', done: 0, total: 1 }); }
          catch (e) { log_('重寫 ' + md + ' 失敗：' + String(e).slice(0, 60)); }
          st.mailDone++;
          st.sub = { done: st.mailDone, total: st.mailQueue.length, label: md };
          st.updatedAt = nowStamp_();
          setFullFixState_(st);
        }
        log_('已重寫 ' + st.mailDone + ' 天的郵件內容，信件與網站重新對齊');
        st.mailQueue = null;
        st.sub = null;
      }

      st.index++;
      st.done = st.index;
      st.updatedAt = nowStamp_();
      setFullFixState_(st);
    }
  } catch (e) {
    /* 當日額度用完不是「失敗」，是「今天到此為止」。
       分開的理由是後續動作完全不同：失敗要人去看哪裡壞了，
       額度用完只要明天再按一次接著跑，而且進度都還在。
       標成失敗會讓人以為資料出事了，跑去翻日誌找不存在的錯。 */
    if (typeof isQuotaExhausted_ === 'function' && isQuotaExhausted_(e)) {
      st.status = '暫停';
      st.lastError = 'Gemini 今日額度已用完，今天到此為止。進度全部保留著，' +
                     '明天按「接著跑」會從停住的那一天繼續，不會重跑已經處理過的。';
      st.log.push('◌ ' + st.step + '：今日額度用完，暫停');
      st.updatedAt = nowStamp_();
      setFullFixState_(st);
      Logger.log('全面重整暫停：Gemini 今日額度已用完。');
      return;
    }
    st.status = '失敗';
    st.lastError = String(e && e.message || e);
    st.log.push('✗ ' + st.step + '：' + st.lastError.slice(0, 120));
    st.updatedAt = nowStamp_();
    setFullFixState_(st);
    notifyAdmin_('全面重整失敗', '卡在「' + st.step + '」\n' + st.lastError +
                 '\n\n' + troubleshoot_(st.lastError));
    return;
  }

  if (st.index >= FULLFIX_STEPS.length - 1) {
    st.status = '完成'; st.step = '完成';
    st.updatedAt = nowStamp_();
    setFullFixState_(st);
    CACHE.remove('tracker');
    notifyAdmin_('全面重整完成', (st.log || []).join('\n'));
    return;
  }
  st.updatedAt = nowStamp_();
  setFullFixState_(st);
  scheduleFullFix_();
}



/* ==================================================================== *
 * 派工給 GitHub Actions
 *
 * 為什麼要這樣做
 * --------------
 * 重活全部搬到 GitHub 跑，Apps Script 只負責存資料、派工、顯示進度。
 * 理由是單次執行時間：Apps Script 一般帳號 6 分鐘，而一份逐字稿的完整
 * 處理要十幾分鐘。先前用觸發器接力硬撐，只要某一棒超時就整個中斷，
 * 進度停在原地、沒有錯誤訊息、也沒有日誌可看，非常難查。
 *
 * GitHub Actions 沒有這個限制，而且每一行輸出都留在執行紀錄裡。
 * 進度則由 pipeline.py 逐步寫回「後台工單」，網站的進度條讀那一列，
 * 所以兩邊都看得到：網站看進度，GitHub 看細節。
 *
 * 需要兩個指令碼屬性：
 *   GITHUB_REPO   格式 擁有者/儲存庫，例如 someone/zhangzhen-stock-site
 *   GITHUB_TOKEN  細粒度存取權杖，只需要對該儲存庫的 Actions 讀寫權限
 * ==================================================================== */

function githubCfg_() {
  var pr = PropertiesService.getScriptProperties();
  return {
    repo: String(pr.getProperty('GITHUB_REPO') || '').trim(),
    token: String(pr.getProperty('GITHUB_TOKEN') || '').trim(),
    workflow: String(pr.getProperty('GITHUB_WORKFLOW') || 'daily.yml').trim(),
    ref: String(pr.getProperty('GITHUB_REF') || 'main').trim()
  };
}

/**
 * 觸發一次 workflow_dispatch。
 * inputs 是要傳給工作流程的參數，例如 { admin_job: 'true' }。
 */
function dispatchGithub_(inputs, workflowName) {
  var g = githubCfg_();
  if (!g.repo || !g.token) {
    return { ok: false,
             reason: '尚未設定 GITHUB_REPO 或 GITHUB_TOKEN。請到 Apps Script 專案設定 → ' +
                     '指令碼屬性補上。GITHUB_REPO 格式是「擁有者/儲存庫」，' +
                     'GITHUB_TOKEN 是有該儲存庫 Actions 讀寫權限的細粒度存取權杖。' };
  }

  // workflow_dispatch 的 204 回應不會帶 run id。派工前先記住目前最新一筆，
  // 派工後只接受「比它更新」的 workflow_dispatch，避免後台剛好顯示到
  // 另一個排程或另一位管理者啟動的工作。
  var marker = String((inputs || {}).sms_job_id || '');
  var before = latestRunInfo_(marker);
  var url = 'https://api.github.com/repos/' + g.repo +
            '/actions/workflows/' + encodeURIComponent(workflowName || g.workflow) + '/dispatches';
  var res;
  try {
    res = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      muteHttpExceptions: true,
      headers: {
        Authorization: 'Bearer ' + g.token,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28'
      },
      payload: JSON.stringify({ ref: g.ref, inputs: inputs || {} })
    });
  } catch (e) {
    return { ok: false, reason: '連線 GitHub 失敗：' + String(e).slice(0, 160) };
  }

  var code = res.getResponseCode();
  if (code === 204) {
    var found = null;
    for (var poll = 0; poll < 5; poll++) {
      Utilities.sleep(poll === 0 ? 1800 : 900);
      var latest = latestRunInfo_(marker);
      if (latest && (!before || String(latest.id) !== String(before.id))) {
        found = latest;
        break;
      }
    }
    return { ok: true,
             runId: found ? String(found.id) : '',
             runUrl: found ? found.url : ('https://github.com/' + g.repo + '/actions') };
  }

  var body = String(res.getContentText() || '').slice(0, 300);
  var hint = '';
  if (code === 404) {
    hint = '　多半是 GITHUB_REPO 打錯，或權杖沒有這個儲存庫的權限，' +
           '或工作流程檔名不是 daily.yml。';
  } else if (code === 401 || code === 403) {
    hint = '　權杖無效或權限不足。細粒度權杖需要對該儲存庫開啟 Actions 的讀寫權限。';
  } else if (code === 422) {
    hint = '　工作流程不接受這組參數，或分支名稱不對（目前設定是 ' + g.ref + '）。';
  }
  return { ok: false, reason: 'GitHub 回應 HTTP ' + code + '：' + body + hint };
}

/** 查這個 workflow 最近一次手動派工；排程執行不會冒充剛按下的工作。 */
function latestRunInfo_(marker) {
  var g = githubCfg_();
  if (!g.repo || !g.token) { return null; }
  try {
    var res = UrlFetchApp.fetch(
      'https://api.github.com/repos/' + g.repo + '/actions/workflows/' +
      encodeURIComponent(g.workflow) + '/runs?event=workflow_dispatch&per_page=10',
      { muteHttpExceptions: true,
        headers: { Authorization: 'Bearer ' + g.token,
                   Accept: 'application/vnd.github+json',
                   'X-GitHub-Api-Version': '2022-11-28' } });
    if (res.getResponseCode() !== 200) { return null; }
    var j = JSON.parse(res.getContentText());
    var runs = j.workflow_runs || [];
    var mark = String(marker || '');
    var r = null;
    for (var i = 0; i < runs.length; i++) {
      if (!mark || String(runs[i].display_title || runs[i].name || '').indexOf(mark) >= 0) {
        r = runs[i]; break;
      }
    }
    return r ? { id: String(r.id), url: r.html_url, status: r.status,
                 conclusion: r.conclusion, started: r.run_started_at } : null;
  } catch (e) { return null; }
}

function latestRunUrl_() {
  var r = latestRunInfo_();
  return r ? r.url : '';
}

function githubHeaders_(token) {
  return { Authorization: 'Bearer ' + token,
           Accept: 'application/vnd.github+json',
           'X-GitHub-Api-Version': '2022-11-28' };
}

/** 從失敗 job、step 與 log 中挑出一行真正可採取行動的原因。 */
function githubFailureReason_(g, runId) {
  var cache = CacheService.getScriptCache();
  var cacheKey = 'ghfail-' + runId;
  var cached = cache.get(cacheKey);
  if (cached) {
    try { return JSON.parse(cached); } catch (ignore) {}
  }
  var out = { summary: '', job: '', step: '' };
  try {
    var jobsRes = UrlFetchApp.fetch(
      'https://api.github.com/repos/' + g.repo + '/actions/runs/' + runId + '/jobs?per_page=100',
      { muteHttpExceptions: true, headers: githubHeaders_(g.token) });
    if (jobsRes.getResponseCode() === 200) {
      var jobs = JSON.parse(jobsRes.getContentText()).jobs || [];
      for (var i = 0; i < jobs.length; i++) {
        if (['failure', 'timed_out', 'cancelled', 'action_required'].indexOf(jobs[i].conclusion) < 0) { continue; }
        out.job = String(jobs[i].name || '');
        var steps = jobs[i].steps || [];
        for (var s = 0; s < steps.length; s++) {
          if (['failure', 'timed_out', 'cancelled'].indexOf(steps[s].conclusion) >= 0) {
            out.step = String(steps[s].name || '');
            break;
          }
        }
        break;
      }
    }

    // GitHub 的 jobs API 只給步驟名稱；真正的 Python/命令錯誤在 zip 日誌內。
    // 權杖若沒有讀取日誌權限，仍會回退到「job／step」，不讓原因整段空白。
    var logRes = UrlFetchApp.fetch(
      'https://api.github.com/repos/' + g.repo + '/actions/runs/' + runId + '/logs',
      { muteHttpExceptions: true, followRedirects: true, headers: githubHeaders_(g.token) });
    if (logRes.getResponseCode() === 200) {
      var blobs = Utilities.unzip(logRes.getBlob());
      var candidates = [];
      var critical = /(::error::|##\[error\]|Traceback|RuntimeError:|SystemExit:|流程失敗)/i;
      var strong = /(No such file or directory|找不到 pipeline\.py|版本過舊|Gemini 配額用盡|HTTP 429|缺少環境變數)/i;
      var weak = /(^|\s)(Error:|Exception:|fatal:|failed:|失敗：)/i;
      blobs.forEach(function(blob) {
        var text = '';
        try { text = blob.getDataAsString('UTF-8'); } catch (ignore) { return; }
        var lines = text.split(/\r?\n/);
        /* 跳過「Run …」那一段。

           GitHub 在每個 run: 步驟的開頭，會把整段腳本原文原樣印進日誌，
           包在 ##[group]Run … 與 ##[endgroup] 之間。那一段是「程式碼」，
           不是「執行結果」，可是裡面往往就有 ::error:: 這幾個字——例如
           cookie 到期檢查裡那一行 print("::error::cookie 已於 {when} 過期…")。

           先前沒有跳過它，於是這一支把那行「原始碼」當成最嚴重的錯誤挑出來，
           後台顯示成「原因：cookie 已於 ***when*** 過期，本次必定失敗。」，
           那句話跟這次為什麼失敗完全無關，卻是使用者唯一看得到的線索。
           （***　是 GitHub 在遮蔽 Secret：多行的 Secret 會讓它把每一行都
           當成要遮的值，包括只有一個大括號的那幾行。）

           真正的錯誤一定在 ##[endgroup] 之後，略過這一段就好。 */
        var inScriptEcho = false;
        for (var n = 0; n < lines.length; n++) {
          var raw = String(lines[n] || '');
          if (/##\[group\]Run\b/.test(raw)) { inScriptEcho = true; continue; }
          if (/##\[endgroup\]/.test(raw)) { inScriptEcho = false; continue; }
          if (inScriptEcho) { continue; }

          var line = raw.replace(/\x1b\[[0-9;]*m/g, '')
            .replace(/^\d{4}-\d\d-\d\dT[^ ]+Z\s*/, '')
            .replace(/^.*?(?:##\[error\]|::error(?: [^:]*)?::)/, '')
            .trim();
          if (!line || /Process completed with exit code/i.test(line)) { continue; }
          /* 再擋一層，防止群組標記因日誌截斷而對不上：
             看起來像原始碼而不是輸出的行一律不採用。 */
          if (/\bprint\s*\(|^\s*echo\s|f"|f'/.test(line)) { continue; }
          if (critical.test(raw)) { candidates.push({ score: 3, text: line }); }
          else if (strong.test(raw)) { candidates.push({ score: 2, text: line }); }
          else if (weak.test(line)) { candidates.push({ score: 1, text: line }); }
        }
      });
      var maxScore = candidates.reduce(function(m, x) { return Math.max(m, x.score); }, 0);
      var best = candidates.filter(function(x) { return x.score === maxScore; });
      if (best.length) {
        var msg = best[best.length - 1].text;
        msg = msg.replace(/&#x20;|&#32;/gi, ' ').replace(/&quot;|&#34;/gi, '"')
                 .replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&amp;/gi, '&');
        // 防止錯誤行意外帶出 query key 或 Bearer token。
        msg = msg.replace(/([?&](?:key|token)=)[^&\s]+/ig, '$1***')
                 .replace(/Bearer\s+[A-Za-z0-9._-]+/ig, 'Bearer ***');
        out.summary = msg.slice(0, 360);
      }
    }
  } catch (e) {
    // 讀不到壓縮日誌不應蓋掉已取得的失敗 step。
  }
  if (!out.summary) {
    out.summary = [out.job, out.step].filter(function(x) { return x; }).join('／') ||
                  'GitHub 未提供錯誤文字，請開啟即時日誌查看。';
  }
  cache.put(cacheKey, JSON.stringify(out), 300);
  return out;
}

function cancelGithubRun_(runId) {
  var g = githubCfg_();
  var id = String(runId || '').replace(/\D/g, '');
  if (!id) { return { ok: false, reason: '找不到這次工作的 GitHub run ID，無法精準取消。' }; }
  var res = UrlFetchApp.fetch(
    'https://api.github.com/repos/' + g.repo + '/actions/runs/' + id + '/cancel',
    { method: 'post', muteHttpExceptions: true, headers: githubHeaders_(g.token) });
  var code = res.getResponseCode();
  if (code === 202) { return { ok: true, message: '已送出 GitHub Actions 取消要求。' }; }
  if (code === 409) {
    return { ok: true, alreadyFinished: true,
             message: 'GitHub 工作已經結束，後台將清除殘留狀態。' };
  }
  return { ok: false, reason: 'GitHub 取消失敗（HTTP ' + code + '）：' +
           String(res.getContentText() || '').slice(0, 180) };
}

/** 後台用：目前這張工單對應的 GitHub 執行狀態與連結。 */
function apiAdminRunInfo(key, runId) {
  try {
    adminAuth_(key);
    var g = githubCfg_();
    if (!g.repo || !g.token) {
      return { ok: true, configured: false,
               reason: '尚未設定 GITHUB_REPO 與 GITHUB_TOKEN' };
    }
    var id = String(runId || '').replace(/\D/g, '');
    var runUrl = id ?
      'https://api.github.com/repos/' + g.repo + '/actions/runs/' + id :
      'https://api.github.com/repos/' + g.repo + '/actions/workflows/' +
        encodeURIComponent(g.workflow) + '/runs?event=workflow_dispatch&per_page=1';
    var res = UrlFetchApp.fetch(runUrl,
      { muteHttpExceptions: true, headers: githubHeaders_(g.token) });
    if (res.getResponseCode() !== 200) {
      return { ok: false, reason: 'GitHub 回應 HTTP ' + res.getResponseCode() };
    }
    var json = JSON.parse(res.getContentText());
    var r = id ? json : (json.workflow_runs || [])[0];
    if (!r) { return { ok: true, configured: true, run: null }; }
    var failure = null;
    if (r.status === 'completed' &&
        ['failure', 'timed_out', 'startup_failure', 'action_required'].indexOf(r.conclusion) >= 0) {
      failure = githubFailureReason_(g, String(r.id));
    }
    // GitHub 已經結束、但 pipeline 來不及回報 Apps Script 時，不能讓後台永遠
    // 卡在「處理中」。在查詢 run 的同時校正會員簡訊工單，下一輪即可再啟動。
    if (id && r.status === 'completed') {
      var smsState = getSmsParseState_();
      if (smsState && smsState.status === '處理中' &&
          String(smsState.runId || '') === String(r.id)) {
        smsState.runUrl = r.html_url || smsState.runUrl || '';
        smsState.auditReady = true;
        if (r.conclusion === 'success') {
          smsState.status = '完成';
          smsState.note = 'GitHub Actions 已完成；後台已自動解除殘留的處理中狀態。';
        } else if (r.conclusion === 'cancelled') {
          smsState.status = '已取消';
          smsState.note = 'GitHub Actions 已取消；已完成資料保留。';
        } else {
          smsState.status = '失敗';
          smsState.lastError = failure ? failure.summary : ('GitHub 結束狀態：' + r.conclusion);
          smsState.note = smsState.lastError;
        }
        setSmsParseState_(smsState);
      }
    }
    return { ok: true, configured: true,
             run: { id: String(r.id), status: r.status, conclusion: r.conclusion,
                    url: r.html_url, started: r.run_started_at,
                    failureReason: failure ? failure.summary : '',
                    failedJob: failure ? failure.job : '',
                    failedStep: failure ? failure.step : '' } };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/**
 * 把原本在 Apps Script 跑的重活改派給 GitHub。
 * 全面重整、整理過去資料這類長時間工作，同樣不適合在 6 分鐘限制下跑。
 */
/**
 * 從指定的那一步開始刷新，一路做到最後。
 *
 * 與全面重整的「從這一步重跑」對齊：某一步失敗、修好之後不必整條鏈重來，
 * 挑那一步接著跑就好。前面已完成的不重做——刷新鏈的每一步彼此獨立，
 * 跳過前面幾步不會讓後面算錯（不像全面重整，那邊後面幾步依賴前面的結果）。
 *
 * 不帶 index 就是從頭跑，等同原本的「開始刷新」。
 */
function apiAdminStartRefreshFrom(key, index) {
  try {
    adminAuth_(key);
    var order = REFRESH_ORDER_;
    var i = Number(index);
    if (!(i >= 0 && i < order.length)) { i = 0; }

    // 上一次留下來的取消旗標要先拔掉，否則新的一輪一進來就被自己擋掉。
    clearChainCancel_();

    var inputs = { refresh_site: 'true' };
    if (i > 0) { inputs.refresh_from = order[i]; }
    var d = dispatchGithub_(inputs);
    if (!d.ok) { return { ok: false, reason: d.reason }; }
    return { ok: true, runUrl: d.runUrl, from: order[i],
             message: (i > 0 ? '從「' + REFRESH_STEPS_[order[i]].name + '」' : '從頭') +
                      '開始刷新，已交給 GitHub Actions。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

function apiAdminDispatch(key, mode) {
  try {
    adminAuth_(key);
    var MAP = {
      full: { repair_codes: 'true', fix_prices: 'true', refresh_site: 'true' },
      prices: { fix_prices: 'true', refresh_site: 'true' },
      codes: { repair_codes: 'true', refresh_site: 'true' },
      reconcile: { reconcile: 'true', refresh_site: 'true' },
      refresh: { refresh_site: 'true' }
    };
    var inputs = MAP[mode];
    if (!inputs) { return { ok: false, reason: '不認得的模式：' + mode }; }
    // 會用到刷新鏈的模式，開跑前先把上一次的取消旗標拔掉。
    if (inputs.refresh_site === 'true' && typeof clearChainCancel_ === 'function') {
      clearChainCancel_();
    }

    var d = dispatchGithub_(inputs);
    if (!d.ok) { return { ok: false, reason: d.reason }; }
    return { ok: true, runUrl: d.runUrl,
             message: '已交給 GitHub Actions 執行，可點下方連結看即時日誌。' };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}


/**
 * 清理影片清單裡的重複列。
 *
 * 同一天有多列時，保留逐字稿最長的那一列，其餘刪除。
 * 若被保留的那列沒有影片ID，會從被刪的列裡補一個真正的（非 MANUAL- 開頭）過來。
 *
 * 這是修正上面那個 bug 之前留下的資料，跑一次就好。
 * 先看執行紀錄確認要刪的都是空白列，再放心。
 */
function dedupeVideoRows() {
  var sh = getSheet_('影片清單');
  var vals = sh.getDataRange().getValues();
  if (vals.length < 3) { Logger.log('沒有足夠的列需要清理'); return; }

  var head = vals[0];
  var cId = head.indexOf('影片ID');
  var cDate = head.indexOf('發布日期');
  var cV1 = head.indexOf('原始逐字稿內容');
  var cV2 = head.indexOf('修飾後逐字稿內容');

  // 依日期分組
  var groups = {};
  for (var i = 1; i < vals.length; i++) {
    var d = fmtDate_(vals[i][cDate]);
    if (!d) { continue; }
    var len = String(vals[i][cV1] || '').length + String(vals[i][cV2] || '').length;
    (groups[d] = groups[d] || []).push({ row: i + 1, len: len,
                                         id: String(vals[i][cId] || '').trim() });
  }

  var toDelete = [];
  Object.keys(groups).forEach(function (d) {
    var g = groups[d];
    if (g.length < 2) { return; }
    g.sort(function (a, b) { return b.len - a.len; });     // 內容最長的排第一
    var keep = g[0];

    // 保留列沒有真正的影片ID時，從其他列撈一個過來
    if (!keep.id || keep.id.indexOf('MANUAL-') === 0) {
      for (var k = 1; k < g.length; k++) {
        if (g[k].id && g[k].id.indexOf('MANUAL-') !== 0) {
          sh.getRange(keep.row, cId + 1).setValue(g[k].id);
          Logger.log('  ' + d + ' 補上影片ID ' + g[k].id);
          break;
        }
      }
    }

    for (var j = 1; j < g.length; j++) {
      toDelete.push(g[j].row);
      Logger.log('  ' + d + ' 刪除第 ' + g[j].row + ' 列（逐字稿 ' + g[j].len +
                 ' 字，保留第 ' + keep.row + ' 列的 ' + keep.len + ' 字）');
    }
  });

  if (!toDelete.length) { Logger.log('沒有重複的列'); return 0; }

  // 由後往前刪，避免列號位移
  toDelete.sort(function (a, b) { return b - a; });
  toDelete.forEach(function (r) { sh.deleteRow(r); });

  CACHE.remove('txDates');
  Logger.log('已刪除 ' + toDelete.length + ' 列重複資料');
  return toDelete.length;
}

/* ------------------------------------------------------------------ *
 * 工單看門狗
 *
 * 為什麼需要
 * ----------
 * 工單是交給 GitHub Actions 跑的，而派工是一次 HTTP 請求——它會失敗：
 * GitHub 短暫 5xx、workflow_dispatch 被吞掉、Actions 排隊太久被取消、
 * 或那一輪自己掛了而沒有回報。這幾種情況的共同症狀都一樣：
 * 工單停在「處理中」，步驟不動，而且不會有任何錯誤訊息——
 * 因為根本沒有人在跑，也就沒有人會回報。
 *
 * 先前唯一的解法是人去後台按「續跑」。但這件事完全不需要人判斷：
 * 「超過 N 分鐘沒有任何進展」是機器看得出來的。
 *
 * 怎麼判斷「卡住」
 * ----------------
 * 只看「更新時間」有沒有往前走。上游每做完一步就會寫一次工單，
 * 所以正常執行時這個時間至少每一兩分鐘就會更新。
 * 超過 STUCK_MINUTES_ 都沒動，才算卡住。
 *
 * 門檻訂在 12 分鐘，比最慢的一步（潤飾，並行後約兩分半）寬鬆好幾倍。
 * 訂太短會在正常執行時重複派工，那比卡住更糟——GitHub 上會有兩份同時跑，
 * 兩邊搶著寫同一張試算表。
 *
 * 重試上限
 * --------
 * 最多自動重派 MAX_AUTO_RESUME_ 次。真的有問題的工單（例如逐字稿本身有問題）
 * 每次派工都會走到同一個地方失敗，無限重派只會把額度燒完。
 * 到上限就標成失敗並講清楚，交給人。
 * ------------------------------------------------------------------ */
var STUCK_MINUTES_ = 12;
var MAX_AUTO_RESUME_ = 3;
var AUTO_RESUME_PROP_ = 'adminJobAutoResume';


function _autoResumeCount_(jobId) {
  try {
    var raw = PropertiesService.getScriptProperties().getProperty(AUTO_RESUME_PROP_);
    var o = raw ? JSON.parse(raw) : {};
    return (o.id === jobId) ? (Number(o.n) || 0) : 0;
  } catch (e) { return 0; }
}


function _autoResumeBump_(jobId, n) {
  try {
    PropertiesService.getScriptProperties().setProperty(
      AUTO_RESUME_PROP_, JSON.stringify({ id: jobId, n: n, at: nowStamp_() }));
  } catch (e) { /* 記不住就下一輪重數，不影響本次判斷 */ }
}


/** 每 10 分鐘觸發一次。卡住的工單自己重新派工，不必等人來按。 */
function adminJobWatchdogJob() {
  var job = currentJob_();
  if (!job || job.status !== '處理中') { return; }
  // 不分步驟名稱。舊名字（重算A 之類）代表這張工單被殘留的觸發器動過，
  // 那更需要救——它已經偏離正軌了，而正軌就是重新派工給 GitHub。

  var last = new Date(String(job.updatedAt || job.startedAt || '').replace(/-/g, '/'));
  if (isNaN(last.getTime())) { return; }
  var idle = (Date.now() - last.getTime()) / 60000;
  if (idle < STUCK_MINUTES_) { return; }     // 還在動，不要插手

  var n = _autoResumeCount_(job.id) + 1;
  if (n > MAX_AUTO_RESUME_) {
    updateJob_({ status: '失敗',
                 note: '自動續跑已試 ' + MAX_AUTO_RESUME_ + ' 次仍然卡在「' + job.step +
                       '」，停止自動重試。請到 GitHub Actions 看最近一次執行的錯誤，' +
                       '或在後台重新送出逐字稿。' });
    Logger.log('看門狗：工單 ' + job.id + ' 自動續跑已達上限，標成失敗。');
    return;
  }

  Logger.log('看門狗：工單 ' + job.id + ' 停在「' + job.step + '」已 ' +
             Math.round(idle) + ' 分鐘，第 ' + n + ' 次自動重新派工。');
  _autoResumeBump_(job.id, n);

  var d = dispatchGithub_({ admin_job: 'true', refresh_site: 'true' });
  if (d.ok) {
    updateJob_({ note: '自動續跑（第 ' + n + ' 次）：停在「' + job.step + '」已 ' +
                       Math.round(idle) + ' 分鐘沒有進展，已重新派工給 GitHub Actions。' });
  } else {
    updateJob_({ note: '自動續跑（第 ' + n + ' 次）失敗：' + d.reason +
                       '　停在「' + job.step + '」已 ' + Math.round(idle) + ' 分鐘。' });
  }
}


/**
 * 裝上看門狗的觸發器。在編輯器執行一次即可，重複執行不會裝兩個。
 * 部署新版之後記得跑一次；沒裝也不會壞，只是卡住時要人去按續跑。
 */
function installAdminJobWatchdog() {
  var have = ScriptApp.getProjectTriggers().filter(function (t) {
    return t.getHandlerFunction() === 'adminJobWatchdogJob';
  });
  if (have.length) {
    return '看門狗已經裝過了（' + have.length + ' 個），不重複裝。';
  }
  ScriptApp.newTrigger('adminJobWatchdogJob').timeBased().everyMinutes(10).create();
  return '已裝上工單看門狗：每 10 分鐘檢查一次，' +
         '停超過 ' + STUCK_MINUTES_ + ' 分鐘沒有進展的工單會自動重新派工（最多 ' +
         MAX_AUTO_RESUME_ + ' 次）。';
}

/**
 * 手動派工啟動影片逐字稿爬取 (transcript.yml)。
 */
function apiAdminCrawlTranscript(key, dateStr) {
  try {
    adminAuth_(key);
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }

  var targetDate = fmtDate_(dateStr) || todayStr_();
  var d = dispatchGithub_({
    mode: 'auto',
    date: targetDate,
    force: 'true',
    once: 'true'
  }, 'transcript.yml');

  if (!d.ok) {
    return { ok: false, reason: '派工給 GitHub 失敗：' + d.reason };
  }

  return {
    ok: true,
    message: '已成功派工至 GitHub Actions 執行影片爬取（' + targetDate + '）。',
    runUrl: d.runUrl
  };
}

/** 只查指定workflow，不把每日稽核的run錯當成影片聽打。 */
function transcriptWorkflowRuns_(workflow){
  var g=githubCfg_();if(!g.repo||!g.token){throw new Error('尚未設定 GitHub 派工權限');}
  var r=UrlFetchApp.fetch('https://api.github.com/repos/'+g.repo+'/actions/workflows/'+encodeURIComponent(workflow)+'/runs?per_page=20',
    {headers:githubHeaders_(g.token),muteHttpExceptions:true});
  if(r.getResponseCode()!==200){throw new Error('GitHub 狀態 HTTP '+r.getResponseCode());}
  return JSON.parse(r.getContentText()).workflow_runs||[];
}
function transcriptTodayState_(day){
  var sh=getSheet_('影片清單'),last=sh.getLastRow();
  if(last<2){return {ready:false,manual:false,complete:false,processingStatus:'',processingDetail:''};}
  // 五分鐘備援只關心今天：先讀輕量日期欄，才讀命中的原稿列。
  // 舊版每棒整張讀入所有日期的原文、潤飾稿與排版 JSON，隨資料累積會拖慢觸發器。
  var dates=sh.getRange(2,2,last-1,1).getDisplayValues(),h=sh.getRange(1,1,1,11).getDisplayValues()[0];
  var rows=[];
  dates.forEach(function(v,i){if(fmtDate_(v[0])!==day){return;}
    var cells=sh.getRange(i+2,1,1,11).getDisplayValues()[0],r={};
    h.forEach(function(name,n){r[name]=cells[n];});rows.push(r);
  });
  // 與投稿、網站讀稿共用選列規則；同日舊列「完成」不能蓋住新貼原文的進度。
  var selected=selectTranscriptRow_(rows,'',day),chosen=selected&&selected.row;
  var source=chosen?String(chosen['逐字稿來源']||''):'',body=chosen?String(chosen['原始逐字稿內容']||'').trim():'';
  return {ready:body.length>200,manual:['手動','手動保留'].indexOf(source)>=0||(!source&&!!body),
    complete:!!chosen&&chosen['處理狀態']==='完成',processingStatus:chosen?String(chosen['處理狀態']||''):'',
    processingDetail:chosen?String(chosen['失敗原因']||''):''};
}
/** 取稿、每日流程共用的停播狀態；五分鐘觸發器只讀時間與類別兩欄。 */
function todayNoShowState_(day){
  try{
    var sh=getSheet_('系統狀態'),last=sh.getLastRow();if(last<2)return '';
    var rows=sh.getRange(2,1,last-1,2).getDisplayValues(),planned=false;
    for(var i=rows.length-1;i>=0;i--){
      if(String(rows[i][0]).indexOf(day)!==0)continue;
      if(rows[i][1]==='今日無直播')return '今日無直播';
      if(rows[i][1]==='預告停播')planned=true;
    }
    return planned?'預告停播':'';
  }catch(e){Logger.log('停播狀態暫時無法讀取，照一般取稿：'+e);return '';}
}
/** GitHub cron今天沒有觸發時，由獨立的GAS時鐘補派；原文落地後也補接pipeline。 */
function transcriptAutomationTick_(){
  var now=new Date(),day=Utilities.formatDate(now,TZ,'yyyy/MM/dd'),hm=Number(Utilities.formatDate(now,TZ,'HHmm'));
  if(Number(Utilities.formatDate(now,TZ,'u'))>5||isMarketHoliday_(now)||hm<1105||hm>1530){return;}
  var state=transcriptTodayState_(day);
  if(state.complete||(!state.ready&&state.manual)){return;}
  if(!state.ready){
    var noShow=todayNoShowState_(day);
    if(noShow==='今日無直播'||(noShow==='預告停播'&&hm<1230)){return;}
  }
  var workflow=state.ready?'daily.yml':'transcript.yml',g=githubCfg_();if(!g.repo||!g.token){return;}
  var runs=transcriptWorkflowRuns_(workflow);
  if(runs.some(function(r){return r.status!=='completed';})){return;}
  var recent=runs.filter(function(r){return Utilities.formatDate(new Date(r.created_at),TZ,'yyyy/MM/dd')===day;})[0];
  // 只取消「這一次」執行；過去直接因最新 run=cancelled 停整天，造成無人接手。
  // 下方 20 分鐘冷卻避免取消後立刻重派，之後仍由排程檢查原文是否落地。
  if(recent&&Date.now()-Date.parse(recent.created_at)<20*60000){return;}
  var p=PropertiesService.getScriptProperties(),key='txAutoDispatch_'+workflow,allowed=withLock_(function(){
    var old=JSON.parse(p.getProperty(key)||'null');if(old&&old.day===day&&(Date.now()-old.at<20*60000||old.count>=8)){return false;}
    p.setProperty(key,JSON.stringify({day:day,at:Date.now(),count:old&&old.day===day?old.count+1:1}));return true;
  });
  if(!allowed){return;}
  var result=dispatchGithub_(state.ready?{}:{mode:'auto',date:day,force:'false',once:'false'},workflow);
  if(!result.ok){Logger.log('自動取稿／稽核備援派工失敗：'+result.reason);return;}
  Logger.log(day+' 備援啟動 '+workflow+'；正式進度以GitHub執行及原文落地為準');
}
/* 今天暫停自動取稿（v54，Codex 規格 18）。
   「取消執行」只停這一次；暫停則是整天不再自動取稿——Apps Script 的補派、GitHub 的排程備援、pipeline 都不會再聽打，
   等管理者貼原稿。做法是把當天那一列的「逐字稿來源」設成既有的「手動保留」（三端本來就認這個值），不另外發明新旗標。
   已經有原文的日子不能暫停（那時暫停沒有意義，要改原文請用投稿覆蓋）。恢復＝把「手動保留」清掉。 */
function apiAdminHoldToday(key, on, dateStr) {
  try {
    adminAuth_(key);
    var day = fmtDate_(dateStr) || todayStr_();
    return withLock_(function () {
      var sh = getSheet_('影片清單'), vals = sh.getDataRange().getValues(), head = vals[0].map(String);
      var ci = function (k) { return head.indexOf(k); };
      var hit = -1;
      for (var i = vals.length - 1; i >= 1; i--) { if (fmtDate_(vals[i][ci('發布日期')]) === day) { hit = i; break; } }
      var body = hit > 0 ? String(vals[hit][ci('原始逐字稿內容')] || '').trim() : '';
      if (on) {
        if (body.length > 200) { return { ok: false, reason: day + ' 已經有原文，不需要暫停；要換原文請用「開始處理」並勾選覆蓋。' }; }
        if (hit > 0) { sh.getRange(hit + 1, ci('逐字稿來源') + 1).setValue('手動保留'); }
        else {
          var row = head.map(function () { return ''; });
          row[ci('影片ID')] = 'MANUAL-' + day.replace(/\//g, ''); row[ci('發布日期')] = day; row[ci('標題')] = '手動保留（後台暫停自動取稿）';
          row[ci('處理狀態')] = '等待中'; row[ci('逐字稿來源')] = '手動保留';
          sh.appendRow(row);
        }
        return { ok: true, held: true, message: day + ' 已暫停自動取稿：不會再自動聽打或判讀，等你在下方貼上原稿。要恢復按「恢復自動取稿」。' };
      }
      if (hit > 0 && String(vals[hit][ci('逐字稿來源')]) === '手動保留') {
        sh.getRange(hit + 1, ci('逐字稿來源') + 1).setValue('');
      }
      return { ok: true, held: false, message: day + ' 已恢復自動取稿，下一次排程（五分鐘內）會重新檢查。' };
    });
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}

function apiAdminCrawlState(key,dateStr){
  try{adminAuth_(key);
    var day=fmtDate_(dateStr)||todayStr_(),state=transcriptTodayState_(day);
    var onDay=function(r){return Utilities.formatDate(new Date(r.created_at),TZ,'yyyy/MM/dd')===day;};
    // GitHub 狀態端偶發逾時或權限不足時，試算表裡的 pipeline 進度仍要顯示。
    var runs=[],dailyRuns=[],githubWarning='';
    try{runs=transcriptWorkflowRuns_('transcript.yml');dailyRuns=transcriptWorkflowRuns_('daily.yml');}
    catch(ghError){githubWarning=String(ghError.message||ghError);}
    var r=runs.filter(onDay)[0]||null;
    var p=dailyRuns.filter(function(x){
      return onDay(x) && /每日資料流程|後台逐字稿/.test(String(x.display_title||''));
    })[0]||null;
    var job=displayJob_();if(job&&fmtDate_(job.date)!==day)job=null;
    var slim=function(x){return x?{id:String(x.id),url:x.html_url,status:x.status,conclusion:x.conclusion,at:x.created_at}:null;};
    return {ok:true,day:day,transcript:state,noShowState:todayNoShowState_(day),run:slim(r),pipelineRun:slim(p),job:job,githubWarning:githubWarning};
  }catch(e){return {ok:false,reason:String(e.message||e)};}
}
function apiAdminCancelCrawl(key,runId){
  try{adminAuth_(key);var r=transcriptWorkflowRuns_('transcript.yml').filter(function(x){return String(x.id)===String(runId);})[0];
    if(!r){return {ok:false,reason:'這不是目前可核對的取稿工單，請先更新進度。'};}
    return cancelGithubRun_(r.id);
  }catch(e){return {ok:false,reason:String(e.message||e)};}
}
