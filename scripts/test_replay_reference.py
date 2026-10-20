"""來源綁定、待核不評分、方向與副作用隔離；完全離線。"""
import copy
import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from replay_reference import load_reference, compare_reference
from summarize_replay import summarize

DAY, RAW = '2026/10/20', '甲公司現在不參與，乙公司年線可以佈局。'


class ReferenceChecks(unittest.TestCase):
    def source(self, **updates):
        data = {'mode': 'independent_review', 'date': DAY,
                'source_sha256': hashlib.sha256(RAW.encode()).hexdigest(),
                'status': 'agent_draft_not_human_gold', 'rows': [
                    {'code': '1111', 'name': '甲公司', 'expected': 'watch_avoid',
                     'quote': '甲公司現在不參與', 'basis': '現在不參與是本股的當下立場'},
                    {'code': '2222', 'name': '乙公司', 'expected': 'unresolved'}]}
        data.update(updates)
        return data

    def load(self, data, raw=RAW, day=DAY):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'reference.json'
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            return load_reference(path, day, raw)

    def test_source_binding_rejects_a_different_date_or_raw_version(self):
        for day, raw in (('2026/10/19', RAW), (DAY, RAW + '改稿')):
            with self.assertRaises(ValueError):
                self.load(self.source(), raw=raw, day=day)
        self.assertEqual(self.load(self.source())['kind'], 'agent_source_draft_not_human_gold')

    def test_missing_avoid_prints_independent_basis_not_website_result(self):
        ref = self.load(self.source())
        report, text = compare_reference({}, ref, DAY)
        self.assertEqual(report['differences'][0]['difference'], 'missing')
        self.assertIn('現在不參與是本股', text)
        self.assertIn('引句：甲公司現在不參與', text)
        self.assertNotIn('網站：', text)
        self.assertFalse(report['model_input'])

    def test_unresolved_is_not_a_correct_exclusion_and_unreviewed_output_is_flagged(self):
        ref = self.load(self.source())
        signals = {'watch_avoid': [{'code': '1111'}, {'code': '2222'}, {'code': '3333'}]}
        original = copy.deepcopy(signals)
        report, _ = compare_reference(signals, ref, DAY)
        self.assertEqual(report['matched_candidates'], 1)
        self.assertEqual(report['decided_candidates'], 1)
        self.assertEqual(report['differences'], [])
        self.assertEqual(report['unreviewed_output_codes'], ['3333'])
        self.assertEqual(report['status'], 'differences_for_review')
        self.assertEqual(signals, original)

    def test_opposite_class_is_not_hidden_by_a_matching_class(self):
        ref = self.load(self.source())
        report, _ = compare_reference({'watch_avoid': [{'code': '1111'}],
                                       'watch_watch': [{'code': '1111'}]}, ref, DAY)
        self.assertEqual(len(report['differences']), 1)

    def test_only_explicit_accepted_alternatives_are_matched(self):
        data = self.source(rows=[{'code': '1111', 'name': '甲公司', 'expected': 'holdings_or_watch_watch'}])
        report, _ = compare_reference({'watch_watch': [{'code': '1111'}]}, self.load(data), DAY)
        self.assertEqual(report['matched_candidates'], 1)
        data['rows'][0]['expected'] = 'holdings'
        report, _ = compare_reference({'watch_watch': [{'code': '1111'}]}, self.load(data), DAY)
        self.assertEqual(report['matched_candidates'], 0)
        self.assertTrue(report['differences'][0]['holding_watch_label_only'])
        self.assertTrue(report['ownership_and_note_support_not_validated_by_label_match'])

    def test_both_frozen_formats_and_explicit_exclusion(self):
        data = {'mode': 'independent_review', 'target_date': DAY,
                'raw_sha256': hashlib.sha256(RAW.encode()).hexdigest(),
                'review_status': 'agent_source_review_draft', 'candidates': [
                    {'code': '1111', 'status': 'decided', 'name': '甲公司',
                     'selected_categories': [], 'rationale': '只有提問，沒有本股看法'}]}
        report, _ = compare_reference({'watch_avoid': [{'code': '1111'}]}, self.load(data), DAY)
        self.assertEqual(report['differences'][0]['difference'], 'unexpected_collection')

    def test_invalid_references_do_not_silently_score(self):
        for changes in ({'mode': 'website'}, {'status': 'gold'},
                        {'rows': [{'code': '1111', 'expected': 'not-a-category'}]},
                        {'rows': [{'code': '1111', 'expected': 'ignored'},
                                  {'code': '1111', 'expected': 'holdings'}]}):
            with self.assertRaises(ValueError):
                self.load(self.source(**changes))

    def test_actions_summary_does_not_claim_accuracy_or_use_website(self):
        self.assertIn('分類品質未評估', summarize({}))
        self.assertIn('未取得重播結果', summarize(None))
        review, _ = compare_reference({}, self.load(self.source()), DAY)
        report = summarize({'date': DAY, 'independent_review_comparison': review})
        self.assertIn('現在不參與是本股的當下立場', report)
        self.assertIn('代理草稿不是核定人工金標', report)
        self.assertNotIn('網站：', report)

    def test_entry_point_never_reads_website_comparison_or_injects_reference(self):
        with contextlib.redirect_stdout(io.StringIO()), patch.dict(os.environ,
                {'SPREADSHEET_ID': 'offline-test', 'GEMINI_API_KEY': 'offline-test'}):
            import replay_extract as replay
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.json'
            result = Path(temp) / 'result.json'
            source.write_text(json.dumps(self.source(), ensure_ascii=False), encoding='utf-8')
            output = io.StringIO()
            def extracted(*args, **kwargs):
                # 引句、期待分類與參考檔皆不應進入模型入口。
                self.assertEqual(args[3], RAW)
                self.assertEqual(kwargs, {'replace_video': False, 'v1': RAW})
                raise replay.Reached({'watch_watch': [{'code': '1111', 'name': '甲公司', 'reason': '流程結果'}]})
            with patch('sys.argv', ['replay', '--date', DAY, '--independent-review', str(source),
                                    '--report-json', str(result)]), \
                 patch.object(replay.pl, 'open_sheets', return_value=object()), \
                 patch.object(replay.pl, 'existing_transcript', return_value=(RAW, '')), \
                 patch.object(replay.pl, 'name_memo_load'), \
                 patch.object(replay.pl, '_stage_extract_impl', side_effect=extracted) as model_stage, \
                 patch.object(replay.pl, 'quality_overview', return_value=([], [], [])), \
                 patch.object(replay.pl, 'public_narrative', side_effect=lambda text, *args: text), \
                 patch.object(replay.pl, 'gemini_usage_report', return_value='0 model requests in this test'), \
                 patch.object(replay, 'invariants', return_value=([], [])), \
                 patch.object(replay, 'site_rows', side_effect=AssertionError('網站不應被讀作答案')), \
                 patch.object(replay, 'site_compare', side_effect=AssertionError('網站不應被比作答案')), \
                 contextlib.redirect_stdout(output):
                with self.assertRaises(SystemExit) as stop:
                    replay.main()
            self.assertEqual(stop.exception.code, 4)
            self.assertEqual(model_stage.call_count, 1)
            saved = json.loads(result.read_text(encoding='utf-8'))
            self.assertFalse(saved['website_as_reference'])
            self.assertFalse(saved['classification_injection'])
            self.assertEqual(saved['classification_evaluation'], 'independent_draft')
            self.assertNotIn('網站：', output.getvalue())
            # 指紋錯誤須在模型之前停止。
            bad = self.source(source_sha256='0' * 64)
            source.write_text(json.dumps(bad), encoding='utf-8')
            with patch('sys.argv', ['replay', '--date', DAY, '--independent-review', str(source)]), \
                 patch.object(replay.pl, 'open_sheets', return_value=object()), \
                 patch.object(replay.pl, 'existing_transcript', return_value=(RAW, '')), \
                 patch.object(replay.pl, '_stage_extract_impl') as model_stage:
                with self.assertRaises(ValueError):
                    replay.main()
                model_stage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
