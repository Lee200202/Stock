/** Adminauth: owner-enabled migration. The legacy browser ADMIN_KEY is NOT a session credential. */
var ADMIN_SECURITY_CONTEXT_ = null;
function adminSecurityEnabled_() {
  return PropertiesService.getScriptProperties().getProperty('ADMIN_SECURITY_ENABLED') === 'true';
}
/** Only Code.gs's fixed machine routes may mint this execution-local context. */
function adminMachineContext_(key,operation) {
  var p=PropertiesService.getScriptProperties(),real=p.getProperty('ADMIN_KEY');
  if(['day-sync','diagnostic'].indexOf(operation)<0||!real||String(key||'')!==real||
     p.getProperty('ADMIN_MACHINE_KEY_ROTATED')!=='true')throw new Error('機器服務密鑰不正確或尚未完成輪替。');
  ADMIN_SECURITY_CONTEXT_={actor:'machine:'+operation};return ADMIN_SECURITY_CONTEXT_;
}
function adminSecurityHex_(bytes) {
  return bytes.map(function(b){return ('0'+((b+256)%256).toString(16)).slice(-2);}).join('');
}
function adminSecurityGrant_(body) {
  var p=PropertiesService.getScriptProperties(),key=p.getProperty('ADMIN_GATEWAY_SECRET'),g=body.grant;
  if(!adminSecurityEnabled_()||!key||key.length<64||!g||g.v!==1||typeof g.at!=='number'||Math.abs(Date.now()-g.at)>60000||
     !/^[a-f0-9]{64}$/.test(g.nonce||'')||g.method!==body.method||typeof g.actor!=='string') throw new Error('安全後台驗證失敗。');
  var hash=adminSecurityHex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,JSON.stringify(body.args),Utilities.Charset.UTF_8));
  if(hash!==g.argsHash)throw new Error('安全後台參數不符。');
  var unsigned={v:g.v,at:g.at,nonce:g.nonce,actor:g.actor,method:g.method,argsHash:g.argsHash};
  var expected=adminSecurityHex_(Utilities.computeHmacSha256Signature(JSON.stringify(unsigned),key,Utilities.Charset.UTF_8));
  if(typeof g.signature!=='string'||g.signature.length!==expected.length)throw new Error('安全後台簽章不符。');
  var diff=0;for(var i=0;i<expected.length;i++)diff|=expected.charCodeAt(i)^g.signature.charCodeAt(i);
  if(diff)throw new Error('安全後台簽章不符。');
  var lock=LockService.getScriptLock();if(!lock.tryLock(5000))throw new Error('安全後台忙碌，請稍後重試。');
  try {var cache=CacheService.getScriptCache(),nk='admin-nonce:'+g.nonce;
    if(cache.get(nk))throw new Error('安全後台請求已受理，請讀回確認。');cache.put(nk,'1',120);
  }finally{lock.releaseLock();}
  return {actor:g.actor};
}
function adminSecurityEmail_(email,code,id) {
  if(!ADMIN_SECURITY_CONTEXT_)throw new Error('未驗證的驗證碼寄送。');
  if(!/^[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+$/.test(email)||email.length>254||!/^\d{6}$/.test(code)||!/^[a-f0-9]{64}$/.test(id))throw new Error('驗證碼請求格式不符。');
  // No subscribers, daily ledgers or LINE. This is only the explicitly requested login code.
  var lock=LockService.getScriptLock();if(!lock.tryLock(5000))throw new Error('驗證碼服務忙碌。');
  try{var cache=CacheService.getScriptCache(),key='admin-mail:'+id;
    if(cache.get(key))return {ok:false,reason:'請重新申請驗證碼。'};
    if(MailApp.getRemainingDailyQuota()<=20)return {ok:false,reason:'保留每日整理信額度，暫停登入驗證碼。'};
    cache.put(key,'pending',600); // Ambiguous delivery must not be retried automatically.
    MailApp.sendEmail({to:email,subject:'盤勢有據｜後台登入驗證碼',name:'盤勢有據',
      body:'您正在登入盤勢有據後台。\n\n驗證碼：'+code+'\n\n有效時間五分鐘，限用一次。請勿轉寄或提供給他人。若不是您本人操作，請忽略這封信。'});
    cache.put(key,'sent',600);return {ok:true};
  }finally{lock.releaseLock();}
}
/** Owner editor only: enables the gate after prerequisites are explicitly checked. No secret output. */
function enableAdminSecurity_() {
  var p=PropertiesService.getScriptProperties();
  if(!/^[a-f0-9]{64}$/.test(p.getProperty('ADMIN_GATEWAY_SECRET')||'')||p.getProperty('ADMIN_MACHINE_KEY_ROTATED')!=='true')
    throw new Error('請先完成獨立閘道秘密與機器密鑰輪替，再啟用安全後台。');
  p.setProperty('ADMIN_SECURITY_ENABLED','true');
  return {ok:true,legacyBrowserLoginDisabled:true};
}
