// 呈現層保留來源審核後的完整意思；公開紀錄價位與即時報價分開。
const fs=require('fs'),vm=require('vm'),assert=require('assert');
for(const name of ['JavaScript.html','Presentationquality.gs']){
 const source=fs.readFileSync('public-site/gas-source/'+name,'utf8');
 const fn=source.match(/function stripMetaClauses_\(text\) \{[\s\S]*?\n\}/);
 assert(fn,name);const ctx={};vm.createContext(ctx);vm.runInContext(fn[0],ctx);
 for(const text of [
  '群聯列為記憶體族群的主要觀察標的，但目前尚未止穩，暫不急於進場。',
  '甲公司曾於去年買進，今天續抱，並非今日再次買進。',
  '台達電已超跌，持有者不宜殺低；目前沒有新買進計畫。']){
  assert.equal(ctx.stripMetaClauses_(text),text,name+'不可依字詞刪除立場或時間');
 }
 assert(!source.includes('var META_CLAUSE_RE_ ='),name+'不可留方向與時間的刪句詞表');
}
const src=fs.readFileSync('public-site/gas-source/Presentationquality.gs','utf8');
const block=src.slice(src.indexOf('function publicScrubDeep_('),src.indexOf('/** 交易價留在價位欄'));
const ctx={scrubNoticeWords_:s=>s};vm.createContext(ctx);vm.runInContext(block,ctx);
assert.equal(ctx.publicScrubDeep_({code:'8210',price:'785元以下',reason:'已核對公開說明'}).price,'不公開');
assert.equal(ctx.publicScrubDeep_({quote:{code:'8210',price:775}}).quote.price,775);
assert.equal(ctx.publicScrubDeep_({code:'8210',reason:'上半年EPS21.83元'}).reason,'上半年EPS21.83元');
console.log('9項來源呈現、語意保留及公開價位邊界契約通過');
