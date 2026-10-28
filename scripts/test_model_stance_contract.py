"""語意問卷與最後定案不得被方向字詞覆寫；新增優先序依人類2026/10/27裁示。"""
import copy,unittest
from contextlib import ExitStack
from unittest.mock import patch
from test_source_fact_ledger import p
class ModelStance(unittest.TestCase):
 def test_complete_neutral_answers_are_votes_but_missing_fields_are_not(self):
  raw='甲公司今天漲停，接著談別家公司。'
  a={'about_itself':True,'independent_analysis':False,'buy_today':False,'sell_today':False,
     'holding_now':False,'past_trade':False,'now':'none','tone':'neutral','entry_for_new':'none',
     'holder_advice':'none','selection_intent':'neutral','content_role':'comparison_only','quotes':{'stance':[]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'ignored')
  for key in ('now','tone','independent_analysis','holding_now'):
   missing=copy.deepcopy(a);missing.pop(key)
   self.assertEqual(p.verdict_class(missing,p._ev_norm(raw),['甲公司'])[0],'')
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['乙公司'])[0],'')

 def test_selection_label_alone_cannot_invent_a_current_prohibition(self):
  raw='甲公司只說本人未持有。'
  a={'about_itself':True,'independent_analysis':False,'now':'none','tone':'neutral',
    'entry_for_new':'none','selection_intent':'explicit_exclusion','quotes':{'stance':[raw]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'ignored')
  a['entry_for_new']='not_yet'
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')

 def test_questionnaire_schema_requests_evidence_before_summary_role(self):
  props=p.verdict_response_schema()['properties']['verdicts']['items']['properties']
  keys=list(props)
  self.assertLess(keys.index('quote_source_ids'),keys.index('content_role'))
  self.assertLess(keys.index('entry_for_new'),keys.index('content_role'))
 def test_content_role_precedes_a_mislabelled_entry_axis_without_keyword_matching(self):
  raw='甲公司只是用來說明本人持有範圍。'
  for role in ('position_disclosure','selection_scope','comparison_only'):
   a={'about_itself':True,'content_role':role,'entry_for_new':'prohibit','now':'prohibit',
      'quotes':{'stance':[raw]}}
   self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'ignored')
   a['quotes']={};self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'')

 def test_actual_holding_fact_precedes_a_position_disclosure_role(self):
  raw='甲公司會員持股繼續抱著。'
  a={'about_itself':True,'content_role':'position_disclosure','holding_now':True,'quotes':{'holding_now':[raw]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['甲公司'])[0],'holdings')

 def test_independent_neutral_analysis_is_observation_without_inventing_bullishness(self):
  raw='甲公司今日第一根反彈，後續觀察往季線的位置。'
  answer={'about_itself':True,'independent_analysis':True,'now':'none','tone':'neutral','entry_for_new':'none',
    'quotes':{'stance':[raw],'analysis':[raw]}}
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'watch_watch')
  answer['quotes'].pop('analysis')
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'ignored')

 def test_analysis_role_does_not_override_real_refusal_or_identity_denial(self):
  raw='甲公司雖有分析，但目前還沒穩，現在不要買。'
  answer={'about_itself':True,'independent_analysis':True,'now':'not_yet','entry_for_new':'not_yet','tone':'neutral',
    'quotes':{'stance':[raw],'analysis':[raw]}}
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')
  answer['about_itself']=False
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'ignored')

 def test_historical_candidate_is_not_moved_or_discarded_by_tone_words(self):
  for text in ('以前買過甲公司，現在不要碰。','以前賣過甲公司，現在可以注意。'):
   row={'code':'1111','name':'甲公司','reason':text}
   sig=p.history_to_watch({'history':[row]},'2026/10/27',transcript=text)
   self.assertEqual(sig['history'][0]['reason'],text)
   self.assertFalse(sig.get('watch_watch'));self.assertFalse(sig.get('watch_avoid'))

 def answer(self,raw,entry,holder='none',tone='neutral'):
  return p.verdict_class({'about_itself':True,'now':'none','tone':tone,'entry_for_new':entry,'holder_advice':holder,
    'quotes':{'stance':[raw]}},p._ev_norm(raw),names=['甲公司'])[0]
 def test_nonholding_and_waiting_are_not_word_based_prohibitions(self):
  self.assertEqual(self.answer('甲公司我沒有要買，已經超跌了，不要殺低，等它反彈。','none','hold_rebound'),'watch_watch')
  self.assertEqual(self.answer('甲公司我沒有要買，已經超跌了，不要殺低，等它反彈。','prohibit','hold_rebound'),'watch_watch')
  self.assertEqual(self.answer('甲公司是主要觀察的股票，還沒有穩，目前沒有出手念頭，不用急。','wait_condition'),'watch_watch')
  self.assertEqual(self.answer('甲公司量能條件未到，現在不要進場，等正式動作。','not_yet'),'watch_avoid')
 def test_flags_cannot_move_or_erase_rows(self):
  signals={'watch_watch':[dict(name='甲公司',code='1111',reason='甲公司目前還不能買。')],
   'watch_avoid':[dict(name='乙公司',code='2222',reason='乙公司已轉強並突破，後續偏多。')]}
  before=copy.deepcopy({k:[(r['code'],r['reason']) for r in v] for k,v in signals.items()})
  p.align_watch_with_plain_refusal(signals,'甲公司還不能買。乙公司轉強。',final=True)
  p.verify_reason_category(signals);p.drop_only_named_watch_rows(signals)
  after={k:[(r['code'],r['reason']) for r in signals.get(k,[])] for k in before}
  self.assertEqual(before,after)
 def test_positive_interest_cannot_override_the_current_wait_instruction(self):
  raw='甲公司是主要追蹤標的，但是還沒穩，現在不用急著買。'
  answer={'about_itself':True,'now':'not_yet','selection_intent':'positive_candidate',
    'entry_for_new':'not_yet','holder_advice':'none','quotes':{'stance':[raw]}}
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')
 def test_final_ignored_enforcement_removes_late_public_row(self):
  row={'name':'甲公司','code':'1111','_date':'2026/10/27','reason':'甲公司只是未持有背景對照。'}
  sig={'watch_avoid':[row],'_verdicts':{'1111':{'cls':'ignored','enforce':True,'name':'甲公司','answer':{'ignored':{'answer':{'about_itself':False,'quotes':{}}}}}}}
  p.apply_verdicts(sig,'2026/10/27',final=True)
  self.assertFalse(sig['watch_avoid']);self.assertEqual(sig['ignored'][0]['code'],'1111')
 def adjudicate(self, category, answers, opened=False):
  raw='今天所說的一家公司只是一般敘事，並沒有確認這是甲公司的事實。'
  sig={category:[{'name':'甲公司','code':'1111','reason':'候選','_date':'2026/10/27'}]}
  with ExitStack() as stack:
   for name,value in {'_CODE_MAP':{'1111':'甲公司'},'_verdict_known':lambda *a,**k:({},set(),{'1111':'甲公司'} if opened else {}),
    '_verdict_cache':lambda *a,**k:{},'_own_segments':lambda *a,**k:[],
    '_verdict_mark':lambda *a,**k:([{'text':raw}],raw),
    '_audit_direct_verdicts':lambda *a,**k:[], '_ask_verdicts':lambda *a,**k:answers,
    'budget_left':lambda:9999,'GEMINI_KEYS':['offline']}.items():stack.enter_context(patch.object(p,name,value))
   return p._adjudicate_classes(None,sig,raw,'2026/10/27',{}, {},'on')
 def test_open_position_does_not_prove_an_ambiguous_daily_mention(self):
  sig=self.adjudicate('ignored',{'1111':{'about_itself':False,'quotes':{}}},opened=True)
  self.assertFalse(sig.get('holdings'));self.assertEqual(sig['_verdicts']['1111']['cls'],'ignored')
 def test_unanswered_late_candidate_is_uncertain_not_ignored(self):
  sig=self.adjudicate('watch_watch',{})
  self.assertEqual(sig['_verdicts']['1111']['cls'],'uncertain')
  self.assertTrue(sig['_verdicts']['1111']['enforce']);self.assertEqual(len(sig['_verdicts']['1111']['round_answers']),3)
 def test_technical_window_miss_cannot_delete_a_claim(self):
  row={'code':'1111','name':'甲公司','reason':'甲公司上半年EPS達21.83元，全年預估40元。'}
  with patch.object(p,'_own_segments',return_value=[]),patch.object(p,'_context_sources',return_value=[]):
   p.strip_unsupported_technical_terms({'watch_watch':[row]},'甲公司後面有過場，半年報21.83。')
  self.assertIn('21.83',row['reason']);self.assertIn('EPS',row['_technical_source_cues'])
 def test_two_valid_public_votes_settle_without_changing_trade_facts(self):
  self.assertEqual(p.verdict_settle('ignored',['watch_watch','watch_avoid','watch_watch'],last=True)[0:2],('定案','watch_watch'))
  self.assertEqual(p.verdict_settle('ignored',['watch_watch','watch_avoid',''],last=True)[0],'未定')
  self.assertEqual(p.verdict_settle('watch_watch',['buy','buy',''],last=True)[0],'未定')
  self.assertEqual(p.verdict_settle('watch_watch',['holdings','watch_watch','watch_watch'],last=True,open_position=True)[0:2],('定案','holdings'))
 def test_daily_identity_denial_precedes_ownership_boolean(self):
  raw='甲公司是這一句旁邊被點名的背景，不是自己的部位。'
  answer={'about_itself':False,'holding_now':True,'quotes':{'holding_now':[raw]}}
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'ignored')
 def test_my_stock_is_a_valid_holding_fact_without_direction_keyword(self):
  raw='甲公司本來就是我的股票。'
  answer={'about_itself':True,'holding_now':True,'quotes':{'holding_now':[raw]}}
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'holdings')
if __name__=='__main__':unittest.main()
