"""離線重現跨排程交棒、人工優先與欄位演進，不呼叫外部服務。"""
import unittest
import tempfile
from pathlib import Path
from datetime import date
from unittest.mock import patch
import transcript as t
from test_transcript import FakeWorksheet, FakeSpreadsheet
from test_transcript_handoff import pl

DAY = '2026/09/18'
TARGET = date(2026, 9, 18)

def row(source='自動', raw='原稿' * 250, vid='video123456', updated='2026/09/18 11:30:00'):
    return {t.COL_DATE:DAY, t.COL_SOURCE:source, t.COL_RAW:raw, t.COL_ID:vid,
            '原文更新時間':updated, '處理狀態':'逐字稿已就緒', '標題':'當日影片'}

class HandoffV45(unittest.TestCase):
    def test_header_migration_keeps_layout_positions(self):
        head = t.FULL_HEADER[:-1] + ['排版稿JSON', '排版稿指紋', '排版稿方式']
        ws=FakeWorksheet(head, [])
        _, after=t.video_sheet(FakeSpreadsheet(ws))
        self.assertEqual(after[:len(head)], head)
        self.assertEqual(after[-1], t.COL_SOURCE)
        self.assertEqual(ws.updates[-1][0], 'N1')

    def test_auto_does_not_write_to_waiting_row(self):
        ws=FakeWorksheet(t.FULL_HEADER,[row(raw='',source='')])
        t.save_transcript(FakeSpreadsheet(ws),{'id':'video123456','title':'當日影片'},DAY,'完整原稿'*250,t.SRC_AUTO)
        self.assertEqual(ws.updates,[])
        self.assertEqual(len(ws.appended),1)
        self.assertEqual(ws.rows[0][t.COL_RAW],'')

    def test_manual_arrived_while_listening_blocks_save(self):
        ws=FakeWorksheet(t.FULL_HEADER,[row(source='手動')])
        with self.assertRaises(t.Done):
            t.save_transcript(FakeSpreadsheet(ws),{'id':'video123456'},DAY,'自動'*250,t.SRC_AUTO)
        self.assertEqual(ws.appended,[])
        self.assertEqual(ws.updates,[])

    def test_manual_wins_even_if_auto_id_matches_and_is_newer(self):
        rows=[row('手動','人貼的'*100,'MANUAL-20260918'),row(updated='2026/09/18 12:30:00')]
        self.assertEqual(pl.select_transcript_row(rows,'video123456',DAY)[1][t.COL_SOURCE],'手動')
        self.assertEqual(t.pick_row(rows,'video123456',DAY)[1][t.COL_SOURCE],'手動')

    def test_legacy_original_has_manual_priority(self):
        rows=[row('', '舊人工稿'*100, 'manual'),row()]
        self.assertEqual(pl.select_transcript_row(rows,'video123456',DAY)[1][t.COL_ID],'manual')

    def test_ready_original_does_not_need_youtube(self):
        with patch.object(pl,'video_rows',return_value=[row()]), patch.object(pl,'fetch_feed',side_effect=AssertionError('不得要求 YouTube')):
            videos,done=pl.today_pipeline_inputs(None,TARGET)
        self.assertEqual(videos[0]['id'],'video123456')
        self.assertEqual(done['video123456'],'逐字稿已就緒')

    def test_hold_does_not_become_no_video_or_call_youtube(self):
        with patch.object(pl,'video_rows',return_value=[row('手動保留','')]), patch.object(pl,'fetch_feed',side_effect=AssertionError('手動保留')):
            videos,done=pl.today_pipeline_inputs(None,TARGET)
        self.assertEqual(done[videos[0]['id']],'手動保留')

    def test_two_hundred_char_boundary_matches_stage(self):
        self.assertIsNone(pl.ready_transcript_video([row(raw='字'*200)],TARGET))
        self.assertIsNotNone(pl.ready_transcript_video([row(raw='字'*201)],TARGET))

    def test_completed_segments_survive_new_process_cache(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'segments.json'
            a=t.SegmentCache(path);a[(0,1800)]='已聽打第一段'
            b=t.SegmentCache(path)
            self.assertEqual(b[(0,1800)],'已聽打第一段')
            self.assertNotIn((1800,3600),b)

    def test_bad_cache_does_not_break_new_run(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'segments.json';path.write_text('truncated',encoding='utf-8')
            self.assertEqual(t.SegmentCache(path),{})

    def test_sync_script_cannot_recreate_namespace_collision(self):
        root=Path(__file__).resolve().parents[1]
        self.assertFalse((root/'pipeline.py').exists())
        sync=root/'scripts/sync_quality.py'
        if sync.exists():
            self.assertNotIn("(ROOT/'pipeline.py').write_bytes",sync.read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()

