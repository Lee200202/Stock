"""Offline public-editor contracts. No API, credentials, or production writes."""
import copy,importlib.util,json,os,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('SPREADSHEET_ID','offline-test')
os.environ.setdefault('GEMINI_API_KEY','offline-test')
spec=importlib.util.spec_from_file_location('public_edit_batch_contract',ROOT/'pipeline/pipeline.py')
pl=importlib.util.module_from_spec(spec);spec.loader.exec_module(pl)

def fixture():
    return {'holdings':[{'code':'1111','name':'甲公司','note':'甲公司仍持有，成本100元，等待季線支撐確認。','evidence':['我有甲公司，成本100元。']}],
            'watch_watch':[{'code':'2222','name':'乙公司','reason':'乙公司在箱型低檔是OＫ的，若要破早就破了。'}],
            'market':[{'kind':'event','text':'大盤回測60分K線支撐，尚需觀察。','_evidence_verified':True,'evidence':['大盤回測60分K線。']}]}

def run_batch(sig, transform=lambda entry, answer:answer):
    calls=[]
    def fake(system,payload,**kwargs):
        data=json.loads(payload);calls.append(data)
        answers=[]
        for e in data['texts']:
            answer={'id':e['id'],'text':e['text'],'meaning_preserved':True,'source_supported':True,'category_consistent':True,'written_style_ok':True,'issues_found':[]}
            answers.append(transform(e,answer))
        return json.dumps({'texts':answers},ensure_ascii=False)
    with patch.object(pl,'call_gemini',side_effect=fake),patch.object(pl,'budget_left',return_value=9999),patch.object(pl,'GEMINI_KEYS',['offline']),patch.dict(pl._QUOTA_STOP,{'daily':False}):
        out=pl.polish_public_texts(sig,'我有甲公司，成本100元。乙公司在箱型下緣。大盤回測60分K線。','2026/10/16')
    return out,calls

class PublicBatchContract(unittest.TestCase):
    def test_neutral_and_unknown_spelling_both_reviewed_once(self):
        out,calls=run_batch(fixture())
        self.assertEqual(len(calls),1);self.assertEqual(len(calls[0]['texts']),3)
        self.assertIn('source',calls[0]);self.assertEqual(calls[0]['texts'][0]['evidence'],['我有甲公司，成本100元。'])
        self.assertTrue(out['holdings'][0]['_public_edit_review']['source_supported'])
    def test_semantic_rejection_does_not_move_or_rewrite_stock(self):
        before=fixture()
        def reject(e,a):
            if e.get('name')=='乙公司':a.update(text='乙公司已經買進並持有。',category_consistent=False)
            return a
        out,calls=run_batch(copy.deepcopy(before),reject)
        self.assertEqual(out['watch_watch'][0]['reason'],before['watch_watch'][0]['reason'])
        self.assertNotIn('buy',out);self.assertEqual(len(calls),1)
        self.assertTrue(out['_repair_gaps'])
    def test_number_guard_still_rejects_model_approval(self):
        def hallucinate(e,a):a['text']=a['text'].replace('100元','120元');return a
        out,calls=run_batch(fixture(),hallucinate)
        self.assertIn('100元',out['holdings'][0]['note']);self.assertEqual(len(calls),1)
    def test_source_claim_must_be_explicit_boolean_true(self):
        for bad in (False,None,'true',1):
            def reject(e,a):a['source_supported']=bad;a['text']='已修改文字但沒有有效來源確認。';return a
            out,calls=run_batch(fixture(),reject)
            self.assertEqual(out['holdings'][0]['note'],fixture()['holdings'][0]['note'])
            self.assertEqual(len(calls),1)
    def test_saved_notes_are_reviewed_but_not_overwritten(self):
        sig=fixture();sig['holdings'][0]['_manual_note_kept']=True
        def edit(e,a):
            if e.get('locked'):a['text']='甲公司仍持有，100元成本，季線支撐等待確認。'
            return a
        out,calls=run_batch(sig,edit)
        self.assertTrue(calls[0]['texts'][0]['locked'])
        self.assertEqual(out['holdings'][0]['note'],fixture()['holdings'][0]['note'])
        self.assertTrue(any('人工說明AI複核' in x for x in out['_repair_gaps']))
    def test_style_word_and_semantic_regexes_removed(self):
        for n in ('_WS_QUESTION','_WS_PARTICLE','_WS_PERSON','_WS_SLANG','_WS_BUY','_WS_SELL','_WS_HOLD','_WS_KEEP_TERMS'):
            self.assertFalse(hasattr(pl,n),n)
    def test_own_denial_prevents_neighbor_ownership_promotion(self):
        tx='這是我們的股票，這一隻是我的，聯詠我沒有啊，聯詠我只是拿來分析而已，我有瑞鼎。'
        sig={'watch_watch':[{'name':'聯詠','code':'3034','reason':'季線向上。'}]}
        with patch.object(pl,'_CODE_MAP',{'3034':'聯詠','3592':'瑞鼎'}):
            out=pl.preserve_explicit_holdings(sig,tx)
        self.assertFalse(out.get('holdings'))
        self.assertEqual(out['watch_watch'][0]['code'],'3034')  # 否認只阻止開部位，不憑否認創造禁買立場。
    def test_past_denial_does_not_override_current_position(self):
        self.assertEqual(pl._explicitly_not_held({'甲公司'},'以前我沒有甲公司，現在已經買進。'),'')
        self.assertEqual(pl._explicitly_not_held({'甲公司'},'我沒有賣甲公司，續抱。'),'')
        self.assertTrue(pl._explicitly_not_held({'甲公司'},'甲公司我沒有啊，只是分析。'))

if __name__=='__main__':unittest.main()
