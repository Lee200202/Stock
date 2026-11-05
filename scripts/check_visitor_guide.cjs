const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {createHash,randomUUID} = require('node:crypto');
const base = 'public-site/gas-source/';
const guide = fs.readFileSync(base+'VisitorGuide.html','utf8');
new vm.Script(guide.match(/<script>([\s\S]*?)<\/script>/)[1]);
const rows=[], messages=[], cache=new Map(), props=new Map([['OWNER_EMAIL','owner@example.test']]);
let sheet, quota=99, failMail=false;
const fixture = {
  appendRow:row=>rows.push(row),
  setFrozenRows(){},
  getLastColumn:()=>rows[0].length,
  getRange:(row,column)=>({
    getValues:()=>[rows[row-1].slice()],
    setValue:value=>{rows[row-1][column-1]=value;}
  })
};
const context = {
  LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},
  CacheService:{getScriptCache:()=>({get:key=>cache.get(key),put:(key,value)=>cache.set(key,value)})},
  PropertiesService:{getScriptProperties:()=>({getProperty:key=>props.get(key),setProperty:(key,value)=>props.set(key,value)})},
  Utilities:{formatDate:()=> '20261105',getUuid:randomUUID,base64EncodeWebSafe:value=>value.toString('base64url'),
    DigestAlgorithm:{SHA_256:'sha256'},computeDigest:(kind,text)=>createHash(kind).update(text).digest()},
  getSS_:()=>({getSheetByName:()=>sheet,insertSheet:()=>sheet=fixture}),
  MailApp:{getRemainingDailyQuota:()=>quota,sendEmail(mail){if(failMail)throw Error('fixture transport failure');messages.push(mail);}},
  Logger:{log(){}}
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(base+'Feedback.gs','utf8'),context);
const submit = (message, extra={}) => context.apiSubmitFeedback({kind:'操作問題',page:'overview',message,...extra});
assert.equal(submit('不足').ok,false);
assert.equal(submit('這是測試用的回饋內容',{website:'filled'}).ok,false);
assert.equal(rows.length,0);
assert.equal(submit('=IMPORTXML("fixture")').ok,true);
assert.equal(rows.length,2);
assert.equal(rows[1][3][0],"'");
assert.equal(messages.length,1);
assert.equal(messages[0].to,'owner@example.test');
assert.equal(submit('=IMPORTXML("fixture")').duplicate,true);
assert.equal(messages.length,1);
quota=20;
assert.equal(submit('低額度時仍保留這筆測試回饋').ok,true);
assert.equal(messages.length,1);
quota=99;failMail=true;
assert.equal(submit('通知失敗時仍接受這筆測試回饋').ok,true);
assert.equal(rows.length,4);
quota=99;failMail=false;
rows[0].pop(); // A sheet created before structured context has the original five headers.
const details = {kind:'內容更正',viewingDate:'2026-11-05',theme:'dark',viewport:'390x668',
  stockCode:'2330',issue:'跨股借句'};
const report = submit('逐字稿中的個股來源需要核對。', details);
assert.equal(report.ok,true);
assert.ok(report.receipt);
assert.equal(rows[0][5],'畫面資訊');
assert.equal(rows[4][4],report.receipt);
assert.match(rows[4][5],/2026-11-05[\s\S]*深色[\s\S]*390x668[\s\S]*2330[\s\S]*跨股借句/);
assert.ok(messages.at(-1).body.includes(rows[4][5]));
assert.equal(submit('逐字稿中的個股來源需要核對。', details).receipt,report.receipt);
assert.equal(rows.length,5);
assert.equal(submit('逐字稿中的個股來源需要核對。',{...details,viewingDate:'2026-11-04'}).ok,true);
assert.equal(rows.length,6);
assert.equal(Object.keys(context.feedbackContext_({
  viewingDate:'2026-02-30',theme:'<img>',viewport:'x',stockCode:'=2+2',issue:'自行輸入'
},'內容更正')).length,0);
assert.equal(Object.keys(context.feedbackContext_({stockCode:'2330',issue:'跨股借句'},'操作問題')).length,0);
console.log('Visitor guide script parses. Feedback validation, safe cell, duplicate, owner-only notification, quota and mail failure checks passed.');
