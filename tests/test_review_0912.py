import copy
import json
import unittest
from unittest.mock import Mock, patch
from test_quality import p, empty
from scripts.evaluate_transcript_assessment import compare


class ReviewQualityTests(unittest.TestCase):
    def test_baseline_comparison_detects_wrong_category_and_missing_stock(self):
        expected={'holdings':[{'code':'3533'}], 'watch_watch':[{'code':'8210'}]}
        actual={'watch_watch':[{'code':'3533'}]}
        result=compare(expected,actual)
        self.assertEqual(result['missing'],[('holdings','3533'),('watch_watch','8210')])
        self.assertEqual(result['unexpected'],[('watch_watch','3533')])

    def test_baseline_keeps_holdings_and_new_money_watch_separate(self):
        sig={'holdings':[{'code':'2330'}], 'watch_watch':[{'code':'2330'}]}
        result=compare(sig,sig)
        self.assertEqual(result['matched'],2)
        self.assertFalse(result['missing'] or result['unexpected'])

    def test_review_finds_holdings_even_when_original_quotes_are_valid(self):
        source = '嘉澤昨天買的，今天還抱著。台積電也是手中持股。'
        initial = empty()
        initial['holdings'] = [{'name':'嘉澤', 'evidence_refs':['S0001']}]
        reviewed = copy.deepcopy(initial)
        reviewed['holdings'].append({'name':'台積電','evidence_refs':['S0001']})
        with patch.dict(p.os.environ, {'GEMINI_SEMANTIC_AUDIT':'true'}), patch.object(p,'call_gemini',return_value=json.dumps(reviewed)) as ai:
            result = p.audit_context_json(source,initial,'2026/09/11')
        ai.assert_called_once()
        self.assertEqual({r['name'] for r in result['holdings']},{'嘉澤','台積電'})

    def test_failed_semantic_review_is_visible_and_preserves_original(self):
        sig=empty();sig['holdings']=[{'name':'嘉澤','evidence_refs':['S0001']}]
        with patch.object(p,'call_gemini',side_effect=TimeoutError('offline')):
            out=p.audit_context_json('嘉澤我們手中還抱著。',sig,'2026/09/11')
        self.assertTrue(out['_quality_requires_review'])
        self.assertEqual(out['holdings'][0]['name'],'嘉澤')

    def test_disappeared_candidate_is_retained_in_audit(self):
        sig=empty();sig['holdings']=[{'name':'嘉澤','evidence_refs':['S0001']}]
        with patch.object(p,'call_gemini',return_value=json.dumps(empty())):
            out=p.audit_context_json('嘉澤我們手中還抱著。',sig,'2026/09/11')
        self.assertTrue(out['_quality_requires_review'])
        self.assertEqual(out['uncertain'][0]['name'],'嘉澤')
        self.assertTrue(out['uncertain'][0]['evidence'])

    def test_bad_review_array_does_not_discard_original(self):
        sig=empty();sig['holdings']=[{'name':'嘉澤','evidence_refs':['S0001']}]
        bad=empty();bad['holdings']=['bad']
        with patch.object(p,'call_gemini',return_value=json.dumps(bad)):
            out=p.audit_context_json('嘉澤我們手中還抱著。',sig,'2026/09/11')
        self.assertTrue(out['_quality_requires_review'])
        self.assertEqual(out['holdings'][0]['name'],'嘉澤')

    def test_timeout_is_provisional_but_auth_failure_is_rejected(self):
        with patch.object(p.requests,'post',side_effect=p.requests.ReadTimeout()):
            usable,status,note=p.smoke_generate('offline','gemini-3.5-flash-lite')
        self.assertTrue(usable);self.assertEqual(status,0);self.assertIn('未完成',note)
        with patch.object(p.requests,'post',return_value=Mock(status_code=403,text='PERMISSION_DENIED')):
            self.assertFalse(p.smoke_generate('offline','gemini-3.5-flash-lite')[0])

    def test_fresh_teaching_replaces_old_article_without_removing_holdings(self):
        sig=empty();sig['holdings']=[{'name':'嘉澤','code':'3533','note':'持續抱著。'}]
        sig['market']=[{'kind':'view','text':'新內容：已有部位續抱，新資金等待。','_evidence_verified':True}]
        old='\n'.join(h+' 舊內容' for h in '①②③④⑤⑥')
        out=p.canonical_article(sig,'2026/09/11',old)
        self.assertIn('新內容',out);self.assertNotIn('⑤ 舊內容',out)
        self.assertIn('會員目前持有股票',out);self.assertIn('嘉澤',out)

    def test_richer_overview_does_not_cut_after_old_650_character_limit(self):
        sig=empty();sig['market']=[{'kind':'flow','text':str(i)+'講者說明。'*28,'_evidence_verified':True} for i in range(7)]
        out=p.canonical_article(sig,'2026/09/11')
        self.assertIn('6講者說明',out)

    def test_pure_history_is_not_a_public_fifth_category(self):
        sig=empty();sig['history']=[{'name':'所羅門','reason':'以前賣了。'}]
        self.assertNotIn('所羅門',p.render_record_chapter(sig,'2026/09/11'))


if __name__ == '__main__': unittest.main()
