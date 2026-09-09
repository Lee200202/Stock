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
import hashlib
import html as html_lib
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
PIPELINE_FEATURES = ("preflight,auth-rotation,lazy-gemini-key,cmoney-audit-v4,natural-reasons,"
                     "sms-checkpoint,sms-item-verifier,dailyk-html-retry,"
                     "sms-scope-v6,sms-quota-breaker,sms-incremental-write,sms-prompt-v6")

# ------------------------------------------------------------------ #
# 會員簡訊：解析版本與配額防護
#
# 解析版本是這一版最重要的欄位。先前判斷「這篇解析過了沒有」只能看
# 解析明細是不是空的，但抓取當下就會先塞一個 "[]" 佔位字串進去，
# 於是「還沒解析」與「解析後真的沒有個股」在資料上長得一模一樣。
# 併入過去資料那一步把佔位的 "[]" 當成有效檢查點，完全不呼叫 Gemini
# 就把該列標成「已解析（無可收錄，稽核通過）」——這就是九月七日那篇
# 「1519華城 775 以上全數獲利賣出」被判成無可收錄的原因。
#
# 改法：另開一欄「解析版本」，只有 Gemini 真的判定過才會寫值。
#   空白      → 從來沒有被 AI 判定過，需要解析
#   等於現值  → 已用現行提示詞判定過，無論收錄幾筆都不再重跑
#   不等於    → 舊提示詞判定過，只有明確按「重新解析」時才重跑
# 有了它就不必再用「無可收錄」這種狀態字串去猜，也不會每天把同一批
# 沒有個股的純盤勢文章重新丟給 Gemini 燒配額。
# ------------------------------------------------------------------ #
SMS_PROMPT_VERSION = "sms-prompt-v6"

# 單次執行最多允許的 Gemini 呼叫數。免費層每日請求數有限，而會員簡訊
# 一篇可能拆成數段，沒有上限的話一次執行就能把一整天的額度用光，
# 連帶讓當天的逐字稿擷取（tag=extract）拿不到配額而失敗。
# 做不完不是失敗：沒輪到的列維持原狀，下一次執行接著做。
SMS_AI_MAX_CALLS = max(1, int(os.environ.get("SMS_AI_MAX_CALLS", "45") or 45))

# 單次執行最多處理幾篇需要 AI 的文章。與上面那條是兩道獨立的閘門，
# 一篇拆很多段時由呼叫數擋，一篇一段時由篇數擋。
SMS_MAX_AI_ARTICLES = max(1, int(os.environ.get("SMS_MAX_AI_ARTICLES", "25") or 25))

# 連續幾篇文章撞到配額就中止本輪。免費層的每分鐘限制退避還有意義，
# 每日限制退避永遠沒有意義，再等下去只是把 job 時間燒完。
SMS_MAX_QUOTA_STRIKES = max(1, int(os.environ.get("SMS_MAX_QUOTA_STRIKES", "2") or 2))


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
# 多把金鑰。與 Apps Script 的 Aiservice.gs 是同一套命名，兩邊要一致：
#   GEMINI_API_KEY      第一把
#   GEMINI_API_KEY_2..5 第二到第五把
#   GEMINI_API_KEYS     多把用逗號寫在同一個變數裡
#
# 為什麼需要：免費層的每日請求數是「以 Google 專案為單位」計算的。
# 不同專案各有各的額度，一把用完換下一把，全部用完才是真的沒有額度。
# Apps Script 那邊早就支援了，這裡先前只讀一把，於是同樣是額度問題，
# 後台還能繼續跑，GitHub 這條卻整天停擺。
def _load_gemini_keys() -> list[tuple[str, str]]:
    """
    回傳 [(來源名稱, 金鑰)]。名稱要帶著走，出問題時才講得出「去修哪一個 Secret」。

    分隔字元同時吃逗號與所有空白。GEMINI_API_KEYS 這種「一個變數裝多把」的
    寫法，人最自然的貼法是一行一把；只用逗號切的話，整段連同換行會被當成
    「一把很長的金鑰」，送出去就是一個看不懂的 404，而真正的原因只是貼法。
    """
    out, seen = [], set()

    def push(source: str, raw: str):
        for part in re.split(r"[,\r\n]+", str(raw or "")):
            # 只去掉頭尾的空白與引號。有些人會把金鑰連同引號一起貼進 Secret，
            # 那是很容易犯又很難看出來的錯，順手處理掉。
            t = part.strip().strip("'\"").strip()
            if not t or t in seen:
                continue
            # 這裡刻意不檢查金鑰的字元組成。
            #
            # 先前有一版加了 [A-Za-z0-9_-]{20,} 的白名單，結果把三把完全正常、
            # 長度 53 的金鑰全部擋掉，變成「缺少環境變數 GEMINI_API_KEY」——
            # 明明設了卻說沒設，比原本的問題糟得多。金鑰的格式是 Google 說了算，
            # 而且會改；我們這邊沒有任何權威可以宣告「長這樣才算金鑰」。
            # 真的無效時，API 會回 400 API_KEY_INVALID，那一條路徑已經會
            # 標記該把金鑰、換下一把並指名是哪一個 Secret 要修。
            #
            # 只擋一種：短到不可能是金鑰。這個門檻低到不會冤枉任何真金鑰。
            if len(t) < 10:
                print(f"警告：{source} 裡有一段太短（{len(t)} 字），不像金鑰，已略過。")
                continue
            seen.add(t)
            out.append((source, t))

    push("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", ""))
    push("GEMINI_API_KEYS", os.environ.get("GEMINI_API_KEYS", ""))
    for i in range(2, 6):
        push(f"GEMINI_API_KEY_{i}", os.environ.get(f"GEMINI_API_KEY_{i}", ""))
    return out


GEMINI_KEY_ENTRIES = _load_gemini_keys()
GEMINI_KEYS = [k for _, k in GEMINI_KEY_ENTRIES]
GEMINI_API_KEY = GEMINI_KEYS[0] if GEMINI_KEYS else ""

# 這一輪不能再用的金鑰。分成兩種原因，因為處理方式完全不同：
#   quota   今日額度用完。明天會自己好，不必動任何設定。
#   broken  金鑰本身有問題（無效、專案沒開通 API、那個專案看不到這個模型）。
#           明天不會自己好，要人去修，所以訊息必須指名是哪一個 Secret。
_KEY_STATE = {"idx": 0, "dead": {}}


def key_label(i: int) -> str:
    if not (0 <= i < len(GEMINI_KEY_ENTRIES)):
        return "未知金鑰"
    return f"第 {i + 1} 把（{GEMINI_KEY_ENTRIES[i][0]}）"


def current_gemini_key() -> str:
    return GEMINI_KEYS[_KEY_STATE["idx"]] if GEMINI_KEYS else ""


def rotate_gemini_key(tag: str, why: str = "quota", detail: str = "") -> bool:
    """把目前這一把標記為不可用並換下一把。沒有可用的了就回 False。"""
    cur = _KEY_STATE["idx"]
    # 存的是「可以直接印給人看的原因」，不是內部代號。
    # 存代號的話，最後那句彙總會變成「第 1 把：broken」，等於沒講。
    _KEY_STATE["dead"][cur] = "quota" if why == "quota" else (detail or "設定有問題")
    reason = "今日額度已用盡" if why == "quota" else f"金鑰不可用（{detail or '設定有問題'}）"
    for i in range(len(GEMINI_KEYS)):
        if i not in _KEY_STATE["dead"]:
            _KEY_STATE["idx"] = i
            print(f"Gemini {tag}：{key_label(cur)} {reason}，改用 {key_label(i)}"
                  f"（共 {len(GEMINI_KEYS)} 把）")
            return True
    print(f"Gemini {tag}：{key_label(cur)} {reason}，而且已經沒有其他可用的金鑰了。")
    return False


def broken_keys_report() -> str:
    """
    把「設定有問題」的金鑰列出來。這種明天不會自己好，要指名讓人去修。

    只取原因的第一句。完整說明在前面每一次輪替時已經印過一次，
    這裡是收尾的彙總，三把金鑰各印一整段會把真正的錯誤訊息推到看不見。
    """
    bad = []
    for i, v in sorted(_KEY_STATE["dead"].items()):
        if v == "quota":
            continue
        bad.append(f"{key_label(i)}：{str(v).split('。')[0]}")
    return "；".join(bad)


def require_gemini_key():
    if not GEMINI_KEYS:
        raise SystemExit("缺少環境變數 GEMINI_API_KEY，請到 GitHub Secrets 或 Variables 補上。"
                         "額度不夠時可另外設 GEMINI_API_KEY_2 到 _5，或用逗號／換行寫在 GEMINI_API_KEYS。")
BACKFILL = os.environ.get("BACKFILL", "false").strip().lower() == "true"
FINAL_ATTEMPT = os.environ.get("FINAL_ATTEMPT", "false").strip().lower() == "true"

# 解析會員簡訊模式。處理「會員簡訊」分頁中待解析的簡訊。
PARSE_SMS = os.environ.get("PARSE_SMS", "false").strip().lower() == "true"
SMS_SINCE = os.environ.get("SMS_SINCE", "").strip()
SMS_MODE = os.environ.get("SMS_MODE", "").strip().lower()
SMS_IDS = os.environ.get("SMS_IDS", "").strip()
SMS_JOB_ID = os.environ.get("SMS_JOB_ID", "").strip()
SMS_RESUME = os.environ.get("SMS_RESUME", "false").strip().lower() == "true"
CMONEY_MEMBER_ID = os.environ.get("CMONEY_MEMBER_ID", "").strip()
# 會員簡訊通常一篇有數段指令。刻意限制為每分鐘約 8 次以下，避免免費額度
# 因短時間連續解析多篇而觸發 429；仍可用 GitHub Variable 調整。
SMS_AI_GAP = max(2.0, float(os.environ.get("SMS_AI_GAP_SEC", "7") or 7))

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

# 全面重整模式。把下游的「全面重整」一棒一棒驅動完：補正名稱與代號、
# 稽核與複審、價位校對、補齊日K、重算持股追蹤、重寫郵件。
#
# 為什麼由這裡驅動而不是讓 Apps Script 自己排觸發器：Apps Script 每個指令碼
# 的觸發器上限是 20 個，裝滿之後 create() 直接拋例外，按鈕按下去只會跳
# 「這個指令碼包含過多觸發條件」。改成從這裡逐次打網頁請求，每一棒都是一次
# 獨立的請求、各自享有完整的執行額度，一個觸發器都不用，而且每一棒的結果
# 都印在 Actions 日誌裡看得到。
FULL_FIX = os.environ.get("FULL_FIX", "false").strip().lower() == "true"

# 全面重整的範圍。這個值不影響執行結果——真正決定範圍的是下游的狀態，
# 這裡只是逐棒敲門，每一棒做什麼是下游決定的。
# 帶著它只為了印進 Actions 日誌：近一周與全部歷史的耗時差十倍，
# 事後回頭看某一次執行時，分不出跑的是哪一種會很難判斷。
FULL_FIX_SCOPE = os.environ.get("FULL_FIX_SCOPE", "").strip() or "week"
FULL_FIX_SCOPE_LABEL = {
    "week": "最近七日", "7days": "最近七日", "month": "近一個月", "all": "全部",
}.get(FULL_FIX_SCOPE, FULL_FIX_SCOPE)

# 從哪一步開始刷新。空的就是從頭。
# 用在「某一步失敗、修好之後只想從那裡接著跑」，不必整條鏈重來。
# 刷新鏈的每一步彼此獨立，跳過前面幾步不會讓後面算錯。
REFRESH_FROM = os.environ.get("REFRESH_FROM", "").strip()

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

# 全面重整要把整段歷史一天一天跑過，一百多個交易日要好幾個小時，
# 25 分鐘的預算會讓它每次只前進一小段，得手動重按十幾次。
# 沒有另外指定時就給五小時，workflow 那邊的 job 逾時也跟著放寬。
if FULL_FIX and not os.environ.get("TIME_BUDGET_SEC", "").strip():
    TIME_BUDGET = 18000

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

    分成六次請求依序呼叫，不是一次要求跑完。
    下游是 Apps Script，網頁請求超過 6 分鐘會被直接砍掉，而整條重算鏈
    遠超過那個上限，一次打必定失敗且看不出原因。

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

    # 逐步呼叫，不要一次要求跑完整條鏈。
    #
    # 下游是 Apps Script，網頁請求超過 6 分鐘會被直接砍掉——那不是拋例外，
    # 是執行被中止，所以下游的 try/catch 接不到，我們只會收到一張
    # 看不出原因的錯誤頁。整條重算鏈遠超過 6 分鐘，一次打必定失敗。
    #
    # 拆成六次請求之後，每一步都在自己的額度內跑完，而且每一步的成敗
    # 都看得到，卡在哪一步一目了然。
    STEPS = [
        ("purge", "清除產業列"),
        # 品質關卡的三段。做完之後，網站與推播信裡就不會再出現
        # 「他今天在操作一檔早就出清的股票」。
        # 排在代號比對之前，因為稽核會補進新的列，那些列還沒有代號。
        #
        # 先前這三段擠成一個 gate，畫面上與日誌上都只看得到一行，
        # 那一行會停三到六分鐘，卡住時看不出卡在哪一段。
        ("gate1", "稽核補漏"),
        ("gate2", "內容複審"),
        ("gate3", "重寫整理"),
        ("codes", "代號比對"),
        ("fund", "更新基本面"),
        ("dailyk", "補齊日K"),
        ("tracker", "重算持股追蹤"),
        ("perf", "記錄績效"),
    ]

    # 指定了起點就從那裡開始。找不到那個代號就當作沒指定，從頭跑——
    # 打錯一個字就整條鏈不做，比從頭跑一次糟得多。
    if REFRESH_FROM:
        keys = [k for k, _ in STEPS]
        if REFRESH_FROM in keys:
            at = keys.index(REFRESH_FROM)
            skipped = [lb for _, lb in STEPS[:at]]
            STEPS = STEPS[at:]
            print(f"指定從「{STEPS[0][1]}」開始，略過前面 {len(skipped)} 步："
                  + "、".join(skipped))
        else:
            usable = "、".join(keys)
            print(f"不認得的起始步驟 {REFRESH_FROM}，改為從頭跑。可用的有：{usable}")

    # 動手之前先確認雙方版本一致。
    #
    # 貼了新程式碼但沒部署新版本，是這個專案最常見的坑，而症狀常常偽裝成
    # 別的問題：舊版收到它不認得的 step 參數會直接忽略，照舊跑完整條鏈然後超時，
    # 回應看起來像「這一步太慢」，跟版本沒對上完全看不出關係。
    # 先問一次 ping，把這種情況攔在前面並講清楚。
    print("\n先確認下游版本……", end=" ", flush=True)
    try:
        pr = requests.get(APPS_SCRIPT_URL, params={"action": "ping"},
                          timeout=60, headers={"User-Agent": "zhangzhen-pipeline"})
    except Exception as e:
        print(f"連線失敗：{e}")
        print(f"設定的網址：{APPS_SCRIPT_URL}")
        return

    try:
        pinfo = json.loads(pr.text[:4000])
    except Exception:
        # 這裡一定要把實際收到什麼印出來。
        #
        # 只說「不是 JSON」等於什麼都沒說，而這個錯誤最常見的成因是
        # 「Secret 裡的網址指向另一個舊部署」——同一支 ping 在瀏覽器好好的，
        # 在這裡卻拿到 HTML，因為兩邊打的根本不是同一個部署。
        # 把最終網址與內容開頭印出來，一眼就能比對出來。
        body = (pr.text or "")[:200].replace("\n", " ")
        low = body.lower()
        print(f"回應不是 JSON（HTTP {pr.status_code}）")
        print(f"  設定的網址：{APPS_SCRIPT_URL}")
        print(f"  最終網址　：{pr.url}")
        print(f"  內容開頭　：{body}")
        print("")
        if "accounts.google.com" in low or "servicelogin" in low:
            print("  >>> 這是 Google 登入頁。部署的「誰可以存取」不是「所有人」。")
            print("      管理部署作業 → 編輯 → 誰可以存取改成「所有人」。")
        elif "<title>error</title>" in low:
            print("  >>> 這是 Apps Script 錯誤頁，doGet 執行時出錯。")
            print("      到 Apps Script 左側「執行紀錄」看實際訊息。")
        else:
            print("  >>> 收到的是網頁而不是 JSON，代表這個部署的程式碼沒有 ping 分支，")
            print("      也就是它跑的是舊版。最常見的原因是這裡設定的網址")
            print("      與你剛才在瀏覽器測試成功的那一個，不是同一個部署。")
            print("")
            print("      怎麼確認：在 Apps Script 執行 showDeployInfo()，")
            print("      比對它印出來的網址與上面「設定的網址」，")
            print("      兩者 /macros/s/ 後面那一長串 ID 必須完全一致。")
            print("      不一致就把 showDeployInfo() 印的那個更新到 GitHub Secret")
            print("      的 APPS_SCRIPT_URL，然後重跑。")
        return

    feats = pinfo.get("features") or []
    print(f"build={pinfo.get('build', '未知')}")
    if "refresh-step" not in feats:
        print("")
        print("=" * 60)
        print("下游版本過舊：不支援分步重算（refresh-step）。")
        print("=" * 60)
        print("舊版會忽略 step 參數、照舊一次跑完整條鏈，必定超過 6 分鐘而失敗，")
        print("而失敗的樣子跟「這一步太慢」一模一樣，很容易誤判。")
        print("")
        print("怎麼修：")
        print("  1. 把最新的 Code.gs 貼進 Apps Script")
        print("  2. 部署 → 管理部署作業 → 編輯（鉛筆）→ 版本選「新版本」→ 部署")
        print("     ※ 只貼程式碼不重新部署是無效的，線上跑的仍是舊版")
        print("  3. 用瀏覽器開 APPS_SCRIPT_URL?action=ping，")
        print("     確認回應裡的 features 含有 refresh-step，再重跑本流程")
        return {"ok": False, "done": 0, "failed": 1}

    if "dailyk-safe-chunks" not in feats:
        print("")
        print("=" * 60)
        print("下游版本過舊：補齊日K仍使用會重寫整張工作表的大批次版本。")
        print("=" * 60)
        print("請更新 Apps Script 的 Code.gs 與 Cachebuilder.gs，並建立『新版本』部署。")
        print("部署後重新開 APPS_SCRIPT_URL?action=ping，確認 features 含 dailyk-safe-chunks。")
        print("目前日K游標會保留；更新部署後可直接從 dailyk 接續，不必重跑前六步。")
        return {"ok": False, "done": 0, "failed": 1}

    # 步驟代號要與下游對得上。
    #
    # 稽核與複審這一步，下游 Code.gs 實作的名稱是 review，早期版本叫 gate。
    # 名稱沒對上時下游只會回「不認得的步驟」，那一步就每天靜靜地失敗，
    # 而複審沒跑過的資料看起來跟跑過的一模一樣，不會有任何徵兆。
    # ping 已經回報它認得哪些步驟，直接照著改名，兩邊誰先更新都不會壞。
    known = pinfo.get("steps") or []
    if known:
        aligned, dropped = [], []
        for k, lb in STEPS:
            if k in known:
                aligned.append((k, lb))
                continue
            # 下游還沒部署新版時，三段都退回舊的 gate。
            # gate 呼叫一次推進一段，所以三段各打一次剛好把整套跑完，
            # 名稱不同但做的事一模一樣。
            alt = next((a for a in ("review", "gate") if a in known), "")
            if k in ("review", "gate", "gate1", "gate2", "gate3") and alt:
                print(f"  下游把「{lb}」叫做 {alt}（本地寫的是 {k}），自動改用下游的名稱")
                aligned.append((alt, lb))
            else:
                dropped.append(f"{lb}（{k}）")
        if dropped:
            print("  下游沒有這些步驟，本次略過：" + "、".join(dropped))
        STEPS = aligned
    print(f"\n要求 Apps Script 重算全站，分成 {len(STEPS)} 步依序執行……")
    ok_n, fail_n = 0, 0

    def sms_sync_progress(done, label, batch=0, result=""):
        """
        保留成空函式，刻意不再回報到會員簡訊的進度條。

        全站重算與會員簡訊是兩條獨立的鏈。把重算進度寫進簡訊狀態，
        會讓「這一篇簡訊收錄了哪些個股」的畫面被「補齊日K 第 12 批」
        這種訊息蓋掉，而且簡訊流程現在根本不會呼叫這條鏈。
        呼叫點留著是為了不動這支函式其餘的控制流。
        """
        return

    for i, (key, label) in enumerate(STEPS, 1):
        # 分批的步驟要重複呼叫到做完為止。
        # 一次做不完是設計，不是失敗：每次只跑約九十秒就回報進度，
        # 這樣每個請求都遠在時間上限之內，不會再被切斷連線。
        rounds, MAX_ROUNDS = 0, 40
        step_failed = False
        while True:
            rounds += 1
            sms_sync_progress(i - 1, label, rounds, "執行中")
            tag = f"  [{i}/{len(STEPS)}] {label}"
            if rounds > 1:
                tag += f"（第 {rounds} 批）"
            print(tag + " ……", end=" ", flush=True)

            body, resp = None, None
            for attempt in range(3):
                try:
                    resp = requests.get(
                        APPS_SCRIPT_URL,
                        params={"action": "refresh", "key": ADMIN_KEY, "step": key},
                        timeout=300,
                        headers={"User-Agent": "zhangzhen-pipeline"},
                    )
                    candidate = resp.text[:4000]
                    candidate_low = candidate.lower()
                    is_html = "<html" in candidate_low or "<!doctype" in candidate_low
                    # 日K舊批次偶爾在完成部分工作後才被 Google 前端切斷，會回 HTML。
                    # 游標與已寫入資料都在 Apps Script；短暫等待後重打同一步即可接續。
                    if key == "dailyk" and is_html and attempt < 2:
                        print(f"日K批次回 HTML（HTTP {resp.status_code}），等待後自動重試同一步 {attempt + 2}/3")
                        time.sleep(10)
                        continue
                    body = candidate
                    break
                except requests.exceptions.RequestException as e:
                    # 連線被切斷通常代表那一次跑太久。等一下再試，
                    # 而且因為進度有存游標，重試會從中斷的地方接著做。
                    if attempt == 2:
                        print(f"連線失敗（已重試 3 次）：{e}")
                    else:
                        time.sleep(10)
            if body is None:
                fail_n += 1
                step_failed = True
                sms_sync_progress(i - 1, label, rounds, "連線失敗")
                break

            low = body.lower()
            if "<title>error</title>" in low or "docs/script/images/favicon" in low:
                fail_n += 1
                step_failed = True
                sms_sync_progress(i - 1, label, rounds, "下游錯誤")
                print("下游回錯誤頁")
                title_m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
                print(f"       HTTP {resp.status_code if resp is not None else '未知'}"
                      + (f"，標題：{html_lib.unescape(title_m.group(1)).strip()}" if title_m else ""))
                if plain:
                    print(f"       錯誤頁摘要：{html_lib.unescape(plain)[:240]}")
                print("       這一步在下游拋出例外或被時間上限中止。")
                print("       到 Apps Script 左側「執行紀錄」找最近一次 doGet，")
                print(f"       它會顯示 {label} 這一支函式的實際錯誤。")
                break
            if "<html" in low or "<!doctype" in low:
                fail_n += 1
                step_failed = True
                sms_sync_progress(i - 1, label, rounds, "回應格式錯誤")
                title_m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
                plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
                print(f"下游回 HTML，不是 JSON（HTTP {resp.status_code if resp is not None else '未知'}）。")
                if title_m:
                    print("       頁面標題：" + html_lib.unescape(title_m.group(1)).strip())
                if plain:
                    print("       回應摘要：" + html_lib.unescape(plain)[:240])
                print("       日K 游標與已完成批次均保留，可直接從補齊日K接續。")
                break

            try:
                data = json.loads(body)
            except Exception:
                fail_n += 1
                step_failed = True
                sms_sync_progress(i - 1, label, rounds, "回應無法解析")
                print(f"回應無法解析：{body[:120]}")
                break

            if not data.get("ok"):
                fail_n += 1
                step_failed = True
                sms_sync_progress(i - 1, label, rounds, "失敗")
                print(f"失敗：{data.get('error', body[:120])}")
                break

            # 後台按了取消。下游不會主動叫停我們，只能在這一步回一句「別打了」，
            # 所以這裡看到就整條鏈收工，不是只跳過這一步。
            if data.get("cancelled"):
                print(data.get("result", "已取消"))
                print("")
                print("刷新已在後台被取消，停止往下做。已完成的步驟都保留著，")
                print("之後在後台用「從這一步重跑」挑一個接著做即可。")
                return

            print(data.get("result", "完成"))
            sms_sync_progress(i if not (data.get("chunked") and not data.get("done")) else i - 1,
                              label, rounds, str(data.get("result", "完成"))[:120])

            # 還沒做完就再打一次；做完了才換下一步。
            if data.get("chunked") and not data.get("done"):
                if rounds >= MAX_ROUNDS:
                    fail_n += 1
                    step_failed = True
                    print(f"       已跑 {rounds} 批仍未完成，先停下來避免無限迴圈。")
                    print("       下次執行會從目前進度接著做，或在編輯器執行")
                    print("       resetDailyKCursor() 重設進度後重跑。")
                    break
                time.sleep(2)
                continue

            ok_n += 1
            break

        if step_failed:
            print(f"       已停止在「{label}」；後續相依步驟本輪不執行。")
            print(f"       修復後從 {key} 接續，即可再依序完成後面的重算。")
            break

    print(f"\n重算結束：成功 {ok_n} 步，失敗 {fail_n} 步。")
    if fail_n:
        print("失敗的步驟可以單獨重跑，其餘已完成的不必重來。")
        print("若某一步固定失敗，到 Apps Script 左側「執行紀錄」看那一支函式的錯誤。")
    else:
        print("網站已是最新內容。")
    return {"ok": fail_n == 0, "done": ok_n, "failed": fail_n}


def _full_fix_progress(last_seen):
    """
    問一次下游的全面重整進度。唯讀，不會做任何事。

    回傳 (有沒有前進, 目前位置)。查不到就回 (None, "")，讓呼叫端當成連線問題。
    """
    try:
        r = requests.get(
            APPS_SCRIPT_URL,
            params={"action": "refresh", "key": ADMIN_KEY, "step": "fullfixstate"},
            timeout=60,
            headers={"User-Agent": "zhangzhen-pipeline"},
        )
        d = json.loads(r.text[:800])
    except Exception:
        return None, ""
    if not d.get("ok"):
        return None, ""
    where = f"{d.get('processed', 0)}/{d.get('total', 0)} {d.get('result', '')}"
    return (where != last_seen), where


def drive_full_fix():
    """
    逐棒驅動下游的全面重整，直到做完或時間預算用盡。

    每一棒是一次 ?action=refresh&step=fullfix 的請求。下游做一小段就回報
    目前在哪一步、跑到第幾天，做不完就回 done=false，這裡再打一次。
    進度存在下游的指令碼屬性裡，所以就算這個 job 被中斷，下次進來也是
    從同一個地方接著做，不會重跑已經處理過的日期。
    """
    print(f"全面重整　範圍：{FULL_FIX_SCOPE_LABEL}（{FULL_FIX_SCOPE}）")
    print("　範圍由後台選定並存在下游的狀態裡，這裡印出來只是為了讓這一次執行看得出跑的是什麼。")
    print("　補齊日K與重算追蹤不受範圍限制，那兩步永遠看全部歷史。")

    if not APPS_SCRIPT_URL or not ADMIN_KEY:
        print("全面重整需要 APPS_SCRIPT_URL 與 ADMIN_KEY 兩個 Secret，缺一不可。")
        print("到 Apps Script 執行 showDeployInfo() 會把兩個值都印出來。")
        return

    # 先確認下游認得 fullfix 這一步。
    #
    # 不確認的話，部署沒更新時第一棒會拿到「不認得的步驟：fullfix」——
    # 那句話完全看不出真正的問題是「Code.gs 貼了但沒有部署新版本」，
    # 而那是這個專案最常見的坑。先問一次 ping 就能講清楚。
    try:
        pr = requests.get(APPS_SCRIPT_URL, params={"action": "ping"},
                          timeout=60, headers={"User-Agent": "zhangzhen-pipeline"})
        pinfo = json.loads(pr.text[:4000])
        feats = pinfo.get("features") or []
    except Exception as e:
        print(f"連不上下游或回應不是 JSON：{e}")
        print(f"設定的網址：{APPS_SCRIPT_URL}")
        return

    if "full-fix" not in feats:
        print("")
        print("=" * 60)
        print("下游版本過舊：不支援全面重整（full-fix）。")
        print("=" * 60)
        print("")
        print("這個網址回報的身分：")
        print(f"  build      {pinfo.get('build', '未知')}")
        print(f"  features   {'、'.join(feats) if feats else '（無）'}")
        print(f"  scriptId   {pinfo.get('scriptId', '（舊版不回報）')}")
        print(f"  它自己認得的網址  {pinfo.get('webAppUrl', '（舊版不回報）')}")
        print("")
        print("你設定的網址：")
        print(f"  {APPS_SCRIPT_URL}")
        if pr.url != APPS_SCRIPT_URL:
            print(f"  實際到達　：{pr.url}")
        print("")
        print("拿上面三項去對，成因只有三種：")
        print("")
        print("A. scriptId 與你在編輯器網址裡看到的那一串不同")
           # 編輯器網址長得像 script.google.com/home/projects/<scriptId>/edit
        print("   → 你改的是另一個專案。打開 scriptId 對得上的那一個再貼一次。")
        print("")
        print("B. scriptId 相同，但「它自己認得的網址」與你設定的網址不同")
        print("   → 這個專案有多個部署，你更新的是其中一個，")
        print("     而 APPS_SCRIPT_URL 指向另一個。兩個選一個：")
        print("     把 APPS_SCRIPT_URL 換成你剛更新的那個部署的網址，或")
        print("     回去把這個網址對應的那一個部署也更新成新版本。")
        print("")
        print("C. 兩個網址都相同，但 build 還是舊的")
        print("   → 更新部署時「版本」沒有選「新版本」，那個部署仍釘在舊版本。")
        print("     管理部署作業 → 編輯（鉛筆）→ 版本下拉選「新版本」→ 部署。")
        print("     選既有的版本號是無效的，那等於什麼都沒改。")
        print("")
        print("改完用瀏覽器開 APPS_SCRIPT_URL?action=ping 確認 features 有 full-fix，再重跑。")
        return

    print("\n開始驅動下游的全面重整。每一棒約四分半，做完會自己停。")
    rounds, fails = 0, 0
    last_seen = ""      # 上一次看到的進度字串，用來判斷逾時後有沒有前進
    while True:
        if out_of_budget():
            print("\n時間預算用盡，本次先停。進度存在下游，重跑一次會從這裡接著做。")
            return
        rounds += 1
        print(f"  第 {rounds} 棒 ……", end=" ", flush=True)

        try:
            r = requests.get(
                APPS_SCRIPT_URL,
                params={"action": "refresh", "key": ADMIN_KEY, "step": "fullfix"},
                # 下游單棒的預算是 3.2 分鐘，正常兩百秒內就會回。
                # 抓 300 秒：超過就代表那一棒被時間上限砍掉了，
                # 早一點發現、早一點去查進度，不必白等七分鐘。
                timeout=300,
                headers={"User-Agent": "zhangzhen-pipeline"},
            )
            body = r.text[:800]
        except requests.exceptions.RequestException as e:
            # 讀取逾時不等於那一棒沒做成。
            #
            # Apps Script 單次執行超過 6 分鐘會被直接中止，那不是拋例外，
            # 是執行被砍掉，所以連線就那樣掛著、我們只能等到自己的讀取逾時。
            # 但被砍掉之前寫進去的進度是留著的——游標、日期、步驟都在。
            # 所以先問一次進度：有前進就當作這一棒有效，繼續打下一棒。
            print(f"連線中斷（{type(e).__name__}），先查一下進度……", end=" ", flush=True)
            moved, where = _full_fix_progress(last_seen)
            if moved is None:
                fails += 1
                print("查不到進度。")
                if fails >= 3:
                    print("連續三次都查不到，停止。進度留在下游，稍後重跑會接續。")
                    return
                time.sleep(15)
                continue
            if moved:
                fails = 0
                last_seen = where
                print(f"有前進：{where}　繼續。")
                time.sleep(5)
                continue
            fails += 1
            print(f"沒有前進（仍在 {where}）。")
            if fails >= 3:
                print("連續三次沒有前進，停止。可能是某一步本身太慢，")
                print("到 Apps Script 的執行紀錄看最近一次 doGet 的錯誤。")
                return
            time.sleep(20)
            continue

        low = body.lower()
        if "<html" in low or "<!doctype" in low:
            print("下游回 HTML 而不是 JSON，多半是沒有部署最新版本。")
            return
        try:
            data = json.loads(body)
        except Exception:
            print(f"回應無法解析：{body[:160]}")
            return
        if not data.get("ok"):
            print(f"失敗：{data.get('error', body[:160])}")
            return

        fails = 0
        note = str(data.get("result", ""))
        last_seen = f"{data.get('processed', 0)}/{data.get('total', 0)} {note}"

        # 下游把「今天的 Gemini 額度用完了」當成暫停而不是失敗回報。
        # 這裡也要跟著停：再打一棒只會再撞一次同樣的牆，進度不會前進，
        # 而且會把這個 job 剩下的幾小時全部耗在等一個今天不會好的東西上。
        if "QUOTA_PAUSED" in note:
            print("")
            print("=" * 60)
            print("Gemini 今日額度已用完，本次到此為止。")
            print("=" * 60)
            print("進度全部保留在下游，沒有任何一天需要重跑。")
            print("明天額度重置之後，到後台按「接著跑」，或再跑一次本工作流程")
            print("並勾選 full_fix，就會從停住的那一天繼續。")
            print("想一次跑完的話，把 GEMINI_API_KEY 換成付費專案的金鑰即可。")
            return

        if "已被取消" in note:
            print("")
            print("這次重整已在後台被取消，停止驅動。已完成的步驟都保留著。")
            return

        print(f"{data.get('result', '')}  進度 {data.get('processed', 0)}/{data.get('total', 0)}")
        if data.get("done"):
            print("\n全面重整完成。網站與郵件都已依新規則更新。")
            return
        time.sleep(2)


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
    """
    Gemini 配額用盡（HTTP 429）。

    daily=True 代表撞到的是「每日請求數」上限。這一種退避完全沒有意義：
    額度要等到太平洋時間午夜才重置，在那之前不管等多久、重按幾次按鈕，
    每一次都會再收一個 429，而每一次都仍然算進當日用量。
    先前沒有分這兩種，於是按鈕被連按了八次、每次再花四分鐘退避六輪，
    把時間與額度一起燒掉，卻一列資料都沒寫進去。
    """

    def __init__(self, message, daily=False, reset_at=""):
        super().__init__(message)
        self.daily = bool(daily)
        self.reset_at = reset_at


# ------------------------------------------------------------------ #
# 配額熔斷器
#
# 一旦判定當日額度用盡，之後任何一次 call_gemini 都直接拋例外，
# 連請求都不發。理由很實際：發出去也只會拿回 429，而那一次仍然
# 計入用量，等於用自己的額度去確認自己沒有額度。
#
# 這一道同時保護逐字稿流程：會員簡訊把額度用完之後，同一個 job 後面的
# extract 會立刻得到「配額用盡」而不是再退避六輪才失敗。
# ------------------------------------------------------------------ #
_QUOTA_STOP = {"daily": False, "reason": "", "reset_at": ""}
_GEMINI_CALLS = {"n": 0}

# 429 回應裡代表「每日」而非「每分鐘」的字樣。Google 兩種都用同一個
# HTTP 狀態碼，只能靠 quota metric 的名稱分辨。
_DAILY_QUOTA_HINTS = (
    "perday", "per_day", "per day", "requests_per_day", "requestsperday",
    "generaterequestsperdayperprojectpermodel", "free_tier_requests",
    "daily limit", "quota_limit_value",
)
_MINUTE_QUOTA_HINTS = ("perminute", "per_minute", "per minute", "requests_per_minute")


def _key_problem(status: int, text: str) -> str:
    """
    這個非 200 是不是「這一把金鑰自己的問題」。是的話回一句可以照著修的話。

    分辨得出來很重要：金鑰問題換一把就能繼續，而且明天不會自己好，
    必須指名要人去修；其他錯誤（模型拒答、請求格式錯）換金鑰沒有用。
    """
    low = str(text or "").lower()
    if status == 404 and ("is not found" in low or "not found for api version" in low
                          or "models/" in low):
        return (f"這個專案看不到 {GEMINI_MODEL}。多半是該金鑰所屬的 Google 專案"
                "沒有啟用 Generative Language API，或那個專案還沒有這個模型的存取權。"
                "到 Google AI Studio 用同一個帳號重新產生金鑰，或在 Google Cloud "
                "把 Generative Language API 開通後再試")
    if status == 403 and ("permission" in low or "disabled" in low or "forbidden" in low
                          or "service_disabled" in low):
        return ("金鑰沒有權限或 API 被停用。到 Google Cloud 確認該專案的 "
                "Generative Language API 是啟用狀態，且金鑰沒有設定 IP／來源限制")
    if status in (400, 401) and ("api_key_invalid" in low or "api key not valid" in low
                                 or "invalid authentication" in low):
        return "金鑰無效。多半是貼的時候少了字元、多了引號或空白，請重新複製一次完整的金鑰"
    if status == 404:
        return f"回 404 但沒講原因。先確認該金鑰的專案能不能用 {GEMINI_MODEL}"
    return ""


def _classify_quota(text: str) -> str:
    """回傳 'daily'、'minute' 或 ''（分不出來）。"""
    low = re.sub(r"\s+", "", str(text or "").lower())
    if any(h.replace(" ", "") in low for h in _MINUTE_QUOTA_HINTS):
        return "minute"
    if any(h.replace(" ", "") in low for h in _DAILY_QUOTA_HINTS):
        return "daily"
    return ""


def _next_quota_reset() -> str:
    """
    免費層的每日請求數在太平洋時間午夜重置。換算成台北時間印出來，
    人才知道「什麼時候再按才有意義」，而不是每十五分鐘再按一次。

    夏令時間用美國規則近似（三月第二個週日到十一月第一個週日），
    差一小時不影響用途——這個時間只是給人看的下次可用時間。
    """
    now_utc = datetime.now(timezone.utc)
    y = now_utc.year
    def _nth_sunday(month, nth):
        d = date(y, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)      # 當月第一個週日
        return d + timedelta(days=7 * (nth - 1))
    dst = _nth_sunday(3, 2) <= now_utc.date() < _nth_sunday(11, 1)
    pacific = timezone(timedelta(hours=-7 if dst else -8))
    local = now_utc.astimezone(pacific)
    reset_pacific = datetime.combine(local.date() + timedelta(days=1),
                                     datetime.min.time(), tzinfo=pacific)
    return reset_pacific.astimezone(TAIPEI).strftime("%Y/%m/%d %H:%M")


def mark_quota_exhausted(tag: str) -> str:
    reset_at = _next_quota_reset()
    _QUOTA_STOP.update({"daily": True, "reset_at": reset_at,
                        "reason": f"Gemini 每日配額用盡（{tag}）"})
    print(f"Gemini 每日配額用盡（{tag}）。額度約於台北時間 {reset_at} 重置，"
          f"在那之前重按按鈕不會有用，本輪立即停止呼叫模型。")
    return reset_at


def quota_exhausted() -> bool:
    return bool(_QUOTA_STOP["daily"])


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
    if isinstance(v, datetime):
        return v.astimezone(TAIPEI).strftime("%Y/%m/%d") if v.tzinfo else v.strftime("%Y/%m/%d")
    if isinstance(v, date):
        return v.strftime("%Y/%m/%d")
    s = str(v or "").strip().replace("-", "/")
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m:
        return f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
    # Apps Script 的 getValues()/寫回舊資料有時留下 JavaScript Date 字串：
    # Thu Sep 03 2026 11:32:44 GMT+0800 (台北標準時間)
    # 這是完整、可驗證的日期，不能因為不是數字格式就隔離。
    eng = re.search(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
                    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
                    r"(\d{1,2})\s+(20\d{2})\b", s, re.I)
    if eng:
        months = {name.lower(): idx for idx, name in enumerate(
            ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
        return f"{eng.group(3)}/{months[eng.group(1).lower()]:02d}/{int(eng.group(2)):02d}"
    return ""


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
def call_gemini(system_text, user_text, want_json=False, thinking=0, max_out=MAX_OUT, tag="",
                max_429=6):
    """
    thinking=0 關閉思考。gemini-2.5-flash 的 thinking 預設開啟，
    且思考 token 計入 maxOutputTokens，是造成輸出被截斷的主因之一。

    finishReason 必須檢查。MAX_TOKENS 時 API 仍回 200 加上半截文字，
    不檢查就會靜默寫入不完整資料。

    max_429 是這一次呼叫最多容忍幾個 429。逐字稿那條路徑一天只跑幾次，
    等得起完整的退避表；會員簡訊一輪要跑幾十篇，每篇都退避六輪的話，
    光是等待就會把 job 的時間預算耗盡，所以那邊會把它調小。
    """
    require_gemini_key()

    # 熔斷器：當日額度已確定用盡時直接停，不再送出必定失敗的請求。
    if _QUOTA_STOP["daily"]:
        raise RateLimited(f"{_QUOTA_STOP['reason']}；本輪已停止呼叫模型（{tag}）",
                          daily=True, reset_at=_QUOTA_STOP["reset_at"])

    # 每一把金鑰都已經證實不可用時，也要秒回。
    # 不擋的話，逐字稿有幾段就會把「每一把都試一次、每一把都退避六輪」
    # 重跑幾次，二十分鐘的 job 全部耗在等一個不會好的東西上。
    if GEMINI_KEYS and len(_KEY_STATE["dead"]) >= len(GEMINI_KEYS):
        report = broken_keys_report()
        raise RuntimeError(
            f"Gemini 呼叫失敗（{tag}）：本輪 {len(GEMINI_KEYS)} 把金鑰全部不可用，已停止嘗試。"
            + (f"　{report}" if report else ""))

    _GEMINI_CALLS["n"] += 1
    # 金鑰改用 params 傳，不再直接串進網址。
    #
    # 串字串的話，金鑰裡只要有一個需要編碼的字元（或貼進 Secret 時混進了
    # 看不見的字元），組出來的就是一個壞掉的網址，而回來的錯誤會是
    # 404 或 400，完全看不出問題出在網址而不是金鑰本身。
    # 交給 requests 編碼就不會有這個問題，也不必自己去猜金鑰的合法字元。
    GEMINI_URL = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                  f"{GEMINI_MODEL}:generateContent")

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
    hits_429 = 0
    skip_delay = False
    # 429 是「這一分鐘打太多」，退避要夠長才有意義。
    # 原本 5/15/40 秒對免費配額太短，常常四次都撞在同一個配額窗口內。
    for attempt, delay in enumerate((0, 12, 30, 75, 150, 240)):
        if skip_delay:
            # 剛換過金鑰。新的那一把有自己的額度，沒有理由先等一段退避。
            skip_delay = False
            delay = 0
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
            # 每次重試都重新取一次金鑰。輪替之後要打到新的那一把，
            # 不是繼續打已經用完或壞掉的那一把。
            r = requests.post(GEMINI_URL, params={"key": current_gemini_key()},
                              json=body, timeout=600)
        except requests.RequestException as e:
            last = f"連線錯誤 {type(e).__name__}"
            print(f"Gemini {tag} {last}，重試中")
            continue

        if r.status_code != 200:
            last = f"HTTP {r.status_code}"
            if r.status_code not in TRANSIENT:
                # 400／403／404 幾乎都是「這一把金鑰的問題」，不是整個流程壞掉：
                #   404  這個專案看不到這個模型（Generative Language API 沒開通，
                #        或該專案沒有 gemini-2.5-flash 的存取權）
                #   403  金鑰沒有權限，或 API 被停用
                #   400  API_KEY_INVALID，多半是貼錯、貼到一半、或多了引號
                #
                # 先前這裡直接拋 RuntimeError，於是輪替到第二把之後只要那一把
                # 設定有問題，整個工單就死在半路——已經潤飾好的第 1 段也一起丟掉。
                # 實際發生過：第一把額度用完換到第二把，第二把回 404，
                # 「Gemini 呼叫失敗（polish 2/3）：HTTP 404」，工單結束、退出碼 1。
                #
                # 正確的作法與額度用完一樣：把這一把標記為不可用，換下一把繼續。
                # 全部都不能用時才失敗，而且要指名是哪幾個 Secret 有問題。
                broken = _key_problem(r.status_code, r.text or "")
                if broken and len(GEMINI_KEYS) > 1:
                    if rotate_gemini_key(tag, why="broken", detail=broken):
                        hits_429 = 0
                        skip_delay = True
                        continue
                hint = f"　{broken}" if broken else ""
                report = broken_keys_report()
                # 錯誤訊息不含金鑰內容，只講是第幾把、哪一個環境變數
                raise RuntimeError(
                    f"Gemini 呼叫失敗（{tag}）：{last}　目前用的是 {key_label(_KEY_STATE['idx'])}{hint}"
                    + (f"　已知有問題的金鑰：{report}" if report else ""))

            if r.status_code == 429:
                hits_429 += 1
                # 每日與每分鐘要分開處理。每分鐘等一下有用，每日等到明天才有用。
                kind = _classify_quota(r.text or "")
                if kind == "daily":
                    # 還有沒用完的金鑰就換一把繼續，不要停。
                    if rotate_gemini_key(tag):
                        hits_429 = 0
                        skip_delay = True
                        continue
                    reset_at = mark_quota_exhausted(tag)
                    raise RateLimited(f"Gemini 每日配額用盡（{tag}）：{last}",
                                      daily=True, reset_at=reset_at)
                if hits_429 >= max_429:
                    # 分不出種類，但已經連撞這麼多次。當成本輪不可用停下來，
                    # 由呼叫端決定要落地已完成的部分還是收尾。
                    print(f"Gemini {tag} 連續 {hits_429} 次 429，本輪不再重試")
                    raise RateLimited(f"Gemini 配額不足（{tag}）：{last}",
                                      daily=False, reset_at="")

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
        raise RateLimited(f"Gemini 配額用盡（{tag}）：{last}", daily=False, reset_at="")
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
# 對照表是不是兩邊都載到了。只有「上市與上櫃都在」時，才敢說
# 「這個代號不在表裡就是不存在」——某一邊掛掉的時候那句話會冤枉一半的股票。
_CODE_MAP_FULL = False

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
        rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
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

    global _CODE_MAP_FULL
    _CODE_MAP_FULL = bool(listed) and bool(otc)

    _CODE_MAP = m
    print(f"代號對照表載入 {len(m)} 檔（上市 {len(listed)}，上櫃 {len(otc)}）"
          + ("" if _CODE_MAP_FULL else "　※兩邊沒有都載到，代號查核這一輪放寬"))
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
    # 這一份與 Adminpipeline.gs 的 PIPE_NON_STOCK_EXACT 必須一模一樣。
    # 兩邊曾經走鐘到 python 100 個、apps script 42 個，於是同一個名稱
    # 在上游被擋、在後台卻放行（矽晶圓就是這樣漏出去的）。有測試在盯。
    "ABF", "ABF載板", "AB載板", "AI", "AI 伺服器", "AI伺服器", "AI概念股", "AI股", "CPO", "CoWoS", "ETF",
    "HBM", "IC 設計", "IC設計", "IC載板", "IP股", "MOSFET", "PCB", "PMIC", "中小型股", "伺服器", "低軌衛星",
    "個股", "傳產", "傳產股", "儲能", "元宇宙", "先進封裝", "光學鏡頭", "光通訊", "光阻", "加權指數", "半導體", "台指",
    "台指期", "台股", "多方", "大盤", "太陽能", "封測", "封裝基板", "導線架", "小型核電", "感測器", "折疊機", "指紋辨識",
    "散熱", "散熱模組", "散熱片", "散熱股", "日股", "晶圓代工", "期貨", "核能", "機器人", "權值股", "氫能", "氮化鎵", "無人機",
    "特化", "玻璃基板", "玻纖布", "現金", "生技", "生技股", "生物辨識", "石英", "石英元件", "矽光子", "矽晶圓", "矽智財",
    "砷化鎵", "硬板", "碳化矽", "空手", "空方", "第三代半導體", "綠能", "美股", "航太", "航運股", "衛星", "被動元件", "觀光股",
    "記憶體", "記憶體族群", "資安", "軍工", "軍工股", "軟板", "載板", "連接器", "選擇權", "邊緣運算", "重電", "重電概念",
    "重電股", "金融股", "銅箔", "銅箔基板", "鏡頭", "陶瓷基板", "陸股", "電動車", "電子股", "電源管理", "面板", "類比IC",
    "風電", "驅動IC", "高速傳輸", "高速傳輸股"
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
    if not re.search(r"[一-鿿]", n):
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
    return bool(re.search(r"[一-鿿]", str(s or "")))


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

    # 0. 代號優先，但代號必須真的存在。
    #
    #    原本這裡是「格式合法就直接採用」。那條規則是為了修一個真實的事故：
    #    上櫃清單掛掉、對照表殘缺的時候，「美琪瑪（4721）」這種本來就正確的
    #    資料因為 4721 不在殘缺的表裡，一路掉進模糊比對被改成「美利達 9914」。
    #    把對的改成錯的，比對不到還糟。
    #
    #    但它也讓幻覺代號一路通行。實際發生過兩筆：
    #      茂聯（2155）　和生堂（3182）
    #    這兩個號碼在上市 1094、上櫃 890、興櫃 363，連 ISIN 全清單
    #    四萬多檔（含 ETF、TDR、權證）裡都查不到——它們不是台股。
    #    表上有名稱有代號，價格與報酬卻永遠是空的，而補日K 每天照樣去敲一次，
    #    每天固定收兩個 404。
    #
    #    兩件事其實可以並存，差別只在「對照表這一刻健不健康」：
    #      兩邊都載到　→ 表裡沒有就是真的沒有，代號不可信，改用名稱比對。
    #      有一邊掛掉　→ 維持原本的照收，美琪瑪那個坑不會重開。
    if re.fullmatch(r"\d{4,6}", hint):
        if hint in m:
            return hint, m[hint], "代號直接命中"
        if not _CODE_MAP_FULL:
            return hint, name, "對照表殘缺（有一邊沒載到），代號格式合法先照收"

        # 代號是假的。丟掉它，讓名稱自己去比一次。
        code2, name2, how2 = resolve_code(name, "")
        if code2 not in (REJECT, UNRESOLVED):
            return code2, name2, f"代號 {hint} 不在上市櫃清單，改以名稱比對：{how2}"

        # 名稱也對不上。這裡刻意只降到待確認，不剔除——
        # 模型敢給一個具體代號，代表它認為這是個股；假代號是「號碼錯了」的證據，
        # 不是「這不是股票」的證據。剔除會靜靜地把一整列丟掉，
        # 待確認則會出現在後台，人可以在逐日編輯裡把名稱與代號改對。
        return UNRESOLVED, name, f"代號 {hint} 不在上市櫃清單，名稱也對不上（{how2}）"

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


def _same_stock(name: str, official: str) -> bool:
    """
    聽到的名稱與官方簡稱是不是同一檔。

    門檻刻意與 resolve_code 裡的比對一致：字面、去後綴、拼音各比一次。
    這一支只回答「像不像」，不負責挑代號，所以可以放心地寬鬆一點——
    寧可判成「是同一檔」而不啟動仲裁，也不要把本來就對的資料送去重判。
    """
    name = str(name or "").strip()
    official = str(official or "").strip()
    if not name or not official:
        return False
    if name == official:
        return True
    nb, ob = _base(name), _base(official)
    if nb and nb == ob:
        return True
    lit = max(difflib.SequenceMatcher(None, name, official).ratio(),
              difflib.SequenceMatcher(None, nb, ob).ratio())
    if lit >= NAME_CUTOFF:
        return True
    pin = max(difflib.SequenceMatcher(None, _npin(name), _npin(official)).ratio(),
              difflib.SequenceMatcher(None, _npin(nb), _npin(ob)).ratio())
    return pin >= PINYIN_CUTOFF


def arbitrate_name_code(name: str, hint: str, transcript: str):
    """
    講者同時講了名稱與代號，但兩者指向不同檔時，決定要相信哪一邊。

    為什麼需要這一關
    ----------------
    resolve_code 的第一條規則是「代號格式合法就直接採用」，那條規則本身是對的
    （四位數代號是硬證據，先前殘缺的對照表害得美琪瑪被改成美利達），
    但它有一個沒被考慮到的前提：代號與名稱指的是同一檔。

    逐字稿是語音轉文字，數字與名字都會被聽錯。實際發生過：
      「隱藏版光訊叫做 2402 的錩新，昨天漲 3 塊 1……」
    2402 的官方簡稱不是錩新。程式照規則採用了 2402，於是把講者口中的錩新
    整列改寫成另一家公司——名稱、代號、股價、K 線全部都是別人的，
    而且看起來完全正常，沒有任何一欄是空的。這種錯比留白難發現得多。

    怎麼仲裁
    --------
    不猜、不用常識、不問模型，只數一件可以驗證的事：這兩個字串在整份逐字稿裡
    各出現幾次。講者真正在談的那一檔，名字會被反覆講；聽錯的那一個通常只出現一次。
    上面那個例子裡「錩新」出現三次、「2402」出現一次，所以名稱贏。

    次數一樣多時不挑邊，回 unsure 讓它留成待確認。兩個都有可能對的時候，
    猜一個填進去只是把不確定藏起來，留白至少看得見。

    回傳 None 代表沒有衝突，照原本的流程走；否則回一個 dict：
      verdict  name  相信名稱，丟掉代號提示
               code  相信代號，維持原本的行為
               unsure 兩邊票數相同，留成待確認
    """
    name = str(name or "").strip()
    hint = str(hint or "").strip()
    if not name or not re.fullmatch(r"\d{4,6}", hint):
        return None

    m = get_code_map()
    official = m.get(hint)
    if not official:
        # 代號不在對照表裡（新上市，或清單還沒更新）。這種情況沒有「兩個候選」，
        # 也就無從仲裁，維持 resolve_code 原本的處理：採用代號、保留名稱。
        return None
    if _same_stock(name, official):
        return None

    # 名稱單獨去比一次。比不出來就沒有第二個候選，代號還是唯一的證據。
    c2, n2, _how2 = resolve_code(name, "")
    if c2 in (REJECT, UNRESOLVED) or c2 == hint:
        return None

    t = transcript or ""
    n_name = t.count(name)
    n_code = t.count(hint)
    head = f"名稱「{name}」→ {c2} {n2}　代號 {hint} → {official}"

    if n_name > n_code:
        return {"verdict": "name", "code": c2, "name": n2,
                "why": f"{head}；逐字稿裡名稱 {n_name} 次多於代號 {n_code} 次，採用名稱"}
    if n_code > n_name:
        return {"verdict": "code", "code": hint, "name": official,
                "why": f"{head}；逐字稿裡代號 {n_code} 次多於名稱 {n_name} 次，採用代號"}
    return {"verdict": "unsure", "code": UNRESOLVED, "name": name,
            "why": f"{head}；兩者在逐字稿各出現 {n_name} 次，無法判定，留待確認"}


# 價位欄位若一個數字都沒有，那不是價位。
# 實際看過模型把「最近」「前幾天」「突破均線」填進 price，
# 那些字串會一路流到網站的價位欄，讀的人會以為系統抓到了什麼價。
_HAS_DIGIT = re.compile(r"\d")


def clean_price_field(v) -> str:
    """沒有數字的價位說明一律歸零成「未說明」。"""
    t = str(v or "").strip()
    if not t or t == "未說明":
        return "未說明"
    if not _HAS_DIGIT.search(t):
        return "未說明"
    return t


BLANK_ = ("", "未說明", NOT_MENTIONED)


def naturalize_reason(value: str) -> str:
    """把模型偶爾產生的「〔現況〕＋〔建議〕」模板轉回完整自然句。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text or text in BLANK_:
        return text
    # 只處理整句由兩個括號區塊組成的舊模板，避免誤傷一般文字裡的數學加號。
    pair = re.match(
        r"^[〔【［\[]\s*(.*?)\s*[〕】］\]]\s*[+＋]\s*"
        r"[〔【［\[]\s*(.*?)\s*[〕】］\]]\s*[。．.]?$", text)
    if pair:
        left = pair.group(1).strip().rstrip("。；;，,：:")
        right = pair.group(2).strip().lstrip("。；;，,：:")
        text = left + ("，" if left and right else "") + right
    # 防模型用不同括號或在外側多包一層；只在看得到「括號＋括號」時清理。
    elif re.search(r"[〕】］\]]\s*[+＋]\s*[〔【［\[]", text):
        text = re.sub(r"[〕】］\]]\s*[+＋]\s*[〔【［\[]", "，", text)
        text = text.strip("〔〕【】［］[] ")
    text = re.sub(r"\s*，\s*", "，", text).strip()
    if text and not re.search(r"[。！？!?]$", text):
        text += "。"
    return text


def naturalize_signal_reasons(signals: dict) -> dict:
    """所有寫入點共用同一規格，防止提示詞偶發違規流進試算表。"""
    for key in ("buy", "sell", "watch_avoid", "watch_watch"):
        for row in signals.get(key, []) or []:
            row["reason"] = naturalize_reason(row.get("reason", ""))
    for row in signals.get("holdings", []) or []:
        row["note"] = naturalize_reason(row.get("note", ""))
    return signals


def _merge_text(a: str, b: str) -> str:
    """兩段說明併成一段。互相包含就留長的，否則接起來。"""
    a = naturalize_reason(a)
    b = naturalize_reason(b)
    if not a or a in BLANK_:
        return b or a
    if not b or b in BLANK_:
        return a
    if b in a:
        return a
    if a in b:
        return b
    return naturalize_reason(a.rstrip("。；;") + "；" + b)


def _merge_price(a: str, b: str) -> str:
    """兩個價位說明併成一個。有講的優先，兩個都有講就都留著。"""
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or a in BLANK_:
        return b or a or "未說明"
    if not b or b in BLANK_ or b in a:
        return a
    if a in b:
        return b
    return a + "、" + b


def merge_duplicates(signals: dict) -> dict:
    """
    同一類裡同一檔只留一列，說明整併。

    症狀：觀望注意那張表裡，陽明（2609）連續出現兩列——一列寫「股價在震盪後
    有機會飆漲」，另一列寫「大戶持股比重持續增加，待航運股整理結束後可留意」。
    兩列都是真的，他確實在節目的不同段落各講了一次；擷取時當成兩件事，
    到了表格上就變成同一檔出現兩次，看起來像資料壞掉。

    「他今天對這一檔的立場」只有一個，所以觀望與持股本來就不該有第二列。
    兩次說明是同一個立場的兩個理由，該併成一句。

    買賣不一樣：同一天分批買、分批賣是真的會發生，兩列各有各的價位，
    合併會把「他做了兩次」抹平成一次。所以買賣只在價位說明也一模一樣時才併——
    那種情況合理的解釋只剩重複擷取。

    這一關排在代號比對之後：比對會把同音錯字與簡稱都收斂到官方名稱與代號
    （加哲→嘉澤、隱藏版光訊→錩新），沒收斂之前同一檔的兩列可能長得完全不同，
    比不出它們是同一個東西。
    """
    ALWAYS = ("watch_avoid", "watch_watch", "holdings")
    BY_PRICE = ("buy", "sell")
    merged = 0

    for key in ALWAYS + BY_PRICE:
        rows = signals.get(key) or []
        if len(rows) < 2:
            continue

        hits = 0
        out, index = [], {}
        for r in rows:
            code = str(r.get("code") or "").strip()
            name = str(r.get("name") or "").strip()
            ident = code if code and code != UNRESOLVED else name
            if not ident:
                out.append(r)
                continue

            # 買賣要連價位一起當鑰匙，不同價位就是不同的一筆操作
            k = (ident, str(r.get("price") or "").strip()) if key in BY_PRICE else (ident,)
            hit = index.get(k)
            if hit is None:
                index[k] = r
                out.append(r)
                continue

            if key == "holdings":
                hit["note"] = _merge_text(hit.get("note", ""), r.get("note", ""))
                if str(hit.get("stance") or "").strip() in BLANK_:
                    hit["stance"] = r.get("stance", "未說明")
            else:
                hit["reason"] = _merge_text(hit.get("reason", ""), r.get("reason", ""))
                hit["price"] = _merge_price(hit.get("price", ""), r.get("price", ""))
            hits += 1

        if hits:
            merged += hits
            signals[key] = out

    if merged:
        print(f"合併重複：{merged} 列（同一類裡同一檔只留一列，說明併起來）")
    return signals


def resolve_watch_conflict(signals: dict) -> dict:
    """
    同一檔同時被收進觀望不碰與觀望注意時，收斂成一列。

    這是真的發生過的：他用「甲不要追、要注意乙」這種對比句，乙的段落裡
    先講乙的優點、後面才講乙現在還不能買。兩段分開讀會得到相反的結論，
    於是表格上同一檔出現一列看好、一列看壞，讀的人不知道該信哪一個。

    留哪一個：觀望不碰。理由不是它比較可能對，是兩種錯的代價不對稱——
    把「不要碰」顯示成「留意」，讀的人可能照著進場；反過來只是錯過一個
    想法。這個網站的立場一向是寧可少講，不要多講。

    兩邊的理由都留著。看好的那一段通常正是條件（「過季線補缺口再說」），
    而條件本來就該寫在說明裡，不是另外開一列。
    """
    avoid = signals.get("watch_avoid") or []
    watch = signals.get("watch_watch") or []
    if not avoid or not watch:
        return signals

    def _ident(r):
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        return code if code and code != UNRESOLVED else name

    by_avoid = {}
    for r in avoid:
        k = _ident(r)
        if k:
            by_avoid.setdefault(k, r)

    keep, moved = [], 0
    for r in watch:
        k = _ident(r)
        hit = by_avoid.get(k) if k else None
        if hit is None:
            keep.append(r)
            continue
        hit["reason"] = _merge_text(hit.get("reason", ""), r.get("reason", ""))
        hit["price"] = _merge_price(hit.get("price", ""), r.get("price", ""))
        moved += 1
        print(f"  立場衝突　{r.get('name', '')}（{k}）同時被收進觀望注意與觀望不碰，"
              f"併成觀望不碰一列")

    if moved:
        signals["watch_watch"] = keep
    return signals



# ------------------------------------------------------------------ #
# 成交歸位：今天真的賣掉了，就不能停在觀望
#
# 實際判錯過（華城 1519）：
#   逐字稿「那我今天賣掉華城創新高賣股票」
#   同一份逐字稿後面又說「華城也是在等季線，我看到它衝高我先賣一次，
#   有下來我再接」。
#   表格上卻是觀望注意，說明欄還寫著「股價創新高且帶量，先行獲利了結」。
#
# 一筆真的發生的賣出，在網站上變成「留意追蹤」。讀的人會以為他還抱著，
# 而持股追蹤那邊的回合也不會平倉——這一檔的報酬會一直掛在未實現，
# 錯得很安靜。
#
# 為什麼模型會這樣判：後面那句「等季線、有下來我再接」是他對這一檔
# 接下來的看法，讀起來像「現在先觀察」。擷取提示詞裡的第 8 條又說
# 「同一天同一檔只能有一個立場，以最終態度為準」——那一條本來只是用來
# 收斂觀望兩類的衝突，卻被拿去把一筆已經成交的賣出降級了。
#
# 提示詞那邊已經把第 8 條的適用範圍寫清楚了。但提示詞是請求，不是保證，
# 而這個錯誤的代價又特別大（持股追蹤的開倉平倉全靠買賣兩類），
# 所以這裡再加一道不靠模型的檢查。
#
# 判斷方式刻意訂得很窄，寧可漏也不要冤枉：
#   以標點把逐字稿切成短句，只看「同時出現這一檔名稱」的那一句。
#   那一句裡要有第一人稱的成交句型（我今天賣掉、我先賣一次、我出清），
#   要有今天的時間詞，而且不能有回顧詞（以前、當初、上次、昨天）。
#   三個條件同時成立才動它。
#
# 為什麼只看一句而不是前後一段：前後一段常常已經跳到下一檔了。
# 上面那段的下一句就是「買一檔我們上一次完美操作的股票」——
# 「上一次」講的是力積電，不是華城。範圍放寬到一段就會被它擋掉。
# ------------------------------------------------------------------ #

# 第一人稱、今天真的成交的句型。「賣」這個字本身不算，
# 「聯電可不可以賣」「你們要不要賣」都有賣字，但都不是他成交了。
_EXEC_SELL = re.compile(
    r"我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中|這個盤中)?(?:先|就)?賣掉"
    r"|我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中)?(?:先)?賣了"
    r"|我(?:們)?(?:今天|今日)?(?:先)?賣一次"
    r"|我(?:們)?(?:今天|今日)?出清"
    r"|獲利了結|停利出場"
)
_EXEC_BUY = re.compile(
    r"我(?:們)?(?:今天|今日|剛剛|剛才|早上|盤中|這個盤中)?(?:先|就)?買(?:進|回來|了)"
    r"|就是我買的"
    r"|我(?:們)?(?:今天|今日)?(?:先)?接回來"
    r"|我(?:今天)?加碼買"
)
_TODAY_WORD = re.compile(r"今天|今日|剛剛|剛才|這個盤中|早盤|早上|開盤")
# 回顧詞。有這些就不算今天的成交，交給提示詞與複審處理，這裡不碰。
_PAST_WORD = re.compile(
    r"以前|當初|上次|上一次|之前|那時|當時|去年|前年|幾年前|個月前"
    r"|上禮拜|上個禮拜|上週|昨天|前一天|除權息前"
)
_CLAUSE_SPLIT = re.compile(r"[。！？；，、\n\r]+")


def _executed_today(transcript: str, name: str, others=()):
    """
    這一檔在逐字稿裡有沒有一句是「我今天賣掉／買了它」。

    回傳 "sell"、"buy" 或 None。

    看的範圍是「含有這一檔名稱的那一句，加上前後各一句」。
    只看一句會漏掉很常見的講法——他先報名字再講動作：
      「那一支股票叫做力積電，早上這邊就是我買的」
    名字與動作分在兩句，只看一句就抓不到。

    放寬到前後一句就會有跨檔的風險：隔壁那一句可能已經在講別檔了。
    所以再加一個硬條件——視窗裡不能出現當天其他任何一檔的名稱。
    出現了就代表這個視窗橫跨兩檔，分不清動作是誰的，一律不動。
    實際會踩到的例子就在同一段裡：
      「那我今天賣掉華城，……買一檔我們上一次完美操作的股票……叫做力積電」
    """
    name = str(name or "").strip()
    if not transcript or len(name) < 2:
        return None
    others = [str(o).strip() for o in others
              if str(o).strip() and str(o).strip() != name and len(str(o).strip()) >= 2]

    clauses = [c for c in _CLAUSE_SPLIT.split(transcript) if c.strip()]

    def verdict(win, alone):
        """一個視窗的判定。alone 表示名稱與動作就在同一句裡。"""
        if not alone and any(o in win for o in others):
            return None               # 視窗橫跨兩檔，分不清動作是誰的
        if not _TODAY_WORD.search(win) or _PAST_WORD.search(win):
            return None
        if _EXEC_SELL.search(win):
            return "sell"
        if _EXEC_BUY.search(win):
            return "buy"
        return None

    for i, c in enumerate(clauses):
        if name not in c:
            continue
        # 名稱與動作在同一句：「我今天賣掉華城」，句子自己就講完了，
        # 隔壁那一句講誰都不影響，不必檢查跨檔。
        got = verdict(c, True)
        if got:
            return got
        # 名稱與動作分在兩句：「那一支股票叫做力積電，早上這邊就是我買的」。
        # 這時才需要確認前後一句沒有跳到別檔去。
        got = verdict("，".join(clauses[max(0, i - 1): i + 2]), False)
        if got:
            return got
    return None


def promote_executed_trades(signals: dict, transcript: str) -> dict:
    """把「今天明講成交、卻被歸到觀望」的個股移回買入或賣出。"""
    if not transcript:
        return signals

    # 當天出現過的所有名稱。視窗裡冒出別檔就代表橫跨兩檔，那一個視窗不採信。
    names = []
    for k in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        for r in signals.get(k) or []:
            n = str(r.get("name") or "").strip()
            if n:
                names.append(n)

    moved = 0
    for src in ("watch_avoid", "watch_watch"):
        keep = []
        for r in signals.get(src) or []:
            act = _executed_today(transcript, r.get("name", ""), names)
            if act is None:
                keep.append(r)
                continue
            # 觀望列沒有 when 與 seq，補上預設值；正確的先後由「日期歸屬」那一步排。
            r.setdefault("when", "today")
            r.setdefault("seq", 1)
            signals.setdefault(act, []).append(r)
            moved += 1
            zh = "賣出" if act == "sell" else "買入"
            print(f"  成交歸位　{r.get('name', '')}"
                  f"（{r.get('code', '') or '代號待確認'}）"
                  f"逐字稿明講今天{zh}，從觀望改回{zh}")
        signals[src] = keep

    if moved:
        print(f"成交歸位：{moved} 檔（今天明講成交，不該停在觀望）")
    return signals

def resolve_signals(signals: dict, transcript: str = "") -> dict:
    """每一筆都跑代號比對。判定為非個股的整筆剔除，不寫進試算表。"""
    stat = {"命中": 0, "修正": 0, "待確認": 0, "剔除": 0, "名代衝突": 0, "價位清空": 0}

    for key in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        kept = []
        for r in signals.get(key, []):
            raw = str(r.get("name", "")).strip()
            hint = str(r.get("code", "")).strip()

            # 沒有數字的價位說明先清掉，免得一路流到網站的價位欄。
            if "price" in r:
                cleaned = clean_price_field(r.get("price"))
                if cleaned != str(r.get("price") or "").strip():
                    stat["價位清空"] += 1
                r["price"] = cleaned

            # 名稱與代號指向不同檔時，先仲裁再比對。
            arb = arbitrate_name_code(raw, hint, transcript)
            if arb:
                stat["名代衝突"] += 1
                _why = arb["why"]
                print(f"  名稱與代號打架　{_why}")
                r["名稱代號衝突"] = arb["why"]
                if arb["verdict"] == "name":
                    hint = ""          # 丟掉代號提示，讓名稱自己去比
                elif arb["verdict"] == "unsure":
                    r["code"] = UNRESOLVED
                    stat["待確認"] += 1
                    kept.append(r)
                    continue

            code, fixed, how = resolve_code(raw, hint)

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
    if stat["名代衝突"]:
        print(f"　其中 {stat['名代衝突']} 筆的名稱與代號指向不同檔，已依逐字稿出現次數仲裁")
    if stat["價位清空"]:
        print(f"　另有 {stat['價位清空']} 筆的價位說明沒有任何數字，已清為未說明")
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
        except RuntimeError as e:
            # 「呼叫沒成功」與「模型回了不對的東西」要分開。
            #
            # 前者（金鑰失效、專案沒開通 API、連 404）跟配額用盡是同一類問題：
            # 這一段潤飾不了，但原文還在，用原文往下走仍然能擷取到完整內容。
            # 實際踩過的坑：第二把金鑰回 404，整個工單當場結束、退出碼 1，
            # 連第 1 段已經潤飾好的結果都一起丟掉，下一輪要從抓逐字稿重來。
            #
            # 後者（MAX_TOKENS、異常結束、空內容）代表切塊或設定要調，
            # 降級會把問題藏起來，所以照舊往外拋。
            msg = str(e)
            if "Gemini 呼叫失敗" not in msg and "金鑰全部不可用" not in msg:
                raise
            POLISH_DEGRADED += 1
            print(f"潤飾第 {i}/{len(chunks)} 段無法呼叫模型，改用原文保留內容（{msg[:160]}）")
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
5. 同一檔在同一類裡只能出現一次。
   他常在節目的不同段落各講同一檔一次——一次講價位，一次講籌碼或族群，
   那是同一個立場的兩個理由，不是兩件事。兩次都收就會在表格上出現兩列
   一模一樣的股票名稱與代號，讀的人會以為資料壞了。
   遇到這種情況：把兩次說明併成一句寫進同一筆，價位取有講到的那一個。
   例外只有一種——同一天分批買或分批賣，兩次的價位不同，那是真的兩筆操作，
   要分開列。
6. 「以前賣過、現在不要碰」是觀望不碰，不是賣出。
   他很常拿舊單當教材：「這檔我以前賺錢賣掉，現在不要碰」「當初一百塊出的，
   現在兩百我也不追」。那是在講一件過去的事，用來說明他現在的態度，
   今天並沒有任何成交。整段的結論是「現在不要進場」，收進 watch_avoid。
   只有他明講今天、剛剛、這個盤中把它賣掉、獲利了結、出清，才算 sell。
   同一條規則反過來也成立：「我以前八百塊買的」不是今天的買入。
7. 整段都在回顧往事、沒有一句在講「現在怎麼辦」的，一筆都不要收。
   他很常花好幾分鐘講一段完整的成功故事：哪一年、幾根漲停、賣在哪一天、
   後來腰斬多少。那是在建立信任，不是在給今天的指示。
   判斷方式：把整段讀完，問「他有沒有告訴我今天該拿這一檔怎麼辦」。
   沒有就整檔不收——不要因為裡面有價位、有動作、有情緒就生出一列。
   實際判錯過的例子（富邦金 2881）：他講「九根半漲停，賣在460到450，
   我在除權息前一天賣掉，現在腰斬了」，整段講的是很久以前那一次賣出，
   結果被收成今天的觀望不碰、價位還填了 460到450。那一天他對富邦金
   沒有任何當下的立場，正確答案是一列都不生。

8. 同一天同一檔只能有一個立場，不可以一邊寫看好一邊寫看壞。
   （這一條只用來收斂觀望兩類之間的衝突。已經成交的買賣不適用，見第 9 條。）
   他常用「甲不要追、要注意乙」這種對比句，而乙的段落裡往往先講乙的優點，
   後面才講乙現在還不能買。兩段分開讀會得到相反的結論，收進來就變成
   同一檔同時是觀望注意與觀望不碰。
   規則：把這一檔在整段逐字稿裡的所有段落合起來讀，以他對「現在能不能進場」
   的最終態度為準，只生一列。
   實際判錯過的例子（聯電 2303）：他先說「獲利比連接好太多，要去衝季線、
   補這個缺口，好好留意這一塊」，接著自問自答「聯電可不可以買？聯電線還壓著，
   我根本連動都不會想動」。結論是現在不動，所以是觀望不碰；
   「等過季線補缺口再說」是條件，寫進說明裡，不是另外開一列看好的。

9. 今天真的成交了就是 buy 或 sell，不可以因為他後面又講了看法而改成觀望。
   實際判錯過的例子（華城 1519）：他說「那我今天賣掉華城，創新高賣股票」，
   後面又說「華城也是在等季線，我看到它衝高我先賣一次，有下來我再接」。
   後面那句是他對這一檔接下來的打算，不是取消前面那筆已經成交的賣出。
   結果整檔被收成觀望注意，說明欄還寫著「先行獲利了結」——
   一筆真的發生的賣出，在表格上變成「留意追蹤」，
   讀的人會以為他還抱著，持股追蹤也不會平倉，這一檔的報酬會一直掛在未實現。
   規則：
     逐字稿裡只要有一句是「我今天（剛剛／早上／這個盤中）賣掉／買了某一檔」，
     那一檔就進 sell 或 buy，不論同一段裡還講了什麼後續打算。
     「賣了之後回檔再接」「賣一趟等季線」「先賣一次」這些話是說明欄的內容，
     不是改分類的理由。
     成交與看法同時存在時，成交優先——看法寫進 reason，分類照成交填。
   反過來也一樣：「我今天買回來，不過我覺得它還要再整理」仍然是 buy。

分類定義：
- buy：影片中明講「今天」執行的買入。
- sell：影片中明講「今天」執行的賣出。
  只要出現「我今天賣掉X」「今天把X賣了」「X我先賣一次」這種第一人稱的成交句，
  就是 sell，優先於任何觀望判斷。
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
- 只要講者「指名到一檔上市櫃個股」並且對它表達了當下的態度，不論在哪個段落、用什麼語氣，都要收進來，歸到最貼近的分類。
- 講者常先談族群再點名個股。例如先說「航運」再說「長榮、陽明」，此時長榮、陽明是個股，一定要收；只有在「完全沒有指名任何一檔」時才可以略過該族群。
- 同一檔在不同段落被多次提到，以最能代表當天結論的那次為準，只收一筆，reason 可綜合。

該不該收（這一關比召回優先）：
不是每一次被提到都該收。收進來的必須是「他在這支影片裡對這一檔的當下態度」。
下列一律不收：
- 純粹回顧以前的操作。例如「這支我已經沒了，我都賺錢賣」「當初跌停我沒賣，
  等上來到黑K棒上緣我才賣」。那是講已經結束的舊事，既不是今天的動作，
  也不是現在的立場。他很常拿以前的單子來講道理，那是教學不是紀錄。
- 拿某一檔當範例講技術面、講心態、回憶某一段行情，沒有給出現在怎麼看。
- 只是報個價、講成交量、回答別人的問題時順口帶到，沒有任何立場。

buy 與 sell 的門檻特別高：逐字稿必須看得到明確執行了動作，而且要講得出是哪一天。
  可以收的樣子：我今天買了、我們今天進場、剛剛掛的成交了、今天調節掉了、
                我昨天早盤請會員賣出、我昨天快十二點去把它買回來。
  不可以收的樣子：以前買的、上禮拜賣的、這個價位可以買、跌下來我會考慮。
看不到明確的執行動作，就一律歸到觀望兩類，不可以因為他講了買賣兩個字
就當成有買賣。明講現在還抱著、還沒賣、續抱的，歸 holdings。

這一筆算哪一天（when 欄位）：
他很常在今天的直播裡回頭講「昨天收盤後我做了什麼」，因為昨天的直播只播到一半。
那些動作發生在昨天，不是今天。實際判錯過的例子：
  「我昨天直播你們只聽到上半場，我直播以後又做了動作……我昨天早盤請我的會員
　　賣出晶心科創新高，大概賣 270 幾……那我昨天快 12 點的時候去把它買回來。」
這兩筆都是昨天的操作，卻被記成今天，於是網站上昨天那一天完全看不到，
今天卻多出兩筆從沒發生過的當日進出。
  when 填 \"today\"：他說今天、剛剛、這個盤中、早上（指今天早上）。
  when 填 \"prev\"　：他說昨天、昨天早盤、昨天收盤後、我昨天直播完之後。
  分辨不出來就填 \"today\"，那是預設值；寧可放在今天，也不要猜。
更早以前的（上禮拜、上個月）不要收，那是回顧舊單，本來就不該進表。

同一天同一檔做了兩件事的先後（seq 欄位）：
上面那個例子裡，昨天先賣（早盤 270 幾）、後買（近中午 250 幾）。
這個順序不能弄反——先買後賣是「加碼之後獲利了結」，先賣後買是「賣掉再撿回來」，
兩者的持有回合、進場價、報酬全部不一樣。
  同一檔在同一天只有一個動作時，seq 填 1。
  有兩個以上時，照他描述的先後填 1、2、3。
  只有 buy 與 sell 需要填，觀望與 holdings 一律填 1。

不指名就不要生（這一條最重要）：
講者說「我不講哪一隻」「我不講是哪一檔」「你們自己猜我買什麼股票」
「你們自己去找我買哪一隻」時，他是刻意不透露。那一段話不可以配給任何一檔。
就算他描述了時間、漲跌幅、型態——今天早上假破低進場、拉了四十塊——
也不可以從這些線索去猜是哪一檔，整筆不要收。
name 必須是逐字稿裡真的出現過的名稱或代號，禁止從敘述、產業、漲跌幅反推。

他更常見的是「根本沒提到要講哪一檔」，只是在複述自己這禮拜做了什麼。
那種段落沒有任何指名，也一樣不可以配給任何一檔。實際發生過的例子：
  「這個禮拜的第三個動作……我請我的會員買 250 的，我們這邊全部賣掉，
　　賣 275 左右賣掉……那我又告訴我的會員 250 幾 26 買回來……
　　收盤收 262.5，這是我這個禮拜的第三個動作。」
整段有價位、有動作、有日期，唯獨沒有股票名稱——當時模型生出了一檔
「位速 3508」，而位速這兩個字在整份逐字稿裡出現零次。
這種段落正確的做法是整筆不收，不是找一檔最像的填進去。

再說一次，因為這是最容易犯的錯：
你填進 name 的每一個字，都必須能在逐字稿裡原字找到。
找不到就代表那是你想出來的，不管你多有把握，都不可以填。
價位對得上、型態對得上、時間對得上，都不構成指名——
他這一小時裡講了幾十檔，價位相近的到處都是。
寧可少收一筆真的有講的，也不要生出一筆他從沒講過的：
少收的那筆只是沒出現，生出來的那筆會被當成事實。

價位與理由不要接錯檔：
他常常連續講好幾檔，價位很容易被接到隔壁那一檔身上。
price 與 reason 必須出自同一段、針對同一檔講的話。
無法確定那個數字是在講哪一檔時，price 填「未說明」。
寧可少一個數字，也不要把甲的價位寫到乙頭上——一個錯的數字看起來
和對的數字一模一樣，沒有人會發現。

逐字稿是語音轉文字，同音錯字很多（加哲就是嘉澤、冒聯就是貿聯）。
name 照聽到的填、不要自行更正，但也不要因為讀音有點像就硬配到另一檔。

name 欄位請填逐字稿裡實際聽到的名稱，即使你覺得可能是同音錯字也照填，不要自行更正。
code 欄位若逐字稿中講者有明講代號就填，沒有就留空字串。
不要自己回想或推測代號，比對官方清單是後續程式的工作。

名稱與代號同時講到時，兩個都要填，而且都照聽到的填：
語音轉文字會聽錯名字，也會聽錯數字，兩種都很常見。所以「他講的代號」
與「他講的名稱」有可能根本不是同一檔。實際發生過的例子是
「隱藏版光訊叫做 2402 的錩新」——2402 與錩新對不上，必有一邊是聽錯的。
你不必判斷是哪一邊錯，也不可以自己挑一邊填、把另一邊丟掉。
兩個都照實填進去，後續程式會拿官方清單，以及這兩個字串在整份逐字稿裡
各出現幾次來仲裁。你要是先幫它「修好」，那個仲裁就沒有依據了。

一段話裡出現兩檔以上時，聲明歸給緊鄰它的那一檔：
他講話常常一句沒說完就跳到下一檔，兩檔的名字會擠在同一段裡。
實際判錯過的例子：
  「我要你去買統振……這一個高點我前幾天告訴你們 66.4 是我會員已經買了，
　　鴻準了，我的會員已經買了」
「我會員已經買了」緊鄰的是鴻準，那句話講的就是鴻準，不是段落開頭的統振。
當時把它記成統振持有，是這個系統出過最嚴重的一種錯。
判斷不出來緊鄰的是哪一檔時，這一筆整個不要收。

價位對不上就是接錯檔的證據：
他講的價位若與那一檔的股價差了一個數量級（幾十塊的股票配上幾百塊的數字，
或反過來），代表那個數字是隔壁那一檔的，這一段並不是在講你要收的這一檔。
這種情形 price 填「未說明」；若整筆的依據只有那個數字，整筆不要收。

「我要你去買 X」「你們可以買 X」「建議你去買 X」是講給觀眾聽的建議，
不是會員已經持有，也不是他今天執行了買入。這種歸 watch_watch。
holdings 要的是「會員現在手上有」，而且那句持有聲明必須緊挨著那一檔的名字。

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

price 只放數字，或帶著數字的短句（238、255 以上、28 到 29）。
時間詞、程度詞、型態詞（最近、前幾天、很便宜、突破均線、量能放大）都不是價位，
這些一律填「未說明」。一個沒有數字的 price 欄位是沒有意義的。

reason 要改寫成證券研究報告的句子，不是把逐字稿剪一段貼上。

你是專業的證券分析師與財經編輯。輸入是語音轉文字的口語，充滿情緒用語與
模糊的形容詞（有鬼、沒用、出事）。你要從口語裡提煉出財經與技術面的真正涵義。

一、去口語化與情緒化。消除情緒性字眼、主觀語氣與贅字。
      「裡面有鬼」→「產業面存有潛在風險或疑慮」
      「大漲沒用」→「單日反彈未能改變趨勢」
二、提煉實質指標。精準抓出技術面（季線、缺口、跌停）、時間籌碼（整理時間、
    等一個月）、價格區間（4000 元以上），這些要保留。
三、邏輯重構。用一個完整自然句，先交代現象或指標，再用「因此、可、建議、仍、宜」
    等語意自然銜接操作結論。通常控制在 55 字以內；保留必要價位與條件時可稍長。
四、禁止模板符號。不可輸出「〔現況〕＋〔建議〕」「【現況】+【建議】」或任何
    中括號、方括號、加號、欄位標籤。不要加引號，不要寫成「他說……」，
    不要保留「好，注意」「有沒有看到」這類口頭禪，句尾使用正常標點。
    絕對不可以加入他沒有講的東西——改寫是換句話說，不是補充。

格式防錯：
  錯誤：〔技術面突破季線〕＋〔可續抱觀察〕
  正確：股價已突破季線並維持強勢，可續抱並持續觀察。

改寫對照：
  「昨天跌停、今天也守不住，不要買。」
    → 技術面弱勢，昨日跌停且今日支撐跌破，建議避開。
  「季線連過都沒過去，今天大漲沒用，不要買。」
    → 技術面受制於季線反壓，單日大漲視為無效反彈，維持觀望。
  「ABF載板裡面有鬼，不敢買、不買。」
    → ABF 載板產業面存有潛在疑慮，建議暫不介入。
  「出事才兩天，不要急，等一個月後再說。」
    → 事件利空剛發酵，籌碼尚未沉澱，建議觀望一個月後再評估。
  「有缺口且沒整理好，4000塊以上有風險，不想買。」
    → 技術面留有缺口且底部未打底完成，4000 元以上具風險，不建議追高。
  「隱藏版光訊叫做2402的錩新，昨天漲3塊1，今天漲兩塊4，好，注意。」
    → 具隱藏版光通訊題材，近兩日連續收漲，列為重點觀察。

只回傳 JSON，不要有其他文字。when 只能是 "today" 或 "prev"，seq 是正整數：
{
  "buy":   [{"name":"", "code":"", "price":"", "reason":"", "when":"today", "seq":1}],
  "sell":  [{"name":"", "code":"", "price":"", "reason":"", "when":"today", "seq":1}],
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
- 純粹回顧以前的操作，或拿舊單當教學範例，也不算漏。例如「這支我已經沒了，
  我都賺錢賣」「當初跌停我沒賣，等上來到黑K棒上緣才賣」，那是完結的舊事，
  既不是今天的動作也不是現在的立場，不要補進來。
- 只是報個價、講成交量、順口帶到而沒有任何立場的，也不算漏。
- 講者刻意不講是哪一檔時（我不講哪一隻、你們自己猜我買什麼），那一段話
  不屬於任何一檔，不可以配給任何個股，也不算漏，不要列。
- buy 與 sell 限「今天、剛剛、這個盤中」明講執行的動作。看不到今天執行就不可以
  填 buy 或 sell，依結論改填觀望兩類；明講現在還抱著、續抱的才是 holdings。
- 反過來也要看：JSON 裡歸在觀望兩類、但逐字稿明講「我今天賣掉／買了它」的，
  算漏抓的買賣，要補進 buy 或 sell。實際發生過（華城 1519）：
  逐字稿「那我今天賣掉華城，創新高賣股票」，表格上卻是觀望注意。
  他後面補一句「等季線、有下來我再接」是接下來的打算，不會取消今天這筆成交。
- 集團順帶點名不算漏。講「台塑集團」「鴻海集團」這種整體時會念到台塑、台化、
  南亞這些成分股的名字，除非他單獨對其中某一檔說了要怎麼做，否則不要列入。
- 族群舉例不算漏。講「航運」「記憶體」「重電」時常拿長榮、陽明當例子，
  重點是整個族群的方向，不是那一檔的個別立場。
- 拿來當對照或比喻的不算漏。例如「這檔跟大立光一樣都是高價股」，
  大立光只是參照物，不是他在談的標的。
- 「我要你去買 X」「你們可以買 X」是給觀眾的建議，不是會員持有也不是今天買入。
  要補的話補到 watch_watch，不可以補到 holdings 或 buy。
- 一段話裡有兩檔以上時，聲明歸給緊鄰它的那一檔；看不出緊鄰哪一檔就不要補。
  他講的價位若與那一檔的股價差了一個數量級，代表那句話在講隔壁那一檔，不要補。

名稱與代號都講到時兩個都填，照聽到的填，不要自己挑一邊、也不要自行更正。
兩者對不上時（例如「2402 的錩新」）後續程式會仲裁，你先修掉會讓它失去依據。

price 只放數字或帶數字的短句；沒有數字的（最近、前幾天、很便宜）一律填「未說明」。
reason 用自己的話寫成一個自然完整句，通常 55 字以內，不要整段照抄逐字稿，也不要留口頭禪。
禁止使用〔〕、【】、[]、+、＋或欄位標籤拼接現況與建議，句尾使用正常標點。

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
同一檔在同一類的表格裡只能出現一列。
清單裡若真的出現同一檔兩筆，把兩段說明併成一句寫在同一列，價位取有講到的那一個。
只有同一天分批買賣（兩筆價位不同）才分兩列。
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
       | 股票名稱 | 股票代號 | 方向 | 價位區間／成本說明 | 張震口頭說明與操作理由 |
       方向欄只能填「買入」或「賣出」兩個詞其中一個，不要寫別的字。
       不要加「動作類型」欄。那一欄每一列填的都是「當日操作」，
       而這張表本來就只收當日操作；真正的動作寫在方向欄裡，兩欄講的是同一件事。
       某欄位清單裡是「未說明」就照填「未說明」。
   ④-2 影片中明講之「會員目前持有股票」
       只列出清單 holdings 類的項目，一檔都不能多、不能少。
       為空時，寫：「本支影片未說明會員目前持股清單。」
       表格欄位固定，完全照這個順序與名稱：
       | 股票名稱 | 股票代號 | 張震在本集節目中的說明重點 |
       不要加「目前立場」欄。整張表就是「他今天明講會員還持有」，
       續抱兩個字每一列重複一次不會多帶任何資訊，卻會把說明欄擠到換行。
       立場如果有變化（例如開始分批調節），寫進說明重點那一欄裡。
   ④-3 觀望個股（當日未執行買賣）
       分成兩類分別列出，各自一張表：
       「觀望不碰」列出清單 watch_avoid 的項目，語氣偏空、情緒偏悲觀。
       「觀望注意」列出清單 watch_watch 的項目，語氣偏多、情緒偏正向。
       某一類為空時，寫：「本支影片未說明。」
       兩張表欄位皆固定，完全照這個順序與名稱：
       | 股票名稱 | 股票代號 | 價位說明 | 張震口頭說明重點 |
       不要加「觀望類型」欄。是哪一類已經寫在表格上面那一行，
       每一列再填一次同一個詞只會佔掉一整欄的寬度。

⑤ 分析師操作邏輯與教學重點
   將清單中的理由摘錄與說明重點，整理為 3 到 8 點條列，格式：
   觀念一：簡短標題
     說明：2 到 3 句，忠實轉述清單內容
   標題與說明都直接寫出來，不要用括號把它們包起來。
   上面那兩行的「簡短標題」「2 到 3 句」是在描述你要寫什麼，不是要照抄的格式。
   不得自行補充清單以外的觀點、個股或散戶提醒。

⑥ 風險揭露與重要提醒
   必須包含下列兩點：
   本文章內容僅為整理節目中之公開資訊與觀點，不構成任何形式之投資建議或獲利保證。
   實際投資操作須自行評估風險與財務狀況，必要時請諮詢專業投資顧問。
   清單中若有風險控管或警語相關內容，接著條列整理。

所有表格使用 Markdown 表格。
表頭一律只寫欄位名稱本身，不要在後面加括號說明。
括號說明會讓表頭長到必須換行，一整排表頭的高度就散掉了；
那些括號原本是寫給你看的欄位提示，不是要顯示給讀者的內容。
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


# ---------------------------------------------------------------- #
# 價位現實檢查
#
# 講者一句話沒講完就跳到下一檔，兩檔的名字擠在同一段裡，於是甲的價位被
# 接到乙頭上。提示語已經要求模型把聲明歸給緊鄰的那一檔，但那是「請它注意」，
# 不是保證。這裡加一道可以驗證的：拿數字去對那一檔真實的股價。
#
# 實際發生過的：
#   「我要你去買統振……66.4 是我會員已經買了，鴻準了，我的會員已經買了」
# 66.4 是鴻準的價位，統振不在那個價位。系統把統振記成會員持有，
# 那是這個網站出過最嚴重的一種錯——它會開一個不存在的持有回合，
# 然後每天用真實股價幫一筆不存在的部位算報酬。
#
# 兩層門檻，因為兩件事的代價不一樣：
#   清掉價位　只是少一個數字，判錯的損失很小 → 超出當日高低三成
#   降級分類　會改掉網站上的持有狀態，判錯的損失大 → 要差到兩倍以上
#
# 只在「這一檔已經有日K」時才會啟動。第一次被提到的股票沒有K線可對，
# 這一關對它是靜默的——那時擋在前面的是提示語，以及後台的內容複審。
# ---------------------------------------------------------------- #

_CAT_LABEL = {"buy": "買入", "sell": "賣出", "holdings": "會員持股"}

PRICE_CLEAR_BAND = 0.3      # 超出當日高低這個比例，就不採用那個數字
PRICE_DEMOTE_X = 2.0        # 差到這個倍數以上，代表那句話在講別檔

# 持有聲明裡的價位。只認緊貼著買進字眼的數字，
# 不然「每股淨值 60 塊」也會被當成成本。
_HOLD_PRICE_PATTERNS = [
    re.compile(r"(\d+(?:\.\d{1,2})?)\s*(?:元|塊)?\s*(?:買到|買進|買入|承接|進場|成本)"),
    re.compile(r"(?:買在|成本|進場價|承接於)\s*(\d+(?:\.\d{1,2})?)"),
]

_ANY_PRICE = re.compile(r"(\d+(?:\.\d{1,2})?)")

_DK_MEMO = {}


def _daily_k_cached(ss) -> dict:
    """日K快取一次執行只讀一次。整張表幾千列，每支影片都重讀是浪費。"""
    if "map" not in _DK_MEMO:
        _DK_MEMO["map"] = load_daily_k(ss)
    return _DK_MEMO["map"]


def _price_band(kmap: dict, code: str, date_str: str):
    """
    這一檔的合理股價區間。優先用當天的高低；那天沒有資料就用整段區間。

    用整段區間是刻意放寬的：跨越好幾個月的高低差本來就大，
    這一關的目的不是抓小誤差，是抓「幾十塊的股票配上幾百塊的數字」，
    放寬之後仍然抓得到，而且不會冤枉正常的波動。
    """
    days = kmap.get(str(code or "").strip())
    if not days:
        return None
    exact = days.get(date_str)
    if exact:
        return float(exact[0]), float(exact[1])
    his = [float(v[0]) for v in days.values() if v and v[0]]
    los = [float(v[1]) for v in days.values() if v and v[1]]
    if not his or not los:
        return None
    return max(his), min(los)


def _first_price(text: str, patterns=None):
    """從一段文字裡取出一個看起來像股價的數字。取不到回 None。"""
    t = str(text or "")
    if not t or t == "未說明":
        return None
    for pat in (patterns or [_ANY_PRICE]):
        m = pat.search(t)
        if not m:
            continue
        try:
            v = float(m.group(1))
        except (TypeError, ValueError):
            continue
        if 1 <= v <= 10000:
            return v
    return None


def price_reality_check(ss, signals: dict, date_str: str) -> dict:
    """把不屬於這一檔的數字清掉；靠那個數字撐起來的買賣與持有降級為觀望。"""
    try:
        kmap = _daily_k_cached(ss)
    except Exception as e:
        print(f"  價位現實檢查略過（讀不到日K快取：{e}）")
        return signals
    if not kmap:
        return signals

    cleared, demoted = 0, 0

    for key in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        keep = []
        for r in signals.get(key, []) or []:
            code = str(r.get("code", "")).strip()
            band = _price_band(kmap, code, date_str) if re.fullmatch(r"\d{4,6}", code) else None
            if not band:
                keep.append(r)
                continue
            hi, lo = band

            if key == "holdings":
                val = _first_price(r.get("note") or r.get("stance"), _HOLD_PRICE_PATTERNS)
            else:
                val = _first_price(r.get("price"))
            if val is None:
                keep.append(r)
                continue

            inside = lo * (1 - PRICE_CLEAR_BAND) <= val <= hi * (1 + PRICE_CLEAR_BAND)
            if inside:
                keep.append(r)
                continue

            nm = r.get("name", "")
            way_off = (val > hi * PRICE_DEMOTE_X) or (val < lo / PRICE_DEMOTE_X)

            if key != "holdings":
                print(f"  價位現實檢查　{nm}（{code}）的 {val} 不在 {lo}-{hi} 之內，"
                      f"價位說明清為未說明")
                r["price"] = "未說明"
                cleared += 1

            if way_off and key in ("buy", "sell", "holdings"):
                # 這一筆的依據是一個不屬於這一檔的數字，那句話講的是隔壁那一檔。
                # 但不能直接刪掉——他確實點名了這一檔，只是立場被接錯了。
                # 降到觀望注意是最保守的落點：留住「有提到」，去掉「有部位」。
                was = _CAT_LABEL.get(key, key)
                why = (f"原判定為{was}，但依據的價位 {val} 與這一檔的區間 "
                       f"{lo}-{hi} 差了超過 {PRICE_DEMOTE_X:.0f} 倍，"
                       f"研判是接錯檔，降為觀望")
                print(f"  價位現實檢查　{nm}（{code}）{why}")
                item = {"name": r.get("name", ""), "code": code, "price": "未說明",
                        "reason": str(r.get("reason") or r.get("note") or "未說明")[:60],
                        "降級原因": why}
                signals.setdefault("watch_watch", []).append(item)
                demoted += 1
                continue

            keep.append(r)
        signals[key] = keep

    if cleared or demoted:
        print(f"價位現實檢查：清掉不屬於該檔的價位 {cleared} 筆，降級為觀望 {demoted} 筆")
    return signals

# ---------------------------------------------------------------- #
# 日期歸屬
#
# 他很常在今天的直播裡回頭講「昨天收盤後我做了什麼」，因為昨天的直播
# 只播到一半。那些動作發生在昨天，不是今天。
#
# 實際發生過：9/2 的影片裡講「我昨天早盤請會員賣出晶心科 270 幾，
# 快 12 點又買回來 250 幾」。兩筆都被記成 9/2，於是 9/1 那天在網站上
# 完全看不到這件事，9/2 卻多出兩筆從沒發生過的當日進出。
#
# 前一個交易日從日K快取取。用快取而不是「日期減一」，是因為週一的前一天
# 是上週五，遇到連假還要再往前——減一天會落在沒有開盤的日子，
# 那一天永遠不會有其他資料，這一筆就會孤零零地掛在一個空白的日期上。
# ---------------------------------------------------------------- #


def _prev_trading_day(ss, date_str: str) -> str:
    """日K快取裡，早於 date_str 的最後一個交易日。取不到回空字串。"""
    try:
        kmap = _daily_k_cached(ss)
    except Exception:
        return ""
    days = set()
    for per_day in kmap.values():
        days.update(per_day.keys())
    earlier = sorted(d for d in days if d and d < date_str)
    return earlier[-1] if earlier else ""


def apply_when_and_seq(ss, signals: dict, date_str: str) -> dict:
    """
    把 when="prev" 的買賣改派到前一個交易日，並把 seq 正規化成正整數。

    只有 buy 與 sell 會被改派。觀望與會員持股講的是「現在的看法」，
    那個看法屬於今天這支影片，就算他順帶提到昨天也一樣。
    """
    prev = ""
    moved = 0

    for key in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        for i, r in enumerate(signals.get(key, []) or []):
            # seq：同日同檔的先後。沒填或填了怪東西就照陣列順序給一個。
            try:
                sq = int(r.get("seq") or 0)
            except (TypeError, ValueError):
                sq = 0
            r["_seq"] = sq if sq > 0 else (i + 1)

            r["_date"] = date_str
            if key not in ("buy", "sell"):
                continue
            if str(r.get("when") or "today").strip().lower() != "prev":
                continue

            if not prev:
                prev = _prev_trading_day(ss, date_str)
            if not prev:
                print(f"  日期歸屬　{r.get('name') or ''} 標為昨天，"
                      f"但日K快取找不到前一個交易日，仍記在 {date_str}")
                continue
            r["_date"] = prev
            moved += 1
            _nm = r.get("name") or ""
            print(f"  日期歸屬　{_nm}（{key}）是昨天的操作，改記在 {prev}")

    if moved:
        print(f"日期歸屬：{moved} 筆買賣改派到前一個交易日 {prev}")
    return signals

# ---------------------------------------------------------------- #
# 名稱必須真的在逐字稿裡
#
# 這是唯一一道不靠提示語、不靠模型自律的防線。
#
# 提示語已經寫了「不指名就不要生」，而且寫得很明白：他說「我不講哪一隻」
# 時不可以從漲跌幅、型態、時間去猜是哪一檔。但那是「請它不要」，不是保證。
#
# 實際發生過：他講「這個禮拜的第三個動作……我請會員買 250 的，賣 275 左右，
# 然後 250 幾 26 買回來」——整段沒有講任何股票名稱，模型卻生出了「位速 3508」。
# 位速這兩個字在整份逐字稿裡出現零次。那一列的名稱、代號、股價、K 線
# 全部是憑空產生的，而且每一欄都有值，看起來完全正常。
#
# 「這個字串有沒有出現在這兩萬字裡」是一個可以驗證的事實，不是判斷。
# 沒出現就是模型自己想出來的，不管它多有把握，一律丟掉。
#
# 為什麼不會誤殺：name 的規格本來就是「照逐字稿裡實際聽到的填」，
# 所以正確的擷取結果一定找得到。代號同理——講者有講代號才填。
# 兩者有一個出現就放行，因為他有時候只講代號、有時候只講名字。
# ---------------------------------------------------------------- #


def _in_transcript(needle: str, hay: str) -> bool:
    """字串有沒有出現在逐字稿裡。比對前把空白拿掉，標點不影響。"""
    n = re.sub(r"\s", "", str(needle or ""))
    if len(n) < 2:
        return False
    return n in hay


def verify_names(signals: dict, transcript: str) -> dict:
    """名稱與代號都沒出現在逐字稿裡的，整筆丟掉。"""
    hay = re.sub(r"\s", "", transcript or "")
    if len(hay) < 500:
        # 逐字稿太短或根本沒拿到，這一關無從驗起。
        # 這種時候不驗比亂驗好——全部丟掉會讓整天沒有資料。
        print("  幻覺檢查略過（沒有可比對的逐字稿）")
        return signals

    dropped = 0
    for key in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"):
        keep = []
        for r in signals.get(key, []) or []:
            nm = str(r.get("name", "")).strip()
            cd = str(r.get("code", "")).strip()
            if _in_transcript(nm, hay) or _in_transcript(cd, hay):
                keep.append(r)
                continue
            dropped += 1
            _cd = cd or "無代號"
            print(f"  幻覺剔除　{nm}（{_cd}）：這個名稱與代號在逐字稿裡都沒有出現")
        signals[key] = keep

    if dropped:
        print(f"幻覺檢查：剔除 {dropped} 筆逐字稿裡查無此名的紀錄")
    else:
        print("幻覺檢查：每一筆的名稱都在逐字稿裡找得到")
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
# 人工補登的列在「來源影片ID」欄用這個前綴標記。
# 必須與 AdminService.gs 的 MANUAL_ENTRY_PREFIX 一字不差。
MANUAL_ENTRY_PREFIX = "MANUALENTRY-"


def delete_rows_for_date(ss, sheet_name, date_str, date_col=1):
    """
    把某一天的資料整批刪掉，供重新分類時覆蓋用。由後往前刪避免列號位移。

    人工補登的列一律留著。那些是管理者親手補進去的——講者當天刻意不講股名，
    機器擷取不到，所以重寫一次也不會重新產生它。整天覆蓋若把它一起刪掉，
    人辛苦補的東西就這樣無聲無息地不見了，而且沒有任何訊息會提到這件事。
    """
    ws = ss.worksheet(sheet_name)
    values = sheets_retry(ws.get_all_values)
    head = values[0] if values else []
    c_vid = head.index("來源影片ID") if "來源影片ID" in head else -1

    targets, kept = [], 0
    for i in range(len(values) - 1, 0, -1):
        if norm_date(values[i][date_col - 1]) != date_str:
            continue
        if c_vid >= 0 and c_vid < len(values[i]):
            vid = str(values[i][c_vid] or "")
            if vid.startswith(MANUAL_ENTRY_PREFIX):
                kept += 1
                continue
        targets.append(i)

    for r in targets:
        sheets_retry(ws.delete_rows, r + 1)
    if targets:
        print(f"  {sheet_name} 刪除 {len(targets)} 筆舊資料")
    if kept:
        print(f"  {sheet_name} 保留 {kept} 筆人工補登，未刪除")
    return len(targets)


def _purge_rows_of_video(ss, sheet_name, date_str, video_id):
    """清掉某一天由某支影片寫進去的列。用在把資料改派到別的日期時避免重複。"""
    if not video_id:
        return 0
    ws = ss.worksheet(sheet_name)
    values = sheets_retry(ws.get_all_values)
    if not values:
        return 0
    head = values[0]
    c_vid = head.index("來源影片ID") if "來源影片ID" in head else -1
    if c_vid < 0:
        return 0
    targets = [i for i in range(len(values) - 1, 0, -1)
               if norm_date(values[i][0]) == date_str
               and c_vid < len(values[i])
               and str(values[i][c_vid] or "") == video_id]
    for r in targets:
        sheets_retry(ws.delete_rows, r + 1)
    if targets:
        print(f"  {sheet_name} 清掉 {date_str} 由本影片寫入的 {len(targets)} 筆舊資料")
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
                rows.append([r.get("_date") or date_str,
                             r.get("name", ""), r.get("code", UNRESOLVED), label,
                             r.get("price", "未說明"), r.get("reason", "未說明"),
                             video_id, r.get("_seq", 1)])

        # 改派到別的日期的那幾列，重跑時清不掉——delete_rows_for_date 只清
        # 這支影片自己的日期。所以先按「日期＋影片ID」清一次，
        # 不然每重跑一次就多一份重複，而且看起來像他真的又做了一次。
        other = sorted({r[0] for r in rows if r[0] != date_str})
        for od in other:
            _purge_rows_of_video(ss, "操作紀錄", od, video_id)

        if rows:
            sheets_retry(ss.worksheet("操作紀錄").append_rows, rows)
        print(f"操作紀錄寫入 {len(rows)} 筆"
              + (f"（其中 {sum(1 for r in rows if r[0] != date_str)} 筆記在 "
                 + "、".join(other) + "）" if other else ""))
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


def stage_extract(ss, video, date_str, v2, done_trades, done_holds, on_step=None):
    """
    階段二：擷取結構化紀錄。與階段一分開，因為它便宜、可重跑。

    on_step(名稱, 說明) 是給後台工單用的進度回報。自動路徑不傳，那時是 None，
    每一步只印在 Actions 的日誌裡——那條路沒有人在看進度條。
    """
    def step(name, note):
        if on_step:
            on_step(name, note)
    # 三樣都齊全才算做完：操作紀錄、會員持股、每日推播內容。
    #
    # 原本只看前兩樣。文章是在這個函式的最後才產生的，所以只要那天在撰稿前
    # 中斷過一次（配額用盡、逾時、執行被中止），紀錄已經寫進去了、文章卻沒有，
    # 之後每一次重跑都會在這裡直接 return，文章永遠補不回來。
    # 而沒有文章就沒有推播列，dailyPushJob 每次都在「今天還沒處理完」那一行退出，
    # 那天的信就再也不會寄，且不會有任何錯誤訊息——信箱只是安靜地沒有東西。
    has_article = date_str in existing_dates(ss, "每日推播內容")
    if date_str in done_trades and date_str in done_holds and has_article:
        print(f"{date_str} 操作紀錄、會員持股與推播內容都已存在，略過擷取")
        return
    if date_str in done_trades and date_str in done_holds and not has_article:
        print(f"{date_str} 紀錄已存在但缺推播內容，重跑擷取以補回文章")

    def _n(sig):
        """目前收了幾檔。放進進度說明裡，讓人看得出每一關動了什麼。"""
        return sum(len(sig.get(k, []) or [])
                   for k in ("buy", "sell", "watch_avoid", "watch_watch", "holdings"))

    step("擷取", "從逐字稿讀出他講了哪幾檔")
    signals = extract_signals(v2, date_str)

    step("稽核補漏", f"目前 {_n(signals)} 檔，回頭比對逐字稿看有沒有漏掉的")
    signals = audit_signals(v2, signals, date_str)

    # 幻覺檢查要排在代號比對之前：比對會把名稱換成官方簡稱
    # （加哲→嘉澤），換過之後就對不到逐字稿了。
    step("查驗幻覺", f"目前 {_n(signals)} 檔，逐一確認名稱真的出現在逐字稿裡")
    signals = verify_names(signals, v2)

    step("代號比對", f"目前 {_n(signals)} 檔，對官方清單、修同音錯字、剔除非個股")
    signals = resolve_signals(signals, v2)

    # 合併要排在代號比對之後：比對會把同音錯字與簡稱收斂到官方名稱與代號，
    # 沒收斂之前同一檔的兩列可能長得完全不同，比不出它們是同一個東西。
    step("合併重複", f"目前 {_n(signals)} 檔，把成交歸位、同一類裡同一檔只留一列")
    # 成交歸位排在合併之前：歸位之後那一檔可能與既有的賣出列撞在一起，
    # 讓後面的合併去收就好，不必在這裡另外處理。
    signals = promote_executed_trades(signals, v2)
    signals = merge_duplicates(signals)
    # 觀望兩類之間的衝突要在合併之後才處理：合併會先把各類裡的重複收乾淨，
    # 剩下的才是「真的一多一空」。
    signals = resolve_watch_conflict(signals)

    step("價位校對", f"目前 {_n(signals)} 檔，用日K驗證每一個數字是不是這一檔的")
    signals = price_reality_check(ss, signals, date_str)
    signals = naturalize_signal_reasons(signals)

    step("日期歸屬", "把昨天的操作改記到昨天，並排出同一天的先後")
    signals = apply_when_and_seq(ss, signals, date_str)
    signals["_video_id"] = video["id"]

    step("撰稿", f"共 {_n(signals)} 檔，產生每日整理")
    article = build_article(v2, signals, date_str)

    step("寫入", f"把 {_n(signals)} 檔寫進試算表")
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
# 後台工單的步驟。前端 Admin.html 的 STEPS 必須與這一份逐字相同，
# 否則進度條會對不到目前這一步，看起來像卡住不動。
ADMIN_STEP_NAMES = ["排程中", "讀取原文", "潤飾", "擷取", "稽核補漏", "查驗幻覺",
                    "代號比對", "合併重複", "價位校對", "日期歸屬", "撰稿", "寫入",
                    "刷新網站", "完成"]

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
    job_progress(job, step="讀取原文", done=1, total=len(ADMIN_STEP_NAMES))
    v1, v2 = existing_transcript(ss, vid, date_str)
    if not v1 or len(v1) < 300:
        raise RuntimeError(f"影片清單裡找不到 {vid} 的原始逐字稿，或內容太短（{len(v1 or '')} 字）。")
    print(f"原始逐字稿 {len(v1)} 字")

    # ---- 潤飾 ----
    # 雲端已有夠長的修飾稿就沿用，讓工單可以從中斷處續跑而不必重跑一次潤飾。
    if v2 and len(v2) > 200:
        print(f"已有修飾後逐字稿 {len(v2)} 字，略過潤飾")
        job_progress(job, step="潤飾", done=2, total=len(ADMIN_STEP_NAMES),
                     note="沿用既有修飾稿")
    else:
        job_progress(job, step="潤飾", done=2, total=len(ADMIN_STEP_NAMES),
                 note=f"原文 {len(v1)} 字")
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
    #
    # 先前這裡只回報一個「擷取與比對」，然後安靜地跑五到十分鐘。
    # 那一格會亮很久，看起來像卡住——而它裡面其實有八件事在跑，
    # 卡住的時候也分不出是卡在哪一件。現在每一件都回報。
    done_trades = existing_dates(ss, "操作紀錄")
    done_holds = existing_dates(ss, "會員持股")

    def _report(name, note):
        idx = ADMIN_STEP_NAMES.index(name) if name in ADMIN_STEP_NAMES else 0
        job_progress(job, step=name, done=idx, total=len(ADMIN_STEP_NAMES),
                     note=note)

    stage_extract(ss, video, date_str, v2, done_trades, done_holds, on_step=_report)

    mark_status(ss, vid, date_str, video["title"], "完成")
    _report("刷新網站", "資料已寫入，通知下游重算")

    print("\n工單處理完成，接著通知下游重算全站。")
    job_progress(job, step="完成", done=len(ADMIN_STEP_NAMES),
                 total=len(ADMIN_STEP_NAMES), status="完成",
                 note="全部完成。網站與郵件內容都已更新。")


def upsert_video_transcript(ss, video_id, date_str, v2):
    """
    把修飾後逐字稿寫回影片清單。

    找列的順序是「先比影片ID、再比日期」。

    先前只比影片ID，找不到就印一行訊息然後放棄——潤飾好的稿子就這樣掉了，
    而且流程照樣顯示成功。後台投稿用 MANUAL-日期 當代號，當天影片清單那列
    卻是真正的影片ID，正好會踩到這個洞：逐字稿分頁永遠等不到內容。
    現在改成日期也算命中，最後才新增一列，絕不靜默丟棄。
    """
    ws = ss.worksheet("影片清單")
    vals = sheets_retry(ws.get_all_values)
    head = vals[0]

    def ci(name, default=None):
        return head.index(name) if name in head else default

    c_id = ci("影片ID", 0)
    c_date = ci("發布日期")
    c_v2 = ci("修飾後逐字稿內容")
    if c_v2 is None:
        print("影片清單沒有『修飾後逐字稿內容』欄，略過寫回")
        return

    by_id, by_date = None, None
    for i in range(1, len(vals)):
        row = vals[i]
        if str(row[c_id]).strip() == video_id:
            by_id = i + 1
            break
        if by_date is None and c_date is not None:
            if norm_date(row[c_date]) == date_str:
                by_date = i + 1

    target = by_id or by_date
    if target:
        sheets_retry(ws.update_cell, target, c_v2 + 1, v2[:SHEET_CELL_LIMIT])
        how = "比對影片ID" if by_id else "比對日期"
        print(f"修飾稿已寫回影片清單第 {target} 列（{how}，{len(v2)} 字）")
        return

    # 兩種都找不到才新增。寧可多一列，也不要把處理好的稿子丟掉。
    row = [""] * len(head)
    row[c_id] = video_id
    if c_date is not None:
        row[c_date] = date_str
    row[c_v2] = v2[:SHEET_CELL_LIMIT]
    c_status = ci("處理狀態")
    if c_status is not None:
        row[c_status] = "完成"
    sheets_retry(ws.append_row, row, value_input_option="RAW")
    print(f"影片清單沒有 {date_str} 的列，已新增一列並寫入修飾稿（{len(v2)} 字）")


# ---------------------------------------------------------------- #
# 會員簡訊解析（GitHub Actions 執行）
# ---------------------------------------------------------------- #

CM_PARSE_SYSTEM = (
    "你在讀一則台灣投顧分析師「張震」發給 VIP 會員的盤中即時操作簡訊，"
    "要把它變成結構化的個股操作紀錄。\n\n"
    "輸入是「一條指令」的內文，開頭的廣播序號（如 張震-1、震1、張震6GJ-1）已經去掉了。\n\n"

    "【最重要的一件事】\n"
    "這些簡訊是會員真金白銀的進出依據。只要句子裡出現「個股 + 當下該做的動作」，"
    "就一定要收錄，漏掉一筆比多寫一筆嚴重得多。\n"
    "但同樣不可以無中生有：沒指名個股的心理喊話、族群評論、大盤點數，一筆都不能開。\n\n"

    "【action 只能是這五個之一】\n"
    "  買入　　　叫會員現在買進、買回、加碼、分批買、掛單買、「站買方」、「轉為…買進」、"
    "「資金轉為…買進」、「將空出資金轉為買進」、「沒有漲停都買」。\n"
    "  賣出　　　叫會員現在賣出、獲利了結、減碼、出清、賣掉、「站賣方」、「應站賣方」、"
    "「手中若有…者應站賣方」、「已填息應減碼」、「請在今天賣出」、「於成本之上獲利賣出」、"
    "「紅盤之上獲利賣出」。\n"
    "  會員持股　明講會員手上有這一檔，而且要續抱、抱牢、不動作、不必急於動作、等待轉折、"
    "不要急於加碼。例如「會員手中持股嘉澤，要耐心等3天…不要急於加碼」、「會員持股…皆續抱」、"
    "「祥碩今天季線正式向上，抱牢」。\n"
    "  觀望不碰　明講現在不可以買、不要碰、避開、不要追這一檔個股。\n"
    "  觀望注意　只是點名要留意、追蹤、準備突破、「耐心等待某價再找賣點」，"
    "沒有叫人現在動作。\n\n"

    "【一句話含兩筆或多筆時，每一筆都要獨立輸出】\n"
    "換股句是最常漏的一種。「賣出A、資金轉為買進B」必須輸出兩筆：A 賣出、B 買入。\n"
    "  「全國會員請將手中璟德於257元以上全數獲利賣出，資金轉為65.5元以下市價買進2354鴻準」\n"
    "    → 璟德 賣出 257 以上；鴻準(2354) 買入 65.5 以下。\n"
    "  「手中持有威剛者，請於415元以上獲利賣出，資金轉為市價買進6770力積電，沒有漲停都買」\n"
    "    → 威剛 賣出 415 以上；力積電(6770) 買入（市價，price 留空）。\n"
    "  「建議一般會員賣出AES-KY，在1170元以上賣出，資金轉為買進3533嘉澤，請於2030元以下買進」\n"
    "    → AES-KY 賣出 1170 以上；嘉澤(3533) 買入 2030 以下。\n\n"

    "【同一句含兩種會員狀態】\n"
    "「未持有者請於260元以下買進，已持有者續抱、不加碼」必須輸出同一檔兩筆：買入與會員持股。\n"
    "  「新進會員，手中未持有2439美律者，請於94元以下買進！已持有者不加碼，續抱即可」\n"
    "    → 美律(2439) 買入 94 以下；美律 會員持股。\n"
    "  「會員手中未持有8112至上者，請於94元以下買進，已持有者續抱即可」\n"
    "    → 至上(8112) 買入 94 以下；至上 會員持股。\n"
    "不要自行判斷歷史成本；程式會查全部操作紀錄。若先前已有更低買入價，"
    "程式會省略本次條件式買入，只保留續抱。\n\n"

    "【重要：台灣上市櫃股票名稱特別提醒】\n"
    "1. 許多台股名稱取自日常成語或形容詞，切勿誤判為非股票！\n"
    "   - 「至上」（8112）：分析師寫「會員手中至上，請於91元以上全數賣出」，"
    "「至上」就是股票名稱（至上電子 8112），絕非形容詞！必須提取！\n"
    "   - 「嘉澤」（3533）：「會員手中持股嘉澤，要耐心等3天…不要急於加碼」→ 會員持股。\n"
    "   - 「鴻海」（2317）、「緯創」（3231）：「手中若有鴻海、緯創者，今天應站賣方」"
    "→ 兩筆賣出。\n"
    "   - 「力積電」（6770）：原文寫「6770力積電」時，名稱填「力積電」、代號填「6770」。\n"
    "   - 其餘常見股名如「大同」「統一」「佳能」「巨大」「光寶科」「致茂」「晶心科」「祥碩」"
    "「華城」「東元」「裕隆」「鴻準」「譜瑞」「旭隼」「大江」「璟德」「威剛」「所羅門」"
    "「世紀鋼」「正德」「漢唐」「京元電」「建準」「陽明」「長榮」「美律」「世芯」「事欣科」"
    "「大立光」「聯電」，均為合法股票名稱。\n"
    "2. 一條指令包含多檔時，每一檔都要獨立開一筆。\n"
    "   - 「鴻準、裕隆、晶心科皆小漲，華城、東元只是洗盤…祥碩今天季線正式向上，抱牢…"
    "持股目前續抱」→ 鴻準、裕隆、晶心科、華城、東元、祥碩各開一筆「會員持股」。\n"
    "   - 「會員持股穩穩的，華城、晶心科、祥碩、嘉澤、鴻準等，皆持股續抱」→ 五筆會員持股。\n\n"

    "【本流程必須正確收錄的操作範例】\n"
    "- 「手中持有1519華城，請於775元以上全數獲利賣出，資金保留下來」"
    "→ 華城（1519），賣出，775，以上。這種句子絕不可回空陣列。\n"
    "- 「手中持股6533晶心科，請於271元以上獲利賣出」→ 晶心科（6533），賣出，271，以上。\n"
    "- 「早上賣出的6533晶心科，請於265元以下買回來」→ 晶心科（6533），買入，265，以下。"
    "前面的賣出是歷史回顧，本次動作是買回。\n"
    "- 「建議買進加碼一次3533嘉澤，請於1590元以下買進做多」→ 嘉澤（3533），買入，1590，以下。\n"
    "- 「建議全國會員將昨日空出的部份資金，轉為買進做多6533晶心科，請於平盤250元以下買進做多」"
    "→ 晶心科（6533），買入，250，以下。\n"
    "- 「建議手中持股2609陽明，請於59.5元以上全數獲利賣出（之前除息2元）」"
    "→ 陽明（2609），賣出，59.5，以上。括號裡的 2 是除息金額，不是操作價位。\n"
    "- 「手中在235元有買鴻海者，建議255元以上獲利賣出一次」→ 鴻海（2317），賣出，255，以上。"
    "235 是會員的持有成本，不是這次的操作價位，不可以填進 price。\n"
    "- 「6770力積電，請於紅盤之上全數獲利賣出」→ 力積電（6770），賣出，price 與 limit 留空，"
    "條件寫進 note。\n"
    "- 「手中持有世紀鋼、持有正德者，請於成本之上獲利賣出」→ 世紀鋼、正德各一筆賣出，"
    "price 留空。\n"
    "- 「之前威剛沒有賣出者，請在今天賣出」→ 威剛，賣出，price 留空。\n"
    "- 「會員手中持有長榮者，已填息應減碼！陽明還有大空間續抱」"
    "→ 長榮 賣出（減碼）；陽明 會員持股。\n"
    "- 「今日一般會員買璟德，已漲停，續抱」→ 璟德，會員持股。\n"
    "- 「會員手中持股祥碩，盤中有大單買進，季線即將向上，準備突破，務必抱牢」"
    "→ 祥碩（5269），會員持股。句中的『大單買進』是盤面現象，不是叫會員買進。\n"
    "- 「上次AES沒賣出者，耐心等待1200元以上再找賣點，我也會再通知大家」"
    "→ AES-KY，觀望注意。他明講「再找賣點、會再通知」，這一刻沒有要動作。\n"
    "- 「手中若持有大立光者，要留意這兩天是賣點」→ 大立光，觀望注意。\n\n"

    "【價位填寫規則】\n"
    "  price 只填「這一條裡真的寫出來、而且屬於這次操作」的純數字（整數或小數），不帶單位：\n"
    "    「請於775元以上全數獲利賣出」→ price: '775', limit: '以上'\n"
    "    「請在264元以下買進」　　　　→ price: '264', limit: '以下'\n"
    "    「請於平盤250元以下買進做多」→ price: '250', limit: '以下'\n"
    "  下面四種數字一律不可以填進 price，只能寫進 note：\n"
    "    1. 除權息金額：「（已除息3.8元）」「（除息2元）」「今日除息8.88元秒填息」。\n"
    "    2. 會員的歷史成本：「手中在235元有買鴻海者」的 235。\n"
    "    3. 大盤點數：「測試46188點」「45234-46188點」「已測試454xx點」。\n"
    "    4. 非數字價位描述：「紅盤之上」「成本之上」「市價」「平盤」單獨出現時。\n"
    "  除權息註記不影響操作，該檔股票仍須正常提取！\n\n"

    "【嚴格不可生出筆數的情況（防雜訊）】\n"
    "1. 族群、概念、類股不是個股。「被動元件」「ABF」「矽晶圓」「航運股」「記憶體」"
    "「高檔AI族群」「電機類股」「機器人概念股」出現時，絕對不可為它們開筆。\n"
    "2. 加權指數、大盤點數（如「測試46188點」）不是個股，不可開筆。\n"
    "3. 純大盤看法或無指名個股的心理喊話，不可開筆。例如"
    "「大盤連續反彈兩天」「今天大盤都沒量，什麼動作都不要做，持股續抱即可」"
    "「存股的股票只能在31元以下買」「今日絕不可隨意殺低手中持股」"
    "「一切進出動作依我通知操作」。這些句子沒有指名任何一檔，一筆都不能開。\n"
    "4. 「台積電快不裝牛了」這種對別人家股票的行情評論，沒有叫會員動作，不開筆。\n\n"

    "【note 說明重點】\n"
    "note 必須是自然、完整的敘述句，先寫條件或原因，再銜接操作結論，句尾加句號。\n"
    "禁止用〔〕、【】、[]、+、＋或『技術面：』『操作建議：』等模板拼接。\n"
    "錯誤：〔股價突破季線〕＋〔可續抱〕\n"
    "正確：股價已突破季線並維持強勢，可續抱並持續觀察。\n"
    "不得補入原簡訊沒有提供的技術指標、價位或判斷。\n\n"

    "【輸出格式】\n"
    "只回傳純 JSON：\n"
    '{"items":[{"name":"股票名稱","code":"代號(無則留空)","action":"買入/賣出/會員持股/觀望不碰/觀望注意","price":"純數字價位(無則留空)","limit":"以上/以下(無則留空)","note":"操作條件或說明重點(30字內)"}]}\n'
    '真的沒有任何一檔個股被指名動作時，才回 {"items":[]}。'
)

CM_EMPTY_AUDIT_SYSTEM = CM_PARSE_SYSTEM + (
    "\n\n你現在執行第二次完整性稽核。第一次解析回傳空陣列，但程式已在原文找到合法台股名稱或代號，"
    "以及買進、賣出、續抱、觀望等操作詞。請重新逐句核對股票名稱與它緊鄰的動作。"
    "有明確個股與當下動作就必須收錄。『手中持有某股，請於某價以上賣出』、"
    "『會員手中持股某股，務必抱牢』、『早上賣出的某股，請於某價以下買回』、"
    "『賣出某股，資金轉為買進另一股』都不可回空陣列；"
    "若只是歷史回顧、純行情敘述或未指名個股，仍回空陣列。"
    "不得為了補足筆數猜測名稱、代號、價位或動作。"
)


def sms_needs_empty_audit(text: str, code_map: dict) -> bool:
    """空結果只有同時出現合法個股與操作語意時才做第二次 AI 稽核。"""
    body = str(text or "")
    if not re.search(r"買進|買入|買回|加碼|賣出|出清|獲利|續抱|抱牢|持股|不要買|不要碰|觀望|留意|追蹤", body):
        return False
    if any(re.search(r"(?<!\d)" + re.escape(str(code)) + r"(?!\d)", body)
           for code in code_map if re.fullmatch(r"\d{4,6}", str(code))):
        return True
    names = {str(name).strip() for name in code_map.values() if len(str(name).strip()) >= 2}
    return any(name in body for name in names)


def report_sms_progress(status="處理中", step="準備", done=0, total=8, pct=0, note="", **metrics):
    """回報簡訊解析進度至終端機與下游 Apps Script 狀態，驅動前台進度條"""
    bar_width = 20
    filled = int(bar_width * max(0, min(100, pct)) / 100)
    bar = "=" * filled + (">" if filled < bar_width else "") + " " * max(0, bar_width - filled - (1 if filled < bar_width else 0))
    print(f"[{bar}] {pct}% | {step} ({done}/{total}) | {note}")

    if not APPS_SCRIPT_URL or not ADMIN_KEY:
        return
    try:
        params = {
            "action": "refresh",
            "key": ADMIN_KEY,
            "step": "smsstate",
            "status": status,
            "sub_step": step,
            "done": done,
            "index": done,
            "total": total,
            "pct": pct,
            "note": str(note)[:200],
            "job_id": SMS_JOB_ID,
            "mode": SMS_MODE or "parse",
            "audit_ready": "true" if status == "完成" else "false",
        }
        params.update({k: str(v) for k, v in metrics.items()})
        requests.get(
            APPS_SCRIPT_URL,
            params=params,
            timeout=10,
            headers={"User-Agent": "zhangzhen-pipeline"},
        )
    except Exception:
        pass


def verify_sms_item(it: dict, body: str, code_map: dict) -> dict | None:
    if not isinstance(it, dict):
        return None
    name = str(it.get("name") or "").strip()
    action = str(it.get("action") or "").strip()
    if not name or action not in ["買入", "賣出", "會員持股", "觀望不碰", "觀望注意"]:
        return None

    # 防呆：若 name 開頭自帶 4-6 位數代號如 "6770力積電"，自動分離代號與純名稱
    hint = str(it.get("code") or "").strip()
    m_code = re.match(r"^(\d{4,6})\s*(.+)$", name)
    if m_code:
        if not hint:
            hint = m_code.group(1)
        name = m_code.group(2).strip()

    if name not in body:
        return None
    non_stock, _ = is_non_stock(name)
    if non_stock:
        return None

    code, official_name, how = resolve_code(name, hint)
    if code == REJECT:
        return None

    price = str(it.get("price") or "").strip()
    price_valid = False
    if price and re.match(r"^\d+(\.\d+)?$", price):
        if price in body:
            # 排除除權息金額
            if not re.search(r"除[權息][^）)]{0,6}" + re.escape(price), body):
                price_valid = True

    clean_price = price if price_valid else ""
    limit = str(it.get("limit") or "").strip()
    if limit not in ["以上", "以下"]:
        limit = ""
    price_text = (clean_price + " 元" + limit) if clean_price else "未說明"

    return {
        "name": official_name,
        "code": code,
        "action": action,
        "price": clean_price,
        "limit": limit,
        "priceText": price_text,
        "note": naturalize_reason(str(it.get("note") or "").strip())[:80],
    }


def load_saved_sms_items(raw_detail: str) -> tuple[bool, list[dict]]:
    """
    讀取先前已驗證並保存的解析明細；格式不完整時回傳 False 供隔離重解析。

    空白與 "[]" 是不一樣的兩件事，這裡也回不一樣的答案：
      空白　→ (False, [])　從來沒有被 AI 判定過，要送 Gemini。
      "[]"　→ 也回 (False, [])。抓取當下就會塞這個佔位值，
              把它當成「解析過、沒有個股」是先前把 1519 華城那篇
              判成無可收錄的直接原因。真正的空結果改由「解析版本」欄位認定。
    """
    text = str(raw_detail or "").strip()
    if not text or text in ("[]", "[ ]"):
        return False, []
    try:
        data = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False, []
    if not isinstance(data, list):
        return False, []
    if not data:
        return False, []

    allowed = {"買入", "賣出", "會員持股", "觀望不碰", "觀望注意"}
    out = []
    for raw in data:
        if not isinstance(raw, dict):
            return False, []
        name = str(raw.get("name") or "").strip()
        code = str(raw.get("code") or "").strip()
        action = str(raw.get("dir") or raw.get("action") or "").strip()
        non_stock, _ = is_non_stock(name)
        if not name or action not in allowed or non_stock:
            return False, []
        # 代號待確認是合法的保存結果，不是壞掉的明細。
        # 先前只要有一筆待確認就把整篇判為損壞，於是那一篇每一輪都被重新
        # 送進 Gemini，而重解析的結果仍然是待確認，永遠不會停。
        if code and not re.fullmatch(r"\d{4,6}", code) and code != UNRESOLVED:
            return False, []
        if not code:
            code = UNRESOLVED

        price_raw = raw.get("price", "")
        if isinstance(price_raw, float) and price_raw.is_integer():
            price_raw = int(price_raw)
        price = str(price_raw or "").strip()
        price_text = str(raw.get("priceText") or "").strip()
        if not price:
            m_price = re.search(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", price_text)
            price = m_price.group(1) if m_price else ""
        if price and not re.fullmatch(r"\d+(?:\.\d+)?", price):
            return False, []
        limit = str(raw.get("limit") or "").strip()
        if limit not in ("以上", "以下"):
            limit = "以下" if "以下" in price_text else "以上" if "以上" in price_text else ""
        if not price_text:
            price_text = (price + " 元" + limit) if price else "未說明"
        out.append({
            "name": name,
            "code": code,
            "action": action,
            "price": price,
            "limit": limit,
            "priceText": price_text,
            "note": naturalize_reason(str(raw.get("reason") or raw.get("note") or "").strip())[:80],
            "tag": "既有解析明細",
        })
    return True, out


def serialize_sms_items(items: list[dict]) -> str:
    """把已驗證明細存成可續跑格式；每篇完成就先落地，避免 429 後重做。"""
    return json.dumps([
        {
            "name": it["name"],
            "code": it["code"],
            "dir": it["action"],
            "price": it.get("price", ""),
            "priceText": it.get("priceText", "未說明"),
            "limit": it.get("limit", ""),
            "reason": naturalize_reason(it.get("note", "")),
        }
        for it in items
    ], ensure_ascii=False)


def get_existing_cmoney_ids(ss) -> set[str]:
    cids = set()
    for sheet_name in ("操作紀錄", "會員持股"):
        try:
            ws = ss.worksheet(sheet_name)
            vals = sheets_retry(ws.get_all_values)
            if len(vals) < 2:
                continue
            headers = [str(h).strip() for h in vals[0]]
            c_src = headers.index("來源影片ID") if "來源影片ID" in headers else -1
            if c_src < 0:
                continue
            for row in vals[1:]:
                if c_src < len(row):
                    src = str(row[c_src]).strip()
                    if src.startswith("CMONEY-"):
                        cids.add(src)
        except Exception:
            pass
    return cids


def get_prior_sms_buy_prices(ss, excluded_sources: set[str]) -> dict[str, list[tuple[str, float]]]:
    """每檔保留既有操作紀錄的最低明講買入價；重解析中的文章先排除。"""
    out: dict[str, list[tuple[str, float]]] = {}
    try:
        values = sheets_retry(ss.worksheet("操作紀錄").get_all_values)
        if len(values) < 2:
            return out
        head = [str(x).strip() for x in values[0]]
        indexes = {x: head.index(x) for x in ("日期", "代號", "方向", "價位說明", "來源影片ID") if x in head}
        if len(indexes) < 5:
            return out
        for row in values[1:]:
            src = str(row[indexes["來源影片ID"]]).strip() if indexes["來源影片ID"] < len(row) else ""
            if src in excluded_sources:
                continue
            action = str(row[indexes["方向"]]).strip() if indexes["方向"] < len(row) else ""
            if not action.startswith("買"):
                continue
            code = str(row[indexes["代號"]]).strip() if indexes["代號"] < len(row) else ""
            price_text = str(row[indexes["價位說明"]]) if indexes["價位說明"] < len(row) else ""
            m = re.search(r"(?<!\d)(\d+(?:\.\d+)?)", price_text)
            if not code or not m:
                continue
            price = float(m.group(1))
            day = norm_date(row[indexes["日期"]] if indexes["日期"] < len(row) else "")
            if day:
                out.setdefault(code, []).append((day + " 23:59:59", price))
    except Exception as e:
        print(f"讀取會員簡訊歷史買入價失敗，將保守保留本次動作：{e}")
    return out


def _delete_rows_by_source(ss, source_id: str) -> int:
    removed = 0
    for sheet_name in ("操作紀錄", "會員持股"):
        try:
            ws = ss.worksheet(sheet_name)
            values = sheets_retry(ws.get_all_values)
            if len(values) < 2:
                continue
            head = [str(x).strip() for x in values[0]]
            if "來源影片ID" not in head:
                continue
            col = head.index("來源影片ID")
            targets = [idx for idx, row in enumerate(values[1:], 2)
                       if col < len(row) and str(row[col]).strip() == source_id]
            for row_num in reversed(targets):
                sheets_retry(ws.delete_rows, row_num)
                removed += 1
        except Exception as e:
            print(f"移除 {sheet_name} 舊衍生列失敗（{source_id}）：{e}")
            raise
    return removed


def _remove_old_source_rows_keep_latest(ss, sheet_name: str, keep_counts: dict[str, int]):
    """新列成功附加後才刪舊列；每個來源保留尾端本次寫入的指定筆數。"""
    ws = ss.worksheet(sheet_name)
    values = sheets_retry(ws.get_all_values)
    if len(values) < 2:
        return
    head = [str(x).strip() for x in values[0]]
    if "來源影片ID" not in head:
        return
    col = head.index("來源影片ID")
    targets: list[int] = []
    for source_id, keep in keep_counts.items():
        rows = [idx for idx, row in enumerate(values[1:], 2)
                if col < len(row) and str(row[col]).strip() == source_id]
        if len(rows) > keep:
            targets.extend(rows[:len(rows) - keep] if keep else rows)
    for row_num in sorted(set(targets), reverse=True):
        sheets_retry(ws.delete_rows, row_num)


CMONEY_LIST_API = "https://www.cmoney.tw/api/mach/api/Article/GetChannelsArticleByWeight"
CMONEY_USER_URL = "https://www.cmoney.tw/forum/user/{member_id}"
CMONEY_ARTICLE_URL = "https://www.cmoney.tw/forum/article/{article_id}"
CMONEY_MARK_RE = re.compile(r"(?:張震|震)\s*(?:6GJ)?\s*[-－—─]?\s*[0-9０-９]{0,2}\s*[:：]")
CMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}
SMS_HEADERS = ["文章ID", "發文時間", "標題", "原文", "解析狀態", "抓取時間", "通知狀態", "網址",
               "解析明細", "內容指紋", "最後偵測", "修訂次數", "解析版本", "判定時間"]
SMS_AUDIT_HEADERS = ["作業ID", "文章ID", "發文時間", "日期", "標題", "原文", "張震判定", "範圍判定",
                     "處理狀態", "說明", "網址", "更新時間"]


def _sms_now() -> str:
    return datetime.now(TAIPEI).strftime("%Y/%m/%d %H:%M:%S")


def _sms_fingerprint(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ensure_sms_sheet(ss, title: str, headers: list[str]):
    try:
        ws = ss.worksheet(title)
    except Exception:
        ws = sheets_retry(ss.add_worksheet, title=title, rows=2000, cols=max(12, len(headers)))
    values = sheets_retry(ws.get_all_values)
    current = [str(x).strip() for x in values[0]] if values else []
    changed = False
    for h in headers:
        if h not in current:
            current.append(h)
            changed = True
    if not current:
        current = list(headers)
        changed = True
    if changed or not values:
        # 先把欄數撐開再寫表頭。
        #
        # 既有的「會員簡訊」分頁是用 12 欄建的，這一版多了解析版本與判定時間
        # 兩欄。不先 resize 就直接寫 A1:N1，Sheets 會回
        # 「exceeds grid limits」，而那個錯誤發生在整條流程的第一步，
        # 看起來像是連分頁都讀不到，很難聯想到只是欄數不夠。
        try:
            have = ws.col_count
            if have < len(current):
                sheets_retry(ws.resize, rows=ws.row_count, cols=len(current))
                print(f"　「{title}」欄數由 {have} 擴充到 {len(current)}，以容納新欄位")
        except Exception as e:
            print(f"　「{title}」擴充欄數失敗（{e}），將直接嘗試寫入表頭")
        sheets_retry(ws.update, range_name=f"A1:{gspread.utils.rowcol_to_a1(1, len(current))}",
                     values=[current])
    return ws, current


def _cmoney_guest_token(session: requests.Session, member_id: str) -> str:
    url = CMONEY_USER_URL.format(member_id=member_id)
    r = session.get(url, headers=CMONEY_HEADERS, timeout=25)
    r.raise_for_status()
    for pat in (r'tokens\s*:\s*\{\s*at\s*:\s*"([^"]+)"',
                r'"tokens"\s*:\s*\{\s*"at"\s*:\s*"([^"]+)"'):
        m = re.search(pat, r.text)
        if m:
            return m.group(1)
    raise RuntimeError("公開會員頁找不到訪客權杖，來源網站可能已改版")


def _clean_cmoney_text(raw: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", str(raw or ""), flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html_lib.unescape(text).strip()


def _api_article(raw: dict) -> dict:
    content = raw.get("content") or {}
    stamp = raw.get("createTime")
    try:
        stamp = float(stamp)
        if stamp > 10_000_000_000:
            stamp /= 1000
        dt = datetime.fromtimestamp(stamp, TAIPEI)
        time_text = dt.strftime("%Y/%m/%d %H:%M:%S") if 2000 <= dt.year <= 2099 else ""
    except Exception:
        time_text = ""
    article_id = str(raw.get("id") or "").strip()
    return {
        "id": article_id,
        "creatorId": str(content.get("creatorId") or "").strip(),
        "title": _clean_cmoney_text(content.get("title")),
        "text": _clean_cmoney_text(content.get("text")),
        "time": time_text,
        "date": norm_date(time_text),
        "url": CMONEY_ARTICLE_URL.format(article_id=article_id),
        "weight": raw.get("weight"),
    }


def _parse_cmoney_html(article_id: str, source: str) -> dict:
    def meta(name: str) -> str:
        p1 = re.search(r'<meta[^>]*(?:property|name)="' + re.escape(name) + r'"[^>]*content="([^"]*)"', source, re.I)
        p2 = re.search(r'<meta[^>]*content="([^"]*)"[^>]*(?:property|name)="' + re.escape(name) + r'"', source, re.I)
        return html_lib.unescape((p1 or p2).group(1)).strip() if (p1 or p2) else ""

    segment = ""
    at = source.find("articleContent__text")
    if at >= 0:
        end = source.find("articleContent__", at + 20)
        segment = source[at:end if end > at else at + 20000]
    pieces = [_clean_cmoney_text(x) for x in re.findall(r"<span[^>]*>([\s\S]*?)</span>", segment, re.I)]
    body = "\n".join(x for x in pieces if x).strip() or meta("og:description") or meta("description")
    published = meta("article:published_time")
    m = re.match(r"^(20\d{2})-(\d{1,2})-(\d{1,2})T(\d{1,2}):(\d{2})(?::(\d{2}))?", published)
    time_text = ""
    if m:
        try:
            dt = datetime(*[int(x or 0) for x in m.groups()], tzinfo=TAIPEI)
            time_text = dt.strftime("%Y/%m/%d %H:%M:%S")
        except ValueError:
            pass
    if not time_text:
        own = source[:source.find("nav__articleItemTime")] if source.find("nav__articleItemTime") > 0 else source
        own = re.sub(r"<script\b[^>]*>[\s\S]*?</script>|<style\b[^>]*>[\s\S]*?</style>", " ", own, flags=re.I)
        visible = re.sub(r"\s+", " ", _clean_cmoney_text(own))
        full = re.search(r"(20\d{2})[\/\-.年](\d{1,2})[\/\-.月](\d{1,2})日?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", visible)
        if full:
            try:
                dt = datetime(*[int(x or 0) for x in full.groups()], tzinfo=TAIPEI)
                time_text = dt.strftime("%Y/%m/%d %H:%M:%S")
            except ValueError:
                pass
        else:
            rel = re.search(r"(今天|昨日|昨天|星期[一二三四五六日天])\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", visible)
            if rel:
                ref = datetime.now(TAIPEI)
                shown_h, shown_m, shown_s = int(rel.group(2)), int(rel.group(3)), int(rel.group(4) or 0)
                target = ref.replace(hour=shown_h, minute=shown_m, second=shown_s, microsecond=0)
                label = rel.group(1)
                if label in ("昨日", "昨天"):
                    target -= timedelta(days=1)
                elif label.startswith("星期"):
                    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
                    back = (ref.weekday() - weekday_map[label[-1]]) % 7
                    target -= timedelta(days=back)
                    if back == 0 and (shown_h, shown_m, shown_s) > (ref.hour, ref.minute, ref.second):
                        target -= timedelta(days=7)
                time_text = target.strftime("%Y/%m/%d %H:%M:%S")
    return {"id": str(article_id), "creatorId": "", "title": meta("og:title"), "text": body,
            "time": time_text, "date": norm_date(time_text),
            "url": CMONEY_ARTICLE_URL.format(article_id=article_id), "weight": None}


def fetch_cmoney_articles(mode: str, ids_text: str = "") -> list[dict]:
    if not CMONEY_MEMBER_ID.isdigit():
        raise SystemExit("缺少 GitHub Variable CMONEY_MEMBER_ID，無法抓取會員簡訊")
    session = requests.Session()
    token = _cmoney_guest_token(session, CMONEY_MEMBER_ID)
    since = norm_date(SMS_SINCE) if SMS_SINCE else (datetime.now(TAIPEI).date() - timedelta(days=6)).strftime("%Y/%m/%d")
    through = datetime.now(TAIPEI).strftime("%Y/%m/%d")
    found: list[dict] = []

    if mode == "ids":
        ids = list(dict.fromkeys(re.findall(r"\d{6,}", ids_text)))[:100]
        if not ids:
            raise SystemExit("依編號補抓沒有收到有效文章 ID")
        # 先從來源帳號的清單 API 找。這裡的 createTime 是完整 Unix 時間，
        # 也能驗證 creatorId；文章頁若顯示「星期三」，只能當最後備援。
        wanted = set(ids)
        by_id: dict[str, dict] = {}
        score = None
        for page in range(1, 1001):
            params = {"count": 20}
            if score is not None:
                params["startScore"] = str(score)
            headers = dict(CMONEY_HEADERS)
            headers.update({"Authorization": f"Bearer {token}", "X-Version": "3.0",
                            "Accept": "application/json",
                            "Referer": CMONEY_USER_URL.format(member_id=CMONEY_MEMBER_ID)})
            resp = session.post(CMONEY_LIST_API, params=params,
                                json={"items": [f"Member-All.{CMONEY_MEMBER_ID}"]},
                                headers=headers, timeout=30)
            if resp.status_code in (401, 403):
                token = _cmoney_guest_token(session, CMONEY_MEMBER_ID)
                headers["Authorization"] = f"Bearer {token}"
                resp = session.post(CMONEY_LIST_API, params=params,
                                    json={"items": [f"Member-All.{CMONEY_MEMBER_ID}"]},
                                    headers=headers, timeout=30)
            resp.raise_for_status()
            raw_items = resp.json()
            if not isinstance(raw_items, list):
                raise RuntimeError("文章清單 API 格式已改變")
            for raw in raw_items:
                article = _api_article(raw)
                if article["id"] in wanted:
                    by_id[article["id"]] = article
            report_sms_progress(step="探索文章", done=min(len(by_id), len(ids)), total=len(ids),
                                pct=5 + int(min(len(by_id), len(ids)) / len(ids) * 30),
                                note=f"以來源清單驗證日期與作者：找到 {len(by_id)}/{len(ids)}")
            if wanted.issubset(by_id) or not raw_items or len(raw_items) < 20:
                break
            next_score = raw_items[-1].get("weight")
            if next_score is None or str(next_score) == str(score):
                break
            score = next_score

        for i, article_id in enumerate(ids, 1):
            if article_id in by_id:
                found.append(by_id[article_id])
                continue
            r = session.get(CMONEY_ARTICLE_URL.format(article_id=article_id), headers=CMONEY_HEADERS, timeout=25)
            if r.status_code == 200:
                found.append(_parse_cmoney_html(article_id, r.text))
            else:
                found.append({"id": article_id, "creatorId": "", "title": "", "text": "", "time": "", "date": "",
                              "url": CMONEY_ARTICLE_URL.format(article_id=article_id), "error": f"HTTP {r.status_code}"})
            report_sms_progress(step="探索文章", done=i, total=len(ids), pct=5 + int(i / len(ids) * 35),
                                note=f"依編號抓取 {i}/{len(ids)}")
        return found

    score = None
    for page in range(1, 5001):
        params = {"count": 20}
        if score is not None:
            params["startScore"] = str(score)
        headers = dict(CMONEY_HEADERS)
        headers.update({"Authorization": f"Bearer {token}", "X-Version": "3.0",
                        "Accept": "application/json", "Referer": CMONEY_USER_URL.format(member_id=CMONEY_MEMBER_ID)})
        r = session.post(CMONEY_LIST_API, params=params, json={"items": [f"Member-All.{CMONEY_MEMBER_ID}"]},
                         headers=headers, timeout=30)
        if r.status_code in (401, 403):
            token = _cmoney_guest_token(session, CMONEY_MEMBER_ID)
            headers["Authorization"] = f"Bearer {token}"
            r = session.post(CMONEY_LIST_API, params=params, json={"items": [f"Member-All.{CMONEY_MEMBER_ID}"]},
                             headers=headers, timeout=30)
        r.raise_for_status()
        raw_items = r.json()
        if not isinstance(raw_items, list):
            raise RuntimeError("文章清單 API 格式已改變")
        batch = [_api_article(x) for x in raw_items]
        found.extend(batch)
        report_sms_progress(step="探索文章", done=page, total=max(page, page + (1 if len(raw_items) == 20 else 0)),
                            pct=min(40, 5 + page * 2), note=f"已掃描 {page} 頁、{len(found)} 篇")
        if not raw_items or len(raw_items) < 20:
            break
        next_score = raw_items[-1].get("weight")
        if next_score is None or str(next_score) == str(score):
            raise RuntimeError("文章清單游標沒有前進，已停止避免重複抓取")
        score = next_score
        if mode == "recent":
            valid = [x["date"] for x in batch if x.get("date")]
            if valid and all(x < since for x in valid):
                break
    return found


def save_cmoney_fetch(ss, articles: list[dict], mode: str) -> dict:
    ws, headers = _ensure_sms_sheet(ss, "會員簡訊", SMS_HEADERS)
    audit_ws, audit_headers = _ensure_sms_sheet(ss, "會員簡訊稽核", SMS_AUDIT_HEADERS)
    existing_values = sheets_retry(ws.get_all_values)
    c_id = headers.index("文章ID")
    existing = {str(row[c_id]).strip(): (idx, row) for idx, row in enumerate(existing_values[1:], 2) if c_id < len(row)}
    since = norm_date(SMS_SINCE) if SMS_SINCE else ((datetime.now(TAIPEI).date() - timedelta(days=6)).strftime("%Y/%m/%d") if mode == "recent" else "")
    through = datetime.now(TAIPEI).strftime("%Y/%m/%d")
    stats = {"scanned": len(articles), "zhang": 0, "saved": 0, "existed": 0, "errors": 0}
    audit_rows = []
    new_rows = []
    job_id = SMS_JOB_ID or f"SMS-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}"

    for a in articles:
        belongs = a.get("creatorId") == CMONEY_MEMBER_ID if a.get("creatorId") else mode == "ids"
        is_zhang = bool(belongs and CMONEY_MARK_RE.search(a.get("text", "")))
        valid_date = bool(a.get("date"))
        in_range = valid_date and a["date"] <= through and (not since or a["date"] >= since)
        error = str(a.get("error") or "")
        status = "已排除"
        note = "正文沒有張震廣播標記"
        if error or not a.get("text"):
            status, note = "抓取失敗", error or "文章沒有可讀正文"
            stats["errors"] += 1
        elif not is_zhang:
            status, note = "已排除", "作者不符或正文沒有張震廣播標記"
        elif not valid_date:
            status, note = "日期待確認", "清單 API、文章 metadata 與頁面時間均無可驗證日期；已隔離，未歸入任何一天，可再次依編號補抓"
            stats["zhang"] += 1; stats["errors"] += 1
        elif not in_range:
            status, note = "範圍外", "張震文章不在本次日期範圍"
            stats["zhang"] += 1
        else:
            stats["zhang"] += 1
            old = existing.get(a["id"])
            if old:
                status, note = "已存在", "正式資料已有同一文章 ID"
                stats["existed"] += 1
                old_row_num, old_row = old
                old_text = old_row[headers.index("原文")] if headers.index("原文") < len(old_row) else ""
                if _sms_fingerprint(old_text) != _sms_fingerprint(a["text"]):
                    # 內容變了就代表舊的 AI 判定不再對應這篇原文。
                    # 清掉解析版本，讓它回到「空白待解析」那一桶重新判定。
                    updates = {
                        "發文時間": a["time"], "標題": a["title"], "原文": a["text"][:20000],
                        "解析狀態": "待解析（補抓發現修訂）", "解析版本": "", "判定時間": "",
                        "內容指紋": _sms_fingerprint(a["text"]), "最後偵測": _sms_now(),
                    }
                    for key, value in updates.items():
                        sheets_retry(ws.update_cell, old_row_num, headers.index(key) + 1, value)
                    status, note = "已更新", "來源內容有變更，已覆蓋並排回解析"
            else:
                row = [""] * len(headers)
                # 解析明細與解析版本刻意留空白，不寫 "[]"。
                # 寫 "[]" 會讓「還沒解析」與「解析後真的沒有個股」在資料上
                # 完全一樣，併入那一步就會把佔位值當成有效結果，直接標成
                # 「無可收錄，稽核通過」而從來沒有問過 Gemini。
                values = {"文章ID": a["id"], "發文時間": a["time"], "標題": a["title"], "原文": a["text"][:20000],
                          "解析狀態": "待解析", "抓取時間": _sms_now(), "通知狀態": "歷史補抓不寄送",
                          "網址": a["url"], "解析明細": "", "內容指紋": _sms_fingerprint(a["text"]),
                          "最後偵測": _sms_now(), "修訂次數": 0, "解析版本": "", "判定時間": ""}
                for key, value in values.items(): row[headers.index(key)] = value
                new_rows.append(row); stats["saved"] += 1
                status, note = "已收錄", "已寫入會員簡訊，等待 GitHub 解析"
        audit_rows.append([job_id, a.get("id", ""), a.get("time", ""), a.get("date", ""), a.get("title", ""),
                           a.get("text", "")[:20000] if is_zhang else "", "是" if is_zhang else "否",
                           "範圍內" if in_range else "範圍外", status, note, a.get("url", ""), _sms_now()])

    if new_rows:
        sheets_retry(ws.append_rows, new_rows, value_input_option="RAW")
    if audit_rows:
        sheets_retry(audit_ws.append_rows, audit_rows, value_input_option="RAW")
    print(f"會員簡訊抓取：掃描 {stats['scanned']}、張震 {stats['zhang']}、新收 {stats['saved']}、既有 {stats['existed']}、錯誤 {stats['errors']}")
    return stats


# ------------------------------------------------------------------ #
# 會員簡訊工作流程的步驟表
#
# 「四、將會員通知併入過去資料」從一格擴成十幾格。理由不是好看：
# 先前整段收錄只回報一個「同步衍生資料」，跑十幾分鐘畫面都不動，
# 卡在哪一段完全看不出來，而它真正在做的事有十幾件。
# 逐日稽核之後的每一件都獨立成一格，卡住時一眼就看得到卡在哪。
# ------------------------------------------------------------------ #
SMS_MERGE_STEPS = [
    "準備",
    "盤點簡訊",
    "逐日稽核",
    "分類已解析與空白",
    "建立收錄佇列",
    "載入代號對照表",
    "讀取歷史買價",
    "AI 收錄個股",
    "規則稽核與代號比對",
    "條件式買賣去重",
    "寫入操作紀錄",
    "寫入會員持股",
    "原子取代舊列",
    "回寫解析狀態",
    "完成",
]

SMS_FETCH_STEPS = ["準備", "探索文章", "辨識張震", "篩選日期"] + SMS_MERGE_STEPS[1:]

# 每處理幾篇就落地一次。不是每篇一次，是因為試算表寫入有配額；
# 也不是全部跑完才寫，那正是先前撞到 429 就一列都沒寫進去的原因。
SMS_FLUSH_EVERY = max(1, int(os.environ.get("SMS_FLUSH_EVERY", "5") or 5))

# 列的分類。這是這一版的核心：先分好類，才知道哪些要花 Gemini。
SMS_ROW_DONE = "已收錄"           # 現行版本判定過，衍生列也寫好了
SMS_ROW_EMPTY = "已判定無個股"    # 現行版本判定過，真的沒有可收錄個股
SMS_ROW_WRITE = "待寫入"          # 判定過但衍生列還沒寫，不必再問 AI
SMS_ROW_BLANK = "空白待解析"      # 從來沒有被 AI 判定過
SMS_ROW_STALE = "舊版判定"        # 舊提示詞判定過
SMS_ROW_FAILED = "解析失敗"
SMS_ROW_QUARANTINE = "日期隔離"
SMS_ROW_CLEANUP = "待清空舊列"    # 判定為無個股，但操作紀錄裡還留著舊的衍生列


def classify_sms_row(state: str, detail: str, version: str, has_date: bool,
                     written: bool) -> str:
    """把一列會員簡訊歸到上面七類之一。只讀資料，不呼叫任何外部服務。"""
    if not has_date:
        return SMS_ROW_QUARANTINE
    st = str(state or "").strip()
    ver = str(version or "").strip()
    ok, items = load_saved_sms_items(detail)

    if ver == SMS_PROMPT_VERSION:
        if not ok or not items:
            # 判定過、結果是空的。這是可信的空結果，不再送 Gemini。
            # 但如果操作紀錄裡還留著這篇的舊衍生列，那是前一次判定的殘留，
            # 必須清掉，否則網站上會一直看到一筆早就被推翻的買賣。
            return SMS_ROW_CLEANUP if written else SMS_ROW_EMPTY
        return SMS_ROW_DONE if written else SMS_ROW_WRITE

    if ver:
        return SMS_ROW_STALE

    # 沒有版本戳記。有可用明細的是舊版檢查點，照樣可以直接寫，不必問 AI。
    if ok and items:
        return SMS_ROW_WRITE
    if "解析失敗" in st:
        return SMS_ROW_FAILED
    return SMS_ROW_BLANK


def sms_scope_of(mode: str) -> str:
    """把工作模式換算成「這一輪要處理哪些列」。"""
    return {
        "today": "today",         # 只更新當天
        "recent": "range",
        "all": "range",
        "ids": "range",
        "blanks": "blanks",       # 只補過去空白的收錄個股
        "merge": "blanks",        # 併入過去資料＝補空白＋把待寫入寫下去
        "reparse": "force",       # 明確要求全部重跑
    }.get(str(mode or "").strip().lower(), "blanks")


class SmsSteps:
    """步驟回報器。把第幾格換算成百分比，順便把步驟清單帶給後台。"""

    def __init__(self, steps: list[str], mode: str):
        self.steps = list(steps)
        self.mode = mode
        self.idx = 0

    def at(self, name: str, note: str = "", status: str = "處理中", **metrics):
        if name in self.steps:
            self.idx = self.steps.index(name)
        total = max(1, len(self.steps) - 1)
        pct = min(100, int(self.idx / total * 100))
        report_sms_progress(status=status, step=name, done=self.idx, total=total,
                            pct=pct, note=note, steps="|".join(self.steps), **metrics)

    def note(self, name: str, note: str, **metrics):
        self.at(name, note, **metrics)


def parse_pending_sms(ss, since="", mode=None, today_only=False):
    """
    會員簡訊：解析、收錄個股、寫入操作紀錄與會員持股。

    這一支要同時滿足兩件互相拉扯的事：
      1. 新的一篇進來時要立刻更新，而且只更新那一筆。
      2. 過去那些「收錄個股」還是空白的舊文章要補得回來。
    做法是先把每一列歸類（已判定／空白／待寫入／失敗），只有空白與失敗
    才會花 Gemini；已判定過的無論收錄幾筆都直接沿用。

    另外三件必須成立的事：
      - 每處理幾篇就把結果落地。撞到配額時已完成的部分要留在試算表裡，
        不能像先前那樣整批回滾、跑八次一列都沒寫進去。
      - 撞到「每日配額」立刻停，不退避。退避只對「每分鐘配額」有意義。
      - 這裡不碰任何衍生資料（每日整理、持股追蹤、績效、日K）。
        那些是另一條鏈的事，混在一起會讓一次簡訊更新跑掉半小時。
    """
    mode = (mode if mode is not None else SMS_MODE) or ""
    scope = "today" if today_only else sms_scope_of(mode)
    # 只有真的會去來源網站抓文章的三種模式，才多前面那三格。
    steps = SmsSteps(SMS_FETCH_STEPS if mode in ("recent", "all", "ids")
                     else SMS_MERGE_STEPS, mode)

    print("\n" + "=" * 60)
    print(f"會員簡訊工作流程　模式={mode or '自動'}　範圍={scope}"
          + (f"　起始={since}" if since else ""))
    print("=" * 60)
    steps.at("準備", "連線試算表並讀取會員簡訊分頁…")

    try:
        ws, headers = _ensure_sms_sheet(ss, "會員簡訊", SMS_HEADERS)
    except Exception as e:
        print(f"找不到或無法建立「會員簡訊」分頁：{e}")
        steps.at("完成", "找不到會員簡訊分頁，略過", status="完成")
        return set()

    records = sheets_retry(ws.get_all_values)
    if not records or len(records) < 2:
        print("「會員簡訊」分頁無資料。")
        steps.at("完成", "分頁無資料", status="完成")
        return set()

    headers = [str(h).strip() for h in records[0]]

    def col_idx(name):
        return headers.index(name) if name in headers else -1

    c_id = col_idx("文章ID")
    c_time = col_idx("發文時間")
    c_text = col_idx("原文")
    c_state = col_idx("解析狀態")
    c_detail = col_idx("解析明細")
    c_ver = col_idx("解析版本")
    c_at = col_idx("判定時間")

    if c_id < 0 or c_text < 0 or c_state < 0:
        print("會員簡訊分頁缺少必要欄位（文章ID、原文、解析狀態）。")
        steps.at("盤點簡訊", "缺少必要欄位", status="失敗")
        return set()
    if c_detail < 0 or c_ver < 0 or c_at < 0:
        print("會員簡訊分頁缺少解析明細／解析版本／判定時間欄位，且自動補欄失敗。")
        steps.at("盤點簡訊", "缺少解析版本欄位，請先讓抓取流程建立表頭", status="失敗")
        return set()

    # ---------------- 盤點 ---------------- #
    steps.at("盤點簡訊", "讀取全部列並還原發文日期…")
    existing_cids = get_existing_cmoney_ids(ss)
    since_norm = norm_date(since) if since else ""
    today_str = datetime.now(TAIPEI).strftime("%Y/%m/%d")

    rows = []
    for r_idx, row in enumerate(records[1:], start=2):
        def get(i):
            return str(row[i]).strip() if 0 <= i < len(row) else ""
        t_str = get(c_time)
        d_str = norm_date(t_str.split(" ")[0]) if t_str else ""
        art_id = get(c_id)
        rows.append({
            "row": r_idx, "id": art_id, "time": t_str, "date": d_str,
            "text": get(c_text), "state": get(c_state), "detail": get(c_detail),
            "version": get(c_ver),
            "kind": classify_sms_row(get(c_state), get(c_detail), get(c_ver),
                                     bool(d_str), f"CMONEY-{art_id}" in existing_cids),
        })

    # ---------------- 逐日稽核 ---------------- #
    # 收錄工作排在稽核之後：先把「哪一天有幾篇、幾篇還沒收錄個股」講清楚，
    # 再去動資料。這樣即使後面因配額停下，也已經知道還差多少。
    steps.at("逐日稽核", "逐日盤點各狀態筆數…")
    tally = {}
    for r in rows:
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
    audit_line = "、".join(f"{k} {v}" for k, v in sorted(tally.items())) or "無資料"
    print(f"逐日稽核：共 {len(rows)} 則　{audit_line}")
    day_gap = sorted({r["date"] for r in rows
                      if r["kind"] in (SMS_ROW_BLANK, SMS_ROW_FAILED, SMS_ROW_WRITE) and r["date"]})
    if day_gap:
        print(f"尚未完成收錄的日期共 {len(day_gap)} 天：{'、'.join(day_gap[:12])}"
              + ("…" if len(day_gap) > 12 else ""))
    steps.note("逐日稽核", f"{audit_line}；待補 {len(day_gap)} 天",
               blank=tally.get(SMS_ROW_BLANK, 0), doneRows=tally.get(SMS_ROW_DONE, 0),
               emptyRows=tally.get(SMS_ROW_EMPTY, 0), pendingWrite=tally.get(SMS_ROW_WRITE, 0))

    # ---------------- 分類與佇列 ---------------- #
    steps.at("分類已解析與空白",
             f"已用 AI 判定 {tally.get(SMS_ROW_DONE, 0) + tally.get(SMS_ROW_EMPTY, 0)} 則、"
             f"空白待解析 {tally.get(SMS_ROW_BLANK, 0)} 則")

    def in_scope(r):
        if r["kind"] == SMS_ROW_QUARANTINE:
            return False
        if scope == "today":
            return r["date"] == (since_norm or today_str)
        if scope == "range":
            return (not since_norm) or (r["date"] and r["date"] >= since_norm)
        if scope == "force":
            return (not since_norm) or (r["date"] and r["date"] >= since_norm)
        return True                                     # blanks：看全部歷史

    def wanted(r):
        if not in_scope(r):
            return False
        if scope == "force":
            return True                                 # 明確要求重跑，全部重判
        return r["kind"] in (SMS_ROW_BLANK, SMS_ROW_FAILED, SMS_ROW_WRITE,
                             SMS_ROW_STALE, SMS_ROW_CLEANUP)

    steps.at("建立收錄佇列", "挑出這一輪要處理的列…")
    queue = [r for r in rows if wanted(r)]
    queue.sort(key=lambda x: (x["time"] or "", x["row"]))

    need_ai = [r for r in queue
               if scope == "force" or r["kind"] in (SMS_ROW_BLANK, SMS_ROW_FAILED, SMS_ROW_STALE)]
    need_ai_rows = {r["row"] for r in need_ai}
    reuse = [r for r in queue if r["row"] not in need_ai_rows]
    if not queue:
        msg = ("當天沒有需要更新的會員簡訊。" if scope == "today"
               else "過去資料的收錄個股都已完成，沒有空白需要補。")
        print(msg)
        steps.at("完成", msg, status="完成")
        return set()

    # 單輪上限。做不完不是失敗：沒輪到的列維持原狀，下一次接著做。
    capped = need_ai[:SMS_MAX_AI_ARTICLES]
    capped_rows = {r["row"] for r in capped}
    deferred_ai = len(need_ai) - len(capped)
    if deferred_ai:
        print(f"本輪最多處理 {SMS_MAX_AI_ARTICLES} 則需要 AI 的文章，"
              f"其餘 {deferred_ai} 則留給下一次執行，避免一次把當日配額用光。")
    # 順序很重要：不需要 AI 的先做。
    #
    # 那些列只是把已經判定好的明細寫進操作紀錄，一次模型呼叫都不花，
    # 也不可能因為配額失敗。放在後面的話，只要前面任何一篇撞到 429，
    # 這些「本來一定能完成」的列就會跟著被拖住，白白多等一天。
    # 先做完它們並落地，再去碰會失敗的那一段。
    reuse.sort(key=lambda x: (x["time"] or "", x["row"]))
    capped.sort(key=lambda x: (x["time"] or "", x["row"]))
    process = reuse + capped

    print(f"本輪處理 {len(process)} 則：直接沿用明細 {len(reuse)} 則（先做，不花配額）、"
          f"需要 AI {len(capped)} 則。")
    steps.note("建立收錄佇列",
               f"待處理 {len(process)} 則（需 AI {len(capped)}、沿用 {len(reuse)}）"
               + (f"，另有 {deferred_ai} 則排入下一輪" if deferred_ai else ""),
               queued=len(process), aiQueued=len(capped), reused=len(reuse),
               deferred=deferred_ai)

    # ---------------- 前置資料 ---------------- #
    steps.at("載入代號對照表", "向證交所與櫃買中心取得上市櫃清單…")
    code_map = get_code_map()
    steps.note("載入代號對照表", f"對照表 {len(code_map)} 檔"
               + ("" if _CODE_MAP_FULL else "（有一邊來源失敗，代號查核本輪放寬）"))

    steps.at("讀取歷史買價", "讀取操作紀錄中既有的明講買入價…")
    pending_sources = {f"CMONEY-{r['id']}" for r in process}
    prior_buy_prices = get_prior_sms_buy_prices(ss, pending_sources)
    steps.note("讀取歷史買價", f"已建立 {len(prior_buy_prices)} 檔的歷史買入價")

    cm_mark = re.compile(r'(?:張震|震)\s*(?:6GJ)?\s*[-－—─]?\s*[0-9０-９]{0,2}\s*[:：]')

    # ---------------- 落地緩衝與批次寫入 ---------------- #
    trades_buf: list[list] = []
    holds_buf: list[list] = []
    status_buf: list[tuple] = []
    replace_counts: dict[str, dict[str, int]] = {}
    changed_dates: set[str] = set()
    written_articles = 0
    ai_calls_used = 0
    quota_note = ""

    def flush(reason=""):
        """把目前累積的結果寫進試算表。中途被中止時已寫入的部分都留著。"""
        nonlocal trades_buf, holds_buf, status_buf, replace_counts
        if not (trades_buf or holds_buf or status_buf):
            return
        if trades_buf:
            steps.at("寫入操作紀錄", f"寫入 {len(trades_buf)} 筆買賣"
                     + (f"（{reason}）" if reason else ""))
            sheets_retry(ss.worksheet("操作紀錄").append_rows, trades_buf,
                         value_input_option="USER_ENTERED")
            print(f"  已寫入操作紀錄 {len(trades_buf)} 筆")
        if holds_buf:
            steps.at("寫入會員持股", f"寫入 {len(holds_buf)} 筆持股"
                     + (f"（{reason}）" if reason else ""))
            sheets_retry(ss.worksheet("會員持股").append_rows, holds_buf,
                         value_input_option="USER_ENTERED")
            print(f"  已寫入會員持股 {len(holds_buf)} 筆")
        if replace_counts:
            steps.at("原子取代舊列", f"移除 {len(replace_counts)} 篇文章的舊衍生列")
            _remove_old_source_rows_keep_latest(
                ss, "操作紀錄", {s: n["操作紀錄"] for s, n in replace_counts.items()})
            _remove_old_source_rows_keep_latest(
                ss, "會員持股", {s: n["會員持股"] for s, n in replace_counts.items()})
        if status_buf:
            steps.at("回寫解析狀態", f"更新 {len(status_buf)} 個欄位")
            sheets_retry(ws.batch_update,
                         [{"range": gspread.utils.rowcol_to_a1(rn, col), "values": [[val]]}
                          for rn, col, val in status_buf],
                         value_input_option="RAW")
        trades_buf, holds_buf, status_buf, replace_counts = [], [], [], {}

    def stamp(r, items, note_state):
        """把一篇的結果排進緩衝。版本戳記一定要跟明細一起寫。"""
        nonlocal written_articles
        src_id = f"CMONEY-{r['id']}"
        t_cnt = h_cnt = 0
        for it in items:
            if it["action"] == "會員持股":
                holds_buf.append([r["date"], it["name"], it["code"], "續抱",
                                  it.get("note") or "簡訊通知持股續抱", src_id])
                h_cnt += 1
            else:
                trades_buf.append([r["date"], it["name"], it["code"], it["action"],
                                   it.get("priceText", "未說明"), it.get("note", "")[:60],
                                   src_id, ""])
                t_cnt += 1
        if items:
            replace_counts[src_id] = {"操作紀錄": t_cnt, "會員持股": h_cnt}
            existing_cids.add(src_id)
        elif src_id in existing_cids:
            # 這一篇先前有衍生列，重判之後沒有個股，舊列必須清掉。
            _delete_rows_by_source(ss, src_id)
            existing_cids.discard(src_id)
        status_buf.append((r["row"], c_state + 1, note_state))
        status_buf.append((r["row"], c_detail + 1, serialize_sms_items(items)))
        status_buf.append((r["row"], c_ver + 1, SMS_PROMPT_VERSION))
        status_buf.append((r["row"], c_at + 1, _sms_now()))
        changed_dates.add(r["date"])
        written_articles += 1

    # ---------------- 逐篇處理 ---------------- #
    steps.at("AI 收錄個股", f"開始處理 {len(process)} 則…")
    total_cnt = len(process)
    quota_strikes = 0
    stopped_early = ""

    try:
        for idx, r in enumerate(process):
            art = r["id"]
            base_note = f"第 {idx + 1}/{total_cnt} 則（文章 {art}）"
            use_ai = r["row"] in capped_rows

            if not use_ai:
                ok, items = load_saved_sms_items(r["detail"])
                if r["kind"] == SMS_ROW_CLEANUP:
                    # 判定過、沒有個股，但舊衍生列還在。用空清單走一次 stamp，
                    # 它會把該來源的舊列刪掉，不需要任何模型呼叫。
                    ok, items = True, []
                    print(f"  文章 {art}：已判定無個股，清除殘留的舊衍生列")
                    steps.note("原子取代舊列", f"{base_note}：清除殘留舊列")
                if not ok:
                    # 排進來時判定為可沿用，這裡卻讀不出來，代表明細在這一輪被改過。
                    print(f"  文章 {art}：明細已不可用，改排入下一輪重新解析")
                    status_buf.append((r["row"], c_state + 1, "待解析（明細缺漏）"))
                    status_buf.append((r["row"], c_ver + 1, ""))
                    continue
                steps.note("規則稽核與代號比對",
                           f"{base_note}：沿用既有明細 {len(items)} 筆，不呼叫 Gemini")
                print(f"  文章 {art}：沿用已保存解析明細 {len(items)} 筆，不呼叫 Gemini")
            else:
                if ai_calls_used >= SMS_AI_MAX_CALLS:
                    stopped_early = (f"本輪已用滿 {SMS_AI_MAX_CALLS} 次模型呼叫，"
                                     f"其餘文章保留原狀，下一次執行接著做。")
                    print(stopped_early)
                    break
                if quota_exhausted():
                    stopped_early = _QUOTA_STOP["reason"]
                    break

                steps.note("AI 收錄個股", f"{base_note}：呼叫 Gemini 抽取買賣與持股",
                           aiUsed=ai_calls_used)
                try:
                    items, parse_err, calls = _sms_extract_items(r, cm_mark, code_map)
                    ai_calls_used += calls
                except RateLimited as e:
                    ai_calls_used += SMS_MAX_QUOTA_STRIKES     # 已經打出去的都算用量
                    # 每日配額：立刻停。等待與重按都只會再拿到 429，且照樣計入用量。
                    if getattr(e, "daily", False):
                        raise
                    # 每分鐘配額：容許跳過這一篇再試下一篇，但次數要有上限，
                    # 否則一輪就會把剩下的每一篇都拿去撞牆。
                    quota_strikes += 1
                    status_buf.append((r["row"], c_state + 1, "待解析（配額續跑）"))
                    print(f"  文章 {art}：撞到每分鐘配額（第 {quota_strikes} 次），"
                          f"本篇保留待解析")
                    if quota_strikes >= SMS_MAX_QUOTA_STRIKES:
                        print(f"  連續 {quota_strikes} 篇撞到配額，本輪到此為止，"
                              f"已完成的部分即將落地。")
                        raise
                    flush(f"配額跳篇後落地（{art}）")
                    continue
                if parse_err:
                    print(f"  文章 {art}：解析失敗，保留原狀等待下一輪")
                    status_buf.append((r["row"], c_state + 1, "解析失敗（未改動舊紀錄）"))
                    continue

            steps.note("條件式買賣去重", f"{base_note}：核對條件式買進與歷史買價")
            items = _sms_dedupe_and_condition(items, r, prior_buy_prices)

            if items:
                state_text = (f"已寫入 {sum(1 for i in items if i['action'] != '會員持股')} 筆買賣、"
                              f"{sum(1 for i in items if i['action'] == '會員持股')} 筆持股")
            else:
                state_text = "已解析（AI 判定無可收錄個股）"
            stamp(r, items, state_text)
            print(f"  文章 {art}：{state_text}")

            # 沿用明細那一段做完就先落地。這一段不會失敗，成果應該立刻進試算表，
            # 不要跟後面可能撞配額的 AI 段落綁在同一個交易裡。
            if idx + 1 == len(reuse) and reuse:
                flush("沿用明細段完成")
            elif (idx + 1) % SMS_FLUSH_EVERY == 0:
                flush(f"已完成 {idx + 1}/{total_cnt}")

    except RateLimited as e:
        # 這是本版最重要的一行：先落地，再往上拋。
        # 先前是整批跑完才寫，429 一來全部回滾，於是連按八次都沒有寫進任何一列。
        quota_note = str(e)
        print(f"配額中止：{quota_note}　已完成的部分即將落地，不會回滾。")
        flush("配額中止前落地")
        report_sms_progress(
            status="配額暫停", step="AI 收錄個股", done=steps.idx,
            total=max(1, len(steps.steps) - 1), pct=min(99, int(steps.idx / max(1, len(steps.steps) - 1) * 100)),
            note=(f"{quota_note}。已完成 {written_articles} 則並寫入試算表，"
                  + (f"額度約於台北時間 {getattr(e, 'reset_at', '') } 重置，在那之前重按不會有用。"
                     if getattr(e, "daily", False) and getattr(e, "reset_at", "") else
                     "稍後再按同一顆按鈕即可從未完成的文章接續。")),
            steps="|".join(steps.steps), written=written_articles,
            cooldown_until=getattr(e, "reset_at", ""),
            quota_kind="daily" if getattr(e, "daily", False) else "minute")
        write_status_log(ss, "配額暫停",
                         f"會員簡訊：{quota_note}；已落地 {written_articles} 則")
        raise

    flush("收尾")

    remaining = deferred_ai + max(0, len(capped) - written_articles)
    fin = (f"本輪完成 {written_articles} 則，模型呼叫 {ai_calls_used} 次。"
           + (f"尚有 {remaining} 則待下一輪。" if remaining else "過去資料的收錄個股已全數補齊。")
           + (f" {stopped_early}" if stopped_early else ""))
    print(fin)
    write_status_log(ss, "會員簡訊", fin)
    steps.at("完成", fin, status="完成", written=written_articles,
             aiUsed=ai_calls_used, remaining=remaining)
    if changed_dates:
        print("本輪異動日期：" + "、".join(sorted(changed_dates)))
        print("（這一支不重算每日整理、持股追蹤、績效與日K。"
              "需要更新網站時，到後台按一次刷新即可。）")
    return changed_dates


def _sms_extract_items(r, cm_mark, code_map):
    """
    對一篇簡訊呼叫 Gemini 並做規則稽核。回傳 (items, 是否失敗, 用掉幾次呼叫)。

    max_429=2：會員簡訊一輪要跑幾十篇，每篇退避六輪的話光等待就會把
    job 的時間預算耗盡，而且每一次退避後的重試仍然計入當日用量。
    """
    text = r["text"]
    items, calls, parse_err = [], 0, False

    marks = list(cm_mark.finditer(text))
    orders = []
    if marks:
        for m_i, m in enumerate(marks):
            start = m.end()
            end = marks[m_i + 1].start() if m_i + 1 < len(marks) else len(text)
            body = text[start:end].strip()
            if body:
                orders.append({"tag": m.group().strip(), "body": body})
    elif text.strip():
        orders.append({"tag": "簡訊", "body": text.strip()})

    for o in orders:
        try:
            calls += 1
            raw_json = call_gemini(CM_PARSE_SYSTEM, o["body"], want_json=True,
                                   tag=f"sms_{r['id']}", max_429=2)
            time.sleep(SMS_AI_GAP)
            data = raw_json if isinstance(raw_json, dict) else None
            if data is None:
                m_json = re.search(r'\{.*\}', str(raw_json).strip(), re.DOTALL)
                data = json.loads(m_json.group()) if m_json else {}
            for it in (data.get("items", []) if isinstance(data, dict) else []):
                v = verify_sms_item(it, o["body"], code_map)
                if v:
                    v["tag"] = o["tag"]
                    items.append(v)
        except RateLimited:
            raise
        except Exception as ex:
            print(f"  文章 {r['id']} 呼叫 Gemini 失敗：{ex}")
            parse_err = True

    # 第一次回空但原文同時有合法台股與操作詞時，做一次獨立複核。
    # 純盤勢或歷史回顧不會進這一關，所以不會為每篇都加倍耗用額度。
    if not items and not parse_err and sms_needs_empty_audit(text, code_map):
        print(f"  文章 {r['id']}：初次無可收錄，但偵測到個股與操作語意，啟動完整性稽核")
        try:
            calls += 1
            audit_raw = call_gemini(CM_EMPTY_AUDIT_SYSTEM, text, want_json=True,
                                    tag=f"sms_empty_audit_{r['id']}", max_429=2)
            time.sleep(SMS_AI_GAP)
            audit_data = audit_raw if isinstance(audit_raw, dict) else None
            if audit_data is None:
                m_a = re.search(r'\{.*\}', str(audit_raw), re.DOTALL)
                audit_data = json.loads(m_a.group()) if m_a else {}
            for raw_it in (audit_data.get("items", []) if isinstance(audit_data, dict) else []):
                verified = verify_sms_item(raw_it, text, code_map)
                if verified:
                    verified["tag"] = "完整性複核"
                    items.append(verified)
            print(f"  文章 {r['id']}：完整性稽核後共收錄 {len(items)} 筆")
        except RateLimited:
            raise
        except Exception as ex:
            print(f"  文章 {r['id']} 完整性稽核失敗，保留重試資格：{ex}")
            parse_err = True

    return items, parse_err, calls


def _sms_dedupe_and_condition(items, r, prior_buy_prices):
    """
    去重、補上條件式續抱，並在歷史已有更低買價時省略這次的條件式買進。

    條件式訊息常同時說「未持有者 X 以下買進、已持有者續抱不加碼」。
    若歷史已有更低的明講買入價，這次不是新的進場，買入列不呈現；
    持股續抱仍保留。第一次出現，或這次價位更低時，才留下買入動作。
    """
    text = r["text"]
    deduped, seen = [], set()
    for it in items:
        key = (it["code"], it["action"], it.get("price", ""), it.get("limit", ""))
        if key not in seen:
            seen.add(key)
            deduped.append(it)
    items = deduped

    conditional_hold = bool(re.search(
        r"未持有.{0,40}(?:買進|買入|買回).{0,80}已持有.{0,40}(?:續抱|不加碼)", text))
    if conditional_hold:
        held = {x["code"] for x in items if x["action"] == "會員持股"}
        for buy in [x for x in items if x["action"] == "買入" and x["code"] not in held]:
            items.append({"name": buy["name"], "code": buy["code"], "action": "會員持股",
                          "price": "", "limit": "", "priceText": "未說明",
                          "note": "已持有者續抱，不加碼", "tag": buy.get("tag", "簡訊")})

    out = []
    for it in items:
        if it["action"] == "買入" and conditional_hold and it.get("price"):
            current = float(it["price"])
            history = prior_buy_prices.get(it["code"], [])
            prior_values = [price for when, price in history if when < (r["time"] or "")]
            prior = min(prior_values) if prior_values else None
            if prior is not None and prior <= current:
                print(f"  {it['code']} {it['name']}：歷史買入 {prior:g} 低於本次 {current:g}，"
                      f"省略條件式買入，保留續抱")
                continue
            prior_buy_prices.setdefault(it["code"], []).append((r["time"] or "", current))
        out.append(it)
    return out


def auto_parse_today_sms(ss):
    """
    每日流程收尾時順手解析「當天」的會員簡訊。

    這裡的範圍限制是為了修一個實際發生過的事故。原本這一行是
    parse_pending_sms(ss)，沒有任何範圍，而挑選條件把「無可收錄」也
    當成未解決，於是每一次排程觸發都會把整個歷史裡所有沒有個股的
    純盤勢文章重新丟給 Gemini 判一次。一天十幾個觸發點乘上二三十篇，
    免費層的每日請求數在中午前就見底，接著當天的逐字稿擷取
    （tag=extract）就會拿到 HTTP 429 而失敗——那正是
    「Gemini 配額用盡（extract）」的來源。

    當天的簡訊本來就只有幾篇，需要 AI 的通常是零到兩篇。
    過去資料的補齊交給後台那顆按鈕，不要跟每日排程搶配額。
    """
    if quota_exhausted():
        print("（Gemini 當日配額已用盡，略過會員簡訊解析）")
        return
    try:
        parse_pending_sms(ss, mode="today", today_only=True)
    except RateLimited as e:
        # 這不是每日流程的失敗。簡訊沒解析到不影響逐字稿與操作紀錄。
        print(f"（會員簡訊解析因配額暫停，不影響主流程：{e}）")
    except Exception as e:
        print(f"（自動解析會員簡訊跳過或出錯，不影響主流程：{e}）")


def refresh_sms_mail_dates(dates: set[str]):
    """
    已停用。保留函式簽章只為了讓舊的呼叫點不會 NameError。

    這一支原本會對每個異動日期打一次 step=smsmail，要下游重寫該日的
    每日整理。它是「同步衍生資料」的一部分，而衍生資料的重算已經從
    會員簡訊這條鏈整個拿掉了：一次簡訊更新不該連帶跑每日整理、
    持股追蹤、績效與補齊日K。要更新網站時，到後台按刷新。
    """
    if dates:
        print("（會員簡訊不再自動重寫每日整理；異動日期："
              + "、".join(sorted(dates)) + "）")


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
    所以沒有拼音誤判、沒有 429，幾秒就跑完。
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


def preflight_gemini_keys():
    """
    開跑前先確認每一把金鑰能不能用。只列模型清單，不產生內容，不算生成配額。

    值得多這一步：一把設定錯的金鑰（專案沒開通 API、貼錯、看不到這個模型）
    只有在真的輪替到它的時候才會爆，而那通常是流程跑到一半、
    第一把額度用完之後——最不希望出事的時間點。先問一次，
    有問題的當場標記起來並印出要修哪一個 Secret，之後直接跳過它。
    """
    if not GEMINI_KEYS or len(GEMINI_KEYS) < 2:
        return                      # 只有一把時沒有「跳過」的餘地，讓它照原路報錯
    print(f"檢查 {len(GEMINI_KEYS)} 把 Gemini 金鑰……")
    for i, (source, key) in enumerate(GEMINI_KEY_ENTRIES):
        try:
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}",
                params={"key": key}, timeout=30)
        except Exception as e:
            print(f"  {key_label(i)}：連線失敗（{type(e).__name__}），本輪照常保留")
            continue
        if r.status_code == 200:
            print(f"  {key_label(i)}：可用")
            continue
        if r.status_code == 429:
            print(f"  {key_label(i)}：目前被限流，保留（額度問題會在實際呼叫時處理）")
            continue
        why = _key_problem(r.status_code, r.text or "") or f"HTTP {r.status_code}"
        _KEY_STATE["dead"][i] = why
        print(f"  {key_label(i)}：不可用　{why}")
    usable = [i for i in range(len(GEMINI_KEYS)) if i not in _KEY_STATE["dead"]]
    if not usable:
        print("警告：沒有任何一把金鑰通過檢查。後面的步驟會用原文降級處理，"
              "逐字稿不會遺失，但擷取與撰稿無法進行。")
        return
    _KEY_STATE["idx"] = usable[0]
    if len(usable) < len(GEMINI_KEYS):
        print(f"本輪可用 {len(usable)}/{len(GEMINI_KEYS)} 把，"
              f"從 {key_label(usable[0])} 開始，不可用的直接跳過。")


def main():
    global _SS
    src = "Variables" if os.environ.get("YOUTUBE_CHANNEL_ID", "").strip() else "內建預設值"
    print(f"頻道 ID：{CHANNEL_ID}（{src}）")

    ss = open_sheets()
    _SS = ss
    write_status_log(ss, "開始", "本輪開始執行")

    # 這三個是互斥模式，同時勾選只有第一個會生效。
    # 先前就發生過三個都勾、結果只跑了修代號的情況，所以這裡明講。
    picked = [n for n, on in (("admin_job", ADMIN_JOB),
                              ("full_fix", FULL_FIX),
                              ("parse_sms", PARSE_SMS),
                              ("repair_codes", REPAIR_CODES),
                              ("reclassify", RECLASSIFY),
                              ("fix_prices", FIX_PRICES),
                              ("reconcile", RECONCILE),
                              ("fill_blanks", FILL_BLANKS),
                              ("backfill", BACKFILL)) if on]
    if len(picked) > 1:
        print(f"注意：同時勾選了 {'、'.join(picked)}，這些是互斥模式，"
              f"本輪只會執行「{picked[0]}」。其餘請分次執行。")

    # 會用到 Gemini 的模式，在這裡就先確認金鑰在不在。
    # 延後到真正呼叫才檢查雖然不會出錯，但可能已經跑了好幾分鐘才炸。
    #
    # 判斷方式是「這個模式自己會不會呼叫 Gemini」，不是看有沒有勾 refresh_site。
    # 舊的條件寫成「REFRESH_SITE 且沒有其中幾個模式就跳過檢查」，於是
    # 後台工單（admin_job）因為派工時一律帶 refresh_site=true，整個檢查被跳過，
    # 一路跑到「擷取」才在第一次呼叫模型時炸出「缺少環境變數 GEMINI_API_KEY」。
    # 那時已經讀完原文、跑完潤飾，白花好幾分鐘，而錯誤訊息又出現在
    # 完全看不出關聯的地方。
    NO_GEMINI_MODES = {"repair_codes", "reclassify", "full_fix"}
    # 只勾 refresh_site、沒有勾任何模式，代表「資料不用動，只要網站重算一次」，
    # 那條路一次模型都不會呼叫，不該為了它要求金鑰。
    refresh_only = REFRESH_SITE and not picked
    needs_gemini = not (PREFLIGHT or refresh_only) and (
        not picked or bool(set(picked) - NO_GEMINI_MODES))
    if needs_gemini:
        require_gemini_key()
        # 順便確認每一把都真的能用。設定錯的那一把若等到輪替時才爆，
        # 通常已經是流程跑到一半、第一把額度用完之後——最不該出事的時間點。
        preflight_gemini_keys()

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

    if FULL_FIX:
        print("模式：全面重整。逐棒驅動下游把過去所有資料套用最新規則。")
        print("不碰 NotebookLM，Gemini 由下游呼叫，這裡只負責一棒一棒催它做完。")
        drive_full_fix()
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

    if PARSE_SMS:
        # 會員簡訊這一條鏈只做一件事：把簡訊變成操作紀錄與會員持股。
        #
        # 它刻意不再呼叫 maybe_refresh_site()，也不再重寫每日整理。
        # 那條全站重算鏈裡包含補齊日K、重算持股追蹤、記錄績效，
        # 動輒十幾分鐘到數十分鐘，而且與「這一篇簡訊有沒有收錄到個股」
        # 是完全獨立的兩件事。綁在一起的後果是：更新一筆簡訊要等一次全站重算，
        # 中間任何一步失敗還會讓已經寫好的簡訊資料被判成失敗。
        # 要更新網站時，到後台按一次刷新，或等當日排程即可。
        if SMS_MODE in ("recent", "all", "ids"):
            print(f"模式：會員簡訊 {SMS_MODE}。先抓取、辨識張震與嚴格日期，再解析寫入。")
            report_sms_progress(step="準備", done=0, total=len(SMS_FETCH_STEPS) - 1, pct=2,
                                note="連線來源網站與試算表", steps="|".join(SMS_FETCH_STEPS))
            articles = fetch_cmoney_articles(SMS_MODE, SMS_IDS)
            report_sms_progress(step="辨識張震", done=2, total=len(SMS_FETCH_STEPS) - 1, pct=18,
                                note=f"已取得 {len(articles)} 篇，開始身分與日期稽核",
                                steps="|".join(SMS_FETCH_STEPS))
            stats = save_cmoney_fetch(ss, articles, SMS_MODE)
            report_sms_progress(step="篩選日期", done=3, total=len(SMS_FETCH_STEPS) - 1, pct=25,
                                note=f"新收 {stats['saved']}、既有 {stats['existed']}、錯誤 {stats['errors']}",
                                steps="|".join(SMS_FETCH_STEPS), **stats)

        label = {
            "today": "只更新當天的會員簡訊。",
            "blanks": "只補過去資料中收錄個股仍空白的文章，已判定過的一律不重跑。",
            "merge": "併入過去資料：沿用已判定明細，並用 AI 補齊仍空白的收錄個股。",
            "reparse": "重新解析：依選定範圍強制重判，會消耗較多模型配額。",
        }.get(SMS_MODE, "解析會員簡訊：處理待解析與空白的收錄個股。")
        print("模式：" + label)

        changed_dates = parse_pending_sms(ss, since=SMS_SINCE,
                                          today_only=(SMS_MODE == "today")) or set()
        if changed_dates:
            print("提醒：本輪只更新了會員簡訊與操作紀錄／會員持股。"
                  "每日整理、持股追蹤、績效與日K不在這條鏈裡，需要時請到後台刷新。")
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
        auto_parse_today_sms(ss)
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

    # 融入既有工作流程：每次每日流程結束後，順道解析「當天」待解析的會員簡訊。
    if not BACKFILL:
        auto_parse_today_sms(ss)


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
    except RateLimited as e:
        # 這是可恢復的額度狀態，不是程式壞掉。GitHub 保持綠燈，後台顯示
        # 「配額暫停」，已完成的部分都已經落地，不會回滾。
        reset_at = getattr(e, "reset_at", "") or _QUOTA_STOP.get("reset_at", "")
        print(f"流程因 Gemini 配額暫停：{e}")
        if getattr(e, "daily", False):
            print("這是「每日請求數」上限，不是「每分鐘」。在額度重置之前，"
                  "不論等多久或重按幾次，每一次都只會再拿到一個 429，"
                  f"而且仍然計入用量。下次可用時間約為台北時間 {reset_at}。")
        if PARSE_SMS:
            # parse_pending_sms 已經把落地結果與 cooldown 回報過一次。
            # 這裡只補「整體停在配額」這個結論，不覆蓋前面比較詳細的說明。
            report_sms_progress(
                status="配額暫停", step="AI 收錄個股", done=7,
                total=len(SMS_MERGE_STEPS) - 1, pct=50,
                note=(f"Gemini 配額暫停：{str(e)[:120]}。已完成文章都已寫入試算表，"
                      + (f"額度約於台北時間 {reset_at} 重置。" if reset_at else "稍後再按同一顆按鈕即可接續。")),
                steps="|".join(SMS_MERGE_STEPS),
                cooldown_until=reset_at,
                quota_kind="daily" if getattr(e, "daily", False) else "minute")
        if _SS is not None:
            write_status_log(_SS, "配額暫停", str(e) + (f"；約於 {reset_at} 重置" if reset_at else ""))
        sys.exit(0)
    except Exception as e:
        print(f"流程失敗：{e}", file=sys.stderr)
        if PARSE_SMS:
            report_sms_progress(status="失敗", step="失敗", done=0, total=8, pct=0, note=str(e)[:200])
        if _SS is not None:
            write_status_log(_SS, "失敗", str(e))
        sys.exit(1)
