/* Worker 的批次報價轉送（/quote-relay）。上游用假的 fetch，不連線。
   node scripts/check_quote_relay.mjs */
import assert from 'node:assert/strict';
import worker, {quoteRelay} from '../site-api/worker.mjs';

const TOKEN = 't'.repeat(40), env = {SITE_BRIDGE_TOKEN: TOKEN, GAS_WEBAPP_URL: 'https://script.google.com/macros/s/x/exec'};
const req = (query, headers = {'X-Bridge-Token': TOKEN}, method = 'GET') =>
  new Request('https://example.workers.dev/quote-relay' + query, {method, headers});
const calls = [];
const upstream = (body, status = 200) => async (url, opt) => { calls.push({url, opt}); return new Response(typeof body === 'string' ? body : JSON.stringify(body), {status}); };
const good = {msgArray: [{c: '2330', n: '台積電', z: '2585.0000', y: '2575.0000', d: '20261006', t: '13:30:00', v: '18343', o: '2575', h: '2590', l: '2565', secret: 'x', a: '1_2_3'}, {z: '-'}]};
const call = async (r, f = upstream(good)) => { const res = await quoteRelay(r, env, f); return {status: res.status, body: await res.json(), cache: res.headers.get('Cache-Control')}; };

// 一、權杖：沒有、不對、長度不同、Worker 沒設定，一律 403，而且不碰上游。
for (const headers of [{}, {'X-Bridge-Token': 'wrong'}, {'X-Bridge-Token': 'u'.repeat(40)}, {'X-Bridge-Token': TOKEN + 'x'}]) {
  assert.equal((await call(req('?ex_ch=tse_2330.tw', headers))).status, 403);
}
assert.equal((await quoteRelay(req('?ex_ch=tse_2330.tw'), {SITE_BRIDGE_TOKEN: 'short'}, upstream(good))).status, 403);
assert.equal(calls.length, 0, '驗證沒過不能連上游');

// 二、參數只能是 tse_／otc_ 代號清單，最多 120 個；其他一律 400，不碰上游。
for (const bad of ['', '?ex_ch=', '?ex_ch=https://evil.example/', '?ex_ch=tse_2330.tw%26x=1', '?ex_ch=tse_2330.tw|', '?ex_ch=nyse_AAPL.us',
                   '?ex_ch=tse_23.tw', '?ex_ch=' + Array.from({length: 121}, (_, i) => 'tse_' + (1000 + i) + '.tw').join('|')]) {
  assert.equal((await call(req(bad))).status, 400, bad);
}
assert.equal((await call(req('?ex_ch=tse_2330.tw', undefined, 'POST'))).status, 405);
assert.equal(calls.length, 0);

// 三、正常：上游網址固定是證交所 MIS，回傳只留報價欄位，不快取。
const many = Array.from({length: 120}, (_, i) => (i % 2 ? 'otc_' : 'tse_') + (1000 + i) + '.tw').join('|');
const ok = await call(req('?ex_ch=' + encodeURIComponent('tse_2330.tw|otc_2330.tw|tse_00981A.tw')));
assert.equal(ok.status, 200);
assert.deepEqual(ok.body, {ok: true, msgArray: [{c: '2330', n: '台積電', z: '2585.0000', y: '2575.0000', d: '20261006', t: '13:30:00', v: '18343', o: '2575', h: '2590', l: '2565'}]});
assert.equal(ok.cache, 'no-store');
assert.equal((await call(req('?ex_ch=' + encodeURIComponent(many)))).status, 200);
assert.equal(calls.length, 2);
for (const c of calls) {
  const u = new URL(c.url);
  assert.equal(u.origin + u.pathname, 'https://mis.twse.com.tw/stock/api/getStockInfo.jsp');
  assert.equal(c.opt.headers['X-Bridge-Token'], undefined, '權杖不能轉給上游');
}
assert.equal(new URL(calls[0].url).searchParams.get('ex_ch'), 'tse_2330.tw|otc_2330.tw|tse_00981A.tw');

// 四、上游失敗：502，內容不外洩。
assert.deepEqual((await call(req('?ex_ch=tse_2330.tw'), upstream('<html>busy</html>', 503))).body, {ok: false, error: 'upstream', status: 503});
assert.equal((await call(req('?ex_ch=tse_2330.tw'), upstream('<html>not json</html>'))).body.error, 'upstream-format');
assert.equal((await call(req('?ex_ch=tse_2330.tw'), async () => { throw new Error('connect failed'); })).body.error, 'upstream-unreachable');
assert.deepEqual((await call(req('?ex_ch=tse_2330.tw'), upstream({}))).body, {ok: true, msgArray: []});

// 五、路由：/quote-relay 走轉送（沒帶權杖是 403，不是 404）；其他路徑照舊。
assert.equal((await worker.fetch(new Request('https://example.workers.dev/quote-relay?ex_ch=tse_2330.tw'), env, {})).status, 403);
assert.equal((await worker.fetch(new Request('https://example.workers.dev/nope'), env, {})).status, 404);
const health = await (await worker.fetch(new Request('https://example.workers.dev/healthz'), env, {})).json();
assert.equal(health.ok, true); assert.match(health.build, /^site-api-v134/);
console.log('quote relay: token, channel allowlist, fixed upstream, trimmed fields, upstream failures, routing — all passed');
