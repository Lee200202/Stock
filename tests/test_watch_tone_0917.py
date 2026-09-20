# -*- coding: utf-8 -*-
"""觀望方向（2026/09/17）：祥碩、勤誠出現在「觀望不碰｜語氣偏空，暫不進場」。

網站上的說明：
  祥碩（5269）「假破底後站上季線，目標價看2000元，拉回或打底後可注意。」
  勤誠（8210）「等待賣壓減輕、散戶在低點賣完後，將是真正可以賺錢準備大漲的時機。」
兩句都是偏多，語氣核對卻把「假破底」裡的「破底」、「賣壓減輕」裡的「賣壓」當成負面線索，
watch_tone 判 watch_avoid，_bearish_or_neutral_basis 也成立，於是改到觀望不碰。

原句取自當天原始逐字稿（去空白），是講者對這兩檔的實際結論。
"""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from test_quality import p, empty

ROOT = Path(__file__).resolve().parents[1]

ASMEDIA = '假破底後站上季線，目標價看2000元，拉回或打底後可注意。'
AMAX = '等待賣壓減輕、散戶在低點賣完後，將是真正可以賺錢準備大漲的時機。'

# 逐字稿原句（語音誤字照留：享碩／降碩＝祥碩、初清城／秦城＝勤誠、捷徑＝竭盡）
SOURCE_ASMEDIA = [
    '今天跌5塊你跟我講他不好趕快賣趕快趕快賣你們想賣的趕快賣我的會員不准賣哦',
    '所有均線通通突破薊限上立公空投那你是會做股票是不會做股票？啊有沒有突破下降壓力線？有',
    '享碩的目標價我是定在2000塊我是定在2000啦',
    '我為什麼一直抱著降碩？你國去腰斬了我的享碩還賺雖然賺不多',
]
SOURCE_AMAX = [
    '初清城昨天通賣掉好注意哦這是昨天哦更新日期到昨天9月16號',
    '只要ETF賣在低點的股票我可以跟你說不會跌了',
    '追高殺低的股票他殺不下去了就會漲上來',
    '賣壓捷徑的時候就是股票要大漲的時候',
]


def normalize(cat, reason):
    s = empty(); s[cat] = [{'name': 'X', 'reason': reason}]
    with redirect_stdout(io.StringIO()):
        p.normalize_watch_tones(s)
    return 'watch_watch' if s['watch_watch'] else 'watch_avoid'


class ReversalToneTests(unittest.TestCase):
    def test_published_reasons_are_watch(self):
        for reason in (ASMEDIA, AMAX):
            self.assertEqual(p.watch_tone(reason), 'watch_watch', reason)
            self.assertFalse(p._strict_bearish_basis(reason), '假破底、賣壓減輕不是偏空依據')
            self.assertFalse(p._bearish_or_neutral_basis(reason))

    def test_both_directions_end_in_watch(self):
        for reason in (ASMEDIA, AMAX):
            self.assertEqual(normalize('watch_watch', reason), 'watch_watch', '模型判注意時不可再被改成不碰')
            self.assertEqual(normalize('watch_avoid', reason), 'watch_watch', '已被列不碰的偏多說明要改回注意')

    def test_reclassify_workflow_uses_the_same_rule(self):
        for reason in (ASMEDIA, AMAX):
            self.assertEqual(p.sentiment_of(reason), 'watch_watch')

    def test_speaker_conclusions_in_transcript_read_as_bullish(self):
        for line in ('只要ETF賣在低點的股票我可以跟你說不會跌了', '賣壓捷徑的時候就是股票要大漲的時候',
                     '追高殺低的股票他殺不下去了就會漲上來', '拍拍手讓他賣完之後股票就漲'):
            self.assertEqual(p.watch_tone(line), 'watch_watch', line)
        # 反話（「你們想賣的趕快賣，我的會員不准賣」）交給 prompt 句型十；關鍵字這一層至少不能把「不准賣」當禁買。
        self.assertNotRegex(SOURCE_ASMEDIA[0], p._PROHIBIT, '「不准賣」不是「不准買」')

    def test_other_bullish_wordings(self):
        for text in ('ETF已在低點全部出清，認為賣壓竭盡、不會再跌，準備大漲。',
                     'ETF 已在低點全部出清，認為賣壓竭盡、不會再跌',   # POLICY 句型十給模型的示範句
                     '只要ETF賣在低點的股票，不會跌了',
                     '外資買進500多張，上半年EPS 72元，所有均線突破、站上季線並突破下降壓力線，目標價2000元。',
                     '賣壓消化完畢，跌不下去，營收成長', '沒量跌破新低是假跌破，站穩季線'):
            self.assertEqual(p.watch_tone(text), 'watch_watch', text)

    def test_real_bearish_wordings_stay_avoid(self):
        for text in ('賣壓沉重，跌破季線', '賣壓還沒消化完，先不要買', '假破底，但追高容易套牢',
                     '外資賣超，賣壓減輕', '破底之後還會跌', '不會大漲，整理為主', '昨天大漲今天大跌',
                     'ETF還沒賣完，現在還不行，賣完就會漲', '還沒站上季線，目標價下修至800元',
                     '外資給的目標價上看9999元', '券商調高目標價至1500元', '不是不會跌，追高容易套牢'):
            self.assertEqual(p.watch_tone(text), 'watch_avoid', text)

    def test_0914_amax_wait_rule_unchanged(self):
        # 9/14 講者明講「現在還不能買」：當天結論仍是不進場（管理者規則），不因「賣壓消化完畢、跌不下去」翻成注意。
        reason = ('勤誠如預期跌破900元整數關卡，但因主動型ETF 00981A目前仍在持續調節賣超中，提醒現階段還不能買進，'
                  '必須耐心等候ETF賣壓消化完畢、跌不下去時再尋找適當的買點進場佈局。')
        self.assertEqual(p.watch_tone(reason), 'watch_avoid')
        self.assertEqual(normalize('watch_watch', reason), 'watch_avoid')
        self.assertEqual(normalize('watch_avoid', reason), 'watch_avoid')


class ReversalPromptTests(unittest.TestCase):
    def test_policy_has_rule_ten(self):
        self.assertIn('十、反話與「賣壓竭盡」是看多，不是看空', p.POLICY)
        self.assertIn('「不准賣」是續抱指示，不是「不准買」', p.POLICY)
        self.assertIn('負面示範的主角必須是這一檔股票本身', p.POLICY)
        self.assertIn('不可寫成「等待賣壓減輕」', p.POLICY)
        self.assertIn('我為什麼一直抱著降碩？…我的享碩還賺', p.POLICY)
        self.assertIn('不算語氣偏負面（見句型十）', p.POLICY)
        self.assertIn('照句型十保留', p.AUDIT_SYSTEM)
        self.assertIn('六種句型', p.AUDIT_SYSTEM)

    def test_prompt_quotes_come_from_the_transcript(self):
        compact = lambda t: ''.join(ch for ch in t if ch not in '，。？！、…「」 ')
        source = ''.join(SOURCE_ASMEDIA + SOURCE_AMAX)
        for quote in ('你們想賣的趕快賣，我的會員不准賣', '我的享碩還賺', '不會跌了', '股票要大漲的時候'):
            self.assertIn(compact(quote), source, quote)

    def test_gas_prompts_synced(self):
        gas = (ROOT / 'apps-script/Adminpipeline.gs').read_text(encoding='utf-8')
        self.assertEqual(gas.count('十、反話與「賣壓竭盡」是看多，不是看空'), 3, '擷取、覆核、複審三份都要有；請執行 scripts/sync_quality.py')
        service = (ROOT / 'apps-script/Adminservice.gs').read_text(encoding='utf-8')
        self.assertIn('反話與「賣壓竭盡」是看多，歸 watch', service)
        self.assertIn('「假破底後站上季線，目標價看2000元，拉回或打底後可注意」→ watch', service)
        self.assertEqual((ROOT / 'pipeline' / 'pipeline.py').read_bytes(), (ROOT / 'pipeline/pipeline.py').read_bytes())


if __name__ == '__main__':
    unittest.main()
