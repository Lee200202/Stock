// 加權指數不可把前一交易日 Yahoo 快取冒充今日盤中報價。
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const local=path.join(__dirname,'..','apps-script','Marketservice.gs');
const source=fs.readFileSync(fs.existsSync(local)?local:path.join(__dirname,'..','public-site','gas-source','Marketservice.gs'),'utf8');
const fixed=Date.parse('2026-09-29T01:15:00Z');
class Clock extends Date {constructor(...a){super(...(a.length?a:[fixed]));}static now(){return fixed;}}
const store=new Map(),props={};let payload;
const ctx={Date:Clock,TZ:'Asia/Taipei',Logger:{log:()=>{}},isMarketHoliday_:()=>false,
  CACHE:{get:k=>store.get(k)||null,put:(k,v)=>store.set(k,v),remove:k=>store.delete(k)},
  CacheService:{getScriptCache:()=>({get:k=>store.get(k)||null,put:(k,v)=>store.set(k,v),remove:k=>store.delete(k)})},
  PropertiesService:{getScriptProperties:()=>({setProperty:(k,v)=>props[k]=v,getProperty:k=>props[k]})},
  UrlFetchApp:{fetch:()=>({getResponseCode:()=>200,getContentText:()=>JSON.stringify(payload)})},
  Utilities:{formatDate:(d,tz,f)=>{const iso=new Date(d.getTime()+8*3600000).toISOString();
    return f==='yyyy/MM/dd'?iso.slice(0,10).replace(/-/g,'/'):
      f==='HHmm'?iso.slice(11,16).replace(':',''):
      f==='HH:mm'?iso.slice(11,16):
      f==='yyyy/MM/dd HH:mm:ss'?iso.slice(0,19).replace('T',' ').replace(/-/g,'/'):'?';}},
  nowStamp_:()=> '2026/09/29 09:15:00'};
vm.createContext(ctx);vm.runInContext(source,ctx,{filename:'Marketservice.gs'});
const result=(dates,close)=>({chart:{result:[{timestamp:dates.map(s=>Date.parse(s)/1000),
  indicators:{quote:[{close,volume:close.map(()=>1000)}]},meta:{chartPreviousClose:100,regularMarketPrice:999,regularMarketTime:Date.parse('2026-09-24T05:30:00Z')/1000}}]}});
payload=result(['2026-09-24T01:00:00Z'],[105]);
assert.throws(()=>ctx.marketSampleTaiex_(),/非今日資料/);
assert(!store.has('market_live_index'));
payload=result(['2026-09-29T01:00:00Z','2026-09-29T01:05:00Z'],[102,103]);
ctx.marketSampleTaiex_();
const card=JSON.parse(store.get('market_live_index'));
assert.equal(card.value,103,'meta 的 999 是舊日價格，不可使用');
assert.match(card.time,/2026\/09\/29 09:05/);
assert.equal(card.line.length,2);
assert.equal(JSON.parse(props.marketIndexSnapshotStatus).ok,true);
assert(source.includes("if(isMarketOpen&&live&&String(live.time||'').slice(0,10)!==stamp){live=null;}"));
console.log('test_market_taiex_v78: stale response rejected, current timestamp accepted');

const marketHtml=fs.readFileSync('public-site/gas-source/Market.html','utf8');
const labelSource=marketHtml.slice(marketHtml.indexOf('  function marketSourceLabel('),marketHtml.indexOf('  // 折線圖：'));
const sourceLabel=new Function('esc',labelSource+';return marketSourceLabel;')(s=>String(s||'').replace(/</g,'&lt;').replace(/"/g,'&quot;'));
assert.match(sourceLabel({time:'2026/09/30 04:59:58',source:'台灣期交所'},'2026/10/01 08:42:51'),/is-old.*較早資料，請核對日期/);
assert.doesNotMatch(sourceLabel({time:'2026/10/01 08:40:00',source:'台灣期交所'},'2026/10/01 08:42:51'),/is-old/);
assert.match(sourceLabel({time:'2026/10/01 08:40:00',source:'Yahoo'},'2026/10/01 08:42:51'),/可能延遲/);
assert.doesNotMatch(sourceLabel({time:'未提供',source:'期交所'},'2026/10/01 08:42:51'),/is-old/);
