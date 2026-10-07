/* Exact-name relevance must survive more than twelve partial matches. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('public-site/gas-source/Quoteservice.gs','utf8');
const fn = source.slice(source.indexOf('function suggestCodes('), source.indexOf('/* ------------------------------------------------------------------ *', source.indexOf('function suggestCodes(')));
const byCode = {};
for(let i=0;i<18;i++) byCode[String(1100+i)]={name:'國泰相關'+i,market:'上市'};
byCode['2882']={name:'國泰',market:'上市'};
byCode['9999']={name:'其他國泰相關',market:'上市'};
const ctx={loadCodeMap_:()=>({byCode}),PUBLIC_CONFIRMED_NAMES:{別名:['2882']}};
vm.createContext(ctx);vm.runInContext(fn,ctx);
assert.equal(ctx.suggestCodes('國泰')[0].code,'2882');
assert.equal(ctx.suggestCodes('2882')[0].name,'國泰');
assert.equal(ctx.suggestCodes('別名')[0].code,'2882');
assert.equal(ctx.suggestCodes('國泰').length,12);
assert.equal(ctx.suggestCodes('不存在').length,0);
assert.equal(ctx.suggestCodes(' ＊ 國泰 ')[0].code,'2882');
console.log('6 stock suggestion relevance checks passed');
