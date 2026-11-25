const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const code=fs.readFileSync('public-site/gas-source/JavaScript.html','utf8');
const start=code.indexOf('  function trendSvg('),end=code.indexOf('  function rulerSvg(',start);
const ctx={readFontScale_:()=>1.25,pxShort:String,esc:String,md_:s=>s.slice(5),fmtRet:String,
 signCls:n=>n>=0?'up':'down',niceTicks:()=>[2500,3000],spreadLabels:items=>items.map(i=>({...i,ly:i.y}))};
vm.createContext(ctx);vm.runInContext(code.slice(start,end),ctx);
const rows=Array.from({length:20},(_,i)=>({date:'2026/08/'+String(i+1).padStart(2,'0'),open:2600,high:3100,low:2500,close:2600+i*20}));
for(const width of [240,265,320,700]){
 const r=ctx.trendSvg({trade:'2026/08/03',entry:2620,avgCost:2650,stated:2700,end:2980,held:true,ret:12},rows,width);
 assert(r.pw>0);assert(!r.svg.includes('NaN'));assert(r.svg.includes('平均成本 2650'));
 const labels=[...r.svg.matchAll(/<text class="tr-lb [^"]*" x="([\d.]+)" y="([\d.]+)"/g)];
 assert.equal(labels.length,3);assert(labels.every(m=>+m[1]<width));
 const height=+r.svg.match(/height="([\d.]+)"/)[1];assert(labels.every(m=>+m[2]<height));
 if(width<440*1.25)assert(labels.every(m=>+m[1]===8));
}
console.log('Trend chart: 240/265/320/700px, positive plot width and readable labels inside SVG pass');
