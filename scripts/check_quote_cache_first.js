// 2026/11/11：報價先讀快取。apiGetQuotesFor(codes, {cacheOnly:true}) 不在請求當下打 MIS；不帶 opts 時照舊即時補過期。
// 前端第一次只讀快取、仍過期的下一輪才即時請求。不連網。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dir = path.join(__dirname, '..', 'public-site', 'gas-source');
const api = fs.readFileSync(path.join(dir, 'API.gs'), 'utf8');
const src = api.slice(api.indexOf('function apiGetQuotesFor('), api.indexOf('/** 郵件查詢：列出所有已產生郵件內容的日期'));
const calls = [];
const ctx = vm.createContext({ withSheetSnapshot_: fn => fn(), getQuotesFor: (...a) => { calls.push(a.slice(1)); return {}; } });
vm.runInContext(src, ctx);
ctx.apiGetQuotesFor(['2330'], { cacheOnly: true });
ctx.apiGetQuotesFor(['2330'], {});
ctx.apiGetQuotesFor(['2330']);
assert.deepEqual(calls, [[false, true, true], [true, true, false], [true, true, false]]);
const js = fs.readFileSync(path.join(dir, 'JavaScript.html'), 'utf8');
const drain = js.slice(js.indexOf('function drainPageQuotes()'), js.indexOf('function refreshPageQuotes()'));
assert.match(drain, /cacheFirst \? \{ cacheOnly: true \} : \{\}/, '前端要把 cacheOnly 帶給後端');
assert.match(drain, /if \(cacheFirst\) quoteCacheTried\[c\] = true; else quoteAttempts\[c\]/, '快取試過一次就改走即時請求，不計入即時重試次數');
console.log('報價先讀快取：cacheOnly 不打 MIS、舊呼叫照舊、前端先快取後即時');
