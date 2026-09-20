# -*- coding: utf-8 -*-
"""逐字稿排版 v44（2026/09/17）：9/17 手動投稿之後逐字稿分頁只剩一整段「這一段」，沒有任何東西再去排。

改法：
  一、模型只回「第幾句開始是新的一段」與小標，段落由程式用原句組回去，一個字都不會被改。
  二、存進影片清單時一起記「排版稿方式」；只拿到機械分段的，網站之後會再排。
  三、排的是網站實際顯示的那一份原文，指紋與 Apps Script 同一套算法。
  四、中文字之間語音轉文字留下的空白拿掉（原始稿每個字之間都有空格）。
"""
import json
import unittest
from unittest.mock import patch

from test_quality import p, ROOT

# 9/17 原始稿的開頭（每個字之間都有空白，沒有標點）與一段有標點的潤飾稿
RAW_0917 = ('各 位 全 國 投 資 朋 友 大 家 早 不 好 意 思 哦 剛 才 我 又 忘 了 按 一 個 東 西 然 後 自 己 講 話 講 了 五 分 鐘 '
            '然 後 馬 上 重 開 重 不 好 意 思 其 實 說 真 的 今 天 是 我 談 中 直 播 兩 週 年 兩 週 年 昨 天 跟 你 們 講 兩 年 前 的 9 月 17 號 ') * 4
POLISHED = ('各位全國投資朋友大家早，不好意思哦，剛才我又忘了按一個東西。其實說真的，今天是我盤中直播兩週年。'
            '我懷著很坦蕩的心，想說我會不 會開直播？沒有人要看。結果直播的第一天就1萬人。'
            '來，第二個族群我推薦給你們的是高速傳輸，享碩跟普瑞。外資昨天買500多張，EPS上半年72塊。'
            '你們想賣的趕快賣，我的會員不准賣。享碩的目標價我是定在2000塊。') * 6


def fake_breaks(system, prompt, **kw):
    lines = prompt.split('\n')
    first = int(lines[0][1:lines[0].index(']')])
    last = int(lines[-1][1:lines[-1].index(']')])
    mid = (first + last) // 2
    return json.dumps({'sections': [{'start': first + 1, 'title': '開場與兩週年'},
                                    {'start': mid, 'title': '祥碩與普瑞目標價'},
                                    {'start': last + 50, 'title': '超出範圍'}]}, ensure_ascii=False)


class SentenceTests(unittest.TestCase):
    def test_every_character_is_kept(self):
        for text in (RAW_0917, POLISHED):
            sents = p._tx_sentences(text)
            self.assertTrue(p._tx_same_text(''.join(sents), text))
            self.assertLessEqual(max(len(s) for s in sents), 100, '沒有標點的原文也要切得開')

    def test_cjk_spaces_removed_but_latin_spaces_kept(self):
        self.assertEqual(p.tidy_transcript_text('我會不 會開直播，又 要看大盤'), '我會不會開直播，又要看大盤')
        self.assertEqual(p.tidy_transcript_text('兩年前的 9 月 17 號'), '兩年前的9月17號')
        self.assertEqual(p.tidy_transcript_text('CPU GPU 都漲完'), 'CPU GPU都漲完')

    def test_fingerprint_matches_apps_script(self):
        # 同一個字串在 Node 用 crypto 算出來也是這個值（見 test_tx_layout_v44_gas.js）
        self.assertEqual(p.transcript_fingerprint('各 位 投 資 朋 友\n大家早。\ufeff今天 CPU 5000億'), 'f1326e594db60343')


class LayoutTests(unittest.TestCase):
    def layout(self, text, **kw):
        with patch.object(p, 'budget_left', return_value=900), patch.dict(p._QUOTA_STOP, {'daily': False}):
            return p.transcript_layout(text)

    def test_model_only_returns_breaks_and_text_is_rebuilt_from_source(self):
        with patch.object(p, 'call_gemini', side_effect=fake_breaks) as ai:
            lay = self.layout(POLISHED * 6)
        self.assertEqual(lay['method'], 'ai')
        self.assertGreaterEqual(ai.call_count, 2, '四千字一批')
        titles = [s['title'] for s in lay['sections']]
        self.assertIn('開場與兩週年', titles)
        self.assertIn('祥碩與普瑞目標價', titles)
        self.assertNotIn('超出範圍', titles)
        joined = ''.join(''.join(s['paras']) for s in lay['sections'])
        self.assertTrue(p._tx_same_text(joined, POLISHED * 6), '段落是原句組回去的，一個字都不少')
        self.assertNotIn('會不 會', joined)

    def test_reply_that_echoes_text_without_positions_falls_back_with_titles(self):
        reply = json.dumps({'sections': [{'title': '摘要', 'text': '今天盤勢震盪，建議觀望。'}]}, ensure_ascii=False)
        with patch.object(p, 'call_gemini', return_value=reply):
            lay = self.layout(POLISHED)
        self.assertEqual(lay['method'], 'rule')
        self.assertTrue(all(s['title'] for s in lay['sections']), '機械分段也要有小標，不再是「這一段」')
        self.assertTrue(p._tx_same_text(''.join(''.join(s['paras']) for s in lay['sections']), POLISHED))

    def test_quota_failure_keeps_all_text_and_marks_rule(self):
        with patch.object(p, 'call_gemini', side_effect=p.RateLimited('配額用盡')):
            lay = self.layout(RAW_0917 * 3)
        self.assertEqual(lay['method'], 'rule')
        self.assertTrue(p._tx_same_text(''.join(''.join(s['paras']) for s in lay['sections']), RAW_0917 * 3))

    def test_meaningless_titles_are_replaced(self):
        self.assertEqual(p._tx_clean_title('這一段'), '')
        self.assertEqual(p._tx_clean_title('「祥碩目標價」'), '祥碩目標價')
        breaks = p._tx_breaks(json.dumps({'sections': [{'start': 7, 'title': '內容一'}, {'start': 3, 'title': 'x'}]}), 1, 10)
        self.assertEqual(breaks[0][0], 1, '第一段一律從本批第一句開始')
        self.assertEqual([k for k, _ in breaks], [1, 7])


class FakeSheet:
    def __init__(self, values):
        self.values = [list(r) for r in values]
        self.col_count = len(values[0])
        self.writes = []

    def get_all_values(self):
        return [list(r) for r in self.values]

    def add_cols(self, n):
        self.col_count += n

    def update_cell(self, r, c, v):
        while len(self.values[r - 1]) < c:
            self.values[r - 1].append('')
        self.values[r - 1][c - 1] = v

    def batch_update(self, data, value_input_option=None):
        import gspread
        for item in data:
            r, c = gspread.utils.a1_to_rowcol(item['range'])
            self.update_cell(r, c, item['values'][0][0])
            self.writes.append((r, c))


class FakeSS:
    def __init__(self, sheet):
        self.sheet = sheet

    def worksheet(self, name):
        assert name == '影片清單'
        return self.sheet


HEAD = ['影片ID', '發布日期', '標題', '處理狀態', '失敗原因', '原始逐字稿內容', '修飾後逐字稿內容', '原文更新時間', '原文SHA256', '來源別名']


class EnsureLayoutTests(unittest.TestCase):
    def run_ensure(self, sheet, reply=fake_breaks, force=False):
        with patch.object(p, 'call_gemini', side_effect=reply) as ai, \
             patch.object(p, 'budget_left', return_value=900), patch.dict(p._QUOTA_STOP, {'daily': False}), \
             patch.object(p, 'sheets_retry', side_effect=lambda fn, *a, **k: fn(*a, **k)):
            ok = p.ensure_transcript_layout(FakeSS(sheet), '2026/09/17', force=force)
        return ok, ai

    def test_writes_json_fingerprint_and_method_to_the_row_the_site_shows(self):
        sheet = FakeSheet([HEAD,
                           ['vid-0916', '2026/09/16', '舊的', '完成', '', POLISHED, POLISHED, '', '', ''],
                           ['MANUAL-20260917', '2026/09/17', '手動投稿 2026/09/17', '完成', '', RAW_0917 * 3, POLISHED * 3,
                            '2026/09/17 12:11:00', '', '']])
        ok, ai = self.run_ensure(sheet)
        self.assertTrue(ok)
        head = sheet.values[0]
        for name in ('排版稿JSON', '排版稿指紋', '排版稿方式'):
            self.assertIn(name, head)
        row = sheet.values[2]
        sections = json.loads(row[head.index('排版稿JSON')])
        self.assertTrue(sections and all(s['title'] for s in sections))
        shown = p.display_transcript_text(dict(zip(HEAD, sheet.values[2])))
        self.assertEqual(row[head.index('排版稿指紋')], p.transcript_fingerprint(shown))
        self.assertEqual(row[head.index('排版稿方式')], 'ai')
        self.assertEqual(sheet.values[1][len(HEAD):], [], '別天的列不動')

        # 第二次：已經是模型排好的，不再呼叫模型
        ok2, ai2 = self.run_ensure(sheet)
        self.assertTrue(ok2)
        ai2.assert_not_called()

    def test_rule_layout_is_retried_and_changed_text_is_relaid(self):
        sheet = FakeSheet([HEAD + ['排版稿JSON', '排版稿指紋', '排版稿方式'],
                           ['MANUAL-20260917', '2026/09/17', '手動投稿', '完成', '', POLISHED, POLISHED, '', '', '',
                            json.dumps([{'title': '', 'paras': ['x']}]), p.transcript_fingerprint(POLISHED), 'rule']])
        ok, ai = self.run_ensure(sheet)
        self.assertTrue(ok)
        self.assertGreater(ai.call_count, 0, '上次只有機械分段，這次要再試模型')
        self.assertEqual(sheet.values[1][12], 'ai')
        sheet.values[1][6] = POLISHED + '後來補上的一段。' * 3
        sheet.values[1][5] = sheet.values[1][6]
        ok, ai = self.run_ensure(sheet)
        self.assertGreater(ai.call_count, 0, '原文改過，指紋對不上就重排')

    def test_hooks_in_the_pipeline(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn("    ensure_transcript_layout(ss, date_str)\n    commit_evidence_manifest", src)
        self.assertIn("從檢查點續跑不會經過擷取那一段，排版要在這裡補", src)
        gas = (ROOT / 'apps-script/SheetService.gs').read_text(encoding='utf-8')
        self.assertIn('var TX_FORMAT_SYSTEM = ' + json.dumps(p.TX_FORMAT_SYSTEM, ensure_ascii=False), gas,
                      '排版提示詞要執行 scripts/sync_quality.py 同步到 Apps Script')


if __name__ == '__main__':
    unittest.main()
