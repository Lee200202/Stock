/* LINE 管理者模式（2026/11/08 管理者核定）
 *
 * 身分：沿用後台「產生綁定碼」→ 在 LINE 輸入「管理者綁定 123456」（十分鐘有效）確認一次；
 *       之後輸入「管理者登入」切換成管理者、「取消管理者」回到使用者。沒綁定的人打關鍵字只會看到「請先綁定」。
 *       管理者模式不取消任何使用者功能（每日總覽、盤中通知、查詢照舊）。
 * 推送：每日寄信前複核（雲端排程 11:40）寫完紀錄、修正套用結束後，五分鐘排程送兩則：
 *       ① 今日稽核結果（摘要＋每一檔已修改的內容各一張卡）② 待你裁示（每一檔一張卡＋格式說明）；沒有待裁示只送 ①。
 * 裁示：管理者輸入「分類—說明」（可加日期與代號，預設今天）→ 確認卡 → 確認送出 → 寫進複核紀錄「管理者裁示」（待處理）
 *       → 呼叫雲端排程「盤勢有據 管理者裁示」的 API 觸發 → 雲端依原稿寫來源核對修正計畫、推 claude/line-ruling-日期-時間
 *       → GitHub line-ruling-apply.yml 套用、讀回、刷新網站、標「已完成／失敗」→ 這裡五分鐘內回報到 LINE。
 * 觸發設定（Script Properties，由擁有者自己貼，程式不讀出、不記錄值）：
 *       CLAUDE_RULING_FIRE_URL   雲端排程 API 觸發的 Fire URL
 *       CLAUDE_RULING_FIRE_TOKEN 同一個排程的 API token
 * 說明書：docs/雲端排程/雲端排程總覽.md 第三個排程、docs/stock-skills/skills/stock-cloud-sync/references/cloud-sync.md
 */

var LINE_ADMIN_RULE_TAB_ = '管理者裁示';
var LINE_ADMIN_RULE_COLS_ = ['建立時間', '裁示ID', '日期', '代號', '名稱', '裁示分類', '管理者說明', '來源', '管理者', '狀態',
  '分支', '結果', '更新時間', 'LINE回報'];
var LINE_ADMIN_CATS_ = ['會員持股', '觀望注意', '觀望不碰', '買入', '賣出', '不列'];
var LINE_ADMIN_CAT_ALIAS_ = { '持股': '會員持股', '注意': '觀望注意', '不碰': '觀望不碰', '買進': '買入', '賣掉': '賣出',
  '刪除': '不列', '移除': '不列', '不列入': '不列' };
var LINE_ADMIN_CAT_COLOR_ = { '會員持股': '#4A4FA3', '觀望注意': '#1F6F8B', '觀望不碰': '#8A6410', '買入': '#B4342C', '賣出': '#1E7B4F', '不列': '#8A958F', '指示': '#0A4A3C' };
var LINE_ADMIN_DAYS_BACK_ = 30;          // 與 line-ruling-apply.yml 的期限相同
var LINE_ADMIN_ITEM_BUBBLES_ = 10;       // 一則最多 12 張：摘要／格式一張＋十張＋「還有幾項」一張
var LINE_ADMIN_CTX_ = false;             // 這一個事件的發送者是不是開著管理者模式（決定快速回覆按鈕）
var LINE_ADMIN_FIRE_GAP_MS_ = 15 * 60 * 1000;

/* ---------------- 身分與模式 ---------------- */
function lineAdminUids_(force) {
  var cache = CacheService.getScriptCache();
  if (!force) { var hit = cache.get('line_admin_uids'); if (hit !== null) { return hit ? hit.split(',') : []; } }
  var uids = lineSubsRead_().rows.filter(function (s) { return s.tester && s.adminMode && s.friend !== 'blocked'; })
    .map(function (s) { return s.uid; });
  cache.put('line_admin_uids', uids.join(','), 600);
  return uids;
}
function lineAdminActive_(uid) {
  try { return !!uid && lineAdminUids_().indexOf(uid) >= 0; } catch (e) { return false; }
}
function lineAdminSetMode_(uid, ch, on) {
  var r = lineUpsertSub_(uid, ch, function (s) { s.adminMode = !!on && s.tester; s.lastSeen = lineNow_(); });
  lineAdminUids_(true);
  return r.sub;
}
function lineAdminOnReply_(uid, ch) {
  var s = lineFindSub_(uid, ch);
  if (!s.tester) {
    return [lineText_('管理者模式要先確認身分：到網站後台「LINE」分頁按「產生綁定碼」，十分鐘內在這裡輸入「管理者綁定 123456」' +
      '（換成畫面上的六位數）。綁定一次後，之後輸入「管理者登入」就能切換。', lineQuickMain_())];
  }
  lineAdminSetMode_(uid, ch, true);
  LINE_ADMIN_CTX_ = true;
  return [lineAdminHomeFlex_(true)];
}
function lineAdminOffReply_(uid, ch) {
  var s = lineFindSub_(uid, ch);
  if (s.tester && s.adminMode) { lineAdminSetMode_(uid, ch, false); }
  LINE_ADMIN_CTX_ = false;
  return [lineAdminExitFlex_()];
}
function lineAdminQuick_() {
  return [linePb_('今日稽核', 'a=adminreview', '今日稽核'), linePb_('待裁示', 'a=adminpending', '待裁示'),
          linePb_('裁示進度', 'a=adminprogress', '裁示進度'),
          linePb_('裁示格式', 'a=adminformat', '裁示格式'), linePb_('今日整理', 'a=today', '今日整理'),
          linePb_('查個股', 'a=askstock', '查個股', { inputOption: 'openKeyboard' }), lineMsg_('回到使用者', '取消管理者')];
}

/* ---------------- 文字指令 ---------------- */
/** 純函式：一句話是不是「分類—說明」的裁示。回傳 { date, code, name, cat, text } 或 null（不是裁示）。
    格式：〔日期〕〔代號或名稱〕分類—說明；日期可寫 11/06 或 2026/11/06，沒寫就是今天（交給呼叫端補）。 */
function lineAdminParseRuling_(raw) {
  var s = String(raw || '')
    .replace(/[０-９Ａ-Ｚａ-ｚ]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
    .replace(/[／]/g, '/').replace(/　/g, ' ').trim();
  if (s.length < 6 || s.length > 600) { return null; }
  var cats = LINE_ADMIN_CATS_.concat(Object.keys(LINE_ADMIN_CAT_ALIAS_)).sort(function (a, b) { return b.length - a.length; });
  var re = new RegExp('^(?:(?:(\\d{4})[/.-])?(\\d{1,2})[/.-](\\d{1,2})\\s*)?(?:(00\\d{3,4}[A-Z]?|\\d{4,6}[A-Z]?)\\s*)?' +
    '([^\\s0-9—－\\-–─~～:：]{0,12}?)\\s*(' + cats.join('|') + ')\\s*[—－\\-–─~～:：]+\\s*([\\s\\S]+)$', 'i');
  var m = s.match(re);
  if (!m) { return null; }
  var text = m[7].trim();
  if (text.replace(/\s/g, '').length < 4) { return null; }
  var name = String(m[5] || '').trim();
  if (name && /^(?:裁示|改成|改為|應為|請改|分類)$/.test(name)) { name = ''; }
  return { year: m[1] ? Number(m[1]) : 0, month: m[2] ? Number(m[2]) : 0, day: m[3] ? Number(m[3]) : 0,
           code: m[4] ? m[4].toUpperCase() : '', name: name, cat: LINE_ADMIN_CAT_ALIAS_[m[6]] || m[6], text: text };
}
/** 純函式（v220，2026/11/09 管理者：「勤誠需是買進或會員持股，應根據簡訊方向判定，請稍作調整」被當成查個股）：
    管理者用一般句子下的裁示。要有分類詞，也要有裁示語氣（應、需、改、調整、判定…）；問句不算。
    回傳 { year, month, day, code, lead, cats: [...], notice, text } 或 null。股票由呼叫端解析（lead 是分類詞前面那段）。 */
function lineAdminLooseRuling_(raw) {
  var s = String(raw || '')
    .replace(/[０-９Ａ-Ｚａ-ｚ]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); })
    .replace(/[／]/g, '/').replace(/　/g, ' ').trim();
  var flat = s.replace(/\s+/g, '');
  if (flat.length < 8 || flat.length > 600) { return null; }
  if (/(?:嗎|呢|\?|？)[。！!]*$/.test(flat) || /(?:能不能|可不可以|該不該|要不要|會不會|是不是|是否|請問)/.test(flat)) { return null; }
  if (!/(?:應|需|須|必須|改|調整|判定|判斷|列為|列成|列入|歸為|歸類|分類|裁示|修正|更正|才對|依照|根據)/.test(flat)) { return null; }
  var words = LINE_ADMIN_CATS_.concat(Object.keys(LINE_ADMIN_CAT_ALIAS_)).sort(function (a, b) { return b.length - a.length; });
  var found = [], taken = [];
  words.forEach(function (w) {
    var at = flat.indexOf(w);
    while (at >= 0) {
      var end = at + w.length;
      if (!taken.some(function (t) { return at < t[1] && end > t[0]; })) { taken.push([at, end]); found.push({ at: at, cat: LINE_ADMIN_CAT_ALIAS_[w] || w }); }
      at = flat.indexOf(w, end);
    }
  });
  if (!found.length) { return null; }
  found.sort(function (a, b) { return a.at - b.at; });
  var cats = [];
  found.forEach(function (f) { if (cats.indexOf(f.cat) < 0) { cats.push(f.cat); } });
  // 日期與代號在保留空白的句子裡找（「11/06 3008」去掉空白會黏成 11/063008）
  var spaced = s.replace(/\s+/g, ' '), dm = spaced.match(/(?:(\d{4})[/.-])?(\d{1,2})[/.-](\d{1,2})(?![\d元點])/);
  var rest = dm ? spaced.replace(dm[0], ' ') : spaced;
  var cm = rest.match(/(?:^|[^\d.])(00\d{3,4}[A-Z]?|\d{4,6}[A-Z]?)(?![\d.]|\s*元|\s*點|\s*張)/i);
  var cue = flat.search(/(?:應|需|須|必須|要|改|調整|判定|判斷|列為|列成|列入|歸為|歸類|分類|裁示|修正|更正|依照|根據)/);
  var lead = flat.slice(0, Math.min(found[0].at, cue < 0 ? flat.length : cue))
    .replace(dm ? dm[0].replace(/\s+/g, '') : '', '').replace(cm ? cm[1] : '', '').replace(/[，。、；：,.;:\s—－\-–─~～]+/g, '')
    .replace(/(?:的|之).*$/, '');
  return { year: dm && dm[1] ? Number(dm[1]) : 0, month: dm ? Number(dm[2]) : 0, day: dm ? Number(dm[3]) : 0,
           code: cm ? cm[1].toUpperCase() : '', lead: lead.length >= 2 && lead.length <= 12 ? lead : '',
           cats: cats, notice: /(?:簡訊|通知|訊息)/.test(flat), text: s };
}
/** 純函式：補上年份並檢查期限。today 'yyyy/MM/dd'。回傳 { date } 或 { error }。 */
function lineAdminRuleDate_(rule, today) {
  if (!rule.month) { return { date: today }; }
  var ty = Number(today.slice(0, 4)), y = rule.year || ty;
  var pad = function (n) { return ('0' + n).slice(-2); };
  var date = y + '/' + pad(rule.month) + '/' + pad(rule.day);
  if (!rule.year && date > today) { date = (ty - 1) + '/' + pad(rule.month) + '/' + pad(rule.day); }
  // 用 UTC 算日期，不受執行環境時區影響（測試機在 UTC 時 10/01 會被算成 9/30）
  var d = new Date(Date.UTC(Number(date.slice(0, 4)), rule.month - 1, rule.day)), t = new Date(Date.UTC(ty, Number(today.slice(5, 7)) - 1, Number(today.slice(8, 10))));
  if (isNaN(d.getTime()) || d.getUTCMonth() + 1 !== rule.month) { return { error: '日期看不懂：' + rule.month + '/' + rule.day }; }
  if (d > t) { return { error: '不能裁示未來的日期（' + date + '）。' }; }
  if ((t - d) / 86400000 > LINE_ADMIN_DAYS_BACK_) { return { error: '只能裁示最近 ' + LINE_ADMIN_DAYS_BACK_ + ' 天（' + date + ' 太早）。' }; }
  return { date: date };
}

/* ---------------- 複核紀錄 ---------------- */
function lineAdminBook_() { return typeof dailyReviewBook_ === 'function' ? dailyReviewBook_() : null; }
function lineAdminRuleSheet_(book) {
  var sh = book.getSheetByName(LINE_ADMIN_RULE_TAB_);
  if (!sh) {
    sh = book.insertSheet(LINE_ADMIN_RULE_TAB_);
    sh.getRange(1, 1, 1, LINE_ADMIN_RULE_COLS_.length).setValues([LINE_ADMIN_RULE_COLS_]).setFontWeight('bold').setBackground('#DDE7F0');
    sh.setFrozenRows(1);
  }
  return sh;
}
function lineAdminReadTab_(book, name, day) {
  var sh = book.getSheetByName(name);
  if (!sh || sh.getLastRow() < 2) { return []; }
  var v = sh.getRange(1, 1, sh.getLastRow(), sh.getLastColumn()).getDisplayValues();
  return v.slice(1).map(function (r, i) {
    var o = { _row: i + 2 };
    v[0].forEach(function (h, j) { o[String(h).trim()] = r[j]; });
    o['日期'] = String(o['日期'] || '').trim().replace(/-/g, '/');
    return o;
  }).filter(function (o) { return !day || o['日期'] === day; });
}
/** 某天（預設今天；今天還沒有就用最近一次）的每日摘要與差異明細。 */
function lineAdminReviewData_(day, latestIfMissing) {
  var book = lineAdminBook_();
  if (!book) { return null; }
  var sums = lineAdminReadTab_(book, '每日摘要', '');
  var summary = sums.filter(function (o) { return o['日期'] === day; }).pop();
  // 隨時查看（「今日稽核」「待裁示」）：今天沒有或今天沒跑（週末、休市、無影片）就看最近一次真的有複核的那天
  if (latestIfMissing && (!summary || String(summary['狀態'] || '').trim() === '未執行')) {
    var ran = sums.filter(function (o) { return String(o['狀態'] || '').trim() !== '未執行'; });
    if (ran.length) { summary = ran[ran.length - 1]; day = summary['日期']; }
  }
  if (!summary) { return null; }
  return { day: day, summary: summary, rows: lineAdminReadTab_(book, '差異明細', day), url: book.getUrl() };
}
function lineAdminPendingRows_(rows) {
  return (rows || []).filter(function (r) { return r['嚴重度'] === '待確認' && r['處理'] !== '已修正'; });
}

/* ---------------- 卡片（純函式，可離線測） ---------------- */
function lineAdminCut_(s, n) { s = String(s || '').replace(/\s+/g, ' ').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; }
function lineAdminName_(r) { return (r['名稱'] || '') + (r['代號'] ? ' ' + r['代號'] : ''); }
function lineAdminLine_(label, value) {
  return value ? fxBox_('vertical', [fxText_(label, { size: 'xxs', color: LINE_C_.muted }), fxText_(value, { size: 'sm', color: LINE_C_.ink })]) : null;
}
function lineAdminChip_(text, color) {
  return fxBox_('vertical', [fxText_(text, { size: 'xs', color: LINE_C_.white, weight: 'bold', align: 'center' })],
    { backgroundColor: color || LINE_C_.accent, cornerRadius: '999px', paddingAll: '4px', paddingStart: '10px', paddingEnd: '10px', flex: 0 });
}
function lineAdminFormatLines_() {
  // 2026/11/10 管理者：裁示格式以「股票 分類—說明」為主（股票寫名稱或代號都可以）。
  return [
    fxText_('格式：股票 分類—說明', { size: 'md', weight: 'bold', color: LINE_C_.ink }),
    fxText_('先寫股票（名稱或代號），再寫正確分類，「—」後面寫說明。沒寫日期就是今天；要改別天，在最前面加日期。', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
    fxBox_('vertical', [
      fxText_('勤誠 買入—簡訊明寫請買進，依簡訊方向判定', { size: 'sm', color: LINE_C_.ink }),
      fxText_('2330 會員持股—紀錄上還沒出場，持股說明照原稿寫', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
      fxText_('11/06 3008 觀望注意—等量縮回穩才買，屬於注意', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
      fxText_('鴻海 不列—只是舉例，沒有本股看法', { size: 'sm', color: LINE_C_.ink, margin: 'sm' })
    ], { backgroundColor: LINE_C_.soft, cornerRadius: '12px', paddingAll: '12px', margin: 'md' }),
    fxText_('也可以直接用一句話：「勤誠應依簡訊方向判定，列買入或會員持股」。句子裡要有股票、分類與「應、需、改、判定」這類字；寫到簡訊或通知，雲端會照當天會員通知的方向判定（簡訊優先，買入與會員持股同時成立就兩列都留）。', { size: 'sm', color: LINE_C_.ink, margin: 'md' }),
    fxText_('可用分類：' + LINE_ADMIN_CATS_.join('、'), { size: 'xs', color: LINE_C_.muted, margin: 'md' }),
    fxNote_('先按卡片上的「裁示這一檔」，就只要輸入「分類—說明」。送出前會再給你一張確認卡。')
  ];
}
function lineAdminFormatFlex_() {
  return lineFlex_('裁示格式：股票 分類—說明，例如「勤誠 買入—依簡訊方向判定」。',
    fxBubble_(fxHeader_('管理者・裁示格式', '這樣輸入'), lineAdminFormatLines_(), []));
}
function lineAdminHomeFlex_(justOn) {
  var rows = [['每天複核完成後', '自動收到兩則：今日稽核結果、待你裁示'], ['裁示', '輸入「分類—說明」或直接用一句話說明，確認後雲端改網站；接手、完成都會回報'],
              ['使用者功能照舊', '每日總覽、盤中通知、查個股都不變']];
  return lineFlex_('管理者模式' + (justOn ? '已開啟' : '') + '：今日稽核、待裁示、裁示格式、回到使用者。', fxBubble_(
    fxHeader_('管理者模式', justOn ? '已切換成管理者' : '管理者功能', '#0A4A3C'),
    rows.map(function (r) { return fxBox_('vertical', [fxText_(r[0], { size: 'sm', weight: 'bold', color: LINE_C_.ink }), fxText_(r[1], { size: 'xs', color: LINE_C_.muted })]); })
      .concat([fxNote_('輸入「取消管理者」回到一般使用者。')]),
    [fxBtn_(linePb_('今日稽核結果', 'a=adminreview', '今日稽核')), fxBtn_(linePb_('待裁示', 'a=adminpending', '待裁示'), 'secondary'),
     fxBtn_(linePb_('裁示進度', 'a=adminprogress', '裁示進度'), 'secondary'),
     fxBtn_(linePb_('裁示格式', 'a=adminformat', '裁示格式'), 'secondary'), fxBtn_(lineMsg_('回到使用者', '取消管理者'), 'link')]));
}
function lineAdminExitFlex_() {
  return lineFlex_('已回到使用者模式。輸入「管理者登入」可再切換。', fxBubble_(
    fxHeader_('使用者模式', '已回到一般使用者'),
    [fxText_('管理者的稽核推送與裁示已關閉；每日總覽、盤中通知與查詢照舊。', { size: 'sm', color: LINE_C_.ink }),
     fxNote_('要再切換，輸入「管理者登入」。')],
    [fxBtn_(linePb_('今日整理', 'a=today', '今日整理')), fxBtn_(linePb_('使用說明', 'a=help', '使用說明'), 'secondary')]));
}
function lineAdminItemBubble_(kicker, r, lines, color, buttons) {
  return fxBubble_(fxHeader_(kicker, lineAdminName_(r) || '（未命名）', color),
    [r['差異類型'] ? fxBox_('horizontal', [lineAdminChip_(r['差異類型'], color)]) : null].concat(lines).filter(Boolean), buttons || []);
}
function lineAdminMoreBubble_(n, url) {
  return fxBubble_(fxHeader_('還有', '另外 ' + n + ' 項'), [fxText_('放不下的項目請看完整複核紀錄。', { size: 'sm', color: LINE_C_.ink })],
    url ? [fxBtn_(lineUri_('完整紀錄', url), 'secondary')] : []);
}
/* 2026/11/11（管理者）：四個分級各一個顏色，每一項一張卡（以前只放已修改的，待確認／邊界／複核錯看不出來）。 */
var LINE_ADMIN_SEV_ORDER_ = ['明確錯誤', '待確認', '邊界', '複核錯'];
var LINE_ADMIN_SEV_COLOR_ = { '明確錯誤': '#B42318', '待確認': '#B54708', '邊界': '#175CD3', '複核錯': '#475467' };
/** 純函式：這一列現在的處理狀態（卡片標題用）。 */
function lineAdminRowState_(r) {
  if (r['處理'] === '已修正') { return '已修正'; }
  if (r['處理'] === '只記錄') { return '只記錄'; }
  if (r['嚴重度'] === '明確錯誤') { return '修正未完成'; }
  if (r['嚴重度'] === '待確認') { return '待你裁示'; }
  return r['處理'] || '未處理';
}
function lineAdminSevRank_(r) { var k = LINE_ADMIN_SEV_ORDER_.indexOf(r['嚴重度']); return k < 0 ? 9 : k; }
/** 一項一張卡：頂部色條＝分級，標題＝分級・處理狀態。 */
function lineAdminSevBubble_(r, day) {
  var sev = r['嚴重度'] || '其他', color = LINE_ADMIN_SEV_COLOR_[sev] || '#667085', state = lineAdminRowState_(r);
  var cls = r['網站分類'] && r['複核分類'] && r['網站分類'] !== r['複核分類'] ? r['網站分類'] + ' → ' + r['複核分類'] : (r['複核分類'] || r['網站分類'] || '');
  var open = state === '修正未完成' || state === '待你裁示';
  var code = String(r['代號'] || '').trim();
  var pick = 'a=rulepick&d=' + encodeURIComponent(day) + '&c=' + encodeURIComponent(code) + '&n=' + encodeURIComponent(r['名稱'] || '');
  return lineAdminItemBubble_(sev + '・' + state, r, [
    lineAdminLine_('分類', lineAdminCut_(cls, 100)),
    state === '已修正' ? lineAdminLine_('改成', lineAdminCut_(r['修正後'], 160)) : lineAdminLine_('網站現在', lineAdminCut_(r['修正前'], 110)),
    lineAdminLine_('判斷', lineAdminCut_(r['判斷理由'], 140)),
    lineAdminLine_('原句', r['原文依據'] ? '「' + lineAdminCut_(r['原文依據'], 90) + '」' : '')
  ], color, open && /^\d{4,6}[A-Z]?$/.test(code) ? [fxBtn_(linePb_('裁示這一檔', pick, '裁示 ' + lineAdminName_(r)))] : []);
}
/** ① 今日稽核結果。data = { day, summary, rows, url }；only＝只看某一級（「看待確認」）。 */
function lineAdminReviewFlex_(data, only) {
  var s = data.summary || {}, rows = data.rows || [], md = lineMd_(data.day) || data.day;
  var state = String(s['狀態'] || '').trim();
  if (state === '未執行') {
    return lineFlex_(md + ' 未複核：' + (s['備註'] || ''), fxBubble_(fxHeader_('管理者・每日稽核', md + ' 未複核', '#0A4A3C'),
      [fxText_(s['備註'] || '今天沒有複核。', { size: 'sm', color: LINE_C_.ink })], []));
  }
  var count = function (k) { return rows.filter(function (r) { return r['嚴重度'] === k; }).length; };
  var fixed = rows.filter(function (r) { return r['處理'] === '已修正'; });
  var pending = lineAdminPendingRows_(rows);
  var stateLabel = { '已完成': '已完成', '修正中': '修正套用中', '修正失敗': '修正套用失敗', '複核中': '複核進行中' }[state] || state;
  var tile = function (k) {
    return fxBox_('vertical', [fxText_(String(count(k)), { size: 'xl', weight: 'bold', color: LINE_ADMIN_SEV_COLOR_[k], align: 'center' }),
      fxText_(k, { size: 'xxs', color: LINE_ADMIN_SEV_COLOR_[k], weight: 'bold', align: 'center' })], { flex: 1 });
  };
  var shown = rows.filter(function (r) { return !only || r['嚴重度'] === only; }).slice()
    .sort(function (a, b) { return lineAdminSevRank_(a) - lineAdminSevRank_(b); });
  var sevPb = function (k) { return 'a=adminsev&d=' + encodeURIComponent(data.day) + '&s=' + encodeURIComponent(k); };
  var btns = only ? [fxBtn_(linePb_('看全部分級', sevPb(''), '看全部分級'), 'secondary')]
    : LINE_ADMIN_SEV_ORDER_.filter(function (k) { return count(k); }).map(function (k) {
      return fxBtn_(linePb_('看' + k + ' ' + count(k), sevPb(k), '看' + k), 'secondary');
    });
  if (data.url) { btns.push(fxBtn_(lineUri_('完整紀錄', data.url), 'secondary')); }
  var headColor = only ? (LINE_ADMIN_SEV_COLOR_[only] || '#0A4A3C') : '#0A4A3C';
  var head = fxBubble_(fxHeader_('管理者・每日稽核', md + (only ? ' ' + only + ' ' + shown.length + ' 項' : ' 稽核結果'), headColor), [
    fxText_('比對 ' + (s['比對檔數'] || 0) + ' 檔・已修正 ' + fixed.length + ' 項・' + stateLabel, { size: 'sm', color: LINE_C_.ink }),
    fxBox_('horizontal', [tile('明確錯誤'), tile('待確認')], { margin: 'md' }),
    fxBox_('horizontal', [tile('邊界'), tile('複核錯')], { margin: 'sm' }),
    fxText_(!rows.length ? '網站內容與獨立判讀一致，今天沒有修改。' : !shown.length ? '這一級今天沒有項目。' :
      '往右滑看每一項；卡片顏色＝分級，標題寫處理狀態（已修正／只記錄／待你裁示）。', { size: 'sm', color: LINE_C_.ink, margin: 'md' }),
    pending.length && !only ? fxText_('待你裁示 ' + pending.length + ' 項，請看下一則。', { size: 'sm', weight: 'bold', color: LINE_C_.amber, margin: 'sm' }) : null
  ], btns);
  var items = shown.map(function (r) { return lineAdminSevBubble_(r, data.day); });
  var bubbles = [head].concat(items.slice(0, LINE_ADMIN_ITEM_BUBBLES_));
  if (items.length > LINE_ADMIN_ITEM_BUBBLES_) { bubbles.push(lineAdminMoreBubble_(items.length - LINE_ADMIN_ITEM_BUBBLES_, data.url)); }
  var alt = md + (only ? ' ' + only + ' ' + shown.length + ' 項' : ' 稽核：已修正 ' + fixed.length + ' 項' + (pending.length ? '，待你裁示 ' + pending.length + ' 項' : ''));
  return { type: 'flex', altText: lineClip_(alt, 400), contents: bubbles.length > 1 ? { type: 'carousel', contents: bubbles } : head };
}
/** ② 待你裁示。沒有待裁示回 null。 */
function lineAdminPendingFlex_(data) {
  var pending = lineAdminPendingRows_(data.rows), md = lineMd_(data.day) || data.day;
  if (!pending.length) { return null; }
  var guide = fxBubble_(fxHeader_('管理者・待你裁示', md + ' 共 ' + pending.length + ' 項', '#8A5A00'), lineAdminFormatLines_(), []);
  var items = pending.map(function (r) {
    var data2 = 'a=rulepick&d=' + encodeURIComponent(data.day) + '&c=' + encodeURIComponent(r['代號'] || '') + '&n=' + encodeURIComponent(r['名稱'] || '');
    return lineAdminItemBubble_('待裁示', r, [
      lineAdminLine_('網站現在', lineAdminCut_([r['網站分類'], r['修正前']].filter(String).join('｜'), 110)),
      lineAdminLine_('複核判斷', lineAdminCut_([r['複核分類'], r['判斷理由']].filter(String).join('｜'), 140)),
      lineAdminLine_('原句', r['原文依據'] ? '「' + lineAdminCut_(r['原文依據'], 90) + '」' : '')
    ], '#8A5A00', [fxBtn_(linePb_('裁示這一檔', data2, '裁示 ' + lineAdminName_(r)))]);
  });
  var bubbles = [guide].concat(items.slice(0, LINE_ADMIN_ITEM_BUBBLES_));
  if (items.length > LINE_ADMIN_ITEM_BUBBLES_) { bubbles.push(lineAdminMoreBubble_(items.length - LINE_ADMIN_ITEM_BUBBLES_, data.url)); }
  return { type: 'flex', altText: lineClip_(md + ' 待你裁示 ' + pending.length + ' 項：輸入「分類—說明」。', 400),
           contents: { type: 'carousel', contents: bubbles } };
}
/** 「買入／會員持股」→ 每個分類一個色塊。 */
function lineAdminCatChips_(cat) {
  return fxBox_('horizontal', String(cat || '').split('／').filter(String).map(function (c) {
    return lineAdminChip_(c, LINE_ADMIN_CAT_COLOR_[c] || LINE_C_.accent);
  }), { spacing: 'sm' });
}
function lineAdminConfirmFlex_(d) {
  return lineFlex_('確認裁示（管理者）：' + d.date + ' ' + d.name + ' ' + d.code + ' → ' + d.cat, fxBubble_(
    fxHeader_('確認裁示・管理者身分', (lineMd_(d.date) || d.date) + ' ' + d.name + ' ' + d.code, '#0A4A3C'),
    [lineAdminCatChips_(d.cat),
     lineAdminLine_('管理者說明', d.text),
     d.basis ? lineAdminLine_('判定依據', d.basis) : null,
     fxNote_('送出後雲端會依原稿' + (d.basis ? '與當天會員通知' : '') + '寫修正、更新網站；雲端接手、完成時都會在這裡回報（約 5–20 分鐘），也可以隨時輸入「裁示進度」。只改這一檔，不改通用規則。')].filter(Boolean),
    [fxBtn_(linePb_('確認送出', 'a=ruleok&k=' + encodeURIComponent(d.id), '確認送出裁示')),
     fxBtn_(linePb_('取消', 'a=ruleno&k=' + encodeURIComponent(d.id), '取消這則裁示'), 'secondary')]));
}
/* v220：裁示每一個階段的標題與說明（回報、「裁示進度」共用）。 */
var LINE_ADMIN_STAGE_ = {
  '待處理': ['裁示已送出', '已記錄，等雲端接手。', '#5C8F7C'],
  '處理中': ['雲端已接手', '雲端正在讀原稿與當天通知、寫修正。', '#2F7A63'],
  '套用中': ['網站套用中', '修正已推到 GitHub，正在套用、讀回並刷新網站。', '#2F7A63'],
  '已完成': ['裁示已完成', '已套用並刷新網站。', '#0A4A3C'],
  '已記錄': ['裁示已記錄', '只記錄，網站沒有改。', '#2F7A63'],
  '失敗': ['裁示未完成', '網站沒有改。', '#8A5A00'],
  '已取消': ['裁示已取消', '網站沒有改。', '#8A958F']
};
function lineAdminRuleBubble_(r) {
  var st = String(r['狀態'] || '').trim(), stage = LINE_ADMIN_STAGE_[st] || [st || '裁示', '', '#8A958F'], ok = st === '已完成';
  var title = (lineMd_(r['日期']) || r['日期']) + ' ' + (r['名稱'] || '') + ' ' + (r['代號'] || '');
  return fxBubble_(fxHeader_(stage[0], title, stage[2]), [
    lineAdminCatChips_(r['裁示分類']),
    lineAdminLine_('管理者說明', lineAdminCut_(r['管理者說明'], 160)),
    lineAdminLine_(ok ? '網站' : r['名稱'] === '管理者提問' && r['結果'] ? '回答' : '進度', lineAdminCut_(r['結果'] || stage[1], r['裁示分類'] === '指示' ? 400 : 200)),
    r['更新時間'] ? fxText_('更新：' + r['更新時間'], { size: 'xxs', color: LINE_C_.muted }) : null,
    ok ? fxNote_('網站已更新；12:30 狀態信與複核紀錄也會列在「今日網站修改」。更新網站不會重寄已寄出的信。') : null
  ].filter(Boolean), []);
}
/** 裁示回報：rows 是「管理者裁示」的列。 */
function lineAdminResultFlex_(rows) {
  var bubbles = rows.slice(0, 11).map(lineAdminRuleBubble_);
  var done = rows.filter(function (r) { return r['狀態'] === '已完成'; }).length;
  var alt = '裁示回報：完成 ' + done + ' 項' + (rows.length - done ? '，未完成或只記錄 ' + (rows.length - done) + ' 項' : '');
  return { type: 'flex', altText: alt, contents: bubbles.length > 1 ? { type: 'carousel', contents: bubbles } : bubbles[0] };
}
/** 雲端接手／套用中的進度回報。 */
function lineAdminStartedFlex_(rows) {
  var bubbles = rows.slice(0, 11).map(lineAdminRuleBubble_);
  var alt = '裁示進度：' + rows.map(function (r) { return (r['名稱'] || r['代號']) + ' ' + ((LINE_ADMIN_STAGE_[r['狀態']] || [r['狀態']])[0]); }).join('、');
  return { type: 'flex', altText: lineClip_(alt, 400), contents: bubbles.length > 1 ? { type: 'carousel', contents: bubbles } : bubbles[0] };
}
/** 純函式：「LINE回報」欄的值 → 回報到哪一段。舊版只寫時間戳記＝已回報結果。 */
function lineAdminReported_(v) {
  v = String(v || '').trim();
  return !v ? '' : /^接手 /.test(v) ? 'started' : 'final';
}
/** 純函式：這一輪要送哪些回報。rows 是「管理者裁示」的列。 */
function lineAdminReportPlan_(rows) {
  var started = [], final = [];
  rows.forEach(function (r) {
    var st = String(r['狀態'] || '').trim(), rep = lineAdminReported_(r['LINE回報']);
    if (/^(?:已完成|失敗|已記錄)$/.test(st) && rep !== 'final') { final.push(r); }
    else if (/^(?:處理中|套用中)$/.test(st) && !rep) { started.push(r); }
  });
  return { started: started, final: final };
}

/* ---------------- 互動：查看、挑選、裁示、確認 ---------------- */
function lineAdminDeny_() { return [lineText_('這個指令只開放給管理者。', lineQuickMain_())]; }
function lineAdminReviewReply_(day, only) {
  var data = day ? lineAdminReviewData_(day, false) : lineAdminReviewData_(todayStr_(), true);
  if (!data) { return [lineText_(day ? (lineMd_(day) || day) + ' 沒有複核紀錄（休市、沒有影片，或那天複核沒有執行）。' : '還沒有複核紀錄。', lineAdminQuick_())]; }
  only = LINE_ADMIN_SEV_ORDER_.indexOf(only) >= 0 ? only : '';
  if (only) { return [lineAdminReviewFlex_(data, only)]; }
  var out = [lineAdminReviewFlex_(data)], p = lineAdminPendingFlex_(data);
  if (p) { out.push(p); }
  return out;
}
function lineAdminPendingReply_(day) {
  var data = day ? lineAdminReviewData_(day, false) : lineAdminReviewData_(todayStr_(), true);
  if (day && !data) { return [lineText_((lineMd_(day) || day) + ' 沒有複核紀錄（休市、沒有影片，或那天複核沒有執行）。', lineAdminQuick_())]; }
  var p = data && lineAdminPendingFlex_(data);
  if (p) { return [p]; }
  return [lineText_((data ? (lineMd_(data.day) || data.day) + ' ' : '') + '沒有待你裁示的項目。要改其他股票，直接輸入「代號 分類—說明」。', lineAdminQuick_())];
}
function lineAdminPickReply_(uid, act) {
  var ctx = { date: String(act.d || todayStr_()), code: String(act.c || ''), name: String(act.n || '') };
  CacheService.getScriptCache().put('line_rule_ctx_' + uid, JSON.stringify(ctx), 1800);
  return [lineText_('請輸入對「' + (lineMd_(ctx.date) || ctx.date) + ' ' + ctx.name + ' ' + ctx.code + '」的裁示：\n分類—說明\n例如：觀望注意—原稿說等拉回再看，不是不碰\n可用分類：' +
    LINE_ADMIN_CATS_.join('、'), lineAdminQuick_())];
}
function lineAdminRuleReply_(uid, rule) {
  var cache = CacheService.getScriptCache(), ctx = null;
  try { ctx = JSON.parse(cache.get('line_rule_ctx_' + uid) || 'null'); } catch (e) { ctx = null; }
  var today = todayStr_();
  var when = rule.month ? lineAdminRuleDate_(rule, today) : { date: ctx && !rule.code && !rule.name ? ctx.date : today };
  if (when.error) { return [lineText_(when.error, lineAdminQuick_())]; }
  var code = rule.code, name = '';
  if (!code && rule.name) {
    var hit = lineResolveStock_(rule.name);
    if (hit.code) { code = hit.code; name = hit.name; }
    else { return [lineText_('找不到「' + rule.name + '」這檔股票。請改用代號，例如「2330 ' + rule.cat + '—' + lineAdminCut_(rule.text, 20) + '」。', lineAdminQuick_())]; }
  }
  if (!code && ctx && ctx.date === when.date) { code = ctx.code; name = ctx.name; }
  if (!code) {
    var data = lineAdminReviewData_(when.date, false), pend = data ? lineAdminPendingRows_(data.rows) : [];
    if (pend.length === 1) { code = pend[0]['代號']; name = pend[0]['名稱']; }
  }
  if (!code) {
    return [lineText_('要裁示哪一檔？請先按卡片上的「裁示這一檔」，或在前面加代號，例如「2330 ' + rule.cat + '—' + lineAdminCut_(rule.text, 20) + '」。', lineAdminQuick_())];
  }
  if (!name) { var r2 = lineResolveStock_(code); name = r2.name || ''; }
  var draft = { id: 'R' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss') + Math.floor(Math.random() * 900 + 100),
                date: when.date, code: code, name: name, cat: rule.cat, text: lineClip_(rule.text, 500) };
  cache.put('line_rule_draft_' + uid, JSON.stringify(draft), 900);
  return [lineAdminConfirmFlex_(draft)];
}
function lineAdminRuleOk_(uid, key) {
  var cache = CacheService.getScriptCache(), d = null;
  try { d = JSON.parse(cache.get('line_rule_draft_' + uid) || 'null'); } catch (e) { d = null; }
  if (!d || d.id !== key) { return [lineText_('這則裁示已逾時或已送出過，請重新輸入。', lineAdminQuick_())]; }
  cache.remove('line_rule_draft_' + uid);
  cache.remove('line_rule_ctx_' + uid);
  var book = lineAdminBook_();
  if (!book) { return [lineText_('複核紀錄試算表還沒建立，裁示沒有送出。', lineAdminQuick_())]; }
  var sh = lineAdminRuleSheet_(book), now = lineNow_();
  var row = { '建立時間': now, '裁示ID': d.id, '日期': d.date, '代號': d.code, '名稱': d.name, '裁示分類': d.cat, '管理者說明': d.text,
              '來源': 'LINE', '管理者': lineMask_(uid), '狀態': '待處理', '分支': '', '結果': '', '更新時間': now, 'LINE回報': '' };
  withLock_(function () {
    sh.getRange(Math.max(sh.getLastRow(), 1) + 1, 1, 1, LINE_ADMIN_RULE_COLS_.length).setValues([LINE_ADMIN_RULE_COLS_.map(function (k) { return row[k]; })]);
  });
  lineSetProp_('LINE_RULE_OPEN', '1');
  var fired = lineAdminFire_([d.id]);
  var tail = fired.ok ? '雲端已開始處理，完成後在這裡回報（約 5–20 分鐘）。' :
    '已記錄，但雲端觸發' + (fired.reason === 'unset' ? '尚未設定' : '暫時失敗（' + fired.reason + '）') + '；五分鐘排程會再試。';
  return [lineText_('已送出裁示：' + (lineMd_(d.date) || d.date) + ' ' + d.name + ' ' + d.code + ' → ' + d.cat + '\n' + tail, lineAdminQuick_())];
}
function lineAdminRuleNo_(uid) {
  CacheService.getScriptCache().remove('line_rule_draft_' + uid);
  return [lineText_('已取消這則裁示，網站不會改。', lineAdminQuick_())];
}

/* ---------------- v220：一般句子的裁示、身分確認、裁示進度 ---------------- */
/** 純函式：差異明細裡還沒處理完的列（明確錯誤或待確認、不是已修正／只記錄），最近 days 天，新的在前。 */
function lineAdminOpenRows_(rows, today, days) {
  var t = Date.UTC(Number(today.slice(0, 4)), Number(today.slice(5, 7)) - 1, Number(today.slice(8, 10)));
  return (rows || []).filter(function (r) {
    var d = String(r['日期'] || '').replace(/-/g, '/');
    if (!/^\d{4}\/\d{2}\/\d{2}$/.test(d)) { return false; }
    var age = (t - Date.UTC(Number(d.slice(0, 4)), Number(d.slice(5, 7)) - 1, Number(d.slice(8, 10)))) / 86400000;
    return age >= 0 && age <= days && /^(?:明確錯誤|待確認)$/.test(r['嚴重度'] || '') && !/^(?:已修正|只記錄)$/.test(r['處理'] || '') &&
      String(r['代號'] || '').trim() && String(r['代號']).trim() !== '—';
  }).sort(function (a, b) { return String(b['日期']).localeCompare(String(a['日期'])); });
}
/** 純函式：一般句子的裁示要套到哪一檔、哪一天。open 是 lineAdminOpenRows_ 的結果；resolve(q) 回 { code, name }。 */
function lineAdminPickTarget_(free, open, ctx, resolve, today) {
  var flat = String(free.text || '').replace(/\s+/g, '');
  var code = '', name = '';
  if (free.code) { var r1 = resolve(free.code) || {}; code = r1.code || free.code; name = r1.name || ''; }
  if (!code) {
    var hit = (open || []).filter(function (r) { return (r['名稱'] && flat.indexOf(r['名稱']) >= 0); })[0];
    if (hit) { code = String(hit['代號']).trim(); name = hit['名稱']; }
  }
  if (!code && free.lead) { var r2 = resolve(free.lead) || {}; if (r2.code) { code = r2.code; name = r2.name || ''; } }
  if (!code && ctx && ctx.code) { code = ctx.code; name = ctx.name; }
  if (!code) { return { error: 'stock' }; }
  var when;
  if (free.month) { when = lineAdminRuleDate_(free, today); if (when.error) { return { error: when.error }; } }
  else {
    var row = (open || []).filter(function (r) { return String(r['代號']).trim() === code; })[0];
    when = { date: row ? String(row['日期']).replace(/-/g, '/') : ctx && ctx.code === code && ctx.date ? ctx.date : today };
  }
  return { date: when.date, code: code, name: name };
}
function lineAdminFreeReply_(uid, free) {
  var cache = CacheService.getScriptCache(), ctx = null, today = todayStr_();
  try { ctx = JSON.parse(cache.get('line_rule_ctx_' + uid) || 'null'); } catch (e) { ctx = null; }
  var book = lineAdminBook_(), open = book ? lineAdminOpenRows_(lineAdminReadTab_(book, '差異明細', ''), today, LINE_ADMIN_DAYS_BACK_) : [];
  var t = lineAdminPickTarget_(free, open, ctx, function (q) { var h = lineResolveStock_(q); return h && h.code ? h : null; }, today);
  if (t.error === 'stock') {
    // 2026/11/11：認不出是哪一檔時，先請第一關（Claude Sonnet）看整句；它也判斷不了才請管理者補代號
    var ai = typeof lineAdminAi_ === 'function' ? lineAdminAi_(uid, free.src || free.raw || free.text) : null;
    if (ai) { return ai; }
    return [lineText_('這句看起來是裁示，但認不出是哪一檔。請在前面加代號或股名，例如「8210 ' + free.cats.join('或') + '—' + lineAdminCut_(free.text, 20) + '」，或先按卡片上的「裁示這一檔」。', lineAdminQuick_())];
  }
  if (t.error) { return [lineText_(t.error, lineAdminQuick_())]; }
  if (!t.name) { var r2 = lineResolveStock_(t.code); t.name = r2.name || ''; }
  var draft = { id: 'R' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss') + Math.floor(Math.random() * 900 + 100),
                date: t.date, code: t.code, name: t.name, cat: free.cats.join('／'), text: lineClip_(free.text, 500),
                basis: free.notice ? '依當天會員通知（簡訊）的方向判定：簡訊優先；買入與會員持股同時成立就兩列都留' : '' };
  cache.put('line_rule_draft_' + uid, JSON.stringify(draft), 900);
  return [lineAdminConfirmFlex_(draft)];
}
/* ---------------- 管理者指示（2026/11/11 管理者） ----------------
   以前 LINE 只會把「股票 分類—說明」這種逐檔裁示交給雲端；管理者回答每日複核提出的問題、補充判讀規則
   （例如「追買不算外洩，外洩只看價格；行程是 8210 勤誠」）這類一般句子，會被當成查股或服務範圍外，雲端收不到。
   現在：開著管理者模式、不是指令／逐檔裁示／查詢、讀起來像指示的句子，先出確認卡；按「確認送出」後寫進
   「管理者裁示」（裁示分類＝指示），觸發雲端。雲端讀那天的原稿與複核紀錄核對後，能改的改網站、該記的記給週五檢討，
   接手與結果照逐檔裁示一樣回報到 LINE。 */
/** 純函式：一般句子是不是管理者的指示（不是查詢、不是問句）。 */
function lineAdminLooksLikeInstruction_(raw) {
  var flat = String(raw || '').replace(/\s+/g, '');
  if (flat.length < 15 || flat.length > 600) { return false; }
  if (/^(?:請|幫我|麻煩)?(?:查|查詢|看一下|找)/.test(flat) && flat.length < 25) { return false; }
  if (/(?:嗎|呢|\?|？)[。！!]*$/.test(flat) && !/(?:請|幫我|應該|不算|就是|其實|改成|寫進|加入)/.test(flat)) { return false; }
  return /(?:請|幫我|應該|應當|不算|算是|就是|其實|改成|改為|寫進|加入|以後|之後|裁示|判定|確認|不要|不用|要把|記得|規則|工作流|流程|外洩|同音|指的是|講的是)/.test(flat);
}
/** 純函式：管理者模式下這句話值不值得送雲端（太短、純符號、打招呼不送）。 */
function lineAdminSendable_(raw) {
  var flat = String(raw || '').replace(/[\s，,。.！!？?～~、…]+/g, '');
  if (flat.length < 4 || flat.length > 600) { return false; }
  return !/^(?:你好|您好|哈囉|嗨|早安|午安|晚安|謝謝|感謝|謝啦|好的|好喔|收到|了解|知道了|ok|OK|沒事)+$/.test(flat);
}
/** 純函式：管理者的句子是問題（要雲端查了回答）還是指示（要雲端照做）。 */
function lineAdminIsQuestion_(raw) {
  var flat = String(raw || '').replace(/\s+/g, '');
  return /(?:嗎|呢|了沒|好沒|沒有?|\?|？)[。！!]*$/.test(flat) || /有沒有|是不是|是否|哪些|哪幾|什麼|怎麼|如何|為什麼|為何|多少|幾檔|列出/.test(flat);
}
/** 查得到的股票（代號、正式名稱、已確認別名）。讀不到代號表時當作查不到。 */
function lineAdminStockHit_(q) {
  try { var r = lineResolveStock_(q); return !!(r && !r.none && (r.code || (r.choices && r.choices.length))); } catch (e) { return false; }
}
function lineAdminInstructionFlex_(d) {
  var ask = d.name === '管理者提問', label = ask ? '管理者提問' : '管理者指示';
  return lineFlex_('送給雲端・' + label + '：' + lineAdminCut_(d.said || d.text, 60), fxBubble_(
    fxHeader_('送給雲端・' + label, '對應 ' + (lineMd_(d.date) || d.date) + ' 的複核', '#0A4A3C'),
    [lineAdminLine_(ask ? '提問內容' : '指示內容', lineAdminCut_(d.said || d.text, 300)),
     d.summary ? lineAdminLine_('我的理解', lineAdminCut_(d.summary, 200)) : null,
     fxNote_(ask ? '送出後雲端會讀那天的原稿與複核紀錄查證後回答，不改網站；答案在這裡回報（約 3–10 分鐘），也可以隨時輸入「裁示進度」。'
       : '送出後雲端會讀那天的原稿與複核紀錄核對：能改的直接改網站並讀回，屬於流程規則的寫進週五檢討清單。雲端接手、完成都會在這裡回報（約 5–20 分鐘），也可以隨時輸入「裁示進度」。')],
    [fxBtn_(linePb_('確認送出', 'a=ruleok&k=' + encodeURIComponent(d.id), '確認送出' + (ask ? '提問' : '指示'))),
     fxBtn_(linePb_('當作一般查詢', 'a=rulequery', '當作一般查詢'), 'secondary'),
     fxBtn_(linePb_('取消', 'a=ruleno&k=' + encodeURIComponent(d.id), '取消這則指示'), 'link')]));
}
/** summary＝第一關（Claude Sonnet）對這句話的理解；ask＝true 提問／false 指示／undefined 用規則判斷。 */
function lineAdminInstructionReply_(uid, raw, summary, ask, day) {
  var data = null;
  if (!day) { try { data = lineAdminReviewData_(todayStr_(), true); } catch (e) { data = null; } }
  var said = lineClip_(String(raw || '').trim(), 500);
  var isAsk = typeof ask === 'boolean' ? ask : lineAdminIsQuestion_(raw);
  summary = lineClip_(String(summary || '').trim(), 300);
  var draft = { id: 'R' + Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyyMMddHHmmss') + Math.floor(Math.random() * 900 + 100),
                date: day || (data ? data.day : todayStr_()), code: '', name: isAsk ? '管理者提問' : '管理者指示', cat: '指示', said: said, summary: summary,
                // 雲端看的是管理者原話；第一關的理解附在後面，標明出處，原話為準
                text: summary ? lineClip_(said + '\n〔第一關理解，原話為準〕' + summary, 800) : said };
  var cache = CacheService.getScriptCache();
  cache.put('line_rule_draft_' + uid, JSON.stringify(draft), 900);
  cache.put('line_rule_text_' + uid, String(raw || ''), 900);   // 「當作一般查詢」用
  return [lineAdminInstructionFlex_(draft)];
}

/** 已綁定、但目前是使用者身分時送出像裁示的句子：先問要不要切換成管理者。 */
function lineAdminIdentityFlex_() {
  return lineFlex_('你目前是使用者身分。這句看起來是管理者裁示，要切換成管理者並送出嗎？', fxBubble_(
    fxHeader_('身分確認', '你目前是使用者身分', '#8A5A00'),
    [fxText_('這句看起來是管理者裁示。你的帳號已綁定管理者，但現在登出（使用者模式），所以還沒有送出。', { size: 'sm', color: LINE_C_.ink }),
     fxNote_('選「切換管理者並裁示」會開啟管理者模式，接著出現確認卡；按確認送出才會改網站。')],
    [fxBtn_(linePb_('切換管理者並裁示', 'a=ruleresume', '切換管理者並裁示')),
     fxBtn_(linePb_('當作一般查詢', 'a=rulequery', '當作一般查詢'), 'secondary')]));
}
function lineAdminIdentityReply_(uid, raw) {
  CacheService.getScriptCache().put('line_rule_text_' + uid, String(raw || ''), 900);
  return [lineAdminIdentityFlex_()];
}
/** 「切換管理者並裁示」：開管理者模式，再把剛才那句當裁示處理。 */
function lineAdminResumeReply_(uid, ch) {
  var s = lineFindSub_(uid, ch);
  if (!s.tester) { return lineAdminOnReply_(uid, ch); }
  var cache = CacheService.getScriptCache(), raw = cache.get('line_rule_text_' + uid);
  lineAdminSetMode_(uid, ch, true);
  LINE_ADMIN_CTX_ = true;
  if (!raw) { return [lineText_('已切換為管理者身分。剛才那句已逾時，請再輸入一次。', lineAdminQuick_())]; }
  cache.remove('line_rule_text_' + uid);
  var rule = lineAdminParseRuling_(raw), free = rule ? null : lineAdminLooseRuling_(raw);
  var out = rule ? lineAdminRuleReply_(uid, rule) : free ? lineAdminFreeReply_(uid, free) : [lineText_('這句不像裁示，請改用「分類—說明」。', lineAdminQuick_())];
  return [lineText_('已切換為管理者身分。')].concat(out);
}
function lineAdminQueryText_(uid) {
  var cache = CacheService.getScriptCache(), raw = cache.get('line_rule_text_' + uid) || '';
  cache.remove('line_rule_text_' + uid);
  return raw;
}
/** 「裁示進度」：最近五則裁示的狀態。 */
function lineAdminProgressReply_() {
  var book = lineAdminBook_();
  var rows = book ? lineAdminReadTab_(book, LINE_ADMIN_RULE_TAB_, '').filter(function (r) { return r['裁示ID']; }) : [];
  if (!rows.length) { return [lineText_('還沒有裁示紀錄。', lineAdminQuick_())]; }
  var last = rows.slice(-5).reverse(), msg = lineAdminStartedFlex_(last);
  msg.altText = lineClip_('裁示進度（最近 ' + last.length + ' 則）：' + msg.altText.replace(/^裁示進度：/, ''), 400);
  msg.quickReply = { items: lineAdminQuick_().map(function (a) { return { type: 'action', action: a }; }) };
  return [msg];
}

/** 「測試雲端」：只檢查觸發設定與雲端排程能不能啟動；沒有待處理裁示時排程讀完佇列就結束，不改網站。 */
function lineAdminTestReply_() {
  var r = lineAdminFire_(['測試（沒有待處理裁示，讀完佇列就結束）']);
  if (r.ok) { return [lineText_('雲端觸發成功：「盤勢有據 管理者裁示」已啟動。沒有待處理裁示時它讀完佇列就結束，不會改網站（約 1–3 分鐘）。', lineAdminQuick_())]; }
  return [lineText_(r.reason === 'unset' ? '雲端觸發尚未設定：Apps Script 指令碼屬性要有 CLAUDE_RULING_FIRE_URL（…/routines/trig_…/fire）與 CLAUDE_RULING_FIRE_TOKEN。'
    : '雲端觸發失敗（' + r.reason + '）。HTTP 401 表示 Token 不對或已重新產生；404 表示 Fire URL 不對。', lineAdminQuick_())];
}

/* ---------------- 觸發雲端排程 ---------------- */
function lineAdminFire_(ids) {
  var url = lineProp_('CLAUDE_RULING_FIRE_URL'), token = lineProp_('CLAUDE_RULING_FIRE_TOKEN');
  lineSetProp_('LINE_RULE_FIRE_AT', String(Date.now()));
  if (!url || !token || !/^https:\/\/api\.anthropic\.com\/v1\/claude_code\/routines\/trig_[\w-]+\/fire$/.test(url)) { return { ok: false, reason: 'unset' }; }
  try {
    var res = UrlFetchApp.fetch(url, { method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token, 'anthropic-beta': 'experimental-cc-routine-2026-04-01', 'anthropic-version': '2023-06-01' },
      payload: JSON.stringify({ text: 'LINE 管理者裁示待處理：' + (ids || []).join(',') }) });
    var code = res.getResponseCode();
    return code >= 200 && code < 300 ? { ok: true } : { ok: false, reason: 'HTTP ' + code };
  } catch (e) {
    return { ok: false, reason: '連線失敗' };
  }
}

/* ---------------- 第一關：Claude Sonnet 理解管理者的話（2026/11/11 管理者） ---------------- */
// 管理者模式下不是固定指令、也不是單純查股的句子，先請 Claude Sonnet 5.5 理解想做什麼：
//   查詢 → 從複核紀錄直接回答，或出既有卡片（稽核分級、待裁示、裁示進度、格式、查股）；
//   要改分類、網站、複核判定或規則 → 出「送給雲端」確認卡，按「確認送出」才交給 Opus 雲端排程核對原稿並套用。
// 金鑰放指令碼屬性 ANTHROPIC_API_KEY；沒設、逾時或回覆不合格式就照舊用規則與 Gemini，不讓管理者卡住。
var LINE_ADMIN_AI_MODEL_ = 'claude-sonnet-5-5';
var LINE_ADMIN_AI_URL_ = 'https://api.anthropic.com/v1/messages';
var LINE_ADMIN_AI_INTENTS_ = ['answer', 'review', 'pending', 'progress', 'format', 'stock', 'change', 'ask_cloud', 'chat', 'unclear'];
var LINE_ADMIN_AI_SCHEMA_ = {
  type: 'object',
  properties: {
    intent: { type: 'string', enum: LINE_ADMIN_AI_INTENTS_ },
    date: { type: 'string', description: 'YYYY/MM/DD，沒提日期就空字串' },
    severity: { type: 'string', enum: ['', '明確錯誤', '待確認', '邊界', '複核錯'] },
    stock: { type: 'string' },
    reply: { type: 'string' },
    summary: { type: 'string' }
  },
  required: ['intent', 'date', 'severity', 'stock', 'reply', 'summary'],
  additionalProperties: false
};
var LINE_ADMIN_AI_SYSTEM_ = [
  '你是「盤勢有據」LINE 機器人的第一關，對話對象是已驗證的網站管理者。網站每天整理一支股市直播，雲端每天中午做寄信前複核，把差異分成四級：明確錯誤、待確認、邊界、複核錯（複核自己判錯）。',
  '你的工作：理解管理者這一句話想做什麼，只回 JSON。intent：',
  'review＝想看某天稽核／複核結果，或某一分級的明細（例如「待確認的明細是什麼」→ review，severity 填待確認）；',
  'pending＝想看還要他裁示／決定的項目；progress＝想看他之前裁示的處理進度；format＝問裁示怎麼輸入；',
  'stock＝只是想查某檔股票在網站上的紀錄（stock 填名稱或代號）；',
  'answer＝用下面提供的複核資料或裁示紀錄就能回答的問題（reply 寫答案）；',
  'change＝要改東西：某檔分類或說明、網站文字、複核判定、判讀規則或流程，或回答複核提出的問題並下決定（summary 用一兩句寫清楚要改什麼、哪一天、哪一檔、依據是什麼）；',
  'ask_cloud＝要讀原稿、會員通知、網站內容等沒提供的資料才能回答的問題（summary 寫要查什麼）；',
  'chat＝打招呼、道謝（reply 簡短回應）；unclear＝看不懂或缺關鍵資訊（reply 用一句話問清楚，可給兩個選項）。',
  '規則：只根據提供的資料回答，不猜不補，資料沒有就用 ask_cloud。語音輸入常有同音錯字：「才是」多半是「裁示」，「複合錯」是「複核錯」，「每日季」是「每日稽核」。',
  'reply 用繁體中文，口語直接，350 字內；列項目時寫日期、名稱代號、分級與處理狀態。不寫會員通知或簡訊原文，不給投資建議。',
  '你不能修改任何東西，也不要說已經改了；要改一律回 change，等管理者確認後才交給雲端。date 把相對日期換成 YYYY/MM/DD，沒提就空字串；severity 只有指定分級時才填。'
].join('\n');
/** 給第一關的資料：最近一次複核的摘要與差異明細、最近的裁示。只放判斷需要的欄位。 */
function lineAdminAiContext_() {
  var out = { 複核日: '', 狀態: '', 差異明細: [], 最近裁示: [] };
  try {
    var data = lineAdminReviewData_(todayStr_(), true);
    if (data) {
      out.複核日 = data.day; out.狀態 = String((data.summary || {})['狀態'] || ''); out.比對檔數 = String((data.summary || {})['比對檔數'] || '');
      out.差異明細 = (data.rows || []).slice(0, 40).map(function (r) {
        return { 代號: r['代號'] || '', 名稱: r['名稱'] || '', 網站分類: lineAdminCut_(r['網站分類'], 60), 複核分類: lineAdminCut_(r['複核分類'], 60),
                 差異類型: r['差異類型'] || '', 嚴重度: r['嚴重度'] || '', 處理: lineAdminRowState_(r),
                 判斷理由: lineAdminCut_(r['判斷理由'], 120), 修正後: lineAdminCut_(r['修正後'], 80) };
      });
    }
    var book = lineAdminBook_();
    if (book) {
      out.最近裁示 = lineAdminReadTab_(book, LINE_ADMIN_RULE_TAB_, '').filter(function (r) { return r['裁示ID']; }).slice(-8).map(function (r) {
        return { 日期: r['日期'], 名稱: r['名稱'], 代號: r['代號'], 分類: r['裁示分類'], 狀態: r['狀態'], 說明: lineAdminCut_(r['管理者說明'], 60), 結果: lineAdminCut_(r['結果'], 140) };
      });
    }
  } catch (e) { /* 讀不到就只靠句子判斷 */ }
  return out;
}
/** 純函式：檢查第一關的回覆；不合格回 null。 */
function lineAdminAiParse_(j) {
  if (!j || j.stop_reason === 'refusal' || j.stop_reason === 'max_tokens') { return null; }
  var text = (j.content || []).filter(function (b) { return b && b.type === 'text'; }).map(function (b) { return b.text; }).join('');
  var o;
  try { o = JSON.parse(text); } catch (e) { return null; }
  if (!o || LINE_ADMIN_AI_INTENTS_.indexOf(o.intent) < 0) { return null; }
  return { intent: o.intent, date: /^20\d{2}\/\d{2}\/\d{2}$/.test(String(o.date || '')) ? String(o.date) : '',
           severity: LINE_ADMIN_SEV_ORDER_.indexOf(o.severity) >= 0 ? o.severity : '',
           stock: String(o.stock || '').slice(0, 20), reply: lineClip_(String(o.reply || '').trim(), 600), summary: lineClip_(String(o.summary || '').trim(), 300) };
}
function lineAdminAiCall_(raw, context) {
  var key = lineProp_('ANTHROPIC_API_KEY');
  if (!key) { return null; }
  var body = {
    model: LINE_ADMIN_AI_MODEL_, max_tokens: 4000, system: LINE_ADMIN_AI_SYSTEM_,
    output_config: { effort: 'low', format: { type: 'json_schema', schema: LINE_ADMIN_AI_SCHEMA_ } },
    fallbacks: 'default',   // 安全分類器拒答時，伺服器改用備援模型重跑同一個請求
    messages: [{ role: 'user', content: '今天（台北）：' + todayStr_() + '\n複核資料（JSON）：' + JSON.stringify(context || {}) +
      '\n\n管理者這一句：' + String(raw || '').slice(0, 600) }]
  };
  try {
    var res = UrlFetchApp.fetch(LINE_ADMIN_AI_URL_, { method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { 'x-api-key': key, 'anthropic-version': '2026-10-21', 'anthropic-beta': 'server-side-fallback-2026-07-01' },
      payload: JSON.stringify(body) });
    var code = res.getResponseCode();
    if (code < 200 || code >= 300) { Logger.log('LINE 第一關 HTTP ' + code + ' ' + String(res.getContentText()).slice(0, 300)); return null; }
    return lineAdminAiParse_(JSON.parse(res.getContentText()));
  } catch (e) {
    Logger.log('LINE 第一關失敗：' + e);
    return null;
  }
}
/** 第一關的理解 → 回覆。回 null 表示交回規則處理。 */
function lineAdminAiReply_(uid, raw, ai) {
  switch (ai.intent) {
    case 'review': return lineAdminReviewReply_(ai.date, ai.severity);
    case 'pending': return lineAdminPendingReply_(ai.date);
    case 'progress': return lineAdminProgressReply_();
    case 'format': return [lineAdminFormatFlex_()];
    case 'stock': return ai.stock && lineAdminStockHit_(ai.stock) ? lineStockReply_(ai.stock, uid) : null;
    case 'change': return lineAdminInstructionReply_(uid, raw, ai.summary, false, ai.date);
    case 'ask_cloud': return lineAdminInstructionReply_(uid, raw, ai.summary, true, ai.date);
    default: return ai.reply ? [lineText_(ai.reply, lineAdminQuick_())] : null;
  }
}
/** 管理者模式的自由句子先走這裡；沒設金鑰或失敗回 null。 */
function lineAdminAi_(uid, raw) {
  if (!lineProp_('ANTHROPIC_API_KEY')) { return null; }
  var ai = lineAdminAiCall_(raw, lineAdminAiContext_());
  return ai ? lineAdminAiReply_(uid, raw, ai) : null;
}

/* ---------------- 管理者的一般問句（2026/11/10 管理者） ---------------- */
// 「11月9號待裁示的有哪些」以前被當成服務範圍外。先用規則抓「日期＋要看什麼」；規則抓不到、又是管理者時，
// 才請 Gemini（Flash Lite）只判斷意圖與日期，回答一律由既有函式從複核紀錄產生，模型不寫答案內容。
var LINE_ADMIN_INTENTS_ = ['review', 'pending', 'progress', 'format'];
/** 純函式：從句子取日期（11月9號、11/9、2026/11/09、昨天、前天、今天）。回傳 YYYY/MM/DD 或 ''。 */
function lineAdminSpokenDate_(raw, today) {
  var s = String(raw || '').replace(/[０-９]/g, function (c) { return String.fromCharCode(c.charCodeAt(0) - 0xFEE0); }).replace(/\s+/g, '');
  var base = String(today || '').split('/').map(Number), y = base[0], m, d, hit;
  if (!y) { return ''; }
  var shift = function (n) { var dt = new Date(Date.UTC(base[0], base[1] - 1, base[2] - n)); return dt.getUTCFullYear() + '/' + ('0' + (dt.getUTCMonth() + 1)).slice(-2) + '/' + ('0' + dt.getUTCDate()).slice(-2); };
  if (/前天/.test(s)) { return shift(2); }
  if (/昨天|昨日/.test(s)) { return shift(1); }
  if ((hit = s.match(/(?:(20\d{2})[年/.-])?(\d{1,2})(?:月|[/.-])(\d{1,2})(?:日|號|号)?/))) {
    y = hit[1] ? Number(hit[1]) : y; m = Number(hit[2]); d = Number(hit[3]);
    if (m < 1 || m > 12 || d < 1 || d > 31) { return ''; }
    if (!hit[1] && m > base[1]) { y -= 1; }                         // 一月問「12/30」是去年
    return y + '/' + ('0' + m).slice(-2) + '/' + ('0' + d).slice(-2);
  }
  if (/今天|今日/.test(s)) { return String(today); }
  return '';
}
/** 純函式：規則判斷管理者想看什麼。回傳 { intent, day } 或 null（沒有明確的查詢詞就不猜）。 */
function lineAdminQueryIntent_(raw, today) {
  var s = String(raw || '').replace(/\s+/g, '');
  if (!s || s.length > 60) { return null; }
  // 2026/11/11：「其實…就是…」「…不算…，請寫進…」這類是指示，不是查詢（交給 lineAdminLooksLikeInstruction_）
  if (/(?:不算|就是|其實|改成|改為|寫進|加入|應該)/.test(s)) { return null; }
  // 2026/11/11：「今天有沒有需要管理者裁示（語音常寫成『才是』）／要我決定的事情」也是待裁示
  var intent = /待裁示|要裁示|需要裁示|待我裁示|待你裁示|還沒裁示|待確認|待處理/.test(s) ||
      /(?:需要|要|待|等)(?:管理者|管理員|我|你)(?:來)?(?:裁示|才是|決定|處理|確認|回覆)|管理者(?:才是|裁示|決定)的(?:事|項|內容|有)|待辦|待決定|要我決定/.test(s) ? 'pending'
    : /裁示進度|裁示狀態|處理到哪|處理進度|修正進度|修改進度/.test(s) ? 'progress'
    : /稽核|複核|改了什麼|修了什麼|修正了什麼|改了哪些|修了哪些|明確錯誤|邊界|複核錯/.test(s) ? 'review'
    : /怎麼裁示|裁示格式|裁示怎麼打|格式是什麼/.test(s) ? 'format' : '';
  if (!intent) { return null; }
  return { intent: intent, day: intent === 'format' || intent === 'progress' ? '' : lineAdminSpokenDate_(s, today) };
}
/** 規則判斷不了時才問 Gemini；只回 JSON 意圖與日期，失敗就回 null（照舊回服務範圍外）。 */
function lineAdminGeminiIntent_(raw, today) {
  if (typeof callGemini_ !== 'function') { return null; }
  var sys = '你是 LINE 機器人的意圖判斷器，使用者是網站管理者。只回 JSON：{"intent": "review|pending|progress|format|stock|instruction|other", "date": "YYYY/MM/DD 或空字串", "stock": "股票名稱或代號或空字串"}。' +
    'review＝想看某天複核／稽核結果、改了什麼、明確錯誤；pending＝想看某天還要他裁示的項目；progress＝想看裁示處理進度；format＝問裁示怎麼輸入；' +
    'stock＝想查某檔股票的紀錄；instruction＝在下指示、回答複核提出的問題或補充判讀規則；other＝其他。今天是 ' + today + '（台北）。相對日期換算成確切日期；沒提日期就給空字串。不要解釋。';
  try {
    var out = JSON.parse(String(callGemini_(sys, String(raw || '').slice(0, 200), { json: true, maxOut: 200, temperature: 0, maxWaitMs: 12000, singleAttempt: true }) || '{}'));
    var intent = String(out.intent || '');
    if (LINE_ADMIN_INTENTS_.indexOf(intent) < 0 && intent !== 'stock' && intent !== 'instruction') { return null; }
    var day = /^20\d{2}\/\d{2}\/\d{2}$/.test(String(out.date || '')) ? String(out.date) : '';
    return { intent: intent, day: day, stock: String(out.stock || '').slice(0, 20), via: 'gemini' };
  } catch (e) {
    return null;
  }
}
function lineAdminIntentReply_(q) {
  switch (q.intent) {
    case 'review': return lineAdminReviewReply_(q.day || '');
    case 'pending': return lineAdminPendingReply_(q.day || '');
    case 'progress': return lineAdminProgressReply_();
    case 'format': return [lineAdminFormatFlex_()];
    default: return null;
  }
}

/* ---------------- 裁示驗收卡（2026/11/10 管理者） ---------------- */
// 新版部署後，五分鐘排程主動推一張驗收卡給已綁定且開著管理者模式的帳號（每個版本只推一次，08:00–23:30）。
// 管理者照卡片格式回覆一則裁示 → 確認卡 → 雲端接手回報 → 套用結果回報，整條流程一次驗完。
var LINE_ADMIN_VERIFY_BUILD_ = '2026-11-10-line-admin-v220r1';
function lineAdminVerifyFlex_() {
  var steps = [['1 輸入', '照下面的格式回覆一則裁示（股票 分類—說明）'], ['2 確認', '馬上回一張確認卡，按「確認送出」才生效'],
               ['3 雲端接手', '約五分鐘內推一則「雲端已接手」'], ['4 結果', '網站改好並讀回後推「已完成」；改不了會說明原因'],
               ['隨時查', '輸入「裁示進度」看最近五則']];
  return lineFlex_('裁示驗收：請回覆一則「股票 分類—說明」，雲端會依原稿改網站並回報進度。', fxBubble_(
    fxHeader_('管理者・裁示驗收', '請回覆一則裁示', '#0A4A3C'),
    steps.map(function (r) { return fxBox_('vertical', [fxText_(r[0], { size: 'sm', weight: 'bold', color: LINE_C_.ink }), fxText_(r[1], { size: 'xs', color: LINE_C_.muted })]); })
      .concat(lineAdminFormatLines_().slice(0, 3)),
    [fxBtn_(linePb_('待裁示', 'a=adminpending', '待裁示'), 'secondary'), fxBtn_(linePb_('裁示格式', 'a=adminformat', '裁示格式'), 'link')]));
}
function lineAdminVerifyTick_() {
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty('LINE_ADMIN_VERIFY_SENT') === LINE_ADMIN_VERIFY_BUILD_) { return 'sent'; }
  var hhmm = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'HHmm'));
  if (hhmm < 800 || hhmm > 2330) { return 'window'; }
  if (!lineAdminUids_().length) { return 'no-admin'; }
  var q = lineQueue_({ id: 'admin-verify|' + LINE_ADMIN_VERIFY_BUILD_, kind: 'admin', date: todayStr_(), source: '管理者裁示驗收卡',
    expiresAt: Date.now() + 12 * 3600 * 1000, messages: [lineAdminVerifyFlex_()] });
  props.setProperty('LINE_ADMIN_VERIFY_SENT', LINE_ADMIN_VERIFY_BUILD_);
  if (q.created) { lineDeliverTick_({ only: q.id, budgetMs: 30000 }); }
  return q.created ? 'queued' : 'exists';
}

/* ---------------- 五分鐘排程 ---------------- */
function lineAdminTick_() {
  if (!lineConfigured_() || linePushMode_() === 'off') { return { skipped: 'off' }; }
  var out = {};
  try { out.rulings = lineAdminRulingTick_(); } catch (e) { out.rulingsError = String(e && e.message || e); }
  try { out.review = lineAdminReviewTick_(); } catch (e2) { out.reviewError = String(e2 && e2.message || e2); }
  try { out.verify = lineAdminVerifyTick_(); } catch (e3) { out.verifyError = String(e3 && e3.message || e3); }
  return out;
}
/** 複核一完成就送 ① ②（同一天只送一次）。 */
function lineAdminReviewTick_() {
  var today = todayStr_(), props = PropertiesService.getScriptProperties();
  if (props.getProperty('LINE_ADMIN_REVIEW_DAY') === today) { return 'sent'; }
  if (typeof whyClosed_ === 'function' && whyClosed_(new Date())) { return 'closed'; }
  var hhmm = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'HHmm'));
  if (hhmm < 1130 || hhmm > 1800) { return 'window'; }
  if (!lineAdminUids_().length) { return 'no-admin'; }
  var data = lineAdminReviewData_(today, false);
  if (!data) { return 'no-review'; }
  var st = String(data.summary['狀態'] || '').trim();
  if ((st === '複核中' || st === '修正中') && hhmm < 1300) { return 'wait:' + st; }
  var msgs = [lineAdminReviewFlex_(data)], p = lineAdminPendingFlex_(data);
  if (p) { msgs.push(p); }
  var q = lineQueue_({ id: 'admin|' + today, kind: 'admin', date: today, source: '每日複核 ' + today,
    expiresAt: lineParseTaipei_(today + ' 23:59:59'), messages: msgs });
  props.setProperty('LINE_ADMIN_REVIEW_DAY', today);
  if (q.created) { lineDeliverTick_({ only: q.id, budgetMs: 45000 }); }
  return q.created ? 'queued' : 'exists';
}
/** 裁示結果回報到 LINE；待處理太久沒動就再觸發一次雲端。 */
function lineAdminRulingTick_() {
  if (lineProp_('LINE_RULE_OPEN') !== '1') { return 'idle'; }
  var book = lineAdminBook_();
  if (!book) { return 'no-book'; }
  var sh = lineAdminRuleSheet_(book), rows = lineAdminReadTab_(book, LINE_ADMIN_RULE_TAB_, '');
  var plan = lineAdminReportPlan_(rows), ready = plan.final;
  var open = rows.filter(function (r) { return /^(?:待處理|處理中|套用中)$/.test(r['狀態']); });
  var res = { reported: 0, started: 0, open: open.length };
  var col = LINE_ADMIN_RULE_COLS_.indexOf('LINE回報') + 1, stamp = lineNow_();
  // v220：雲端接手（處理中／套用中）先回報一次；結果出來再回報一次。每則裁示最多兩則推播。
  if (plan.started.length) {
    var sids = plan.started.map(function (r) { return r['裁示ID']; });
    var qs = lineQueue_({ id: 'admin-rule-start|' + sids.join(',').slice(0, 110), kind: 'admin', date: todayStr_(), source: '管理者裁示進度 ' + sids.join(','),
      expiresAt: Date.now() + 2 * 3600 * 1000, messages: [lineAdminStartedFlex_(plan.started)] });
    plan.started.forEach(function (r) { sh.getRange(r._row, col).setValue('接手 ' + stamp); });
    if (qs.created) { lineDeliverTick_({ only: qs.id, budgetMs: 30000 }); }
    res.started = plan.started.length;
  }
  if (ready.length) {
    var ids = ready.map(function (r) { return r['裁示ID']; });
    var q = lineQueue_({ id: 'admin-rule|' + ids.join(',').slice(0, 120), kind: 'admin', date: todayStr_(), source: '管理者裁示 ' + ids.join(','),
      expiresAt: Date.now() + 6 * 3600 * 1000, messages: [lineAdminResultFlex_(ready)] });
    ready.forEach(function (r) { sh.getRange(r._row, col).setValue(stamp); });
    if (q.created) { lineDeliverTick_({ only: q.id, budgetMs: 45000 }); }
    res.reported = ready.length;
  }
  var waiting = open.filter(function (r) { return r['狀態'] === '待處理'; });
  if (waiting.length && Date.now() - Number(lineProp_('LINE_RULE_FIRE_AT') || 0) > LINE_ADMIN_FIRE_GAP_MS_) {
    res.refire = lineAdminFire_(waiting.map(function (r) { return r['裁示ID']; }));
  }
  if (!open.length) { lineSetProp_('LINE_RULE_OPEN', ''); }
  return res;
}
