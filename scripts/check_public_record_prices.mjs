// 快照、新回覆及個股查詢使用同一公開邊界；不改後台原始價位或市場報價。
import assert from 'node:assert/strict';
import {publicRecordPayload} from '../site-api/worker.mjs';
const input={ok:true,result:{buy:[{code:8210,name:'勤誠',price:'785元以下',reason:'上半年EPS21.83元，假破底。'}],
 quote:{code:8210,price:775,reason:''}}};
for(const method of ['apiGetDashboard','apiSearchByDate','apiSearchStock','apiGetStockSummary']){
 const output=publicRecordPayload(method,input);
 assert.equal(output.result.buy[0].price,'不公開');
 assert.equal(output.result.buy[0].reason,input.result.buy[0].reason);
 assert.equal(output.result.quote.price,775);
 assert.equal(input.result.buy[0].price,'785元以下');
}
for(const method of ['apiAdminOpsDay','apiGetMemberSms','apiGetQuotesFor'])assert.equal(publicRecordPayload(method,input),input);
console.log('公開操作價位隱藏、EPS與行情保留、後台原文不改：19項契約通過');
