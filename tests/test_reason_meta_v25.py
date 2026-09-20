"""說明不寫分類理由、控制字數（2026/09/16 管理者回報的台積電案例）。"""
import re
import unittest
from pathlib import Path

from test_quality import p, empty

ROOT = Path(__file__).resolve().parents[1]
TSMC = ('詳細以台積電作為技術面與外部因素干擾的最佳教學教材，說明其季線下方的買點與外部升息事件的影響，'
        '但在當日節目中並未將台積電列為當日會員實際買進或持有的個股明細，故列入市場教學與觀察範疇。'
        '台積電季線下方本為正常買點，但因外部升息等變數干擾可能導致短線急跌後再回升，提醒不宜在變數未定前盲目躁進。')


class MetaReasonTests(unittest.TestCase):
    def test_classification_rationale_is_dropped(self):
        out = p.clean_meta_reason(TSMC)
        self.assertEqual(out, '詳細以台積電作為技術面與外部因素干擾的最佳教學教材，說明其季線下方的買點與外部升息事件的影響。'
                              '台積電季線下方本為正常買點，但因外部升息等變數干擾可能導致短線急跌後再回升，提醒不宜在變數未定前盲目躁進。')
        for gone in ('並未將', '個股明細', '故列入', '觀察範疇'):
            self.assertNotIn(gone, out)

    def test_dropped_sentence_keeps_the_full_stop(self):
        # 丟掉的子句原本結束了一句，句號要讓給前一句，不能把兩句黏成一句。
        self.assertEqual(p.clean_meta_reason('季線下方本為買點，故列入觀察範疇。外部變數未定前不宜躁進。'),
                         '季線下方本為買點。外部變數未定前不宜躁進。')

    def test_real_descriptions_are_untouched(self):
        for text in ('會員先前在1170賣出，若後續再度跌破1000或來到更低位置時，可留意其止跌回穩與尋求切入的機會。',
                     '提到該公司為好公司，因00981A ETF目前還有幾百張尚未把低檔殺完，若手中有持股的投資人900以下不要急著賣出。',
                     '若沒有持股想買的人，可等待900以下或跌破900時再行注意買點。',
                     '在前幾天9月9日的節目中就曾明確提醒，友達已經連續漲了三天，因此提醒不能再盲目追買。'):
            self.assertEqual(p.clean_meta_reason(text), text)

    def test_writes_go_through_the_filter(self):
        sig = empty()
        sig['watch_watch'] = [{'name': '台積電', 'code': '2330', 'reason': TSMC}]
        sig['holdings'] = [{'name': '鴻準', 'code': '2354', 'note': '續抱，故列入觀察範疇。'}]
        p.naturalize_signal_reasons(sig)
        self.assertNotIn('個股明細', sig['watch_watch'][0]['reason'])
        self.assertNotIn('觀察範疇', sig['holdings'][0]['note'])

    def test_prompt_asks_for_short_point_first_descriptions(self):
        self.assertIn('約 40～120 字', p.POLICY)
        self.assertNotIn('約 70～160 字', p.POLICY)
        self.assertIn('「為什麼把這一檔歸到這一類」同樣是內部流程，不寫進說明', p.POLICY)
        self.assertIn('故列入市場教學與觀察範疇', p.POLICY)   # 反例照抄在提示詞裡
        gas = (ROOT / 'apps-script/Adminpipeline.gs').read_text(encoding='utf-8')
        self.assertIn('約 40～120 字', re.sub(r'\n', '\n', gas))


if __name__ == '__main__':
    unittest.main()
