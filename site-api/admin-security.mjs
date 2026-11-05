// Admin authentication authority. Never use public KV, analytics IDs or browser keys here.
const enc = new TextEncoder();
const READ_ADMIN = new Set(('apiAdminLogin apiAdminChainState apiAdminCrawlState apiAdminDayRows apiAdminReviewHistory '+
  'apiAdminDaySyncState apiAdminDecisions apiAdminFixState apiAdminFullFixState apiAdminHeldList apiAdminJobStatus '+
  'apiAdminKCoverage apiAdminLineDiagnose apiAdminLineLookup apiAdminLineStatus apiAdminListSms apiAdminManualDays '+
  'apiAdminOpsDay apiAdminRunInfo apiAdminSmsDays apiAdminSmsState apiAdminTodayStatus').split(' '));
export const POLICY = Object.freeze({idle: 15 * 60000, public: 30 * 60000,
  trusted: 7 * 86400000, fresh: 5 * 60000, otp: 5 * 60000, ip: 7 * 86400000, audit: 90 * 86400000});
export const COOKIE = Object.freeze({session: '__Host-psSession', trust: '__Host-psTrust', pre: '__Host-psPre'});
const AI_TICKET_MS = 5 * 60000;
const AI_SESSION_MS = 15 * 60000;
const AI_READ_METHODS = new Set(['apiAdminLogin', 'apiAdminTodayStatus', 'apiAdminReviewHistory', 'apiAdminHeldList', 'apiAdminChainState', 'apiAdminDaySyncState']);
const hex = b => Array.from(new Uint8Array(b), x => x.toString(16).padStart(2, '0')).join('');
export const random = () => hex(crypto.getRandomValues(new Uint8Array(32)));
export const sha = async s => hex(await crypto.subtle.digest('SHA-256', enc.encode(s)));
export async function mac(key, s) {
  const k = await crypto.subtle.importKey('raw', enc.encode(key), {name:'HMAC', hash:'SHA-256'}, false, ['sign']);
  return hex(await crypto.subtle.sign('HMAC', k, enc.encode(s)));
}
export function equal(a, b) { // Fixed length digests only.
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let n = 0; for (let i = 0; i < a.length; i++) n |= a.charCodeAt(i) ^ b.charCodeAt(i); return n === 0;
}
export async function passwordDigest(password, record) {
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  try{return hex(await crypto.subtle.deriveBits({name:'PBKDF2', hash:'SHA-256',
    salt:enc.encode(record.salt), iterations:record.iterations}, key, 256));}
  catch(e){if(/iteration/i.test(String(e.message||'')))deny('password-cost-unsupported',503);throw e;}
}
export function cookie(name, value, seconds) {
  return `${name}=${value}; Path=/; Secure; HttpOnly; SameSite=Strict${seconds === undefined ? '' : '; Max-Age=' + seconds}`;
}
export function cookies(request) {
  const out = {};
  for (const part of (request.headers.get('cookie') || '').split(';')) {
    const at = part.indexOf('='); if (at > 0) out[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  } return out;
}
export function maskIp(ip) {
  if (/^\d+\.\d+\.\d+\.\d+$/.test(ip)) return ip.split('.').slice(0, 3).join('.') + '.*';
  return ip.includes(':') ? ip.split(':').slice(0, 3).join(':') + ':…' : '未提供';
}
export function normalizeEmail(v) {
  const s = String(v || '').trim().toLowerCase();
  return s.length <= 254 && /^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}$/.test(s) ? s : '';
}
export function metadata(request) {
  const ip = request.headers.get('CF-Connecting-IP') || '';
  return {ip, maskedIp:maskIp(ip), country:request.headers.get('X-Admin-Country') || '', city:request.headers.get('X-Admin-City') || '',
    ua:(request.headers.get('user-agent') || '未提供').slice(0, 300)};
}
export function json(body, status = 200, setCookies = []) {
  const headers = new Headers({'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store, private',
    'Pragma':'no-cache', 'X-Content-Type-Options':'nosniff', 'Referrer-Policy':'no-referrer'});
  setCookies.forEach(c => headers.append('Set-Cookie', c));
  return new Response(JSON.stringify(body), {status, headers});
}
class AuthError extends Error { constructor(code, status = 401) { super(code); this.status = status; } }
const deny = (code, status) => { throw new AuthError(code, status); };

// Envelope authenticates method AND exact arguments. A browser never gets this key or envelope.
export async function gatewayEnvelope(env, method, args, actor, now = Date.now()) {
  const grant = {v:1, at:now, nonce:random(), actor:String(actor).slice(0, 80), method, argsHash:await sha(JSON.stringify(args))};
  grant.signature = await mac(env.ADMIN_GATEWAY_SECRET, JSON.stringify(grant));
  return grant;
}
export async function gatewayCall(env, method, args, actor) {
  const grant = await gatewayEnvelope(env, method, args, actor);
  // Never follow the credential-bearing POST. Only the content result GET may follow redirects.
  let r = await fetch(env.GAS_WEBAPP_URL + '?action=site-bridge', {method:'POST', redirect:'manual',
    signal:AbortSignal.timeout(20000), headers:{'Content-Type':'application/json'},
    body:JSON.stringify({method, args, grant, bridgeToken:env.SITE_BRIDGE_TOKEN})});
  if ([302,303].includes(r.status)) {
    const url = new URL(r.headers.get('Location') || '', env.GAS_WEBAPP_URL);
    if (url.protocol !== 'https:' || url.hostname !== 'script.googleusercontent.com' || url.pathname !== '/macros/echo') deny('gateway-redirect', 503);
    r = await fetch(url, {redirect:'follow', signal:AbortSignal.timeout(20000)});
  }
  if (!r.ok || !/json/i.test(r.headers.get('content-type') || '')) deny('gateway-unavailable', 503);
  const b = await r.json(); if (!b.ok) deny('gateway-unavailable', 503); return b.result;
}

export class AdminSecurity {
  constructor(state, env) { this.state = state; this.env = env; this.store = state.storage; this.tail = Promise.resolve(); }
  // Serialize across asynchronous crypto/network waits; a revoke acknowledgement is an admission barrier.
  fetch(request) {
    const p = this.tail.then(() => this.handle(request));
    this.tail = p.catch(() => {}); return p;
  }
  now() { return Date.now(); }
  async audit(action, meta, device = '', actor = '', target = '') {
    const at = this.now(), id = random().slice(0, 24);
    await this.store.put('a:' + at + ':' + id, {id, at, action, device, actor, target,
      ip:await this.seal(meta.ip || ''), maskedIp:meta.maskedIp, country:meta.country, city:meta.city});
    if (!await this.store.getAlarm()) await this.store.setAlarm(at + 6 * 3600000);
  }
  async seal(s) {
    const key = await crypto.subtle.importKey('raw', Uint8Array.from(this.env.ADMIN_DATA_KEY.match(/../g), x => parseInt(x, 16)), 'AES-GCM', false, ['encrypt']);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    return {iv:hex(iv), text:hex(await crypto.subtle.encrypt({name:'AES-GCM', iv}, key, enc.encode(s)))};
  }
  async unseal(s) {
    const bytes = x => Uint8Array.from(x.match(/../g) || [], b => parseInt(b, 16));
    const key = await crypto.subtle.importKey('raw', bytes(this.env.ADMIN_DATA_KEY), 'AES-GCM', false, ['decrypt']);
    return new TextDecoder().decode(await crypto.subtle.decrypt({name:'AES-GCM', iv:bytes(s.iv)}, key, bytes(s.text)));
  }
  async limit(key, count, window) {
    if (!await this.store.getAlarm()) await this.store.setAlarm(this.now() + 6 * 3600000);
    const name = 'r:' + await mac(this.env.ADMIN_DATA_KEY, key);
    const old = await this.store.get(name), now = this.now();
    const b = old && old.expires > now ? old : {n:0, expires:now + window};
    if (b.n >= count) deny('rate-limited', 429);
    b.n++; await this.store.put(name, b);
  }
  async pre(request) {
    const token = cookies(request)[COOKIE.pre];
    if (!/^[a-f0-9]{64}$/.test(token || '')) deny('login-context');
    const key = 'p:' + await sha(token), p = await this.store.get(key);
    if (!p || p.expires <= this.now() || !equal(request.headers.get('X-CSRF-Token'), p.csrf)) deny('login-context', 403);
    return {key, p};
  }
  async session(request, fresh = false) {
    const value = cookies(request)[COOKIE.session] || '';
    if (!/^[a-f0-9]{64}$/.test(value)) deny('session-required');
    const key = 's:' + await sha(value), s = await this.store.get(key);
    const d = s && await this.store.get('d:' + s.device), now = this.now();
    if (!s || !d || d.revoked || s.revoked || s.epoch !== this.env.ADMIN_AUTH_EPOCH || s.expires <= now || now - s.active >= POLICY.idle) deny('session-expired');
    if (request.method !== 'GET' && !equal(request.headers.get('X-CSRF-Token'), s.csrf)) deny('csrf', 403);
    if (fresh && now - s.verified >= POLICY.fresh) deny('fresh-auth-required', 403);
    return {key, s, d};
  }
  async issue(device, meta, personal, actor, verified = this.now(), ai = false) {
    const now = this.now(), sessionToken = random(), trustToken = personal ? random() : '';
    const csrf = random(), d = await this.store.get('d:' + device) || {id:device, created:now, name:''};
    // New manual login/resume supersedes this browser's former sessions (not other browsers).
    for (const [k, s] of await this.store.list({prefix:'s:'})) if (s.device === device) await this.store.delete(k);
    for (const [k, t] of await this.store.list({prefix:'t:'})) if (t.device === device) await this.store.delete(k);
    const expires = personal ? (verified === 0 ? Math.min(d.trustUntil, now + POLICY.trusted) : now + POLICY.trusted) : now + (ai ? AI_SESSION_MS : POLICY.public);
    const s = {device, actor, csrf, created:now, active:now, connected:now, expires, verified, personal, ai, epoch:this.env.ADMIN_AUTH_EPOCH};
    await this.store.put('s:' + await sha(sessionToken), s);
    Object.assign(d, {revoked:0, signedOut:0, actor, ua:meta.ua, maskedIp:meta.maskedIp, ip:await this.seal(meta.ip || ''),
      ipAt:now, country:meta.country, city:meta.city, login:now, active:now, connected:now, expires, personal,
      ai, trustUntil:personal ? expires : 0});
    await this.store.put('d:' + device, d);
    if (personal) await this.store.put('t:' + await sha(trustToken), {device, actor, expires, epoch:this.env.ADMIN_AUTH_EPOCH});
    return {s, setCookies:[cookie(COOKIE.session, sessionToken), cookie(COOKIE.trust, trustToken,
      personal ? Math.max(0, Math.floor((expires - now)/1000)) : 0)]};
  }
  async revoke(id) {
    const d = await this.store.get('d:' + id); if (!d) return;
    d.revoked = this.now(); d.trustUntil = 0; await this.store.put('d:' + id, d);
    for (const prefix of ['s:', 't:']) for (const [key, s] of await this.store.list({prefix})) if (s.device === id) await this.store.delete(key);
  }
  async handle(request) {
    try {
      if (!this.env.ADMIN_GATEWAY_SECRET || !/^[a-f0-9]{64}$/.test(this.env.ADMIN_DATA_KEY || '') || !this.env.ADMIN_AUTH_EPOCH) deny('security-not-configured', 503);
      const path = new URL(request.url).pathname, meta = metadata(request), now = this.now();
      let input = {};
      if (request.method === 'POST') {
        const raw = await request.text(); if (enc.encode(raw).length > 64000) deny('size', 413);
        try { input = JSON.parse(raw); } catch { deny('json', 400); }
        if (!input || Array.isArray(input) || typeof input !== 'object') deny('json', 400);
      } else if (request.method !== 'GET') deny('method', 405);
      if (path === '/bootstrap' && request.method === 'GET') {
        await this.limit('bootstrap:' + meta.ip, 60, 15 * 60000);
        const token = random(), csrf = random();
        await this.store.put('p:' + await sha(token), {csrf, expires:now + 15 * 60000});
        let session = null;
        try { const a = await this.session(request); session = {device:a.d.id, csrf:a.s.csrf, expires:a.s.expires, ai:!!a.s.ai}; } catch {}
        const t = await this.store.get('t:' + await sha(cookies(request)[COOKIE.trust] || ''));
        const d = t && await this.store.get('d:' + t.device);
        return json({ok:true, csrf, session, canResume:!!(t && d && !d.revoked && t.epoch === this.env.ADMIN_AUTH_EPOCH && t.expires > now)}, 200,
          [cookie(COOKIE.pre, token)]);
      }
      if (path === '/ai-login' && request.method === 'POST') {
        await this.pre(request);
        await this.limit('ai-login:' + meta.ip, 12, 15 * 60000);
        if (typeof input.ticket !== 'string' || !/^PS-AI-[a-f0-9]{64}$/.test(input.ticket)) deny('ai-ticket-invalid');
        const key = 'ai:' + await sha(input.ticket);
        const ticket = await this.store.get(key);
        const issuer = ticket && await this.store.get('d:' + ticket.device);
        if (!ticket || !issuer || issuer.revoked || ticket.expires <= now || ticket.epoch !== this.env.ADMIN_AUTH_EPOCH) deny('ai-ticket-invalid');
        // The authority serializes requests. Consume before issuing any session.
        await this.store.delete(key);
        const device = random().slice(0, 24);
        const out = await this.issue(device, meta, false, 'AI 臨時存取', 0, true);
        await this.audit('ai-login', meta, device, 'AI 臨時存取', ticket.reference);
        return json({ok:true, csrf:out.s.csrf, device, expires:out.s.expires, ai:true}, 200, out.setCookies);
      }
      if (['/login', '/verify', '/resume'].includes(path)) {
        if (request.method !== 'POST') deny('method', 405);
        const {key, p} = await this.pre(request);
        if (path === '/login') {
          await this.limit('password:' + meta.ip, 8, 15 * 60000);
          const email = normalizeEmail(input.email);
          let record; try { record = JSON.parse(this.env.ADMIN_PASSWORD_RECORD || ''); } catch {}
          if (!record || record.iterations !== 600000 || !/^[a-f0-9]{64}$/.test(record.hash) || !/^[a-f0-9]{64}$/.test(record.salt)) deny('security-not-configured', 503);
          if (typeof input.password !== 'string' || input.password.length > 256 || !email ||
              !equal(await passwordDigest(input.password, record), record.hash)) {
            await this.audit('login-failed', meta); deny('login-failed');
          }
          const addressKey = await mac(this.env.ADMIN_DATA_KEY, email);
          await this.limit('email-minute:' + addressKey, 1, 60000);
          await this.limit('email-hour:' + addressKey, 5, 3600000);
          await this.limit('mail-all', 20, 3600000);
          let n; do { n = crypto.getRandomValues(new Uint32Array(1))[0]; } while (n >= 4294000000);
          const code = String(n % 1000000).padStart(6, '0'), id = random();
          // Invalidate the old challenge BEFORE sending, including ambiguous delivery failures.
          delete p.challenge; await this.store.put(key, p);
          const sent = await gatewayCall(this.env, 'adminSecurityEmail', [email, code, id], 'login');
          if (!sent?.ok) deny('mail-unavailable', 503);
          p.challenge = {id, digest:await mac(this.env.ADMIN_DATA_KEY, id + ':' + code),
            actor:email, personal:input.personal === true, expires:now + POLICY.otp, attempts:0};
          await this.store.put(key, p);
          await this.audit('code-sent', meta, '', email);
          return json({ok:true, expires:p.challenge.expires, retryAfter:60});
        }
        if (path === '/verify') {
          const c = p.challenge;
          if (!c || c.expires <= now || c.attempts >= 5) deny('code-expired');
          c.attempts++; await this.store.put(key, p);
          if (!/^[0-9]{6}$/.test(input.code || '') || !equal(await mac(this.env.ADMIN_DATA_KEY, c.id + ':' + input.code), c.digest)) {
            await this.audit('code-failed', meta, '', c.actor); deny('code-invalid');
          }
          delete p.challenge; await this.store.put(key, p); // One-use even on later failure.
          let device = random().slice(0, 24);
          try { device = (await this.session(request)).d.id; } catch {
            const t=await this.store.get('t:'+await sha(cookies(request)[COOKIE.trust]||''));
            const d=t&&await this.store.get('d:'+t.device);
            if(t&&d&&!d.revoked&&t.epoch===this.env.ADMIN_AUTH_EPOCH&&t.expires>now)device=d.id;
          }
          const out = await this.issue(device, meta, c.personal, c.actor);
          await this.audit('login', meta, device, c.actor);
          return json({ok:true, csrf:out.s.csrf, device, expires:out.s.expires}, 200, out.setCookies);
        }
        // Explicit trust recovery; rotate credentials. Stale/concurrent old token fails closed.
        const token = cookies(request)[COOKIE.trust] || '', tk = 't:' + await sha(token), t = await this.store.get(tk);
        const d = t && await this.store.get('d:' + t.device);
        if (!t || !d || d.revoked || t.epoch !== this.env.ADMIN_AUTH_EPOCH || t.expires <= now) deny('trust-expired');
        await this.store.delete(tk);
        const out = await this.issue(t.device, meta, true, t.actor, 0);
        await this.audit('trust-resume', meta, t.device, t.actor);
        return json({ok:true, csrf:out.s.csrf, device:t.device, expires:out.s.expires}, 200, out.setCookies);
      }
      const sensitive = ['/revoke', '/revoke-others', '/ip', '/ai-ticket'].includes(path);
      const a = await this.session(request, sensitive);
      if (a.s.ai && !['/session','/activity','/logout','/admit'].includes(path)) deny('ai-read-only',403);
      if (path === '/ai-ticket' && request.method === 'POST') {
        await this.limit('ai-issue:' + a.d.id, 5, 15 * 60000);
        const ticket = 'PS-AI-' + random();
        const expires = now + AI_TICKET_MS;
        const reference = random().slice(0,12);
        await this.store.put('ai:' + await sha(ticket), {device:a.d.id, reference, expires, epoch:this.env.ADMIN_AUTH_EPOCH});
        await this.audit('ai-ticket-issued', meta, a.d.id, a.s.actor, reference);
        return json({ok:true, ticket, reference, expires});
      }
      if (path === '/session' && request.method === 'GET') {
        a.s.connected = now; a.d.connected = now;
        await this.store.put(a.key, a.s); await this.store.put('d:' + a.d.id, a.d);
        return json({ok:true, csrf:a.s.csrf, device:a.d.id, expires:a.s.expires, ai:!!a.s.ai, idleExpires:a.s.active + POLICY.idle});
      }
      if (path === '/activity' && request.method === 'POST') {
        a.s.active = now; a.d.active = now;
        await this.store.put(a.key, a.s); await this.store.put('d:' + a.d.id, a.d); return json({ok:true});
      }
      if (path === '/devices' && request.method === 'GET') {
        const devices = Array.from((await this.store.list({prefix:'d:'})).values()).map(d => ({id:d.id, name:d.name,
          actor:d.actor, ua:d.ua, maskedIp:d.maskedIp, country:d.country, city:d.city, login:d.login, active:d.active,
          connected:d.connected, expires:d.expires, trustUntil:d.trustUntil, personal:d.personal, ai:!!d.ai,
          status:d.revoked ? 'revoked' : d.signedOut ? 'signedout' : d.expires <= now ? 'expired' : now - d.active >= POLICY.idle ? 'locked' : 'active', current:d.id === a.d.id}));
        return json({ok:true, devices});
      }
      if (path === '/events' && request.method === 'GET') {
        const all = Array.from((await this.store.list({prefix:'a:', reverse:true})).values()).filter(x => now - x.at <= POLICY.audit);
        const events = all.slice(0,200).map(({ip, ...x}) => x);
        return json({ok:true, events, limit:200, total:all.length,
          failed24:all.filter(x => now - x.at <= 86400000 && /failed$/.test(x.action)).length});
      }
      if (path === '/rename' && request.method === 'POST') {
        const d = await this.store.get('d:' + String(input.device)); if (!d) deny('device-missing', 404);
        if (typeof input.name !== 'string' || input.name.length > 60 || /[\x00-\x1f]/.test(input.name)) deny('name', 400);
        d.name = input.name.trim(); await this.store.put('d:' + d.id, d);
        await this.audit('rename', meta, a.d.id, a.s.actor, d.id); return json({ok:true});
      }
      if (path === '/revoke' && request.method === 'POST') {
        await this.revoke(String(input.device)); await this.audit('revoke', meta, a.d.id, a.s.actor, String(input.device));
        return json({ok:true});
      }
      if (path === '/revoke-others' && request.method === 'POST') {
        for (const d of (await this.store.list({prefix:'d:'})).values()) if (d.id !== a.d.id) await this.revoke(d.id);
        await this.audit('revoke-others', meta, a.d.id, a.s.actor); return json({ok:true});
      }
      if (path === '/ip' && request.method === 'POST') {
        const d = await this.store.get('d:' + String(input.device));
        if (!d || !d.ip || now - d.ipAt > POLICY.ip) return json({ok:true, ip:'已逾保留期限或未提供'});
        await this.audit('view-ip', meta, a.d.id, a.s.actor, d.id);
        return json({ok:true, ip:await this.unseal(d.ip)});
      }
      if (path === '/logout' && request.method === 'POST') {
        await this.store.delete(a.key);
        if (input.forget === true || !a.s.personal) await this.revoke(a.d.id);
        else {a.d.signedOut=now;await this.store.put('d:'+a.d.id,a.d);}
        await this.audit('logout', meta, a.d.id, a.s.actor);
        return json({ok:true}, 200, [cookie(COOKIE.session, '', 0), ...(input.forget === true || !a.s.personal ? [cookie(COOKIE.trust, '', 0)] : [])]);
      }
      if (path === '/admit' && request.method === 'POST') {
        // Worker validates the fixed method allowlist; DO issues no general reusable credential.
        if (!/^apiAdmin[A-Za-z]+$/.test(input.method) || !Array.isArray(input.args) || input.args.length > 8) deny('method', 400);
        if (a.s.ai && !AI_READ_METHODS.has(input.method)) deny('ai-read-only',403);
        if(!READ_ADMIN.has(input.method))await this.audit('admit:' + input.method, meta, a.d.id, a.s.actor);
        return json({ok:true, grant:await gatewayEnvelope(this.env, input.method, input.args, a.d.id)});
      }
      deny('not-found', 404);
    } catch (e) {
      // Do not emit exception text: fetch errors can contain URLs, email and credentials.
      return json({ok:false, error:e instanceof AuthError ? e.message : 'security-unavailable'}, e instanceof AuthError ? e.status : 503);
    }
  }
  alarm() { // Same serialized authority as HTTP requests.
    const p = this.tail.then(async () => {
      const now = this.now();
      for (const prefix of ['a:', 'd:', 's:', 't:', 'p:', 'r:', 'ai:']) {
        for (const [key, row] of await this.store.list({prefix})) {
          if (prefix === 'a:') {
            if (now - row.at > POLICY.audit) { await this.store.delete(key); continue; }
            if (now - row.at > POLICY.ip && row.ip) { delete row.ip; await this.store.put(key, row); }
          } else if (prefix === 'd:') {
            if (now - row.connected > POLICY.audit) { await this.store.delete(key); continue; }
            if (now - row.ipAt > POLICY.ip && row.ip) { delete row.ip; await this.store.put(key, row); }
          } else if (row.expires <= now) await this.store.delete(key);
        }
      }
      await this.store.setAlarm(now + 6 * 3600000);
    }); this.tail = p.catch(() => {}); return p;
  }
}
