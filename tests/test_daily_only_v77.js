const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'API.gs'), 'utf8');
const subs = new Map();
let publicPayload;
const ctx = vm.createContext({
  createSubscription(p) { publicPayload = p; return {ok: true}; },
  adminAuth_(key) { if (key !== 'admin') throw new Error('unauthorized'); },
  withLock_(fn) { return fn(); },
  findSubscription_(email) { const row = subs.get(email); return row && {row, head: Object.keys(row)}; },
  writeSubscriptionFields_(hit, changes) { Object.assign(hit.row, changes); },
  getSheet_() { return {appendRow(row) { subs.set(row[0], {'Email': row[0], '訂閱項目': row[1], '狀態': row[5]}); }}; },
  Utilities: {getUuid() { return '12345678-1234-1234-1234-123456789abc'; }},
  todayStr_() { return '2026/09/28'; },
  fmtDate_(value) { return String(value || ''); },
  sendWelcomeMail_() {},
  Logger: {log() {}},
  smsDates_() { throw new Error('public sms leak'); },
  memberSmsData_() { throw new Error('public sms leak'); }
});
vm.runInContext(src, ctx);

assert.equal(ctx.apiSubscribe({email: 'a@example.com', daily: false, sms: true}).ok, true);
assert.equal(publicPayload.daily, true);
assert.equal(publicPayload.sms, false);
assert.throws(() => ctx.apiListSmsDates(), /unauthorized/);
assert.throws(() => ctx.apiGetMemberSms(1, ''), /unauthorized/);
assert.equal(ctx.apiAdminSetSmsEmail('bad', 'a@example.com', true, true).ok, false);
assert.equal(ctx.apiAdminSetSmsEmail('admin', 'a@example.com', true, false).ok, false);
subs.set('a@example.com', {'Email': 'a@example.com', '訂閱項目': '每日總覽', '狀態': '生效'});
assert.equal(ctx.apiAdminSetSmsEmail('admin', 'a@example.com', true, true).ok, true);
assert.match(subs.get('a@example.com')['訂閱項目'], /每日總覽/);
assert.match(subs.get('a@example.com')['訂閱項目'], /會員簡訊/);
assert.equal(Object.hasOwn(ctx.apiLookupSubscription('a@example.com'), 'sms'), false);
assert.equal(ctx.apiAdminSetSmsEmail('admin', 'a@example.com', false, false).ok, true);
assert.equal(subs.get('a@example.com')['訂閱項目'], '每日總覽');
assert.equal(subs.get('a@example.com')['狀態'], '生效');
console.log('daily-only v77 subscription and private SMS guards passed');
