import hashlib
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
from test_quality import p, empty
from test_gate_after_codes import FakeSheet

D='2026/09/10'
VID='MANUAL-20260910'
HEAD=['影片ID','發布日期','標題','處理狀態','失敗原因','原始逐字稿內容','修飾後逐字稿內容','原文更新時間']


class WritableSheet(FakeSheet):
    def append_row(self, row, **kw): self.rows.append(row)
    def update_cell(self, row, col, value): self.rows[row-1][col-1]=value
    def update(self, range_name, values):
        import re
        col,row=re.match(r'([A-Z]+)(\d+)',range_name).groups()
        start=ord(col)-65
        self.rows[int(row)-1][start:start+len(values[0])]=values[0]


class OverwriteTests(unittest.TestCase):
    def test_exact_id_new_shorter_raw_beats_old_first_date_hit(self):
        rows=[dict(zip(HEAD,['real-id',D,'','完成','','舊稿'*500,'舊修飾稿',''])),
              dict(zip(HEAD,[VID,D,'','處理中','','新稿'*250,'','2026-09-13T00:00:00.000Z']))]
        with patch.object(p,'video_rows',return_value=rows),patch.object(p,'note_decision') as note:
            raw,polished=p.existing_transcript(None,VID,D)
        self.assertEqual(raw,'新稿'*250);self.assertEqual(polished,'');note.assert_called_once()

    def test_latest_date_fallback_and_tie_break_are_deterministic(self):
        rows=[{'發布日期':D,'原始逐字稿內容':'long'*100},
              {'發布日期':D,'原始逐字稿內容':'short','原文更新時間':'2026-09-13T01:00:00.000Z'}]
        self.assertEqual(p.select_transcript_row(rows,VID,D)[0],3)
        self.assertEqual(p.select_transcript_row(rows,'',D)[0],3)

    def test_mark_status_updates_same_day_without_creating_manual_duplicate(self):
        ws=WritableSheet([HEAD,['real-id',D,'原標題','等待中','','原文','']])
        ss=Mock();ss.worksheet.return_value=ws
        p.mark_status(ss,VID,D,'新標題','完成')
        self.assertEqual(len(ws.rows),2);self.assertEqual(ws.rows[1][0],'real-id')
        self.assertEqual(ws.rows[1][3],'完成')

    def test_mark_status_fills_only_empty_video_id(self):
        ws=WritableSheet([HEAD,['',D,'','等待中','','原文','']]);ss=Mock();ss.worksheet.return_value=ws
        p.mark_status(ss,VID,D,'','完成');self.assertEqual(ws.rows[1][0],VID)

    def setup_admin(self, stack, raw='新稿'*250, fingerprint=None, checkpoint=None):
        job={'id':'J1','videoId':VID,'date':D,'raw_sha256':fingerprint if fingerprint is not None else hashlib.sha256(raw.encode()).hexdigest()}
        stack.enter_context(patch.object(p,'find_pending_job',return_value=job))
        stack.enter_context(patch.object(p,'existing_transcript',return_value=(raw,'潤飾'*200)))
        stack.enter_context(patch.object(p,'flush_decisions'))
        stack.enter_context(patch.object(p,'note_decision'))
        stack.enter_context(patch.object(p,'load_refresh_checkpoint',return_value=checkpoint))
        stack.enter_context(patch.object(p,'existing_dates',return_value=set()))
        stack.enter_context(patch.object(p,'mark_status'))
        return stack.enter_context(patch.object(p,'job_progress'))

    def test_fingerprint_mismatch_fails_before_checkpoint_or_ai_or_write(self):
        with ExitStack() as st:
            progress=self.setup_admin(st,fingerprint='bad')
            extract=st.enter_context(patch.object(p,'stage_extract'))
            with self.assertRaisesRegex(RuntimeError,'SHA256'):p.run_admin_job(Mock())
            extract.assert_not_called()
            self.assertTrue(any(c.kwargs.get('status')=='失敗' for c in progress.call_args_list))

    def test_retained_result_cannot_be_overwritten_by_final_success(self):
        with ExitStack() as st:
            progress=self.setup_admin(st)
            st.enter_context(patch.object(p,'stage_extract',return_value=p.ExtractionOutcome([D],True,'未覆蓋，保留舊資料，待複核2項')))
            refresh=st.enter_context(patch.object(p,'finish_transcript_refresh'))
            p.run_admin_job(Mock());refresh.assert_not_called()
            self.assertEqual(progress.call_args.kwargs['status'],'待複核')
            self.assertNotIn('全部更新成功',progress.call_args.kwargs['note'])

    def test_checkpoint_pending_marks_waiting_not_complete(self):
        with ExitStack() as st:
            progress=self.setup_admin(st,checkpoint={'affected':[D],'completed':['codes:'+D]})
            st.enter_context(patch.object(p,'finish_transcript_refresh',return_value={'pending':True,'note':'等待日K'}))
            ai=st.enter_context(patch.object(p,'stage_extract'))
            p.run_admin_job(Mock());ai.assert_not_called()
            self.assertEqual(progress.call_args.kwargs['status'],'等待日K')

    def test_legacy_job_without_fingerprint_still_runs(self):
        with ExitStack() as st:
            progress=self.setup_admin(st,fingerprint='')
            st.enter_context(patch.object(p,'stage_extract',return_value=p.ExtractionOutcome([D])))
            st.enter_context(patch.object(p,'finish_transcript_refresh',return_value={'ok':True}))
            p.run_admin_job(Mock());self.assertEqual(progress.call_args.kwargs['status'],'完成')

    def test_source_ids_include_real_manual_and_historical_audit_aliases(self):
        ss=Mock();ss.worksheet.return_value.get_all_records.return_value=[{'影片日期':D,'來源影片ID':'old-real'}]
        with patch.object(p,'video_rows',return_value=[{'發布日期':D,'影片ID':'real','來源別名':'alias,CMONEY-1'}]):
            self.assertEqual(p.transcript_source_ids(ss,VID,D),{VID,'real','alias','old-real'})

    def test_cross_day_cleanup_keeps_unrelated_video_sms_and_manual(self):
        headers=['日期','股票名稱','股票代號','方向','價位說明','理由摘錄','來源影片ID','同日先後']
        sources=['old-real',VID,'another-days-video','CMONEY-1','MANUALENTRY-1','人工補登']
        ws=FakeSheet([headers]+[['2026/09/09','台積電','2330','賣出','','',v,1] for v in sources])
        ss=Mock();ss.worksheet.return_value=ws
        for source in ['old-real',VID,'CMONEY-1','MANUALENTRY-1','人工補登']:
            p._purge_rows_of_video(ss,'操作紀錄','2026/09/09',source)
        self.assertEqual([r[6] for r in ws.rows[1:]],sources[2:])

    def test_dailyk_dependency_repairs_tracker_then_history_before_snapshot(self):
        calls=[]
        def refresh(**kw):
            step=kw['only'][0];calls.append(step)
            return {'pending':True,'blocked_by':'dailyk'} if step=='perfhist' and calls.count(step)==1 else {'ok':True}
        with patch.object(p,'load_refresh_checkpoint',return_value={'affected':[D],'completed':['smsmail:'+D,'codes:'+D,'tracker:'+D]}), \
             patch.object(p,'save_refresh_checkpoint'),patch.object(p,'_transcript_rows_of_day',return_value=None), \
             patch.object(p,'maybe_refresh_site',side_effect=refresh):
            self.assertTrue(p.finish_transcript_refresh(Mock(),VID,D,'raw',[D])['ok'])
        self.assertEqual(calls,['perfhist','dailyk','tracker','perfhist','perf'])

    def test_dailyk_budget_partial_does_not_mark_checkpoint_complete(self):
        with patch.object(p,'load_refresh_checkpoint',return_value={'affected':[D],'completed':['smsmail:'+D,'codes:'+D,'tracker:'+D]}), \
             patch.object(p,'save_refresh_checkpoint') as save,patch.object(p,'_transcript_rows_of_day',return_value=None), \
             patch.object(p,'maybe_refresh_site',side_effect=[{'pending':True,'blocked_by':'dailyk'},{'partial':True}]):
            result=p.finish_transcript_refresh(Mock(),VID,D,'raw',[D])
            self.assertTrue(result['pending']);save.assert_not_called()

    def test_dailyk_network_failure_stays_failure(self):
        with patch.object(p,'load_refresh_checkpoint',return_value={'affected':[D],'completed':['smsmail:'+D,'codes:'+D,'tracker:'+D]}), \
             patch.object(p,'_transcript_rows_of_day',return_value=None), \
             patch.object(p,'maybe_refresh_site',side_effect=[{'pending':True,'blocked_by':'dailyk'},{'ok':False}]):
            with self.assertRaisesRegex(RuntimeError,'補齊日K失敗'):p.finish_transcript_refresh(Mock(),VID,D,'raw',[D])

    def test_csv_bom_ignores_wrong_http_latin1_encoding(self):
        response=Mock(status_code=200,content='\ufeff公司代號,公司簡稱\n4966,譜瑞-KY\n'.encode('utf-8'))
        response.text=response.content.decode('latin1')
        with patch.object(p.requests,'get',return_value=response):
            rows=p._fetch_rows({'url':'https://example.invalid','kind':'csv'})
        self.assertEqual(rows[0]['公司代號'],'4966')

    def test_admin_can_explicitly_request_dailyk_and_yield_before_timeout(self):
        ping=Mock(text=json.dumps({'features':['refresh-step','evidence-v2','dailyk-safe-chunks'],'steps':['dailyk']}))
        with patch.object(p,'ADMIN_JOB',True),patch.object(p,'DAILYK_ONLY',False), \
             patch.object(p,'APPS_SCRIPT_URL','https://example.invalid'),patch.object(p,'ADMIN_KEY','offline'), \
             patch.object(p,'budget_left',return_value=30),patch.object(p.requests,'get',return_value=ping) as get:
            result=p.maybe_refresh_site(only=['dailyk'],force=True,date_str=D)
        self.assertTrue(result['partial']);self.assertEqual(get.call_count,1)

    def test_checkpoint_only_admin_main_skips_all_gemini_health_calls(self):
        with ExitStack() as st:
            for flag in ('CHECK_KEYS','SMS_PRIORITY','FULL_FIX','PARSE_SMS','REPAIR_CODES','RECLASSIFY','FIX_PRICES',
                         'RECONCILE','FILL_BLANKS','BACKFILL','DAILYK_ONLY','PREFLIGHT','REFRESH_SITE'):
                st.enter_context(patch.object(p,flag,False))
            st.enter_context(patch.object(p,'ADMIN_JOB',True))
            st.enter_context(patch.object(p,'open_sheets',return_value=Mock()))
            st.enter_context(patch.object(p,'write_status_log'))
            st.enter_context(patch.object(p,'find_pending_job',return_value={'videoId':VID,'date':D,'step':'刷新網站'}))
            st.enter_context(patch.object(p,'existing_transcript',return_value=('raw','polished')))
            st.enter_context(patch.object(p,'load_refresh_checkpoint',return_value={'affected':[D],'completed':[]}))
            for name in ('require_gemini_key','preflight_gemini_keys','call_gemini'):
                st.enter_context(patch.object(p,name,side_effect=AssertionError('should not call AI')))
            run=st.enter_context(patch.object(p,'run_admin_job'))
            p.main();run.assert_called_once()

    def test_isin_warrants_before_equities_do_not_hide_stocks(self):
        html='<tr><td colspan=7>上櫃認購(售)權證</td></tr>'
        for code,name,cfi in [('700019','權證','RWSCCA'),('4966','譜瑞-KY','ESVUFR'),('00981A','ETF','CEOGEU')]:
            html+='<tr>'+''.join('<td>'+x+'</td>' for x in [code+'　'+name,'TW000','日期','上櫃','產業',cfi])+'</tr>'
        self.assertEqual(p._parse_isin(html.encode('cp950')),[{'code':'4966','name':'譜瑞-KY'}])

    def test_full_write_replaces_aliases_but_preserves_other_days_and_protected_rows(self):
        th=['日期','股票名稱','股票代號','方向','價位說明','理由摘錄','來源影片ID','同日先後']
        hh=['日期','股票名稱','股票代號','目前立場','說明重點','來源影片ID']
        trade=lambda day,source:[day,'舊','2330','買入','','',source,1]
        ts=FakeSheet([th,trade(D,'real'),trade(D,'CMONEY-1'),trade(D,'MANUALENTRY-1'),
                      trade('2026/09/09','real'),trade('2026/09/09','unrelated'),
                      trade('2026/09/09','CMONEY-2'),trade('2026/09/08','untouched')])
        hs=FakeSheet([hh,[D,'舊','2330','','','real'],[D,'人工','2330','','','MANUALENTRY-2']])
        tables={'操作紀錄':ts,'會員持股':hs};ss=Mock();ss.worksheet.side_effect=lambda n:tables[n]
        sig=empty();sig.update(_video_id=VID,_source_ids=[VID,'real'],_affected_dates=[D,'2026/09/09'])
        sig['buy']=[{'name':'新','code':'4966','_date':'2026/09/09'}]
        sig['holdings']=[{'name':'新持股','code':'4966'}]
        def counts(*args):return {n:p._count_days(w.get_all_values()) for n,w in tables.items()}
        with patch.object(p,'_day_counts',side_effect=counts), \
             patch.object(p,'append_rows_safe',side_effect=lambda ws,rows:ws.rows.extend(rows)), \
             patch.object(p,'_upsert_daily_article'),patch.object(p,'run_post_write_steps'):
            p.write_results(ss,D,sig,'文章',set(),set(),replace_video=True)
        sources=[r[6] for r in ts.rows[1:]]
        self.assertNotIn('real',sources)
        for source in ['CMONEY-1','CMONEY-2','MANUALENTRY-1','unrelated','untouched',VID]:self.assertIn(source,sources)
        self.assertEqual([r[5] for r in hs.rows[1:]],['MANUALENTRY-2',VID])


if __name__=='__main__':unittest.main()
