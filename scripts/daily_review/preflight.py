"""推修正分支前的總檢查（2026/11/10 管理者：格式在雲端排程就要確定，GitHub 只負責套用，不該再因計畫檔錯誤而失敗）。

    python scripts/daily_review/preflight.py --date 2026/11/10 [--raw .daily-review/20261110/raw.txt]

11/10 套用推了五輪才成功，其中三輪是計畫檔本身的問題，要到 GitHub 才發現：
  - 引句不到 6 字（「昨天漲停」）；
  - 刪除項缺舊說明指紋（世界 5347）；
  - 同一則通知改兩檔（工具的錯，已另修）。
這支把四種計畫（notice-reason／source-review／points-review／notice-ruling）一次用和套用工具相同的規則檢查：
欄位齊全、指紋格式、原稿指紋、每句引句逐字在原稿且至少 6 字、新文字不含通知字眼、一份通知計畫只改一張表、章節品質。
不連試算表、不呼叫模型、不寫任何東西；有問題就列出全部並以結束代碼 1 結束（不要推送）。
雲端排程在 write_log.py 之後、git push 之前執行；daily-review-apply.yml 也會在動任何資料前先跑一次。
"""
import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
os.environ.setdefault('GEMINI_API_KEY', 'offline')
HEX64 = re.compile(r'[0-9a-f]{64}')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def quote_problems(raw, quotes, where):
    """純函式（給定 source_quote）：引句至少 6 字且逐字在原稿。"""
    from apply_reviewed_points import source_quote
    out = []
    for q in quotes or []:
        if len(''.join(str(q).split())) < 6:
            out.append(f'{where}：引句不到 6 字「{q}」，請引長一點的原句')
            continue
        try:
            source_quote(raw, q)
        except ValueError:
            out.append(f'{where}：引句不在原稿「{q}」')
    return out


def check_notice_reason(plan, raw, day):
    from apply_notice_reason_fix import check_item, target
    out = []
    if plan.get('date') != day or not plan.get('authorization') or not plan.get('review_basis'):
        out.append('notice-reason：缺日期一致的 authorization／review_basis')
    if raw is not None and plan.get('raw_sha256') and sha(raw) != plan['raw_sha256']:
        out.append('notice-reason：raw_sha256 與今天原稿不符')
    try:
        if len({target(i)[0] for i in plan.get('items', [])}) > 1:
            out.append('notice-reason：一份計畫只改一張表（操作紀錄或會員持股），請拆成兩份')
    except ValueError as exc:
        out.append('notice-reason：' + str(exc))
    for item in plan.get('items', []):
        code = str(item.get('code') or '?')
        for k in ('code', 'name', 'notice_source_id', 'article_id', 'previous_reason_sha256', 'previous_reason_chars', 'text'):
            if not item.get(k):
                out.append(f'notice-reason {code}：缺 {k}')
        for k in ('previous_reason_sha256', 'previous_detail_sha256'):
            if item.get(k) and not HEX64.fullmatch(str(item[k])):
                out.append(f'notice-reason {code}：{k} 不是 64 位十六進位指紋')
        try:
            check_item(item, None, lambda r, q: None, raw)
        except ValueError as exc:
            out.append(f'notice-reason {code}：{exc}')
        if raw is not None:
            out += quote_problems(raw, item.get('quotes'), 'notice-reason ' + code)
    return out


def check_source_review(plan, raw, day):
    from apply_source_review import plan_problems
    out = []
    if plan.get('date') != day or not plan.get('authorization') or not plan.get('review_basis'):
        out.append('source-review：缺日期一致的 authorization／review_basis')
    if raw is not None and plan.get('raw_sha256') and sha(raw) != plan['raw_sha256']:
        out.append('source-review：raw_sha256 與今天原稿不符')
    out += ['source-review ' + p for p in plan_problems(plan)]
    out += [f"source-review {i.get('code')}：缺 rationale" for i in plan.get('items', []) if not i.get('rationale')]
    if raw is not None:
        for item in plan.get('items', []):
            if item.get('action') != 'delete':
                out += quote_problems(raw, item.get('quotes'), 'source-review ' + str(item.get('code')))
    return out


def check_notice_ruling(plan, raw, day):
    from apply_notice_ruling import check_plan
    out = []
    try:
        check_plan(plan, day)
    except ValueError as exc:
        out.append('notice-ruling：' + str(exc))
    if raw is not None:
        for item in plan.get('items', []):
            out += quote_problems(raw, item.get('quotes'), 'notice-ruling ' + str(item.get('code')))
    return out


def check_points(path, raw_path):
    import chapters
    if raw_path is None:
        return []
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            ok = chapters.check_plan(str(path), str(raw_path))
        except (SystemExit, ValueError) as exc:
            ok, buf = False, io.StringIO(str(exc))
    lines = buf.getvalue().strip().splitlines()
    return [] if ok else ['points-review：章節核對不通過' + ('——' + lines[-1] if lines else '')]


CHECKS = {'notice-reason': check_notice_reason, 'source-review': check_source_review,
          'notice-ruling': check_notice_ruling}


def preflight(day, raw_path=None, root=ROOT):
    """回傳 (檢查過的計畫檔, 問題清單)。"""
    raw = Path(raw_path).read_text(encoding='utf-8') if raw_path and Path(raw_path).exists() else None
    stem = day.replace('/', '-')
    seen, problems = [], []
    for kind in ('notice-reason', 'source-review', 'notice-ruling', 'points-review'):
        path = root / 'scripts/repairs' / f'{stem}-{kind}.json'
        if not path.exists():
            continue
        seen.append(path.name)
        try:
            plan = json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:
            problems.append(f'{path.name}：不是有效的 JSON（{exc}）')
            continue
        if kind == 'points-review':
            problems += check_points(path, raw_path if raw is not None else None)
        else:
            problems += CHECKS[kind](plan, raw, day)
    return seen, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--date', required=True)
    ap.add_argument('--raw', help='當天 raw.txt；不給就找 .daily-review/<YYYYMMDD>/raw.txt，找不到只做格式檢查')
    a = ap.parse_args()
    day = a.date.replace('-', '/')
    raw_path = a.raw or str(ROOT / '.daily-review' / day.replace('/', '') / 'raw.txt')
    if not Path(raw_path).exists():
        print('（沒有原稿檔，只做格式與指紋檢查；引句與章節核對交給套用工具）')
        raw_path = None
    seen, problems = preflight(day, raw_path)
    if not seen:
        print('沒有修正計畫：', day)
        return 0
    if problems:
        print('推送前檢查不通過（' + '、'.join(seen) + '）：')
        for p in problems:
            print('  -', p)
        return 1
    print('推送前檢查通過：' + '、'.join(seen))
    return 0


if __name__ == '__main__':
    sys.exit(main())
