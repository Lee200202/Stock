// LINE uri 動作只收 ASCII：排入前 lineFixUris_ 會把中文路徑編碼、把不能用的網址換成網站首頁。
// 2026/11/20 管理者通知 W20261120182558 因「報告.md」整則被拒（HTTP 400 Invalid action URI）。
const fs = require('fs');
const assert = require('assert');
const src = fs.readFileSync('public-site/gas-source/Line.gs', 'utf8');
const a = src.indexOf('var LINE_URI_FALLBACK_'), b = src.indexOf('function lineQueue_(m)');
assert(a > 0 && b > a, 'lineFixUris_ 不見了');
assert(/function lineQueue_\(m\) \{\s*lineFixUris_\(/.test(src), 'lineQueue_ 必須先跑 lineFixUris_');
const ctx = {};
new Function('ctx', src.slice(a, b) + '\nctx.fix = lineFixUris_;')(ctx);
const msg = [{ type: 'flex', contents: { type: 'carousel', contents: [{ type: 'bubble', footer: { type: 'box', contents: [
  { type: 'button', action: { type: 'uri', label: '報告', uri: 'https://github.com/Lee200202/Stock/blob/main/docs/weekly-review/2026-11-20/報告.md' } },
  { type: 'button', action: { type: 'uri', label: '壞', uri: 'javascript:alert(1)' } },
  { type: 'button', action: { type: 'uri', label: '已編碼', uri: 'https://a.example/%E5%A0%B1?q=1' } },
  { type: 'button', action: { type: 'uri', label: '太長', uri: 'https://a.example/' + 'x'.repeat(1001) } }] } }] } }];
ctx.fix(msg);
const uris = msg[0].contents.contents[0].footer.contents.map(x => x.action.uri);
assert.strictEqual(uris[0], 'https://github.com/Lee200202/Stock/blob/main/docs/weekly-review/2026-11-20/%E5%A0%B1%E5%91%8A.md');
assert.strictEqual(uris[1], 'https://lee200202.github.io/Stock/');
assert.strictEqual(uris[2], 'https://a.example/%E5%A0%B1?q=1');
assert.strictEqual(uris[3], 'https://lee200202.github.io/Stock/');
assert(uris.every(u => /^[\x21-\x7e]+$/.test(u)));
console.log('LINE 網址：中文編碼、已編碼不重複、危險或過長改首頁，4 項通過');
