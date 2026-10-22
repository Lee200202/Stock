const fs=require('fs'),vm=require('vm'),assert=require('assert');
const js=fs.readFileSync('public-site/gas-source/JavaScript.html','utf8');
const a=js.indexOf('  var HISTORY_BASE'),b=js.indexOf('  /** 某一天',a);
let calls=0,clock=1000000,fail=true;
const ctx={window:{fetch:true,Promise},Date:{now:()=>clock},fetch:()=>{calls++;return fail?Promise.reject(Error('timeout')):Promise.resolve({ok:true,json:()=>Promise.resolve({code:'6770',rows:[['2020-01-02',10,12,9,11,1000]]})});}};
vm.createContext(ctx);vm.runInContext(js.slice(a,b),ctx);
(async()=>{
 const p=ctx.loadHistory('6770');assert.strictEqual(p,ctx.loadHistory('6770'));await p;assert.strictEqual(calls,1);
 clock+=60001;fail=false;const h=await ctx.loadHistory('6770');assert.strictEqual(calls,2);assert.strictEqual(h.rows[0].volume,1);
 await ctx.loadHistory('6770');assert.strictEqual(calls,2);
 clock+=1200001;fail=true;const kept=await ctx.loadHistory('6770');assert.strictEqual(kept,h);assert.strictEqual(calls,3);
 console.log('歷史讀取：並發去重、失敗重試、有效記憶、單位換算、失敗保留既有資料，5項通過');
})().catch(e=>{console.error(e);process.exitCode=1});
