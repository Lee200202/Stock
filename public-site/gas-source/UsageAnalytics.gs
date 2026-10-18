/** v190 匿名使用分析。累計快照取最大值，不把重送秒數相加。 */
var USAGE_HEADERS_ = ['時間','訪客識別','頁面','動作','對象','停留秒數','裝置','彙整識別','瀏覽批次','更新時間','來源網域','閱讀深度','快照次數','入口頁','瀏覽次數','互動次數','來源版本','時段秒數'];
function usageText190_(v,n){var s=String(v||'').slice(0,n);return /^[=+@-]/.test(s)?"'"+s:s;}
function usageHash190_(v){return Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,String(v),Utilities.Charset.UTF_8).map(function(b){return('0'+((b+256)%256).toString(16)).slice(-2);}).join('').slice(0,24);}
function usageMillis190_(s){if(s instanceof Date)return s.getTime();return Date.parse(String(s||'').replace(/\//g,'-').replace(' ','T')+(String(s||'').match(/Z|[+-]\d\d:\d\d$/)?'':'+08:00'));}
function usageWrite190_(who,events){
 var id=String(who||'').slice(0,40).replace(/[^\w-]/g,'');if(!id)return{ok:false,reason:'no id'};
 if(!Array.isArray(events)||!events.length)return{ok:true,n:0,added:0,updated:0};
 var lock=LockService.getScriptLock();if(!lock.tryLock(1000))return{ok:false,reason:'busy'};
 try{
  var sh=getSheet_(USAGE_SHEET),now=nowStamp_(),today=todayStr_(),nowMs=Date.now(),cache=CacheService.getScriptCache(),added=[],pending={},updated=0,ignored=0,stays=[],legacy=null;
  if(sh.getRange(1,8).getValue()!==USAGE_HEADERS_[7]){sh.getRange(1,8,1,11).setValues([USAGE_HEADERS_.slice(7)]);sh.hideColumns(8,11);}
  function rowAt(n){return n>=2&&n<=sh.getLastRow()?sh.getRange(n,1,1,18).getValues()[0]:null;}
  function find(key){var ck='usage190:'+usageHash190_(key),n=Number(cache.get(ck)||0),r=rowAt(n);if(r&&r[7]===key)return{n:n,row:r,ck:ck};
   if(sh.getLastRow()>1){var hit=sh.getRange(2,8,sh.getLastRow()-1,1).createTextFinder(key).matchEntireCell(true).useRegularExpression(false).findNext();if(hit){n=hit.getRow();cache.put(ck,String(n),21600);return{n:n,row:rowAt(n),ck:ck};}}
   return{n:0,row:null,ck:ck};}
  events.slice(0,USAGE_MAX_BATCH).forEach(function(e){if(!e||typeof e!=='object'||e.action==='進入分頁')return;
   var page=usageText190_(e.page,40),action=usageText190_(e.action,40),target=usageText190_(e.target,80),device=usageText190_(e.device,20),seconds=Math.max(0,Math.min(86400,Number(e.seconds)||0));if(!page||!action)return;
   var v2=e.v===2&&/^[a-zA-Z0-9_-]{8,64}$/.test(String(e.session||'')),day=String(e.day||'').replace(/\//g,'-');
   if(!/^\d{4}-\d{2}-\d{2}$/.test(day)||Math.abs(nowMs-Date.parse(day+'T12:00:00+08:00'))>172800000)day=today.replace(/\//g,'-');
   var key=v2?'u190|'+JSON.stringify([id,e.session,day,page,action,target,String(e.visit||e.session)]):'',existing=null;
   if(key&&pending[key])existing={n:0,row:pending[key]};else if(key)existing=find(key);
   // Old open tabs have no session ID. Only coalesce long snapshots with the
   // same inferred start (±30s); never sum their cumulative seconds.
   if(!v2&&action==='停留'&&seconds>=60){
    if(!legacy){var count=Math.min(200,Math.max(0,sh.getLastRow()-1));legacy=count?sh.getRange(sh.getLastRow()-count+1,1,count,18).getValues().map(function(r,i){return{n:sh.getLastRow()-count+1+i,row:r};}):[];}
    var anchor=nowMs-seconds*1000;
    existing=legacy.concat(added.map(function(r){return{n:0,row:r};})).filter(function(x){var r=x.row;if(String(r[1])!==id||r[2]!==page||r[3]!==action||r[4]!==target||r[6]!==device||Number(r[5])<60)return false;
     var stamp=String(r[7]||'').indexOf('legacy190|')===0?Number(String(r[7]).split('|')[1]):usageMillis190_(r[0])-Number(r[5])*1000;return !String(r[7]||'').startsWith('u190|')&&Math.abs(stamp-anchor)<=30000;
    }).pop()||null;
    if(existing&&!existing.row[7])existing.row[7]='legacy190|'+Math.round(anchor);if(!existing)key='legacy190|'+Math.round(anchor);
   }
   var depth=Math.max(0,Math.min(100,Number(e.depth)||0)),reports=Math.max(1,Math.min(100000,Number(e.reports)||1)),views=Math.max(0,Math.min(10000,Number(e.views)||0)),count=Math.max(0,Math.min(10000,Number(e.count)||0)),hours=new Array(24).fill(0);
   if(v2&&Array.isArray(e.hours))hours=hours.map(function(_,i){return Math.max(0,Math.min(seconds,Number(e.hours[i])||0));});
   if(hours.reduce(function(a,b){return a+b;},0)>seconds+1)hours=new Array(24).fill(0);
   if(existing&&existing.row){var r=existing.row,old=JSON.stringify(r);r[5]=Math.max(Number(r[5])||0,seconds);r[9]=now;r[11]=Math.max(Number(r[11])||0,depth);r[12]=Math.max(Number(r[12])||0,reports);r[14]=Math.max(Number(r[14])||0,views);r[15]=Math.max(Number(r[15])||0,count);
    if(v2){var prior=[];try{prior=JSON.parse(r[17]||'[]');}catch(x){}r[17]=JSON.stringify(hours.map(function(n,i){return Math.max(n,Number(prior[i])||0);}));}
    // Timestamp-only retries do not rewrite the sheet.
    var before=JSON.parse(old);before[9]=r[9];if(JSON.stringify(before)!==JSON.stringify(r)){if(existing.n)sh.getRange(existing.n,1,1,18).setValues([r]);updated++;}else ignored++;
    if(action==='停留')stays.push({page:page,seconds:r[5]});return;
   }
   var ref=String(e.referrer||'直接/未知').slice(0,100);if(!/^[a-zA-Z0-9.-]+$/.test(ref)&&!['直接/未知','站內'].includes(ref))ref='直接/未知';
   var r=[now,id,page,action,target,seconds,device,key,String(e.visit||e.session||'').replace(/[^\w-]/g,'').slice(0,96),now,ref,depth,reports,usageText190_(e.landing,40),views,count,v2?'v2':'legacy',JSON.stringify(hours)];added.push(r);if(key&&v2)pending[key]=r;if(action==='停留')stays.push({page:page,seconds:seconds});
  });
  if(added.length){var start=sh.getLastRow()+1;sh.getRange(start,1,added.length,18).setValues(added);added.forEach(function(r,i){if(String(r[7]).indexOf('u190|')===0)cache.put('usage190:'+usageHash190_(r[7]),String(start+i),21600);});}
  return{ok:true,n:Math.min(events.length,USAGE_MAX_BATCH),added:added.length,updated:updated,unchanged:ignored,stays:stays};
 }catch(e){return{ok:false,reason:String(e.message||e).slice(0,80)};}finally{lock.releaseLock();}
}

function usageAggregate190_(rows,days,now){
 var end=now.slice(0,10).replace(/\//g,'-'),endMs=Date.parse(end+'T00:00:00+08:00'),startMs=endMs-(days-1)*86400000,priorStart=startMs-days*86400000;
 var clean=[],legacy=0,earliest='',keys={};rows.forEach(function(r){if(r[16]!=='v2'){legacy++;return;}var key=String(r[7]||''),parts;try{parts=JSON.parse(key.slice(5));}catch(e){return;}var date=parts[2],ms=Date.parse(date+'T00:00:00+08:00');if(!earliest||date<earliest)earliest=date;if(ms<priorStart||ms>endMs)return;
  // Defensive deduplication of retry rows, if a previous release ever wrote one.
  if(keys[key]){if(Number(r[5])>Number(keys[key][5]))keys[key]=r;}else keys[key]=r;
 });Object.keys(keys).forEach(function(k){clean.push(keys[k]);});
 function roll(list){var users={},visits={},pages={},sources={},landings={},devices={},actions={},trend={},hours=new Array(24).fill(0),total=0,views=0,reports=0,stays=0;
  list.forEach(function(r){var parts=JSON.parse(String(r[7]).slice(5)),date=parts[2],id=String(r[1]),visit=String(r[8]),sec=Number(r[5])||0,u=users[id]||(users[id]={seconds:0,days:{},visits:{},pages:{},reading:{},last:'',depth:0});u.days[date]=true;u.visits[visit]=true;u.pages[r[2]]=true;var stamp=r[9] instanceof Date?Utilities.formatDate(r[9],'Asia/Taipei','yyyy/MM/dd HH:mm:ss'):String(r[9]||'');if(stamp>u.last)u.last=stamp;
   var v=visits[visit]||(visits[visit]={seconds:0,views:0,acted:false,source:r[10]||'直接/未知',landing:r[13]||r[2]});if(r[3]==='停留'){stays++;total+=sec;views+=Number(r[14])||0;reports+=Number(r[12])||1;u.seconds+=sec;u.reading[r[2]]=(u.reading[r[2]]||0)+sec;u.depth=Math.max(u.depth,Number(r[11])||0);v.seconds+=sec;v.views+=Number(r[14])||0;
    var p=pages[r[2]]||(pages[r[2]]={page:r[2],seconds:0,views:0,users:{},depth:0,rows:0});p.seconds+=sec;p.views+=Number(r[14])||0;p.users[id]=true;p.depth+=Number(r[11])||0;p.rows++;
    var t=trend[date]||(trend[date]={day:date,seconds:0,users:{}});t.seconds+=sec;t.users[id]=true;
    var d=devices[r[6]]||(devices[r[6]]={name:r[6],users:{},seconds:0});d.users[id]=true;d.seconds+=sec;
    try{JSON.parse(r[17]||'[]').forEach(function(n,i){if(i<24)hours[i]+=Number(n)||0;});}catch(e){}
   }else{v.acted=true;var ak=r[3]+'|'+r[4],a=actions[ak]||(actions[ak]={action:r[3],target:r[4],count:0});a.count+=Number(r[15])||1;}
  });
  Object.keys(visits).forEach(function(k){var v=visits[k];sources[v.source]=(sources[v.source]||0)+1;landings[v.landing]=(landings[v.landing]||0)+1;});
  var ids=Object.keys(users),vs=Object.keys(visits),returning=ids.filter(function(k){return Object.keys(users[k].days).length>=2;}).length,frequent=ids.filter(function(k){return Object.keys(users[k].days).length>=3;}).length;
  var audience=ids.map(function(k){var u=users[k];return{id:'訪客 '+usageHash190_(k).slice(0,8),seconds:u.seconds,activeDays:Object.keys(u.days).length,sessions:Object.keys(u.visits).length,pages:Object.keys(u.pages).length,last:u.last,lastDay:Object.keys(u.days).sort().pop(),depth:u.depth,activeDates:Object.keys(u.days).sort(),topPages:Object.keys(u.reading).map(function(p){return{page:p,seconds:u.reading[p]};}).sort(function(a,b){return b.seconds-a.seconds;}).slice(0,6)};}).map(function(p){p.recency=Math.max(0,Math.floor((endMs-Date.parse(p.lastDay+'T00:00:00+08:00'))/86400000));p.repeatVisits=Math.max(0,p.sessions-1);return p;});
  return{visitors:ids.length,sessions:vs.length,views:views,seconds:total,average:ids.length?total/ids.length:0,returning:returning,returnRate:ids.length?returning/ids.length*100:null,frequent:frequent,engagement:vs.length?vs.filter(function(k){var v=visits[k];return v.seconds>=10||v.views>=2||v.acted;}).length/vs.length*100:null,
   pages:Object.keys(pages).map(function(k){var p=pages[k];return{page:k,seconds:p.seconds,views:p.views,visitors:Object.keys(p.users).length,depth:p.rows?p.depth/p.rows:0};}).sort(function(a,b){return b.seconds-a.seconds;}),
   landings:Object.keys(landings).map(function(k){return{page:k,count:landings[k]};}).sort(function(a,b){return b.count-a.count;}),sources:Object.keys(sources).map(function(k){return{name:k,count:sources[k]};}).sort(function(a,b){return b.count-a.count;}),devices:Object.keys(devices).map(function(k){return{name:k,visitors:Object.keys(devices[k].users).length,seconds:devices[k].seconds};}),
   actions:Object.keys(actions).map(function(k){return actions[k];}).sort(function(a,b){return b.count-a.count;}).slice(0,30),hours:hours,
   trend:Object.keys(trend).sort().map(function(k){return{day:k,seconds:trend[k].seconds,visitors:Object.keys(trend[k].users).length};}),
   people:audience.slice().sort(function(a,b){return b.seconds-a.seconds;}).slice(0,50),rfAudience:audience.slice().sort(function(a,b){return b.sessions-a.sessions||b.seconds-a.seconds;}).slice(0,500).map(function(p){return{id:p.id,recency:p.recency,sessions:p.sessions,repeatVisits:p.repeatVisits,seconds:p.seconds,activeDays:p.activeDays,last:p.last,lastDay:p.lastDay};}),rfTotal:audience.length,
   snapshotRows:stays,snapshots:reports,rowReduction:reports?Math.max(0,(1-stays/reports)*100):null};
 }
 var curr=clean.filter(function(r){return Date.parse(JSON.parse(String(r[7]).slice(5))[2]+'T00:00:00+08:00')>=startMs;}),prev=clean.filter(function(r){return Date.parse(JSON.parse(String(r[7]).slice(5))[2]+'T00:00:00+08:00')<startMs;});
 var first=earliest?Date.parse(earliest+'T00:00:00+08:00'):Infinity;
 return{ok:true,days:days,from:Utilities.formatDate(new Date(startMs),'Asia/Taipei','yyyy/MM/dd'),to:end.replace(/-/g,'/'),generatedAt:now,current:roll(curr),previous:roll(prev),comparable:first<=priorStart,coverageFrom:earliest||null,legacyExcluded:legacy,definitions:{time:'頁面可見的累積時間；背景不計，不代表實際注視。',returning:'所選期間至少2個台灣日期有紀錄的匿名訪客。',frequent:'所選期間至少3個台灣日期有紀錄的匿名訪客。',engagement:'一次瀏覽至少10秒、至少2次頁面瀏覽或有互動。',loyalty:'回訪與常訪為行為代理指標；不推斷真人身分或投資偏好。',rowReduction:'1－停留彙整列數／客戶端快照次數；比較基準是每張快照各新增一列，不是帳單或執行時間降幅。'}};
}
function usageAnalytics190_(options){
 var days=[7,28,90].indexOf(Number(options.days))>=0?Number(options.days):28,includeAdmin=options.includeAdmin===true,key='usageAnalytics190r1:'+days+':'+includeAdmin,cache=CacheService.getScriptCache(),saved=cache.get(key);
 if(saved&&!options.refresh){var v=JSON.parse(saved);v.cached=true;return v;}
 var sh=getSheet_(USAGE_SHEET),last=sh.getLastRow(),n=Math.min(30000,Math.max(0,last-1)),rows=n?sh.getRange(last-n+1,1,n,18).getValues():[];
 if(!includeAdmin)rows=rows.filter(function(r){return String(r[2]).indexOf('後台/')!==0&&r[6]!=='admin';});
 var out=usageAggregate190_(rows,days,nowStamp_());out.scanned=n;out.truncated=last-1>n;out.includeAdmin=includeAdmin;out.cached=false;
 try{var s=JSON.stringify(out);if(s.length<90000)cache.put(key,s,60);}catch(e){}return out;
}
