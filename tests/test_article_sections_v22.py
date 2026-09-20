"""v22 文章章節（2026/09/15）：標題不編號、拿掉基本資訊、盤勢總覽起算 ①，Python 與 GAS 同一結構。
v44（2026/09/17）起沒有 ④ 風險揭露：信尾固定呈現一次，文章裡再寫就重複了。"""
import json
import re
import unittest
from pathlib import Path

from test_quality import p, empty, trade

ROOT = Path(__file__).resolve().parents[1]


def heads(article):
    out = []
    for line in article.splitlines():
        if line.startswith('文章標題：'):
            out.append('文章標題：')
        elif re.match(r'^[①②③④⑤⑥]', line):
            out.append(line.split(' ')[0])
    return out


class ArticleSectionsV22Tests(unittest.TestCase):
    def test_structure_title_then_three_sections(self):
        sig = empty()
        sig['market'] = [{'kind': 'level', 'text': '量縮震盪，市場靜待CPI公布再決定方向。', 'headline': '量縮震盪，市場靜待CPI公布再決定方向', '_evidence_verified': True},
                         {'kind': 'view', 'text': '等拉回再買：追高容易套牢。', '_evidence_verified': True}]
        sig['holdings'] = [{'name': '鴻準', 'code': '2354', 'note': '續抱。'}]
        out = p.canonical_article(sig, '2026/09/15', '① 文章標題：舊\n② 基本資訊\n• 節目名稱：x\n⑥ 舊')
        self.assertEqual(heads(out), ['文章標題：', '①', '②', '②-1', '②-2', '②-3', '③'])
        self.assertEqual(out.splitlines()[0], '文章標題：量縮震盪，市場靜待CPI公布再決定方向！')
        for gone in ('基本資訊', '節目名稱', '播出平台', '主要講者', '④', '⑤', '⑥', '風險揭露'):
            self.assertNotIn(gone, out)
        self.assertIn('① 盤勢總覽重點整理\n\n• 量縮震盪', out)
        self.assertIn('③ 分析師操作邏輯與教學重點\n\n• 等拉回再買', out)

    def test_past_trades_use_new_subsection_number(self):
        sig = empty(); r = trade(); r['_date'] = '2026/09/12'; sig['sell'] = [r]
        out = p.enforce_article_records('', sig, '2026/09/15')
        self.assertEqual(heads(out), ['文章標題：', '①', '②', '②-1', '②-1補', '②-2', '②-3', '③'])

    def test_prompts_use_new_numbering(self):
        prompt = p.ARTICLE_SYSTEM
        for text in ('① 盤勢總覽重點整理', '② 會員操作紀錄與持股明細', '②-1 當日明確說明之買入／賣出紀錄', '②-1補',
                     '②-2 影片中明講', '②-3 觀望個股', '③ 分析師操作邏輯與教學重點'):
            self.assertIn(text, prompt)
        self.assertNotIn('④ 風險揭露', prompt, 'v44：風險揭露只在信尾出現一次')
        self.assertNotIn('② 基本資訊', prompt)
        self.assertIsNone(re.search(r'[⑤⑥]|④-|③ 盤勢', prompt))
        self.assertIn('信件第①章', p.POLICY); self.assertIn('信件第③章', p.POLICY)
        self.assertIn('信件第③章', p.LESSON_TOPUP_SYSTEM)
        self.assertIn('第①章盤勢與第③章教學', p.SUMMARY_TOPUP_SYSTEM)
        gas = (ROOT / 'apps-script/Adminpipeline.gs').read_text(encoding='utf-8')
        mirrored = json.loads(re.search(r'var PIPE_ARTICLE_SYSTEM = (".*?");\n', gas).group(1))
        self.assertEqual(mirrored, p.ARTICLE_SYSTEM)

    def test_python_and_gas_share_section_titles(self):
        gas = (ROOT / 'apps-script/Articlequality.gs').read_text(encoding='utf-8') + \
              (ROOT / 'apps-script/Evidencequality.gs').read_text(encoding='utf-8')
        out = p.canonical_article(empty(), '2026/09/15')
        for line in out.splitlines():
            if re.match(r'^[①②③④](?:-\d)?\s', line):
                self.assertIn(line, gas)


if __name__ == '__main__':
    unittest.main()
