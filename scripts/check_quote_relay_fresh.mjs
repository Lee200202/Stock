import assert from 'node:assert/strict';
import worker from '../site-api/worker.mjs';
const env={GAS_WEBAPP_URL:'https://script.google.com/macros/s/test/exec',SITE_BRIDGE_TOKEN:'x'.repeat(40)};
const req=()=>new Request('https://site.example.workers.dev/api',{method:'POST',headers:{Origin:'https://lee200202.github.io'},body:JSON.stringify({method:'apiGetQuotesFor',args:[['3533']]})});
let count=0;const original=globalThis.fetch;
globalThis.fetch=async()=>{const n=++count;await new Promise(r=>setTimeout(r,10));return Response.json({ok:true,result:{'3533':{last:2000+n}}});};
try {
 const first=await Promise.all([worker.fetch(req(),env),worker.fetch(req(),env)]);assert.equal(count,1);assert.deepEqual(await first[0].json(),await first[1].json());
 const next=await worker.fetch(req(),env);assert.equal(count,2);assert.equal((await next.json()).result['3533'].last,2002);
 console.log('Quote relay: simultaneous reads share one request; next read gets a new result, no 60-second stale replay');
}finally{globalThis.fetch=original;}
