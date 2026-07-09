# 部署與設定說明

張震股市盤中家教班　逐日追蹤網站（v3 架構）

本文件分成三部分：Apps Script 網站端、GitHub Actions 資料蒐集端、以及金鑰安全處理。照順序做，中文不要跳。

---

## 檔案清單

```
gas/                              貼進 Apps Script 專案
  Code.gs                         主入口，doGet 路由與前端可呼叫的 API
  Setup.gs                        一次性設定，建立試算表、安裝觸發器
  SheetService.gs                 試算表讀寫、查詢、統計、年度封存
  QuoteService.gs                 即時報價、歷史 K 線、共用快取與速率限制
  MailService.gs                  訂閱、取消訂閱、每日推播、失敗告警
  AiService.gs                    Gemini 呼叫，金鑰只從 Script Properties 讀取
  Index.html                      主頁
  Tech.html                       技術說明分頁，含下拉 QA 與作者資訊
  Stylesheet.html                 樣式，雙主題
  JavaScript.html                 前端腳本，圖表與互動
  Unsubscribed.html               取消訂閱結果頁

github/                           推到 GitHub 儲存庫的根目錄
  pipeline.py                     每日流程主程式
  requirements.txt                Python 相依套件
  .github/workflows/daily.yml     排程工作流程
```

---

## 第一部分：Apps Script 網站端

### 1. 建立專案並貼檔案

1. 前往 `script.google.com`，按「新增專案」，把專案命名為「張震盤中家教班追蹤」。
2. 左側檔案面板，把預設的 `程式碼.gs` 改名為 `Code`，內容換成 `gas/Code.gs`。
3. 依序按「+」新增檔案。副檔名 `.gs` 選「指令碼」，`.html` 選「HTML」。檔名不要帶副檔名，Apps Script 會自己補。
   - 指令碼：`Setup`、`SheetService`、`QuoteService`、`MailService`、`AiService`
   - HTML：`Index`、`Tech`、`Stylesheet`、`JavaScript`、`Unsubscribed`
4. 存檔。

### 2. 建立試算表，取得試算表 ID

這一步就是「直接建立試算表 ID」。

1. 上方函式下拉選單選 `setupSpreadsheet`，按「執行」。
2. 第一次執行會跳授權視窗，選你的 Google 帳號，按「進階」再按「前往（不安全）」，允許。這是自己的專案在存取自己的雲端硬碟，屬正常流程。
3. 執行完成後看下方「執行紀錄」，會印出：

```
試算表已建立。
SPREADSHEET_ID = 1AbCdEf...（這一串就是你要的 ID）
網址：https://docs.google.com/spreadsheets/d/...
```

這支函式同時做完了三件事：建立試算表、建立七個分頁並填好欄位標題、把 ID 寫進 Script Properties。網站端不需要你再手動填 ID。

**GitHub Actions 端還是需要這串 ID**，等一下會用到，先複製起來。

### 3. 設定 Gemini 金鑰

金鑰不寫進程式碼。到左側「專案設定」，往下捲到「指令碼屬性」，按「新增指令碼屬性」：

| 屬性名稱 | 值 |
| --- | --- |
| `GEMINI_API_KEY` | 你的 Gemini 金鑰字串 |

存檔。這個位置只有專案擁有者看得到，網頁使用者端無法讀取，`google.script.run` 的回傳值也不會帶出來。

### 4. 設定收信人與觸發器

1. 執行 `setOwnerEmail`。失敗告警信會寄到這個信箱，不是寄給訂閱者。若要換信箱，直接到指令碼屬性改 `OWNER_EMAIL` 這一欄。
2. 執行 `installTriggers`。會裝三個時間觸發器：
   - `dailyPushJob`　每天 13:30，寄每日總覽與關注股票異動提醒
   - `failureAlertJob`　每天 13:45，檢查當日處理狀態，失敗就通知你
   - `yearlyArchiveJob`　每月 5 號檢查，只有 1 月才真的執行年度封存
3. 若要每週更新股票代號對照表，再手動加一個觸發器指向 `refreshCodeMapJob`，每週執行一次。

### 5. 驗證設定

執行 `checkSetup`，執行紀錄會列出五項狀態。金鑰只顯示長度，不顯示內容。全部顯示已設定就可以往下走。

### 6. 部署為網頁應用程式

1. 右上角「部署」→「新增部署作業」→ 類型選「網頁應用程式」。
2. 執行身分：**我**。存取權：**任何人**。
3. 按「部署」，複製出現的網址（結尾是 `/exec`）。這就是網站網址，也是取消訂閱連結的基底。

取消訂閱連結由 `ScriptApp.getService().getUrl()` 動態產生，不需要手動填。之後每次改程式碼，記得按「部署」→「管理部署作業」→ 編輯 → 版本選「新版本」，否則使用者看到的還是舊版。

---

## 第二部分：GitHub Actions 資料蒐集端

### 1. 本機一次性設定（在你自己的電腦上做，不在 Actions 裡做）

```bash
pip install "notebooklm-py[browser]"
playwright install chromium

notebooklm login              # 用你備妥的專用 Google 帳號登入
notebooklm auth check --test --json
```

`auth check` 回傳驗證成功後，本機會產生一個 session 憑證檔案（多半在 `~/.notebooklm/storage_state.json`，實際路徑以 `notebooklm login` 的輸出為準）。把這個檔案的**完整內容**複製起來，等一下貼進 GitHub Secrets。

日後 Actions 開始回報授權失敗時，重跑這三行、更新 Secret，就恢復了。這是整套系統唯一需要人工定期維護的環節。

### 2. 建立服務帳號，讓 Actions 寫得進試算表

1. 前往 Google Cloud Console，建立或選一個專案。
2. 「API 和服務」→「程式庫」→ 搜尋 Google Sheets API → 啟用。
3. 「API 和服務」→「憑證」→「建立憑證」→「服務帳號」，填名稱後建立。
4. 進入該服務帳號 →「金鑰」→「新增金鑰」→「建立新的金鑰」→ 選 JSON → 下載。
5. 回到第一部分建立的試算表，右上角「共用」，把服務帳號的 Email（長得像 `xxx@xxx.iam.gserviceaccount.com`）加為**編輯者**。沒做這步，Actions 會拿到 403。

### 3. 建立儲存庫並放檔案

把 `github/` 底下三個檔案放到儲存庫根目錄，維持這個結構：

```
你的儲存庫/
  pipeline.py
  requirements.txt
  .github/
    workflows/
      daily.yml
```

建議設為**公開儲存庫**，Actions 完全免費且無時數上限。程式碼裡沒有任何金鑰，公開沒有風險。若你堅持設為私有，每月有免費額度，這個工作量用不完。

### 4. 設定 Secrets

儲存庫 → Settings → Secrets and variables → Actions → Secrets 分頁 → New repository secret。

| Secret 名稱 | 內容 |
| --- | --- |
| `NOTEBOOKLM_AUTH_JSON` | 步驟 1 產生的 session 憑證檔案完整內容 |
| `GEMINI_API_KEY` | 你的 Gemini 金鑰字串 |
| `GOOGLE_SHEETS_SERVICE_ACCOUNT` | 步驟 2 下載的 JSON 金鑰完整內容 |
| `SPREADSHEET_ID` | 第一部分步驟 2 印出來的那串 ID |

### 5. 設定 Variables

同一頁切到 Variables 分頁 → New repository variable。

| Variable 名稱 | 內容 |
| --- | --- |
| `YOUTUBE_CHANNEL_ID` | 頻道 ID，格式 `UCxxxxxxxxxxxxxxxxxxxxxx` |

取得方式：開啟 `youtube.com/@xinchenginsta`，網頁上按右鍵「檢視網頁原始碼」，用 Ctrl+F 搜尋 `channelId`，後面那串 `UC` 開頭的就是。

放 Variables 不放 Secrets，是因為頻道 ID 本來就是公開資訊，放 Secrets 反而讓 log 難以除錯。

### 6. 程式碼要改哪裡

`pipeline.py` 裡標了三個「【要改】」，搜尋這三個字就找得到：

**【要改】1，第 30 行附近**

```python
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "").strip()
```

如果你不想用 Variables，可以直接把預設值填進去：

```python
CHANNEL_ID = os.environ.get("YOUTUBE_CHANNEL_ID", "UC你的頻道ID").strip()
```

**【要改】2，第 34 行附近**

```python
TITLE_KEYWORDS = ["盤中", "家教班"]
```

RSS 會回傳頻道所有近期影片。標題命中任一關鍵字才會被當成當日直播處理。如果張震偶爾發別的影片而標題也含「盤中」，就把條件收緊，例如改成 `["盤中家教班"]`。

**【要改】3，第 38 行附近**

```python
GEMINI_MODEL = "gemini-2.5-flash"
```

Flash 目前仍有免費額度，文字潤飾與資訊擷取通常夠用。想要更強的語意判斷就換成 Pro 系列模型名稱，但 Pro 自 2026 年 4 月起僅供付費使用，換之前先確認金鑰對應的帳號已開通計費。

`daily.yml` 通常不用改。三個地方你可能想動：

- `cron` 三行：目前是台灣時間 11:30 到 13:00 每 10 分鐘。改的時候記得 GitHub 的 cron 走 UTC，台灣時間減 8 小時。例如台灣 11:30 就是 UTC 03:30，寫成 `30 3 * * 1-5`。
- `timeout-minutes: 90`：NotebookLM 索引影片的時間不受我們控制，這個上限給得寬一點比較安全。
- `concurrency.group`：不要拿掉。它保證同一時間只有一個工作在跑，這是防止同一支影片被重複處理的第二道保險（第一道是試算表裡的「處理中」狀態）。

### 7. 第一次執行：回補 5 支歷史影片

儲存庫 → Actions 分頁 → 左側選 `daily-transcript-pipeline` → 右上「Run workflow」→ `backfill` 欄位填 `true` → 執行。

程式會把 RSS 裡所有標題命中關鍵字、且尚未標記為完成的影片，依序處理完。因為不需要下載影音也不跑 CPU 語音辨識，5 支在同一次工作裡跑完沒問題。

跑完後打開試算表檢查：

- 影片清單的處理狀態是否都是「完成」
- 原始逐字稿內容欄位是否有東西，長度是否合理
- 操作紀錄裡的股票名稱是否正確，代號待確認的比例高不高

如果代號待確認比例偏高，回到 `pipeline.py` 的 `EXTRACT_SYSTEM` 提示詞，加一段要求模型更積極比對常見台股名稱。這是規格書 3.2 節建議的品質驗證步驟。

### 8. 之後就自動跑了

排程會在平日 11:30 開始每 10 分鐘檢查一次。行為如下：

- RSS 還沒出現今日影片，且現在還沒到 13:00：印一行 log 就結束，等下一輪
- RSS 還沒出現今日影片，且已過 13:00：在影片清單寫入「今日無影片」
- 找到今日影片但狀態是「處理中」：跳過，避免重複啟動
- 找到今日影片且未處理：標記處理中，取逐字稿，Gemini 處理，寫入試算表，標記完成
- 任一步驟失敗：試算表寫入失敗原因，工作以 exit code 1 結束並顯示紅色，13:45 時 Apps Script 那邊會寄信通知你

### 9. 除錯

| 症狀 | 多半是 |
| --- | --- |
| Actions 紅色，訊息含 403 | 服務帳號沒有被加入試算表的編輯者 |
| 訊息含 auth / storage_state | notebooklm session 失效，重跑本機三行指令並更新 Secret |
| 訊息含 HTTP 429 | Gemini 額度用完，等額度重置或改用其他模型 |
| 逐字稿全文過短或為空 | NotebookLM 對這支影片索引失敗，多半是影片還在轉 VOD，等下一輪 |
| 網站有資料但沒收到信 | 檢查 `installTriggers` 是否執行過，以及每日推播內容那一列的寄送狀態 |

---

## 第三部分：金鑰安全

你在對話裡直接貼出了 Gemini 金鑰字串。那組金鑰現在等於已經公開過。你自己的規格書第八節寫得很清楚：金鑰曾以明碼形式出現在非 Secrets 的地方時，建議重新產生一組新的並讓舊的失效。

具體做法：

1. 前往 Google AI Studio 的 API keys 頁面
2. 找到那組舊金鑰，刪除
3. 建立新金鑰
4. 新金鑰貼到兩個地方：Apps Script 的指令碼屬性 `GEMINI_API_KEY`，以及 GitHub Secrets 的 `GEMINI_API_KEY`

這兩個位置都是後端才讀得到的。本次交付的所有程式碼檔案裡，沒有任何一行寫著金鑰本身，所以就算儲存庫公開、Apps Script 專案被分享出去，金鑰也不會外流。錯誤訊息與 workflow log 也都不會印出金鑰內容。

---

## 附錄：關於 Magic UI

Magic UI 是 React 加 TypeScript 加 Tailwind CSS 加 Motion 的元件庫，要透過 npm 安裝、經過建置流程才能用。Apps Script 的 HTML Service 只吃靜態 HTML、CSS、JS，沒有建置流程，所以無法直接 `npm install magicui`。

本專案的做法是把 Magic UI 的核心視覺語彙以純 CSS 重寫，class 名稱保留原本命名方便對照：

| Magic UI 元件 | 本專案對應 | 用在哪裡 |
| --- | --- | --- |
| Marquee | `.mui-marquee` | 首頁的訊號帶，滑鼠移上去會暫停 |
| BlurFade | `.mui-blur-fade` | 區塊進入視窗時的淡入 |
| NumberTicker | `tick()` 函式 | 三個統計數字的滾動 |
| ShimmerButton | `.mui-shimmer` | 建立訂閱按鈕 |
| DotPattern | `.mui-dot-pattern` | 主視覺背景 |
| BorderBeam | `.mui-border-beam` | 訂閱卡片的邊框流光 |

全部都尊重 `prefers-reduced-motion`，使用者系統設定關閉動畫時會自動停用。

如果你之後真的想用原生 Magic UI，路線是這樣：另外開一個 Vite + React 專案，`npm install` 好 Tailwind、shadcn/ui、Magic UI，本地開發，`npm run build` 產出 `dist/index.html` 加一包 JS 與 CSS，再把建置產物的內容整包貼進 Apps Script 的 HTML 檔案裡，並把資料呼叫從 `fetch` 改成 `google.script.run`。可行，但每次改介面都要重跑一次建置再貼一次，維護成本比現在這版高不少。以這個網站的複雜度來說，不太划算。
