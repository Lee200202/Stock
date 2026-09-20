# -*- coding: utf-8 -*-
"""v18：2026/09/14 第三次重跑仍分錯的語氣、舊推薦與「沿用前一版」。原句取自當天原始逐字稿（去空白）。"""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from test_quality import p, empty

SOURCE = ['再來361KY我買3800多的算9啦現在是不是賠錢因為禮拜五宣布營收創歷史新高營收創歷史新高你有沒有看到營收創歷史新高啊跌要不是有外部因素我早就衝進去買了好不好？營收創歷史新高那代表你來看啊我來我來跟你們模擬一下這是第二季的營收我們看第一個數字叫213對不對？那它得出來EPS嘛啊這是第二第三季的營收78我問你第三季的營收有沒有遠是第二季營收的兩倍以上那代表四星KY第三季的EPS會是第二季的兩倍以上那我幹嘛緊張啊我就給你放到你衝到50006000我要賣在再來賣我幹嘛在跌的時候去賣所以投資人你要先了解自己公司到底是有沒有問題沒有問題營收創歷史新高就跟台積電你要跌你就去跌嘛那個叫做卡你賣低點以後就被笑賣在阿呆谷一模一樣等一下我會講這個東西就是跟你們講什麼叫做外你知道嗎？我以前在大學上課就是這麼上了所以我的學生超級多的我在經濟系上課連財經系的都跑過來旁聽法律系的也跑過來旁聽我以前在福人大學上課的時候我的一個教室300多個位置都要排到走廊外面去就是因為我不想存講理論我希望把理論跟食物結合當然以前我在大學上課的時候都會報名牌都會跟他們講說', '要講理論這樣你好像在拖時間如果真的你今天要買股票放長線一點一定是一隻股票我有跟你們講存股嘛我跟你們講的存股是哪一隻？浴龍嘛很多人說你講玉龍我們知道你30塊以下就開始講為什麼美股禁值60.65玉龍的資產價值非常高不是因為玉龍車子賣的多好而是資產價值他把lestant賣給紅海你們應該知道然後它是長線你看這麼長線你看玉龍我在這一邊推薦了連長兩天之後他有沒有修正這邊有個小缺口他回補了回補了之後就沒事啊這就是玉啊他的美股禁值多少？60啊啊現在多少？30啊跟我那時候講華新一樣啊20跟你們講說每股禁止在40塊你們都不相信20的時候有沒有？這邊就是我推薦華新的時候去年的這個時候啊我現在不推薦華新哦我現在沒有推薦花星哦我現在推薦的是什麼？推薦的是這個啊對不對？來如果你覺得玉龍很慢你們就想要聽聽人家推薦股票嘛你們如果覺得玉龍很慢你開始要對未來有比較活潑的股票又要好公司的穩健資的還有一隻叫做紅準紅準今天跌哦我特別講哦紅蠢今天跌哦我特別講你看最近外資買他是買什麼意思的？每天買他是買什麼意思的？是買什麼意思的啊？我紅我的會員是買這一天我的會員是買這一天我必須要很老實的告訴你我的會員是買第一根長虹棒那一天所以如果真的要你們今天先從這兩檔便宜的開始吧先從這兩檔便宜的開始對不對？這樣才對啊而不是開化性化上幾百塊你敢買嗎？這個東西都算了這不是你們該買的這個是有錢人在玩的遊戲這不是你們該碰的啊你們該碰的要穩健我當時推薦華新20塊有人跟我講推薦這個什麼爛股票結果漲一倍比你投資的ETF還要對不對？所以投資朋友你們都有先入為主的觀念你介紹會玉龍衝場對不對？你介紹我問你啦我愛普介紹幾塊錢？273塊愛普愛普所有人聽我講了一個月的愛普73塊現在愛普幾塊？給你們我在哪裡推薦愛普的這邊？我在這邊推薦愛普的所以投資朋友你們都要等長上來才想買啊當時的愛普200多塊沒人要買啊我我買了還被罵那個什麼爛股票啊幹嘛幹嘛你們都不知道我們的研究是已經研究到一個月兩個月後的事情我的研究不是跟你研究明天好不好研究明天幹什麼我都敢跟你講事會到五字頭了我都敢跟你講四星會到五字頭了我有什麼好擔心的？所以這個東西是你們在操作股票裡面一定要注意的那今天當然光學股受到影響大力光來我們一個一個大立光跌停玉金光也跌亞光亞光所以今天的光學股受到影響受到影響大力光一定要回補缺口回補缺口啦所以大光千啊張正叫你不要買你說漲停張廳你看當時的漲停跳空不是一定掛嗎？當時的漲停跳空不是一定會掛嗎？你看你買漲停幹什麼？一定會補缺口的啊所以你好好去注意好去注意這樣的觀念這樣的觀念啊紅海當時跟你們講238買進啊238買進你也不會有事啊對不對？所以投資朋友你要去注意注意這一塊那再來我們講一些高價股高價股我的會員加折加折你看哦這順便教你們一下我都會有人家折在這邊第二這一天我買1580以下的我已經電視講好幾天了對不對？1580以下買加折沒有錯吧？會員我沒有說謊吧對不對？好很多人說張總你買到最低點了這一天好買到最低點不是代表你馬上會賺錢股票會有一個邏輯它會這樣疊下來這一邊是最低點然後他要這樣子它會在這邊打一個底再上去所以股票絕對你買到最低點是OK可是買到最低點你還是要等股票不會你買到最低點就無上去你們永遠都在想這個啊你們都不要這一段不要這一段又想要最低點慢慢等不會有這件事的所以我跟我的會員講我們買1580以下的這一天算是最低點可是他還是會這樣子他還是會這樣子給你磨啊你融資都洗掉啊不然現在也沒有賠啊所以投資朋友你們要去了解很多時候來再來講高價股成本限定投資法還記不記得我之前至少講了一個月以上我以前操作過兩支高價股全部大賺錢如', '最中肯的完了時間超過了上個禮拜日元大聲子是不是新聞就寫新聞寫什麼？新聞寫日元生值台灣工具機將獲得轉單大單來的有沒有上個禮拜五大家是不是都在買上癮對不對？上個禮拜五因為日元生值台灣廠商受賄新聞寫給你們看了對不對？來上疊停板疊停板疊停板這樣懂嗎？這樣懂嗎？好前幾天我看到一篇文章正正式講中我心裡的話我也順便要跟你們講你們有沒有注意我一直跟你們講不要看新聞做股票有沒有？有沒有不要看新聞做股票？好我有講過嘛']
TRANSCRIPT = ''.join(SOURCE)


def normalize(cat, reason):
    s = empty(); s[cat] = [{'name': 'X', 'reason': reason}]
    with redirect_stdout(io.StringIO()):
        p.normalize_watch_tones(s)
    return 'watch_watch' if s['watch_watch'] else 'watch_avoid'


class ToneV18Tests(unittest.TestCase):
    LARGAN = '大立光因遭受特定外資針對性調降評等，加上自身股價漲多偏離合理價值，盤中出現跌停且下方缺口尚未完全回補。指出漲多本就會回跌，並藉此提醒投資人切勿盲目追高買進，目前走勢疲弱且受外部干擾影響顯著。'
    GSEO = '玉晶光受到光學股整體走弱與大立光跌停的連動影響而跟進重挫，藉此作為負面示範，提醒追高容易受傷套牢，不應把低檔股票賣掉跑去追買高檔弱勢股，應避免過度交易。'
    AMAX = '勤誠如預期跌破900元整數關卡，但因主動型ETF 00981A目前仍在持續調節賣超中，提醒現階段還不能買進，必須耐心等候ETF賣壓消化完畢、跌不下去時再尋找適當的買點進場佈局。'

    def test_negative_examples_are_avoid_in_both_directions(self):
        for reason in (self.LARGAN, self.GSEO, self.AMAX):
            self.assertEqual(p.watch_tone(reason), 'watch_avoid', reason[:12])
            self.assertEqual(normalize('watch_avoid', reason), 'watch_avoid', '模型判不碰、說明偏空，不可翻成注意')
            self.assertEqual(normalize('watch_watch', reason), 'watch_avoid', '模型判注意、說明偏空，改成不碰')

    def test_keyword_false_positives_removed(self):
        self.assertEqual(p.watch_tone('外資調降評等，加上股價漲多'), 'watch_avoid', '評等的「等」不是等待')
        self.assertEqual(p.watch_tone('把低檔股票賣掉跑去追買高檔股'), 'watch_avoid')
        self.assertEqual(p.watch_tone('低檔可以佈局，拉回到季線會再買回來'), 'watch_watch')
        self.assertEqual(p.watch_tone('等補完缺口再站回去'), 'watch_watch')

    def test_positive_rows_still_move_to_watch(self):
        self.assertEqual(normalize('watch_avoid', '業績很好不必擔心，還有一隻叫做3545敦泰，值得納入觀察'), 'watch_watch')
        self.assertEqual(normalize('watch_avoid', '900以下是買點'), 'watch_watch')


OFFICIAL = {'6531': '愛普*', '3661': '世芯-KY', '1605': '華新', '2201': '裕隆', '2233': '宇隆', '2317': '鴻海',
            '3008': '大立光', '3406': '玉晶光', '3019': '亞光', '2330': '台積電', '2049': '上銀'}


class PastRecapV18Tests(unittest.TestCase):
    def excluded(self, row):
        s = empty(); s['watch_watch'] = [dict(row, _date='2026/09/14')]
        with patch.object(p, '_CODE_MAP', dict(OFFICIAL)):   # 正式執行時官方清單一定已載入
            p.exclude_past_recommendations(s, TRANSCRIPT)
        return not s['watch_watch']

    def test_apu_recap_excluded(self):
        self.assertTrue(self.excluded({'name': '愛普*', 'code': '6531', 'aliases': ['愛普'], 'reason': '曾被推薦',
                                        'evidence': [SOURCE[1][640:900]]}))

    def test_yulon_current_recommendation_kept(self):
        self.assertFalse(self.excluded({'name': '裕隆', 'code': '2201', '原始語音名稱': '浴龍', 'aliases': ['玉龍'],
                                         'reason': '存股', 'evidence': [SOURCE[1][:700]]}),
                         '「我現在推薦的是什麼？推薦的是這個」是現在的推薦')

    def test_alchip_not_blamed_for_neighbouring_apu_recap(self):
        self.assertFalse(self.excluded({'name': '世芯-KY', 'code': '3661', '原始語音名稱': '四星KY',
                                         'reason': '營收創新高', 'evidence': [SOURCE[1][640:1000]]}),
                         '愛普的推薦回顧不能算到世芯-KY')


class CarryForwardV18Tests(unittest.TestCase):
    def test_prior_wrong_rows_are_not_carried_but_missed_real_rows_are(self):
        prior = [{'_cat': 'watch_watch', 'name': '愛普*', 'code': '6531', 'price': '273', 'reason': '曾被推薦', '_seq': 1},
                 {'_cat': 'watch_watch', 'name': '金益鼎', 'code': '8390', 'price': '未說明', 'reason': '權利金', '_seq': 1},
                 {'_cat': 'watch_avoid', 'name': '上銀', 'code': '2049', 'price': '未說明', 'reason': '看新聞追', '_seq': 1},
                 {'_cat': 'watch_watch', 'name': '裕隆', 'code': '2201', 'price': '30', 'reason': '存股', '_seq': 1}]
        s = empty()
        with patch.object(p, '_CODE_MAP', dict(OFFICIAL, **{'8390': '金益鼎'})):
            rec = p.reconcile_with_prior(s, [dict(r) for r in prior], '2026/09/14', TRANSCRIPT)
        text = '｜'.join(rec['accepted'])
        self.assertIn('愛普*（前一版觀望注意）：本輪原文只有舊推薦回顧', text)
        self.assertIn('金益鼎（前一版觀望注意）：本輪原文找不到這個名稱', text)
        self.assertEqual(sorted(r['name'] for r in s['watch_watch'] + s['watch_avoid']), ['上銀', '裕隆'])
        self.assertFalse(rec['anomaly'])

    def test_mismatched_transcript_still_triggers_anomaly(self):
        prior = [{'_cat': 'watch_watch', 'name': n, 'code': c, 'price': '未說明', 'reason': '候選', '_seq': 1}
                 for n, c in (('聖暉*', '5536'), ('辛耘', '3583'), ('牧德', '3563'), ('新代', '7750'))]
        rec = p.reconcile_with_prior(empty(), prior, '2026/09/14', TRANSCRIPT)
        self.assertTrue(rec['anomaly'], '前一版大多找不到時，要當成原文不完整、保留舊資料')


if __name__ == '__main__':
    unittest.main()
