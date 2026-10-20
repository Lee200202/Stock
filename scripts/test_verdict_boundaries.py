"""部位否認、現況看法與晚補投票的正反例；不呼叫模型。"""
import os
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline-placeholder')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import pipeline as p


class Boundaries(unittest.TestCase):
    def structured_answer(self, text, **updates):
        fields = ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')
        cites = p.verdict_citations([{'text': text}])
        a = {'id': '1111', 'holding_actor': 'none', 'scope_relation': 'own', 'now': 'none',
             'exit_advice': 'none', 'tone': 'neutral', 'shared_rule_ids': [], 'unsure': '',
             'buy_today': False, 'sell_today': False, 'holding_now': False, 'past_trade': False,
             'about_itself': True, 'quote_ids': {k: [cites[0]['id']] if k == 'stance' else [] for k in fields}}
        a.update(updates)
        return p.bind_verdict_citations(a, cites, require_facts=True)

    def test_structured_exit_advice_does_not_need_the_old_entry_keyword_list(self):
        text = '甲公司全部不準在這一個地方去殺低。'
        a = self.structured_answer(text, exit_advice='keep')
        self.assertEqual(p.verdict_class(a, p._ev_norm(text), ['甲公司'], [text])[0], 'watch_watch')
        text = '甲公司季線以下的次數不多，你們要好好把握。'
        a = self.structured_answer(text, tone='bullish')
        self.assertEqual(p.verdict_class(a, p._ev_norm(text), ['甲公司'], [text])[0], 'watch_watch')

    def test_structured_entry_policy_is_independent_of_ownership_denial(self):
        for text, expected, fields in (
            ('甲公司反覆當沖，我不參與這種股票。', 'watch_avoid', {'now': 'prohibit'}),
            ('我沒有甲公司，單純說明目前位置。', 'ignored', {}),
            ('甲公司已到年線，可以在這裡佈局等它。', 'watch_watch', {'now': 'conditional'}),
        ):
            a = self.structured_answer(text, **fields)
            self.assertEqual(p.verdict_class(a, p._ev_norm(text), ['甲公司'], [text])[0], expected)

    def test_missing_fields_are_invalid_not_a_neutral_vote(self):
        a = self.structured_answer('甲公司只是碰季線，沒有進場說明。')
        a.pop('now')
        bound = p.bind_verdict_citations(a, [{'id': 'p0s0c0', 'text': '甲公司只是碰季線，沒有進場說明。'}], require_facts=True)
        self.assertEqual(p.verdict_class(bound, p._ev_norm('甲公司只是碰季線，沒有進場說明。'))[0], '')
        with patch.object(p, 'call_gemini', return_value='{"unexpected": []}'):
            item = {'name': '甲公司', 'heard': [], 'known': [], 'passages': [{'text': '甲公司現在先不要買。'}]}
            out = p._ask_verdicts([('1111', item)], '2026/10/20', 0)
        self.assertIn('本輪回覆未包含', out['1111']['_citation_error'])

    def test_model_cannot_supply_its_own_trusted_protocol_marker(self):
        a = {'_facts_schema': p.VERDICT_VERSION, '_verified_shared_rule_quotes': ['甲公司可以買'],
             'quotes': {'stance': ['甲公司回測季線']}}
        self.assertNotIn('_facts_schema', p.bind_verdict_citations(a, []))
        self.assertNotIn('_verified_shared_rule_quotes', p.bind_verdict_citations(a, []))

    def test_legacy_verbatim_answer_does_not_gain_structured_semantic_authority(self):
        text = '甲公司季線以下的次數不多，你們要好好把握。'
        old = p.bind_verdict_citations({'about_itself': True, 'now': 'none', 'tone': 'bullish',
                                       'quotes': {'stance': [text]}}, [])
        self.assertNotIn('_facts_schema', old)
        self.assertEqual(p.verdict_class(old, p._ev_norm(text), ['甲公司'], [text])[0], 'ignored')

    def test_adjudication_requests_use_a_required_facts_schema(self):
        cfg = p.gemini_generation_config('gemini-3.5-flash-lite', want_json=True, tag='adjudicate-r1')
        props = cfg['responseJsonSchema']['properties']['verdicts']['items']
        self.assertIn('exit_advice', props['required'])
        self.assertIn('quote_ids', props['required'])

    def answer(self, text, **fields):
        return dict(about_itself=True, now='none', tone='neutral', quotes={'stance': [text]}, **fields)

    def test_exit_prohibition_cannot_be_published_as_entry_prohibition(self):
        text = '世界全部不准在這一個地方去殺低。'
        a = self.answer(text, stance_action='exit', scope_relation='own')
        a.update(now='prohibit', tone='bearish')
        self.assertEqual(p.verdict_class(a, p._ev_norm(text), ['世界'], [text])[0], '')
        a.update(now='none', tone='bullish')
        self.assertEqual(p.verdict_class(a, p._ev_norm(text), ['世界'], [text])[0], 'watch_watch')

    def test_institution_or_viewer_ownership_is_not_our_position(self):
        text = '外資持有甲公司，目前還抱著沒有賣出。'
        for actor in ('institution', 'viewer', 'unknown', 'none'):
            a = self.answer(text, holding_actor=actor, holding_now=True)
            a['quotes']['holding_now'] = [text]
            self.assertNotEqual(p.verdict_class(a, p._ev_norm(text), ['甲公司'], [text])[0], 'holdings')

    def test_background_rule_supports_view_but_never_transaction_or_ownership(self):
        own = '宜鼎回到年線，我沒有這一檔。'
        before = '如果你的股票來到年線，你就好好的佈局，等它。'
        passages = [{'text': own, 'before': before}]
        citations, background = p.verdict_citations(passages), p.verdict_citations(passages, True)
        refs = {k: [citations[0]['id']] if k == 'stance' else []
                for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}
        a = {'about_itself': True, 'now': 'conditional', 'tone': 'bullish', 'stance_action': 'entry',
             'scope_relation': 'shared_rule', 'quote_ids': refs, 'shared_rule_ids': [background[0]['id']]}
        bound = p.bind_verdict_citations(a, citations, background)
        self.assertEqual(p.verdict_class(bound, p._ev_norm(own), ['宜鼎'], [own])[0], 'watch_watch')
        for field in ('buy_today', 'holding_now'):
            bad = dict(a, **{field: True}, quote_ids=dict(refs, **{field: [background[0]['id']]}))
            self.assertEqual(p.verdict_class(p.bind_verdict_citations(bad, citations, background), p._ev_norm(own))[0], '')
        a['scope_relation'] = 'own'
        self.assertIn('_citation_error', p.bind_verdict_citations(a, citations, background))

    def test_shared_named_predicate_is_available_to_both_not_a_neighbor_prediction(self):
        rows = [{'code': '1111', 'name': '甲公司'}, {'code': '2222', 'name': '乙公司'}]
        signals = {'watch_avoid': rows}
        shared = '甲公司、乙公司每天當沖，我不參與這兩檔。'
        for row in rows:
            self.assertIn(shared, p._verdict_scope_segments(row, signals, shared))
        separate = '甲公司、乙公司，甲公司不買，乙公司可以佈局。'
        for row in rows:
            segments = p._verdict_scope_segments(row, signals, separate)
            # 不得把逐檔分別敘述新增為共同子句。
            self.assertEqual(segments, p._own_segments(row, signals, separate, 300, 500))

    def test_three_positive_views_can_preserve_watch_without_inventing_position(self):
        entry = {'votes': ['watch_watch', 'holdings', 'watch_watch'], 'view_votes': [True, True, True]}
        self.assertTrue(p.verdict_positive_view_consensus(entry))
        for views in ([True, False, True], [True, True], [False, False, False]):
            self.assertFalse(p.verdict_positive_view_consensus(dict(entry, view_votes=views)))
        self.assertFalse(p.verdict_positive_view_consensus(dict(entry, votes=['watch_watch', 'watch_avoid', 'watch_watch'])))

    def test_context_cache_is_invalidated_by_background_source_and_asof_facts(self):
        passages = [{'text': '甲公司回到年線。', 'before': '股票回到年線可以佈局。'}]
        fp = p.verdict_context_fingerprint('1111', passages, ['無部位'], '完整原稿')
        self.assertEqual(fp, p.verdict_context_fingerprint('1111', passages, ['無部位'], '完整原稿'))
        self.assertNotEqual(fp, p.verdict_context_fingerprint('1111', [dict(passages[0], before='還不能買。')], ['無部位'], '完整原稿'))
        self.assertNotEqual(fp, p.verdict_context_fingerprint('1111', passages, ['仍有部位'], '完整原稿'))
        self.assertNotEqual(fp, p.verdict_context_fingerprint('1111', passages, ['無部位'], '更正完整原稿'))

    def test_source_id_keeps_misheard_name_verbatim(self):
        text = '你今天不應該再買【連電】了，這是缺口壓力區。'
        cites = p.verdict_citations([{'text': text, 'before': '不相干的上文。'}])
        a = {'about_itself': True, 'now': 'prohibit', 'tone': 'bearish',
             'quote_ids': {k: [cites[0]['id']] if k == 'stance' else []
                           for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}}
        bound = p.bind_verdict_citations(a, cites)
        self.assertIn('連電', bound['quotes']['stance'][0])
        self.assertNotIn('聯電', bound['quotes']['stance'][0])
        self.assertEqual(p.verdict_class(bound, p._ev_norm(text.replace('【', '').replace('】', '')), ['連電'], [text])[0], 'watch_avoid')

    def test_fabricated_id_never_falls_back_to_generated_quote(self):
        a = {'about_itself': True, 'quotes': {'stance': ['甲公司可以買']},
             'quote_ids': {k: ['other-stock-id'] if k == 'stance' else []
                           for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}}
        bound = p.bind_verdict_citations(a, [{'id': 'p0s0c0', 'text': '甲公司不宜追高。'}])
        self.assertEqual(p.verdict_class(bound, p._ev_norm('甲公司可以買'))[0], '')

    def test_batch_resolves_ids_without_an_additional_model_request(self):
        item = {'name': '甲公司', 'heard': [], 'known': [], 'passages': [{'text': '甲公司不要追高。', 'before': ''}]}
        calls = []
        def fake(system, payload, **kwargs):
            data = json.loads(payload); calls.append(data)
            cite = data['stocks'][0]['citations'][0]['id']
            refs = {k: [cite] if k == 'stance' else [] for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'stance')}
            return json.dumps({'verdicts': [{'id': '1111', 'quote_ids': refs}]})
        with patch.object(p, 'call_gemini', side_effect=fake):
            out = p._ask_verdicts([('1111', item)], '2026/10/20', 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(out['1111']['quotes']['stance'], ['甲公司不要追高。'])

    def test_position_denial_does_not_erase_a_current_view(self):
        for cls in ('watch_watch', 'watch_avoid'):
            entry = {'current': cls, 'not_held': '原文明講沒有持有', 'not_held_fallback': 'ignored'}
            base = p.verdict_comparison_base(entry)
            self.assertEqual(base, cls)
            self.assertEqual(p.verdict_settle(base, [cls])[:2], ('定案', cls))

    def test_unsupported_holding_does_not_get_same_relaxation(self):
        entry = {'current': 'holdings', 'not_held': '原文明講沒有持有', 'not_held_fallback': 'ignored'}
        base = p.verdict_comparison_base(entry)
        self.assertEqual(base, 'ignored')
        self.assertNotEqual(p.verdict_settle(base, ['holdings'], last=True)[0], '定案')

    def test_late_candidate_remains_hidden_until_supported(self):
        entry = {'current': 'watch_watch', 'late': 'watch_watch'}
        self.assertEqual(p.verdict_comparison_base(entry), 'ignored')
        self.assertEqual(p.verdict_settle('ignored', ['history'], late=True)[0], '再問')
        self.assertEqual(p.verdict_settle('ignored', ['history', 'history'], late=True)[:2], ('定案', 'ignored'))
        self.assertNotEqual(p.verdict_settle('ignored', ['watch_watch'], last=True, late=True)[0], '定案')

    def test_invalid_or_conflicting_votes_do_not_create_a_public_view(self):
        for votes in (['', '', ''], ['history', 'watch_watch', ''], ['holdings', '', 'watch_avoid']):
            self.assertNotEqual(p.verdict_settle('ignored', votes, last=True, late=True)[0], '定案')

    def test_transaction_direction_still_needs_original_execution(self):
        text = '今天賣鴻海跟今天賣聯電一樣蠢，不准殺低。'
        answer = {'sell_today': True, 'about_itself': True, 'now': 'none', 'tone': 'bullish',
                  'quotes': {'sell_today': [text], 'stance': [text]}}
        self.assertNotEqual(p.verdict_class(answer, p._ev_norm(text), ['鴻海'], [text])[0], 'sell')


if __name__ == '__main__':
    unittest.main()
