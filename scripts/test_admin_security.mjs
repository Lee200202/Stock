import {test, beforeEach, afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {createHash, createHmac, pbkdf2Sync} from 'node:crypto';
import {AdminSecurity, POLICY, COOKIE, cookie, sha, gatewayEnvelope, normalizeEmail} from '../site-api/admin-security.mjs';
import {adminRoute} from '../site-api/admin-router.mjs';
import worker from '../site-api/worker.mjs';

class Storage {
  data = new Map(); alarm = null;
  async get(k){return structuredClone(this.data.get(k));}
  async put(k,v){this.data.set(k,structuredClone(v));}
  async delete(k){this.data.delete(k);}
  async list({prefix,reverse,limit}={}){let v=[...this.data].filter(([k])=>k.startsWith(prefix||'')).sort(([a],[b])=>a.localeCompare(b));if(reverse)v.reverse();if(limit)v=v.slice(0,limit);return new Map(structuredClone(v));}
  async getAlarm(){return this.alarm;}
  async setAlarm(n){this.alarm=n;}
}
const password='local-test-only-a-long-passphrase-xxxx';
const salt='ab'.repeat(32), hash=pbkdf2Sync(password,salt,600000,32,'sha256').toString('hex');
let auth,store,env,now,mail,originalFetch;
beforeEach(()=>{
  store=new Storage();env={ADMIN_GATEWAY_SECRET:'cd'.repeat(32),ADMIN_DATA_KEY:'ef'.repeat(32),ADMIN_AUTH_EPOCH:'test1',
    ADMIN_PASSWORD_RECORD:JSON.stringify({salt,hash,iterations:600000}),GAS_WEBAPP_URL:'https://script.google.com/macros/s/test/exec',SITE_BRIDGE_TOKEN:'x'.repeat(64)};
  auth=new AdminSecurity({storage:store},env);now=Date.now();auth.now=()=>now;mail=[];originalFetch=globalThis.fetch;
  globalThis.fetch=async(_url,init)=>{const b=JSON.parse(init.body);mail.push(b);return Response.json({ok:true,result:{ok:true}});};
});
afterEach(()=>{globalThis.fetch=originalFetch;});
class Browser {
  jar={}; pre='';csrf='';ip='203.0.113.14';
  async send(path,body){const r=await auth.fetch(new Request('https://authority/'+path,{method:body===undefined?'GET':'POST',
    headers:{cookie:Object.entries(this.jar).map(([k,v])=>k+'='+v).join('; '),'X-CSRF-Token':['login','verify','resume','ai-login'].includes(path)?this.pre:this.csrf,
      'CF-Connecting-IP':this.ip,'User-Agent':'test-browser','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)}));
    for(const c of r.headers.getSetCookie()){const [pair]=c.split(';'),i=pair.indexOf('=');this.jar[pair.slice(0,i)]=pair.slice(i+1);}
    const b=await r.json();if(path==='bootstrap'&&b.ok)this.pre=b.csrf;
    if(['verify','resume','session','ai-login'].includes(path)&&b.ok)this.csrf=b.csrf;
    return {r,b};}
  async code(personal=false,email='manager@example.test'){
    await this.send('bootstrap');const out=await this.send('login',{password,email,personal});assert.equal(out.r.status,200);
    return mail.at(-1).args[1];}
  async login(personal=false){const code=await this.code(personal);const out=await this.send('verify',{code});assert.equal(out.r.status,200);return out.b;}
}
test('new browser requires passphrase AND a one-use email code',async()=>{
  const b=new Browser();assert.equal((await b.send('devices')).r.status,401);
  await b.code();assert.equal((await b.send('devices')).r.status,401);
  const code=mail.at(-1).args[1],v=await b.send('verify',{code});assert.equal(v.r.status,200);
  assert.equal((await b.send('verify',{code})).r.status,401);
  assert.equal((await b.send('devices')).b.devices.length,1);
});
test('user policy: any syntactically valid email may verify, with no account allowlist',async()=>{
  const b=new Browser();const code=await b.code(false,'another.person@example.test');await b.send('verify',{code});
  assert.equal((await b.send('devices')).b.devices[0].actor,'another.person@example.test');
});
test('wrong passphrase never sends a code or grants a session',async()=>{
  const b=new Browser();await b.send('bootstrap');assert.equal((await b.send('login',{password:'wrong',email:'a@example.test'})).r.status,401);assert.equal(mail.length,0);
});
test('invalid email, header injection and oversized password never reach mail transport',async()=>{
  assert.equal(normalizeEmail('a@example.test\r\nBcc:x@y.test'),'');const b=new Browser();await b.send('bootstrap');
  assert.equal((await b.send('login',{password,email:'not-an-email'})).r.status,401);assert.equal(mail.length,0);
});
test('login context is cookie-bound and CSRF protected',async()=>{
  const b=new Browser();await b.send('bootstrap');b.pre='wrong';assert.equal((await b.send('login',{password,email:'a@example.test'})).r.status,403);assert.equal(mail.length,0);
});
test('verification code expires after five minutes',async()=>{
  const b=new Browser(),code=await b.code();now+=POLICY.otp+1;assert.equal((await b.send('verify',{code})).r.status,401);
});
test('five invalid code attempts exhaust challenge; correct sixth attempt fails',async()=>{
  const b=new Browser(),code=await b.code();for(let i=0;i<5;i++)assert.equal((await b.send('verify',{code:code==='000000'?'111111':'000000'})).r.status,401);
  assert.equal((await b.send('verify',{code})).r.status,401);
});
test('a code from another browser cannot be used',async()=>{
  const b=new Browser(),c=new Browser();c.ip='203.0.113.15';const code=await b.code();await c.send('bootstrap');assert.equal((await c.send('verify',{code})).r.status,401);
});
test('resend is throttled and invalidates previous code after a successful new request',async()=>{
  const b=new Browser(),first=await b.code();assert.equal((await b.send('login',{password,email:'manager@example.test'})).r.status,429);
  now+=61000;assert.equal((await b.send('login',{password,email:'manager@example.test'})).r.status,200);
  const second=mail.at(-1).args[1];if(first!==second)assert.equal((await b.send('verify',{code:first})).r.status,401);
  assert.equal((await b.send('verify',{code:second})).r.status,200);
});
test('mail service rejection never creates an authenticated session',async()=>{
  globalThis.fetch=async()=>Response.json({ok:true,result:{ok:false}});
  const b=new Browser();await b.send('bootstrap');assert.equal((await b.send('login',{password,email:'manager@example.test'})).r.status,503);
  assert.equal((await store.list({prefix:'s:'})).size,0);
});
test('passphrase, OTP and cookie tokens are not stored in audit or returned device data',async()=>{
  const b=new Browser();await b.login(true);const data=JSON.stringify([...store.data]);
  assert(!data.includes(password));for(const value of Object.values(b.jar))assert(!data.includes(value));
  const devices=await b.send('devices');assert(!JSON.stringify(devices.b).includes('"digest":'));assert(!JSON.stringify(devices.b).includes('"iv":'));
  const events=JSON.stringify((await b.send('events')).b);assert(!events.includes('203.0.113.14'));assert(!events.includes('ip":{"'));
});
test('session cookie is HttpOnly, Secure, SameSite Strict and never a localStorage credential',()=>{
  const value=cookie(COOKIE.session,'x');for(const s of ['Path=/','Secure','HttpOnly','SameSite=Strict'])assert(value.includes(s));
  const source=readFileSync(new URL('../public-site/gas-source/AdminSecurity.html',import.meta.url),'utf8');assert(!source.includes('localStorage.setItem'));
});
test('polling updates connection time without extending fifteen minute idle deadline',async()=>{
  const b=new Browser();await b.login();for(let i=0;i<14;i++){now+=60000;assert.equal((await b.send('session')).r.status,200);}
  now+=60001;assert.equal((await b.send('session')).r.status,401);
});
test('public session absolute limit is thirty minutes despite active usage',async()=>{
  const b=new Browser();await b.login();now+=14*60000;await b.send('activity',{});now+=14*60000;await b.send('activity',{});now+=2*60000+1;
  assert.equal((await b.send('session')).r.status,401);
});
test('personal trust can explicitly resume; old trust rotates and cannot replay',async()=>{
  const b=new Browser();await b.login(true);const old=b.jar[COOKIE.trust];now+=16*60000;
  assert.equal((await b.send('session')).r.status,401);await b.send('bootstrap');assert.equal((await b.send('resume',{})).r.status,200);
  assert.notEqual(b.jar[COOKIE.trust],old);b.jar[COOKIE.trust]=old;assert.equal((await b.send('resume',{})).r.status,401);
});
test('personal trust expires at seven days, not sliding on resume',async()=>{
  const b=new Browser(),s=await b.login(true);now+=POLICY.trusted-1;await b.send('bootstrap');assert.equal((await b.send('resume',{})).b.expires,s.expires);
  now+=2;assert.equal((await b.send('resume',{})).r.status,401);
});
test('trusted recovery is not fresh reauthentication for revoking or revealing raw IP',async()=>{
  const b=new Browser(),s=await b.login(true);now+=16*60000;await b.send('bootstrap');await b.send('resume',{});
  assert.equal((await b.send('ip',{device:s.device})).r.status,403);assert.equal((await b.send('revoke-others',{})).r.status,403);
});
test('revoke-device cancels all session and trust tokens before acknowledging',async()=>{
  const a=new Browser(),b=new Browser();const sa=await a.login(true);now+=61000;b.ip='198.51.100.12';const sb=await b.login(true);
  assert.equal((await a.send('revoke',{device:sb.device})).r.status,200);
  assert.equal((await b.send('devices')).r.status,401);assert.equal((await b.send('admit',{method:'apiAdminUpdateRow',args:[null,{}]})).r.status,401);
  await b.send('bootstrap');assert.equal((await b.send('resume',{})).r.status,401);assert.equal((await a.send('session')).b.device,sa.device);
});
test('revoke-others keeps current browser valid and denies other admitted-after requests',async()=>{
  const a=new Browser(),b=new Browser();await a.login(true);now+=61000;b.ip='198.51.100.99';await b.login(true);
  await a.send('revoke-others',{});assert.equal((await b.send('session')).r.status,401);assert.equal((await a.send('session')).r.status,200);
});
test('device ID alone is not authentication',async()=>{
  const a=new Browser(),s=await a.login();const b=new Browser();b.jar[COOKIE.session]=s.device;assert.equal((await b.send('devices')).r.status,401);
});
test('logout invalidates session server-side; forgetting also kills trust',async()=>{
  const b=new Browser();await b.login(true);const old=b.jar[COOKIE.session];await b.send('logout',{forget:true});b.jar[COOKIE.session]=old;
  assert.equal((await b.send('session')).r.status,401);await b.send('bootstrap');assert.equal((await b.send('resume',{})).r.status,401);
});
test('changed auth epoch invalidates all old sessions and trust',async()=>{
  const b=new Browser();await b.login(true);env.ADMIN_AUTH_EPOCH='test2';assert.equal((await b.send('session')).r.status,401);await b.send('bootstrap');assert.equal((await b.send('resume',{})).r.status,401);
});
test('IP is encrypted, reveal is fresh-only and unavailable after retention',async()=>{
  const b=new Browser(),s=await b.login();assert(!JSON.stringify([...store.data]).includes('203.0.113.14'));
  assert.equal((await b.send('ip',{device:s.device})).b.ip,'203.0.113.14');now+=POLICY.fresh+1;assert.equal((await b.send('ip',{device:s.device})).r.status,403);
  now+=POLICY.ip;await auth.alarm();for(const d of (await store.list({prefix:'d:'})).values())assert.equal(d.ip,undefined);
});
test('audit expiry deletes ninety day old events and rate/prelogin records',async()=>{
  const b=new Browser();await b.login();now+=POLICY.audit+1;await auth.alarm();assert.equal((await store.list({prefix:'a:'})).size,0);assert.equal((await store.list({prefix:'p:'})).size,0);
});
test('every POST authenticated request enforces CSRF',async()=>{
  const b=new Browser();await b.login();b.csrf='forged';assert.equal((await b.send('revoke-others',{})).r.status,403);assert.equal((await b.send('rename',{device:'x',name:'x'})).r.status,403);
});
test('external route cannot expose internal admit/envelope endpoint',async()=>{
  env.ADMIN_SECURITY_ENABLED='true';env.ADMIN_AUTH={get(){throw Error('must not call');},idFromName(){return 'x';}};
  const r=await adminRoute(new Request('https://admin.test/admin-api/admit',{method:'POST',headers:{Origin:'https://admin.test','Content-Type':'application/json'},body:'{}'}),env,[],()=>{throw Error('network');});assert.equal(r.status,404);
});
test('cross-origin JSON, form POST and missing Origin are refused before DO',async()=>{
  env.ADMIN_SECURITY_ENABLED='true';env.ADMIN_AUTH={};
  for(const headers of [{Origin:'https://evil.test','Content-Type':'application/json'},{Origin:'https://admin.test','Content-Type':'text/plain'},{'Content-Type':'application/json'}]){
    const r=await adminRoute(new Request('https://admin.test/admin-api/login',{method:'POST',headers,body:'{}'}),env,[],()=>{});assert.equal(r.status,403);
  }
});
test('legacy Pages /api admin path is closed after activation',async()=>{
  env.ADMIN_SECURITY_ENABLED='true';const r=await worker.fetch(new Request('https://admin.test/api',{method:'POST',headers:{Origin:'https://lee200202.github.io','Content-Type':'application/json'},body:JSON.stringify({method:'apiAdminLogin',args:['old-key']})}),env,{});
  assert.equal(r.status,403);assert.equal(mail.length,0);
});
test('missing security bindings fail closed, without a legacy auth fallback',async()=>{
  env.ADMIN_SECURITY_ENABLED='true';const r=await adminRoute(new Request('https://admin.test/admin-api/session'),env,[],()=>{});assert.equal(r.status,503);
});
test('RPC allowlist is enforced before grant; browser key replaced and metadata cannot be injected',async()=>{
  env.ADMIN_SECURITY_ENABLED='true';const b=new Browser();await b.login();let seen;
  env.ADMIN_AUTH={idFromName:()=>1,get:()=>({fetch:async r=>{seen=r.clone();return auth.fetch(r);}})};
  let forwarded;
  const r=await adminRoute(new Request('https://admin.test/admin-api/rpc',{method:'POST',headers:{Origin:'https://admin.test',cookie:Object.entries(b.jar).map(([k,v])=>k+'='+v).join('; '),'X-CSRF-Token':b.csrf,'X-Admin-City':'forged','Content-Type':'application/json'},body:JSON.stringify({method:'apiAdminLogin',args:['forged-browser-key']})}),env,['apiAdminLogin'],async(_e,body)=>{forwarded=body;return {payload:{ok:true,result:{ok:true}},status:200};});
  assert.equal(r.status,200);assert.equal(forwarded.args[0],null);assert.equal(seen.headers.get('X-Admin-City'),'');assert(forwarded.grant.signature);
});
test('GAS verifies exact method, arguments, freshness, nonce and unforgeable runtime context',async()=>{
  const props=new Map([['ADMIN_SECURITY_ENABLED','true'],['ADMIN_GATEWAY_SECRET',env.ADMIN_GATEWAY_SECRET]]),cache=new Map();
  const bytes=b=>Array.from(b,x=>x>127?x-256:x);
  const ctx={PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)})},Utilities:{Charset:{UTF_8:'utf8'},DigestAlgorithm:{SHA_256:'sha256'},
    computeDigest:(_a,s)=>bytes(createHash('sha256').update(s).digest()),computeHmacSha256Signature:(s,k)=>bytes(createHmac('sha256',k).update(s).digest())},
    LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},CacheService:{getScriptCache:()=>({get:k=>cache.get(k),put:(k,v)=>cache.set(k,v)})}};
  vm.createContext(ctx);vm.runInContext(readFileSync(new URL('../public-site/gas-source/Adminauth.gs',import.meta.url),'utf8'),ctx);
  // Only evaluate permission function; never run unrelated production code.
  const src=readFileSync(new URL('../public-site/gas-source/Adminservice.gs',import.meta.url),'utf8');
  vm.runInContext(src.slice(src.indexOf('function adminAuth_(key)'),src.indexOf('/* ------------------------------------------------------------------',src.indexOf('function adminAuth_(key)'))),ctx);
  const body={method:'apiAdminUpdateRow',args:[null,{x:1}]};body.grant=await gatewayEnvelope(env,body.method,body.args,'device');
  ctx.body=body;assert.throws(()=>vm.runInContext('adminAuth_("old-key")',ctx));assert.throws(()=>vm.runInContext('adminAuth_({actor:"device"})',ctx));
  vm.runInContext('ADMIN_SECURITY_CONTEXT_=adminSecurityGrant_(body);adminAuth_(ADMIN_SECURITY_CONTEXT_);',ctx);
  assert.throws(()=>vm.runInContext('adminSecurityGrant_(body)',ctx));
  cache.clear();body.args[1].x=2;assert.throws(()=>vm.runInContext('adminSecurityGrant_(body)',ctx));
});
test('concurrent trust recovery admits only one successor without reviving old token',async()=>{
  const b=new Browser();await b.login(true);await b.send('bootstrap');
  const [a,c]=await Promise.all([b.send('resume',{}),b.send('resume',{})]);
  assert.deepEqual([a.r.status,c.r.status].sort(),[200,401]);
});
test('a revoke queued before another admission is an immediate authorization barrier',async()=>{
  const a=new Browser(),b=new Browser();await a.login();now+=61000;b.ip='198.51.100.91';const s=await b.login();
  const [revoke,admit]=await Promise.all([a.send('revoke',{device:s.device}),b.send('admit',{method:'apiAdminUpdateRow',args:[null,{}]})]);
  assert.equal(revoke.r.status,200);assert.equal(admit.r.status,401);
});
test('GAS mail quota reserve fails closed and never invokes MailApp when quota is low',()=>{
  let calls=0;const ctx={ADMIN_SECURITY_CONTEXT_:{actor:'test'},LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},
    CacheService:{getScriptCache:()=>({get:()=>null,put(){}})},MailApp:{getRemainingDailyQuota:()=>20,sendEmail(){calls++;}}};
  vm.createContext(ctx);vm.runInContext(readFileSync(new URL('../public-site/gas-source/Adminauth.gs',import.meta.url),'utf8'),ctx);
  vm.runInContext('ADMIN_SECURITY_CONTEXT_={actor:"test"};',ctx);
  const result=vm.runInContext('adminSecurityEmail_("test@example.invalid","123456","a".repeat(64))',ctx);
  assert.equal(result.ok,false);assert.equal(calls,0);
});
test('machine day-sync remains authorized after cutover, without making service keys browser credentials',()=>{
  const props=new Map([['ADMIN_SECURITY_ENABLED','true'],['ADMIN_MACHINE_KEY_ROTATED','true'],['ADMIN_KEY','rotated-machine-fixture']]);
  const ctx={PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)})},configMissing_:()=>[],rememberWebAppUrl_(){},jsonOut_:x=>x};
  vm.createContext(ctx);
  vm.runInContext(readFileSync(new URL('../public-site/gas-source/Adminauth.gs',import.meta.url),'utf8'),ctx);
  const src=readFileSync(new URL('../public-site/gas-source/Adminservice.gs',import.meta.url),'utf8');
  vm.runInContext(src.slice(src.indexOf('function adminAuth_(key)'),src.indexOf('/* ------------------------------------------------------------------',src.indexOf('function adminAuth_(key)'))),ctx);
  vm.runInContext(readFileSync(new URL('../public-site/gas-source/Code.gs',import.meta.url),'utf8'),ctx);
  ctx.configMissing_=()=>[];ctx.rememberWebAppUrl_=()=>{};
  vm.runInContext('function apiDaySyncDrive_(key,op,id){adminAuth_(key);return {ok:true,op:op,id:id};}',ctx);
  assert.throws(()=>vm.runInContext('adminAuth_("rotated-machine-fixture")',ctx));
  assert.equal(vm.runInContext('doGet({parameter:{action:"day-sync",key:"rotated-machine-fixture",op:"state"}})',ctx).ok,true);
  assert.equal(ctx.ADMIN_SECURITY_CONTEXT_,null);
  assert.throws(()=>vm.runInContext('adminAuth_("rotated-machine-fixture")',ctx));
  assert.equal(vm.runInContext('doGet({parameter:{action:"day-sync",key:"old-browser-fixture",op:"state"}})',ctx).ok,false);
  props.set('ADMIN_MACHINE_KEY_ROTATED','false');
  assert.equal(vm.runInContext('doGet({parameter:{action:"day-sync",key:"rotated-machine-fixture",op:"state"}})',ctx).ok,false);
});
test('static secure admin may be staged while authentication is disabled; API remains closed',async()=>{
  env.ADMIN_SECURITY_ENABLED='false';env.ADMIN_ASSETS={fetch:async()=>new Response('<h1>empty login UI</h1>')};
  const page=await adminRoute(new Request('https://admin.test/admin/'),env,[],()=>{});
  assert.equal(page.status,200);assert.equal(page.headers.get('X-Frame-Options'),'DENY');
  assert.equal((await adminRoute(new Request('https://admin.test/admin-api/bootstrap'),env,[],()=>{})).status,503);
  assert.equal((await store.list({prefix:'s:'})).size,0);
});
test('AI ticket is consumed once across concurrent browsers, with read-only access and no email',async()=>{
  const owner=new Browser();await owner.login();
  const {ticket}=(await owner.send('ai-ticket',{})).b;
  const a=new Browser(),b=new Browser();await a.send('bootstrap');await b.send('bootstrap');
  const results=await Promise.all([a.send('ai-login',{ticket,personal:true}),b.send('ai-login',{ticket})]);
  assert.deepEqual(results.map(x=>x.r.status).sort(),[200,401]);
  const reader=results[0].r.ok?a:b;
  assert.equal(mail.length,1);
  assert.equal((await reader.send('admit',{method:'apiAdminReviewHistory',args:[null,{}]})).r.status,200);
  assert.equal((await reader.send('admit',{method:'apiAdminReviewHistorySave',args:[null,{}]})).r.status,403);
  assert.equal((await reader.send('devices')).r.status,403);
  assert.equal((await reader.send('ai-ticket',{})).r.status,403);
  assert.equal((await reader.send('bootstrap')).b.canResume,false);
  now+=15*60000;
  assert.equal((await reader.send('session')).r.status,401);
});
test('AI tickets expire after five minutes and cannot be issued from a stale verification',async()=>{
  const owner=new Browser();await owner.login();const {ticket}=(await owner.send('ai-ticket',{})).b;
  now+=5*60000;
  const reader=new Browser();await reader.send('bootstrap');
  assert.equal((await reader.send('ai-login',{ticket})).r.status,401);
  assert.equal((await owner.send('ai-ticket',{})).r.status,403);
});
