const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('public-site/gas-source/Sheetservice.gs','utf8'),ctx={};vm.createContext(ctx);vm.runInContext(src,ctx);
const buy={date:'2026/10/08',kind:'buy',reason:'當日買入',seq:1},hold={date:'2026/10/14',kind:'hold',reason:'另一部位續抱'},partial={date:'2026/10/14',kind:'sell',reason:'部分部位賣出。',seq:1};
for(const text of ['部分部位賣出','賣出部分','部分賣出，其餘续抱','持股減碼','減持']){let r=ctx.buildRounds_([buy,hold,{...partial,reason:text}],[]);assert.equal(r.length,1);assert.equal(r[0].close,null);assert.equal(r[0].partialSells.length,1);}
for(const text of ['全部賣出','全數出場','全出','清倉','出清','部分賣出後，剩餘全部賣出']){let r=ctx.buildRounds_([buy,hold,{...partial,reason:text}],[]);assert.equal(r.length,1);assert.equal(r[0].close.kind,'sell');}
assert.equal(ctx.buildRounds_([partial],[]).length,0);assert.equal(ctx.buildRounds_([hold,partial],[])[0].close,null);
let r=ctx.buildRounds_([buy,partial,{date:'2026/10/15',kind:'sell',reason:'全數賣出'}],[]);assert.equal(r.length,1);assert.equal(r[0].close.date,'2026/10/15');assert.equal(r[0].open.date,buy.date);
assert.equal(ctx.isPartialSaleRecord_({kind:'hold',reason:'部分賣出'}),false);
console.log('Partial disposal: preserve original round/entry, remaining hold accepted, full exits close, no position invented; 15 cases passed');
