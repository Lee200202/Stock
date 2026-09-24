/** Evidence-first quality helpers and durable day-edit publication chain. */
function rawTranscript_(row) {
  return String(row['原始逐字稿內容'] || row['修飾後逐字稿內容'] || '');
}

function validEvidence_(quotes, source) {
  function compact(t) { return String(t || '').replace(/\s+/g, ''); }
  var hay = compact(source);
  return Array.isArray(quotes) && quotes.length > 0 && quotes.every(function (q) {
    return typeof q === 'string' && compact(q).length >= 6 && hay.indexOf(compact(q)) >= 0;
  });
}

function recordChapter_(signals, d) {
  function table(head, rows) {
    if (!rows.length) { return '本支影片未說明。\n'; }
    function cell(x) { return String(x || '未說明').replace(/\|/g, '／').replace(/\n/g, ' '); }
    return ['| ' + head.join(' | ') + ' |', '| ' + head.map(function () { return '---'; }).join(' | ') + ' |']
      .concat(rows.map(function (r) { return '| ' + r.map(cell).join(' | ') + ' |'; })).join('\n') + '\n';
  }
  var rows = [];
  [['buy', '買入'], ['sell', '賣出']].forEach(function (pair) {
    (signals[pair[0]] || []).forEach(function (r) {
      rows.push([r.name, r.code, pair[1], displayPrice_(r.price,r.price_evidence,r.reason), publicNarrative_(r.reason,r)]);
    });
  });
  var text = '② 會員操作紀錄與持股明細\n\n②-1 當日明確說明之買入／賣出紀錄\n\n';
  text += rows.length ? table(['股票名稱', '股票代號', '方向', '價位區間／成本說明', '張震口頭說明與操作理由'], rows)
                      : '本支影片未說明當日具體買賣紀錄。\n';
  text += '\n②-2 影片中明講之「會員目前持有股票」\n\n';
  text += table(['股票名稱', '股票代號', '張震在本集節目中的說明重點'],
    (signals.holdings || []).map(function (r) { return [r.name, r.code, publicNarrative_(r.note,r)]; }));
  text += '\n②-3 觀望個股（當日未執行買賣）\n';
  [['watch_avoid', '觀望不碰'], ['watch_watch', '觀望注意']].forEach(function (pair) {
    text += '\n' + pair[1] + '\n\n' + table(['股票名稱', '股票代號', '價位說明', '張震口頭說明重點'],
      (signals[pair[0]] || []).map(function (r) { return [r.name, r.code, displayPrice_(r.price,r.price_evidence,r.reason), publicNarrative_(r.reason,r)]; }));
  });
  return text;
}

// Article structure (title + ①–④) lives in Articlequality.gs.

var DAY_SYNC_KEY = 'dayEditSyncV1';
var DAY_SYNC_NAMES = ['同步郵件內容', '重算持股追蹤', '重算歷史績效', '記錄目前績效'];

/* 怎麼判斷「停住了」
 *
 * 每一步是一次獨立的 Apps Script 執行，單次上限 6 分鐘。超過上限的執行會被
 * Google 直接中止——不是拋例外，catch 接不到，所以狀態會永遠停在「處理中」：
 *   取消　只在狀態上做記號、等「這一步結束」才生效，而這一步永遠不會結束；
 *   重跑　被「更新中，請等此輪完成」擋下。
 * 2026/09/10 就停在「重算歷史績效：處理中」，取消與重跑都按不動。
 *
 * 所以改用時間判斷，不再等狀態欄自己走完：
 *   一步開始超過 DAY_SYNC_STEP_DEAD_MIN_ 分鐘還沒結束 → 那次執行一定已經被中止
 *   兩步之間等觸發器超過 DAY_SYNC_IDLE_DEAD_MIN_ 分鐘都沒開始 → 觸發器沒接上
 * 兩種都算「已停住」：可以直接取消，也可以直接重新送出。 */
var DAY_SYNC_STEP_DEAD_MIN_ = 7;
var DAY_SYNC_IDLE_DEAD_MIN_ = 5;

function daySyncState_() {
  return JSON.parse(PropertiesService.getScriptProperties().getProperty(DAY_SYNC_KEY) || 'null');
}
function saveDaySync_(s) {
  s.updatedAt = nowStamp_();
  s.updatedMs = Date.now();
  PropertiesService.getScriptProperties().setProperty(DAY_SYNC_KEY, JSON.stringify(s));
}
function clearDaySyncTriggers_() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'runDayEditSync_') { ScriptApp.deleteTrigger(t); }
  });
}
function scheduleDaySync_() {
  clearDaySyncTriggers_();
  var st=daySyncState_();
  if(st&&Number(st.githubUntil||0)>Date.now()){return;}
  // Actions 排步驟，GAS 保留資料運算。派工失敗才回退原本的單步觸發器。
  if(st&&typeof githubCfg_==='function'){
    var g=githubCfg_();
    if(g.repo&&g.token){try{
      var response=UrlFetchApp.fetch('https://api.github.com/repos/'+g.repo+'/actions/workflows/day-sync.yml/dispatches',{
        method:'post',contentType:'application/json',muteHttpExceptions:true,
        headers:{Authorization:'Bearer '+g.token,Accept:'application/vnd.github+json'},
        payload:JSON.stringify({ref:g.ref,inputs:{mode:'queue'}})});
      if(response.getResponseCode()===204){st.githubUntil=Date.now()+10*60000;st.runner='GitHub Actions';saveDaySync_(st);return;}
    }catch(e){Logger.log('GitHub 派工暫不可用，使用 GAS 續跑');}}
  }
  ScriptApp.newTrigger('runDayEditSync_').timeBased().after(2000).create();
}

/**
 * 這一輪現在的實際狀況。只讀，不改狀態。
 *   running  這一步的執行還活著（還在上限之內）
 *   stalled  狀態寫著處理中，但已經沒有任何執行會把它往前推
 *   minutes  距離上一次有動靜幾分鐘
 *   left     還在跑的那一步，最多再幾分鐘一定會結束
 */
function daySyncHealth_(st) {
  var h = { running: false, stalled: false, minutes: 0, left: 0 };
  if (!st) { return h; }
  var now = Date.now(), age, limit;
  if (st.running === true) {
    age = st.stepStartedMs ? (now - Number(st.stepStartedMs)) / 60000 : Infinity;
    limit = DAY_SYNC_STEP_DEAD_MIN_;
    h.running = age < limit;
    h.stalled = st.status === '處理中' && !h.running;
  } else if (st.status === '處理中') {
    // 在等下一步的觸發器；或是舊版留下的狀態——沒有 running 這一欄，
    // 分不出是在跑還是在等，保守起見用比較長的那個門檻。
    var last = Number(st.updatedMs) ||
               new Date(String(st.updatedAt || '').replace(/-/g, '/')).getTime();
    age = (last > 0) ? (now - last) / 60000 : Infinity;
    limit = (st.running === false) ? DAY_SYNC_IDLE_DEAD_MIN_ : DAY_SYNC_STEP_DEAD_MIN_;
    h.stalled = age >= limit;
  } else {
    return h;
  }
  h.minutes = isFinite(age) ? Math.floor(age) : 999;
  h.left = h.running ? Math.max(1, Math.ceil(limit - age)) : 0;
  return h;
}

function apiAdminStartDaySync(key, dateStr) {
  try {
    adminAuth_(key);
    var d = fmtDate_(dateStr);
    if (!/^20\d{2}\/\d{2}\/\d{2}$/.test(d)) { throw new Error('請選擇完整日期'); }
    return withLock_(function () {
      var prev = daySyncState_();
      var h = daySyncHealth_(prev);
      if (prev && prev.status === '處理中' && !h.stalled) {
        throw new Error(prev.date + ' 的「' + prev.step + '」正在更新，請等此輪完成；要中止請按「取消工單」');
      }
      if (prev && h.running) {
        // 已取消，但那一步還在跑。這時開新的一輪，兩輪會同時寫同一批分頁。
        throw new Error('上一輪的「' + prev.step + '」還在背景收尾（Apps Script 無法中途打斷），' +
                        '約 ' + h.left + ' 分鐘內結束，之後再按一次即可');
      }
      var note = (prev && h.stalled)
        ? '（上一輪停在「' + prev.step + '」已中止 ' + h.minutes + ' 分鐘，這次從頭重跑）' : '';
      saveDaySync_({ id: 'DAY-' + Date.now(), date: d, status: '處理中', index: 0,
        total: DAY_SYNC_NAMES.length, step: DAY_SYNC_NAMES[0], log: [], startedAt: nowStamp_(),
        running: false });
      scheduleDaySync_();
      return { ok: true, message: d + ' 已排程，會接續完成郵件、持股追蹤及績效。' + note };
    });
  } catch (e) { return { ok: false, reason: String(e.message || e) }; }
}
function apiAdminDaySyncState(key) {
  adminAuth_(key);
  var st = daySyncState_();
  return { ok: true, state: st, names: DAY_SYNC_NAMES, health: daySyncHealth_(st) };
}

/**
 * 取消立刻生效，不再等「這一步結束」。
 *
 * Apps Script 沒辦法從外面中止另一個正在跑的執行，所以正在跑的那一步會自己跑完；
 * 但狀態當場改成已取消、還沒開始的下一步當場撤掉，它跑完之後看到已取消就不會再往下排。
 * 已經停住的（那次執行早就被中止）則直接收尾，馬上可以重跑。
 */
function apiAdminCancelDaySync(key) {
  try {
    adminAuth_(key);
    return withLock_(function () {
      var st = daySyncState_();
      if (!st || st.status !== '處理中') {
        return { ok: true, message: '目前沒有進行中的更新' +
                 (st ? '（上一輪：' + st.status + '）' : '') + '，不需要取消。' };
      }
      var h = daySyncHealth_(st);
      clearDaySyncTriggers_();
      st.cancelled = true;
      st.status = '已取消';
      st.cancelledAt = nowStamp_();
      if (!h.running) { st.running = false; }   // 停住的那次執行不會回來改它，這裡替它收尾
      saveDaySync_(st);
      if (h.running) {
        return { ok: true, message: '已取消。「' + st.step + '」這一步正在執行，Apps Script 無法中途打斷，' +
                 '它會自己跑完（約 ' + h.left + ' 分鐘內），之後的步驟不會再執行；已完成的資料保留。' };
      }
      return { ok: true, message: '已取消。' +
               (h.stalled ? '「' + st.step + '」那一次執行早已中止（停住 ' + h.minutes + ' 分鐘）；' : '') +
               '之後的步驟不會再執行，已完成的資料保留。可以直接重新按「重寫郵件並刷新網站」。' };
    });
  } catch (e) { return { ok: false, message: '取消沒有成功：' + String(e.message || e) + '，請再按一次。' }; }
}

function runDayEditSync_() {
  // 一次只做一步；游標只在那一步成功回來之後才往前。
  var st = withLock_(function(){
    var current=daySyncState_();
    if(!current||current.status!=='處理中'||daySyncHealth_(current).running){return null;}
    current.running=true;current.stepStartedMs=Date.now();saveDaySync_(current);return current;
  });
  if (!st) { return; }
  if (st.cancelled) { st.status = '已取消'; st.running = false; saveDaySync_(st); return; }
  var myId = st.id, myIndex = st.index;
  st.step = DAY_SYNC_NAMES[myIndex];
  st.running = true;
  st.stepStartedMs = Date.now();
  saveDaySync_(st);
  try {
    if (myIndex === 0) {
      stepArticle_({ date: st.date, id: st.id, videoId: '', _nested: true });
    } else if (myIndex === 1) {
      rebuildHoldingsTrackerJob();
    } else if (myIndex === 2) {
      var result = rebuildPerformanceHistoryJob(st.date);
      if (!result || !result.ok || result.skipped || result.pending) { throw new Error(result && (result.reason || result.error) || '歷史績效未完成，請先補日K'); }
    } else if (myIndex === 3) { snapshotPerformanceJob(); }
  } catch (e) {
    var bad = daySyncState_();
    if (!bad || bad.id !== myId) {
      Logger.log('補登後同步：舊的一輪 ' + myId + ' 在「' + DAY_SYNC_NAMES[myIndex] +
                 '」出錯，但已換成新的一輪，不改狀態：' + (e.message || e));
      return;
    }
    bad.running = false;
    bad.error = String(e.message || e);
    if (bad.status === '已取消') { saveDaySync_(bad); return; }   // 已經取消了，錯誤只記下來
    bad.status = '等待續跑';
    bad.retryAt = Date.now()+5*60000;
    saveDaySync_(bad);
    Logger.log('儲存後同步等待重試：'+bad.error);
    return;
  }

  // 這一步可能跑了好幾分鐘，這段時間狀態可能被改過，一律以「現在」的為準：
  //   按了取消　 → 已取消，不可以再排下一步
  //   已重新送出 → 換成另一輪了，不可以拿手上這份舊的去蓋它
  var cur = daySyncState_();
  if (!cur || cur.id !== myId) { return; }
  cur.log = (cur.log || []).concat([DAY_SYNC_NAMES[myIndex] + '：完成']);
  cur.index = myIndex + 1;
  cur.running = false;
  if (cur.cancelled || cur.status === '已取消') { cur.status = '已取消'; }
  else { cur.status = cur.index >= cur.total ? '完成' : '處理中'; }
  cur.step = cur.status==='完成' ? '完成' : DAY_SYNC_NAMES[cur.index];
  cur.error='';
  saveDaySync_(cur);
  if (cur.status === '處理中') { scheduleDaySync_(); }
}

/* ------------------------------------------------------------------ *
 * 判定歷程
 *
 * 上游每一輪的稽核都會做很多決定：丟掉哪幾筆、哪幾筆改列歷史、
 * 哪個名字被改判成另一家公司、哪一段沒潤飾到。那些決定原本只印在
 * GitHub 的日誌裡——沒有人會每天去看，而且過幾天就被新的執行洗掉。
 *
 * 上游現在把它們寫進「判定歷程」分頁，這一支只負責讀出來給後台看。
 * 它是給人判讀用的，不參與任何計算。
 * ------------------------------------------------------------------ */
var DECISION_SHEET_ = '判定歷程';

function apiAdminDecisions(key, opts) {
  try {
    adminAuth_(key);
    var o = opts || {};
    var want = String(o.date || '').trim();
    var limit = Math.min(Math.max(Number(o.limit) || 200, 1), 500);

    var sh;
    try { sh = getSheet_(DECISION_SHEET_); }
    catch (e) { return { ok: true, rows: [], runs: [], note: '還沒有任何判定紀錄。' }; }

    var vals = sh.getDataRange().getValues();
    if (vals.length < 2) { return { ok: true, rows: [], runs: [], note: '還沒有任何判定紀錄。' }; }

    var head = vals[0].map(function (h) { return String(h).trim(); });
    var ci = {};
    ['時間', '執行代號', '影片日期', '步驟', '動作', '對象', '說明', '來源']
      .forEach(function (k) { ci[k] = head.indexOf(k); });

    var rows = [], runs = {};
    for (var i = vals.length - 1; i >= 1; i--) {
      var r = vals[i];
      var g = function (k) {
        var j = ci[k];
        return (j >= 0 && j < r.length) ? String(r[j]).trim() : '';
      };
      var d = fmtDate_(g('影片日期'));
      if (want && d !== want) { continue; }
      var run = g('執行代號');
      runs[run] = (runs[run] || 0) + 1;
      if (rows.length < limit) {
        rows.push({ at: g('時間'), run: run, date: d, step: g('步驟'),
                    action: g('動作'), subject: g('對象'),
                    detail: g('說明'), source: g('來源') });
      }
    }

    // 同一種判定重複出現，通常代表提示詞或規則該調了，所以順便統計。
    var tally = {};
    rows.forEach(function (x) {
      var k = x.step + '｜' + x.action;
      tally[k] = (tally[k] || 0) + 1;
    });
    var top = Object.keys(tally).sort(function (a, b) { return tally[b] - tally[a]; })
                .slice(0, 8).map(function (k) { return { what: k, n: tally[k] }; });

    return { ok: true, rows: rows, top: top,
             runs: Object.keys(runs).sort().reverse().slice(0, 20) };
  } catch (e) {
    return { ok: false, reason: String(e.message || e) };
  }
}

/** 先把日期記入後端佇列；同日連續儲存合併，執行中又有修改則再跑一輪。 */
function queueDayEditSync_(dateStr) {
  var d=fmtDate_(dateStr);
  withLock_(function(){
    var p=PropertiesService.getScriptProperties(),q=JSON.parse(p.getProperty('dayEditQueueV47')||'{}');
    q[d]=Date.now();p.setProperty('dayEditQueueV47',JSON.stringify(q));
  });
  try{dayEditSyncTick_();}catch(e){Logger.log('資料已儲存，同步由每五分鐘排程接續：'+e.message);}
  return {queued:true,date:d};
}
/** 續跑只推進游標，不重寫已成功步驟。硬中止由七分鐘租約回收。 */
function dayEditSyncTick_() {
  var runNow=false;
  function schedule(){try{scheduleDaySync_();}catch(e){Logger.log('續跑觸發器未排入，這一棒直接處理一步：'+e.message);runNow=true;}}
  withLock_(function(){
    var p=PropertiesService.getScriptProperties(),st=daySyncState_(),h=daySyncHealth_(st);
    if(h.running){return;}
    if(st&&Number(st.githubUntil||0)>Date.now()){return;}
    if(st&&st.status==='處理中'&&!h.stalled){return;}
    if(st&&(st.status==='等待續跑'||h.stalled)){
      if(Number(st.retryAt||0)>Date.now()){return;}
      st.running=false;st.status='處理中';st.step=DAY_SYNC_NAMES[st.index];saveDaySync_(st);schedule();return;
    }
    var q=JSON.parse(p.getProperty('dayEditQueueV47')||'{}'),dates=Object.keys(q).sort(function(a,b){return q[a]-q[b];});
    if(!dates.length){return;}
    var d=dates[0];
    /* 「COST:日期」是現有持股改成本排進來的（v54）：郵件內容沒變，從重算持股追蹤開始。 */
    var costOnly=/^COST:/.test(d),day=costOnly?d.slice(5):d;
    saveDaySync_({id:(costOnly?'COST-':'DAY-')+Date.now(),date:day,status:'處理中',index:costOnly?1:0,total:DAY_SYNC_NAMES.length,
      step:DAY_SYNC_NAMES[costOnly?1:0],log:costOnly?['同步郵件內容：修正成本不影響郵件，略過']:[],startedAt:nowStamp_(),running:false,
      kind:costOnly?'cost':'day'});
    // 狀態先存再移除佇列，觸發器失敗仍能由 watchdog 接回。
    delete q[d];p.setProperty('dayEditQueueV47',JSON.stringify(q));schedule();
  });
  if(runNow){runDayEditSync_();}
}

/** 現有持股改成本之後排同步：重算持股追蹤 → 從回合開始日重算歷史績效 → 記錄目前績效。 */
function queueCostSync_(roundStart) {
  var d = fmtDate_(roundStart);
  withLock_(function () {
    var p = PropertiesService.getScriptProperties(), q = JSON.parse(p.getProperty('dayEditQueueV47') || '{}');
    q['COST:' + d] = Date.now(); p.setProperty('dayEditQueueV47', JSON.stringify(q));
  });
  try { dayEditSyncTick_(); } catch (e) { Logger.log('成本已儲存，重算由每五分鐘排程接續：' + e.message); }
  return { queued: true, date: d };
}

/** Actions 帶工單ID推進一步；舊請求不能推進新工單，重送也不重做已完成的索引。 */
function apiDaySyncDrive_(key,op,id,index){
  adminAuth_(key);
  if(op==='performance'){
    withLock_(function(){
      var current=daySyncState_();
      if(current&&(current.status==='處理中'||current.status==='等待續跑')){throw new Error('已有更新工單，請待完成後再重建績效');}
      saveDaySync_({id:'PERF-'+Date.now(),date:PERFORMANCE_START_DATE,status:'處理中',index:1,total:DAY_SYNC_NAMES.length,
        step:DAY_SYNC_NAMES[1],log:[],startedAt:nowStamp_(),running:false,githubUntil:Date.now()+10*60000,runner:'GitHub Actions'});
    });
  }else if(op==='step'){
    var accepted=withLock_(function(){var st=daySyncState_();
      if(!st||st.id!==id||st.index!==Number(index)||st.cancelled||st.status==='完成'||st.status==='已取消'){return false;}
      if(st.status==='等待續跑'&&Number(st.retryAt||0)>Date.now()){return false;}
      st.status='處理中';st.githubUntil=Date.now()+10*60000;saveDaySync_(st);return true;
    });
    if(accepted){runDayEditSync_();}
  }
  return {ok:true,state:daySyncState_(),names:DAY_SYNC_NAMES};
}
