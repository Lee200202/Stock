"""2026/11/12：個股說明的中文數字換成阿拉伯數字（管理者：「930 元都是中文，要呈現數字」）。不連線。"""
import os
import sys
from pathlib import Path
os.environ.setdefault('GEMINI_API_KEY', 'offline')
os.environ.setdefault('SPREADSHEET_ID', 'x')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
sys.path.insert(0, str(ROOT / 'scripts'))
import pipeline as pl  # noqa: E402
from apply_number_digits import plan_cells  # noqa: E402

f = pl.digits_for_reading
cases = {
    # 11/12 正式站實際句子
    '會員於平盤以下約九百五十三元低檔買進宜鼎，今日盤中上漲至一千零二十五元。宜鼎季線位於一千二百六十五元':
        '會員於平盤以下約953元低檔買進宜鼎，今日盤中上漲至1025元。宜鼎季線位於1265元',
    '台積電短短三天大漲三百八十點': '台積電短短三天大漲380點',
    '聯電前三季每股盈餘為六元，股價一百零四元': '聯電前三季每股盈餘為6元，股價104元',
    # 其他量詞
    '外資賣超兩萬張': '外資賣超20000張', '營收三十億': '營收30億', '漲幅百分之二十': '漲幅20%',
    '三點五倍': '3.5倍', '十五元': '15元', '漲停十趴': '漲停10%', '五塊錢': '5塊錢', '一千五百八十塊': '1580塊',
    # 不是確切數字或一般用語：不換
    '有一點擔心': '有一點擔心', '這一張圖': '這一張圖', '幾百元': '幾百元', '上千張': '上千張', '一兩百元': '一兩百元',
    '第三張': '第三張', '一檔股票': '一檔股票', '三天': '三天', '一塊兒': '一塊兒', '好幾倍': '好幾倍',
    # 已經是數字：不動
    '2950元': '2950元', '': '',
}
bad = [(k, f(k), v) for k, v in cases.items() if f(k) != v]
assert not bad, bad

signals = {'buy': [{'name': '聯電', 'reason': '股價一百零四元'}], 'holdings': [{'note': '季線一千二百六十五元', 'reason': '三天'}],
           'market': [{'text': '大漲三百八十點'}]}
pl.public_number_digits(signals)
assert signals['buy'][0]['reason'] == '股價104元' and signals['holdings'][0]['note'] == '季線1265元'
assert signals['market'][0]['text'] == '大漲三百八十點'        # ①③ 重點由網站從判讀稽核重組，這裡不換

values = [['日期', '股票名稱', '理由摘錄'], ['2026/11/12', '聯電', '股價一百零四元'], ['2026/11/11', '宜鼎', '九百五十三元'],
          ['2026/11/12', '勤誠', '570元以下']]
assert plan_cells(values, '理由摘錄', '2026/11/12') == [(2, '股價一百零四元', '股價104元')]
try:
    plan_cells(values, '說明重點', '2026/11/12')
    raise AssertionError('缺欄要報錯')
except ValueError:
    pass
print('11/12 數字寫法：中文數字＋單位換成阿拉伯數字、一般用語不換、只改當天個股說明通過')
