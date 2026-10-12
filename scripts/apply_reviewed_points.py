"""Publish explicitly reviewed article points, keeping stock rows and delivery state."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import pipeline as pl

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def source_quote(raw, quote):
    positions = [i for i, ch in enumerate(raw) if not ch.isspace()]
    compact = ''.join(raw[i] for i in positions)
    needle = ''.join(quote.split())
    at = compact.find(needle)
    if at < 0 or len(needle) < 6:
        raise ValueError('核對引句不在原稿：' + quote)
    return raw[positions[at]:positions[at + len(needle) - 1] + 1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    args = parser.parse_args()
    day = pl.norm_date(args.date)
    reviewed = json.loads((ROOT / 'scripts/golden' / (day.replace('/', '-') + '.json')).read_text(encoding='utf-8')).get('public_points_review')
    if not reviewed:
        raise SystemExit('該日沒有人工逐句核對的章節修正；未寫入')
    book = pl.open_sheets()
    source = {}
    raw, _ = pl.existing_transcript(book, '', day, snapshot=source)
    if hashlib.sha256(raw.encode()).hexdigest() != reviewed['sha256']:
        raise SystemExit('原稿指紋已變；須重新核對，未寫入')
    points = []
    for item in reviewed['points']:
        point = dict(item)
        point['evidence'] = [source_quote(raw, q) for q in point.pop('quotes')]
        point.update(_evidence_verified=True, _manual_public_review=True)
        points.append(point)
    signals = {'market': points}
    points[0].update(headline=reviewed['headline'], _headline_checked=True)
    hard, _ = pl.print_quality_overview(signals, raw)
    if hard:
        raise SystemExit('人工章節仍不通過品質核對：' + '；'.join(hard))
    names = {w.title for w in book.worksheets()}
    ledgers = [n for n in ('寄送帳本', 'LINE 寄送帳本') if n in names]
    before = {n: digest(book.worksheet(n).get_all_values()) for n in ledgers + ['操作紀錄', '會員持股']}
    sent = pl._daily_article_status(book, day)
    stamp = datetime.now(pl.TAIPEI).strftime('%m%d_%H%M%S')
    for name in ('逐字稿判讀稽核', '每日推播內容'):
        book.duplicate_sheet(book.worksheet(name).id, new_sheet_name=f'備份_{stamp}_{name}')
    # Append a new evidence batch; do not rewrite the original model audit.
    pl.save_evidence_audit(book, source['video_id'], day, raw, signals)
    pl.commit_evidence_manifest(book, source['video_id'], day, raw)
    result = pl.maybe_refresh_site(force=True, only=['smsmail'], date_str=day) or {}
    if not result.get('ok'):
        raise RuntimeError('章節已保存，郵件查詢尚需接續刷新；未寄送')
    if sent != pl._daily_article_status(book, day) or any(before[n] != digest(book.worksheet(n).get_all_values()) for n in before):
        raise RuntimeError('同期資料或帳本變動，須讀回核對；本程式未更改分類或寄送')
    print('人工章節核對及網站刷新完成；原稿、個股列與寄送帳本不變，未呼叫模型或寄送')

if __name__ == '__main__':
    main()
