"""固定既有說明與原稿，唯讀重播來源審閱；參考判斷僅在推論後比較。"""
import argparse
import hashlib
import json
from pathlib import Path
from replay_extract import pl, DryBook


def run_verdict_probes(probes, signals, raw, day):
    """只對指定身分讀全篇問三輪；期待分類在推論完成後才讀取。"""
    items, trace = [], {}
    for spec in probes:
        code, name = spec['code'], spec['name']
        passages = pl._own_segments({'code': code, 'name': name}, signals, raw)
        marked, _ = pl._verdict_mark(passages, [name])
        items.append((code, {'name': name, 'heard': [name], 'known': [], 'passages': marked}))
        trace[code] = []
    if not items:
        return [], []
    for index in range(3):
        answers = pl._ask_verdicts(items, day, index, raw)
        for code, item in items:
            answer = answers.get(code) or {}
            category, basis = pl.verdict_class(answer, pl._ev_norm(raw), names=[item['name']])
            trace[code].append({'round': index + 1, 'class': category, 'basis': basis, 'answer': answer})
    results, errors = [], []
    for spec in probes:
        code = spec['code']
        state, category, why = pl.verdict_settle('uncertain', [r['class'] for r in trace[code]], last=True)
        allowed = spec.get('expected_categories') or []
        passed = state == '定案' and category in allowed if allowed else None
        results.append({'code': code, 'name': spec['name'], 'state': state, 'category': category,
            'basis': why, 'round_answers': trace[code], 'expected_categories': allowed, 'reference_match': passed})
        if passed is False:
            errors.append(f'{day} {code}：局部分類探針未通過：{state}／{category}')
    return results, errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True)
    parser.add_argument('--report-json', required=True)
    parser.add_argument('--date', help='只測指定日；未指定時沿用全部固定日期')
    args = parser.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding='utf-8'))
    book = DryBook(pl.open_sheets())
    # 與完整流程相同的公司識別背景；只讀既有別名，不讀分類答案、通知或部位，不新增別名。
    pl.get_code_map()
    pl.name_memo_load(book)
    reports, errors = [], []
    for day in cases['days']:
        if args.date and pl.norm_date(args.date) != day['date']:
            continue
        raw, _ = pl.existing_transcript(book, '', day['date'], snapshot={})
        sha = hashlib.sha256(raw.encode()).hexdigest()
        if not raw or sha != day['source_sha256']:
            raise ValueError(day['date'] + ' 原稿不存在或指紋變更；不使用不同來源重播')
        pl._DAY_SOURCE_CONTEXT.clear()
        notice_count = 0
        if day.get('include_original_notices'):
            # 只讀原通知，不讀解析明細、操作、持股、分類答案或寄送資料。
            values = book.worksheet('會員簡訊').get_all_values()
            notices = []
            for cells in values[1:] if values else []:
                row = dict(zip(values[0], cells))
                if pl.norm_date(str(row.get('發文時間') or '')[:10]) == day['date'] and row.get('原文'):
                    notices.append({'id': str(row.get('文章ID') or ''), 'original': row['原文']})
            pl._DAY_SOURCE_CONTEXT.update(date=day['date'], transcript_sha256=sha, notices=notices,
                company_candidates={c['code']: (pl._CODE_MAP or {}).get(c['code']) or c['name'] for c in day['notes']})
            notice_count = len(notices)
        signals = {}
        for case in day['notes']:
            field = 'note' if case['category'] == 'holdings' else 'reason'
            row = {'name': case['name'], 'code': case['code'], field: case['note']}
            # 沿用保存模型列當時的身分候選，不把它們加成全域別名，也不當成已確認主體。
            row.update({key: case[key] for key in ('aliases', '原始語音名稱') if key in case})
            signals.setdefault(case['category'], []).append(row)
        # 不重跑整日擷取、不讀操作或部位帳本；參考不傳模型。
        probes, probe_errors = run_verdict_probes(day.get('verdict_probes') or [], signals, raw, day['date'])
        errors.extend(probe_errors)
        pl.polish_public_texts(signals, raw, day['date'])
        rows = []
        for case in day['notes']:
            row = next(r for r in signals[case['category']] if r['code'] == case['code'])
            review = row.get('_source_attribution_review') or {}
            input_review = ((row.get('_source_projection') or {}).get('input_review')
                or (row.get('_claim_repair') or {}).get('input_review') or review)
            expected = case.get('expected_review_status') or []
            passed = input_review.get('status') in expected if expected else None
            if passed is False:
                errors.append(f'{day["date"]} {case["code"]}：原說明來源審核草稿期望{expected}，實測{input_review.get("status")}')
            field = 'note' if case['category'] == 'holdings' else 'reason'
            hard = pl.quality_overview({case['category']: [row]}, raw, check_chapters=False)[1]
            final_verified = bool(row.get(field)) and pl._current_source_review(row, raw, field).get('status') == 'supported' and not hard
            if case.get('require_final_verified') and not final_verified:
                errors.append(f'{day["date"]} {case["code"]}：修後說明尚未完整核實：' + '；'.join(hard))
            assert row['code'] == case['code'] and row['name'] == case['name'], '來源核對不得換股'
            rows.append({'code': case['code'], 'name': case['name'], 'input_note': case['note'],
                         'output_note': row.get(field), 'claim_repair': row.get('_claim_repair'),
                         'human_source_number_cautions': pl._source_number_cautions(row, raw),
                         'input_review': input_review, 'review': review, 'projection': row.get('_source_projection'),
                         'claim_bindings': row.get('_source_claim_bindings'),
                         'final_verified': final_verified, 'final_errors': hard,
                         'expected_review_status': expected, 'reference_match': passed})
        reports.append({'date': day['date'], 'source_sha256': sha, 'notes': rows,
                        'original_notice_reads': notice_count,
                        'targeted_verdict_probes': probes,
                        'source_fact_ledger': signals.get('_source_fact_ledger')})
    if not reports:
        raise ValueError('指定日期沒有固定案例，不以空測試回報成功')
    result = {'status': 'completed', 'date': '固定說明跨日來源審閱', 'read_only': True,
              'classification_evaluation': 'not_evaluated', 'classification_injection': False,
              'build': pl.pipeline_build(), 'blocked_writes': len(book.blocked),
              'case_manifest_sha256': hashlib.sha256(Path(args.cases).read_bytes()).hexdigest(),
              'reference_kind': '來源審核草稿，非人類核定金標；expected只在推論後比對',
              'numeric_review_mode': '使用來源指紋綁定的人類待核數字標記；不得稱為未輔助模型自行辨認數字錯誤',
              'days': reports, 'errors': errors, 'warnings': [], 'rows': {},
              'usage': pl.gemini_usage_report(), 'exit_code': 4 if errors else 0}
    result.update(model_calls=pl._GEMINI_CALLS['n'], model_http_calls=pl._GEMINI_CALLS.get('http', 0))
    destination = Path(args.report_json)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('usage', 'errors', 'exit_code')}, ensure_ascii=False))
    raise SystemExit(result['exit_code'])


if __name__ == '__main__':
    main()
