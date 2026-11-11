"""2026/11/13 週檢討（11/11 提前執行）：紀錄上未出場的部位當天有談到卻漏收（裁示一-2、11/06 Q3）。

本週案例（原句取自 11/09、11/10 原稿，前後加無關的填充句）：
- 11/09、11/10 台積電：紀錄上 08/05 買入未出場、會員持股列未斷，今天講「2,280 以下你就可以買台積電」
  「2280 以下不用去賣你的台積電」。流程與單檔覆核都列觀望注意，問卷三輪只有缺答（11/09：缺答／缺答／觀望注意；
  11/10：缺答×3）→ 待確認，整檔不公開。規則一：紀錄上未出場、流程是持股或觀望注意、可用票只有持股或觀望注意、
  其餘是缺答 → 會員持股（缺答不是反對）。
- 11/10 力智：紀錄上 10/29 買入未出場，原文只念「這一支叫做6719」，沒念股名；未出場部位的補候選只用股名與同音寫法找段落，
  整檔沒有送裁決。規則二：未出場部位原文用「叫做／代號／這一支」引出代號時，也補一筆候選送裁決（仍由問卷定分類）。

反例：沒有紀錄上的部位、原文否認持有、有一張不碰／排除票、流程分類是不碰、今天明講賣出本股；
代號緊貼價位單位或買賣動詞（「6719元」「跌到6719」）、沒有引出語的數字、成本／缺口後面的數字。
"""
import contextlib
import io
import os
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
with contextlib.redirect_stdout(io.StringIO()):
    import pipeline as p  # noqa: E402

FILLER_A = '好，我們先看一下今天的天氣，台北有一點下雨，大家出門記得帶傘。'
FILLER_B = '來，喝口水，我們等一下再講下一段，今天的節目比較長一點。'
# 11/09 原稿原句
TSMC_1109 = (FILLER_A + '來，一檔一檔看。現在看台積電。台積電，我也沒有很誠實的跟你們講，我的堅持就是2,280以下，你就可以買台積電。'
             '張正一句話都沒有改過，2,280以下。' + FILLER_B)
# 11/10 原稿原句（手動稿，沒有標點）
TSMC_1110 = (FILLER_A + '看台積電張正說2280以下是安全買點還沒有到2280反正就是2280以下不用去賣你的台積電' + FILLER_B)
LIZHI_1110 = (FILLER_A + '如果你要買IC設計去注意你看這一支股票最近有沒有已經站回薊限了？這個叫做電源管理所以你要去注意'
              '這一支叫做6719例來你看它多低你看它多低買股票買這一個位置要買這個位置不要去追高啊' + FILLER_B)


def entry(**kw):
    base = {'name': '台積電', 'current': 'watch_watch', 'cls': 'watch_watch', 'open': True, 'votes': [], 'answer': {}}
    base.update(kw)
    return base


class OpenPositionMissingVotes(unittest.TestCase):
    zones = [TSMC_1109]

    def held(self, e, usable, transcript=TSMC_1109):
        return p.open_position_missing_votes_holding(e, usable, self.zones, transcript, ['台積電', '2330'])

    def test_1109_one_watch_vote_and_two_missing_is_holding(self):
        self.assertIn('會員持股', self.held(entry(late='watch_watch'), ['watch_watch']))

    def test_1110_three_missing_answers_is_holding(self):
        self.assertTrue(self.held(entry(late='watch_watch'), [], TSMC_1110))

    def test_holding_and_watch_votes_count_as_one_group(self):
        self.assertTrue(self.held(entry(current='holdings', cls='holdings'), ['holdings']))

    def test_no_record_position_stays_uncertain(self):
        self.assertEqual(self.held(entry(open=False), ['watch_watch']), '')

    def test_denied_holding_stays_uncertain(self):
        self.assertEqual(self.held(entry(not_held='原文明講沒有持有：台積電我沒有'), []), '')

    def test_any_avoid_or_hidden_vote_blocks(self):
        self.assertEqual(self.held(entry(), ['watch_watch', 'watch_avoid']), '')
        self.assertEqual(self.held(entry(), ['ignored']), '')
        self.assertEqual(self.held(entry(), ['history']), '')

    def test_avoid_or_hidden_flow_class_is_not_turned_into_holding(self):
        self.assertEqual(self.held(entry(current='watch_avoid', cls='watch_avoid'), []), '')
        self.assertEqual(self.held(entry(current='ignored', cls='ignored'), []), '')

    def test_trade_candidate_is_not_settled_here(self):
        self.assertEqual(self.held(entry(trade_check='sell'), []), '')

    def test_today_sell_sentence_blocks(self):
        raw = FILLER_A + '我今天早上賣台積電，2,400塊全部賣掉了。' + FILLER_B
        self.assertEqual(self.held(entry(), [], raw), '')

    def test_too_short_own_passage_blocks(self):
        self.assertEqual(p.open_position_missing_votes_holding(entry(), [], ['台積電'], TSMC_1109, ['台積電']), '')


class AdjudicationIntegration(unittest.TestCase):
    """走 _adjudicate_classes 的實際路徑：問卷三輪缺答時，紀錄上未出場→會員持股；沒有部位→維持待確認。"""

    def run_day(self, answers, opened, category='watch_watch', raw=TSMC_1110):
        sig = {category: [{'name': '台積電', 'code': '2330', 'reason': '候選', '_date': '2026/11/10'}]}
        with ExitStack() as stack:
            for name, value in {'_CODE_MAP': {'2330': '台積電'},
                                '_verdict_known': lambda *a, **k: ({}, set(), {'2330': '台積電'} if opened else {}),
                                '_verdict_cache': lambda *a, **k: {}, '_own_segments': lambda *a, **k: [],
                                '_verdict_mark': lambda *a, **k: ([{'text': raw}], raw),
                                '_audit_direct_verdicts': lambda *a, **k: [], '_ask_verdicts': lambda *a, **k: answers,
                                'budget_left': lambda: 9999, 'GEMINI_KEYS': ['offline']}.items():
                stack.enter_context(patch.object(p, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return p._adjudicate_classes(None, sig, raw, '2026/11/10', {}, {}, 'on')

    def test_open_position_with_only_missing_answers_becomes_holding(self):
        out = self.run_day({}, opened=True)
        self.assertEqual(out['_verdicts']['2330']['cls'], 'holdings')
        self.assertEqual(out['_verdicts']['2330']['tier'], '裁決')

    def test_without_record_position_missing_answers_stay_uncertain(self):
        out = self.run_day({}, opened=False)
        self.assertEqual(out['_verdicts']['2330']['cls'], 'uncertain')

    def test_open_position_with_an_avoid_vote_is_not_forced_to_holding(self):
        raw = FILLER_A + '台積電現在不要買，台積電還會跌，我不會碰台積電。' + FILLER_B
        answer = {'about_itself': True, 'now': 'prohibit', 'tone': 'bearish', 'entry_for_new': 'prohibit',
                  'quotes': {'stance': ['台積電現在不要買，台積電還會跌，我不會碰台積電。']}}
        out = self.run_day({'2330': answer}, opened=True, raw=raw)
        self.assertNotEqual(out['_verdicts']['2330']['cls'], 'holdings')


class SpokenCodeProbe(unittest.TestCase):
    def test_1110_code_introduced_by_this_one_is_called(self):
        self.assertEqual(p.open_position_code_mention('6719', LIZHI_1110), '6719')

    def test_code_with_name_intro_variants(self):
        self.assertEqual(p.open_position_code_mention('6719', FILLER_A + '代號6719，電源管理IC。' + FILLER_B), '6719')
        self.assertEqual(p.open_position_code_mention('6719', FILLER_A + '這一支股票就是6719。' + FILLER_B), '6719')

    def test_price_like_numbers_are_not_code_mentions(self):
        for raw in ('這一支叫做6719元的那個價位', '跌到6719就買', '這一支股票成本6719', '大盤這一支就是6719點',
                    '叫做2330以下就買', '缺口叫做6719的位置是缺口'):
            self.assertEqual(p.open_position_code_mention('6719' if '6719' in raw else '2330', FILLER_A + raw + FILLER_B), '', raw)

    def test_bare_number_without_intro_is_not_a_mention(self):
        self.assertEqual(p.open_position_code_mention('6719', FILLER_A + '今天成交量6719億元，指數漲6719。' + FILLER_B), '')
        self.assertEqual(p.open_position_code_mention('6719', FILLER_A + '我說67190那個是別的。' + FILLER_B), '')

    def test_non_code_is_ignored(self):
        self.assertEqual(p.open_position_code_mention('00981A', LIZHI_1110), '')

    def test_open_position_probe_adds_candidate_for_code_only_mention(self):
        sig = {'watch_watch': [{'name': '台積電', 'code': '2330', 'reason': '候選', '_date': '2026/11/10'}]}
        seen = {}

        def segs(row, *a, **k):
            seen.setdefault(row.get('code'), list(row.get('aliases') or []))
            return [LIZHI_1110] if '6719' in (row.get('aliases') or []) else []

        with ExitStack() as stack:
            for name, value in {'_CODE_MAP': {'2330': '台積電', '6719': '力智'},
                                '_verdict_known': lambda *a, **k: ({}, set(), {'6719': '力智'}),
                                '_verdict_today_rows': lambda *a, **k: {}, '_own_segments': segs}.items():
                stack.enter_context(patch.object(p, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            out = p._adjudicate_classes(None, sig, LIZHI_1110, '2026/11/10', {}, {}, 'on')
        probes = [r for r in out.get('ignored', []) if r.get('code') == '6719']
        self.assertEqual(len(probes), 1)
        self.assertEqual(probes[0]['exclusion_reason'], 'open_position_recheck')
        self.assertEqual(probes[0]['aliases'], ['6719'])

    def test_probe_does_not_add_code_alias_when_name_is_spoken(self):
        raw = FILLER_A + '力智這一支叫做6719，你看它多低。' + FILLER_B
        sig = {'watch_watch': []}
        seen = {}

        def segs(row, *a, **k):
            seen[row.get('code')] = list(row.get('aliases') or [])
            return [raw]

        with ExitStack() as stack:
            for name, value in {'_CODE_MAP': {'6719': '力智'},
                                '_verdict_known': lambda *a, **k: ({}, set(), {'6719': '力智'}),
                                '_verdict_today_rows': lambda *a, **k: {}, '_own_segments': segs}.items():
                stack.enter_context(patch.object(p, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            p._adjudicate_classes(None, sig, raw, '2026/11/10', {}, {}, 'on')
        self.assertEqual(seen.get('6719'), [])


if __name__ == '__main__':
    unittest.main()
