/* 離線契約檢查，不使用網路、不寫試算表、不呼叫模型或郵件。 */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('public-site/gas-source/MailService.gs', 'utf8').replace(/\r\n/g,'\n');
const start = source.indexOf('function spokenCodeFor_(');
const end = source.indexOf('\n/**\n * 品質關卡的收尾保險', start);
assert(start > 0 && end > start);
function run({name='甲公司',raw='甲公司回測季線，仍應續抱。',ruled=[],fail=false,reason='甲公司維持續抱。',wrong=false}={}) {
  let calls=0; const writes=[];
  const ctx = {
    fmtDate_: x=>String(x),todayStr_:()=> '2026/10/16',
    digestAuditVerdict_:()=>({missing:[{name,where:'持股',evidence:raw}]}),
    readSheetObjects_: name=> {
      if(name==='分類裁決') {if(fail)throw Error('unavailable');return ruled;}
      if(name==='影片清單') return [{'發布日期':'2026/10/16','影片ID':'v1',raw}];
      return [];
    },rawTranscript_:r=>r.raw,loadCodeMap_:()=>({byCode:{3037:{name:'欣興'}}}),
    reviewSnips_:()=>raw,validEvidence_:()=>true,PIPE_EXTRACT_SYSTEM:'',
    callGemini_:()=>{calls++;return JSON.stringify({results:[{index:1,is_stock:true,should_publish:true,
      confident:true,direction:'會員持股',reason:wrong?'乙公司維持續抱。':reason,evidence:[raw],when:'today'}]});},
    withLock_:fn=>fn(),Logger:{log:()=>{}},CACHE:{remove:()=>{}},AUDIT_SKIP_LABEL:{},
    getSheet_:name=>({getLastRow:()=>1,getRange:()=>({setValues:rows=>writes.push({name,rows})})}),
    proposeAuditFixes:()=>{throw Error('Unexpected fallback model');},
  };
  vm.createContext(ctx); vm.runInContext(source.slice(start,end),ctx);
  const result=ctx.autoFixAuditMissing('2026/10/16');
  return {result,calls,writes};
}
let r=run({name:'聯詠',ruled:[{'日期':'2026/10/16','代號':'3034','名稱':'聯詠','分類':'排除'}]});
assert.equal(r.calls,0);assert.equal(r.writes.length,0);
r=run({name:'新興',raw:'3037的新興昨天跌停，只作教學。',ruled:[{'日期':'2026/10/16','代號':'3037','名稱':'欣興','分類':'排除'}]});
assert.equal(r.calls,0);assert.equal(r.writes.length,0);
r=run({raw:''});assert.equal(r.calls,0);assert.equal(r.result.pending,1);
r=run({fail:true});assert.equal(r.calls,0);assert.equal(r.result.pending,1);
const long='甲公司回測向上的季線後維持持有。'+ '後續觀察量能與價格能否持續配合，尚未確認轉弱前不因短線震盪調節。'.repeat(2);
r=run({reason:long});assert.equal(r.calls,1);assert.equal(r.writes[0].rows[0][4],long);
r=run({wrong:true});assert.equal(r.writes.length,0);assert.equal(r.result.skipped,1);
console.log('6 gate contracts passed: ruled names/codes, unreadable source/decision, full sentences, own-stock note.');

// 後台原稿重判只能派出既有 main 預覽，不另建理由文字裁決器。
const adminSource=fs.readFileSync('public-site/gas-source/Adminservice.gs','utf8').replace(/\r\n/g,'\n');
const adminStart=adminSource.indexOf('function apiAdminReclassify(');
const adminEnd=adminSource.indexOf('/* ==================================================================== *',adminStart);
assert(adminStart>0 && adminEnd>adminStart);
function adminPreview(opts={}) {
  const sent=[],urls=[];let released=0;
  const ctx={
    adminAuth_:()=>{if(opts.authFail)throw Error('auth');},
    fmtDate_:x=>x||'',todayStr_:()=> '2026/11/02',
    LockService:{getScriptLock:()=>({tryLock:()=>!opts.busy,releaseLock:()=>released++})},
    dayVideoRow_:()=>{if(opts.readFail)throw Error('read failed');return opts.missing?null:{'原始逐字稿內容':opts.polishedOnly?'':'甲公司目前仍要等。','修飾後逐字稿內容':'修飾稿'};},
    githubCfg_:()=>({repo:'Lee200202/Stock',token:'offline',ref:opts.ref||'main'}),githubHeaders_:()=>({}),
    UrlFetchApp:{fetch:u=>{urls.push(u);return {getResponseCode:()=>opts.http||200,getContentText:()=>JSON.stringify({workflow_runs:opts.runs||[]})};}},
    dispatchGithub_:(inputs,wf,marker)=>{sent.push({inputs,wf,marker});return opts.dispatchFail?{ok:false,reason:'dispatch failed'}:{ok:true,runUrl:'https://github.com/Lee200202/Stock/actions/runs/123'};},
    getSheet_:()=>{throw Error('preview must not write rows');},callGemini_:()=>{throw Error('no second model flow');}
  };
  vm.createContext(ctx);vm.runInContext(adminSource.slice(adminStart,adminEnd),ctx);
  const result=ctx.apiAdminReclassify('offline',opts.day===undefined?'2026/11/02':opts.day);
  return {result,sent,urls,released};
}
let adminCases=0;
function adminCheck(name,fn){fn();adminCases++;}
for(const opts of [{day:''},{day:'2026/09/01'},{day:'2026/11/03'},{authFail:true},{busy:true},{missing:true},{polishedOnly:true},{readFail:true},{ref:'old-branch'},{http:403}]) {
  adminCheck(JSON.stringify(opts),()=>{const x=adminPreview(opts);assert.equal(x.result.ok,false);assert.equal(x.sent.length,0);});
}
for(const status of ['queued','in_progress','waiting','pending','requested']) {
  adminCheck(status,()=>{const x=adminPreview({runs:[{status}]});assert.equal(x.result.ok,false);assert.equal(x.sent.length,0);assert.equal(x.released,1);});
}
adminCheck('canonical preview only',()=>{
 const x=adminPreview();assert.equal(x.result.ok,true);assert.equal(x.result.previewOnly,true);assert.equal(x.sent.length,1);assert.equal(x.released,1);
 assert.equal(x.sent[0].wf,'regenerate-verified.yml');assert.equal(x.sent[0].marker,'2026/11/02');
 assert.equal(JSON.stringify(x.sent[0].inputs),JSON.stringify({date:'2026/11/02',mode:'preview'}));
});
adminCheck('dispatch failure is preserved',()=>{const x=adminPreview({dispatchFail:true});assert.equal(x.result.ok,false);assert.equal(x.released,1);});
adminCheck('workflow-specific result lookup',()=>{
 const a=adminSource.indexOf('function latestRunInfo_('),b=adminSource.indexOf('function latestRunUrl_(',a);const urls=[];
 const ctx={githubCfg_:()=>({repo:'Lee200202/Stock',token:'offline',workflow:'daily.yml'}),UrlFetchApp:{fetch:u=>{urls.push(u);return {getResponseCode:()=>200,getContentText:()=>'{"workflow_runs":[]}'};}}};
 vm.createContext(ctx);vm.runInContext(adminSource.slice(a,b),ctx);
 ctx.latestRunInfo_('', 'regenerate-verified.yml');ctx.latestRunInfo_();
 assert(urls[0].includes('/regenerate-verified.yml/runs?'));assert(urls[1].includes('/daily.yml/runs?'));
});
assert(!adminSource.includes('var PIPE_RECLASSIFY_SYSTEM ='));
const adminHtml=fs.readFileSync('public-site/gas-source/Admin.html','utf8');
assert(!adminHtml.includes('id="reclsAll"'));assert(!adminHtml.includes('觀望注意（看好、留意、等回檔）'));

for(const capability of [undefined,0,1]) {
 adminCheck('frontend deployment capability '+capability,()=>{
  let handler=null,dispatched=0;const nodes={reclsBtn:{addEventListener:(event,fn)=>handler=fn},fixMsg:{}};
  const ctx={$:id=>nodes[id],fixDate:()=> '2026/11/02',KEY:'offline',show:()=>{},document:{}};
  ctx.google={script:{}};Object.defineProperty(ctx.google.script,'run',{get(){let success=()=>{};const chain={withSuccessHandler:fn=>{success=fn;return chain;},withFailureHandler:()=>chain,
    apiAdminFixState:()=>success({ok:true,sourceClassificationPreview:capability}),apiAdminReclassify:()=>{dispatched++;success({ok:false,reason:'offline dispatch stopped'});}};return chain;}});
  const a=adminHtml.indexOf("$('reclsBtn').addEventListener('click', function(){"),b=adminHtml.indexOf('\n});',a)+4;
  vm.createContext(ctx);vm.runInContext(adminHtml.slice(a,b),ctx);handler();
  assert.equal(dispatched,capability===1?1:0);assert.equal(nodes.reclsBtn.disabled,false);
 });
}
console.log(adminCases+' admin source-preview contracts passed; no model, sheet write, email or LINE calls.');
