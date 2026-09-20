# -*- coding: utf-8 -*-
"""v17：2026/09/14 郵件的分類錯誤、標題格式、說明星號，以及 v16 可能造成工單中斷的地方。

原句取自 9/14 原始逐字稿（語音稿每個字之間有空白，這裡保留那個樣子）。
"""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from test_quality import p, empty

APU = ('我 問 你 啦 我 愛 普 介 紹 幾 塊 錢 ？ 273 塊 愛 普 愛 普 所 有 人 聽 我 講 了 一 個 月 的 愛 普 73 塊 現 在 愛 普 幾 塊 ？ '
       '給 你 們 我 在 哪 裡 推 薦 愛 普 的 這 邊 ？ 我 在 這 邊 推 薦 愛 普 的 所 以 投 資 朋 友 你 們 都 要 等 長 上 來 才 想 買 啊 '
       '當 時 的 愛 普 200 多 塊 沒 人 要 買 啊 我 我 買 了 還 被 罵 那 個 什 麼 爛 股 票 啊 ')
HANTANG = ('來 我 還 操 作 一 支 股 票 賺 五 成 2404 大 家 記 不 記 得 我 們 買 在 這 邊 1000 塊 以 下 買 漢 糖 920 我 漢 堂 賣 這 一 天 1360 '
           '這 樣 子 1360 賺 了 460 塊 我 從 沒 有 在 提 過 漢 唐 這 一 支 股 票 對 不 對 ？ ')
EVAL_MAP = {'6531': '愛普*', '2404': '漢唐', '6533': '晶心科', '8390': '金益鼎', '3131': '弘塑', '2354': '鴻準',
            '2327': '國巨*', '6643': 'M31', '3008': '大立光', '5534': '長虹', '8183': '精星', '2330': '台積電'}


def row(name, reason, code='', **kw):
    r = {'name': name, 'code': code, 'reason': reason, 'evidence': kw.pop('evidence', [reason]), '_date': '2026/09/14'}
    r.update(kw)
    return r


class PastRecapTests(unittest.TestCase):
    def test_apu_old_recommendation_is_excluded(self):
        s = empty()
        s['watch_watch'] = [row('愛普*', '愛普在273元時曾被推薦，具備成長潛力，是值得留意的標的。', '6531',
                                aliases=['愛普'], evidence=[APU])]
        p.exclude_past_recommendations(s, APU)
        self.assertFalse(s['watch_watch'], '「現在愛普幾塊？」是問句，不是現在的推薦')
        self.assertEqual(s['ignored'][0]['exclusion_reason'], 'past_recommendation_only')

    def test_hantang_finished_trade_is_excluded(self):
        s = empty()
        s['watch_avoid'] = [row('漢唐', '漢唐過去曾在920元附近操作並在1360元獲利。', '2404',
                                aliases=['漢糖', '漢堂'], evidence=[HANTANG])]
        p.exclude_past_recommendations(s, HANTANG)
        self.assertFalse(s['watch_avoid'])

    def test_real_current_instruction_still_kept(self):
        quote = '以前推薦瑞昱100元沒人買？現在看好瑞昱，等拉回再買進。'
        s = empty(); s['watch_watch'] = [row('瑞昱', quote, '2379')]
        p.exclude_past_recommendations(s, quote)
        self.assertEqual(len(s['watch_watch']), 1)
        quote2 = '我現在不推薦華新。當時推薦華新20塊沒人要買。'
        s = empty(); s['watch_watch'] = [row('華新', quote2, '1605')]
        p.exclude_past_recommendations(s, quote2)
        self.assertFalse(s['watch_watch'], '「現在不推薦」不是現在的推薦')


class ToneTests(unittest.TestCase):
    def normalize(self, cat, reason):
        s = empty(); s[cat] = [row('X', reason)]
        with redirect_stdout(io.StringIO()):
            p.normalize_watch_tones(s)
        return 'watch_watch' if s['watch_watch'] else 'watch_avoid'

    def test_global_wafers_negative_example_goes_to_avoid(self):
        reason = '點名矽晶圓等族群過去一個月買進的人全部賠錢，並明確表示不准買，應避開相關風險，不適合盲目進場。'
        self.assertEqual(self.normalize('watch_watch', reason), 'watch_avoid')
        self.assertEqual(self.normalize('watch_watch', '總比你這三個月去買環球晶好吧'), 'watch_avoid')

    def test_focaltech_positive_group_mention_goes_to_watch(self):
        reason = '驅動IC族群中基期較低且獲利良好的公司，上半年的EPS達8塊多，全年預估17元，業績很好不需擔憂，值得納入觀察清單。'
        self.assertEqual(self.normalize('watch_avoid', reason), 'watch_watch')
        self.assertEqual(self.normalize('watch_watch', reason), 'watch_watch')

    def test_not_yet_buyable_waiting_for_etf_is_avoid(self):
        # 管理者規則（2026/09/14）：明講現在還不能買、要等 ETF 賣完，當下結論是不進場 → 觀望不碰
        reason = '勤誠如預期跌破900元，但00981A尚未賣完，因此還不能馬上買進，需等ETF賣不下去時再尋求買點。'
        self.assertEqual(p.watch_tone(reason), 'watch_avoid')
        self.assertEqual(p.watch_tone('這一支股票今天破900你可以買嗎還不行，現在還不能買，等00981A賣完才買'), 'watch_avoid')
        self.assertEqual(p.watch_tone('900以下是買點'), 'watch_watch')
        self.assertEqual(p.watch_tone('雖然是好股票，但不要碰'), 'watch_avoid')
        self.assertEqual(p.watch_tone('不准買，被動元件絕對不要碰'), 'watch_avoid')

    def test_neutral_still_avoid(self):
        self.assertEqual(self.normalize('watch_watch', '原地橫盤整理'), 'watch_avoid')
        self.assertEqual(self.normalize('watch_watch', '只是持續觀察'), 'watch_avoid')


class NameTests(unittest.TestCase):
    def test_model_name_not_in_source_falls_back_to_heard_name(self):
        transcript = '權力金第三支股票叫精心科，是最便宜的。' * 30
        s = empty(); s['watch_watch'] = [row('金益鼎', '權利金族群中較便宜的標的', aliases=['精心科'])]
        with patch.object(p, '_CODE_MAP', dict(EVAL_MAP)), patch.object(p, 'get_code_map', return_value=dict(EVAL_MAP)), \
             redirect_stdout(io.StringIO()) as out:
            p.resolve_signals(s, transcript)
        r = s['watch_watch'][0]
        self.assertNotEqual(r['code'], '8390', '原文沒有金益鼎，不能對到 8390')
        self.assertEqual(r.get('未採用模型名稱'), '金益鼎')
        self.assertIn('改以原字「精心科」比對', out.getvalue())

    def test_confirmed_aliases_from_0914(self):
        for heard, code in (('金星科', '6533'), ('精星科', '6533'), ('利望', '3529'), ('蹲態', '3545'),
                            ('秦城', '8210'), ('漢堂', '2404'), ('玉金光', '3406'), ('環球金', '6488'), ('國具', '2327'), ('玉龍', '2201')):
            self.assertEqual(p.CONFIRMED_NAMES[heard][0], code, heard)
        self.assertEqual(p.CONFIRMED_INDUSTRY['長虹棒'], '長紅K棒')

    def test_yulon_homophones_resolve_to_2201_but_official_yulong_stays(self):
        full = {'2201': '裕隆', '2233': '宇隆', '9941': '裕融', '2330': '台積電'}
        for heard in ('玉龍', '浴龍', '御隆', '雨龍', '宇龍'):
            self.assertEqual(p.CONFIRMED_NAMES[heard], ('2201', '裕隆'), heard)
        for heard in ('域龍', '與龍', '於龍', '魚龍', '余龍', '于龍'):
            self.assertNotIn(heard, p.CONFIRMED_NAMES, heard + ' 會出現在一般詞或人名裡，不列入逐字替換')
        with patch.object(p, '_CODE_MAP', dict(full)), patch.object(p, '_CODE_MAP_FULL', True),              patch.object(p, 'get_code_map', return_value=dict(full)):
            for heard in ('魚龍', '于隆', '玉瓏', '昱隆'):
                self.assertEqual(p.resolve_code(heard, '')[0], '2201', heard)
            self.assertEqual(p.resolve_code('宇隆', '')[0], '2233', '原文明寫宇隆仍是 2233')
            self.assertEqual(p.resolve_code('裕融', '')[0], '9941')
            self.assertNotEqual(p.resolve_code('玉融', '')[0], '2201', '讀音不同（rong）不套用')
        r = {'name': '裕隆', 'code': '2201', '原始語音名稱': '浴龍'}
        s = empty(); s['watch_watch'] = [r]
        self.assertEqual(p.public_narrative('浴龍資產價值非常高，玉龍是存股標的。', r, s), '裕隆資產價值非常高，裕隆是存股標的。')
        self.assertEqual(p.public_narrative('區域龍頭與龍頭股位於龍潭。', {}, empty()), '區域龍頭與龍頭股位於龍潭。')

    def test_misnamed_company_in_reason_is_repaired(self):
        transcript = '紅 準 今 天 跌 哦 我 特 別 講 哦 紅 蠢 今 天 跌 哦 你 看 最 近 外 資 買 他 ' * 20
        s = empty(); s['watch_watch'] = [row('鴻準', '弘塑近期雖然下跌，但外資持續買進。', '2354', aliases=['紅準'])]
        with patch.object(p, '_CODE_MAP', dict(EVAL_MAP)), redirect_stdout(io.StringIO()):
            p.repair_misnamed_subjects(s, transcript)
        self.assertEqual(s['watch_watch'][0]['reason'], '鴻準近期雖然下跌，但外資持續買進。')

    def test_unrelated_company_in_reason_is_not_rewritten(self):
        transcript = '紅 準 今 天 跌 哦 ' * 60
        s = empty(); s['watch_watch'] = [row('鴻準', '跟台積電一樣外資持續買進。', '2354', aliases=['紅準'])]
        with patch.object(p, '_CODE_MAP', dict(EVAL_MAP)), redirect_stdout(io.StringIO()):
            p.repair_misnamed_subjects(s, transcript)
        self.assertEqual(s['watch_watch'][0]['reason'], '跟台積電一樣外資持續買進。')


class InventoryTests(unittest.TestCase):
    def test_spaced_asr_transcript_is_inventoried_without_substring_noise(self):
        raw = ('大 立 光 今 天 跌 停 。 我 的 會 員 是 買 第 一 根 長 虹 棒 那 一 天 。 第 三 支 股 票 叫 精 星 科 。 '
               '四 星 KY 我 買 3800 多 。 M31 是 權 力 金 。')
        with patch.object(p, '_CODE_MAP', dict(EVAL_MAP)):
            names = [i['name'] for i in p.source_inventory(p.source_segments(raw))]
            data = json.loads(p.assessment_payload('2026/09/14', p.source_segments(raw)))
        self.assertIn('大立光', names); self.assertIn('M31', names); self.assertIn('精星科', names)
        self.assertNotIn('長虹', names, '長虹棒是長紅K棒')
        self.assertNotIn('精星', names, '精星科是晶心科的聽錯寫法')
        self.assertIn('四星KY', data['confirmed_names'], '確認名稱要用去空白的原文比對')

    def test_budget_default_is_150000(self):
        with patch.dict(p.os.environ, {'GEMINI_ASSESSMENT_TOKEN_BUDGET': ''}):
            self.assertEqual(p.assessment_token_budget(), 150000)
        with patch.dict(p.os.environ, {'GEMINI_ASSESSMENT_TOKEN_BUDGET': '120000'}):
            self.assertEqual(p.assessment_token_budget(), 120000)


class PresentationTests(unittest.TestCase):
    def test_asterisk_never_multiplies(self):
        r = {'name': '國巨*', 'code': '2327', 'aliases': ['國巨', '國具'], '原始語音名稱': '國具'}
        s = empty(); s['watch_avoid'] = [r]
        once = p.public_narrative('國巨近期走勢詭異，越想解套國具越危險。', r, s)
        twice = p.public_narrative(once, r, s)
        self.assertEqual(once, '國巨近期走勢詭異，越想解套國巨越危險。')
        self.assertEqual(twice, once)
        r2 = {'name': '世芯-KY', 'code': '3661'}
        s2 = empty(); s2['holdings'] = [dict(r2, 原始語音名稱='四星KY')]
        t = p.public_narrative(p.public_narrative('四星KY營收創新高', r2, s2), r2, s2)
        self.assertEqual(t, '世芯-KY營收創新高。')

    def test_title_is_168_style(self):
        s = empty()
        s['market'] = [{'kind': 'event', 'text': '兩個大人在打架，我們坐在旁邊看，打完再進場。',
                        'evidence': ['兩 個 大 人 在 打 架 那 我 們 坐 在 旁 邊'], 'headline': '兩個大人在打架 我們坐在旁邊看就好！',
                        '_evidence_verified': True}]
        self.assertEqual(p.article_title(s), '兩個大人在打架 我們坐在旁邊看就好！')
        s['market'][0]['headline'] = '張震：兩個大人在打架 我們坐在旁邊看就好'
        self.assertEqual(p.article_title(s), '兩個大人在打架 我們坐在旁邊看就好！', '模型自己加的「張震：」要拿掉（2026/09/15 起標題不加前綴）')
        s['market'][0]['headline'] = '外資目標價上看9999元，必漲！'
        self.assertFalse(p.article_title(s).startswith('張震'))
        self.assertNotIn('9999', p.article_title(s))


class NoInterruptionTests(unittest.TestCase):
    def call(self, responses):
        sent = []

        def post(*a, **kw):
            sent.append(copy.deepcopy(kw['json']['generationConfig']))
            return responses.pop(0)
        ok = Mock(status_code=200, json=lambda: {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': '{}'}]}}]})
        responses.append(ok)
        with patch.object(p, 'require_gemini_key'), patch.object(p, 'GEMINI_KEYS', ['a', 'b']), \
             patch.object(p, '_QUOTA_STOP', {'daily': False}), patch.object(p, 'current_gemini_model', return_value='gemini-3.5-flash-lite'), \
             patch.object(p, 'current_gemini_key', return_value='offline'), patch.object(p, 'throttle_gemini'), \
             patch.object(p, 'budget_left', return_value=1000), patch.object(p.time, 'sleep'), \
             patch.dict(p._SCHEMA_STATE, {'on': True, 'why': ''}), \
             patch.object(p, 'rotate_gemini_key', side_effect=AssertionError('schema 被拒不能換鑰匙')) , \
             patch.object(p.requests, 'post', side_effect=post), redirect_stdout(io.StringIO()):
            text = p.call_gemini('system', 'input', want_json=True, tag='assess-json-1')
            state = dict(p._SCHEMA_STATE)
        return text, sent, state

    def test_schema_rejection_retries_without_schema_and_keeps_key(self):
        bad = Mock(status_code=400, text=json.dumps({'error': {'code': 400, 'message':
                   'Invalid JSON payload received. Unknown name "responseJsonSchema" at \'generation_config\': Cannot find field.'}}))
        text, sent, state = self.call([bad])
        self.assertEqual(text, '{}')
        self.assertIn('responseJsonSchema', sent[0])
        self.assertNotIn('responseJsonSchema', sent[1])
        self.assertFalse(state['on'])

    def test_extract_retries_empty_model_output_but_not_quota(self):
        good = json.dumps({c: [] for c in p.SIGNAL_CATEGORIES + ('history', 'uncertain', 'ignored', 'market')})
        with patch.object(p, 'call_gemini', side_effect=[RuntimeError('Gemini 回傳空內容（assess-json-1）'), good]) as ai, \
             patch.object(p.time, 'sleep'), redirect_stdout(io.StringIO()):
            p.extract_context_json('原文' * 300, '2026/09/14')
        self.assertEqual(ai.call_count, 2)
        with patch.object(p, 'call_gemini', side_effect=p.RateLimited('今日額度用完', daily=True)) as ai, \
             patch.object(p.time, 'sleep'), redirect_stdout(io.StringIO()):
            with self.assertRaises(p.RateLimited):
                p.extract_context_json('原文' * 300, '2026/09/14')
        self.assertEqual(ai.call_count, 1)


if __name__ == '__main__':
    unittest.main()
