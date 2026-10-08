// v147: pure price rule and the rebuilt round's self-audit.
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'public-site', 'gas-source', 'SheetService.gs'), 'utf8');
function grab(name) {
  const start = src.indexOf('function ' + name + '(');
  assert(start >= 0, name);
  let depth = 0, end = src.indexOf('{', start);
  for (; end < src.length; end++) {
    if (src[end] === '{') depth++;
    else if (src[end] === '}' && --depth === 0) break;
  }
  return src.slice(start, end + 1);
}
const ctx = vm.createContext({});
vm.runInContext(['midPrice_', 'roundPrice_', 'midPriceAudit_'].map(grab).join('\n'), ctx);
const run = code => JSON.parse(JSON.stringify(vm.runInContext(code, ctx)));
assert.deepStrictEqual(run("roundPrice_({low:100,high:110},102,'entry')"), {price:102, stated:102});
assert.deepStrictEqual(run("roundPrice_({low:100,high:110},108,'entry')"), {price:105, stated:108});
assert.deepStrictEqual(run("roundPrice_({low:118,high:124},123,'exit')"), {price:123, stated:123});
assert.deepStrictEqual(run("roundPrice_({low:118,high:124},119,'exit')"), {price:121, stated:119});
assert.deepStrictEqual(run("roundPrice_({low:100,high:110},null,'entry')"), {price:105, stated:null});
assert.deepStrictEqual(run("roundPrice_({low:100,high:110},95,'entry')"), {price:105, stated:null});
assert.strictEqual(run('midPrice_({low:10.05,high:10.1})'), 10.08);
const good = [{entry:102,entryRange:{lo:100,hi:110},entryPriceHint:102,
               exit:123,exitRange:{lo:118,hi:124},exitPriceHint:123}];
assert.deepStrictEqual(run('midPriceAudit_("測試",'+JSON.stringify(good)+')'), {checked:2,mismatch:[]});
const bad = [{...good[0],exit:121}];
assert.strictEqual(run('midPriceAudit_("測試",'+JSON.stringify(bad)+')').mismatch.length,1);
assert.deepStrictEqual(run('midPriceAudit_("測試",'+JSON.stringify([{entry:99,entryOverridden:true,entryRange:{lo:100,hi:110}}])+')'), {checked:0,mismatch:[]});
assert(/rd\.entry = entryChoice\.price/.test(src) && /rd\.exit = exitChoice\.price/.test(src));
assert(/ep: rd\.entryPriceHint/.test(src) && /xp: rd\.exitPriceHint/.test(src));
console.log('v147 price formula, range guard, override and round audit passed');
