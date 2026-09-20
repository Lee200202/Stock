// 2026/09/13 v7：GAS 端的句首人名保險與重新分類提示詞，與 pipeline.py 同一條規則。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const src=fs.readFileSync(path.join(root,'apps-script/Adminservice.gs'),'utf8');
const m=src.match(/^function naturalReason_\(text\) \{[\s\S]*?^\}/m);
assert(m,'naturalReason_ not found');
const ctx=vm.createContext({});vm.runInContext(m[0],ctx);
for(const [input,expected] of [
 ['張正指出國巨外資成本在597','指出國巨外資成本在597。'],
 ['會員買了祥碩兩個多月，張正強調自己就是在等他噴出去','會員買了祥碩兩個多月，強調自己就是在等他噴出去。'],
 ['雖然張正已於昨天買進','雖然已於昨天買進。'],
 ['台積電碰到特定線再注意','台積電碰到特定線再注意。']]) assert.equal(ctx.naturalReason_(input),expected,input);
const r=src.match(/var PIPE_RECLASSIFY_SYSTEM =[\s\S]*?;\r?\n/);
assert(r && r[0].includes('語氣偏負面也歸 avoid'));
assert(!r[0].includes('真正看不出結論、完全中性時歸 watch'));
const cfg=fs.readFileSync(path.join(root,'apps-script/Config.gs'),'utf8');
const build=cfg.match(/GAS_BUILD = '\d{4}-\d{2}-\d{2}-quality-v(\d+)'/);
assert(build && Number(build[1])>=7,'build must be quality-v7 or later');
console.log('PASS: GAS speaker-name strip, loosened reclassify prompt, build v7+.');
