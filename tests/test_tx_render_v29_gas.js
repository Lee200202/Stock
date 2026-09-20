// 逐字稿分頁（2026/09/16）：先用工作流排好的版面，畫面改成與信件相同的卡片。
const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const root = path.resolve(__dirname, '..');
const sheet = fs.readFileSync(path.join(root, 'apps-script/SheetService.gs'), 'utf8');
const js = fs.readFileSync(path.join(root, 'apps-script/JavaScript.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'apps-script/Stylesheet.html'), 'utf8');

// storedTranscriptLayout_：有排版稿、有指紋，而且指紋與目前顯示的原文相同才算數（v44 起真的比對指紋）
const crypto = require('crypto');
const ctx = vm.createContext({ JSON, String, Array, Number, Math, RegExp,
  Logger: { log: () => {} },
  Utilities: { computeDigest: (a, t) => Array.from(crypto.createHash('sha256').update(t, 'utf8').digest()).map(b => b > 127 ? b - 256 : b),
               DigestAlgorithm: { SHA_256: 1 }, Charset: { UTF_8: 1 } } });
vm.runInContext(sheet, ctx);

const sections = [{ title: '開盤震盪', paras: ['各位投資朋友大家早。'] }];
const fp = ctx.transcriptFingerprint_('各位投資朋友大家早。');
const row = { '排版稿JSON': JSON.stringify(sections), '排版稿指紋': fp };
assert.deepEqual(JSON.parse(JSON.stringify(ctx.storedTranscriptLayout_(row, '各位 投資朋友 大家早。'))), sections, '排版稿存在、指紋相同（只差空白）就直接用');
assert.equal(ctx.storedTranscriptLayout_(row, '各位投資朋友大家晚。'), null, '原文改過，指紋對不上就不採信');
assert.equal(ctx.storedTranscriptLayout_({ '排版稿JSON': '', '排版稿指紋': 'abc' }, 'x'), null, '沒有排版稿就回 null');
assert.equal(ctx.storedTranscriptLayout_({ '排版稿JSON': JSON.stringify(sections) }, 'x'), null, '沒有指紋不採信');
assert.equal(ctx.storedTranscriptLayout_({ '排版稿JSON': '{壞掉的', '排版稿指紋': 'abc' }, 'x'), null, '壞掉的 JSON 退回規則分段');
assert.equal(ctx.storedTranscriptLayout_({ '排版稿JSON': '[]', '排版稿指紋': 'abc' }, 'x'), null, '空陣列不算');

// getTranscript 會優先回傳工作流排好的版面，而且標明來源
assert(/var stored = storedTranscriptLayout_\(row, v2\);/.test(sheet));
assert(/layoutSource = 'pipeline'/.test(sheet));
assert(!/transcriptFormatCacheKey_/.test(sheet), 'v44 起按鈕排完存回試算表，不再有六小時快取');

// 畫面：每一段一張卡片，卡上有段號與小標；標籤分得出是每日排版還是臨時排的
assert(js.includes('<section class="txcard">'));
assert(js.includes('txcard-n'));
assert(/r\.layoutSource === 'pipeline' \? '每日排版' : '已智慧排版'/.test(js));
assert(/\.txcard \{/.test(css) && /\.txcard-h \{/.test(css));
console.log('PASS: transcript uses the layout produced by the daily run and renders as mail-style cards.');
