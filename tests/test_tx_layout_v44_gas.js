// v44（2026/09/17）：逐字稿排版在 Apps Script 這一邊。
// 9/17 手動投稿之後逐字稿分頁只剩一整段「這一段」、按鈕顯示「智慧排版」——工作流沒有排成，也沒有任何東西會再去排。
// 現在：模型只回分段位置與小標（不抄原文）、存回試算表、每 15 分鐘補排、指紋與 pipeline 相同、中文字間空白拿掉。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert'), crypto = require('crypto');
const root = path.resolve(__dirname, '..');
const read = f => fs.readFileSync(path.join(root, 'apps-script', f), 'utf8');
const BOM = String.fromCharCode(0xfeff);

function makeSheet(values) {
  const data = values.map(r => r.slice());
  return {
    data,
    getDataRange: () => ({ getValues: () => data.map(r => r.slice()) }),
    getRange: (r, c, nr, nc) => ({
      setValue: v => { while (data[r - 1].length < c) data[r - 1].push(''); data[r - 1][c - 1] = v; },
      getValues: () => [data[r - 1].slice(0, nc || 1).concat(Array(Math.max(0, (nc || 1) - data[r - 1].length)).fill(''))]
    })
  };
}

function project(sheet, gemini) {
  const props = {};
  const ctx = vm.createContext({
    console, JSON, Math, Date, String, Number, Object, Array, RegExp, Error, isNaN,
    Logger: { log: () => {} },
    Utilities: {
      DigestAlgorithm: { SHA_256: 'sha256' }, Charset: { UTF_8: 'utf8' },
      computeDigest: (alg, text) => Array.from(crypto.createHash('sha256').update(text, 'utf8').digest()).map(b => b > 127 ? b - 256 : b),
      formatDate: (d, tz, fmt) => new Date(d.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 10).replace(/-/g, '/')
    },
    PropertiesService: { getScriptProperties: () => ({ getProperty: k => props[k] || null, setProperty: (k, v) => { props[k] = v; } }) },
    CACHE: { remove: () => {}, get: () => null, put: () => {} },
    CALL_MAX_WAIT_MS: 150000,
    callGemini_: gemini,
    getSheet_: () => sheet,
    dkExecStart_: now => now
  });
  vm.runInContext(read('Transcriptstore.gs'), ctx);
  vm.runInContext(read('SheetService.gs'), ctx);
  ctx.readSheetObjects_ = () => {
    const v = sheet.data, head = v[0];
    return v.slice(1).map(r => { const o = {}; head.forEach((h, i) => { o[h] = r[i]; }); return o; });
  };
  ctx.__props = props;
  return ctx;
}

const HEAD = ['影片ID', '發布日期', '標題', '處理狀態', '失敗原因', '原始逐字稿內容', '修飾後逐字稿內容', '原文更新時間', '原文SHA256', '來源別名'];
const RAW = '各 位 全 國 投 資 朋 友 大 家 早 不 好 意 思 哦 剛 才 我 又 忘 了 按 一 個 東 西 然 後 自 己 講 話 講 了 五 分 鐘 '.repeat(80);
const POLISHED = ('各位全國投資朋友大家早，不好意思哦，剛才我又忘了按一個東西。其實說真的，今天是我盤中直播兩週年。' +
  '我懷著很坦蕩的心，想說我會不 會開直播？沒有人要看。結果直播的第一天就1萬人。' +
  '來，第二個族群我推薦給你們的是高速傳輸，享碩跟普瑞。外資昨天買500多張，EPS上半年72塊。' +
  '你們想賣的趕快賣，我的會員不准賣。享碩的目標價我是定在2000塊。').repeat(40);
const strip = s => s.replace(/\s/g, '');

let calls = 0;
const fakeGemini = (system, prompt) => {
  calls++;
  assert(system.includes('原文已經拆成編號的句子'), '用的是同步過來的排版提示詞');
  const lines = prompt.split('\n');
  const first = Number(lines[0].slice(1, lines[0].indexOf(']')));
  const last = Number(lines[lines.length - 1].slice(1, lines[lines.length - 1].indexOf(']')));
  return JSON.stringify({ sections: [{ start: first + 3, title: '開場與兩週年' }, { start: Math.floor((first + last) / 2), title: '祥碩與普瑞目標價' }, { start: last + 9, title: '超出範圍' }] });
};

// 一、指紋與 pipeline.py 相同（同一個字串，Python 算出來也是 f1326e594db60343）
{
  const ctx = project(makeSheet([HEAD]), fakeGemini);
  assert.strictEqual(ctx.transcriptFingerprint_('各 位 投 資 朋 友\n大家早。' + BOM + '今天 CPU 5000億'), 'f1326e594db60343');
  assert.strictEqual(ctx.tidyTranscriptText_('我會不 會開直播，兩年前的 9 月 17 號，CPU GPU 都漲'), '我會不會開直播，兩年前的9月17號，CPU GPU都漲');
}

// 二、沒有版面的那一天：先用規則分段顯示（每段有小標），按鈕在；自動補排之後變成模型分段、存回同一列
{
  const sheet = makeSheet([HEAD,
    ['vid-0916', '2026/09/16', '舊的', '完成', '', POLISHED, POLISHED, '', '', ''],
    ['MANUAL-20260917', '2026/09/17', '手動投稿 2026/09/17', '完成', '', RAW, POLISHED, '2026/09/17 12:11:00', '', '']]);
  const ctx = project(sheet, fakeGemini);
  const before = ctx.getTranscript('2026/09/17');
  assert.strictEqual(before.formatted, false);
  assert(before.sections.length > 1 && before.sections.every(s => s.title && s.title !== '這一段'), '沒有版面時也分段、每段有小標');
  assert(!before.sections.some(s => s.paras.some(p => p.includes('會不 會'))), '中文字間的空白拿掉');

  // 規則分段時原文也要一字不少：這一天潤飾稿與原文的字不一樣，網站顯示原文
  const shown = ctx.transcriptDisplayText_(ctx.readSheetObjects_()[1]);
  assert.strictEqual(strip(before.sections.map(s => s.paras.join('')).join('')), strip(ctx.tidyTranscriptText_(shown)));

  calls = 0;
  const r = ctx.ensureTranscriptLayoutJob();
  assert.strictEqual(r.ok, true); assert.strictEqual(r.date, '2026/09/17'); assert.strictEqual(r.method, 'ai');
  assert(calls >= 1);
  const head = sheet.data[0];
  ['排版稿JSON', '排版稿指紋', '排版稿方式'].forEach(n => assert(head.includes(n), '補上欄位 ' + n));
  const row = sheet.data[2];
  assert.strictEqual(row[head.indexOf('排版稿指紋')], ctx.transcriptFingerprint_(shown));
  assert.strictEqual(row[head.indexOf('排版稿方式')], 'ai');
  const saved = JSON.parse(row[head.indexOf('排版稿JSON')]);
  assert(saved.some(s => s.title === '開場與兩週年') && !saved.some(s => s.title === '超出範圍'));
  assert.strictEqual(strip(saved.map(s => s.paras.join('')).join('')), strip(ctx.tidyTranscriptText_(shown)), '段落由原句組回，一字不少');

  const after = ctx.getTranscript('2026/09/17');
  assert.strictEqual(after.formatted, true); assert.strictEqual(after.layoutSource, 'pipeline'); assert.strictEqual(after.layoutMethod, 'ai');

  // 下一棒：9/17 已經排好，改排 9/16（由新到舊）；再下一棒什麼都不用做
  calls = 0;
  assert.strictEqual(ctx.ensureTranscriptLayoutJob().date, '2026/09/16');
  calls = 0;
  assert.strictEqual(ctx.ensureTranscriptLayoutJob().idle, true); assert.strictEqual(calls, 0);

  // 原文改過：指紋對不上，舊版面不採信，重排
  sheet.data[2][6] = POLISHED + '後來補上的一段話。'.repeat(5);
  sheet.data[2][5] = sheet.data[2][6];
  assert.strictEqual(ctx.getTranscript('2026/09/17').formatted, false, '指紋對不上不能沿用舊版面');
  assert.strictEqual(ctx.ensureTranscriptLayoutJob().date, '2026/09/17');
}

// 三、模型不能用（配額）：存規則分段、標成 rule，兩小時內不重打，一天最多四次
{
  const sheet = makeSheet([HEAD, ['MANUAL-20260917', '2026/09/17', '手動投稿', '完成', '', POLISHED, POLISHED, '', '', '']]);
  let tries = 0;
  const ctx = project(sheet, () => { tries++; throw new Error('QUOTA_DAILY　所有 Gemini 金鑰的今日額度都已用完'); });
  const r1 = ctx.ensureTranscriptLayoutJob();
  assert.strictEqual(r1.method, 'rule');
  const head = sheet.data[0];
  assert.strictEqual(sheet.data[1][head.indexOf('排版稿方式')], 'rule');
  const t = ctx.getTranscript('2026/09/17');
  assert.strictEqual(t.formatted, false); assert.strictEqual(t.layoutMethod, 'rule');
  assert(t.sections.every(s => s.title));
  const n = tries;
  assert.strictEqual(ctx.ensureTranscriptLayoutJob().idle, true, '剛失敗過，兩小時內不重打');
  assert.strictEqual(tries, n);
}

// 四、舊版（v29）排版稿沒有「排版稿方式」欄：每段都有小標算模型排的
{
  const ctx = project(makeSheet([HEAD]), fakeGemini);
  assert.strictEqual(ctx.storedLayoutMethod_([{ title: '開盤', paras: [] }], ''), 'ai');
  assert.strictEqual(ctx.storedLayoutMethod_([{ title: '', paras: [] }], ''), 'rule');
  assert.strictEqual(ctx.transcriptCleanTitle_('這一段'), '');
  const b = ctx.transcriptBreaks_(JSON.stringify({ sections: [{ start: 7, title: '內容一' }, { start: 3, title: '大盤' }] }), 1, 10);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(b)), [{ start: 1, title: '大盤' }, { start: 7, title: '' }]);
}

// 五、接線：每五分鐘那一棒最後補排；按鈕排完存回試算表；提示詞由 sync 同步
{
  const setup = read('Setup.gs'), sheetSrc = read('SheetService.gs'), js = read('JavaScript.html');
  assert(/if \(!weekday\) \{ transcriptLayoutTick_\(\); return; \}/.test(setup));
  assert(/transcriptLayoutTick_\(\);\r?\n\}/.test(setup));
  assert(/if \(!dueEvery_\('txLayout', 15\)\) \{ return; \}/.test(setup));
  assert(/function formatTranscript\(dateStr\) \{[\s\S]*?layoutTranscriptDate_\(d,/.test(sheetSrc), '按鈕排完存回試算表');
  assert(!/transcriptFormatCacheKey_/.test(sheetSrc), '不再只放六小時快取');
  assert(/暫用自動分段/.test(js));
}
console.log('PASS: v44 transcript layout — breaks-only model reply, stored with fingerprint and method, auto re-layout every 15 min, CJK spaces tidied.');
