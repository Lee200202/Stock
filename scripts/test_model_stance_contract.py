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

 def test_waiting_condition_needs_a_verified_condition_quote(self):
  # 2026/10/28 國巨：答 conditional 卻寫不出任何條件，不能列觀望注意；改看模型自己的語氣
  raw='甲公司這兩天漲的還不夠他一天跌，上方有大套牢區，彈上來一定還會再下去。如果回到年線就可以注意甲公司。'
  base={'about_itself':True,'independent_analysis':False,'buy_today':False,'sell_today':False,'holding_now':False,
        'past_trade':False,'now':'conditional','tone':'bearish','entry_for_new':'wait_condition','holder_advice':'none',
        'selection_intent':'neutral','content_role':'independent_analysis',
        'quotes':{'stance':['甲公司這兩天漲的還不夠他一天跌'],'condition':[]}}
  self.assertEqual(p.verdict_class(base,p._ev_norm(raw),['甲公司'])[0],'watch_avoid')
  with_condition=copy.deepcopy(base);with_condition['quotes']['condition']=['如果回到年線就可以注意甲公司']
  self.assertEqual(p.verdict_class(with_condition,p._ev_norm(raw),['甲公司'])[0],'watch_watch')
  rebound=copy.deepcopy(base);rebound.update(holder_advice='hold_rebound',entry_for_new='none')
  self.assertEqual(p.verdict_class(rebound,p._ev_norm(raw),['甲公司'])[0],'watch_watch')
  legacy=copy.deepcopy(base);legacy['quotes'].pop('condition')
  self.assertEqual(p.verdict_class(legacy,p._ev_norm(raw),['甲公司'])[0],'watch_watch')

 def test_bare_spoken_code_is_only_an_inventory_candidate(self):
  flat='老師早，4526再抱嗎？張總，4526繼續抱嗎？這隻股票是什麼？4526。你怎麼不賣1580？1580買的可不可以賣？今天7200億假的。缺口是2490。'
  self.assertEqual(p._bare_code_mentions(flat),['4526'])
  with patch.object(p,'_CODE_MAP',{'4526':'東台'}):
   self.assertEqual(p.is_non_stock('4526')[0],True)   # 剔除規則本身不變；只在代號比對先認官方代號
  # 名稱表裡本來就有「東台→4526」：只念代號時仍要列成盤點候選；念過名稱的不重複列
  raw='老師早，4526再抱嗎？張總，4526繼續抱嗎？這隻股票是什麼？4526。嘉澤3533今天也漲。'
  with patch.object(p,'_CODE_MAP',{'4526':'東台','3533':'嘉澤'}):
   p._INVENTORY_CACHE['key']=None
   found={x['code'] for x in p.source_inventory({'S1':{'text':raw}})}
  self.assertIn('4526',found);self.assertIn('3533',found)

 def test_short_quote_with_own_name_in_the_cited_passage_counts(self):
  # 2026/10/28 大立光：「大立光，不買。」去標點 5 字，被 6 字門檻當成引用不實
  seg='大立光， 不買。聯發科， 不買。這是我現在的邏輯。'
  a={'about_itself':True,'now':'prohibit','tone':'bearish','entry_for_new':'prohibit',
     'quotes':{'stance':['大立光， 不買。']},'_verified_quote_sources':{'stance':[seg]}}
  self.assertEqual(p._questionnaire_verified_quotes(a,'stance',['大立光']),['大立光， 不買。'])
  self.assertEqual(p._questionnaire_verified_quotes(a,'stance',['聯發科']),[])       # 沒有本股名稱不放寬
  b=dict(a,quotes={'stance':['不買不碰。']})
  self.assertEqual(p._questionnaire_verified_quotes(b,'stance',['大立光']),[])
  c=dict(a,quotes={'stance':['大立光，不賣。']})
  self.assertEqual(p._questionnaire_verified_quotes(c,'stance',['大立光']),[])      # 不在所選原段不放寬
  self.assertEqual(p.verdict_class(a,p._ev_norm(seg),['大立光'])[0],'watch_avoid')
  code_only={'about_itself':True,'now':'prohibit','tone':'bearish','quotes':{'stance':['4526。']},
     '_verified_quote_sources':{'stance':['這隻股票是什麼？4526。好，']}}
  self.assertEqual(p._questionnaire_verified_quotes(code_only,'stance',['4526','東台']),[])   # 只有代號，沒有主張
  name_only=dict(a,quotes={'stance':['大立光。']})
  self.assertEqual(p._questionnaire_verified_quotes(name_only,'stance',['大立光']),[])

 def test_note_length_follows_the_material(self):
  # 2026/10/28 管理者：材料真的少，短沒關係；有材料就不應該那麼短
  self.assertEqual(p.note_length_target(40,0)['min_chars'],0)
  self.assertEqual(p.note_length_target(40,120)['level'],'thin')
  self.assertEqual(p.note_length_target(60,200)['level'],'normal')
  self.assertEqual(p.note_length_target(150)['min_chars'],40)
  self.assertEqual(p.note_length_target(400)['level'],'rich')

 def test_watch_note_holding_claim_is_flagged_not_rewritten_after_review(self):
  text='甲公司為會員持股，先前推薦後創新高。已持有者續抱。'
  sig={'watch_watch':[{'name':'甲公司','code':'1111','reason':text}],
       'watch_avoid':[{'name':'乙公司','code':'2222','reason':'乙公司已持有者不宜殺低。'}]}
  p.flag_watch_holding_claims(sig)
  self.assertEqual(sig['watch_watch'][0]['reason'],text)                 # 審閱後不改字，指紋不失效
  self.assertTrue(any('甲公司' in g for g in sig['_repair_gaps']))
  self.assertFalse(any('乙公司' in g for g in sig['_repair_gaps']))       # 給讀者的條件建議不算

 def test_unreadable_records_are_not_a_confirmed_empty_position(self):
  class Bad:
   def worksheet(self,name): raise RuntimeError('quota')
  p._verdict_known(Bad(),'2026/10/28',with_open=True)
  self.assertEqual(p._verdict_position_key({'open':False}),'position=unknown')
  p._VERDICT_RECORDS['readable']=True
  self.assertEqual(p._verdict_position_key({'open':False}),'position=none')

 def test_only_unanimous_settled_verdicts_are_reused(self):
  head=p.VERDICT_HEADERS
  def row(code,votes,cls='觀望不碰'):
   return ['2026/10/28','v',code,'甲',cls,'裁決',votes+'｜依據','{}','fp'+code,'v40','t','']
  class WS:
   def get_all_values(self): return [head,row('1111','觀望不碰／觀望不碰'),row('2222','觀望不碰／排除／觀望不碰'),
                                     row('3333','無效／缺答（不計票）／觀望不碰／觀望不碰'),row('4444','觀望不碰')]
  class Book:
   def worksheet(self,name): return WS()
  got=p._verdict_cache(Book(),'2026/10/28')
  self.assertTrue(got['1111']['by_fp']);self.assertFalse(got['2222']['by_fp'])   # 2 比 1 定案不沿用
  self.assertTrue(got['3333']['by_fp']);self.assertFalse(got['4444']['by_fp'])   # 缺答不算反對；只有一票不沿用

 def test_complete_words_ending_in_a_connector_are_not_spoken_fragments(self):
  # 2026/10/28 大立光：「…曾提及。」被當成停在「及」上的殘句，整檔隔離
  for done in ('權值股現階段不追價，先前在2000多塊時曾提及。','外資持續參與。','股權已經轉讓。','會員依建議聽從。','跌勢可能波及。'):
   self.assertFalse(p.spoken_fragment(done),done)
  for cut in ('鈺邦，你來看，它是不是跟。','記憶體族群以及。','他把。','這一檔股票會從。'):
   self.assertTrue(p.spoken_fragment(cut),cut)

 def test_source_review_failure_names_the_check_that_failed(self):
  # 2026/10/28 審閱：七檔隔離都寫成同一句「未提供唯一主張編號…」，看不出卡在哪一關；判定不變，只記卡點
  row={'code':'1111','name':'甲公司','_source_fact_ledger':{'facts':{}}}
  r=p._validated_source_review({'source_review':{'claims':[]}},row,'甲公司今天大漲。','甲公司今天大漲，外資連續買超。')
  self.assertEqual(r['status'],'insufficient')
  self.assertIn('沒有唯一回覆',r['claims'][0]['explanation'])

 def test_verdict_fingerprint_includes_the_position_snapshot(self):
  self.assertNotEqual(p._verdict_position_key({'open':True}),p._verdict_position_key({'open':False}))

 def test_verdict_cache_version_carries_the_prompt_and_schema_fingerprint(self):
  # 改了裁決提示詞或答案格式，快取鍵自動換掉，不靠人記得升版（2026/10/28 審閱）
  import hashlib,json
  want=hashlib.sha256((p.ADJUDICATE_SYSTEM+json.dumps(p.verdict_response_schema(),ensure_ascii=False,sort_keys=True)).encode('utf-8')).hexdigest()[:8]
  self.assertTrue(p.VERDICT_VERSION.endswith('+'+want))
  self.assertEqual(p.VERDICT_VERSION.count('+'),1)

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
  # 2026/10/28：模型自己答沒有進場立場、語氣中性，依 2026/10/08 管理者規則（只描述行情、沒有結論：不列）不公開；
  # 有方向時（tone=bullish 或等待條件）才列觀望注意（10/28 管理者：聖暉不放）
  self.assertEqual(p.verdict_class(answer,p._ev_norm(raw),['甲公司'])[0],'ignored')
  self.assertEqual(p.verdict_class(dict(answer,tone='bullish'),p._ev_norm(raw),['甲公司'])[0],'watch_watch')
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
  raw='甲公司是主要追蹤標的，但正式指令尚未發出，現在不得進場，必須等執行授權。'
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
