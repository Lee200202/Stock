/* 排程安全契約：官方來源和AI是替身；不宣稱這是實際模型品質測試。 */
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('public-site/gas-source/MarketSession.gs','utf8');
const props=new Map();let aiCalls=0,sourceCalls=0,locked=false,failAI=false,onAI=null;
const day='2024-10-31';
// 原始公告來源：證交所113/10/30康芮颱風全日休市公告。
const notice={id:'news-0',kind:'exchange-news',date:'2024-10-30',url:'https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69236c2e30192dd5179bc0327.pdf',text:'臺灣證券交易所集中交易市場113年10月31日休市一天'};
const good=()=>({status:'closed',affectedDate:day,scope:'market',fullDay:true,limitedOrFuture:false,sourceId:'news-0',quote:notice.text,reason:'官方公告全日休市'});
const context={Date,JSON,Number,String,Array,Math,RegExp,Logger:{log(){}},Utilities:{formatDate(d,tz,fmt){const p=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit'}).format(d);return fmt==='u'?String((new Date(p+'T12:00:00+08:00').getUTCDay()+6)%7+1):p;}},PropertiesService:{getScriptProperties:()=>({getProperty:k=>props.get(k)||null,setProperty:(k,v)=>props.set(k,v)})},LockService:{getScriptLock:()=>({tryLock:()=>!locked,releaseLock(){}})},whyClosed_:()=>'',marketHolidaySet_:()=>({})};
vm.createContext(context);vm.runInContext(source,context);
context.marketSessionOfficialSources_=()=>{sourceCalls++;return [notice];};
context.marketSessionAskAI_=(claim,sources)=>{aiCalls++;if(onAI)onAI();if(failAI)throw Error('測試配額');return good();};
let count=0;function test(name,fn){fn();count++;console.log('✓ '+name);}
const at=(time,d=day)=>new Date(d+'T'+time+':00+08:00');
function reset(){props.clear();aiCalls=0;sourceCalls=0;failAI=false;onAI=null;locked=false;}
function health(time,n=0){return context.marketSessionQuoteHealth_(n,at(time).getTime(),at(time));}
function twenty(){health('09:00');health('09:05');health('09:10');health('09:15');return health('09:20');}
const valid=(a=good(),s=[notice],seen=false)=>context.marketSessionValidate_(day,a,s,seen);
test('官方颱風公告日期與引句核對通過',()=>assert.equal(valid().status,'closed'));
test('僅模型肯定但沒有官方來源不停止',()=>assert.equal(valid(good(),[]).verified,false));
test('假來源編號不停止',()=>assert.equal(valid({...good(),sourceId:'no'}).verified,false));
test('幻覺引句不停止',()=>assert.equal(valid({...good(),quote:'臺灣證券交易所今天休市，這句是模型自行產生'}).verified,false));
test('錯誤日期不停止',()=>assert.equal(valid({...good(),affectedDate:'2024-11-01'}).verified,false));
test('局部地區及個股不停止全市場',()=>assert.equal(valid({...good(),scope:'partial'}).verified,false));
test('下午或盤後停止不當全日休市',()=>assert.equal(valid({...good(),fullDay:false}).verified,false));
test('未來或有限範圍事實有衝突不停止',()=>assert.equal(valid({...good(),limitedOrFuture:true}).verified,false));
test('已有當日成交時休市結論不生效',()=>assert.equal(valid(good(),[notice],true).status,'open'));
test('非官方網域不停止',()=>assert.equal(valid(good(),[{...notice,url:'https://example.com/news'}]).verified,false));
test('日期不能只靠發布時間',()=>assert.equal(valid({...good(),quote:'颱風影響相關作業請參閱公告'},[{...notice,text:'颱風影響相關作業請參閱公告'}]).verified,false));
test('11月1日不會誤配1月1日',()=>assert.equal(context.marketSessionQuoteHasDay_('11月1日休市', {...notice,date:'2024-01-01'},'2024-01-01'),false));
test('明日相對日期以官方發布日為錨',()=>assert.equal(context.marketSessionQuoteHasDay_('明(31)日集中市場休市',notice,day),true));
test('相對日期括號日號不一致不通過',()=>assert.equal(context.marketSessionQuoteHasDay_('明(30)日集中市場休市',notice,day),false));
test('民國數字及中文日期兩種來源皆能解析',()=>{assert.equal(context.marketSessionRocDate_('1131030'),'2024-10-30');assert.equal(context.marketSessionRocDate_('113年10月30日'),'2024-10-30');});
test('未滿20分鐘不發AI',()=>{reset();health('09:00');health('09:19');assert.equal(aiCalls,0);});
test('滿20分鐘且多輪失敗才合批問一次',()=>{reset();assert.equal(twenty().status,'closed');assert.equal(aiCalls,1);assert.equal(sourceCalls,1);assert.equal(context.marketSessionClosed_(at('09:21')),true);});
test('已確認停止後不反覆請求',()=>{reset();twenty();health('09:25');assert.equal(aiCalls,1);});
test('跨日自動恢復不延用昨日停止',()=>{reset();twenty();assert.equal(context.marketSessionClosed_(at('09:00','2024-11-01')),false);});
test('中途有效成交重設連續失敗起點',()=>{reset();health('09:00');health('09:15',1);health('09:20');health('09:30');assert.equal(aiCalls,0);});
test('模型失敗保留unknown與抓取資格',()=>{reset();failAI=true;assert.equal(twenty().status,'unknown');assert.equal(context.marketSessionClosed_(at('09:21')),false);});
test('未知結果30分鐘冷卻且每日最多三批',()=>{reset();failAI=true;twenty();health('09:25');assert.equal(aiCalls,1);health('09:50');health('10:20');health('10:50');assert.equal(aiCalls,3);});
test('官方來源中斷不推定休市',()=>{reset();let previous=context.marketSessionOfficialSources_;context.marketSessionOfficialSources_=()=>{throw Error('來源中斷');};assert.equal(twenty().verified,false);context.marketSessionOfficialSources_=previous;});
test('並行等待期間取得成交，舊AI不蓋回休市',()=>{reset();onAI=()=>context.marketSessionQuoteHealth_(1,at('09:21').getTime(),at('09:21'));assert.equal(twenty().status,'open');});
test('鎖被其他工作占用不呼叫模型',()=>{reset();locked=true;twenty();assert.equal(aiCalls,0);});
test('既知國定假日直接略過，不需問AI',()=>{reset();context.marketHolidaySet_=()=>({[day]:1});assert.equal(context.marketSessionSkipPull_(at('09:00')),true);assert.equal(aiCalls,0);context.marketHolidaySet_=()=>({});});
test('年度表取得失敗不冒充已確認休市',()=>{reset();context.whyClosed_=()=> '年度休市日尚未核實';assert.equal(context.marketSessionSkipPull_(at('09:00')),false);context.whyClosed_=()=>'';});
// 確認接線在實際來源，而非僅測純函式。
test('報價、盤中輪詢與自動補抓都有接入',()=>{const q=fs.readFileSync('public-site/gas-source/Quoteservice.gs','utf8'),c=fs.readFileSync('public-site/gas-source/Cmoney.gs','utf8');assert(q.includes('marketSessionQuoteHealth_(sessionLive, sessionStartedAt'));assert(q.includes('marketSessionClosed_(now)'));assert(c.includes("out.skipped = '官方已確認當日休市'"));assert(c.includes('marketSessionSkipPull_(now)) { return; }'));});
test('AI異常診斷限制單次實際請求、不改分類預設',()=>{const ai=fs.readFileSync('public-site/gas-source/Aiservice.gs','utf8');assert(ai.includes('if (opt.singleAttempt) { throw e; }'));assert(ai.includes('if (opt.singleAttempt) { throw new Error(lastErr); }'));assert(source.includes('singleAttempt: true'));});
console.log(count+' 項休市異常契約通過（模型為替身，非真實模型正確率）');
const aiCode=fs.readFileSync('public-site/gas-source/Aiservice.gs','utf8');
for(const code of [429,503])test('真實呼叫器HTTP '+code+'只打一次、不輪換金鑰',()=>{
 let requests=0;const a={Date,JSON,Number,String,Array,Math,RegExp,Logger:{log(){}},PropertiesService:{getScriptProperties:()=>({getProperty:()=>null,setProperty(){}})},Utilities:{sleep(){}},UrlFetchApp:{fetch(){requests++;return {getResponseCode:()=>code,getContentText:()=>JSON.stringify({error:{message:'測試來源暫時無法回應'}}),getHeaders:()=>({})};}}};
 vm.createContext(a);vm.runInContext(aiCode,a);a.usableKeys_=()=>[{idx:0,key:'測試一'},{idx:1,key:'測試二'}];
 assert.throws(()=>a.callGemini_('系統','輸入',{singleAttempt:true,maxWaitMs:25000}),new RegExp('HTTP '+code));assert.equal(requests,1);
});
console.log('含真實AI呼叫器 '+count+' 項契約通過');
