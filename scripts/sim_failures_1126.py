"""2026/11/26 故障模擬（離線，不連 Google、不連 Gemini、不動正式資料）：python -B scripts/sim_failures_1126.py

用正式程式碼的實際函式，配上假的 Google／Gemini 回應，觸發各種故障，記錄系統的反應：
自動恢復、交給下一輪排程重試，或失敗退出（由下一輪排程或管理者處理）。等待時間以假時鐘記錄，不真的等。
主表「檔案太大」換卷另由 check_main_volume.js 與 test_main_volumes.py 驗證，本檔一併呼叫。
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.update({'SPREADSHEET_ID': 'offline-main', 'GEMINI_API_KEY': 'fake-gemini-key-one-000000,fake-gemini-key-two-000000', 'PYTHONUTF8': '1'})
os.environ.pop('ANTHROPIC_API_KEY', None)
os.environ.pop('APPS_SCRIPT_URL', None)
os.environ.pop('ADMIN_KEY', None)

import gspread  # noqa: E402

# 子程序照離線契約入口的作法放佔位值；test_main_volumes 自己設定主表 ID，不給
SUB_ENV = dict(os.environ, SPREADSHEET_ID='offline', GEMINI_API_KEY='offline')
MV_ENV = {k: v for k, v in SUB_ENV.items() if k != 'SPREADSHEET_ID'}

results = []


def record(name, outcome, detail):
    results.append({'情境': name, '結果': outcome, '細節': detail})
    print(f'[{outcome}] {name}：{detail}')


class Clock:
    def __init__(self):
        self.waited = []

    def sleep(self, s):
        self.waited.append(round(float(s), 1))


class Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body
        self.text = json.dumps(body, ensure_ascii=False)
        self.headers = {}

    def json(self):
        return self._body


# ---------- 一、主表「檔案太大」 ----------
for cmd in (['node', 'scripts/check_main_volume.js'], [sys.executable, '-B', 'scripts/test_main_volumes.py']):
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', env=MV_ENV)
    record('主表檔案太大（' + Path(cmd[-1]).name + '）', '自動恢復' if p.returncode == 0 else '未通過',
           (p.stdout.strip().splitlines() or [''])[-1][:120])

# ---------- 二、Sheets 每分鐘配額 429 ----------
import transcript as tx  # noqa: E402

for label, fails in (('短暫 429（兩次後恢復）', 2), ('持續 429', 99)):
    clock = Clock()
    tx.time.sleep = clock.sleep
    n = {'c': 0}

    def call():
        n['c'] += 1
        if n['c'] <= fails:
            raise gspread.exceptions.APIError(Resp(429, {'error': {'code': 429, 'message': 'Quota exceeded for quota metric Write requests per minute per user', 'status': 'RESOURCE_EXHAUSTED'}}))
        return 'ok'
    try:
        out = tx.sheets_retry(call)
        record('Sheets ' + label, '自動恢復', f'第 {n["c"]} 次成功；等待 {clock.waited} 秒（含抖動前 10／30 秒）')
    except RuntimeError as e:
        record('Sheets ' + label, '失敗退出→下一輪', f'嘗試 {n["c"]} 次、共等約 {sum(clock.waited):.0f} 秒後放棄：{str(e)[:60]}；逐字稿下一輪約 25 分鐘、每日資料約 15 分鐘後再跑')

# ---------- 三、Gemini 503／額度用完（每日資料流程 call_gemini） ----------
spec = importlib.util.spec_from_file_location('pl_sim', ROOT / 'pipeline' / 'pipeline.py')
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)
OK = {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}, 'finishReason': 'STOP'}],
      'usageMetadata': {'promptTokenCount': 10, 'candidatesTokenCount': 5, 'totalTokenCount': 15}}
E503 = {'error': {'code': 503, 'message': 'The model is overloaded. Please try again later.', 'status': 'UNAVAILABLE'}}
E429D = {'error': {'code': 429, 'message': 'Quota exceeded for metric generate_content_free_tier_requests, limit: 500 PerDay', 'status': 'RESOURCE_EXHAUSTED'}}


def gemini_case(label, plan):
    clock = Clock()
    pl.time.sleep = clock.sleep
    seq = list(plan)
    keys = []

    def post(url, params=None, **kw):
        keys.append((params or {}).get('key', '')[:19])
        code, body = seq.pop(0) if seq else plan[-1]
        return Resp(code, body)
    pl.requests.post = post
    try:
        pl.call_gemini('系統', '使用者', want_json=True, tag='sim')
        record('Gemini ' + label, '自動恢復', f'送出 {len(keys)} 次，用到金鑰 {sorted(set(keys))}；等待合計 {sum(clock.waited):.0f} 秒')
    except Exception as e:  # noqa: BLE001
        record('Gemini ' + label, '失敗退出→下一輪', f'送出 {len(keys)} 次、等待合計 {sum(clock.waited):.0f} 秒後拋出 {type(e).__name__}：{str(e)[:70]}')


gemini_case('短暫 503（兩次後恢復）', [(503, E503), (503, E503), (200, OK)])
gemini_case('持續 503', [(503, E503)])
gemini_case('每日額度用完', [(429, E429D)])

# ---------- 四、回放還沒好 ----------
p = subprocess.run([sys.executable, '-B', 'scripts/test_transcript_noon_cutoff.py'], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', env=SUB_ENV)
record('YouTube 回放還沒好／直播中', '交給下一輪' if p.returncode == 0 else '未通過',
       '逐字稿退出碼 0（回放還沒好算「沒事做」），11:05 起每 25 分鐘一輪到 15:20；中午截止規則測試' + ('通過' if p.returncode == 0 else '失敗'))

# ---------- 五、GitHub 排程延遲或漏跑 ----------
for t in ('scripts/test_work_completion.py', 'scripts/test_screen_identify.py'):
    p = subprocess.run([sys.executable, '-B', t], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', env=SUB_ENV)
    record('排程延遲或漏跑（' + Path(t).name + '）', '交給下一輪' if p.returncode == 0 else '未通過',
           '每日資料一天 15 輪、逐字稿 10 輪，任何一輪漏跑由下一輪接續；完成後的輪次只讀不重做；畫面辨識失敗有限次延後重試')

# ---------- 六、Apps Script /exec 版本落後（沒有分卷動作） ----------
from pipeline import main_volumes as mv  # noqa: E402
mv._reset_for_tests()
mv._call = lambda *a, **k: {'ok': False, 'error': '備份分頁不在允許範圍'}   # 舊版不認得 main-route
active = mv.active_id('offline-main')
record('Apps Script /exec 版本落後', '照舊運作' if active == 'offline-main' and mv.route()['fallback'] else '未通過',
       '查不到分卷路由就照舊用 SPREADSHEET_ID；刷新網站前的 ping 另核對 build／features，不符就停止並印出要部署哪一版')

# ---------- 七、雲端環境變數缺少 ----------
mv._reset_for_tests()
del mv._call
import importlib  # noqa: E402
importlib.reload(mv)
active = mv.active_id('offline-main')
can_relieve = mv.relieve('This document is too large to continue editing.', 'offline-main')
record('雲端環境缺 APPS_SCRIPT_URL／ADMIN_KEY', '有缺口' if not can_relieve else '照舊運作',
       f'平時照舊用 SPREADSHEET_ID（{"是" if active == "offline-main" else "否"}）；但換卷後讀不到新卷、寫入被拒時也無法請求處理（relieve={can_relieve}）')

# ---------- 八、套用工作流程失敗 ----------
for t in ('scripts/test_apply_robustness_1110.py', 'scripts/test_daily_review_autofix.py'):
    p = subprocess.run([sys.executable, '-B', t], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace', env=SUB_ENV)
    record('套用工作流程失敗（' + Path(t).name + '）', '標記並通知' if p.returncode == 0 else '未通過',
           '套用失敗時把差異標成「修正失敗」、寫進複核紀錄與 12:30 狀態信；每週檢討逐檔補修')

if os.environ.get('SIM_OUT'):   # 要留結果檔時指定路徑；預設不在 repo 裡產生檔案
    Path(os.environ['SIM_OUT']).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
bad = [r for r in results if r['結果'] == '未通過']
print(f'\n共 {len(results)} 項；未通過 {len(bad)} 項；有缺口 {sum(r["結果"] == "有缺口" for r in results)} 項')
sys.exit(1 if bad else 0)
