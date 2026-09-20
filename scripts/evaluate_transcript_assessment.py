"""Compare final resolved stock categories with a reviewed transcript baseline.

This is a local comparison, not an AI or Sheets call. Input may be a date mapping
or one signals object with --date. Exit 1 means differences need human review.
"""
import argparse
import json
from pathlib import Path


CATEGORIES = ('buy', 'sell', 'watch_avoid', 'watch_watch', 'holdings')


def identities(signals):
    return {(category, str(row.get('code') or row.get('name') or '').strip())
            for category in CATEGORIES for row in signals.get(category, [])
            if isinstance(row, dict)}


def compare(expected, actual):
    wanted, found = identities(expected), identities(actual)
    return {'missing': sorted(wanted-found), 'unexpected': sorted(found-wanted),
            'matched': len(wanted & found), 'expected': len(wanted), 'actual': len(found)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--actual', type=Path, required=True)
    parser.add_argument('--date')
    args = parser.parse_args()
    expected = json.loads(args.expected.read_text(encoding='utf-8-sig'))
    actual = json.loads(args.actual.read_text(encoding='utf-8-sig'))
    expected = expected.get('dates', expected)
    actual = actual.get('dates', actual)
    if args.date and any(c in actual for c in CATEGORIES):
        actual = {args.date: actual}
    results = {day:compare(expected[day], actual.get(day, {}))
               for day in expected if not args.date or day == args.date}
    if not results:
        parser.error('基準檔找不到指定日期')
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return int(any(r['missing'] or r['unexpected'] for r in results.values()))


if __name__ == '__main__':
    raise SystemExit(main())
