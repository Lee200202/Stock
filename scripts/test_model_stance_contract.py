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
  self.assertEqual(p.note_length_target(150)['min_chars'],30)
  self.assertEqual(p.note_length_target(400)['level'],'rich')

 def test_watch_note_holding_claim_is_flagged_not_rewritten_after_review(self):
  text='甲公司為會員持股，先前推薦後創新高。已持有者續抱。'
  sig={'watch_watch':[{'name':'甲公司','code':'1111','reason':text}],
       'watch_avoid':[{'name':'乙公司','code':'2222','reason':'乙公司已持有者不宜殺低。'}]}
  p.flag_watch_holding_claims(sig)
  self.assertEqual(sig['watch_watch'][0]['reason'],text)                 # 審閱後不改字，指紋不失效
  self.assertTrue(any('甲公司' in g for g in sig['_repair_gaps']))
  self.assertFalse(any('乙公司' in g for g in sig['_repair_gaps']))       # 給讀者的條件建議不算
  for claim in ('敦泰股價創新高，此為先前已佈局持有的驅動IC個股。','甲公司為會員持股。','這是會員的股票。'):
   self.assertTrue(p._HOLDING_CLAIM.search(claim),claim)
  for advice in ('已持有者續抱，不必急著賣出。','目前尚未持有，等回測季線。','持有者不宜殺低。'):
   self.assertFalse(p._HOLDING_CLAIM.search(advice),advice)
  # 條件句不算宣稱持有：不標記、不刪
  cond={'watch_watch':[{'name':'甲公司','code':'1111','reason':'若會員先前已持有，跌破月線才減碼。'}]}
  p.flag_watch_holding_claims(cond); self.assertFalse(cond.get('_repair_gaps'))

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

 def test_connector_ending_is_a_soft_flag_and_spoken_tics_still_block(self):
  def gates(note):
   _,hard,soft=p.quality_overview({'watch_avoid':[{'name':'甲公司','code':'1111','reason':note}]},check_chapters=False)
   return any('口語殘句' in x for x in hard), any('句尾停在連接字' in x for x in soft)
  self.assertEqual(gates('甲公司賣壓已逐漸緩和。'),(False,True))     # 完整句不擋，只標記
  self.assertEqual(gates('甲公司，你來看，它是不是跟。')[0],True)

 def test_source_review_keeps_the_reply_explanation_and_failed_checks(self):
  row={'code':'1111','name':'甲公司','_source_fact_ledger':{'facts':{}}}
  r=p._validated_source_review({'source_review':{'claims':[{'id':'c0','status':'supported','explanation':'模型原說明',
     'source_quote':'甲公司今天大漲','subject':'甲公司','subject_code':'1111'}]}},row,'甲公司今天大漲。','甲公司今天大漲，外資連續買超。')
  c=r['claims'][0]
  self.assertEqual(c['reply_explanation'],'模型原說明');self.assertEqual(c['reply_status'],'supported')
  self.assertTrue(c['failed_checks'])

 def test_record_position_comes_from_records_not_from_the_class(self):
  row={'code':'1111','name':'甲公司'}
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'cls':'holdings'}}},row),'none')     # 分類是持股不等於有紀錄
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'open':True}}},row),'open')
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'notice_sides':['buy']}}},row),'open')
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'open':True,'notice_sides':['sell']}}},row),'sold_today')   # 全數賣出不算仍持有
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'open':True,'cls':'sell','execution_verified':True}}},row),'sold_today')
  self.assertEqual(p.record_position({'_verdicts':{'1111':{'execution_verified':True,'cls':'buy'}}},row),'open')

 def test_quote_spanning_an_uncited_neighbour_segment_counts(self):
  # 2026/10/28 嘉澤：引句跨 S0011→S0012，編號只列 S0012，兩輪作廢
  s11='嘉澤昨天漲130塊，這是不是昨天漲130塊的';s12='嘉澤？那今天早上嘉澤，有沒有漲到2,000多？我也沒有賣啊。'
  q='這是不是昨天漲130塊的嘉澤？那今天早上嘉澤，有沒有漲到2,000多？'
  a={'quotes':{'stance':[q]},'_verified_quote_sources':{'stance':[s12]},'_verified_quote_neighbors':{'stance':[s11+chr(10)+s12]}}
  self.assertEqual(p._questionnaire_verified_quotes(a,'stance',['嘉澤']),[q])
  # P0-5（外部審查）：沒有相鄰段時，只收逐字在所引原段、含本股名稱的那一句，不收跨段的整段引句
  self.assertEqual(p._questionnaire_verified_quotes(dict(a,_verified_quote_neighbors={}),'stance',['嘉澤']),['那今天早上嘉澤，有沒有漲到2,000多？'])
  far=dict(a,quotes={'stance':['別的段落才有的一句話']})
  self.assertEqual(p._questionnaire_verified_quotes(far,'stance',['嘉澤']),[])

 def test_notice_backed_two_char_holding_is_not_dropped_as_an_ordinary_word(self):
  # 2026/10/28 旭隼：原稿沒念名稱，續抱通知卻有
  raw='有一隻股票今天跌，只是洗盤，續抱。'
  sig=lambda:{'holdings':[{'code':'6409','name':'旭隼','note':'旭隼今日下跌只是洗盤，續抱。'}]}
  with patch.object(p,'_CODE_MAP',{'6409':'旭隼'}),patch.object(p,'_DAY_SOURCE_CONTEXT',{'company_candidates':{'6409':'旭隼'}}):
   self.assertEqual(len(p.drop_ordinary_word_rows(sig(),raw)['holdings']),1)
  with patch.object(p,'_CODE_MAP',{'6409':'旭隼'}),patch.object(p,'_DAY_SOURCE_CONTEXT',{}):
   self.assertEqual(len(p.drop_ordinary_word_rows(sig(),raw)['holdings']),0)   # 沒有通知照舊不開持股

 def test_condensed_long_quote_counts_only_when_every_clause_is_verbatim_in_order(self):
  C=p._clauses_in_order
  src='國巨這兩天漲的還不夠他一天跌。你有沒有看到？上個禮拜跌一根黑K棒，這兩天還漲不過去。啊你們只會看到漲的，你們只會看到漲的。而不是知道買賣股票要懂成本。'
  self.assertTrue(C('國巨這兩天漲的還不夠他一天跌。上個禮拜跌一根黑K棒，這兩天還漲不過去。……而不是知道買賣股票要懂成本。',src))   # 省掉完整子句
  self.assertTrue(C('啊你們只會看到漲的，而不是知道買賣股票要懂成本。',src))                                                     # 省掉重複的整句
  self.assertFalse(C('國巨這兩天漲的還不夠他一天跌。這兩天會大漲突破。',src))
  self.assertFalse(C('而不是知道買賣股票要懂成本。國巨這兩天漲的還不夠他一天跌。',src))                                         # 順序顛倒
  self.assertTrue(C('大立光，不買。這是我現在的邏輯。','大立光，不買。聯發科，不買。這是我現在的邏輯。'))
  self.assertFalse(C('台積電我本連賣都沒有賣，台積電會補缺口。','台積電我本來連賣都沒有賣，台積電會來補這一個缺口。'))       # 改字不收
  other='甲公司不買，EPS29元。乙公司外資連續買超，今天創新高。'
  self.assertFalse(C('乙公司外資連續買超，不買，今天創新高。',other))
  self.assertFalse(C('乙公司外資連續買超，29，今天創新高。',other))
  # 審閱反例：不能從一個原稿子句中間挑字
  self.assertFalse(C('甲公司，不買，仍需等待回測確認。','甲公司不是不買，仍需等待回測確認。'))
  self.assertFalse(C('今天我，買進甲公司，後續等待量能確認。','今天我如果買進甲公司，後續等待量能確認。'))
  self.assertFalse(C('甲公司，買進，仍需等待回測確認。','甲公司不一定會買進，仍需等待回測確認。'))
  self.assertFalse(C('今天我，買進甲公司，後續等待量能確認。','今天我假如真的買進甲公司，後續等待量能確認。'))
  self.assertFalse(C('今天我，買進甲公司，後續等待量能確認。','今天我，如果，買進甲公司，後續等待量能確認。'))           # 省掉的整句只是假設字

 def test_comparison_role_with_verified_bearish_analysis_is_not_ignored(self):
  # 2026/10/28 環球晶：答「陪襯」又答本股有獨立分析、現在不能買，分析引句核對過
  raw='環球晶昨天破新低，今天大漲有什麼用？不要看到破新低一漲上來，你就買。'
  a={'about_itself':True,'content_role':'comparison_only','independent_analysis':True,'now':'prohibit','tone':'bearish',
     'entry_for_new':'prohibit','quotes':{'stance':['不要看到破新低一漲上來，你就買。'],'analysis':['環球晶昨天破新低，今天大漲有什麼用？']}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['環球晶'])[0],'watch_avoid')
  b=dict(a,independent_analysis=False,quotes={'stance':['不要看到破新低一漲上來，你就買。']})
  self.assertEqual(p.verdict_class(b,p._ev_norm(raw),['環球晶'])[0],'watch_avoid')  # v9：陪襯＋明講不能買＋偏空＋本股具體原句
  c=dict(b,tone='neutral')
  self.assertEqual(p.verdict_class(c,p._ev_norm(raw),['環球晶'])[0],'ignored')      # 沒有偏空語氣的陪襯照舊不列

 def test_unresolved_holding_name_gets_its_code_back_and_follows_the_record_rule(self):
  # 2026/10/28 東台：代號在後段遺失，被當成「對不到代號」隔離
  sig={'holdings':[{'name':'東台','code':'','note':'提問會員自己買進，續抱。'}]}
  with patch.object(p,'_CODE_MAP',{'4526':'東台'}):
   out=p.hold_back_unresolved_positions(sig,'2026/10/28')
  self.assertFalse(out['holdings']);self.assertFalse(out.get('uncertain'))
  self.assertEqual(out['watch_watch'][0]['code'],'4526')
  sig2={'holdings':[{'name':'東台','code':'','note':'續抱。'}],'_verdicts':{'4526':{'open':True}}}
  with patch.object(p,'_CODE_MAP',{'4526':'東台'}):
   self.assertEqual(p.hold_back_unresolved_positions(sig2,'2026/10/28')['holdings'][0]['code'],'4526')
  sig3={'holdings':[{'name':'無名股','code':'','note':'續抱。'}]}
  with patch.object(p,'_CODE_MAP',{'4526':'東台'}):
   self.assertTrue(p.hold_back_unresolved_positions(sig3,'2026/10/28')['uncertain'])            # 對不到照舊隔離

 def test_spoken_official_code_is_not_sent_back_to_name_guessing(self):
  # 2026/10/28 東台：原字「4526」已對到 4526，不能再被名稱釐清洗成待確認
  sig={'watch_watch':[{'name':'東台','原始語音名稱':'4526','code':'4526','reason':'續抱。','evidence':['老師早，4526再抱嗎？']}]}
  def boom(*a,**k): raise AssertionError('不應呼叫模型')
  with patch.object(p,'get_code_map',return_value={'4526':'東台','2330':'台積電'}),patch.object(p,'call_gemini',side_effect=boom):
   out=p.resolve_unclear_names(sig,'老師早，4526再抱嗎？')
  self.assertEqual(out['watch_watch'][0]['code'],'4526')

 def test_past_trade_recap_with_comparison_role_is_history(self):
  # 2026/10/28 聖暉：拿已賣出的股票回顧，沒有本股當下分析
  raw='聖暉今天大漲，這一支股票是不是我們會員大賺的股票？我們賣在這邊，我沒有賣在最高點。有沒有一堆人套在聖暉？'
  a={'about_itself':True,'past_trade':True,'content_role':'historical_only','independent_analysis':False,'now':'prohibit','tone':'bearish',
     'entry_for_new':'none','quotes':{'past_trade':['我們賣在這邊，我沒有賣在最高點'],'stance':['有沒有一堆人套在聖暉？']},
     '_verified_quote_sources':{'past_trade':[raw],'stance':[raw]}}   # 回顧引句沒有股名時，要靠所引原段確認是本股
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['聖暉'])[0],'history')
  b=dict(a,content_role='independent_analysis')
  self.assertEqual(p.verdict_class(b,p._ev_norm(raw),['聖暉'])[0],'history')        # q8 P3-3：引句全是過去買賣／套牢回顧、沒有當下指令時，不靠角色欄
  raw2=raw+'聖暉後面還會再往下走，跌破季線。'
  c=dict(b,quotes={'past_trade':['我們賣在這邊，我沒有賣在最高點'],'stance':['有沒有一堆人套在聖暉？','聖暉後面還會再往下走，跌破季線。']},
         _verified_quote_sources={'past_trade':[raw2],'stance':[raw2]})
  self.assertNotEqual(p.verdict_class(c,p._ev_norm(raw2),['聖暉'])[0],'history')   # 有往後的看法就不是單純回顧

 def test_excluded_review_skip_keeps_the_stock_for_adjudication(self):
  sig={'ignored':[]}
  with patch.object(p,'_only_ordinary_word',return_value=False):
   p._hold_for_adjudication(sig,{'heard':'台達電','official':'台達電','code':'2308'},'台達電','台達電現在要來回震盪打底。')
   p._hold_for_adjudication(sig,{'heard':'台達電','official':'台達電','code':'2308'},'台達電','台達電現在要來回震盪打底。')
  self.assertEqual([x['code'] for x in sig['ignored']],['2308'])                   # 只補一次
  sig2={'ignored':[]}
  with patch.object(p,'_only_ordinary_word',return_value=True):
   p._hold_for_adjudication(sig2,{'heard':'世界','official':'世界','code':'5347'},'世界','全世界都在漲。')
  self.assertFalse(sig2['ignored'])                                                 # 日常用語不補

 def test_recomputed_holding_uses_the_holding_quote_and_hold_advice(self):
  # 2026/10/28 東台、敦泰：「繼續抱著」只放在持有欄、立場欄空；建議續抱、語氣中性
  raw='老師早，4526再抱嗎？張總，4526繼續抱嗎？你看，我還是回答抱。沒有背離，你就續抱。'
  a={'about_itself':True,'holding_now':False,'_recomputed_from_holding':True,'holder_advice':'hold','now':'none','tone':'neutral',
     'entry_for_new':'none','quotes':{'holding_now':['沒有背離，你就續抱'],'stance':[]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['東台','4526'])[0],'watch_watch')
  b=dict(a); b.pop('_recomputed_from_holding')
  self.assertNotEqual(p.verdict_class(b,p._ev_norm(raw),['東台','4526'])[0],'watch_watch')   # 只在持有不成立的重算時這樣處理

 def test_comparison_role_with_explicit_bearish_prohibit_and_specific_quote_is_avoid(self):
  raw='今天大漲哪裡有6488環球金，大漲，來，昨天破低，昨天破新低。啊今天大漲有什麼用？啊你看到大漲，就去買了。'
  a={'about_itself':True,'content_role':'comparison_only','independent_analysis':False,'now':'prohibit','tone':'bearish',
     'entry_for_new':'prohibit','quotes':{'stance':['昨天破低，昨天破新低。啊今天大漲有什麼用？']}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['環球晶','環球金','6488'])[0],'watch_avoid')
  self.assertEqual(p.verdict_class(dict(a,selection_intent='explicit_exclusion'),p._ev_norm(raw),['環球晶'])[0],'ignored')  # 選股範圍的排除照舊
  self.assertEqual(p.verdict_class(dict(a,tone='neutral'),p._ev_norm(raw),['環球晶'])[0],'ignored')

 def test_invalid_analysis_quote_with_verified_stance_does_not_void_the_answer(self):
  seg='昨天破低，昨天破新低。啊今天大漲有什麼用？啊你看到大漲，就去買了。'
  a={'about_itself':True,'independent_analysis':True,'now':'prohibit','tone':'bearish','entry_for_new':'prohibit',
     'quotes':{'stance':['昨天破低，昨天破新低。啊今天大漲有什麼用？'],'analysis':['環球晶破底後反彈無力，不宜追高']},
     '_verified_quote_sources':{'stance':[seg],'analysis':[seg]}}
  self.assertEqual(p._questionnaire_issues(a,seg,['環球晶']),[])
  self.assertTrue(a.get('_field_warnings'))
  c={'about_itself':True,'independent_analysis':True,'now':'prohibit','tone':'bearish',
     'quotes':{'stance':['環球晶完全不是這樣講'],'analysis':['環球晶破底後反彈無力']},'_verified_quote_sources':{'stance':[seg],'analysis':[seg]}}
  self.assertTrue(p._questionnaire_issues(c,seg,['環球晶']))                       # 立場也對不回就照舊作廢

 def test_historical_role_with_recap_in_analysis_and_no_instruction_is_history(self):
  raw='聖暉今天大漲，這一支股票是不是我們會員大賺的股票？我們賣在這邊，我沒有賣在最高點。有沒有一堆人套在聖暉？'
  a={'about_itself':True,'past_trade':True,'content_role':'historical_only','independent_analysis':True,'now':'none','tone':'bearish',
     'entry_for_new':'none','holder_advice':'none','quotes':{'past_trade':['我們賣在這邊，我沒有賣在最高點'],'analysis':['有沒有一堆人套在聖暉？']},
     '_verified_quote_sources':{'past_trade':[raw],'analysis':[raw]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['聖暉'])[0],'history')
  # P0-7（外部審查，10/28 聖暉實際答案兩輪都同時填 historical_only 與 prohibit）：全文角色是回顧時，順帶的禁買也算回顧
  self.assertEqual(p.verdict_class(dict(a,now='prohibit',entry_for_new='prohibit'),p._ev_norm(raw),['聖暉'])[0],'history')
  # 但給了新的買進或等待條件、或續抱指令，就不是純回顧
  self.assertNotEqual(p.verdict_class(dict(a,entry_for_new='wait_condition',now='conditional'),p._ev_norm(raw),['聖暉'])[0],'history')

 def test_excluded_review_failure_still_sends_every_target_to_adjudication(self):
  raw='好，昨天有沒有跟你們講不要亂殺台達電？台達電現在要來回震盪打底，我沒有買台達電啦，我沒有買台達電。'
  sig={k:[] for k in ('buy','sell','holdings','watch_watch','watch_avoid','history','uncertain','ignored')}
  def boom(*a,**k): raise RuntimeError('模擬呼叫失敗')
  with patch.object(p,'_CODE_MAP',{'2308':'台達電'}),patch.object(p,'call_gemini',side_effect=boom),       patch.object(p,'budget_left',return_value=9999),patch.object(p,'GEMINI_KEYS',['offline']),patch.object(p,'_only_ordinary_word',return_value=False):
   p._INVENTORY_CACHE['key']=None
   out=p.review_excluded_stocks(sig,raw,'2026/10/28')
  self.assertIn('2308',[str(x.get('code')) for x in out['ignored']])

 def test_positive_candidate_not_yet_needs_own_analysis_and_position_disclosure_needs_explicit_no_analysis(self):
  # 10/27 實際答案：群聯、宜鼎「還不能買」那兩票都答本股有獨立分析（還沒穩、不用急）→ 觀望注意
  raw='我說我記憶體在看的是群聯，跟這一隻宜蘭之光。他也還沒有穩。所以我還沒有對記憶體有任何出手的念頭。'
  a={'about_itself':True,'independent_analysis':True,'entry_for_new':'not_yet','now':'not_yet','tone':'neutral','selection_intent':'positive_candidate',
     'holder_advice':'none','past_trade':False,'quotes':{'stance':[raw],'analysis':[raw]}}
  self.assertEqual(p.verdict_class(a,p._ev_norm(raw),['群聯'])[0],'watch_watch')
  # 10/27 南亞科：「記憶體我目前還沒有買」，明確答沒有獨立分析 → 不列
  raw2='南亞科。我說記憶體我目前還沒有買，好，我還沒有買。'
  b={'about_itself':True,'independent_analysis':False,'entry_for_new':'not_yet','now':'not_yet','tone':'neutral','selection_intent':'neutral',
     'holder_advice':'none','past_trade':False,'quotes':{'stance':[raw2]}}
  self.assertEqual(p.verdict_class(b,p._ev_norm(raw2),['南亞科'])[0],'ignored')

 def test_member_asking_about_own_position_is_not_turned_into_a_member_holding(self):
  # 2026/10/29 管理者：東台是會員單獨的操作（會員問老師要不要賣），不是老師的會員持股
  tx='昨天，禮拜二，會員問我，老師早，4526再抱嗎？張震回答抱。張總，4526繼續抱嗎？你看，我還是回答抱。沒有背離，你就續抱。'
  sig={'watch_watch':[{'name':'4526','code':'4526','reason':'東台沒有背離，可以續抱。'}],'holdings':[]}
  with patch.object(p,'_CODE_MAP',{'4526':'東台'}):
   out=p.preserve_explicit_holdings(sig,tx)
  self.assertFalse(out.get('holdings'))
  tx2='美律，日K線MACD翻紅，沒有背離，所以你繼續抱著。'
  sig2={'watch_watch':[{'name':'美律','code':'2439','reason':'美律續抱。'}],'holdings':[]}
  with patch.object(p,'_CODE_MAP',{'2439':'美律'}):
   self.assertEqual([r['code'] for r in p.preserve_explicit_holdings(sig2,tx2)['holdings']],['2439'])   # 講者的直接指示照舊

 def test_verdict_fingerprint_includes_the_position_snapshot(self):
  self.assertNotEqual(p._verdict_position_key({'open':True}),p._verdict_position_key({'open':False}))

 def test_verdict_cache_version_carries_the_prompt_and_schema_fingerprint(self):
  # 改了裁決提示詞或答案格式，快取鍵自動換掉，不靠人記得升版（2026/10/28 審閱）
  import hashlib,json
  want=hashlib.sha256((p.ADJUDICATE_SYSTEM+json.dumps(p.verdict_response_schema(),ensure_ascii=False,sort_keys=True)+getattr(p,'QUOTE_VERIFIER_VERSION','')).encode('utf-8')).hexdigest()[:8]
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
