"""股名學習：每日複核確認「唸的名稱是另一檔的聽錯」後，把它寫進主資料的「名稱判定紀錄」（來源＝複核）。

管理者 2026/11/03：雲端複核全權確認股名聽錯，確認後寫進資料庫，之後的比對照著用（pipeline 的
manual_memo_match 會直接套用「人工」「複核」兩種來源，不再問模型），像讓流程從過去的經驗學習。

計畫檔 scripts/repairs/<日期>-name-memo.json：
  {"date": "2026/11/03", "items": [{"heard": "雅德客", "code": "1590", "name": "亞德客-KY",
     "keys": [], "why": "…", "quotes": ["原稿逐字句（要含唸的名稱）"]}]}
硬規則：
  - 代號與官方名稱一致；引句逐字出自當天原稿，而且至少一句含唸的名稱。
  - 唸的名稱本身是另一家公司的正式名稱（例：力致 3483）時，必須帶情境關鍵詞 keys，
    只有上下文出現那些詞才套用，否則以後真的講到那家公司會被換掉。
  - 表上已有同名、同情境、但對到另一檔的人工／複核紀錄：停止，交給管理者。
用法：python scripts/daily_review/name_memo.py --date 2026/11/03 [--apply]
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
TRUSTED = ('人工', '複核')


def plan_rows(head, rows, items, official, day):
    """純函式：回傳 (要新增的列, 已存在而略過的唸法, 問題清單)。rows 是表上現有資料列（不含表頭）。"""
    by_name = {}
    for code, name in official.items():
        by_name.setdefault(name, set()).add(code)
    col = {h: i for i, h in enumerate(head)}
    cell = lambda r, h: str(r[col[h]]).strip() if h in col and col[h] < len(r) else ''
    existing = [(cell(r, '聽到的名稱'), cell(r, '代號'), cell(r, '情境關鍵詞'), cell(r, '來源')) for r in rows]
    new, skipped, bad = [], [], []
    for it in items:
        heard, code, name = str(it.get('heard') or '').strip(), str(it.get('code') or '').strip(), str(it.get('name') or '').strip()
        keys = [k for k in (it.get('keys') or []) if str(k).strip()]
        if not heard or not code or not it.get('why') or not it.get('quotes'):
            bad.append(f'{heard or "?"}：缺少欄位（heard、code、why、quotes）')
            continue
        if official.get(code) != name:
            bad.append(f'{heard}：代號 {code} 的官方名稱是 {official.get(code) or "（查無）"}，不是 {name}')
            continue
        if by_name.get(heard, set()) - {code} and not keys:
            bad.append(f'{heard}：本身是另一家公司（{"、".join(sorted(by_name[heard]))}）的名稱，要帶情境關鍵詞才能記')
            continue
        key_text = '、'.join(keys)
        clash = [e for e in existing if e[0] == heard and e[2] == key_text and e[3] in TRUSTED and e[1] != code]
        if clash:
            bad.append(f'{heard}：表上已有{clash[0][3]}紀錄對到 {clash[0][1]}，需要管理者決定')
            continue
        if any(e[0] == heard and e[1] == code and e[2] == key_text for e in existing):
            skipped.append(heard)
            continue
        values = {'聽到的名稱': heard, '判定': 'stock', '正式名稱': name, '代號': code, '情境關鍵詞': key_text,
                  '依據': f'每日複核 {day} 確認：{it["why"]}'[:200], '來源': '複核', '建立日期': day,
                  '命中次數': 0, '最後命中': ''}
        new.append([values.get(h, '') for h in head])
        existing.append((heard, code, key_text, '複核'))
    return new, skipped, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / 'pipeline'))
    sys.path.insert(0, str(ROOT / 'scripts'))
    import pipeline as pl
    from apply_reviewed_points import source_quote
    day = pl.norm_date(args.date)
    path = ROOT / 'scripts/repairs' / (day.replace('/', '-') + '-name-memo.json')
    if not path.exists():
        print('沒有股名學習計畫：', path.name)
        return
    plan = json.loads(path.read_text(encoding='utf-8'))
    if plan.get('date') != day:
        raise SystemExit('計畫日期不符')
    book = pl.open_sheets()
    raw, _ = pl.existing_transcript(book, '', day, snapshot={})
    for it in plan['items']:
        for q in it.get('quotes') or []:
            source_quote(raw, q)
        if not any(it['heard'] in q for q in it.get('quotes') or []):
            raise SystemExit(f'{it["heard"]}：引句裡沒有唸的名稱')
    official = {str(k): pl._display_name(v) for k, v in pl.get_code_map().items()}
    try:
        ws = book.worksheet(pl.NAME_MEMO_SHEET)
    except Exception:
        ws = book.add_worksheet(title=pl.NAME_MEMO_SHEET, rows=500, cols=len(pl.NAME_MEMO_HEADERS))
        ws.append_row(pl.NAME_MEMO_HEADERS)
    values = pl.sheets_retry(ws.get_all_values) or [pl.NAME_MEMO_HEADERS]
    head = [str(h).strip() for h in values[0]]
    if [h for h in pl.NAME_MEMO_HEADERS if h not in head]:
        raise SystemExit('名稱判定紀錄表頭不完整，未寫入')
    new, skipped, bad = plan_rows(head, values[1:], plan['items'], official, day)
    if bad:
        raise SystemExit('股名學習不通過：\n' + '\n'.join(bad))
    for r in new:
        print('記入名稱判定紀錄（複核）：', r[head.index('聽到的名稱')], '→', r[head.index('代號')], r[head.index('正式名稱')],
              ('情境：' + r[head.index('情境關鍵詞')]) if r[head.index('情境關鍵詞')] else '')
    if skipped:
        print('表上已有、略過：', '、'.join(skipped))
    if not args.apply:
        print('預覽完成；寫入 0')
        return
    if new:
        pl.append_rows_safe(ws, new)
    after = pl.sheets_retry(ws.get_all_values)
    seen = [[str(x) for x in row[:len(head)]] for row in after]
    for r in new:
        if [str(x) for x in r] not in seen:
            raise SystemExit('寫入後讀回不一致')
    print(f'名稱判定紀錄：新增 {len(new)} 列、讀回一致；之後的比對會直接套用')


if __name__ == '__main__':
    main()
