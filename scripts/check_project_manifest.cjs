const fs=require('fs'),vm=require('vm'),assert=require('assert');
const base='public-site/gas-source/';
const setup=fs.readFileSync(base+'Setup.gs','utf8');
const config=fs.readFileSync(base+'Config.gs','utf8');
const source=setup.slice(setup.indexOf('var PROJECT_BUILD_'),setup.indexOf('/** 部署後只讀驗收'));
const files=new Map(fs.readdirSync(base).filter(f=>f.endsWith('.html')).map(f=>[f.slice(0,-5),fs.readFileSync(base+f,'utf8')]));
const ctx={GAS_BUILD:config.match(/var GAS_BUILD = '([^']+)'/)[1],HtmlService:{createTemplateFromFile:n=>{if(!files.has(n))throw Error('找不到');return {getRawContent:()=>files.get(n)}}},Logger:{log:()=>{}}};
vm.createContext(ctx);vm.runInContext(source,ctx);
// 此測試只核對版本與真實HTML契約，不用替身宣稱驗證全部GAS函式。
ctx.PROJECT_FILES_=[];
assert.deepEqual(Array.from(ctx.projectFileProblems_()),[]);
ctx.GAS_BUILD='過期版本';assert(ctx.projectFileProblems_().some(p=>p.file==='Config.gs'));ctx.GAS_BUILD=ctx.PROJECT_BUILD_;
const story=files.get('StoryDesign');files.set('StoryDesign',story.replaceAll('techJourneyV197','techStoryV195'));assert(ctx.projectFileProblems_().some(p=>p.file==='StoryDesign.html'));files.set('StoryDesign',story);
files.delete('TideDesign');assert(ctx.projectFileProblems_().some(p=>p.file==='TideDesign.html'));
console.log('專案版本、全部HTML標記、舊圖解拒收及缺少共用介面檔：4項契約通過');
