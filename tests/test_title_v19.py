# -*- coding: utf-8 -*-
"""v19 文章標題：不限字數、全部已驗證原句都可當依據、帶標題的那一筆被剔除時標題不消失、印出來源。"""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from test_quality import p, empty

EV = '這 個 禮 拜 很 熱 鬧 不 代 表 你 要 跟 著 人 家 熱 鬧 你 要 更 冷 靜 兩 個 大 人 在 打 架 你 不 要 參 進 去 你 就 坐 在 旁 邊 看'


def market(**kw):
    row = {'kind': 'event', 'text': '本週外部變數多，大盤震盪。', 'evidence': [EV], '_evidence_verified': True}
    row.update(kw)
    return row


class TitleV19Tests(unittest.TestCase):
    def test_long_headline_is_accepted(self):
        s = empty()
        head = '這個禮拜很熱鬧不代表你要跟著人家熱鬧，兩個大人在打架你不要參進去，坐在旁邊看就好！'
        s['market'] = [market(headline=head)]
        self.assertGreater(len(head), 26)
        self.assertEqual(p.article_title(s), head)

    def test_headline_can_be_supported_by_other_verified_rows(self):
        s = empty()
        s['market'] = [market(text='聯準會利率決策前震盪。', evidence=['聯 準 會'], headline='你要更冷靜，坐在旁邊看兩個大人打架！'),
                       market(kind='view', text='冷靜觀察：兩個大人在打架，坐在旁邊看。', evidence=[EV])]
        self.assertEqual(p.article_title_detail(s)[1], '模型標題')

    def test_fallback_uses_lesson_concept_before_themes(self):
        s = empty()
        s['market'] = [market(text='聯準會利率決策前大盤震盪。', evidence=['聯 準 會 利 率']),
                       market(kind='view', text='克服過度交易：真正賺錢的人一兩個禮拜才出手一次。', evidence=['過 度 交 易'])]
        title, src, _ = p.article_title_detail(s)
        # v44：觀念標題不到 15 字時接上說明的子句
        self.assertEqual(title, '克服過度交易，真正賺錢的人一兩個禮拜才出手一次！'); self.assertEqual(src, '教學重點的觀念標題')
        s['market'] = s['market'][:1]
        self.assertEqual(p.article_title(s), '利率決策與震盪盤勢，本集盤勢與操作重點整理')

    def test_invented_number_or_words_rejected_with_reason(self):
        s = empty(); s['market'] = [market(headline='外資目標價上看9999元！')]
        title, src, rejected = p.article_title_detail(s)
        self.assertNotIn('9999', title); self.assertEqual(rejected[0][1], '數字不在原句')

    def test_headline_survives_when_its_row_fails_verification(self):
        hay = '大盤震盪。兩個大人在打架。' * 20
        s = empty()
        s['market'] = [{'kind': 'event', 'text': '指數來到99999點', 'headline': '兩個大人在打架！', 'evidence_refs': []},
                       {'kind': 'event', 'text': '大盤震盪', 'evidence_refs': []}]
        with patch.object(p, 'market_item_verified', side_effect=lambda item, h: '99999' not in item['text']), \
             redirect_stdout(io.StringIO()):
            p.validate_evidence(s, hay, '2026/09/14', after_codes=True)
        self.assertEqual([r.get('headline') for r in s['market']], ['兩個大人在打架！'])

    def test_prompts_have_no_upper_limit_but_at_least_15_chars(self):
        self.assertNotIn('8～26字', p.POLICY)
        self.assertIn('至少15字（不設上限）', p.POLICY)
        self.assertIn('至少15字、不設上限', p.ARTICLE_SYSTEM)


if __name__ == '__main__':
    unittest.main()
