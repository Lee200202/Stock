"""2026/11/06 週檢討：盤勢①、教學③與標題不得出現只在當日會員通知才有的用語（裁示紀錄 三，10/06、11/02）。

本週 11/02（①③）、11/06（①③與標題）的公開重點用了只在私人會員通知出現的用語，原稿與修飾稿都沒有。
主判讀看得到通知原文（只供核對身分與部位），盤勢與教學的唯一證據卻是原稿；規則：三個以上連續漢字、
出現在當日通知、原稿沒有的用語 → 這一點不採用，交由只看原稿的章節補問重寫；標題候選同樣換下一個。

通知原文不進公開 repo：這裡的「通知」是為測試編的句子，原稿句子取自本週原稿（11/06）並前後加無關的填充句。
反例：兩字的書面寫法（今日、因此、出現、買進，11/04、11/06 複核判為一般用語）、時間詞片段（10/28 回測「今日大漲」）、
個股官方名與聽寫不同（通知寫官方名、原稿是聽寫）、原稿自己講過的詞。
"""
import contextlib
import io
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as p  # noqa: E402

FILLER_A = '好，我們先看一下今天的天氣，台北有一點下雨，大家出門記得帶傘。'
FILLER_B = '來，喝口水，我們等一下再講下一段，今天的節目比較長一點。'
# 11/06 原稿原句（講者口語）
SOURCE = (FILLER_A + '你看，好股爛股一起跌。所以你今天賣股票就完全是錯的。完全是錯的。'
          '賣點都是這一種彈上去才可以賣。季線負乖離過大會漲回去。我有沒有告訴你我有雅德客？'
          '台積電 2,280 以下，都是安全區；這一個缺口一定補。' + FILLER_B)
# 為測試編的通知句（不是任何一天的真實通知）
NOTICE = '今日盤勢雞飛狗跳式殺盤，因此出現短線逃難潮，持有亞德客者續抱，可分批買進，今日大漲勿追。'
DAY = '2026/11/06'


class NoticeOnlyTermsTest(unittest.TestCase):
    def test_notice_term_absent_from_source_is_found(self):
        text = '盤勢：好股爛股一起跌，盤面雞飛狗跳，季線負乖離過大，之後會漲回去。'
        self.assertEqual(p.notice_only_terms(text, SOURCE, [NOTICE]), ['雞飛狗跳'])

    def test_longer_run_is_merged(self):
        text = '教學：賣點要等彈上去，不要在短線逃難潮裡跟著賣。'
        self.assertEqual(p.notice_only_terms(text, SOURCE, [NOTICE]), ['短線逃難潮'])

    def test_two_char_written_words_are_not_leaks(self):
        # 11/04「因此」、11/06「今日」「出現」「買進」：書面與口語寫法不同，不是通知專屬用語
        text = '盤勢：今日好股爛股一起跌，因此賣壓沉重，後來出現反彈；季線負乖離過大，跌深可以買進。'
        self.assertEqual(p.notice_only_terms(text, SOURCE, [NOTICE]), [])

    def test_time_word_fragment_is_not_a_leak(self):
        # 10/28 回測：③「今日大漲不是讓投資人買股票用的」只是「今日」接下一個詞
        text = '教學：今日大漲不是讓投資人買股票用的，賣點都是彈上去才可以賣。'
        self.assertEqual(p.notice_only_terms(text, SOURCE, [NOTICE]), [])

    def test_stock_official_name_vs_heard_form_is_not_a_leak(self):
        # 原稿念「雅德客」、通知寫官方名「亞德客」：名稱不算外洩
        text = '教學：融資少、沒有包袱的股票反彈最兇，例如亞德客。'
        self.assertEqual(p.notice_only_terms(text, SOURCE, [NOTICE], names={'亞德客-KY', '亞德客', '雅德客'}), [])

    def test_words_also_spoken_in_source_are_not_leaks(self):
        text = '盤勢：台積電 2,280 以下都是安全區，這一個缺口一定補。'
        self.assertEqual(p.notice_only_terms(text, SOURCE + '今天又雞飛狗跳式的下殺。', [NOTICE]), [])

    def test_no_notice_no_terms(self):
        self.assertEqual(p.notice_only_terms('盤面雞飛狗跳', SOURCE, []), [])


class ValidateEvidenceDropsLeakedPointsTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(p._DAY_SOURCE_CONTEXT)
        p._DAY_SOURCE_CONTEXT.clear()
        p._DAY_SOURCE_CONTEXT.update(date=DAY, notices=[{'id': 'T1', 'time': DAY, 'original': NOTICE}])

    def tearDown(self):
        p._DAY_SOURCE_CONTEXT.clear()
        p._DAY_SOURCE_CONTEXT.update(self.saved)

    def run_validate(self, market, date=DAY):
        signals = {'market': market,
                   'watch_watch': [{'name': '台積電', 'code': '2330', 'reason': '台積電 2,280 以下都是安全區，缺口一定補',
                                    'evidence': ['台積電 2,280 以下，都是安全區']}]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = p.validate_evidence(signals, SOURCE, date, after_codes=True)
        return out, buf.getvalue()

    def test_leaked_point_dropped_clean_point_kept_and_log_has_no_term(self):
        leaked = {'kind': 'view', 'text': '觀念：好股爛股一起跌、盤面雞飛狗跳時，不要殺低。', 'evidence': ['好股爛股一起跌'],
                  'headline': '雞飛狗跳 今天賣股票一定後悔'}
        clean = {'kind': 'view', 'text': '觀念：好股爛股一起跌時賣股票是錯的，賣點要等彈上去。', 'evidence': ['好股爛股一起跌']}
        out, log = self.run_validate([leaked, clean])
        self.assertEqual([m['text'] for m in out['market']], [clean['text']])
        self.assertNotIn('雞飛狗跳', log)                      # 公開日誌不印通知用語
        self.assertFalse(any(m.get('headline') for m in out['market']))   # 被剔除那點的標題不搬到別點
        self.assertTrue(any('通知' in g and '雞飛狗跳' not in g for g in out.get('_repair_gaps', [])))

    def test_other_day_notice_is_not_used(self):
        point = {'kind': 'view', 'text': '觀念：好股爛股一起跌、盤面雞飛狗跳時，不要殺低。', 'evidence': ['好股爛股一起跌']}
        out, _ = self.run_validate([point], date='2026/11/05')
        self.assertEqual(len(out['market']), 1)


if __name__ == '__main__':
    unittest.main()
