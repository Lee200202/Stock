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

SYSTEM = (
    '你在看台股教學直播「張震 股市盤中家教班」的一段回放（主持人邊講邊切換 K 線圖與報價畫面）。'
    '你的工作只有一件：找出「主持人正在談某一檔個股，但口頭沒有清楚說出是哪一檔」的時刻，讀出那時畫面上顯示的股票。'
    '只回報畫面上真的看得到的名稱與代號，不要猜。'
)


def prompt(start, end):
    return (
        f'影片範圍 {hms(start)} 到 {hms(end)}（時間一律從影片開頭算）。\n'
        '要找的說法：\n- ' + '\n- '.join(PATTERNS) + '\n\n'
        '主持人有清楚說出股名或代號、而且聽起來就是那一檔的，不用列。' + EXCLUDE + '\n'
        '每一個時刻回一個物件：\n'
        '{"time":"h:mm:ss（從影片開頭算）","said":"主持人當時那句話（30 字內，盡量照原話）",'
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
                    'name': name, 'code': code if 4 <= len(code) <= 6 else '',
                    'confidence': conf, 'note': str(it.get('note') or '').strip()[:40]})
    return out


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

    def read(self, url, start, end):
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
                            types.Part(text=prompt(start, end))]), config=types.GenerateContentConfig(**config))
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
    print(f'{day} 影片 {video.id}，{hms(video.duration_sec)}，分 {len(plan)} 段看畫面（fps={FPS}）')
    reader = Reader()
    items, errors = [], []
    for start, end in plan:
        try:
            got = parse_items(reader.read(url, start, end), start, end)
        except RuntimeError as exc:
            errors.append(f'{hms(start)}–{hms(end)} {exc}')
            continue
        if got is None:
            errors.append(f'{hms(start)}–{hms(end)} 回覆不是 JSON 陣列')
            continue
        print(f'  {hms(start)}–{hms(end)}：{len(got)} 個時刻')
        items.extend(got)
    items = dedupe(items)
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    for it in items:
        print(f"  {it['time']} {it['confidence']} {it['code']} {it['name']}｜{it['said']}｜{it['note']}")
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
    return 1 if errors and not items else 0


if __name__ == '__main__':
    sys.exit(main())
