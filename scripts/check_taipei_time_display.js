#!/usr/bin/env node
// 同一個 UTC 時刻跨日、帶位移的 ISO、既有台灣文字都要正確呈現。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'public-site/gas-source/TimeDisplay.html'), 'utf8');
const script = source.match(/<script>([\s\S]*?)<\/script>/);
assert.ok(script, '共用時間腳本存在');
const context = { window: {}, Date, Intl, isFinite };
vm.runInNewContext(script[1], context);
const show = context.window.formatTaipeiTime;

assert.equal(show('2026-10-12T03:09:21Z'), '2026-10-12 11:09:21');
assert.equal(show('2026-10-11T16:00:00.123Z'), '2026-10-12 00:00:00');
assert.equal(show('2026-10-12T11:09:21+08:00'), '2026-10-12 11:09:21');
assert.equal(show('2026-10-12T11:09:21'), '2026-10-12 11:09:21');
assert.equal(show('2026/10/12 11:09:21'), '2026/10/12 11:09:21');
assert.equal(show('2026/10/12'), '2026/10/12');
assert.equal(show('2026-10-12T99:09:21Z'), '2026-10-12T99:09:21Z');
assert.equal(show(null), '');
console.log('台灣時間顯示：8 種時間格式與跨日情境通過');
