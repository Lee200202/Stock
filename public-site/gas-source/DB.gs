/** 訂閱資料存取；批次寫入須置於 withLock_ 的讀改寫範圍內。 */

/** 批次更新相鄰欄位；不覆寫間隔欄位或公式。
 * @param {Object} hit 在鎖內重新查得的訂閱列。
 * @param {Object<string,string>} changes 欄名與新值。
 * @return {void}
 */
function writeSubscriptionFields_(hit, changes) {
  var columns = Object.keys(changes).map(function (name) {
    var index = hit.head.indexOf(name);
    if (index < 0) { throw new Error('訂閱表缺少欄位：' + name); }
    return {index:index,value:changes[name]};
  }).sort(function (a,b) { return a.index-b.index; });
  var groups = [];
  columns.forEach(function (column) {
    var group = groups[groups.length-1];
    if (!group || column.index !== group.start + group.values.length) {
      group = {start:column.index,values:[]}; groups.push(group);
    }
    group.values.push(column.value);
  });
  groups.forEach(function (group) {
    hit.sheet.getRange(hit.rowNum,group.start+1,1,group.values.length).setValues([group.values]);
  });
}

/** 依 Email 找出那一列生效中的訂閱。找不到或已取消都回 null。 */
function findSubscription_(email) {
  email = String(email || '').trim().toLowerCase();
  if (!email) { return null; }
  var sh = getSheet_('使用者訂閱清單');
  var vals = sh.getDataRange().getValues();
  if (vals.length < 2) { return null; }
  var head = vals[0].map(function (h) { return String(h).trim(); });
  var iEmail = head.indexOf('Email');
  var iState = head.indexOf('狀態');
  if (iEmail < 0) { return null; }
  for (var i = 1; i < vals.length; i++) {
    if (String(vals[i][iEmail]).trim().toLowerCase() !== email) { continue; }
    if (iState >= 0 && String(vals[i][iState]).trim() === '已取消') { continue; }
    var row = {};
    head.forEach(function (h, n) { row[h] = vals[i][n]; });
    return { rowNum: i + 1, head: head, row: row, sheet: sh };
  }
  return null;
}
