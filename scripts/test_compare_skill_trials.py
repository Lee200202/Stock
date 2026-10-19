"""Synthetic tests of local aggregation, not measurements of real model quality."""
import copy
import unittest

from compare_skill_trials import compare


def fixture():
    h = lambda n: format(n, '064x')
    manifest = {'schema_version': 1, 'experiment_id': 'synthetic-only', 'rubric_hash': h(8),
                'assistance': 'unassisted', 'repeats': 2,
                'variants': {'A': {'artifact_hash': h(1)}, 'B': {'artifact_hash': h(2)}},
                'cases': [{'id': str(i), 'unit_id': 'day-' + str(i // 2), 'split': 'regression',
                           'input_hash': h(10 + i), 'context_hash': h(20 + i)} for i in range(3)]}
    rows = []
    for c in manifest['cases']:
        for v in ('A', 'B'):
            for t in (1, 2):
                rows.append({'case_id': c['id'], 'variant': v, 'trial': t, 'input_hash': c['input_hash'],
                             'context_hash': c['context_hash'], 'artifact_hash': manifest['variants'][v]['artifact_hash'],
                             'rubric_hash': h(8), 'trace_hash': h(30 + t), 'decision_hash': h(40),
                             'task_pass': v == 'B' or c['id'] == '2', 'hard_errors': [], 'latency_ms': 100 * t,
                             'requests': 2, 'retries': 1, 'input_tokens': 100, 'output_tokens': 20})
    return manifest, rows


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.m, self.rows = fixture()

    def test_paired_counts_and_cluster_weight(self):
        r = compare(self.m, self.rows)
        self.assertEqual(r['paired']['B_only_pass'], 4)
        self.assertAlmostEqual(r['summary']['A']['clean_pass_rate'], 1 / 3)
        self.assertEqual(r['summary']['A']['unit_macro_clean_pass_rate'], .5)
        self.assertEqual(r['summary']['B']['input_tokens'], 600)
        self.assertEqual(r['statistical_significance'], 'not_computed')

    def test_hard_error_cannot_be_cancelled_by_success_or_speed(self):
        self.rows[2].update(task_pass=True, hard_errors=['fabricated_trade'], latency_ms=0)
        r = compare(self.m, self.rows)
        self.assertIn('candidate_hard_errors', r['review_flags'])
        self.assertEqual(r['paired']['both_fail'], 1)
        self.assertEqual(r['promotion'], 'requires_human_review')

    def test_missing_and_duplicate_trials_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Missing trials'):
            compare(self.m, self.rows[:-1])
        with self.assertRaisesRegex(ValueError, 'Duplicate trial'):
            compare(self.m, self.rows + [self.rows[0]])

    def test_drift_rejected(self):
        for field, message in [('context_hash', 'Source/context'), ('input_hash', 'Source/context'),
                               ('artifact_hash', 'Variant artifact'), ('rubric_hash', 'Rubric')]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = '0' * 64
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                compare(self.m, rows)

    def test_unknown_variant_and_invalid_numbers(self):
        for field, value in [('variant', 'C'), ('latency_ms', float('nan')), ('requests', True), ('trial', 0),
                             ('input_tokens', -1), ('task_pass', 1), ('hard_errors', [''])]:
            rows = copy.deepcopy(self.rows)
            rows[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                compare(self.m, rows)

    def test_unmeasured_tokens_and_stability_are_not_zero(self):
        self.rows[0]['input_tokens'] = None
        r = compare(self.m, self.rows)
        self.assertIsNone(r['summary']['A']['input_tokens'])
        self.assertEqual(r['summary']['A']['input_tokens_missing_trials'], 1)
        self.m['repeats'] = 1
        r = compare(self.m, [x for x in self.rows if x['trial'] == 1])
        self.assertIsNone(r['summary']['A']['unstable_cases'])

    def test_semantic_instability_assistance_and_split_leakage(self):
        self.rows[1]['decision_hash'] = 'a' * 64
        self.m['assistance'] = 'assisted'
        r = compare(self.m, self.rows)
        self.assertEqual(r['summary']['A']['unstable_cases'], 1)
        self.assertIn('assisted_or_component_not_independent_end_to_end', r['review_flags'])
        self.m['cases'][0]['split'] = 'holdout'
        with self.assertRaisesRegex(ValueError, 'multiple dataset splits'):
            compare(self.m, self.rows)

    def test_holdout_is_reported_separately(self):
        self.m['cases'][2]['split'] = 'holdout'
        r = compare(self.m, self.rows)
        self.assertEqual(r['by_split']['holdout']['summary']['A']['cases'], 1)
        self.assertNotIn('no_holdout', r['review_flags'])


if __name__ == '__main__':
    unittest.main()
