/** HTML 入口與模板 include；對外 API 見 API.gs。 */

/**
 * 網頁進入點。
 * 支援 ?action=unsubscribe&email=...&token=... 取消訂閱（規格書 4.5 節）
 */
function doGet(e) {
  var params = (e && e.parameter) ? e.parameter : {};

  // Config.gs 沒載入（貼成別的檔案的內容、沒存到、被刪掉）時，後面每一步都會丟 ReferenceError，
  // 看到的只有「APP_TITLE is not defined (第 141 行，檔案名稱：Code)」，看不出是哪個檔案壞了（2026/09/16）。
  // 一進來先檢查，網頁與 JSON 入口都回得出「哪裡壞、怎麼修」。
  var configGap = configMissing_();
  if (configGap.length) { return configMissingResponse_(params, configGap); }

  // 記下正式網址給排程寄信用（退訂連結、網站連結）。見 MailService.gs 的 publicWebAppUrl_。
  rememberWebAppUrl_();
  if(params.action==='day-sync'){
    try{return jsonOut_(apiDaySyncDrive_(params.key||'',params.op||'state',params.id||'',params.index));}
    catch(err){return jsonOut_({ok:false,error:String(err.message||err)});}
  }


  /* 退訂（v54）：GET 只顯示確認頁，按下頁面上的按鈕才真的停止（google.script.run → apiUnsubscribeConfirm）。
     信件掃描、連結預覽或預先載入會發 GET，先前 GET 直接改訂閱狀態，有被誤觸的風險。 */
  if (params.action === 'unsubscribe') {
    return renderUnsubscribePage_({ mode: 'confirm', email: params.email, token: params.token, type: params.type });
  }

  /**
   * 遠端刷新入口：?action=refresh&key=ADMIN_KEY
   *
   * 存在的理由：上游 GitHub 改的是試算表，下游 Apps Script 才把試算表算成
   * 網站看到的樣子。少了這個入口，在 GitHub 手動整理完資料之後，
   * 還得自己再去 Apps Script 按一次執行，或乾脆等下一個交易日的排程。
   * 有了它，pipeline.py 跑完可以直接打這一支，資料整理與網站更新一次到底。
   *
   * 一定要帶對 ADMIN_KEY。沒有密鑰的人打這個網址只會拿到 401，
   * 不會觸發任何運算，也不會洩漏任何內容。
   * 回傳純 JSON 而不是網頁，方便 workflow 直接判讀結果。
   */
  /**
   * 最小診斷端點。?action=ping
   *
   * 它什麼都不做，只回一段固定的 JSON，不碰試算表、不連外、不寄信、
   * 不建觸發器——也就是不需要任何額外授權。
   *
   * 用途是把問題一刀切開：
   *   ping 回 JSON、refresh 回錯誤頁 → 程式碼跑得動，是 refresh 那條鏈出錯
   *   ping 也回錯誤頁               → 程式碼根本沒被執行，
   *                                   問題出在授權或部署，try/catch 接不到
   *
   * 之所以需要這個，是因為授權不足時 Apps Script 會在執行你的程式之前
   * 就回錯誤頁，看起來和程式內部出錯一模一樣，光看回應分不出來。
   */
  if (params.action === 'ping') {
    return jsonOut_({
      ok: true, pong: true,
      time: Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss'),
      // 版本標記與支援的能力。
      //
      // 存在的理由：貼了新程式碼但沒有部署新版本，是這個專案最常見的坑，
      // 而且症狀常常偽裝成別的問題——舊版收到它不認得的參數會直接忽略，
      // 照著舊邏輯跑，於是回應看起來像超時或當機，跟版本沒對上完全看不出關係。
      // 呼叫端先問一次 ping，就能在動手之前確認雙方版本是否一致。
      build: GAS_BUILD,
      features: GAS_FEATURES,
      steps: Object.keys(REFRESH_STEPS_),
      /* 身分。
       *
       * 「貼了新程式碼卻還是舊版」這件事有三種成因，光看 build 分不出來：
       *   一個專案有多個部署，設定的網址指向的不是你剛更新的那一個
       *   更新時版本沒選「新版本」，於是那個部署仍釘在舊版本
       *   根本有兩份專案，你改的是 A，網址指向 B
       * 把 scriptId 與這個部署自己認得的網址回報出來，三種就分得開：
       * scriptId 與編輯器網址裡那一串不同 → 改錯專案
       * scriptId 相同但 build 是舊的 → 部署或版本沒更新
       */
      scriptId: (function () {
        try { return ScriptApp.getScriptId(); } catch (e) { return '取不到'; }
      })(),
      webAppUrl: (function () {
        try { return ScriptApp.getService().getUrl(); } catch (e) { return '取不到'; }
      })(),
      note: '能看到這段就代表程式碼跑得動。請比對 build 與 features 是否為預期版本。'
    });
  }

  /**
   * 簡訊優先就緒檢查端點：?action=checkSms&date=YYYY/MM/DD
   * 供 Python pipeline.py 與排程在刷新全站前檢查會員簡訊是否已解析就緒。
   */
  if (params.action === 'checkSms') {
    var d = params.date || '';
    var status = isSmsReady_(d);
    return jsonOut_({
      ok: true,
      status: status,
      ready: status === 'ready',
      date: (typeof fmtDate_ === 'function' ? fmtDate_(d) : '') || (typeof todayStr_ === 'function' ? todayStr_() : ''),
      guard: typeof SMS_PRIORITY_GUARD === 'undefined' ? true : !!SMS_PRIORITY_GUARD
    });
  }

  /**
   * 日K API 診斷端點：?action=debugDailyK&code=2330
   * 立即測試 Fugle、FinMind、TWSE 的連線狀態、回應內容與解析結果。
   */
  /* v54：這兩個診斷端點會真的去打富果、FinMind、證交所，先前不需要密鑰——
     任何人反覆打這個網址，就能耗掉每分鐘 50～55 次的行情額度。改成要帶 ADMIN_KEY。 */
  if ((params.action === 'debugDailyK' || params.action === 'probe') &&
      typeof adminAuth_ === 'function') {
    try { adminAuth_(params.key || ''); }
    catch (authErr) { return jsonOut_({ ok: false, error: 'unauthorized', message: String(authErr.message || authErr) }); }
  }

  if (params.action === 'debugDailyK') {
    var debugCode = params.code || '2330';
    return jsonOut_({
      ok: true,
      code: debugCode,
      report: typeof debugDailyKFetch === 'function' ? debugDailyKFetch(debugCode) : '未載入 debugDailyKFetch'
    });
  }

  /**
   * 系統端點完整探針：?action=probe
   */
  if (params.action === 'probe') {
    return jsonOut_({
      ok: true,
      report: typeof probeEndpoints === 'function' ? probeEndpoints() : '未載入 probeEndpoints'
    });
  }

  if (params.action === 'refresh') {
    // 包起來回傳 JSON。不包的話，任何例外都會變成 Apps Script 的通用錯誤頁，
    // 呼叫端只會看到一坨 HTML，看不出真正的原因——實際踩過，
    // 授權不足時就是這樣，訊息卻被誤判成「部署版本太舊」。
    try {
      // step 參數把整條鏈拆成獨立的請求。
      //
      // 為什麼一定要拆：整條重算鏈（清產業、代號比對、基本面、補日K、
      // 持股追蹤、績效）跑完遠超過 6 分鐘，而網頁請求超過就會被直接砍掉。
      // 那不是拋例外，是執行被中止，所以 try/catch 接不到，
      // 呼叫端只會拿到一張看不出原因的錯誤頁——先前查了很久就是卡在這裡。
      //
      // 拆開之後每一步都是獨立請求，各自享有完整的時間額度，
      // 呼叫端依序打六次即可，而且每一步的成敗都看得到。
      return jsonOut_(apiRefreshStep_(params.key || '', params.step || 'all', params.date || '', params));
    } catch (err) {
      return jsonOut_({
        ok: false,
        error: String(err && err.message || err),
        hint: '若訊息提到授權或 Authorization，請到 Apps Script 編輯器手動執行一次任一函式並允許新權限，再重新部署新版本。'
      });
    }
  }

  /**
   * 後台：手動投稿逐字稿。網址 ?page=admin
   *
   * 這一頁本身不含任何機密，也不會顯示任何資料——所有內容都要先通過
   * apiAdminLogin 驗證管理密鑰才拿得到。所以直接讓它可以開啟是安全的，
   * 沒有密鑰的人只會看到一個登入框。
   * 加 noindex 是不希望它被搜尋引擎收錄。
   */
  // ?page=admin-legacy：v54 改版前的後台原樣留存（AdminLegacy.html），只供對照；密鑰驗證與 API 與新版相同。
  if (params.page === 'admin' || params.page === 'admin-legacy') {
    try {
      // 用 Template 而不是 HtmlOutput，才能把網頁應用程式的絕對網址注入進去。
      // 後台頁面裡的「回首頁」連結需要它：沙箱 iframe 內的相對網址
      // 會指向 googleusercontent.com，那不是進入點，點了只會得到空白頁。
      var legacyAdmin = params.page === 'admin-legacy';
      var at = HtmlService.createTemplateFromFile(legacyAdmin ? 'AdminLegacy' : 'Admin');
      at.webAppUrl = ScriptApp.getService().getUrl();
      return at.evaluate()
        .setTitle(legacyAdmin ? '舊版後台（改版前留存）' : '後台')
        // viewport 要與前台逐字相同。少了 viewport-fit=cover，有瀏海的機型
        // 左右會多出一條空白，前後台一比就看得出不是同一個站。
        .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover')
        // 一定要 ALLOWALL，不能用 DEFAULT。
        //
        // Apps Script 不會直接把你的 HTML 交給瀏覽器，而是包進一層位於
        // googleusercontent.com 的巢狀 iframe（網址會變成 userCodeAppPanel）。
        // DEFAULT 會送出限制 framing 的標頭，瀏覽器就把那層 iframe 擋掉，
        // 結果是網址正常、標題正常，但內容區一片空白，主控台也不見得有錯誤。
        // 主網站一直是 ALLOWALL，後台也必須一致。
        .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
    } catch (err) {
      // 檔案不存在或名稱打錯時，給一個看得懂的訊息，而不是又一個空白頁。
      return HtmlService.createHtmlOutput(
        '<div style="font-family:sans-serif;padding:40px;line-height:1.8">' +
        '<h2>後台載入失敗</h2>' +
        '<p>' + String(err).replace(/</g, '&lt;') + '</p>' +
        '<p>請確認 Apps Script 專案裡有一個名為 <b>Admin</b> 的 HTML 檔案' +
        '（新增時選「HTML」，檔名輸入 Admin，不要加副檔名）。</p></div>')
        .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
    }
  }

  var t = HtmlService.createTemplateFromFile('Index');
  t.appTitle = APP_TITLE;
  // 信件裡的「到網站管理訂閱」帶 ?tab=subscribe 進來，直接打開那一頁。只認得分頁列上有的名字。
  t.initialTab = /^(overview|market|tracker|perf|subscribe|mail|sms|tx|tech)$/.test(String(params.tab || ''))
    ? String(params.tab) : '';
  t.disclaimer = DISCLAIMER;
  t.webAppUrl = ScriptApp.getService().getUrl();

  return t.evaluate()
    .setTitle(APP_TITLE)
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** 在 HTML 內以 <?!= include('Stylesheet') ?> 引入其他 HTML 檔 */
function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

/* Config.gs 的常數有沒有載入。

   刻意逐一用 typeof 寫死，不透過全域物件查名字：typeof 對沒宣告的名字只回 'undefined'、
   不會丟例外，這支本身在 Config.gs 壞掉時也一定跑得動。 */
function configMissing_() {
  var miss = [];
  if (typeof APP_TITLE === 'undefined') { miss.push('APP_TITLE'); }
  if (typeof DISCLAIMER === 'undefined') { miss.push('DISCLAIMER'); }
  if (typeof GAS_BUILD === 'undefined') { miss.push('GAS_BUILD'); }
  if (typeof GAS_FEATURES === 'undefined') { miss.push('GAS_FEATURES'); }
  if (typeof REFRESH_ORDER_ === 'undefined') { miss.push('REFRESH_ORDER_'); }
  return miss;
}

/* Config.gs 沒載入時的回應。只用 Apps Script 內建服務，不呼叫其他檔案的函式（它們可能也沒貼好）。
   ping／refresh 給 GitHub Actions 讀，回 JSON；其他（網站、後台、退訂頁）回一頁看得懂的說明。 */
function configMissingResponse_(params, miss) {
  var why = 'Apps Script 專案裡的 Config.gs 沒有正確載入，找不到 ' + miss.join('、') + '。';
  var fix = [
    '打開 Config.gs，確認第 3 行是 var APP_TITLE = …；不是的話，把本機的 Config.gs 整份重新貼上並存檔。',
    '在編輯器的函式選單選 checkProjectFiles 執行，執行記錄會列出其他沒貼好或還是舊版的檔案。',
    '全部修好後：部署 → 管理部署作業 → 鉛筆編輯 → 版本選「新版本」→ 部署。'
  ];
  if (params.action === 'ping' || params.action === 'refresh') {
    return ContentService.createTextOutput(JSON.stringify({
      ok: false, error: 'config-missing', missing: miss, message: why, fix: fix
    })).setMimeType(ContentService.MimeType.JSON);
  }
  var esc = function (s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); };
  return HtmlService.createHtmlOutput(
    '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Noto Sans TC\',\'Microsoft JhengHei\',sans-serif;' +
      'max-width:640px;margin:0 auto;padding:40px 20px;line-height:1.8;color:#12161A;">' +
    '<h2 style="margin:0 0 8px;">網站暫時無法顯示</h2>' +
    '<p style="margin:0 0 20px;color:#667069;">網站設定正在更新，請稍後再試。</p>' +
    '<div style="border:1px solid #D9DFDA;border-radius:14px;padding:14px 18px;background:#FDFDFC;">' +
      '<p style="margin:0 0 8px;font-weight:700;">給管理者</p>' +
      '<p style="margin:0 0 8px;">' + esc(why) + '</p>' +
      '<ol style="margin:0;padding-left:20px;">' +
        fix.map(function (t) { return '<li>' + esc(t) + '</li>'; }).join('') +
      '</ol>' +
    '</div></div>')
    .setTitle('網站暫時無法顯示')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}
