"""價位只提供查核線索；以來源、主體與上下文處理說明，無模型／網路呼叫。"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('price_context_pipeline', ROOT / 'pipeline/pipeline.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PriceContextContract(unittest.TestCase):
    def test_complete_decimal_and_cross_paragraph_number_are_not_foreign_by_window(self):
        raw='甲公司'+('接著說明'*85)+'上半年21.83元，全年40元。'
        row={'name':'甲公司','code':'1111','reason':'甲公司上半年EPS21.83元，全年40元。'}
        with patch.object(p,'_CODE_MAP',{}):
            self.assertFalse(p._entity_issue(row,{'watch_watch':[row]},raw)[1])
        with patch.object(p,'_entity_scope',return_value=[('甲公司只說83元','甲公司只說83元')]):
            self.assertTrue(p._entity_issue(row,{'watch_watch':[row]},raw)[1])

    def test_local_number_window_miss_is_a_cue_not_a_deletion(self):
        row={'name':'甲公司','code':'1111','reason':'甲公司去年EPS29元，上半年21.83元，全年估40元。','price':'100元'}
        before=copy.deepcopy(row)
        with patch.object(p,'_entity_issue',return_value=(False,True,[])):
            p.sanitize_entity_claims({'watch_watch':[row]},'長段落的原稿')
        self.assertEqual(row['reason'],before['reason']);self.assertEqual(row['price'],before['price'])
        self.assertTrue(row['_entity_source_cues']['number_window_miss'])

    def test_historical_or_split_price_does_not_delete_technical_context(self):
        text = '甲公司先前成本1080元，目前回測年線，等待量縮止穩。'
        row = {'code': '1111', 'name': '甲公司', 'reason': text}
        signals = {'watch_watch': [row]}
        with patch.object(p, '_daily_k_cached', return_value={'1111': {'2026/10/21': [60, 50]}}):
            p.strip_implausible_price_claims(None, signals, '2026/10/21')
            p.price_reality_check(None, signals, '2026/10/21')
        self.assertEqual(row['reason'], text)

    def test_price_match_cannot_rename_company(self):
        raw = '甲公司今天來到237元，先前曾在238元買進。乙公司今天來到237元。'
        row = {'name': '甲公司', 'code': '1111', 'reason': '甲公司回測季線。'}
        signals = {'watch_watch': [row, {'name': '乙公司', 'code': '2222'}]}
        with patch.object(p, '_daily_k_cached', return_value={}), \
             patch.object(p, '_recent_band', side_effect=lambda _k, c, _d: (250, 230) if c == '2222' else (45, 40)):
            p.recheck_names_by_price(None, signals, raw, '2026/10/21')
        self.assertEqual((row['code'], row['name']), ('1111', '甲公司'))
        self.assertNotIn('aliases', row)

    def test_distance_alone_does_not_remove_a_comparison_or_incomplete_asr(self):
        text = '甲公司先前成本1080元，目前回測年線，等待量縮止穩。'
        row = {'name': '甲公司', 'code': '1111', 'reason': text}
        raw = '乙公司先前成本1080元。' + '補充市場背景。' * 50 + '甲公司目前回測年線。'
        with patch.object(p, '_mention_index', return_value={}), \
             patch.object(p, 'source_inventory', return_value=[]), \
             patch.object(p, '_price_attribution', return_value=('foreign', '離 2222 只有 4 個字')):
            p.strip_foreign_price_claims({'watch_watch': [row]}, raw)
        self.assertEqual(row['reason'], text)
        self.assertTrue(row['_price_context_suspicions'])

    def test_anonymous_price_match_is_not_promoted_to_verified_writing_source(self):
        row = {'name': '甲公司', 'code': '1111', 'note': '回測年線，持股續抱。'}
        raw = '市場背景。' * 50 + '我買在74元以下，短線小幅套牢。'
        with patch.dict(p._DK_MEMO, {'map': {'1111': {'2026/10/21': [75, 73]}}}), \
             patch.object(p, '_only_stock_trading_at', return_value=True):
            result = p.unnamed_cost_passages({'holdings': [row]}, raw, '2026/10/21')
        self.assertEqual(result, {})
        self.assertTrue(row['_price_context_suspicions'])

    def review(self, status, source_quote, subject='', note_quote='成本1080元', manual=False, **extra):
        raw = '甲公司目前回測年線，等待量縮止穩。乙公司先前成本1080元，現在跌破季線。'
        text = '甲公司成本1080元，目前回測年線，等待量縮止穩。'
        row = {'name': '甲公司', 'code': '1111', 'reason': text, 'evidence': [raw[:21]]}
        if manual:
            row['_manual_note_kept'] = True
        signals = {'watch_watch': [row]}
        answer = dict(id='n0', text=text, meaning_preserved=True, category_consistent=True,
                      source_supported=status == 'supported', written_style_ok=True, issues_found=[],
                      source_review={'claims': [dict(id=c['id'] if note_quote == '成本1080元' else 'missing',
                          status=status if i == 0 else 'supported', source_quote=source_quote,
                          subject=subject, subject_code='2222' if status == 'cross_entity' and i == 0 else '1111',
                          source_ids=[], safe_text='', explanation='先核主體與時間，再看價位是否可能為誤聽。')
                          for i, c in enumerate(p._note_claims(text))]})
        answer.update(extra)
        with patch.object(p, 'call_gemini', return_value=json.dumps({'texts': [answer]}, ensure_ascii=False)) as call, \
             patch.object(p, 'budget_left', return_value=1000), patch.object(p, 'GEMINI_KEYS', ['offline']):
            p._polish_public_texts(signals, raw, '2026/10/21')
        return signals, row, raw, call

    def test_only_confirmed_cross_entity_claim_is_removed_and_original_preserved(self):
        signals, row, raw, call = self.review('cross_entity', '乙公司先前成本1080元，現在跌破季線。', '乙公司')
        before = copy.deepcopy(row)
        p.quarantine_public_rows(signals, raw)
        self.assertEqual(signals['watch_watch'], [row])
        self.assertNotIn('成本1080元', row['reason'])
        self.assertIn('回測年線', row['reason'])
        self.assertIn('成本1080元', row['_claim_repair']['original'])
        self.assertEqual(row['code'], '1111')
        payload = json.loads(call.call_args.args[1])
        self.assertEqual(''.join(s['text'] for s in payload['sources'].values()), raw)
        self.assertNotIn('context', payload['texts'][0])
        self.assertIn('source_hints', payload['texts'][0])

    def test_fabricated_or_incomplete_cross_entity_evidence_cannot_delete(self):
        for quote, subject, note in [('乙公司從未說過的1080元成本', '乙公司', '成本1080元'),
                                     ('乙公司先前成本1080元，現在跌破季線。', '丙公司', '成本1080元'),
                                     ('乙公司先前成本1080元，現在跌破季線。', '乙公司', '不存在的說明')]:
            with self.subTest(quote=quote, subject=subject):
                signals, row, raw, _ = self.review('cross_entity', quote, subject, note)
                _, hard, _ = p.quality_overview(signals, raw, check_chapters=False)
                self.assertFalse(any('上下文已確認跨股' in e for e in hard))

    def test_asr_uncertainty_is_not_a_company_rejection_or_digit_repair(self):
        signals, row, raw, _ = self.review('asr_uncertain', '乙公司先前成本1080元，現在跌破季線。')
        self.assertEqual(row['code'], '1111')
        self.assertNotIn('1080', row['reason'])
        self.assertIn('回測年線', row['reason'])
        self.assertTrue(signals['_asr_suspect_notes'])
        _, hard, soft = p.quality_overview(signals, raw, check_chapters=False)
        self.assertFalse(any('上下文已確認跨股' in e for e in hard))
        self.assertNotIn('560', row['reason'])

    def test_changed_text_or_source_invalidates_attribution_decision(self):
        for changed_source in (False, True):
            signals, row, raw, _ = self.review('cross_entity', '乙公司先前成本1080元，現在跌破季線。', '乙公司')
            if changed_source:
                raw += '後來更正了主體。'
            else:
                row['reason'] = '甲公司目前回測年線，等待量縮止穩。'
            _, hard, _ = p.quality_overview(signals, raw, check_chapters=False)
            self.assertFalse(any('上下文已確認跨股' in e for e in hard))

    def test_approved_source_review_keeps_technical_claims(self):
        signals, row, _, _ = self.review('supported', '甲公司目前回測年線，等待量縮止穩。')
        self.assertIn('回測年線', row['reason'])
        self.assertIn('等待量縮止穩', row['reason'])
        self.assertIn('_text_before_polish', row)

    def test_manual_note_is_preserved_even_when_ai_flags_other_subject(self):
        signals, row, raw, _ = self.review('cross_entity', '乙公司先前成本1080元，現在跌破季線。', '乙公司', manual=True)
        p.quarantine_public_rows(signals, raw)
        self.assertEqual(signals['watch_watch'], [row])
        self.assertIn('成本1080元', row['reason'])

    def test_missing_source_review_does_not_start_claim_repair(self):
        # 2026/10/27：模型沒回逐項核對（舊契約或漏填）時，不得把每一項判成不足再整段清空
        row = {'name': '甲公司', 'code': '1111'}
        self.assertEqual(p._validated_source_review({}, row, '甲公司回測年線。', '甲公司回測年線。'), {})
        self.assertEqual(p._validated_source_review({'source_review': {'claims': []}}, row, '甲公司回測年線。', '甲公司回測年線。'), {})

    def test_ellipsis_quote_of_one_passage_is_verified_span_by_span(self):
        # 10/15 嘉澤重播：模型用「...」節錄同一段前後句，逐段都在原稿且順序相同才算原字引用
        raw = '昨天甲公司漲到2,000塊。張總，你在這邊叫我們出一半啦。甲公司跌到1,800多。啊再等一下，靠近季線比較好。'
        text = '甲公司自2,000元回落至1,800多元，等待靠近季線。'
        reply = {'id': 'c0', 'status': 'supported', 'subject': '甲公司', 'subject_code': '1111', 'explanation': '本股位置',
                 'source_quote': '昨天甲公司漲到2,000塊...甲公司跌到1,800多。啊再等一下，靠近季線比較好。'}
        ok = p._validated_source_review({'source_review': {'claims': [reply, dict(reply, id='c1')]}},
                                        {'name': '甲公司', 'code': '1111'}, text, raw)
        self.assertEqual(ok['status'], 'supported')
        swapped = dict(reply, source_quote='甲公司跌到1,800多...昨天甲公司漲到2,000塊')
        bad = p._validated_source_review({'source_review': {'claims': [swapped, dict(swapped, id='c1')]}},
                                         {'name': '甲公司', 'code': '1111'}, text, raw)
        self.assertEqual(bad['status'], 'insufficient')

    def test_insufficient_claims_keep_original_wording(self):
        # 只有確認跨股才移除；不足（引用不完整）保留原句、只記待核
        text = '甲公司股本較小，拉抬較快，目前尚未見低檔量能。'
        raw = '乙公司股本比較小，拉起來速度比較快。甲公司目前尚未見低檔量能。'
        row = {'name': '甲公司', 'code': '1111', 'reason': text}
        signals = {'watch_watch': [row]}
        review = p._validated_source_review({'source_review': {'claims': [{'id': 'c2', 'status': 'supported', 'subject': '甲公司',
            'subject_code': '1111', 'source_quote': '甲公司目前尚未見低檔量能。', 'explanation': '本股量能'}]}}, row, text, raw)
        self.assertFalse(p._apply_source_claim_review(signals, row, 'reason', review))
        self.assertEqual(row['reason'], text)

    def test_one_supported_claim_cannot_approve_a_mixed_note(self):
        text = '甲公司股本較小，拉抬較快，目前尚未見低檔量能。'
        raw = '乙公司股本比較小，拉起來速度比較快。甲公司目前尚未見低檔量能。'
        row = {'name': '甲公司', 'code': '1111'}
        answer = {'source_review': {'claims': [{'id': 'c2', 'status': 'supported',
            'source_quote': '甲公司目前尚未見低檔量能。', 'subject': '甲公司', 'explanation': '後半句量能屬本股'}]}}
        review = p._validated_source_review(answer, row, text, raw)
        self.assertEqual(review['status'], 'insufficient')
        self.assertEqual(len(review['claims']), 3)

    def test_duplicate_claim_answers_are_not_counted_as_verified(self):
        text, raw = '甲公司目前回測年線。', '甲公司目前回測年線。'
        claim = dict(id='c0', status='supported', source_quote=raw, subject='甲公司', explanation='本股位置')
        review = p._validated_source_review({'source_review': {'claims': [claim, claim]}},
                                             {'name': '甲公司', 'code': '1111'}, text, raw)
        self.assertEqual(review['status'], 'insufficient')

    def test_batch_schema_and_claim_numbering_keep_thousands_intact(self):
        self.assertEqual(p._note_claims('先前成本1,580元，目前回測季線。'),
                         [{'id': 'c0', 'text': '先前成本1,580元'}, {'id': 'c1', 'text': '目前回測季線'}])
        with patch.dict(p._SCHEMA_STATE, {'on': True}):
            config = p.gemini_generation_config('offline', want_json=True, tag='public-edit')
        fields = config['responseJsonSchema']['properties']['texts']['items']['properties']
        self.assertIn('claims', fields['source_review']['required'])

    def test_source_ids_use_original_text_instead_of_model_retyping(self):
        raw, old = '甲公司回測年線，尚待量縮止穩。', '甲公司回測年線。'
        answer = {'source_review': {'claims': [dict(id='c0', status='supported', source_ids=['S0001'],
            source_quote='模型改字或省略的引句', subject='甲公司', subject_code='1111', explanation='本股技術位置')]}}
        review = p._validated_source_review(answer, {'code': '1111', 'name': '甲公司'}, old, raw)
        self.assertEqual(review['status'], 'supported')
        self.assertEqual(review['claims'][0]['source_quote'], raw)

    def test_claim_subject_code_conflict_overrides_blanket_supported(self):
        raw = '乙公司股本比較小，拉起來速度較快。甲公司尚待低檔成交量。'
        old = '甲公司股本比較小。'
        reply = dict(id='c0', status='supported', source_ids=['S0001'], source_quote='',
                     subject='乙公司', subject_code='2222', explanation='股本特徵真正主體為乙公司')
        review = p._validated_source_review({'source_review': {'claims': [reply]}},
                                           {'code': '1111', 'name': '甲公司'}, old, raw)
        self.assertEqual(review['status'], 'cross_entity')

    def test_shared_group_claim_is_not_rejected_for_also_naming_another_stock(self):
        raw = '甲公司和乙公司都來到年線，不要再殺低。'
        answer = {'source_review': {'claims': [dict(id='c0', status='supported', source_ids=['S0001'],
            subject='甲公司', subject_code='1111', explanation='原句明確涵蓋並列的兩檔')]}}
        review = p._validated_source_review(answer, {'code': '1111', 'name': '甲公司'}, '甲公司來到年線。', raw)
        self.assertEqual(review['status'], 'supported')

    def test_explicit_identity_rejection_remains_an_effective_ignored_vote(self):
        answer = {'about_itself': False, 'buy': False, 'sell': False, 'holding': False,
                  'quotes': {}, 'now': 'none', 'tone': 'neutral'}
        result = p.verdict_class(answer, p._ev_norm('這個字只是一般語句，沒有在分析公司。'), names=['甲公司'])
        self.assertEqual(result[0], 'ignored')

    def test_human_number_caution_only_applies_to_the_bound_source(self):
        raw = '甲公司先前成本1080元，後來這一波56是不是跌破了，原稿數字待核。'
        caution = {'number': '56', 'quote': raw}
        row = {'code': '1111', 'name': '甲公司'}
        item = {'source_review': {'claims': [dict(id='c0', status='supported', source_ids=['S0001'],
            subject='甲公司', subject_code='1111', explanation='原字存在', safe_text='上方套牢賣壓待消化。')]}}
        with patch.object(p, '_source_number_cautions', return_value=[caution]):
            review = p._validated_source_review(item, row, '先前跌破56元後面臨套牢賣壓。', raw)
        self.assertEqual(review['status'], 'asr_uncertain')
        self.assertEqual(p._source_number_cautions({'code': '2327'}, raw + '新版更正。'), [])


if __name__ == '__main__':
    unittest.main()
