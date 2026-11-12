"""畫面辨識學到的詞（複核紀錄「詞庫學習」分頁）。2026/11/12 管理者：「擷取畫面時發現詞庫裡沒有的字詞，
回傳給之後聽打與候選詞單用」。2026/11/12 晚依外部 AI 建議收緊（見 docs/daily-review/每日複核作業.md 5-2-1）。

兩種詞：
  新股名：左上角清楚、代號表兩欄一致、K 線／報價畫面且話題相同、聽打詞表還沒有 → 直接「已採用」。
  聽錯：主持人說的音和畫面股名不同 →「待核對」，雲端複核對照原稿確認後改「已採用」或「不採用」。
採用規則：同一配對取最新狀態（後來改「不採用」會撤銷）；同一個音指向多檔時全部停用，留給管理者。
讀的人：transcript.py、scripts/screen_identify.py；寫的人：screen_identify.py、screen_refs.py vocab-set。
"""
import os
import re
import unicodedata

TAB = '詞庫學習'
HEAD = ['建立時間', '日期', '類型', '聽到', '股名', '代號', '次數', '時間', '狀態', '說明']
STATES = ('已採用', '待核對', '不採用')


def norm(name):
    # 保留 KY，避免與另一檔同音或近似名稱混為一談。
    return re.sub(r'[\s*＊.-]', '', unicodedata.normalize('NFKC', str(name or '')).upper())


def is_pronoun(heard):
    h = norm(heard)
    return not h or len(h) > 10 or bool(re.fullmatch(r'(?:這|那|哪|它|他|她|第[一二三四五六七八九十]|另一|再一)[一個支隻檔家]*(?:股票)?|股票', h))


def adopted(rows):
    # 同一配對取最新狀態；後續否決可以撤銷舊採用。同一讀音指向多檔時停用全部配對，留給管理者解衝突。
    latest = {}
    for r in rows:
        latest[(r.get('類型'), norm(r.get('聽到')), norm(r.get('股名')))] = r
    approved = [r for r in latest.values() if r.get('狀態') == '已採用' and r.get('股名')]
    targets = {}
    for r in approved:
        if r.get('類型') == '聽錯':
            targets.setdefault(norm(r.get('聽到')), set()).add(norm(r['股名']))
    names, pairs = [], []
    for r in reversed(approved):
        name, heard = r['股名'], r.get('聽到', '')
        if name not in names:
            names.append(name)
        if r.get('類型') == '聽錯' and len(targets.get(norm(heard), ())) == 1 and not is_pronoun(heard):
            pairs.append((heard, name))
    return names, pairs


def learn(items, known_names, known_pairs=(), code_names=None):
    known = {norm(n) for n in known_names}
    pairs = {(norm(h), norm(n)) for h, n in known_pairs}
    real = {norm(n) for n in (code_names or {}).values()}
    out, observations = {}, {}
    for it in items:
        name, code, heard = it.get('name', ''), it.get('code', ''), str(it.get('heard', '')).strip()
        if not (it.get('identity_verified') and it.get('confidence') == '清楚'
                and it.get('scene') in ('chart', 'quote') and it.get('target_match') == 'same'
                and norm((code_names or {}).get(code, '')) == norm(name)):
            continue
        if re.search(r'(?<!是)不是|不叫|而是|改口|以及|還有|、', it.get('said', '')):
            continue
        kinds = []
        if norm(name) not in known:
            kinds.append(('新股名', name))
        if (it.get('speech_kind') == 'misheard' and not is_pronoun(heard)
                and 2 <= len(norm(heard)) <= 8 and norm(heard) not in real and norm(heard) != norm(name)
                and (norm(heard), norm(name)) not in pairs
                and not re.search(r'\d|[元塊點%％]|^[零〇一二兩三四五六七八九十百千萬億]+$', heard)):
            kinds.append(('聽錯', heard))
        for kind, h in kinds:
            key = (kind, h, name, code)
            row = out.setdefault(key, {'類型': kind, '聽到': h, '股名': name, '代號': code, '次數': 0, '時間': it['time']})
            observations.setdefault(key, set()).add(it['time'])
            row['次數'] = len(observations[key])   # 只表示不同時刻數，不是獨立驗證票數
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
