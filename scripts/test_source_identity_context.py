"""量詞、跨股切窗、通知原文及S編號引用的正反例，不呼叫模型。"""
import copy
import json
import unittest
from unittest.mock import patch
from test_source_fact_ledger import p


class IdentityContext(unittest.TestCase):
    def test_rejected_ordinary_surface_is_not_an_entity_boundary(self):
        raw='勤誠假破底。看一下他的數字，去年29，今年上半年21.83。'
        row={'name':'勤誠','code':'8210'}
        for reason in ('ordinary_word','ambiguous_weak_name','mention_only'):
            sig={'holdings':[row],'ignored':[{'name':'數字','code':'5287','exclusion_reason':reason}]}
            self.assertIn('21.83',''.join(t for t,_ in p._entity_scope(row,sig,raw,after_chars=900)))
        sig['ignored'][0]['exclusion_reason']='independent_analysis'
        self.assertNotIn('21.83',''.join(t for t,_ in p._entity_scope(row,sig,raw,after_chars=900)))

    def test_connector_does_not_cut_off_model_evidence(self):
        raw='甲公司目前尚未推薦。所以工具機落後補漲，後續仍需整理。'
        _,text=p._verdict_mark([raw],['甲公司'])
        self.assertIn('後續仍需整理',text)

    def test_today_notice_hold_is_a_position_fact_not_a_buy(self):
        class Sheet:
            def __init__(self,rows):self.rows=rows
            def get_all_values(self):return self.rows
        class Book:
            def worksheet(self,name):
                return Sheet({'操作紀錄':[['日期','代號','股票名稱','方向','來源影片ID']],
                  '會員持股':[['日期','代號','股票名稱','來源影片ID'],
                    ['2026/10/27','1111','甲公司','CMONEY-1'],
                    ['2026/10/27','2222','乙公司','today-video']]}[name])
        with patch.object(p,'_source_sale_scopes',return_value={}):
            known,notice,opened=p._verdict_known(Book(),'2026/10/27',with_open=True)
        self.assertIn('1111',opened);self.assertNotIn('2222',opened)
        self.assertIn('1111',known);self.assertNotIn(('1111','buy'),notice)

    def test_quantity_resolution_does_not_disable_real_company_codes(self):
        with patch.object(p,'get_code_map',return_value={'2402':'毅嘉','5287':'數字'}):
            self.assertEqual(p.resolve_code('一家','')[0],p.REJECT)
            self.assertEqual(p.resolve_code('毅嘉','2402')[0],'2402')
            self.assertEqual(p.resolve_code('數字','5287')[0],'5287')
    def test_quantity_is_not_a_confirmed_phonetic_company(self):
        with patch.object(p, 'CONFIRMED_SOUNDS', {'毅嘉': ('2402','毅嘉')}), patch.object(p, '_SOUND_MEMO', {}):
            self.assertNotIn('一家', p._sound_hits('你看這一家公司成交量才多少，半年報21.83。'))
            self.assertEqual(p._sound_hits('毅嘉2402成交量放大。')['毅嘉'], ('2402','毅嘉'))
        with patch.object(p, '_SOUND_MEMO', {'flat':'前面一家後面', 'table':{'一家':('2402','毅嘉')}}):
            self.assertNotIn('一家', p._sound_hits('一家'))

    def test_quantity_cannot_cut_off_the_current_company_eps(self):
        raw='最適合假破底的股票叫勤誠。你看這一家公司成交量才多少？去年29，今年上半年21.83，全年預估40。'
        row={'name':'勤誠','code':'8210'}
        signals={'holdings':[row], 'ignored':[{'name':'一家','code':'2402'}]}
        material=''.join(t for t,_ in p._entity_scope(row,signals,raw,after_chars=900))
        self.assertIn('21.83',material)
        other=raw.replace('這一家公司','毅嘉公司')
        signals['ignored']=[{'name':'毅嘉','code':'2402'}]
        material=''.join(t for t,_ in p._entity_scope(row,signals,other,after_chars=900))
        self.assertNotIn('21.83',material)

    def test_notice_context_uses_original_not_saved_rewrite(self):
        class Sheet:
            def __init__(self,rows):self.rows=rows
            def get_all_values(self):return copy.deepcopy(self.rows)
        class Book:
            def worksheet(self,name):
                return Sheet({'會員簡訊':[['發文時間','文章ID','原文','解析明細'],
                    ['2026/10/27 09:43','1','旭隼續抱；勤誠可買。','人工改寫答案'],
                    ['2026/10/28 09:43','2','未來通知','答案']],
                    '操作紀錄':[['日期','代號','股票名稱','來源影片ID','理由摘錄']],
                    '會員持股':[['日期','代號','股票名稱','來源影片ID','說明重點'],
                        ['2026/10/27','6409','旭隼','CMONEY-1','人工改寫不可輸入']] }[name])
        with patch.object(p,'_DAY_SOURCE_CONTEXT',{}):
            context=p.load_day_source_context(Book(),'2026/10/27')
            text=json.dumps(context,ensure_ascii=False)
            self.assertIn('旭隼續抱',text);self.assertNotIn('人工改寫',text);self.assertNotIn('未來通知',text)
            self.assertEqual(context['company_candidates'],{'6409':'旭隼'})

    def test_quote_ids_restore_only_real_source_and_not_model_private_fields(self):
        raw='甲公司還在低檔整理，目前等待季線止穩後再評估。'
        item=('1111',{'name':'甲公司','heard':[],'known':[],'passages':[{'text':raw}]})
        reply={'verdicts':[{'id':'1111','about_itself':True,'now':'conditional','entry_for_new':'wait_condition',
            'quotes':{},'quote_source_ids':{'stance':['S0001']},'_verified_quote_sources':{'stance':['杜撰內容']}}]}
        with patch.object(p,'call_gemini',return_value=json.dumps(reply)),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}),patch.object(p,'_DAY_SOURCE_CONTEXT',{}):
            answer=p._ask_verdicts([item],'2026/10/27',0,raw)['1111']
        self.assertEqual(answer['_verified_quote_sources']['stance'],[raw])
        self.assertEqual(p.verdict_class(answer,'被切斷的定位窗')[0],'watch_watch')
        reply['verdicts'][0]['quote_source_ids']={'stance':['missing']}
        with patch.object(p,'call_gemini',return_value=json.dumps(reply)),patch.object(p,'_CODE_MAP',{'1111':'甲公司'}):
            answer=p._ask_verdicts([item],'2026/10/27',0,raw)['1111']
        self.assertNotIn('_verified_quote_sources',answer)
        self.assertEqual(p.verdict_class(answer,raw)[0],'')


if __name__=='__main__':unittest.main()
