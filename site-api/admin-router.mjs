import {json} from './admin-security.mjs';
export async function adminRoute(request, env, adminMethods, forward) {
  const url = new URL(request.url);
  if (env.ADMIN_SECURITY_ENABLED !== 'true') return json({ok:false, error:'security-not-enabled'}, 503);
  if (!env.ADMIN_AUTH) return json({ok:false, error:'security-not-configured'}, 503);
  const api = url.pathname.startsWith('/admin-api/');
  if (!api) {
    if (!['GET','HEAD'].includes(request.method)) return json({ok:false,error:'method'},405);
    if (!['/admin/', '/admin/original-bridge.js'].includes(url.pathname) || !env.ADMIN_ASSETS) return json({ok:false,error:'not-found'},404);
    const response = await env.ADMIN_ASSETS.fetch(request);
    const headers = new Headers(response.headers);
    headers.set('Cache-Control','no-store, private'); headers.set('X-Frame-Options','DENY');
    headers.set('Referrer-Policy','no-referrer'); headers.set('X-Content-Type-Options','nosniff');
    headers.set('Permissions-Policy','geolocation=(), camera=(), microphone=()');
    // Legacy admin has inline handlers; scripts stay same-origin. No external analytics or frames.
    headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'");
    return new Response(response.body, {status:response.status, headers});
  }
  if (request.method === 'POST' && (request.headers.get('Origin') !== url.origin ||
      !/^application\/json(?:;|$)/i.test(request.headers.get('Content-Type') || ''))) return json({ok:false,error:'origin'},403);
  if (!['GET','POST'].includes(request.method)) return json({ok:false,error:'method'},405);
  const routes = new Set(['bootstrap','login','verify','resume','session','activity','devices','events','rename','revoke','revoke-others','ip','logout','rpc']);
  if (!routes.has(url.pathname.slice('/admin-api/'.length))) return json({ok:false,error:'not-found'},404);
  const stub = env.ADMIN_AUTH.get(env.ADMIN_AUTH.idFromName('admin-authority-v1'));
  const doRequest = async (path, body) => {
    const headers = new Headers(request.headers);
    headers.set('X-Admin-Country', request.cf?.country || '');
    headers.set('X-Admin-City', request.cf?.city || '');
    // Location metadata is trusted only from the incoming edge, never a JSON field.
    if(body===undefined&&request.method==='POST'){
      const raw=await limitedBody(request);try{body=JSON.parse(raw);}catch{throw new Error('json');}
    }
    return stub.fetch(new Request('https://authority' + path, {method:body === undefined ? request.method : 'POST',
      headers, body:body === undefined ? undefined : JSON.stringify(body)}));
  };
  try {
    if (url.pathname !== '/admin-api/rpc') return await doRequest(url.pathname.slice('/admin-api'.length));
    if (request.method !== 'POST') return json({ok:false,error:'method'},405);
    const raw = await limitedBody(request);
    let body; try { body = JSON.parse(raw); } catch { return json({ok:false,error:'json'},400); }
    if (!adminMethods.includes(body?.method) || !Array.isArray(body.args) || !body.args.length || body.args.length > 8) return json({ok:false,error:'method'},400);
    // The first argument is a compatibility placeholder, never a browser-supplied key.
    body.args[0] = null;
    const admitted = await doRequest('/admit', body), grant = await admitted.json();
    if (!admitted.ok || !grant.ok) return json(grant, admitted.status);
    const out = await forward(env, {...body, grant:grant.grant}, false);
    return json(out.payload, out.status);
  } catch(e) { return json({ok:false,error:['size','json'].includes(e.message)?e.message:'security-unavailable'},e.message==='size'?413:e.message==='json'?400:503); }
}
async function limitedBody(request){
  const reader=request.body?.getReader();if(!reader)return '';
  let n=0;const chunks=[];
  while(true){const {done,value}=await reader.read();if(done)break;n+=value.byteLength;
    if(n>64000){await reader.cancel();throw new Error('size');}chunks.push(value);}
  const out=new Uint8Array(n);let offset=0;for(const chunk of chunks){out.set(chunk,offset);offset+=chunk.byteLength;}
  return new TextDecoder().decode(out);
}
