import copy
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

os.environ.setdefault('SPREADSHEET_ID', 'offline-test')
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('stock_pipeline', ROOT / 'pipeline/pipeline.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
# 判讀前現在會先載入名稱表；離線測試預設空快取，各案例自行提供官方清單。
p._CODE_MAP = {}


def empty():
    return {k: [] for k in p.SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')}


def trade(when='yesterday'):
    q = '像我昨天叫人家賣力積電，我一定在漲的時候賣。'
    return dict(name='力積電', code='', evidence=[q], aliases=[], price='未說明',
                reason='昨天賣出', when=when, time_evidence=q, seq=1)


class EvidenceTests(unittest.TestCase):
    def test_yesterday_retained_with_exact_quote(self):
        sig = empty(); sig['sell'] = [trade()]
        p.validate_evidence(sig, trade()['evidence'][0], '2026/09/10')
        with patch.object(p, '_prev_trading_day', side_effect=AssertionError('must not read stale cache')):
            p.apply_when_and_seq(None, sig, '2026/09/10')
        self.assertEqual(sig['sell'][0]['_date'], '2026/09/09')

    def test_past_not_allowed_as_today(self):
        sig = empty(); sig['sell'] = [trade('today')]
        p.validate_evidence(sig, trade()['evidence'][0], '2026/09/10')
        self.assertFalse(sig['sell']); self.assertEqual(sig['history'][0]['when'],'unknown')

    def test_missing_date_never_defaults_today(self):
        sig = empty(); sig['sell'] = [trade(None)]
        p.apply_when_and_seq(None, sig, '2026/09/10')
        self.assertFalse(sig['sell']);self.assertEqual(sig['history'][0]['when'],'unknown')

    def test_unknown_huacheng_kept_out_of_events(self):
        sig = empty(); sig['history'] = [{'name':'華城','evidence':['華城賣775塊的，現在華城726塊的。'],'when':'unknown','reason':'先前賣出，日期未明'}]
        p.validate_evidence(sig, sig['history'][0]['evidence'][0], '2026/09/10')
        self.assertFalse(sig['sell'])

    def test_fabricated_quote_blocks_write(self):
        sig = empty(); sig['sell'] = [trade()]
        p.validate_evidence(sig, '只有華城的說明', '2026/09/10')
        self.assertFalse(sig['sell']);self.assertEqual(len(sig['uncertain']),1)

    def test_unmentioned_name_cannot_borrow_other_stock_quote(self):
        sig = empty(); r = trade(); r['name'] = '鴻準'; sig['sell'] = [r]
        p.validate_evidence(sig, trade()['evidence'][0], '2026/09/10')
        self.assertFalse(sig['sell']);self.assertIn('名稱',sig['uncertain'][0]['_疑點'])

    def test_industry_spelling_variants(self):
        for name in ('ABF載版', 'ABF 載板', 'AB載板', '矽晶圓'):
            with self.subTest(name=name): self.assertTrue(p.is_non_stock(name)[0])

    def test_polish_cannot_invent_or_erase_names(self):
        sources = p.transcript_sources('原始稿有辛耘和宏捷科', '潤飾稿有新代與鴻準而且很長很長很長')
        for k in ('extract','audit','verify','arbitrate'):
            self.assertEqual(sources[k], '原始稿有辛耘和宏捷科')

    def test_audit_failure_stops_instead_of_silent_pass(self):
        with patch.object(p, 'call_gemini', side_effect=RuntimeError('quota')):
            with self.assertRaisesRegex(RuntimeError, 'quota'):
                p.audit_signals('原稿', empty(), '2026/09/10')

    def test_independent_audit_can_remove_bad_initial_sell(self):
        initial = empty(); initial['sell'] = [trade('today')]
        final = empty(); final['sell'] = [trade()]
        with patch.object(p, 'call_gemini', return_value=json.dumps(final)):
            result = p.audit_signals(trade()['evidence'][0], initial, '2026/09/10')
        self.assertEqual(result['sell'][0]['when'], 'yesterday')

    def test_missing_category_fails(self):
        with self.assertRaises(ValueError): p.validate_evidence({}, '原文', '2026/09/10')

    def test_uncertain_is_not_silently_made_watch(self):
        sig = empty(); sig['uncertain'] = [{'name':'萬在','evidence':['萬在這一檔股票現在怎麼辦。']}]
        p.validate_evidence(sig, sig['uncertain'][0]['evidence'][0], '2026/09/10')
        self.assertFalse(sig['watch_watch']);self.assertEqual(len(sig['uncertain']),1)

    def test_old_cache_cannot_make_prev_today(self):
        sig = empty(); sig['sell'] = [trade('prev_trading_day')]
        with patch.object(p,'_prev_trading_day',return_value='2026/08/01'):
            p.apply_when_and_seq(None,sig,'2026/09/10')
        self.assertFalse(sig['sell']);self.assertEqual(sig['history'][0]['when'],'unknown')

    def test_explicit_august_date(self):
        sig = empty(); r=trade('date');r.update(event_date='2026/08/25',time_evidence='8月25號買加折',name='加折', evidence=['8月25號買加折，1580以下。']);sig['buy']=[r]
        p.validate_evidence(sig, r['evidence'][0], '2026/09/10')
        p.apply_when_and_seq(None,sig,'2026/09/10')
        self.assertEqual(r['_date'],'2026/08/25')

    def test_two_different_days_not_merged(self):
        sig=empty();a=trade();b=trade('today');sig['sell']=[a,b]
        p.merge_duplicates(sig);self.assertEqual(len(sig['sell']),2)

    def test_complete_code_map_preserves_wanhai(self):
        with patch.object(p,'get_code_map',return_value={'2615':'萬海','4543':'萬在'}):
            self.assertEqual(p.resolve_code('萬海','')[0],'2615')

    def test_partial_code_map_does_not_turn_wanhai_into_wanzai(self):
        with patch.object(p,'get_code_map',return_value={'4543':'萬在'}),patch.object(p,'_CODE_MAP_FULL',False):
            self.assertEqual(p.resolve_code('萬海','')[0],p.UNRESOLVED)

    def test_article_table_cannot_relabel_yesterday_as_today(self):
        sig=empty();r=trade();r['_date']='2026/09/09';sig['sell']=[r]
        article='① 文章標題\n④ 會員操作紀錄與持股明細\n錯誤當日力積電\n⑤ 分析師操作邏輯與教學重點\n教學\n'
        result=p.enforce_article_records(article,sig,'2026/09/10')
        self.assertNotIn('錯誤當日',result)
        self.assertIn('本支影片未說明當日具體買賣紀錄',result)
        self.assertIn('2026/09/09',result)

    def test_malformed_article_not_published(self):
        result=p.enforce_article_records('只有標題',empty(),'2026/09/10')
        self.assertTrue(result.startswith('文章標題：'))
        for heading in ('① 盤勢總覽','② 會員操作紀錄','③ 分析師操作邏輯'):self.assertIn(heading,result)
        for gone in ('④','⑤','⑥','基本資訊','風險揭露'):self.assertNotIn(gone,result)
        self.assertIn('本支影片未說明當日具體買賣紀錄',result)

    def test_refresh_passes_the_actual_date_and_requires_all_steps(self):
        ping=Mock(text=json.dumps({'features':['refresh-step','evidence-v2','dailyk-safe-chunks'],'steps':['tracker','perf']}))
        ok=Mock(text=json.dumps({'ok':True,'done':True,'result':'完成'}))
        with patch.object(p,'APPS_SCRIPT_URL','https://example.invalid'),patch.object(p,'ADMIN_KEY','test-only'),patch.object(p.requests,'get',side_effect=[ping,ok,ok]) as get:
            result=p.maybe_refresh_site(only=['tracker','perf'],force=True,date_str='2026/09/10')
        self.assertTrue(result['ok'])
        self.assertEqual(get.call_args_list[-1].kwargs['params']['date'],'2026/09/10')

    def test_tracker_failure_does_not_snapshot_performance(self):
        ping=Mock(text=json.dumps({'features':['refresh-step','evidence-v2','dailyk-safe-chunks'],'steps':['tracker','perf']}))
        bad=Mock(text=json.dumps({'ok':False,'error':'tracker failed'}))
        with patch.object(p,'APPS_SCRIPT_URL','https://example.invalid'),patch.object(p,'ADMIN_KEY','test-only'),patch.object(p.requests,'get',side_effect=[ping,bad]) as get:
            result=p.maybe_refresh_site(only=['tracker','perf'],force=True,date_str='2026/09/10')
        self.assertFalse(result['ok']);self.assertEqual(get.call_count,2)

if __name__=='__main__': unittest.main()
