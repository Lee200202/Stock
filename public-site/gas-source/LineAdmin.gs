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
var LINE_ADMIN_CAT_COLOR_ = { '會員持股': '#4A4FA3', '觀望注意': '#1F6F8B', '觀望不碰': '#8A6410', '買入': '#B4342C', '賣出': '#1E7B4F', '不列': '#8A958F' };
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
/** ① 今日稽核結果。data = { day, summary, rows, url }。 */
function lineAdminReviewFlex_(data) {
  var s = data.summary || {}, rows = data.rows || [], md = lineMd_(data.day) || data.day;
  var count = function (k) { return Number(s[k]) || 0; };
  var state = String(s['狀態'] || '').trim();
  if (state === '未執行') {
    return lineFlex_(md + ' 未複核：' + (s['備註'] || ''), fxBubble_(fxHeader_('管理者・每日稽核', md + ' 未複核', '#0A4A3C'),
      [fxText_(s['備註'] || '今天沒有複核。', { size: 'sm', color: LINE_C_.ink })], []));
  }
  var fixed = rows.filter(function (r) { return r['處理'] === '已修正'; });
  var failed = rows.filter(function (r) { return r['嚴重度'] === '明確錯誤' && r['處理'] !== '已修正'; });
  var pending = lineAdminPendingRows_(rows);
  var stateLabel = { '已完成': '已完成', '修正中': '修正套用中', '修正失敗': '修正套用失敗', '複核中': '複核進行中' }[state] || state;
  var tile = function (k, color) {
    return fxBox_('vertical', [fxText_(String(count(k)), { size: 'xl', weight: 'bold', color: color, align: 'center' }),
      fxText_(k, { size: 'xxs', color: LINE_C_.muted, align: 'center' })], { flex: 1 });
  };
  var head = fxBubble_(fxHeader_('管理者・每日稽核', md + ' 稽核結果', '#0A4A3C'), [
    fxText_('比對 ' + (s['比對檔數'] || 0) + ' 檔・已修正 ' + count('已修正') + ' 項・' + stateLabel, { size: 'sm', color: LINE_C_.ink }),
    fxBox_('horizontal', [tile('明確錯誤', '#0A4A3C'), tile('待確認', '#2F7A63')], { margin: 'md' }),
    fxBox_('horizontal', [tile('邊界', '#5C8F7C'), tile('複核錯', '#8A958F')], { margin: 'sm' }),
    fxText_(!rows.length ? '網站內容與獨立判讀一致，今天沒有修改。' :
      fixed.length ? '往右滑看每一檔改了什麼。' : '今天沒有修改網站內容。', { size: 'sm', color: LINE_C_.ink, margin: 'md' }),
    pending.length ? fxText_('待你裁示 ' + pending.length + ' 項，請看下一則。', { size: 'sm', weight: 'bold', color: LINE_C_.amber, margin: 'sm' }) : null
  ], data.url ? [fxBtn_(lineUri_('完整紀錄', data.url), 'secondary')] : []);
  var items = fixed.map(function (r) {
    var cls = r['網站分類'] && r['複核分類'] && r['網站分類'] !== r['複核分類'] ? r['網站分類'] + ' → ' + r['複核分類'] : (r['複核分類'] || '');
    return lineAdminItemBubble_('已修改', r, [lineAdminLine_('分類', cls), lineAdminLine_('改成', lineAdminCut_(r['修正後'], 160)),
      lineAdminLine_('依據', r['原文依據'] ? '「' + lineAdminCut_(r['原文依據'], 80) + '」' : '')], '#0A4A3C');
  }).concat(failed.map(function (r) {
    // v220：修正未完成也可以直接裁示這一檔（原本只有待裁示卡片有按鈕，管理者在這裡回覆時系統不知道講哪一檔）
    var pick = 'a=rulepick&d=' + encodeURIComponent(data.day) + '&c=' + encodeURIComponent(r['代號'] || '') + '&n=' + encodeURIComponent(r['名稱'] || '');
    return lineAdminItemBubble_('修正未完成', r, [lineAdminLine_('網站', lineAdminCut_([r['網站分類'], r['修正前']].filter(String).join('｜'), 100)),
      lineAdminLine_('判斷', lineAdminCut_([r['複核分類'], r['判斷理由']].filter(String).join('｜'), 120)), lineAdminLine_('處理', r['處理'])], '#8A5A00',
      [fxBtn_(linePb_('裁示這一檔', pick, '裁示 ' + lineAdminName_(r)))]);
  }));
  var bubbles = [head].concat(items.slice(0, LINE_ADMIN_ITEM_BUBBLES_));
  if (items.length > LINE_ADMIN_ITEM_BUBBLES_) { bubbles.push(lineAdminMoreBubble_(items.length - LINE_ADMIN_ITEM_BUBBLES_, data.url)); }
  var alt = md + ' 稽核：已修正 ' + fixed.length + ' 項' + (pending.length ? '，待你裁示 ' + pending.length + ' 項' : '');
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
    lineAdminLine_(ok ? '網站' : '進度', lineAdminCut_(r['結果'] || stage[1], 200)),
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
function lineAdminReviewReply_(day) {
  var data = day ? lineAdminReviewData_(day, false) : lineAdminReviewData_(todayStr_(), true);
  if (!data) { return [lineText_(day ? (lineMd_(day) || day) + ' 沒有複核紀錄（休市、沒有影片，或那天複核沒有執行）。' : '還沒有複核紀錄。', lineAdminQuick_())]; }
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
  var intent = /待裁示|要裁示|需要裁示|待我裁示|待你裁示|還沒裁示|待確認|待處理/.test(s) ? 'pending'
    : /裁示進度|裁示狀態|處理到哪|處理進度|修正進度|修改進度/.test(s) ? 'progress'
    : /稽核|複核|改了什麼|修了什麼|修正了什麼|改了哪些|修了哪些|明確錯誤|邊界|複核錯/.test(s) ? 'review'
    : /怎麼裁示|裁示格式|裁示怎麼打|格式是什麼/.test(s) ? 'format' : '';
  if (!intent) { return null; }
  return { intent: intent, day: intent === 'format' || intent === 'progress' ? '' : lineAdminSpokenDate_(s, today) };
}
/** 規則判斷不了時才問 Gemini；只回 JSON 意圖與日期，失敗就回 null（照舊回服務範圍外）。 */
function lineAdminGeminiIntent_(raw, today) {
  if (typeof callGemini_ !== 'function') { return null; }
  var sys = '你是 LINE 機器人的意圖判斷器，使用者是網站管理者。只回 JSON：{"intent": "review|pending|progress|format|stock|other", "date": "YYYY/MM/DD 或空字串", "stock": "股票名稱或代號或空字串"}。' +
    'review＝想看某天複核／稽核結果、改了什麼、明確錯誤；pending＝想看某天還要他裁示的項目；progress＝想看裁示處理進度；format＝問裁示怎麼輸入；' +
    'stock＝想查某檔股票的紀錄；other＝其他。今天是 ' + today + '（台北）。相對日期換算成確切日期；沒提日期就給空字串。不要解釋。';
  try {
    var out = JSON.parse(String(callGemini_(sys, String(raw || '').slice(0, 200), { json: true, maxOut: 200, temperature: 0, maxWaitMs: 12000, singleAttempt: true }) || '{}'));
    var intent = String(out.intent || '');
    if (LINE_ADMIN_INTENTS_.indexOf(intent) < 0 && intent !== 'stock') { return null; }
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
