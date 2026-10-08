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
var PUBLIC_CONFIRMED_NAMES = {"普威": ["4966", "譜瑞-KY"], "普位": ["4966", "譜瑞-KY"], "譜位": ["4966", "譜瑞-KY"], "普銳": ["4966", "譜瑞-KY"], "普瑞": ["4966", "譜瑞-KY"], "加折": ["3533", "嘉澤"], "加哲": ["3533", "嘉澤"], "加澤": ["3533", "嘉澤"], "家澤": ["3533", "嘉澤"], "茂聯": ["3665", "貿聯-KY"], "出金城": ["8210", "勤誠"], "初清程": ["8210", "勤誠"], "00981A": ["00981A", "主動統一台股增長"], "主動統一台股增長": ["00981A", "主動統一台股增長"], "瑞獄": ["2379", "瑞昱"], "雨沾": ["8271", "宇瞻"], "維星": ["2377", "微星"], "邦店": ["2344", "華邦電"], "連電": ["2303", "聯電"], "大力光": ["3008", "大立光"], "利基電": ["6770", "力積電"], "宜頂": ["5289", "宜鼎"], "移頂": ["5289", "宜鼎"], "以頂": ["5289", "宜鼎"], "川服": ["2059", "川湖"], "木德": ["3563", "牧德"], "四星KY": ["3661", "世芯-KY"], "四星": ["3661", "世芯-KY"], "宏準": ["2354", "鴻準"], "弘準": ["2354", "鴻準"], "紅準": ["2354", "鴻準"], "弘海": ["2317", "鴻海"], "連陽": ["3014", "聯陽"], "聯揚": ["3014", "聯陽"], "連揚": ["3014", "聯陽"], "威星": ["2377", "微星"], "想碩": ["5269", "祥碩"], "享碩": ["5269", "祥碩"], "紅蠢": ["2354", "鴻準"], "降碩": ["5269", "祥碩"], "詳碩": ["5269", "祥碩"], "利望": ["3529", "力旺"], "金星科": ["6533", "晶心科"], "精星科": ["6533", "晶心科"], "金新科": ["6533", "晶心科"], "蹲態": ["3545", "敦泰"], "漢糖": ["2404", "漢唐"], "漢堂": ["2404", "漢唐"], "秦城": ["8210", "勤誠"], "立即電": ["6770", "力積電"], "立基電": ["6770", "力積電"], "力基電": ["6770", "力積電"], "城成": ["8210", "勤誠"], "勤城": ["8210", "勤誠"], "晶成": ["8210", "勤誠"], "玉金光": ["3406", "玉晶光"], "環球金": ["6488", "環球晶"], "國具": ["2327", "國巨*"], "聖輝": ["5536", "聖暉*"], "聖暉": ["5536", "聖暉*"], "有達": ["2409", "友達"], "奇鴻": ["3017", "奇鋐"], "瑞澤": ["7703", "銳澤"], "楊基工程": ["6691", "洋基工程"], "翔碩": ["5269", "祥碩"], "索羅門": ["2359", "所羅門"], "秦成": ["8210", "勤誠"], "立積電": ["6770", "力積電"], "情晨": ["8210", "勤誠"], "偉穎": ["6669", "緯穎"], "星KY": ["3661", "世芯-KY"], "四新科": ["4916", "事欣科"], "四新KY": ["3661", "世芯-KY"], "連發科": ["2454", "聯發科"], "立旺": ["3529", "力旺"], "紅柱恩": ["2354", "鴻準"], "創億": ["3443", "創意"], "致源": ["3035", "智原"], "隱身版光通訊": ["2402", "毅嘉"], "隱藏版光通訊": ["2402", "毅嘉"], "隱藏版光訊": ["2402", "毅嘉"], "意嘉": ["2402", "毅嘉"], "億嘉": ["2402", "毅嘉"], "益嘉": ["2402", "毅嘉"], "義嘉": ["2402", "毅嘉"], "易嘉": ["2402", "毅嘉"], "一嘉": ["2402", "毅嘉"], "毅加": ["2402", "毅嘉"], "意加": ["2402", "毅嘉"], "益加": ["2402", "毅嘉"], "億加": ["2402", "毅嘉"], "玉龍": ["2201", "裕隆"], "浴龍": ["2201", "裕隆"], "育龍": ["2201", "裕隆"], "預龍": ["2201", "裕隆"], "御龍": ["2201", "裕隆"], "遇龍": ["2201", "裕隆"], "愈龍": ["2201", "裕隆"], "欲龍": ["2201", "裕隆"], "喻龍": ["2201", "裕隆"], "郁龍": ["2201", "裕隆"], "譽龍": ["2201", "裕隆"], "豫龍": ["2201", "裕隆"], "裕龍": ["2201", "裕隆"], "雨龍": ["2201", "裕隆"], "羽龍": ["2201", "裕隆"], "語龍": ["2201", "裕隆"], "宇龍": ["2201", "裕隆"], "玉隆": ["2201", "裕隆"], "浴隆": ["2201", "裕隆"], "育隆": ["2201", "裕隆"], "預隆": ["2201", "裕隆"], "御隆": ["2201", "裕隆"], "遇隆": ["2201", "裕隆"], "愈隆": ["2201", "裕隆"], "欲隆": ["2201", "裕隆"], "喻隆": ["2201", "裕隆"], "郁隆": ["2201", "裕隆"], "譽隆": ["2201", "裕隆"], "豫隆": ["2201", "裕隆"], "雨隆": ["2201", "裕隆"], "羽隆": ["2201", "裕隆"], "語隆": ["2201", "裕隆"]};
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
  // 正式名稱以 -KY 結尾時，原文跟在後面的 KY 一起換掉（「茂聯-KY」「茂聯KY」→「貿聯-KY」，不是「貿聯-KY-KY」）。
  var ky = /-KY$/i.test(fixed) && !/KY$/i.test(heard) ? '(?:\\s*-?\\s*KY)?' : '';
  if (fixed.indexOf(heard) === 0 && fixed.length > heard.length) {
    return text.replace(new RegExp(esc(heard) + '(?!' + esc(fixed.slice(heard.length)) + ')' + ky, ky ? 'gi' : 'g'), fixed);
  }
  return ky ? text.replace(new RegExp(esc(heard) + ky, 'gi'), fixed) : text.split(heard).join(fixed);
}
/** 已經存進資料的連寫（貿聯-KY-KY、貿聯-KYKY）收成一個。 */
function collapseKy_(text) { return String(text || '').replace(/-KY(?:\s*-?\s*KY)+/gi, '-KY'); }
/* 內部判斷字眼：這一列「為什麼被歸到這一類」是流程，不是講者說的話。
   與 pipeline.py 的 _META_CLAUSE 同一份清單；寫在這裡，已經存進試算表的舊說明呈現時也會乾淨。
   2026/09/15 的台積電寫成「……但在當日節目中並未將台積電列為當日會員實際買進或持有的個股明細，
   故列入市場教學與觀察範疇」，讀的人要的是他對台積電講了什麼（管理者回報，2026/09/16）。 */
var META_CLAUSE_RE_ = /歷史回顧|列為觀望|列入觀望|改列|日期未明|依原文語氣|依上下文判|原判|原為買入|原為賣出|不列入當日|非影片當日|非當日執行|依規則|歷史買進|歷史賣出|並未將|未將本檔|(?:並)?未列(?:為|入)|個股明細|故列入|因此列入|列入市場教學|(?:觀察|教學|觀望)範疇|歸類為|分類為|列為.{0,10}?(?:觀望|等待|等候|追蹤|觀察|避開)|屬於.{0,10}?(?:觀察|觀望|避開)(?:的)?(?:對象|標的|名單)|前述價位屬於|並非(?:本日|當日|今日)(?:再次)?(?:買進|買入|賣出)(?:的)?(?:通知|指令)|既有標的的行情與條件追蹤|未新增(?:本日|當日|今日)(?:買賣|交易)(?:通知|指令)?|屬節目看法|不能視為已確定的交易|回顧價位不代表(?:本日|當日|今日)(?:再次)?買進/;
/** 去掉寫作過程，只留下其中的實際事實與警示。 */
function stripEditorialWrappers_(text) {
  return String(text || '')
    .replace(/(?:原文|逐字稿)(?:以|用)(「[^」]+」|[^。；]+?)作為(?:風險)?(?:警示|提醒)/g, '$1')
    .replace(/(?:逐字稿|影片)(?:補充的|補充)?(?:重點|說明)(?:是|為)[：:]?/g, '')
    .replace(/(?:原文|逐字稿)(?:補充|提到|提及|強調|提醒|指出|說明)(?:的)?/g, '')
    .replace(/原文回顧/g, '先前').replace(/原文強調的/g, '');
}
/* 公開文字不寫通知管道（2026/10/06）：前台只呈現分類與這一檔的說明。
   規則與 pipeline.py 的 _NOTICE_WORD_RULES 同一組、同一順序；寫在這裡，已經存進試算表的舊說明呈現時也會乾淨。 */
var NOTICE_WORD_RULES_ = [
  [/(?:新加入|新進|剛加入)(?:的)?會員(?:們)?(?=(?:可以|可)?(?:買進|買入|加碼|賣出|續抱))/g, ''],
  [/(?:會員簡訊|簡訊通知(?!(?:了)?(?:所有|全部|全體|全國|一般|新進|新加入)?(?:的)?會員)|盤中(?:即時)?通知|即時通知|會員通知)(?:的)?(?:當日|當天|今日|當時)?(?:通知|說明|指出|提到|要求|內容)?(?:在|以)?[：:]?/g, ''],
  [/依(?:照|據)?(?:當日|今日|盤中|上述|該)?(?:的)?(?:通知|簡訊|指示)(?:內容|指示)?[，,]?/g, ''],
  [/(?:我)?(?:告訴|跟)我的會員(?:講|說)?[，,]?/g, ''],
  [/[，,]?(?:全部|所有|全體)(?:的)?會員都(?:有|已經?)?通知(?:了|到)?/g, ''],
  [/(?:已經?|並|也|就|都)?(?:發(?:出|送|布)?)?(?:簡訊)?通知(?:了)?(?:所有|全部|全體|全國|一般)?(?:的)?(?:新進|新加入)?(?:的)?會員(?:們)?/g, ''],
  [/(?:發(?:出|送|布)|收到|接獲)(?:的)?(?:簡訊|通知)/g, ''],
  [/簡訊/g, '']
];
function scrubNoticeWords_(text) {
  text = String(text || '');
  if (!/通知|簡訊|告訴我的會員|新加入|新進|剛加入/.test(text)) return text;
  NOTICE_WORD_RULES_.forEach(function (rule) { text = text.replace(rule[0], rule[1]); });
  return text.replace(/([，,])[，,]+/g, '$1').replace(/(^|[。；\n])[，,：:]+/g, '$1');
}
/** 公開 API 送出前的最後一關：物件裡每一段文字都過一次，歷程、出場原因這些沒有經過 publicNarrative_ 的欄位也算。 */
function publicScrubDeep_(value) {
  if (typeof value === 'string') return scrubNoticeWords_(value);
  if (Array.isArray(value)) return value.map(publicScrubDeep_);
  if (value && Object.prototype.toString.call(value) === '[object Object]') {
    var out = {};
    Object.keys(value).forEach(function (k) { out[k] = publicScrubDeep_(value[k]); });
    return out;
  }
  return value;
}
/** 交易價留在價位欄，說明保留技術、消息與風險。 */
function smsNoteBody_(text, row) {
  row = row || {};
  text = String(text || '').replace(/(?:會員簡訊|盤中(?:即時)?通知)(?:的)?(?:當日|當天|今日|當時)?(?:通知|說明|指出|提到|要求|內容)?(?:在|以)?[：:]?/g, '');
  var known = String(row.price || '').match(/\d+(?:\.\d+)?/g) || [];
  return text.split(/(?<=[。！？；])/).map(function(sentence) {
    var trade = /買進|買入|買回|賣出|賣掉|出清|加碼|減碼|成本|成交|平盤/.test(sentence);
    var removePrice = function(match, index) {
      // 同一個子句前面講的是 EPS／配息，後面一串數字都是它的數字（「EPS從3.5元、5元、6元提升至10元、11元」）。
      if (/EPS|每股盈餘|股利|配息|除息|除權|權利金/i.test(sentence.slice(Math.max(0, index - 12), index) + sentence.slice(0, index).split(/[，；。]/).pop())) return match;
      // 賺賠與漲跌的幅度不是交易價（10/05 聖暉「賺取6、70塊的持股賣出」被挖成「賺取6、的持股賣出」）。
      if (/(?:賺|賠|獲利|虧損?|價差|差價|漲|跌)(?:了|取|進)?(?:約|近|大約|將近|快)?\s*$/.test(sentence.slice(Math.max(0, index - 12), index))) return match;
      if (!trade && !(match.match(/\d+(?:\.\d+)?/g) || []).some(function(n) { return known.indexOf(n) >= 0; })) return match;
      return '';
    };
    // 「6、70塊」「74到76塊」是一個數量，整組一起留或一起拿，不能只挖後半。
    sentence = sentence.replace(/(?:在|以|於)?(?:平盤)?(?:\d+(?:\.\d+)?\s*[、~～到至]\s*)?\d+(?:\.\d+)?\s*(?:元|塊)(?:附近|以上|以下|之上|之下)?/g, removePrice);
    known.forEach(function(price) {
      var escaped = price.replace(/\./g, '\\.');
      sentence = sentence.replace(new RegExp('(?:在|以|於)?(?:平盤)?(?<![\\d.])'+escaped+'(?![\\d.])(?:以上|以下|之上|之下)', 'g'), removePrice);
    });
    return sentence;
  }).join('').replace(/^[ ，：:。；]+|[ ，：:]+$/g, '');
}
function publicSmsNote_(text, row) { return smsNoteBody_(publicNarrative_(text, row), row); }
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
  var sms = /會員簡訊|盤中(?:即時)?通知/.test(String(text || '')) || /^CMONEY-/.test(String(row.videoId || row.sourceId || row['來源影片ID'] || ''));
  // 語音稿把「不准」聽寫成「不準」（「會員不準賣」）；後面接動作才換，「預測不準」不動。
  text = toTraditional_(text);
  text = scrubNoticeWords_(text);
  text = stripEditorialWrappers_(text).replace(/不準(?=給我|亂|再|去|賣|買|碰|追|用|借|操作|進場|放空|做空)/g, '不准')
    .replace(/(被|遭|獲)(?:張震|張正|張總|張中|講者)(?:老師)?/g, '$1')   // 被動句裡的人名也不寫（v96）
    .replace(/講者(?:本人)?(?:的(?=會員|持股|看法|說法))?/g, '')        // 其餘位置的「講者」也不留（v142）
    .replace(/(^|[，,。；;：:、「『（(\s]|雖然|但是|但|而且|而|並且|並|且|因為|所以|目前|現在|今天|今日|昨天|昨日|先前|之前|當時|這次)(?:張震|張正|張總|張中|講者)(?:老師)?(?:本人)?(?:的)?/g,'$1')
    // 只寫「老師」當主詞的句子也一樣（2026/10/06）；只在子句開頭、後面接動作時拿掉。
    .replace(/(^|[，,。；;：:、\s]|雖然|但是|但|而且|而|並且|並|且|因為|所以)老師(?=強調|認為|建議|指出|表示|提醒|判斷|明確|自稱|特別|持續|已|亦|昨日|今日|直言|看好|選擇|並|也|則|以|在|於|將|會|指示|通知|帶領|偏好|這是|自己|手中|說|講|提到|點名|預期|預計|先前|目前|本人)/g,'$1')
    .replace(/老師(?:本人)?(?:的(?=會員|持股|核心|成本|部位|買點)|(?=手中|自己的|持股|持有|買進|賣出|看好|已經|所|最不))/g,'')
    .replace(/(^|[。；;])在(?=今日|今天|昨日|昨天)/g,'$1');
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
  var seen={},parts=[],earlier='';
  text.split(/[。；;]/).forEach(function(part){
    var key=part.replace(/回顧|過往|目前|\s|[，,]/g,'');
    // 比對時不算本股自己的名稱（2026/10/08 譜瑞-KY：第二句只多了自己的名字）。
    var probe=name ? key.split(name).join('') : key;
    if(key && !seen[key] && !restatesEarlier_(probe,earlier)){seen[key]=true;earlier+=key;parts.push(part.trim());}
  });
  text = collapseKy_((parts.join('。')+(parts.length?'。':'')).replace(/[*＊]+/g,''));
  return sms ? smsNoteBody_(text, row) : text;
}
/** v107：與 pipeline.py 的 _restates_earlier 同一規則：換個起頭把前文再講一次的句子不重複列。 */
function restatesEarlier_(key, earlier) {
  key = key.replace(/^(?:其|顯示|這|此|以此|並|且|也)/, '');
  if (key.length < 10 || !earlier) return false;
  var nums = key.match(/\d+(?:\.\d+)?/g) || [];
  if (nums.some(function (n) { return earlier.indexOf(n) < 0; })) return false;
  var hit = 0, total = key.length - 1;
  for (var i = 0; i < total; i++) { if (earlier.indexOf(key.substr(i, 2)) >= 0) hit++; }
  return hit / total >= 0.5;
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
/** 同一句話連講兩次，標題只留一次（v102，與 pipeline 的 _title_drop_repeats 相同）。 */
function titleDropRepeats_(text) {
  var parts = String(text || '').split(/([，,、])/), out = [], last = '';
  for (var i = 0; i < parts.length; i += 2) {
    var key = parts[i].replace(/\s/g, '');
    if (key && key === last) { continue; }
    out.push(parts[i] + (i + 1 < parts.length ? parts[i + 1] : ''));
    if (key) { last = key; }
  }
  return out.join('').replace(/[，,、]+$/, '');
}
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
      var one=titleOneSentence_(titleDropRepeats_(body));
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

/* 盤中通知原文的公開版本（v104，2026/10/02 管理者：簡訊明講的價位當作不存在，盤中即時通知的原文也一樣）。

   作法是「講到價位的那一個子句整句不顯示」，不是把數字挖掉——挖掉數字會留下「請於元以下買進」這種讀不通的句子。
   以逗號、分號切子句；句子裡每個子句都講價位，整句就不顯示；整行都不顯示時那一行拿掉。
   算價位的寫法：數字＋元／塊；數字＋以上／以下／之上／之下／附近；成本、買在、賣在、目標價、挑戰、突破、跌破、站上、守住、
   停損、停利、上看、壓力、支撐後面接兩位數以上的數字（後面是點、日、天、張、%、億、均線這類單位的不算）。
   股票代號（6770力積電）、日期、百分比、成交量都不受影響。後台會員簡訊的「原始內容」與試算表照舊是全文。 */
var NOTICE_PRICE_RE_ = /\d+(?:\.\d+)?\s*(?:元|塊)|\d+(?:\.\d+)?\s*(?:以上|以下|之上|之下|附近)|(?:成本|買在|賣在|目標價?|挑戰|突破|跌破|站上|站穩|守住|停損|停利|上看|下看|壓力|支撐)(?:價|位)?\s*(?:在|為|是|約)?\s*\d{2,}(?:\.\d+)?(?!\d|\.\d|\s*(?:點|日|天|檔|張|%|％|月|年|億|萬|均|分|倍|根|次|週|周|MA))/;
function publicNoticeText_(text) {
  var original = String(text == null ? '' : text);
  var shown = original.split(/\n/).map(function (line) {
    var out = '';
    line.split(/(?<=[。！？!?])/).forEach(function (sentence) {
      var end = (sentence.match(/[。！？!?]+$/) || [''])[0];
      var body = end ? sentence.slice(0, sentence.length - end.length) : sentence;
      var kept = body.split(/[，,；;]/).map(function (c) { return c.trim(); })
        .filter(function (c) { return c && !NOTICE_PRICE_RE_.test(c); });
      if (kept.length) { out += kept.join('，') + end; }
    });
    return out.trim();
  }).filter(String).join('\n');
  // 全文每個子句都含價位時，舊規則會留下空白通知。不能只刪數字後
  // 把附價位條件的買賣寫成無條件指示；明示需回來源核對完整條件。
  return shown || (original.trim() ? '這則通知含價位條件，公開版不顯示操作原句；請查看來源原文確認完整標的與條件。' : '');
}
/** 這段原文有沒有被拿掉東西（給信件與 LINE 的附註用）。 */
function noticeTextTrimmed_(text) {
  return publicNoticeText_(text).replace(/\s/g, '').length < String(text == null ? '' : text).replace(/\s/g, '').length - 2;
}
