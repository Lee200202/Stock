const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');
const root = path.join(__dirname, '..', 'public-site', 'gas-source');
for (const file of ['Presentationquality.gs', 'JavaScript.html']) {
  const source = fs.readFileSync(path.join(root, file), 'utf8');
  const helper = source.match(/\/\/ BEGIN PUBLIC NARRATIVE V10[\s\S]*?\/\/ END PUBLIC NARRATIVE V10/);
  assert(helper, `missing shared narrative helper: ${file}`);
  const ctx = vm.createContext({});
  vm.runInContext(helper[0], ctx);
  const row = { name: '達邁', code: '3645' };
  assert.strictEqual(ctx.publicNarrative_('已持有達邁者續抱即可。', row), '已持有達邁者續抱即可。');
  assert.strictEqual(ctx.publicNarrative_('已持友達邁者續抱即可。', row), '已持有達邁者續抱即可。');
  assert(ctx.publicNarrative_('你叫我們買有達？不是。', { name: '友達', code: '2409' }).includes('友達'));
}
console.log('GAS and frontend public narrative: alias boundary and stored text repaired');
