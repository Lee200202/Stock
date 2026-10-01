/**
 * 公開價位摘要。保留試算表原始多次交易價，避免最高展示價變成平均成本。
 * @param {*} value 原始價位
 * @param {string=} evidence 價位原句
 * @param {string=} context 說明
 * @return {string} 單一價位、以上／以下或未說明
 */
function displayPrice_(value, evidence, context) {
  var text = String(value || '').trim();
  if (!text || /^[−-]|\d[/／]\d|[xX]|\d\s*(?:日|天|張|%|％)|EPS|均線/i.test(text)) return '未說明';
  text = text.replace(/182020252135/g, '1820、2025、2135');
  text = text.replace(/(\d),(?=\d{3}(?:\D|$))/g, '$1');
  if (/\d{7,}/.test(text)) return '未說明';
  var re = /\d+(?:\.\d+)?/g, m, best = null;
  while ((m = re.exec(text))) {
    if (!(Number(m[0]) > 0 && Number(m[0]) <= 100000)) return '未說明';
    if (!best || Number(m[0]) > Number(best[0])) best = m;
  }
  if (!best) return '未說明';
  var amount=best[0].replace('.', '\\.');
  var ctx=String(context||'');
  if(new RegExp('(?:大跌|下跌|上漲|漲|跌|漲幅|跌幅)\\s*'+amount+'\\s*(?:元|塊)').test(ctx) &&
     !new RegExp('(?:成本|買點|現價|股價|目標價|買在|賣在|來到|收在)\\s*(?:為|是|約|在)?\\s*'+amount+'(?!\\d)').test(ctx)) return '未說明';
  if ([5,10,20,60,120,200,240].indexOf(Number(best[0])) >= 0 && /均線|技術線|碰.{0,6}線|線型/.test(String(context || '')) &&
      !(new RegExp(best[0] + '\\s*(?:元|塊)')).test(String(evidence || ''))) return '未說明';
  var suffix = /^\s*(以上|以下)/.exec(text.slice(best.index + best[0].length));
  return String(Number(best[0])) + (suffix ? suffix[1] : '');
}

/** 僅供畫面日K預覽，絕不寫進正式日K或績效快取。 */
function intradayPreview_(daily, quote, today) {
  var rows = daily.slice();
  if (!quote || quote.date !== today || !quote.open || !quote.high || !quote.low || !quote.last) return rows;
  if (![quote.open,quote.high,quote.low,quote.last].every(function(v){return isFinite(v) && v > 0;})) return rows;
  if (quote.low > Math.min(quote.open,quote.last) || quote.high < Math.max(quote.open,quote.last)) return rows;
  // 收盤日K已存在時以正式資料為準。舊報價不跨日延伸。
  if (rows.some(function(r){return String(r.date).replace(/-/g,'/') === today;})) return rows;
  rows.push({date:today,open:quote.open,high:quote.high,low:quote.low,close:quote.last,
    volume:quote.volume || 0,_provisional:true,_asOf:quote.time});
  rows.sort(function(a,b){return String(a.date).localeCompare(String(b.date));});
  return rows;
}

// BEGIN PUBLIC NARRATIVE V10
var PUBLIC_CONFIRMED_NAMES = {"普威": ["4966", "譜瑞-KY"], "普位": ["4966", "譜瑞-KY"], "譜位": ["4966", "譜瑞-KY"], "普銳": ["4966", "譜瑞-KY"], "普瑞": ["4966", "譜瑞-KY"], "加折": ["3533", "嘉澤"], "加哲": ["3533", "嘉澤"], "加澤": ["3533", "嘉澤"], "家澤": ["3533", "嘉澤"], "茂聯": ["3665", "貿聯-KY"], "出金城": ["8210", "勤誠"], "初清程": ["8210", "勤誠"], "00981A": ["00981A", "主動統一台股增長"], "主動統一台股增長": ["00981A", "主動統一台股增長"], "瑞獄": ["2379", "瑞昱"], "雨沾": ["8271", "宇瞻"], "維星": ["2377", "微星"], "邦店": ["2344", "華邦電"], "連電": ["2303", "聯電"], "大力光": ["3008", "大立光"], "利基電": ["6770", "力積電"], "宜頂": ["5289", "宜鼎"], "移頂": ["5289", "宜鼎"], "以頂": ["5289", "宜鼎"], "川服": ["2059", "川湖"], "木德": ["3563", "牧德"], "四星KY": ["3661", "世芯-KY"], "四星": ["3661", "世芯-KY"], "宏準": ["2354", "鴻準"], "弘準": ["2354", "鴻準"], "紅準": ["2354", "鴻準"], "弘海": ["2317", "鴻海"], "連陽": ["3014", "聯陽"], "聯揚": ["3014", "聯陽"], "連揚": ["3014", "聯陽"], "威星": ["2377", "微星"], "想碩": ["5269", "祥碩"], "享碩": ["5269", "祥碩"], "紅蠢": ["2354", "鴻準"], "降碩": ["5269", "祥碩"], "詳碩": ["5269", "祥碩"], "利望": ["3529", "力旺"], "金星科": ["6533", "晶心科"], "精星科": ["6533", "晶心科"], "金新科": ["6533", "晶心科"], "蹲態": ["3545", "敦泰"], "漢糖": ["2404", "漢唐"], "漢堂": ["2404", "漢唐"], "秦城": ["8210", "勤誠"], "立即電": ["6770", "力積電"], "立基電": ["6770", "力積電"], "力基電": ["6770", "力積電"], "城成": ["8210", "勤誠"], "勤城": ["8210", "勤誠"], "晶成": ["8210", "勤誠"], "玉金光": ["3406", "玉晶光"], "環球金": ["6488", "環球晶"], "國具": ["2327", "國巨*"], "聖輝": ["5536", "聖暉*"], "聖暉": ["5536", "聖暉*"], "有達": ["2409", "友達"], "奇鴻": ["3017", "奇鋐"], "瑞澤": ["7703", "銳澤"], "楊基工程": ["6691", "洋基工程"], "翔碩": ["5269", "祥碩"], "索羅門": ["2359", "所羅門"], "秦成": ["8210", "勤誠"], "立積電": ["6770", "力積電"], "立旺": ["3529", "力旺"], "紅柱恩": ["2354", "鴻準"], "創億": ["3443", "創意"], "致源": ["3035", "智原"], "隱身版光通訊": ["2402", "毅嘉"], "隱藏版光通訊": ["2402", "毅嘉"], "隱藏版光訊": ["2402", "毅嘉"], "意嘉": ["2402", "毅嘉"], "億嘉": ["2402", "毅嘉"], "益嘉": ["2402", "毅嘉"], "義嘉": ["2402", "毅嘉"], "易嘉": ["2402", "毅嘉"], "一嘉": ["2402", "毅嘉"], "毅加": ["2402", "毅嘉"], "意加": ["2402", "毅嘉"], "益加": ["2402", "毅嘉"], "億加": ["2402", "毅嘉"], "玉龍": ["2201", "裕隆"], "浴龍": ["2201", "裕隆"], "育龍": ["2201", "裕隆"], "預龍": ["2201", "裕隆"], "御龍": ["2201", "裕隆"], "遇龍": ["2201", "裕隆"], "愈龍": ["2201", "裕隆"], "欲龍": ["2201", "裕隆"], "喻龍": ["2201", "裕隆"], "郁龍": ["2201", "裕隆"], "譽龍": ["2201", "裕隆"], "豫龍": ["2201", "裕隆"], "裕龍": ["2201", "裕隆"], "雨龍": ["2201", "裕隆"], "羽龍": ["2201", "裕隆"], "語龍": ["2201", "裕隆"], "宇龍": ["2201", "裕隆"], "玉隆": ["2201", "裕隆"], "浴隆": ["2201", "裕隆"], "育隆": ["2201", "裕隆"], "預隆": ["2201", "裕隆"], "御隆": ["2201", "裕隆"], "遇隆": ["2201", "裕隆"], "愈隆": ["2201", "裕隆"], "欲隆": ["2201", "裕隆"], "喻隆": ["2201", "裕隆"], "郁隆": ["2201", "裕隆"], "譽隆": ["2201", "裕隆"], "豫隆": ["2201", "裕隆"], "雨隆": ["2201", "裕隆"], "羽隆": ["2201", "裕隆"], "語隆": ["2201", "裕隆"]};
/* 公開說明裡的簡體字換回繁體（v89，2026/09/30「避开」）。兩兩一組：簡體、繁體；與 pipeline.py 的 S2T_PAIRS 同一串。 */
var PUBLIC_S2T_PAIRS_ = "开開关關这這说說们們进進买買卖賣涨漲价價会會时時来來对對点點还還没沒过過个個发發线線级級场場资資从從与與应應该該边邊继繼续續险險风風业業绩績营營获獲势勢仅僅尽盡实實际際预預长長张張让讓认認为為头頭经經济濟现現须須导導钱錢赚賺赔賠损損亏虧稳穩筹籌码碼压壓撑撐区區间間内內频頻选選择擇标標规規则則检檢测測讯訊号號报報体體广廣厂廠电電车車东東门門问問见見观觀视視觉覺亲親气氣满滿帮幫动動务務员員图圖团團园園圆圓国國队隊阶階阳陽阴陰陆陸离離难難双雙变變专專两兩严嚴临臨举舉义義乐樂习習书書乱亂争爭亚亞产產亿億众眾优優传傳伤傷债債储儲兴興养養写寫军軍农農决決况況净淨减減创創别別剧劇办辦华華协協单單卫衛却卻县縣参參吗嗎么麼启啟响響坏壞块塊坚堅执執扩擴护護担擔拥擁挤擠换換数數断斷无無旧舊显顯暂暫术術机機杀殺杂雜权權条條极極构構栏欄样樣档檔楼樓欢歡欧歐残殘毕畢汉漢沟溝泽澤洁潔浅淺润潤湾灣湿濕灭滅灵靈灾災热熱爱愛牵牽独獨环環画畫畅暢疗療盖蓋盘盤矿礦础礎确確种種积積称稱税稅穷窮竞競笔筆签簽简簡类類紧緊红紅纪紀约約纯純纳納纷紛纸紙练練组組细細织織终終结結给給络絡绝絕统統维維综綜绿綠缓緩编編缩縮网網罗羅职職联聯脑腦节節药藥虑慮虽雖补補装裝览覽触觸计計订訂讨討训訓议議记記讲講许許论論设設证證评評识識试試话話询詢详詳语語误誤请請读讀调調谁誰谈談谓謂谢謝负負财財责責败敗货貨质質购購贵貴贷貸费費贴貼贸貿赏賞赛賽赶趕趋趨转轉轮輪软軟轻輕载載较較输輸达達运運远遠违違连連迟遲递遞遗遺释釋钢鋼铁鐵银銀链鏈销銷锁鎖错錯键鍵闪閃闭閉闻聞阅閱随隨隐隱顶頂项項顺順领領题題额額飞飛驱驅验驗黄黃适適";
var PUBLIC_S2T_ = null;
function toTraditional_(text) {
  if (!PUBLIC_S2T_) {
    PUBLIC_S2T_ = {};
    for (var i = 0; i + 1 < PUBLIC_S2T_PAIRS_.length; i += 2) { PUBLIC_S2T_[PUBLIC_S2T_PAIRS_.charAt(i)] = PUBLIC_S2T_PAIRS_.charAt(i + 1); }
  }
  return String(text || '').replace(/[一-鿿]/g, function (ch) { return PUBLIC_S2T_[ch] || ch; });
}
/** 官方簡稱尾巴的「*」是證交所註記，說明文字不用。 */
function narrativeName_(name) { return String(name || '').replace(/[*＊]+$/, ''); }
/** 把 heard 換成 fixed，重複套用不會越換越長（「世芯」→「世芯-KY」不會變成「世芯-KY-KY」）。 */
function replaceNarrativeName_(text, heard, fixed) {
  if (!heard || heard === fixed) return text;
  var esc = function (v) { return v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); };
  if (fixed.indexOf(heard) === 0 && fixed.length > heard.length) {
    return text.replace(new RegExp(esc(heard) + '(?!' + esc(fixed.slice(heard.length)) + ')', 'g'), fixed);
  }
  return text.split(heard).join(fixed);
}
/* 內部判斷字眼：這一列「為什麼被歸到這一類」是流程，不是講者說的話。
   與 pipeline.py 的 _META_CLAUSE 同一份清單；寫在這裡，已經存進試算表的舊說明呈現時也會乾淨。
   2026/09/15 的台積電寫成「……但在當日節目中並未將台積電列為當日會員實際買進或持有的個股明細，
   故列入市場教學與觀察範疇」，讀的人要的是他對台積電講了什麼（管理者回報，2026/09/16）。 */
var META_CLAUSE_RE_ = /歷史回顧|列為觀望|列入觀望|改列|日期未明|依原文語氣|依上下文判|原判|原為買入|原為賣出|不列入當日|非影片當日|非當日執行|依規則|歷史買進|歷史賣出|並未將|未將本檔|(?:並)?未列(?:為|入)|個股明細|故列入|因此列入|列入市場教學|(?:觀察|教學|觀望)範疇|歸類為|分類為|列為.{0,10}?(?:觀望|等待|等候|追蹤|觀察|避開)|屬於.{0,10}?(?:觀察|觀望|避開)(?:的)?(?:對象|標的|名單)/;
/** 按頂層逗號、分號、句號切子句，丟掉含內部判斷字眼的那幾句；括號裡的標點不切。 */
function stripMetaClauses_(text) {
  var str = String(text || ''), parts = [], buf = '', depth = 0;
  for (var i = 0; i < str.length; i++) {
    var ch = str.charAt(i);
    if (ch === '（' || ch === '(') { depth++; }
    else if ((ch === '）' || ch === ')') && depth) { depth--; }
    if (!depth && '，,；;。'.indexOf(ch) >= 0) { parts.push([buf, ch]); buf = ''; }
    else { buf += ch; }
  }
  if (buf) { parts.push([buf, '']); }
  var kept = [];
  parts.forEach(function (x) {
    if (!x[0].replace(/\s/g, '')) { return; }
    if (!META_CLAUSE_RE_.test(x[0])) { kept.push([x[0], x[1]]); return; }
    // 丟掉的那一句原本結束了一個句子時，句號讓給前一句，不要把兩句黏成一句。
    if (x[1] === '。' && kept.length) { kept[kept.length - 1][1] = '。'; }
  });
  return kept.map(function (x) { return x[0] + x[1]; }).join('');
}
/** 說明名稱與代號對齊，原始逐字稿與引用不經過這個函式。 */
function publicNarrative_(text, row) {
  row = row || {};
  // 語音稿把「不准」聽寫成「不準」（「會員不準賣」）；後面接動作才換，「預測不準」不動。
  text = toTraditional_(text).replace(/不準(?=給我|亂|再|去|賣|買|碰|追|用|借|操作|進場|放空|做空)/g, '不准').replace(/(^|[，,。；;：:、「『（(\s]|雖然|但是|但|而且|而|並且|並|且|因為|所以)(?:張震|張正|張總|張中|講者)(?:老師)?(?:本人)?(?:的(?=會員))?/g,'$1');
  text = stripMetaClauses_(text);
  text=text.replace(/[（(][^（）()]*?(?:原文(?:作|為|寫|說)|語音(?:作|為)|誤植|誤字)[^（）()]*[）)]/g,'');
  if(String(row.code||'')==='5274')text=text.replace(/信化/g,'信驊');
  Object.keys(PUBLIC_CONFIRMED_NAMES).sort(function(a,b){return b.length-a.length;}).forEach(function(heard){
    if(heard==='00981A')return;
    var fixed=narrativeName_(PUBLIC_CONFIRMED_NAMES[heard][1]);
    text=replaceNarrativeName_(text,heard,fixed);
  });
  Object.keys(PUBLIC_CONFIRMED_NAMES).forEach(function(heard){
    var fixed=narrativeName_(PUBLIC_CONFIRMED_NAMES[heard][1]), escaped=fixed.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    text=text.replace(new RegExp(escaped+'\\s*[（(]'+escaped+'[）)]','g'),fixed);
  });
  // 「國巨」→「國巨*」每套一次多一顆星，信裡出現「國巨***」（2026/09/14）；說明一律用不帶星號的名稱。
  var name=narrativeName_(row.name), code=String(row.code || ''), raw=row['原始語音名稱'];
  if(raw && raw.length>=2 && name && (!PUBLIC_CONFIRMED_NAMES[raw] || PUBLIC_CONFIRMED_NAMES[raw][0]===code)) text=replaceNarrativeName_(text,raw,name);
  if(name && /^(?:00981A|\d{4,6})$/.test(code)) {
    var escaped=name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
    text=text.replace(new RegExp(escaped+'\\s*(?:[（(]\\s*(?:00981A|\\d{4,6})\\s*[）)]|(?:00981A|\\d{4,6})(?=為例|這檔|這支|這一檔)|代號\\s*(?:00981A|\\d{4,6})(?!\\d))','g'),name+'（'+code+'）');
    text=text.replace(new RegExp(escaped+'\\s*[（(]'+escaped+'[）)]','g'),name);
    text=text.replace(new RegExp('(?:'+escaped+'){2,}','g'),name);
  }
  var seen={},parts=[];
  text.split(/[。；;]/).forEach(function(part){
    var key=part.replace(/回顧|過往|目前|\s|[，,]/g,'');
    if(key && !seen[key]){seen[key]=true;parts.push(part.trim());}
  });
  return (parts.join('。')+(parts.length?'。':'')).replace(/[*＊]+/g,'');
}
// END PUBLIC NARRATIVE V10

/**
 * 文章標題，與 pipeline.py 的 article_title_detail 一致；重建郵件不另呼叫模型。
 * 格式照 168 聚財網〈168看電視〉張震文章：「張震：你買在高檔 神仙都難救！」。不設上限（2026/09/14）。
 * 至少 15 字（2026/09/17 管理者：「操作重音與心態！」太短）。算字只算中文、英文與數字，與 pipeline 同一套。
 * 來源依序：模型標題（數字與八成用字出自全部已驗證盤勢／教學原句，而且滿 15 字）
 *   → 教學重點的觀念標題（不足 15 字時接上說明的子句）→ 盤勢主題加上說明。
 */
var TITLE_PREFIX_ = '';   // 2026/09/15 起不加「張震：」前綴
var TITLE_MIN_CHARS_ = 15;
function titleChars_(text) {
  return (String(text || '').match(/[A-Za-z0-9_\u3400-\u9fff]/g) || []).length;
}
/** 好幾句串成一句時，從第一個六字以上的句子開始，接到滿 15 字為止（與 pipeline 的 _title_one_sentence 相同）。 */
function titleOneSentence_(text) {
  var body = String(text || '').trim();
  var pieces = body.split(/([？?！!。；;])/), sentences = [];
  for (var i = 0; i < pieces.length; i += 2) {
    var s = pieces[i].trim();
    if (s) { sentences.push([s, i + 1 < pieces.length ? pieces[i + 1] : '']); }
  }
  if (sentences.length <= 1) { return body; }
  var start = 0;
  for (var k = 0; k < sentences.length; k++) { if (sentences[k][0].length >= 6) { start = k; break; } }
  var out = '';
  for (var j = start; j < sentences.length; j++) {
    out += sentences[j][0];
    if (titleChars_(out) >= TITLE_MIN_CHARS_) { return out; }
    out += sentences[j][1];
  }
  return out.replace(/[？?！!。；;]+$/, '');
}
/** 觀念標題太短時接上說明的子句，接到滿 15 字；超過 40 字就不用這一條。 */
function titleExtend_(head, detail) {
  var title = String(head || '').trim();
  var clauses = String(detail || '').split(/[，,。；;！!？?\n]/);
  for (var i = 0; i < clauses.length; i++) {
    if (titleChars_(title) >= TITLE_MIN_CHARS_) { break; }
    var clause = clauses[i].replace(/^[ 　：:「」]+|[ 　：:「」]+$/g, '');
    if (titleChars_(clause) < 2 || /張震|張正|講者|\d{4}[/年]/.test(clause)) { continue; }
    title += '，' + clause;
  }
  var n = titleChars_(title);
  return (n < TITLE_MIN_CHARS_ || n > 40) ? '' : title;
}
function articleTitle_(signals) {
  var rows=(signals.market||[]).filter(function(r){return r._evidence_verified;});
  var source=rows.map(function(r){return String(r.text||'')+(r.evidence||[]).join('');}).join('').replace(/\s/g,'');
  var ordered=rows.filter(function(r){return r.kind!=='view';}).concat(rows.filter(function(r){return r.kind==='view';}));
  for(var i=0;i<ordered.length;i++) {
    var raw=String(ordered[i].headline||'').trim();
    if(!raw)continue;
    var title=raw.replace(/^(?:張震|張正)\s*[：:]\s*/,'').replace(/^[ ①：:。「」]+|[ ①：:。「」]+$/g,'');
    var body=title.replace(/[！!？?]+$/,'');
    var chars=body.match(/[A-Za-z0-9_\u3400-\u9fff]/g)||[], nums=body.match(/\d+(?:\.\d+)?/g)||[];
    if(body && !/張震|張正|講者|盤勢與操作紀錄|\d{4}[/年]/.test(body) &&
       nums.every(function(n){return source.indexOf(n)>=0;}) &&
       (!chars.length || chars.filter(function(c){return source.indexOf(c)>=0;}).length/chars.length>=0.8)) {
      var ending=title.slice(body.length,body.length+1)||'！';
      var one=titleOneSentence_(body);
      if(titleChars_(one)<TITLE_MIN_CHARS_)continue;
      return TITLE_PREFIX_+one+({'!':'！','?':'？'}[ending]||ending);
    }
  }
  for(var j=0;j<rows.length;j++) {
    if(rows[j].kind!=='view')continue;
    var text=String(rows[j].text||'');
    var m=text.match(/^\s*([^：:。！？!?\n]{2,40})[：:]/);
    if(!m || /張震|張正|講者|\d{4}[/年]/.test(m[1]))continue;
    var extended=titleExtend_(m[1], text.slice(m[0].length).replace(/張震|張正/g,''));
    if(extended)return TITLE_PREFIX_+extended+'！';
  }
  var macro=rows.filter(function(r){return r.kind!=='view';}).map(function(r){return r.text||'';}).join(' '),themes=[];
  [[/CPI|消費者物價指數/,'CPI動向'],[/PPI|生產者物價指數/,'PPI動向'],[/利率|聯準會/,'利率決策'],
   [/量縮|成交量縮/,'量縮整理'],[/震盪|壓縮|橫盤/,'震盪盤勢'],[/外資|資金/,'外資動向'],
   [/美元|匯率/,'匯率變化'],[/融資/,'融資變化'],[/缺口|支撐|關卡/,'技術關卡']].forEach(function(pair){if(pair[0].test(macro))themes.push(pair[1]);});
  return TITLE_PREFIX_+(themes.length?themes.slice(0,2).join('與')+'，本集盤勢與操作重點整理':'本集市場觀察與會員操作重點整理');
}
