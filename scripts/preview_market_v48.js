// 真實 Lightweight Charts 4.1.3 + 正式模板；行情為明確的測試資料，不觸及遠端試算表。
const fs=require('fs'),path=require('path'),assert=require('assert'),{createRequire}=require('module');
const runtime=createRequire('C:/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/package.json');
const {chromium}=runtime('playwright'),out=path.resolve('docs/0920-v48/preview');fs.mkdirSync(out,{recursive:true});
let html=fs.readFileSync('apps-script/MarketDetail.html','utf8').replace('<?!= include(\'MarketCharts\'); ?>',fs.readFileSync('apps-script/MarketCharts.html','utf8')).replace(/<\?= symbol \?>/g,'taiex').replace(/<\?= webAppUrl \?>/g,'https://example.test/app');
html=html.replace(/<script src="[^"]+"><\/script>/,'<script>'+fs.readFileSync('.market-runtime/lightweight-charts-4.1.3.js','utf8').replace(/\$/g,'$$$$')+'</script>');
const fixture=`<script>
window.__requests=0;function run(fn){return {withSuccessHandler:run,withFailureHandler:function(){return this},apiGetMarketOverview:function(){window.__requests++;var rows=Array.from({length:100},function(_,i){var v=42000+i*40+Math.sin(i/5)*350;return [new Date(Date.UTC(2026,5,1+i)).toISOString().slice(0,10).replace(/-/g,'/'),v-30,v+120,v-150,v,10000+i*100];});setTimeout(function(){fn({at:'本機測試',cards:[{key:'taiex',label:'加權指數 · 模擬驗收',time:'2026/09/21 10:30',source:'測試資料',unit:'點',value:46000,change:100,percent:.2,candles:window.__noHistory?[]:rows,turnover:1e11,line:Array.from({length:19},function(_,i){return {time:'2026-09-21T'+String(9+Math.floor(i*5/60)).padStart(2,'0')+':'+String(i*5%60).padStart(2,'0')+':00+08:00',value:45000+i*20};})}]});},10)}}};window.google={script:{run:run()}};
</script>`;
html=html.replace('</head>','<meta name="viewport" content="width=device-width,initial-scale=1">'+fixture+'</head>');
fs.writeFileSync(path.join(out,'market-detail.html'),html);
(async()=>{const browser=await chromium.launch({channel:'msedge',headless:true}),results=[];
try{for(const width of [320,390,1440]){const page=await browser.newPage({viewport:{width,height:1000},deviceScaleFactor:1}),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('file:///'+path.join(out,'market-detail.html').replace(/\\/g,'/'));await page.waitForSelector('#priceChart canvas');await page.waitForTimeout(100);
assert.equal(await page.locator('[data-period=intraday]').getAttribute('aria-pressed'),'true');
await page.locator('[data-period=day]').click();await page.waitForTimeout(100);assert(await page.locator('#macdChart canvas').count());assert(await page.locator('#kdChart canvas').count());
const box=await page.locator('#priceChart').boundingBox();await page.mouse.move(box.x+box.width*.6,box.y+box.height*.5);await page.waitForTimeout(50);assert((await page.locator('#priceRead').textContent()).includes('開'));
await page.mouse.wheel(0,-120);await page.waitForTimeout(50);
await page.screenshot({path:path.join(out,`market-detail-${width}.png`),fullPage:true});
const size=await page.evaluate(()=>({w:innerWidth,scroll:document.documentElement.scrollWidth}));assert(size.scroll<=size.w+1,JSON.stringify(size));
await page.locator('[data-period=week]').click();await page.waitForTimeout(50);assert(await page.locator('#macdChart').isHidden());
await page.locator('[data-period=month]').click();await page.waitForTimeout(50);assert(await page.locator('#kdChart').isHidden());
assert.equal(await page.evaluate(()=>window.__requests),1,'週期切换不能重打行情');assert.deepEqual(errors,[]);results.push({width,overflow:false,chartErrors:errors,periodSwitch:true,crosshair:true});await page.close();}
}finally{await browser.close();}fs.writeFileSync(path.join(out,'market-detail-check.json'),JSON.stringify(results,null,2));console.log(results);})().catch(e=>{console.error(e);process.exit(1)});
