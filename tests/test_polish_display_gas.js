// 網站逐字稿顯示：修飾稿只要字與原文一致就顯示修飾稿；忽略的標點須與 pipeline.py 的 _ev_norm 一致（2026/09/13）。
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const root=path.resolve(__dirname,'..');
const src=fs.readFileSync(path.join(root,'apps-script/SheetService.gs'),'utf8');
const m=src.match(/^function displayTranscriptText_\(row\) \{[\s\S]*?^\}/m);
assert(m,'displayTranscriptText_ not found');
const ctx=vm.createContext({});vm.runInContext(m[0],ctx);
const raw='昨 天 有 一 個 會 員 這 樣 問 我 張 總 這 樣 我 是 不 是 要 趕 快 賣 掉 我 從 1820,2025,2135 買 到 現 在';
const show=p=>ctx.displayTranscriptText_({'原始逐字稿內容':raw,'修飾後逐字稿內容':p});
for(const p of ['昨天有一個會員這樣問我：「張總，這樣我是不是要趕快賣掉？」我從1820,2025,2135買到現在。',
  '昨天有一個會員這樣問我“張總……這樣我是不是要趕快賣掉”我從1820,2025,2135買到現在～',
  '昨天有一個會員這樣問我〈張總〉‧這樣我是不是要趕快賣掉•我從1820,2025,2135買到現在－'])
  assert.equal(show(p),p,p);
// 字真的不一樣（模型改了名字或數字）仍然改顯示原文
assert.equal(show('昨天有一個會員這樣問我：「張總，這樣我是不是要趕快賣掉？」我從1820買到現在。'),raw);
console.log('PASS: transcript display keeps punctuated polish, rejects changed words.');
