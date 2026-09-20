// v17：Apps Script 端的標題格式與說明星號，與 pipeline.py 一致（重建郵件走這一邊）。
const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const root=path.resolve(__dirname,'..'),ctx=vm.createContext({console});
for(const name of ['Presentationquality.gs','Evidencequality.gs','Articlequality.gs','MailService.gs'])vm.runInContext(fs.readFileSync(path.join(root,'apps-script',name),'utf8'),ctx);

// 「國巨」→「國巨*」不能越換越多星（9/14 信裡出現「國巨***」）
const r={name:'國巨*',code:'2327','原始語音名稱':'國巨'};
const once=ctx.publicNarrative_('國巨近期走勢詭異，越想解套國巨越危險。',r);
assert.equal(once,'國巨近期走勢詭異，越想解套國巨越危險。');
assert.equal(ctx.publicNarrative_(once,r),once);
assert.equal(ctx.publicNarrative_(ctx.publicNarrative_('愛普***曾被推薦',{name:'愛普*',code:'6531'}),{name:'愛普*',code:'6531'}),'愛普曾被推薦。');
const ky={name:'世芯-KY',code:'3661','原始語音名稱':'世芯'};
assert.equal(ctx.publicNarrative_(ctx.publicNarrative_('世芯營收創新高',ky),ky),'世芯-KY營收創新高。');

// 標題：講者口吻的一句觀點，不加「張震：」前綴（2026/09/15）
const s={market:[{kind:'event',text:'兩個大人在打架，我們坐在旁邊看，打完再進場。',evidence:['兩 個 大 人 在 打 架'],
  headline:'張震：兩個大人在打架 我們坐在旁邊看就好',_evidence_verified:true}]};
assert.equal(ctx.articleTitle_(s),'兩個大人在打架 我們坐在旁邊看就好！');
s.market[0].headline='外資目標價上看9999元，必漲！';
assert(!ctx.articleTitle_(s).startsWith('張震'));assert(!ctx.articleTitle_(s).includes('9999'));

// v19：不限字數；教學觀念標題當第二層備援
const longHead='這個禮拜很熱鬧不代表你要跟著人家熱鬧，兩個大人在打架你不要參進去，坐在旁邊看就好！';
assert.equal(ctx.articleTitle_({market:[{kind:'event',text:'本週外部變數多',evidence:['這 個 禮 拜 很 熱 鬧 不 代 表 你 要 跟 著 人 家 熱 鬧 兩 個 大 人 在 打 架 你 不 要 參 進 去 你 就 坐 在 旁 邊 看 就 好'],headline:longHead,_evidence_verified:true}]}),longHead);
assert.equal(ctx.articleTitle_({market:[{kind:'event',text:'聯準會利率決策前大盤震盪。',_evidence_verified:true},
  {kind:'view',text:'克服過度交易：真正賺錢的人一兩個禮拜才出手一次。',_evidence_verified:true}]}),'克服過度交易，真正賺錢的人一兩個禮拜才出手一次！');
// v44：標題至少 15 字；觀念標題太短時接上說明，接不到就用盤勢主題加說明
assert.equal(ctx.articleTitle_({market:[{kind:'event',text:'聯準會利率決策前大盤震盪。',_evidence_verified:true},
  {kind:'view',text:'操作重音與心態：不要用當日的漲跌去判斷股票，低檔買進的股票要抱住。',_evidence_verified:true}]}),'操作重音與心態，不要用當日的漲跌去判斷股票！');
assert.equal(ctx.articleTitle_({market:[{kind:'event',text:'聯準會利率決策前大盤震盪。',_evidence_verified:true}]}),'利率決策與震盪盤勢，本集盤勢與操作重點整理');
assert.equal(ctx.articleTitle_({market:[]}),'本集市場觀察與會員操作重點整理');
assert.equal(ctx.titleOneSentence_('大家有沒有看到成交量？預估今天成交量多少？5000億'),'大家有沒有看到成交量？預估今天成交量多少');

// 前台同一份說明函式也要同步
const frontend=fs.readFileSync(path.join(root,'apps-script/JavaScript.html'),'utf8');
assert(frontend.includes('function narrativeName_('),'JavaScript.html 要執行 sync_quality.py 同步');
console.log('PASS: v17 GAS 168-style title and idempotent narrative names without asterisks.');
