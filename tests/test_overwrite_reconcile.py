"""覆蓋前逐檔核對前一版（2026/09/13），取代「新筆數少於舊筆數就整批保留」。

真實情境：9/10 重跑驗證出 19 筆，前一版 22 筆，被整批擋下。
少的多半是分類變準；真的漏掉的是譜瑞-KY 持股，00981A 被排除。
"""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from test_quality import p, empty
import test_overwrite_0913 as overwrite_tests      # 匯入模組而非類別，避免被 unittest 重複收集

D, VID = overwrite_tests.D, overwrite_tests.VID

W, A, H = 'watch_watch', 'watch_avoid', 'holdings'

# 前一版郵件的 22 筆（9/10，quality-v6 產出）
PRIOR = ([{'_cat': H, 'name': n, 'code': c, 'stance': '續抱', 'note': note} for n, c, note in (
            ('祥碩', '5269', '會員持有'), ('台積電', '2330', '一路抱'),
            ('譜瑞-KY', '4966', '張正昨天在3850附近大跌時買進四星KY'), ('鴻準', '2354', '還有宏準'))]
         + [{'_cat': A, 'name': '國巨*', 'code': '2327', 'price': '597', 'reason': '一定會殺破', '_seq': 1}]
         + [{'_cat': W, 'name': n, 'code': c, 'price': '未說明', 'reason': '觀察', '_seq': 1} for n, c in (
            ('台積電', '2330'), ('譜瑞-KY', '4966'), ('聖暉*', '5536'), ('新代', '7750'), ('嘉澤', '3533'),
            ('牧德', '3563'), ('主動統一台股增長', '00981A'), ('瑞昱', '2379'), ('宇瞻', '8271'),
            ('廣達', '2382'), ('微星', '2377'), ('萬海', '2615'), ('華邦電', '2344'), ('群創', '3481'),
            ('聯電', '2303'), ('勤誠', '8210'), ('大立光', '3008'))])


def new_run():
    """這一輪日誌的結果：持股 5、觀望注意 4、觀望不碰 10、回顧 2、排除 4。"""
    sig = empty()
    sig[H] = [{'name': n, 'code': c, 'note': '持有'} for n, c in (
        ('祥碩', '5269'), ('台積電', '2330'), ('世芯-KY', '3661'), ('嘉澤', '3533'), ('鴻準', '2354'))]
    sig[W] = [{'name': n, 'code': c, 'reason': '候選'} for n, c in (
        ('勤誠', '8210'), ('聖暉*', '5536'), ('新代', '7750'), ('牧德', '3563'))]
    sig[A] = [{'name': n, 'code': c, 'reason': '追高容易套牢'} for n, c in (
        ('國巨*', '2327'), ('廣達', '2382'), ('微星', '2377'), ('萬海', '2615'), ('群創', '3481'),
        ('聯電', '2303'), ('大立光', '3008'), ('瑞昱', '2379'), ('宇瞻', '8271'), ('華邦電', '2344'))]
    sig['history'] = [{'name': '華城'}, {'name': '力積電'}]
    sig['ignored'] = [{'name': n} for n in ('日幣', '主動統一台股增長ETF', '矽晶圓', '矽智財')]
    return sig


def fake_resolve(name, hint):
    return {'主動統一台股增長ETF': ('00981A', '主動統一台股增長', '管理者確認'),
            '華城': ('1519', '華城', '命中'), '力積電': ('6770', '力積電', '命中')}.get(
        name, (p.UNRESOLVED, name, ''))


class ReconcileTests(unittest.TestCase):
    def reconcile(self, sig, prior=PRIOR):
        with patch.object(p, 'resolve_code', side_effect=fake_resolve):
            return p.reconcile_with_prior(sig, [dict(r) for r in prior], D)

    def test_0910_rerun_publishes_new_result_and_carries_only_unaccounted(self):
        sig = new_run()
        rec = self.reconcile(sig)
        self.assertFalse(rec['anomaly'])
        self.assertEqual(rec['identities'], 20)
        self.assertEqual(rec['carried'], ['譜瑞-KY（會員持股、觀望注意，本輪沒有收錄）',
                                          '主動統一台股增長（觀望注意，本輪列為排除）'])
        self.assertIn('台積電：會員持股、觀望注意 → 會員持股', rec['changes'])
        self.assertIn('嘉澤：觀望注意 → 會員持股', rec['changes'])
        self.assertIn('廣達：觀望注意 → 觀望不碰', rec['changes'])
        carried = [r for c in p.SIGNAL_CATEGORIES for r in sig[c] if r.get('_carried_forward')]
        self.assertEqual(sorted((r['name'], r['code']) for r in carried),
                         [('主動統一台股增長', '00981A'), ('譜瑞-KY', '4966'), ('譜瑞-KY', '4966')])
        self.assertEqual(sum(len(sig[c]) for c in p.SIGNAL_CATEGORIES), 22)   # 本輪 19 + 沿用 3 列
        note = next(r['note'] for r in sig[H] if r['name'] == '譜瑞-KY')
        self.assertEqual(note, '昨天在3850附近大跌時買進四星KY。')              # 沿用時一樣拿掉人名

    def test_history_is_accepted_removal_not_carried(self):
        sig = empty(); sig['history'] = [{'name': '華城'}]
        prior = [{'_cat': W, 'name': '華城', 'code': '1519', 'price': '未說明', 'reason': '賣775', '_seq': 1}]
        rec = self.reconcile(sig, prior)
        self.assertEqual(rec['carried'], []); self.assertEqual(len(rec['accepted']), 1)
        self.assertFalse(sig[W])

    def test_many_missing_is_anomaly_and_nothing_carried(self):
        sig = empty(); sig[H] = [{'name': '台積電', 'code': '2330'}]
        rec = self.reconcile(sig, PRIOR[1:2] + PRIOR[4:9])     # 台積電之外 5 檔全部不見
        self.assertTrue(rec['anomaly'])
        self.assertFalse(any(r.get('_carried_forward') for c in p.SIGNAL_CATEGORIES for r in sig[c]))

    def test_one_or_two_missing_is_not_anomaly(self):
        sig = empty(); sig[H] = [{'name': '台積電', 'code': '2330'}]
        rec = self.reconcile(sig, PRIOR[1:2] + PRIOR[4:5])
        self.assertFalse(rec['anomaly']); self.assertEqual(len(rec['carried']), 1)

    def test_editorial_gaps_do_not_count_as_review(self):
        gaps = ['教學內容偏短：…', '盤勢內容偏短：…', '台積電說明偏短：…', '大盤摘要有未驗證的引用或數字：…',
                'holdings[0] 請用正確 evidence_refs 定位原句', '初稿候選消失：普位', '排除覆核：主動統一台股增長ETF …']
        self.assertEqual(p.needs_review_gaps(gaps), gaps[4:])


class StageExtractReconcileTests(unittest.TestCase):
    def run_stage(self, sig, prior):
        identity = lambda s, *a, **k: s
        with ExitStack() as st:
            for name in ('verify_names', 'resolve_signals', 'validate_evidence', 'resolve_unclear_names',
                         'merge_duplicates', 'naturalize_signal_reasons', 'history_to_watch'):
                st.enter_context(patch.object(p, name, side_effect=identity))
            def when(ss, s, d):   # 真實的日期歸屬一定會給每一列 _date
                for c in p.SIGNAL_CATEGORIES:
                    for r in s.get(c) or []:
                        r.setdefault('_date', d)
                return s
            st.enter_context(patch.object(p, 'apply_when_and_seq', side_effect=when))
            st.enter_context(patch.object(p, 'extract_signals', return_value=sig))
            audit = st.enter_context(patch.object(p, 'audit_signals', side_effect=lambda v, s, d: s))
            st.enter_context(patch.object(p, 'existing_dates', return_value=set()))
            st.enter_context(patch.object(p, 'source_record_dates', return_value=set()))
            st.enter_context(patch.object(p, 'transcript_source_ids', return_value={VID}))
            st.enter_context(patch.object(p, 'prior_published_rows', return_value=prior))
            st.enter_context(patch.object(p, 'resolve_code', side_effect=fake_resolve))
            for name in ('save_evidence_audit', 'flush_decisions', 'note_decision', 'commit_evidence_manifest'):
                st.enter_context(patch.object(p, name))
            checkpoint = st.enter_context(patch.object(p, 'save_refresh_checkpoint'))
            st.enter_context(patch.object(p, 'build_article', return_value='article'))
            writer = st.enter_context(patch.object(p, 'write_results'))
            outcome = p.stage_extract(Mock(), {'id': VID}, D, 'display', set(), set(),
                                      replace_video=True, v1='原文' * 300 + '比如說普位。00981A換股。')
        return outcome, writer, checkpoint, audit

    def test_0910_rerun_writes_and_reports_review(self):
        outcome, writer, checkpoint, audit = self.run_stage(new_run(), [dict(r) for r in PRIOR])
        self.assertFalse(outcome.retained)
        self.assertIn('沿用前一版 2 檔待複核', outcome.review)
        written = writer.call_args.args[2]
        self.assertEqual(sum(len(written[c]) for c in p.SIGNAL_CATEGORIES), 22)
        self.assertIn('沿用前一版 2 檔待複核', checkpoint.call_args.kwargs['review'])
        # 前一版清單交給覆核逐一重新判定
        self.assertIn('譜瑞-KY（會員持股、觀望注意）', audit.call_args.args[1]['_prior_published'])

    def test_clean_rerun_has_no_review(self):
        sig = empty(); sig[H] = [{'name': '台積電', 'code': '2330', 'note': '續抱'}]
        prior = [{'_cat': H, 'name': '台積電', 'code': '2330', 'stance': '續抱', 'note': '續抱'}]
        outcome, writer, checkpoint, _ = self.run_stage(sig, prior)
        self.assertEqual(outcome.review, ''); writer.assert_called_once()

    def test_unreadable_prior_keeps_old_data(self):
        outcome, writer, _, _ = self.run_stage(new_run(), None)
        self.assertTrue(outcome.retained); writer.assert_not_called()
        self.assertIn('無法逐檔核對', outcome.note)


class PriorHintAndCheckpointTests(unittest.TestCase):
    def test_review_request_lists_prior_identities(self):
        sig = empty(); sig['_prior_published'] = ['譜瑞-KY（會員持股）']
        sent = []

        def call(system, payload, **kw):
            sent.append(json.loads(payload))
            return json.dumps(empty())
        with patch.object(p, 'call_gemini', side_effect=call), \
             patch.object(p, 'assessment_batches', return_value=[{'S0001': {'text': '比如說普位'}}]):
            out = p.audit_context_json('比如說普位', sig, D)
        self.assertTrue(any('前一版網站已發布：譜瑞-KY（會員持股）' in i for i in sent[0]['issues']))
        self.assertEqual(out['_prior_published'], ['譜瑞-KY（會員持股）'])

    def test_checkpoint_keeps_review_until_new_extraction_overwrites_it(self):
        ws = Mock(); ws.get_all_values.return_value = [['header']]
        with patch.object(p, 'refresh_checkpoint_sheet', return_value=ws):
            p.save_refresh_checkpoint(None, VID, D, 'raw', [D], review='沿用前一版 1 檔待複核：X')
            row = ws.append_row.call_args.args[0]
            ws.get_all_values.return_value = [['header'], row]
            p.save_refresh_checkpoint(None, VID, D, 'raw', [D], ['codes:' + D])          # 刷新途中
            kept = json.loads(ws.update.call_args.kwargs['values'][0][4])
            self.assertEqual(kept['review'], '沿用前一版 1 檔待複核：X')
            ws.get_all_values.return_value = [['header'], ws.update.call_args.kwargs['values'][0]]
            p.save_refresh_checkpoint(None, VID, D, 'raw', [D], review='')              # 新一輪沒有待複核
            self.assertNotIn('review', json.loads(ws.update.call_args.kwargs['values'][0][4]))


class FinishStatusTests(unittest.TestCase):
    setup_admin = overwrite_tests.OverwriteTests.setup_admin

    def test_published_with_review_is_not_reported_as_full_success(self):
        with ExitStack() as st:
            progress = self.setup_admin(st)
            st.enter_context(patch.object(p, 'stage_extract',
                                          return_value=p.ExtractionOutcome([D], review='沿用前一版 1 檔待複核：譜瑞-KY')))
            st.enter_context(patch.object(p, 'finish_transcript_refresh', return_value={'ok': True, 'review': ''}))
            p.run_admin_job(Mock())
        final = progress.call_args.kwargs
        self.assertEqual(final['status'], '待複核')
        self.assertIn('譜瑞-KY', final['note']); self.assertNotIn('全部更新成功', final['note'])

    def test_checkpoint_resume_uses_saved_review(self):
        with ExitStack() as st:
            progress = self.setup_admin(st, checkpoint={'affected': [D], 'completed': ['codes:' + D]})
            st.enter_context(patch.object(p, 'finish_transcript_refresh',
                                          return_value={'ok': True, 'review': '待複核 2 項'}))
            p.run_admin_job(Mock())
        self.assertEqual(progress.call_args.kwargs['status'], '待複核')

    def test_clean_run_still_reports_success(self):
        with ExitStack() as st:
            progress = self.setup_admin(st)
            st.enter_context(patch.object(p, 'stage_extract', return_value=p.ExtractionOutcome([D])))
            st.enter_context(patch.object(p, 'finish_transcript_refresh', return_value={'ok': True, 'review': ''}))
            p.run_admin_job(Mock())
        self.assertEqual(progress.call_args.kwargs['status'], '完成')


if __name__ == '__main__':
    unittest.main()
