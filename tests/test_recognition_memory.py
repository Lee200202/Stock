# -*- coding: utf-8 -*-
"""辨識經歷落地與重用：名稱判定紀錄（累加、人工確認才套用）與辨識日誌。"""
import io
import json
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from test_quality import p, empty

MAP = {'2201': '裕隆', '2233': '宇隆', '2354': '鴻準', '6533': '晶心科', '2330': '台積電'}


class FakeSheet:
    def __init__(self, title):
        self.title, self.rows = title, []

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def append_row(self, row, **kw):
        self.rows.append(list(row))

    def append_rows(self, rows, **kw):
        self.rows.extend(list(r) for r in rows)

    def batch_update(self, data, **kw):
        for item in data:
            m = re.fullmatch(r'([A-Z]+)(\d+)', item['range'])
            col = sum((ord(ch) - 64) * 26 ** i for i, ch in enumerate(reversed(m.group(1)))) - 1
            row = self.rows[int(m.group(2)) - 1]
            while len(row) <= col:
                row.append('')
            row[col] = item['values'][0][0]


class FakeBook:
    def __init__(self):
        self.sheets = {}

    def worksheet(self, title):
        if title not in self.sheets:
            raise Exception('WorksheetNotFound')
        return self.sheets[title]

    def add_worksheet(self, title, rows=0, cols=0):
        self.sheets[title] = FakeSheet(title)
        return self.sheets[title]


def memo_rows(book):
    rows = book.sheets[p.NAME_MEMO_SHEET].rows
    head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:]]


class MemoRecordTests(unittest.TestCase):
    def test_events_accumulate_per_run_and_suggest_after_three(self):
        book = FakeBook()
        ev = [{'heard': '洪準', 'verdict': 'stock', 'real': '鴻準', 'code': '2354', 'why': '拼音相似', 'source': '規則'}]
        out = io.StringIO()
        with redirect_stdout(out):
            p.name_memo_record(book, ev + ev)            # 同一輪兩次只算一次
            p.name_memo_record(book, ev)
            p.name_memo_record(book, ev)
        rows = memo_rows(book)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]['命中次數']), 3)
        self.assertEqual(rows[0]['來源'], '規則')
        self.assertIn('可在表上把來源改成「人工」', out.getvalue())

    def test_conflicting_codes_get_no_suggestion(self):
        book = FakeBook()
        a = {'heard': '加折', 'verdict': 'stock', 'real': '嘉澤', 'code': '3533', 'why': '', 'source': 'ai'}
        b = dict(a, real='家登', code='3680')
        out = io.StringIO()
        with redirect_stdout(out):
            for _ in range(3):
                p.name_memo_record(book, [a, b])
        self.assertEqual(len(memo_rows(book)), 2)
        self.assertNotIn('改成「人工」', out.getvalue(), '同一個寫法對過兩家公司，不建議人工確認')

    def test_header_mismatch_does_not_write(self):
        book = FakeBook(); ws = book.add_worksheet(p.NAME_MEMO_SHEET); ws.rows = [['聽到的名稱', '判定']]
        with redirect_stdout(io.StringIO()) as out:
            p.name_memo_record(book, [{'heard': '洪準', 'verdict': 'stock', 'real': '鴻準', 'code': '2354', 'why': '', 'source': '規則'}])
        self.assertEqual(len(ws.rows), 1)
        self.assertIn('表頭少了', out.getvalue())


class MemoReuseTests(unittest.TestCase):
    def load(self, rows):
        book = FakeBook(); ws = book.add_worksheet(p.NAME_MEMO_SHEET)
        ws.rows = [p.NAME_MEMO_HEADERS] + rows
        p._NAME_MEMO = None
        with redirect_stdout(io.StringIO()):
            p.name_memo_load(book)
        return book

    def tearDown(self):
        p._NAME_MEMO = None

    def row(self, heard, verdict, real, code, source, keys=''):
        return [heard, verdict, real, code, keys, '', source, '2026/09/14', 1, '2026/09/14']

    def test_manual_entry_is_applied_but_ai_entry_is_not(self):
        self.load([self.row('洪蠢', 'stock', '鴻準', '2354', '人工'),
                   self.row('精心科', 'stock', '晶心科', '6533', 'ai')])
        s = empty()
        s['watch_watch'] = [{'name': '洪蠢', 'reason': '外資持續買進'}, {'name': '精心科', 'reason': '權利金族群'}]
        with patch.object(p, '_CODE_MAP', dict(MAP)), patch.object(p, '_CODE_MAP_FULL', True), \
             patch.object(p, 'get_code_map', return_value=dict(MAP)), redirect_stdout(io.StringIO()) as out:
            p.resolve_signals(s, '洪蠢今天跌。精心科是權利金。' * 40)
        by = {r.get('原始語音名稱') or r['name']: r for r in s['watch_watch']}
        self.assertEqual(by['洪蠢']['code'], '2354')
        self.assertIn('名稱判定紀錄：人工確認', out.getvalue())
        log = out.getvalue()
        self.assertIn('精心科 -> 6533 晶心科（拼音相似', log, '來源 ai 的紀錄不套用，照一般比對')
        self.assertNotIn('精心科 -> 6533 晶心科（名稱判定紀錄', log)

    def test_manual_wrong_code_and_context_keys(self):
        self.load([self.row('紅海', 'stock', '鴻海', '9999', '人工'),                 # 打錯代號：不採用
                   self.row('加折', 'stock', '家登', '3680', '人工', keys='光罩盒')])  # 有情境關鍵詞
        with patch.object(p, '_CODE_MAP', dict(MAP, **{'3680': '家登', '3533': '嘉澤'})):
            self.assertIsNone(p.manual_memo_match({'name': '紅海', 'reason': ''}))
            self.assertIsNone(p.manual_memo_match({'name': '加折', 'reason': '1580以下買'}))
            self.assertEqual(p.manual_memo_match({'name': '加折', 'reason': '光罩盒需求'})[1]['code'], '3680')

    def test_manual_industry_drops_row_and_manual_names_reach_prompt(self):
        self.load([self.row('全力金', 'industry', '矽智財權利金', '', '人工'),
                   self.row('洪蠢', 'stock', '鴻準', '2354', '人工')])
        s = empty(); s['watch_watch'] = [{'name': '全力金', 'reason': '族群'}]
        with patch.object(p, '_CODE_MAP', dict(MAP)), patch.object(p, 'get_code_map', return_value=dict(MAP)), \
             redirect_stdout(io.StringIO()):
            p.resolve_signals(s, '全力金族群。' * 50)
            data = json.loads(p.assessment_payload('2026/09/14', p.source_segments('洪 蠢 今 天 跌 。 全 力 金 族 群 。')))
        self.assertFalse(s['watch_watch'])
        self.assertEqual(data['confirmed_names']['洪蠢'], ['2354', '鴻準'])
        self.assertEqual(data['confirmed_industries']['全力金'], '矽智財權利金')


class RecognitionLogTests(unittest.TestCase):
    def tearDown(self):
        p._NAME_MEMO = None

    def test_every_run_saves_log_and_name_events(self):
        book = FakeBook()

        def impl(ss, video, date_str, *a, **kw):
            print('===== 步驟：代號比對 =====')
            p.note_name_event('玉龍', 'stock', '裕隆', '2201', '管理者確認名稱', '內建確認')
            print('  代號比對　玉龍 -> 2201 裕隆（管理者確認名稱）')
            return ['2026/09/14']
        outer = io.StringIO()
        with patch.object(p, '_stage_extract_impl', side_effect=impl), redirect_stdout(outer):
            result = p.stage_extract(book, {'id': 'MANUAL-20260914'}, '2026/09/14', 'v2', set(), set(), v1='原文')
            self.assertIs(p.sys.stdout, outer, '結束後要還原輸出')
        self.assertEqual(result, ['2026/09/14'])
        log = book.sheets[p.RECOGNITION_LOG_SHEET].rows
        self.assertEqual(log[0], p.RECOGNITION_LOG_HEADERS)
        self.assertIn('玉龍 -> 2201 裕隆', log[1][6])
        self.assertEqual(log[1][2], 'MANUAL-20260914')
        self.assertEqual(memo_rows(book)[0]['代號'], '2201')
        self.assertIn('代號比對', outer.getvalue(), 'GitHub 日誌照樣看得到')

    def test_failure_still_saves_log_and_reraises(self):
        book = FakeBook()

        def impl(*a, **kw):
            print('擷取結果　持股 1：台積電')
            raise RuntimeError('Gemini 呼叫失敗')
        with patch.object(p, '_stage_extract_impl', side_effect=impl), redirect_stdout(io.StringIO()):
            with self.assertRaises(RuntimeError):
                p.stage_extract(book, {'id': 'v'}, '2026/09/14', 'v2', set(), set())
        text = book.sheets[p.RECOGNITION_LOG_SHEET].rows[1][6]
        self.assertIn('擷取結果', text); self.assertIn('辨識中斷：RuntimeError', text)

    def test_skip_only_run_is_not_logged(self):
        book = FakeBook()
        with patch.object(p, '_stage_extract_impl', side_effect=lambda *a, **k: print('2026/09/14 操作紀錄、會員持股與推播內容都已存在，略過擷取')), \
             redirect_stdout(io.StringIO()):
            p.stage_extract(book, {'id': 'v'}, '2026/09/14', 'v2', set(), set())
        self.assertNotIn(p.RECOGNITION_LOG_SHEET, book.sheets)

    def test_long_log_is_split_into_cell_sized_parts(self):
        book = FakeBook()
        with redirect_stdout(io.StringIO()):
            p.save_recognition_log(book, 'v', '2026/09/14', 'raw', '字' * (p.RECOGNITION_LOG_CHUNK * 2 + 10))
        rows = book.sheets[p.RECOGNITION_LOG_SHEET].rows[1:]
        self.assertEqual([r[4] for r in rows], [1, 2, 3]); self.assertEqual(rows[0][5], 3)
        self.assertTrue(all(len(r[6]) <= 45000 for r in rows))


if __name__ == '__main__':
    unittest.main()
