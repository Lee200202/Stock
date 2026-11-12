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
# 每段讀幾次（2026/11/12 管理者同意兩次一致才算清楚；設 1 回到單次讀取）。
PASSES = max(1, int(os.environ.get('SCREEN_PASSES', '2')))

# 純函式與模型介面拆開，方便離線測試；部署時須一起複製這兩個模組。
from screen_contracts import hms, parse_report, check_codes, consensus, explicit, dedupe, not_listed
from screen_runtime import Reader


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
    items, errors, foreign = [], [], []
    for start, end in plan:
        reads = []
        for n in range(PASSES):
            try:
                got, dropped = parse_report(reader.read(url, start, end, learned_pairs), start, end)
            except RuntimeError as exc:
                print(f'  {hms(start)}–{hms(end)} 第 {n + 1} 次讀取失敗：{exc}')
                continue
            if got is None:
                print(f'  {hms(start)}–{hms(end)} 第 {n + 1} 次回覆不是 JSON 陣列或未完整')
                continue
            why = '、'.join(f'{k} {v} 筆' for k, v in dropped.items())
            print(f'  {hms(start)}–{hms(end)} 第 {n + 1} 次：{len(got)} 個時刻' + (f'（丟掉：{why}）' if why else ''))
            foreign += [it for it in got if not_listed(it, code_names)]
            reads.append(check_codes([it for it in got if not not_listed(it, code_names)], code_names))
        if not reads:
            errors.append(f'{hms(start)}–{hms(end)} 讀取失敗')
            continue
        if PASSES == 1:
            merged = reads[0]
        else:
            merged = consensus(reads[0], reads[1] if len(reads) > 1 else [])
            print(f'  {hms(start)}–{hms(end)} 兩次一致 {sum(it["agreed"] for it in merged)} 處、只有一次 {sum(not it["agreed"] for it in merged)} 處'
                  + ('（第二次失敗，清楚全部降成模糊）' if len(reads) < 2 else ''))
        items.extend(merged)
    items = dedupe(items)
    if foreign:
        print(f'不是台股個股、不列 {len(foreign)} 處：' + '、'.join(sorted({f"{it['time']} {it.get('name') or it.get('heard')}" for it in foreign})))
    stamp = now().strftime('%Y/%m/%d %H:%M:%S')
    for it in items:
        print(f"  {it['time']} {it['confidence']} {it['code']} {it['name']}｜{it.get('heard', '')}｜{it['said']}｜"
              f"{it.get('scene')}/{it.get('target_match')}/{it.get('speech_kind')} {it['note']}"
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
    result_path = Path(os.environ.get('SCREEN_RESULT_PATH', f'scratch/screen-identify/results-{time.time_ns()}.json'))
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps({'date': day, 'video_id': video.id, 'complete': not errors,
                                      'items': items, 'failed_segments': len(errors)}, ensure_ascii=False, indent=2), encoding='utf-8')

    head = LOG_TABS[TAB]
    rows = [{'建立時間': stamp, '日期': day, '影片ID': video.id, '時間': it['time'], '講者原話': it['said'],
             '畫面股名': it['name'], '代號': it['code'], '把握': it['confidence'], '說明': f"scene={it.get('scene')}；topic={it.get('target_match')}；{it['note']}"[:160], '狀態': '待核對', '處理說明': ''}
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
    return 1 if errors else 0  # 部分失敗也必須在 Actions 顯示不完整


def vocab_row(w, stamp, day):
    """新股名：只在畫面左上角清楚、代號表兩欄一致、話題相同時才會學到，直接「已採用」（2026/11/12 管理者要自動學詞；
    它只是把真的股名加進聽打專有名詞，不認定任何一句是哪一檔）。聽錯：等雲端複核對原稿。"""
    new = w['類型'] == '新股名'
    return {**w, '建立時間': stamp, '日期': day, '狀態': '已採用' if new else '待核對',
            '說明': '畫面左上角清楚、代號表兩欄一致、話題相同' if new else '主持人說的音和畫面股名不同，待雲端複核對原稿'}


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
    except (Exception, SystemExit) as exc:                       # noqa: BLE001
        print(f'（代號表載不到，本輪清楚結果全部降為模糊：{type(exc).__name__}）')
        return {}


def known_day_names(tx, day):
    """聽打詞表裡當天會加的股名（操作紀錄、會員持股近兩週）。"""
    try:
        return tx.load_day_vocabulary(tx.open_sheets(), day)
    except (Exception, SystemExit) as exc:                       # noqa: BLE001
        print(f'（讀不到近兩週股名：{type(exc).__name__}）')
        return []


if __name__ == '__main__':
    sys.exit(main())
