/** GitHub Pages 原站前台的傳輸橋接；畫面檔案仍是原本的 Index/Stylesheet/JavaScript。 */
function siteBridgeSetup() {
  var p = PropertiesService.getScriptProperties();
  var token = p.getProperty('SITE_BRIDGE_TOKEN');
  if (!token || token.length < 32) {
    token = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
    p.setProperty('SITE_BRIDGE_TOKEN', token);
  }
  Logger.log('把這串貼進 Cloudflare Worker 的 SITE_BRIDGE_TOKEN Secret（不要貼進 GitHub）：' + token);
  return { ok: true, configured: true };
}

function siteBridge_(e) {
  var token = PropertiesService.getScriptProperties().getProperty('SITE_BRIDGE_TOKEN');
  if (!token || token.length < 32) { return { ok: false, error: 'bridge-not-configured' }; }
  var body;
  try { body = JSON.parse(String(e && e.postData && e.postData.contents || '')); }
  catch (err) { return { ok: false, error: 'invalid-json' }; }
  if (!body || body.bridgeToken !== token) { return { ok: false, error: 'unauthorized' }; }
  var args = body.args;
  if (!Array.isArray(args) || args.length > 5) { return { ok: false, error: 'invalid-args' }; }

  /* 不用 globalThis[method] 任意呼叫：即使前台遭注入，也絕不能調到後台、
     寄信、排程、讀密鑰或清表函式。只列原站公開 HTML 實際用的 API。 */
  var methods = {
    apiAsk: apiAsk,
    apiFormatTranscript: apiFormatTranscript,
    apiGetCandlesBundle: apiGetCandlesBundle,
    apiGetDashboard: apiGetDashboard,
    apiGetHoldingsTracker: apiGetHoldingsTracker,
    apiGetLineEntry: apiGetLineEntry,
    apiGetMailContent: apiGetMailContent,
    apiGetMarketOverview: apiGetMarketOverview,
    apiGetMemberSms: apiGetMemberSms,
    apiGetPerformanceSeries: apiGetPerformanceSeries,
    apiGetQuotesFor: apiGetQuotesFor,
    apiGetStockFundamentals: apiGetStockFundamentals,
    apiGetStockSummary: apiGetStockSummary,
    apiGetTechStats: apiGetTechStats,
    apiGetTranscript: apiGetTranscript,
    apiGetUserContext: apiGetUserContext,
    apiListMailDates: apiListMailDates,
    apiListModels: apiListModels,
    apiListRecordDates: apiListRecordDates,
    apiListSmsDates: apiListSmsDates,
    apiListTranscriptDates: apiListTranscriptDates,
    apiLogUsage: apiLogUsage,
    apiLookupSubscription: apiLookupSubscription,
    apiResetSession: apiResetSession,
    apiSearchByDate: apiSearchByDate,
    apiSearchStock: apiSearchStock,
    apiSendUnsubscribeLink: apiSendUnsubscribeLink,
    apiStopAllMail: apiStopAllMail,
    apiSubscribe: apiSubscribe,
    apiSuggestCodes: apiSuggestCodes,
    apiUnsubscribeConfirm: apiUnsubscribeConfirm,
    apiUpdateSubscription: apiUpdateSubscription,
    apiValidateKey: apiValidateKey
  };
  var method = String(body.method || '');
  if (!Object.prototype.hasOwnProperty.call(methods, method)) {
    /* 後台原版面也可從 Pages 開，但絕不能只靠前端隱藏按鈕。
       所有後台請求在這裡再驗一次 ADMIN_KEY；登入交原函式驗證並回原有訊息。 */
    var adminNames = ('apiAdminCancelCrawl apiAdminCancelDaySync apiAdminCancelFix ' +
      'apiAdminCancelFullFix apiAdminCancelJob apiAdminCancelRefresh apiAdminCancelSmsJob ' +
      'apiAdminChainState apiAdminCrawlState apiAdminCrawlTranscript apiAdminDayRows ' +
      'apiAdminDaySyncState apiAdminDecisions apiAdminDeleteRow apiAdminDeleteSms ' +
      'apiAdminDeleteSmsBatch apiAdminDispatch apiAdminFixState apiAdminFullFixState ' +
      'apiAdminHeldList apiAdminHoldToday apiAdminInstallSectorCatchup apiAdminJobStatus ' +
      'apiAdminKCoverage apiAdminLineLookup apiAdminLineRetry apiAdminLineSaveConfig ' +
      'apiAdminLineStatus apiAdminListSms apiAdminLogin apiAdminManualDays ' +
      'apiAdminManualEntry apiAdminMergeTranscripts apiAdminOpsDay apiAdminPreviewCleanup ' +
      'apiAdminRebuildMail apiAdminReclassify apiAdminResumeFullFix apiAdminResumeJob ' +
      'apiAdminRetryFullFixStep apiAdminRunInfo apiAdminSetDailyPushStart ' +
      'apiAdminSetHoldingCost apiAdminSmsDays apiAdminSmsState apiAdminStartDaySync ' +
      'apiAdminStartFullFix apiAdminStartRefreshFrom apiAdminStartSmsJob apiAdminSubmit ' +
      'apiAdminTodayStatus apiAdminUpdateRow').split(' ');
    if (adminNames.indexOf(method) < 0) { return { ok: false, error: 'method-not-allowed' }; }
    var adminKey = PropertiesService.getScriptProperties().getProperty('ADMIN_KEY');
    if (method !== 'apiAdminLogin' && (!adminKey || String(args[0] || '') !== adminKey)) {
      return { ok: false, error: 'admin-unauthorized' };
    }
    if (typeof globalThis[method] !== 'function') { return { ok: false, error: 'method-unavailable' }; }
    methods[method] = globalThis[method];
  }
  try { return { ok: true, result: methods[method].apply(null, args) }; }
  catch (err) { return { ok: false, error: String(err && err.message || err).slice(0, 200) }; }
}
