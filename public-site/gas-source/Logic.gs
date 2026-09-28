/** 刷新步驟及進度；每個請求只處理一個可續跑的工作單位。 */

/**
 * 分步重算。每一步是一次獨立的網頁請求，各自享有完整的執行時間額度。
 *
 * step 可以是：
 *   purge   清除產業列
 *   gate    稽核與複審（分三次請求：完整性稽核 → 內容複審 → 重寫每日整理）
 *           可另外帶 date=YYYY/MM/DD 指定日期，沒帶就是今天
 *   codes   代號比對
 *   fund    更新基本面
 *   dailyk  補齊日K
 *   tracker 重算持股追蹤
 *   perf    記錄績效
 *   all     依序全部跑（保留給手動測試，正式呼叫請一步一步來，
 *           因為 all 很可能再次撞上時間上限）
 */
var REFRESH_STEPS_ = {
  purge:   { name: '清除產業列',   fn: function () { return purgeIndustryRows() + ' 列'; } },
  /* 品質關卡的三段，各自一格。

        本來就是分三次請求做完的（稽核 → 複審 → 重寫每日整理），
        先前卻擠成一格「稽核與複審」——那一格會亮三到六分鐘，
        期間分不出它在做哪一段，卡住時也看不出卡在哪一段。
        拆成三格不多花任何一次模型呼叫。

        排在代號比對之前，是因為稽核會補進新的列，那些列還沒有代號。

        gate 保留著沒有拿掉：舊版的 pipeline.py 與任何直接打
        ?action=refresh&step=gate 的東西還在用它。它不在 REFRESH_ORDER_ 裡，
        所以每日刷新走的是拆開後的三格。 */
  gate:    { name: '稽核與複審', chunked: true,
             fn: function (dateStr) { return qualityGateChunk_(dateStr); } },
  gate1:   { name: '稽核補漏', chunked: true,
             fn: function (dateStr) { return gatePhaseChunk_(dateStr, '稽核'); } },
  gate2:   { name: '內容複審', chunked: true,
             fn: function (dateStr) { return gatePhaseChunk_(dateStr, '複審'); } },
  gate3:   { name: '重寫整理', chunked: true,
             fn: function (dateStr) { return gatePhaseChunk_(dateStr, '重寫'); } },
  codes:   { name: '代號比對',     fn: function () { repairCodesJob(); return '完成'; } },
  fund:    { name: '更新基本面',   fn: function () { rebuildFundamentalsJob(); return '完成'; } },
  // 補日K改用安全小批次：每次最多約四十五秒，剩下的由呼叫端再打一次。
  // 一次跑完會久到讓 Google 前端把連線切掉，呼叫端只會看到
  // 「連線被切斷且沒有回應」，完全看不出是超時。
  dailyk:  { name: '補齊日K', chunked: true,
             fn: function () { return backfillDailyKChunk_(45); } },
  tracker: { name: '重算持股追蹤', fn: function () { rebuildHoldingsTrackerJob(); return '完成'; } },
  perf:    { name: '記錄績效',     fn: function () { snapshotPerformanceJob(); return '完成'; } },
  /* 績效歷史重算。不在 REFRESH_ORDER_ 裡，所以每日刷新不會碰到它——
     每日只需要補今天那一個點（perf），整條曲線重算是資料被修正之後才要做的事。
     用「持股追蹤的回合JSON ＋ 日K收盤價」直接算，不重跑追蹤，也不花模型額度。 */
  perfhist: { name: '重算績效歷史',
              fn: function (dateStr) {
                var r = rebuildPerformanceHistoryJob(dateStr || '');
                if (!r || !r.ok) { throw new Error('績效歷史未重算：' + ((r && r.reason) || '未知')); }
                // 「沒有東西可以重算」與「重算了 0 天」在日誌上要看得出差別，
                // 否則下次有人看到 0 會以為壞了。
                if (r.skipped) { return {ok:false,pending:true,done:false,blockedBy:'dailyk',error:r.reason || '日K資料尚未備齊'}; }
                return '重算 ' + r.days + ' 個交易日，' + r.codes + ' 檔';
              } },
  // 全面重整。不在 REFRESH_ORDER_ 裡，所以每日刷新不會碰到它，
  // 只有 GitHub 的 full_fix 模式會指名呼叫。一次做一棒，呼叫端重複打到 done。
  fullfix: { name: '全面重整', chunked: true,
             fn: function () { return runFullFixChunk_(); } },
  /* 唯讀查進度，不做任何事。
     驅動端遇到讀取逾時時用它判斷「那一棒到底有沒有做成」——
     逾時只代表連線沒等到回應，不代表工作沒進行：Apps Script 被時間上限
     砍掉時執行是被中止的，但砍掉之前寫進去的進度都還在。
     沒有這一支的話，驅動端只能把逾時一律當失敗，重試三次就放棄，
     而實際上每一次它都在前進。 */
  fullfixstate: { name: '全面重整進度', chunked: true,
                  fn: function () {
                    var st = fullFixState_() || {};
                    return { done: st.status !== '處理中',
                             processed: Number(st.index) || 0,
                             total: Number(st.total) || (FULLFIX_STEPS.length - 1),
                             note: (st.step || '（沒有進行中的重整）') +
                                   (st.sub && st.sub.total
                                     ? '　' + st.sub.done + '/' + st.sub.total : '') +
                                   '　' + (st.status || '') };
                  } },
  /* 會員簡訊進度：讀取或由 GitHub Actions 回報即時進度 */
  smsstate: { name: '會員簡訊進度', chunked: true,
              fn: function (d, params) {
                if (params && (params.status || params.pct || params.sub_step)) {
                  var prev = getSmsParseState_() || {};
                  var next = {
                    jobId: params.job_id || prev.jobId || '',
                    runId: prev.runId || '',
                    runUrl: prev.runUrl || '',
                    mode: prev.mode || params.mode || 'parse',
                    execution: prev.execution || 'github',
                    status: params.status || prev.status || '處理中',
                    step: params.sub_step || params.step_name || prev.step || 'AI模型解析',
                    index: params.index !== undefined ? Number(params.index) : (prev.index || 2),
                    total: params.total !== undefined ? Number(params.total) : (prev.total || 4),
                    done: params.done !== undefined ? Number(params.done) : (prev.done || 0),
                    pct: params.pct !== undefined ? Number(params.pct) : (prev.pct || 50),
                    note: params.note || prev.note || '',
                    lastError: params.status === '失敗' ? (params.note || prev.lastError || '') : '',
                    since: prev.since || '', through: prev.through || todayStr_(),
                    scanned: params.scanned !== undefined ? Number(params.scanned) : (prev.scanned || 0),
                    zhang: params.zhang !== undefined ? Number(params.zhang) : (prev.zhang || 0),
                    saved: params.saved !== undefined ? Number(params.saved) : (prev.saved || 0),
                    existed: params.existed !== undefined ? Number(params.existed) : (prev.existed || 0),
                    errors: params.errors !== undefined ? Number(params.errors) : (prev.errors || 0),
                    /* 步驟清單由 GitHub 回報，後台照著畫。兩邊版本不同步時
                       進度條的格數才不會對不上（例如後台畫 8 格、實際已跑到第 12 步）。 */
                    steps: params.steps ? String(params.steps).split('|') : (prev.steps || null),
                    /* 配額冷卻。撞到「每日請求數」上限時，pipeline.py 會算出
                       太平洋時間午夜換算成台北時間的重置點並送過來。
                       後台派工前會看這個值，還沒到就直接擋下，
                       不要再讓人每十五分鐘按一次、每次都白跑十四分鐘。 */
                    cooldownUntil: params.cooldown_until !== undefined ?
                                   String(params.cooldown_until) : (prev.cooldownUntil || ''),
                    quotaKind: params.quota_kind !== undefined ?
                               String(params.quota_kind) : (prev.quotaKind || ''),
                    /* 分桶計數：已判定有資料、已判定無個股、空白待解析、待寫入。
                       這是「哪些要花配額、哪些不用」在畫面上的依據。 */
                    blankRows: params.blank !== undefined ? Number(params.blank) : (prev.blankRows || 0),
                    doneRows: params.doneRows !== undefined ? Number(params.doneRows) : (prev.doneRows || 0),
                    emptyRows: params.emptyRows !== undefined ? Number(params.emptyRows) : (prev.emptyRows || 0),
                    pendingWrite: params.pendingWrite !== undefined ? Number(params.pendingWrite) : (prev.pendingWrite || 0),
                    aiQueued: params.aiQueued !== undefined ? Number(params.aiQueued) : (prev.aiQueued || 0),
                    aiUsed: params.aiUsed !== undefined ? Number(params.aiUsed) : (prev.aiUsed || 0),
                    reused: params.reused !== undefined ? Number(params.reused) : (prev.reused || 0),
                    deferred: params.deferred !== undefined ? Number(params.deferred) : (prev.deferred || 0),
                    writtenRows: params.written !== undefined ? Number(params.written) : (prev.writtenRows || 0),
                    remaining: params.remaining !== undefined ? Number(params.remaining) : (prev.remaining || 0),
                    auditReady: String(params.audit_ready || '').toLowerCase() === 'true' ||
                                (params.status === '完成') || !!prev.auditReady,
                    startedAt: prev.startedAt || Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss'),
                    updatedAt: Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd HH:mm:ss')
                  };
                  setSmsParseState_(next);
                  // 每日配額用盡時把冷卻時間鎖起來，後台派工那一關會擋。
                  if (next.quotaKind === 'daily' && next.cooldownUntil) {
                    smsSetCooldown_(next.cooldownUntil);
                  }
                  // 順利跑完就解除冷卻，不要讓昨天的鎖擋住今天。
                  if (next.status === '完成') {
                    PropertiesService.getScriptProperties().deleteProperty(SMS_COOLDOWN_PROP_);
                  }
                  return { ok: true, state: next };
                }
                return { ok: true, state: getSmsParseState_() };
              } },
  /* 會員簡訊改動某個歷史日期後，單獨重寫該日的每日整理。
     寄送狀態由 stepArticle_ 保留，因此不會把歷史信再寄一次。 */
  smsmail: { name: '同步會員簡訊衍生內容',
             fn: function (dateStr) {
               var d = fmtDate_(dateStr);
               if (!d) { throw new Error('缺少可驗證日期，未重寫郵件內容'); }
               stepArticle_({ date: d, videoId: '', id: 'SMSMAIL-' + d,
                              step: '撰稿', done: 0, total: 1 });
               return d + ' 郵件查詢已同步';
             } }
};

/* 這一棒的子進度。分批步驟（補日K、稽核與複審）回報 processed/total 時填進來，
   markChainStep_ 會把它一起寫進狀態。用模組變數而不是再存一份指令碼屬性，
   是因為它只在同一次執行裡有意義——下一次請求會重新算。 */
var CHAIN_SUB_ = null;

/**
 * 記下刷新鏈目前在哪一步。
 *
 * fullfix 不記——它有自己那一套更細的進度（跑到第幾天），記進來只會把
 * 兩件不同的事混在同一個狀態裡。
 */
function markChainStep_(step, name, status, err) {
  // fullfixstate 與 smsstate 是獨立進度查詢/更新，不該被當成「整條鏈跑到這一步」記進去。
  if (step === 'fullfix' || step === 'fullfixstate' || step === 'smsstate' || step === 'smsmail') { return; }
  try {
    var order = REFRESH_ORDER_.indexOf(step) >= 0 ? REFRESH_ORDER_ : [step];
    var at = order.indexOf(step);
    // 取消旗標要留著。整包覆寫會把它洗掉，那樣按了取消之後下一步照樣做完，
    // 看起來就像取消沒有用。
    var prev = chainState_() || {};
    PropertiesService.getScriptProperties().setProperty(CHAIN_KEY_, JSON.stringify({
      step: step, name: name, status: status,
      // 分批步驟的「第幾批／共幾批」。補日K與稽核複審一格會亮好幾分鐘，
      // 沒有這個數字的話，畫面上與卡住完全分不出來。
      sub: (CHAIN_SUB_ && CHAIN_SUB_.step === step) ? CHAIN_SUB_.sub : null,
      note: (CHAIN_SUB_ && CHAIN_SUB_.step === step) ? CHAIN_SUB_.note : '',
      index: at, total: order.length, order: order,
      error: err || '',
      cancelled: !!prev.cancelled,
      updatedAt: Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy/MM/dd HH:mm:ss')
    }));
  } catch (e) { /* 記進度失敗不能影響真正的工作 */ }
}

function chainState_() {
  var raw = PropertiesService.getScriptProperties().getProperty(CHAIN_KEY_);
  try { return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
}

function chainCancelled_() {
  var st = chainState_();
  return !!(st && st.cancelled);
}

/** 開始新的一輪刷新之前要把旗子拔掉，否則新的一輪一進來就被自己擋掉。 */
function clearChainCancel_() {
  var st = chainState_() || {};
  st.cancelled = false;
  PropertiesService.getScriptProperties().setProperty(CHAIN_KEY_, JSON.stringify(st));
}

/**
 * 檢查會員簡訊是否已解析就緒（簡訊優先守衛）。
 *
 * 回傳 'ready' 或 'pending'。
 * 若 SMS_PRIORITY_GUARD 關閉，則一律回傳 'ready'。
 * 若當前有簡訊作業處於「處理中」，或該日（預設今日）有「待解析」簡訊，則回傳 'pending'。
 */
function isSmsReady_(dateStr) {
  if (typeof SMS_PRIORITY_GUARD !== 'undefined' && !SMS_PRIORITY_GUARD) {
    return 'ready';
  }
  var d = (typeof fmtDate_ === 'function' ? fmtDate_(dateStr) : '') || (typeof todayStr_ === 'function' ? todayStr_() : '');
  try {
    var st = (typeof getSmsParseState_ === 'function') ? getSmsParseState_() : null;
    if (st && st.status === '處理中') {
      return 'pending';
    }
    if (typeof cmCountPendingSms_ === 'function' && d) {
      var pending = cmCountPendingSms_(d);
      if (pending > 0) {
        return 'pending';
      }
    }
  } catch (e) {
    Logger.log('isSmsReady_ 檢查失敗：' + e);
  }
  return 'ready';
}
