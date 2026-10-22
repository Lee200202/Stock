/* 離線契約檢查，不使用網路、不寫試算表、不呼叫模型或郵件。 */
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const source = fs.readFileSync('public-site/gas-source/MailService.gs', 'utf8').replace(/\r\n/g,'\n');
const start = source.indexOf('function spokenCodeFor_(');
const end = source.indexOf('\n/**\n * 品質關卡的收尾保險', start);
assert(start > 0 && end > start);
function run({name='甲公司',raw='甲公司回測季線，仍應續抱。',ruled=[],fail=false,reason='甲公司維持續抱。',wrong=false}={}) {
  let calls=0; const writes=[];
  const ctx = {
    fmtDate_: x=>String(x),todayStr_:()=> '2026/10/16',
    digestAuditVerdict_:()=>({missing:[{name,where:'持股',evidence:raw}]}),
    readSheetObjects_: name=> {
      if(name==='分類裁決') {if(fail)throw Error('unavailable');return ruled;}
      if(name==='影片清單') return [{'發布日期':'2026/10/16','影片ID':'v1',raw}];
      return [];
    },rawTranscript_:r=>r.raw,loadCodeMap_:()=>({byCode:{3037:{name:'欣興'}}}),
    reviewSnips_:()=>raw,validEvidence_:()=>true,PIPE_EXTRACT_SYSTEM:'',
    callGemini_:()=>{calls++;return JSON.stringify({results:[{index:1,is_stock:true,should_publish:true,
      confident:true,direction:'會員持股',reason:wrong?'乙公司維持續抱。':reason,evidence:[raw],when:'today'}]});},
    withLock_:fn=>fn(),Logger:{log:()=>{}},CACHE:{remove:()=>{}},AUDIT_SKIP_LABEL:{},
    getSheet_:name=>({getLastRow:()=>1,getRange:()=>({setValues:rows=>writes.push({name,rows})})}),
    proposeAuditFixes:()=>{throw Error('Unexpected fallback model');},
  };
  vm.createContext(ctx); vm.runInContext(source.slice(start,end),ctx);
  const result=ctx.autoFixAuditMissing('2026/10/16');
  return {result,calls,writes};
}
let r=run({name:'聯詠',ruled:[{'日期':'2026/10/16','代號':'3034','名稱':'聯詠','分類':'排除'}]});
assert.equal(r.calls,0);assert.equal(r.writes.length,0);
r=run({name:'新興',raw:'3037的新興昨天跌停，只作教學。',ruled:[{'日期':'2026/10/16','代號':'3037','名稱':'欣興','分類':'排除'}]});
assert.equal(r.calls,0);assert.equal(r.writes.length,0);
r=run({raw:''});assert.equal(r.calls,0);assert.equal(r.result.pending,1);
r=run({fail:true});assert.equal(r.calls,0);assert.equal(r.result.pending,1);
const long='甲公司回測向上的季線後維持持有。'+ '後續觀察量能與價格能否持續配合，尚未確認轉弱前不因短線震盪調節。'.repeat(2);
r=run({reason:long});assert.equal(r.calls,1);assert.equal(r.writes[0].rows[0][4],long);
r=run({wrong:true});assert.equal(r.writes.length,0);assert.equal(r.result.skipped,1);
console.log('6 gate contracts passed: ruled names/codes, unreadable source/decision, full sentences, own-stock note.');
