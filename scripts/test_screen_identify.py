"""直播畫面辨識的純函式與模型包裝（不連 Gemini、不連試算表）。

2026/11/12 晚改版：採用外部 AI 建議（結構化輸出、證據守門、詞庫撤銷、用量紀錄），
大部分案例取自其交付的 test_proposal.py；另加本 repo 的調整（代號對上時允許簡稱／KY 寫法差異、
保留十天歸納的說法清單、新股名在嚴格條件下自動採用）。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE), str(HERE / 'daily_review')]
os.environ.setdefault('GEMINI_API_KEY', 'offline')

import screen_contracts as s  # noqa: E402
import vocab_learn as v  # noqa: E402
from common import LOG_TABS  # noqa: E402
from screen_refs import day_rows, parse_batch  # noqa: E402


def observation(**extra):
    row = dict(second=20, visual_second=20, said='看這一檔的量價', heard='這一檔',
               screen_code='1111', screen_name='甲公司', confidence='清楚', scene='chart',
               target_match='same', speech_kind='deictic', note='左上標題')
    row.update(extra)
    return row


def parse(row, start=0, end=900):
    return s.parse_items(json.dumps(dict(time_origin='video', coverage='complete', items=[row]), ensure_ascii=False), start, end)


class Contract(unittest.TestCase):
    def test_valid(self):
        got = parse(observation())[0]
        self.assertEqual((got['sec'], got['time'], got['code'], got['name']), (20, '0:00:20', '1111', '甲公司'))

    def test_time_is_never_guessed(self):
        # 片段 [900,1800) 回 20 秒：不猜成片段內時間，這一筆丟掉並計數，其他筆照收
        text = json.dumps(dict(time_origin='video', coverage='complete',
                               items=[observation(), observation(second=930, visual_second=930)]), ensure_ascii=False)
        got, dropped = s.parse_report(text, 900, 1800)
        self.assertEqual(([g['sec'] for g in got], dropped), ([930], 1))

    def test_bare_list_and_fence(self):
        got = s.parse_items('```json\n' + json.dumps([observation()], ensure_ascii=False) + '\n```', 0, 900)
        self.assertEqual(got[0]['sec'], 20)

    def test_bad_json_is_failure(self):
        self.assertIsNone(s.parse_items('not json', 0, 900))

    def test_empty_is_success(self):
        self.assertEqual(s.parse_items('{"time_origin":"video","coverage":"complete","items":[]}', 0, 900), [])

    def test_partial_is_failure(self):
        self.assertIsNone(s.parse_items('{"coverage":"partial","time_origin":"video","items":[]}', 0, 900))

    def test_end_exclusive(self):
        self.assertEqual(parse(observation(second=900)), [])

    def test_bool_is_not_second(self):
        self.assertEqual(parse(observation(second=True)), [])

    def test_visual_offset_bound(self):
        self.assertEqual(parse(observation(visual_second=100)), [])

    def test_missing_field_dropped(self):
        row = observation()
        del row['scene']
        self.assertEqual(parse(row), [])

    def test_no_digit_stripping(self):
        got = parse(observation(screen_code='5289-KY'))[0]
        self.assertEqual((got['code'], got['raw_code']), ('', '5289-KY'))

    def test_conflict_keeps_both_observations(self):
        got = s.check_codes(parse(observation(screen_name='乙公司')), {'1111': '甲公司', '2222': '乙公司'})[0]
        self.assertEqual((got['code'], got['name'], got['confidence']), ('1111', '乙公司', '模糊'))

    def test_empty_registry_cannot_certify(self):
        self.assertEqual(s.check_codes(parse(observation()), {})[0]['confidence'], '模糊')

    def test_chat_is_not_same_subject(self):
        self.assertEqual(s.check_codes(parse(observation(scene='chat')), {'1111': '甲公司'})[0]['confidence'], '模糊')

    def test_code_only_is_not_certified(self):
        self.assertEqual(s.check_codes(parse(observation(screen_name='')), {'1111': '甲公司'})[0]['confidence'], '模糊')

    def test_no_prefix_matching_across_stocks(self):
        self.assertFalse(s.same_stock('甲公司', '甲'))
        self.assertFalse(s.same_stock('甲公司-KY', '甲公司'))

    def test_spelling_variant_under_same_code(self):
        # 代號已對上時，簡稱與全名、KY 有無都算同一檔（這是寫法差異，不是兩家公司）
        codes = {'1111': '甲公司-KY', '2222': '乙丙'}
        self.assertEqual(s.check_codes(parse(observation(screen_name='甲公司')), codes)[0]['confidence'], '清楚')
        self.assertEqual(s.check_codes(parse(observation(screen_code='2222', screen_name='乙丙先進')), codes)[0]['confidence'], '清楚')
        self.assertFalse(s.name_variant('乙', '乙丙'))  # 單字前綴不算

    def test_correction_sentence_kept(self):
        row = s.check_codes(parse(observation(said='不是乙公司，叫做甲公司', heard='甲公司', speech_kind='explicit')), {'1111': '甲公司'})[0]
        self.assertFalse(s.explicit(row))
        row = s.check_codes(parse(observation(said='這一檔是不是乙公司？不是，是甲公司', heard='甲公司', speech_kind='explicit')), {'1111': '甲公司'})[0]
        self.assertFalse(s.explicit(row))

    def test_direct_explicit_removed(self):
        row = s.check_codes(parse(observation(said='甲公司看起來不錯', heard='甲公司', speech_kind='explicit')), {'1111': '甲公司'})[0]
        self.assertTrue(s.explicit(row))

    def test_heard_holding_whole_sentence(self):
        # 2026/11/12 實跑：模型把整句填進 heard，仍要認出有說出名稱
        said = '甲公司是不是底部有爆大量'
        row = s.check_codes(parse(observation(said=said, heard=said, speech_kind='explicit')), {'1111': '甲公司'})[0]
        self.assertTrue(s.explicit(row))

    def test_pronoun_never_explicit(self):
        row = s.check_codes(parse(observation()), {'1111': '甲公司'})[0]
        self.assertFalse(s.explicit(row))

    def test_dedupe_better_observation(self):
        first = parse(observation(confidence='模糊'))[0]
        second = parse(observation(second=25, visual_second=25))[0]
        self.assertEqual(s.dedupe([first, second])[0]['confidence'], '清楚')

    def test_different_utterance_kept(self):
        one = parse(observation())[0]
        two = parse(observation(second=22, visual_second=22, said='另一句獨立評論'))[0]
        self.assertEqual(len(s.dedupe([one, two])), 2)


class Prompt(unittest.TestCase):
    def test_layout_and_patterns(self):
        p = s.prompt(900, 1800, [('嘉公司', '甲公司')])
        self.assertIn('左上角', s.LAYOUT)
        self.assertIn(s.LAYOUT, s.SYSTEM)
        self.assertIn('[900, 1800)', p)
        self.assertIn('0:15:00', p)
        self.assertIn('F. 說自己的買賣', p)
        self.assertIn('「嘉公司」可能是甲公司', p)
        self.assertIn('待查線索', p)
        self.assertIn('：無', s.prompt(0, 900))
        self.assertIn('"time_origin":"video"', p)
        self.assertIn('不是整句', p)

    def test_schema_bounds(self):
        sch = s.schema(900, 1800)['properties']['items']['items']['properties']['second']
        self.assertEqual((sch['minimum'], sch['maximum']), (900, 1799))

    def test_sdk_schema_builds_without_request(self):
        from google.genai import types
        types.GenerateContentConfig(response_schema=s.schema(0, 900), response_mime_type='application/json')


class Vocabulary(unittest.TestCase):
    codes = {'1111': '甲公司'}

    def rows(self, **extra):
        return s.check_codes(parse(observation(heard='嘉公司', speech_kind='misheard', **extra)), self.codes)

    def test_new_name_and_misheard(self):
        out = v.learn(self.rows(), [], code_names=self.codes)
        self.assertEqual([(x['類型'], x['聽到'], x['股名']) for x in out], [('新股名', '甲公司', '甲公司'), ('聽錯', '嘉公司', '甲公司')])

    def test_known_name_not_relearned(self):
        self.assertEqual([x['類型'] for x in v.learn(self.rows(), ['甲公司'], code_names=self.codes)], ['聽錯'])

    def test_repeated_time_not_votes(self):
        out = v.learn(self.rows() * 4, [], code_names=self.codes)
        self.assertTrue(all(x['次數'] == 1 for x in out))

    def test_price_not_alias(self):
        rows = s.check_codes(parse(observation(heard='四千六', speech_kind='misheard')), self.codes)
        self.assertFalse(any(x['類型'] == '聽錯' for x in v.learn(rows, [], code_names=self.codes)))

    def test_scene_blocks_learning(self):
        self.assertEqual(v.learn(self.rows(scene='chat'), [], code_names=self.codes), [])

    def test_real_other_stock_is_not_misheard(self):
        codes = {'1111': '甲公司', '2222': '嘉公司'}
        rows = s.check_codes(parse(observation(heard='嘉公司', speech_kind='misheard')), codes)
        self.assertFalse(any(x['類型'] == '聽錯' for x in v.learn(rows, [], code_names=codes)))

    def test_conflicting_approved_alias_suppressed(self):
        rows = [dict(類型='聽錯', 聽到='嘉公司', 股名=n, 狀態='已採用') for n in ('甲公司', '乙公司')]
        self.assertEqual(v.adopted(rows)[1], [])

    def test_later_rejection_revokes_adoption(self):
        rows = [dict(類型='聽錯', 聽到='嘉公司', 股名='甲公司', 狀態=st) for st in ('已採用', '不採用')]
        self.assertEqual(v.adopted(rows), ([], []))

    def test_adopted_names_and_pairs(self):
        rows = [{'類型': '新股名', '聽到': '丙公司', '股名': '丙公司', '狀態': '已採用'},
                {'類型': '聽錯', '聽到': '嘉公司', '股名': '甲公司', '狀態': '已採用'},
                {'類型': '聽錯', '聽到': '戊', '股名': '丁公司', '狀態': '待核對'}]
        self.assertEqual(v.adopted(rows), (['甲公司', '丙公司'], [('嘉公司', '甲公司')]))

    def test_pronouns(self):
        self.assertTrue(all(v.is_pronoun(w) for w in ('這一檔', '它', '這一支股票', '第二支', '')))
        self.assertFalse(any(v.is_pronoun(w) for w in ('嘉公司', '一頂', '行程')))

    def test_vocab_row_states(self):
        import screen_identify as si
        new = si.vocab_row({'類型': '新股名', '聽到': '甲公司', '股名': '甲公司'}, 'x', '2026/11/12')
        mis = si.vocab_row({'類型': '聽錯', '聽到': '嘉公司', '股名': '甲公司'}, 'x', '2026/11/12')
        self.assertEqual((new['狀態'], mis['狀態']), ('已採用', '待核對'))
        self.assertEqual(v.HEAD, LOG_TABS[v.TAB])


class Runtime(unittest.TestCase):
    def reader(self, directory, response=None, error=None):
        from screen_runtime import Reader
        r = Reader.__new__(Reader)

        def send(**kwargs):
            if error:
                raise error
            return response
        r.client = SimpleNamespace(models=SimpleNamespace(generate_content=send))
        r.models, r.used = ['test-model'], []
        r.attempts, r.last_send, r.deadline = 0, -1e9, time.monotonic() + 480
        r.max_calls, r.total_input, r.input_budget = 12, 0, 1800000
        r.thinking, r.resolution, r.fps, r.blocked = 'low', 'default', 0.5, False
        r.path = Path(directory) / 'metrics.jsonl'
        return r

    def test_truncated_output_is_failure(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.reader(d, SimpleNamespace(candidates=[SimpleNamespace(finish_reason='MAX_TOKENS')], text='{}', usage_metadata=None))
            with self.assertRaises(RuntimeError):
                r.read('https://www.youtube.com/watch?v=test', 0, 900)
            self.assertEqual(r.attempts, 1)

    def test_429_stops_the_round(self):
        error = type('QuotaError', (Exception,), {'code': 429})('private details must not be logged')
        with tempfile.TemporaryDirectory() as d:
            r = self.reader(d, error=error)
            for start in (0, 900):
                with self.assertRaises(RuntimeError):
                    r.read('https://www.youtube.com/watch?v=test', start, start + 900)
            self.assertEqual(r.attempts, 1)
            self.assertNotIn('private details', r.path.read_text(encoding='utf-8'))

    def test_budget_stops_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.reader(d)
            r.attempts = r.max_calls
            with self.assertRaises(RuntimeError):
                r.read('https://www.youtube.com/watch?v=test', 0, 900)
            self.assertFalse(r.path.exists())

    def test_usage_metadata_saved(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.reader(d, SimpleNamespace(candidates=[SimpleNamespace(finish_reason='STOP')], text='{}',
                                               usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5)))
            self.assertEqual(r.read('https://www.youtube.com/watch?v=test', 0, 900), '{}')
            row = json.loads(r.path.read_text(encoding='utf-8'))
            self.assertEqual((row['input_tokens'], row['output_tokens']), (10, 5))


class ReviewTools(unittest.TestCase):
    def test_day_rows(self):
        rows = [{'日期': '2026/11/12', '時間': '0:17:05'}, {'日期': '2026-11-11', '時間': '0:01:00'}, {'日期': '2026/11/12', '時間': ''}]
        self.assertEqual([r['_row'] for r in day_rows(rows, '2026/11/12')], [2, 4])
        self.assertEqual(LOG_TABS['畫面辨識'][:4], ['建立時間', '日期', '影片ID', '時間'])

    def test_parse_batch(self):
        self.assertEqual(parse_batch('0:03:17\t不需處理\t說明\n\n0:04:16\t網站已有'),
                         [('0:03:17', '不需處理', '說明'), ('0:04:16', '網站已有', '')])
        with self.assertRaises(SystemExit):
            parse_batch('0:03:17\t亂寫')

    def test_transcript_hint_is_soft(self):
        import transcript as tx
        tx._LEARNED[:], tx._MISHEARD[:] = ['丙公司'], [('嘉公司', '甲公司')]
        try:
            text = tx.vocabulary()
            self.assertIn('丙公司', text)
            self.assertIn('嘉公司→甲公司', text)
            self.assertIn('只供辨音參考', text)
            self.assertNotIn('請寫成右邊', text)
        finally:
            tx._LEARNED[:], tx._MISHEARD[:] = [], []


if __name__ == '__main__':
    unittest.main()
