"""只以凍結回覆做離線消融，不呼叫模型或更動來源、正式資料。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from semantic_context import source_direction_consensus


def normalize(value):
    return ''.join(c for c in str(value) if c.isalnum())


def evaluate(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    aliases = {}
    for item in data.get('candidate_inventory', {}).get('proposed', []):
        aliases.setdefault(str(item.get('code')), set()).add(item.get('heard', ''))
    comparison = data.get('independent_review_comparison') or {}
    differences = {row['code']: row for row in comparison.get('differences', [])}
    changes = []
    for code, original in data.get('verdicts', {}).items():
        entry = copy.deepcopy(original)
        # 舊收據沒有在各筆裁決中保存候選別名。
        # 只還原盤點已記錄的原字，不使用參考分類標籤。
        entry.setdefault('source_names', sorted(aliases.get(code, set()) | {code, str(entry.get('name', ''))}))
        result = source_direction_consensus(entry, normalize)
        if not result:
            continue
        category, basis = result
        judgment = differences.get(code)
        changes.append({'code': code, 'direction': category, 'basis': basis,
                        'reference_effect': ('resolves_existing_difference' if judgment and category in judgment['expected']
                                             else 'new_or_opposing_case_requires_source_review')})
    return {'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'run_artifact': path.parent.name, 'date': data.get('date'),
            'reference_matched_before': comparison.get('matched_candidates'),
            'reference_decided': comparison.get('decided_candidates'),
            'changes': changes, 'models_called': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports', nargs='+', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'kind': 'frozen_answer_counterfactual_not_full_replay',
              'does_not_validate_inventory_publication_or_notes': True,
              'results': [evaluate(path) for path in args.reports]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
