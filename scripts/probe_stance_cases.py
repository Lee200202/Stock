"""Development probe: real source, supplied identities, no labels in model input.

This isolates context/stance from inventory, positions, note writing and publication.
It is deliberately not a full pipeline acceptance or a held-out quality estimate.
"""
import argparse
import hashlib
import json
from pathlib import Path
from replay_extract import pl, DryBook


CASES = [
    ('8299', '群聯', ['群聯'], 'watch_watch'),
    ('6269', '台郡', ['台郡', '台俊'], 'watch_avoid'),
    ('1815', '富喬', ['富喬', '富橋'], 'watch_avoid'),
    ('1802', '台玻', ['台玻', '台播'], 'watch_avoid'),
    ('5289', '宜鼎', ['宜鼎', '移頂'], 'watch_watch'),
    ('3443', '創意', ['創意'], 'ignored'),
    ('3167', '大量', ['大量'], 'ignored'),
    ('5536', '聖暉', ['聖暉', '聖輝'], 'history'),
    ('3661', '世芯-KY', ['世芯-KY', '四星KY'], 'watch_avoid'),
]


def build_items(source):
    items = []
    for code, name, heard, _ in CASES:
        names = set(heard) | {code, name}
        zones = pl.semantic_windows(names, source)
        if not zones:
            raise ValueError('原稿無候選定位：' + code)
        items.append((code, {'name': name, 'heard': heard, 'known': [],
                            'passages': [{'before': '', 'text': z} for z in zones],
                            '_semantic_zones': zones, '_names': sorted(names)}))
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()
    if pl.norm_date(args.date) != '2026/10/21':
        raise SystemExit('此開發案例集僅綁2026/10/21，不冒稱適用其他日')
    book = DryBook(pl.open_sheets())
    raw, polished = pl.existing_transcript(book, '', args.date, snapshot={})
    if not raw:
        raise SystemExit('沒有保存原稿')
    source = pl.transcript_sources(raw, polished)['audit']
    items = build_items(source)
    report = {'kind': 'development_stance_probe_not_full_pipeline', 'date': args.date,
              'source_sha256': hashlib.sha256(raw.encode()).hexdigest(),
              'prompt_sha256': hashlib.sha256(pl.ADJUDICATE_SYSTEM.encode()).hexdigest(),
              'candidate_identities_supplied': True, 'classification_injection': False,
              'website_as_reference': False, 'read_only': True,
              'contexts': {code: item['_semantic_zones'] for code, item in items}, 'rounds': []}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        for turn in range(3):
            answers = pl._ask_verdicts(items, args.date, turn)
            rows = []
            for (code, item), (_, _, _, expected) in zip(items, CASES):
                zones = item['_semantic_zones']
                actual, why = pl.verdict_class(answers.get(code), ''.join(map(pl._ev_norm, zones)),
                                             item['_names'], zones, [], zones)
                rows.append({'code': code, 'expected': expected, 'actual': actual,
                             'matched': actual == expected, 'why': why, 'answer': answers.get(code)})
            report['rounds'].append(rows)
            print('probe round', turn + 1, sum(r['matched'] for r in rows), '/', len(rows))
    finally:
        report['usage'] = pl.gemini_usage_report()
        report['blocked_writes'] = book.blocked
        report['receipts'] = pl._VERDICT_BATCH_RECEIPTS
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if not all(row['matched'] for rows in report['rounds'] for row in rows):
        raise SystemExit(4)


if __name__ == '__main__':
    main()
