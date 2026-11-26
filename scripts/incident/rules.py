"""自主事故處理：分類與決策規則（2026/11/26 管理者核准方案）。純函式，不連網；離線測試見 scripts/test_incident_rules.py。

GitHub 正式工作失敗 → incident-triage.yml 讀失敗步驟與錯誤紀錄 → 這裡分類 → 決定動作：
  暫時性（Sheets 429、Gemini 503、網路、GitHub 執行環境）：當天後面還有排程就只記錄；當天最後一輪或同一工作連續 3 次失敗，
      20 分鐘後重跑一次；每個工作每天最多自動重跑 2 次，超過就通知管理者。
  Gemini 每日額度用完：太平洋時間午夜（額度重置）後 5 分鐘重跑一次並通知。
  主表容量：換卷機制已處理時立刻重跑；沒有換卷就通知管理者。
  版本落後、設定缺漏：通知管理者（/exec 只有擁有者能切；金鑰由管理者貼）。
  程式錯誤（以上皆非）：交給雲端「事故處理」排程判斷修 pipeline、網站或 Apps Script；
      同一個錯誤指紋修過一次仍失敗，就停下等管理者。
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo('Asia/Taipei')
PACIFIC = ZoneInfo('America/Los_Angeles')

# 會觸發事故處理的正式工作（工作流程 name:）與它們當天的最後一輪排程（台北時間；None＝不是排程型或只跑一次）
WATCHED = {
    'transcript-gemini': '15:20',
    'daily-transcript-pipeline': '15:03',
    'daily-review-apply': None,
    'line-ruling-apply': None,
    'screen-identify': None,
    'public-site-pages': None,
    'sheet-capacity-audit': '17:17',
    'main-volume': None,
    'stock-hourly-cache': None,
    'market-history': None,
    'corp-events': None,
    'cloud-cleanup': None,
    'day-edit-sync': None,
}
RETRY_PER_DAY = 2          # 每個工作每天最多自動重跑幾次
CONSECUTIVE_NOTIFY = 3     # 同一工作連續失敗幾次就重跑／通知（即使後面還有排程）
RETRY_DELAY_MIN = 20
CLOUD_FIX_PER_DAY = 1
CLOUD_FIX_PER_WEEK = 3

LABEL = {
    'capacity': '主表容量', 'sheets_quota': 'Sheets 讀寫配額', 'gemini_overload': 'Gemini 暫時過載',
    'gemini_daily_quota': 'Gemini 每日額度用完', 'network': '網路或服務暫時錯誤', 'github_infra': 'GitHub 執行環境',
    'version_lag': 'Apps Script 版本落後', 'config': '設定或權限缺漏', 'code': '程式錯誤',
}
# 依序比對，先中先贏。只看失敗步驟的錯誤紀錄（GitHub 已遮蔽金鑰）。
PATTERNS = [
    ('capacity', r'too large to continue editing|above the limit of [\d,]+ cells|檔案太大'),
    ('config', r'缺少環境變數|缺少 [A-Z_]{4,}|不是服務帳戶金鑰|雲端環境變數未設定|SpreadsheetNotFound|HTTP 40[34]\)|PermissionError|bridge-not-configured|admin-unauthorized|未授權'),
    ('version_lag', r'features 含|確認 features|method-not-allowed|後台 API 還沒更新|部署後重新開 APPS_SCRIPT_URL|build 不符|版本不符'),
    ('gemini_daily_quota', r'每日配額用盡|PerDay|per day|QuotaExhausted|額度都已用完'),
    ('sheets_quota', r"Quota exceeded for quota metric '(?:Write|Read) requests|Sheets 回傳 429|Google Sheets 連續重試失敗，最後狀態 429"),
    ('gemini_overload', r'HTTP 503|UNAVAILABLE|overloaded|high demand|Gemini 連續重試失敗|ModelOverloaded'),
    ('network', r'ConnectionError|ReadTimeout|ConnectTimeout|RemoteDisconnected|ECONNRESET|HTTP 50[024]\b|Max retries exceeded|Temporary failure in name resolution'),
    ('github_infra', r'The runner has received a shutdown signal|The operation was canceled|startup_failure|Found 0 artifact|No space left on device|Process completed with exit code 143|lost communication with the server'),
]
TRANSIENT = {'sheets_quota', 'gemini_overload', 'network', 'github_infra'}


def classify(log_text: str, conclusion: str = 'failure') -> str:
    text = log_text or ''
    if conclusion == 'startup_failure':
        return 'github_infra'
    for cat, pat in PATTERNS:
        if re.search(pat, text, re.I):
            return cat
    if conclusion == 'timed_out':
        return 'network'
    return 'code'


def fingerprint(workflow: str, step: str, log_text: str) -> str:
    """同一種錯誤的指紋：工作＋失敗步驟＋最後一行錯誤（去掉數字、時間、雜湊），用來判斷「修過一次仍失敗」。"""
    lines = [l.strip() for l in (log_text or '').splitlines() if re.search(r'Error|錯誤|失敗|Exception|Traceback|exit code', l)]
    last = lines[-1] if lines else (log_text or '').strip()[-200:]
    last = re.sub(r'^\S*\d{4}-\d\d-\d\dT[\d:.]+Z\s*', '', last)
    norm = re.sub(r'[0-9a-f]{7,}|\d+', '#', last)[:200]
    return hashlib.sha1(f'{workflow}|{step}|{norm}'.encode('utf-8')).hexdigest()[:10]


def pacific_reset(now: datetime) -> datetime:
    """Gemini 每日額度在太平洋時間午夜重置；回傳下一次重置後 5 分鐘（台北時間）。"""
    p = now.astimezone(PACIFIC)
    nxt = datetime(p.year, p.month, p.day, tzinfo=PACIFIC) + timedelta(days=1, minutes=5)
    return nxt.astimezone(TAIPEI)


def last_run_passed(workflow: str, now: datetime) -> bool:
    """這個工作今天的最後一輪排程是不是已經過了（過了就沒有下一輪可以接手）。"""
    last = WATCHED.get(workflow)
    if not last:
        return True
    h, m = map(int, last.split(':'))
    return now.astimezone(TAIPEI) >= now.astimezone(TAIPEI).replace(hour=h, minute=m, second=0, microsecond=0)


def decide(cat: str, workflow: str, now: datetime, history: list, fp: str, switched: bool = False,
           consecutive: int = 1) -> dict:
    """history：近 7 天的事故紀錄，舊到新，每列 {'day','workflow','decision','fingerprint','category'}。
    consecutive：這個工作到這一次為止連續失敗幾次（由 GitHub 執行紀錄算，中間成功就歸零）。
    回傳 {'action','due','notify','why'}。
    action：record（只記錄，交給下一輪）／retry（due 時重跑失敗的工作）／notify（只通知，等人）／cloud（交雲端事故處理）／stop（停下等管理者）。"""
    today = now.astimezone(TAIPEI).strftime('%Y/%m/%d')
    mine = [h for h in history if h.get('workflow') == workflow and h.get('day') == today]
    retries = sum(1 for h in mine if h.get('decision') == 'retry')
    week_cloud = [h for h in history if h.get('decision') == 'cloud']
    day_cloud = [h for h in week_cloud if h.get('day') == today]
    due = None
    if cat in TRANSIENT:
        if not last_run_passed(workflow, now) and consecutive < CONSECUTIVE_NOTIFY:
            return {'action': 'record', 'due': None, 'notify': False, 'why': '當天後面還有排程，交給下一輪'}
        if retries >= RETRY_PER_DAY:
            return {'action': 'notify', 'due': None, 'notify': True, 'why': f'今天已自動重跑 {retries} 次仍失敗，請管理者查看'}
        due = now + timedelta(minutes=RETRY_DELAY_MIN)
        why = ('當天最後一輪也失敗' if last_run_passed(workflow, now) else f'連續 {consecutive} 次失敗') + f'，{RETRY_DELAY_MIN} 分鐘後重跑一次'
        return {'action': 'retry', 'due': due, 'notify': consecutive >= CONSECUTIVE_NOTIFY or retries >= 1, 'why': why}
    if cat == 'gemini_daily_quota':
        if retries >= RETRY_PER_DAY:
            return {'action': 'notify', 'due': None, 'notify': True, 'why': '額度重置後重跑仍失敗，請管理者查看'}
        due = pacific_reset(now)
        return {'action': 'retry', 'due': due, 'notify': True, 'why': '額度重置（太平洋時間午夜）後重跑一次'}
    if cat == 'capacity':
        if switched and retries < RETRY_PER_DAY:
            return {'action': 'retry', 'due': now, 'notify': True, 'why': '主表已換到新卷，立刻重跑'}
        return {'action': 'notify', 'due': None, 'notify': True, 'why': '主表寫不進去且尚未換卷，請管理者查看'}
    if cat in ('version_lag', 'config'):
        return {'action': 'notify', 'due': None, 'notify': True,
                'why': '需要擁有者處理：' + ('把 /exec 切到最新版本' if cat == 'version_lag' else '補設定、金鑰或分享權限')}
    # 程式錯誤
    if any(h.get('fingerprint') == fp and h.get('decision') == 'cloud' for h in history):
        return {'action': 'stop', 'due': None, 'notify': True, 'why': '同一個錯誤已交雲端修過一次仍失敗，停下等管理者'}
    if len(day_cloud) >= CLOUD_FIX_PER_DAY or len(week_cloud) >= CLOUD_FIX_PER_WEEK:
        return {'action': 'notify', 'due': None, 'notify': True, 'why': '已達雲端自動修正上限（每天 1 件、每週 3 件），請管理者查看'}
    return {'action': 'cloud', 'due': None, 'notify': True, 'why': '程式錯誤，交雲端事故處理判斷要修 pipeline、網站或 Apps Script'}
