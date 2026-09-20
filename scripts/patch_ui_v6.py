from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def edit(name, fn):
    p=ROOT/name;p.write_text(fn(p.read_text(encoding='utf-8')),encoding='utf-8')
edit('pipeline/pipeline.py',lambda s:s.replace('and is_non_stock(name))','and is_non_stock(name)[0])').replace('    _CODE_MAP = m','    m["00981A"] = "主動統一台股增長"\n    _CODE_MAP = m').replace('1 <= v <= 10000','1 <= v <= 100000'))
edit('apps-script/Adminpipeline.gs',lambda s:s.replace("'初清程':['8210','勤誠']", "'初清程':['8210','勤誠'], '00981A':['00981A','主動統一台股增長'], '主動統一台股增長':['00981A','主動統一台股增長'], '瑞獄':['2379','瑞昱'], '雨沾':['8271','宇瞻'], '維星':['2377','微星'], '邦店':['2344','華邦電'], '連電':['2303','聯電'], '大力光':['3008','大立光'], '利基電':['6770','力積電'], '宜頂':['5289','宜鼎']"))
edit('apps-script/Cachebuilder.gs',lambda s:s.replace("var hit = CACHE.get('codemap');", "var hit = CACHE.get('codemap-v6');").replace("CACHE.put('codemap',", "CACHE.put('codemap-v6',").replace("  var out = { byCode: byCode, byName: byName };", "  byCode['00981A'] = {name:'主動統一台股增長',market:'上市',industry:'ETF'};\n  byName['主動統一台股增長'] = '00981A';\n  var out = { byCode: byCode, byName: byName };"))
# 失效處一律同步快取 key，否則重新建表後仍可能讀六小時舊版本。
for p in (ROOT/'apps-script').glob('*.gs'):
    edit(p.relative_to(ROOT),lambda s:s.replace("CACHE.remove('codemap')","CACHE.remove('codemap-v6')"))
edit('apps-script/Evidencequality.gs',lambda s:s.replace('pair[1], r.price, r.reason','pair[1], displayPrice_(r.price,r.price_evidence,r.reason), r.reason').replace('r.code, r.price, r.reason','r.code, displayPrice_(r.price,r.price_evidence,r.reason), r.reason'))
def quotes(s):
    s=s.replace('    code: String(j.symbol || code),','    date: String(j.date || "").replace(/-/g,"/"),\n    code: String(j.symbol || code),',1)
    s=s.replace("    code: code, name: m.n ||", "    date: String(m.d || '').replace(/^(\\d{4})(\\d{2})(\\d{2})$/, '$1/$2/$3'),\n    code: code, name: m.n ||")
    s=s.replace('      stamp\n    ]);','      stamp, q.open || "", q.high || "", q.low || "", q.date || ""\n    ]);',1)
    s=s.replace("sh.getRange(1, 1, 1, 8).setValues([['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間']]);", "sh.getRange(1, 1, 1, 12).setValues([['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']]);")
    s=s.replace('sh.getRange(2, 1, rows.length, 8).setValues(rows);','sh.getRange(2, 1, rows.length, 12).setValues(rows);',1)
    s=s.replace("      time: qTime_(r['更新時間'])", "      open: Number(r['開']) || null, high: Number(r['高']) || null, low: Number(r['低']) || null,\n      date: r['行情日期'] ? fmtDate_(r['行情日期']) : '',\n      time: qTime_(r['更新時間'])",1)
    s=s.replace("  return daily;\n}\n\n/** 已落地的 60 分 K", "  return intradayPreview_(daily, getQuoteCache()[code], Utilities.formatDate(new Date(), TZ, 'yyyy/MM/dd'));\n}\n\n/** 已落地的 60 分 K",1)
    return s
edit('apps-script/Quoteservice.gs',quotes)
edit('apps-script/Setup.gs',lambda s:s.replace("'即時快取': ['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間']", "'即時快取': ['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']"))
def js(s):
    # 同一支展示函式給瀏覽器；部署前由此版本同步，測試逐案例比對 Python/GAS。
    helper=(ROOT/'apps-script/Presentationquality.gs').read_text(encoding='utf-8')
    helper=helper[helper.index('function displayPrice_'):helper.index('/** 僅供畫面日K')]
    s=s.replace('  function priceCell(r) {',helper+'\n  function priceCell(r) {',1)
    s=s.replace("esc(r.price || '未說明')", "esc(displayPrice_(r.price, r.price_evidence, r.reason))")
    s=s.replace("esc(r.price || (r.dir === '會員持股' ? '續抱' : '未說明'))", "esc(displayPrice_(r.price, r.price_evidence, r.reason))")
    s=s.replace('  function showDay(date) {','  var dayRequest = 0;\n  function showDay(date) {\n    var request = ++dayRequest;')
    start=s.index('  function showDay(');end=s.index('  /* 有紀錄的日期清單',start)
    part=s[start:end].replace('withSuccessHandler(function (r) {','withSuccessHandler(function (r) {\n      if (request !== dayRequest) return;').replace('withFailureHandler(function () {','withFailureHandler(function () {\n      if (request !== dayRequest) return;')
    s=s[:start]+part+s[end:]
    start=s.index('  function loadPerf()');end=s.index("  document.querySelectorAll('[data-perf]')",start)
    part=s[start:end].replace('      if (!p.series.length) {','      if (!p || !p.series || !p.series.length) {\n        if (perfSeries) perfSeries.setData([]);').replace("$('perfNote').textContent = p.note;","$('perfNote').textContent = (p && p.note) || '背景績效尚未備齊。';").replace('withFailureHandler(function () {','withFailureHandler(function () {\n      $(\'perfChart\').classList.remove(\'is-loading\');\n      if (perfSeries) perfSeries.setData([]);')
    s=s[:start]+part+s[end:]
    s=s.replace('  function openDetail(code) {','  var detailRequest = 0;\n  function openDetail(code) {\n    var request = ++detailRequest;')
    start=s.index('  function openDetail(');end=s.index('  function renderHeader',start)
    part=s[start:end]
    for fn in ['renderHeader','renderTrackerBlock','renderRecordBlock','renderFundBlock']:
        part=part.replace('withSuccessHandler('+fn+')','withSuccessHandler(function(data){if(request === detailRequest) '+fn+'(data);})')
    part=part.replace('withFailureHandler(function () {', 'withFailureHandler(function () { if(request !== detailRequest) return;')
    s=s[:start]+part+s[end:]
    s=s.replace('  function closeDetail() {','  function closeDetail() {\n    ++detailRequest; ++candleRequest;')
    s=s.replace('  function loadCandles(code, p) {','  var candleRequest = 0;\n  function loadCandles(code, p) {\n    var request = ++candleRequest;\n    rawData = [];\n    setChartVisible(false);\n    $(\'periodNote\').textContent = \'K 線載入中…\';')
    start=s.index('  function loadCandles(');end=s.index('  /* 圖表區的顯示與隱藏',start)
    part=s[start:end].replace('withSuccessHandler(function (rows) {','withSuccessHandler(function (rows) {\n      if(request !== candleRequest || code !== currentCode || p !== period) return;').replace('withFailureHandler(function () {','withFailureHandler(function () {\n      if(request !== candleRequest) return;')
    part=part.replace("$('periodNote').textContent = PERIOD_NOTE[p] || '';", "$('periodNote').textContent = PERIOD_NOTE[p] || '';\n      var preview = rawData.find(function(r){return r._provisional;});\n      if(preview) $('periodNote').textContent += '　盤中預覽（快取更新 '+preview._asOf+'），尚非正式收盤日K，不計入績效。';")
    s=s[:start]+part+s[end:]
    return s
edit('apps-script/JavaScript.html',js)
def admin(s):
    s=s.replace('<div class="pnote" id="progNote"></div>', '<div id="publishState" class="pnote" aria-live="polite" hidden></div>\n        <div class="pnote" id="progNote"></div>')
    s=s.replace("  var cancelled = j.status === '已取消';", "  var contentReady = /文章已更新/.test(j.note || '') || done;\n  $('publishState').hidden = !contentReady;\n  $('publishState').textContent = contentReady ? '① 網站與郵件查詢內容：已更新（100%）\\n② 日 K／績效：' + (done ? '已完成' : j.status === '等待日K' ? '已排入背景續跑，尚未完成' : '背景處理中，依實際回報顯示進度') : '';\n  var cancelled = j.status === '已取消';",1)
    s=s.replace("資料已寫入，績效尚未完成；系統將自動續跑日K，無須重新投稿。", "網站內容已可閱讀；日K與績效在背景更新，尚未完成。系統會自動續跑，無須重新投稿。")
    return s
edit('apps-script/Admin.html',admin)
print('Updated UI guards, provisional candle cache, ETF and shared price rendering.')
