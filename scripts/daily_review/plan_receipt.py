"""列出某天哪些修正計畫已經真的套用過（主資料「修正建議」有該計畫內容指紋的收據）。

    python scripts/daily_review/plan_receipt.py --date 2026/11/10
    → 每份已套用的計畫印一行：APPLIED scripts/repairs/2026-11-10-source-review.json

2026/11/10：daily-review-apply.yml 以前只要「這次推送沒改到」就跳過那份計畫，假設前一輪已套用；
11/10 第三輪通知理由計畫失敗，第四、五輪卻因此跳過它。現在改成：沒改到、而且有收據，才跳過。
收據格式與各工具相同：<種類>:sha256(json.dumps(計畫, ensure_ascii=False, sort_keys=True))。只讀，不寫。
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIX = {'notice-reason': '通知理由修正:', 'source-review': '來源核對修正:',
          'points-review': '章節修正:', 'notice-ruling': '通知裁示修正:'}


def receipt_of(kind, plan):
    return PREFIX[kind] + hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def applied(day, receipt_rows, root=ROOT):
    """純函式：回傳已套用（有收據）的計畫相對路徑清單。"""
    blob = '\n'.join(' '.join(map(str, r)) for r in receipt_rows)
    out = []
    for kind in PREFIX:
        rel = f'scripts/repairs/{day.replace("/", "-")}-{kind}.json'
        path = root / rel
        if path.exists() and receipt_of(kind, json.loads(path.read_text(encoding='utf-8'))) in blob:
            out.append(rel)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', required=True)
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
    sys.path.insert(0, str(ROOT / 'pipeline'))
    import common  # noqa: F401  金鑰若是 Base64 先還原
    import pipeline as pl
    rows = pl.sheets_retry(pl.open_sheets().worksheet('修正建議').get_all_values)
    for rel in applied(a.date.replace('-', '/'), rows[1:]):
        print('APPLIED', rel)


if __name__ == '__main__':
    main()
