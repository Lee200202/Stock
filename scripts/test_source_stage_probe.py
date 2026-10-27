"""局部裁決探針只傳原稿與身分，不把期待分類塞進推論。"""
import json,unittest
from unittest.mock import patch
import test_source_fact_ledger  # 建立離線環境變數。
import replay_note_sources as replay

class SourceStageProbe(unittest.TestCase):
 def test_expectation_is_read_only_after_three_real_questionnaire_slots(self):
  raw='甲公司在低檔可以等待回測後布局。';calls=[]
  def ask(items,day,round_no,transcript):
   text=json.dumps(items,ensure_ascii=False)
   self.assertNotIn('expected_categories',text);self.assertNotIn('watch_watch',text)
   self.assertEqual(transcript,raw);calls.append(round_no)
   return {'1111':{'about_itself':True,'entry_for_new':'wait_condition','quotes':{'stance':[raw]}}}
  with patch.object(replay.pl,'_own_segments',return_value=[raw]),patch.object(replay.pl,'_ask_verdicts',side_effect=ask):
   rows,errors=replay.run_verdict_probes([{'code':'1111','name':'甲公司','expected_categories':['watch_watch']}],{},raw,'2026/10/27')
  self.assertEqual(calls,[0,1,2]);self.assertFalse(errors);self.assertTrue(rows[0]['reference_match'])

if __name__=='__main__':unittest.main()
