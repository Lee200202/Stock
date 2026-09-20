// 績效歷史與逾期出場一致（2026/09/14）。
// 世紀鋼最後一次提及 8/10，到 8/24 左右才判定逾期出場；中間那段的每日績效當時以持有中記下。
// 刷新流程只從投稿日期（例如 9/10）往後重算，那段要一併重算，才會與持股追蹤的出場日一致。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
function weekdays(from,to){const out=[];for(let d=new Date(from+'T00:00:00Z');d<=new Date(to+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+1)){
  const w=d.getUTCDay();if(w&&w<6)out.push(d.toISOString().slice(0,10).replace(/-/g,'/'));}return out;}
const DAYS=weekdays('2026-08-03','2026-09-11');

function run(rounds9958,fromDate){
  const kRows=[];
  DAYS.forEach((d,i)=>{kRows.push({'代號':'9958','日期':d,'收':100+i});kRows.push({'代號':'2330','日期':d,'收':2000+i});});
  const tracker=[{'代號':'9958','股票名稱':'世紀鋼','回合JSON':JSON.stringify(rounds9958)},
                 {'代號':'2330','股票名稱':'台積電','回合JSON':JSON.stringify([{o:'2026/08/03',od:'2026/08/03',e:2000,c:'',x:null,k:'buy',s:0}])}];
  // 舊的每日績效：每一天都記成兩檔持有（逾期判定之前的樣子）
  let perf=[['日期','追蹤檔數','持有檔數','平均報酬','正報酬比例']].concat(DAYS.map(d=>[d,2,2,1,50]));
  const logs=[];
  const ctx=vm.createContext({console,JSON,Math,Date,String,Number,Object,Array,isFinite,
    Logger:{log:m=>logs.push(String(m))},CACHE:{get:()=>null,put:()=>{},remove:()=>{}},
    readSheetObjects_:n=>n==='日K快取'?kRows:n==='持股追蹤'?tracker:[],fmtDate_:s=>String(s||''),withLock_:fn=>fn(),
    getSheet_:()=>({getDataRange:()=>({getValues:()=>perf.map(r=>r.slice())}),clearContents:()=>{perf=[]},
      getRange:(r,c,n,m)=>({setValues:v=>{v.forEach((row,i)=>{perf[r-1+i]=row;});}})})});
  const src=fs.readFileSync(path.join(root,'apps-script/Cachebuilder.gs'),'utf8');
  const m=src.match(/^function rebuildPerformanceHistoryJob\(fromDate\) \{[\s\S]*?^\}/m);
  assert(m,'rebuildPerformanceHistoryJob not found');
  vm.runInContext(m[0],ctx);
  const res=ctx.rebuildPerformanceHistoryJob(fromDate);
  const byDate=Object.fromEntries(perf.slice(1).map(r=>[r[0],r]));
  return {res,logs,byDate};
}

// 逾期出場（s=1）：起點由 9/10 提前到 8/10，8/10 起世紀鋼不再計入持有
let r=run([{o:'2026/08/06',od:'2026/08/06',e:104,c:'2026/08/10',x:107,k:'buy',s:1}],'2026/09/10');
assert(r.res.ok);
assert(r.logs.some(l=>l.includes('提前到 2026/08/10')),r.logs.join('\n'));
assert.equal(r.byDate['2026/08/07'][2],2,'出場前一天仍兩檔持有（舊點保留，本來就正確）');
assert.equal(r.byDate['2026/08/10'][2],1,'出場日當天起不再計入（與賣出的算法相同）');
assert.equal(r.byDate['2026/08/20'][2],1,'逾期判定前那段的舊值被蓋掉');
assert.equal(r.byDate['2026/09/11'][2],1);

// 一般賣出（s=0）：不延伸起點，9/10 之前的點原樣保留
r=run([{o:'2026/08/06',od:'2026/08/06',e:104,c:'2026/08/10',x:107,k:'buy',s:0}],'2026/09/10');
assert(!r.logs.some(l=>l.includes('提前到')));
assert.equal(r.byDate['2026/08/20'][2],2,'不是逾期出場就不動起點之前的歷史');
assert.equal(r.byDate['2026/09/10'][2],1);

// 舊的回合JSON 沒有 s 欄：行為與先前相同
r=run([{o:'2026/08/06',od:'2026/08/06',e:104,c:'2026/08/10',x:107,k:'buy'}],'2026/09/10');
assert(!r.logs.some(l=>l.includes('提前到')));

// 全部重算（沒有起點）：照舊全部重算
r=run([{o:'2026/08/06',od:'2026/08/06',e:104,c:'2026/08/10',x:107,k:'buy',s:1}],'');
assert.equal(r.byDate['2026/08/20'][2],1);assert.equal(r.byDate['2026/08/07'][2],2);

console.log('PASS: performance history rewrites from stale-exit date, sells and legacy rounds unchanged, full rebuild intact.');
