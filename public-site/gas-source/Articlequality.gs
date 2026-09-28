/** Fixed article structure, shared facts and no additional AI call on refresh.
    結構與 pipeline.py 的 canonical_article 一致（2026/09/15 v22）：
    標題一行不編號，① 盤勢總覽 ② 會員操作紀錄（②-1／②-2／②-3）③ 教學重點；不再有「基本資訊」。
    2026/09/17 v44 起不再寫 ④ 風險揭露：信尾固定呈現（mailRiskHtml_），文章裡再寫一次就重複了。
    全文由試算表與已驗證稽核重建，不沿用舊文章段落，所以舊的六章文章重寫後就是新結構。 */
function enforceArticleRecords_(article, signals, d) {
  // ① 放盤勢；講者的操作邏輯與教學重點（kind=view）放 ③，不在兩章重複出現。
  var macro = (signals.market || []).filter(function(r){return r._evidence_verified && r.kind !== 'view';})
    .map(function(r){return '• ' + publicNarrative_(r.text);});
  var lessons = (signals.market || []).filter(function(r){return r._evidence_verified && r.kind === 'view';})
    .map(function(r){return '• ' + publicNarrative_(r.text);});
  var kept = [], length = 0;
  macro.forEach(function(line){if(length+line.length+1<=2400){kept.push(line);length+=line.length+1;}});
  var chapter = recordChapter_(signals,d);
  // 日期未明的回顧不再另列一段：有現況看法的已經進了觀望類（上面的表格就有），
  // 沒有的本來就不列。另列會讓信件出現網站「翻到某一天」查不到的股票（2026/09/10）。
  var past = signals._past || [];
  var appendix = [];
  past.forEach(function(r){appendix.push('• ' + r._date + ' ' + r.name + '：' + publicNarrative_(r.reason,r));});
  if(appendix.length){chapter=chapter.replace('②-2', '補記（記在其他日期，不列入當日買賣）\n\n'+appendix.join('\n')+'\n\n②-2');}
  return ['文章標題：' + articleTitle_(signals),
    '① 盤勢總覽重點整理\n\n'+(kept.join('\n') || '本集未整理出可引用的盤勢重點。'),
    chapter.trim(), ('③ 分析師操作邏輯與教學重點\n\n' +
      (lessons.length ? lessons.join('\n') : '本集未整理出可引用的操作教學。'))].join('\n\n');
}

function attachArticleEvidence_(signals, d) {
  var rows = [];
  try { rows = readSheetObjects_('逐字稿判讀稽核'); } catch(e) { return signals; }
  var items = [], latest = null;
  rows.forEach(function(r){
    // 規則版本從 evidence-v2 升到 context-json-v3 之後，這裡仍只認 evidence-v2，
    // 於是整批大盤摘要都被略過，信件盤勢章（當時第③章）變成「本支影片沒有已驗證的大盤摘要」（2026/09/11）。
    if(fmtDate_(r['影片日期'])!==d || !/^(evidence-v2|context-json-v\d+)$/.test(String(r['規則版本']||''))){return;}
    try {
      var p=JSON.parse(r['判讀JSON']); p.video=r['來源影片ID']; items.push(p);
      if(p.category==='manifest' && p.item.status==='published'){latest={video:p.video,batch:p.batch};}
    } catch(e) { Logger.log('略過無效稽核JSON'); }
  });
  signals.market=[];signals.history=[];signals._past=[];
  items.forEach(function(p){
    if(!latest || latest.video!==p.video || latest.batch!==p.batch){return;}
    if(p.category==='market' && p.item._evidence_verified){signals.market.push(p.item);}
    if(p.category==='history' && p.item._evidence_verified){signals.history.push(p.item);}
    if((p.category==='buy'||p.category==='sell') && p.item._date && p.item._date!==d){signals._past.push(p.item);}
  });
  return signals;
}
