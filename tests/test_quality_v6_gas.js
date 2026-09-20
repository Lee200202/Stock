const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..'),ctx=vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Presentationquality.gs'),'utf8'),ctx);
for(const [input,expected] of [['900以下','900以下'],['1000以上','1000以上'],['1820、2025、2135','2135'],
 ['182020252135','2135'],['2385-2405','2405'],['17,880','17880'],['12.5、13.25','13.25'],
 ['20日均線','未說明'],['1820249999','未說明'],['38X','未說明'],['-200','未說明'],['2026/09/11買1580','未說明']]) assert.equal(ctx.displayPrice_(input),expected,input);
assert.equal(ctx.displayPrice_('200','','碰到特定技術線型'),'未說明');
assert.equal(ctx.displayPrice_('200','200元','等碰線再注意'),'200');
const today='2026/09/11',daily=[{date:'2026/09/10',open:100,high:110,low:99,close:105}],
 q={date:today,open:105,high:111,low:101,last:110,time:'09-11 12:35:00',volume:100};
let result=ctx.intradayPreview_(daily,q,today);
assert.equal(result.length,2);assert.equal(result[1]._provisional,true);assert.equal(daily.length,1);
assert.equal(ctx.intradayPreview_(daily,{...q,date:'2026/09/10'},today).length,1);
assert.equal(ctx.intradayPreview_(daily,{...q,open:null},today).length,1);
assert.equal(ctx.intradayPreview_(daily,{...q,low:120},today).length,1);
assert.equal(ctx.intradayPreview_(result,q,today).length,2);
const frontend=fs.readFileSync(path.join(root,'apps-script/JavaScript.html'),'utf8');
const fn=frontend.slice(frontend.indexOf('function displayPrice_'),frontend.indexOf('  function priceCell'));
vm.runInContext(fn,ctx);assert.equal(ctx.displayPrice_('182020252135'),'2135');
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Evidencequality.gs'),'utf8'),ctx);
const chapter=ctx.recordChapter_({watch_watch:[{name:'台積電',code:'2330',price:'1820、2025、2135',reason:'等拉回'}],holdings:[{name:'台積電',code:'2330',note:'仍持有'}]},today);
assert(chapter.includes('| 2135 |'));assert(chapter.includes('會員目前持有股票'));assert(chapter.includes('仍持有'));
console.log('PASS: shared public prices, invalid technical prices, provisional OHLC, missing/stale cache, official priority, no mutation.');
