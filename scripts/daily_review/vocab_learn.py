"""畫面辨識學到的詞（複核紀錄「詞庫學習」分頁）。2026/11/12 管理者：「擷取畫面時發現詞庫裡沒有的字詞，
回傳給之後聽打與候選詞單用」。

兩種詞：
  新股名：畫面左上角清楚讀到、代號表對得上、但聽打詞表裡還沒有的股名 → 直接「已採用」（下次聽打把它當專有名詞）。
  聽錯：主持人說的音和畫面股名不同（四星KY＝世芯-KY）→「待核對」，11:40 雲端複核對照原稿確認後改「已採用」或「不採用」。

讀的人：transcript.py（聽打詞表與「易聽錯」提示）、scripts/screen_identify.py（D 類例子）。
寫的人：screen_identify.py（新增列）、screen_refs.py vocab-set（雲端複核改狀態）。
這裡只放純函式與讀取；讀不到複核紀錄時一律回空，不影響聽打。
"""
import os
import re

TAB = '詞庫學習'
HEAD = ['建立時間', '日期', '類型', '聽到', '股名', '代號', '次數', '時間', '狀態', '說明']
STATES = ('已採用', '待核對', '不採用')
# 指代詞：主持人沒說名字，不是聽錯
PRONOUN = re.compile(r'^(這|那|它|他|她|哪|一|第.|再|另|某)[一個支隻檔家]*(股票|檔|支|隻|個)?$|^(它|他|她)$|股票$')


def norm(name):
    """比對用：去掉 -KY、星號、空白、全半形差異。"""
    text = str(name or '').upper().replace('－', '-').replace('＊', '*')
    text = re.sub(r'[\s*＊·．.]', '', text)
    text = re.sub(r'-?KY$', '', text)
    return text.replace('-', '')


def is_pronoun(heard):
    h = re.sub(r'\s', '', str(heard or ''))
    return not h or bool(PRONOUN.search(h)) or len(h) > 10


def adopted(rows):
    """純函式：已採用的列 → (股名清單, [(聽到, 股名)])，新的在前、不重複。"""
    names, pairs = [], []
    for r in reversed(rows):
        if str(r.get('狀態', '')).strip() != '已採用':
            continue
        name = str(r.get('股名', '')).strip()
        heard = str(r.get('聽到', '')).strip()
        if name and name not in names:
            names.append(name)
        if r.get('類型') == '聽錯' and heard and name and (heard, name) not in pairs:
            pairs.append((heard, name))
    return names, pairs


def learn(items, known_names, known_pairs=(), code_names=None):
    """純函式：畫面辨識結果 → 要寫進「詞庫學習」的新列（類型、聽到、股名、代號、次數、時間）。
    只用把握「清楚」且有代號的時刻；已知的詞不再寫。code_names＝{代號: 股名}，用來排除
    「主持人說的是另一檔真的股票」（那是畫面和話題對不上，不是聽錯）。"""
    known = {norm(n) for n in known_names if n}
    pairs = {(norm(h), norm(n)) for h, n in known_pairs}
    real = {norm(v) for v in (code_names or {}).values()}
    out = {}
    for it in items:
        if it.get('confidence') != '清楚' or not it.get('code') or not it.get('name'):
            continue
        name, heard = it['name'], str(it.get('heard') or '').strip()
        if norm(name) not in known:
            key = ('新股名', name, name)
            row = out.setdefault(key, {'類型': '新股名', '聽到': name, '股名': name, '代號': it['code'], '次數': 0, '時間': it['time']})
            row['次數'] += 1
        if (not is_pronoun(heard) and norm(heard) != norm(name) and norm(name) not in norm(heard)
                and (norm(heard), norm(name)) not in pairs and norm(heard) not in real and 2 <= len(norm(heard)) <= 8):
            key = ('聽錯', heard, name)
            row = out.setdefault(key, {'類型': '聽錯', '聽到': heard, '股名': name, '代號': it['code'], '次數': 0, '時間': it['time']})
            row['次數'] += 1
    return list(out.values())


def load_rows():
    """讀「詞庫學習」所有列；沒有 REVIEW_LOG_ID 或讀不到就回 []。"""
    if not os.environ.get('REVIEW_LOG_ID', '').strip():
        return []
    try:
        from common import open_log, rows_of
        return rows_of(open_log().worksheet(TAB))[1]
    except (Exception, SystemExit) as exc:          # noqa: BLE001  聽打不能因為學習詞表讀不到而停
        print(f'（讀不到「{TAB}」，不加學到的詞：{type(exc).__name__}）')
        return []


def load_adopted():
    return adopted(load_rows())
