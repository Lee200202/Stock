"""2026/11/18 個股判定修正（雲端複核診斷 A–E）的離線契約。

11/18 網站多收了華新科（只拿來示範找最大量K線、算融資斷頭）、創意（只報跌停、跌停打開），
台達電的說明多了原稿沒有的「具備觀察價值、等待回穩與支撐確認後再行佈局」。
這裡用當天原稿的原句當反例，驗證：
  A 問卷「本股看法的依據」事實題 → 程式決定（沒有進場看法、依據不是講者判斷 → 不公開，不看語氣）
  B 擷取／覆核提示詞與 publication_gaps 和裁示一致（11/03 E1、11/06 Q5、11/12、11/03 問卷）
  C 觀望列發布前最後一關；fill_empty_notes 不替觀望補
  D 套話擋句；擷取說明比照補充說明逐句核對
  E 複核比對的「個股說明疑點」
規則都只看句型與結構化答案，不看股名；同一條規則另用不屬於任何一天的「甲公司」驗反例。
模型會不會照這樣填，由真實模型重播驗收（見報告），不在這裡。
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))
sys.path.insert(0, str(ROOT / 'scripts' / 'weekly_review'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as p  # noqa: E402

# 11/18 原稿原句（取自雲端複核第三輪計畫 commit 36037229b 的引句，逐字對應原稿 sha256 89a02bb5…）；
# 計畫檔之後會換成新一輪的修正，所以這裡寫死，不再讀計畫檔
Q = {"華新科": ["好，再來， 所以今天是不是在斷 被動元件的融資，來，再給你看 2492 和，啊，華新科。", "那這一根 K 線最大量，這個以上一堆的人買， 這一根 K 線融資 斷頭。"], "創意": ["所以我昨天看到很多股票跌停板的時候， FBF 跌停，創意跌停，什麼跌停，那個就是賣不掉。", "今天的創意、三根跌停也打開了，也清得差不多了。"], "台達電": ["台達電快破1,000了，你知道嗎？", "上次也是最低點1,000，你還記不記得？", "張總，我很想買，說真的，不只只有你很想，我也很想。", "那有一隻股票，我要建議你們，多看幾天，就是台達電。", "這一根是台達電的量，1020最低點。", "早上台達電有跌破了。", "所以台達電有去跌破這個，除非它跌破之後馬上收回去。", "否則，不能收盤跌破1020。", "你收盤跌破1020，代表這這邊全部套牢。"]}
CHUANGYI = Q['創意']            # 只報跌停、跌停打開
WALSIN = Q['華新科']            # 只拿來示範最大量K線、融資斷頭
DELTA = Q['台達電']             # 想買、多看幾天、收盤不能跌破1020
YAGEO = '我昨天幫你們算國巨，我說國巨的前一波最低點是456.5，融資斷頭價是328，國巨我每一段都說不能買，你不要去碰它。'
JAZZ = '加澤，比如說，我現在沒有加澤了。加澤現在幾塊？1,600多，你在賣什麼意思的？我可以很肯定，加澤後面還會再來到2,000。'
IGS = '新象的邏輯就是800到700區間，來到800你賣，來到700你買，我們會員做過很多次了。'

# 11/18 正式站最後（複核修正後）的說明：該留下的四檔
SITE_NOTES = {
    '國巨': ('watch_avoid', '用國巨示範融資斷頭價：前一波最低點456.5元，換算這三個月買進的融資斷頭價是328元，來到這裡籌碼才會清乾淨；'
             '現在約331元、今天跌停價325元，殺到低檔量已經出來。說國巨從1,000元一路跌到現在300多元，他每一段都說不能買；'
             '被動元件、ABF、矽晶圓、銅箔基板、PCB是前陣子散戶最多的股票，風險就在這裡。'),
    '鈊象': ('watch_avoid', '鈊象的操作邏輯是800到700元區間：來到800元就賣、來到700元就買，說會員照這個區間操作過很多次，'
             '每一次都是800元賣、700多元撿。依這個邏輯，價位接近800元時是賣點、不是買點，不追高。'),
    '台達電': ('watch_watch', '台達電股價快破1,000元，上次的最低點也在1,000元。說他也很想買，但建議再多看幾天：最低點1020元那根是爆量K線，'
              '早上台達電已經跌破，除非跌破後馬上收回去，否則收盤不能跌破1020元；收盤跌破代表這一區買的人全部套牢。'),
    '嘉澤': ('watch_watch', '嘉澤目前未持有，股價在1600多元整理，預期後面會再度來到2000元。'),
}
# 11/18 主流程原本公開的說明（日誌「書面化」那一行）：該拿掉的兩檔
PIPELINE_NOTES = {'創意': '創意遭遇跌停與融資斷頭賣壓。', '華新科': '華新科在最大量K線以上有許多人買進。'}
INVENTED = '若未破底則具備觀察價值，等待回穩與支撐確認後再行佈局。'


def answer(raw, **fields):
    a = dict(about_itself=True, independent_analysis=False, content_role='independent_analysis',
             buy_today=False, sell_today=False, holding_now=False, past_trade=False,
             entry_for_new='none', now='none', tone='neutral', holder_advice='none', selection_intent='neutral',
             quotes={'stance': [raw], 'analysis': []})
    a.update(fields)
    return a


def klass(name, raw, **fields):
    return p.verdict_class(answer(raw, **fields), p._ev_norm(raw), [name])[0]


class A_ViewBasisQuestion(unittest.TestCase):
    """問卷事實題：沒有進場看法，而且依據不是講者的判斷 → 一律不公開，不看語氣。"""

    def test_schema_and_prompt_ask_the_fact_not_the_class(self):
        props = p.verdict_response_schema()['properties']['verdicts']['items']['properties']
        self.assertEqual(props['view_basis']['enum'], ['speaker_judgment', 'price_move_only', 'example_only'])
        self.assertIn('view_basis', p.verdict_response_schema()['properties']['verdicts']['items']['required'])
        self.assertIn('view_basis是本股看法的依據，只答事實，不是分類', p.ADJUDICATE_SYSTEM)
        for name in ('華新科', '創意', '台達電', '鈊象'):
            self.assertNotIn(name, p.ADJUDICATE_SYSTEM)

    def test_verdict_version_moved_up_for_the_class_change(self):
        m = re.match(r'v(\d+)-(\d{8})', p.VERDICT_VERSION)
        self.assertTrue(m and int(m.group(1)) >= 44 and m.group(2) >= '20261118', p.VERDICT_VERSION)

    def test_1118_price_only_and_example_only_are_not_published_whatever_the_tone(self):
        raw_cy, raw_ws = ''.join(CHUANGYI), ''.join(WALSIN)
        for tone in ('bearish', 'neutral', 'bullish'):
            self.assertEqual(klass('創意', raw_cy, tone=tone, view_basis='price_move_only'), 'ignored', tone)
            self.assertEqual(klass('華新科', raw_ws, tone=tone, view_basis='example_only'), 'ignored', tone)

    def test_the_1118_votes_were_avoid_only_because_of_the_tone(self):
        # 11/18 日誌：兩檔問卷都答 now=none、tone=bearish → 觀望不碰。沒有依據欄的舊答案照舊（快取已因版號失效）
        self.assertEqual(klass('創意', ''.join(CHUANGYI), tone='bearish'), 'watch_avoid')
        self.assertEqual(klass('華新科', ''.join(WALSIN), tone='bearish'), 'watch_avoid')

    def test_1118_kept_stocks_keep_their_class(self):
        self.assertEqual(klass('台達電', ''.join(DELTA), entry_for_new='wait_condition', now='conditional',
                               view_basis='speaker_judgment', independent_analysis=True,
                               quotes={'stance': [''.join(DELTA)], 'analysis': [''.join(DELTA)]}), 'watch_watch')
        self.assertEqual(klass('國巨', YAGEO, entry_for_new='prohibit', now='prohibit', tone='bearish',
                               view_basis='speaker_judgment'), 'watch_avoid')
        self.assertEqual(klass('嘉澤', JAZZ, tone='bullish', view_basis='speaker_judgment', past_trade=False,
                               independent_analysis=True, quotes={'stance': [JAZZ], 'analysis': [JAZZ]}), 'watch_watch')

    def test_a_stated_entry_view_is_not_dropped_by_the_basis(self):
        # 依據欄答錯也不能把明講的進場看法吃掉：只有「沒有進場看法」時才看依據
        raw = '甲公司昨天跌停，今天又跌停，甲公司你不要碰'
        self.assertEqual(klass('甲公司', raw, entry_for_new='prohibit', now='prohibit', tone='bearish',
                               view_basis='price_move_only'), 'watch_avoid')
        self.assertEqual(klass('甲公司', raw, holder_advice='hold_rebound', view_basis='example_only'), 'watch_watch')
        self.assertEqual(klass('甲公司', raw, selection_intent='positive_candidate', tone='bullish',
                               view_basis='price_move_only'), 'watch_watch')

    def test_speaker_judgment_without_entry_still_follows_the_tone(self):
        raw = '甲公司這個位置我看還會再跌，套牢的人很多'
        self.assertEqual(klass('甲公司', raw, tone='bearish', view_basis='speaker_judgment'), 'watch_avoid')

    def test_trades_and_holdings_are_decided_before_the_basis(self):
        raw = '我今天買甲公司，買在100塊'
        a = answer(raw, buy_today=True, view_basis='example_only', quotes={'stance': [], 'analysis': [], 'buy_today': [raw]})
        self.assertEqual(p.verdict_class(a, p._ev_norm(raw), ['甲公司'])[0], 'buy')


class A_ExtractionStance(unittest.TestCase):
    """擷取的 watch_stance 同一題：category_from_stance 由程式決定。"""

    def test_basis_decides_when_there_is_no_entry_view(self):
        f = lambda **kw: p.category_from_stance(dict(subject_ok=True, **kw))
        for tone in ('bearish', 'bullish', 'neutral'):
            self.assertEqual(f(now='none', tone=tone, basis='price_move_only'), 'ignored')
            self.assertEqual(f(now='none', tone=tone, basis='example_only'), 'ignored')
        self.assertEqual(f(now='none', tone='bearish', basis='speaker_judgment'), 'watch_avoid')
        self.assertEqual(f(now='none', tone='bullish', basis='speaker_judgment'), 'watch_watch')
        self.assertEqual(f(now='none', tone='bearish'), 'watch_avoid')               # 舊回覆沒有依據：照舊
        self.assertEqual(f(now='none', tone='bearish', basis='亂填'), 'watch_avoid')
        self.assertEqual(f(now='prohibit', tone='bearish', basis='example_only'), 'watch_avoid')
        self.assertEqual(f(now='conditional', tone='neutral', basis='price_move_only'), 'watch_watch')

    def test_prompt_asks_for_basis(self):
        self.assertIn('"basis": "speaker_judgment|price_move_only|example_only"', p.EXTRACT_SYSTEM)
        self.assertIn('exclusion_reason=price_or_example_only', p.EXTRACT_SYSTEM)
        self.assertNotIn('拿本檔當追高受傷、操作失誤的例子算 bearish', p.EXTRACT_SYSTEM)


class B_PromptsMatchRulings(unittest.TestCase):
    OLD = ('watch_avoid 從寬', '也屬本日觀察範圍，不能因此排除', '它跌停、連跌幾天、追高的人套牢、他說以後也不買）才列 watch_avoid',
           '正向選股池仍需等止穩、等低接或突破條件而尚不能進場，也列watch_avoid', '玉晶光跟跌）→ watch_avoid',
           '語氣偏負面或當風險示範者依從寬標準改列 watch_avoid', '牧德高檔利多追買受傷')

    def test_conflicting_lines_are_gone_from_extract_audit_and_review(self):
        for text in (p.EXTRACT_SYSTEM, p.AUDIT_SYSTEM, p.EXCLUDED_REVIEW_SYSTEM):
            for old in self.OLD:
                self.assertNotIn(old, text)
        self.assertNotIn('拿它當風險、追高受傷、散戶被坑、法人大賣、財務操作的例子', p.EXCLUDED_REVIEW_SYSTEM)

    def test_e1_is_cancelled_a_chase_example_is_avoid(self):
        # 管理者 2026/11/18 取消 11/03 問卷 E1：追高反面教材回到觀望不碰；只報跌停（Q5）、只當示範（11/12）照舊不列
        for text in (p.EXTRACT_SYSTEM, p.AUDIT_SYSTEM, p.EXCLUDED_REVIEW_SYSTEM, p.ADJUDICATE_SYSTEM):
            self.assertNotIn('2026/11/03 E1）', text)
            self.assertNotIn('2026/11/03 管理者問卷 E1）：about_itself=false', text)
        self.assertIn('屬本股當下風險與禁買判斷', p.ADJUDICATE_SYSTEM)
        self.assertIn('2026/11/18 管理者取消 11/03 問卷 E1', p.ADJUDICATE_SYSTEM)
        self.assertIn('拿本股當追高反面教材（昨天漲停今天跌停、不要追利多）是本股的風險，列觀望不碰', p.EXTRACT_SYSTEM)
        for name in ('玉晶光', '精測', '大立光'):
            self.assertNotIn(name, p.ADJUDICATE_SYSTEM)
        raw = '昨天甲公司漲停，打開報紙全部甲公司的利多。今天甲公司跌停。為什麼我昨天跟你們講，不要買'
        self.assertEqual(klass('甲公司', raw, entry_for_new='prohibit', now='prohibit', tone='bearish',
                               view_basis='speaker_judgment', independent_analysis=True,
                               quotes={'stance': [raw], 'analysis': [raw]}), 'watch_avoid')
        # 只報跌停（Q5）仍不列
        self.assertEqual(klass('甲公司', '今天甲公司跌停打開了', tone='bearish', view_basis='price_move_only'), 'ignored')

    def test_new_lines_follow_q5_e1_and_the_1103_questionnaire(self):
        self.assertIn('只報本股漲跌、跌停、斷頭、法人一買一賣，或只拿它示範算法', p.EXTRACT_SYSTEM)
        self.assertIn('依 2026/11/03 管理者問卷列 watch_watch', p.EXTRACT_SYSTEM)
        self.assertIn('只報它跌停、連跌幾天，沒有判斷、條件或建議，仍放 ignored', p.EXTRACT_SYSTEM)
        self.assertIn('只報漲跌、跌停或只當示範例子而沒有本股自己判斷者改列 ignored', p.AUDIT_SYSTEM)

    def test_publication_gaps_no_longer_asks_to_pull_back_price_only_names(self):
        signals = {'ignored': [{'name': '華新科', 'code': '2492'}, {'name': '創意', 'code': '3443'}]}
        saved, p._CODE_MAP = p._CODE_MAP, {'2492': '華新科', '3443': '創意'}       # 離線：不載官方代號表
        try:
            gaps = [g for g in p.publication_gaps(signals, '短稿') if g.startswith('排除覆核')]
        finally:
            p._CODE_MAP = saved
        self.assertTrue(gaps)
        for g in gaps:
            self.assertNotIn('亦列觀望', g)
            self.assertIn('有講者對本股自己的判斷、條件或建議才收', g)
            self.assertIn('只報漲跌、法人買賣或只當示範例子不列', g)


def _row(name, code, cat, note):
    row = {'name': name, 'code': code, 'evidence': []}
    row['note' if cat == 'holdings' else 'reason'] = note
    return row


class C_LastGate(unittest.TestCase):
    def test_1118_counter_examples_are_removed_and_kept_stocks_stay(self):
        signals = {'watch_avoid': [_row('創意', '3443', 'watch_avoid', PIPELINE_NOTES['創意']),
                                   _row('華新科', '2492', 'watch_avoid', PIPELINE_NOTES['華新科'])],
                   'watch_watch': [], 'ignored': []}
        codes = {'國巨': '2327', '鈊象': '3293', '台達電': '2308', '嘉澤': '3533'}
        for name, (cat, note) in SITE_NOTES.items():
            signals[cat].append(_row(name, codes[name], cat, note))
        out = p.gate_watch_conclusions(signals)
        public = {r['name'] for c in ('watch_watch', 'watch_avoid') for r in out[c]}
        self.assertEqual(public, {'國巨', '鈊象', '台達電', '嘉澤'})
        hidden = {r['name']: r for r in out['ignored']}
        self.assertEqual(set(hidden), {'創意', '華新科'})
        for r in hidden.values():
            self.assertEqual(r['exclusion_reason'], 'watch_note_no_conclusion')
            self.assertEqual(r['_publication_gate']['category'], 'watch_avoid')
            self.assertTrue(r['_publication_gate']['note'])           # 稽核留原說明

    def test_patterns_not_names(self):
        keep = ['甲公司要等站回季線才買。', '甲公司跌破80元就不要碰。', '甲公司收盤不能跌破52元，跌破代表上面全部套牢。',
                '甲公司在季線獲得支撐，技術面已經背離。', '甲公司目前不考慮買進。', '甲公司預期後面會再到120元。',
                '甲公司漲多了，現在追高風險很大。', '甲公司看好會再上去。', '甲公司來到45元你賣、來到40元你買。']
        # 回測 10/27–11/16 已發布說明找到的同義寫法（推薦與否、不參與、不必追、名單、反壓、會補、沒事）
        keep += ['甲公司未被列為推薦買進標的。', '甲公司不需要再追價買進。', '甲公司明確表示不予注意與買進。', '甲公司現階段仍不參與。',
                 '甲公司列為優先注意的口袋名單。', '甲公司季線是這一波的第一個反壓。', '甲公司上面的缺口一定會補。',
                 '甲公司在這邊整理完全沒事。', '甲公司持有者建議抱緊。', '甲公司不要盲目追買。', '電機類股可以好好去注意甲公司。',
                 # 11/18 重播：發布前關卡時說明的數字還是中文寫法
                 '甲公司在二千塊的時候賣了三次，現在一千六百多塊不用急著賣，後面還會再來二千塊。',
                 '甲公司先前已分批獲利了結。目前股價拉回至1600多元，後續可望重回2000元。',
                 '甲公司目前股價在1600多元，後續向上挑戰2000元。']
        drop = ['甲公司今天跌停。', '甲公司昨天跌停，今天跌停打開，融資清得差不多。', '甲公司回來碰季線。',
                '甲公司在最大量K線以上有一堆人買。', '外資今天買超甲公司3000張。', '甲公司遭遇融資斷頭賣壓。', '',
                # 說明只有漲跌（是否公開另看問卷的進場看法；11/18 取消 E1 後，追高教材由問卷答不碰）
                '甲公司昨日大漲335元後今日隨即大跌300元。', '甲公司昨日漲停且報紙充滿利多，今日卻直接跌停。',
                # 模型套話不算本股結論
                '甲公司值得留意。', '甲公司走勢值得持續關注。']
        for t in keep:
            self.assertTrue(p.watch_note_conclusion(t), t)
        for t in drop:
            self.assertFalse(p.watch_note_conclusion(t), t)

    def test_weak_note_with_a_stated_entry_view_stays_public_and_is_flagged(self):
        # 11/12 重播：台達電問卷是「等條件」、國巨是「不買」，說明卻只剩型態描述——保留公開、記提醒
        vote = lambda **kw: {'answer': {'watch_watch': {'answer': dict(dict(entry_for_new='none', now='none', holder_advice='none',
                                                                              selection_intent='neutral'), **kw)}}}
        signals = {'watch_watch': [_row('甲公司', '1111', 'watch_watch', '甲公司呈現島狀反轉型態，相較於乙公司更有優勢。'),
                                   _row('丙公司', '3333', 'watch_watch', '丙公司今天跌停打開，一堆人買。')],
                   'watch_avoid': [], 'ignored': [],
                   '_verdicts': {'1111': vote(entry_for_new='wait_condition', now='conditional'),
                                 '3333': vote(tone='bearish')}}           # 11/18 華新科那一票：沒有進場看法
        out = p.gate_watch_conclusions(signals)
        self.assertEqual([r['name'] for r in out['watch_watch']], ['甲公司'])
        self.assertEqual([r['name'] for r in out['ignored']], ['丙公司'])
        self.assertTrue(any(g.startswith('甲公司說明偏短：') for g in out['_repair_gaps']))
        self.assertEqual(p.needs_review_gaps(out['_repair_gaps']), [])      # 篇幅類提醒，不擋發布

    def test_unnamed_note_needs_a_stated_view(self):
        # 11/18 重播：「安安心心」被認成安心，說明只有一句不點名的勸告
        vote = {'answer': {'watch_watch': {'answer': {'now': 'none', 'tone': 'bullish', 'entry_for_new': 'none'}}}}
        signals = {'watch_watch': [_row('安心', '1259', 'watch_watch', '遇到這種行情不用亂殺，沒有最壞的情況出現。'),
                                   _row('甲公司', '1111', 'watch_watch', '季線向上時回測季線是買點，現階段不宜殺低。')],
                   'watch_avoid': [], 'ignored': [],
                   '_verdicts': {'1259': vote, '1111': {'answer': {'watch_watch': {'answer': {'holder_advice': 'hold_rebound'}}}}}}
        out = p.gate_watch_conclusions(signals)
        self.assertEqual([r['name'] for r in out['watch_watch']], ['甲公司'])     # 不點名但問卷有持有者建議：照常
        self.assertEqual(out['ignored'][0]['_publication_gate']['reason'], '說明沒有點到本股，問卷也沒有進場看法')

    def test_extraction_stance_is_used_when_there_is_no_vote(self):
        row = dict(_row('甲公司', '1111', 'watch_avoid', '甲公司今天又跌停。'),
                   watch_stance={'subject_ok': True, 'now': 'prohibit', 'tone': 'bearish'}, _watch_stance_ok=True)
        self.assertTrue(p.stated_entry_view(row, 'watch_avoid', {}))
        self.assertFalse(p.stated_entry_view(dict(row, _watch_stance_ok=False), 'watch_avoid', {}))
        self.assertFalse(p.stated_entry_view(dict(row, watch_stance={'now': 'none', 'tone': 'bearish'}), 'watch_avoid', {}))

    def test_manual_rows_and_other_categories_are_untouched(self):
        signals = {'watch_avoid': [dict(_row('甲公司', '1111', 'watch_avoid', '甲公司今天跌停。'), _manual_note_kept=True),
                                   _row('乙公司', '2222', 'watch_avoid', '乙公司今天跌停。')],
                   'watch_watch': [], 'holdings': [_row('丙公司', '3333', 'holdings', '丙公司今天跌停。')], 'ignored': [],
                   '_verdicts': {'2222': {'tier': '人工裁決', 'cls': 'watch_avoid'}}}
        out = p.gate_watch_conclusions(signals)
        self.assertEqual([r['name'] for r in out['watch_avoid']], ['甲公司', '乙公司'])
        self.assertEqual(len(out['holdings']), 1)
        self.assertEqual(out['ignored'], [])

    def test_gate_runs_after_the_last_rewrite_and_before_quarantine_and_audit(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        a = src.index('    signals = polish_public_texts(signals, TX["audit"], date_str)\n    signals = gate_watch_conclusions(signals)')
        b = src.index("    signals = quarantine_public_rows(signals, TX['audit'])", a)
        c = src.index("    save_evidence_audit(ss, video['id'], date_str, TX['audit'], signals)", b)
        self.assertTrue(a < b < c)


class C_FillOnlyPositions(unittest.TestCase):
    def test_watch_rows_are_not_filled_positions_are(self):
        tx = ('甲公司今天跌停，乙公司我續抱，乙公司成本50元。' * 3)
        signals = {'watch_avoid': [{'name': '甲公司', 'code': '1111', 'reason': '', 'evidence': ['甲公司今天跌停，']}],
                   'holdings': [{'name': '乙公司', 'code': '2222', 'note': '', 'evidence': ['乙公司我續抱，乙公司成本50元。']}],
                   'watch_watch': [], 'buy': [], 'sell': []}
        out = p.fill_empty_notes(signals, tx)
        self.assertEqual(out['watch_avoid'][0]['reason'], '')
        self.assertTrue(out['holdings'][0]['note'])


class D_FillerAndSentenceCheck(unittest.TestCase):
    def test_invented_conclusion_phrases_are_stripped_numbers_kept(self):
        out = p.strip_filler_clauses('台達電股價快破1,000元，' + INVENTED)
        for bad in ('具備觀察價值', '再行佈局', '等待回穩與支撐確認'):
            self.assertNotIn(bad, out)
        self.assertIn('1,000元', out)
        # 帶數字的子句不動（既有規則）；原稿講的「等回穩」本身不是套話
        self.assertIn('等它回穩再說', p.strip_filler_clauses('甲公司等它回穩再說。'))
        # 11/18 重播第 1 次又寫出「待…站穩後再行評估佈局時點」
        self.assertEqual(p.strip_filler_clauses('甲公司建議多看幾天，待盤勢收腳站穩後再行評估佈局時點。'), '甲公司建議多看幾天。')

    def _delta_signals(self, note):
        tx = '。'.join(['今天先講大盤，大盤今天殺融資'] * 6 + DELTA + ['好，接下來講別的話題，大盤明天看量'] * 6) + '。'
        row = {'name': '台達電', 'code': '2308', 'reason': note, 'evidence': DELTA[:4]}
        return {'watch_watch': [row], 'watch_avoid': [], 'holdings': [], 'buy': [], 'sell': []}, tx

    def test_extracted_note_gets_the_same_sentence_check_as_context_notes(self):
        signals, tx = self._delta_signals('台達電股價快破1,000元，最低點1020，收盤不能跌破1020元，' + INVENTED)
        out = p.verify_extracted_note_sentences(signals, tx)
        note = out['watch_watch'][0]['reason']
        for bad in ('觀察價值', '回穩', '再行佈局'):
            self.assertNotIn(bad, note)
        self.assertIn('1020', note)

    def test_numbers_and_terms_outside_the_window_are_cues_not_deletions(self):
        # 11/10 重播：切窗漏掉的「季線」、國字念的「七字頭」被誤刪；數字與技術詞只記線索，交語意審閱與價位核對
        signals, tx = self._delta_signals('台達電股價快破1,000元，目標價1500元，MACD翻紅。')
        out = p.verify_extracted_note_sentences(signals, tx)['watch_watch'][0]
        self.assertEqual(out['reason'], '台達電股價快破1,000元，目標價1500元，MACD翻紅。')
        cues = '；'.join(out['_note_source_cues'])
        self.assertIn('1500', cues)
        self.assertIn('MACD', cues)

    def test_supported_note_is_left_alone(self):
        text = '台達電股價快破1,000元，最低點1020元那根是爆量K線，收盤不能跌破1020元。'
        signals, tx = self._delta_signals(text)
        self.assertEqual(p.verify_extracted_note_sentences(signals, tx)['watch_watch'][0]['reason'], text)

    def test_check_runs_before_context_enrichment(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        a = src.index('    signals = verify_extracted_note_sentences(signals, TX["audit"])')
        self.assertLess(a, src.index('    signals = enrich_stock_context(signals, TX["audit"], date_str)', a))


class E_ReviewNoteSuspicions(unittest.TestCase):
    def test_1118_notes_are_flagged_for_review(self):
        import compare_site as cs
        self.assertTrue(cs.note_suspicions(PIPELINE_NOTES['創意']))
        self.assertIn('沒有數字也沒有立場', cs.note_suspicions(PIPELINE_NOTES['華新科']))
        self.assertTrue(any('套話' in w for w in cs.note_suspicions('台達電股價逼近1000元，' + INVENTED)))
        for _cat, note in SITE_NOTES.values():
            self.assertEqual(cs.note_suspicions(note), [], note[:20])

    def test_weekly_auto_check_for_the_new_type(self):
        import week
        row = {'差異類型': '個股說明疑點'}
        self.assertEqual(week.judge(row, [], '')[0], '通過')
        bad = [{'category': 'watch_avoid', 'note': PIPELINE_NOTES['創意']}]
        self.assertEqual(week.judge(row, bad, '')[0], '需人工')
        good = [{'category': 'watch_watch', 'note': SITE_NOTES['台達電'][1]}]
        self.assertEqual(week.judge(row, good, '')[0], '通過')


if __name__ == '__main__':
    unittest.main()
