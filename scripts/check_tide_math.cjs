/* 驗證RSI與管理者指定移除的介面。 */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../public-site/gas-source/TideDesign.html'),'utf8');
const start=source.indexOf('  function num('),end=source.indexOf('  window.TideMath=');
const context={window:{}};vm.createContext(context);vm.runInContext(source.slice(start,end)+'window.math={rsi};',context);
const {rsi}=context.window.math;
let checks=0;function test(name,fn){fn();checks++;console.log('通過：'+name);}
const rows=Array.from({length:250},(_,i)=>({date:'2026/'+String(i),close:i+10,low:i+9,high:i+11,volume:100}));
test('RSI單邊與平盤',()=>{assert.equal(rsi(rows,14).at(-1).value,100);assert.equal(rsi(rows.map(r=>({...r,close:100})),14).at(-1).value,50);assert.equal(rsi([...rows].reverse(),14).at(-1).value,0);});
test('不足14期不補RSI',()=>assert.equal(rsi(rows.slice(0,14),14).length,0));
test('缺值不計成零價',()=>assert.equal(rsi([...rows.slice(0,14),{close:null}],14).length,0));
const page=fs.readFileSync(path.join(__dirname,'../public-site/gas-source/Index.html'),'utf8');
test('移除水位、情境選單、摘要與教學框',()=>['tideGrade','tideMethod','tideAnchor','tideSummary','短線波段 · 60 日','技術指標怎麼看'].forEach(s=>assert(!page.includes(s))));
test('沒有新增全域暫停動態按鈕',()=>assert(!source.includes("toggle.id='tideMotion'")));
test('RSI採Wilder平滑而非重新平均',()=>{const a=Array.from({length:15},(_,i)=>({date:String(i),close:i+1}));a.push({date:'15',close:1});assert(Math.abs(rsi(a,14).at(-1).value-1300/27)<1e-10);});
test('進場與持有共用單一區塊',()=>{assert.equal((page.match(/id="dSecHold"/g)||[]).length,1);assert(!page.includes('>持有期間</h2>'));assert(!page.includes('id="tideTrace"'));});
console.log('圖表計算與介面契約 '+checks+' 項通過。');
