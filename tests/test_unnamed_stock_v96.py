# -*- coding: utf-8 -*-
"""
沒講名字的個股段落：用行情認出是哪一檔，再過二次稽核（v96，管理者 2026/10/01）。

當天節目有一段整段沒有公司名稱：
  「我昨天有沒有跟你們講一支股票？我說這一支股票只要1745突破，這一支股票就要大漲。
    ……昨天收盤收多少？1770。……啊現在1800。」
模型把它掛到附近點名過的世芯-KY（當天收 3765）。管理者指出：去持股追蹤看昨天收盤價，
就知道是嘉澤（3533，9/30 收 1770）；對不到的不計入。

這裡釘住的邊界：
  只有「昨收一模一樣、而且只有一檔」才認；同價兩檔、對不到、沒有明講收盤價都不收。
  認出來的一定再過一次模型（二次稽核），不通過不收；說明逐句核對引用與數字。
  只會列觀望注意／觀望不碰；會員持股只給持股追蹤裡正持有、原話有買進說法的。
  任何一步失敗都不擋發布，也不補造。
"""

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent.parent

os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")
_spec = importlib.util.spec_from_file_location("pl_unnamed", ROOT / "pipeline" / "pipeline.py")
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)

D = '2026/10/01'
Q1 = '我說這一支股票 只要 1745突破， 這一支股票就要大漲。'
Q2 = '昨天收盤收多少？ 1770。'
Q3 = '所以 昨天是不是漲到1745點之上？ 好，啊漲過去了，你就不用緊張了。'
Q4 = '這是不是張正通知你買進的價格？ 我有沒有告訴你這邊趕快買。'
Q5 = '啊現在1800。'
Q6 = '啊這一支股票， 這一天是跌的。我叫你買。900以下的。'
TX = ('所以 紅準根本還沒有漲完。來。我昨天有沒有跟你們講一支股票？ ' + Q1 +
      '我昨天有沒有跟你們講這一支股票？ 昨天收盤收多少？ ' + Q2 +
      '對不對？我講的1745在這裡。' + Q3 + '來， 給你看。' + Q4 + '趕快買。' + Q5 +
      '啊這一支股票要過2400很隨便的事情。' + Q6 + '啊四星KY跟國外的大廠的合作，後面你們拭目以待。')

DAILY_K = [
    ['代號', '日期', '開', '高', '低', '收', '量'],
    ['3533', '2026/09/29', '1680', '1765', '1680', '1710', '1'],
    ['3533', '2026/09/30', '1730', '1785', '1705', '1770', '1'],
    ['3533', '2026/10/01', '1780', '1865', '1760', '1855', '1'],
    ['3661', '2026/09/30', '3745', '3810', '3730', '3740', '1'],
    ['3661', '2026/10/01', '3770', '3820', '3735', '3765', '1'],
    ['2354', '2026/09/30', '65.6', '66.4', '65.5', '65.7', '1'],
    ['2354', '2026/10/01', '67.1', '69.5', '65.3', '66.2', '1'],
]
QUOTES = [['代號', '名稱', '現價', '昨收', '漲跌', '漲跌幅', '成交量', '更新時間', '開', '高', '低', '行情日期']]
TRACKER = [['代號', '股票名稱', '狀態'], ['3533', '嘉澤', '持有中'], ['3661', '世芯-KY', '持有中'], ['9999', '舊股', '已出場']]

PASSAGE = {'quotes': [Q1, Q2, Q3, Q4, Q5],
           'clues': [{'kind': 'level', 'value': 1745, 'quote': Q1}, {'kind': 'prev_close', 'value': 1770, 'quote': Q2},
                     {'kind': 'today_price', 'value': 1800, 'quote': Q5}],
           'stance': 'bullish', 'summary': '突破關卡後看好續漲'}
AUDIT_OK = {'audits': [{'id': 'u0', 'accept': True, 'category': 'holdings', 'why': '', 'sentences': [
    {'text': '嘉澤昨日收盤1770，已站上先前強調的1745關卡，不必緊張。', 'quotes': [Q2, Q3]},
    {'text': '先前已通知買進，目前股價來到1800。', 'quotes': [Q4, Q5]}]}]}


class Sheet:
    def __init__(self, rows):
        self.rows = rows

    def get_all_values(self):
        return [list(r) for r in self.rows]


class Book:
    def __init__(self, daily=DAILY_K, quotes=QUOTES, tracker=TRACKER):
        self.sheets = {'日K快取': daily, '即時快取': quotes, '持股追蹤': tracker}
        self.reads = []

    def worksheet(self, name):
        self.reads.append(name)
        return Sheet(self.sheets[name])


def empty_signals():
    sig = {k: [] for k in pl.SIGNAL_CATEGORIES}
    sig['watch_watch'].append({'name': '世芯-KY', 'code': '3661', 'price': '未說明', 'aliases': ['四星KY'],
                               'reason': '與國外大廠合作。', 'evidence': ['四星KY跟國外的大廠的合作']})
    return sig


class UnnamedStockTests(unittest.TestCase):
    def setUp(self):
        pl._DK_MEMO.clear()
        pl._CODE_MAP = {'3533': '嘉澤', '3661': '世芯-KY', '2354': '鴻準', '5269': '祥碩'}
        pl._QUOTA_STOP['daily'] = False
        pl._DECISIONS.clear() if hasattr(pl, '_DECISIONS') else None

    def run_identify(self, replies, book=None, sig=None, tx=TX):
        calls = []

        def fake(system, payload, **kw):
            calls.append((kw.get('tag'), json.loads(payload)))
            reply = replies[len(calls) - 1]
            if isinstance(reply, Exception):
                raise reply
            return json.dumps(reply, ensure_ascii=False)
        book, sig, out = book or Book(), sig or empty_signals(), io.StringIO()
        with patch.object(pl, 'call_gemini', side_effect=fake), patch.object(pl, 'budget_left', return_value=900), \
             patch.object(pl, 'GEMINI_KEYS', ['k']), contextlib.redirect_stdout(out):
            pl.identify_unnamed_stocks(book, sig, tx, D)
        return sig, calls, out.getvalue(), book

    def test_yesterday_close_identifies_the_held_stock(self):
        sig, calls, out, _ = self.run_identify([{'passages': [PASSAGE]}, AUDIT_OK])
        self.assertEqual([c[0] for c in calls], ['unnamed-stock', 'unnamed-audit'])
        entry = calls[1][1]['entries'][0]
        self.assertEqual((entry['candidate'], entry['code'], entry['held']), ('嘉澤', '3533', True))
        self.assertEqual(entry['market']['prev_close'], 1770.0)
        self.assertEqual((entry['market']['today_low'], entry['market']['today_high']), (1760.0, 1865.0))
        row = sig['holdings'][-1]
        self.assertEqual((row['name'], row['code'], row['stance'], row['price']), ('嘉澤', '3533', '續抱', '未說明'))
        self.assertIn('1770', row['note']); self.assertIn('1745', row['note'])
        self.assertTrue(row['_evidence_verified'])
        self.assertEqual(row['_price_identified']['prev_date'], '2026/09/30')
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['世芯-KY'])      # 沒有掛到別檔
        self.assertNotIn('1745', sig['watch_watch'][0]['reason'])
        self.assertIn('嘉澤（3533）列會員持股', out)

    def test_not_held_is_never_listed_as_member_holding(self):
        book = Book(tracker=[['代號', '股票名稱', '狀態'], ['3533', '嘉澤', '已出場']])
        sig, _, _, _ = self.run_identify([{'passages': [PASSAGE]}, AUDIT_OK], book=book)
        self.assertEqual(sig['holdings'], [])
        self.assertEqual(sig['watch_watch'][-1]['name'], '嘉澤')
        self.assertIn('1770', sig['watch_watch'][-1]['reason'])

    def test_two_stocks_with_the_same_close_are_not_guessed(self):
        book = Book(daily=DAILY_K + [['5269', '2026/09/30', '1750', '1790', '1740', '1770', '1'],
                                     ['5269', '2026/10/01', '1775', '1810', '1770', '1800', '1']])
        sig, calls, out, _ = self.run_identify([{'passages': [PASSAGE]}], book=book)
        self.assertEqual(len(calls), 1)                                   # 沒認出來就不送二次稽核
        self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)
        self.assertIn('分不出是哪一檔', out)

    def test_today_price_breaks_the_tie(self):
        # 同樣昨收 1770，但另一檔今天在 1500 上下：原話「現在1800」只符合嘉澤。
        book = Book(daily=DAILY_K + [['5269', '2026/09/30', '1750', '1790', '1740', '1770', '1'],
                                     ['5269', '2026/10/01', '1600', '1610', '1480', '1500', '1']])
        sig, calls, _, _ = self.run_identify([{'passages': [PASSAGE]}, AUDIT_OK], book=book)
        self.assertEqual(calls[1][1]['entries'][0]['code'], '3533')
        self.assertEqual(sig['holdings'][-1]['name'], '嘉澤')

    def test_no_match_is_dropped(self):
        passage = dict(PASSAGE, clues=[{'kind': 'prev_close', 'value': 1770, 'quote': Q2}])
        book = Book(daily=[r for r in DAILY_K if r[0] != '3533'])
        sig, calls, out, _ = self.run_identify([{'passages': [passage]}], book=book)
        self.assertEqual(len(calls), 1); self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)
        self.assertIn('沒有一檔昨收是 1770', out)

    def test_today_price_outside_the_days_range_rejects_the_match(self):
        passage = dict(PASSAGE, clues=PASSAGE['clues'][:2] + [{'kind': 'today_price', 'value': 2400, 'quote': '啊這一支股票要過2400很隨便的事情。'}],
                       quotes=PASSAGE['quotes'] + ['啊這一支股票要過2400很隨便的事情。'])
        sig, calls, out, _ = self.run_identify([{'passages': [passage]}])
        self.assertEqual(len(calls), 1); self.assertEqual(sig['holdings'], [])

    def test_second_audit_can_refuse(self):
        refuse = {'audits': [{'id': 'u0', 'accept': False, 'why': '原話提到無塵室，與候選公司不符', 'sentences': []}]}
        sig, calls, out, _ = self.run_identify([{'passages': [PASSAGE]}, refuse])
        self.assertEqual(len(calls), 2); self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)
        self.assertIn('二次稽核不通過', out)

    def test_audit_sentences_are_verified_one_by_one(self):
        audit = {'audits': [{'id': 'u0', 'accept': True, 'category': 'watch_watch', 'sentences': [
            {'text': '嘉澤昨日收盤1770，已站上1745關卡。', 'quotes': [Q2, Q3]},
            {'text': '目標價上看2600。', 'quotes': [Q5]},                       # 數字不在引用裡
            {'text': '法人連續買超。', 'quotes': ['外資連續買超三天']}]}]}       # 引用不在這一段
        sig, _, _, _ = self.run_identify([{'passages': [PASSAGE]}, audit])
        reason = sig['watch_watch'][-1]['reason']
        self.assertIn('1770', reason); self.assertNotIn('2600', reason); self.assertNotIn('買超', reason)

    def test_audit_with_no_valid_sentence_is_dropped(self):
        audit = {'audits': [{'id': 'u0', 'accept': True, 'category': 'watch_watch', 'sentences': [
            {'text': '目標價上看2600。', 'quotes': [Q5]}]}]}
        sig, _, _, _ = self.run_identify([{'passages': [PASSAGE]}, audit])
        self.assertEqual(len(sig['watch_watch']), 1)

    def test_buy_and_sell_are_never_produced(self):
        audit = json.loads(json.dumps(AUDIT_OK)); audit['audits'][0]['category'] = 'buy'
        sig, _, _, _ = self.run_identify([{'passages': [PASSAGE]}, audit])
        self.assertEqual(sig['buy'], []); self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)

    def test_clue_must_be_in_its_quote_and_close_must_be_stated(self):
        fake_close = dict(PASSAGE, clues=[{'kind': 'prev_close', 'value': 1770, 'quote': Q1}])       # 那一句沒有 1770
        only_level = dict(PASSAGE, clues=[{'kind': 'level', 'value': 1745, 'quote': Q1}])           # 沒有明講收盤價
        made_up = dict(PASSAGE, quotes=['這一支股票昨天收盤 1770，今天會漲停'])                         # 原文沒有這一句
        for passage in (fake_close, only_level, made_up):
            sig, calls, _, book = self.run_identify([{'passages': [passage]}])
            self.assertEqual(len(calls), 1)
            self.assertEqual(sig['holdings'], [])
            self.assertNotIn('日K快取', book.reads)                       # 沒有可用的線索就不必讀行情

    def test_passage_that_names_a_listed_stock_is_not_unnamed(self):
        passage = dict(PASSAGE, quotes=PASSAGE['quotes'] + ['啊四星KY跟國外的大廠的合作，後面你們拭目以待。'])
        sig, calls, _, _ = self.run_identify([{'passages': [passage]}])
        self.assertEqual(len(calls), 1); self.assertEqual(sig['holdings'], [])

    def test_foreign_tail_is_cut_before_the_audit(self):
        passage = dict(PASSAGE, quotes=PASSAGE['quotes'] + [Q6],
                       clues=PASSAGE['clues'] + [{'kind': 'buy_price', 'value': 900, 'quote': Q6}])
        sig, calls, _, _ = self.run_identify([{'passages': [passage]}, AUDIT_OK])
        entry = calls[1][1]['entries'][0]
        self.assertNotIn(Q6, entry['quotes'])
        self.assertTrue(all(c['value'] != 900 for c in entry['clues']))

    def test_already_listed_stock_is_not_duplicated(self):
        sig = empty_signals()
        sig['watch_watch'].append({'name': '嘉澤', 'code': '3533', 'price': '未說明', 'reason': '高價股續抱。', 'evidence': ['嘉澤']})
        sig, calls, out, _ = self.run_identify([{'passages': [PASSAGE]}], sig=sig)
        self.assertEqual(len(calls), 1)
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['世芯-KY', '嘉澤'])
        self.assertIn('今天已收錄', out)

    def test_every_outcome_leaves_a_line_in_the_decision_log(self):
        # 後台看不到 GitHub 日誌時，判定歷程要講得出這一步有沒有做、為什麼沒結果。
        notes = []
        with patch.object(pl, 'note_decision', side_effect=lambda *a, **k: notes.append(a)):
            self.run_identify([{'passages': []}])
            self.run_identify([{'passages': [dict(PASSAGE, clues=[{'kind': 'level', 'value': 1745, 'quote': Q1}])]}])
            calls = []
            with patch.object(pl, 'call_gemini', side_effect=lambda *a, **k: calls.append(1)), \
                 patch.object(pl, 'budget_left', return_value=100), patch.object(pl, 'GEMINI_KEYS', ['k']), \
                 contextlib.redirect_stdout(io.StringIO()):
                pl.identify_unnamed_stocks(Book(), empty_signals(), TX, D)
        actions = [n[1] for n in notes if n[0] == '無名段落']
        self.assertEqual(actions.count('沒有可以對行情的無名段落'), 2)
        self.assertIn('時間或配額不足，本輪未比對', actions)
        self.assertEqual(calls, [])
        self.assertIn('沒有明講收盤價 1', [n[3] for n in notes if n[1] == '沒有可以對行情的無名段落'][1])

    def test_no_closing_price_talk_means_no_model_call(self):
        sig, calls, _, book = self.run_identify([], tx='台積電抱著，鴻海抱著。聯發科不要碰。' * 30)
        self.assertEqual(calls, []); self.assertEqual(book.reads, [])

    def test_model_failure_never_blocks_publication(self):
        sig, calls, out, _ = self.run_identify([RuntimeError('503')])
        self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)
        self.assertIn('無名段落比對未完成', out)
        sig, calls, out, _ = self.run_identify([{'passages': [PASSAGE]}, RuntimeError('503')])
        self.assertEqual(sig['holdings'], []); self.assertEqual(len(sig['watch_watch']), 1)

    def test_snapshot_falls_back_to_live_quote_reference_price(self):
        # 日K快取沒有前一個交易日那一檔（富果整批回 0 根的日子）：用即時快取的昨收，行情日期必須是當天。
        daily = [r for r in DAILY_K if not (r[0] == '3533' and r[1] == '2026/09/30')]
        quotes = QUOTES + [['3533', '嘉澤', '1800', '1770', '30', '1.7', '900', '2026/10/01 11:20:00', '1780', '1865', '1760', '2026/10/01'],
                           ['2354', '鴻準', '66', '65.7', '0', '0', '1', '2026/09/30 13:30:00', '65', '66', '65', '2026/09/30']]
        with contextlib.redirect_stdout(io.StringIO()):
            snap = pl.market_snapshot(Book(daily=daily, quotes=quotes), D)
        self.assertEqual(snap['3533']['prev_close'], 1770.0)
        self.assertEqual(snap['3533']['prev_date'], '即時快取昨收')
        self.assertEqual(snap['3661']['prev_close'], 3740.0)

    def test_match_rules(self):
        snap = {'3533': {'prev_close': 1770.0, 'prev_date': '2026/09/30', 'low': 1760.0, 'high': 1865.0},
                '3661': {'prev_close': 3740.0, 'prev_date': '2026/09/30'}}
        code, why = pl.match_unnamed_by_price(PASSAGE, snap)
        self.assertEqual(code, '3533'); self.assertIn('只有這一檔相符', why)
        code, why = pl.match_unnamed_by_price({'clues': [{'kind': 'prev_close', 'value': 1771, 'quote': ''}]}, snap)
        self.assertEqual(code, '')
        code, why = pl.match_unnamed_by_price({'clues': [{'kind': 'level', 'value': 1745, 'quote': ''}]}, snap)
        self.assertEqual((code, why), ('', '原話沒有明講收盤價'))


class LongNoteGuards(unittest.TestCase):
    """v96 重跑 2026/10/01 實際出現的三個問題。"""

    FILLER = '短線客今天買進明天賣出，長線客抱得緊緊的，這個叫做波段操作。'

    def setUp(self):
        pl._CODE_MAP = {'3533': '嘉澤', '3661': '世芯-KY', '2354': '鴻準', '5536': '聖暉*', '2454': '聯發科'}
        pl._ATTR_MEMO.clear()

    def transcript(self):
        return ('張總叫你們買的是世芯KY啊。世芯KY有沒有漲？' + self.FILLER * 8 +
                '鴻準這一支股票沿著月線走，鴻準今天拉到69.5。' + self.FILLER * 12 +
                '我說這一支股票只要1745突破就要大漲。昨天收盤收多少？1770。啊現在1800。' + self.FILLER * 12 +
                '鴻準今天高點一定突破。' + self.FILLER * 12 + '為什麼我偏偏去挑世芯KY？世芯KY跟國外的大廠合作。')

    def signals(self, reason):
        sig = {k: [] for k in pl.SIGNAL_CATEGORIES}
        sig['watch_watch'] = [{'name': '世芯-KY', 'code': '3661', 'price': '未說明', 'reason': reason, 'evidence': []},
                              {'name': '鴻準', 'code': '2354', 'price': '未說明', 'reason': '沿月線走，今天拉到69.5。', 'evidence': []}]
        return sig

    def strip(self, sig, tx):
        with contextlib.redirect_stdout(io.StringIO()):
            return pl.strip_foreign_price_claims(sig, tx)

    def test_numbers_far_from_own_name_with_another_stock_between_are_removed(self):
        sig = self.strip(self.signals('世芯-KY在成功突破洗盤價1745之後轉強向上，目前股價來到1800附近。表示這是先前推薦會員佈局的標的。'),
                         self.transcript())
        reason = sig['watch_watch'][0]['reason']
        self.assertNotIn('1745', reason); self.assertNotIn('1800', reason)
        self.assertTrue(reason.startswith('這是先前推薦會員佈局的標的'), reason)       # 不以「表示」開頭
        self.assertEqual(sig['watch_watch'][1]['reason'], '沿月線走，今天拉到69.5。')  # 本檔附近的數字不動
        self.assertTrue(any('1745' in g and '世芯' in g for g in sig['_repair_gaps']))

    def test_long_monologue_about_the_same_stock_is_kept(self):
        # 同一檔講很久沒再報名字、中間沒有別檔：不動。
        tx = '世芯KY這一支股票我在跌的時候叫你們買。' + self.FILLER * 30 + '所以它只要1745突破就要大漲。' + self.FILLER * 4
        sig = self.strip(self.signals('世芯-KY只要突破1745就會轉強。'), tx)
        self.assertIn('1745', sig['watch_watch'][0]['reason'])

    def test_number_named_later_is_kept(self):
        # 先講內容、後報名字（中間沒有別檔）：不動。
        tx = self.FILLER * 4 + '這一支股票當時800多塊，現在970塊。' + self.FILLER * 20 + '買世芯KY的人有沒有很爽？' + self.FILLER * 4
        sig = self.strip(self.signals('世芯-KY先前在800多元布局，現在漲到970元。'), tx)
        self.assertIn('970', sig['watch_watch'][0]['reason'])

    def test_numbers_only_in_member_sms_are_left_alone(self):
        sig = self.strip(self.signals('世芯-KY於3750元以下買進。'), self.transcript())
        self.assertIn('3750', sig['watch_watch'][0]['reason'])

    def test_written_style_holding_statements_keep_the_member_holding(self):
        sig = {k: [] for k in pl.SIGNAL_CATEGORIES}
        sig['holdings'] = [
            {'name': '聖暉*', 'code': '5536', 'price': '未說明', 'evidence': ['會員我沒叫你們賣，你們不準給我亂賣'],
             'note': '會員低檔佈局已獲利70元以上，強調打底完成並正式突破，指示會員不准亂賣，目標看缺口1045元。'},
            {'name': '聯發科', 'code': '2454', 'price': '未說明', 'evidence': ['台積電、鴻海、四星KY、大立光、聯發科都在這一顆球裡面'],
             'note': '影片中提及聯發科在大球（大環境）裡面。'}]
        with contextlib.redirect_stdout(io.StringIO()):
            pl.demote_holding_mentions(sig)
        self.assertEqual([r['name'] for r in sig['holdings']], ['聖暉*'])
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['聯發科'])
        for text in ('我的會員不準賣就是不準賣', '指示會員續抱', '會員已獲利', '會員低檔布局'):
            self.assertTrue(pl._has_own_cue(text), text)
        for text in ('公司獲利很好', '外資賣超', '在這顆球裡面'):
            self.assertFalse(pl._has_own_cue(text), text)

    def test_public_note_never_says_named_by_the_speaker(self):
        out = pl.public_narrative('力旺被講者點名為收取權利金的股票，在權值股分析中遭講者明確列入不要碰的名單。',
                                  {'name': '力旺', 'code': '3529'}, {})
        self.assertNotIn('講者', out); self.assertIn('被點名', out); self.assertIn('遭明確列入', out)
        gas = (ROOT / 'scripts' / 'public_narrative_v10.txt').read_text(encoding='utf-8')
        self.assertIn("(被|遭|獲)(?:張震|張正|張總|張中|講者)(?:老師)?", gas)

    def test_recap_with_a_current_view_is_not_pure_recap(self):
        # 2026/10/01 鴻準：前半是「從64塊一直推薦」的回顧，後半是今天的判斷。
        pl._CODE_MAP = {'2354': '鴻準', '2404': '漢唐', '3008': '大立光'}
        row = {'name': '鴻準', 'code': '2354', 'aliases': ['紅準'], 'evidence': []}
        tx = ('來，一支股票。紅準。早上我有沒有一直推薦紅準？從64塊有沒有一直推薦紅準？我說紅準這一支股票，沿著月線走。'
              '來，今天紅準拉很高，拉到69.5。所以紅準根本還沒有漲完。鴻準今天的高點，有誰那麼大的？今天高點一定突破。')
        sig = {k: [] for k in pl.SIGNAL_CATEGORIES}
        sig['watch_watch'] = [row]
        self.assertFalse(pl._past_recommendation_only(row, sig, tx))
        # 只有回顧（過去式）的仍然算純回顧
        old = {'name': '漢唐', 'code': '2404', 'aliases': [], 'evidence': []}
        tx_old = '以前我跟你們推薦漢唐，當時說跌破1000就是買點，那時候沒人要買。漢唐現在多少錢？'
        sig['watch_watch'] = [old]
        self.assertTrue(pl._past_recommendation_only(old, sig, tx_old))
        for sentence, expected in (('所以紅準根本還沒有漲完。', True), ('2354鴻準剛剛翻紅。', True),
                                   ('鴻準短線客賣完了，波段客抱得死死的。', True), ('台積電抱著，鴻海抱著。', True),
                                   ('我在2000多塊的時候推薦大力光，說它一定會突破。', False),
                                   ('當時我說漢唐準備啟動。', False), ('啊漢唐現在多少錢？', False),
                                   ('早上我有沒有一直推薦紅準？', False)):
            self.assertEqual(pl._states_current_view(sentence), expected, sentence)

    def test_prompt_tells_the_model_not_to_attach_unnamed_passages(self):
        self.assertIn('沒講名字的段落', pl.POLICY)
        self.assertIn('不寫進任何一檔的說明', pl.POLICY)


if __name__ == '__main__':
    unittest.main()
