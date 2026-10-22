"""GitHub 雲端接棒：只續補預算未走完的範圍，不以完整度旗標無限重跑。"""
import io
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path


def api(path, body=None, raw=False):
    cmd = ['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}{path}']
    if body is not None:
        cmd += ['--method', 'POST', '--input', '-']
    result = subprocess.run(cmd, input=json.dumps(body).encode() if body is not None else None,
                            check=True, capture_output=True)
    return result.stdout if raw else json.loads(result.stdout or b'null')


def parse_progress(text):
    # 相容目前已在執行的版本；這是固定進度格式，不以文字推論資料完整度。
    pattern = re.compile(r'(上市|上櫃)探查 \[[^\]]*\] [\d.]+% (\d+)/(\d+)（尚餘 (\d+)）'
                         r'｜新增封存 (\d+)｜複核/更新 (\d+)｜沿用 (\d+)｜空回應待核 (\d+)'
                         r'｜失敗 (\d+)｜休市略過 (\d+)｜[^\n]*?｜(本輪預算到，待續補|本輪已走訪，非完整核實)')
    latest, saved = {}, 0
    for m in pattern.finditer(text):
        market = m[1]
        state = dict(processed=int(m[2]), total=int(m[3]), remaining=int(m[4]),
                     new=int(m[5]), refreshed=int(m[6]), failed=int(m[9]),
                     phase='paused' if m[11] == '本輪預算到，待續補' else 'scanned')
        latest[market] = state
        saved += state['new'] + state['refreshed']
    return {'markets': latest, 'saved': saved}


def decision(run, progress, previous=None):
    if run.get('status') != 'completed':
        return False, '已有補抓執行或排隊，保留現有工作。'
    if run.get('conclusion') != 'success':
        return False, '前次未成功（含人工取消）；不自動復活或掩蓋錯誤，需查看執行紀錄。'
    markets = progress.get('markets', {})
    if set(markets) != {'上市', '上櫃'}:
        return False, '缺少兩個市場的結束進度，不憑空假定已完成或直接重跑。'
    if any(s['phase'] == 'paused' and s['remaining'] > 0 for s in markets.values()):
        if progress['saved'] == 0 and previous is not None and previous.get('saved') == 0:
            return False, '連續兩次沒有新增或更新封存，暫停接棒並保留缺口；平日排程再核對來源。'
        return True, '本輪時間預算已到，仍有未走訪範圍；由 GitHub 接續增量補抓。'
    if any(s['failed'] for s in markets.values()):
        return False, '範圍已走訪但仍有來源失敗；保留缺口，平日排程重試，不無限立即重跑。'
    return False, '本輪範圍已走訪；找不到或待冷卻複核的資料保留狀態，並非宣稱完整。平日排程後續複核。'


def read_progress(run_id, call=api):
    blob = call(f'/actions/runs/{run_id}/logs', raw=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        # GitHub 同時包含整份工作與分步紀錄，只讀最長的一份避免加總兩次。
        logs = [i for i in z.infolist() if i.filename.endswith('.txt')]
        if not logs:
            raise RuntimeError('找不到補抓工作日誌')
        item = max(logs, key=lambda i: i.file_size)
        if item.file_size > 50_000_000:
            raise RuntimeError('日誌超過讀取預算，保留工作並要求核對')
        return parse_progress(z.read(item).decode('utf-8', 'replace'))


def control(call=api, read=None):
    read = read or (lambda run_id: read_progress(run_id, call))
    runs = call('/actions/workflows/market-history.yml/runs?per_page=100')['workflow_runs']
    # 不取消、不替換 pending；包含別的分支正在進行的同目的工作。
    active = [r for r in runs if r['status'] != 'completed']
    if active:
        return {'action': 'keep', 'reason': '已有執行／佇列，未派出重複工作。', 'runs': [r['id'] for r in active]}
    eligible = [r for r in runs if r['head_branch'] == 'main']
    if not eligible:
        return {'action': 'review', 'reason': '沒有可核對的 main 補抓紀錄。'}
    latest = eligible[0]
    if latest.get('conclusion') != 'success':
        yes, reason = decision(latest, {})
        return {'action': 'review', 'reason': reason, 'run': latest['id']}
    progress = read(latest['id'])
    previous = None
    if progress.get('saved') == 0 and len(eligible) > 1 and eligible[1].get('conclusion') == 'success':
        previous = read(eligible[1]['id'])
    yes, reason = decision(latest, progress, previous)
    result = {'action': 'dispatch' if yes else 'wait', 'reason': reason, 'run': latest['id'], 'progress': progress}
    if yes:
        # 派出前再查一次，縮短與人類／定時工作競爭的視窗。
        fresh = call('/actions/workflows/market-history.yml/runs?per_page=100')['workflow_runs']
        if any(r['status'] != 'completed' for r in fresh) or fresh[0]['id'] != runs[0]['id']:
            return {'action': 'keep', 'reason': '核對期間已有新工作，保留現有佇列。'}
        call('/actions/workflows/market-history.yml/dispatches', {'ref': 'main', 'inputs': {'years': '0', 'minutes': '150'}})
    return result


def main():
    result = control()
    if result['action'] == 'review':
        print('::warning::' + result['reason'])
    Path('continuation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    text = '# 完整歷史日K：GitHub 自動接棒\n\n' + result['reason'] + '\n\n```json\n' + json.dumps(result, ensure_ascii=False, indent=2) + '\n```\n'
    print(text)
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(text)


if __name__ == '__main__':
    main()
