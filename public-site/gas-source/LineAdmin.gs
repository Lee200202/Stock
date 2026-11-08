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
/** 純函式：補上年份並檢查期限。today 'yyyy/MM/dd'。回傳 { date } 或 { error }。 */
function lineAdminRuleDate_(rule, today) {
  if (!rule.month) { return { date: today }; }
  var ty = Number(today.slice(0, 4)), y = rule.year || ty;
  var pad = function (n) { return ('0' + n).slice(-2); };
  var date = y + '/' + pad(rule.month) + '/' + pad(rule.day);
  if (!rule.year && date > today) { date = (ty - 1) + '/' + pad(rule.month) + '/' + pad(rule.day); }
  var d = new Date(date.replace(/\//g, '-') + 'T00:00:00+08:00'), t = new Date(today.replace(/\//g, '-') + 'T00:00:00+08:00');
  if (isNaN(d.getTime()) || d.getMonth() + 1 !== rule.month) { return { error: '日期看不懂：' + rule.month + '/' + rule.day }; }
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
  return [
    fxText_('格式：分類—說明', { size: 'md', weight: 'bold', color: LINE_C_.ink }),
    fxText_('分類寫在「—」前面，管理者的說明寫在後面。沒寫日期就是今天；要改別天，在最前面加日期與代號。', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
    fxBox_('vertical', [
      fxText_('觀望注意—原稿說等拉回月線再看，是正面等待，不是不碰', { size: 'sm', color: LINE_C_.ink }),
      fxText_('2330 會員持股—紀錄上還沒出場，持股說明照原稿寫', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
      fxText_('11/06 3008 觀望注意—等量縮回穩才買，屬於注意', { size: 'sm', color: LINE_C_.ink, margin: 'sm' }),
      fxText_('2317 不列—只是舉例，沒有本股看法', { size: 'sm', color: LINE_C_.ink, margin: 'sm' })
    ], { backgroundColor: LINE_C_.soft, cornerRadius: '12px', paddingAll: '12px', margin: 'md' }),
    fxText_('可用分類：' + LINE_ADMIN_CATS_.join('、'), { size: 'xs', color: LINE_C_.muted, margin: 'md' }),
    fxNote_('先按卡片上的「裁示這一檔」，就只要輸入「分類—說明」。送出前會再給你一張確認卡。')
  ];
}
function lineAdminFormatFlex_() {
  return lineFlex_('裁示格式：分類—說明，例如「觀望注意—原稿說等拉回再看」。',
    fxBubble_(fxHeader_('管理者・裁示格式', '這樣輸入'), lineAdminFormatLines_(), []));
}
function lineAdminHomeFlex_(justOn) {
  var rows = [['每天複核完成後', '自動收到兩則：今日稽核結果、待你裁示'], ['裁示', '輸入「分類—說明」，確認後雲端改網站並回報'],
              ['使用者功能照舊', '每日總覽、盤中通知、查個股都不變']];
  return lineFlex_('管理者模式' + (justOn ? '已開啟' : '') + '：今日稽核、待裁示、裁示格式、回到使用者。', fxBubble_(
    fxHeader_('管理者模式', justOn ? '已切換成管理者' : '管理者功能', '#0A4A3C'),
    rows.map(function (r) { return fxBox_('vertical', [fxText_(r[0], { size: 'sm', weight: 'bold', color: LINE_C_.ink }), fxText_(r[1], { size: 'xs', color: LINE_C_.muted })]); })
      .concat([fxNote_('輸入「取消管理者」回到一般使用者。')]),
    [fxBtn_(linePb_('今日稽核結果', 'a=adminreview', '今日稽核')), fxBtn_(linePb_('待裁示', 'a=adminpending', '待裁示'), 'secondary'),
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
    return lineAdminItemBubble_('修正未完成', r, [lineAdminLine_('網站', lineAdminCut_([r['網站分類'], r['修正前']].filter(String).join('｜'), 100)),
      lineAdminLine_('判斷', lineAdminCut_([r['複核分類'], r['判斷理由']].filter(String).join('｜'), 120)), lineAdminLine_('處理', r['處理'])], '#8A5A00');
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
function lineAdminConfirmFlex_(d) {
  var color = LINE_ADMIN_CAT_COLOR_[d.cat] || LINE_C_.accent;
  return lineFlex_('確認裁示：' + d.date + ' ' + d.name + ' ' + d.code + ' → ' + d.cat, fxBubble_(
    fxHeader_('確認裁示', (lineMd_(d.date) || d.date) + ' ' + d.name + ' ' + d.code, '#0A4A3C'),
    [fxBox_('horizontal', [lineAdminChip_(d.cat, color)]),
     lineAdminLine_('管理者說明', d.text),
     fxNote_('送出後雲端會依原稿寫修正、更新網站，完成後在這裡回報（約 5–20 分鐘）。只改這一檔，不改通用規則。')],
    [fxBtn_(linePb_('確認送出', 'a=ruleok&k=' + encodeURIComponent(d.id), '確認送出裁示')),
     fxBtn_(linePb_('取消', 'a=ruleno&k=' + encodeURIComponent(d.id), '取消這則裁示'), 'secondary')]));
}
/** 裁示結果回報：rows 是「管理者裁示」的列。 */
function lineAdminResultFlex_(rows) {
  var bubbles = rows.slice(0, 11).map(function (r) {
    var st = r['狀態'], ok = st === '已完成';
    var color = ok ? '#0A4A3C' : st === '已記錄' ? '#2F7A63' : '#8A5A00';
    var title = (lineMd_(r['日期']) || r['日期']) + ' ' + (r['名稱'] || '') + ' ' + (r['代號'] || '');
    return fxBubble_(fxHeader_(ok ? '裁示已完成' : st === '已記錄' ? '裁示已記錄' : '裁示未完成', title, color), [
      fxBox_('horizontal', [lineAdminChip_(r['裁示分類'], LINE_ADMIN_CAT_COLOR_[r['裁示分類']])]),
      lineAdminLine_('管理者說明', lineAdminCut_(r['管理者說明'], 160)),
      lineAdminLine_(ok ? '網站' : '結果', lineAdminCut_(r['結果'] || (ok ? '已套用並刷新網站' : ''), 200)),
      ok ? fxNote_('網站已更新；12:30 狀態信與複核紀錄也會列在「今日網站修改」。') : null
    ], []);
  });
  var done = rows.filter(function (r) { return r['狀態'] === '已完成'; }).length;
  var alt = '裁示回報：完成 ' + done + ' 項' + (rows.length - done ? '，未完成或只記錄 ' + (rows.length - done) + ' 項' : '');
  return { type: 'flex', altText: alt, contents: bubbles.length > 1 ? { type: 'carousel', contents: bubbles } : bubbles[0] };
}

/* ---------------- 互動：查看、挑選、裁示、確認 ---------------- */
function lineAdminDeny_() { return [lineText_('這個指令只開放給管理者。', lineQuickMain_())]; }
function lineAdminReviewReply_() {
  var data = lineAdminReviewData_(todayStr_(), true);
  if (!data) { return [lineText_('還沒有複核紀錄。', lineAdminQuick_())]; }
  var out = [lineAdminReviewFlex_(data)], p = lineAdminPendingFlex_(data);
  if (p) { out.push(p); }
  return out;
}
function lineAdminPendingReply_() {
  var data = lineAdminReviewData_(todayStr_(), true);
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

/* ---------------- 五分鐘排程 ---------------- */
function lineAdminTick_() {
  if (!lineConfigured_() || linePushMode_() === 'off') { return { skipped: 'off' }; }
  var out = {};
  try { out.rulings = lineAdminRulingTick_(); } catch (e) { out.rulingsError = String(e && e.message || e); }
  try { out.review = lineAdminReviewTick_(); } catch (e2) { out.reviewError = String(e2 && e2.message || e2); }
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
  var ready = rows.filter(function (r) { return /^(?:已完成|失敗|已記錄)$/.test(r['狀態']) && !String(r['LINE回報'] || '').trim(); });
  var open = rows.filter(function (r) { return /^(?:待處理|處理中|套用中)$/.test(r['狀態']); });
  var res = { reported: 0, open: open.length };
  if (ready.length) {
    var ids = ready.map(function (r) { return r['裁示ID']; });
    var q = lineQueue_({ id: 'admin-rule|' + ids.join(',').slice(0, 120), kind: 'admin', date: todayStr_(), source: '管理者裁示 ' + ids.join(','),
      expiresAt: Date.now() + 6 * 3600 * 1000, messages: [lineAdminResultFlex_(ready)] });
    var col = LINE_ADMIN_RULE_COLS_.indexOf('LINE回報') + 1, stamp = lineNow_();
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
