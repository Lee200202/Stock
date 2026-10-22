"""離線驗證原子來源修正契約，不呼叫模型或網路。"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('source_review', ROOT/'scripts/apply_source_review.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
H = ['日期','股票名稱','代號','目前立場','說明重點','來源影片ID']
T = ['日期','股票名稱','代號','方向','價位說明','理由摘錄','來源影片ID','序']


def fixture():
    tables = {'會員持股': [H, ['2026/10/18','甲','1111','持有','前日仍持有','older'],
                             ['2026/10/19','甲','1111','持有','大盤說明','video']],
              '操作紀錄': [T, ['2026/10/19','乙','2222','觀望不碰','123','等待拉回','video','1']]}
    items = [dict(code='1111',name='甲',category='holdings',action='delete',
                  previous_note_sha256=m.sha('大盤說明')),
             dict(code='2222',name='乙',category='watch_watch',previous_category='watch_avoid',
                  action='update',previous_note_sha256=m.sha('等待拉回'),text='拉回有買點'),
             dict(code='3333',name='丙',category='holdings',action='insert',text='既有部位續抱')]
    plan = dict(date='2026/10/19',video_id='video',items=items)
    return tables, plan, {'1111':'甲','2222':'乙','3333':'丙'}


class SourceReviewTests(unittest.TestCase):
    def test_clear_dividend_or_eps_as_price_requires_explicit_review(self):
        t,p,n=fixture(); item=p['items'][1]
        item['clear_non_trade_price']=True
        with self.assertRaises(ValueError):m.make_changes(t,p,n)
        item['non_trade_price_review_reason']='原文7元是現金股利，不是進出場或觀察股價'
        out=m.projected(t,m.make_changes(t,p,n));m.verify_final(out,p)
        self.assertEqual(out['操作紀錄'][1][4],'未說明')
        out['操作紀錄'][1][4]='7'
        with self.assertRaises(ValueError):m.verify_final(out,p)
        item.pop('clear_non_trade_price')
        out=m.projected(t,m.make_changes(t,p,n))
        self.assertEqual(out['操作紀錄'][1][4],'123')

    def test_clear_price_cannot_modify_holding_or_transaction_cost(self):
        t,p,n=fixture();p['items'][0].update(action='update',text='已核對續抱',
            clear_non_trade_price=True,non_trade_price_review_reason='不應改持有成本')
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_plan_preserves_historical_position_and_non_targets(self):
        tables,p,n=fixture(); before=copy.deepcopy(tables)
        changes=m.make_changes(tables,p,n); out=m.projected(tables,changes)
        self.assertEqual(tables,before)
        self.assertEqual(out['會員持股'][1],before['會員持股'][1])
        self.assertEqual(out['會員持股'][2][3:5],['續抱','既有部位續抱'])
        self.assertEqual(out['操作紀錄'][1][3:6],['觀望注意','123','拉回有買點'])
        m.verify_final(out,p)

    def test_changed_note_aborts(self):
        t,p,n=fixture();t['會員持股'][2][4]='別的工作已修正'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_notice_and_manual_sources_never_overwritten(self):
        for source in ('CMONEY-1','manual'):
            t,p,n=fixture();t['會員持股'][2][5]=source
            with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_duplicate_identity_aborts(self):
        t,p,n=fixture();t['操作紀錄'].append(t['操作紀錄'][1][:])
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_insert_cannot_bypass_protected_existing_row(self):
        t,p,n=fixture();t['會員持股'].append(['2026/10/19','丙','3333','持有','人工','manual'])
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_trades_cannot_be_created(self):
        t,p,n=fixture();p['items'][2]['category']='buy'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_manual_submission_repair_is_explicit_date_bound_and_sms_is_protected(self):
        t,p,n=fixture();t['會員持股'][2][5]='MANUAL-20261019'
        p['items'][0]['previous_source_id']='MANUAL-20261019'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)
        p['items'][0].update(reviewed_manual_source=True,manual_source_review_reason='完整來源已覆核')
        changes=m.make_changes(t,p,n)
        m.verify_final(m.projected(t,changes),p)
        p['items'][0]['previous_source_id']='CMONEY-123'
        t['會員持股'][2][5]='CMONEY-123'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)

    def test_single_atomic_batch_contains_receipt_and_ordered_mutations(self):
        t,p,n=fixture();changes=m.make_changes(t,p,n)
        req=m.requests_for(changes,{'操作紀錄':1,'會員持股':2,'修正建議':3},[['receipt']])
        self.assertEqual([list(r)[0] for r in req],
                         ['updateCells','deleteDimension','appendCells','appendCells'])
        self.assertEqual(req[-1]['appendCells']['sheetId'],3)
        self.assertEqual(req[1]['deleteDimension']['range']['startIndex'],2)

    def test_historical_trade_requires_explicit_review_and_cannot_be_today(self):
        t,p,n=fixture();t['操作紀錄'][1][3]='賣出'
        p['items'][1]['previous_category']='sell'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)
        p['items'][1]['historical_trade_review']={'actor_and_time_reviewed':True,'event_date':'2026/10/19'}
        with self.assertRaises(ValueError):m.make_changes(t,p,n)
        p['items'][1]['historical_trade_review']['event_date']='2026/10/18'
        with self.assertRaises(ValueError):m.make_changes(t,p,n)
        t['操作紀錄'].append(['2026/10/18','乙','2222','賣出','123','已賣','earlier','1'])
        m.make_changes(t,p,n)

    def test_final_state_verification_rejects_note_or_class_drift(self):
        t,p,n=fixture();out=m.projected(t,m.make_changes(t,p,n))
        out['操作紀錄'][1][3]='觀望不碰'
        with self.assertRaises(ValueError):m.verify_final(out,p)


if __name__=='__main__':unittest.main()
