const assert=require('node:assert/strict');
const {makeWorld,taipei,DAY,CODES}=require('./check_quote_flow');
const w=makeWorld({}),codes=CODES.slice(0,12);let fetched=[];
w.ctx.getQuoteCache=()=>({});w.ctx.misBatchQuotes_=()=>({});w.ctx.hasFugle_=()=>true;w.ctx.lastCloseOf_=()=>null;
w.ctx.fugleQuote_=c=>{fetched.push(c);return {date:DAY,stamp:w.ctx.Utilities.formatDate(new w.ctx.Date(), 'Asia/Taipei','yyyy/MM/dd HH:mm:ss'),last:100,prevClose:99};};
for(let i=0;i<3;i++){w.clock.now=taipei(DAY,'09:'+String(10+i).padStart(2,'0')+':00');let q=w.ctx.getQuotesFor(codes,true,true);assert.equal(Object.keys(q).length,4*(i+1));}
assert.equal(fetched.length,12);assert.equal(new Set(fetched).size,12);
w.ctx.getQuoteCache=()=>({[codes[0]]:{date:DAY,stamp:DAY+' 09:12:00',last:120,prevClose:99}});
assert.equal(w.ctx.getQuotesFor([codes[0]],true,true)[codes[0]].last,120);
console.log('Batch fallback: 12 codes covered in 3 polls, 4 fetches each, no repeated first-code starvation; newer sheet quote wins');
