"""2026/11/12：直播畫面辨識的純函式（不連 Gemini、不連試算表）。"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / 'daily_review'))
os.environ.setdefault('GEMINI_API_KEY', 'offline')
from screen_identify import dedupe, parse_items, prompt  # noqa: E402
from screen_refs import day_rows  # noqa: E402
from common import LOG_TABS  # noqa: E402

reply = '''```json
[{"time":"0:17:05","said":"你看這一檔，底部爆量","screen_name":"勤誠","screen_code":"8210","confidence":"清楚","note":"K線圖標題"},
 {"time":"2:10","said":"這檔先不要","screen_name":"","screen_code":"","confidence":"清楚","note":"只有講者"},
 {"time":"0:17:20","said":"這一檔","screen_name":"勤誠","screen_code":"8210","confidence":"模糊","note":""},
 {"time":"1:40:00","said":"超出範圍","screen_name":"x","screen_code":"1234","confidence":"清楚","note":""},
 {"time":"0:20:00","said":"代號亂寫","screen_name":"宜鼎","screen_code":"5289-KY","confidence":"怪","note":""}]
```'''
items = parse_items(reply, 900, 1800)
assert [i['time'] for i in items] == ['0:17:05', '0:17:10', '0:17:20', '0:20:00'], items   # 2:10 是片段內時間→0:17:10；超出範圍的丟掉
assert items[1]['confidence'] == '模糊'                       # 清楚卻沒有名稱代號 → 模糊
assert items[3]['code'] == '5289' and items[3]['confidence'] == '模糊'
kept = dedupe(items)
assert [(i['time'], i['confidence']) for i in kept] == [('0:17:05', '清楚'), ('0:17:10', '模糊'), ('0:20:00', '模糊')], kept   # 30 秒內同一檔只留一筆
assert parse_items('不是 JSON', 0, 900) is None and parse_items('[]', 0, 900) == []
assert '行程' in prompt(0, 900) and '0:15:00' in prompt(0, 900)
rows = [{'日期': '2026/11/12', '時間': '0:17:05'}, {'日期': '2026-11-11', '時間': '0:01:00'}, {'日期': '2026/11/12', '時間': ''}]
assert [r['_row'] for r in day_rows(rows, '2026/11/12')] == [2, 4]
assert LOG_TABS['畫面辨識'][:4] == ['建立時間', '日期', '影片ID', '時間']

# ---- 11/12 追加：左上角技巧、代號表核對、有說出名稱不列、自動學詞 ----
from screen_identify import LAYOUT, SYSTEM, check_codes, explicit  # noqa: E402
import vocab_learn as vl  # noqa: E402
assert '左上角' in LAYOUT and LAYOUT in SYSTEM
assert '「四星KY」＝世芯-KY' in prompt(0, 900, [('四星KY', '世芯-KY')]) and '之前確認過' not in prompt(0, 900)
codes = {'2330': '台積電', '2303': '聯電', '3661': '世芯-KY', '6770': '力積電', '8210': '勤誠', '5289': '宜鼎'}
base = {'time': '0:01:00', 'sec': 60, 'said': '', 'heard': '', 'note': '', 'confidence': '清楚'}
got = check_codes([dict(base, name='聯電', code='2330'),            # 代號讀錯 → 依名稱更正
                   dict(base, name='', code='8210'),                 # 只有代號 → 補名稱
                   dict(base, name='力積電', code=''),               # 只有名稱 → 補代號
                   dict(base, name='Micron', code=''),               # 美股 → 不列
                   dict(base, name='立機電', code=''),               # 表裡沒有 → 模糊
                   dict(base, name='世芯KY', code='3661')], codes)  # -KY 寫法不同仍算同一檔
assert [(g['name'], g['code'], g['confidence']) for g in got] == [
    ('聯電', '2303', '清楚'), ('勤誠', '8210', '清楚'), ('力積電', '6770', '清楚'), ('立機電', '', '模糊'), ('世芯KY', '3661', '清楚')], got
assert '讀成 2330' in got[0]['note']
assert check_codes([dict(base, name='聯電', code='2330')], {}) == [dict(base, name='聯電', code='2330')]   # 沒有代號表不核對
# 有說出名稱＝不用看畫面
assert explicit(dict(base, name='勤誠', code='8210', heard='勤誠', said='你來看勤誠'))
assert explicit(dict(base, name='勤誠', code='8210', heard='', said='你現在看8210勤誠'))
assert not explicit(dict(base, name='世芯-KY', code='3661', heard='四星KY', said='我有四星KY'))       # 聽錯 → 要列
assert not explicit(dict(base, name='宜鼎', code='5289', heard='這一支股票', said='你有講這一支股票'))  # 指代 → 要列
assert not explicit(dict(base, name='', code='', heard='', said='老師請問', confidence='沒有顯示'))
# 指代詞判斷
assert all(vl.is_pronoun(w) for w in ('這一檔', '它', '這一支股票', '第二支', '一檔股票', ''))
assert not any(vl.is_pronoun(w) for w in ('四星KY', '一頂', '行程', '雅德客'))
assert vl.norm('世芯-KY') == vl.norm('世芯KY') == '世芯'
# 自動學詞：新股名直接用；聽錯另列；已知的、指代的、另一檔真股名的不學
items = [dict(base, name='力積電', code='6770', heard='力積電'),
         dict(base, name='力積電', code='6770', heard='立機電', time='1:06:53'),
         dict(base, name='世芯-KY', code='3661', heard='四星KY'),
         dict(base, name='勤誠', code='8210', heard='這一檔'),
         dict(base, name='台積電', code='2330', heard='聯電'),               # 說的是另一檔真的股票，不是聽錯
         dict(base, name='宜鼎', code='5289', heard='一頂', confidence='模糊')]  # 模糊不學
words = vl.learn(items, ['台積電', '勤誠', '世芯-KY'], [('四星KY', '世芯-KY')], codes)
assert [(w['類型'], w['聽到'], w['股名'], w['次數']) for w in words] == [
    ('新股名', '力積電', '力積電', 2), ('聽錯', '立機電', '力積電', 1)], words
rows = [{'類型': '新股名', '聽到': '力積電', '股名': '力積電', '狀態': '已採用'},
        {'類型': '聽錯', '聽到': '四星KY', '股名': '世芯-KY', '狀態': '已採用'},
        {'類型': '聽錯', '聽到': '行程', '股名': '勤誠', '狀態': '待核對'},
        {'類型': '聽錯', '聽到': '一頂', '股名': '宜鼎', '狀態': '不採用'}]
assert vl.adopted(rows) == (['世芯-KY', '力積電'], [('四星KY', '世芯-KY')])
assert vl.HEAD == LOG_TABS[vl.TAB]
# 聽打詞表帶上學到的詞與易聽錯提示
import transcript as tx  # noqa: E402
tx._LEARNED[:], tx._MISHEARD[:] = ['力積電'], [('四星KY', '世芯-KY')]
assert '力積電' in tx.vocabulary() and '四星KY→世芯-KY' in tx.vocabulary()
tx._LEARNED[:], tx._MISHEARD[:] = [], []
print('11/12 畫面辨識：時間換算、範圍、把握、去重、日期篩選、左上角技巧、代號核對、有名不列、自動學詞通過')
