import copy
import json
import unittest
from unittest.mock import patch, Mock
from test_quality import p, empty

class V6PriceTests(unittest.TestCase):
    def test_public_formats(self):
        for value, expected in [('900以下','900以下'),('1000以上','1000以上'),('1820、2025、2135','2135'),
                ('182020252135','2135'),('2385-2405','2405'),('17,880','17880'),('12.5、13.25','13.25'),
                ('20日均線','未說明'),('1820249999','未說明'),('38X','未說明'),('未說明','未說明'),
                ('-200','未說明'),('2026/09/11買1580','未說明')]:
            with self.subTest(value=value): self.assertEqual(p.display_price(value), expected)

    def test_technical_200_cleared_without_dailyk(self):
        sig=empty();sig['watch_watch']=[{'name':'台積電','code':'2330','price':'200','reason':'碰到特定技術線型（200附近）再注意'}]
        with patch.object(p,'_daily_k_cached',return_value={}): p.price_reality_check(None,sig,'2026/09/11')
        self.assertEqual(sig['watch_watch'][0]['price'],'未說明')

    def test_explicit_currency_price_can_be_200(self):
        self.assertEqual(p.display_price('200','買點是200元','等碰線再注意'),'200')

    def test_no_scaling_or_holdings_demotion(self):
        sig=empty();sig['buy']=[{'name':'測試','code':'2330','price':'38','reason':'今天38買進',
                              'evidence':['今天38元買進測試股票']}]
        with patch.object(p,'_daily_k_cached',return_value={'2330':{'2026/09/11':(4000,3800)}}):
            p.price_reality_check(None,sig,'2026/09/11')
        self.assertEqual(sig['buy'][0]['price'],'未說明')
        self.assertFalse(sig['watch_watch'])

    def test_multiple_trade_prices_preserved_in_storage(self):
        sig=empty();sig['buy']=[{'name':'台積電','price':'1820、2025、2135'}]
        with patch.object(p,'_daily_k_cached',return_value={}): p.price_reality_check(None,sig,'2026/09/11')
        self.assertEqual(sig['buy'][0]['price'],'1820、2025、2135')
        self.assertIn('| 2135 |',p.canonical_article(sig,'2026/09/11'))

class V6CoverageTests(unittest.TestCase):
    def test_confirmed_aliases_and_currency(self):
        for name, code in [('00981A','00981A'),('瑞獄','2379'),('雨沾','8271'),('維星','2377'),
                           ('邦店','2344'),('連電','2303'),('大力光','3008'),('利基電','6770'),('宜頂','5289')]:
            self.assertEqual(p.resolve_code(name,'')[0],code)
        self.assertEqual(p.resolve_code('日幣','')[0],p.REJECT)

    def test_ignored_coverage_and_richness_are_real_checks(self):
        sig=empty();sig['ignored']=[{'name':n} for n in ['日幣','ABF載板','航運股','00981A','廣達','瑞獄']]
        gaps=p.publication_gaps(sig,'原文'*3000)
        self.assertEqual(sum(g.startswith('排除覆核') for g in gaps),3)
        self.assertTrue(any(g.startswith('盤勢內容偏短') for g in gaps))
        self.assertTrue(any(g.startswith('教學內容偏短') for g in gaps))

    def test_bounded_coverage_retry(self):
        sig=empty();sig['ignored']=[{'name':'廣達','reason':'行情舉例','evidence_refs':['S0001']}]
        with patch.object(p,'call_gemini',return_value=json.dumps(sig)),patch.object(p,'budget_left',return_value=900):
            with patch.object(p,'assessment_batches',return_value=[{'S0001':{'text':'廣達昨天漲今天跌，追高容易套牢。'}}]):
                result=p.audit_context_json('廣達昨天漲今天跌，追高容易套牢。',copy.deepcopy(sig),'2026/09/10')
            self.assertEqual(p.call_gemini.call_count,2)
        self.assertTrue(result['_quality_requires_review'])

class V6BackgroundTests(unittest.TestCase):
    def test_content_refresh_precedes_deferred_performance(self):
        saved=[]; state={'affected':['2026/09/10'],'completed':[]}
        def checkpoint(*a): state['completed']=list(a[-1])
        with patch.object(p,'_DEFER_BACKGROUND',True),patch.object(p,'_BACKGROUND_REFRESH',[]), \
             patch.object(p,'load_refresh_checkpoint',side_effect=lambda *a:copy.deepcopy(state)), \
             patch.object(p,'save_refresh_checkpoint',side_effect=checkpoint), \
             patch.object(p,'_transcript_rows_of_day',return_value=None),patch.object(p,'budget_left',return_value=900), \
             patch.object(p,'maybe_refresh_site',side_effect=lambda **kw:saved.append(kw['only'][0]) or {'ok':True}), \
             patch.object(p,'_CURRENT_JOB',None,create=True):
            result=p.finish_transcript_refresh(None,'MANUAL-20260910','2026/09/10','raw',['2026/09/10'])
            self.assertTrue(result['pending']);self.assertIn('文章已更新',result['note'])
            self.assertEqual(saved,['smsmail','codes','tracker'])
            self.assertNotIn('complete',state['completed'])
            p.drain_background_refresh()
            self.assertEqual(saved,['smsmail','codes','tracker','perfhist','perf'])
            self.assertIn('complete',state['completed'])

if __name__=='__main__':unittest.main()
