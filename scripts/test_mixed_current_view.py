"""現況分析可接在歷史／比較後，角色摘要不得吞掉已核引文。"""
import unittest
from test_source_fact_ledger import p

class MixedCurrentView(unittest.TestCase):
 def answer(self,role='comparison_only',entry='wait_condition'):
  raw='甲公司以前沒有買，今天第一根反彈往季線走，拉回等止穩可留意。'
  a=dict(about_itself=True,content_role=role,independent_analysis=True,now='conditional',tone='neutral',
   entry_for_new=entry,buy_today=False,sell_today=False,holding_now=False,past_trade=False,
   quotes={'analysis':[raw]})
  return raw,a

 def test_mixed_role_with_verified_analysis_retains_current_conditional_view(self):
  for role in ('comparison_only','selection_scope','position_disclosure','historical_only'):
   raw,a=self.answer(role)
   self.assertEqual(p._questionnaire_issues(a,raw,['甲公司']),[])
   self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_watch')

 def test_missing_analysis_quote_cannot_borrow_the_current_view(self):
  raw,a=self.answer();a['quotes']={}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'')

 def test_actual_restriction_still_avoid_even_without_duplicate_stance_quote(self):
  raw,a=self.answer('independent_analysis','not_yet')
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')

 def test_identity_denial_or_ordinary_word_is_not_promoted_by_analysis(self):
  raw,a=self.answer('ordinary_word')
  self.assertTrue(p._questionnaire_issues(a,raw,['甲公司']))
  a['about_itself']=False
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'ignored')

 def test_past_event_does_not_hide_present_analysis(self):
  raw,a=self.answer('historical_only');a['past_trade']=True
  a['quotes']['past_trade']=['甲公司以前沒有買']
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_watch')

 def test_policy_does_not_turn_waiting_into_executed_purchase(self):
  raw,a=self.answer('independent_analysis');a['selection_intent']='positive_candidate'
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_watch')
  a['selection_intent']='authorization_pending';a['entry_for_new']='not_yet'
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')

if __name__=='__main__':unittest.main()
