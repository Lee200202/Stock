// 收盤後的修訂（2026/09/16）：輪詢只在 09:00–15:00，稽核那一棒要能發現「已編輯」並立刻通知。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');

function makeEnv(opt) {
  opt = opt || {};
  const clock = { now: Date.parse(opt.at || '2026-09-16T09:30:00Z') };      // 台北 17:30（收盤後）
  class FDate extends Date { constructor(...a) { a.length ? super(...a) : super(clock.now); } static now() { return clock.now; } }
  const props = Object.assign({ CMONEY_MEMBER_ID: '1230411' }, opt.props || {});
  const head = ['文章ID', '發文時間', '標題', '原文', '解析狀態', '抓取時間', '通知狀態', '網址',
                '解析明細', '內容指紋', '最後偵測', '修訂次數', '解析版本', '判定時間'];
  const sheets = {
    '會員簡訊': { rows: [head.slice()].concat((opt.saved || []).map(r =>
      [r.id, '2026/09/16 11:00:00', '', r.text, '已解析', '', '', '', '', r.hash || '', '', 0, 'v1', ''])) },
    '系統狀態': { rows: [['時間', '類別', '說明', '模式', '執行環境']] },
    '使用者訂閱清單': { rows: [['Email', '狀態', '訂閱項目', '取消訂閱權杖'], ['a@b.co', '啟用', '會員簡訊', 't1']] }
  };
  const sheetApi = name => {
    const s = sheets[name];
    return {
      getLastRow: () => s.rows.length, getLastColumn: () => s.rows[0].length, getMaxColumns: () => 30,
      insertColumnsAfter: () => {},
      getRange: (r, c, nr, nc) => ({
        getValues: () => s.rows.slice(r - 1, r - 1 + (nr || 1)).map(x => x.slice(c - 1, c - 1 + (nc || 1))),
        setValue: v => { s.rows[r - 1][c - 1] = v; }, setNote: () => {}
      }),
      getDataRange: () => { fullReads.push(name); return { getValues: () => s.rows.map(x => x.slice()) }; },
      appendRow: row => s.rows.push(row)
    };
  };
  const fullReads = [];
  const pad = n => String(n).padStart(2, '0');
  const ctx = vm.createContext({ console, JSON, Math, Date: FDate, String, Number, Object, Array, RegExp, isNaN, Infinity,
    Logger: { log: () => {} },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => (k in props ? props[k] : null),
      setProperty: (k, v) => { props[k] = String(v); }, deleteProperty: k => { delete props[k]; } }) },
    Utilities: {
      formatDate: (d, tz, f) => { const x = new Date(new Date(d).getTime() + 8 * 3600e3);
        const m = { yyyy: x.getUTCFullYear(), MM: pad(x.getUTCMonth() + 1), dd: pad(x.getUTCDate()),
                    HH: pad(x.getUTCHours()), mm: pad(x.getUTCMinutes()), ss: pad(x.getUTCSeconds()), u: x.getUTCDay() || 7 };
        return f.replace(/yyyy|MM|dd|HH|mm|ss|u/g, k => m[k]); },
      computeDigest: (alg, text) => Array.from(String(text)).map(c => c.charCodeAt(0) % 97),
      DigestAlgorithm: { SHA_256: 1 }, Charset: { UTF_8: 1 },
      base64EncodeWebSafe: b => b.join('-').slice(0, 24)
    },
    CacheService: { getScriptCache: () => ({ get: () => null, put: () => {} }) }
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'apps-script/Cmoney.gs'), 'utf8'), ctx);
  const calls = { dispatch: 0, mail: [], notes: [] };
  Object.assign(ctx, {
    TZ: 'Asia/Taipei', getSheet_: sheetApi, withLock_: fn => fn(),
    todayStr_: () => '2026/09/16', nowStamp_: () => '2026/09/16 17:30:00',
    cmGetGuestToken_: () => 'guest', cmFetchApiPage_: () => opt.api || [],
    cmDispatchGithubParse_: () => { calls.dispatch++; return { ok: true }; },
    notifyAdmin_: () => {},
    activeSubscribers_: () => [{ Email: 'a@b.co', '訂閱項目': '會員簡訊', '取消訂閱權杖': 't1' }],
    wrapMail_: b => b, unsubscribeUrl_: () => '#', DISCLAIMER: '免責',
    esc_: v => String(v == null ? '' : v),
    MailApp: { sendEmail: m => calls.mail.push(m) }
  });
  ctx.cmNote_ = msg => calls.notes.push(msg);
  return { ctx, calls, sheets, fullReads, props };
}
const post = (id, iso, text) => ({ id, content: { creatorId: '1230411', text, title: '' }, createTime: Date.parse(iso) });

const ORIGINAL = '張震-1:今天台指期結算，不做任何動作，等待觀望！';
const EDITED = '張震-1:今天台指期結算，明天美國聯準會利率決策，不做任何動作，不加碼也不賣股，等待觀望！';

// 一、收盤後被編輯：稽核那一棒發現、寄出修訂通知、排回待解析並派工
let env = makeEnv({ api: [post('184600001', '2026-09-16T03:00:00Z', EDITED)], saved: [{ id: '184600001', text: ORIGINAL, hash: 'OLD' }] });
env.ctx.cmFingerprint_ = t => (String(t) === ORIGINAL ? 'OLD' : 'NEW');
let r = env.ctx.cmAutoReconcileToday_(false);
assert.deepEqual(r.revised, ['184600001'], '收盤後的修訂要被發現');
assert.equal(env.calls.mail.length, 1);
assert(/會員通知內容已修訂/.test(env.calls.mail[0].subject), '寄的是修訂通知');
assert.equal(env.calls.dispatch, 1, '排回待解析並重新派工');
const row = env.sheets['會員簡訊'].rows[1];
assert.equal(row[3], EDITED, '原文換成編輯後的');
assert.equal(row[4], '待解析（文章已修訂）');
assert.equal(row[11], 1, '修訂次數 +1');

// 二、沒有改過就什麼都不做，而且不整張讀會員簡訊（整張讀是輪詢停掉的嫌疑）
env = makeEnv({ api: [post('184600001', '2026-09-16T03:00:00Z', ORIGINAL)], saved: [{ id: '184600001', text: ORIGINAL, hash: 'OLD' }] });
env.ctx.cmFingerprint_ = t => (String(t) === ORIGINAL ? 'OLD' : 'NEW');
r = env.ctx.cmAutoReconcileToday_(false);
assert.deepEqual(r.revised, []);
assert.equal(env.calls.mail.length, 0);
assert.equal(env.calls.dispatch, 0);
assert(!env.fullReads.includes('會員簡訊'), '沒改過的時候不整張讀');

// 三、舊列沒有指紋：不當成修訂，免得收盤後平白寄一封
env = makeEnv({ api: [post('184600001', '2026-09-16T03:00:00Z', EDITED)], saved: [{ id: '184600001', text: ORIGINAL, hash: '' }] });
r = env.ctx.cmAutoReconcileToday_(false);
assert.deepEqual(r.revised, []);
assert.equal(env.calls.mail.length, 0);
console.log('PASS: after-hours edits are detected by the reconcile pass, notified once, and cost only two column reads when nothing changed.');
