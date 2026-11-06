"""觀望注意 vs 觀望不碰的邊界（管理者 2026/11/03 問卷，docs/daily-review/觀望注意邊界問卷.md）。

每一題用原稿原句，答案欄位是提示詞要求模型填的語意（本股看法、等待理由、持有者建議）；
這裡驗證「語意 → 分類」的對應與問卷勾選一致。模型會不會照這樣填，由真實模型重播（scripts/golden/2026-11-03.json）驗收。
"""
import os, sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
os.environ.setdefault('SPREADSHEET_ID', 'offline'); os.environ.setdefault('GEMINI_API_KEY', 'offline')
import pipeline as p  # noqa: E402


def case(name, raw, **fields):
    a = dict(about_itself=True, independent_analysis=True, content_role='independent_analysis',
             buy_today=False, sell_today=False, holding_now=False, past_trade=False,
             entry_for_new='none', now='none', tone='neutral', holder_advice='none', selection_intent='neutral',
             quotes={'stance': [raw], 'analysis': [raw]})
    a.update(fields)
    return p.verdict_class(a, p._ev_norm(raw), [name])[0]


CASES = [
    # (題號, 股名, 原句, 模型應填的語意, 管理者勾選)
    ('A1', '台積電', '台積電，如果，你們運氣好，有2280，你們就去買吧，我不會擋你們',
     dict(entry_for_new='wait_condition', tone='bullish', selection_intent='positive_candidate'), 'watch_watch'),
    ('A2', '世芯-KY', '不是世芯-KY不好，我不買，我也想買啊，世芯-KY跌破3,300，你知道嗎？我都流口水了',
     dict(entry_for_new='not_yet', tone='bullish', selection_intent='positive_candidate', past_trade=True), 'watch_watch'),
    ('A3', '聯電', '聯電也背離了，你有沒有看到？這邊創新低，這邊再往走，聯電也背離了',
     dict(entry_for_new='wait_condition', tone='bullish', selection_intent='positive_candidate'), 'watch_watch'),
    ('B1', '宜鼎', '我未來等大環境回穩，我會買的記憶體就是這支股票',
     dict(entry_for_new='wait_condition', tone='bullish', selection_intent='positive_candidate'), 'watch_watch'),
    ('B2', '群聯', '我現在剔除群聯，我不碰群聯，群聯不管我的事了，不想看了',
     dict(entry_for_new='prohibit', tone='bearish', selection_intent='explicit_exclusion'), 'watch_avoid'),
    ('C1', '群聯', '我說群聯還沒有回穩之前，你們都不要碰',
     dict(entry_for_new='wait_condition', tone='neutral', selection_intent='positive_candidate'), 'watch_watch'),
    ('C2', '台達電', '跌停可以買了嗎？還沒有，還沒有，一定要用60分K線看',
     dict(entry_for_new='not_yet', tone='neutral'), 'watch_avoid'),
    ('C3', '聯發科', '聯發科不買，因為聯發科這個缺口補了，我會怕這個',
     dict(entry_for_new='prohibit', tone='bearish', selection_intent='explicit_exclusion'), 'watch_avoid'),
    ('D1', '聯電', '130以下，不準賣聯電。130以下，我沒有聯電',
     dict(holder_advice='hold_rebound'), 'watch_watch'),
    ('D2', '台達電', '不要亂殺台達電？台達電現在要來回震盪打底，我沒有買台達電',
     dict(holder_advice='hold_rebound', entry_for_new='none'), 'watch_watch'),
    ('D3', '鴻海', '我有沒有告訴你鴻海，是這一波對的股票',
     dict(entry_for_new='wait_condition', tone='bullish', selection_intent='positive_candidate'), 'watch_watch'),
    ('D4', '東元', '那傳產股便宜的還有這一支叫東元',
     dict(tone='bullish', selection_intent='positive_candidate'), 'watch_watch'),
    ('E1', '玉晶光', '昨天玉晶光漲停，打開報紙全部玉晶光。來，今天玉晶光了，跌停',
     dict(about_itself=False, independent_analysis=False, content_role='comparison_only'), 'ignored'),
    ('F1', '國巨', '國巨這兩天漲的還不夠他一天跌，彈上來一定還會再下去',
     dict(entry_for_new='prohibit', tone='bearish', selection_intent='explicit_exclusion'), 'watch_avoid'),
    ('F2', '大立光', '我不會碰大立光，昨天大力光，漲60塊，今天跌260',
     dict(entry_for_new='prohibit', tone='bearish'), 'watch_avoid'),
]


class WatchBoundary1103(unittest.TestCase):
    def test_every_questionnaire_case_maps_to_the_manager_answer(self):
        for qid, name, raw, fields, want in CASES:
            self.assertEqual(case(name, raw, **fields), want, qid)

    def test_market_wait_does_not_turn_a_positive_view_into_avoid(self):
        # 同一句「等大環境」：本股偏多是注意，本股偏空才是不碰（不依字詞）
        raw = '甲公司技術面已經背離，等大環境穩了再買'
        self.assertEqual(case('甲公司', raw, entry_for_new='wait_condition', tone='bullish'), 'watch_watch')
        self.assertEqual(case('甲公司', raw, entry_for_new='wait_condition', tone='bearish'), 'watch_avoid')

    def test_formal_authorization_wait_stays_avoid_even_on_a_positive_list(self):
        raw = '甲公司我很看好，但要等我正式通知才能買，現在先不要動'
        self.assertEqual(case('甲公司', raw, entry_for_new='not_yet', tone='bullish',
                              selection_intent='authorization_pending'), 'watch_avoid')

    def test_prompts_carry_the_new_boundary_and_not_the_1102_rule(self):
        src = (ROOT / 'pipeline/pipeline.py').read_text(encoding='utf-8')
        self.assertIn('大環境的等待適用所有股票，不是本股的禁令', src)
        self.assertIn('最新政策（2026/11/03 管理者問卷）', src)
        self.assertNotIn('即使是正向觀察名單或未來看好也不改成注意', src)
        # 11/03 的邊界上線時升到 v42；之後的管理者裁示（11/06 v43）再往上升，舊快取都不能沿用
        m = __import__('re').search(r'VERDICT_VERSION = "v(\d+)-', src)
        self.assertTrue(m and int(m.group(1)) >= 42, '裁決版號要 v42 以上')
        gas = (ROOT / 'public-site/gas-source/Adminpipeline.gs').read_text(encoding='utf-8')
        self.assertIn('大環境的等待適用所有股票', gas)


if __name__ == '__main__':
    unittest.main()
