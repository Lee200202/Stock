/** Accept only the public view context offered by the feedback form. */
function feedbackContext_(payload, kind) {
  var context = {};
  var date = typeof payload.viewingDate === 'string' ? payload.viewingDate : '';
  if (/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    var parsed = new Date(date + 'T00:00:00Z');
    if (isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date) context.viewingDate = date;
  }
  if (['light', 'dark'].indexOf(payload.theme) >= 0) context.theme = payload.theme;
  if (typeof payload.viewport === 'string' && /^[1-9]\d{0,4}x[1-9]\d{0,4}$/.test(payload.viewport)) {
    context.viewport = payload.viewport;
  }
  if (kind === '內容更正') {
    if (typeof payload.stockCode === 'string' && /^(?:00981A|\d{4,6})$/.test(payload.stockCode)) {
      context.stockCode = payload.stockCode;
    }
    if (['分類方向有誤', '價位或數字有誤', '語音同音錯字', '跨股借句', '其他內容補充'].indexOf(payload.issue) >= 0) {
      context.issue = payload.issue;
    }
  }
  return context;
}

function feedbackContextText_(context) {
  return [
    ['畫面日期', context.viewingDate],
    ['主題', {light:'淺色', dark:'深色'}[context.theme]],
    ['視窗', context.viewport],
    ['股票代號', context.stockCode],
    ['更正項目', context.issue]
  ].filter(function (item) { return item[1]; })
    .map(function (item) { return item.join('　'); }).join('\n');
}

/** Save website feedback privately, then notify the configured owner. */
function apiSubmitFeedback(payload) {
  var kinds = ['操作問題', '內容更正', '功能建議'];
  var kind = payload && payload.kind;
  var page = payload && payload.page;
  var message = payload && typeof payload.message === 'string' ? payload.message.trim() : '';
  if (kinds.indexOf(kind) === -1 || message.length < 8 || message.length > 1000 ||
      /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(message)) {
    return {ok:false, message:'請選擇分類，並輸入 8 至 1,000 字。'};
  }
  if (payload.website) return {ok:false, message:'請重新開啟回饋表單。'};
  if (['overview','tracker','subscribe','perf','mail','tx','tech','stock'].indexOf(page) === -1) page = '網站';
  var contextText = feedbackContextText_(feedbackContext_(payload, kind));
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1500)) return {ok:false, message:'回饋服務忙碌，請稍後再試。'};
  var receipt;
  var owner;
  var notificationAllowed = false;
  try {
    var cache = CacheService.getScriptCache();
    var hash = Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, kind + '\n' + page + '\n' + message + (contextText ? '\n' + contextText : '')));
    var key = 'visitor-feedback:' + hash;
    var previous = cache.get(key);
    if (previous) return {ok:true, duplicate:true, receipt:previous === 'saved' ? '' : previous};
    var props = PropertiesService.getScriptProperties();
    var today = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMdd');
    var prior = String(props.getProperty('VISITOR_FEEDBACK_DAILY') || '').split(':');
    var count = prior[0] === today ? Number(prior[1]) || 0 : 0;
    if (count >= 80) return {ok:false, message:'今日回饋收件量已達上限，請稍後再試。'};
    var ss = getSS_();
    var sheet = ss.getSheetByName('網站意見回饋');
    if (!sheet) {
      sheet = ss.insertSheet('網站意見回饋');
      sheet.appendRow(['收到時間', '分類', '頁面', '回饋內容', '收件編號', '畫面資訊']);
      sheet.setFrozenRows(1);
    }
    receipt = Utilities.getUuid();
    // Prefix visitor text with a quote so it cannot become a spreadsheet formula.
    var row = [new Date(), kind, page, "'" + message, receipt];
    if (contextText) {
      var header = sheet.getRange(1, 1, 1, Math.max(5, sheet.getLastColumn())).getValues()[0];
      var contextIndex = header.indexOf('畫面資訊');
      if (contextIndex < 0) {
        contextIndex = header.length;
        sheet.getRange(1, contextIndex + 1).setValue('畫面資訊');
      }
      while (row.length < contextIndex) row.push('');
      row[contextIndex] = contextText;
    }
    sheet.appendRow(row);
    cache.put(key, receipt, 600);
    props.setProperty('VISITOR_FEEDBACK_DAILY', today + ':' + (count + 1));
    owner = String(props.getProperty('OWNER_EMAIL') || '').trim();
    // Bound automated mail from this public form independently of subscriber delivery.
    notificationAllowed = count < 20 && /^[^\s,@]+@[^\s,@]+\.[^\s,@]+$/.test(owner);
  } catch (error) {
    return {ok:false, message:'目前無法確認是否收到回饋，請稍後再試。'};
  } finally {
    lock.releaseLock();
  }
  if (notificationAllowed) {
    try {
      if (MailApp.getRemainingDailyQuota() > 20) {
        MailApp.sendEmail({to:owner, name:'盤勢有據',
          subject:'盤勢有據｜收到網站回饋 · ' + kind,
          body:'分類　' + kind + '\n頁面　' + page + '\n收件編號　' + receipt +
            (contextText ? '\n' + contextText : '') + '\n\n' + message + '\n\n完整紀錄請查看管理試算表的「網站意見回饋」。'});
      } else {
        Logger.log('網站回饋已存檔，通知保留郵件額度。收件編號 ' + receipt);
      }
    } catch (error) {
      Logger.log('網站回饋已存檔，管理通知失敗。收件編號 ' + receipt);
    }
  }
  return {ok:true, receipt:receipt};
}
