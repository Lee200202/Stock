"""重播結束後才使用的來源審核草稿；不讀網站，不提供模型輸入。"""
import hashlib
import json
from pathlib import Path

CATEGORIES = {'buy', 'sell', 'holdings', 'watch_watch', 'watch_avoid'}
LABELS = {'buy': '買入', 'sell': '賣出', 'holdings': '會員持股',
          'watch_watch': '觀望注意', 'watch_avoid': '觀望不碰'}


def workflow_diagnostic(signals, code, actual):
    """只摘取流程痕跡；它是原因線索，不能代替來源判斷或替缺答決定語意。"""
    entry = (signals.get('_verdicts') or {}).get(code)
    if not isinstance(entry, dict):
        return {'status': 'no_verdict_trace', 'basis': '沒有本股裁決痕跡，需回查候選建立或其他隔離階段'}
    votes = list(entry.get('votes') or [])
    invalid = [{'round': a.get('round'), 'basis': a.get('basis')}
               for a in entry.get('unusable_answers') or [] if isinstance(a, dict)]
    incomplete = bool(invalid or any(not v for v in votes))
    block = entry.get('publish_block') if not actual else None
    status = ('publication_identity_block' if block else 'review_incomplete' if incomplete else 'post_verdict_check' if
              entry.get('cls') in CATEGORIES and entry.get('cls') not in actual else 'verdict_trace')
    return {'status': status, 'initial_class': entry.get('current'), 'final_class': entry.get('cls'),
            'tier': entry.get('tier'), 'votes': votes, 'basis': str(entry.get('basis') or '')[:600]
                + ('｜公開前身分核對阻擋：' + json.dumps(block, ensure_ascii=False) if block else ''),
            'publication_block': block,
            'invalid_answers': invalid,
            'incomplete_is_not_evidence_of_no_stance': incomplete}


def load_reference(path, date, raw):
    """只接受綁定同日同原稿的代理草稿；未知身分／待核項不當成不收錄答案。"""
    payload = Path(path).read_bytes()
    data = json.loads(payload.decode('utf-8-sig'))
    if data.get('mode') != 'independent_review':
        raise ValueError('來源審核必須是 independent_review')
    if data.get('review_status', data.get('status')) not in {
            'agent_source_review_draft', 'agent_draft_not_human_gold'}:
        raise ValueError('來源審核未標明代理草稿身分')
    if data.get('target_date', data.get('date')) != date:
        raise ValueError('來源審核日期與重播日期不同')
    digest = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    if data.get('raw_sha256', data.get('source_sha256')) != digest:
        raise ValueError('來源審核原稿指紋不同；禁止拿另一版原稿的判斷評分')
    decided, unresolved = {}, []
    candidates = data.get('candidates', data.get('rows'))
    if not isinstance(candidates, list) or not candidates:
        raise ValueError('來源審核沒有候選聯集')
    seen = set()
    for row in candidates:
        code = str(row.get('code') or '')
        if code and code in seen:
            raise ValueError('來源審核代號重複：' + code)
        seen.add(code)
        if 'selected_categories' in row:
            if row.get('status') != 'decided' or not code:
                unresolved.append({'code': code, 'name': row.get('name')})
                continue
            expected = row['selected_categories']
        else:
            expectation = row.get('expected')
            if expectation == 'unresolved' or not code:
                unresolved.append({'code': code, 'name': row.get('name')})
                continue
            expected = ([] if expectation in {'ignored', 'not_a_stock', 'history'} else
                        ['holdings', 'watch_watch'] if expectation == 'holdings_or_watch_watch' else
                        [expectation])
        if not isinstance(expected, list) or any(x not in CATEGORIES for x in expected):
            raise ValueError('來源審核分類格式錯誤：' + code)
        decided[code] = {'name': row.get('name'), 'expected': expected,
                         'quote': row.get('quote', ''),
                         'basis': row.get('rationale', row.get('basis', ''))}
    return {'kind': 'agent_source_draft_not_human_gold', 'date': date,
            'source_sha256': digest, 'review_sha256': hashlib.sha256(payload).hexdigest(),
            'target_result_exposure': data.get('target_result_exposure', 'not_recorded'),
            'as_of_positions': data.get('as_of_positions', data.get('notification_context', 'not_recorded')),
            'decided': decided, 'unresolved': unresolved}


def compare_reference(signals, reference, date):
    """對照凍結判斷與公開輸出，缺列保留原判斷理由；本函式不修改 signals。"""
    published = signals.get('_article_records')
    rows = published if isinstance(published, dict) else signals
    actual = {}
    for cat in sorted(CATEGORIES):
        for row in rows.get(cat, []) or []:
            if row.get('_date') and row['_date'] != date:
                continue
            code = str(row.get('code') or '')
            if code:
                actual.setdefault(code, set()).add(cat)
    differences, matched = [], 0
    for code, judgment in reference['decided'].items():
        expected, got = set(judgment['expected']), actual.get(code, set())
        # 持股＋注意可被明列為兩個可接受選項；不能掩蓋同時多出的買賣或反向分類。
        agrees = bool(expected & got) and got <= expected if expected else not got
        if agrees:
            matched += 1
            continue
        kind = ('missing' if expected and not got else 'unexpected_collection' if not expected else
                'direction_or_category_difference')
        differences.append({'code': code, **judgment, 'actual': sorted(got), 'difference': kind,
                            'workflow_diagnostic': workflow_diagnostic(signals, code, got),
                            'holding_watch_label_only': bool(expected and got and
                                expected | got <= {'holdings', 'watch_watch'})})
    unknown_codes = {r['code'] for r in reference['unresolved'] if r['code']}
    unreviewed = sorted(set(actual) - set(reference['decided']) - unknown_codes)
    result = {k: v for k, v in reference.items() if k != 'decided'}
    result.update(status='differences_for_review' if differences or unreviewed else 'aligned_with_draft',
                  comparison_only_after_inference=True, model_input=False,
                  matched_candidates=matched, decided_candidates=len(reference['decided']),
                  differences=differences, unreviewed_output_codes=unreviewed,
                  ownership_and_note_support_not_validated_by_label_match=True)
    lines = [f"獨立來源審核草稿 {matched}/{len(reference['decided'])} 項分類相符；"
             f"{len(differences)} 項差異待核；{len(reference['unresolved'])} 項待核不評分。",
             '這是代理依原稿保存的判斷，非核定人工答案；標籤相符不代表說明、部位及數字皆正確。']
    label = lambda values: '／'.join(LABELS[v] for v in sorted(values)) or '不收錄'
    for row in differences:
        lines.append(f"  ≠ {row['name']}（{row['code']}）來源審核草稿：{label(row['expected'])}；"
                     f"流程輸出：{label(row['actual'])}；差異：{row['difference']}")
        lines.append(f"    判斷依據：{row['basis']}；引句：{row['quote']}")
        trace = row['workflow_diagnostic']
        lines.append(f"    流程痕跡：{trace['status']}；{trace['basis']}")
        if trace.get('incomplete_is_not_evidence_of_no_stance'):
            lines.append('    有無效／缺答，表示審核未完成，不能推定本股沒有立場。')
    if unreviewed:
        lines.append('  未有獨立判斷的流程輸出，待核：' + '、'.join(unreviewed))
    return result, '\n'.join(lines)
