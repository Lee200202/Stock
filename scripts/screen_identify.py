#!/usr/bin/env python3
"""盤中直播畫面辨識（2026/11/12 管理者）。

逐字稿只有聲音：主持人常說「這檔」「這一隻」「你看這個」，或股名被聽寫成別的字（「行程」＝勤誠），
名稱其實只出現在當時的畫面（K 線圖標題、報價視窗）。這支程式和每日聽打逐字稿用同一個方式——
把公開的 YouTube 網址與起訖秒數交給 Gemini（不下載影片）——請它找出這類時刻並讀出畫面上的股名與代號。

    python scripts/screen_identify.py [--date YYYY/MM/DD] [--force] [--dry-run]

結果寫進複核紀錄「畫面辨識」分頁（每一個時刻一列；沒找到也寫一列「無」，失敗寫一列「失敗」，
讓 11:40 的雲端複核知道已經跑過）。只讀影片與寫複核紀錄，不改網站、不寄信。
由 .github/workflows/screen-identify.yml 在回放處理好後執行；雲端複核用
scripts/daily_review/screen_refs.py 讀結果。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts' / 'daily_review'))

TAB = '畫面辨識'
SEGMENT_SECONDS = 15 * 60
FPS = float(os.environ.get('SCREEN_FPS', '0.5'))          # 兩秒一張畫面：讀得到標題，又不爆輸入量
CONFIDENCE = ('清楚', '模糊', '沒有顯示')

# 依 2026/10/29–11/11 十天逐字稿的候選池分析整理（約 230 個粗篩、去掉雜訊後約 27 處，每天 2–4 處）；見 docs/daily-review/每日複核作業.md 5-2。
PATTERNS = [
    'A. 講一檔股票的價位、EPS、營收、外資連買、融資等細節，卻只說「這一支／這一檔／這隻股票／一檔股票」不說名字'
    '（例：「現在要講一檔股票，是最委屈、最超跌」「這一檔股票跌多深，2025 跌到 800 多」）',
    'B. 序數或預告：「第一支可以買的股票」「再來第二支」「再告訴你們一檔」「給你看一隻」',
    'C. 接在 A、B 後面用「它」繼續評論同一檔（例：「它的營收年成長還是很高」「它根本沒有融資」）——和 A、B 算同一檔，只回一次',
    'D. 有說名字但聽起來不像股名或可能聽錯（同音字、斷詞錯；例：「四星 KY」＝世芯-KY、「一頂」＝宜鼎、「雅德客」＝亞德客、「行程」＝勤誠）',
    'E. 只講族群或題材不說個股（例：「今天新增加了這一隻股票，叫做電源管理 IC」「記憶體只看這一支」）',
    'F. 說自己的買賣卻不說名字（例：「我賣的這一支股票，1580 塊買」「我買了一隻你們想不到的股票」）',
]
EXCLUDE = ('不用列：「這個電話／號碼／LINE」；「這個行情、這個東西、這個地方、這個缺口」；「它」指大盤、指數、K 線、匯率、融資比、均線；'
           '「哪一隻不是這樣買的」這類反問；舉例的寓言或理論；片尾廣告。')

# 2026/11/12 管理者：「它固定在左上角，上面就是股票跟代碼，寫成技能去判斷，加快辨認」。
# 看畫面的固定技巧（也寫在 docs/stock-skills/skills/stock-content-audit/references/screen-reading.md）。
LAYOUT = (
    '看股名的固定技巧（這個節目的版面固定）：\n'
    '1. K 線圖與報價視窗的左上角固定是「代號 名稱」（例：8210 勤誠、3661 世芯-KY、2303 聯電），先看左上角，看到就以它為準；'
    '左上角字小時放大看清每一碼，不要用常見的大型股代號（2330 等）代替。\n'
    '2. 左上角沒有個股（只有講者、LINE 對話截圖、新聞頁、切換中）才看畫面其他地方的股名；都沒有就是「沒有顯示」。\n'
    '3. 代號通常是 4 碼數字；名稱照畫面上的字，保留 -KY。美股、指數、期貨不是台股個股，不用列。\n'
    '4. 主持人說的音和左上角的名稱不同時（同音字、斷詞錯），以左上角為準，並把主持人說的那個詞照音寫在 heard。'
)
SYSTEM = (
    '你在看台股教學直播「張震 股市盤中家教班」的一段回放（主持人邊講邊切換 K 線圖與報價畫面）。'
    '你的工作只有一件：找出「主持人正在談某一檔個股，但口頭沒有清楚說出是哪一檔，或說的名稱聽起來不對」的時刻，讀出那時畫面上顯示的股票。'
    '只回報畫面上真的看得到的名稱與代號，不要猜。\n' + LAYOUT
)


def prompt(start, end, learned=()):
    """learned：之前確認過的聽錯詞 [(聽到, 股名)]，放進 D 類當例子（詞庫學習「已採用」）。"""
    extra = ''
    if learned:
        extra = '\n- 之前確認過的聽錯詞（聽到這些音，就是畫面上那一檔）：' + '、'.join(f'「{h}」＝{n}' for h, n in list(learned)[:20])
    return (
        f'影片範圍 {hms(start)} 到 {hms(end)}（時間一律從影片開頭算）。\n'
        '要找的說法：\n- ' + '\n- '.join(PATTERNS) + extra + '\n\n'
        '主持人有清楚說出股名或代號、而且聽起來就是那一檔的，不用列。' + EXCLUDE + '\n'
        '每一個時刻回一個物件：\n'
        '{"time":"h:mm:ss（從影片開頭算）","said":"主持人當時那句話（30 字內，盡量照原話）",'
        '"heard":"主持人口中指這一檔的那個詞，照聽到的音寫（例：這一檔、它、四星KY、一頂；有說對名字就寫那個名字）",'
        '"screen_name":"畫面上的股票名稱，看不到就空字串","screen_code":"畫面上的股票代號，看不到就空字串",'
        '"confidence":"清楚｜模糊｜沒有顯示","note":"20 字內：在畫面哪裡看到（K 線圖標題、報價視窗…），或畫面狀況（只有講者、畫面卡住、黑畫面、切換中）"}\n'
        '清楚＝名稱或代號清楚可讀，而且就是主持人正在談的那一檔；模糊＝看得到但字太小、被擋住或不確定是不是同一檔；'
        '沒有顯示＝那時畫面上沒有個股資訊（可能是設備或畫面切換問題）。\n'
        '只回 JSON 陣列；這一段沒有這種時刻就回 []。'
    )


def hms(sec):
    sec = int(sec)
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def to_sec(text):
    parts = [int(p) for p in re.findall(r'\d+', str(text or ''))]
    if not parts or len(parts) > 3:
        return None
    while len(parts) < 3:
        parts.insert(0, 0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_items(text, start, end):
    """純函式：模型回覆 → 正規化的時刻清單。時間若像是從片段開頭算的，換成從影片開頭算。"""
    raw = str(text or '').strip()
    m = re.search(r'\[.*\]', raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
    except ValueError:
        return None
    if not isinstance(data, list):
        return None
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        t = to_sec(it.get('time'))
        if t is None:
            continue
        if t < start - 5 and start + t <= end + 5:
            t = start + t                       # 模型回的是片段內時間
        if not (start - 5 <= t <= end + 5):
            continue
        conf = str(it.get('confidence') or '').strip()
        conf = conf if conf in CONFIDENCE else '模糊'
        code = re.sub(r'\D', '', str(it.get('screen_code') or ''))[:6]
        name = str(it.get('screen_name') or '').strip()[:20]
        if conf == '清楚' and not (code or name):
            conf = '模糊'
        out.append({'time': hms(t), 'sec': t, 'said': str(it.get('said') or '').strip()[:60],
                    'heard': str(it.get('heard') or '').strip()[:20],
                    'name': name, 'code': code if 4 <= len(code) <= 6 else '',
                    'confidence': conf, 'note': str(it.get('note') or '').strip()[:40]})
    return out


def check_codes(items, code_names):
    """純函式：用代號表核對畫面讀到的名稱與代號（{代號: 簡稱}；空表就不核對）。
    - 名稱和代號指不同檔：名稱在表裡就改用名稱的代號（左上角小字的代號最常讀錯），否則降成模糊；
    - 只有代號：補上表裡的名稱；只有名稱：補上代號；
    - 沒有代號、名稱也不在表裡的英文名（美股）：不列。"""
    from vocab_learn import norm
    if not code_names:
        return items
    by_name = {}
    for c, n in code_names.items():
        by_name.setdefault(norm(n), []).append(c)
    out = []
    for it in items:
        it = dict(it)
        name, code = it['name'], it['code']
        hits = by_name.get(norm(name), []) if name else []
        if code and code in code_names:
            if not name:
                it['name'] = code_names[code]
            elif not same_stock(name, code_names[code]):
                if len(hits) == 1:
                    it['code'] = hits[0]
                    it['note'] = (it['note'] + f'；代號依名稱更正（讀成 {code}）').strip('；')[:60]
                else:
                    it['confidence'] = '模糊' if it['confidence'] == '清楚' else it['confidence']
                    it['note'] = (it['note'] + f'；{code} 是{code_names[code]}').strip('；')[:60]
        elif name and len(hits) == 1:
            if code:
                it['note'] = (it['note'] + f'；代號依名稱更正（讀成 {code}）').strip('；')[:60]
            it['code'] = hits[0]
            it['name'] = code_names[hits[0]]
        elif name and not code and re.fullmatch(r'[A-Za-z .&-]+', name):
            continue
        elif it['confidence'] == '清楚':
            it['confidence'] = '模糊'
        out.append(it)
    return out


def same_stock(screen, listed):
    """純函式：畫面名稱和代號表簡稱是同一檔（表上是簡稱：世界＝世界先進）。"""
    from vocab_learn import norm
    a, b = norm(screen), norm(listed)
    return a == b or (len(b) >= 2 and (a.startswith(b) or b.startswith(a)))


def explicit(it):
    """純函式：主持人有清楚說出畫面上這一檔（說對名稱或代號）＝不用看畫面，不寫列（但仍用來學新股名）。"""
    from vocab_learn import is_pronoun, norm
    if not (it.get('name') or it.get('code')) or it.get('confidence') == '沒有顯示':
        return False
    heard = it.get('heard') or ''
    said = norm(it.get('said'))
    if heard and not is_pronoun(heard):
        return norm(heard) == norm(it.get('name')) or bool(it.get('code') and it['code'] in heard)
    if heard:
        return False                                    # 說的是「這一檔」「它」
    return bool((it.get('name') and norm(it['name']) in said) or (it.get('code') and it['code'] in said))


def dedupe(items):
    """純函式：同一檔在 30 秒內重複回報只留一筆（保留把握較高的）。"""
    rank = {c: i for i, c in enumerate(CONFIDENCE)}
    keep = []
    for it in sorted(items, key=lambda x: (x['sec'], rank[x['confidence']])):
        twin = next((k for k in keep if abs(k['sec'] - it['sec']) <= 30 and (k['code'] or k['name']) == (it['code'] or it['name'])), None)
        if twin is None:
            keep.append(it)
    return keep


class Reader:
    """把片段交給 Gemini 看畫面；金鑰與模型輪替，和逐字稿相同的金鑰設定。"""

    def __init__(self):
        import transcript as tx
        from google import genai
        self.tx = tx
        self.keys = tx.gemini_keys() or ['']
        self.clients = [genai.Client(api_key=k or 'dummy', http_options={'retry_options': {'attempts': 1}}) for k in self.keys]
        primary = os.environ.get('SCREEN_MODEL', '').strip() or tx.GEMINI_MODEL
        self.models = [primary] + [m for m in tx.GEMINI_FALLBACK_MODELS if m and m != primary]
        self.used = []

    def read(self, url, start, end, learned=()):
        from google.genai import types
        last = ''
        for model in self.models:
            for n, client in enumerate(self.clients):
                for attempt in range(2):
                    try:
                        meta = {'start_offset': f'{start}s', 'end_offset': f'{end}s'}
                        if FPS > 0 and attempt == 0:
                            meta['fps'] = FPS
                        config = {'system_instruction': SYSTEM, 'max_output_tokens': 8192,
                                  'response_mime_type': 'application/json'}
                        if re.search(r'gemini-3(?:\.|-)', model):
                            config['thinking_config'] = types.ThinkingConfig(thinking_level='low')
                        res = client.models.generate_content(model=model, contents=types.Content(role='user', parts=[
                            types.Part(file_data=types.FileData(file_uri=url), video_metadata=types.VideoMetadata(**meta)),
                            types.Part(text=prompt(start, end, learned))]), config=types.GenerateContentConfig(**config))
                        self.used.append(f'{model}#{n + 1}')
                        return res.text or '[]'
                    except Exception as exc:                       # noqa: BLE001
                        last = f'{model} 金鑰#{n + 1}：{str(exc)[:200]}'
                        print('  ' + last)
                        if 'fps' in str(exc).lower() or 'invalid argument' in str(exc).lower():
                            continue                                # 不帶 fps 再試一次
                        if any(k in str(exc).lower() for k in ('quota', 'exhausted', '429')):
                            break                                   # 這把金鑰在這個模型用完，換下一把
                        time.sleep(5)
                        break
        raise RuntimeError('Gemini 讀不到這一段：' + last)


def trading_day_check():
    """排程預檢：只用標準庫與休市表，印 run=true/false 給 GitHub。"""
    from zoneinfo import ZoneInfo
    from pipeline.market_holidays import why_closed
    reason = why_closed(datetime.now(ZoneInfo('Asia/Taipei')).date())
    print('run=' + ('false' if reason else 'true'))
    print(('略過：' + reason) if reason else '今日是台股交易日', file=sys.stderr)
    return 0


def main():
    if '--trading-day-check' in sys.argv:
        return trading_day_check()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--date', default='')
    ap.add_argument('--force', action='store_true', help='已經跑過也重跑（會再寫一批列）')
    ap.add_argument('--dry-run', action='store_true', help='只印結果，不寫複核紀錄')
    a = ap.parse_args()
    import transcript as tx
    from common import LOG_TABS, norm_day, now, open_log, rows_of
    day = norm_day(a.date) if a.date else now().strftime('%Y/%m/%d')
    target = datetime.strptime(day, '%Y/%m/%d').date()
    log = None if a.dry_run else open_log()
    ws = log.worksheet(TAB) if log else None
    if ws is not None and not a.force:
        head, rows = rows_of(ws)
        if any(norm_day(r.get('日期')) == day for r in rows):
            print(f'{day} 已經辨識過（畫面辨識分頁已有列）；要重跑加 --force')
            return 0
    video = tx.find_video(target, require_details=True)
    if not video:
        print(f'{day} 找不到當天的直播影片；不辨識')
        return 0
    if not video.is_ready(datetime.now(tx.TAIPEI)):
        print(f'{day} 回放還沒處理好（{video.status_text(datetime.now(tx.TAIPEI))}）；下一輪再試')
        return 0
    url = f'https://www.youtube.com/watch?v={video.id}'
    plan = tx.plan_segments(video.duration_sec, SEGMENT_SECONDS)
    import vocab_learn as vl
    vocab_rows = vl.load_rows()
    learned_names, learned_pairs = vl.adopted(vocab_rows)
    code_names = load_code_names()
    print(f'{day} 影片 {video.id}，{hms(video.duration_sec)}，分 {len(plan)} 段看畫面（fps={FPS}）')
    reader = Reader()
    items, errors = [], []
    for start, end in plan:
        try:
            got = parse_items(reader.read(url, start, end, learned_pairs), start, end)
        except RuntimeError as exc:
            errors.append(f'{hms(start)}–{hms(end)} {exc}')
            continue
        if got is None:
            errors.append(f'{hms(start)}–{hms(end)} 回覆不是 JSON 陣列')
            continue
        print(f'  {hms(start)}–{hms(end)}：{len(got)} 個時刻')
        items.extend(got)
    items = dedupe(check_codes(items, code_names))
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    for it in items:
        print(f"  {it['time']} {it['confidence']} {it['code']} {it['name']}｜{it.get('heard', '')}｜{it['said']}｜{it['note']}"
              + ('（有說出名稱，不列）' if explicit(it) else ''))
    known = list(tx.VOCABULARY.split(',')) + learned_names + known_day_names(tx, day)
    new_words = vl.learn(items, known, learned_pairs, code_names)
    seen = {(r.get('類型'), r.get('聽到'), r.get('股名')) for r in vocab_rows}
    new_words = [w for w in new_words if (w['類型'], w['聽到'], w['股名']) not in seen]
    for w in new_words:
        print(f"  學到{w['類型']}：{w['聽到']} → {w['股名']} {w['代號']}（{w['次數']} 次，{w['時間']}）")
    explicit_n = sum(1 for it in items if explicit(it))
    items = [it for it in items if not explicit(it)]
    print(f'有說出名稱的 {explicit_n} 處不列；要看畫面的 {len(items)} 處')
    head = LOG_TABS[TAB]
    rows = [{'建立時間': stamp, '日期': day, '影片ID': video.id, '時間': it['time'], '講者原話': it['said'],
             '畫面股名': it['name'], '代號': it['code'], '把握': it['confidence'], '說明': it['note'], '狀態': '待核對', '處理說明': ''}
            for it in items]
    if not rows:
        rows = [{'建立時間': stamp, '日期': day, '影片ID': video.id, '時間': '', '講者原話': '', '畫面股名': '', '代號': '', '把握': '',
                 '說明': '；'.join(errors)[:300] if errors else '整支影片沒有找到要看畫面才知道股名的地方',
                 '狀態': '失敗' if errors and not items else '無', '處理說明': ''}]
    elif errors:
        rows.append({'建立時間': stamp, '日期': day, '影片ID': video.id, '時間': '', '講者原話': '', '畫面股名': '', '代號': '',
                     '把握': '', '說明': ('部分片段失敗：' + '；'.join(errors))[:300], '狀態': '部分失敗', '處理說明': ''})
    print(f'模型：{"、".join(reader.used) or "無"}；寫入 {len(rows)} 列' + ('（試跑，不寫入）' if a.dry_run else ''))
    if ws is not None:
        ws.append_rows([[r.get(h, '') for h in head] for r in rows], value_input_option='RAW')
    if log is not None and new_words:
        vws = log.worksheet(vl.TAB)
        vws.append_rows([[vocab_row(w, stamp, day).get(h, '') for h in vl.HEAD] for w in new_words], value_input_option='RAW')
        print(f'詞庫學習寫入 {len(new_words)} 列')
    return 1 if errors and not items else 0


def vocab_row(w, stamp, day):
    """新股名直接採用（畫面清楚、代號表對得上）；聽錯等雲端複核對原稿。"""
    new = w['類型'] == '新股名'
    return {**w, '建立時間': stamp, '日期': day, '狀態': '已採用' if new else '待核對',
            '說明': '畫面左上角清楚、代號表對得上' if new else '主持人說的音和畫面股名不同，待雲端複核對原稿'}


def load_code_names():
    """{代號: 簡稱}（主流程的上市櫃對照表，含快取）；載不到就回空，不核對。"""
    try:
        # transcript.py 先載入了 pipeline 套件（pipeline.market_holidays），import pipeline 會拿到套件而不是
        # pipeline/pipeline.py，所以直接用檔案路徑載入主流程。
        import importlib.util
        sys.path.insert(0, str(ROOT / 'pipeline'))
        spec = importlib.util.spec_from_file_location('stock_pipeline_main', ROOT / 'pipeline' / 'pipeline.py')
        pl = importlib.util.module_from_spec(spec)
        sys.modules['stock_pipeline_main'] = pl
        spec.loader.exec_module(pl)
        return dict(pl.get_code_map())
    except Exception as exc:                                     # noqa: BLE001
        print(f'（代號表載不到，不核對名稱代號：{type(exc).__name__}）')
        return {}


def known_day_names(tx, day):
    """聽打詞表裡當天會加的股名（操作紀錄、會員持股近兩週）。"""
    try:
        return tx.load_day_vocabulary(tx.open_sheets(), day)
    except Exception as exc:                                     # noqa: BLE001
        print(f'（讀不到近兩週股名：{type(exc).__name__}）')
        return []


if __name__ == '__main__':
    sys.exit(main())
