"""Summarize locally graded, paired A/B trials; no network, models or deployment.

Usage: python scripts/compare_skill_trials.py --manifest PLAN.json --trials RUNS.jsonl --output REPORT.json
Manifest v1: experiment_id, rubric_hash (SHA256), assistance (unassisted/assisted/component),
repeats (positive integer), variants {A: {artifact_hash}, B: {artifact_hash}}, and cases
[{id, unit_id, input_hash, context_hash, split: development/regression/holdout}].
Each JSONL record: case_id, variant, trial (1..repeats), input_hash, context_hash,
artifact_hash, rubric_hash, trace_hash, decision_hash (all hashes are SHA256), task_pass
(bool), hard_errors (string list), latency_ms (finite >=0, includes failures/retries),
requests, retries (nonnegative integers), input_tokens, output_tokens, thinking_tokens
(integer or null; provider buckets may overlap, never add blindly).
Hashes/provenance and grading are supplied by the evaluation harness, not authenticated
by this aggregator. decision_hash hashes normalized semantic decisions, not prose.
All planned trials, including failures/timeouts, must be present. Output is descriptive;
no p-values, claims of independent samples, prices or automatic promotion.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from statistics import mean


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def percentile(values, p):
    values = sorted(values)
    point = (len(values) - 1) * p
    lo, hi = math.floor(point), math.ceil(point)
    return values[lo] + (values[hi] - values[lo]) * (point - lo)


def compare(manifest, records):
    require(manifest.get('schema_version') == 1, 'Expected manifest schema_version=1')
    require(bool(manifest.get('experiment_id')), 'Missing experiment_id')
    require(sha(manifest.get('rubric_hash')), 'Missing frozen rubric_hash')
    require(manifest.get('assistance') in ('unassisted', 'assisted', 'component'), 'Invalid assistance')
    repeats = manifest.get('repeats')
    require(integer(repeats, 1), 'Invalid repeats')
    variants = manifest.get('variants', {})
    require(set(variants) == {'A', 'B'}, 'Exactly A and B are required')
    require(all(sha(v.get('artifact_hash')) for v in variants.values()), 'Invalid artifact_hash')
    cases = {}
    for c in manifest.get('cases', []):
        require(isinstance(c.get('id'), str) and bool(c['id']) and c['id'] not in cases, 'Duplicate/missing case id')
        require(isinstance(c.get('unit_id'), str) and bool(c['unit_id']), 'Missing unit_id')
        require(c.get('split') in ('development', 'regression', 'holdout'), 'Invalid split')
        require(sha(c.get('input_hash')) and sha(c.get('context_hash')), 'Invalid source/context hash')
        cases[c['id']] = c
    require(bool(cases), 'No cases')
    unit_splits = defaultdict(set)
    for c in cases.values():
        unit_splits[c['unit_id']].add(c['split'])
    require(all(len(s) == 1 for s in unit_splits.values()), 'Same unit occurs in multiple dataset splits')
    indexed = {}
    for r in records:
        c = cases.get(r.get('case_id'))
        v = r.get('variant')
        require(c is not None and v in variants, 'Unknown case/variant')
        trial = r.get('trial')
        require(integer(trial, 1) and trial <= repeats, 'Invalid trial')
        key = (r['case_id'], v, trial)
        require(key not in indexed, 'Duplicate trial')
        require(r.get('input_hash') == c['input_hash'] and r.get('context_hash') == c['context_hash'], 'Source/context drift')
        require(r.get('artifact_hash') == variants[v]['artifact_hash'], 'Variant artifact drift')
        require(r.get('rubric_hash') == manifest['rubric_hash'], 'Rubric drift')
        require(sha(r.get('trace_hash')) and sha(r.get('decision_hash')), 'Missing trace/decision hash')
        require(type(r.get('task_pass')) is bool, 'task_pass must be boolean')
        require(isinstance(r.get('hard_errors'), list) and all(isinstance(e, str) and e.strip() for e in r['hard_errors']), 'Invalid hard_errors')
        latency = r.get('latency_ms')
        require(type(latency) in (int, float) and math.isfinite(latency) and latency >= 0, 'Invalid latency_ms')
        for field in ('requests', 'retries'):
            require(integer(r.get(field)), 'Invalid ' + field)
        require(r['retries'] <= r['requests'], 'Retries exceed requests')
        for field in ('input_tokens', 'output_tokens', 'thinking_tokens'):
            require(r.get(field) is None or integer(r[field]), 'Invalid ' + field)
        indexed[key] = r
    require(len(indexed) == len(cases) * 2 * repeats, 'Missing trials; failures/timeouts must remain in denominator')

    def clean(r):
        return r['task_pass'] and not r['hard_errors']

    def summarize(variant, chosen):
        rows = [indexed[(cid, variant, t)] for cid in chosen for t in range(1, repeats + 1)]
        units = defaultdict(list)
        for cid in chosen:
            units[cases[cid]['unit_id']].extend(clean(indexed[(cid, variant, t)]) for t in range(1, repeats + 1))
        unstable = sum(len({indexed[(cid, variant, t)]['decision_hash'] for t in range(1, repeats + 1)}) > 1 for cid in chosen)
        result = {
            'trials': len(rows), 'cases': len(chosen), 'units': len(units),
            'task_pass_rate': mean(r['task_pass'] for r in rows),
            'clean_pass_rate': mean(clean(r) for r in rows),
            'unit_macro_clean_pass_rate': mean(mean(values) for values in units.values()),
            'hard_error_trials': sum(bool(r['hard_errors']) for r in rows),
            'unstable_cases': unstable if repeats > 1 else None,
            'latency_ms_p50': percentile([r['latency_ms'] for r in rows], .5),
            'latency_ms_p95': percentile([r['latency_ms'] for r in rows], .95),
            'requests': sum(r['requests'] for r in rows), 'retries': sum(r['retries'] for r in rows),
        }
        for field in ('input_tokens', 'output_tokens', 'thinking_tokens'):
            values = [r.get(field) for r in rows]
            result[field] = sum(values) if all(v is not None for v in values) else None
            result[field + '_missing_trials'] = sum(v is None for v in values)
        return result

    def paired(chosen):
        counts = {'both_pass': 0, 'A_only_pass': 0, 'B_only_pass': 0, 'both_fail': 0}
        per_unit = defaultdict(list)
        for cid in chosen:
            for t in range(1, repeats + 1):
                a, b = (clean(indexed[(cid, v, t)]) for v in ('A', 'B'))
                counts['both_pass' if a and b else 'A_only_pass' if a else 'B_only_pass' if b else 'both_fail'] += 1
                per_unit[cases[cid]['unit_id']].append(int(b) - int(a))
        return {**counts, 'unit_mean_delta': {u: mean(d) for u, d in sorted(per_unit.items())}}

    summary = {v: summarize(v, cases) for v in variants}
    splits = {s: [cid for cid, c in cases.items() if c['split'] == s] for s in ('development', 'regression', 'holdout')}
    return {
        'experiment_id': manifest['experiment_id'], 'assistance': manifest['assistance'],
        'summary': summary, 'paired': paired(cases),
        'by_split': {s: {'summary': {v: summarize(v, ids) for v in variants}, 'paired': paired(ids)} for s, ids in splits.items() if ids},
        'review_flags': [flag for flag, active in (
            ('assisted_or_component_not_independent_end_to_end', manifest['assistance'] != 'unassisted'),
            ('candidate_hard_errors', summary['B']['hard_error_trials'] > 0),
            ('paired_regressions', paired(cases)['A_only_pass'] > 0),
            ('no_holdout', not splits['holdout']),
            ('stability_not_measured', repeats == 1),
        ) if active],
        'promotion': 'requires_human_review', 'statistical_significance': 'not_computed',
        'limitations': 'Externally graded records and supplied hashes; no source/trace authenticity check, judge calibration, causal or independence claim.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--trials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.output.resolve() not in (args.manifest.resolve(), args.trials.resolve()), 'Output cannot overwrite input')
    report = compare(json.loads(args.manifest.read_text(encoding='utf-8-sig')),
                     [json.loads(line) for line in args.trials.read_text(encoding='utf-8-sig').splitlines() if line.strip()])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'experiment_id': report['experiment_id'], 'review_flags': report['review_flags'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
