import copy
import json
import unittest
from unittest.mock import patch, Mock
from test_quality import p, empty, trade


class SourceReferenceTests(unittest.TestCase):
    def test_segments_cover_entire_source_in_order(self):
        source=('世芯-KY昨天買；今天續抱。\n' * 95)+'收尾我還有紅準。'
        segs=p.source_segments(source)
        self.assertEqual(''.join(s['text'] for s in segs.values()),source)
        for seg in segs.values():self.assertEqual(source[seg['start']:seg['end']],seg['text'])

    def test_refs_reconstruct_quote_without_ai_paraphrase(self):
        source='昨天叫人家賣力積電，今天看它拉回。'
        sig=empty();sig['sell']=[dict(trade(),evidence=['虛构'],evidence_refs=['S0001'])]
        p.materialize_evidence(sig,source)
        self.assertEqual(sig['sell'][0]['evidence'],[source])
        self.assertEqual(sig['sell'][0]['_source_spans'],[[0,len(source)]])

    def test_invalid_ref_cannot_fall_back_to_model_quote(self):
        sig=empty();sig['sell']=[dict(trade(),evidence_refs=['S9999'])]
        p.materialize_evidence(sig,trade()['evidence'][0])
        self.assertFalse(sig['sell'][0]['evidence'])

    def test_similar_but_wrong_date_quote_rejected(self):
        self.assertFalse(p._quote_is_real('我今天叫會員賣力積電',p._ev_norm('我昨天叫會員賣力積電')))
        self.assertTrue(p._quote_is_real('我 昨天叫會員，賣力積電。',p._ev_norm('我昨天叫會員賣力積電')))

    def test_automatic_repair_recovers_source_quotes(self):
        source=trade()['evidence'][0]
        initial=empty();initial['sell']=[trade()]
        broken=copy.deepcopy(initial);broken['sell'][0]['evidence']=['改寫後而找不到的賣出引用']
        fixed=copy.deepcopy(initial);fixed['sell'][0]['evidence_refs']=['S0001']
        with patch.object(p,'call_gemini',side_effect=[json.dumps(broken),json.dumps(fixed)]) as call:
            out=p.audit_signals(source,initial,'2026/09/10')
        self.assertEqual(call.call_count,2)
        self.assertFalse(out['_quality_requires_review'])
        self.assertEqual(out['sell'][0]['when'],'yesterday')

    def test_disappearing_candidates_trigger_repair_and_hold_publication(self):
        source='我還有台積電。聖暉、辛耘、牧德列候選。'
        initial=empty();initial['holdings']=[{'name':'台積電','evidence_refs':['S0001']}]
        initial['watch_watch']=[{'name':n,'evidence_refs':['S0001']} for n in ('聖暉','辛耘','牧德')]
        partial=empty();partial['holdings']=initial['holdings']
        with patch.object(p,'call_gemini',return_value=json.dumps(partial)) as call:
            result=p.audit_signals(source,initial,'2026/09/10')
        self.assertEqual(call.call_count,2)
        self.assertTrue(result['_quality_requires_review'])
        self.assertEqual(len(result['_repair_gaps']),3)

    def test_partial_assessment_cannot_delete_previous_rows(self):
        sig=empty();sig['holdings']=[{'name':'台積電','code':'2330','note':'續抱','_date':'2026/09/10'}]
        sig['_quality_requires_review']=True
        identity=lambda x,*a,**k:x
        names=('verify_names','resolve_signals','validate_evidence','resolve_unclear_names','merge_duplicates','naturalize_signal_reasons')
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name in names:stack.enter_context(patch.object(p,name,side_effect=identity))
            stack.enter_context(patch.object(p,'existing_dates',return_value=set()))
            stack.enter_context(patch.object(p,'extract_signals',return_value=sig))
            stack.enter_context(patch.object(p,'audit_signals',return_value=sig))
            stack.enter_context(patch.object(p,'source_record_dates',return_value=set()))
            stack.enter_context(patch.object(p,'transcript_source_ids',return_value={'manual'}))
            prior=[{'_cat':'holdings','name':'台積電','code':'2330','stance':'續抱','note':'續抱'}]+[
                {'_cat':'watch_watch','name':n,'code':c,'price':'未說明','reason':'候選','_seq':1}
                for c,n in (('5536','聖暉*'),('3583','辛耘'),('3563','牧德'),('7750','新代'))]
            stack.enter_context(patch.object(p,'prior_published_rows',return_value=prior))
            stack.enter_context(patch.object(p,'resolve_code',return_value=('代號待確認','','')))
            audit=stack.enter_context(patch.object(p,'save_evidence_audit'))
            stack.enter_context(patch.object(p,'flush_decisions'))
            writer=stack.enter_context(patch.object(p,'write_results'))
            outcome=p.stage_extract(Mock(),{'id':'manual'},'2026/09/10','display',set(),set(),replace_video=True,v1='我還有台積電。')
        audit.assert_called_once();writer.assert_not_called()
        self.assertTrue(outcome.retained);self.assertIn("4/5 檔",outcome.note);self.assertIn("判讀不完整",outcome.note)

    def test_original_official_names_do_not_go_to_ai_or_old_memo(self):
        sig=empty();sig['watch_watch']=[{'name':n,'code':c} for c,n in [('3583','辛耘'),('3563','牧德'),('7750','新代')]]
        with patch.object(p,'get_code_map',return_value={'3583':'辛耘','3563':'牧德','7750':'新代'}),patch.object(p,'call_gemini') as ai,patch.object(p,'name_memo_load') as memo:
            p.resolve_unclear_names(sig,'辛耘、牧德、新代列候選。',Mock())
        ai.assert_not_called();memo.assert_not_called()

    def test_polish_name_change_falls_back_to_original(self):
        original='我還有紅準，紅準今天跌兩毛。世芯、譜瑞、矽晶圓原字保留。'
        with patch.object(p,'call_gemini',return_value='我還有宏捷科，宏捷科今天跌兩毛。四星、普位、細金元。'):
            out,degraded,_=p._polish_one(1,1,original)
        self.assertTrue(degraded);self.assertEqual(p._ev_norm(out),p._ev_norm(original))

    def test_paragraph_reflow_preserves_words_and_reduces_tiny_lines(self):
        original='\n\n'.join(['我還有紅準。','今天跌兩毛。','不用加別的公司。']*20)
        out=p.format_readable_transcript(original)
        self.assertEqual(p._ev_norm(out),p._ev_norm(original))
        self.assertLess(len(out.splitlines()),len(original.splitlines())/3)

    def test_raw_missing_does_not_promote_polished_source(self):
        with self.assertRaisesRegex(ValueError,'缺少原始'):p.transcript_sources('', '看起來很順的修飾稿')

    def test_macro_numeric_prefix_is_not_a_quote(self):
        sig=empty();sig['market']=[{'kind':'level','text':'指數關卡466','evidence':['指數關卡46620，回補缺口。']}]
        gaps=p.evidence_gaps(sig,'指數關卡46620，回補缺口。')
        self.assertTrue(any('數字' in x for x in gaps))

    def test_macro_and_structure_survive_malformed_article(self):
        sig=empty();sig['market']=[{'kind':'level','text':'轉折454XX、46188、46620。','_evidence_verified':True}]
        out=p.canonical_article(sig,'2026/09/10','壞掉的信：華城今天賣775')
        self.assertIn('454XX、46188、46620',out);self.assertNotIn('華城今天',out)
        self.assertEqual(out.count('② 會員操作紀錄與持股明細'),1)

    def test_resume_skips_successful_mail_and_tracker(self):
        state={'affected':['2026/09/09','2026/09/10'],'completed':['smsmail:2026/09/09','smsmail:2026/09/10','codes:2026/09/09','tracker:2026/09/09']}
        with patch.object(p,'load_refresh_checkpoint',return_value=state),patch.object(p,'save_refresh_checkpoint') as save,patch.object(p,'maybe_refresh_site',return_value={'ok':True}) as refresh:
            result=p.finish_transcript_refresh(Mock(),'v','2026/09/10','source',state['affected'])
        self.assertTrue(result['ok'])
        self.assertEqual([c.kwargs['only'] for c in refresh.call_args_list],[['perfhist'],['perf']])
        self.assertIn('complete',save.call_args.args[-1])

    def test_refresh_failure_does_not_record_later_success(self):
        with patch.object(p,'load_refresh_checkpoint',return_value={'affected':['2026/09/10'],'completed':[]}),patch.object(p,'save_refresh_checkpoint') as save,patch.object(p,'maybe_refresh_site',return_value={'ok':False}) as refresh:
            with self.assertRaisesRegex(RuntimeError,'檢查點'):p.finish_transcript_refresh(Mock(),'v','2026/09/10','source',['2026/09/10'])
        self.assertEqual(refresh.call_count,1);save.assert_not_called()


if __name__=='__main__':unittest.main()
