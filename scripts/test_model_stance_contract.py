"""語意問卷與最後定案不得被方向字詞覆寫；新增優先序依人類2026/10/27裁示。"""
import copy,unittest
from test_source_fact_ledger import p
class ModelStance(unittest.TestCase):
 def answer(self,raw,entry,holder='none',tone='neutral'):
  return p.verdict_class({'about_itself':True,'now':'none','tone':tone,'entry_for_new':entry,'holder_advice':holder,
    'quotes':{'stance':[raw]}},p._ev_norm(raw),names=['甲公司'])[0]
 def test_nonholding_and_waiting_are_not_word_based_prohibitions(self):
  self.assertEqual(self.answer('甲公司我沒有要買，已經超跌了，不要殺低，等它反彈。','none','hold_rebound'),'watch_watch')
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
 def test_final_ignored_enforcement_removes_late_public_row(self):
  row={'name':'甲公司','code':'1111','_date':'2026/10/27','reason':'甲公司只是未持有背景對照。'}
  sig={'watch_avoid':[row],'_verdicts':{'1111':{'cls':'ignored','enforce':True,'name':'甲公司','answer':{'ignored':{'answer':{'about_itself':False,'quotes':{}}}}}}}
  p.apply_verdicts(sig,'2026/10/27',final=True)
  self.assertFalse(sig['watch_avoid']);self.assertEqual(sig['ignored'][0]['code'],'1111')
if __name__=='__main__':unittest.main()
