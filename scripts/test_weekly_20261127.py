"""2026/11/27 週檢討（週五正式排程）：兩條通用規則，都在個股說明的發布前處理，不動分類問卷與提示詞。

規則一（撰稿：套話擋句）：原稿沒講的評語結論子句不留。
- 11/23 聯電「…具備正面觀察條件」、11/25 世芯-KY「未來基本面與法人籌碼面具備潛力」、11/27 群聯「若有拉回可持續關注與布局」，
  原稿都沒有這些話（每日複核三天都判明確錯誤）。
- 依據：裁示紀錄 三「說明要寫成書面文字…不加原稿沒講的套話（10/09、10/28）」；11/18 套話擋句。
- 反例（已發布說明命中回測找到的實質內容）：09/22 聯發科「不代表個股已具備進場條件」、09/21 嘉澤「可持續關注等待賣壓解除後的買點」、
  11/06 力旺「2,000元以下時可持續留意」（帶數字）。

規則二（發布前最後一關）：問卷自己答「本股只是陪襯」（content_role=comparison_only）時，
那份答案的進場欄不能再替「沒有本股結論」的觀望說明作保。
- 11/23 聯發科：只在「政府這一波護盤三大買進標的，台積電買最多，第二名是聯發科」名單裡被點名；
  問卷答 comparison_only 又填 positive_candidate，說明只有「政府基金這一波護盤三大買進標的包含聯發科。」，網站列觀望注意。
- 依據：裁示紀錄 一-4（只描述價格變化、沒有結論不列）、11/06 Q5（族群一起被點名、沒有本股判斷不列）、
  11/18 發布前最後一關（觀望列說明至少要有一句本股的結論或條件）、11/24（法人／ETF 買賣是在描述市場）。
- 反例：問卷答本股獨立分析（independent_analysis）的照舊保留並提醒；陪襯但說明有本股結論的照舊公開；追高教材不受影響。
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
with contextlib.redirect_stdout(io.StringIO()):
    import pipeline as p  # noqa: E402

FILLER_A = '今天天氣不錯，大家早安。'
FILLER_B = '我們等一下再講下一段。'

# 本週原句（每日複核差異明細的原文依據）
MTK_1123_QUOTE = '政府這一波護盤三大買進標的，台積電買最多，第二名是聯發科'
UMC_1123_QUOTE = '早上聯電還有拉漲停，所以聯電已經把這一些融資開始都消化掉'
ALCHIP_1125_QUOTE = '看台幣匯率升值的沒看他有沒有補這個缺口啊'
PHISON_1127_QUOTE = '兩隻股票都好啦，我沒有說哪一隻好，哪一隻不好。'

# 本週網站原本的說明（修正前）
UMC_1123_NOTE = FILLER_A + '聯電融資已開始消化，外資買超且政府護盤點名拉抬，具備正面觀察條件。' + FILLER_B
ALCHIP_1125_NOTE = '世芯-KY被看好會取代創意，未來基本面與法人籌碼面具備潛力。'
PHISON_1127_NOTE = '群聯前3季EPS 325、股價1650，兩檔都好，若有拉回可持續關注與布局。'
MTK_1123_NOTE = '政府基金這一波護盤三大買進標的包含聯發科。'


def _quiet(fn, *a, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


def _row(name, code, note):
    return {'name': name, 'code': code, 'evidence': [], 'reason': note}


def _vote(cat, **kw):
    base = dict(entry_for_new='none', now='none', holder_advice='none', selection_intent='neutral',
                tone='neutral', forward_view='none', view_basis='speaker_judgment', content_role='independent_analysis')
    return {'answer': {cat: {'answer': dict(base, **kw)}}}


class A_FillerClauses(unittest.TestCase):
    def test_week_boilerplate_is_removed(self):
        out = p.strip_filler_clauses(UMC_1123_NOTE)
        self.assertNotIn('具備正面觀察條件', out)
        self.assertIn('外資買超且政府護盤點名拉抬', out)
        self.assertIn(FILLER_A.rstrip('。'), out)
        out = p.strip_filler_clauses(ALCHIP_1125_NOTE)
        self.assertNotIn('具備潛力', out)
        self.assertIn('被看好會取代創意', out)
        out = p.strip_filler_clauses(PHISON_1127_NOTE)
        self.assertNotIn('布局', out)
        self.assertIn('325', out)
        self.assertIn('兩檔都好', out)

    def test_substantive_clauses_stay(self):
        for text in ('偏多的大盤環境不代表個股已具備進場條件。',            # 09/22 聯發科
                     '嘉澤可持續關注等待賣壓解除後的買點。',               # 09/21 嘉澤
                     '力旺在2,000元以下時可持續留意。',                    # 11/06 力旺（帶數字）
                     '屬可持續留意的權值布局選項，季線支撐守住。'):
            self.assertEqual(p.strip_filler_clauses(FILLER_A + text), FILLER_A + text, text)

    def test_public_narrative_applies_it(self):
        out = p.public_narrative(PHISON_1127_NOTE, {'name': '群聯', 'code': '8299'}, {})
        self.assertNotIn('可持續關注與布局', out)
        self.assertIn('325', out)


class B_ComparisonOnlyGate(unittest.TestCase):
    def test_comparison_only_vote_does_not_rescue_a_note_without_conclusion(self):
        # 11/23 聯發科：問卷答陪襯＋positive_candidate，說明只有護盤買進
        self.assertEqual(p.watch_note_conclusion(MTK_1123_NOTE), '')
        signals = {'watch_watch': [_row('聯發科', '2454', MTK_1123_NOTE)], 'watch_avoid': [], 'ignored': [],
                   '_verdicts': {'2454': _vote('watch_watch', selection_intent='positive_candidate', tone='bullish',
                                               forward_view='rise', content_role='comparison_only')}}
        out = _quiet(p.gate_watch_conclusions, signals)
        self.assertEqual(out['watch_watch'], [])
        self.assertEqual([r['code'] for r in out['ignored']], ['2454'])
        self.assertEqual(out['ignored'][0]['exclusion_reason'], 'watch_note_no_conclusion')

    def test_independent_analysis_vote_still_rescues_and_flags(self):
        # 反例：同一句說明，問卷答本股獨立分析＋正向名單——照舊保留公開並記「說明偏短」（11/12 契約）
        signals = {'watch_watch': [_row('聯發科', '2454', MTK_1123_NOTE)], 'watch_avoid': [], 'ignored': [],
                   '_verdicts': {'2454': _vote('watch_watch', selection_intent='positive_candidate', tone='bullish')}}
        out = _quiet(p.gate_watch_conclusions, signals)
        self.assertEqual([r['code'] for r in out['watch_watch']], ['2454'])
        self.assertTrue(any(g.startswith('聯發科說明偏短') for g in out.get('_repair_gaps', [])))

    def test_comparison_only_with_own_conclusion_stays(self):
        # 反例：11/16 環球晶（問卷答陪襯、說明有本股結論「因此不能碰」）照舊公開
        note = '環球晶屬於矽晶圓族群。在國巨跌完後輪到環球晶。因此不能碰。'
        signals = {'watch_watch': [], 'watch_avoid': [_row('環球晶', '6488', note)], 'ignored': [],
                   '_verdicts': {'6488': _vote('watch_avoid', entry_for_new='prohibit', now='prohibit', tone='bearish',
                                               selection_intent='explicit_exclusion', content_role='comparison_only')}}
        out = _quiet(p.gate_watch_conclusions, signals)
        self.assertEqual([r['code'] for r in out['watch_avoid']], ['6488'])
        self.assertEqual(out['ignored'], [])

    def test_chase_lesson_and_manual_rows_are_untouched(self):
        chase = _vote('watch_avoid', entry_for_new='prohibit', view_basis='chase_lesson', content_role='comparison_only')
        self.assertFalse(p.voted_comparison_only({'code': '3008'}, 'watch_avoid', {'3008': chase}))
        self.assertTrue(p.voted_comparison_only({'code': '2454'}, 'watch_watch',
                                                {'2454': _vote('watch_watch', content_role='comparison_only')}))
        self.assertFalse(p.voted_comparison_only({'code': '2454'}, 'watch_watch', {}))
        signals = {'watch_watch': [_row('聯發科', '2454', MTK_1123_NOTE)], 'watch_avoid': [], 'ignored': [],
                   '_verdicts': {'2454': {'tier': '人工裁決', 'answer': {'watch_watch': {'answer': {'content_role': 'comparison_only'}}}}}}
        out = _quiet(p.gate_watch_conclusions, signals)
        self.assertEqual([r['code'] for r in out['watch_watch']], ['2454'])

    def test_quotes_are_this_weeks_source(self):
        # 原句只作紀錄：本股名稱在原句裡、說明的關鍵詞出自原句
        self.assertIn('聯發科', MTK_1123_QUOTE)
        self.assertIn('融資', UMC_1123_QUOTE)
        self.assertIn('缺口', ALCHIP_1125_QUOTE)
        self.assertIn('都好', PHISON_1127_QUOTE)


if __name__ == '__main__':
    unittest.main()
