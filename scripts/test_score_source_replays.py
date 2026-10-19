import unittest
from score_source_replays import score, transcript_rows


class FrozenReferenceScoring(unittest.TestCase):
    def test_website_and_explanatory_sections_cannot_supply_the_answer(self):
        log = '''===== 重播結果 2026/10/19 =====
【買入】0 檔
【觀望注意】1 檔
- 甲（1111）8 字：回測均線後可留意。
【① 盤勢】3 點
- 乙（2222）8 字：這一行不是個股。
===== 與網站目前這一天的資料比對 =====
- 丙（3333）8 字：網站說買入。
'''
        result = transcript_rows(log)
        self.assertEqual(set(result), {'1111'})
        self.assertFalse(score({'must':{'1111':['buy']}},result)['checks'][0]['ok'])

    def test_holding_watch_acceptance_does_not_accept_a_new_trade(self):
        ref={'must':{'1111':['holdings','watch_watch']}}
        self.assertEqual(score(ref,{'1111':[{'class':'holdings'}]})['matched_required'],1)
        self.assertEqual(score(ref,{'1111':[{'class':'buy'}]})['matched_required'],0)

    def test_missing_is_not_a_wrong_category_and_unresolved_is_not_a_pass(self):
        result=score({'must':{'1111':['buy']},'must_not':['3333']},
                     {'2222':[{'class':'holdings'}], '3333':[{'class':'watch_watch'}]})
        self.assertEqual(result['missing'],['1111'])
        self.assertEqual(result['wrong_category'],[])
        self.assertEqual(result['unscored_codes'],['2222'])
        self.assertEqual(len(result['forbidden_present']),1)


if __name__=='__main__':unittest.main()
