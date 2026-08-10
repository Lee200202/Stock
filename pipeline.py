"""
資料蒐集後端主流程（GitHub Actions 執行）

紅線：所有輸出僅能根據影片中明確講述的內容產生，不可自行推論或補完。
金鑰：全部從環境變數讀取，不得寫入程式碼，不得印進 workflow logs。

本版修正三件事：
  1. 頻道 ID 內建預設值（公開資訊），Variables 沒設也能跑
  2. Google Sheets 偶發 503 自動重試
  3. Gemini 靜默截斷 —— 切塊處理 + 關閉 thinking + 檢查 finishReason + 長度守門
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import gspread
import requests
from google.oauth2.service_account import Credentials
from pypinyin import lazy_pinyin
import difflib

TAIPEI = timezone(timedelta(hours=8))
NOT_MENTIONED = "本支影片未說明"

# ---------------------------------------------------------------- #
# 設定
# ---------------------------------------------------------------- #

# 頻道 ID 是公開資訊，寫死當預設值。GitHub Variables 有設就以 Variables 為準。
DEFAULT_CHANNEL_ID = "UCPqyYS3n6yyXL2jygauXpzg"
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() or DEFAULT_CHANNEL_ID

# 標題必須含此關鍵字才視為當日直播。頻道標題格式：2026/07/15(三)張震 股市盤中家教班
TITLE_KEYWORDS = ["盤中家教班"]

# 只處理這個日期（含）以後的影片。頻道 RSS 裡混有 2025 年的宣傳片，一律略過。
MIN_DATE = date(2026, 1, 1)

# 潤飾後長度佔原文的比例門檻。
#   低於 FAIL：研判模型改成摘要而非潤飾，中止。
#   介於 FAIL 與 WARN：印警告但照常寫入。中文逐字稿贅字多時，7 成上下是正常的。
# 真正的「輸出被截斷」由 finishReason == MAX_TOKENS 直接攔截，不靠這個比例判斷。
RATIO_FAIL = 0.45
RATIO_WARN = 0.70

# 一小時直播的逐字稿約 13000 字以上。低於此值印警告，提醒抽查上游是否索引不全。
SHORT_TRANSCRIPT_HINT = 5000

# 股票名稱比對門檻。字面比對優先，過不了才用拼音比對抓同音錯字。
# 實測：四星科 對 事欣科 拼音 0.88，紅傑科 對 宏捷科 拼音 1.00。
# PINYIN_LOOSE 用於首字同音且長度相近的情形，例如 旭準 對 旭隼 只有 0.73，
# 但兩字都念 xu，是很強的訊號。放寬的前提是非個股已經先被剔除。
NAME_CUTOFF = 0.75
PINYIN_CUTOFF = 0.80
PINYIN_LOOSE = 0.68

# 跟全清單裡「最像的那一檔」都低於這個值，代表它根本不是股票名稱，直接刪除，
# 不留成代號待確認。像「高速傳輸」這種產業名詞，人工去看影片也填不出代號。
#
# 實測：高速傳輸 0.44、記憶體 0.40、光通訊 0.40、散熱模組 0.38、PMIC 0.00。
# 而真的是股票的同音錯字，最低是 引細 0.73，離 0.60 還有很大距離。
# 0.40 到 0.60 之間掃過，誤刪真股票都是 0 個。
DELETE_THRESHOLD = 0.60

UNRESOLVED = "代號待確認"
REJECT = "__REJECT__"

GEMINI_MODEL = "gemini-2.5-flash"

# 潤飾切塊大小。逐字稿標點稀疏時靠 CHUNK_HARD 保底。
# 切得越大段數越少、呼叫次數越少，撞每分鐘配額的機會就越低，
# 但單段輸出也越長。7000/9500 是兼顧兩者的設定。
CHUNK_SIZE = 7000
CHUNK_HARD = 9500

# 段與段之間的間隔秒數。免費配額按每分鐘請求數計算，
# 拉開間隔比事後重試有效得多。
POLISH_GAP = 8

# gemini-2.5-flash 輸出上限 65,535 tokens
MAX_OUT = 65535

# Google 試算表單格上限 50,000 字元
SHEET_CELL_LIMIT = 49000

TRANSIENT = (429, 500, 502, 503, 504)


# ------------------------------------------------------------------ #
# 功能標記
#
# 工作流程會在執行前檢查這一行還在不在。存在的意義是把「檔案沒更新」
# 這種狀況變成一句看得懂的話。
#
# 實際踩過的坑：工作流程更新了、pipeline.py 沒更新，於是探測步驟用舊版程式碼
# 執行，舊版在載入時就硬性要求 Gemini 金鑰，而探測步驟刻意沒帶那把金鑰，
# 結果吐出「缺少環境變數 GEMINI_API_KEY」。訊息本身沒有錯，卻把人引導到
# 「去補一個 Secret」這個完全錯誤的方向——真正該做的是把 pipeline.py 更新。
# 有了這個標記，就會直接說「檔案版本不符，請更新」。
# ------------------------------------------------------------------ #
PIPELINE_FEATURES = "preflight,auth-rotation,lazy-gemini-key"


def env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"缺少環境變數 {name}，請到 GitHub Secrets 或 Variables 補上。")
    return v


SPREADSHEET_ID = env("SPREADSHEET_ID")

# Gemini 金鑰改成「用到才檢查」，不在載入時就硬性要求。
#
# 原因：有幾種模式根本不呼叫 Gemini——探測（只查影片清單與試算表）、
# 純修代號（只跑拼音比對）、只刷新網站。把檢查放在載入時，
# 這些模式就必須為了通過檢查而拿到一把它們用不到的金鑰，
# 違反最小權限，也讓探測步驟白白多綁一個 Secret。
# 真正要呼叫時才檢查，缺了照樣會有一模一樣的錯誤訊息，不會靜默出錯。
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()


def require_gemini_key():
    if not GEMINI_API_KEY:
        raise SystemExit("缺少環境變數 GEMINI_API_KEY，請到 GitHub Secrets 或 Variables 補上。")
BACKFILL = os.environ.get("BACKFILL", "false").strip().lower() == "true"
FINAL_ATTEMPT = os.environ.get("FINAL_ATTEMPT", "false").strip().lower() == "true"

# 純修代號模式。只把試算表既有的股票名稱重跑一次拼音比對，
# 不碰 NotebookLM，不呼叫 Gemini，幾十秒就跑完。
REPAIR_CODES = os.environ.get("REPAIR_CODES", "false").strip().lower() == "true"

# 補空白模式。逐一檢視「影片清單」，凡是缺原始或修飾後逐字稿的列，
# 重新抓取、潤飾並重跑擷取，把空白補齊。
FILL_BLANKS = os.environ.get("FILL_BLANKS", "false").strip().lower() == "true"

# 重新分類模式。用試算表已存的「修飾後逐字稿」重跑擷取，
# 把舊資料套用新版規則（例如觀望拆成觀望不碰與觀望注意），
# 並覆蓋該日的操作紀錄、會員持股與每日推播內容。
# 不碰 NotebookLM，所以不需要登入憑證，也不會重抓影片。
RECLASSIFY = os.environ.get("RECLASSIFY", "false").strip().lower() == "true"

# 整頓模式。用已存逐字稿：AI 判定產業並刪除、抽取張震明講的買入價並核對後寫回。
# 不重抓影片、不呼叫 NotebookLM。
RECONCILE = os.environ.get("RECONCILE", "false").strip().lower() == "true"

# 價位說明校對模式。逐列檢查價位說明，修正三類錯誤：
#   1. 方向矛盾（方向是買入，說明卻寫「255 以上全部賣掉」）
#   2. 不是股價的數字（「241億以下」是營收不是股價）
#   3. 概數當精確價（「1400多」不可拿來算報酬）
# 先用規則快篩，只有可疑的列才送 Gemini，所以大多數的列是零成本通過。
# 用已存逐字稿，不重抓影片、不呼叫 NotebookLM。
FIX_PRICES = os.environ.get("FIX_PRICES", "false").strip().lower() == "true"

# YouTube Data API 金鑰。有設定就優先用它取影片清單，
# 因為 RSS（feeds/videos.xml）對 GitHub 機房 IP 會穩定回 404，重試無效。
# 沒設定則退回 RSS，維持本機或非機房環境可用。
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()

# ---------------------------------------------------------------- #
# 輪詢逾時
#
# 這是整套排程能不能在 11:30 開始運作的關鍵。
#
# 11:30 直播還在進行，VOD 尚未生成，NotebookLM 一定索引不到。
# 若像先前那樣一次等 30 分鐘，11:33 那次會一路卡到 12:03，
# concurrency 又把後面每一輪全擋在佇列，等於一整個中午只敲了三次門。
#
# 改成 4 分鐘。索引不到就立刻放棄，讓下一輪接手。
# 回補模式與手動長跑則給足時間。
# ---------------------------------------------------------------- #
POLL_TIMEOUT = 240
FULL_TIMEOUT = 1800
INDEX_TIMEOUT = FULL_TIMEOUT if (BACKFILL or FINAL_ATTEMPT or FILL_BLANKS) else POLL_TIMEOUT

# 整體時間預算。GitHub Actions 單一 job 若跑太久會消耗大量額度，也可能撞上
# job timeout。內部輪詢循環靠這個預算收尾：一個 job 進來後最多敲門這麼久就停，
# 交給下一次 cron 觸發接力。預設 1500 秒（25 分鐘），配合每 30 分鐘一次的 cron，
# 相鄰兩次觸發就能無縫覆蓋 11:30 到 15:00。
RUN_STARTED = time.monotonic()
TIME_BUDGET = int(os.environ.get("TIME_BUDGET_SEC", "1500"))   # 25 分鐘

# 內部輪詢循環：進來後自己每隔幾分鐘敲一次門，而不是靠 GitHub cron 準點觸發多次。
# GitHub 的 cron 是 best-effort，尖峰會大量漏跑，這是輪詢次數遠少於預期的主因。
# 手動補跑（backfill / final / fill_blanks / repair_codes / reclassify / reconcile）
# 不走循環，維持單次執行。
POLL_LOOP = os.environ.get("POLL_LOOP", "true").strip().lower() == "true" and not (
    BACKFILL or FINAL_ATTEMPT)
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SEC", "180"))   # 每 3 分鐘敲一次


# ---------------------------------------------------------------- #
# 「我現在就要看到網站更新」開關
#
# 這裡要先講清楚一件常被搞混的事：整理資料與更新網站是兩件事，在兩個地方。
#
#   上游 GitHub Actions 改的是「試算表」。它把資料整理好、寫進去，就結束了。
#   下游 Apps Script 才是把試算表算成網站看到的樣子（持股追蹤、報酬、圖表）。
#
# 所以只在 GitHub 按 Run workflow，試算表確實會變，但網站畫面不會立刻跟著變，
# 要等下游排程（平日 14:50 那一輪）跑過才會。這就是「為什麼我改完了網站還是舊的」。
#
# REFRESH_SITE 就是用來把這兩段接起來的：GitHub 這邊做完資料整理之後，
# 直接打一通 HTTP 給 Apps Script 的網頁應用程式，要它立刻重算全站，
# 不必等下一個交易日，也不必再自己去 Apps Script 編輯器按一次執行。
#
# 需要兩個 Secret 才會動作，缺一就安靜略過（不影響資料整理本身）：
#   APPS_SCRIPT_URL：Apps Script 部署後的網頁應用程式網址（/exec 結尾）
#   ADMIN_KEY      ：與 Apps Script 指令碼屬性中的 ADMIN_KEY 相同的那組密鑰
# ---------------------------------------------------------------- #
# 探測模式。只判斷「有沒有事情要做」，不碰 NotebookLM、不呼叫 Gemini、
# 不需要登入憑證。工作流程用它決定要不要啟動後面那些昂貴的步驟。
# 後台工單模式。逐字稿已經由管理者貼進試算表，這裡只負責把後面的流程跑完。
#
# 為什麼要搬到這裡跑：Apps Script 單次執行有 6 分鐘上限，而潤飾一份兩萬多字的
# 逐字稿加上擷取、稽核、代號比對、價位校對、撰稿，遠遠超過那個上限。
# 先前用「分段 + 觸發器接力」硬撐，一旦某一棒超時就整個中斷且沒有錯誤訊息，
# 排查非常困難。GitHub Actions 沒有這個限制，而且執行紀錄看得到每一行輸出。
ADMIN_JOB = os.environ.get("ADMIN_JOB", "false").strip().lower() == "true"

PREFLIGHT = os.environ.get("PREFLIGHT", "false").strip().lower() == "true"

# VOD 最早可能出現的台灣時間（小時）。直播約 12:30 到 13:00 結束，
# YouTube 轉檔再十幾分鐘，所以這之前敲門必定空手而回。
# 探測模式用它判斷哪些觸發點是純粹浪費，可以直接跳過。
VOD_EARLIEST_HOUR = int(os.environ.get("VOD_EARLIEST_HOUR", "12"))


# ------------------------------------------------------------------ #
# 登入憑證的續命機制
#
# 這是「為什麼上午失敗、下午又好了」的結構性原因。
#
# NotebookLM 用的是 Google 的 web session cookie。Google 會在使用過程中
# 輪換這些 cookie：每用一次就可能發一組新的回來，用戶端把新的寫回
# storage_state.json，下次用新的。在自己電腦上這個循環是完整的，
# 所以平常用瀏覽器不會突然被登出。
#
# 但在 CI 上這個循環是斷的：storage_state.json 是每次從 Secret 還原出來的，
# 工作結束就連同整台機器一起消失，輪換後的新 cookie 從來沒有被保存。
# 於是每一次執行都拿著「同一份、越來越舊」的 cookie 去敲門。
# Google 對舊 cookie 有一段寬限期，寬限期內時好時壞——這就是為什麼
# 上午兩次失敗、下午卻能成功，而中間你什麼都沒改。等寬限期真的過完，
# 就會變成穩定失敗，那時才需要重新登入。
#
# 解法是把輪換後的 cookie 存回一個跨執行都在的地方。
# 這裡選試算表而不是 GitHub Secret，理由是不必額外申請可以寫入 Secret 的
# 個人存取權杖：這支程式本來就有試算表的寫入權限，不引入新的憑證。
#
# 安全性：cookie 等同於這個 Google 帳號在 NotebookLM 的登入狀態。
# 存放的試算表必須維持私有（只分享給你自己與服務帳號），
# 絕對不要開成「知道連結的人都可以檢視」。
# ------------------------------------------------------------------ #
AUTH_SHEET = "登入憑證"
# 本次執行開始時的憑證指紋。用清單包起來是為了讓巢狀函式也能改到它。
_AUTH_FP = [""]
AUTH_PATHS = [
    os.path.expanduser("~/.notebooklm/storage_state.json"),
    os.path.expanduser("~/.notebooklm/profiles/default/storage_state.json"),
]


def _read_local_auth():
    """讀本機目前的 storage_state。讀不到或不是合法 JSON 就回 None。"""
    for path in AUTH_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("cookies"):
                return d
        except Exception:
            continue
    return None


def _write_local_auth(d: dict):
    for path in AUTH_PATHS:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception as e:
            print(f"  寫入 {path} 失敗：{e}")


def _auth_fingerprint(d) -> str:
    """用 cookie 的名稱與值算一個指紋，用來判斷有沒有被輪換過。"""
    try:
        items = sorted((c.get("name", ""), str(c.get("value", "")))
                       for c in d.get("cookies", []))
        return str(hash(tuple(items)))
    except Exception:
        return ""


def load_saved_auth(ss) -> bool:
    """
    把試算表裡存的最新憑證覆蓋到本機。
    回傳 True 代表用了試算表版本，False 代表沿用 Secret 還原出來的版本。
    """
    try:
        ws = ss.worksheet(AUTH_SHEET)
    except Exception:
        return False   # 還沒建立這張分頁，第一次執行時是正常的
    try:
        raw = str(ws.acell("B2").value or "").strip()
        saved_at = str(ws.acell("B1").value or "").strip()
    except Exception as e:
        print(f"讀取{AUTH_SHEET}失敗（不影響流程）：{e}")
        return False
    if not raw:
        return False
    try:
        d = json.loads(raw)
        if not (isinstance(d, dict) and d.get("cookies")):
            raise ValueError("內容不是合法的 storage_state")
    except Exception as e:
        print(f"{AUTH_SHEET}的內容無法解析（{e}），改用 Secret 的版本")
        return False

    _write_local_auth(d)
    print(f"已套用試算表保存的登入憑證（上次更新 {saved_at or '未知'}）")
    return True


def save_rotated_auth(ss, before_fp: str):
    """
    執行成功後把輪換過的憑證存回試算表。指紋沒變就不寫，避免無謂的寫入。
    """
    d = _read_local_auth()
    if not d:
        return
    if _auth_fingerprint(d) == before_fp:
        return
    try:
        try:
            ws = ss.worksheet(AUTH_SHEET)
        except Exception:
            ws = ss.add_worksheet(title=AUTH_SHEET, rows=10, cols=2)
            ws.update("A1", [["最後更新"], ["憑證內容"], ["說明"]])
            ws.update("B3", [["這是 NotebookLM 的登入狀態，等同帳號登入憑證。"
                              "請維持本試算表私有，不要開放連結分享。"
                              "由程式自動維護，不需手動編輯。"]])
        ws.update("B1", [[datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")]])
        ws.update("B2", [[json.dumps(d, ensure_ascii=False)]])
        print("登入憑證已輪換，新的版本已存回試算表，下次執行會沿用。")
    except Exception as e:
        print(f"保存輪換後的憑證失敗（不影響本次結果）：{e}")


def write_preflight(has_work: str, reason: str):
    """
    把探測結果寫給 GitHub Actions。
    後續步驟用 steps.preflight.outputs.has_work 判斷要不要跑。
    不在 Actions 環境裡（例如本機測試）就只印出來。
    """
    print(f"\n探測結果：has_work={has_work}　（{reason}）")
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"has_work={has_work}\n")
            f.write(f"reason={reason}\n")
    except Exception as e:
        print(f"寫入 GITHUB_OUTPUT 失敗（不影響流程）：{e}")


REFRESH_SITE = os.environ.get("REFRESH_SITE", "false").strip().lower() == "true"
APPS_SCRIPT_URL = os.environ.get("APPS_SCRIPT_URL", "").strip()
ADMIN_KEY = os.environ.get("ADMIN_KEY", "").strip()


def maybe_refresh_site():
    """
    要求 Apps Script 立刻重算全站。只有 REFRESH_SITE=true 時才動作。

    刷新本身可能要一兩分鐘（補日K、重算追蹤、記績效），所以逾時給到 300 秒。
    失敗不視為整體失敗：資料已經寫進試算表了，網站晚一點由排程刷新也會正確，
    所以這裡只印警告，不讓整個 workflow 亮紅燈。
    """
    if not REFRESH_SITE:
        return
    if not APPS_SCRIPT_URL or not ADMIN_KEY:
        # 明確講出「缺哪一個」，不要讓人兩個都去翻。
        missing = []
        if not APPS_SCRIPT_URL:
            missing.append("APPS_SCRIPT_URL")
        if not ADMIN_KEY:
            missing.append("ADMIN_KEY")
        print("\n" + "=" * 60)
        print(f"要求刷新網站，但缺少：{'、'.join(missing)}　本次略過刷新。")
        print("（資料整理本身沒有受影響，已經寫進試算表了。）")
        print("=" * 60)
        print("")
        print("最快的補法：到 Apps Script 編輯器執行 showDeployInfo() 這支函式，")
        print("它會把下面兩個值直接印在執行紀錄裡，複製貼上即可。")
        print("")
        if "APPS_SCRIPT_URL" in missing:
            print("【APPS_SCRIPT_URL】網頁應用程式的部署網址")
            print("  Apps Script → 右上角「部署」→ 管理部署作業 → 複製網頁應用程式網址。")
            print("  長得像 https://script.google.com/macros/s/AKfycb.../exec")
            print("  務必是 /exec 結尾。/dev 結尾那個只有你登入時能開，GitHub 打不進來。")
            print("")
        if "ADMIN_KEY" in missing:
            print("【ADMIN_KEY】管理密鑰，這是你自己訂的字串，不是任何人發給你的")
            print("  Apps Script → 專案設定 → 指令碼屬性，看 ADMIN_KEY 那一列的值。")
            print("  還沒設過的話，執行 showDeployInfo() 會自動幫你產生一組。")
            print("  GitHub 這邊要填「一模一樣」的字串，前後不能多空格。")
            print("")
        print("填的位置：GitHub → Settings → Secrets and variables → Actions")
        print("          → New repository secret，名稱全大寫照打。")
        print("")
        print("設好之後，重跑一次本工作流程並勾選 refresh_site 即可。")
        print("在那之前，想更新網站可以用另外兩種方式：")
        print("  1. 網站技術說明頁最下方，輸入管理密鑰按「立即刷新網站內容」")
        print("  2. Apps Script 編輯器直接執行 refreshSiteNow()")
        print("=" * 60)
        return

    print("\n要求 Apps Script 立刻重算全站（約一到兩分鐘）……")
    try:
        # Apps Script 的 /exec 會 302 轉址到 googleusercontent，
        # requests 預設會跟著轉址，正是我們要的行為。
        resp = requests.get(
            APPS_SCRIPT_URL,
            params={"action": "refresh", "key": ADMIN_KEY},
            timeout=300,
            headers={"User-Agent": "zhangzhen-pipeline"},
        )
        body = resp.text[:600]
        clean = body.replace(" ", "")
        if '"ok":true' in clean:
            print(f"刷新回應（HTTP {resp.status_code}）：{body}")
            print("網站已刷新完成，重新整理頁面即可看到最新內容。")
        elif '"ok":false' in clean:
            # 端點有通、但被拒絕。九成是密鑰對不上。
            print(f"刷新回應（HTTP {resp.status_code}）：{body}")
            print("")
            print("端點有回應，但被拒絕了。最常見的原因是兩邊的 ADMIN_KEY 不一致：")
            print("  Apps Script → 專案設定 → 指令碼屬性，比對 ADMIN_KEY 的值")
            print("  GitHub → Settings → Secrets，確認貼上時前後沒有多空格或換行")
        elif "<html" in body.lower() or "<!doctype" in body.lower():
            # 回傳網頁而不是 JSON。有兩種完全不同的成因，解法也不同，必須分開判斷。
            low = body.lower()
            is_login = ("accounts.google.com" in low or "servicelogin" in low
                        or "signin" in low)
            # Apps Script 自己的錯誤頁。特徵是標題就叫 Error，
            # 而且載入的是 docs/script 的圖示。這代表 doGet 執行時拋了例外——
            # 與「部署版本太舊」是完全不同的問題，處理方式也不一樣，
            # 先前把兩者混在一起判讀，指引就把人帶錯方向。
            is_gas_error = ("<title>error</title>" in low
                            or "docs/script/images/favicon" in low)
            print(f"刷新回應（HTTP {resp.status_code}）：回傳的是 HTML 網頁，不是預期的 JSON。")
            print(f"最終網址：{resp.url}")
            print(f"內容開頭：{body[:200]}")
            print("")
            if is_gas_error:
                print(">>> 這是 Apps Script 的錯誤頁，代表 doGet 執行時拋出例外。")
                print("    程式碼有進去跑，但中途出錯了，不是版本太舊的問題。")
                print("")
                print("    最常見的原因是「授權過期或不足」：")
                print("    專案新增了會用到新服務的程式碼（例如對外連網、建立觸發器、寄信）之後，")
                print("    必須重新授權一次，否則網頁應用程式一執行到那段就會直接拋例外。")
                print("")
                print("    怎麼修：")
                print("    1. 到 Apps Script 編輯器，隨便選一個函式（例如 showDeployInfo）按執行")
                print("    2. 跳出授權視窗就一路允許到底，把新的權限補齊")
                print("    3. 部署 → 管理部署作業 → 編輯 → 版本選「新版本」→ 部署")
                print("    4. 想看確切錯誤：把上面那個網址直接貼到瀏覽器，頁面會顯示例外訊息；")
                print("       或到 Apps Script 左側「執行紀錄」看最近一次 doGet 的失敗原因")
            elif is_login:
                print(">>> 這是 Google 登入頁，代表請求根本沒有進到你的程式碼。")
                print("    成因：部署的「誰可以存取」不是「所有人」。")
                print("    GitHub Actions 沒有 Google 帳號可以登入，會被擋在驗證這一關。")
                print("    修法：管理部署作業 → 編輯 → 誰可以存取改成「所有人」。")
                print("    注意「凡是擁有 Google 帳戶的使用者」也不行，必須是「所有人」。")
            else:
                print(">>> 這是網站本身的頁面，代表請求有進到你的程式，")
                print("    但那份程式碼裡沒有 action=refresh 這個分支。")
                print("    也就是說：部署中的版本還是舊的 Code.gs。")
                print("")
                print("    請依序確認：")
                print("    1. Code.gs 裡真的有 params.action === 'refresh' 這段（搜尋 jsonOut_）")
                print("    2. 管理部署作業 → 編輯（鉛筆）→ 版本選「新版本」→ 部署")
                print("       ※ 用「新增部署作業」會產生另一組網址，舊網址仍指向舊版")
                print("    3. 部署清單若有多筆，確認 APPS_SCRIPT_URL 是你剛才更新的那一筆")
                print("       兩者的 /s/ 後面那串 ID 必須一致")
                print("    4. 執行 showDeployInfo()，用它印出來的網址覆蓋 GitHub Secret")
        else:
            print(f"刷新回應（HTTP {resp.status_code}）：{body}")
            print("回應格式不如預期，請確認部署網址是否為 /exec 結尾。")
    except Exception as e:
        print(f"刷新網站失敗（不影響已寫入的資料）：{e}")
        print("可改用備援方式：到網站的技術說明頁最下方輸入管理密鑰按「立即刷新網站內容」，")
        print("或在 Apps Script 編輯器直接執行 refreshSiteNow()。")


def budget_left() -> float:
    return TIME_BUDGET - (time.monotonic() - RUN_STARTED)


def out_of_budget() -> bool:
    return budget_left() <= 0

# 過了這個時間仍拿不到逐字稿，才判定今天真的沒有影片
GIVE_UP_HOUR = 15


class NotReadyYet(Exception):
    """VOD 還沒好。這不是錯誤，是還沒輪到。工作要顯示綠色。"""
    pass


class RateLimited(Exception):
    """Gemini 配額用盡（HTTP 429）。退避後仍失敗，代表這段時間內配額真的不夠。"""
    pass


class AuthExpired(Exception):
    """
    NotebookLM 登入狀態失效。Google 的 session cookie 有壽命，
    大約數週會過期，也可能因為異地登入被提前作廢。
    這種錯誤重試沒有用，必須換一份新的 storage_state.json。
    """
    pass


AUTH_HINTS = (
    "authentication expired", "authentication invalid", "not authenticated",
    "accounts.google.com", "notebooklm login", "re-authenticate",
    "unauthorized", "401", "403", "sign in", "login required",
    # ---- 以下是 NotebookLM 用戶端實際吐出來的形狀 ----
    # 這個函式原本抓不到它們，於是登入失效被當成一般錯誤，
    # 輪詢迴圈就每 180 秒重敲一次、一路敲到時間預算用完才停，
    # 而每一次都必然失敗。認證過期重試永遠沒有用，要立刻停下來換 cookie。
    #
    # 典型訊息：
    #   RPC CCqFvf returned null result with status code 16 (Unauthenticated).
    #   RPCError rpc_code=16
    #   Token refresh failed: Client error '400 Bad Request' ...
    "unauthenticated",          # 注意與上面的 not authenticated 是不同字串
    "status code 16",
    "rpc_code=16",
    "token refresh failed",
    "invalid_grant",
    "servicelogin",
    "weblitesignin",
    "confirmidentifier",
)


def looks_like_auth_error(e) -> bool:
    m = str(e).lower()
    return any(k in m for k in AUTH_HINTS)


# ---------------------------------------------------------------- #
# 試算表
# ---------------------------------------------------------------- #
def sheets_retry(fn, *args, **kwargs):
    """
    Google Sheets 偶發 503 / 429。429 是每分鐘寫入配額，退避要跨過整個
    一分鐘窗口才有意義，所以最長等到 70 秒，並加抖動避免同時醒來又一起撞。
    """
    last = None
    for i, delay in enumerate((0, 10, 30, 70)):
        if delay:
            time.sleep(delay + random.uniform(0, 3))
        try:
            return fn(*args, **kwargs)
        except gspread.exceptions.APIError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code not in TRANSIENT:
                raise
            last = code
            print(f"Sheets 回傳 {code}，第 {i + 1} 次重試")
    raise RuntimeError(f"Google Sheets 連續重試失敗，最後狀態 {last}")


def open_sheets():
    info = json.loads(env("GOOGLE_SHEETS_SERVICE_ACCOUNT"))
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    return sheets_retry(gc.open_by_key, SPREADSHEET_ID)


def video_rows(ss):
    return sheets_retry(ss.worksheet("影片清單").get_all_records)


def norm_date(v) -> str:
    """把試算表各種日期寫法統一成 yyyy/MM/dd。"""
    s = str(v or "").strip().replace("-", "/")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}" if m else ""


def existing_transcript(ss, video_id, date_str):
    """
    查雲端是否已經有這一天的逐字稿。影片ID 與日期任一對上就算數。
    回傳 (原始逐字稿, 修飾後逐字稿)，沒有則回 ("", "")。
    """
    for row in video_rows(ss):
        same_id = str(row.get("影片ID")) == video_id
        same_date = norm_date(row.get("發布日期")) == date_str
        if same_id or same_date:
            return (str(row.get("原始逐字稿內容") or ""),
                    str(row.get("修飾後逐字稿內容") or ""))
    return "", ""


def existing_dates(ss, sheet_name) -> set:
    """某張表已經有哪些日期的資料。用來避免重複寫入操作紀錄與會員持股。"""
    try:
        rows = sheets_retry(ss.worksheet(sheet_name).get_all_records)
    except Exception:
        return set()
    return {norm_date(r.get("日期")) for r in rows} - {""}


def find_video_row(ss, video_id):
    ws = ss.worksheet("影片清單")
    for idx, row in enumerate(sheets_retry(ws.get_all_records), start=2):
        if str(row.get("影片ID")) == video_id:
            return ws, idx
    return ws, None


def mark_status(ss, video_id, published, title, status, reason=""):
    ws, idx = find_video_row(ss, video_id)
    if idx is None:
        sheets_retry(ws.append_row, [video_id, published, title, status, reason, "", ""])
    else:
        sheets_retry(ws.update, range_name=f"D{idx}:E{idx}", values=[[status, reason]])


def write_status_log(ss, kind: str, detail: str = ""):
    """
    每一輪執行都往「系統狀態」寫一列。Apps Script 讀這張表，
    就能在不接觸 GitHub 的情況下寄出健康狀態信與認證過期告警。
    寫入失敗不可影響主流程。
    """
    try:
        try:
            ws = ss.worksheet("系統狀態")
        except Exception:
            ws = sheets_retry(ss.add_worksheet, title="系統狀態", rows=2000, cols=6)
            sheets_retry(ws.append_row, ["時間", "類別", "說明", "模式", "執行環境"])
        mode = ("補空白" if FILL_BLANKS else "回補" if BACKFILL else
                "修代號" if REPAIR_CODES else "長逾時手動" if FINAL_ATTEMPT else "排程輪詢")
        where = "GitHub Actions" if os.environ.get("GITHUB_ACTIONS") else "本機"
        sheets_retry(ws.append_row, [
            datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S"),
            kind, str(detail)[:800], mode, where,
        ])
    except Exception as e:
        print(f"  （系統狀態寫入失敗，不影響主流程：{e}）")


def cell(text: str) -> str:
    """試算表單格上限保護。超長時明確標示截斷，不靜默吞掉。"""
    text = text or ""
    if len(text) > SHEET_CELL_LIMIT:
        print(f"警告：內容 {len(text)} 字超過試算表單格上限，已截斷")
        return text[:SHEET_CELL_LIMIT] + "\n\n（超過試算表單格上限，已截斷）"
    return text


def write_transcripts(ss, video_id, v1, v2):
    ws, idx = find_video_row(ss, video_id)
    if idx:
        sheets_retry(ws.update, range_name=f"F{idx}:G{idx}", values=[[cell(v1), cell(v2)]])


# ---------------------------------------------------------------- #
# 影片偵測（YouTube 公開 RSS，不需金鑰，不下載影音）
# ---------------------------------------------------------------- #
TITLE_DATE = re.compile(r"(20\d{2})[/\-.](\d{1,2})[/\-.](\d{1,2})")


def date_from_title(title: str, fallback):
    """
    標題日期優先於 RSS published。
    直播的 published 是「排程建立時間」，可能早於實際開播日；
    但標題 2026/07/15(三)張震 股市盤中家教班 一定是當天。
    """
    m = TITLE_DATE.search(title or "")
    if not m:
        return fallback
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TAIPEI).date()
    except ValueError:
        return fallback


def fetch_feed():
    """
    取影片清單。優先走 YouTube Data API，因為 RSS 對 GitHub 機房 IP
    會穩定回 404（不是暫時節流，重試無效）。沒有 API 金鑰才退回 RSS，
    讓本機或非機房環境仍可運作。
    """
    if YOUTUBE_API_KEY:
        try:
            return fetch_feed_api()
        except Exception as e:
            print(f"  YouTube Data API 失敗（{e}），改用 RSS 備援")
    return fetch_feed_rss()


def fetch_feed_api():
    """
    用 uploads 播放清單列出最近上傳。頻道 ID 的 UC 開頭換成 UU 即為
    該頻道的 uploads 播放清單 ID。單次 1 unit 配額，穩定不擋機房 IP。
    """
    if not CHANNEL_ID.startswith("UC"):
        raise RuntimeError(f"頻道 ID {CHANNEL_ID} 非 UC 開頭，無法推出 uploads 播放清單")
    uploads = "UU" + CHANNEL_ID[2:]
    url = "https://www.googleapis.com/youtube/v3/playlistItems"
    params = {"part": "snippet", "maxResults": 25, "playlistId": uploads, "key": YOUTUBE_API_KEY}

    r = requests.get(url, params=params, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}：{r.text[:200]}")

    items = r.json().get("items", [])
    out = []
    for it in items:
        sn = it.get("snippet", {})
        rid = sn.get("resourceId", {})
        vid = rid.get("videoId")
        if not vid:
            continue
        title = (sn.get("title") or "").strip()
        published = sn.get("publishedAt")
        rss_date = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(TAIPEI).date()
        out.append({
            "id": vid,
            "title": title,
            "date": date_from_title(title, rss_date),
            "rss_date": rss_date,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    out.sort(key=lambda v: v["date"], reverse=True)
    print(f"  YouTube Data API 取得 {len(out)} 支影片")
    return out


def fetch_feed_rss():
    """
    RSS 備援。對機房 IP 常被擋，所以帶 User-Agent 並重試。
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
        "Accept": "application/atom+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }

    last = ""
    for attempt, delay in enumerate((0, 4, 12, 30, 60)):
        if delay:
            time.sleep(delay)
        try:
            r = requests.get(url, timeout=30, headers=headers)
        except Exception as e:
            last = str(e)
            print(f"  RSS 第 {attempt + 1} 次連線失敗（{last}），重試")
            continue

        if r.status_code == 200:
            if attempt:
                print(f"  RSS 第 {attempt + 1} 次成功")
            return parse_feed_xml(r.text)

        last = f"HTTP {r.status_code}"
        print(f"  RSS 第 {attempt + 1} 次回 {last}，重試")

    raise RuntimeError(
        f"YouTube RSS 連續 5 次失敗（最後 {last}）。"
        f"這通常是 YouTube 對 GitHub 機房 IP 的暫時性節流，下一輪排程會再試。"
    )


def parse_feed_xml(text):
    root = ET.fromstring(text)
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

    out = []
    for e in root.findall("a:entry", ns):
        vid = e.find("yt:videoId", ns).text
        title = (e.find("a:title", ns).text or "").strip()
        published = e.find("a:published", ns).text
        rss_date = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(TAIPEI).date()
        out.append({
            "id": vid,
            "title": title,
            "date": date_from_title(title, rss_date),
            "rss_date": rss_date,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })
    out.sort(key=lambda v: v["date"], reverse=True)
    return out


def is_target(title: str) -> bool:
    return any(k in (title or "") for k in TITLE_KEYWORDS)


# ---------------------------------------------------------------- #
# 逐字稿：notebooklm-py 來源全文存取
# ---------------------------------------------------------------- #
async def fetch_fulltext(video_url, title, timeout):
    """
    timeout 短的時候（輪詢），索引不完就丟 NotReadyYet，讓下一輪接手。
    索引不完與真的出錯必須分開，不然每一輪都會亮紅燈並發告警。
    """
    from notebooklm import NotebookLMClient

    try:
        client_cm = NotebookLMClient.from_storage()
    except Exception as e:
        if looks_like_auth_error(e):
            raise AuthExpired(str(e)[:300])
        raise

    async with client_cm as client:
        try:
            notebook = await client.notebooks.create(title=title)
        except Exception as e:
            if looks_like_auth_error(e):
                raise AuthExpired(str(e)[:300])
            raise
        try:
            try:
                source = await client.sources.add_url(
                    notebook.id, video_url, wait=True, wait_timeout=timeout
                )
            except NotReadyYet:
                raise
            except Exception as e:
                msg = str(e).lower()
                # 認證失效要先判，否則會被下面的關鍵字誤判成「還沒好」而無限重試
                if looks_like_auth_error(e):
                    raise AuthExpired(str(e)[:300])
                # 逾時、還在處理、佇列中，都代表 VOD 還沒好，不是壞掉
                if any(k in msg for k in ("timeout", "timed out", "processing", "pending", "queue")):
                    raise NotReadyYet(f"NotebookLM 在 {timeout} 秒內尚未完成索引")
                raise

            fulltext = await client.sources.get_fulltext(notebook.id, source.id)
            content = fulltext.content or ""

            # 索引剛開始時可能回傳極短的殘缺內容，這也算還沒好
            if len(content) < 200:
                raise NotReadyYet(f"取回的全文僅 {len(content)} 字，索引尚未完成")

            return content
        finally:
            try:
                await client.notebooks.delete(notebook.id)
            except Exception:
                pass


# ---------------------------------------------------------------- #
# Gemini
# ---------------------------------------------------------------- #
def call_gemini(system_text, user_text, want_json=False, thinking=0, max_out=MAX_OUT, tag=""):
    """
    thinking=0 關閉思考。gemini-2.5-flash 的 thinking 預設開啟，
    且思考 token 計入 maxOutputTokens，是造成輸出被截斷的主因之一。

    finishReason 必須檢查。MAX_TOKENS 時 API 仍回 200 加上半截文字，
    不檢查就會靜默寫入不完整資料。
    """
    require_gemini_key()
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")

    cfg = {
        "temperature": 0.1,
        "maxOutputTokens": max_out,
        "thinkingConfig": {"thinkingBudget": thinking},
    }
    if want_json:
        cfg["responseMimeType"] = "application/json"

    body = {
        "systemInstruction": {"parts": [{"text": system_text}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": cfg,
    }

    last = ""
    # 429 是「這一分鐘打太多」，退避要夠長才有意義。
    # 原本 5/15/40 秒對免費配額太短，常常四次都撞在同一個配額窗口內。
    for attempt, delay in enumerate((0, 12, 30, 75, 150, 240)):
        if delay:
            # 如果等下去就會超過整體時間預算，不如現在就放棄這一段，
            # 讓上層決定降級或收尾，總比等到一半被 GitHub 硬砍好。
            if delay > budget_left() - 10:
                last = last or "HTTP 429"
                print(f"Gemini {tag} 退避 {delay} 秒會超出時間預算，提前放棄本段")
                break
            # 加抖動，避免多個請求在同一秒同時醒來又一起撞牆
            time.sleep(delay + random.uniform(0, 5))

        try:
            r = requests.post(url, json=body, timeout=600)
        except requests.RequestException as e:
            last = f"連線錯誤 {type(e).__name__}"
            print(f"Gemini {tag} {last}，重試中")
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            if r.status_code not in TRANSIENT:
                # 錯誤訊息不含金鑰，也不回傳原始回應內容
                raise RuntimeError(f"Gemini 呼叫失敗（{tag}）：{last}")

            # 伺服器指定的等待秒數優先於我們的表定退避
            wait_hint = 0
            try:
                wait_hint = int(float(r.headers.get("Retry-After", 0)))
            except Exception:
                wait_hint = 0
            if not wait_hint:
                m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', r.text or "")
                if m:
                    wait_hint = int(m.group(1))
            if wait_hint:
                wait_hint = min(wait_hint, 300)
                print(f"Gemini {tag} 回傳 {r.status_code}，伺服器要求等待 {wait_hint} 秒")
                time.sleep(wait_hint + random.uniform(0, 3))
            else:
                print(f"Gemini {tag} 回傳 {r.status_code}，第 {attempt + 1} 次重試")
            continue

        data = r.json()
        cands = data.get("candidates") or []
        if not cands:
            reason = data.get("promptFeedback", {}).get("blockReason", "")
            raise RuntimeError(f"Gemini 未回傳候選（{tag}），blockReason={reason or '無'}")

        cand = cands[0]
        finish = cand.get("finishReason", "STOP")
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts)

        u = data.get("usageMetadata", {})
        print(f"  [{tag}] finish={finish} 輸入={u.get('promptTokenCount')} "
              f"思考={u.get('thoughtsTokenCount', 0)} 輸出={u.get('candidatesTokenCount')} "
              f"文字={len(text)} 字")

        if finish == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini 輸出遭截斷（{tag}）：finishReason=MAX_TOKENS。請調小 CHUNK_SIZE 後重跑。"
            )
        if finish not in ("STOP", "", None):
            raise RuntimeError(f"Gemini 異常結束（{tag}）：finishReason={finish}")
        if not text.strip():
            raise RuntimeError(f"Gemini 回傳空內容（{tag}）")
        return text

    if "429" in last:
        raise RateLimited(f"Gemini 配額用盡（{tag}）：{last}")
    raise RuntimeError(f"Gemini 連續重試失敗（{tag}）：{last}")


def split_transcript(text, size=CHUNK_SIZE, hard=CHUNK_HARD):
    """在句末標點切段。原始逐字稿標點常常稀疏，故加硬上限保底。"""
    seps = "。！？!?\n"
    chunks, cur, n = [], [], 0
    for ch in text:
        cur.append(ch)
        n += 1
        if (n >= size and ch in seps) or n >= hard:
            chunks.append("".join(cur))
            cur, n = [], 0
    tail = "".join(cur)
    if tail.strip():
        chunks.append(tail)
    return chunks or [text]


POLISH_SYSTEM = """你負責整理一段中文直播逐字稿的其中一個片段。

你只能做這三件事：
1. 修正同音錯字。
2. 補上合理的斷句與標點。
3. 刪除純粹的填充詞，僅限「嗯、啊、呃、那個、就是說」這類完全沒有實質意義的字。

除了上述三項，原文的每一句話都必須保留下來，逐句對應輸出。

嚴格禁止：
- 禁止摘要、濃縮、改寫語意。
- 禁止省略任何一句有實質內容的話，即使它重複、離題或聽起來不重要。
- 禁止新增或刪除任何事實資訊。
- 禁止補完語意不清的地方。

若某處聽起來像是股票名稱但拼字有誤，可依常見台股名稱修正，其餘一律照原文保留。

這是長逐字稿的其中一段，可能從句子中間開始或結束，這是正常的，照樣逐句處理即可。
必須處理到片段的最後一個字，不可中途停止。

輸出的長度應該與輸入相近。直接輸出整理後的文字，全文使用繁體中文。
不要加開場白、結語、標題、片段編號或任何說明。"""


# ---------------------------------------------------------------- #
# 股票名稱與代號比對
#
# 這一段的存在理由：語音辨識會把「事欣科」聽成「四星科」。
# 原本把代號交給 Gemini 憑記憶填，模型既記不全上市櫃三千多檔，
# 也無從得知「四星科」根本不是一家公司。所以比對必須在 Python 這邊，
# 拿證交所與櫃買中心的權威清單做，不是靠提示詞拜託模型。
# ---------------------------------------------------------------- #
_CODE_MAP = None

# 上市與上櫃的代號名稱來源，依序嘗試，第一個成功就停。
#
# 為什麼需要備援：櫃買中心的 openapi 路徑改版過，舊網址回傳的是 HTML 錯誤頁，
# 而 .json() 只會吐一句 Expecting value: line 1 column 1，完全看不出端點死了。
# 這正是先前對照表只有 1090 檔（純上市）、所有上櫃股全部對不到的原因。
LISTED_SOURCES = [
    {"label": "證交所 OpenAPI 上市公司基本資料", "kind": "json",
     "url": "https://openapi.twse.com.tw/v1/opendata/t187ap03_L",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
    {"label": "公開資訊觀測站 上市 CSV", "kind": "csv",
     "url": "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
]

OTC_SOURCES = [
    {"label": "公開資訊觀測站 上櫃 CSV", "kind": "csv",
     "url": "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv",
     "code": ["公司代號"], "name": ["公司簡稱", "公司名稱"]},
    {"label": "櫃買 OpenAPI 本益比表", "kind": "json",
     "url": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
     "code": ["SecuritiesCompanyCode", "Code"],
     "name": ["CompanyName", "CompanyAbbreviation", "Name"]},
    {"label": "櫃買 OpenAPI 上櫃公司基本資料", "kind": "json",
     "url": "https://www.tpex.org.tw/openapi/v1/opendata_t187ap03_O",
     "code": ["SecuritiesCompanyCode", "公司代號"],
     "name": ["CompanyAbbreviation", "CompanyName", "公司簡稱"]},
]


def _fetch_rows(src):
    r = requests.get(src["url"], timeout=40,
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")

    text = r.text
    if text.lstrip()[:1] == "<":
        raise RuntimeError("回傳的是網頁不是資料，這個端點多半已改版")

    if src["kind"] == "csv":
        import csv, io
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
    else:
        rows = r.json()

    if not rows:
        raise RuntimeError("回傳 0 筆")
    return rows


def _pick(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _try_sources(sources, market):
    for s in sources:
        try:
            raw = _fetch_rows(s)
            out = {}
            for r in raw:
                c, n = _pick(r, s["code"]), _pick(r, s["name"])
                if re.fullmatch(r"\d{4,6}", c) and n:
                    out[c] = n
            if not out:
                raise RuntimeError("解析後 0 筆，欄位名稱可能改了")
            print(f"  {market}：{s['label']} 成功，{len(out)} 檔")
            return out
        except Exception as e:
            print(f"  {market}：{s['label']} 失敗（{e}），換下一個來源")
    return {}


def get_code_map() -> dict:
    """{代號: 簡稱}，含上市與上櫃。"""
    global _CODE_MAP
    if _CODE_MAP is not None:
        return _CODE_MAP

    print("載入代號對照表")
    m = {}
    listed = _try_sources(LISTED_SOURCES, "上市")
    otc = _try_sources(OTC_SOURCES, "上櫃")
    m.update(listed)
    m.update(otc)

    if not m:
        raise RuntimeError("上市與上櫃的所有來源都失敗，無法進行代號比對。")

    if not listed:
        print("警告：上市清單全部來源都失敗，對照表只有上櫃的部分。")
    if not otc:
        print("警告：上櫃清單全部來源都失敗。上櫃股票將全部無法對到代號。")

    _CODE_MAP = m
    print(f"代號對照表載入 {len(m)} 檔（上市 {len(listed)}，上櫃 {len(otc)}）")
    return m

# ---------------------------------------------------------------- #
# 非個股剔除
#
# 逐字稿裡會出現「台塑集團」「高速傳輸股」「PMIC」這些東西。
# 它們是集團、族群、產業縮寫，不是個股，不該給代號也不該留在操作紀錄裡。
#
# 這一關必須跑在比對之前。實測「台塑集團」對「台積電」拼音相似度 0.70，
# 首字又都是「台」，放寬門檻後會被硬湊成 2330。先剔除才不會製造錯誤資料。
# ---------------------------------------------------------------- #
NON_STOCK_SUFFIX = ("集團", "族群", "概念股", "概念", "類股", "板塊", "產業", "供應鏈", "相關股", "相關")
NON_STOCK_EXACT = {
    "權值股", "中小型股", "傳產", "電子股", "金融股", "航運股", "生技股", "觀光股",
    "大盤", "加權指數", "台股", "美股", "陸股", "日股", "期貨", "選擇權", "ETF",
    "個股", "多方", "空方", "現金", "空手",
    # 產業、技術、材料名詞，不是個股。像「記憶體」曾被寫成代號待確認留在資料裡。
    "記憶體", "面板", "被動元件", "散熱", "重電", "軍工", "無人機", "機器人",
    "矽光子", "光通訊", "高速傳輸", "散熱模組", "伺服器", "半導體", "封測",
    "晶圓代工", "IC設計", "IC 設計", "第三代半導體", "碳化矽", "氮化鎵",
    "銅箔基板", "PCB", "ABF", "CoWoS", "HBM", "AI", "AI伺服器", "AI 伺服器",
    "電動車", "儲能", "太陽能", "風電", "生技", "重電股", "航太", "資安",
    "元宇宙", "低軌衛星", "衛星", "折疊機", "先進封裝", "玻璃基板",
    # 更多材料、零件、載板類產業名詞
    "石英元件", "石英", "AB載板", "ABF載板", "載板", "IC載板", "軟板", "硬板",
    "連接器", "銅箔", "玻纖布", "導線架", "封裝基板", "陶瓷基板", "散熱片",
    "矽晶圓", "砷化鎵", "光阻", "特化", "電源管理", "類比IC", "感測器",
    "光學鏡頭", "鏡頭", "驅動IC", "指紋辨識", "生物辨識", "邊緣運算",
    "矽智財", "IP股", "重電概念", "綠能", "氫能", "核能", "小型核電",
}


# ------------------------------------------------------------------ #
# 外國股票
#
# 張震在盤中常拿美股當台股的風向球講：「輝達昨天漲了」「美光財報好」。
# 這些是行情背景，不是他要會員買賣的標的，而且它們根本不在台股掛牌，
# 寫進操作紀錄只會製造出一批永遠對不到代號的「待確認」列，
# 還會被當成個股顯示在網站上。
#
# 之所以要獨立成一份名單，而不是靠相似度自然淘汰：這些名字都是正常的中文詞，
# 跟某些台股簡稱的拼音很接近（實際發生過美光 0.77、美超微 0.75、輝達 0.73），
# 門檻只要再鬆一點就會被硬湊到不相干的台股上，比留成待確認更糟。
#
# 收錄原則是「台股沒有同名公司」。像三星在台灣有掛牌（5007 三星），
# 所以名單裡放的是三星電子而不是三星；比對時還會再確認一次官方清單，
# 名稱若能精確對上台股就一律以台股為準，不會被這份名單誤殺。
# ------------------------------------------------------------------ #
FOREIGN_STOCKS = {
    # 美股半導體
    "輝達", "英偉達", "輝達ADR", "美光", "美超微", "超微", "英特爾", "高通", "博通",
    "邁威爾", "安謀", "應材", "應用材料", "科林", "科林研發", "泛林", "科磊",
    "德儀", "德州儀器", "亞德諾", "恩智浦", "意法半導體", "英飛凌",
    "格芯", "格羅方德", "西數", "威騰", "希捷", "英睿達", "新思", "益華",
    # 美股科技與其他
    "蘋果", "微軟", "亞馬遜", "谷歌", "谷哥", "字母", "臉書", "特斯拉",
    "奈飛", "網飛", "甲骨文", "思科", "戴爾", "惠普", "帕蘭泰爾", "超微半導體",
    "萬國商業機器", "輝瑞", "莫德納", "禮來", "波音", "迪士尼", "星巴克",
    "麥當勞", "可口可樂", "沃爾瑪", "高盛", "摩根", "摩根大通", "波克夏", "巴菲特",
    # 亞洲與歐洲
    "阿斯麥", "艾司摩爾", "海力士", "SK海力士", "三星電子", "軟銀",
    "東京威力", "東京電子", "鎧俠", "瑞薩", "村田", "京瓷",
}


def is_foreign_stock(name: str, official_names) -> bool:
    """
    是不是外國股票。

    official_names 是台股官方簡稱的集合。名稱若能精確對上台股，
    一律以台股為準，這樣未來若有台股與外國公司同名也不會被誤刪。
    """
    n = str(name or "").strip()
    if not n:
        return False
    if n in official_names:          # 台股有同名公司，以台股為準
        return False
    if n in FOREIGN_STOCKS:
        return True
    # 去掉常見的後綴再比一次：輝達ADR、蘋果公司、特斯拉股價
    stem = re.sub(r"(ADR|公司|股價|集團)$", "", n).strip()
    return bool(stem) and stem != n and stem in FOREIGN_STOCKS


def is_non_stock(name: str):
    """回傳 (是否非個股, 原因)。"""
    n = str(name or "").strip()
    if not n:
        return True, "空白"
    if n in NON_STOCK_EXACT:
        return True, "市場泛稱"
    for s in NON_STOCK_SUFFIX:
        if n.endswith(s):
            return True, f"以「{s}」結尾，是集團或族群不是個股"
    if not re.search(r"[\u4e00-\u9fff]", n):
        return True, "無中文字，是產業縮寫或英文術語"
    if len(n) >= 3 and n.endswith("股"):
        return True, "以「股」結尾，是族群不是個股"
    if len(n) > 8:
        return True, "過長，不像股票簡稱"
    # 台股沒有一個字的官方簡稱。單字幾乎都是語音辨識切錯的碎片，
    # 而單字拿去跟兩三個字的簡稱比拼音，很容易湊出虛假的高分。
    # 實際發生過「秦」被比對成擎亞（0.75）寫進資料。
    if len(n) == 1:
        return True, "只有一個字，不是股票簡稱"
    return False, ""



def _pin(s: str) -> str:
    return "".join(lazy_pinyin(str(s)))


def _norm_pin(p: str) -> str:
    """
    台灣國語音變正規化。這一步是同音錯字比對能不能成立的關鍵。

    台灣人講國語普遍前後鼻音不分（chen 對 cheng、yin 對 ying、xin 對 xing），
    捲舌音也不分（zh 對 z、ch 對 c、sh 對 s）。語音辨識忠實地反映了這個特徵，
    所以「誠美材」會被聽成「陳美」，「英濟」會被聽成「引細」。

    不做這一步的話，chen 對 cheng 的首字比對會判定為不同音，
    放寬門檻不會生效，這些字就永遠對不上。
    """
    p = re.sub(r"([aeiou])ng", r"\1n", p)          # cheng -> chen
    return p.replace("zh", "z").replace("ch", "c").replace("sh", "s")


def _npin(s: str) -> str:
    return _norm_pin(_pin(s))


def _npin1(s: str) -> str:
    """首字的正規化拼音。續 和 旭 都是 xu，誠 和 陳 正規化後都是 cen。"""
    s = str(s or "")
    return _norm_pin(lazy_pinyin(s[:1])[0]) if s else ""


def _base(s: str) -> str:
    """去掉 -KY、*、投控 這類後綴。讓「譜瑞」能對上「譜瑞-KY」。"""
    # 連字號設成可有可無：語音辨識常把「世芯KY」寫成沒有連字號的樣子。
    return re.sub(r"(-?KY|-?DR|\*|投控|控股)$", "", str(s or "")).strip()


def _latin_core(s: str) -> str:
    """
    取出英數字骨架，大寫、去掉連字號與空白。
    AES-KY -> AESKY，aes ky -> AESKY，AESY -> AESY。
    """
    return re.sub(r"[^A-Za-z0-9]", "", str(s or "")).upper()


def _latin_stem(s: str) -> str:
    """再去掉結尾的 KY / DR。AESKY -> AES。"""
    return re.sub(r"(KY|DR)$", "", _latin_core(s))


def _has_cjk(s: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(s or "")))


# 純英文名稱的比對門檻。這一組候選很少（全上市櫃只有個位數檔），
# 誤中的機率極低，所以門檻可以比中文名稱寬一點。
LATIN_CUTOFF = 0.75


def match_latin_stock(name: str, m: dict):
    """
    比對官方簡稱不含中文的個股，例如 AES-KY、IET-KY、TPK-KY。

    為什麼需要獨立一條路：is_non_stock 有一條「無中文字就剔除」的規則，
    用意是擋掉 HBM、CoWoS、AI 這類產業縮寫。但它會連帶把這幾檔
    官方簡稱本來就是英文的股票一起殺掉，實際發生過的例子是
    逐字稿寫成「AESY」，比對階段直接整列剔除。

    做法是只拿「官方簡稱也不含中文」的那幾檔來比，候選集合極小；
    同時比完整骨架與去掉 KY 後的字根，取高者。
    AESY 對 AESKY 是 0.89，對 AES 是 0.86，兩邊都過得了門檻。

    回傳 (代號, 名稱, 相似度) 或 None。
    """
    lat = _latin_core(name)
    if len(lat) < 2:
        return None
    best_c, best_s = None, 0.0
    for c, n in m.items():
        if _has_cjk(n):
            continue
        nl = _latin_core(n)
        if not nl:
            continue
        s = max(difflib.SequenceMatcher(None, lat, nl).ratio(),
                difflib.SequenceMatcher(None, _latin_stem(lat), _latin_stem(nl)).ratio())
        if s > best_s:
            best_c, best_s = c, s
    if best_c and best_s >= LATIN_CUTOFF:
        return best_c, m[best_c], best_s
    return None


def resolve_code(name: str, hint: str):
    """
    回傳 (代號, 名稱, 方式)。
      REJECT      不是個股，呼叫端整列剔除
      UNRESOLVED  是個股但對不上，保留並標示待確認
    絕不亂猜。
    """
    name = str(name or "").strip()
    hint = str(hint or "").strip()

    m = get_code_map()

    # 0. 代號格式合法就直接採用，不再往下模糊比對。
    #
    #    這一條是硬規則。先前對照表因為上櫃清單掛掉而殘缺，
    #    「美琪瑪（4721）」這種本來就正確的資料，因為 4721 不在殘缺的表裡，
    #    就一路掉進模糊比對，被改成「美利達 9914」。把對的改成錯的，
    #    比對不到還糟。四位數代號是硬證據，優先於任何名稱推測。
    if re.fullmatch(r"\d{4,6}", hint):
        if hint in m:
            return hint, m[hint], "代號直接命中"
        return hint, name, "代號格式合法，直接採用（不在對照表，可能是新上市或清單未更新）"

    # 1. 純英文名稱的個股，必須在 is_non_stock 之前處理。
    #    那裡有一條「無中文字就剔除」的規則用來擋 HBM、CoWoS 這類產業縮寫，
    #    但會連帶殺掉 AES-KY、IET-KY、TPK-KY 這幾檔官方簡稱本來就是英文的股票。
    #    先在這裡比一次，比中了就直接採用；比不中再往下走原本的剔除邏輯，
    #    所以真正的產業縮寫仍然會被擋掉，不會因為這一步而放行。
    if not _has_cjk(name):
        hit = match_latin_stock(name, m)
        if hit:
            c, n, s = hit
            return c, n, f"英文名稱相似 {s:.2f}"

    # 2. 外國股票剔除。要排在相似度比對之前，否則「美光」「輝達」這種
    #    正常中文詞會被硬湊到拼音相近的台股上，比留成待確認更糟。
    #    傳入官方簡稱集合，名稱能精確對上台股時以台股為準，不會誤殺。
    if is_foreign_stock(name, set(m.values())):
        return REJECT, name, "剔除：外國股票，不在台股掛牌"

    # 3. 非個股先剔除。必須在比對之前，否則「台塑集團」會被硬湊成「台積電」。
    bad, why = is_non_stock(name)
    if bad:
        return REJECT, name, f"剔除：{why}"

    # 2. 名稱完全相同
    for c, n in m.items():
        if n == name:
            return c, n, "名稱完全相同"

    # 3. 去後綴後相同。譜瑞 對 譜瑞-KY。
    nb = _base(name)
    if nb:
        for c, n in m.items():
            if _base(n) == nb:
                return c, n, "去後綴後相同"

    # 4. 字面相似。原名與去後綴版各比一次，取高者。
    best_c, best_s = None, 0.0
    for c, n in m.items():
        s = max(difflib.SequenceMatcher(None, name, n).ratio(),
                difflib.SequenceMatcher(None, nb, _base(n)).ratio())
        if s > best_s:
            best_c, best_s = c, s
    if best_s >= NAME_CUTOFF:
        return best_c, m[best_c], f"字面相似 {best_s:.2f}"

    # 5. 拼音相似。首字「同音」時放寬門檻。
    #    比的是正規化後的拼音：續 對 旭 都是 xu，誠 對 陳 正規化後都是 cen。
    #    台灣國語前後鼻音與捲舌音不分，不正規化的話這些永遠對不上。
    nk, nk1 = _npin(name), _npin1(name)
    cands = []
    best_p = 0.0
    for c, n in m.items():
        s = max(difflib.SequenceMatcher(None, nk, _npin(n)).ratio(),
                difflib.SequenceMatcher(None, _npin(nb), _npin(_base(n))).ratio())
        if s > best_p:
            best_p = s
        loose = (nk1 and nk1 == _npin1(n) and abs(len(name) - len(n)) <= 2)
        if s >= (PINYIN_LOOSE if loose else PINYIN_CUTOFF):
            cands.append((s, c, loose))
    if cands:
        cands.sort(reverse=True)
        s, c, loose = cands[0]
        return c, m[c], "拼音相似 %.2f%s" % (s, "，首字同音" if loose else "")

    # 6. 跟全清單裡最像的那一檔都低於門檻，代表它根本不是股票名稱。
    #    「高速傳輸」「記憶體」「光通訊」這種產業名詞落在這裡。
    #    留成待確認沒有意義，人工去看影片也填不出代號，直接刪。
    top = max(best_s, best_p)
    if top < DELETE_THRESHOLD:
        return REJECT, name, f"剔除：與所有上市櫃名稱都不像（最高 {top:.2f}），研判不是個股"

    return UNRESOLVED, name, f"無法確定（最高相似 {top:.2f}）"


def resolve_signals(signals: dict) -> dict:
    """每一筆都跑代號比對。判定為非個股的整筆剔除，不寫進試算表。"""
    stat = {"命中": 0, "修正": 0, "待確認": 0, "剔除": 0}

    for key in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        kept = []
        for r in signals.get(key, []):
            raw = str(r.get("name", "")).strip()
            code, fixed, how = resolve_code(raw, r.get("code", ""))

            if code == REJECT:
                stat["剔除"] += 1
                print(f"  代號比對　{raw} -> 剔除（{how.replace('剔除：', '')}）")
                continue

            r["code"] = code
            if code == UNRESOLVED:
                stat["待確認"] += 1
                print(f"  代號比對　{raw} -> 待確認（{how}）")
            elif fixed != raw:
                stat["修正"] += 1
                r["name"] = fixed
                r["原始語音名稱"] = raw
                print(f"  代號比對　{raw} -> {code} {fixed}（{how}）")
            else:
                stat["命中"] += 1
            kept.append(r)
        signals[key] = kept

    print(f"代號比對結果：命中 {stat['命中']}，同音修正 {stat['修正']}，"
          f"待確認 {stat['待確認']}，剔除非個股 {stat['剔除']}")
    return signals


POLISH_SYSTEM = """你負責整理一段中文直播逐字稿的其中一個片段。

你只能做這三件事：
1. 修正同音錯字。
2. 補上合理的斷句與標點。
3. 刪除純粹的填充詞，僅限「嗯、啊、呃、那個、就是說」這類完全沒有實質意義的字。

除了上述三項，原文的每一句話都必須保留下來，逐句對應輸出。

嚴格禁止：
- 禁止摘要、濃縮、改寫語意。
- 禁止省略任何一句有實質內容的話，即使它重複、離題或聽起來不重要。
- 禁止新增或刪除任何事實資訊。
- 禁止補完語意不清的地方。

若某處聽起來像是股票名稱但拼字有誤，可依常見台股名稱修正，其餘一律照原文保留。

這是長逐字稿的其中一段，可能從句子中間開始或結束，這是正常的，照樣逐句處理即可。
必須處理到片段的最後一個字，不可中途停止。

輸出的長度應該與輸入相近。直接輸出整理後的文字，全文使用繁體中文。
不要加開場白、結語、標題、片段編號或任何說明。"""


POLISH_DEGRADED = 0     # 本輪有幾段因配額不足而改用原文


def polish(transcript: str) -> str:
    """
    逐段潤飾。某一段撞到配額上限時，改用原文那一段繼續，不讓整輪失敗。
    理由：內容完整度（後續擷取靠它）比可讀性重要，而且整輪失敗會連already
    拿到的逐字稿都寫不進去，下一輪又要重抓一次，反而更容易再撞配額。
    """
    global POLISH_DEGRADED
    POLISH_DEGRADED = 0

    chunks = split_transcript(transcript)
    print(f"逐字稿 {len(transcript)} 字，切成 {len(chunks)} 段送出潤飾")

    out = []
    for i, c in enumerate(chunks, 1):
        try:
            r = call_gemini(POLISH_SYSTEM, c, thinking=0, tag=f"polish {i}/{len(chunks)}")
            cr = len(r) / max(len(c), 1)
            flag = "" if cr >= RATIO_WARN else "  ← 這段壓縮偏多"
            print(f"潤飾第 {i}/{len(chunks)} 段：{len(c)} → {len(r)} 字（{cr:.0%}）{flag}")
            out.append(r)
        except RateLimited as e:
            POLISH_DEGRADED += 1
            print(f"潤飾第 {i}/{len(chunks)} 段配額不足，改用原文保留內容（{e}）")
            out.append(c)
        # 段間節流。免費配額是每分鐘計次，段與段之間拉開就少撞牆。
        time.sleep(POLISH_GAP)

    if POLISH_DEGRADED:
        print(f"注意：本次有 {POLISH_DEGRADED}/{len(chunks)} 段未潤飾，"
              f"內容完整但可讀性較差。稍後可用 fill_blanks 或 backfill 重跑改善。")

    joined = "\n".join(out)
    ratio = len(joined) / max(len(transcript), 1)
    print(f"潤飾完成：{len(transcript)} → {len(joined)} 字（{ratio:.0%}）")

    # 輸出被截斷已由 finishReason == MAX_TOKENS 攔截。
    # 這裡只防「模型改成摘要」，門檻放寬，避免對贅字多的短片誤判。
    if ratio < RATIO_FAIL:
        raise RuntimeError(
            f"潤飾後長度僅原文的 {ratio:.0%}，低於 {RATIO_FAIL:.0%} 下限，"
            f"研判模型改成了摘要而非逐句潤飾，中止以免寫入不完整資料。"
        )
    if ratio < RATIO_WARN:
        print(f"警告：潤飾後長度為原文的 {ratio:.0%}。逐字稿贅字多時這是正常的，"
              f"但請抽查試算表的「修飾後逐字稿內容」是否有整段消失。")
    return joined


EXTRACT_SYSTEM = """你從一段完整的直播逐字稿中，擷取講者「明確講出」的操作紀錄。

嚴格禁止：
1. 禁止創造逐字稿中沒有提到的股票名稱、價位、操作紀錄或會員持股。
2. 禁止引用其他日期或其他來源的內容。
3. 禁止產出含糊語句，例如可能、應該、大約。
4. 某一類若逐字稿中完全沒有提到，該陣列回傳空陣列，不要編造。

分類定義：
- buy：影片中明講「今天」執行的買入。
- sell：影片中明講「今天」執行的賣出。
- watch_avoid（觀望不碰）：講者的結論是「現在不要進場」。
  包含不要碰、不要買、不用買了、不建議進場、追高風險、已經漲上去了、
  漲太多、來不及了、會整理一段時間、急彈不要追、轉弱、破線、套牢、避開、
  先出場觀察。只要結論是現在別買，一律歸這一類。
- watch_watch（觀望注意）：講者的結論是「現在或回檔後值得留意」。
  包含看好、留意、追蹤、等回檔進場、跌到某價位可以買、有機會、可以觀察。
- holdings：明確說「會員目前持有」或語意明顯等同的股票。

觀望兩類怎麼分，只看一件事：講者對「現在進場」的態度。

最重要的判斷規則：以整段話的結論為準，不要被前半句的鋪陳帶走。
講者很常先講這檔公司多好、適合什麼人，最後才說現在不要買。
那種情況結論是不要買，一律歸 watch_avoid。實際判錯過的兩個例子：
  「適合退休、想穩定賺錢的投資人，現在已漲上去，不用買了」→ 結論是不用買 → watch_avoid
  「作為範本，急跌後急彈不要買，會整理兩個月」→ 結論是不要買 → watch_avoid
兩句的前半段都是正面的，但結論都是現在別進場。

只要出現「不要買」「不用買」「別追」「來不及」「漲上去了」這類字眼，
不論前面講得多正面，一律 watch_avoid。
真正看不出結論、完全中性時才歸到 watch_watch。

召回要求（很重要）：
- 逐字稿是完整一小時內容。請從頭掃到尾，逐段檢視，中段與後段和開頭一樣重要，不可只看開頭。
- 只要講者「指名到一檔上市櫃個股」，不論在哪個段落、用什麼語氣，都要收進來，歸到最貼近的分類。
- 講者常先談族群再點名個股。例如先說「航運」再說「長榮、陽明」，此時長榮、陽明是個股，一定要收；只有在「完全沒有指名任何一檔」時才可以略過該族群。
- 同一檔在不同段落被多次提到，以最能代表當天結論的那次為準，只收一筆，reason 可綜合。

name 欄位請填逐字稿裡實際聽到的名稱，即使你覺得可能是同音錯字也照填，不要自行更正。
code 欄位若逐字稿中講者有明講代號就填，沒有就留空字串。
不要自己回想或推測代號，比對官方清單是後續程式的工作。

只擷取「在台灣上市或上櫃的單一公司」。以下這些都不要放進來：
- 外國公司：輝達、美光、美超微、超微、英特爾、高通、博通、蘋果、微軟、特斯拉、
  亞馬遜、谷歌、阿斯麥、三星電子、海力士、應材、東京威力等。
  講者很常拿美股當台股的風向球講，例如「輝達昨天大漲，今天台積電應該會強」。
  這種是行情背景，不是他要會員操作的標的，而且它們不在台股掛牌，一律不收。
  唯一例外：同一句話裡若因此指名了某檔台股，收那檔台股，不收外國公司。
- 集團或控股：台塑集團、鴻海集團、遠東集團
- 族群或概念：高速傳輸股、AI概念股、權值股、航運股、記憶體族群
- 產業或技術縮寫：PMIC、ABF、CoWoS、HBM、光通訊
- 指數與市場泛稱：大盤、加權指數、台股、美股、期貨、選擇權
- 只有一個字的名稱：台股沒有單字簡稱，那是聽錯的碎片，不要當成股票

price 或 reason 若逐字稿未提及，填「未說明」。

只回傳 JSON，不要有其他文字：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":""}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch_avoid":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch_watch":  [{"name":"", "code":"", "price":"", "reason":""}],
  "holdings": [{"name":"", "code":"", "stance":"", "note":""}]
}"""


AUDIT_SYSTEM = """你是擷取完整性稽核員。給你「完整逐字稿」與「已擷取的操作紀錄 JSON」。
你的任務：找出逐字稿中「有明確指名、但已擷取 JSON 漏掉」的個股，只補漏，不重列。

判斷規則與 EXTRACT 相同：
- 只收單一上市櫃個股。純族群、概念、指數、集團若沒有指名個股，不算漏。
- 外國公司（輝達、美光、美超微、超微、英特爾、蘋果、特斯拉、阿斯麥、三星電子等）
  不在台股掛牌，本來就不該收，沒有出現在結構化紀錄裡不算漏，不要補。
- buy/sell 限「今天」明講執行的買賣；未買賣但被點名的個股，語氣偏空歸 watch_avoid（觀望不碰），
  語氣偏多或中性歸 watch_watch（觀望注意）；holdings 限明講會員持有。
- 名稱照逐字稿實際講的填，不更正、不猜代號。
- 已經在 JSON 裡的個股（以名稱或代號比對）不要再列。

只回傳與 EXTRACT 相同結構的 JSON，內容「只包含漏掉的項目」；沒有漏就四個陣列全空：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":""}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch_avoid":  [{"name":"", "code":"", "price":"", "reason":""}],
  "watch_watch":  [{"name":"", "code":"", "price":"", "reason":""}],
  "holdings": [{"name":"", "code":"", "stance":"", "note":""}]
}"""


INDUSTRY_JUDGE_SYSTEM = """你要判斷一串名稱，每一個到底是「單一上市櫃個股」，還是「產業、族群、概念、集團、技術或材料名詞」。

判斷原則：
- 個股：一家可以在台股掛牌交易的具體公司，例如台積電、聯發科、群聯、南亞科。
- 非個股：產業或族群（記憶體、面板、航運股、AI 伺服器）、技術或材料（ABF、CoWoS、HBM、矽光子）、
  集團（台塑集團、遠東集團）、市場泛稱（大盤、權值股）、英文技術縮寫。
- 拿不準時，若這個詞比較像「一整類公司的統稱」而不是「某一家公司」，就判非個股。

只回傳 JSON，鍵是原始名稱，值是 "stock" 或 "industry"，不要多餘文字：
{"名稱A": "stock", "名稱B": "industry"}"""


ENTRY_PRICE_SYSTEM = """你要從逐字稿中找出「張震針對某一檔股票，明確說出的買入或賣出價位」，
以便當作這一檔的進場價（或出場價）。

規則：
1. 只找他明講、與這一檔股票直接相關的操作價位：買入價、進場價、成本價、承接價、
   掛單買到的價；或賣出價、出場價、獲利了結價。
   不是目標價、不是壓力支撐、不是預期價、不是別人的成本、不是財報數字、不是指數點位。
2. 「XX 以上買入」「XX 以下承接」「跌到 XX 買」這類帶條件的說法，
   取那個門檻數字當價位，並在 note 標明條件（例如「45 以上買入」「跌到 88 承接」）。
3. 若同一檔在不同段落講了不同價位，取「最能代表實際進場/出場成本」的那一個；
   若他後來才補講當初的買入價，以那個明確數字為準。
4. 價位必須是他真的講出來的數字，不可推估、不可從漲跌幅回推。
5. 找不到明確操作價位就回 price=null，不要編。
6. 逐字稿可能有同音或辨識錯誤，數字若明顯不合理（與其他段落差十倍、超出常見股價範圍）回 null。
7. 判斷這個價位是買入還是賣出：買入相關回 side="buy"，賣出相關回 side="sell"，不確定回 side="buy"。

輸入會給你一檔股票的名稱，以及逐字稿中提到它的相關段落。
只回傳 JSON，不要多餘文字：
{"price": 數字或 null, "side": "buy" 或 "sell", "note": "來源說明（例如：張震在 45 以上買入）", "quote": "你依據的那一句原話（20字內）"}"""


PRICE_FIX_SYSTEM = """你是財經資料的校對員。給你「一檔股票在某一天的一筆紀錄」，
包含：股票名稱、日期、操作方向、目前的價位說明、當天的股價區間、以及逐字稿中的相關段落。

你的任務：判斷目前這句「價位說明」對不對，不對就改對。你不是在做摘要，是在做校對。

═══ 第一原則：價位說明必須與操作方向一致 ═══

價位說明描述的動作，必須跟「操作方向」欄講的是同一件事。這是最常見也最嚴重的錯誤。

實際發生過的錯誤：
  方向＝買入，價位說明＝「張震發訊息給會員，告知 255 以上鴻海全部賣掉」
  這句話描述的是賣出，卻掛在買入那一列。兩者矛盾，必錯其一。

遇到方向與內容矛盾時，一律以「方向」欄為準，因為方向是另外獨立判定的，可信度較高。
請回頭在逐字稿裡找「符合該方向」的價位：
  方向是買入 → 只找買進、承接、進場、掛單買到的價位。
  方向是賣出 → 只找賣出、出場、獲利了結、停損的價位。
  方向是觀望不碰 → 只找他說「跌破什麼價才會考慮」「什麼價以下才有機會」這類觀察價。
  方向是觀望注意 → 只找他說「等回到什麼價位」「什麼價以上可以留意」這類觀察價。
找不到符合該方向的價位，就回 price=null、note="未說明"。
絕對不可以把賣出的價位寫進買入那一列，寧可留「未說明」。

═══ 第二原則：數字必須真的是這一檔的股價 ═══

實際發生過的錯誤：
  鴻海，價位說明＝「241億以下」。241 億是營收或市值，不是股價，鴻海不在這個價位。

下列數字一律不是股價，遇到就回 price=null：
  金額單位：億、兆、萬元、千萬（營收、市值、成交金額）
  數量單位：張、萬張、口、股（成交量、持股數）
  財報數字：EPS、每股盈餘、毛利率、營益率、本益比、殖利率、年增率、月增率
  指數點位：大盤、加權指數、費半、道瓊、那斯達克，以及任何「點」結尾的數字
  百分比：漲跌幅、報酬率
  年份、日期、時間、電話、代號

我會提供「當天的股價區間」（最高、最低）。這是硬性驗證：
  數字落在區間內 → 通過。
  數字落在區間外但相差在三成以內 → 可能是他講的是條件價（例如「255 以上才賣」），
    這種可以保留，但務必在 note 裡寫清楚那是條件價不是成交價。
  數字與區間差距超過三成（例如區間 160-165，卻講 241）→ 這一定不是股價，回 price=null。
若我沒有提供區間，就用常識判斷：台股個股股價幾乎都在 5 到 2000 元之間。

═══ 第三原則：模糊數字不可以當成精確價 ═══

實際發生過的錯誤：
  價位說明＝「1400多」。這是概數，不是成交價，拿它算報酬會算出看起來精確但其實是編的數字。

「1400多」「兩百出頭」「五百左右」「大概 90」「90 上下」「120 到 125 之間」這類：
  price 一律回 null（不可以自作主張取 1400、也不可以取中間值 122.5）。
  但 note 要保留他原本的講法，寫成「約 1400 多（概數，非成交價）」。
  這樣讀者知道他講過大概的價位，程式也不會誤把概數當成本。

「255 以上」「88 以下」這種帶門檻的說法不算模糊，那是明確的門檻數字，
price 就填那個門檻，note 寫明條件，例如「255 以上全部賣出（條件價）」。

═══ 輸出格式 ═══

note 要寫成一句人看得懂的短句，25 個字以內，開頭直接講動作與價位，不要贅字。
好的 note：「明講在 168 買入」「255 以上全部賣出（條件價）」「約 1400 多（概數，非成交價）」
不好的 note：「張震在影片中有提到說他大概是在 168 這個價位附近買進的」（太長太囉嗦）

沒有任何可用價位時：price=null、note="未說明"。這是完全可以接受的答案，
留「未說明」永遠好過填一個編出來的數字。

changed 欄位：你有改動原本的價位說明就填 true，判定原本就是對的、不需要改就填 false。
reason 欄位：一句話說明你為什麼這樣判（例如「原說明描述賣出但方向是買入，已改抓買入價」）。

只回傳 JSON，不要有其他文字：
{"price": 數字或 null, "note": "修正後的價位說明", "changed": true 或 false, "reason": "判斷理由（30字內）", "quote": "你依據的那一句原話（20字內）"}"""


ARTICLE_SYSTEM = """你是一位專業財經記者與投顧整理編輯，負責撰寫「張震 股市盤中家教班」
每日影音內容的文字稿，語氣與結構貼近 168 聚財網 168-TV 欄位中張震相關文章的風格。

資料來源限制（最重要）：
你只依據下方「已擷取的操作紀錄」撰寫。除了這份清單，你沒有任何其他資料來源。
禁止列入清單以外的任何股票名稱或代號，禁止引用其他日期的內容，
禁止創造清單中沒有的價位、操作紀錄或會員持股。
禁止產出含糊語句，例如可能有、應該是、大約。
某一段資訊清單中沒有時，明確寫「本段內容：本支影片未說明，故不予記錄。」

全文繁體中文。章節標題與表格欄位名稱完全照下列格式，不可省略或改名，依序輸出：

① 文章標題
   觀察清單中的核心主題與關鍵字，產出 1 個具體標題，風格參考
   「張震：換手太明顯，這就是財富重分配！」這類語氣，但不可直接複製。
   輸出一行：文章標題：（你產生的標題）

② 基本資訊
   以條列輸出：
   節目名稱：張震 股市盤中家教班
   播出平台：YouTube 直播 / 影片
   播出日期：依提供的影片日期填寫
   主要講者：張震
   節目簡述：2 到 3 句，說明本集聚焦的主題與盤勢情境，只能根據清單內容歸納。

③ 盤勢總覽重點整理
   依清單中各筆的理由摘錄與說明重點，整理 3 到 7 點條列。
   不得引入清單以外的個股、指數數據或外資動向。
   清單資訊不足以支撐某一點時，就不要寫那一點。

④ 會員操作紀錄與持股明細
   ④-1 當日明確說明之買入／賣出紀錄
       只列出清單 buy 與 sell 兩類的項目，一檔都不能多、不能少。
       兩類皆為空時，寫：「本支影片未說明當日具體買賣紀錄。」
       表格欄位固定，完全照這個順序與名稱：
       | 動作類型 | 股票名稱 | 股票代號 | 方向（買入／賣出） | 價位區間／成本說明 | 張震口頭說明與操作理由（摘錄重點） |
       某欄位清單裡是「未說明」就照填「未說明」。
   ④-2 影片中明講之「會員目前持有股票」
       只列出清單 holdings 類的項目，一檔都不能多、不能少。
       為空時，寫：「本支影片未說明會員目前持股清單。」
       表格欄位固定，完全照這個順序與名稱：
       | 股票名稱 | 股票代號 | 目前立場（續抱／加碼觀察／分批調節等） | 張震在本集節目中的說明重點 |
   ④-3 觀望個股（當日未執行買賣）
       分成兩類分別列出，各自一張表：
       「觀望不碰」列出清單 watch_avoid 的項目，語氣偏空、情緒偏悲觀。
       「觀望注意」列出清單 watch_watch 的項目，語氣偏多、情緒偏正向。
       某一類為空時，寫：「本支影片未說明。」
       兩張表欄位皆固定，完全照這個順序與名稱：
       | 股票名稱 | 股票代號 | 觀望類型（觀望不碰／觀望注意） | 價位說明 | 張震口頭說明重點 |

⑤ 分析師操作邏輯與教學重點
   將清單中的理由摘錄與說明重點，整理為 3 到 8 點條列，格式：
   觀念一：（簡短標題）
     說明：（2 到 3 句，忠實轉述清單內容）
   不得自行補充清單以外的觀點、個股或散戶提醒。

⑥ 風險揭露與重要提醒
   必須包含下列兩點：
   本文章內容僅為整理節目中之公開資訊與觀點，不構成任何形式之投資建議或獲利保證。
   實際投資操作須自行評估風險與財務狀況，必要時請諮詢專業投資顧問。
   清單中若有風險控管或警語相關內容，接著條列整理。

所有表格使用 Markdown 表格。
股票代號一律直接抄用清單裡的 code 欄位，那是比對過官方清單的結果，
不要自己判斷或修改。code 為「代號待確認」時就照樣寫「代號待確認」。
每個章節以條列與短段落結合呈現，避免單一超長段落。
禁止提供任何投資建議、目標價或看多看空判斷。
直接輸出，不要加開場白。不要使用 emoji，不要使用破折號，
項目符號一律用實心圓點或數字。"""


def extract_signals(v2: str, date_str: str) -> dict:
    raw = call_gemini(
        EXTRACT_SYSTEM,
        f"影片日期：{date_str}\n\n完整逐字稿：\n{v2}",
        want_json=True, thinking=0, tag="extract",
    )
    raw = re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


def audit_signals(v2: str, signals: dict, date_str: str) -> dict:
    """
    完整性稽核：對照逐字稿，找出第一次擷取漏掉的個股，補進 signals。
    這是為了避免像「航運股裡點名的個股」被漏掉。找不到漏就原樣返回。
    """
    clean = {k: signals.get(k, []) for k in ("buy", "sell", "watch_avoid", "watch_watch", "holdings")}
    try:
        raw = call_gemini(
            AUDIT_SYSTEM,
            f"影片日期：{date_str}\n\n"
            f"已擷取的操作紀錄 JSON：\n{json.dumps(clean, ensure_ascii=False)}\n\n"
            f"完整逐字稿：\n{v2}",
            want_json=True, thinking=0, tag="audit",
        )
        raw = re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        missed = json.loads(raw)
    except Exception as e:
        print(f"  完整性稽核略過（{e}）")
        return signals

    added = 0
    skipped_industry = 0
    for cat in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        have = {str(x.get("name", "")).strip() for x in signals.get(cat, [])}
        for item in missed.get(cat, []) or []:
            nm = str(item.get("name", "")).strip()
            if not nm or nm in have:
                continue
            # 補回前先判定：是產業/族群就不補（也不會再被當成漏抓一直提醒）。
            # 像「石英元件」「AB載板」「光通訊」這種點名，是族群不是個股。
            if is_non_stock(nm)[0]:
                skipped_industry += 1
                continue
            signals.setdefault(cat, []).append(item)
            have.add(nm)
            added += 1
    if added:
        print(f"  完整性稽核補回 {added} 檔第一次擷取漏掉的個股")
    if skipped_industry:
        print(f"  完整性稽核略過 {skipped_industry} 個產業/族群名稱（非個股，不補）")
    if not added and not skipped_industry:
        print("  完整性稽核：無漏抓")
    return signals


def build_article(v2: str, signals: dict, date_str: str) -> str:
    clean = {k: v for k, v in signals.items() if not k.startswith("_")}
    payload = (
        f"影片日期：{date_str}\n\n"
        f"已擷取的操作紀錄（唯一資料來源，代號已比對官方清單，"
        f"禁止列入清單以外的任何股票，禁止改動代號）：\n"
        f"{json.dumps(clean, ensure_ascii=False, indent=2)}"
    )
    try:
        return call_gemini(ARTICLE_SYSTEM, payload, thinking=0, tag="article")
    except RuntimeError as e:
        # 內容特別多的那幾天，整理文章可能超過輸出上限被截斷（MAX_TOKENS）。
        # 不要報錯要人工重跑，改為自動用「精簡版」指示再生一次：
        # 只保留每檔一到兩句重點，去掉鋪陳與重複，通常就能壓進上限。
        if "MAX_TOKENS" not in str(e):
            raise
        print("  article 因內容過多被截斷，改用精簡指示自動重生一次")
        concise = ARTICLE_SYSTEM + (
            "\n\n【本次特別要求】這次內容較多，請大幅精簡："
            "每一檔最多一到兩句重點，去除所有鋪陳、形容與重複，"
            "務必在有限篇幅內完整涵蓋每一檔，不可中途截斷或省略任何一檔。"
        )
        return call_gemini(concise, payload, thinking=0, tag="article-concise")


# ---------------------------------------------------------------- #
# 寫入
# ---------------------------------------------------------------- #
def delete_rows_for_date(ss, sheet_name, date_str, date_col=1):
    """把某一天的資料整批刪掉，供重新分類時覆蓋用。由後往前刪避免列號位移。"""
    ws = ss.worksheet(sheet_name)
    values = sheets_retry(ws.get_all_values)
    targets = [i for i in range(len(values) - 1, 0, -1)
               if norm_date(values[i][date_col - 1]) == date_str]
    for r in targets:
        sheets_retry(ws.delete_rows, r + 1)
    if targets:
        print(f"  {sheet_name} 刪除 {len(targets)} 筆舊資料")
    return len(targets)


def write_results(ss, date_str, signals, article, done_trades, done_holds, replace=False):
    video_id = signals.get("_video_id", "")

    if replace:
        # 重新分類：先清掉該日舊資料，再用新版規則寫回
        delete_rows_for_date(ss, "操作紀錄", date_str)
        delete_rows_for_date(ss, "會員持股", date_str)
        delete_rows_for_date(ss, "每日推播內容", date_str)
        done_trades.discard(date_str)
        done_holds.discard(date_str)

    if date_str in done_trades:
        print(f"{date_str} 操作紀錄已存在，不重複寫入")
    else:
        rows = []
        for key, label in (("buy", "買入"), ("sell", "賣出"),
                           ("watch_avoid", "觀望不碰"), ("watch_watch", "觀望注意")):
            for r in signals.get(key, []):
                rows.append([date_str, r.get("name", ""), r.get("code", UNRESOLVED), label,
                             r.get("price", "未說明"), r.get("reason", "未說明"), video_id])
        if rows:
            sheets_retry(ss.worksheet("操作紀錄").append_rows, rows)
        print(f"操作紀錄寫入 {len(rows)} 筆")
        done_trades.add(date_str)

    if date_str in done_holds:
        print(f"{date_str} 會員持股已存在，不重複寫入")
    else:
        holds = [[date_str, r.get("name", ""), r.get("code", UNRESOLVED),
                  r.get("stance", "未說明"), r.get("note", "未說明"), video_id]
                 for r in signals.get("holdings", [])]
        if holds:
            sheets_retry(ss.worksheet("會員持股").append_rows, holds)
        print(f"會員持股寫入 {len(holds)} 筆")
        done_holds.add(date_str)

    if date_str not in existing_dates(ss, "每日推播內容"):
        sheets_retry(ss.worksheet("每日推播內容").append_row,
                     [date_str, cell(article or f"本日內容：{NOT_MENTIONED}。"), "待寄送"])


# ---------------------------------------------------------------- #
# 兩階段處理
# ---------------------------------------------------------------- #
def stage_transcript(ss, video, date_str):
    """
    階段一：取得逐字稿。最貴也最容易壞的一段。
    雲端已經有修飾後逐字稿就直接沿用，不重跑。
    """
    v1, v2 = existing_transcript(ss, video["id"], date_str)
    if v2 and len(v2) > 200:
        print(f"{date_str} 雲端已有修飾後逐字稿 {len(v2)} 字，略過轉錄與潤飾")
        return v1, v2

    if v1 and len(v1) > 200:
        print(f"{date_str} 已有原始逐字稿 {len(v1)} 字但缺修飾後版本，只補潤飾")
    else:
        mode = "長逾時" if INDEX_TIMEOUT == FULL_TIMEOUT else "輪詢短逾時"
        print(f"向 NotebookLM 索取逐字稿（{mode}，上限 {INDEX_TIMEOUT} 秒）")
        v1 = asyncio.run(fetch_fulltext(video["url"], f"張震_{date_str}", INDEX_TIMEOUT))
        print(f"取得原始逐字稿 {len(v1)} 字")
        if len(v1) < SHORT_TRANSCRIPT_HINT:
            print(f"警告：逐字稿僅 {len(v1)} 字，對一小時直播而言偏短。"
                  f"可能是 NotebookLM 索引不完整，或這支影片本身就短。")
        # 一拿到原始逐字稿就先落地，v2 暫時留空。
        # 這樣就算接下來的潤飾階段撞 429、逾時或任何失敗，
        # 這份原始逐字稿也已經在 Excel 裡，不會白抓一次。
        if v1 and len(v1) > 200:
            write_transcripts(ss, video["id"], v1, "")
            print("原始逐字稿已先寫入影片清單（潤飾前落地）")

    v2 = polish(v1)
    write_transcripts(ss, video["id"], v1, v2)   # 潤飾完再補寫 v2
    return v1, v2


def stage_extract(ss, video, date_str, v2, done_trades, done_holds):
    """階段二：擷取結構化紀錄。與階段一分開，因為它便宜、可重跑。"""
    if date_str in done_trades and date_str in done_holds:
        print(f"{date_str} 操作紀錄與會員持股都已存在，略過擷取")
        return

    signals = extract_signals(v2, date_str)
    signals = audit_signals(v2, signals, date_str)
    signals = resolve_signals(signals)
    signals["_video_id"] = video["id"]
    article = build_article(v2, signals, date_str)
    write_results(ss, date_str, signals, article, done_trades, done_holds)


# ---------------------------------------------------------------- #
# 後台工單
#
# 管理者在網站後台貼上逐字稿之後，原文會先寫進「影片清單」，
# 並在「後台工單」開一列狀態。這裡負責把後面的流程跑完，
# 每做完一步就把進度寫回工單，網站的進度條讀的就是那一列。
#
# 好處是兩邊都看得到：網站上有進度條，GitHub 的執行紀錄有完整輸出，
# 出事時直接看日誌就知道卡在哪一行，不必再猜。
# ---------------------------------------------------------------- #
ADMIN_JOB_SHEET = "後台工單"

# 工單欄位順序，與 Apps Script 端的 SHEET_SCHEMA 一致。
# 兩邊都用名稱找欄位，所以順序調整不會壞掉，但保持一致比較好讀。
JOB_COLS = ["工單ID", "日期", "影片ID", "狀態", "步驟", "已完成", "總數",
            "備註", "開始時間", "更新時間", "來源"]


def _job_sheet(ss):
    return ss.worksheet(ADMIN_JOB_SHEET)


def find_pending_job(ss):
    """找出最後一列狀態為處理中的工單。找不到回 None。"""
    try:
        ws = _job_sheet(ss)
        vals = sheets_retry(ws.get_all_values)
    except Exception as e:
        print(f"讀不到{ADMIN_JOB_SHEET}：{e}")
        return None
    if len(vals) < 2:
        return None

    head = vals[0]

    def ci(name, d):
        return head.index(name) if name in head else d

    c = {k: ci(k, i) for i, k in enumerate(JOB_COLS)}
    for i in range(len(vals) - 1, 0, -1):
        row = vals[i]

        def g(k):
            j = c[k]
            return row[j] if j < len(row) else ""
        if str(g("狀態")).strip() != "處理中":
            continue
        return {"row": i + 1, "cols": c, "ws": ws,
                "id": g("工單ID"), "date": norm_date(g("日期")),
                "videoId": str(g("影片ID")).strip(),
                "step": str(g("步驟")).strip()}
    return None


def job_progress(job, step=None, done=None, total=None, note=None, status=None):
    """把進度寫回工單。失敗不中斷流程——進度只是給人看的，不該拖垮主要工作。"""
    if not job:
        return
    try:
        ws, c, row = job["ws"], job["cols"], job["row"]
        cells = []
        if status is not None:
            cells.append((c["狀態"], status))
        if step is not None:
            cells.append((c["步驟"], step))
        if done is not None:
            cells.append((c["已完成"], done))
        if total is not None:
            cells.append((c["總數"], total))
        if note is not None:
            cells.append((c["備註"], str(note)[:400]))
        cells.append((c["更新時間"], datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")))
        data = [{"range": gspread.utils.rowcol_to_a1(row, j + 1), "values": [[v]]}
                for j, v in cells]
        sheets_retry(ws.batch_update, data, value_input_option="RAW")
    except Exception as e:
        print(f"（寫入進度失敗，不影響流程：{e}）")

    if step:
        print(f"\n===== 步驟：{step}"
              + (f"　{done}/{total}" if total else "")
              + (f"　{note}" if note else "") + " =====")


def run_admin_job(ss):
    """
    執行一張後台工單。整段流程與自動路徑完全相同，
    差別只在逐字稿的來源是管理者貼上的，而不是從外部服務抓的。
    """
    job = find_pending_job(ss)
    if not job:
        print("沒有待處理的後台工單。")
        return

    vid, date_str = job["videoId"], job["date"]
    print(f"工單 {job['id']}　{date_str}　影片 {vid}")

    # ---- 取出管理者貼上的原文 ----
    job_progress(job, step="讀取原文", done=0, total=6)
    v1, v2 = existing_transcript(ss, vid, date_str)
    if not v1 or len(v1) < 300:
        raise RuntimeError(f"影片清單裡找不到 {vid} 的原始逐字稿，或內容太短（{len(v1 or '')} 字）。")
    print(f"原始逐字稿 {len(v1)} 字")

    # ---- 潤飾 ----
    # 雲端已有夠長的修飾稿就沿用，讓工單可以從中斷處續跑而不必重跑一次潤飾。
    if v2 and len(v2) > 200:
        print(f"已有修飾後逐字稿 {len(v2)} 字，略過潤飾")
        job_progress(job, step="潤飾", done=1, total=6, note="沿用既有修飾稿")
    else:
        job_progress(job, step="潤飾", done=1, total=6, note=f"原文 {len(v1)} 字")
        v2 = polish(v1)
        upsert_video_transcript(ss, vid, date_str, v2)
        print(f"潤飾完成 {len(v1)} → {len(v2)} 字")

    video = {
        "id": vid,
        "title": f"後台投稿 {date_str}",
        "date": datetime.strptime(date_str, "%Y/%m/%d").date(),
        "url": f"https://www.youtube.com/watch?v={vid}",
    }

    # ---- 擷取、稽核、代號、寫入、撰稿 ----
    # 這一整段直接沿用自動路徑的 stage_extract，不另外實作。
    # 兩條路走同一段程式，產出的品質與格式就不可能不一致。
    job_progress(job, step="擷取與比對", done=2, total=6,
                 note="擷取、完整性稽核、代號比對、價位校對、寫入、撰稿")
    done_trades = existing_dates(ss, "操作紀錄")
    done_holds = existing_dates(ss, "會員持股")
    stage_extract(ss, video, date_str, v2, done_trades, done_holds)

    mark_status(ss, vid, date_str, video["title"], "完成")
    job_progress(job, step="刷新網站", done=5, total=6, note="資料已寫入，通知下游重算")

    print("\n工單處理完成，接著通知下游重算全站。")
    job_progress(job, step="完成", done=6, total=6, status="完成",
                 note="全部完成。網站與郵件內容都已更新。")


def upsert_video_transcript(ss, video_id, date_str, v2):
    """把修飾後逐字稿寫回影片清單。"""
    ws = ss.worksheet("影片清單")
    vals = sheets_retry(ws.get_all_values)
    head = vals[0]
    c_id = head.index("影片ID") if "影片ID" in head else 0
    c_v2 = head.index("修飾後逐字稿內容") if "修飾後逐字稿內容" in head else None
    if c_v2 is None:
        print("影片清單沒有『修飾後逐字稿內容』欄，略過寫回")
        return
    for i in range(1, len(vals)):
        if str(vals[i][c_id]).strip() == video_id:
            sheets_retry(ws.update_cell, i + 1, c_v2 + 1, v2[:SHEET_CELL_LIMIT])
            return
    print(f"影片清單裡找不到 {video_id}，修飾稿沒有寫回")


def process_one(ss, video, done_trades, done_holds):
    date_str = video["date"].strftime("%Y/%m/%d")
    print(f"\n=== 處理 {date_str}　{video['title']}　{video['id']} ===")

    try:
        _, v2 = stage_transcript(ss, video, date_str)
    except NotReadyYet as e:
        # 這不是失敗。VOD 還在轉檔，下一輪會再敲一次門。
        mark_status(ss, video["id"], date_str, video["title"], "等待中", str(e)[:200])
        print(f"尚未就緒：{e}")
        print("這是正常的，直播結束後 YouTube 要一段時間轉檔。下一輪排程會再試。")
        raise
    except AuthExpired as e:
        mark_status(ss, video["id"], date_str, video["title"], "認證過期", str(e)[:400])
        raise
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise

    try:
        mark_status(ss, video["id"], date_str, video["title"], "處理中")
        stage_extract(ss, video, date_str, v2, done_trades, done_holds)
        if POLISH_DEGRADED:
            mark_status(ss, video["id"], date_str, video["title"], "完成",
                        f"有 {POLISH_DEGRADED} 段因配額不足未潤飾，建議稍後重跑")
        else:
            mark_status(ss, video["id"], date_str, video["title"], "完成")
        print(f"完成 {video['id']}")
    except Exception as e:
        mark_status(ss, video["id"], date_str, video["title"], "失敗", str(e)[:400])
        raise


# ---------------------------------------------------------------- #
# 純修代號
# ---------------------------------------------------------------- #
def fill_video_blanks(ss):
    """
    逐一檢視「影片清單」，把缺原始或修飾後逐字稿的列補齊，
    並在該日尚未擷取時補跑擷取。不動已完整的列。
    """
    done_trades = existing_dates(ss, "操作紀錄")
    done_holds = existing_dates(ss, "會員持股")

    targets = []
    for r in video_rows(ss):
        vid = str(r.get("影片ID") or "").strip()
        if not vid or vid.startswith("NO_VIDEO_"):
            continue
        v1 = str(r.get("原始逐字稿內容") or "").strip()
        v2 = str(r.get("修飾後逐字稿內容") or "").strip()
        if len(v1) > 200 and len(v2) > 200:
            continue
        targets.append(r)

    if not targets:
        print("影片清單沒有需要補的空白")
        return

    print(f"影片清單待補空白 {len(targets)} 支")
    for r in targets:
        if out_of_budget():
            print(f"時間預算用盡，本輪先停，剩下的下次再補。")
            break
        vid = str(r.get("影片ID")).strip()
        title = str(r.get("標題") or "")
        ds = norm_date(r.get("發布日期"))
        if not ds:
            m = TITLE_DATE.search(title)
            if m:
                ds = f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
        if not ds:
            print(f"  {vid} 無法判斷日期，略過")
            continue

        video = {
            "id": vid,
            "title": title,
            "date": datetime.strptime(ds, "%Y/%m/%d").date(),
            "url": f"https://www.youtube.com/watch?v={vid}",
        }
        print(f"\n--- 補空白 {ds}　{vid} ---")
        try:
            process_one(ss, video, done_trades, done_holds)
        except NotReadyYet as e:
            print(f"  逐字稿尚未就緒，稍後再補：{e}")
        except Exception as e:
            print(f"  補空白失敗：{e}")


# 情緒關鍵字。用來把舊的「觀望／不碰」依理由摘錄重新歸類。
# 偏空詞出現就歸「觀望不碰」，否則歸「觀望注意」（中性也算注意）。
NEG_HINTS = (
    "不碰", "不要碰", "不建議", "不宜", "避開", "避免", "風險高", "危險",
    "轉弱", "走弱", "破線", "破底", "跌破", "套牢", "被套", "認賠", "停損",
    "出場觀察", "先出", "空方", "偏空", "看壞", "看空", "弱勢", "疲弱",
    "小心", "留意風險", "崩", "殺", "利空", "觀望為宜", "暫不", "別追",
)
POS_HINTS = (
    "看好", "偏多", "強勢", "轉強", "走強", "留意", "注意", "追蹤", "觀察",
    "有機會", "可期待", "回檔進場", "拉回買", "布局", "卡位", "潛力",
    "續強", "多方", "站上", "突破", "帶量", "值得", "不錯",
)


def sentiment_of(reason: str) -> str:
    """依理由摘錄判斷情緒。偏空回 watch_avoid，偏多或中性回 watch_watch。"""
    text = str(reason or "")
    neg = sum(1 for k in NEG_HINTS if k in text)
    pos = sum(1 for k in POS_HINTS if k in text)
    if neg > pos:
        return "watch_avoid"
    if pos > neg:
        return "watch_watch"
    # 平手或都沒有：明確講「不碰」歸不碰，否則歸注意
    return "watch_avoid" if ("不碰" in text) else "watch_watch"


# 需要被重新歸類的舊方向值：只動觀望類，買入與賣出一律不碰。
WATCH_LABELS = {"觀望", "不碰", "觀望不碰", "觀望注意", "觀望／不碰", "不碰／觀望"}


def reclassify_from_transcripts(ss):
    """
    重新分類（純表格版）。

    只讀「操作紀錄」既有的內容：股票名稱、代號、方向、理由摘錄都已經是
    比對過、正確的，不需要也不應該再去逐字稿重跑擷取。

    做的事只有一件：把方向是舊「觀望／不碰」這類的列，
    依「理由摘錄」的情緒關鍵字，改寫成「觀望不碰」或「觀望注意」。
    買入、賣出完全不動。

    完全不呼叫 NotebookLM，也完全不呼叫 Gemini，
    所以沒有拼音誤判、沒有 429、幾秒就跑完。
    """
    ws = ss.worksheet("操作紀錄")
    values = sheets_retry(ws.get_all_values)
    if len(values) < 2:
        print("操作紀錄沒有資料，無需重新分類")
        return

    header = values[0]
    # 找欄位位置，避免寫死欄號
    def col(name, default):
        return header.index(name) if name in header else default
    ci_dir = col("方向", 3)
    ci_reason = col("理由摘錄", 5)

    changed = []          # (列號, 新方向)
    stat = {"avoid": 0, "watch": 0, "skip": 0}

    for i in range(1, len(values)):
        row = values[i]
        direction = str(row[ci_dir]).strip() if ci_dir < len(row) else ""
        if direction not in WATCH_LABELS:
            continue      # 買入、賣出，或其他，不動
        reason = row[ci_reason] if ci_reason < len(row) else ""
        new_dir = "觀望不碰" if sentiment_of(reason) == "watch_avoid" else "觀望注意"
        if new_dir != direction:
            changed.append((i + 1, new_dir))
        stat["avoid" if new_dir == "觀望不碰" else "watch"] += 1

    if not changed:
        print(f"重新分類完成：觀望類共 {stat['avoid'] + stat['watch']} 筆，"
              f"其中觀望不碰 {stat['avoid']}、觀望注意 {stat['watch']}，皆已是最新分類，無需改寫。")
        return

    # 關鍵：一次批次寫回，不要逐格更新。
    # 逐格 update_acell 一筆就是一次 API 寫入請求，113 筆等於 113 次，
    # 而 Google Sheets 每分鐘每使用者寫入上限約 60 次，必爆 429。
    # batch_update 把所有格子併成「一次」請求送出，就不會撞限額。
    col_letter = chr(ord("A") + ci_dir)
    data = [{"range": f"{col_letter}{r}", "values": [[new_dir]]} for r, new_dir in changed]
    print(f"重新分類：一次批次改寫 {len(changed)} 筆方向"
          f"（觀望不碰 {stat['avoid']}、觀望注意 {stat['watch']}）")

    # 每批最多 500 個範圍，超過就分批，批間稍作停頓，避免瞬間打太多。
    BATCH = 500
    for start in range(0, len(data), BATCH):
        chunk = data[start:start + BATCH]
        sheets_retry(ws.batch_update, chunk, value_input_option="RAW")
        if start + BATCH < len(data):
            time.sleep(2)

    print("方向欄批次改寫完成。買入與賣出未更動。")
    print("接著請到 Apps Script 執行 rebuildHoldingsTrackerJob()，讓持股追蹤反映新分類。")


def _relevant_snippets(v2: str, name: str, span: int = 260) -> str:
    """從逐字稿抓出所有提到 name 的段落，前後各留一點上下文，串起來給 AI。"""
    if not v2 or not name:
        return ""
    out, i = [], 0
    while True:
        j = v2.find(name, i)
        if j < 0:
            break
        a = max(0, j - span)
        b = min(len(v2), j + len(name) + span)
        out.append(v2[a:b])
        i = j + len(name)
        if len(out) >= 6:
            break
    return "\n…\n".join(out)


def reconcile_all(ss):
    """
    整頓既有資料，做三件事：
      1. 用 AI 判定「代號待確認」的名稱是不是產業/族群，是就整列刪除。
      2. 對每一檔買入，從逐字稿抽出張震明講的買入價（可能不是第一天講的），
         核對落在當日 K 線高低之間才採用，寫回操作紀錄的價位說明。
      3. 逐日不一致由下游 rebuildHoldingsTrackerJob 以聯集方式統一，這裡不處理。
    不重抓影片、不呼叫 NotebookLM。
    """
    trades_ws = ss.worksheet("操作紀錄")
    tvals = sheets_retry(trades_ws.get_all_values)
    if len(tvals) < 2:
        print("操作紀錄是空的，無需整頓")
        return
    th = tvals[0]

    def ci(name, default):
        return th.index(name) if name in th else default
    c_date, c_name, c_code = ci("日期", 0), ci("股票名稱", 1), ci("代號", 2)
    c_dir, c_price = ci("方向", 3), ci("價位說明", 4)

    # ---- 準備逐字稿索引：日期 -> 修飾後逐字稿 ----
    tx = {}
    for r in video_rows(ss):
        d = norm_date(r.get("發布日期"))
        v2 = str(r.get("修飾後逐字稿內容") or "")
        if d and len(v2) > 200:
            tx[d] = v2

    # ---- 步驟 1：AI 判定產業並刪除 ----
    # 只送「代號待確認」或判不出代號的名稱，省 token。
    suspect = sorted({str(row[c_name]).strip()
                      for row in tvals[1:]
                      if str(row[c_name]).strip()
                      and (not re.match(r"^\d{4,6}$", str(row[c_code]).strip()))})
    industry = set()
    if suspect:
        # 先用規則擋一輪，剩下的才問 AI
        rule_ind = {n for n in suspect if is_non_stock(n)[0]}
        industry |= rule_ind
        ask = [n for n in suspect if n not in rule_ind]
        for i in range(0, len(ask), 40):
            batch = ask[i:i + 40]
            try:
                raw = call_gemini(INDUSTRY_JUDGE_SYSTEM,
                                  json.dumps(batch, ensure_ascii=False),
                                  want_json=True, thinking=0, tag="industry")
                verdict = json.loads(re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.M).strip())
                for n, v in verdict.items():
                    if str(v).lower().startswith("indus"):
                        industry.add(n)
            except Exception as e:
                print(f"  產業判定略過一批（{e}）")

    if industry:
        print(f"判定為產業/族群，將整列刪除：{'、'.join(sorted(industry))}")
        for sheet in ("操作紀錄", "會員持股"):
            ws = ss.worksheet(sheet)
            vals = sheets_retry(ws.get_all_values)
            head = vals[0]
            nm = head.index("股票名稱") if "股票名稱" in head else 1
            drop = [i for i in range(len(vals) - 1, 0, -1)
                    if str(vals[i][nm]).strip() in industry]
            for r in drop:
                sheets_retry(ws.delete_rows, r + 1)
            if drop:
                print(f"  {sheet} 刪除 {len(drop)} 列")
        # 重新讀操作紀錄，因為列號已變
        tvals = sheets_retry(trades_ws.get_all_values)

    # ---- 步驟 2：抽取並核對買入價 ----
    # 蒐集每一檔（以名稱為鍵）的所有買入列與其日期
    buys = {}
    for idx in range(1, len(tvals)):
        row = tvals[idx]
        nm = str(row[c_name]).strip()
        if not nm or nm in industry:
            continue
        if str(row[c_dir]).strip().find("買") != 0:
            continue
        buys.setdefault(nm, []).append({"rowno": idx + 1, "date": norm_date(row[c_date])})

    price_updates = []   # (rowno, 新價位說明)
    checked = 0
    for nm, lst in buys.items():
        if out_of_budget():
            print("時間預算用盡，買入價整頓先停，下次再跑。")
            break
        # 把這一檔在各買入日的逐字稿段落串起來（多天一起看，才能抓到後來才補講的價）
        chunks = []
        for b in lst:
            v2 = tx.get(b["date"], "")
            snip = _relevant_snippets(v2, nm)
            if snip:
                chunks.append(f"[{b['date']}]\n{snip}")
        if not chunks:
            continue
        checked += 1
        try:
            raw = call_gemini(ENTRY_PRICE_SYSTEM,
                              f"股票名稱：{nm}\n\n相關逐字稿段落：\n" + "\n\n".join(chunks),
                              want_json=True, thinking=0, tag="entryprice")
            res = json.loads(re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.M).strip())
            price = res.get("price")
            if price is None:
                continue
            price = float(price)
            side = str(res.get("side", "buy")).lower()
            ai_note = str(res.get("note", "")).strip()
            # 價位說明優先用 AI 給的來源說明（含「45 以上買入」這種條件），
            # 沒有就退回一個明確標註。下游持股追蹤會據此標明是張震明講價。
            if ai_note:
                note = ai_note
            else:
                act = "賣出" if side == "sell" else "買入"
                note = f"張震明講在 {price} {act}"
            # 更新該檔所有買入列的價位說明為明講價（讓下游進場價採用）
            for b in lst:
                price_updates.append((b["rowno"], note))
            print(f"  {nm}　抽到{('賣出' if side=='sell' else '買入')}價 {price}"
                  f"（依據：{res.get('quote','')}）")
        except Exception as e:
            print(f"  {nm} 買入價抽取略過（{e}）")

    if price_updates:
        col = chr(ord("A") + c_price)
        data = [{"range": f"{col}{rn}", "values": [[note]]} for rn, note in price_updates]
        for i in range(0, len(data), 500):
            sheets_retry(trades_ws.batch_update, data[i:i + 500], value_input_option="RAW")
        print(f"買入價寫回 {len(price_updates)} 列（涵蓋 {checked} 檔）。")
    else:
        print(f"檢查了 {checked} 檔，沒有抽到可更新的明講買入價。")

    print("整頓完成。請到 Apps Script 執行 rebuildHoldingsTrackerJob() 讓進場價與逐日說明更新。")


# 明顯不是股價的單位與詞。命中就直接判定要修，不必先問 AI。
NON_PRICE_UNITS = ("億", "兆", "萬元", "千萬", "萬張", "張", "口",
                   "EPS", "每股", "毛利", "營益", "本益比", "殖利率",
                   "營收", "點", "指數", "大盤", "%", "％")

# 概數用語。命中代表這個數字不能當成交價。
VAGUE_UNITS = ("多", "左右", "上下", "附近", "大概", "約", "出頭", "之間", "~", "～")

# 方向與動作字眼的對應。價位說明裡出現「相反方向」的動作字眼就是矛盾。
DIR_ACTION_WORDS = {
    "買入": {"self": ("買", "承接", "進場", "掛進", "布局", "加碼"),
             "opposite": ("賣", "出清", "出場", "獲利了結", "停損", "全部賣掉", "減碼")},
    "賣出": {"self": ("賣", "出清", "出場", "獲利了結", "停損", "減碼"),
             "opposite": ("買", "承接", "進場", "掛進", "布局", "加碼")},
}


def price_note_is_suspect(direction: str, note: str) -> tuple:
    """
    先用規則快篩，判斷這筆價位說明需不需要送 AI 校對。
    回傳 (要不要修, 原因)。這一層擋掉大多數乾淨的列，省 token 也省時間。
    """
    d = str(direction or "").strip()
    s = str(note or "").strip()

    if not s or s == "未說明":
        return (False, "")

    # 1. 方向矛盾：價位說明裡出現與方向相反的動作字眼
    rule = DIR_ACTION_WORDS.get(d)
    if rule:
        has_opposite = any(w in s for w in rule["opposite"])
        has_self = any(w in s for w in rule["self"])
        if has_opposite and not has_self:
            return (True, f"方向是{d}，但價位說明描述的是相反動作")

    # 2. 非股價單位
    for u in NON_PRICE_UNITS:
        if u in s:
            return (True, f"含非股價單位「{u}」")

    # 3. 概數
    for u in VAGUE_UNITS:
        if u in s:
            return (True, f"含概數用語「{u}」")

    # 4. 太長：正常的價位說明是「168 買入」這種短句。
    #    超過 20 字幾乎都是把整段口述塞進來了，需要濃縮。
    if len(s) > 20:
        return (True, "價位說明過長，應濃縮成一句")

    return (False, "")


def load_daily_k(ss) -> dict:
    """
    從「日K快取」讀出 {代號: {日期: (最高, 最低)}}。
    用來硬性驗證 AI 給的價位真的是那天的股價，而不是營收或指數。
    快取是空的也不影響流程，只是少一道驗證。
    """
    try:
        vals = sheets_retry(ss.worksheet("日K快取").get_all_values)
    except Exception as e:
        print(f"  讀不到日K快取（{e}），本次略過價位區間驗證")
        return {}
    if len(vals) < 2:
        return {}

    head = vals[0]

    def ci(name, default):
        return head.index(name) if name in head else default
    c_code, c_date = ci("代號", 0), ci("日期", 1)
    c_high, c_low = ci("高", 3), ci("低", 4)

    out = {}
    for row in vals[1:]:
        try:
            code = str(row[c_code]).strip()
            date = norm_date(row[c_date])
            hi = float(row[c_high])
            lo = float(row[c_low])
        except (ValueError, IndexError):
            continue
        if not code or not date or hi <= 0:
            continue
        out.setdefault(code, {})[date] = (hi, lo)
    print(f"  日K快取載入 {len(out)} 檔，供價位區間驗證")
    return out


def fix_prices_all(ss):
    """
    價位說明校對。逐列檢查「操作紀錄」的價位說明，用 AI 修正三類錯誤：

      1. 方向矛盾。方向是買入，說明卻寫「255 以上鴻海全部賣掉」。
         以方向欄為準，回逐字稿重抓符合該方向的價位，找不到就留未說明。
      2. 不是股價的數字。「241億以下」是營收不是股價，用日K區間硬性驗證後剔除。
      3. 概數當精確價。「1400多」保留敘述但不給數字，避免被當成本算報酬。

    只用已存的逐字稿，不重抓影片、不呼叫 NotebookLM。
    先用規則快篩，只有可疑的列才送 AI，所以大部分的列是零成本通過的。
    """
    ws = ss.worksheet("操作紀錄")
    vals = sheets_retry(ws.get_all_values)
    if len(vals) < 2:
        print("操作紀錄是空的，無需校對")
        return

    head = vals[0]

    def ci(name, default):
        return head.index(name) if name in head else default
    c_date, c_name, c_code = ci("日期", 0), ci("股票名稱", 1), ci("代號", 2)
    c_dir, c_price = ci("方向", 3), ci("價位說明", 4)

    # 逐字稿索引：日期 -> 修飾後逐字稿
    tx = {}
    for r in video_rows(ss):
        d = norm_date(r.get("發布日期"))
        v2 = str(r.get("修飾後逐字稿內容") or "")
        if d and len(v2) > 200:
            tx[d] = v2
    print(f"  可用逐字稿 {len(tx)} 天")

    kmap = load_daily_k(ss)

    # ---- 規則快篩 ----
    suspects = []
    for i in range(1, len(vals)):
        row = vals[i]

        def get(idx):
            return str(row[idx]).strip() if idx < len(row) else ""
        name, direction, note = get(c_name), get(c_dir), get(c_price)
        if not name:
            continue
        need, why = price_note_is_suspect(direction, note)
        if need:
            suspects.append({
                "rowno": i + 1, "name": name, "code": get(c_code),
                "date": norm_date(get(c_date)), "dir": direction,
                "note": note, "why": why,
            })

    total_rows = len(vals) - 1
    if not suspects:
        print(f"價位說明校對完成：{total_rows} 列全部通過規則快篩，沒有需要修正的。")
        return
    print(f"共 {total_rows} 列，規則快篩挑出 {len(suspects)} 列可疑，送 AI 校對：")
    for s in suspects[:15]:
        print(f"  第 {s['rowno']} 列　{s['date']} {s['name']} [{s['dir']}]"
              f"　「{s['note'][:28]}」　← {s['why']}")
    if len(suspects) > 15:
        print(f"  ……另有 {len(suspects) - 15} 列")

    # ---- 逐列送 AI 校對 ----
    updates, stat = [], {"fixed": 0, "cleared": 0, "kept": 0, "skipped": 0}

    for s in suspects:
        if out_of_budget():
            print("時間預算用盡，價位校對先停，下次再跑（已處理的會先寫回）。")
            break

        v2 = tx.get(s["date"], "")
        snippet = _relevant_snippets(v2, s["name"]) if v2 else ""
        if not snippet:
            # 沒有逐字稿佐證就不敢改內容，但明顯不是股價的仍要清掉，
            # 否則「241億以下」會一直留著被下游當成價位解析。
            if any(u in s["note"] for u in NON_PRICE_UNITS):
                updates.append((s["rowno"], "未說明"))
                stat["cleared"] += 1
                print(f"  第 {s['rowno']} 列　{s['name']}　無逐字稿佐證但確定非股價，清為未說明")
            else:
                stat["skipped"] += 1
            continue

        # 當天的股價區間，給 AI 當硬性驗證依據
        rng = kmap.get(s["code"], {}).get(s["date"])
        rng_text = (f"當天股價區間：最高 {rng[0]}，最低 {rng[1]}"
                    if rng else "當天股價區間：日K快取沒有這一天的資料，請用常識判斷")

        user = (
            f"股票名稱：{s['name']}\n"
            f"日期：{s['date']}\n"
            f"操作方向：{s['dir']}\n"
            f"目前的價位說明：{s['note']}\n"
            f"系統初步判定的問題：{s['why']}\n"
            f"{rng_text}\n\n"
            f"逐字稿相關段落：\n{snippet}"
        )

        try:
            raw = call_gemini(PRICE_FIX_SYSTEM, user, want_json=True, thinking=0, tag="pricefix")
            res = json.loads(re.sub(r"^```json|^```|```$", "", raw.strip(), flags=re.M).strip())
        except Exception as e:
            print(f"  第 {s['rowno']} 列　{s['name']} 校對略過（{e}）")
            stat["skipped"] += 1
            continue

        price = res.get("price")
        new_note = str(res.get("note") or "").strip() or "未說明"

        # ---- 程式端再驗一次。AI 說通過不算數，數字要自己對過 K 線才算。 ----
        if price is not None and rng:
            try:
                pv = float(price)
                hi, lo = rng
                if pv > hi * 1.3 or pv < lo * 0.7:
                    print(f"  第 {s['rowno']} 列　{s['name']}　AI 給的 {pv} 偏離當日區間 "
                          f"{lo}-{hi} 超過三成，不採用，清為未說明")
                    new_note = "未說明"
            except (TypeError, ValueError):
                new_note = "未說明"

        if new_note == s["note"]:
            stat["kept"] += 1
            continue

        updates.append((s["rowno"], new_note))
        if new_note == "未說明":
            stat["cleared"] += 1
        else:
            stat["fixed"] += 1
        print(f"  第 {s['rowno']} 列　{s['name']} [{s['dir']}]"
              f"　「{s['note'][:24]}」→「{new_note}」　（{res.get('reason', '')}）")

    # ---- 批次寫回 ----
    if updates:
        col = chr(ord("A") + c_price)
        data = [{"range": f"{col}{rn}", "values": [[note]]} for rn, note in updates]
        for i in range(0, len(data), 500):
            sheets_retry(ws.batch_update, data[i:i + 500], value_input_option="RAW")
            if i + 500 < len(data):
                time.sleep(2)
        print(f"\n價位說明批次寫回 {len(updates)} 列。")
    else:
        print("\n沒有需要寫回的修正。")

    print(f"校對統計：改寫 {stat['fixed']}、清為未說明 {stat['cleared']}、"
          f"維持原樣 {stat['kept']}、略過 {stat['skipped']}")
    print("接著請刷新網站（見下方說明），讓持股追蹤與表格反映新的價位說明。")


def repair_codes_only(ss):
    """
    不碰 NotebookLM，不呼叫 Gemini，只把試算表既有的股票名稱
    重跑一次 resolve_code。

    非個股（台塑集團、PMIC、高速傳輸股）整列刪除，不留在資料裡。
    由後往前刪，這樣刪掉一列不會讓還沒處理的列號位移。
    """
    get_code_map()

    total = {"fixed": 0, "ok": 0, "still": 0, "deleted": 0}
    unresolved, deleted = [], []

    for sheet_name, name_col, code_col in (("操作紀錄", 2, 3), ("會員持股", 2, 3)):
        ws = ss.worksheet(sheet_name)
        values = sheets_retry(ws.get_all_values)
        if len(values) < 2:
            print(f"{sheet_name} 是空的，略過")
            continue

        print(f"\n=== {sheet_name}　{len(values) - 1} 列 ===")

        keep_rows, to_delete = [], []

        for i, row in enumerate(values[1:], start=2):
            old_name = (row[name_col - 1] if len(row) >= name_col else "").strip()
            old_code = (row[code_col - 1] if len(row) >= code_col else "").strip()

            if not old_name:
                to_delete.append(i)
                total["deleted"] += 1
                continue

            code, fixed, how = resolve_code(old_name, old_code)

            if code == REJECT:
                to_delete.append(i)
                total["deleted"] += 1
                deleted.append((sheet_name, i, old_name, how.replace("剔除：", "")))
                print(f"  第 {i:>3} 列　{old_name} -> 刪除（{how.replace('剔除：', '')}）")
                continue

            new_row = list(row) + [""] * (max(name_col, code_col) - len(row))
            new_row[name_col - 1] = fixed
            new_row[code_col - 1] = code
            keep_rows.append(new_row)

            if code == UNRESOLVED:
                total["still"] += 1
                unresolved.append((sheet_name, i, old_name, how))
                print(f"  第 {i:>3} 列　{old_name} -> 仍待確認（{how}）")
            elif fixed != old_name or code != old_code:
                total["fixed"] += 1
                print(f"  第 {i:>3} 列　{old_name}（{old_code or '空白'}）"
                      f" -> {fixed}（{code}）　{how}")
            else:
                total["ok"] += 1

        # 由後往前刪，避免列號位移
        for r in sorted(to_delete, reverse=True):
            sheets_retry(ws.delete_rows, r)
        if to_delete:
            print(f"  已刪除 {len(to_delete)} 列非個股")

        # 刪完之後才寫回名稱與代號，此時列號已經重新對齊
        if keep_rows:
            width = len(values[0])
            padded = [r[:width] + [""] * (width - len(r)) for r in keep_rows]
            sheets_retry(ws.update, range_name=f"A2:{chr(64 + width)}{len(padded) + 1}",
                         values=padded)
            print(f"  已寫回 {len(padded)} 列")

    print("\n" + "=" * 56)
    print(f"修正 {total['fixed']} 筆，本來就正確 {total['ok']} 筆，"
          f"仍待確認 {total['still']} 筆，刪除非個股 {total['deleted']} 筆")

    if deleted:
        print("\n已刪除的非個股：")
        for sheet, row, name, why in deleted:
            print(f"  {sheet} 原第 {row} 列　{name}　{why}")

    if unresolved:
        print("\n以下是個股但對不上，需要人工看影片填入代號：")
        for sheet, row, name, how in unresolved:
            print(f"  {sheet} 第 {row} 列　{name}　{how}")
        print("\n填法：直接在試算表的「代號」欄填四位數字，「股票名稱」欄改成正式簡稱，")
        print("      然後回 Apps Script 執行 rebuildHoldingsTrackerJob()。")

    print("\n下一步：回到 Apps Script 執行 rebuildHoldingsTrackerJob()。")
    return total


# ---------------------------------------------------------------- #
# 主流程
# ---------------------------------------------------------------- #
_SS = None      # 供 __main__ 的例外處理寫入系統狀態用


def main():
    global _SS
    src = "Variables" if os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() else "內建預設值"
    print(f"頻道 ID：{CHANNEL_ID}（{src}）")

    ss = open_sheets()
    _SS = ss
    write_status_log(ss, "開始", "本輪開始執行")

    # 會用到 Gemini 的模式，在這裡就先確認金鑰在不在。
    # 延後到真正呼叫才檢查雖然不會出錯，但可能已經跑了好幾分鐘才炸，
    # 所以正式流程仍在開頭擋一次，只有探測與純修代號例外。
    if not (PREFLIGHT or REPAIR_CODES or (REFRESH_SITE and not any(
            (RECLASSIFY, FIX_PRICES, RECONCILE, FILL_BLANKS, BACKFILL)))):
        require_gemini_key()

    # 這三個是互斥模式，同時勾選只有第一個會生效。
    # 先前就發生過三個都勾、結果只跑了修代號的情況，所以這裡明講。
    picked = [n for n, on in (("admin_job", ADMIN_JOB),
                              ("repair_codes", REPAIR_CODES),
                              ("reclassify", RECLASSIFY),
                              ("fix_prices", FIX_PRICES),
                              ("reconcile", RECONCILE),
                              ("fill_blanks", FILL_BLANKS),
                              ("backfill", BACKFILL)) if on]
    if len(picked) > 1:
        print(f"注意：同時勾選了 {'、'.join(picked)}，這些是互斥模式，"
              f"本輪只會執行「{picked[0]}」。其餘請分次執行。")

    # 探測遇到手動模式時，一律回報「有事要做」並立刻結束。
    #
    # 這一段是必要的防呆。手動模式（修代號、整頓、校對價位、補跑等）不走
    # 每日排程那套「今天有沒有新影片」的判斷，若讓探測往下走，會有兩種壞結果：
    #   1. 探測步驟自己把整頓工作做掉了，而真正的執行步驟卻因為
    #      has_work 沒被設定而被跳過，看起來像是沒跑。
    #   2. 需要 Gemini 的模式會在探測階段就要求金鑰，但探測步驟刻意沒有帶，
    #      於是整個工作在第一步就失敗。
    # 手動觸發本來就是人明確要它跑，不需要探測代為判斷。
    if PREFLIGHT and (picked or REFRESH_SITE):
        label = "、".join(picked) if picked else "只刷新網站"
        print(f"探測：手動模式（{label}），直接放行。")
        write_preflight("true", f"手動模式：{label}")
        return

    # refresh_site 不算模式，它是附掛在任一模式之後的動作，可以與其他選項同時勾。
    # 只勾它、其他都沒勾時，代表「資料不用動，我只想讓網站立刻用現有資料重算一次」，
    # 這時不該往下跑抓影片的流程，刷新完就結束。
    if REFRESH_SITE and not picked:
        print("模式：只刷新網站。不動任何資料，只要求 Apps Script 用現有資料重算全站。")
        maybe_refresh_site()
        return

    if REPAIR_CODES:
        print("模式：純修代號。不碰 NotebookLM，不呼叫 Gemini。")
        repair_codes_only(ss)
        maybe_refresh_site()
        return

    if RECLASSIFY:
        print("模式：重新分類。只讀操作紀錄既有內容，依理由摘錄的情緒把觀望類")
        print("改寫成觀望不碰或觀望注意。不碰逐字稿、不呼叫 Gemini，不改動買入與賣出。")
        reclassify_from_transcripts(ss)
        maybe_refresh_site()
        return

    if ADMIN_JOB:
        print("模式：後台工單。逐字稿已由管理者貼進試算表，這裡把後面的流程跑完。")
        run_admin_job(ss)
        maybe_refresh_site()
        return

    if FIX_PRICES:
        print("模式：價位說明校對。修正方向矛盾、非股價數字、概數當精確價三類錯誤。")
        print("先用規則快篩，只有可疑的列才送 Gemini。用已存逐字稿，不重抓影片。")
        fix_prices_all(ss)
        maybe_refresh_site()
        return

    if RECONCILE:
        print("模式：整頓。AI 判定產業並刪除、抽取張震明講的買入價並核對後寫回。")
        print("用已存逐字稿，不重抓影片。")
        reconcile_all(ss)
        maybe_refresh_site()
        return

    # 接下來的流程都會用到 NotebookLM。先把試算表保存的最新憑證套用上去：
    # Secret 裡那份只是「種子」，真正在用的是輪換後、存回試算表的最新版本。
    # 探測模式不碰 NotebookLM，所以不需要這一步。
    if not PREFLIGHT:
        load_saved_auth(ss)
        _AUTH_FP[0] = _auth_fingerprint(_read_local_auth() or {})

    if FILL_BLANKS:
        print("模式：補空白。逐一檢視影片清單，補齊缺逐字稿的列。")
        fill_video_blanks(ss)
        save_rotated_auth(ss, _AUTH_FP[0])
        return

    feed = [v for v in fetch_feed() if is_target(v["title"])]
    print(f"RSS 取得 {len(feed)} 支符合關鍵字的影片")

    old = [v for v in feed if v["date"] < MIN_DATE]
    feed = [v for v in feed if v["date"] >= MIN_DATE]
    if old:
        print(f"略過 {len(old)} 支 {MIN_DATE:%Y/%m/%d} 之前的舊影片："
              + "、".join(v["date"].strftime("%Y/%m/%d") for v in old))
    print(f"待處理範圍內共 {len(feed)} 支")

    if not feed:
        raise RuntimeError(
            f"RSS 沒有任何標題含 {TITLE_KEYWORDS} 且日期在 {MIN_DATE:%Y/%m/%d} 之後的影片，"
            f"請確認頻道 ID 與關鍵字設定。"
        )

    done = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}
    done_trades = existing_dates(ss, "操作紀錄")
    done_holds = existing_dates(ss, "會員持股")
    print(f"雲端已有操作紀錄 {len(done_trades)} 天、會員持股 {len(done_holds)} 天")

    if BACKFILL:
        targets = [v for v in feed if done.get(v["id"]) != "完成"]
        if not targets:
            print("沒有需要回補的影片")
            return
        print(f"回補模式：共 {len(targets)} 支")
        targets.sort(key=lambda v: v["date"])       # 由舊到新，維持試算表時序
        if PREFLIGHT:
            print(f"探測：補跑模式有 {len(targets)} 支待處理。")
            write_preflight("true" if targets else "false", f"補跑 {len(targets)} 支")
            return
        for v in targets:
            if out_of_budget():
                print("時間預算用盡，本輪先停，剩下的下次再補。")
                break
            process_one(ss, v, done_trades, done_holds)
        save_rotated_auth(ss, _AUTH_FP[0])
        return

    today = datetime.now(TAIPEI).date()

    # 週六日不開盤、正常沒有盤中直播。即使有人手動在週末觸發，
    # 也不要標「今日無影片」或示警，直接安靜結束。
    if today.weekday() >= 5:   # 5 週六, 6 週日
        print("今天是週末，不開盤，略過。")
        write_preflight("false", "週末不開盤")
        return

    # ------------------------------------------------------------------ #
    # 探測模式（PREFLIGHT=true）
    #
    # 只用 YouTube API 與試算表判斷「現在到底有沒有事情要做」，
    # 不碰 NotebookLM、不呼叫 Gemini、不需要登入憑證，幾秒就跑完。
    #
    # 為什麼值得單獨做這一步：真正花時間與額度的是 NotebookLM 與 Gemini，
    # 而一天當中大多數的觸發點其實是空跑的（直播還沒結束、VOD 還沒生成）。
    # 先探一次，沒事就讓整個工作提早結束，後面那些昂貴的步驟根本不會啟動，
    # 連登入憑證都不會用到——憑證失效時也就不會在這些空跑的時段一直報錯。
    # ------------------------------------------------------------------ #
    if PREFLIGHT:
        feed_now = [v for v in fetch_feed() if is_target(v["title"]) and v["date"] >= MIN_DATE]
        done_now = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}
        todays = [v for v in feed_now if v["date"] == today]
        now_h = datetime.now(TAIPEI).hour

        if not todays:
            # 這裡要小心，不能一律「沒影片就跳過」。
            #
            # 內部輪詢是這套系統的核心：一個 job 進來之後每三分鐘敲一次門，
            # 敲到 VOD 出現為止。如果影片還沒出現就直接跳過，等於把輪詢廢掉，
            # 又退回去依賴 GitHub cron 準點觸發，而那正是當初要解決的問題。
            #
            # 所以只跳過「確定不可能有 VOD」的時段：直播進行中。
            # 直播約在台灣 12:30 到 13:00 結束，YouTube 再花十幾分鐘轉檔，
            # 因此 VOD_EARLIEST_HOUR 之前不管怎麼敲都不會有東西。
            if now_h >= GIVE_UP_HOUR:
                print("已到收工時間仍無今日影片。要跑最後一輪以標記「今日無影片」。")
                write_preflight("true", "收工時間，需標記今日無影片")
            elif now_h < VOD_EARLIEST_HOUR:
                print(f"現在台灣 {now_h} 點，直播還在進行，VOD 不可能存在，本輪跳過。")
                write_preflight("false", f"台灣 {now_h} 點，早於 VOD 最早可能時間")
            else:
                print(f"RSS 還沒出現 {today} 的影片，但已進入等待窗，要進去輪詢。")
                write_preflight("true", "等待窗內，需輪詢等 VOD")
            return

        v = todays[0]
        status = done_now.get(v["id"], "")
        if status == "完成":
            print(f"今日影片 {v['id']} 已處理完成，沒有事情要做。")
            write_preflight("false", "今日影片已完成")
            return

        print(f"今日影片 {v['id']} 狀態為「{status or '未處理'}」，需要執行。")
        write_preflight("true", f"待處理：{v['id']}（{status or '未處理'}）")
        return

    # ------------------------------------------------------------------ #
    # 內部輪詢循環。
    #
    # 為什麼要這樣：GitHub 的 cron 是 best-effort，尖峰時段會大量漏跑或延遲，
    # 塞很多個 cron 觸發點，實際跑起來的次數遠少於預期（你看到的 12:30 才 2 次
    # 就是這個原因）。與其依賴 GitHub 準點觸發很多次，不如只讓它觸發「一次」，
    # 進來之後由這支程式自己每隔幾分鐘敲一次門，敲到抓到逐字稿、或敲到收工時間
    # （台灣 15:00）為止。這樣輪詢次數由我們自己精準控制，不再受 GitHub 影響。
    #
    # 每敲一次門就寫一列系統狀態，所以「本日輪詢次數」會如實反映實際敲門次數。
    # 手動補跑（backfill / final / fill_blanks 等）不走這個循環，維持單次執行。
    # ------------------------------------------------------------------ #
    def handle_today_once():
        """敲一次門。回傳 True 表示今天已完成或確定無影片，可以收工。"""
        feed_now = [v for v in fetch_feed() if is_target(v["title"]) and v["date"] >= MIN_DATE]
        done_now = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}
        todays = [v for v in feed_now if v["date"] == today]
        now_h = datetime.now(TAIPEI).hour

        if not todays:
            if now_h >= GIVE_UP_HOUR:
                mark_status(ss, f"NO_VIDEO_{today}", today.strftime("%Y/%m/%d"), "", "今日無影片")
                print("已到收工時間仍無今日影片，判定今日無影片")
                return True
            print(f"RSS 尚未出現 {today} 的影片，稍後再敲")
            return False

        v = todays[0]
        status = done_now.get(v["id"], "")
        if status == "完成":
            print("今日影片已處理完成，收工")
            return True
        if status == "處理中":
            print("偵測到前次殘留的『處理中』狀態，重新處理")
        if status == "等待中":
            print("前一輪 VOD 尚未就緒，本輪再敲一次門")

        try:
            process_one(ss, v, done_trades, done_holds)
            # process_one 成功且狀態為完成才收工；仍在等待則繼續循環
            fresh = {str(r["影片ID"]): str(r["處理狀態"]) for r in video_rows(ss)}
            return fresh.get(v["id"]) == "完成"
        except NotReadyYet as e:
            print(f"VOD 還沒好：{e}，稍後再敲")
            write_status_log(ss, "等待中", str(e))
            return False

    # 不走內部循環的情況：手動補跑用長逾時、只敲一次就結束。
    if not POLL_LOOP:
        handle_today_once()
        save_rotated_auth(ss, _AUTH_FP[0])
        return

    # 走內部循環：每 POLL_INTERVAL 秒敲一次，直到收工或超過時間預算。
    poll_n = 0
    last_err, same_err_n = "", 0
    # 同一個錯誤連續這麼多次就停。認證過期已經由 looks_like_auth_error 擋掉了，
    # 這一道是防未來冒出沒見過的錯誤形狀：任何「每次都一樣的失敗」都不會因為
    # 多等 180 秒而變好，繼續敲只是把時間預算燒完，還讓工作紀錄被同一行洗版。
    MAX_SAME_ERR = 3

    while True:
        now = datetime.now(TAIPEI)
        poll_n += 1
        print(f"\n--- 第 {poll_n} 次輪詢　{now:%H:%M:%S} 台北時間 ---")
        write_status_log(ss, "輪詢", f"第 {poll_n} 次輪詢")

        try:
            if handle_today_once():
                break
            last_err, same_err_n = "", 0     # 這一輪沒炸，重新計數
        except AuthExpired:
            raise   # 認證過期交給外層處理，寫「認證過期」狀態並結束
        except Exception as e:
            msg = str(e)

            # 認證失效有時是在這一層才看得出來（用戶端把它包成一般例外）。
            # 判定成立就直接升級成 AuthExpired，讓外層寫「認證過期」並寄信通知，
            # 不要留在迴圈裡空轉。
            if looks_like_auth_error(e):
                print(f"本次輪詢的錯誤研判為登入失效：{msg}")
                raise AuthExpired(msg)

            # 單次敲門的非致命錯誤，記錄後繼續下一輪，不讓整個循環中斷
            print(f"本次輪詢出錯（不中斷循環）：{msg}")
            write_status_log(ss, "失敗", msg)

            # 錯誤訊息裡的請求 ID 每次都不同，比對時要抽掉才看得出是不是同一種。
            sig = re.sub(r"[0-9a-f]{6,}", "", msg)
            if sig == last_err:
                same_err_n += 1
                if same_err_n >= MAX_SAME_ERR:
                    print(f"同一個錯誤已連續 {same_err_n + 1} 次，重試不會有幫助，停止輪詢。")
                    print("請檢查上面的錯誤訊息；若與登入或授權有關，"
                          "請重新產生 storage_state.json 並更新 NOTEBOOKLM_AUTH_JSON。")
                    write_status_log(ss, "失敗", f"同一錯誤連續 {same_err_n + 1} 次，停止輪詢：{msg}")
                    break
            else:
                last_err, same_err_n = sig, 0

        # 收工時間到（台灣 15:00）或時間預算用盡就停。
        # 到收工時間時 handle_today_once 內部已會標「今日無影片」，這裡不重複標。
        if datetime.now(TAIPEI).hour >= GIVE_UP_HOUR:
            print("已到收工時間，停止輪詢")
            break
        if out_of_budget():
            print("時間預算用盡，本 job 先停，交給下一次 cron 觸發接力。")
            break

        print(f"等待 {POLL_INTERVAL} 秒後再敲……")
        time.sleep(POLL_INTERVAL)

    # 輪詢結束（收工、預算用盡或已完成）。把輪換過的憑證存回試算表，
    # 讓下一次執行接續使用，而不是每次都退回 Secret 裡那份越來越舊的種子。
    save_rotated_auth(ss, _AUTH_FP[0])


if __name__ == "__main__":
    try:
        main()
        if _SS is not None:
            write_status_log(_SS, "完成", f"本輪正常結束（未潤飾段數 {POLISH_DEGRADED}）")
    except NotReadyYet as e:
        # 綠燈離開。VOD 還沒好不是壞掉，不該亮紅燈，也不該觸發失敗告警。
        print(f"本輪未取得逐字稿：{e}")
        if _SS is not None:
            write_status_log(_SS, "等待中", str(e))
        sys.exit(0)
    except AuthExpired as e:
        # 認證過期。重試沒有用，必須換新的 storage_state.json。
        print("流程失敗：NotebookLM 登入狀態已失效，需要重新產生 storage_state.json "
              "並更新 GitHub Secret NOTEBOOKLM_AUTH_JSON。", file=sys.stderr)
        print(f"原始訊息：{e}", file=sys.stderr)
        if _SS is not None:
            write_status_log(_SS, "認證過期",
                             "NotebookLM 登入狀態失效，請在本機執行 notebooklm login "
                             "後，把新的 storage_state.json 內容更新到 GitHub Secret "
                             "NOTEBOOKLM_AUTH_JSON。原始訊息：" + str(e))
        sys.exit(1)
    except Exception as e:
        print(f"流程失敗：{e}", file=sys.stderr)
        if _SS is not None:
            write_status_log(_SS, "失敗", str(e))
        sys.exit(1)
