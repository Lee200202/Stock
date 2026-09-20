"""Embedded helpers for context-based inclusion and bounded JSON assessment.

The standalone pipeline is the deployed runtime; this file documents the helpers.
"""

CONFIRMED_NAMES = {'普威': ('4966', '譜瑞-KY'), '戲制台': ('', '矽製材'), '矽製材': ('', '矽製材')}
NON_EQUITY_NAMES = {'日幣', '日圓', '日元', '美元', '美金', '台幣', '臺幣', '新台幣', '人民幣', '歐元'}
ASSESSMENT_VERSION = 'context-json-v3'


def compact_assessment(signals):
    """Do not resend materialized quotes or internal metadata with source IDs."""
    return {cat: [{k: v for k, v in r.items()
                   if not k.startswith('_') and (k != 'evidence' or not r.get('evidence_refs'))}
                  for r in signals.get(cat, []) if isinstance(r, dict)]
            for cat in SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')}


def recover_context(signals, transcript):
    """Repair citation location locally; retain the model's category with review notes."""
    segments = source_segments(transcript)
    hay = _ev_norm(transcript)
    categories = SIGNAL_CATEGORIES + ('history', 'uncertain')
    for cat in categories:
        for row in signals.get(cat, []) or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get('name') or '')
            names = [name] + [a for a in row.get('aliases', []) if isinstance(a, str)]
            for heard, (_, corrected) in CONFIRMED_NAMES.items():
                if name in (heard, corrected):
                    names.extend([heard, corrected])
            row['aliases'] = list(dict.fromkeys(n for n in names if n != name and _ev_norm(n) in hay))
            names = [n for n in names if len(_ev_norm(n)) >= 2]
            good = [q for q in row.get('evidence', []) if isinstance(q, str) and _quote_is_real(q, hay)]
            ev = _ev_norm('\n'.join(good))
            if not any(_ev_norm(n) in ev for n in names):
                matches = [sid for sid, seg in segments.items()
                           if any(_ev_norm(n) in _ev_norm(seg['text']) for n in names)]
                if matches:
                    refs = list(dict.fromkeys(list(row.get('evidence_refs') or []) + matches))
                    row['evidence_refs'] = [sid for sid in refs if sid in segments]
                    good = list(dict.fromkeys(good + [segments[sid]['text'] for sid in matches]))
                    row['_context_included'] = True
                    row['_review_note'] = '依完整原文補回名稱上下文，保留原判讀分類'
            row['evidence'] = good
            # A missing separate time quote is a formatting gap, not proof of history.
            if cat in ('buy', 'sell', 'uncertain') and not _ev_norm(row.get('time_evidence')):
                when = row.get('when')
                pattern = _WHEN_MARKERS.get(when)
                matching = [q for q in good if pattern and re.search(pattern, q)]
                if matching:
                    row['time_evidence'] = matching[0]
                elif good and when in ('today', 'yesterday', 'prev_trading_day'):
                    row['_time_from_context'] = True
                    row['_review_note'] = '日期依上下文判讀，未附獨立時間短句'
    remaining = []
    labels = {'買入': 'buy', '賣出': 'sell', '會員持股': 'holdings', '觀望注意': 'watch_watch', '觀望不碰': 'watch_avoid'}
    for row in signals.get('uncertain', []) or []:
        if not isinstance(row, dict):
            remaining.append(row); continue
        target = row.get('_原分類') or row.get('suggested_category') or row.get('category')
        target = labels.get(target, target)
        names = [row.get('name', '')] + row.get('aliases', [])
        ev = _ev_norm('\n'.join(row.get('evidence') or []))
        if target in SIGNAL_CATEGORIES and any(len(_ev_norm(n)) >= 2 and _ev_norm(n) in ev for n in names):
            row['_context_included'] = True
            row['_review_note'] = row.get('_疑點') or row.get('reason') or '依上下文納入，保留待確認註記'
            signals.setdefault(target, []).append(row)
        else:
            remaining.append(row)
    signals['uncertain'] = remaining
    return signals


def assessment_payload(date_str, segments, candidates=None, issues=None):
    data = {'version': ASSESSMENT_VERSION, 'video_date': date_str,
            'tasks': ['擷取全部標的', '上下文分類與日期', '逐段補漏自查', '大盤摘要'],
            'confirmed_names': CONFIRMED_NAMES, 'non_equity_names': sorted(NON_EQUITY_NAMES),
            'source': {sid: seg['text'] for sid, seg in segments.items()}}
    if candidates is not None:
        data['candidates'] = compact_assessment(candidates)
    if issues:
        data['issues'] = issues
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def assessment_batches(transcript, date_str):
    # UTF-8 bytes are a conservative token upper estimate, not an exact tokenizer.
    # Cap normal requests well below known 1M contexts to respect per-minute quotas.
    context = min(int(os.environ.get('GEMINI_CONTEXT_TOKENS', '1048576')), 1048576)
    cap = min(context, int(os.environ.get('GEMINI_ASSESSMENT_TOKEN_BUDGET', '120000')))
    limit = cap - len(EXTRACT_SYSTEM.encode('utf-8')) - min(MAX_OUT, 20000) - 4096
    if limit < 4096:
        raise ValueError('JSON判讀輸入預算太小；請增加 GEMINI_ASSESSMENT_TOKEN_BUDGET')
    result, batch = [], {}
    for sid, seg in source_segments(transcript).items():
        trial = dict(batch, **{sid: seg})
        if batch and len(assessment_payload(date_str, trial).encode('utf-8')) > limit:
            result.append(batch)
            # Preserve the preceding two segments as boundary context, keeping global IDs.
            batch = dict(list(batch.items())[-2:])
            trial = dict(batch, **{sid: seg})
        if len(assessment_payload(date_str, trial).encode('utf-8')) > limit:
            raise ValueError('單一來源段落超過JSON判讀預算')
        batch = trial
    if batch:
        result.append(batch)
    return result


def extract_context_json(transcript, date_str):
    categories = SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')
    merged = {cat: [] for cat in categories}
    seen = {cat: set() for cat in categories}
    batches = assessment_batches(transcript, date_str)
    print(f'JSON合併判讀：{len(transcript)} 字，分 {len(batches)} 批；擷取／分類／補漏／大盤一次處理')
    for index, batch in enumerate(batches, 1):
        raw = call_gemini(EXTRACT_SYSTEM, assessment_payload(date_str, batch),
                          want_json=True, thinking=2048, tag=f'assess-json-{index}', max_out=min(MAX_OUT, 20000))
        parsed = json.loads(re.sub(r'^```json|^```|```$', '', raw.strip(), flags=re.MULTILINE).strip())
        if not isinstance(parsed, dict) or any(not isinstance(parsed.get(c), list) for c in categories):
            raise ValueError('JSON合併判讀必須包含完整九類陣列；未寫入資料')
        for cat in categories:
            for row in parsed[cat]:
                if not isinstance(row, dict):
                    raise ValueError('JSON判讀列不是物件；未寫入資料')
                identity = json.dumps(row, ensure_ascii=False, sort_keys=True)
                if identity not in seen[cat]:
                    merged[cat].append(row); seen[cat].add(identity)
    merged['_combined_pass'] = True
    merged['_assessment_batches'] = len(batches)
    return merged


def audit_context_json(transcript, signals, date_str):
    materialize_evidence(signals, transcript)
    recover_context(signals, transcript)
    gaps = evidence_gaps(signals, transcript)
    # One bounded repair per batch, with compact candidates and no repeated quotes.
    if gaps:
        repaired = {cat: [] for cat in SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')}
        for batch in assessment_batches(transcript, date_str):
            selected = {cat: [r for r in signals.get(cat, []) if isinstance(r, dict) and
                             (not r.get('evidence_refs') or set(r['evidence_refs']) & set(batch))]
                        for cat in repaired}
            payload = assessment_payload(date_str, batch, selected, gaps)
            # Recheck the FULL request after attaching candidates; never silently truncate.
            cap = min(int(os.environ.get('GEMINI_CONTEXT_TOKENS', '1048576')),
                      int(os.environ.get('GEMINI_ASSESSMENT_TOKEN_BUDGET', '120000')), 1048576)
            if len((AUDIT_SYSTEM + payload).encode('utf-8')) + min(MAX_OUT, 20000) + 4096 > cap:
                print('修復JSON超過預算，沿用已判讀內容並留下稽核註記')
                repaired = None; break
            raw = call_gemini(AUDIT_SYSTEM, payload, want_json=True, thinking=2048,
                              tag='context-repair', max_out=min(MAX_OUT, 20000))
            parsed = json.loads(re.sub(r'^```json|^```|```$', '', raw.strip(), flags=re.MULTILINE).strip())
            if not isinstance(parsed, dict) or any(not isinstance(parsed.get(c), list) or
                   any(not isinstance(r, dict) for r in parsed[c]) for c in repaired):
                raise ValueError('JSON修復格式不完整；未寫入資料')
            for cat in repaired:
                repaired[cat].extend(parsed[cat])
        if repaired is not None:
            materialize_evidence(repaired, transcript)
            recover_context(repaired, transcript)
            gaps = evidence_gaps(repaired, transcript, signals)
            signals = repaired
    signals['_repair_gaps'] = gaps
    validated = validate_evidence(signals, transcript, date_str)
    validated['_quality_requires_review'] = bool(gaps or validated.get('uncertain'))
    print(f'JSON本機校對完成：{len(gaps)} 項待複核')
    return validated
