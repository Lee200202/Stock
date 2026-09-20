// diagnoseCmoneyToday：只讀診斷，從來源、輪詢、存檔到解析派工給出結論（2026/09/14 會員簡訊沒進來）。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..');
function run(opts){
  const props=Object.assign({CMONEY_MEMBER_ID:'1230411'},opts.props||{}), writes=[];
  const ctx=vm.createContext({console,JSON,Math,Date,String,Number,Object,Array,RegExp,isNaN,
    Logger:{log:()=>{}},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>k in props?props[k]:null,setProperty:(k,v)=>writes.push(['set',k]),deleteProperty:k=>writes.push(['del',k])})},
    ScriptApp:{getProjectTriggers:()=>(opts.triggers||['cmoneyPollJob']).map(h=>({getHandlerFunction:()=>h}))},
    Utilities:{formatDate:(d,tz,f)=>{const x=new Date(new Date(d).getTime()+8*3600e3),p=n=>String(n).padStart(2,'0');
      const m={yyyy:x.getUTCFullYear(),MM:p(x.getUTCMonth()+1),dd:p(x.getUTCDate()),HH:p(x.getUTCHours()),mm:p(x.getUTCMinutes()),ss:p(x.getUTCSeconds()),u:x.getUTCDay()||7};
      return f.replace(/yyyy|MM|dd|HH|mm|ss|u/g,k=>m[k]);}},
    CacheService:{getScriptCache:()=>({get:()=>null,put:()=>{}})}});
  vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Cmoney.gs'),'utf8'),ctx);
  Object.assign(ctx,{todayStr_:()=>'2026/09/14',TZ:'Asia/Taipei',fmtDate_:v=>String(v||'').slice(0,10),
    cmGetGuestToken_:()=>'t',cmFetchApiPage_:()=>opts.api||[],
    readSheetObjects_:n=>n==='會員簡訊'?(opts.rows||[]):(opts.status||[]),
    getSheet_:()=>{throw new Error('診斷不可寫入');}});
  const lines=ctx.diagnoseCmoneyToday(opts.id);
  assert.deepEqual(writes.filter(w=>w[0]==='set'),[],'診斷不可改任何屬性');
  return lines.join('\n');
}
const api=[{id:'184582595',content:{creatorId:'1230411',text:'張震-1:目前世芯超跌，抱牢',title:''},createTime:Date.parse('2026-09-14T03:28:16Z')}];
// 來源有、分頁沒有 → 補抓
let out=run({api});
assert(/184582595/.test(out)&&/沒有存進會員簡訊/.test(out)&&/依編號補抓/.test(out),out);
// 已存、待解析、工單卡在處理中 → 看 GitHub 是否被取消、重新解析
out=run({api,rows:[{'文章ID':'184582595','解析狀態':'待解析','抓取時間':'2026/09/14 11:29'}],
  props:{smsParseState:JSON.stringify({jobId:'SMS-1',status:'處理中',execution:'github',step:'準備',updatedAt:'2026/09/14 11:29:00'})}});
assert(/卡在解析/.test(out)&&/Cancelled/.test(out)&&/處理中/.test(out),out);
// 沒有觸發器要講出來
out=run({api,triggers:[]});
assert(/installTriggers/.test(out),out);
// 11:28 之後的文章全都沒看過（早上的看過）→ 指出每一棒從那時起中斷
const api2=[{id:'184578241',content:{creatorId:'1230411',text:'楊少凱贏家1 大家早安',title:''},createTime:Date.parse('2026-09-14T01:42:42Z')},
  {id:'184582595',content:{creatorId:'1230411',text:'張震-1:目前世芯超跌，抱牢',title:''},createTime:Date.parse('2026-09-14T03:28:16Z')},
  {id:'184583191',content:{creatorId:'1230411',text:'[啟發郭憲政-普]1 93元低佈多',title:''},createTime:Date.parse('2026-09-14T03:44:06Z')}];
out=run({api:api2,props:{cmSeenIds:JSON.stringify(['184578241'])}});
assert(/11:28:16 之後的文章輪詢全都沒看過/.test(out)&&/執行項目/.test(out),out);
assert(/09:42:42　184578241　看過/.test(out)&&/11:44:06　184583191　沒看過/.test(out),out);
console.log('PASS: CMoney diagnosis is read-only and points to fetch, save or parse-dispatch stage.');
