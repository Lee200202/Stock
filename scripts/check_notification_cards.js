// Render-only acceptance: no messages, requests, sheet writes or credentials.
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ctx={console,Date,JSON,Utilities:{formatDate:()=> '2026/10/14 15:40',getUuid:()=> 'fixture'},PropertiesService:{getScriptProperties:()=>({getProperty:()=>null})},ScriptApp:{getService:()=>({getUrl:()=> 'https://example.com/exec'})},CacheService:{getScriptCache:()=>({get:()=>null})},Logger:{log(){}},esc_:esc,escAttr_:esc,MailApp:{sendEmail(){throw Error('must never send');}},PUBLIC_SITE_URL_DEFAULT:'https://lee200202.github.io/Stock/'};
vm.createContext(ctx);for(const n of ['Presentationquality.gs','Evidencequality.gs','Articlequality.gs','Mailservice.gs','Cmoney.gs','Line.gs'])vm.runInContext(fs.readFileSync('public-site/gas-source/'+n,'utf8'),ctx);
const fixture='文章標題：原稿證據與條件核對的版面示例\n② 個股\n②-2 會員目前持有股票\n|股票|代號|目前立場|說明重點|\n|---|---|---|---|\n|測試甲|9991|會員持股|季線轉升，站上120元以上觀察量能；EPS 3.5元仍須核對。|\n|測試乙|9992|會員持股|回測80元以下觀察支撐，與100元附近的前高比較。|';
const html=ctx.mdToHtml_(fixture);assert.equal((html.match(/class="mc-table mc-stack mc-stock-card"/g)||[]).length,2);assert(!html.includes('border-top:1px solid'));assert(html.includes('price-upper'));assert(html.includes('price-lower'));assert(html.includes('price-reference'));
assert(!ctx.mailConditionPills_('EPS 3.5元以上、營收200元以下').includes('<span'));assert.equal(ctx.mailConditionPills_('1800～2000元附近'),'1800～2000元附近');
const text=ctx.mailConditionPills_('120元以上與80元以下').replace(/<[^>]+>/g,'');assert.equal(text,'120元以上與80元以下');
const unsafe=ctx.mdToHtml_(fixture+'\n<script>alert(1)</script>');assert(!unsafe.includes('<script>'));assert(unsafe.includes('&lt;script&gt;'));
const privateMail=ctx.cmMailBody_({time:'2026/10/14 09:30:00',text:'虛構通知：測試甲請於2010元之上賣出部分部位，1580元的部位續抱。'},false);assert(privateMail.includes('2010元之上'));assert(privateMail.includes('1580元'));assert(privateMail.includes('price-upper'));
const line=ctx.lineDailyRowBox_({name:'虛構測試甲',code:'9991',reason:'季線轉升，觀察回測支撐。'},'hold');assert.equal(line.cornerRadius,'20px');assert.equal(line.paddingAll,'20px');assert.equal(line.margin,'16px');assert.equal(line.backgroundColor,'#F5F3FF');assert.equal(line.contents[1].size,'md');
const out='scratch/ui180';fs.mkdirSync(out,{recursive:true});fs.writeFileSync(out+'/mail-fixture.html',ctx.mailShell_(html));fs.writeFileSync(out+'/private-fixture.html',ctx.mailShell_(privateMail));fs.writeFileSync(out+'/line-fixture.json',JSON.stringify(line,null,2));
console.log('two independent cards, unchanged words, three condition colors, EPS/range exclusion, escaping, private completeness and LINE spacing passed; rendered only');
