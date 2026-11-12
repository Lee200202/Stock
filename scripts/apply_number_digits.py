"""把某一天已發布的個股說明裡的中文數字換成阿拉伯數字（2026/11/12 管理者：「930 元都是中文，要呈現數字」）。

    python scripts/apply_number_digits.py --date 2026/11/12            # 預覽：列出會改的格
    python scripts/apply_number_digits.py --date 2026/11/12 --apply    # 備份 → 寫入 → 讀回 → 刷新網站與郵件查詢

只改「操作紀錄」理由摘錄與「會員持股」說明重點兩欄的數字寫法，換法和主流程寫入前相同
（pipeline.digits_for_reading）。不改分類、價位欄、成交事實、通知原文；不呼叫模型、不寄信、不動寄送帳本。
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
sys.path.insert(0, str(ROOT / 'scripts'))
import common  # noqa: F401,E402  雲端金鑰 Base64 先還原
import pipeline as pl  # noqa: E402

FIELDS = (('操作紀錄', '理由摘錄'), ('會員持股', '說明重點'))
RECEIPT = '數字寫法:'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_cells(values, field, day):
    """純函式：那一天要改的格 [(列號, 舊字, 新字)]（列號從 1 起算，含表頭）。"""
    if not values or field not in values[0]:
        raise ValueError(f'表頭沒有「{field}」')
    col = values[0].index(field)
    out = []
    for i, row in enumerate(values[1:], 2):
        if not row or pl.norm_date(row[0]) != day or col >= len(row):
            continue
        new = pl.digits_for_reading(row[col])
        if new != row[col]:
            out.append((i, row[col], new))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--date', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--refresh', action='store_true', help='資料已寫好、只接續刷新網站與郵件查詢')
    a = ap.parse_args()
    day = pl.norm_date(a.date)
    if a.refresh:
        result = pl.maybe_refresh_site(force=True, only=['smsmail', 'tracker', 'perf'], date_str=day) or {}
        print('刷新' + ('完成' if result.get('ok') else '未完成'))
        return 0 if result.get('ok') else 1
    book = pl.open_sheets()
    sheets = {tab: book.worksheet(tab) for tab, _ in FIELDS}
    before = {tab: pl.sheets_retry(ws.get_all_values) for tab, ws in sheets.items()}
    plan = {tab: plan_cells(before[tab], field, day) for tab, field in FIELDS}
    total = sum(len(v) for v in plan.values())
    for tab, cells in plan.items():
        for row, old, new in cells:
            print(f'{tab} 第 {row} 列：{old}\n  → {new}')
    print(f'{day}：要改 {total} 格')
    if not total or not a.apply:
        print('預覽完成（未寫入）' if total else '沒有要改的數字寫法')
        return 0
    from backup_archive import backup_tables
    names = {w.title for w in book.worksheets()}
    ledgers = [n for n in ('寄送帳本', 'LINE 寄送帳本') if n in names]
    ledger_before = {n: digest(book.worksheet(n).get_all_values()) for n in ledgers}
    sent = pl._daily_article_status(book, day)
    backup_tables(pl, book, ('操作紀錄', '會員持股', '每日推播內容'))
    requests = []
    for tab, field in FIELDS:
        now = sheets[tab].get_all_values()
        col = before[tab][0].index(field)
        for row, old, new in plan[tab]:
            if row > len(now) or now[row - 1] != before[tab][row - 1]:
                raise RuntimeError(f'{tab} 第 {row} 列在備份期間變了，未寫入')
            requests.append({'updateCells': {'start': {'sheetId': sheets[tab].id, 'rowIndex': row - 1, 'columnIndex': col},
                                             'rows': [{'values': [{'userEnteredValue': {'stringValue': new}}]}],
                                             'fields': 'userEnteredValue'}})
    book.batch_update({'requests': requests})
    for tab, field in FIELDS:
        now = sheets[tab].get_all_values()
        col = before[tab][0].index(field)
        for row, _, new in plan[tab]:
            if now[row - 1][col] != new:
                raise RuntimeError(f'{tab} 第 {row} 列寫入後讀回不一致')
    print(f'{total} 格寫入並讀回一致')
    stamp = datetime.now(pl.TAIPEI).strftime('%Y/%m/%d %H:%M:%S')
    meta = json.dumps({'receipt': RECEIPT + digest(plan), 'date': day,
                       'cells': {t: [[r, o, n] for r, o, n in c] for t, c in plan.items()}}, ensure_ascii=False)
    book.worksheet('修正建議').append_row([stamp, day, '數字寫法', '操作紀錄／會員持股', '', '', meta, '已套用（獨立備份可復原）', stamp],
                                       value_input_option='RAW')
    result = pl.maybe_refresh_site(force=True, only=['smsmail', 'tracker', 'perf'], date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('資料已保存；網站刷新未完成，加 --refresh 重跑接續刷新')
    article = ' '.join(next((r for r in book.worksheet('每日推播內容').get_all_values()[1:] if pl.norm_date(r[0]) == day), ['']))
    stale = [old for cells in plan.values() for _, old, new in cells if old in article and new not in article]
    if stale:
        print('注意：刷新後的每日整理仍有舊寫法（可能來自會員簡訊解析）：' + '；'.join(s[:30] for s in stale))
    if sent != pl._daily_article_status(book, day) or any(
            digest(book.worksheet(n).get_all_values()) != h for n, h in ledger_before.items()):
        raise RuntimeError('寄送狀態或帳本變動，請核對')
    print('網站與郵件查詢已刷新；寄送狀態與帳本未變，未呼叫模型、未寄信')
    return 0


if __name__ == '__main__':
    sys.exit(main())
