const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const root = path.resolve(__dirname, '..');
let syntaxCount = 0;
for (const f of fs.readdirSync(path.join(root, 'apps-script'))) {
  const text = fs.readFileSync(path.join(root, 'apps-script', f), 'utf8');
  if (f.endsWith('.gs')) { new vm.Script(text, {filename: f}); syntaxCount++; }
  if (f.endsWith('.html')) {
    for (const match of text.replace(/<!--[\s\S]*?-->/g, '').matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)) {
      new vm.Script(match[1].replace(/<\?[\s\S]*?\?>/g, 'null'), {filename:f}); syntaxCount++;
    }
  }
}
const properties = {};
const calls = [];
const ctx = vm.createContext({
  PropertiesService: {getScriptProperties: () => ({getProperty:k=>properties[k],setProperty:(k,v)=>properties[k]=v})},
  ScriptApp: {getProjectTriggers:()=>[], newTrigger:()=>({timeBased:()=>({after:()=>({create:()=>{}})})})},
  nowStamp_:()=> '2026/09/10 16:00:00', fmtDate_:s=>s, adminAuth_:()=>{}, withLock_:f=>f(),
  stepArticle_:()=> calls.push('mail'), rebuildHoldingsTrackerJob:()=> calls.push('tracker'),
  rebuildPerformanceHistoryJob:d=>{calls.push('history:'+d);return {ok:true};},
  snapshotPerformanceJob:()=> calls.push('perf'),
});
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Presentationquality.gs'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Evidencequality.gs'),'utf8'),ctx);
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Articlequality.gs'),'utf8'),ctx);
assert(ctx.validEvidence_(['我昨天買世芯-KY，今天還有。'], '我昨天買世芯-KY，今天還有。'));
assert(!ctx.validEvidence_(['新代我們今天買了。'], '我昨天買世芯-KY，今天還有。'));
assert.equal(ctx.rawTranscript_({'原始逐字稿內容':'原文辛耘','修飾後逐字稿內容':'摘要星雲'}),'原文辛耘');
assert(ctx.apiAdminStartDaySync('k','2026/09/09').ok);
assert(!ctx.apiAdminStartDaySync('k','2026/09/10').ok);
ctx.runDayEditSync_();
assert.equal(ctx.daySyncState_().status,'處理中');
assert.equal(ctx.daySyncState_().index,1);
ctx.runDayEditSync_();ctx.runDayEditSync_();ctx.runDayEditSync_();
assert.deepStrictEqual(calls,['mail','tracker','history:2026/09/09','perf']);
assert.equal(ctx.daySyncState_().status,'完成');
ctx.apiAdminStartDaySync('k','2026/09/09');
ctx.runDayEditSync_();
ctx.rebuildHoldingsTrackerJob=()=>{throw new Error('tracker unavailable');};
ctx.Logger={log(){}};ctx.runDayEditSync_();
assert.equal(ctx.daySyncState_().status,'等待續跑');
assert.equal(ctx.daySyncState_().index,1);
const oldCalls=calls.length;ctx.runDayEditSync_();assert.equal(calls.length,oldCalls);
ctx.apiAdminStartDaySync('k','2026/09/09');ctx.apiAdminCancelDaySync('k');ctx.runDayEditSync_();
assert.equal(ctx.daySyncState_().status,'已取消');
const sig={buy:[],sell:[],holdings:[{name:'鴻準',code:'2354',note:'目前仍持有。'}],watch_avoid:[],watch_watch:[]};
const article=ctx.enforceArticleRecords_('① 標題\n④ 會員操作紀錄與持股明細\n華城今日賣出\n⑤ 分析師操作邏輯與教學重點\n',sig,'2026/09/10');
assert(!article.includes('華城'));assert(article.includes('鴻準'));assert(article.includes('未說明當日具體買賣'));
const rebuilt=ctx.enforceArticleRecords_('不完整文章',sig,'2026/09/10');
assert(rebuilt.includes('③ 分析師操作邏輯與教學重點'));assert(!rebuilt.includes('風險揭露'),'v44：風險揭露只在信尾');
// 日期未明的回顧不再另列：信件不可出現網站「翻到某一天」查不到的股票（2026/09/11）。
const sigHistory=Object.assign({},sig,{history:[{name:'索羅門',reason:'大漲時賣出索羅門。'}]});
const withHistory=ctx.enforceArticleRecords_('① 標題\n④ 會員操作紀錄\n⑤ 教學\n',sigHistory,'2026/09/10');
assert(!withHistory.includes('索羅門'));assert(!withHistory.includes('歷史回顧'));
// 規則版本升到 context-json-v3 之後，大盤摘要仍要讀得到；盤勢章放盤勢、教學章放教學重點（2026/09/11；v22 起為 ① 與 ③）。
const auditRow=(item,cat)=>({'影片日期':'2026/09/11','規則版本':'context-json-v3','來源影片ID':'MANUAL-20260911',
  '判讀JSON':JSON.stringify({batch:'R1',category:cat,item:item})});
ctx.readSheetObjects_=()=>[auditRow({kind:'level',text:'指數關卡46188',_evidence_verified:true},'market'),
  auditRow({kind:'view',text:'下跌不賣、大漲才賣',_evidence_verified:true},'market'),
  auditRow({status:'published'},'manifest')];
const sig3=ctx.attachArticleEvidence_({buy:[],sell:[],holdings:[],watch_avoid:[],watch_watch:[]},'2026/09/11');
assert.equal(sig3.market.length,2);
const art3=ctx.enforceArticleRecords_('① 標題\n④ 會員操作紀錄\n⑤ 教學\n',sig3,'2026/09/11');
const third3=art3.split('① 盤勢總覽')[1].split('② 會員操作紀錄')[0], fifth3=art3.split('③ 分析師操作邏輯')[1].split('④ 風險揭露')[0];
assert(third3.includes('46188'));assert(!third3.includes('下跌不賣'));assert(fifth3.includes('下跌不賣'));
// 同一天同一人只寄一次：整天重跑把那一列刪掉重建成「待寄送」，也不再寄第二封（2026/09/11）。
// v54 起改成逐收件者寄送帳本：中途失敗只續送還沒收到的人，已收到的人不會再收（Codex 規格 86）。
const mailProps = {}, mailSent = [];
let pushRows, failAt = 0, today = '2026/09/11';
const ledgerRows = [['訊息ID', '種類', '日期', '內容版本', '收件者', '狀態', '嘗試次數', '最後錯誤', '更新時間', '服務接受時間']];
const ledgerSheet = {
  getDataRange: () => ({getValues: () => ledgerRows.map(r => r.slice())}),
  getLastRow: () => ledgerRows.length,
  getRange: (r, c, n, m) => ({setValues(data) { data.forEach((row, i) => { ledgerRows[r - 1 + i] = row.slice(); }); return this; }})
};
const mctx = vm.createContext({
  PropertiesService: {getScriptProperties: () => ({getProperty: k => mailProps[k], setProperty: (k, v) => { mailProps[k] = v; }})},
  Utilities: {formatDate: (_date, _tz, pattern) => pattern === 'HHmm' ? '1305' : '2026/09/11'},
  TZ: 'Asia/Taipei',
  Logger: {log: () => {}},
  MailApp: {sendEmail: m => { if (failAt && mailSent.length + 1 === failAt) { throw new Error('quota'); } mailSent.push(m.to); }},
});
vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/MailService.gs'), 'utf8'), mctx);
const pushSheet = {
  getDataRange: () => ({getValues: () => pushRows.map(r => r.slice())}),
  getRange: (r, c) => ({getValue: () => pushRows[r - 1][c - 1], getNote: () => '',
    setValue(v) { pushRows[r - 1][c - 1] = v; return this; }, setNote() { return this; }})
};
Object.assign(mctx, {
  todayStr_: () => today, isTradingDayToday_: () => true, fmtDate_: s => s, nowStamp_: () => today + ' 13:05:00',
  withLock_: f => f(), gateReady_: () => true, readSheetObjects_: () => [], mdToHtml_: s => s, wrapMail_: b => b,
  activeSubscribers_: () => ['a', 'b', 'c'].map(e => ({Email: e + '@x', '取消訂閱權杖': 't', '訂閱項目': '每日總覽', '關注股票代號': ''})),
  getSheet_: name => name === '寄送帳本' ? ledgerSheet : pushSheet
});
// 沒有正式網址（/exec）時一封都不寄：寄出去的退訂按鈕會打不開（v54，Codex 規格 74）。
pushRows = [['日期', '文字稿', '寄送狀態'], ['2026/09/11', '第一版', '待寄送']];
mailProps.DAILY_PUSH_START_TODAY = '20260911|1400';
mctx.dailyPushJob();
assert.equal(mailSent.length, 0, '今日設定的最早時間之前不可寄送');
delete mailProps.DAILY_PUSH_START_TODAY;
mctx.dailyPushJob();
assert.equal(mailSent.length, 0, '沒有正式網址不能寄'); assert.equal(pushRows[1][2], '部分寄送');
ledgerRows.length = 1; delete mailProps.dailyPushSentDates;
mctx.WEBAPP_URL_DEFAULT = 'https://script.google.com/macros/s/AKfycbTESTDEPLOYMENTID/exec';
const pushHead = ['日期', '文字稿', '寄送狀態'];
pushRows = [pushHead, ['2026/09/11', '第一版', '待寄送']];
mctx.dailyPushJob();
assert.equal(mailSent.length, 3); assert.equal(pushRows[1][2], '已寄送');
assert(JSON.parse(mailProps.dailyPushSentDates)['2026/09/11']);
pushRows = [pushHead, ['2026/09/11', '第二版', '待寄送']];          // 整天重跑：列被刪掉重建
mctx.dailyPushJob(); mctx.dailyPushJob();
assert.equal(mailSent.length, 3); assert.equal(pushRows[1][2], '已寄送');
// 寄到一半額度出錯：已收到的人記在帳本，狀態「部分寄送」；下一棒只寄還沒收到的人。
today = '2026/09/14'; failAt = 5; pushRows = [pushHead, ['2026/09/14', '文章', '待寄送']];
mctx.dailyPushJob();
assert.equal(mailSent.length, 4); assert.equal(pushRows[1][2], '部分寄送');
failAt = 0; mctx.dailyPushJob();
assert.deepEqual(mailSent.slice(4), ['b@x', 'c@x'], '只續送沒收到的人'); assert.equal(pushRows[1][2], '已寄送');
mctx.dailyPushJob(); assert.equal(mailSent.length, 6, '寄完之後不再寄');
// 第一封就出錯：沒有人收到，下一棒照常從頭寄。
today = '2026/09/15'; failAt = 7; pushRows = [pushHead, ['2026/09/15', '文章', '待寄送']];
mctx.dailyPushJob();
assert.equal(mailSent.length, 6); assert.equal(pushRows[1][2], '部分寄送');
failAt = 0; mctx.dailyPushJob(); assert.equal(mailSent.length, 9); assert.equal(pushRows[1][2], '已寄送');
// 帳本：寄送中卻停住超過 15 分鐘的列算「結果不明」，不自動重寄。
const stuck = ledgerRows.find(r => r[0] === 'daily|2026/09/15' && r[4] === 'c@x');
stuck[5] = 'sending'; stuck[8] = '2026/09/15 10:00:00';
const led = mctx.deliveryLedger_('daily|2026/09/15', {kind: 'daily', date: '2026/09/15'});
assert.equal(led.state('c@x'), 'unknown');
// 沒有直播的日子不寄每日總覽（盤中即時通知另外照寄）。
today = '2026/09/24'; pushRows = [pushHead, ['2026/09/24', '只有簡訊生出的文章', '待寄送']];
mctx.readSheetObjects_ = name => name === '系統狀態' ? [{'時間': '2026/09/24 12:31:00', '類別': '今日無直播'}] : [];
assert(/今日無直播/.test(mctx.dailyPushJob())); assert.equal(mailSent.length, 9);
mctx.readSheetObjects_ = () => [];
// 上線前就寄過（試算表已寄送、寄送紀錄還沒有）：先補記，之後被改回待寄送也不寄。
today = '2026/09/16'; pushRows = [pushHead, ['2026/09/16', '文章', '已寄送']];
mctx.dailyPushJob(); pushRows[1][2] = '待寄送'; mctx.dailyPushJob();
assert.equal(mailSent.length, 9); assert.equal(pushRows[1][2], '已寄送');
// whyNoMail() 在編輯器執行曾丟 ReferenceError: chr is not defined（2026/09/11）：實際跑一次。
const whyText = mctx.whyNoMail();
assert(whyText.split('\n').length > 5); assert(whyText.includes('今天（2026/09/16）的推播狀態'));
assert(whyText.includes('第一封已在 2026/09/16 13:05:00 寄出'));
console.log(`PASS: ${syntaxCount} script syntax checks; evidence, durable day-sync, failure, cancellation, article and one-mail-per-day regression checks.`);
