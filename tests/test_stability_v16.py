"""同原文兩輪漏項、M31 誤刪與不完整 JSON 的離線重現。"""
import json
import unittest
from unittest.mock import patch
from test_quality import p, empty


class StabilityTests(unittest.TestCase):
    def test_official_latin_survives_gate_but_industry_does_not(self):
        with patch.object(p, '_CODE_MAP', {'6643': 'M31', '6781': 'AES-KY'}):
            self.assertFalse(p.is_non_stock('M31')[0])
            self.assertFalse(p.is_non_stock('AES-KY')[0])
            self.assertTrue(p.is_non_stock('HBM')[0])
            s=empty();s['watch_watch']=[{'name':'M31','code':'6643','reason':'可留意',
                                        'evidence':['M31可以留意。']}]
            p.validate_evidence(s,'M31可以留意。','2026/09/14',after_codes=True)
            self.assertEqual(len(s['watch_watch']),1)

    def test_census_finds_omission_without_inventing_category(self):
        with patch.object(p, '_CODE_MAP', {'6643':'M31','3443':'創意','2330':'台積電'}):
            raw='台積電我還有。M31、創意等買點。'
            sig=empty();sig['holdings']=[{'name':'台積電','code':'2330'}]
            gaps=p.inventory_gaps(sig,raw)
            # 兩字官方簡稱（創意）常與日常用語同形（世界、全國、大量），只當提示、不產生漏項缺口（v17）
            self.assertEqual(len(gaps),1)
            self.assertIn('M31',' '.join(gaps))
            self.assertTrue(next(i for i in p.source_inventory(p.source_segments(raw)) if i['name']=='創意').get('weak'))
            self.assertEqual(sig['watch_watch'],[])
            sig['ignored']=[{'name':'M31'},{'name':'創意'}]
            self.assertEqual(p.inventory_gaps(sig,raw),[])
            data=json.loads(p.assessment_payload('2026/09/14',p.source_segments(raw)))
            self.assertEqual(len(data['source_inventory']),3)

    def test_no_substring_latin_or_price_candidate(self):
        with patch.object(p, '_CODE_MAP', {'6643':'M31','2330':'台積電'}):
            self.assertEqual(p.source_inventory(p.source_segments('M310與2330元。')),[])

    def test_schema_scoped_to_full_assessment_and_rotation_models(self):
        for model in ('gemini-3.5-flash-lite','gemini-2.5-flash'):
            for tag in ('assess-json-1','assess-json-1-r1','context-review-retry'):
                cfg=p.gemini_generation_config(model,want_json=True,tag=tag)
                self.assertEqual(set(cfg['responseJsonSchema']['required']),set(empty()))
            self.assertNotIn('responseJsonSchema',p.gemini_generation_config(model,want_json=True,tag='unclear'))
        self.assertLess(len(json.dumps(p.assessment_response_schema()).encode()),2000)

    def test_partial_review_rejected_instead_of_silent_empty_categories(self):
        with self.assertRaises(ValueError):p._parse_review_json('{"holdings":[]}')
        self.assertEqual(p._parse_review_json(json.dumps(empty())),empty())

    def test_extract_does_not_retry_transport_as_format_error(self):
        with patch.object(p,'call_gemini',side_effect=TimeoutError('offline')) as ai:
            with self.assertRaises(TimeoutError):p.extract_context_json('原文','2026/09/14')
            self.assertEqual(ai.call_count,1)

    def test_long_source_remains_one_batch_and_no_chars_are_removed(self):
        raw='股'*24396
        batches=p.assessment_batches(raw,'2026/09/14')
        self.assertEqual(len(batches),1)
        self.assertEqual(''.join(s['text'] for s in batches[0].values()),raw)
