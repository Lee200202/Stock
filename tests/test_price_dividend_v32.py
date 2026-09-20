"""除息金額不是股價（2026/09/16）：台積電現價 2380，價位說明卻寫 7。"""
import unittest

from test_quality import p


class DividendPriceTests(unittest.TestCase):
    def test_dividend_is_not_a_price(self):
        note = '今天除息7元沒有秒填息不用緊張，過去幾乎百分之百一定會填息，後面一定會填回來。'
        self.assertEqual(p.display_price('7', '', note), '未說明')

    def test_other_payout_words_too(self):
        for note in ('今天除權3元。', '配息5元入帳。', '股利發放12元。'):
            num = ''.join(ch for ch in note if ch.isdigit())
            self.assertEqual(p.display_price(num, '', note), '未說明', note)

    def test_real_prices_still_show(self):
        self.assertEqual(p.display_price('900', '', '等對方賣完跌破900後再來買進。'), '900')
        self.assertEqual(p.display_price('775', '', '9月7號最高786，我賣775。'), '775')
        self.assertEqual(p.display_price('7', '', '成本7元附近買進。'), '7')

    def test_prompt_states_the_rule(self):
        self.assertIn('除息、除權、配息、股利的每股金額同樣不是股價', p.POLICY)


if __name__ == '__main__':
    unittest.main()
