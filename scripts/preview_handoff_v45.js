// Local browser fixture using actual templates, CSS, JS and mail renderer.
// All google.script.run requests are mocked; this cannot send mail or write Sheets.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const {createRequire}=require('module');
const runtime=createRequire('C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=runtime('playwright');
const root=path.resolve(__dirname,'..'),out=path.join(root,process.env.PREVIEW_OUTPUT || 'docs/0919-v45/preview');fs.mkdirSync(out,{recursive:true});
const gas=vm.createContext({console});
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/MailService.gs'),'utf8'),gas);
vm.runInContext(fs.readFileSync(path.join(root,'apps-script/Presentationquality.gs'),'utf8'),gas);
// 舊版人工示範只作排版 fixture；說明欄仍走本版的公開文字處理。
let section='';
const article=fs.readFileSync(path.join(root,'docs/0912/0911-郵件內容示範.md'),'utf8').split('\n').map(function(line){
  const heading=line.match(/[③④⑤⑥]/);if(heading)section=heading[0];
  if(line.startsWith('|') && !line.includes('---') && !line.includes('股票名稱')) {
    const cells=line.split('|');if(cells.length>=5){const row={name:cells[1].trim(),code:cells[2].trim()};cells[cells.length-2]=' '+gas.publicNarrative_(cells[cells.length-2],row)+' ';if(cells.length===6)cells[3]=' '+gas.displayPrice_(cells[3],'',cells[4])+' ';}
    return cells.join('|');
  }
  if((section==='③'||section==='⑤') && /^[・•-]/.test(line))return line[0]+' '+gas.publicNarrative_(line.slice(1));
  if(line.includes('① 文章標題'))return '① 文章標題：'+gas.articleTitle_({market:[{kind:'event',text:'量縮震盪，靜待CPI',headline:'量縮震盪，靜待CPI',_evidence_verified:true}]});
  return line;
}).join('\n');
assert(!/講者表示|張震表示|張正指出/.test(article));
const mail=gas.mailShell_(gas.mdToHtml_(article));
function template(name){
 return fs.readFileSync(path.join(root,'apps-script',name+'.html'),'utf8')
  .replace(/<\?!= include\('([^']+)'\); \?>/g,(_,name)=>template(name))
  .replace(/<\?= webAppUrl \?>/g,'#').replace(/<\?= disclaimer \?>/g,'本機版面預覽，內容依原稿獨立整理。')
  .replace(/<\?[\s\S]*?\?>/g,'');
}
const fixture=`<script>
window.__previewMail=${JSON.stringify(mail).replace(/</g,'\u003c')};
window.__previewApiCalls=[];
function previewRunner(success,failure){return new Proxy({}, {get:function(_,name){
 if(name==='withSuccessHandler')return function(fn){return previewRunner(fn,failure)};
 if(name==='withFailureHandler')return function(fn){return previewRunner(success,fn)};
 return function(){var args=Array.from(arguments);window.__previewApiCalls.push(String(name));
  var value={};
  if(name==='apiGetDashboard')value={today:{hasData:false},updatedAt:'本機預覽'};
  else if(name==='apiGetHoldingsTracker')value={summary:null,rows:[],note:'本機預覽'};
  else if(name==='apiGetMemberSms')value={items:[]};
  else if(/^apiList/.test(name))value=['2026/09/10','2026/09/11'];
  else if(name==='apiGetMailContent')value={found:true,date:args[0],subject:'張震：'+args[0]+' 盤勢與操作紀錄',sent:'預覽，未寄送',html:window.__previewMail};
  else if(name==='apiSearchByDate')value={found:false,date:args[0]};
  else if(name==='apiGetStockHeader')value={code:args[0],name:args[0]==='2330'?'台積電':'主動統一台股增長'};
  else if(name==='apiLogUsage')return;
  else if(failure){setTimeout(function(){failure(new Error('本機預覽未連線'))},0);return;}
  setTimeout(function(){if(success)success(value)}, args[0]==='2026/09/10'||args[0]==='2330'?120:10);
 };
}})}
window.google={script:{run:previewRunner()}};
</script>`;
let html=template('Index').replace('</head>','<meta name="viewport" content="width=device-width,initial-scale=1">'+fixture+'</head>');
// 測試專用入口；不改正式前端的作用域或暴露內部函式。
html=html.replace('  function showDay(date) {','  window.__v6ShowDay=showDay;\n  function showDay(date) {')
 .replace('  function openDetail(code) {','  window.__v6OpenDetail=openDetail;\n  function openDetail(code) {')
 .replace('  function loadPerf() {','  window.__v6LoadPerf=loadPerf;\n  function loadPerf() {');
html=html.replace('<body>','<body><div style="padding:8px 20px;background:#e5eee8;color:#19392b;font-size:13px">本機版面預覽 · 使用實際前端程式與獨立整理示範 · 未連接正式資料</div>');
fs.writeFileSync(path.join(out,'index.html'),html);
fs.writeFileSync(path.join(out,'mail.html'),'<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>郵件 HTML 預覽</title><body style="margin:24px">'+mail+'</body></html>');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});const results=[];
 for(const [label,width,height] of [['desktop',1440,1000],['mobile',390,844],['narrow',320,740]]){
  const page=await browser.newPage({viewport:{width,height},reducedMotion:'reduce'});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.route('https://**/*',r=>r.abort());
  await page.setContent(html,{waitUntil:'domcontentloaded'});
  await page.locator('[data-tab="mail"]').click();
  await page.locator('#calGrid button').filter({hasText:/^11$/}).click();
  await page.waitForFunction(()=>document.getElementById('mailContent').textContent.includes('會員目前持有股票'));
  // Actual date-switch handlers, with deliberately reversed response timing.
  await page.locator('#calGrid button').filter({hasText:/^10$/}).click();
  await page.locator('#calGrid button').filter({hasText:/^11$/}).click();
  await page.waitForTimeout(180);assert((await page.locator('#mailSubj').textContent()).includes('2026/09/11'));
  const dimensions=await page.evaluate(()=>({viewport:innerWidth,body:document.documentElement.scrollWidth,
   mail:document.getElementById('mailContent').getBoundingClientRect().width,
   tableBoxes:[...document.querySelectorAll('.mail-table-scroll')].map(e=>({width:e.clientWidth,scroll:e.scrollWidth})),
   animation:getComputedStyle(document.querySelector('#p-mail')).animationName}));
  assert(dimensions.body<=width+1,JSON.stringify(dimensions));assert.equal(dimensions.animation,'none');
  assert(await page.locator('#mailContent .mc-table').count() >= 3);
  const missing=await page.locator('#mailContent td').filter({hasText:/^未說明$/}).evaluateAll(nodes=>nodes.map(e=>({wrap:getComputedStyle(e).whiteSpace,height:e.getBoundingClientRect().height})));
  assert(missing.length>0);assert(missing.every(e=>e.wrap==='nowrap'));
  dimensions.missingPriceNoWrap=missing.length;
  await page.locator('#mailBody').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,label+'-mail.png'),fullPage:true});
  await page.locator('#mailContent table').first().scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(out,label+'-table.png')});
  await page.evaluate(()=>document.documentElement.dataset.theme='dark');
  const ink=await page.evaluate(()=>({article:getComputedStyle(document.querySelector('.mail-shell')).color,
    expected:getComputedStyle(document.getElementById('mailContent')).color}));
  assert.equal(ink.article,ink.expected);
  await page.screenshot({path:path.join(out,label+'-dark.png')});
  await page.emulateMedia({reducedMotion:'no-preference'});
  assert.equal(await page.locator('#p-mail').evaluate(e=>getComputedStyle(e).animationName),'rd-fade');
  await page.evaluate(()=>{__v6ShowDay('2026/09/10');__v6ShowDay('2026/09/11');__v6OpenDetail('2330');__v6OpenDetail('00981A');__v6LoadPerf();});
  await page.waitForTimeout(180);
  assert((await page.locator('#dateResult').textContent()).includes('2026/09/11'));
  assert.equal(await page.locator('#dName').textContent(),'主動統一台股增長');
  assert(!(await page.locator('#perfChart').evaluate(e=>e.classList.contains('is-loading'))));
  assert((await page.locator('#periodNote').textContent()).includes('載入失敗'));
  await page.locator('#detailClose').click();
  await page.locator('[data-tab="tech"]').click();
  for(let i=0;i<6;i++){
    await page.locator('#techSteps [data-step="'+i+'"]').click();
    assert(await page.locator('#tech-chunk-'+i).isVisible());
    const over=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    assert(!over,label+' tech '+i+' overflow');
  }
  await page.locator('#techSteps [data-step="0"]').click();
  await page.screenshot({path:path.join(out,label+'-tech.png'),fullPage:true});
  assert(await page.locator('.tech-fixed .author').isVisible());
  for(const tab of ['overview','tracker','perf','subscribe','sms','tx']){
    await page.locator('[data-tab="'+tab+'"]').click();
    await page.waitForTimeout(30);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),label+' '+tab+' overflow');
  }
  assert.deepEqual(errors,[]);results.push({label,dimensions,errors});await page.close();
 }
 const adminPage=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'reduce'}),adminErrors=[];
 adminPage.on('pageerror',e=>adminErrors.push(e.message));await adminPage.route('https://**/*',r=>r.abort());
 const adminHtml=template('Admin').replace('</head>','<meta name="viewport" content="width=device-width,initial-scale=1">'+fixture+'</head>');
 await adminPage.setContent(adminHtml,{waitUntil:'domcontentloaded'});
 await adminPage.evaluate(()=>{
  document.getElementById('loginWrap').style.display='none';document.getElementById('app').style.display='block';
  document.querySelectorAll('.panel').forEach(e=>e.hidden=e.id!=='p-maint');
  CHAIN_NAMES=['同步郵件內容','代號比對','重算持股追蹤','重算績效歷史','記錄績效'];
  renderChain({index:3,name:'重算績效歷史',status:'待補資料',error:'日K快取還沒有資料；補齊後從這一步接續。'});
 });
 await adminPage.locator('#refProg').scrollIntoViewIfNeeded();
 assert.equal(await adminPage.locator('#refBar').evaluate(e=>e.parentElement.getAttribute('aria-valuenow')),'60');
 assert(await adminPage.locator('#refCancelBtn').isDisabled());
 await adminPage.screenshot({path:path.join(out,'admin-progress.png')});
 assert.deepEqual(adminErrors,[]);results.push({label:'admin-pending',percent:60,errors:adminErrors});
 for(const status of ['待複核','等待日K']){
  await adminPage.evaluate(status=>{
   document.querySelectorAll('.panel').forEach(e=>e.hidden=e.id!=='p-post');
   renderJob({status,step:status==='待複核'?'完成':'刷新網站',done:13,total:15,
    note:status==='待複核'?'這一次沒有覆蓋，保留舊資料，待複核2項':'文章已更新；日K已補8/221檔，等待自動續跑',startedAt:'2026/09/13',updatedAt:'2026/09/13'});
   stopPoll();
  },status);
  await adminPage.locator('#progCard').scrollIntoViewIfNeeded();
  assert.equal(await adminPage.locator('#barFill').evaluate(e=>e.style.background),'rgb(183, 121, 31)');
  if(status==='等待日K') {
   assert(await adminPage.locator('#publishState').isVisible());
   assert((await adminPage.locator('#publishState').textContent()).includes('已更新（100%）'));
   assert((await adminPage.locator('#publishState').textContent()).includes('尚未完成'));
  } else { assert(!(await adminPage.locator('#publishState').isVisible())); }
  await adminPage.screenshot({path:path.join(out,status==='待複核'?'admin-review-warning.png':'admin-dailyk-waiting.png')});
  results.push({label:status,warning:true});
 }
 for(const width of [390,320]){
  await adminPage.setViewportSize({width,height:844});
  await adminPage.evaluate(()=>{renderJob({status:'等待續跑',step:'刷新網站',done:12,total:15,note:'資料已寫入；同步暫時未完成'});stopPoll();});
  assert(!(await adminPage.locator('#publishState').textContent()).includes('100%'));
  await adminPage.locator('#progCard').scrollIntoViewIfNeeded();
  assert(await adminPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'admin overflow '+width);
  await adminPage.screenshot({path:path.join(out,'admin-'+width+'-progress.png'),fullPage:true});
  const clips=await adminPage.locator('#steps .step').evaluateAll(nodes=>nodes.filter(n=>n.scrollWidth>n.clientWidth+1).map(n=>n.textContent));
  assert.deepEqual(clips,[],'階段名稱不可溢出');
  results.push({label:'admin-'+width,overflow:false,stepClips:clips});
 }
 fs.writeFileSync(path.join(out,'admin.html'),adminHtml);
 await adminPage.close();
 await browser.close();fs.writeFileSync(path.join(out,'browser-checks.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1)});

