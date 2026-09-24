// 持股追蹤回合規則（2026/09/14）：實際跑 rebuildHoldingsTrackerJob，核對狀態、出場日與出場價。
//   一、祥碩：會員持股卻不在「目前持有」。
//   二、逾 10 個交易日未再提及的自動出場：出場日＝「每天講了什麼」最後一次提及日（當日或之前的交易日）。
//   v58（2026/09/24）起出場價取那一天的「當日最高」、進場價取進場日「當日最低」；明講價另記在「進場明講／出場明講」。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');

// 交易日曆：2026/07/01 起的平日（不含 7/10 以外的假日細節，足夠測試用）
function weekdays(from,to){const out=[];for(let d=new Date(from+'T00:00:00Z');d<=new Date(to+'T00:00:00Z');d.setUTCDate(d.getUTCDate()+1)){
  const w=d.getUTCDay();if(w&&w<6)out.push(d.toISOString().slice(0,10).replace(/-/g,'/'));}return out;}
const DAYS=weekdays('2026-07-01','2026-09-11');
function candles(base){return DAYS.map((d,i)=>({date:d,open:base+i,high:base+i+2,low:base+i-2,close:base+i,volume:1000}));}
const closeOn=(base,d)=>base+DAYS.indexOf(d);
const lowOn=(base,d)=>closeOn(base,d)-2, highOn=(base,d)=>closeOn(base,d)+2;

function run(trades,holds,opts){
  // 重算在「操作紀錄是空的」時會直接結束。正式資料不會是空的，補一筆不相干的列，
  // 讓只有會員持股的情境也真的跑完整個判定流程（否則斷言會因為提早結束而假通過）。
  trades=trades.concat([T('2026/09/01','台泥','1101','觀望注意','填充列')]);
  const written={rows:null};
  const LONG=weekdays('2025-09-01','2026-09-11').map((d,i)=>({date:d,open:62,high:62+((i%9)-4)+1,low:62+((i%9)-4)-1,close:62+((i%9)-4),volume:1000}));
  const K={'5269':candles(1300),'9958':candles(80),'2330':candles(2000),'3260':candles(368),
    '6666':candles(500).filter(k=>k.date>='2026/07/27'),     // 快取從 7/27 才有收盤
    '2354':LONG};                                             // 快取涵蓋一整年，價位 57～67
  const sheets={'操作紀錄':trades,'會員持股':holds,'日K快取':DAYS.map(d=>({'代號':'0050','日期':d,'收':100})).concat((opts||{}).calendarExtra||[])};
  const logs=[];
  const ctx=vm.createContext({console,JSON,Math,Date,String,Number,Object,Array,
    Logger:{log:m=>logs.push(String(m))},CACHE:{get:()=>null,put:()=>{},remove:()=>{}},
    LockService:{getScriptLock:()=>({waitLock:()=>{},tryLock:()=>true,releaseLock:()=>{}})},
    Utilities:{formatDate:()=> '2026/09/14',sleep:()=>{}},
    PropertiesService:{getScriptProperties:()=>({getProperty:()=>null,setProperty:()=>{}})}});
  vm.runInContext(fs.readFileSync(path.join(root,'apps-script/SheetService.gs'),'utf8'),ctx);
  Object.assign(ctx,{
    readSheetObjects_:n=>(sheets[n]||[]).map(r=>Object.assign({},r)),
    fillMissingDailyK_:()=>({checked:0}),getCachedDailyK:c=>K[c]||[],getQuoteCache:()=>({}),
    naturalReason_:s=>String(s||''),todayStr_:()=> '2026/09/14',
    getSheet_:()=>({clearContents:()=>{},getRange:()=>({setValues:v=>{if(v.length>1||v[0][0]!=='代號')written.rows=v;}})}),
    withLock_:fn=>fn()});
  const res=ctx.rebuildHoldingsTrackerJob(opts);
  const head=['代號','股票名稱','首次買入日','進場價','進場價來源','最近賣出日','出場價','狀態','提及次數','首次理由','逐日說明','更新時間',
    '回合數','本回合進場日','出場原因','回合明細','參考價位','參考價位來源','首次進場方式','本回合進場方式','累積報酬','各回合報酬','回合JSON',
    '最新說明日期','進場明講','出場明講'];
  const rows=(written.rows||[]).map(r=>Object.fromEntries(head.map((h,i)=>[h,r[i]])));
  return {rows,logs,res,ctx,byCode:c=>rows.find(r=>r['代號']===c)};
}
const T=(d,name,code,dir,reason,src='MANUAL-x')=>({'日期':d,'股票名稱':name,'代號':code,'方向':dir,'價位說明':'未說明','理由摘錄':reason,'來源影片ID':src,'序':1});
const H=(d,name,code,note)=>({'日期':d,'股票名稱':name,'代號':code,'目前立場':'續抱','說明重點':note,'來源影片ID':'MANUAL-x'});

// ---- 一、祥碩 ----
// (a) 同一天既明講會員持有，又對還沒有的人列觀望不碰：不能讓持有聲明作廢
let r=run([T('2026/09/11','祥碩','5269','觀望不碰','還沒有的人不要急著買')],
          [H('2026/09/10','祥碩','5269','會員買了兩個多月'),H('2026/09/11','祥碩','5269','成本1355續抱')]);
assert.equal(r.byCode('5269')['狀態'],'持有中','(a) 同日持股＋觀望不碰，仍應持有中');

// (b) 同一天只有持股聲明與觀望不碰（首次出現）：持有聲明照樣開倉
r=run([T('2026/09/08','祥碩','5269','觀望不碰','高價股不能承受就不要玩')],
      [H('2026/09/08','祥碩','5269','會員持有'),H('2026/09/10','祥碩','5269','會員買了兩個多月')]);
assert(r.byCode('5269'),'(b) 首次持股聲明當天另有觀望不碰，不應整檔消失');
assert.equal(r.byCode('5269')['狀態'],'持有中');

// (c) 較早一筆孤立的持股聲明沒通過三日確認，不能把之後確認過的持股一起刪掉
r=run([],[H('2026/07/10','祥碩','5269','會員持有'),H('2026/09/08','祥碩','5269','會員持有'),
          H('2026/09/10','祥碩','5269','會員買了兩個多月'),H('2026/09/11','祥碩','5269','成本1355')]);
assert(r.byCode('5269'),'(c) 早期未確認的聲明不能讓後來的持股整檔消失');
assert.equal(r.byCode('5269')['狀態'],'持有中');
assert.equal(r.byCode('5269')['本回合進場日'],'2026/09/08');

// ---- 二、逾期自動出場的計價 ----
// 世紀鋼：8/06 買入、8/07 觀望注意、8/10 會員持股，之後沒有再提及（到 9/11 已逾 10 個交易日）
r=run([T('2026/08/06','世紀鋼','9958','買入','穩穩賺錢的投資人買入'),T('2026/08/07','世紀鋼','9958','觀望注意','底部爆巨量')],
      [H('2026/08/06','世紀鋼','9958','續抱'),H('2026/08/10','世紀鋼','9958','會員都賺錢')]);
let row=r.byCode('9958');
assert.equal(row['狀態'],'已出場');
assert.equal(row['最近賣出日'],'2026/08/10','出場日＝最後一次提及日');
assert.equal(Number(row['出場價']),highOn(80,'2026/08/10'),'出場價＝8/10 當日最高');
let json=JSON.parse(row['回合JSON']);
assert.equal(json[json.length-1].c,'2026/08/10');assert.equal(json[json.length-1].x,highOn(80,'2026/08/10'));
assert(String(row['進場價來源']).includes('取 2026/08/10 當日最高'),'來源欄寫明取哪一天');
assert.equal(Number(row['進場價']),lowOn(80,'2026/08/06'),'進場價＝8/06 當日最低');

// 最後一次提及落在非交易日（週六的會員簡訊）：取當天之前最近交易日收盤，不能拿之後的價格
r=run([T('2026/08/06','世紀鋼','9958','買入','買入'),T('2026/08/08','世紀鋼','9958','觀望注意','週六簡訊提到','CMONEY-1')],
      [H('2026/08/06','世紀鋼','9958','續抱'),H('2026/08/07','世紀鋼','9958','續抱')]);
row=r.byCode('9958');
assert.equal(row['最近賣出日'],'2026/08/08');
assert.equal(Number(row['出場價']),highOn(80,'2026/08/07'),'週六提及 → 取 8/07 當日最高，而不是 8/10');

// ---- 既有規則不變 ----
// 觀望不碰之後沒有下文（當天也沒有持股聲明）：照舊視為出場，v58 起取當日最高
r=run([T('2026/08/03','台積電','2330','買入','買入'),T('2026/08/12','台積電','2330','觀望不碰','不要碰')],
      [H('2026/08/04','台積電','2330','續抱'),H('2026/08/05','台積電','2330','續抱')]);
row=r.byCode('2330');
assert.equal(row['狀態'],'已出場');assert.equal(row['出場原因'],'轉為觀望不碰，視為出場');
assert.equal(Number(row['出場價']),highOn(2000,'2026/08/12'));
// 只出現一次、之後三個交易日都沒再提的持股聲明，照舊移除
r=run([],[H('2026/07/10','祥碩','5269','會員持有')]);
assert.equal(r.byCode('5269'),undefined);
assert(r.byCode('1101')===undefined && r.rows!==null,'確認重算真的跑完（填充列只有觀望注意，不列入）');
assert.equal(r.res.holdDropped,1,'孤立聲明是被確認規則移除，不是提早結束');

// ---- 診斷函式：只讀不寫，印出判定過程 ----
r=run([T('2026/09/11','祥碩','5269','觀望不碰','還沒有的人不要急著買')],
      [H('2026/07/10','祥碩','5269','會員持有'),H('2026/09/08','祥碩','5269','會員持有'),H('2026/09/10','祥碩','5269','續抱'),
       H('2026/09/11','祥碩','5269','成本1355續抱')],
      {dryRun:true,explain:'5269'});
assert.equal(r.rows.length,0,'乾跑不寫持股追蹤');
const trace=r.res.trace.join('\n');
assert(trace.includes('移除未確認的持有聲明 2026/07/10'),trace);
assert(trace.includes('2026/09/08 會員持股 → 持有中'),trace);
assert(trace.includes('不計出場的觀望不碰：2026/09/11'),trace);
assert(trace.includes('結果：持有中'),trace);
// 只有觀望不碰、當天與之後都沒有持股聲明：照規則出場，診斷要講得出原因
r=run([T('2026/09/11','祥碩','5269','觀望不碰','賠很多')],
      [H('2026/09/08','祥碩','5269','會員持有'),H('2026/09/10','祥碩','5269','續抱')],{dryRun:true,explain:'祥碩'});
assert(r.res.trace.join('\n').includes('結果：已出場'));assert(r.res.trace.join('\n').includes('轉為觀望不碰，視為出場'));

// ---- 三、進場定價（2026/09/14 日誌）----
const TP=(d,name,code,dir,price,reason)=>Object.assign(T(d,name,code,dir,reason),{'價位說明':price});
// 威剛：7/09 明講會員持有，8/06 才提到成本。持有聲明當天部位已在手上，成本不能往後接到 8/06
const cost=String(closeOn(368,'2026/08/06'));
r=run([TP('2026/08/06','威剛','3260','觀望注意',cost,'會員成本')],
      [H('2026/07/09','威剛','3260','會員持有'),H('2026/07/10','威剛','3260','續抱')]);
row=r.byCode('3260');json=JSON.parse(row['回合JSON']);
assert.equal(json[0].od,'2026/07/09','持有聲明開倉的進場日不能被往後接');
assert.equal(Number(row['進場價']),lowOn(368,'2026/07/09'),'v58：持有聲明當日最低，不用後來講的成本');
assert.equal(row['進場明講'],'明講成本 '+cost+'（查無成交紀錄）');
assert(String(row['進場價來源']).includes('2026/08/06 提到成本 '+cost),row['進場價來源']);
assert(String(row['進場價來源']).includes('未經驗證'),row['進場價來源']);

// 祥碩：持有聲明隔天提到的成本落在聲明當日區間 → 來源欄照實寫，不寫成「明講在 X 買入」
const c5269=String(closeOn(1300,'2026/07/08'));
r=run([TP('2026/07/09','祥碩','5269','觀望注意',c5269,'成本')],
      [H('2026/07/08','祥碩','5269','會員持有'),H('2026/07/10','祥碩','5269','續抱')]);
row=r.byCode('5269');
assert(String(row['進場價來源']).includes('首次明講會員持有；2026/07/09 提到成本 '+c5269+'，落在持有聲明當日的成交區間'),row['進場價來源']);
assert.equal(Number(row['進場價']),lowOn(1300,'2026/07/08'));assert.equal(row['進場明講'],'明講成本 '+c5269);
assert(!String(row['進場價來源']).includes('張震明講在'));

// 日K快取最早 7/27：7/20 的持有聲明不能被標成「當日收盤」，要照實標示並用最早可用的那一天
r=run([],[H('2026/07/20','某股','6666','會員持有'),H('2026/07/21','某股','6666','續抱')]);
row=r.byCode('6666');json=JSON.parse(row['回合JSON']);
assert(String(row['進場價來源']).includes('日K快取最早只到 2026/07/27，2026/07/20 無K線'),row['進場價來源']);
assert.equal(json[0].od,'2026/07/27');
assert(String(row['回合明細']).includes('（日K最早 2026/07/27）')||Number(row['回合數'])===1);

// 鴻準：快取涵蓋一整年、73 從未成交 → 不當成交價；v58 起取當日最低，附註標「期間未成交」
r=run([TP('2026/08/05','鴻準','2354','買入','73','買入')],[H('2026/08/06','鴻準','2354','續抱')]);
row=r.byCode('2354');
const LONGK=weekdays('2025-09-01','2026-09-11');const close0805=62+((LONGK.indexOf('2026/08/05')%9)-4);
assert.equal(Number(row['進場價']),close0805-1,'一年內從未成交的明講價不採用，取當日最低');
assert.equal(row['進場明講'],'明講 73 買進（期間未成交）');
assert(String(row['進場價來源']).includes('未曾成交'),row['進場價來源']);
// 快取很短（7/27 起）時查無成交，仍可能是快取之前的成本 → 維持採用明講價並標未驗證
// （650 在歷史區間 1.3 倍以內但從未成交；超出區間的數字另由「不是股價」規則處理）
r=run([TP('2026/08/05','某股','6666','買入','650','買入')],[H('2026/08/06','某股','6666','續抱')]);
row=r.byCode('6666');
assert.equal(Number(row['進場價']),lowOn(500,'2026/08/05'),'v58：明講價只作附註');
assert.equal(row['進場明講'],'明講 650 買進（查無成交紀錄）');

console.log('PASS: same-day holding vs avoid, unconfirmed early holding no longer deletes later holdings, stale exit on last-mention day (on or before) at that day high, entry at day low, stated prices as notes.');

// 無效收盤不可拿來當最後提及日的出場價；未來日期不得提早觸發逾期。
assert.equal(r.ctx.closeOnOrBefore_([{date:'2026/08/07',close:100},{date:'2026/08/10',close:-1}], '2026/08/10').price,100);
r=run([T('2026/09/11','世紀鋼','9958','買入','今天買入')],[],{calendarExtra:weekdays('2026-09-15','2026-10-15').map(d=>({'日期':d,'收':100}))});
assert.equal(r.byCode('9958')['狀態'],'持有中');
