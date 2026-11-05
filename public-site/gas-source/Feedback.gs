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
  if (['overview','tracker','subscribe','perf','mail','tx','tech'].indexOf(page) === -1) page = '網站';
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1500)) return {ok:false, message:'回饋服務忙碌，請稍後再試。'};
  var receipt;
  var owner;
  var notificationAllowed = false;
  try {
    var cache = CacheService.getScriptCache();
    var hash = Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, kind + '\n' + page + '\n' + message));
    var key = 'visitor-feedback:' + hash;
    if (cache.get(key)) return {ok:true, duplicate:true};
    var props = PropertiesService.getScriptProperties();
    var today = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMdd');
    var prior = String(props.getProperty('VISITOR_FEEDBACK_DAILY') || '').split(':');
    var count = prior[0] === today ? Number(prior[1]) || 0 : 0;
    if (count >= 80) return {ok:false, message:'今日回饋收件量已達上限，請稍後再試。'};
    var ss = getSS_();
    var sheet = ss.getSheetByName('網站意見回饋');
    if (!sheet) {
      sheet = ss.insertSheet('網站意見回饋');
      sheet.appendRow(['收到時間', '分類', '頁面', '回饋內容', '收件編號']);
      sheet.setFrozenRows(1);
    }
    receipt = Utilities.getUuid();
    // Prefix visitor text with a quote so it cannot become a spreadsheet formula.
    sheet.appendRow([new Date(), kind, page, "'" + message, receipt]);
    cache.put(key, 'saved', 600);
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
            '\n\n' + message + '\n\n完整紀錄請查看管理試算表的「網站意見回饋」。'});
      } else {
        Logger.log('網站回饋已存檔，通知保留郵件額度。收件編號 ' + receipt);
      }
    } catch (error) {
      Logger.log('網站回饋已存檔，管理通知失敗。收件編號 ' + receipt);
    }
  }
  return {ok:true};
}
