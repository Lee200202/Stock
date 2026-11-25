"""2026/11/23–11/25 每日複核結果寫進流程（v50，管理者 2026/11/25）的離線契約。

原句取自當天保存的原稿（11/23 6iuy…、11/24 QDb3…、11/25 MANUAL-20261125）；規則只看句型與結構化答案，
另用不屬於任何一天的「甲公司」驗反例。模型會不會照這樣答，由真實模型重播驗收（見報告），不在這裡。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as p  # noqa: E402


def answer(raw, **fields):
    a = dict(about_itself=True, independent_analysis=False, content_role='independent_analysis',
             buy_today=False, sell_today=False, holding_now=False, past_trade=False,
             entry_for_new='none', now='none', tone='neutral', holder_advice='none', selection_intent='neutral',
             view_basis='speaker_judgment', forward_view='none', quotes={'stance': [raw], 'analysis': []})
    a.update(fields)
    return a


def klass(name, raw, **fields):
    return p.verdict_class(answer(raw, **fields), p._ev_norm(raw), [name])[0]


class F1_ForwardNeedsAForwardQuote(unittest.TestCase):
    """11/23 聯發科、聯電：問卷答「向前會漲」，引句只是政府基金買了誰。誰在買是行情，不是向前說法。"""
    GOV = '政府基金這一波護盤三大買進標的，台積電買最多，第二名是聯發科。'
    UMC = '聯電竟然消化掉融資。早上聯電還有拉漲停，所以聯電已經把這個融資消化掉了。'

    def test_buying_by_others_is_not_a_forward_statement(self):
        self.assertNotIn(klass('聯發科', self.GOV, forward_view='rise', view_basis='price_move_only'), ('watch_watch',))
        self.assertNotIn(klass('聯電', self.UMC, forward_view='rise', tone='neutral', view_basis='price_move_only'),
                         ('watch_watch',))

    def test_real_forward_statements_still_count(self):
        raw = '甲公司現在1,600多，我可以很肯定的跟你講，甲公司後面還會來，來2,000。'
        self.assertEqual(klass('甲公司', raw, forward_view='rise'), 'watch_watch')
        rule = '甲公司的邏輯就是800到700區間，只要來到800你賣就對了，來到700你買就對了。'
        self.assertEqual(klass('甲公司', rule, forward_view='buy_sell_rule'), 'watch_watch')
        adr = '甲公司ADR漲了5%，台股今天只漲2%，所以甲公司明天還有高點。'
        self.assertEqual(klass('甲公司', adr, forward_view='rise'), 'watch_watch')

    def test_prompt_says_buyers_are_price_facts(self):
        self.assertIn('法人、政府基金、外資、投信、ETF買了或賣了多少是行情', p.ADJUDICATE_SYSTEM)
        self.assertGreaterEqual(int(p.VERDICT_VERSION.split('-')[0][1:]), 51)

    def test_flow_only_quotes_do_not_carry_a_positive_entry_answer(self):
        """v51 G2：11/23 重播兩次都答 now=conditional／buy_ok、偏多，引句只有政府基金買誰、融資消化。"""
        self.assertEqual(klass('聯發科', self.GOV, now='conditional', tone='bullish', forward_view='rise'), 'ignored')
        pool = [self.UMC, '今天拉記憶體、聯電、力積電，啊至於被動元件啦、細晶圓啦。']
        a = answer(self.UMC, now='buy_ok', tone='bullish', forward_view='rise', quotes={'stance': pool, 'analysis': []})
        self.assertEqual(p.verdict_class(a, p._ev_norm(''.join(pool)), ['聯電'])[0], 'ignored')
        # 引句裡有講者對本股的看法就照舊
        raw = '外資連買三天甲公司，甲公司技術面轉強，等回到季線可以買。'
        self.assertEqual(klass('甲公司', raw, entry_for_new='wait_condition', tone='bullish'), 'watch_watch')
        raw = '政府基金護盤買甲公司，但甲公司我不碰。'
        self.assertEqual(klass('甲公司', raw, entry_for_new='prohibit', tone='bearish'), 'watch_avoid')

    def test_only_up_this_much_compared_is_forward(self):
        """v51 G3：11/24 聯電「聯電漲19%…今天漲，聯電也才10%而已」，下一句才說明天都有高點（與日月光並列）。"""
        raw = '聯電漲19%。好，所以今天漲完，台積電也才10%而已。今天漲，聯電也才10%而已。'
        self.assertTrue(p.forward_quote_ok(raw, 'rise'))
        self.assertEqual(klass('聯電', raw, forward_view='rise', content_role='comparison_only', tone='neutral'),
                         'watch_watch')


class F2_UpThenDownIsAChaseLesson(unittest.TestCase):
    """11/25 聯發科、鴻海（管理者裁示觀望不碰）：「昨天漲停…今天跌停」被答成只報漲跌，還把觀眾的買答成過去買賣。"""
    MTK = '昨天聯發科漲停365塊漲365今天聯發科跌停跌405昨天去追的人兩天加起來還是賠。'
    HH = '昨天鴻海漲停對不對？昨天鴻海漲停漲20塊半今天鴻海跌停，你昨天買的人怎麼辦？'

    def test_price_only_answer_on_an_up_then_down_quote_is_avoid(self):
        self.assertEqual(klass('聯發科', self.MTK, view_basis='price_move_only', tone='neutral'), 'watch_avoid')
        a = answer(self.HH, view_basis='price_move_only', past_trade=True,
                   quotes={'stance': [self.HH], 'analysis': [], 'past_trade': ['你昨天買的人怎麼辦？']})
        self.assertEqual(p.verdict_class(a, p._ev_norm(self.HH), ['鴻海'])[0], 'watch_avoid')

    def test_down_only_or_down_then_up_is_not_listed(self):
        self.assertEqual(klass('甲公司', '你看甲公司早上有沒有跌停？', view_basis='price_move_only'), 'ignored')
        raw = 'ETF在甲公司跌停6天以後才出清，今天甲公司漲停。'
        self.assertEqual(klass('甲公司', raw, view_basis='price_move_only'), 'ignored')

    def test_positive_view_or_own_trade_is_not_overridden(self):
        raw = '甲公司是這一波對的股票，昨天甲公司漲停漲10塊，今天甲公司跌5塊，跌下來就是買點。'
        self.assertEqual(klass('甲公司', raw, tone='bullish', entry_for_new='wait_condition'), 'watch_watch')
        own = '我昨天賣甲公司，昨天甲公司漲停漲10塊，今天甲公司跌停。'
        a = answer(own, view_basis='price_move_only', past_trade=True,
                   quotes={'stance': [own], 'analysis': [], 'past_trade': [own]})
        self.assertNotEqual(p.verdict_class(a, p._ev_norm(own), ['甲公司'])[0], 'watch_avoid')


class G1_ChaseNoteKeptByTheLastGate(unittest.TestCase):
    """v51 G1：11/25 重播，聯發科、鴻海問卷已判觀望不碰，說明「昨天漲停…今天跌停，兩天加起來還是賠」被發布前關卡當成只有行情；
    10/29 大立光「昨日漲335元後今日跌300元」（答案檔觀望不碰）也一樣。"""

    def gate(self, cat, name, note):
        sig = {cat: [{'code': '9999', 'name': name, 'reason': note}], '_verdicts': {}}
        with mock.patch.object(p, 'note_decision'):
            out = p.gate_watch_conclusions(sig)
        return [r['name'] for r in out.get(cat, [])]

    def test_up_then_down_avoid_note_is_kept(self):
        self.assertEqual(self.gate('watch_avoid', '鴻海', '鴻海昨天漲停漲20.5元、今天跌停跌22.5元，兩天加起來其實是下跌的。'), ['鴻海'])
        self.assertEqual(self.gate('watch_avoid', '大立光', '大立光昨日漲335元後今日跌300元。'), ['大立光'])

    def test_other_price_only_notes_still_hidden(self):
        self.assertEqual(self.gate('watch_avoid', '甲公司', '甲公司今天跌停。'), [])
        self.assertEqual(self.gate('watch_watch', '甲公司', '甲公司昨天漲停、今天跌停。'), [])   # 只有觀望不碰才是追高教材


class F3_OpenPositionNamedOnlyInAList(unittest.TestCase):
    """管理者 2026/11/25：紀錄上持有、只在多檔名單裡被點名、沒有本股自己的話，當天不列（部位不關閉）。"""
    TSMC = ('大環境小個股還記不記得我說你這時候眼中你們在想我要買台積電台達電連電還是聯發科比較好，'
            '你們是不是都一直在想這件事情。其實說真的在今天來講都一樣啊都一樣跌。')
    PSMC = '否則，我昨天怎麼會講在戶記憶體，還有所謂的成熟製程的聯電立積電。這些都漲停啊，漲停就漲停啊。'

    def test_list_only_vs_named_with_its_own_words(self):
        with mock.patch.object(p, '_stance_names', return_value=['聯發科', '台積電', '台達電', '連電', '聯電', '立積電']):
            self.assertTrue(p.named_only_in_lists(self.TSMC, ['台積電']))
            self.assertFalse(p.named_only_in_lists(self.PSMC, ['立積電', '力積電']))

    def test_named_holding_rule_respects_the_list_guard(self):
        entry = {'open': True, 'round_answers': [
            {'class': 'holdings', 'answer': {'about_itself': True}},
            {'class': 'ignored', 'answer': {'about_itself': True, 'content_role': 'independent_analysis'}}]}
        with mock.patch.object(p, '_stance_names', return_value=['聯發科', '台積電', '台達電', '連電']):
            self.assertEqual(p.open_position_named_holding(entry, self.TSMC, ['台積電']), '')
        with mock.patch.object(p, '_stance_names', return_value=['聯電', '立積電']):
            self.assertTrue(p.open_position_named_holding(entry, self.PSMC, ['立積電', '力積電']))


class F4_F5_F6(unittest.TestCase):
    def test_manager_confirmed_aliases(self):
        self.assertEqual(p.CONFIRMED_NAMES.get('紅海'), ('2317', '鴻海'))
        self.assertEqual(p.CONFIRMED_NAMES.get('華南京'), ('2880', '華南金'))

    def test_filler_conclusions_are_stripped(self):
        self.assertEqual(p.strip_filler_clauses('聯電外資買超且政府護盤點名拉抬，具備正面觀察條件。'), '聯電外資買超且政府護盤點名拉抬。')
        self.assertNotIn('潛力', p.strip_filler_clauses('世芯-KY基本面具備潛力，等匯率升值再買。'))
        self.assertIn('2500', p.strip_filler_clauses('甲公司具備上漲條件到2500元。'))      # 帶數字的子句照舊不動

    def test_watch_notes_without_a_conclusion_get_the_second_pass(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("['本股的結論或條件（原稿講到的買、不買、不碰、等某價、本股風險）']", src)


if __name__ == '__main__':
    unittest.main()
