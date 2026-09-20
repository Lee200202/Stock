"""2026/09/11 這一輪的修改：品質關卡改到代號比對之後、買入沒附時間句改列觀望注意、
普位／細金元的管理者確認、潤飾稿逐字對齊、後台重跑整天覆蓋。"""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import patch, Mock
from test_quality import p, empty, ROOT


class FakeSheet:
    def __init__(self, rows):
        self.rows = [list(r) for r in rows]

    def get_all_values(self):
        return [list(r) for r in self.rows]

    def get_all_records(self):
        head = self.rows[0]
        return [dict(zip(head, r)) for r in self.rows[1:]]

    def delete_rows(self, index):
        del self.rows[index - 1]


class ConfirmedNameTests(unittest.TestCase):
    def test_puwei_variants_map_to_parade(self):
        with patch.object(p, 'get_code_map', return_value={'4966': '譜瑞-KY', '8249': '精元'}):
            for heard in ('普威', '普位', '譜位'):
                with self.subTest(heard=heard):
                    self.assertEqual(p.resolve_code(heard, '')[:2], ('4966', '譜瑞-KY'))

    def test_xijinyuan_is_industry_not_a_homophone_stock(self):
        with patch.object(p, 'get_code_map') as official:
            code, name, how = p.resolve_code('細金元', '')
        official.assert_not_called()
        self.assertEqual(code, p.REJECT)
        self.assertIn('矽晶圓', how)

    def test_code_matching_drops_xijinyuan_row(self):
        sig = empty(); sig['watch_avoid'] = [{'name': '細金元', 'code': '', 'reason': '不准碰'}]
        with patch.object(p, 'get_code_map', return_value={'8249': '精元', '3066': '李洲'}):
            out = p.resolve_signals(sig, '細金元不准給我碰')
        self.assertEqual(out['watch_avoid'], [])

    def test_puwei_holding_is_kept_as_parade(self):
        sig = empty(); sig['holdings'] = [{'name': '普位', 'code': '', 'note': '現在跌兩塊'}]
        with patch.object(p, 'get_code_map', return_value={'4966': '譜瑞-KY', '5269': '祥碩'}):
            out = p.resolve_signals(sig, '比如說祥碩、比如說普位，普位現在跌兩塊')
        self.assertEqual((out['holdings'][0]['name'], out['holdings'][0]['code']), ('譜瑞-KY', '4966'))
        self.assertIn('普位', out['holdings'][0]['aliases'])

    def test_payload_tells_model_about_confirmed_names(self):
        data = json.loads(p.assessment_payload('2026/09/10', p.source_segments('細金元不准給我碰。手中還有普位。')))
        self.assertEqual(data['confirmed_industries']['細金元'], '矽晶圓')
        self.assertEqual(data['confirmed_names']['普位'], ['4966', '譜瑞-KY'])
        self.assertNotIn('加折', data['confirmed_names'])  # 原文未用的別名不佔 JSON 輸入預算
        self.assertIn('普位', p.POLICY); self.assertIn('細金元', p.POLICY)

    def test_manual_entry_prefix_matches_apps_script(self):
        self.assertEqual(p.MANUAL_ENTRY_PREFIX, 'MANUALENTRY-')
        gs = (ROOT / 'apps-script/Adminservice.gs').read_text(encoding='utf-8')
        self.assertIn('var MANUAL_ENTRY_PREFIX = "MANUALENTRY-"', gs)


class GateAfterCodesTests(unittest.TestCase):
    def test_buy_without_time_sentence_becomes_watch_watch(self):
        q = '四星KY我們會員買進了，現在先放著。'
        sig = empty()
        sig['buy'] = [{'name': '世芯-KY', 'code': '3661', '原始語音名稱': '四星KY', 'when': 'today',
                       'evidence': [q], 'time_evidence': '另一段講時間的話', 'price': '未說明',
                       'reason': '會員買進'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertFalse(sig['buy']); self.assertFalse(sig['history'])
        row = sig['watch_watch'][0]
        self.assertEqual((row['name'], row['code']), ('世芯-KY', '3661'))
        self.assertEqual(row['reason'], '會員買進。')
        self.assertNotIn('改列觀望注意', row['reason'])

    def test_watch_row_still_needs_price_quote(self):
        q = '四星KY我們會員買進了，現在先放著。'
        sig = empty()
        sig['buy'] = [{'name': '世芯-KY', 'code': '3661', '原始語音名稱': '四星KY', 'when': 'today',
                       'evidence': [q], 'time_evidence': '另一段講時間的話', 'price': '3900以下'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertEqual(sig['watch_watch'][0]['price'], '未說明')

    def test_sell_without_time_sentence_still_goes_to_history(self):
        q = '華城賣775塊的，現在華城726塊的。'
        sig = empty()
        sig['sell'] = [{'name': '華城', 'code': '1519', 'when': 'today', 'evidence': [q],
                        'time_evidence': '不在引用裡的時間句'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertFalse(sig['sell']); self.assertFalse(sig['watch_watch'])
        self.assertEqual(len(sig['history']), 1)

    def test_gate_after_codes_keeps_official_code_and_accepts_heard_name(self):
        q = '比如說想碩，想碩現在我們手上還有。'
        sig = empty()
        sig['holdings'] = [{'name': '祥碩', 'code': '5269', '原始語音名稱': '想碩',
                            'evidence': [q], 'note': '手上還有'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertEqual(len(sig['holdings']), 1)
        self.assertEqual(sig['holdings'][0]['code'], '5269')
        self.assertFalse(sig['uncertain'])

    def test_before_codes_model_code_still_needs_quote(self):
        q = '祥碩我們手上還有。'
        sig = empty(); sig['holdings'] = [{'name': '祥碩', 'code': '5269', 'evidence': [q]}]
        p.validate_evidence(sig, q, '2026/09/10')
        self.assertEqual(sig['holdings'][0]['code'], '')

    def test_stage_extract_runs_gate_after_code_matching(self):
        order = []
        sig = empty(); sig['holdings'] = [{'name': '台積電', 'code': '2330', 'note': '續抱'}]

        def rec(name):
            def f(signals, *a, **k):
                order.append(name)
                return signals
            return f

        with ExitStack() as st:
            for name in ('verify_names', 'resolve_signals', 'resolve_unclear_names',
                         'merge_duplicates', 'naturalize_signal_reasons'):
                st.enter_context(patch.object(p, name, side_effect=rec(name)))
            gate = st.enter_context(patch.object(p, 'validate_evidence', side_effect=rec('validate_evidence')))
            st.enter_context(patch.object(p, 'audit_signals',
                                          side_effect=lambda v, s, d: (order.append('audit_signals'), s)[1]))
            st.enter_context(patch.object(p, 'extract_signals', return_value=sig))
            st.enter_context(patch.object(p, 'existing_dates', return_value=set()))
            st.enter_context(patch.object(p, 'source_record_dates', return_value=set()))
            st.enter_context(patch.object(p, 'transcript_source_ids', return_value={'MANUAL-20260910'}))
            st.enter_context(patch.object(p, 'existing_video_rows', return_value=0))
            st.enter_context(patch.object(p, 'prior_published_rows', return_value=[]))
            for name in ('save_evidence_audit', 'flush_decisions', 'write_results',
                         'commit_evidence_manifest', 'save_refresh_checkpoint'):
                st.enter_context(patch.object(p, name))
            st.enter_context(patch.object(p, 'build_article', return_value='article'))
            p.stage_extract(Mock(), {'id': 'MANUAL-20260910'}, '2026/09/10', 'display', set(), set(),
                            replace_video=True, v1='我還有台積電，續抱。' * 60)
        self.assertLess(order.index('audit_signals'), order.index('resolve_signals'))
        self.assertLess(order.index('resolve_signals'), order.index('validate_evidence'))
        self.assertLess(order.index('validate_evidence'), order.index('resolve_unclear_names'))
        self.assertTrue(gate.call_args.kwargs.get('after_codes'))


class PolishMergeTests(unittest.TestCase):
    RAW = ('我 還 有 紅 準 紅 準 直 接 今 天 跌 兩 毛 那 我 懶 得 講 鴻 海 66.4 買 的 '
           '世 芯 譜 瑞 矽 晶 圓 原 字 保 留')
    MODEL = '我還有鴻準，鴻準直接今天跌兩毛，那我懶得講。鴻海66.4買的。世芯、譜瑞、矽晶圓原字保留。'

    def test_model_punctuation_kept_and_changed_names_restored(self):
        with patch.object(p, 'call_gemini', return_value=self.MODEL):
            out, degraded, line = p._polish_one(1, 1, self.RAW)
        self.assertFalse(degraded)
        self.assertEqual(out, '我還有紅準，紅準直接今天跌兩毛，那我懶得講。鴻海66.4買的。世芯、譜瑞、矽晶圓原字保留。')
        self.assertEqual(p._ev_norm(out), p._ev_norm(self.RAW))
        self.assertLess(len(out), len(self.RAW))
        self.assertIn('換回原文', line)

    def test_deleted_decimal_is_restored_with_its_point(self):
        model = '我還有紅準，紅準直接今天跌兩毛，那我懶得講。鴻海買的。世芯、譜瑞、矽晶圓原字保留。'
        with patch.object(p, 'call_gemini', return_value=model):
            out, degraded, _ = p._polish_one(1, 1, self.RAW)
        self.assertFalse(degraded)
        self.assertIn('66.4', out)
        self.assertEqual(p._ev_norm(out), p._ev_norm(self.RAW))

    def test_rewrite_falls_back_to_despaced_original(self):
        with patch.object(p, 'call_gemini', return_value='今天大盤不錯，大家加油。'):
            out, degraded, line = p._polish_one(1, 1, self.RAW)
        self.assertTrue(degraded)
        self.assertEqual(p._ev_norm(out), p._ev_norm(self.RAW))
        self.assertNotIn('我 還', out)

    def test_call_failure_falls_back_without_char_spaces(self):
        with patch.object(p, 'call_gemini', side_effect=RuntimeError('quota')):
            out, degraded, _ = p._polish_one(1, 1, self.RAW)
        self.assertTrue(degraded)
        self.assertNotIn('我 還', out)
        self.assertIn('66.4', out)

    def test_whole_polish_ratio_ignores_asr_spaces(self):
        raw = ' '.join('我還有台積電今天不動，會員續抱，等洗完再說。' * 120)
        with patch.object(p, 'call_gemini',
                          side_effect=lambda system, text, **k: p._despace_cjk(text)):
            out = p.polish(raw)
        self.assertLess(len(out), len(raw) * 0.7)
        self.assertEqual(p._ev_norm(out), p._ev_norm(raw))


class OverwriteTests(unittest.TestCase):
    HEAD = ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']

    def rows(self):
        return [self.HEAD,
                ['2026/09/10', '宏捷科', '8086', '觀望注意', '', '', 'YOUTUBEID1', 1],
                ['2026/09/10', '聖暉*', '5536', '觀望注意', '', '', 'MANUAL-20260910', 1],
                ['2026/09/10', '台積電', '2330', '會員持股', '', '', 'CMONEY-123', 1],
                ['2026/09/10', '補登的', '1234', '買入', '', '', 'MANUALENTRY-9', 1],
                ['2026/09/10', '核准的', '5678', '觀望注意', '', '', '人工補登', 1],
                ['2026/09/10', '空白來源', '9999', '觀望注意', '', '', '', 1],
                ['2026/09/09', '力積電', '6770', '賣出', '', '', 'YOUTUBE0909', 1]]

    def test_every_transcript_row_of_the_day_is_replaced(self):
        ws = FakeSheet(self.rows()); ss = Mock(); ss.worksheet.return_value = ws
        n = p._purge_transcript_rows_of_day(ss, '操作紀錄', '2026/09/10')
        self.assertEqual(n, 3)
        self.assertEqual([r[1] for r in ws.rows[1:]], ['台積電', '補登的', '核准的', '力積電'])

    def test_overwrite_guard_counts_the_same_rows(self):
        sheets = {'操作紀錄': FakeSheet(self.rows()), '會員持股': FakeSheet([self.HEAD])}
        ss = Mock(); ss.worksheet.side_effect = lambda name: sheets[name]
        self.assertEqual(p.existing_video_rows(ss, 'MANUAL-20260910', '2026/09/10'), 3)


class HistoryToWatchTests(unittest.TestCase):
    """日期未明或非當日的買賣：逐字稿另有這一檔現況看法才列入觀望，沒有就不列（2026/09/11）。"""

    def test_guoju_with_current_view_becomes_watch_avoid(self):
        sig = empty()
        sig['history'] = [{'name': '國巨*', 'code': '2327', '原始語音名稱': '國巨', 'action': 'sell',
                           'reason': '回顧過去在禮拜一叫人597以上賣一次國巨，屬於歷史回顧，列為觀望不碰。',
                           'view': '越想解套國巨越死，它一定會殺破', 'watch_bias': 'watch_avoid',
                           'view_evidence': ['你越想解套國巨，你就越死……它一定會殺破啊']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['history'])
        row = out['watch_avoid'][0]
        self.assertEqual((row['name'], row['code'], row['_date']), ('國巨*', '2327', '2026/09/10'))
        self.assertIn('禮拜一叫人597以上賣一次國巨', row['reason'])
        self.assertIn('它一定會殺破', row['reason'])
        for meta in ('歷史回顧', '列為觀望', '回顧過去', '日期未明'):
            self.assertNotIn(meta, row['reason'])

    def test_past_trade_without_current_view_is_not_listed(self):
        sig = empty()
        sig['history'] = [{'name': '索羅門', 'code': '', 'action': 'sell',
                           'reason': '回顧過去在大漲時賣出索羅門，屬於歷史回顧，列為觀望注意。',
                           'evidence': ['索羅門大漲的時候我賣掉了。']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['watch_watch']); self.assertFalse(out['watch_avoid'])
        self.assertFalse(out['history'])

    def test_direction_comes_from_view_not_from_past_sell(self):
        sig = empty()
        sig['history'] = [{'name': '力積電', 'code': '6770', 'action': 'sell', 'reason': '大漲三天賣掉',
                           'view': '拉回到季線會再買回來，年底前還有空間',
                           'view_evidence': ['力積電拉回到季線我會再買回來']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertEqual([r['name'] for r in out['watch_watch']], ['力積電'])
        self.assertFalse(out['watch_avoid'])

    def test_view_must_mention_the_stock(self):
        sig = empty()
        sig['history'] = [{'name': '華城', 'code': '1519', 'action': 'sell', 'reason': '華城賣775',
                           'view': '這一檔會殺破', 'watch_bias': 'watch_avoid',
                           'view_evidence': ['國巨一定會殺破']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['watch_avoid'])

    def test_view_refs_resolve_against_transcript(self):
        src = '華城賣775塊的，現在華城726塊的。華城我覺得還會再跌，不要去接。'
        segs = p.source_segments(src)
        sid = next(k for k, v in segs.items() if '不要去接' in v['text'])
        sig = empty()
        sig['history'] = [{'name': '華城', 'code': '1519', 'action': 'sell', 'reason': '華城賣775',
                           'view': '還會再跌，不要去接', 'watch_bias': 'watch_avoid', 'view_refs': [sid]}]
        out = p.history_to_watch(sig, '2026/09/10', transcript=src)
        self.assertEqual([r['name'] for r in out['watch_avoid']], ['華城'])

    def test_chinese_bias_label_accepted(self):
        sig = empty()
        sig['history'] = [{'name': '華城', 'code': '1519', 'watch_bias': '觀望不碰', 'view': '還會再跌',
                           'view_evidence': ['華城還會再跌']}]
        self.assertEqual(p.history_to_watch(sig, '2026/09/10')['watch_avoid'][0]['name'], '華城')

    def test_same_day_record_wins(self):
        sig = empty()
        sig['holdings'] = [{'name': '世芯-KY', 'code': '3661', 'note': '昨天買的', '_date': '2026/09/10'}]
        sig['history'] = [{'name': '世芯-KY', 'code': '3661', 'watch_bias': 'watch_avoid', 'evidence': ['世芯-KY']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['watch_avoid']); self.assertEqual(len(out['holdings']), 1)

    def test_sms_holding_blocks_watch_avoid(self):
        head = ['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID']
        sheets = {'會員持股': FakeSheet([head, ['2026/09/10', '國巨*', '2327', '持有', '', 'CMONEY-555']]),
                  '操作紀錄': FakeSheet([OverwriteTests.HEAD])}
        ss = Mock(); ss.worksheet.side_effect = lambda n: sheets[n]
        sig = empty()
        sig['history'] = [{'name': '國巨*', 'code': '2327', 'watch_bias': 'watch_avoid', 'evidence': ['國巨會殺破'],
                           'view': '會殺破', 'view_evidence': ['國巨會殺破']}]
        out = p.history_to_watch(sig, '2026/09/10', ss)
        self.assertFalse(out['watch_avoid']); self.assertFalse(out['history'])

    def test_stage_extract_publishes_history_as_watch(self):
        sig = empty()
        sig['history'] = [{'name': '國巨*', 'code': '2327', 'watch_bias': 'watch_avoid', 'evidence': ['國巨會殺破'],
                           'view': '會殺破', 'view_evidence': ['國巨會殺破']}]
        identity = lambda s, *a, **k: s
        with ExitStack() as st:
            for name in ('verify_names', 'resolve_signals', 'validate_evidence', 'resolve_unclear_names',
                         'merge_duplicates', 'naturalize_signal_reasons'):
                st.enter_context(patch.object(p, name, side_effect=identity))
            st.enter_context(patch.object(p, 'audit_signals', side_effect=lambda v, s, d: s))
            st.enter_context(patch.object(p, 'extract_signals', return_value=sig))
            st.enter_context(patch.object(p, 'existing_dates', return_value=set()))
            st.enter_context(patch.object(p, 'source_record_dates', return_value=set()))
            st.enter_context(patch.object(p, 'transcript_source_ids', return_value={'MANUAL-20260910'}))
            st.enter_context(patch.object(p, 'existing_video_rows', return_value=0))
            st.enter_context(patch.object(p, 'prior_published_rows', return_value=[]))
            for name in ('save_evidence_audit', 'flush_decisions', 'commit_evidence_manifest',
                         'save_refresh_checkpoint'):
                st.enter_context(patch.object(p, name))
            st.enter_context(patch.object(p, 'build_article', return_value='article'))
            writer = st.enter_context(patch.object(p, 'write_results'))
            p.stage_extract(Mock(), {'id': 'MANUAL-20260910'}, '2026/09/10', 'display', set(), set(),
                            replace_video=True, v1='國巨會殺破。' * 100)
        written = writer.call_args.args[2]
        self.assertEqual([r['name'] for r in written['watch_avoid']], ['國巨*'])
        self.assertFalse(written['history'])


class UnclearIndustryTests(unittest.TestCase):
    """名稱釐清：模型判為產業就剔除，但只限代號比對也對不上公司的名稱。"""

    def _run(self, rows, reply=None, fail=False):
        sig = empty(); sig['watch_watch'] = rows
        with patch.object(p, 'get_code_map', return_value={'5536': '聖暉*', '3533': '嘉澤'}), \
             patch.object(p, 'call_gemini', side_effect=RuntimeError('quota') if fail else None,
                          return_value=json.dumps(reply or [])):
            return p.resolve_unclear_names(sig, '')

    def test_unresolved_name_judged_industry_is_removed(self):
        rows = [{'name': '晶什麼', 'code': p.UNRESOLVED, 'evidence': ['晶什麼這個族群不要碰。']}]
        out = self._run(rows, [{'id': 1, 'kind': 'industry', 'code': '',
                                'quote': '晶什麼這個族群不要碰', 'why': '族群'}])
        self.assertEqual(out['watch_watch'], [])

    def test_matched_company_cannot_be_turned_into_industry(self):
        rows = [{'name': '聖暉*', 'code': '5536', '原始語音名稱': '聖輝', 'evidence': ['聖輝列在候選名單裡面。']}]
        out = self._run(rows, [{'id': 1, 'kind': 'industry', 'code': '',
                                'quote': '聖輝列在候選名單裡面', 'why': '誤判'}])
        self.assertEqual(len(out['watch_watch']), 1)

    def test_call_failure_keeps_code_matching_result(self):
        rows = [{'name': '聖暉*', 'code': '5536', '原始語音名稱': '聖輝', 'evidence': ['聖輝列在候選名單裡面。']}]
        out = self._run(rows, fail=True)
        self.assertEqual(out['watch_watch'][0]['code'], '5536')
        self.assertTrue(out['_quality_requires_review'])

    def test_xizhitai_is_removed_without_asking_ai(self):
        sig = empty(); sig['watch_watch'] = [{'name': '戲制台', 'code': p.UNRESOLVED}]
        with patch.object(p, 'get_code_map', return_value={}), patch.object(p, 'call_gemini') as ai:
            p.resolve_unclear_names(sig, '戲制台')
        ai.assert_not_called()
        self.assertEqual(sig['watch_watch'], [])
        with patch.object(p, 'get_code_map') as official:
            self.assertEqual(p.resolve_code('戲制台', '')[0], p.REJECT)
        official.assert_not_called()


class CleanMetaReasonTests(unittest.TestCase):
    def test_clean_meta_reason_user_example(self):
        raw = "張正在昨天（9月9日）大跌時買進四星KY，因確切交易日期為昨日而非影片當日，故改列歷史回顧。（日期未明的回顧（原為買入），依原文語氣判定列入觀望注意）。"
        # 2026/09/13 起公開說明不寫人名，句首的「張正」一併拿掉。
        expected = "在昨天（9月9日）大跌時買進四星KY。"
        self.assertEqual(p.clean_meta_reason(raw), expected)
        self.assertEqual(p.naturalize_reason(raw), expected)


class SafeLoadJsonTests(unittest.TestCase):
    def test_trailing_comma_repaired(self):
        # 測試使用者回報的 Expecting property name enclosed in double quotes (尾隨逗號)
        raw = '''{
            "buy": [
                {
                    "name": "力積電",
                    "code": "6770",
                    "reason": "大漲三天賣掉",
                },
            ],
            "sell": [],
            "holdings": [],
            "watch_avoid": [],
            "watch_watch": [],
            "history": [],
            "uncertain": [],
            "ignored": [],
            "market": [],
        }'''
        res = p.safe_load_json(raw)
        self.assertIsInstance(res, dict)
        self.assertEqual(res['buy'][0]['name'], '力積電')

    def test_markdown_wrapped_json(self):
        raw = '''這是分析結果：
```json
{"buy": [{"name": "世芯-KY", "code": "3661"}]}
```
請查收。'''
        res = p.safe_load_json(raw)
        self.assertEqual(res['buy'][0]['code'], '3661')

    def test_unescaped_control_characters_and_comments(self):
        raw = '''{
            // 說明
            "reason": "第一行\\n第二行",
            /* 多行註解 */
            "price": "未說明",
        }'''
        res = p.safe_load_json(raw)
        self.assertEqual(res['price'], '未說明')


class ReasonAndStanceTests(unittest.TestCase):
    def test_suoluomen_reason_keeps_only_the_fact(self):
        self.assertEqual(p.clean_meta_reason('回顧過去在大漲時賣出索羅門，屬於歷史回顧，列為觀望注意。'),
                         '大漲時賣出索羅門。')

    def test_commas_inside_brackets_are_not_clause_breaks(self):
        self.assertEqual(p.clean_meta_reason('昨天（9月9日，盤中）買進世芯-KY'), '昨天（9月9日，盤中）買進世芯-KY。')

    def test_model_placed_retrospective_goes_back_to_history(self):
        sig = empty()
        sig['watch_watch'] = [{'name': '索羅門', 'reason': '回顧過去在大漲時賣出索羅門，屬於歷史回顧，列為觀望注意。'},
                              {'name': '聖暉*', 'reason': '列在候選名單，等洗完'}]
        p.demote_watch_retrospectives(sig)
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['聖暉*'])
        self.assertEqual([r['name'] for r in sig['history']], ['索羅門'])
        # 退回之後沒有現況看法，就不會再出現在網站上
        self.assertFalse(p.history_to_watch(sig, '2026/09/10')['watch_watch'][1:])

    def test_english_stance_becomes_chinese(self):
        for raw, want in (('long', '續抱'), ('Hold', '續抱'), ('', '未說明'), ('續抱', '續抱'), ('something', '持有')):
            with self.subTest(raw=raw):
                self.assertEqual(p.stance_zh(raw), want)

    def test_blank_holding_note_falls_back_to_reason(self):
        sig = empty()
        sig['holdings'] = [{'name': '祥碩', 'stance': 'long', 'note': '', 'reason': '等它噴出去，不管漲跌10塊'}]
        p.naturalize_signal_reasons(sig)
        self.assertEqual(sig['holdings'][0]['stance'], '續抱')
        self.assertIn('等它噴出去', sig['holdings'][0]['note'])

    def test_prompt_no_longer_maps_past_action_to_watch_direction(self):
        self.assertNotIn('原為賣出一律', p.POLICY)
        self.assertNotIn('依原文語氣判定應該為列入觀望不碰', p.POLICY)
        self.assertNotIn('非當日操作直接進觀望', p.POLICY)
        self.assertIn('日期未明與現況看法', p.POLICY)


class RefreshWatchTests(unittest.TestCase):
    def test_rows_lost_during_refresh_are_reported_with_the_step(self):
        head = ['日期', '股票名稱', '代號', '方向', '價位說明', '理由摘錄', '來源影片ID', '序']
        trades = FakeSheet([head, ['2026/09/10', '聖暉*', '5536', '觀望注意', '', '', 'MANUAL-20260910', 1],
                                  ['2026/09/10', '嘉澤', '3533', '觀望注意', '', '', 'MANUAL-20260910', 2]])
        holds = FakeSheet([['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID']])
        ss = Mock(); ss.worksheet.side_effect = lambda n: {'操作紀錄': trades, '會員持股': holds}[n]

        def refresh(only, force, date_str):
            if only == ['codes']:
                trades.delete_rows(3)          # 模擬某一步把「嘉澤」刪掉
            return {'ok': True}

        with patch.object(p, 'load_refresh_checkpoint', return_value=None), \
             patch.object(p, 'save_refresh_checkpoint'), patch.object(p, 'flush_decisions'), \
             patch.object(p, 'maybe_refresh_site', side_effect=refresh):
            result = p.finish_transcript_refresh(ss, 'MANUAL-20260910', '2026/09/10', 'raw', ['2026/09/10'])
        self.assertTrue(result['ok'])
        self.assertEqual([x['step'] for x in result['lost']], ['codes'])
        self.assertIn('嘉澤', result['lost'][0]['rows'])


class TranscriptComparisonTests(unittest.TestCase):
    """2026/09/10、09/11 兩份原始逐字稿與人工分類比對後補的規則。"""

    def test_homophone_name_in_evidence_passes_gate(self):
        q = '漢糖1000塊以下才可以買，漢堂1000以下是買點。'
        sig = empty()
        sig['watch_watch'] = [{'name': '漢唐', 'code': '2404', 'evidence': [q], 'reason': '1000以下是買點'}]
        p.validate_evidence(sig, q, '2026/09/11', after_codes=True)
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['漢唐'])
        self.assertFalse(sig['uncertain'])

    def test_spoken_code_in_evidence_passes_gate(self):
        q = '再給你看5289移頂，我們宜蘭人的驕傲，一定要等他補完這個缺口。'
        sig = empty()
        sig['watch_watch'] = [{'name': '宜鼎', 'code': '5289', '原始語音名稱': '宜頂', 'evidence': [q],
                               'reason': '等補完缺口再站回去'}]
        p.validate_evidence(sig, q, '2026/09/11', after_codes=True)
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['宜鼎'])

    def test_market_examples_remain_watch_v6(self):
        sig = empty()
        sig['watch_watch'] = [{'name': '瑞昱', 'code': '2379', 'reason': '節目中回顧其這三個月停在這邊沒有動的走勢情況。'},
                              {'name': '宇瞻', 'code': '8271', 'reason': '節目中回顧其買了三個月至一個半月停在這邊的情況。'},
                              {'name': '新代', 'code': '7750', 'reason': '候選名單，等洗完可以抄起來'}]
        p.demote_watch_examples(sig)
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['瑞昱', '宇瞻', '新代'])
        self.assertEqual(sig['ignored'], [])

    def test_past_sells_placed_in_watch_are_dropped(self):
        sig = empty()
        sig['watch_avoid'] = [{'name': '華城', 'code': '1519', 'reason': '過去賣出華城在775塊。'},
                              {'name': '力積電', 'code': '6770', 'reason': '過去大漲三天時已將利基電賣掉。'}]
        sig['watch_watch'] = [{'name': '威剛', 'code': '3260',
                               'reason': '回顧過去在415以上大漲時通知會員賣掉威剛，屬於歷史回顧，列為觀望注意。'}]
        p.demote_watch_retrospectives(sig)
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['watch_avoid']); self.assertFalse(out['watch_watch'])

    def test_past_trade_with_current_directive_is_kept(self):
        sig = empty()
        sig['watch_watch'] = [{'name': '勤誠', 'code': '8210', '原始語音名稱': '秦成',
                               'reason': '過去曾買進800多後賣出；900以下是買點，第四季伺服器出貨會很好',
                               'evidence': ['秦成900塊錢以下才可以買，秦900以下是買點。']}]
        p.demote_watch_retrospectives(sig)
        self.assertEqual([r['name'] for r in sig['history']], ['勤誠'])     # 先退回，再依指示列回來
        out = p.history_to_watch(sig, '2026/09/11')
        self.assertEqual([r['name'] for r in out['watch_watch']], ['勤誠'])
        self.assertIn('900以下是買點', out['watch_watch'][0]['reason'])

    def test_more_meta_clauses_removed(self):
        self.assertEqual(p.clean_meta_reason('昨天買進四星KY，但因屬昨天歷史買進非影片當日執行，依規則列入觀望注意。'),
                         '昨天買進四星KY。')

    def test_prompt_rules_from_comparison(self):
        for gone in ('或非當日買入之回顧', '或非當日賣出之回顧'):
            self.assertNotIn(gone, p.POLICY)
        for rule in ('秦成', '股王', '還沒有的人', '原文點名的行情或法人例子也逐檔列觀望', '你們都知道我有'):
            self.assertIn(rule, p.POLICY)


class OtherDaysSafetyTests(unittest.TestCase):
    """覆蓋只能針對當天：2026/09/11 重跑 09/10 把 09/11 的操作紀錄蓋掉了。"""

    def test_append_inserts_rows_instead_of_overwriting(self):
        ws = Mock()
        p.append_rows_safe(ws, [['2026/09/10', '嘉澤']])
        self.assertEqual(ws.append_rows.call_args.kwargs['insert_data_option'], 'INSERT_ROWS')
        ws.append_rows.reset_mock()
        p.append_rows_safe(ws, [])
        ws.append_rows.assert_not_called()

    def test_no_direct_append_left_in_pipeline(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        body = src.split('def append_rows_safe', 1)[1]
        self.assertEqual(src.count('.append_rows,'), 1)          # 只剩 append_rows_safe 裡那一個
        self.assertIn('.append_rows,', body.split('def _rows_per_day', 1)[0])

    def test_guard_stops_when_another_day_loses_rows(self):
        head = OverwriteTests.HEAD
        trades = FakeSheet([head, ['2026/09/11', '信驊', '5274', '觀望不碰', '', '', 'MANUAL-20260911', 1],
                                  ['2026/09/11', '川湖', '2059', '觀望不碰', '', '', 'MANUAL-20260911', 2]])
        holds = FakeSheet([['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID']])
        ss = Mock(); ss.worksheet.side_effect = lambda n: {'操作紀錄': trades, '會員持股': holds}[n]
        before = {s: p._rows_per_day(ss, s) for s in ('操作紀錄', '會員持股')}
        trades.delete_rows(3)                      # 模擬 09/11 的一列被蓋掉
        with self.assertRaisesRegex(RuntimeError, '2026/09/11'):
            p._guard_other_days(ss, before, {'2026/09/10'}, '2026/09/10')

    def test_guard_ignores_the_day_being_rewritten(self):
        head = OverwriteTests.HEAD
        trades = FakeSheet([head, ['2026/09/10', '嘉澤', '3533', '觀望注意', '', '', 'MANUAL-20260910', 1]])
        holds = FakeSheet([['日期', '股票名稱', '代號', '目前立場', '說明重點', '來源影片ID']])
        ss = Mock(); ss.worksheet.side_effect = lambda n: {'操作紀錄': trades, '會員持股': holds}[n]
        before = {s: p._rows_per_day(ss, s) for s in ('操作紀錄', '會員持股')}
        trades.delete_rows(2)
        p._guard_other_days(ss, before, {'2026/09/10'}, '2026/09/10')     # 當天少了是正常的


class ViewAndPriceQualityTests(unittest.TestCase):
    def test_past_price_is_not_a_current_view(self):
        sig = empty()
        sig['history'] = [{'name': '華城', 'code': '1519', 'action': 'sell', 'reason': '華城賣775塊',
                           'view': '華城賣775塊，現在726', 'watch_bias': 'watch_watch',
                           'view_evidence': ['華城賣775塊的，現在華城726塊的。']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertFalse(out['watch_watch']); self.assertFalse(out['watch_avoid'])

    def test_price_kept_when_number_sits_next_to_the_name(self):
        q = '我有沒有告訴你38x你們就只要3900塊以下，我說我絕對不買四字頭的四星嘛。'
        sig = empty()
        sig['watch_watch'] = [{'name': '世芯-KY', 'code': '3661', '原始語音名稱': '四星KY', 'evidence': [q],
                               'price': '3900以下', 'reason': '38X、3900以下才買'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertEqual(sig['watch_watch'][0]['price'], '3900以下')

    def test_price_from_another_stock_is_still_cleared(self):
        q = '嘉澤我們8月25號買的。另外那一檔你沒有破900我不想買。'
        sig = empty()
        sig['watch_watch'] = [{'name': '嘉澤', 'code': '3533', 'evidence': [q], 'price': '900以下',
                               'reason': '等低檔'}]
        p.validate_evidence(sig, q, '2026/09/10', after_codes=True)
        self.assertEqual(sig['watch_watch'][0]['price'], '未說明')

    def test_prompt_asks_for_specific_reasons(self):
        # v25 起說明改短：1～3 句、約 40～120 字，第一句先講重點（2026/09/16）。
        for rule in ('約 40～120 字', '不可以搬進這一檔的說明', '華城賣775塊，現在726'):
            self.assertIn(rule, p.POLICY)


class ArticleHoldingAndReadTests(unittest.TestCase):
    """2026/09/11 重跑後的信件與持股：③ 空白、⑤ 只有一點、聯發科被列成持股、Sheets 429。"""

    def test_market_and_lessons_go_to_different_chapters(self):
        sig = empty()
        sig['market'] = [
            {'kind': 'level', 'text': '指數關卡46188，失守再看454XX。', '_evidence_verified': True},
            {'kind': 'view', 'text': '下跌不賣、大漲才賣：低檔的股票跌也不賣，大漲時才賣。', '_evidence_verified': True},
            {'kind': 'view', 'text': '看大戶持股：400張以上大戶增加就不用擔心。', '_evidence_verified': True}]
        out = p.canonical_article(sig, '2026/09/11')
        # v22 起盤勢是 ①、教學是 ③（標題不編號、沒有基本資訊）
        third = out.split('① 盤勢總覽', 1)[1].split('② 會員操作紀錄', 1)[0]
        fifth = out.split('③ 分析師操作邏輯', 1)[1]   # v44 起 ③ 是最後一章
        self.assertIn('46188', third); self.assertNotIn('下跌不賣', third)
        self.assertIn('下跌不賣', fifth); self.assertIn('看大戶持股', fifth)

    def test_analogy_mention_is_not_a_holding(self):
        q = '台積電紅海紅海哦四星KY大立光都在聯發科都在這一顆球裡面都在這一顆球裡面'
        sig = empty()
        sig['holdings'] = [
            {'name': '聯發科', 'code': '2454', 'note': '影片中提及聯發科在大球（大環境）裡面。', 'evidence': [q]},
            {'name': '鴻準', 'code': '2354', 'note': '張正表示自己持有紅準，大戶持續增加。',
             'evidence': ['你們都知道我有這一隻啊紅準']}]
        p.demote_holding_mentions(sig)
        self.assertEqual([r['name'] for r in sig['holdings']], ['鴻準'])
        self.assertEqual([r['name'] for r in sig['watch_watch']], ['聯發科'])

    def test_holding_kept_when_owning_words_sit_next_to_the_name(self):
        q = '你們都知道我有這一隻啊紅準這邊買進啊到現在也沒賺多'
        sig = empty()
        sig['holdings'] = [{'name': '鴻準', 'code': '2354', '原始語音名稱': '紅準', 'note': '大戶一直在增加',
                            'evidence': [q]}]
        p.demote_holding_mentions(sig)
        self.assertEqual(len(sig['holdings']), 1)

    def test_one_batch_read_for_both_record_sheets(self):
        ss = Mock()
        ss.values_batch_get.return_value = {'valueRanges': [
            {'values': [['日期', '股票名稱'], ['2026/09/11', '川湖']]},
            {'values': [['日期', '股票名稱'], ['2026/09/11', '台積電'], ['2026/09/10', '祥碩']]}]}
        counts = p._day_counts(ss)
        self.assertEqual(counts['操作紀錄']['2026/09/11'], 1)
        self.assertEqual(counts['會員持股']['2026/09/10'], 1)
        self.assertEqual(ss.values_batch_get.call_count, 1)
        ss.worksheet.assert_not_called()

    def test_refresh_recounts_only_after_steps_that_can_delete(self):
        data = {'操作紀錄': [['日期', '股票名稱', '方向', '來源影片ID']], '會員持股': [['日期', '股票名稱']]}
        with patch.object(p, '_record_sheet_values', return_value=data) as reads, \
             patch.object(p, 'load_refresh_checkpoint', return_value=None), \
             patch.object(p, 'save_refresh_checkpoint'), \
             patch.object(p, 'maybe_refresh_site', return_value={'ok': True}):
            p.finish_transcript_refresh(Mock(), 'MANUAL-20260911', '2026/09/11', 'raw', ['2026/09/11'])
        self.assertEqual(reads.call_count, 3)          # 開始一次、同步郵件後一次、代號比對後一次

    def test_prompt_rules_from_0911_rerun(self):
        for rule in ('觀念標題', '只在比喻或舉例被點名，不代表持有', '不得自行拆成上下界', '這些公司以後都會漲回去'):
            self.assertIn(rule, p.POLICY)


class DailyMailOnceTests(unittest.TestCase):
    """重跑之後一直寄信：整天重來把每日推播內容那一列刪掉、再新增一列「待寄送」（2026/09/11）。"""

    class MailSheet(FakeSheet):
        def update_cell(self, r, c, v):
            row = self.rows[r - 1]
            row.extend([''] * (c - len(row)))
            row[c - 1] = v

        def append_row(self, values, **kw):
            self.rows.append(list(values))

    def sheet(self, status):
        ws = self.MailSheet([['日期', '文字稿', '寄送狀態'],
                             ['2026/09/10', '前一天的文章', '已寄送'],
                             ['2026/09/11', '第一版文章', status]])
        ss = Mock(); ss.worksheet.return_value = ws
        return ws, ss

    def test_whole_day_rerun_keeps_sent_status(self):
        ws, ss = self.sheet('已寄送')
        prior = p._daily_article_status(ss, '2026/09/11')
        p.delete_rows_for_date(ss, '每日推播內容', '2026/09/11')
        p._upsert_daily_article(ss, '2026/09/11', '第二版文章', prior_sent=prior)
        self.assertEqual(ws.rows[1], ['2026/09/10', '前一天的文章', '已寄送'])      # 別天不動
        self.assertEqual(ws.rows[-1], ['2026/09/11', '第二版文章', '已寄送'])

    def test_rerun_in_place_never_resets_sent(self):
        ws, ss = self.sheet('已寄送')
        p._upsert_daily_article(ss, '2026/09/11', '第二版文章')
        self.assertEqual(ws.rows[2], ['2026/09/11', '第二版文章', '已寄送'])

    def test_first_article_of_the_day_is_pending(self):
        ws, ss = self.sheet('')
        del ws.rows[2]
        p._upsert_daily_article(ss, '2026/09/11', '第一版文章')
        self.assertEqual(ws.rows[-1], ['2026/09/11', '第一版文章', '待寄送'])

    def test_unsent_day_stays_sendable_after_rerun(self):
        ws, ss = self.sheet('待寄送')
        prior = p._daily_article_status(ss, '2026/09/11')
        p.delete_rows_for_date(ss, '每日推播內容', '2026/09/11')
        p._upsert_daily_article(ss, '2026/09/11', '第二版文章', prior_sent=prior)
        self.assertEqual(ws.rows[-1][2], '待寄送')

    def test_status_is_read_before_the_row_is_deleted(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        body = src.split('def write_results', 1)[1]
        self.assertLess(body.index('prior_sent = _daily_article_status'),
                        body.index('delete_rows_for_date(ss, "每日推播內容"'))
        self.assertNotIn('keep_sent', src)


class RecallFrom0910Tests(unittest.TestCase):
    """2026/09/10 重跑只收到 4 檔：持股、名單、國巨的現況看法都被漏掉或退掉。"""

    def test_misheard_name_in_view_quote_still_counts(self):
        sig = empty()
        sig['history'] = [{'name': '國巨*', 'code': '2327', 'action': 'sell', 'watch_bias': 'watch_avoid',
                           'reason': '週一國巨漲到605時提醒597外資成本以上要賣一次',
                           'view': '越想解套越死，他一定會殺破',
                           'view_evidence': ['有人買國具不知道怎麼辦的一直來找我說怎麼辦我說我也不知道他一定會殺破啊']}]
        out = p.history_to_watch(sig, '2026/09/10')
        self.assertEqual([r['name'] for r in out['watch_avoid']], ['國巨*'])
        self.assertIn('殺破', out['watch_avoid'][0]['reason'])

    def test_still_owned_wording_keeps_holdings(self):
        sig = empty()
        sig['holdings'] = [
            {'name': '鴻準', 'code': '2354', '原始語音名稱': '紅準', 'note': '今天跌兩毛',
             'evidence': ['對我還有紅準紅準直接今天跌兩毛那我懶得講啊']},
            {'name': '台積電', 'code': '2330', 'note': '三個月都沒有漲',
             'evidence': ['我還沒有賣哦張正沒有叫會員賣半張台積電哦我從1820,2025,2135買到現在哦']}]
        p.demote_holding_mentions(sig)
        self.assertEqual([r['name'] for r in sig['holdings']], ['鴻準', '台積電'])

    def test_roster_lists_names_per_category(self):
        sig = empty()
        sig['holdings'] = [{'name': '祥碩'}, {'name': '譜瑞-KY'}]
        sig['history'] = [{'name': '華城'}]
        line = p.signal_roster(sig)
        self.assertIn('持股 2：祥碩、譜瑞-KY', line); self.assertIn('回顧 1：華城', line)
        self.assertEqual(p.signal_roster(empty()), '一檔都沒有')

    def test_prompt_has_one_rule_for_past_trades(self):
        for gone in ('直接列入 watch_avoid（觀望不碰）！', '亦不再進 history', '不要直接放進觀望類'):
            self.assertNotIn(gone, p.POLICY)
        for rule in ('先盤點，再分類', '比如說想碩、比如說普位', '聖輝、新代、加折還有木德',
                     '越想解套國巨你就越死', '華城賣775塊，現在726'):
            self.assertIn(rule, p.POLICY)
        self.assertIn('先盤點，再分類', p.EXTRACT_SYSTEM); self.assertIn('先盤點，再分類', p.AUDIT_SYSTEM)


class JiazeAndMisheardPriceTests(unittest.TestCase):
    """2026/09/10 第二次重跑：加折被寫成家登、國巨的 597 因為原句寫「國具」被清掉。"""

    def test_confirmed_heard_name_beats_model_rewrite(self):
        self.assertEqual(p.resolve_code('加折', '')[:2], ('3533', '嘉澤'))
        sig = empty()
        sig['watch_watch'] = [{'name': '家登', 'code': '3680', 'aliases': ['加折'], 'reason': '候選名單'}]
        with patch.object(p, 'arbitrate_name_code', return_value=None), \
             patch.object(p, 'get_code_map', return_value={'3533': '嘉澤', '3680': '家登'}):
            out = p.resolve_signals(sig, '聖輝新代加折還有木德')
        row = out['watch_watch'][0]
        self.assertEqual((row['name'], row['code']), ('嘉澤', '3533'))

    def test_real_jiadeng_is_untouched(self):
        sig = empty()
        sig['watch_watch'] = [{'name': '家登', 'code': '3680', 'aliases': [], 'reason': '候選名單'}]
        with patch.object(p, 'arbitrate_name_code', return_value=None), \
             patch.object(p, 'get_code_map', return_value={'3533': '嘉澤', '3680': '家登'}):
            out = p.resolve_signals(sig, '家登我會買')
        self.assertEqual(out['watch_watch'][0]['code'], '3680')

    def test_price_next_to_misheard_name_is_kept(self):
        self.assertTrue(p._price_near_name('597', ['597以上要賣一次國具這是外資成本'], ['國巨*']))
        self.assertFalse(p._price_near_name('597', ['597以上要賣一次這是外資成本'], ['國巨*']))

    def test_prompt_names_jiaze_and_yesterday_holding(self):
        for rule in ('加折、加哲、加澤＝嘉澤（3533），不是家登', '我昨天買的四星KY，今天漲30幾塊',
                     '8月25號1580以下買加折'):
            self.assertIn(rule, p.POLICY)

    def test_etf_sell_off_stock_is_qincheng(self):
        # 「ETF昨天來初清程」「出金城」：語音把「出清勤誠」聽成這兩種寫法（2026/09/10）。
        for heard in ('出金城', '初清程'):
            self.assertEqual(p.resolve_code(heard, '')[:2], ('8210', '勤誠'))
        self.assertIn('出金城、初清程＝勤誠（8210）', p.POLICY)
        self.assertIn('同一段真的沒有名稱才放 ignored', p.POLICY)

    def test_held_stock_also_watched_for_non_holders(self):
        for rule in ('台積電只要碰到這一條線你們就去注意', '你要先買四星KY', '還沒有的人'):
            self.assertIn(rule, p.POLICY)


if __name__ == '__main__':
    unittest.main()
