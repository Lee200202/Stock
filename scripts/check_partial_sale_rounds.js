const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync('public-site/gas-source/Sheetservice.gs','utf8'),ctx={};vm.createContext(ctx);vm.runInContext(src,ctx);
const buy={date:'2026/10/08',kind:'buy',reason:'當日買入',seq:1},hold={date:'2026/10/14',kind:'hold',reason:'另一部位續抱'},partial={date:'2026/10/14',kind:'sell',reason:'部分部位賣出。',seq:1};
for(const text of ['部分部位賣出','賣出部分','部分賣出，其餘续抱','持股減碼','減持']){let r=ctx.buildRounds_([buy,hold,{...partial,reason:text}],[]);assert.equal(r.length,1);assert.equal(r[0].close,null);assert.equal(r[0].partialSells.length,1);}
for(const text of ['全部賣出','全數出場','全出','清倉','出清','部分賣出後，剩餘全部賣出']){let r=ctx.buildRounds_([buy,hold,{...partial,reason:text}],[]);assert.equal(r.length,1);assert.equal(r[0].close.kind,'sell');}
assert.equal(ctx.buildRounds_([partial],[]).length,0);assert.equal(ctx.buildRounds_([hold,partial],[])[0].close,null);
let r=ctx.buildRounds_([buy,partial,{date:'2026/10/15',kind:'sell',reason:'全數賣出'}],[]);assert.equal(r.length,1);assert.equal(r[0].close.date,'2026/10/15');assert.equal(r[0].open.date,buy.date);
assert.equal(ctx.isPartialSaleRecord_({kind:'hold',reason:'部分賣出'}),false);
console.log('Partial disposal: preserve original round/entry, remaining hold accepted, full exits close, no position invented; 15 cases passed');
const sms=(reason='部分部位賣出',held=true)=>({'文章ID':'123','發文時間':'2026/10/14 09:30:04','解析明細':JSON.stringify([{code:'9999',dir:'賣出',reason},...(held?[{code:'9999',dir:'會員持股'}]:[])])});
let scopes=ctx.sourceSaleScopesFromSms_([sms()]);assert.equal(scopes['CMONEY-123|9999|2026/10/14'],true);
ctx.readSheetFields_=()=>[sms()];
let trade={'日期':'2026/10/14','代號':'9999','來源影片ID':'CMONEY-123','方向':'賣出','理由摘錄':'部分指定部位調整，並非整檔全數出場'};
assert.equal(ctx.bindSourceSaleScopes_([trade])[0]._verifiedPartialSale,true);
assert.equal(ctx.buildRounds_([buy,{...partial,reason:trade['理由摘錄'],_verifiedPartialSale:trade._verifiedPartialSale}],[])[0].close,null);
assert.equal(ctx.buildRounds_([buy,{...partial,reason:'减碼',_verifiedPartialSale:false}],[])[0].close.kind,'sell');
assert.equal(ctx.sourceSaleScopesFromSms_([sms('全數賣出',false)])['CMONEY-123|9999|2026/10/14'],false);
assert.equal(ctx.sourceSaleScopesFromSms_([sms(),sms('全數出場',false)])['CMONEY-123|9999|2026/10/14'],false);
for(const patch of [{'日期':'2026/10/13'},{'代號':'8888'},{'來源影片ID':'CMONEY-456'}])assert.equal(ctx.bindSourceSaleScopes_([{...trade,_verifiedPartialSale:undefined,...patch}])[0]._verifiedPartialSale,undefined);
console.log('Original sale scopes: source/code/date isolation; edited prose cannot change partial/full exit; 9 cases passed');
