/**
 * 舊版 step=all 的續跑入口。每次只執行一個步驟或一批，不在一次 GAS
 * 執行內重複最多 40 批。相同日期的呼叫沿用游標，失败保留原位置。
 * @param {string} adminKey 已由 API 驗證的管理密鑰。
 * @param {string} dateStr 目標日期。
 * @param {Object} params 原請求參數。
 * @return {Object} 呼叫端在 chunked=true、done=false 時再次請求。
 */
function runRefreshAllChunk_(adminKey, dateStr, params) {
  return withRefreshAllLease_(dateStr, function () {
  var props = PropertiesService.getScriptProperties();
  var stateKey = 'refreshAllCursor:' + (dateStr || 'latest');
  var index = Number(props.getProperty(stateKey) || 0);
  if (!isFinite(index) || index < 0 || index >= REFRESH_ORDER_.length) { index = 0; }
  var step = REFRESH_ORDER_[index];
  var result = apiRefreshStep_(adminKey, step, dateStr, params);
  if (!result.ok || result.cancelled) { return result; }
  if (result.done) { index++; }
  if (index === REFRESH_ORDER_.length) { props.deleteProperty(stateKey); }
  else { props.setProperty(stateKey, String(index)); }
  return {ok:true,step:'all',chunked:true,done:index === REFRESH_ORDER_.length,
          processed:index,total:REFRESH_ORDER_.length,result:result.name + '：' + result.result};
  });
}

/** 同一日期的 all 游標一次只准一個請求推進；執行業務時不持有 ScriptLock。 */
function withRefreshAllLease_(dateStr, fn) {
  var props = PropertiesService.getScriptProperties();
  var key = 'refreshAllLease:' + (dateStr || 'latest');
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(1500)) { return {ok:false,busy:true,done:false,error:'刷新正在更新進度，請稍後重試'}; }
  var token = Utilities.getUuid();
  try {
    var lease;
    try { lease = JSON.parse(props.getProperty(key) || 'null'); } catch (e) { lease = null; }
    if (lease && lease.until > Date.now()) { return {ok:false,busy:true,done:false,error:'同一天的刷新仍在執行，請稍後重試'}; }
    props.setProperty(key, JSON.stringify({token:token,until:Date.now()+7*60*1000}));
  } finally { lock.releaseLock(); }
  try { return fn(); }
  finally {
    // If a GAS execution is terminated, the lease expires without manual cleanup.
    if (lock.tryLock(1500)) {
      try {
        var current = JSON.parse(props.getProperty(key) || 'null');
        if (current && current.token === token) { props.deleteProperty(key); }
      } finally { lock.releaseLock(); }
    }
  }
}
