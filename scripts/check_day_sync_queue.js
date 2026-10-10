// A completed GitHub lease must not hold later edits of the same day.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const source = fs.readFileSync('public-site/gas-source/Evidencequality.gs', 'utf8');
const properties = new Map();
const props = {
  getProperty: key => properties.get(key) || '',
  setProperty: (key, value) => properties.set(key, value),
};
const context = vm.createContext({
  PropertiesService: {getScriptProperties: () => props},
  Date, JSON,
});
vm.runInContext(source, context);
context.withLock_ = fn => fn();
context.fmtDate_ = value => value;
context.nowStamp_ = () => '2026/10/10 22:00:00';
let scheduled = 0;
context.scheduleDaySync_ = () => { scheduled += 1; };

function state(value) {
  properties.set('dayEditSyncV1', JSON.stringify(value));
  properties.set('dayEditQueueV47', JSON.stringify({'2026/10/08': 123}));
  scheduled = 0;
}

state({status: '處理中', githubUntil: Date.now() + 600000, running: false, updatedMs: Date.now()});
context.dayEditSyncTick_();
assert.equal(scheduled, 0);
assert.equal(JSON.parse(properties.get('dayEditQueueV47'))['2026/10/08'], 123);

state({status: '完成', githubUntil: Date.now() + 600000, running: false, updatedMs: Date.now()});
context.dayEditSyncTick_();
assert.equal(scheduled, 1);
assert.equal(JSON.parse(properties.get('dayEditSyncV1')).status, '處理中');
assert.deepEqual(JSON.parse(properties.get('dayEditQueueV47')), {});
console.log('completed lease releases the next day-edit sync; active lease remains protected');

const setup = fs.readFileSync('public-site/gas-source/Setup.gs', 'utf8');
const fiveMin = setup.slice(setup.indexOf('function everyFiveMinJobRun_()'), setup.indexOf('function ', setup.indexOf('function everyFiveMinJobRun_()') + 9));
const syncAt = fiveMin.indexOf("safe_('dayEditSyncTick_'");
assert(syncAt > 0);
assert(syncAt < fiveMin.indexOf('if (hhmm < 800 || hhmm >= 2230)'));
assert(syncAt < fiveMin.indexOf('if (closedToday)'));
assert.equal(fiveMin.indexOf("safe_('dayEditSyncTick_'", syncAt + 1), -1);
console.log('five-minute scheduler checks day edits before night and market-holiday exits');
