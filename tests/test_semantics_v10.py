"""離線重現 9/10 的跨股說明、ETF 主詞與語氣錯誤，不把錯誤郵件當標準答案。"""
import copy
import unittest
from unittest.mock import patch
from test_quality import p, empty


class NarrativeTests(unittest.TestCase):
    def test_speaker_alias_code_and_evidence(self):
        sig=empty()
        row={'name':'宇瞻','code':'8271','reason':'講者以雨沾2379為例，張正表示正在整理。',
             'evidence':['張正以雨沾2379為例']}
        sig['watch_avoid']=[row]
        evidence=copy.deepcopy(row['evidence'])
        p.naturalize_signal_reasons(sig)
        self.assertIn('宇瞻（8271）',row['reason'])
        self.assertNotIn('講者',row['reason']);self.assertNotIn('张正',row['reason'])
        self.assertNotIn('張正',row['reason']);self.assertEqual(row['evidence'],evidence)

    def test_prices_are_not_replaced_with_ticker(self):
        for note in ('嘉澤1580以下買進','嘉澤1580元附近買進','嘉澤1580附近布局','嘉澤1580買進'):
            self.assertIn('1580',p.public_narrative(note,{'name':'嘉澤','code':'3533'}))

    def test_repeated_churn_statement(self):
        text='回顧外資過往操作呈現先買再賣或先賣再買；外資操作呈現先買再賣或先賣再買。'
        self.assertEqual(p.public_narrative(text).count('先買再賣'),1)

    def test_official_rival_alias_not_rewritten(self):
        s=empty();s['watch_watch']=[{'name':'宇瞻','code':'8271','aliases':['瑞昱']}]
        self.assertIn('瑞昱',p.public_narrative('瑞昱是另一檔',signals=s))


class ToneTests(unittest.TestCase):
    def test_neutral_and_negative(self):
        for text in ('外資操作呈現先買再賣或先賣再買','原地橫盤整理','不看好，後續不會漲',
                     '雖然是好股票，但不要碰','昨天漲今天跌，追高容易套牢'):
            self.assertEqual(p.watch_tone(text),'watch_avoid',text)

    def test_conditional_buy_does_not_become_bearish(self):
        for text in ('不要追高，900以下是買點','拉回到季線會再買回來',
                     '不是不要碰，要等900以下買進','是好股票，等補完缺口再站回去'):
            self.assertEqual(p.watch_tone(text),'watch_watch',text)

    def test_final_tones_preserve_holdings(self):
        s=empty();s['watch_watch']=[{'name':'華邦電','reason':'外資先買再賣或先賣再買'}]
        s['holdings']=[{'name':'祥碩','note':'目前持有，今天跌30塊'}]
        p.normalize_watch_tones(s)
        self.assertFalse(s['watch_watch']);self.assertEqual(s['watch_avoid'][0]['name'],'華邦電')
        self.assertEqual(s['holdings'][0]['name'],'祥碩')

    def test_current_holding_not_lost_among_candidates(self):
        raw='新代是候選名單。這一檔本來就是我會員買的股票，8月25號1580以下買加折，低檔買好等他整理完。木德以後要買。'
        s=empty();s['watch_watch']=[{'name':'嘉澤','code':'3533','aliases':['加折'],
                                    'reason':'會員在1580以下買進，等待整理。','_date':'2026/09/10'}]
        p.preserve_explicit_holdings(s,raw)
        self.assertFalse(s['watch_watch']);self.assertEqual(s['holdings'][0]['name'],'嘉澤')
        self.assertFalse(s['buy']);self.assertEqual(s['holdings'][0]['_date'],'2026/09/10')

    def test_old_trade_not_inferred_as_current_holding(self):
        s=empty();s['watch_watch']=[{'name':'嘉澤','code':'3533','reason':'先前會員買過'}]
        p.preserve_explicit_holdings(s,'加折以前會員買過，後來全部賣掉，現在等整理。')
        self.assertFalse(s['holdings'])


class EntityTests(unittest.TestCase):
    def test_etf_actor_does_not_own_stock_buy_price(self):
        raw='00981A昨天出金城。出金城這一檔等900以下再買。'
        s=empty();etf={'name':'主動統一台股增長','code':'00981A','price':'900以下',
            'price_subject':'勤誠','price_evidence':'00981A昨天出金城。出金城這一檔等900以下再買。',
            'reason':'ETF反覆換股。等900以下再買。','view':'等900以下再買'}
        stock={'name':'勤誠','code':'8210','price':'900以下','price_subject':'出金城',
               'price_evidence':'出金城這一檔等900以下再買。','reason':'900以下再買進。'}
        s['watch_watch']=[etf,stock]
        self.assertTrue(p.entity_claim_gaps(s,raw))
        p.sanitize_entity_claims(s,raw);p.normalize_watch_tones(s)
        self.assertEqual(etf['price'],'未說明');self.assertNotIn('900',etf['reason'])
        self.assertEqual(stock['price'],'900以下');self.assertIn(stock,s['watch_watch'])
        self.assertIn(etf,s['watch_avoid']);self.assertTrue(s['_quality_requires_review'])

    def test_explicit_etf_own_price_survives(self):
        s=empty();r={'name':'主動統一台股增長','code':'00981A','price':'20以下',
            'price_evidence':'00981A跌到20以下才買','reason':'20以下才買'}
        s['watch_watch']=[r];p.sanitize_entity_claims(s,r['price_evidence'])
        self.assertEqual(r['price'],'20以下')

    def test_foreign_trade_note_removed_but_real_holding_kept(self):
        raw='昨天四星KY跌100多塊快200塊我買進，今天四星KY漲30幾塊。手中還有想碩，普位現在跌兩塊，享碩跌30塊。'
        s=empty();r={'name':'譜瑞-KY','code':'4966','aliases':['普位'],
             'note':'昨天大跌100多塊快200塊買進，今天漲30幾塊。','evidence':['普位現在跌兩塊']}
        s['holdings']=[r,{'name':'世芯-KY','code':'3661','aliases':['四星KY'],
                           'note':'昨天跌100多塊快200塊買進，今天漲30幾塊。'}]
        p.sanitize_entity_claims(s,raw);p.naturalize_signal_reasons(s)
        self.assertEqual(len(s['holdings']),2)
        self.assertNotIn('200',r['note']);self.assertIn('跌兩塊',r['note'])
        self.assertIn('200',s['holdings'][1]['note'])
        self.assertEqual(r['evidence'],['普位現在跌兩塊'])

    def test_missing_named_candidate_triggers_review_even_with_spaces(self):
        self.assertTrue(p.entity_claim_gaps(empty(),'四 星KY 我昨天買進'))


class LessonTests(unittest.TestCase):
    def test_same_rhythm_with_different_titles_is_not_two_lessons(self):
        a='操作心態與節奏：不要追高殺低，大漲時賣股、下跌時買進。'
        b='震盪盘勢的買賣節奏：不追高、不殺低，大漲時賣出，回檔時買進。'
        self.assertEqual(p._lesson_key(a),p._lesson_key(b))
        s=empty();s['market']=[{'kind':'view','text':t,'_evidence_verified':True} for t in (a,b)]
        with patch.object(p,'call_gemini',side_effect=AssertionError('short source must not call')):
            p.ensure_min_lessons(s,'短原文','2026/09/10')
        self.assertEqual(len(s['market']),1)


if __name__=='__main__':unittest.main()
