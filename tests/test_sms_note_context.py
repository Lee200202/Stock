# -*- coding: utf-8 -*-
"""
會員簡訊「說明重點」的逐字稿補充。

背景：簡訊本文常常只有一句「晶心科連續大漲，務必抱牢」，
寫進網站與郵件之後，讀的人看不出為什麼。同一天的直播逐字稿裡通常有理由
（族群、法人動向、技術位置），把那幾句撈出來當依據，說明重點才有內容。

這裡要釘住的是邊界：**只撈簡訊真的點名過、而且逐字稿真的講過的那幾檔**。
撈到別檔會讓模型有機會替沒被點名的股票開筆，那是這個專案最不能出的錯。
"""

import importlib.util
import os
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent

os.environ.setdefault("SPREADSHEET_ID", "test")
os.environ.setdefault("GEMINI_API_KEY", "AIzaSyDUMMY_local_import_only_0000000000")
_spec = importlib.util.spec_from_file_location("pl", ROOT / "pipeline" / "pipeline.py")
pl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pl)


TX = (
    "大盤突破46224點，代表多方重掌盤面。"
    "會員的晶心科連續大漲，法人持續買超，這一檔的量能結構沒有走壞，務必抱牢。"
    "事欣科也連續大漲，量能穩定放大，同樣是會員手上的部位。"
    "祥碩將發動，這一檔外資回補中，技術面已經整理夠久了。"
    "譜瑞也是一樣的狀況，將發動，務必抱牢。"
    "聯陽已開始突破，持股續抱即可。"
    "台積電今天量縮，那是別人家的股票，跟我們手上的部位無關，不要去追。"
) * 3

SMS = ("張震6GJ-1:會員晶心科、事欣科連續大漲，祥碩、譜瑞也將發動，務必抱牢！"
       "聯陽已開始突破，持股續抱！")

CODE_MAP = {"6533": "晶心科", "4916": "事欣科", "5269": "祥碩",
            "4966": "譜瑞-KY", "3014": "聯陽", "2330": "台積電", "3661": "世芯"}


class ExcerptScope(unittest.TestCase):
    def setUp(self):
        pl._SMS_TX_CACHE.clear()
        pl._SMS_TX_CACHE["2026/09/18"] = TX

    def excerpt(self, sms=SMS, date="2026/09/18"):
        return pl._sms_transcript_excerpt(None, date, sms, CODE_MAP)

    def test_picks_named_stocks(self):
        out = self.excerpt()
        for name in ("晶心科", "事欣科", "祥碩", "聯陽"):
            self.assertIn(name, out, name)

    def test_ky_suffix_is_matched(self):
        """對照表寫「譜瑞-KY」，簡訊與講稿都只說「譜瑞」。

        不脫掉後綴就整檔漏撈，而 KY 股在台股很常見。
        """
        self.assertIn("譜瑞", self.excerpt())

    def test_never_picks_unnamed_stocks(self):
        """簡訊沒點名的個股，逐字稿講再多也不能進摘錄。

        撈進來就等於給模型一個替沒被點名的股票開筆的機會——
        「台積電今天量縮」是行情評論，不是叫會員動作。
        """
        out = self.excerpt()
        self.assertNotIn("台積電", out)
        self.assertNotIn("世芯", out)

    def test_no_transcript_returns_empty(self):
        """當天沒有逐字稿就回空字串，說明重點維持原本的簡短寫法。"""
        pl._SMS_TX_CACHE["2026/09/19"] = ""
        self.assertEqual(self.excerpt(date="2026/09/19"), "")

    def test_too_short_transcript_ignored(self):
        """殘缺的短稿不能當依據。"""
        pl._SMS_TX_CACHE["2026/09/19"] = "今天大盤還好。"
        self.assertEqual(self.excerpt(date="2026/09/19"), "")

    def test_blank_date_returns_empty(self):
        self.assertEqual(pl._sms_transcript_excerpt(None, "", SMS, CODE_MAP), "")

    def test_sms_with_no_matching_stock(self):
        """簡訊只講大盤、沒點名個股時，不該撈出任何句子。"""
        self.assertEqual(self.excerpt(sms="今天大盤都沒量，什麼動作都不要做。"), "")

    def test_bounded_length(self):
        """一輪要跑幾十篇簡訊，摘錄必須有上限，否則 token 會爆。"""
        out = pl._sms_transcript_excerpt(None, "2026/09/18", SMS, CODE_MAP, limit=60)
        self.assertLessEqual(len(out), 60)

    def test_cached_per_date(self):
        """同一天只讀一次試算表。讀第二次代表快取沒生效，一輪會多打幾十次 API。"""
        calls = []

        def fake(ss, vid, date_str):
            calls.append(date_str)
            return TX, TX
        real = pl.existing_transcript
        pl.existing_transcript = fake
        pl._SMS_TX_CACHE.clear()
        try:
            self.excerpt()
            self.excerpt()
            self.excerpt()
        finally:
            pl.existing_transcript = real
        self.assertEqual(len(calls), 1, f"讀了 {len(calls)} 次，應該只有 1 次")


class PromptRules(unittest.TestCase):
    def test_prompt_allows_transcript_but_forbids_invention(self):
        """提示語要同時講清楚兩件事：可以用摘錄補充，但不能自己編。

        這個專案的底線是「只呈現影片中明確講過的內容」。放寬 note 的同時
        必須把邊界寫死，否則模型會開始補技術指標與價位。
        """
        src = (ROOT / "pipeline" / "pipeline.py").read_text(encoding="utf-8")
        i = src.index("【note 說明重點】")
        block = src[i:i + 1200]
        self.assertIn("當日逐字稿摘錄", block)
        self.assertIn("只能用簡訊或逐字稿摘錄裡真的講過的內容", block)
        self.assertIn("絕對不可以自己補", block)

    def test_excerpt_is_labelled_in_user_message(self):
        """摘錄要標清楚來源，否則模型會把它當成簡訊本文而誤開筆。"""
        src = (ROOT / "pipeline" / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("不得據此新增或刪除任何一檔", src)

class EnrichmentV46(unittest.TestCase):
    def test_raw_transcript_wins_over_polished(self):
        from unittest.mock import patch
        pl._SMS_TX_CACHE.clear()
        with patch.object(pl,'existing_transcript',return_value=(TX, '台積電完全不同的修飾稿。'*40)):
            out=pl._sms_transcript_excerpt(None,'2026/09/18',SMS,CODE_MAP)
        self.assertIn('晶心科',out)
        self.assertNotIn('不同的修飾稿',out)

    def test_confirmed_alias_and_following_reason_are_kept(self):
        pl._SMS_TX_CACHE['2026/09/18']='金 星 科今天量縮。這一檔的法人持續買超，量能結構沒有走壞。台積電今天跌很多。'+'大盤震盪。'*60
        out=pl._sms_transcript_excerpt(None,'2026/09/18','晶心科抱牢',CODE_MAP)
        self.assertIn('法人持續買超',out)
        self.assertNotIn('台積電',out)

    def test_late_context_preserves_order_and_is_idempotent(self):
        raw='晶心科法人持續買超，量能結構沒有走壞。'
        candidate={'name':'晶心科','code':'6533','_evidence_verified':True,'evidence':[raw],
                   'note':'法人持續買超，量能結構沒有走壞。會員應續抱。'}
        note=pl.sms_context_note('271元以上獲利賣出。',candidate,raw)
        self.assertTrue(note.startswith('271元以上獲利賣出。'))
        self.assertIn('量能結構',note)
        self.assertNotIn('應續抱',note)
        self.assertEqual(pl.sms_context_note(note,candidate,raw),note)

    def test_late_context_rejects_unverified_or_invented_numbers(self):
        raw='晶心科法人持續買超。'
        candidate={'name':'晶心科','code':'6533','evidence':[raw],'reason':'營收成長99%。'}
        self.assertEqual(pl.sms_context_note('抱牢。',candidate,raw),'抱牢。')
        candidate['_evidence_verified']=True
        self.assertEqual(pl.sms_context_note('抱牢。',candidate,raw),'抱牢。')
        candidate['evidence']=['原文沒有的引句']
        self.assertEqual(pl.sms_context_note('抱牢。',candidate,raw),'抱牢。')

    def test_saved_notes_no_longer_truncated(self):
        import json
        note='法人持續買超且量能維持穩定，股價在整理後逐步改善。營收與產品需求是本次說明的觀察重點，後续還需配合產業變化核對。盤面震盪時應關注量價與籌碼是否出現新的變化，保留原先操作條件並避免將不同股票的價位混用。'
        ok,items=pl.load_saved_sms_items(json.dumps([{'name':'晶心科','code':'6533','action':'會員持股','note':note}]))
        self.assertTrue(ok)
        self.assertGreater(len(items[0]['note']),80)

    def test_prompt_has_one_consistent_length_target(self):
        self.assertNotIn('30字內',pl.CM_PARSE_SYSTEM)
        self.assertIn('70 到 160',pl.CM_PARSE_SYSTEM)

if __name__ == "__main__":
    unittest.main(verbosity=2)
