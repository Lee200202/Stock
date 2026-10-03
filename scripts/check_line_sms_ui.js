// Run with: node scripts/check_line_sms_ui.js
// Exercise the consent path and the LINE Flex cards without contacting LINE.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'Line.gs'), 'utf8');
const ctx = vm.createContext({ console, Date, JSON, Math, Number, String, Object, Array });
vm.runInContext(source, ctx, { filename: 'Line.gs' });

let now = '2026/10/03 12:00:00';
let sub = { daily: true, sms: true, consentAt: '2026/09/27 12:00:00', consentVer: '' };
ctx.lineNow_ = () => now;
ctx.lineDeliveryBlock_ = () => '';
ctx.lineUpsertSub_ = (_uid, _channel, update) => {
  const before = { ...sub };
  update(sub);
  return { before, sub: { ...sub } };
};

function actions(message) {
  return message.contents.footer.contents.map(button => button.action);
}

assert.equal(ctx.lineParseText_('開啟盤中通知').a, 'secretsms');
assert.equal(ctx.lineParseText_('開啟盤中通知').on, true);

const pending = ctx.lineManageFlex_(sub);
const confirm = actions(pending).find(action => action.label === '確認開啟盤中通知');
assert.equal(confirm.type, 'message');
assert.equal(confirm.text, '開啟盤中通知');

const enabled = ctx.lineSecretSmsReply_('Utest', 'channel', true)[0];
assert.equal(enabled.type, 'flex');
assert.match(enabled.altText, /盤中即時通知已確認/);
assert.equal(sub.daily, true);
assert.equal(sub.consentVer, 'v1-2026-09-27:sms-keyword');
assert.equal(sub.consentAt, now);
assert(actions(enabled).some(action => action.type === 'message' && action.text === '關閉盤中通知'));

now = '2026/10/03 12:10:00';
const again = ctx.lineSecretSmsReply_('Utest', 'channel', true)[0];
assert.equal(sub.consentAt, '2026/10/03 12:00:00', 'repeat command preserves original consent time');
assert.match(JSON.stringify(again), /沒有重複建立訂閱或補發舊通知/);

const stopped = ctx.lineSecretSmsReply_('Utest', 'channel', false)[0];
assert.equal(sub.sms, false);
assert.equal(sub.daily, true);
assert.match(stopped.altText, /已停止/);
assert(actions(stopped).some(action => action.type === 'message' && action.text === '開啟盤中通知'));

ctx.lineDeliveryBlock_ = () => '推送目前暫停，管理者恢復後才會收到通知。';
const paused = ctx.lineSmsStateFlex_({ daily: true, sms: true }, true, false);
assert.match(JSON.stringify(paused), /設定已確認・推送暫停/);
assert(JSON.stringify(paused).length < 30000);

console.log('LINE intraday consent, repeat, stop, pending button, and Flex states OK');
