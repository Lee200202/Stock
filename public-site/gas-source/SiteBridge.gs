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
    apiUpdateSubscription: apiUpdateSubscription,
    apiValidateKey: apiValidateKey
  };
  var method = String(body.method || '');
  if (!Object.prototype.hasOwnProperty.call(methods, method)) {
    return { ok: false, error: 'method-not-allowed' };
  }
  try { return { ok: true, result: methods[method].apply(null, args) }; }
  catch (err) { return { ok: false, error: String(err && err.message || err).slice(0, 200) }; }
}
