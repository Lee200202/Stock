import json
import unittest
from unittest.mock import patch
from test_quality import p, empty


class ArticleV12Tests(unittest.TestCase):
    def test_parenthetical_typos_removed_without_changing_evidence(self):
        r={'name':'祥碩','code':'5269','note':'成本1400（原文作14），歷經除息45元，目前續抱。','evidence':['成本14除息45']}
        s=empty();s['holdings']=[r];p.naturalize_signal_reasons(s)
        self.assertEqual(r['note'],'成本1400，歷經除息45元，目前續抱。')
        self.assertEqual(r['evidence'],['成本14除息45'])
        self.assertEqual(p.public_narrative('川服(川湖)今天大跌600元。',{'name':'川湖','code':'2059'}),'川湖今天大跌600元。')
        self.assertEqual(p.public_narrative('信化身為股王。',{'name':'信驊','code':'5274'}),'信驊身為股王。')
        self.assertEqual(p.public_narrative('川服(川湖)今天大跌600元。'),'川湖今天大跌600元。')
        self.assertIn('信化',p.public_narrative('信化尚待確認。'))  # 不把模糊同音全域強配

    def test_change_amount_is_not_price(self):
        self.assertEqual(p.display_price('600','','川湖今天大跌600元'),'未說明')
        self.assertEqual(p.display_price('17880','','股價17880元，大跌820元'),'17880')
        self.assertEqual(p.display_price('600','','目前股價600元，大跌600元'),'600')

    def test_title_uses_verified_content_and_is_twenty_chars(self):
        # v44：標題至少 15 字
        s=empty();s['market']=[{'kind':'event','text':'量縮震盪，市場靜待CPI公布再決定方向。','evidence':['量縮震盪市场靜待CPI'],
                              'headline':'量縮震盪，市場靜待CPI公布再決定方向','_evidence_verified':True}]
        self.assertEqual(p.article_title(s),'量縮震盪，市場靜待CPI公布再決定方向！')
        article=p.canonical_article(s,'2026/09/11','① 文章標題：張震：2026/09/11 盤勢與操作紀錄')
        self.assertEqual(article.splitlines()[0],'文章標題：量縮震盪，市場靜待CPI公布再決定方向！')
        self.assertNotIn('基本資訊',article)  # v22 起拿掉基本資訊一章
        s['market'][0]['headline']='必漲99999點，明日漲停'
        self.assertEqual(p.article_title(s),'CPI動向與量縮整理，本集盤勢與操作重點整理')
        self.assertLessEqual(len(p.article_title(s)),30)
        s['market'][0]['_evidence_verified']=False
        self.assertEqual(p.article_title(s),'本集市場觀察與會員操作重點整理')

    def run_topup(self,s,reply):
        source=('大盤量縮震盪。市場關注CPI公布。外資資金持續流入。買賣節奏要有耐心。不要追高殺低。持股要看基本面。'*150)
        with patch.object(p,'GEMINI_KEYS',['offline']),patch.object(p,'budget_left',return_value=900),patch.dict(p._QUOTA_STOP,{'daily':False}),patch.object(p,'call_gemini',return_value=json.dumps(reply)) as ai:
            p.ensure_article_minimums(s,source,'2026/09/11')
        return ai

    def test_macro_and_lesson_gaps_share_one_call(self):
        s=empty();rows=[]
        for kind,text in [('volume','量能：大盤量縮震盪。'),('event','事件：市場關注CPI公布。'),('flow','資金：外資資金持續流入。'),
                          ('view','耐心：買賣節奏要有耐心。'),('view','追高：不要追高殺低。'),('view','基本面：持股要看基本面。')]:
            rows.append({'kind':kind,'text':text,'evidence_refs':['S0001']})
        ai=self.run_topup(s,{'market':rows})
        self.assertEqual(ai.call_count,1)
        self.assertEqual(len([r for r in s['market'] if r['kind']!='view']),3)
        self.assertEqual(len([r for r in s['market'] if r['kind']=='view']),3)
        payload=json.loads(ai.call_args.args[1]);self.assertEqual((payload['need_macro'],payload['need_view']),(3,3))

    def test_sufficient_macro_and_lessons_skip_supplement(self):
        s=empty();s['market']=[{'kind':kind,'text':title+'：已驗證內容','_evidence_verified':True}
                            for kind in ('event','view') for title in ('甲','乙','丙')]
        self.run_topup(s,{}).assert_not_called()

    def test_unsupported_numbers_and_duplicate_bullets_do_not_fill_minimum(self):
        s=empty();rows=[{'kind':'event','text':'事件：CPI公布','evidence_refs':['S0001']},
            {'kind':'event','text':'事件：CPI公布','evidence_refs':['S0001']},
            {'kind':'flow','text':'資金999999億元','evidence_refs':['S0001']}]
        self.run_topup(s,{'market':rows})
        self.assertEqual(len(s['market']),1)
        self.assertTrue(any('盤勢內容偏短' in x for x in s['_repair_gaps']))
        self.assertFalse(p.needs_review_gaps(s['_repair_gaps']))


if __name__=='__main__':unittest.main()
