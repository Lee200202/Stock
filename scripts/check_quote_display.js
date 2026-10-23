const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
const src=fs.readFileSync('public-site/gas-source/JavaScript.html','utf8');
const start=src.indexOf('  var displayedQuotes ='),end=src.indexOf('  function refreshPageQuotes()',start);
function element(code,kind){return {dataset:{[kind]:code},parentNode:{querySelector:()=>null,appendChild:()=>{}},textContent:'',className:''};}
let cards=[element('2317','px')],changes=[element('2317','chg')],band=[element('2317','bandpx')];
let clock=Date.parse('2026-10-14T09:30:00+08:00');class FakeDate extends Date {constructor(...a){super(...(a.length?a:[clock]));}static now(){return clock;}}
let ctx={Date:FakeDate,Intl,Number,String,Object,isFinite,fmtPx:n=>n.toFixed(2),signCls:n=>n>0?'up':n<0?'down':'flat',formatTaipeiTime:v=>v,
 document:{querySelectorAll:s=>s==='[data-px]'?cards:s==='[data-chg]'?changes:s==='[data-bandpx]'?band:[],createElement:()=>({classList:{toggle:()=>{}}})}};
vm.createContext(ctx);vm.runInContext(src.slice(start,end),ctx);
const fresh={date:'2026/10/14',stamp:'2026/10/14 09:29:00',time:'10-14 09:29:00',last:244.5,prevClose:240,changePct:-99};
ctx.renderQuotes({'2317':fresh});assert.equal(cards[0].textContent,'244.50');assert.equal(changes[0].textContent,'▲ 1.88%');assert.match(band[0].textContent,/244.50.*1.88%/);
ctx.renderQuotes({'2317':{...fresh,stamp:'2026/10/13 13:30:00',last:240,prevClose:244.5}});assert.equal(cards[0].textContent,'244.50');
assert.equal(ctx.quoteView({...fresh,prevClose:null}).pct,null);assert.equal(ctx.quoteView({last:NaN}),null);
clock+=600000;assert.equal(ctx.quoteView(fresh).stale,true);assert.match(ctx.quoteView(fresh).label,/10\/14 成交 09:29.*待更新/);ctx.renderQuotes({});assert.equal(cards[0].textContent,'—');assert.equal(changes[0].textContent,'—');assert.equal(band[0].textContent,'—');
assert.match(ctx.quoteView({...fresh,date:'2026/10/13'}).label,/10\/13 成交 09:29/);
// 前次成交價有來源與日期，不能因平日盤中一律變成缺值；當日過期仍不冒充即時價。
const previous={...fresh,date:'2026/10/13',stamp:'2026/10/13 13:30:00',last:282.5,prevClose:291};
ctx.displayedQuotes={};ctx.renderQuotes({'2317':previous});
assert.equal(cards[0].textContent,'282.50');assert.equal(changes[0].textContent,'▼ 2.92%');
assert.match(band[0].textContent,/282.50.*2.92%/);assert.doesNotMatch(band[0].textContent,/10\/13|收盤|行情|成交/);
assert.equal(ctx.quoteView(previous).stale,true);assert.equal(ctx.quoteView(previous).previous,true);
assert.equal(ctx.quoteView({...fresh,date:'',stamp:''}).readable,false);
clock=Date.parse('2026-10-14T14:00:00+08:00');assert.equal(ctx.quoteView({...fresh,time:'10-14 13:30:00',stamp:'2026/10/14 13:30:00'}).label,'10/14 收盤');
clock=Date.parse('2026-10-14T09:30:00+08:00');const detail={detail:{classList:{contains:()=>true}},dPrice:{},dChange:{}};ctx.DETAIL={header:{}};ctx.currentCode='2317';ctx.$=id=>detail[id];ctx.renderQuotes({'2317':fresh});assert.equal(detail.dPrice.textContent,'244.50');assert.match(detail.dChange.textContent,/1.88%/);clock+=600000;ctx.renderQuotes({});assert.equal(detail.dPrice.textContent,'—');assert.match(detail.dChange.textContent,/10\/14 成交 09:29.*待更新/);
ctx.displayedQuotes={};ctx.renderQuotes({'2317':previous});assert.equal(detail.dPrice.textContent,'282.50');assert.match(detail.dChange.textContent,/10\/13 收盤/);
console.log('Quote UI: card/band share price and reference, reject older replies, unknown reference is not 0%, stale and previous-day labels passed');
