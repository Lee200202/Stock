"""固定既有說明與原稿，唯讀重播來源審閱；參考判斷僅在推論後比較。"""
import argparse
import hashlib
import json
from pathlib import Path
from replay_extract import pl, DryBook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True)
    parser.add_argument('--report-json', required=True)
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    book = DryBook(pl.open_sheets())
    reports, errors = [], []
    for day in cases['days']:
        raw, _ = pl.existing_transcript(book, '', day['date'], snapshot={})
        sha = hashlib.sha256(raw.encode()).hexdigest()
        if not raw or sha != day['source_sha256']:
            raise ValueError(day['date'] + ' 原稿不存在或指紋變更；不使用不同來源重播')
        signals = {}
        for case in day['notes']:
            field = 'note' if case['category'] == 'holdings' else 'reason'
            signals.setdefault(case['category'], []).append({
                'name': case['name'], 'code': case['code'], field: case['note']})
        # 不呼叫擷取／分類、不讀操作或部位帳本，也不把expected傳入模型。
        pl.polish_public_texts(signals, raw, day['date'])
        rows = []
        for case in day['notes']:
            row = next(r for r in signals[case['category']] if r['code'] == case['code'])
            review = row.get('_source_attribution_review') or {}
            expected = case.get('expected_review_status') or []
            passed = review.get('status') in expected if expected else None
            if passed is False:
                errors.append(f'{day["date"]} {case["code"]}：來源審核草稿期望{expected}，實測{review.get("status")}')
            rows.append({'code': case['code'], 'name': case['name'], 'input_note': case['note'],
                         'review': review, 'expected_review_status': expected, 'reference_match': passed})
        reports.append({'date': day['date'], 'source_sha256': sha, 'notes': rows})
    result = {'status': 'completed', 'date': '固定說明跨日來源審閱', 'read_only': True,
              'classification_evaluation': 'not_evaluated', 'classification_injection': False,
              'build': pl.pipeline_build(), 'blocked_writes': len(book.blocked),
              'case_manifest_sha256': hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
              'reference_kind': '來源審核草稿，非人類核定金標；expected只在推論後比對',
              'days': reports, 'errors': errors, 'warnings': [], 'rows': {},
              'usage': pl.gemini_usage_report(), 'exit_code': 4 if errors else 0}
    destination = Path(args.report_json)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('usage', 'errors', 'exit_code')}, ensure_ascii=False))
    raise SystemExit(result['exit_code'])


if __name__ == '__main__':
    main()
