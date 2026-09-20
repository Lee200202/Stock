// GAS 端 Gemini 503 直接換鑰匙，與 pipeline.py 的 call_gemini 同一套規則（2026/09/13）。
// 在 vm 裡模擬 Apps Script：UrlFetchApp 依序回指定狀態碼，Utilities.sleep 只記錄不真的等。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const src=fs.readFileSync(path.join(root,'apps-script/Aiservice.gs'),'utf8');
const OK=JSON.stringify({candidates:[{finishReason:'STOP',content:{parts:[{text:'OK'}]}}]});
const DAILY=JSON.stringify({error:{message:'Quota exceeded for metric: free_tier_requests, limit: 250 (per day)'}});

function env(statuses,keys=['k1','k2','k3']){
  const props={};keys.forEach((k,i)=>props[i?'GEMINI_API_KEY_'+(i+1):'GEMINI_API_KEY']=k);
  const used=[],backoff=[],logs=[],seq=statuses.slice();
  const ctx=vm.createContext({console,JSON,Date,Math,String,Number,Error,
    Logger:{log:m=>logs.push(String(m))},
    Utilities:{sleep:ms=>{if(ms>=5000)backoff.push(ms);},formatDate:()=> '2026/09/13'},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k]===undefined?null:props[k],
      setProperty:(k,v)=>{props[k]=v;},deleteProperty:k=>{delete props[k];}})},
    UrlFetchApp:{fetch:(url)=>{
      used.push(decodeURIComponent(url.split('key=')[1]));
      const s=seq.shift();
      if(s===undefined) throw new Error('unexpected extra request');
      const body=s===200?OK:(s==='daily'?DAILY:'{"error":{"status":"UNAVAILABLE"}}');
      return {getResponseCode:()=>s==='daily'?429:s,getContentText:()=>body,getHeaders:()=>({})};
    }}});
  vm.runInContext(src,ctx);
  return {ctx,used,backoff,logs,props,call:(opt)=>ctx.callGemini_('system','input',opt||{})};
}

// 1. 第一次 503 就換下一把，不退避、不記為用完；同一次執行之後的呼叫沿用新的那一把
let e=env([503,200,200]);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k2']);
assert.deepEqual(e.backoff,[]);
assert.equal(e.props.geminiKeyExhausted,undefined);
assert(e.logs.some(l=>l.includes('直接改用第 2 把金鑰重試')));
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k2','k2']);

// 2. 三把都回過 503 才等第一格退避
e=env([503,503,503,200]);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k2','k3','k3']);
assert.deepEqual(e.backoff,[5000]);
assert(e.logs.some(l=>l.includes('每一把可用金鑰都回過 503')));

// 3. 服務持續過載：換鑰匙不佔退避格子，等待總量與原本相同；請求數上限為 5 格 × 3 把
e=env(Array(15).fill(503));
assert.throws(()=>e.call(),/HTTP 503/);
assert.deepEqual(e.backoff,[5000,15000,30000,60000]);
assert.equal(e.used.length,15);
assert.equal(e.props.geminiKeyExhausted,undefined);

// 4. 只有一把：維持原本同一把退避
e=env([503,200],['k1']);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k1']);assert.deepEqual(e.backoff,[5000]);

// 5. 使用者自帶金鑰不參與輪替
e=env([503,200]);
assert.equal(e.call({userKey:'user-key-000000000000'}),'OK');
assert.deepEqual(e.used,['user-key-000000000000','user-key-000000000000']);assert.deepEqual(e.backoff,[5000]);

// 6. 其他暫時性錯誤（500）照舊在同一把退避
e=env([500,200]);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k1']);assert.deepEqual(e.backoff,[5000]);

// 7. 當日額度用完照舊：記為用完、換下一把並從第一格重新開始
e=env(['daily',200]);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k2']);assert.deepEqual(e.backoff,[]);
assert.deepEqual(JSON.parse(e.props.geminiKeyExhausted).list,[0]);

// 8. 503 換到的那一把接著回當日額度用完：不會回頭用已用完的，也不會把過載的第 1 把記為用完
e=env([503,'daily',200]);
assert.equal(e.call(),'OK');
assert.deepEqual(e.used,['k1','k2','k3']);
assert.deepEqual(JSON.parse(e.props.geminiKeyExhausted).list,[1]);

// 9. 全部用完仍丟 QUOTA_DAILY
e=env(['daily','daily','daily']);
assert.throws(()=>e.call(),/QUOTA_DAILY/);

console.log('PASS: GAS Gemini 503 key switch, shared backoff, single/user key unchanged, daily quota rotation intact.');
