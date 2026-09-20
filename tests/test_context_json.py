import copy
import json
import os
import unittest
from unittest.mock import patch, Mock
from test_quality import p, empty, trade


class ContextJSONTests(unittest.TestCase):
    def test_currency_overrides_even_wrong_valid_stock_code(self):
        with patch.object(p, 'get_code_map') as official:
            self.assertEqual(p.resolve_code('日幣', '1526')[0], p.REJECT)
        official.assert_not_called()

    def test_confirmed_names_do_not_use_phonetic_guess(self):
        with patch.object(p, 'get_code_map', return_value={'1526': '日馳', '4966': '譜瑞-KY'}):
            self.assertEqual(p.resolve_code('普威', '')[:2], ('4966', '譜瑞-KY'))
            # 管理者確認（2026/09/11）：戲制台是產業（矽智財），整列剔除，不再掛待確認。
            self.assertEqual(p.resolve_code('戲制台', '')[0], p.REJECT)

    def test_confirmed_names_skip_second_ai(self):
        sig = empty(); sig['holdings'] = [{'name': '矽製材', '原始語音名稱': '戲制台'}]
        with patch.object(p, 'get_code_map', return_value={'1526': '日馳'}), patch.object(p, 'call_gemini') as ai:
            p.resolve_unclear_names(sig, '戲制台我還有')
        ai.assert_not_called()
        self.assertEqual(sig['holdings'], [])     # 管理者確認為產業，整列剔除

    def test_quarantined_stock_with_category_is_published(self):
        sig = empty()
        sig['uncertain'] = [{'name': '普威', 'suggested_category': 'holdings', 'reason': '引用缺名稱',
                             'evidence': ['這個部位我們都還有。']}]
        source = '普威與祥碩我們手上還有。這個部位我們都還有。'
        p.validate_evidence(sig, source, '2026/09/10')
        self.assertFalse(sig['uncertain'])
        self.assertEqual(sig['holdings'][0]['name'], '普威')
        self.assertIn('待確認註記', sig['holdings'][0]['note'])

    def test_missing_name_quote_is_recovered_for_three_reported_stocks(self):
        for name in ('祥碩', '普威', '國巨'):
            sig = empty(); sig['holdings'] = [{'name': name, 'evidence': ['這個部位我們都還有。']}]
            p.validate_evidence(sig, name + '我們手上還有。這個部位我們都還有。', '2026/09/10')
            self.assertEqual(len(sig['holdings']), 1)

    def test_time_quote_missing_recovered_from_evidence(self):
        sig = empty(); row = trade(); row.pop('time_evidence'); sig['sell'] = [row]
        p.validate_evidence(sig, row['evidence'][0], '2026/09/10')
        p.apply_when_and_seq(None, sig, '2026/09/10')
        self.assertEqual(sig['sell'][0]['_date'], '2026/09/09')

    def test_context_date_without_separate_time_sentence_is_retained(self):
        sig = empty(); sig['buy'] = [{'name': '四星KY', 'when': 'today', 'evidence': ['四星KY我們買進了。']}]
        p.validate_evidence(sig, '四星KY我們買進了。', '2026/09/10')
        self.assertEqual(len(sig['buy']), 1)
        self.assertIn('日期依上下文', sig['buy'][0]['reason'])

    def test_historical_context_never_becomes_today(self):
        sig = empty(); sig['sell'] = [{'name': '華城', 'when': 'today', 'evidence': ['華城以前我們已經賣了。']}]
        p.validate_evidence(sig, '華城以前我們已經賣了。', '2026/09/10')
        self.assertFalse(sig['sell']); self.assertEqual(len(sig['history']), 1)

    def test_one_model_call_for_extract_and_audit(self):
        source = trade()['evidence'][0]
        sig = empty(); sig['sell'] = [dict(trade(), evidence_refs=['S0001'])]
        with patch.dict(p.os.environ, {'GEMINI_SEMANTIC_AUDIT':'false'}), patch.object(p, 'call_gemini', return_value=json.dumps(sig)) as ai:
            out = p.extract_signals(source, '2026/09/10')
            out = p.audit_signals(source, out, '2026/09/10')
        self.assertEqual(ai.call_count, 1)
        payload = json.loads(ai.call_args.args[1])
        self.assertEqual(''.join(payload['source'].values()), source)
        self.assertFalse(out['_quality_requires_review'])

    def test_unrepairable_name_is_not_fabricated(self):
        source = '今天只談大盤與成交量。'
        sig = empty(); sig['holdings'] = [{'name': '祥碩', 'evidence_refs': ['S0001']}]
        with patch.object(p, 'call_gemini', return_value=json.dumps(sig)) as ai:
            out = p.audit_signals(source, p.extract_signals(source, '2026/09/10'), '2026/09/10')
        self.assertEqual(ai.call_count, 2)
        # 逐筆品質關卡改到代號比對之後才跑；這裡照正式流程的順序接著跑一次。
        out = p.validate_evidence(out, source, '2026/09/10', after_codes=True)
        self.assertFalse(out['holdings']); self.assertTrue(out['_quality_requires_review'])

    def test_repair_payload_does_not_repeat_expanded_quotes(self):
        sig = empty(); sig['holdings'] = [{'name': '祥碩', 'evidence_refs': ['S0001'], 'evidence': ['long' * 1000], '_date': 'x'}]
        compact = p.compact_assessment(sig)['holdings'][0]
        self.assertNotIn('evidence', compact); self.assertNotIn('_date', compact)

    def test_long_input_preserves_every_source_segment(self):
        source = '今天會員持股與候選逐檔說明。' * 6000
        batches = p.assessment_batches(source, '2026/09/10')
        self.assertGreater(len(batches), 1)
        combined = {}
        for batch in batches:
            combined.update(batch)
        self.assertEqual(''.join(s['text'] for s in combined.values()), source)
        self.assertEqual(len(p.assessment_batches('股' * 24396, '2026/09/10')), 1)

    def test_malformed_json_stops_before_write(self):
        with patch.object(p, 'call_gemini', return_value='{"buy": []}'):
            with self.assertRaises(ValueError): p.extract_signals('原文', '2026/09/10')

    def test_article_uses_final_records_without_ai(self):
        with patch.object(p, 'call_gemini') as ai:
            result = p.build_article('原文', empty(), '2026/09/10')
        ai.assert_not_called()
        self.assertTrue(result.startswith('文章標題：'))
        for heading in ('① 盤勢總覽', '② 會員操作紀錄', '③ 分析師操作邏輯'): self.assertIn(heading, result)
        for gone in ('④', '⑤', '⑥', '基本資訊', '風險揭露'): self.assertNotIn(gone, result)

    def test_refresh_checkpoint_uses_same_version_on_read_and_write(self):
        ws = Mock(); ws.get_all_values.return_value = [['header']]
        with patch.object(p, 'refresh_checkpoint_sheet', return_value=ws):
            p.save_refresh_checkpoint(None, 'v', '2026/09/10', 'source', ['2026/09/10'])
            row = ws.append_row.call_args.args[0]
            ws.get_all_values.return_value = [['header'], row]
            self.assertEqual(p.load_refresh_checkpoint(None, 'v', '2026/09/10', 'source')['affected'], ['2026/09/10'])
        self.assertEqual(row[3], p.ASSESSMENT_VERSION)


if __name__ == '__main__': unittest.main()
