"""主資料分卷的正式驗收（2026/11/25）。只印數字與「通過／不通過」，不印試算表 ID、不印任何儲存格內容（公開 repo 的 Actions 紀錄看得到）。

  python scripts/main_volume_check.py status        # 目前使用中第幾卷、有沒有備用卷
  python scripts/main_volume_check.py standby       # 建立（或沿用）備用卷：Apps Script 以擁有者帳號建立、表頭對齊、分享給服務帳號
  python scripts/main_volume_check.py selftest      # 實際讀寫驗收（不影響正式資料，測試列寫完即刪）
  python scripts/main_volume_check.py trim-preview  # 只算清空白欄可省多少格，不動主表

selftest 逐項：
  1. 路由：GitHub secret 的 SPREADSHEET_ID 是使用中的一卷，Apps Script 回得出備用卷。
  2. 服務帳號開得了備用卷（分享與 API 串接）；每張分頁的表頭與主表逐欄相同。
  3. 備用卷寫得進去：新增暫時分頁寫一列、讀回相同、刪掉分頁。
  4. 合併讀取（Python）：以「主表＝舊卷、備用卷＝新卷」組合讀使用紀錄，筆數＝主表筆數＋測試列，欄位對齊；測試列刪除。
  5. 合併讀取（Apps Script）：同上，於 Apps Script 端讀使用紀錄與日K快取。
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import main_volumes as mv  # noqa: E402


def client_and_email():
    import gspread
    raw = os.environ['GOOGLE_SHEETS_SERVICE_ACCOUNT'].strip()
    if not raw.startswith('{'):
        raw = base64.b64decode(raw + '=' * (-len(raw) % 4)).decode()
    info = json.loads(raw)
    return gspread.service_account_from_dict(info), info.get('client_email', '')


def gas(op, payload=None):
    data = mv._call(op, payload or {})
    if not data:
        raise SystemExit(f'Apps Script 沒有回應 {op}（/exec 可能還不是含分卷的版本）')
    if not data.get('ok', False):
        raise SystemExit(f'{op} 失敗：' + str(data.get('error', ''))[:200])
    return data


def heads(book):
    names = [ws.title for ws in book.worksheets()]
    got = book.values_batch_get([f"'{n}'!1:1" for n in names]).get('valueRanges', [])
    out = {}
    for n, vr in zip(names, got):
        row = (vr.get('values') or [[]])[0]
        while row and not str(row[-1]).strip():
            row.pop()
        out[n] = [str(x).strip() for x in row]
    return out


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else 'status').strip()
    gc, email = client_and_email()
    if mode == 'status':
        s = gas('main-status')
        print(f"使用中：第 {len(s.get('archives') or []) + 1} 卷；舊卷 {len(s.get('archives') or [])} 份；"
              f"備用卷：{'有（第 ' + str(s['standby']['volume']) + ' 卷，' + s['standby']['at'] + ' 建立）' if s.get('standby') else '尚未建立'}；"
              f"換卷進行中：{'是' if s.get('switching') else '否'}；跨卷合併讀取的分頁：{'、'.join(s.get('splitTabs') or [])}")
        for x in s.get('log') or []:
            print(f"  換卷紀錄：{x.get('at')} 共複製 {x.get('tabs')} 張分頁；原因：{x.get('reason')}")
        return 0
    if mode == 'trim-preview':
        t = gas('main-trim-preview')
        print(f"清空白欄可省 {t.get('cells', 0):,} 格（只算不刪）。最多的分頁：")
        for x in t.get('tabs') or []:
            print(f"  {x['name']}：{x['columns']} 欄 × 列數 = {x['cells']:,} 格")
        return 0
    if mode == 'standby':
        t0 = time.time()
        r = gas('main-standby', {'serviceEmail': email})
        a = r.get('align') or {}
        print(f"備用卷：{'新建立' if r.get('created') else '沿用既有'}（第 {r.get('volume')} 卷）；分享給服務帳號：{'是' if r.get('shared') else '否'}；"
              f"分頁 {a.get('tabs')} 張、本次新建 {a.get('created')} 張、補表頭 {len(a.get('fixed') or [])} 張、表頭不一致 {len(a.get('mismatch') or [])} 張；{time.time() - t0:.0f} 秒")
        if a.get('mismatch'):
            print('  表頭不一致的分頁：' + '、'.join(a['mismatch']))
            return 1
        return 0 if r.get('shared') else 1
    if mode != 'selftest':
        raise SystemExit('不認得的模式：' + mode)

    ok = True
    secret = os.environ['SPREADSHEET_ID'].strip()
    route = gas('main-route', {'serviceEmail': email})
    standby = route.get('standby') or ''
    c1 = route.get('active') == secret and bool(standby) and not route.get('archives')
    print(f"1. 路由：secret 指向使用中的一卷 {'是' if route.get('active') == secret else '否'}；舊卷 {len(route.get('archives') or [])} 份；備用卷 {'有' if standby else '沒有'} → {'通過' if c1 else '不通過'}")
    ok &= c1
    if not standby:
        return 1
    main_book, sb = gc.open_by_key(secret), gc.open_by_key(standby)
    hm, hs = heads(main_book), heads(sb)
    missing = [n for n in hm if n not in hs]
    diff = [n for n in hm if n in hs and hm[n] != hs[n]]
    c2 = not missing and not diff
    print(f"2. 服務帳號開得了備用卷；主表 {len(hm)} 張分頁，備用卷 {len(hs)} 張；缺 {len(missing)} 張、表頭不同 {len(diff)} 張 → {'通過' if c2 else '不通過'}")
    if missing or diff:
        print('   ' + '、'.join(missing + diff))
    ok &= c2
    title = '分卷驗收_' + time.strftime('%H%M%S')
    ws = sb.add_worksheet(title, rows=5, cols=3)
    try:
        ws.update([['欄一', '欄二'], ['分卷驗收', '寫入讀回']], 'A1', value_input_option='RAW')
        back = ws.get_all_values()
        c3 = back == [['欄一', '欄二'], ['分卷驗收', '寫入讀回']]
    finally:
        sb.del_worksheet(ws)
    gone = title not in [w.title for w in sb.worksheets()]
    print(f"3. 備用卷寫入、讀回一致、暫時分頁已刪 → {'通過' if c3 and gone else '不通過'}")
    ok &= c3 and gone
    old_vals = main_book.worksheet('使用紀錄').get_all_values()
    sw = sb.worksheet('使用紀錄')
    head = [str(h).strip() for h in sw.row_values(1)]
    mark = 'VOLTEST' + time.strftime('%H%M%S')
    col = head.index('頁面')
    test = ['' for _ in head]
    test[col] = mark
    sw.append_rows([test], value_input_option='RAW')
    try:
        new_vals = sw.get_all_values()
        merged = mv.merge_values([old_vals, new_vals])
        expect = len(mv.merge_values([old_vals])) - 1
        c4 = merged[0] == head and len(merged) - 1 == expect + 1 and merged[-1][col] == mark
    finally:
        last = len(sw.get_all_values())
        if last >= 2 and sw.cell(last, col + 1).value == mark:
            sw.delete_rows(last)
    clean = len(sw.get_all_values()) <= 1
    print(f"4. Python 合併讀取使用紀錄：主表 {expect:,} 筆＋備用卷測試 1 筆＝合併 {len(merged) - 1:,} 筆；表頭對齊；測試列已刪 {'是' if clean else '否'} → {'通過' if c4 and clean else '不通過'}")
    ok &= c4 and clean
    g = gas('main-selftest', {})
    for t in g.get('tabs') or []:
        print(f"5. Apps Script 合併讀取{t['name']}：主表 {t.get('oldRows', 0):,} 筆、合併 {t.get('mergedRows', 0):,} 筆、表頭對齊 {'是' if t.get('headAligned') else '否'}、最後一筆是測試列 {'是' if t.get('lastIsTest') else '否'} → {'通過' if t.get('ok') else '不通過'}")
    print(f"   測試列已刪：{'是' if g.get('cleaned') else '否'}")
    ok &= bool(g.get('ok')) and bool(g.get('cleaned'))
    print('分卷驗收：' + ('全部通過' if ok else '有項目不通過'))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
