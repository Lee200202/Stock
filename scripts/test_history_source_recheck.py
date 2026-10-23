"""來源更正與價格推測的邊界。"""
import json
from pathlib import Path
import tempfile
import unittest
from . import recheck_history_sources as repair
from . import market_history as h


class RecheckTests(unittest.TestCase):
    def case(self, stamp=None):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);p=root/'archive/tpex/6191/199501.json'
        bad=['1995-01-18',22.7,22.5,22.5,22.5,-.2,3000]
        valid=['1995-01-19',22.5,23,22,22.5,0,5000]
        rec={'rows':[bad,valid]}
        if stamp:rec['fetchedAt']=stamp
        h._write(p,rec)
        meta={'quarantinedRows':[{'code':'6191','date':bad[0],'raw':bad,'sourceFile':'tpex/6191/199501.json'}]}
        return root,p,meta,bad,valid

    def test_official_corrected_row_only_other_days_kept(self):
        root,p,m,bad,valid=self.case();correct=[bad[0],22.5,22.5,22.5,22.5,-.2,3000]
        # 更正值只作替身契約，不代表目前官方已有這個開盤價。
        r=repair.recheck(root/'archive',m,root/'report',fetch=lambda *a:{'rows':[correct]})
        j=json.loads(p.read_text());self.assertEqual(j['rows'],[correct,valid]);self.assertEqual(r['repairedRows'],1)
        self.assertEqual(j['qualityRepairs'][0]['before'],bad);self.assertFalse(r['published'])

    def test_same_invalid_response_not_clamped(self):
        root,p,m,bad,valid=self.case();r=repair.recheck(root/'archive',m,root/'report',fetch=lambda *a:{'rows':[bad,valid]})
        self.assertEqual(json.loads(p.read_text())['rows'],[bad,valid]);self.assertEqual(r['repairedRows'],0)

    def test_empty_or_failed_response_preserves_records(self):
        for failure in (False,True):
            root,p,m,bad,valid=self.case()
            def fetch(*args):
                if failure:raise TimeoutError('暫時來源失敗')
                return {'rows':[]}
            r=repair.recheck(root/'archive',m,root/'report',fetch=fetch)
            self.assertEqual(json.loads(p.read_text())['rows'],[bad,valid]);self.assertEqual(r['failedMonths'],1)

    def test_recent_archive_does_not_requery(self):
        root,p,m,bad,valid=self.case(h._iso_now())
        r=repair.recheck(root/'archive',m,root/'report',fetch=lambda *a:self.fail('冷卻期間不能重查'))
        self.assertEqual(r['keptCooldownMonths'],1)

    def test_other_edit_and_path_traversal_not_overwritten(self):
        root,p,m,bad,valid=self.case();m['quarantinedRows'][0]['raw']=['不同指紋']
        m['quarantinedRows'].append({'sourceFile':'../../outside.json'})
        correct=[bad[0],22.5,22.5,22.5,22.5,-.2,3000]
        r=repair.recheck(root/'archive',m,root/'report',fetch=lambda *a:{'rows':[correct]})
        self.assertEqual(json.loads(p.read_text())['rows'],[bad,valid]);self.assertEqual(r['repairedRows'],0)

    def test_budget_limits_source_requests(self):
        root,p,m,bad,valid=self.case()
        r=repair.recheck(root/'archive',m,root/'report',limit=0,fetch=lambda *a:self.fail('不能超過預算'))
        self.assertEqual(r['pendingMonths'],1)

    def test_edit_during_request_is_preserved(self):
        root,p,m,bad,valid=self.case();changed=[bad[0],22.6,22.7,22.4,22.5,-.2,3000]
        def fetch(*args):
            h._write(p,{'rows':[changed,valid],'otherWork':'保留'})
            return {'rows':[[bad[0],22.5,22.5,22.5,22.5,-.2,3000]]}
        r=repair.recheck(root/'archive',m,root/'report',fetch=fetch)
        j=json.loads(p.read_text());self.assertEqual(j['rows'],[changed,valid]);self.assertEqual(j['otherWork'],'保留');self.assertEqual(r['repairedRows'],0)


if __name__ == '__main__':unittest.main()
