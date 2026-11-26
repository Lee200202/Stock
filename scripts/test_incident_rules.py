"""2026/11/26 自主事故處理（離線）：python -B scripts/test_incident_rules.py

分類用的是近期實際失敗的錯誤文字；決策涵蓋交給下一輪、延後重跑、每日上限、額度重置、換卷、通知、交雲端、同一錯誤停下、雲端上限；
修正分支守門涵蓋允許／禁止的檔案、缺計畫、改 pipeline 沒重播。另核對 Apps Script 事故卡能畫、舊卡照舊。"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts' / 'incident'))
import rules  # noqa: E402
import fix_gate  # noqa: E402

T = rules.TAIPEI
ok = 0


def check(cond, msg):
    global ok
    assert cond, msg
    ok += 1


# ---------- 分類（實際錯誤文字） ----------
cases = {
    "gspread.exceptions.APIError: APIError: [429]: Quota exceeded for quota metric 'Write requests' and limit 'Write requests per minute per user'": 'sheets_quota',
    'RuntimeError: Google Sheets 連續重試失敗，最後狀態 429': 'sheets_quota',
    'assess-json-1 連續 HTTP 503\nRuntimeError: Gemini 連續重試失敗（assess-json-1）：HTTP 503': 'gemini_overload',
    'RateLimited: Gemini 每日配額用盡（polish）：HTTP 429 PerDay': 'gemini_daily_quota',
    'APIError: [400]: This document is too large to continue editing.': 'capacity',
    '部署後重新開 APPS_SCRIPT_URL?action=ping，確認 features 含 dailyk-safe-chunks。': 'version_lag',
    'SystemExit: 缺少環境變數 SPREADSHEET_ID，請到 GitHub Secrets 或 Variables 補上。': 'config',
    'requests.exceptions.ConnectionError: Max retries exceeded with url': 'network',
    'Error: Found 0 artifact(s)': 'github_infra',
    'Traceback (most recent call last):\n  File "pipeline.py", line 10\nKeyError: \'代號\'': 'code',
}
for text, want in cases.items():
    check(rules.classify(text) == want, f'分類錯：{text[:40]} → {rules.classify(text)}（應為 {want}）')
check(rules.classify('', 'startup_failure') == 'github_infra', 'startup_failure')
check(rules.classify('', 'timed_out') == 'network', 'timed_out')

# 執行紀錄的 name 會是 run-name（例如「逐字稿自動抓取」），一律用檔案路徑認工作
check(rules.PATHS['.github/workflows/transcript.yml'] == 'transcript-gemini', '用檔案路徑認工作')
check(set(rules.PATHS.values()) == set(rules.WATCHED), '路徑對照涵蓋所有監看的工作')
wf = (ROOT / '.github/workflows/incident-triage.yml').read_text(encoding='utf-8')
check(all(n in wf for n in rules.WATCHED), 'workflow_run 清單涵蓋所有監看的工作')
check("group: incident-${{ github.event.workflow_run.id" in wf, '每件事故各自一組，避免同時發生的事故被取消')

# ---------- 指紋：數字與時間不同仍是同一種錯誤 ----------
a = rules.fingerprint('daily-transcript-pipeline', '主流程', "2026-11-26T03:20:01Z KeyError: '代號' at row 1234")
b = rules.fingerprint('daily-transcript-pipeline', '主流程', "2026-11-26T05:05:09Z KeyError: '代號' at row 98")
check(a == b, '指紋應忽略數字與時間')

# ---------- 決策 ----------
morning = datetime(2026, 11, 26, 11, 50, tzinfo=T)
late = datetime(2026, 11, 26, 15, 10, tzinfo=T)
d = rules.decide('gemini_overload', 'daily-transcript-pipeline', morning, [], 'fp', consecutive=1)
check(d['action'] == 'record' and not d['notify'], '早上第一次 503：交給下一輪、不通知')
d = rules.decide('gemini_overload', 'daily-transcript-pipeline', morning, [], 'fp', consecutive=3)
check(d['action'] == 'retry' and d['notify'], '連續 3 次：重跑並通知')
d = rules.decide('sheets_quota', 'daily-transcript-pipeline', late, [], 'fp', consecutive=1)
check(d['action'] == 'retry' and (d['due'] - late).seconds == 20 * 60, '最後一輪也失敗：20 分鐘後重跑')
hist = [{'day': '2026/11/26', 'workflow': 'daily-transcript-pipeline', 'decision': 'retry'}] * 2
d = rules.decide('sheets_quota', 'daily-transcript-pipeline', late, hist, 'fp', consecutive=1)
check(d['action'] == 'notify', '每天自動重跑 2 次後改通知管理者')
d = rules.decide('gemini_daily_quota', 'transcript-gemini', morning, [], 'fp')
check(d['action'] == 'retry' and d['due'].strftime('%H:%M') == '16:05', f"額度用完：太平洋午夜後 5 分鐘（台北 16:05），實得 {d['due']}")
summer = datetime(2026, 7, 1, 11, 0, tzinfo=T)
check(rules.decide('gemini_daily_quota', 'transcript-gemini', summer, [], 'fp')['due'].strftime('%H:%M') == '15:05', '夏令時間 15:05')
check(rules.decide('capacity', 'transcript-gemini', morning, [], 'fp', switched=True)['action'] == 'retry', '已換卷：立刻重跑')
check(rules.decide('capacity', 'transcript-gemini', morning, [], 'fp', switched=False)['action'] == 'notify', '沒換卷：通知')
check(rules.decide('version_lag', 'public-site-pages', morning, [], 'fp')['action'] == 'notify', '版本落後：通知擁有者')
check(rules.decide('config', 'transcript-gemini', morning, [], 'fp')['action'] == 'notify', '設定缺漏：通知')
check(rules.decide('code', 'daily-transcript-pipeline', morning, [], 'fpX')['action'] == 'cloud', '程式錯誤：交雲端')
hist = [{'day': '2026/11/24', 'workflow': 'daily-transcript-pipeline', 'decision': 'cloud', 'fingerprint': 'fpX'}]
check(rules.decide('code', 'daily-transcript-pipeline', morning, hist, 'fpX')['action'] == 'stop', '同一錯誤修過仍失敗：停下')
hist = [{'day': '2026/11/26', 'workflow': 'screen-identify', 'decision': 'cloud', 'fingerprint': 'other'}]
check(rules.decide('code', 'daily-transcript-pipeline', morning, hist, 'fpY')['action'] == 'notify', '每天雲端修正 1 件上限')
hist = [{'day': f'2026/11/2{i}', 'workflow': 'x', 'decision': 'cloud', 'fingerprint': f'f{i}'} for i in (2, 3, 4)]
check(rules.decide('code', 'daily-transcript-pipeline', morning, hist, 'fpZ')['action'] == 'notify', '每週雲端修正 3 件上限')

# ---------- 修正分支守門 ----------
plan = json.dumps({'incident_id': 'INC20261126120000', 'target': 'pipeline', 'cause': 'c', 'fix': 'f', 'repro': 'r',
                   'replay_days': ['2026/10/29'], 'holdout_days': ['2026/10/27', '2026/10/28']})
br = 'claude/incident-20261126-INC20261126120000'
fix_gate.show = lambda ref, path: None
r = fix_gate.check('origin/main', br, ['scripts/foo.py', 'docs/incident/INC20261126120000/plan.json'], plan)
check(r['ok'], f'允許的檔案應通過：{r["problems"]}')
r = fix_gate.check('origin/main', br, ['.github/workflows/daily.yml'], plan)
check(not r['ok'] and any('.github' in p for p in r['problems']), '不准改工作流程')
r = fix_gate.check('origin/main', br, ['scripts/incident/rules.py'], plan)
check(not r['ok'], 'AI 不准改自己的規則與上限')
r = fix_gate.check('origin/main', br, ['scripts/foo.py'], None)
check(not r['ok'] and any('plan.json' in p for p in r['problems']), '缺計畫不通過')
r = fix_gate.check('origin/main', 'claude/whatever', ['scripts/foo.py'], plan)
check(not r['ok'], '分支名稱要對')
noreplay = json.dumps({'incident_id': 'INC20261126120000', 'target': 'pipeline', 'cause': 'c', 'fix': 'f', 'repro': 'r'})
r = fix_gate.check('origin/main', br, ['pipeline/pipeline.py'], noreplay)
check(not r['ok'] and any('重播' in p for p in r['problems']), '改 pipeline 要有重播日與保留日')
r = fix_gate.check('origin/main', br, ['public-site/gas-source/Line.gs'], plan)
check(r['ok'] and r['gas_changed'], '改 Apps Script 原始碼允許，並標記要建新版本')

# ---------- Apps Script 事故卡（node 畫一次） ----------
js = r"""
const fs=require('fs'),vm=require('vm');const ctx={};vm.createContext(ctx);
const src=fs.readFileSync('public-site/gas-source/LineAdmin.gs','utf8');
const pick=n=>{const a=src.indexOf('function '+n+'(');const b=src.indexOf('\nfunction ',a+10);return src.slice(a,b);};
vm.runInContext(`var LINE_C_={muted:'#777',ink:'#111',hero:'#0a5'};var LINE_ADMIN_SCALE_={deepRed:'#9B1C1C',red:'#D0645A',green:'#4A9A6A',deepGreen:'#1E5E3E'};
function fxText_(t,o){return Object.assign({type:'text',text:String(t)},o||{});}function fxBox_(l,c,o){return Object.assign({type:'box',layout:l,contents:c.filter(Boolean)},o||{});}
function fxBtn_(a,s){return {type:'button',action:a,style:s||'primary'};}function fxHeader_(k,t,bg){return {k:k,t:t,bg:bg};}function fxNote_(t){return fxText_(t);}
function lineClip_(s,n){s=String(s);return s.length>n?s.slice(0,n-1)+'…':s;}function lineAdminBubble_(h,b,btn){return {type:'bubble',header:h,body:b,footer:btn};}
var LINE_ADMIN_NOTICE_ICON_={pass:'✓',infra:'⚠',fail:'✗'};var LINE_ADMIN_NOTICE_COLOR_={pass:'#1',infra:'#2',fail:'#3'};`,ctx);
['lineAdminUri_','lineAdminStatusRow_'].forEach(n=>vm.runInContext(pick(n),ctx));
const a=src.indexOf('var LINE_INCIDENT_STATUS_');const b=src.indexOf('/** 五分鐘排程：「管理者通知」');vm.runInContext(src.slice(a,b),ctx);
const card={type:'incident',hour:'2026/11/26 15',items:[{time:'15:10',workflow:'daily-transcript-pipeline',label:'Sheets 讀寫配額',decision:'retry',due:'15:30',why:'當天最後一輪也失敗',runUrl:'https://x/1'},{time:'15:20',workflow:'transcript-gemini',label:'程式錯誤',decision:'cloud',why:'交雲端'}]};
const f=ctx.lineAdminIncidentFlex_(card);console.log(JSON.stringify({type:f.type,alt:f.altText,title:f.contents.header.t,rows:f.contents.body.length,btn:f.contents.footer.length}));
const tick=src.slice(src.indexOf('function lineAdminNoticeTick_('),src.indexOf('/* ---------------- 五分鐘排程'));
console.log(tick.includes("card.type !== 'incident'")&&tick.includes('lineAdminIncidentFlex_(card)'));
"""
p = subprocess.run(['node', '-e', js], cwd=ROOT, capture_output=True, text=True, encoding='utf-8')
check(p.returncode == 0, 'node 畫事故卡失敗：' + p.stderr[-300:])
out = p.stdout.strip().splitlines()
card = json.loads(out[0])
check(card['type'] == 'flex' and card['rows'] == 3 and card['btn'] == 1 and '自動處理 2 件' in card['title'], f'事故卡內容：{card}')
check(out[1] == 'true', '五分鐘排程要認得事故卡')
cfg = (ROOT / 'public-site/gas-source/Config.gs').read_text(encoding='utf-8')
trg = (ROOT / 'scripts/incident/triage.py').read_text(encoding='utf-8')
feat = trg.split("NOTICE_FEATURE = '")[1].split("'")[0]
check(f"'{feat}'" in cfg, 'Apps Script features 要有事故卡旗標，GitHub 端才知道可以推送')
print(f'自主事故處理：分類、決策、上限、守門、事故卡 {ok} 項通過')
