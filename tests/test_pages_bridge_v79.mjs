import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../public-site/original-bridge.js', import.meta.url), 'utf8');
let requests = 0;
const window = {};
const context = {
  window,
  location: { search: '', href: 'https://lee200202.github.io/Stock/' },
  document: { addEventListener() {}, body: { dataset: {} } },
  URL, URLSearchParams, AbortController, setTimeout, clearTimeout, console,
  fetch: async () => {
    requests++;
    return { ok: true, async json() { return { ok: true, result: { version: requests } }; } };
  }
};
vm.runInNewContext(source, context);
const call = (method, ...args) => new Promise((resolve, reject) => {
  window.google.script.run.withSuccessHandler(resolve).withFailureHandler(reject)[method](...args);
});

const [first, second] = await Promise.all([call('apiGetDashboard'), call('apiGetDashboard')]);
assert.equal(requests, 1, 'simultaneous identical dashboard reads share a request');
assert.equal(first.version, second.version);
await call('apiGetDashboard');
assert.equal(requests, 1, 'short-lived read result is reused');
await Promise.all([call('apiSubscribe', { email: 'a@example.com' }), call('apiSubscribe', { email: 'a@example.com' })]);
assert.equal(requests, 3, 'writes must never be cached or coalesced');
console.log('Pages bridge v79 request coalescing passed');
