// 「等待續跑」：下游暫時性錯誤不再把工單標成失敗，由既有觸發器自動續跑（2026/09/13）。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..'),props={};let job=null,dispatches=0,dispatchOk=true;
const ctx=vm.createContext({console,Logger:{log:()=>{}},
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k],setProperty:(k,v)=>props[k]=v,deleteProperty:k=>delete props[k]})},
 LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
 currentJob_:()=>job,updateJob_:patch=>Object.assign(job,patch),
 dispatchGithub_:()=>{dispatches++;return dispatchOk?{ok:true}:{ok:false,reason:'network'};}});
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Transcriptstore.gs'),'utf8'),ctx);

// 等待續跑會被自動續跑，說明寫的是下游暫時無回應，不是日K
job={id:'T1',status:'等待續跑'};ctx.resumePendingTranscriptRefresh_();
assert.equal(dispatches,1);assert.equal(job.status,'處理中');assert(job.note.includes('下游暫時無回應'));
// 冷卻期間不重複派工
job.status='等待續跑';ctx.resumePendingTranscriptRefresh_();assert.equal(dispatches,1);
// 派工失敗維持等待續跑，不會被改成等待日K
props['txResume:T2']=JSON.stringify({count:0,at:0});dispatchOk=false;
job={id:'T2',status:'等待續跑'};ctx.resumePendingTranscriptRefresh_();assert.equal(job.status,'等待續跑');
// 連續 6 輪都失敗才轉待處理，說明指向 ping 檢查
dispatchOk=true;props['txResume:T3']=JSON.stringify({count:6,at:0});
job={id:'T3',status:'等待續跑'};ctx.resumePendingTranscriptRefresh_();
assert.equal(job.status,'待處理');assert(job.note.includes('?action=ping'));
// 等待日K 的行為不變
props['txResume:T4']=JSON.stringify({count:6,at:0});job={id:'T4',status:'等待日K'};ctx.resumePendingTranscriptRefresh_();
assert(job.note.includes('日K已自動續跑6輪'));

// 送出、取消、整併都把等待續跑視為工單尚未結束
const admin=fs.readFileSync(path.join(root,'apps-script/Adminservice.gs'),'utf8');
assert.equal((admin.match(/\['處理中','等待日K','等待續跑'\]/g)||[]).length,2);
assert(fs.readFileSync(path.join(root,'apps-script/Transcriptstore.gs'),'utf8').includes("['處理中','等待日K','等待續跑'].indexOf(job.status)"));

// 後台畫面：警示色、可取消、持續輪詢、提示寫自動續跑
const html=fs.readFileSync(path.join(root,'apps-script/Admin.html'),'utf8'),nodes={};
const node=id=>nodes[id]||(nodes[id]={style:{},parentElement:{setAttribute:()=>{}}});let started=0,stopped=0;
const ui=vm.createContext({$:node,esc:s=>s,STEPS:['讀取原文','刷新網站','完成'],stepIndexOf:(s,n)=>s.indexOf(n),
 layoutSteps:()=>{},setProgressA11y_:()=>{},startPoll:()=>started++,stopPoll:()=>stopped++});
vm.runInContext(html.match(/^function renderJob\(j\)\{[\s\S]*?^}/m)[0],ui);
ui.renderJob({status:'等待續跑',step:'刷新網站',done:13,total:15,note:'文章已更新；codes:2026/09/11 暫停'});
assert.equal(started,1);assert.equal(nodes.cancelBtn.disabled,false);assert.equal(nodes.barFill.style.background,'#b7791f');
assert(nodes.progHint.textContent.includes('自動續跑'));assert(!nodes.progHint.textContent.includes('處理完成'));
ui.renderJob({status:'等待日K',step:'刷新網站',done:13,total:15,note:'文章已更新'});
assert.equal(started,2);assert(nodes.progHint.textContent.includes('日K與績效'));

const cfg=fs.readFileSync(path.join(root,'apps-script/Config.gs'),'utf8');
assert(cfg.includes("'refresh-transient-resume'"));
console.log('PASS: transient refresh waits and auto-resumes, limit message, submit/cancel/merge guards, admin waiting UI.');
