// 2026/10/05：公開價位規則與隱藏版通知分離，補送只給原通知已接受者。
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const dir = path.join(__dirname, '..', 'public-site', 'gas-source');
const read = name => fs.readFileSync(path.join(dir, name), 'utf8');
const raw = '張震-1:請於955元以上獲利賣出5536聖暉，資金轉為880元以下市價買進8210勤誠！';

const p = vm.createContext({ console });
vm.runInContext(read('Presentationquality.gs'), p);
assert(!/955|880/.test(p.publicNoticeText_(raw)));
assert(p.publicNoticeText_(raw).includes('確認完整標的與條件'));

const c = vm.createContext({ console, Logger: { log() {} } });
vm.runInContext(read('Cmoney.gs'), c);
c.esc_ = s => String(s); c.publicSiteUrl_ = () => 'https://lee200202.github.io/Stock/';
const a = { id: '185117227', time: '2026/10/05 09:41:55', text: raw, url: 'https://www.cmoney.tw/forum/article/185117227' };
const html = c.cmMailBody_(a, false, true);
assert(html.includes(raw) && html.includes('文字補充') && html.includes('並非新的操作指示'));
assert(!html.includes('提到價位的句子不顯示'));

const sent = [];
c.CM_SHEET = '會員簡訊'; c.DELIVERY_SHEET_ = '寄送帳本';
c.readSheetObjects_ = sheet => sheet === '會員簡訊'
  ? [{ '文章ID': a.id, '發文時間': a.time, '原文': raw, '網址': a.url, '標題': '僅供參考！' }]
  : [{ '訊息ID': 'sms|' + a.id, '內容版本': 'v1', '收件者': 'a@example.com', '狀態': 'accepted' },
     { '訊息ID': 'sms|' + a.id, '內容版本': 'v1', '收件者': 'b@example.com', '狀態': 'accepted' },
     { '訊息ID': 'sms|' + a.id, '內容版本': 'v1', '收件者': 'c@example.com', '狀態': 'unknown' }];
c.cmDate_ = () => '2026/10/05'; c.todayStr_ = () => '2026/10/05'; c.cmFmtTime_ = x => x;
c.deliveryVersion_ = () => 'v1'; c.cmSubscribers_ = () => ['a', 'b', 'c'].map(x => ({ Email: x + '@example.com', '取消訂閱權杖': x }));
c.cmMailQuotaLeft_ = () => ({ left: 10, st: {} }); c.siteName_ = () => '盤勢有據';
c.cmSmsPreheader_ = () => raw; c.wrapMail_ = body => body;
c.deliverMessage_ = (subs, options) => { sent.push({ subs, options }); return { accepted: subs.length, total: subs.length, fresh: subs.length, open: 0, unknown: 0 }; };
c.cmMailCount_ = () => {}; c.lineQueueSmsTextCorrection_ = () => ({ accepted: 1, total: 1 }); c.cmNote_ = () => {};
const result = c.resendTodaySmsTextCorrection();
assert.strictEqual(result.mail.accepted, 2);
assert.deepStrictEqual(sent[0].subs.map(s => s.Email), ['a@example.com', 'b@example.com']);
assert.strictEqual(sent[0].options.messageId, 'sms|185117227|text-correction|v1');
assert(sent[0].options.html('a@example.com', 'a').includes(raw));
let sender = 's711333105@gm.ntpu.edu.tw';
c.Session = { getEffectiveUser: () => ({ getEmail: () => sender }) };
assert.throws(() => c.resendTodaySmsFromOwner(), /正式寄件身分不是/);
assert.strictEqual(sent.length, 1);
sender = 'rainforecast2026@gmail.com';
c.lineQueueSmsTextCorrection_ = () => { throw Error('owner correction must not resend LINE'); };
const ownerResult = c.resendTodaySmsFromOwner();
assert.strictEqual(ownerResult.mail.accepted, 2);
assert.strictEqual(ownerResult.line.skipped, 'email-only');
assert.strictEqual(sent[1].options.messageId, 'sms|185117227|sender-correction|v1');
assert.deepStrictEqual(sent[1].subs.map(s => s.Email), ['a@example.com', 'b@example.com']);

const l = vm.createContext({ console, Logger: { log() {} } });
vm.runInContext(read('Line.gs'), l);
l.lineConfigured_ = () => true; l.linePushMode_ = () => 'live';
l.lineLedgerFor_ = id => ({ rows: id === 'sms|185117227'
  ? [{ uid: 'u1', state: 'accepted' }, { uid: 'u2', state: 'unknown' }] : [] });
l.lineParseTaipei_ = () => Date.now() - 60000; l.LINE_SMS_WINDOW_MIN_ = 60;
l.deliveryVersion_ = () => 'v1'; l.fmtDate_ = () => '2026/10/05';
l.lineSmsFlex_ = () => ({ type: 'flex', altText: raw, contents: {} });
let queued, ledger;
l.lineQueue_ = msg => { queued = msg; return { created: true, id: msg.id }; };
l.lineLedgerCreate_ = (msg, users) => { ledger = users; };
l.lineDeliverTick_ = () => ({ accepted: 1 });
l.lineOutboxRead_ = () => ({ rows: [{ id: queued.id, state: 'done', accepted: 1, total: 1 }] });
const lr = l.lineQueueSmsTextCorrection_(a);
assert.strictEqual(lr.accepted, 1);
assert.deepStrictEqual(ledger.map(s => s.uid), ['u1']);
assert.strictEqual(queued.id, 'sms|185117227|text-correction|v1');
console.log('PASS: public text remains price-free; private Email/LINE contain full source; correction targets only original accepted recipients.');
