// 會員簡訊自動稽核補抓（2026/09/14）：輪詢漏掉的今日張震簡訊，自動補存、派解析、通知一次；不重複、不越時段。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');

function makeEnv(opt) {
  opt = opt || {};
  const clock = { now: Date.parse(opt.at || '2026-09-14T04:00:00Z') };      // 台北 12:00（週一）
  class FDate extends Date { constructor(...a) { a.length ? super(...a) : super(clock.now); } static now() { return clock.now; } }
  const props = Object.assign({ CMONEY_MEMBER_ID: '1230411' }, opt.props || {});
  const sheets = {
    '會員簡訊': { rows: [['文章ID', '發文時間', '標題', '原文', '解析狀態', '抓取時間', '通知狀態', '網址', '解析明細', '內容指紋', '最後偵測', '修訂次數', '解析版本', '判定時間']]
                .concat((opt.saved || []).map(id => [id, '2026/09/14 09:00:00', '', '舊', '已解析', '', '', '', '', '', '', 0, '', ''])) },
    '系統狀態': { rows: [['時間', '類別', '說明', '模式', '執行環境']] }
  };
  const sheetApi = name => {
    const s = sheets[name];
    return {
      getLastRow: () => s.rows.length, getLastColumn: () => s.rows[0].length, getMaxColumns: () => 30,
      insertColumnsAfter: () => {},
      getRange: (r, c, nr, nc) => ({
        getValues: () => s.rows.slice(r - 1, r - 1 + (nr || 1)).map(x => x.slice(c - 1, c - 1 + (nc || 1))),
        setValue: v => { s.rows[r - 1][c - 1] = v; }
      }),
      getDataRange: () => { if (opt.forbidFullRead && name === '會員簡訊') { throw new Error('不可整張讀會員簡訊'); } return { getValues: () => s.rows.map(x => x.slice()) }; },
      appendRow: row => s.rows.push(row)
    };
  };
  const pad = n => String(n).padStart(2, '0');
  const ctx = vm.createContext({ console, JSON, Math, Date: FDate, String, Number, Object, Array, RegExp, isNaN, Infinity,
    Logger: { log: () => {} },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null), setProperty: (k, v) => { props[k] = String(v); }, deleteProperty: k => { delete props[k]; } }) },
    Utilities: {
      formatDate: (d, tz, f) => { const x = new Date(new Date(d).getTime() + 8 * 3600e3);
        const m = { yyyy: x.getUTCFullYear(), MM: pad(x.getUTCMonth() + 1), dd: pad(x.getUTCDate()), HH: pad(x.getUTCHours()), mm: pad(x.getUTCMinutes()), ss: pad(x.getUTCSeconds()), u: x.getUTCDay() || 7 };
        return f.replace(/yyyy|MM|dd|HH|mm|ss|u/g, k => m[k]); },
      computeDigest: () => [1, 2, 3], DigestAlgorithm: { SHA_256: 1 }, Charset: { UTF_8: 1 }
    },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {} }) }
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Cmoney.gs'), 'utf8'), ctx);
  const calls = { dispatch: 0, notify: [], admin: [] };
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', getSheet_: sheetApi, withLock_: fn => fn(),
    todayStr_: () => ctx.Utilities.formatDate(new FDate(), 'Asia/Taipei', 'yyyy/MM/dd'),
    nowStamp_: () => '2026/09/14 12:00:00',
    cmGetGuestToken_: () => 'guest', cmFetchApiPage_: () => opt.api || [],
    cmDispatchGithubParse_: () => { calls.dispatch++; return { ok: true }; },
    cmNotifyNew_: a => calls.notify.push(a.id),
    notifyAdmin_: (subject, body) => calls.admin.push(subject)
  });
  return { ctx, clock, props, sheets, calls };
}
const post = (id, iso, text, creator) => ({ id, content: { creatorId: creator || '1230411', text, title: '' }, createTime: Date.parse(iso) });
const API = [post('184583191', '2026-09-14T03:44:06Z', '[啟發郭憲政-普]1 93元低佈多'),
             post('184582595', '2026-09-14T03:28:16Z', '張震-1:目前世芯超跌，不理會，一定會上4000元之上，抱牢'),
             post('184581780', '2026-09-14T03:03:07Z', '蘇麗芬:賀8431匯鑽科漲停')];

// 一、漏抓的今日張震簡訊：補存、派解析、寫系統狀態、通知管理者一次；發文超過 60 分鐘不寄會員即時通知
let env = makeEnv({ api: API, forbidFullRead: true, at: '2026-09-14T05:00:00Z' });   // 台北 13:00
let r = env.ctx.cmAutoReconcileToday_(false);
assert.equal(r.checked, 1); assert.equal(r.missing, 1); assert.equal(r.saved.length, 1);
const row = env.sheets['會員簡訊'].rows.find(x => x[0] === '184582595');
assert(row && row[4] === '待解析', '存成待解析，交給 GitHub 解析');
assert.equal(env.calls.dispatch, 1);
assert(env.sheets['系統狀態'].rows.some(x => /自動稽核補抓 1 篇/.test(x[2])));
assert.equal(env.calls.admin.length, 1);
assert.deepEqual(env.calls.notify, [], '13:00 才補到 11:28 的文章，超過 60 分鐘不寄會員即時通知');
assert(JSON.parse(env.props.cmSeenIds).includes('184582595'));

// 十分鐘內再叫：還沒到下一次；十分鐘後：已經在分頁裡，不重複存、不再派工、不再通知
r = env.ctx.cmAutoReconcileToday_(false); assert.equal(r.skipped, '還沒到下一次');
env.clock.now += 10 * 60000;
r = env.ctx.cmAutoReconcileToday_(false);
assert.equal(r.missing, 0); assert.equal(env.calls.dispatch, 1); assert.equal(env.calls.admin.length, 1);
assert.equal(env.sheets['會員簡訊'].rows.filter(x => x[0] === '184582595').length, 1);

// 二、發文 60 分鐘內補到的，照樣寄會員即時通知
env = makeEnv({ api: API, at: '2026-09-14T03:40:00Z' });
env.ctx.cmAutoReconcileToday_(false);
assert.deepEqual(env.calls.notify, ['184582595']);

// 三、已存過的不動；別人發的、不是今天的都不收
env = makeEnv({ api: API.concat([post('184545552', '2026-09-11T03:18:33Z', '張震-1:舊文')]), saved: ['184582595'] });
r = env.ctx.cmAutoReconcileToday_(false);
assert.equal(r.checked, 1); assert.equal(r.missing, 0); assert.equal(env.calls.dispatch, 0);

// 四、時段：週末、09:05 以前、21:00 以後不跑；15:30 之後每 60 分鐘
assert.equal(makeEnv({ api: API, at: '2026-09-13T04:00:00Z' }).ctx.cmAutoReconcileToday_(false).skipped, '不在稽核時段');
assert.equal(makeEnv({ api: API, at: '2026-09-14T01:00:00Z' }).ctx.cmAutoReconcileToday_(false).skipped, '不在稽核時段');
assert.equal(makeEnv({ api: API, at: '2026-09-14T13:30:00Z' }).ctx.cmAutoReconcileToday_(false).skipped, '不在稽核時段');
env = makeEnv({ api: API, at: '2026-09-14T08:00:00Z', saved: ['184582595'] });   // 16:00
env.ctx.cmAutoReconcileToday_(false); env.clock.now += 20 * 60000;
assert.equal(env.ctx.cmAutoReconcileToday_(false).skipped, '還沒到下一次');
env.clock.now += 45 * 60000;
assert.equal(env.ctx.cmAutoReconcileToday_(false).skipped, '');

// 五、流量上限時不抓；手動立即執行不看時段
env = makeEnv({ api: API, props: { cmFetchStat: JSON.stringify({ date: '2026/09/14', page: 0, art: 0, kb: 70 * 1024 }) } });
assert.equal(env.ctx.cmAutoReconcileToday_(false).skipped, '今日流量已達上限');
env = makeEnv({ api: API, at: '2026-09-13T04:00:00Z' });
assert.equal(env.ctx.cmoneyReconcileNow().checked, 0, '週六手動執行：來源上沒有「今天」的文章');

// 六、每五分鐘那一棒有掛上這一支
const setup = fs.readFileSync(path.join(root, 'apps-script/Setup.gs'), 'utf8');
assert(/safe_\('cmAutoReconcileToday_', function \(\) \{ cmAutoReconcileToday_\(false\); \}\);/.test(setup));
console.log('PASS: CMoney auto reconcile saves missed posts once, dispatches parse, alerts admin, respects window, budget and duplicates.');
