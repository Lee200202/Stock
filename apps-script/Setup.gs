/**
 * 檔案：Setup.gs
 * 一次性設定工具。部署前在 Apps Script 編輯器手動執行，之後不再需要動它。
 *
 * 執行順序：
 *   1. setupSpreadsheet()      建立試算表與七個分頁，並把 ID 存進 Script Properties
 *   2. （手動）到「專案設定 → 指令碼屬性」新增 GEMINI_API_KEY
 *   3. setOwnerEmail()         設定失敗告警要寄給誰
 *   4. installTriggers()       安裝每日推播、失敗告警、年度封存三個觸發器
 *   5. checkSetup()            檢查上述四項是否都就緒
 */

var PROP = PropertiesService.getScriptProperties();

var SHEET_SCHEMA = {
  '簡訊內容同步':['日期','版本','狀態','更新時間','備註'],
  '分K歷史':['代號','更新時間','資料JSON','來源','狀態'],
  'Yahoo分K':['代號','更新時間','資料JSON','來源','狀態'],
  '市場總覽快取':['代號','更新時間','資料JSON','來源','狀態'],
  '逐字稿判讀稽核': ['來源影片ID', '影片日期', '原文SHA256', '規則版本', '判讀JSON', '更新時間'],
  /* 「逐字稿來源」是自動取稿接回來之後新增的最後一欄，值只有三種：
       手動　　　人貼的（後台投稿、直接編輯這張表，或本機跑 transcript.py manual）
       手動保留　人宣告「今天我自己貼」，還沒貼
       自動　　　transcript.py 用 Gemini 聽 YouTube 產生的
     只要是前兩種，transcript.py 就對那一天完全停手，不覆蓋、不重抓。
     空白且已有原文的舊資料一律視同「手動」——那一欄是後來才加的，
     在它之前的原文全是人貼的，當成自動就等於允許機器覆蓋人工成果。
     這一欄由 ensureHeaders_ 自動補在最後面，既有試算表不必手動加。 */
  '影片清單': ['影片ID', '發布日期', '標題', '處理狀態', '失敗原因', '原始逐字稿內容', '修飾後逐字稿內容', '原文更新時間', '原文SHA256', '來源別名', '逐字稿來源'],
  // 「序」是同一天同一檔的動作先後。多數列是空的（一天只講一次），
  // 只有「早上賣掉、下午買回來」這種同日來回才會填 1、2。
  // 少了它，持股追蹤只能用固定的優先級猜順序（買一律排在賣前面），
  // 於是同日先賣後買會被算成「加碼之後那筆賣出不算數」，整個回合都錯。
  '操作紀錄': ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序'],
  '會員持股': ['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID'],
  '每日推播內容': ['日期', '文字稿', '寄送狀態'],
  /* 會員簡訊。盤中即時通知的原文，一則一列。
     解析出來的股票與價位會另外寫進操作紀錄與會員持股，
     來源影片ID 填 CMONEY-<文章ID>，看得出那一列是簡訊來的還是影片來的。 */
  /* 解析版本與判定時間是這一版新增的。

     只有 Gemini 真的判定過那一列才會寫值，空白代表「從來沒有判定過」。
     先前沒有這兩欄，判斷「解析過了沒有」只能看解析明細是不是空的，
     但抓取當下就會先塞一個 "[]" 佔位字串進去，於是「還沒判定」與
     「判定過、真的沒有個股」在資料上長得一模一樣——那正是
     「1519 華城 775 以上全數獲利賣出」被標成無可收錄的原因。 */
  '會員簡訊': ['文章ID', '發文時間', '標題', '原文', '解析狀態', '抓取時間', '通知狀態', '網址',
               '解析明細', '內容指紋', '最後偵測', '修訂次數', '解析版本', '判定時間'],
  // 後台補抓的逐篇稽核軌跡。日期解析失敗的文章也留在這裡，但絕不寫進會員簡訊。
  '會員簡訊稽核': ['作業ID', '文章ID', '發文時間', '日期', '標題', '原文', '張震判定', '範圍判定', '處理狀態', '說明', '網址', '更新時間'],
  '使用者訂閱清單': ['Email', '訂閱項目', '關注股票代號', '建立時間', '取消訂閱權杖', '狀態'],
  '使用者上下文記憶': ['使用者識別', '歷史互動摘要', '偏好設定', '更新時間'],
  '資料索引': ['封存分頁名稱', '來源工作表', '涵蓋期間', '建立時間'],
  /* 使用紀錄。訪客識別是瀏覽器本地產生的隨機碼，不是 Email、也不是 IP，
     清掉瀏覽器資料就換一個新的，這邊無從還原成任何真人身分。
     只留 90 天：它的價值是「這一週大家在看什麼」，不是永久檔案。 */
  '使用紀錄': ['時間', '訪客識別', '頁面', '動作', '對象', '停留秒數', '裝置'],
  // 代號與名稱的權威對照，來自證交所與櫃買中心公開清單。每週更新。
  '股票對照表': ['代號', '名稱', '市場', '產業', '更新時間'],
  // 日K 落地快取。線上不再逐月向證交所抓，網站載入才會快。
  '日K快取': ['代號', '日期', '開', '高', '低', '收', '量'],
  // 盤中現價落地快取。前端直接讀這張，不對外請求。
  '即時快取': ['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期'],
  // 每日收盤後記一筆整體績效，供績效走勢折線圖使用。往前補不了。
  '每日績效': ['日期', '追蹤檔數', '持有檔數', '平均報酬', '正報酬比例'],
  // 盤中每 5 分鐘寫入一次，收盤後聚合成小時K 就清空。這張表是暫存，不保留。
  '盤中快照': ['日期', '時間', '代號', '成交價', '累計成交量'],
  // 60 分 K。當日部分由 Fugle 提供，收盤後落地。往前補不了。
  '小時K': ['日期', '時段', '代號', '開', '高', '低', '收', '量'],
  // 基本面落地快取。線上不再向證交所抓兩千筆，個股面板才不會卡住。
  '基本面快取': ['代號', '名稱', '市場', '產業', '本益比', '股價淨值比', '殖利率', '上市櫃日', '董事長', '更新時間'],
  // 進場價是歷史事實，算一次就固定。現價由即時快取提供，不存這裡。
  // 一檔可能買了又賣、賣了又買，所以用「回合」表示。前 12 欄描述最新一個回合，
  // 後 6 欄補上回合資訊、出場原因與參考價位。欄位由程式依表頭名稱尋找，順序可調。
  // 後台手動投稿用。工單記錄每一次投稿的處理進度，
  // 影片候選由每 10 分鐘的偵測工作寫入。
  '後台工單': ['工單ID', '日期', '影片ID', '狀態', '步驟', '已完成', '總數', '備註', '開始時間', '更新時間', '來源', '原文SHA256', '執行網址', '程式版本'],
  // 自動取稿的工單（v54）。由 pipeline.py 的 open_auto_job 寫，欄位與後台工單相同。
  // 與後台工單分開放：這裡十幾處用「最後一列就是目前工單」做業務判斷，混在一起會互相干擾。
  // 管理者直接修正某一回合的成本（v54）。以「代號＋回合開始日」對到那一回合，重算持股追蹤時套用；
  // 回合換了（賣出後又買回）就不會誤套到新回合。系統原判的成本與來源保留在「修改前成本」「修改前來源」。
  '持股成本覆寫': ['代號', '股票名稱', '回合開始日', '成本', '備註', '修改時間', '修改前成本', '修改前來源'],
  // 逐收件者寄送帳本（v54）：每一則信 × 每一位收件者一列。見 MailService.gs 的 deliverMessage_。
  '寄送帳本': ['訊息ID', '種類', '日期', '內容版本', '收件者', '狀態', '嘗試次數', '最後錯誤', '更新時間', '服務接受時間'],
  '自動工單': ['工單ID', '日期', '影片ID', '狀態', '步驟', '已完成', '總數', '備註', '開始時間', '更新時間', '來源', '原文SHA256', '執行網址', '程式版本'],
  '今日影片候選': ['日期', '影片ID', '標題', '網址', '偵測時間'],
  '持股追蹤': ['代號', '股票名稱', '首次買入日', '進場價', '進場價來源', '最近賣出日', '出場價', '狀態', '提及次數', '首次理由', '逐日說明', '更新時間', '回合數', '本回合進場日', '出場原因', '回合明細', '參考價位', '參考價位來源', '首次進場方式', '本回合進場方式', '累積報酬', '各回合報酬', '回合JSON', '最新說明日期', '進場明講', '出場明講'],
  // GitHub Actions 每一輪的執行結果。狀態信與認證過期告警都讀這張表。
  '系統狀態': ['時間', '類別', '說明', '模式', '執行環境'],
  // AI 稽核提出的修正建議。核准後才會套用，保留稽核軌跡。
  '修正建議': ['提出時間', '日期', '動作', '分頁', '股票名稱', '代號', '內容', '狀態', '處理時間'],
  // 人工補登的軌跡。講者刻意不講股名的那幾段，只能由管理者事後補進去，
  // 這張表留下他當時的依據：哪一天、哪一檔、根據哪一段話。
  // 操作紀錄裡那一列看起來與 AI 擷取的沒有兩樣，事後要查只能查這裡。
  '人工補登': ['補登時間', '日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '逐字稿節錄', '狀態']
};

/**
 * 步驟 1：建立雲端試算表。
 * 執行後請看「執行紀錄」，裡面會印出試算表 ID 與網址，同時已自動寫入 Script Properties。
 * 這支就是你要的「直接建立試算表 ID」。
 */
function setupSpreadsheet() {
  var existing = PROP.getProperty('SPREADSHEET_ID');
  if (existing) {
    Logger.log('已存在試算表 ID：' + existing);
    Logger.log('若要重建，請先手動清除 Script Properties 的 SPREADSHEET_ID。');
    return existing;
  }

  var ss = SpreadsheetApp.create('張震股市盤中家教班_資料庫');

  Object.keys(SHEET_SCHEMA).forEach(function (name, idx) {
    var sh = (idx === 0) ? ss.getSheets()[0] : ss.insertSheet();
    sh.setName(name);
    var headers = SHEET_SCHEMA[name];
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    sh.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#EDF0EE');
    sh.setFrozenRows(1);
    sh.autoResizeColumns(1, headers.length);
  });

  var id = ss.getId();
  PROP.setProperty('SPREADSHEET_ID', id);

  Logger.log('試算表已建立。');
  Logger.log('SPREADSHEET_ID = ' + id);
  Logger.log('網址：' + ss.getUrl());
  Logger.log('請把上面這組 ID 一併存入 GitHub Secrets 的 SPREADSHEET_ID。');
  Logger.log('另外記得到試算表右上角「共用」，把 GitHub Actions 用的服務帳號 Email 加為編輯者。');
  return id;
}

/** 步驟 3：設定收信人（失敗告警寄給你本人，不是寄給訂閱者） */
function setOwnerEmail() {
  var email = Session.getEffectiveUser().getEmail();
  PROP.setProperty('OWNER_EMAIL', email);
  Logger.log('OWNER_EMAIL = ' + email);
  Logger.log('若要改寄別的信箱，請到「專案設定 → 指令碼屬性」直接改這一欄。');
}

/** 步驟 4：安裝觸發器 */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });

  /* 只裝十個。
   *
   * Apps Script 每個指令碼的觸發器上限是 20 個，而先前這裡剛好裝滿 20 個，
   * 一格都不剩。後果是任何一次性觸發器都建不出來：後台的全面重整按下去會跳
   * 「這個指令碼包含過多觸發條件」，而每日品質關卡更糟——它是在 dailyPushJob
   * 裡面排的，建不出來就往外拋，整封推播信寄不出去，畫面上還什麼都看不到。
   *
   * 原本那十二個「只是在等某個時刻」的（推播三次、狀態信三次、報價、看門狗、
   * 認證守望、影片偵測、稽核收尾、失敗告警）全部併進 everyFiveMinJob，
   * 由它依時間決定該做哪一件。空出十一格給一次性觸發器用。
   *
   * 後來多了一個 cmoneyPollJob，它必須每分鐘一次（會員簡訊要即時），
   * 併不進五分鐘那一棒，所以自己佔一格。剩十格。 */
  ScriptApp.newTrigger('everyFiveMinJob').timeBased().everyMinutes(5).create();

  /* 會員簡訊要每分鐘看一次，所以自己佔一個觸發器，不能併進五分鐘那一棒。
     它自己會判斷平日與時段，非時段一進去就返回，一棒不到 0.1 秒。
     沒有設定 CMONEY_MEMBER_ID 時整支不動作，所以裝著也不會有事。

     先確認函式存在。Cmoney.gs 是獨立的一個檔案，忘了建就裝了一個
     每分鐘失敗一次的觸發器——一天 1440 封執行失敗通知，而且看不出原因。 */
  if (typeof cmoneyPollJob === 'function') {
    ScriptApp.newTrigger('cmoneyPollJob').timeBased().everyMinutes(1).create();
  } else {
    Logger.log('※ 找不到 cmoneyPollJob，會員簡訊那一個觸發器沒有裝。');
    Logger.log('  請先把 Cmoney.gs 建起來再執行一次 installTriggers()。');
  }

  // 下午這一串各自都是重活（抓 K 線、算追蹤），必須各佔一次完整的執行額度，
  // 不能併進五分鐘那一棒，否則一次跑不完會被時間上限腰斬。
  ScriptApp.newTrigger('aggregateHourlyJob').timeBased().atHour(14).nearMinute(5).everyDays(1).create();
  ScriptApp.newTrigger('repairCodesJob').timeBased().atHour(14).nearMinute(20).everyDays(1).create();
  ScriptApp.newTrigger('rebuildFundamentalsJob').timeBased().atHour(14).nearMinute(30).everyDays(1).create();
  // 16:45：富果歷史行情 16:30 前完成當日更新（見 Cachebuilder.gs 的 DK_DAILY_HOUR_ 說明）
  ScriptApp.newTrigger('backfillDailyKJob').timeBased().atHour(DK_DAILY_HOUR_).nearMinute(DK_DAILY_MINUTE_).everyDays(1).create();
  ScriptApp.newTrigger('rebuildHoldingsTrackerJob').timeBased().atHour(14).nearMinute(50).everyDays(1).create();
  ScriptApp.newTrigger('snapshotPerformanceJob').timeBased().atHour(15).nearMinute(5).everyDays(1).create();

  ScriptApp.newTrigger('rebuildCodeMapJob').timeBased().onWeekDay(ScriptApp.WeekDay.SUNDAY).atHour(4).create();
  ScriptApp.newTrigger('yearlyArchiveJob').timeBased().onMonthDay(5).atHour(3).create();

  var n = ScriptApp.getProjectTriggers().length;
  Logger.log('已安裝 ' + n + ' 個觸發器，還剩 ' + (20 - n) + ' 格給一次性觸發器。');
  Logger.log('');
  Logger.log('  everyFiveMinJob            每 5 分鐘。它依時間再決定要做哪一件：');
  Logger.log('      每一棒        報價快取、看門狗');
  Logger.log('      每 30 分鐘    認證過期守望');
  Logger.log('      每 10 分鐘    今日影片偵測（僅平日 11:30-17:00）');
  Logger.log('      12:00-22:00   每一棒試一次推播（已寄或未就緒會立刻返回）');
  Logger.log('      12:30/13:10/15:50  狀態報告');
  Logger.log('      13:45         盤中快照聚合成小時K');
  Logger.log('      15:20         品質關卡收尾保險');
  Logger.log('      15:45         失敗告警');
  Logger.log('  cmoneyPollJob              每 1 分鐘。平日 09:00-15:00 看有沒有新的會員簡訊；');
  Logger.log('                             非時段或沒設 CMONEY_MEMBER_ID 會立刻返回');
  Logger.log('  aggregateHourlyJob         14:05  當日 60 分 K 落地');
  Logger.log('  repairCodesJob             14:20  修代號待確認');
  Logger.log('  rebuildFundamentalsJob     14:30  更新基本面快取');
  Logger.log('  rebuildHoldingsTrackerJob  14:50  重算持股追蹤（收盤後第一版，用即時報價）');
  Logger.log('  snapshotPerformanceJob     15:05  記錄當日績效（第一版）');
  Logger.log('  backfillDailyKJob          16:45  補日K快取（富果 16:30 前更新完當日資料；沒補完自動續跑，');
  Logger.log('                                    整輪完成後自動 afterDailyKDoneJob：重算持股追蹤 → 記錄績效）');
  Logger.log('  rebuildCodeMapJob          每週日 04:00');
  Logger.log('  yearlyArchiveJob           每月 5 號 03:00');
  Logger.log('');
  Logger.log('順序是有意義的：');
  Logger.log('  修代號在前，追蹤才有合格輸入；補日K做完整輪才接著重算追蹤與績效，進場價才用得到當天的正式K線。');
  Logger.log('  推播改成 12:00 起每五分鐘試一次，內容一備妥就寄，不必等整點。');
  Logger.log('  告警排在 15:45，因為輪詢期間狀態本來就是等待中，提早檢查會天天誤報。');
}
/* ==================================================================== *
 * 每五分鐘一棒：所有「看時間決定要不要做」的工作都由它派
 *
 * 為什麼要合併
 * ------------
 * Apps Script 每個指令碼的觸發器上限是 20 個，而原本 installTriggers()
 * 剛好建了 20 個——一格都不剩。於是任何一次性觸發器都建不出來：
 *   全面重整的 runFullFix_      → 按下去跳「包含過多觸發條件」
 *   每日品質關卡的 runQualityGate_ → 同樣建不出來，而且它是在 dailyPushJob
 *                                    裡面呼叫的，例外往外拋就讓整封信寄不出去
 * 後者是無聲的：信沒寄出去，畫面上什麼都看不到。
 *
 * 把 12 個「只是在等某個時刻」的觸發器併成這一個，就空出 11 格。
 *
 * 順便修掉一個一直存在的脆弱點
 * ----------------------------
 * 原本每件事都綁死在一個時刻（13:00 推播、13:10 狀態信）。Apps Script 的
 * 時間觸發器是 best effort，那一棒被跳過，那件事整天就不會發生。
 * 改成「過了時間而且今天還沒做過就做」，漏一棒下一棒會補上。
 *
 * 每一件都用 safe_ 包起來
 * ----------------------
 * 十幾件事現在跑在同一次執行裡，任何一件拋例外都會讓後面的全部不執行。
 * 包起來之後，壞掉的那一件只影響它自己，其餘照常。
 * ==================================================================== */

var DAY_MARK_KEY = 'dayMarks';

/** 執行一件事，出錯只記錄不往外拋。 */
function safe_(name, fn) {
  try { fn(); }
  catch (e) { Logger.log('每五分鐘一棒：' + name + ' 失敗（不影響其他工作）：' + e); }
}

/** 每 n 分鐘才做一次。用上次執行時間判斷，不依賴觸發器的精準度。 */
function dueEvery_(key, minutes) {
  var pr = PropertiesService.getScriptProperties();
  var k = 'due_' + key;
  var last = Number(pr.getProperty(k) || 0);
  var now = Date.now();
  if (now - last < minutes * 60 * 1000) { return false; }
  pr.setProperty(k, String(now));
  return true;
}

/** 今天過了 target 這個時刻、而且還沒做過就做。做過的用日期加標記記著。 */
function onceAfter_(today, mark, hhmm, target, name, fn) {
  if (hhmm < target) { return; }
  var pr = PropertiesService.getScriptProperties();
  var raw = pr.getProperty(DAY_MARK_KEY) || '{}';
  var st;
  try { st = JSON.parse(raw); } catch (e) { st = {}; }
  // 換日就整包丟掉，標記不會無限累積
  if (st.date !== today) { st = { date: today }; }
  if (st[mark]) { return; }
  st[mark] = 1;
  pr.setProperty(DAY_MARK_KEY, JSON.stringify(st));
  safe_(name, fn);
}

function everyFiveMinJob() {
  // 監控頁用這個時間判斷五分鐘總排程有沒有停擺；只記啟動，不宣稱後續工作成功。
  try { PropertiesService.getScriptProperties().setProperty('OPS_HEARTBEAT_AT', String(Date.now())); } catch (e) { Logger.log('監控心跳無法寫入：' + e); }
  var tz = 'Asia/Taipei';
  var now = new Date();
  var hhmm = Number(Utilities.formatDate(now, tz, 'HHmm'));
  var today = Utilities.formatDate(now, tz, 'yyyy/MM/dd');
  var weekday = Number(Utilities.formatDate(now, tz, 'u')) <= 5;

  // 先守住取稿與稽核派工，不能排在耗時行情工作之後而被六分鐘上限截掉。
  safe_('transcriptAutomationTick_', transcriptAutomationTick_);
  safe_('marketSnapshotJob', marketSnapshotJob);
  // 郵件查詢的待同步內容先落地；行情／K線更新可能耗盡本輪六分鐘。
  // 先前簡訊已解析、逐字稿也寫完，但這一步排在行情後面而一直顯示舊內容。
  safe_('cmSyncContentTick_', cmSyncContentTick_);
  /* 寄信排在行情與K線之前（v54，Codex 規格 89）：先前 dailyPushJob 排在最後面，
     前面的行情工作吃掉執行時間時，信就一直沒寄。兩支沒事做時都在一秒內返回。 */
  safe_('deliveryRetryTick_', deliveryRetryTick_);
  if (weekday && hhmm >= 1200 && hhmm <= 2200) { safe_('dailyPushJob', dailyPushJob); }

  // ---- 每一棒都做。兩支自己都會判斷該不該動作，非盤中會立刻返回。 ----
  safe_('refreshQuoteCacheJob', refreshQuoteCacheJob);
  safe_('watchdogJob', watchdogJob);
  safe_('resumePendingTranscriptRefresh_', resumePendingTranscriptRefresh_);

  // ---- 依間隔 ----
  // K 線逐檔快取 6 小時到期，5.5 小時重新整批寫一次，訪客不會碰到「整張表重讀」的那一次
  // 執行時間預算（v54，Codex 規格 20）：前面已經用掉 150 秒就不做這種可以晚一棒的暖快取。
  if (Date.now() - dkExecStart_(Date.now()) < 150 * 1000 && dueEvery_('kcacheWarm', 330)) { safe_('warmKCaches_', warmKCaches_); }
  if (weekday && hhmm >= 1130 && hhmm <= 1700 && dueEvery_('pollVideo', 10)) {
    safe_('pollTodayVideoJob', pollTodayVideoJob);
  }

  /* 盤中快照。每五分鐘記一次現價與累計量，收盤後聚合成小時K。

     這種資料往前一天都補不了——證交所不提供歷史盤中行情，
     所以只能從今天開始往後累積。那也是為什麼它值得每天做。
     函式自己會判斷是不是盤中，非盤中立刻返回。 */
  safe_('snapshotIntradayJob', snapshotIntradayJob);

  /* 解析統一交給 GitHub Actions。新文與文章修訂會立即派工；這裡每五分鐘
     補看一次待解析列，負責接回短暫派工失敗的情況。 */
  safe_('cmDispatchPendingGithubJob_', function () { cmDispatchPendingGithubJob_(); });
  // 自動稽核補抓：來源上今天的張震簡訊不在會員簡訊分頁就補存並派解析（自己控制頻率與時段）。
  safe_('cmAutoReconcileToday_', function () { cmAutoReconcileToday_(false); });
  // 後台大量補抓與重新解析由一次性觸發器快速接力；若其中一棒被 Apps Script
  // 硬切斷，這裡會在三分鐘無更新後從已保存的階段接回，不會無聲卡死。
  safe_('cmAdminWatchdog_', function () { cmAdminWatchdog_(); });

  safe_('dayEditSyncTick_', function(){dayEditSyncTick_();});
  if (!weekday) { transcriptLayoutTick_(); return; }   // 週末不開盤，下面的都不做（逐字稿補排照做）

  /* ---- 推播 ----

     窗口是 12:00 到 22:00，中間每一棒都試一次。兩端各有理由。

     早的那端從 13:00 提早到 12:00。上游的 VOD_EARLIEST_HOUR 就是 12——
     直播約 12:30 到 13:00 結束，但收得早的那幾天影片十二點多就上架，
     逐字稿與十四道檢查跑完，十二點半就備妥了。窗口卡在 13:00 的話，
     那些日子的信會平白在試算表裡躺半小時到一小時才寄出去。
     現在是「12:00 備妥就寄，沒備妥就 12:05、12:10⋯⋯下一棒再問」。

     晚的那端原本是 15:40，太窄：上游跑得晚一點（重跑、配額退避、
     GitHub 排隊），文章在 15:40 之後才落地的話，就再也沒有任何一棒會呼叫
     dailyPushJob，當天的信永遠不會寄——而且信箱只是安靜地沒有東西，
     沒有任何徵兆。現在拉到 22:00。

     多問幾十次的成本接近零：dailyPushJob 進去第一件事就是查狀態，
     已寄送或還沒備妥都立刻返回，一棒不到一秒。
     寧可多問，也不要有一天因為晚了十分鐘就整天沒有信。 */
  // （dailyPushJob 已移到這一棒的開頭，見上面「寄信排在行情與K線之前」。）

  // ---- 一天各做一次 ----
  onceAfter_(today, 'status1', hhmm, 1230, 'statusReportJob', statusReportJob);
  onceAfter_(today, 'status2', hhmm, 1310, 'statusReportJob', statusReportJob);
  onceAfter_(today, 'gate',    hhmm, 1520, 'auditAutoFixJob', auditAutoFixJob);
  onceAfter_(today, 'alert',   hhmm, 1545, 'failureAlertJob', failureAlertJob);
  onceAfter_(today, 'status3', hhmm, 1550, 'statusReportJob', statusReportJob);
  // 績效走勢保底（v54）：不等補日K，最後一筆早於今天就補記一筆。原因見 ensurePerformanceContinuityJob_。
  onceAfter_(today, 'perfSafe1', hhmm, 1730, 'ensurePerformanceContinuityJob_', ensurePerformanceContinuityJob_);
  onceAfter_(today, 'perfSafe2', hhmm, 1930, 'ensurePerformanceContinuityJob_', ensurePerformanceContinuityJob_);

  /* 收尾巡檢。到這個時間還沒寄出去，就把原因記進系統狀態。

     上面那些 return 都是靜默的，信沒來時查不出是哪一關擋下來的。
     這一棒把當下的原因寫進「系統狀態」分頁，隔天回頭看得到，
     狀態報告信也帶得上。它只記錄，不改任何東西。 */
  onceAfter_(today, 'pushcheck', hhmm, 1800, 'pushCheckJob', pushCheckJob);

  /* 收盤後把當天的盤中快照聚合成小時K，然後清掉快照。
     排在 13:45——收盤是 13:30，留一點時間讓最後一批快照寫完。 */
  onceAfter_(today, 'hourly', hhmm, 1345, 'aggregateSnapshotJob', aggregateSnapshotJob);

  // 使用紀錄只留 90 天。一天清一次就夠，排在收盤後的離峰時間。
  onceAfter_(today, 'prune', hhmm, 1900, 'pruneUsageLogJob', pruneUsageLogJob);

  transcriptLayoutTick_();
}

/* 逐字稿自動補排（2026/09/17 v44）。放在每一棒的最後：前面的推播、快照都做完才排，
   這一棒前面已經用掉超過 90 秒就先不排，留給下一棒，不跟推播搶六分鐘的執行時間。
   每 15 分鐘最多一次，一次排一天（見 SheetService.gs 的 ensureTranscriptLayoutJob）。 */
function transcriptLayoutTick_() {
  if (Date.now() - dkExecStart_(Date.now()) > 90 * 1000) { return; }
  if (!dueEvery_('txLayout', 15)) { return; }
  safe_('ensureTranscriptLayoutJob', function () { ensureTranscriptLayoutJob(); });
}


/** 收尾巡檢：今天的信還沒寄的話，把原因記下來。只記錄，不動任何資料。 */
function pushCheckJob() {
  var d = todayStr_();
  var sent = '';
  try {
    var rows = getSheet_('每日推播內容').getDataRange().getValues();
    for (var i = 1; i < rows.length; i++) {
      if (fmtDate_(rows[i][0]) === d) { sent = String(rows[i][2] || ''); break; }
    }
  } catch (e) { sent = '讀不到：' + e; }

  if (String(sent).indexOf('已寄送') === 0) { return; }

  var why = '';
  try {
    var raw = PropertiesService.getScriptProperties().getProperty(PUSH_WHY_KEY);
    why = raw ? (JSON.parse(raw).why || '') : '';
  } catch (e) { /* 沒有就空著 */ }

  var msg = d + ' 到 18:00 仍未寄出。寄送狀態「' + (sent || '空白') +
            '」，最後的原因：' + (why || '沒有記錄') +
            '。在編輯器執行 whyNoMail() 看完整狀態，forceSendToday() 可強制補寄。';
  Logger.log(msg);
  // 「系統狀態」沒有現成的寫入函式，直接補一列。欄位順序照 SHEET_SCHEMA。
  try {
    var sh = getSheet_('系統狀態');
    sh.appendRow([nowStamp_(), '推播未寄出', msg, 'push-check', 'Apps Script']);
  } catch (e) { /* 記不進去就只留在執行紀錄 */ }
}


/**
 * 首次啟用，或改版後要補齊資料時，執行這一支。
 * 會依正確順序把所有東西建起來。每一步都印進度，中途失敗看得出卡在哪。
 */
function bootstrapAll() {
  Logger.log('=== 1/6 補建缺少的分頁 ===');
  addMissingSheets();

  Logger.log('=== 2/6 建立股票對照表 ===');
  rebuildCodeMapJob();

  Logger.log('=== 3/6 修復代號待確認 ===');
  repairCodesJob();

  Logger.log('=== 3.5/6 建立基本面快取 ===');
  rebuildFundamentalsJob();

  Logger.log('=== 4/6 補日K快取（這裡只補 3 分鐘，其餘自動續跑）===');
  backfillDailyKJob({ deadline: Date.now() + 3 * 60 * 1000, source: 'bootstrapAll' });

  Logger.log('=== 5/6 重算持股追蹤 ===');
  rebuildHoldingsTrackerJob();

  Logger.log('=== 6/6 回補績效走勢 ===');
  backfillPerformanceJob();

  Logger.log('');
  Logger.log('完成。第 4 步若沒補完會自動續跑，用 backfillDailyKStatus() 看進度；');
  Logger.log('顯示「已完成」之後再重跑一次 rebuildHoldingsTrackerJob()。');
}

/**
 * 給既有專案用：試算表已經建好，只是缺了新加的分頁。
 * 會補上缺少的分頁，不會動到既有資料。
 */
function addMissingSheets() {
  var ss = getSS_();
  var added = [];
  Object.keys(SHEET_SCHEMA).forEach(function (name) {
    if (ss.getSheetByName(name)) { return; }
    var sh = ss.insertSheet(name);
    var headers = SHEET_SCHEMA[name];
    sh.getRange(1, 1, 1, headers.length).setValues([headers]);
    sh.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#EDF0EE');
    sh.setFrozenRows(1);
    added.push(name);
  });
  Logger.log(added.length ? '已補建分頁：' + added.join('、') : '所有分頁都已存在');
  return added;
}

/** 步驟 5：檢查設定是否就緒。不會印出任何金鑰內容。 */
function checkSetup() {
  var out = [];
  var id = PROP.getProperty('SPREADSHEET_ID');
  out.push('SPREADSHEET_ID：' + (id ? '已設定（' + id + '）' : '尚未設定，請先執行 setupSpreadsheet()'));

  var key = PROP.getProperty('GEMINI_API_KEY');
  out.push('GEMINI_API_KEY：' + (key ? '已設定（長度 ' + key.length + '，內容不顯示）' : '尚未設定'));

  var fugle = PROP.getProperty('FUGLE_API_KEY');
  out.push('FUGLE_API_KEY：' + (fugle ? '已設定（長度 ' + fugle.length + '，內容不顯示）'
    : '尚未設定。沒有這個仍可運作，會退回證交所介面，但沒有當日 60 分 K。'));

  var owner = PROP.getProperty('OWNER_EMAIL');
  out.push('OWNER_EMAIL：' + (owner ? owner : '尚未設定，請執行 setOwnerEmail()'));

  var triggers = ScriptApp.getProjectTriggers().map(function (t) { return t.getHandlerFunction(); });
  out.push('觸發器：' + (triggers.length ? triggers.length + ' 個（' + triggers.join('、') + '）' : '尚未安裝'));

  if (id) {
    var ss = SpreadsheetApp.openById(id);
    var missing = Object.keys(SHEET_SCHEMA).filter(function (n) { return !ss.getSheetByName(n); });
    out.push('工作表分頁：' + (missing.length ? '缺少 ' + missing.join('、') + '，請執行 addMissingSheets()'
      : Object.keys(SHEET_SCHEMA).length + ' 個分頁齊全'));

    ['股票對照表', '基本面快取', '日K快取', '持股追蹤', '每日績效', '小時K'].forEach(function (n) {
      var sh = ss.getSheetByName(n);
      out.push('  ' + n + '：' + (sh ? Math.max(sh.getLastRow() - 1, 0) + ' 筆' : '不存在'));
    });
  }

  Logger.log(out.join('\n'));
  return out.join('\n');
}

/**
 * 持股追蹤沒東西時執行這一支，它會告訴你卡在哪一環。
 * 這是為了避免再次發生「追蹤表空白但看不出原因」的狀況。
 */
function diagnoseTracker() {
  var out = [];
  var trades = readSheetObjects_('操作紀錄');
  out.push('操作紀錄共 ' + trades.length + ' 筆');

  var buys = trades.filter(function (r) { return String(r['方向']).indexOf('買') === 0; });
  out.push('其中買入 ' + buys.length + ' 筆');

  var valid = buys.filter(function (r) { return /^(?:00981A|\d{4,6})$/.test(String(r['代號'] || '').trim()); });
  var invalid = buys.length - valid.length;
  out.push('買入當中代號合格的 ' + valid.length + ' 筆，代號待確認或空白的 ' + invalid + ' 筆');

  if (invalid > 0) {
    out.push('');
    out.push('>>> 代號不合格的那些會被持股追蹤直接濾掉，這是追蹤表空白的最常見原因。');
    out.push('>>> 請執行 repairCodesJob() 嘗試修復，仍修不好的需要人工看影片確認。');
    var samples = buys.filter(function (r) { return !/^(?:00981A|\d{4,6})$/.test(String(r['代號'] || '').trim()); })
      .slice(0, 8).map(function (r) { return r['股票名稱'] + '（' + r['代號'] + '）'; });
    out.push('    例如：' + samples.join('、'));
  }

  var map = readSheetObjects_('股票對照表');
  out.push('');
  out.push('股票對照表 ' + map.length + ' 檔' + (map.length ? '' : '　<<< 是空的，請執行 rebuildCodeMapJob()'));

  var kcache = readSheetObjects_('日K快取');
  var kcodes = {};
  kcache.forEach(function (r) { kcodes[String(r['代號'])] = 1; });
  out.push('日K快取 ' + kcache.length + ' 根，涵蓋 ' + Object.keys(kcodes).length + ' 檔');

  var tracker = readSheetObjects_('持股追蹤');
  out.push('持股追蹤 ' + tracker.length + ' 檔'
    + (tracker.length ? '' : '　<<< 是空的，請依序執行 repairCodesJob → backfillDailyKJob → rebuildHoldingsTrackerJob'));

  if (tracker.length) {
    var noEntry = tracker.filter(function (r) { return !Number(r['進場價']); });
    if (noEntry.length) {
      out.push('  其中 ' + noEntry.length + ' 檔沒有進場價，代表日K快取還沒補到那一天。請執行 backfillDailyKJob()（會自動續跑到補完），用 backfillDailyKStatus() 看進度。');
    }
  }

  Logger.log(out.join('\n'));
  return out.join('\n');
}

/** 取得試算表物件，其他 Service 檔共用 */
function getSS_() {
  var id = PROP.getProperty('SPREADSHEET_ID');
  if (!id) { throw new Error('尚未設定 SPREADSHEET_ID，請先執行 setupSpreadsheet()。'); }
  return SpreadsheetApp.openById(id);
}

/**
 * 取得分頁。不存在就依 SHEET_SCHEMA 自動補建。
 *
 * 會自動補建，是因為每次改版新增分頁都要記得手動執行 addMissingSheets()，
 * 忘一次整條排程就炸，而且錯誤訊息只說「找不到工作表分頁」，看不出要做什麼。
 * 讓它自己長出來比較不會出事。
 */
/* 這次執行已經確認過表頭的分頁。

   改版新增欄位時，既有的試算表不會自己長出那一欄——getSheet_ 只補建
   整張不存在的分頁，不管欄位。結果是新程式用 head.indexOf('序') 找不到，
   回 -1，然後那一欄就安靜地永遠寫不進去，沒有任何錯誤訊息。

   所以這裡順手補欄位。用記憶體記著「這次執行已經檢查過」，
   一次執行每張分頁只多讀一列表頭，成本接近零。 */
var _HEAD_CHECKED = {};

function ensureHeaders_(sh, name) {
  if (_HEAD_CHECKED[name]) { return; }
  _HEAD_CHECKED[name] = 1;
  var want = SHEET_SCHEMA[name];
  if (!want || !want.length) { return; }
  try {
    var lastCol = sh.getLastColumn();
    if (name === '影片清單') {
      var headers = lastCol ? sh.getRange(1,1,1,lastCol).getValues()[0].map(function(h){return String(h).trim();}) : [];
      if (headers.slice(0,7).some(function(h,i){return h!==want[i];})) { return; }
      var missing = want.filter(function(h){return headers.indexOf(h)<0;});
      if (missing.length) {
        var required = lastCol + missing.length;
        if (sh.getMaxColumns && sh.getMaxColumns()<required) { sh.insertColumnsAfter(sh.getMaxColumns(),required-sh.getMaxColumns()); }
        sh.getRange(1,lastCol+1,1,missing.length).setValues([missing]);
        Logger.log('影片清單附加缺欄（保留排版欄位置）：'+missing.join('、'));
      }
      return;
    }
    if (lastCol >= want.length) { return; }       // 欄位夠多就不動
    var have = lastCol ? sh.getRange(1, 1, 1, lastCol).getValues()[0] : [];
    // 只補「尾巴少掉的那幾欄」。中間對不上時不動，那種情況要人看過，
    // 自動補只會把欄位錯開，比缺一欄糟得多。
    for (var i = 0; i < have.length; i++) {
      if (String(have[i]).trim() !== want[i]) { return; }
    }
    var add = want.slice(have.length);
    sh.getRange(1, have.length + 1, 1, add.length).setValues([add])
      .setFontWeight('bold').setBackground('#EDF0EE');
    Logger.log('分頁「' + name + '」補上欄位：' + add.join('、'));
  } catch (e) {
    Logger.log('補欄位略過（不影響流程）：' + e);
  }
}

function getSheet_(name) {
  var ss = getSS_();
  var sh = ss.getSheetByName(name);
  if (sh) { ensureHeaders_(sh, name); return sh; }

  var headers = SHEET_SCHEMA[name];
  if (!headers) {
    throw new Error('未知的工作表分頁：' + name + '（不在 SHEET_SCHEMA 裡，可能是打錯字）');
  }

  sh = ss.insertSheet(name);
  sh.getRange(1, 1, 1, headers.length).setValues([headers]);
  sh.getRange(1, 1, 1, headers.length).setFontWeight('bold').setBackground('#EDF0EE');
  sh.setFrozenRows(1);
  Logger.log('自動補建分頁：' + name);
  return sh;
}

/* 唯讀請求內的試算表快照（v54，Codex 規格 67）。
   同一個請求裡好幾個函式各自 readSheetObjects_ 同一張表時，每次都整張重讀。
   只在明確宣告「這一段只讀」的地方（withSheetSnapshot_）開啟：同一張表在這一段內只讀一次。
   預設關閉——會寫入的流程不能拿到寫之前的舊快照。Apps Script 每個請求的全域變數是新的，不會跨請求共用。 */
var SHEET_SNAPSHOT_ = null;
function withSheetSnapshot_(fn) {
  var outer = SHEET_SNAPSHOT_;
  if (!outer) { SHEET_SNAPSHOT_ = {}; }
  try { return fn(); } finally { if (!outer) { SHEET_SNAPSHOT_ = null; } }
}

/** 讀取整張表為物件陣列，第一列為欄位名稱 */
function readSheetObjects_(name) {
  if (SHEET_SNAPSHOT_ && SHEET_SNAPSHOT_[name]) {
    return SHEET_SNAPSHOT_[name].map(function (o) { return Object.assign({}, o); });
  }
  var out = readSheetObjectsRaw_(name);
  if (SHEET_SNAPSHOT_) { SHEET_SNAPSHOT_[name] = out.map(function (o) { return Object.assign({}, o); }); }
  return out;
}
function readSheetObjectsRaw_(name) {
  var values = getSheet_(name).getDataRange().getValues();
  if (values.length < 2) { return []; }
  var headers = values[0];
  return values.slice(1).map(function (row) {
    var o = {};
    headers.forEach(function (h, i) { o[h] = row[i]; });
    return o;
  }).filter(function (o) {
    return Object.keys(o).some(function (k) { return o[k] !== '' && o[k] !== null; });
  });
}

/* ------------------------------------------------------------------ *
 * 部署資訊
 * ------------------------------------------------------------------ */

/**
 * 印出設定 refresh_site 需要的兩個值，並順便檢查狀態。
 *
 * 為什麼需要這一支：APPS_SCRIPT_URL 藏在部署對話框裡、ADMIN_KEY 是自己訂的字串，
 * 兩個都不在同一個畫面上，要湊齊得點好幾層。這支一次全印出來，
 * 直接複製貼到 GitHub Secrets 即可。ADMIN_KEY 沒設過時會自動產生一組安全的隨機字串。
 *
 * 用法：在 Apps Script 編輯器選這個函式按執行，然後看「執行紀錄」。
 */
/**
 * 我是誰、我服務的網址是哪一個。
 *
 * 專門用來查「貼了新程式碼卻還是舊版」。
 *
 * 關鍵在於它跑在編輯器裡，用的是你剛貼上的那份程式碼（HEAD），
 * 而瀏覽器開 ?action=ping 拿到的是「那個部署所釘住的版本」。
 * 兩邊印出來的 build 一比，就知道問題在哪一段：
 *
 *   編輯器新、ping 舊  →  程式碼有貼到，是部署或版本沒更新
 *   編輯器也是舊的　　 →  根本沒貼成功，或貼到另一個專案
 *   scriptId 對不上　　→  你改的專案與網址指向的專案不是同一個
 *
 * 這支不需要部署就能跑，所以卡在部署的時候也用得了。
 */
function whoAmI() {
  var out = [];
  out.push('========================================');
  out.push('  這個專案的身分（編輯器裡的最新程式碼）');
  out.push('========================================');

  var sid = '';
  try { sid = ScriptApp.getScriptId(); } catch (e) { sid = '取不到：' + e; }
  out.push('scriptId　' + sid);
  out.push('  編輯器網址裡的那一串應該與它相同：');
  out.push('  script.google.com/home/projects/' + sid + '/edit');

  var url = '';
  try { url = ScriptApp.getService().getUrl(); } catch (e) { url = ''; }
  out.push('');
  out.push('從編輯器問到的網址');
  out.push('  ' + (url || '（取不到）'));
  if (url && url.indexOf('/dev') >= 0) {
    out.push('  這是開發網址，從編輯器呼叫時本來就會拿到它，不代表沒有部署。');
    out.push('  /dev 服務的是 HEAD，也就是你剛貼的程式碼；');
    out.push('  /exec 服務的是那個部署「釘住的版本」。兩者本來就可能不同。');
    out.push('  所以拿 /dev 去比對編輯器永遠會一樣，那個比對沒有意義。');
    out.push('  真正要比的是 GitHub Secret 裡那個 /exec 網址，見下面。');
  }

  out.push('');
  out = out.concat(webAppUrlReport_());

  out.push('');
  out.push('編輯器裡這份程式碼的版本標記');
  out.push('  build    ' + (typeof GAS_BUILD === 'string' ? GAS_BUILD : '（讀不到 GAS_BUILD）'));
  out.push('  features ' + (typeof GAS_FEATURES !== 'undefined' ? GAS_FEATURES.join('、') : '（讀不到）'));
  out.push('  步驟表　 ' + (typeof REFRESH_STEPS_ !== 'undefined'
                            ? Object.keys(REFRESH_STEPS_).join('、') : '（讀不到）'));

  var hasFullfix = (typeof REFRESH_STEPS_ !== 'undefined') && !!REFRESH_STEPS_.fullfix;
  out.push('  有 fullfix 這一步嗎　' + (hasFullfix ? '有' : '沒有'));

  out.push('');
  out.push('========================================');
  out.push('  接下來怎麼比對');
  out.push('========================================');
  if (!hasFullfix) {
    out.push('編輯器裡這份就沒有 fullfix，代表 Code.gs 根本沒有貼成功，');
    out.push('或者貼到了另一個專案。先把最新的 Code.gs 貼進「這個」專案再說。');
  } else {
    out.push('編輯器這一側是對的：程式碼有貼到，而且貼在這個專案。');
    out.push('剩下的問題一定在「部署」那一側。照下面三步走。');
    out.push('');
    out.push('【第一步】列出這個專案所有的網頁應用程式部署');
    out.push('  部署 → 管理部署作業。把類型是「網頁應用程式」的每一列都看過，');
    out.push('  記下每一列的網址（/macros/s/ 後面那一長串）與它的「版本」號碼。');
    out.push('');
    out.push('【第二步】拿 GitHub Secret 的 APPS_SCRIPT_URL 去對');
    out.push('  對得到某一列 → 那一列就是要更新的。點它的鉛筆，');
    out.push('                 「版本」下拉手動選「新版本」，再按部署。');
    out.push('                 那個下拉預設停在目前釘住的版本號，');
    out.push('                 不動它直接按部署等於什麼都沒改——這是最常見的一次。');
    out.push('  一列都對不到 → Secret 指向的是別的部署。最常見的成因是更新時');
    out.push('                 按了「新增部署作業」而不是「編輯」，那會產生一個全新的');
    out.push('                 網址，你更新的是新的那個，Secret 裡還是舊的那個。');
    out.push('                 改用清單裡任一個網址，並把 Secret 換成它。');
    out.push('');
    out.push('【第三步】用瀏覽器驗，不要用猜的');
    out.push('  開　<那個 /exec 網址>?action=ping');
    out.push('  要看到兩件事都對才算成功：');
    out.push('    build 　　' + (typeof GAS_BUILD === 'string' ? GAS_BUILD : ''));
    out.push('    scriptId 　' + sid);
    out.push('  build 還是舊的 → 版本沒選「新版本」，回第二步。');
    out.push('  scriptId 不一樣 → 那個網址屬於「另一個專案」，');
    out.push('                    要改的是那個專案，或把 Secret 換成這個專案的網址。');
  }

  Logger.log(out.join('\n'));
  return { scriptId: sid, webAppUrl: url,
           build: (typeof GAS_BUILD === 'string' ? GAS_BUILD : ''),
           hasFullfix: hasFullfix };
}


/** 信件裡用的正式網址現況（whoAmI 與 showDeployInfo 共用）。 */
function webAppUrlReport_() {
  var out = [];
  var saved = '';
  try { saved = String(PropertiesService.getScriptProperties().getProperty('WEBAPP_URL') || ''); } catch (e) {}
  var fixed = (typeof WEBAPP_URL_DEFAULT === 'string') ? WEBAPP_URL_DEFAULT : '';
  out.push('信件裡用的正式網址（退訂連結、網站連結）');
  out.push('  實際使用　' + (publicWebAppUrl_() || '（沒有，退訂連結會指向 /dev，別人點了打不開）'));
  out.push('  指令碼屬性 WEBAPP_URL　' + (saved || '（未設定）'));
  out.push('  Config.gs WEBAPP_URL_DEFAULT　' + (fixed || '（未設定）'));
  if (saved && fixed && saved !== fixed) {
    out.push('  兩份不一樣，信件用的是指令碼屬性那一份。若 Config.gs 才是對的，執行 setWebAppUrl() 覆蓋。');
  }
  if (!saved) {
    out.push('  想寫進指令碼屬性：在編輯器執行 setWebAppUrl()（專案屬性超過 50 個時，專案設定頁面無法新增）。');
  }
  return out;
}

/**
 * 把網站正式網址寫進指令碼屬性 WEBAPP_URL（在編輯器選這支函式執行）。
 *
 * 專案的指令碼屬性超過 50 個時，專案設定頁面只列前 50 個而且唯讀，沒辦法手動新增，
 * Google 的提示是改用「屬性」服務寫入——這支就是做這件事。
 * 不帶參數時寫入 Config.gs 的 WEBAPP_URL_DEFAULT；換了部署時可以帶新網址：setWebAppUrl('https://.../exec')。
 */
function setWebAppUrl(url) {
  var value = String(url || (typeof WEBAPP_URL_DEFAULT === 'string' ? WEBAPP_URL_DEFAULT : '')).trim();
  if (!/^https:\/\/script\.google\.com\/.+\/exec$/.test(value)) {
    throw new Error('網址要是 https://script.google.com/ 開頭、/exec 結尾的正式網址（不能用 /dev）：' + value);
  }
  var props = PropertiesService.getScriptProperties();
  var before = props.getProperty('WEBAPP_URL');
  props.setProperty('WEBAPP_URL', value);
  var after = props.getProperty('WEBAPP_URL');
  if (after !== value) { throw new Error('寫入後讀回來不一致：' + after); }
  Logger.log('WEBAPP_URL 已寫入指令碼屬性。\n  原本　' + (before || '（未設定）') + '\n  現在　' + after +
             '\n信件裡的退訂連結與網站連結之後都會用這個網址。');
  return { ok: true, before: before || '', after: after };
}

/**
 * 寫入 FinMind API Token 至指令碼屬性。
 * Token 只存指令碼屬性，不再放在 Config.gs；呼叫端須明確傳入。
 */
function setFinMindToken(token) {
  var value = String(token || '').trim();
  if (!value) { throw new Error('Token 不得為空'); }
  var props = PropertiesService.getScriptProperties();
  props.setProperty('FINMIND_API_TOKEN', value);
  var after = props.getProperty('FINMIND_API_TOKEN');
  if (after !== value) throw new Error('FINMIND_API_TOKEN 寫入後讀回不一致');
  Logger.log('FINMIND_API_TOKEN 已寫入指令碼屬性；內容不顯示。');
  return { ok: true };
}

function showDeployInfo() {
  var out = [];
  // 只有 /dev 可用時，後面那些需要 /exec 的網址就不要印，
  // 印了只會讓人照著貼上去然後一直失敗。
  var devOnly = false;
  out.push('========================================');
  out.push('  refresh_site 需要的兩個 GitHub Secret');
  out.push('========================================');
  out.push('');

  // ---- 1. APPS_SCRIPT_URL ----
  var url = '';
  try { url = ScriptApp.getService().getUrl(); } catch (e) { url = ''; }

  out.push('【1】APPS_SCRIPT_URL');
  if (!url) {
    out.push('  取不到網址，代表這個專案還沒有部署成網頁應用程式。');
    out.push('  請先「部署 → 新增部署作業 → 類型選網頁應用程式」，再執行一次這支函式。');
  } else if (url.indexOf('/dev') >= 0) {
    // 只拿得到 /dev，代表這個專案還沒有「版本化的網頁應用程式部署」，
    // 或目前的執行情境對應到的是開發網址。
    //
    // 這裡刻意不把 /dev 網址拿去組後面那些網址。
    // 先前的版本一邊警告「這個不能用」、一邊又用它組出後台與測試網址，
    // 等於把人往錯的方向帶——照著貼進 GitHub Secret 就會一直失敗，
    // 而且失敗的樣子看起來像別的問題。
    devOnly = true;
    out.push('  取不到 /exec 網址，目前只有開發用的 /dev：');
    out.push('  ' + url);
    out.push('');
    out.push('  這個網址不能用。/dev 只有你本人登入時開得起來，');
    out.push('  GitHub Actions 沒有 Google 帳號可以登入，一定被擋。');
    out.push('');
    out.push('  請這樣拿正確的網址：');
    out.push('    1. 右上角「部署」→「管理部署作業」');
    out.push('    2. 選類型是「網頁應用程式」的那一個');
    out.push('    3. 複製底下那串 /exec 結尾的網址');
    out.push('  清單裡一個都沒有的話，先「新增部署作業」→ 類型選網頁應用程式');
    out.push('  → 執行身分「我」→ 誰可以存取「所有人」→ 部署。');
    out.push('  （這是唯一該用「新增」的時機，之後更新一律用「編輯」）');
  } else {
    out.push('  ' + url);
    out.push('');
    out.push('  這就是要貼到 GitHub Secret 的值，整串複製，結尾是 /exec。');

    // 把部署 ID 單獨拉出來。
    //
    // 一個專案可以有多個部署，每一個有自己的網址與自己的程式碼版本。
    // 最常踩的坑是：在瀏覽器測的是這一個（新的），
    // 而 GitHub Secret 裡存的是另一個（舊的），
    // 於是同一支端點在瀏覽器好好的、在 GitHub 卻拿到舊版行為。
    // 兩邊只要比對這一串 ID 就能立刻確認是不是同一個部署。
    var m = url.match(/\/macros\/s\/([^\/]+)\//);
    if (m) {
      out.push('');
      out.push('  部署 ID：' + m[1]);
      out.push('  ↑ 拿這一串去比對 GitHub Secret 裡 APPS_SCRIPT_URL 的同一段。');
      out.push('    不一致就代表兩邊指向不同的部署，把上面整串網址覆蓋過去即可。');
    }
  }
  out.push('');

  // ---- 2. ADMIN_KEY ----
  var key = PROP.getProperty('ADMIN_KEY');
  out.push('【2】ADMIN_KEY');
  if (!key) {
    // 沒設過就直接產一組，省得自己想。用 UUID 去掉連字號，夠長也夠亂。
    key = Utilities.getUuid().replace(/-/g, '');
    PROP.setProperty('ADMIN_KEY', key);
    out.push('  原本沒有設定，已自動產生一組並存進指令碼屬性：');
    out.push('');
    out.push('  ' + key);
    out.push('');
    out.push('  這組字串同時是網站管理入口的密鑰，請自己留一份。');
  } else {
    out.push('  ' + key);
    out.push('');
    out.push('  指令碼屬性裡已經有這一組。GitHub Secret 要填的是「一模一樣」的字串，');
    out.push('  前後不能多空格，大小寫要完全相同。');
  }
  out.push('');

  // ---- 3. 怎麼填 ----
  out.push('----------------------------------------');
  out.push('接下來到 GitHub：');
  out.push('  Settings → Secrets and variables → Actions → New repository secret');
  out.push('  建兩筆，名稱要完全照這樣打（全大寫、底線）：');
  out.push('    APPS_SCRIPT_URL');
  out.push('    ADMIN_KEY');
  out.push('');

  // ---- 4. 自我檢查 ----
  out.push('----------------------------------------');
  out.push('診斷用網址（出問題時先開這個）：');
  if (devOnly) {
    out.push('  （尚未取得 /exec 網址，請先依上面的步驟建立或複製網頁應用程式部署）');
  } else if (url) {
    out.push('  ' + url + '?action=ping');
    out.push('  它什麼都不做，只回一段 JSON，不需要任何額外授權。');
    out.push('  看到 {"ok":true,"pong":true...} → 程式碼跑得動，問題在後面的環節。');
    out.push('  看到 Apps Script 的錯誤頁       → 程式碼根本沒被執行，');
    out.push('                                    是授權或部署的問題，先做下面兩件事：');
    out.push('    a. 在編輯器手動執行任一函式，把跳出來的權限全部允許');
    out.push('    b. 部署 → 管理部署作業 → 編輯 → 版本一定要選「新版本」');
    out.push('       （沿用舊版本的話，權限範圍還是舊的，等於沒更新）');
  }
  out.push('');
  out.push('----------------------------------------');
  out.push('後台網址（手動投稿逐字稿）：');
  if (devOnly) {
    out.push('  （尚未取得 /exec 網址，同上）');
  } else if (url) {
    out.push('  ' + url + '?page=admin');
    out.push('  用上面那組 ADMIN_KEY 登入。可以加到手機主畫面當捷徑。');
    out.push('  注意要用這個完整網址，不要用網站裡看到的 googleusercontent 網址，');
    out.push('  那是內層沙箱的位址，直接開會是空白頁。');
  } else {
    out.push('  （尚未部署）');
  }
  out.push('');
  out.push('----------------------------------------');
  out.push('想先確認端點通不通，把下面這串貼到瀏覽器網址列：');
  out.push('');
  if (devOnly) {
    out.push('  （尚未取得 /exec 網址，同上）');
  } else if (url) {
    out.push('  ' + url + '?action=refresh&key=' + encodeURIComponent(key));
    out.push('');
    out.push('  正常會等一兩分鐘後回傳 {"ok":true,...} 這種純文字。');
    out.push('  若回傳的是網站首頁的 HTML，代表部署的還是舊版程式碼，');
    out.push('  請「部署 → 管理部署作業 → 編輯 → 版本選新版本 → 部署」再試一次。');
  } else {
    out.push('  （尚未部署，無法產生測試網址）');
  }
  out.push('');
  out.push('注意：部署設定的「誰可以存取」必須是「所有人」。');
  out.push('GitHub Actions 沒有 Google 帳號可以登入，設成僅限自己會被擋在登入頁。');
  out.push('');
  out.push('----------------------------------------');
  out.push('目前這份程式碼有沒有遠端刷新入口：');
  // 直接檢查自己的原始碼有沒有那個分支，避免「我明明貼了」的爭議。
  // 貼錯檔案或貼到一半的情況，這裡會直接抓出來。
  var hasRefresh = (typeof jsonOut_ === 'function');
  if (hasRefresh) {
    out.push('  有。jsonOut_ 存在，Code.gs 是新版的。');
    out.push('  若遠端呼叫仍拿到網站首頁，就是「部署中的版本」還是舊的，');
    out.push('  請到管理部署作業按鉛筆編輯、版本選「新版本」再部署。');
    out.push('  切記不要按「新增部署作業」，那會產生另一組網址。');
  } else {
    out.push('  沒有！jsonOut_ 不存在，代表 Code.gs 還是舊版或沒貼完整。');
    out.push('  請先把新版 Code.gs 整份覆蓋進去，再重新部署。');
  }
  out.push('----------------------------------------');
  out = out.concat(webAppUrlReport_());
  out.push('========================================');

  var text = out.join('\n');
  Logger.log(text);
  return text;
}

/* ================================================================== *
 * 專案檔案檢查：checkProjectFiles()（2026/09/16 v37）
 *
 * 每次更新都是手動把好幾個檔案整份貼進編輯器，貼錯檔、少貼、貼到一半沒存到都很容易，
 * 而症狀往往離真正的原因很遠——Config.gs 被貼成別的內容時，看到的是
 * 「APP_TITLE is not defined (第 141 行，檔案名稱：Code)」。
 * 這支在編輯器執行，逐檔確認：檔案在不在、該有的函式與常數有沒有、是不是這一版的內容。
 * 只讀，不改任何東西。
 * ================================================================== */

// 這份檢查表對應的程式碼版本，必須與 Config.gs 的 GAS_BUILD 相同（測試會核對）。
var PROJECT_BUILD_ = '2026-09-24-quality-v58';

// names：該檔案宣告的函式或常數（缺了代表沒貼或貼成別的檔案）。
// marker：[函式名, 這一版才有的字串]（找不到代表還是舊版）。
var PROJECT_FILES_ = [
  { file: 'Marketservice.gs', names: ['apiGetMarketOverview', 'mergedHourlyHistory_', 'backfillHourlyHistoryJob', 'auditHourlyCoverage', 'installMarketDataJobs', 'marketRollingWindow_'], marker: ['yahooIntradayCard_', 'meta.previousClose'] },
  { file: 'Holidays.gs', names: ['marketHolidaySet_', 'isMarketHoliday_', 'whyClosed_'] },
  { file: 'Config.gs', names: ['APP_TITLE', 'DISCLAIMER', 'WEBAPP_URL_DEFAULT', 'GAS_BUILD', 'GAS_FEATURES', 'REFRESH_ORDER_', 'CHAIN_KEY_'] },
  { file: 'Code.gs', names: ['doGet', 'include', 'configMissing_', 'configMissingResponse_'], marker: ['doGet', 'admin-legacy'] },
  { file: 'API.gs', names: ['jsonOut_', 'apiLookupSubscription', 'apiUpdateSubscription', 'renderUnsubscribePage_', 'apiGetCandlesBundle', 'apiUnsubscribeConfirm', 'apiGetStockSummary', 'apiGetTechStats'], marker: ['renderUnsubscribePage_', 'homeUrl'] },
  { file: 'Adminpipeline.gs', names: ['pipeIsForeign_', 'pipeBase_', 'PIPE_EXTRACT_SYSTEM'], marker: ['PIPE_EXTRACT_SYSTEM', '主體、條件與限制解除'] },
  { file: 'Adminservice.gs', names: ['adminAuth_', 'apiAdminLogin', 'PIPE_RECLASSIFY_SYSTEM', 'apiAdminTodayStatus', 'apiAdminHeldList', 'apiAdminSetHoldingCost', 'apiAdminHoldToday', 'apiAdminKCoverage'], marker: ['PIPE_RECLASSIFY_SYSTEM', '限制後段解除時依後段判斷'] },
  { file: 'Aiservice.gs', names: ['validateKey', 'sanitizeDraft_', 'draftReady_', 'isPromptProbe_', 'guardReply_'], marker: ['askAssistant', 'isPromptProbe_'] },
  { file: 'Articlequality.gs', names: ['enforceArticleRecords_', 'attachArticleEvidence_'] },
  { file: 'Cachebuilder.gs', names: ['budgetLeft_', 'trackedCodes_', 'readSnapshotRows_', 'officialDailyAll_', 'auditDailyKCache', 'repairDailyKCache', 'afterDailyKDoneJob', 'rescheduleDailyKTrigger', 'warmKCaches_', 'dailyKFloors_', 'resetDailyKFloor', 'isTradingDateStr_', 'ensurePerformanceContinuityJob_'], marker: ['fetchMissingDailyK_', 'setDailyKFloor_'] },
  { file: 'Cmoney.gs', names: ['cmMailBody_', 'cmNotifyNew_', 'cmSyncContentTick_', 'cmTranscriptExcerpt_', 'deliveryRetryTick_', 'diagnoseInstantMail', 'cmSetNotifyState_', 'resendInstantMail'], marker: ['cmNotifyNew_', 'cmSetNotifyState_(a.id'] },
  { file: 'DB.gs', names: ['writeSubscriptionFields_', 'findSubscription_'] },
  { file: 'Evidencequality.gs', names: ['rawTranscript_', 'validEvidence_', 'queueDayEditSync_', 'dayEditSyncTick_', 'queueCostSync_'], marker: ['dayEditSyncTick_', 'COST:'] },
  { file: 'Logic.gs', names: ['markChainStep_', 'REFRESH_STEPS_'] },
  { file: 'MailService.gs', names: ['createSubscription', 'mailHero_', 'publicWebAppUrl_', 'escAttr_', 'mailRiskHtml_', 'deliverMessage_', 'deliveryLedger_', 'mailPlainText_', 'isExecUrl_', 'mailStockName_'], marker: ['mdToHtml_', 'mailStockName_(cells[0])'] },
  { file: 'Presentationquality.gs', names: ['displayPrice_', 'narrativeName_', 'titleChars_'], marker: ['articleTitle_', 'TITLE_MIN_CHARS_'] },
  { file: 'Quoteservice.gs', names: ['getFugleKey_', 'fugleFetch_', 'sharesToLots_', 'volumeInLots_', 'hourSlot_', 'readHourlyRows_', 'fugleHistPace_', 'kcPutAll_', 'getCandlesBundle'], marker: ['repairDailyKVolume', 'disabled: true'] },
  { file: 'Refreshrunner.gs', names: ['runRefreshAllChunk_', 'withRefreshAllLease_'] },
  { file: 'Setup.gs', names: ['setupSpreadsheet', 'setWebAppUrl', 'webAppUrlReport_', 'checkProjectFiles', 'checkAutomationReadiness', 'ensureAutomationTick', 'withSheetSnapshot_'] },
  { file: 'SheetService.gs', names: ['fmtDate_', 'withLock_', 'ensureTranscriptLayoutJob', 'transcriptFingerprint_', 'stripTranscribeEcho_', 'readCostOverrides_', 'searchTerms_', 'repairLiwangExitPriceNow', 'rangeCandle_', 'statedNote_', 'trackerRoundList_'], marker: ['rebuildHoldingsTrackerJob', '進場明講'] },
  { file: 'Transcriptstore.gs', names: ['transcriptSha256_', 'selectTranscriptRow_'] }
];

// HTML 檔名不含 .html；marker 是這一版才有的字串。
var PROJECT_HTML_ = [
  { file: 'MarketDetail', marker: 'minmax(max(140px,calc((100% - 72px) / 3)),1fr)' },
  { file: 'MarketCharts', marker: 'window.marketRollingBounds' },
  { file: 'Market', marker: '開盤不久，走勢累積中' },
  { file: 'Index', marker: 'id="dPxRange"' },
  { file: 'JavaScript', marker: 'function pxRangeHtml(t)' },
  { file: 'Stylesheet', marker: 'v58 持股追蹤取價' },
  { file: 'Changelog', marker: 'v58 持股追蹤改成「進場日最低、出場日最高」' },
  { file: 'Tech', marker: 'id="techLive"' },
  { file: 'Admin', marker: 'he-form' },
  { file: 'AdminLegacy', marker: '改版前的舊版後台' },
  { file: 'Settings', marker: '手機預覽' },
  { file: 'Unsubscribed', marker: 'apiUnsubscribeConfirm' }
];

// 全域物件。每個 .gs 頂層的函式宣告與 var 都掛在它上面，才能用名字查。
var PROJECT_GLOBAL_ = (typeof globalThis !== 'undefined') ? globalThis : this;

/** 檢查結果（不寫紀錄），給 checkProjectFiles 與測試用。 */
function projectFileProblems_() {
  var g = PROJECT_GLOBAL_;
  var problems = [];

  PROJECT_FILES_.forEach(function (f) {
    var missing = f.names.filter(function (n) { return typeof g[n] === 'undefined'; });
    if (missing.length === f.names.length) {
      problems.push({ file: f.file, reason: '整個檔案沒有載入（缺 ' + missing.join('、') + '）：檔案不見了，或內容被貼成別的檔案。' });
      return;
    }
    if (missing.length) {
      problems.push({ file: f.file, reason: '缺 ' + missing.join('、') + '：內容不完整或是舊版。' });
      return;
    }
    if (f.marker) {
      var src = '';
      try { src = String(g[f.marker[0]]); } catch (e) {}
      if (src.indexOf(f.marker[1]) < 0) {
        problems.push({ file: f.file, reason: '還是舊版（' + f.marker[0] + ' 裡找不到「' + f.marker[1] + '」）。' });
      }
    }
  });

  if (typeof GAS_BUILD !== 'undefined' && GAS_BUILD !== PROJECT_BUILD_) {
    problems.push({ file: 'Config.gs', reason: '版本是 ' + GAS_BUILD + '，這份 Setup.gs 對應的是 ' + PROJECT_BUILD_ + '：兩個檔案其中一個不是最新版。' });
  }

  PROJECT_HTML_.forEach(function (h) {
    var raw = null;
    try { raw = HtmlService.createTemplateFromFile(h.file).getRawContent(); } catch (e) { raw = null; }
    if (raw === null) {
      problems.push({ file: h.file + '.html', reason: '找不到這個 HTML 檔（新增時選「HTML」，檔名 ' + h.file + '，不要加副檔名）。' });
    } else if (h.marker && raw.indexOf(h.marker) < 0) {
      problems.push({ file: h.file + '.html', reason: '還是舊版（找不到「' + h.marker + '」）。' });
    }
  });

  return problems;
}

/**
 * 在編輯器執行：逐檔檢查專案裡的程式碼有沒有貼齊、是不是這一版。
 * 看到錯誤訊息像「XXX is not defined」、或更新後網站行為怪怪的，先跑這一支。
 */
function checkProjectFiles() {
  var problems = projectFileProblems_();
  var out = [];
  out.push('========================================');
  out.push('  專案檔案檢查（對應版本 ' + PROJECT_BUILD_ + '）');
  out.push('========================================');
  out.push('檢查了 ' + PROJECT_FILES_.length + ' 個 .gs 與 ' + PROJECT_HTML_.length + ' 個 .html。');
  out.push('');
  if (!problems.length) {
    out.push('全部正常。若網站仍是舊的樣子，代表還沒部署新版本：');
    out.push('部署 → 管理部署作業 → 鉛筆編輯 → 版本選「新版本」→ 部署。');
  } else {
    out.push('有 ' + problems.length + ' 個問題：');
    problems.forEach(function (p) { out.push('  ✗ ' + p.file + '　' + p.reason); });
    out.push('');
    out.push('修法：打開上面列出的檔案，全選刪掉，把本機同名檔案整份貼上，存檔後再執行一次這支確認。');
    out.push('全部正常後再部署新版本（管理部署作業 → 編輯 → 新版本）。');
  }
  var text = out.join('\n');
  Logger.log(text);
  return { ok: !problems.length, problems: problems, text: text };
}

/** 部署後只讀驗收：列出自動化必需的觸發器、設定與快取筆數，不寄信、不派工。 */
function checkAutomationReadiness() {
  var p=PropertiesService.getScriptProperties(),triggers=ScriptApp.getProjectTriggers().map(function(t){return t.getHandlerFunction();});
  var result={build:GAS_BUILD,triggerCount:triggers.length,
    everyFiveMin:triggers.indexOf('everyFiveMinJob')>=0,
    dailyK:triggers.indexOf('backfillDailyKJob')>=0,
    hourlyHistory:triggers.indexOf('backfillHourlyHistoryJob')>=0,
    // 市場快照已由每五分鐘總排程呼叫；沒有獨立觸發器不代表未運作。
    marketSnapshots:triggers.indexOf('everyFiveMinJob')>=0||triggers.indexOf('marketSnapshotJob')>=0,
    webapp:!!publicWebAppUrl_(),fugle:hasFugle_(),daySync:daySyncState_(),marketRows:marketRows_('市場總覽快取').length};
  result.ready=result.everyFiveMin&&result.webapp;
  result.hourlyHistoryMode=result.hourlyHistory?'每日 19:15 獨立排程':'缺少 19:15 歷史 60 分 K 排程，執行 installMarketDataJobs()';
  result.marketSnapshotMode=triggers.indexOf('everyFiveMinJob')>=0?'五分鐘總排程':result.marketSnapshots?'獨立五分鐘排程':'未排程';
  try { var ms=JSON.parse(p.getProperty('marketSnapshotStatus')||'{}');result.marketSnapshotLast={at:ms.at||'',ok:ms.ok===true,note:ms.note||''}; } catch(e) {}
  var github=githubCfg_();result.transcriptBackup=!!(result.everyFiveMin&&github.repo&&github.token);
  // 盤中即時通知（v54 追加）：每分鐘輪詢觸發器、會員帳號、訂閱人數、今日封數
  try{result.instantMail={pollTrigger:triggers.indexOf('cmoneyPollJob')>=0,memberId:cmEnabled_(),subscribers:cmSubscribers_().length,
    quotaLeft:cmMailQuotaLeft_().left};result.instantMail.ready=result.instantMail.pollTrigger&&result.instantMail.memberId&&result.instantMail.subscribers>0&&result.webapp;}
  catch(e){result.instantMail={error:String(e&&e.message||e)};}
  result.note=result.ready?'排程入口已存在；GitHub 金鑰與影片可讀性需另外執行 transcript check。':'缺少每五分鐘觸發器或正式網站網址；請先補設定。';
  Logger.log(JSON.stringify(result));return result;
}
/** 僅補缺少的每五分鐘觸發器，不使用會重建全套排程的 installTriggers。 */
function ensureAutomationTick() {
  var ts=ScriptApp.getProjectTriggers();
  if(ts.some(function(t){return t.getHandlerFunction()==='everyFiveMinJob';})){Logger.log('每五分鐘觸發器已存在，不重複建立');return;}
  if(ts.length>=20){throw new Error('觸發器已達20個，請先移除確認不用的舊觸發器，再執行本函式');}
  ScriptApp.newTrigger('everyFiveMinJob').timeBased().everyMinutes(5).create();
  Logger.log('已補建每五分鐘自動維運入口');
}
