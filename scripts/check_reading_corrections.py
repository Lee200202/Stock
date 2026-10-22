"""無網路驗收批次校字與來源證據。"""
import importlib.util,json,hashlib,unittest,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from pypinyin import lazy_pinyin
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'pipeline'))
import reading_corrections as r
OFF={'3533':'嘉澤','3081':'聯亞','3086':'華義','2330':'台積電','2498':'宏達電'}
BASE='今天加折在季線附近整理，接下來要觀察景線是否突破。聯亞9999的買點仍須等待，不要追高。'
def edit(before,after,kind='term',code=''):
 return {'quote':BASE,'before':before,'after':after,'kind':kind,'code':code}
fp=lambda s:hashlib.sha256(''.join(s.split()).encode()).hexdigest()[:16]
class ReadingTests(unittest.TestCase):
 def test_accept_local_spelling(self):
  self.assertFalse(r.validate_edit(edit('景線','頸線'),BASE,OFF,lazy_pinyin))
  self.assertFalse(r.validate_edit(edit('加折','嘉澤','stock_name','3533'),BASE,OFF,lazy_pinyin))
 def test_code_requires_adjacent_official_name(self):
  self.assertFalse(r.validate_edit(edit('9999','3081','stock_code'),BASE,OFF,lazy_pinyin))
  self.assertTrue(r.validate_edit(edit('9999','2330','stock_code'),BASE,OFF,lazy_pinyin))
 def test_two_valid_codes_need_cross_evidence(self):
  text=BASE.replace('9999','3086');e={'quote':text,'before':'3086','after':'3081','kind':'stock_code'}
  self.assertTrue(r.validate_edit(e,text,OFF,lazy_pinyin))
  text+='另外明確提到3081聯亞。';e['evidence_quote']='另外明確提到3081聯亞。'
  self.assertFalse(r.validate_edit(e,text,OFF,lazy_pinyin))
 def test_no_price_negation_or_company_guess(self):
  for e in [edit('不要','需要'),edit('9999','2000'),edit('加折','台積電','stock_name','2330'),edit('宏達電','嘉澤','stock_name','3533')]:
   self.assertTrue(r.validate_edit(e,BASE,OFF,lazy_pinyin))
 def test_unique_quote_and_overlap(self):
  self.assertTrue(r.validate_edit(edit('景線','頸線'),BASE*2,OFF,lazy_pinyin))
  with self.assertRaises(ValueError):r.apply_edits(BASE,[edit('景線','頸線')]*2)
 def test_manifest_binds_raw_and_reconstructs_draft(self):
  edits=[edit('景線','頸線'),edit('加折','嘉澤','stock_name','3533')]
  draft=r.apply_edits(BASE,edits);proof={'version':r.VERSION,'rawFingerprint':fp(BASE),'base':BASE,'edits':edits}
  row={'原始逐字稿內容':BASE,'修飾後逐字稿內容':draft,r.COL:json.dumps(proof)}
  self.assertTrue(r.verified_reading(row,fp))
  for changed in [{'原始逐字稿內容':BASE+'新原文'},{'修飾後逐字稿內容':draft+'自行增字'},{r.COL:''}]:self.assertFalse(r.verified_reading(row|changed,fp))
 def test_all_edits_share_one_model_call(self):
  api=SimpleNamespace(get_code_map=lambda:OFF,lazy_pinyin=lazy_pinyin,transcript_fingerprint=fp,note_decision=Mock(),MAX_OUT=8000,
   call_gemini=Mock(return_value='x'),safe_load_json=lambda *a,**k:{'edits':[edit('景線','頸線'),edit('加折','嘉澤','stock_name','3533'),edit('不要','需要')],'uncertain':[]})
  text,proof=r.correct(api,BASE,BASE);api.call_gemini.assert_called_once()
  self.assertEqual(len(proof['edits']),2);self.assertIn('不要',text);self.assertEqual(BASE.count('加折'),1)
if __name__=='__main__':unittest.main()
