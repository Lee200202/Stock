const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
const source=fs.readFileSync('public-site/gas-source/JavaScript.html','utf8');
const fragment=source.slice(source.indexOf('  var quoteQueue ='),source.indexOf('  function refreshPageQuotes()'));
let jobs=[],calls=[],success,failure;
const ctx={document:{hidden:false},displayedQuotes:{},pageQuotePending:false,isCode:c=>/^\d{4}$/.test(c),quoteView:q=>q?{stale:q.stale}:null,
 setTimeout:f=>{jobs.push(f);return jobs.length;},renderQuotes:q=>Object.assign(ctx.displayedQuotes,q)};
ctx.google={script:{run:{withSuccessHandler:f=>{success=f;return {withFailureHandler:g=>{failure=g;return {apiGetQuotesFor:codes=>calls.push(codes)}}}}}}};
vm.createContext(ctx);vm.runInContext(fragment,ctx);
const codes=Array.from({length:12},(_,i)=>String(2300+i));
ctx.queuePageQuotes(codes.slice(0,6),false);ctx.queuePageQuotes(codes.slice(4),false);assert.equal(jobs.length,1);
for(let n=0;n<3;n++){jobs.shift()();assert.equal(calls[n].length,12-n*4);let q={};calls[n].slice(0,4).forEach(c=>q[c]={stale:false});success(q);}
jobs.shift()();assert.equal(calls.length,3);assert.equal(Object.keys(ctx.displayedQuotes).length,12);
ctx.queuePageQuotes(['9999'],false);for(let n=0;n<4;n++){jobs.shift()();success({});}jobs.shift()();assert.equal(calls.length,7);assert.equal(jobs.length,0);
ctx.queuePageQuotes(['9999'],true);jobs.shift()();failure();assert.equal(jobs.length,0);
ctx.document.hidden=true;ctx.queuePageQuotes(['8888'],true);assert.equal(jobs.length,0);
console.log('Quote queue: coalesced callers, progressive 4/8/12 completion, only unresolved codes, four-attempt bound, failure/hidden stop passed');
