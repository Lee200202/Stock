import unittest
from test_quality import p, empty


class PastRecommendationTests(unittest.TestCase):
    def signal(self, quote, category='watch_watch'):
        s=empty();s[category]=[{'name':'瑞昱','code':'2379','reason':quote,'evidence':[quote],'_date':'2026/09/11'}]
        return s

    def test_recommendation_recap_does_not_create_watch_category(self):
        for quote in ('我推薦瑞昱在100元，我在那裡推薦瑞昱的。',
                      '當時推薦瑞昱100元沒人要買，你們等漲上來再買。',
                      '我在那裡推薦瑞昱的，你們都不敢買。'):
            for cat in ('watch_watch','watch_avoid','history'):
                with self.subTest(quote=quote,cat=cat):
                    s=self.signal(quote,cat);p.exclude_past_recommendations(s,quote)
                    self.assertFalse(s[cat]);self.assertEqual(len(s['ignored']),1)
                    self.assertTrue(s['ignored'][0]['_past_recommendation_only_verified'])
                    self.assertFalse(p.publication_gaps(s,quote))
                    self.assertNotIn('瑞昱',p.canonical_article(s,'2026/09/11'))

    def test_current_instruction_and_breakout_condition_survive(self):
        for quote in ('以前推薦瑞昱100元沒人買，現在看好瑞昱，等拉回再买進。',
                      '我當時推薦瑞昱100元，今天等突破120元再買瑞昱。',
                      '今天我推薦瑞昱在100元，現在留意。'):
            s=self.signal(quote);p.exclude_past_recommendations(s,quote)
            self.assertEqual(len(s['watch_watch']),1);self.assertFalse(s['ignored'])

    def test_current_holdings_never_inferred_or_removed_by_recap(self):
        quote='以前推薦瑞昱100元沒人要買。'
        s=self.signal(quote);s['holdings']=[{'name':'瑞昱','code':'2379','note':'現在仍持有瑞昱','evidence':['現在仍持有瑞昱']}]
        p.exclude_past_recommendations(s,quote+'現在仍持有瑞昱')
        self.assertEqual(len(s['holdings']),1)

    def test_confirmed_exclusion_is_not_carried_back(self):
        quote='以前推薦瑞昱100元沒人買，你們等漲上來才買。'
        s=self.signal(quote);p.exclude_past_recommendations(s,quote)
        prior=[{'name':'瑞昱','code':'2379','reason':'以前推薦100元','_cat':'watch_watch'}]
        rec=p.reconcile_with_prior(s,prior,'2026/09/11')
        self.assertFalse(rec['carried']);self.assertEqual(len(rec['accepted']),1)
        self.assertFalse(s['watch_watch'])

    def test_model_tag_alone_does_not_authorize_removal(self):
        s=self.signal('以前推薦瑞昱100元沒人買。','ignored')
        s['ignored'][0].update(exclusion_reason='past_recommendation_only',_past_recommendation_only_verified=True)
        p.exclude_past_recommendations(s,'今天只提台積電量縮整理。')
        self.assertFalse(s['ignored'][0].get('_past_recommendation_only_verified'))
        prior=[{'name':'瑞昱','code':'2379','reason':'現況待確認','_cat':'watch_watch'}]
        self.assertEqual(len(p.reconcile_with_prior(s,prior,'2026/09/11')['carried']),1)

    def test_repeat_filter_is_idempotent(self):
        quote='當時推薦瑞昱100元沒人買。'
        s=self.signal(quote)
        p.exclude_past_recommendations(s,quote);p.exclude_past_recommendations(s,quote)
        self.assertEqual(len(s['ignored']),1)


if __name__=='__main__':unittest.main()
