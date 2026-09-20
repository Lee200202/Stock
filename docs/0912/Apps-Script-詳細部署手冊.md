> 最新版本與驗收請看 [v12完整修改與部署流程](../0914-v12/修改與部署流程.md)。目前build為 `2026-09-14-quality-v12`，以下舊build字樣是歷史範例，實際部署請填v12。

> 最新操作請先看 [v6修改與詳細部署流程](../0913-v6/修改與部署流程.md)，包括新增Presentationquality、文章先發布及盤中快取欄位。

> 9/13 已更新至目前 27 檔。這次錯誤的恢復方式請先看 [9/13 修正與部署說明](../0913/修正與部署說明.md)。

# 更新既有 Apps Script 專案：詳細部署手冊

適用版本：`2026-09-13-quality-v6`。本次核對來源為 `C:\Users\user\Downloads\zhangzhen-stock-site-updated\apps-script`。這是更新既有正式專案的流程，不需要另建試算表或重新設定所有訂閱。

## 1. 哪些檔案要放進 Apps Script

**目前該資料夾的 27 個檔案全部需要，放進同一個 Apps Script 專案。** 共 19 個指令碼、8 個 HTML。資料夾中目前沒有測試或備份檔。

| 檔案 | 在編輯器新增時選擇 | 用途 |
| --- | --- | --- |
| Code.gs | 指令碼，名稱 Code | 網站入口與模板載入 |
| Config.gs | 指令碼，名稱 Config | 共用常數及版本，這次新增 |
| API.gs | 指令碼，名稱 API | 公開 API，這次新增 |
| DB.gs | 指令碼，名稱 DB | 訂閱資料存取，這次新增 |
| Logic.gs | 指令碼，名稱 Logic | 刷新步驟與狀態，這次新增 |
| Refreshrunner.gs | 指令碼，名稱 Refreshrunner | 刷新分批續跑，這次新增 |
| Transcriptstore.gs | 指令碼，名稱 Transcriptstore | 9/13 新增：原文版本、指紋、整併與日K續跑 |
| Presentationquality.gs | 指令碼，名稱 Presentationquality | v6新增：公開價位與盤中日K預覽 |
| Setup.gs | 指令碼，名稱 Setup | 設定檢查、共用工作表定義與存取工具 |
| SheetService.gs | 指令碼，名稱 SheetService | 工作表資料服務 |
| Aiservice.gs | 指令碼，名稱 Aiservice | Gemini 模型、金鑰、思考設定與請求 |
| Adminpipeline.gs | 指令碼，名稱 Adminpipeline | 共用 Prompt、名稱及價位工具 |
| Adminservice.gs | 指令碼，名稱 Adminservice | 後台工單、編輯、GitHub 派工 |
| Articlequality.gs | 指令碼，名稱 Articlequality | 文章結構與摘要同步 |
| Evidencequality.gs | 指令碼，名稱 Evidencequality | 證據與逐日同步工作 |
| Cachebuilder.gs | 指令碼，名稱 Cachebuilder | 日K、基本面及績效快取 |
| Quoteservice.gs | 指令碼，名稱 Quoteservice | 行情請求 |
| MailService.gs | 指令碼，名稱 MailService | 郵件生成、查詢與寄送 |
| Cmoney.gs | 指令碼，名稱 Cmoney | 會員簡訊流程 |
| Index.html | HTML，名稱 Index | 前台頁面 |
| Admin.html | HTML，名稱 Admin | 後台頁面 |
| JavaScript.html | HTML，名稱 JavaScript | 前台互動程式，含 script 標籤 |
| Stylesheet.html | HTML，名稱 Stylesheet | 共用 CSS，含 style 標籤 |
| Settings.html | HTML，名稱 Settings | 版面與閱讀設定 |
| Tech.html | HTML，名稱 Tech | 前台技術說明頁 |
| Changelog.html | HTML，名稱 Changelog | 後台更新說明頁 |
| Unsubscribed.html | HTML，名稱 Unsubscribed | 取消訂閱結果頁 |

HTML 檔名大小寫照表保留。`JavaScript.html` 不是 `.gs`；`Stylesheet.html` 也不是另建 `.css`。新增時輸入名稱本體，讓編輯器加副檔名。已存在同名檔案就更新內容，不要再新增同名副本。

`Tech`、`Changelog`、`Unsubscribed` 都有實際引用，不是可省略的附件。`Setup.gs` 也包含其他服務使用的共用定義，不能因「設定做過了」就移除。

不放進 Apps Script 的項目：根目錄／pipeline 內的 Python、requirements.txt、`.github`、scripts、tests、docs、backups、release。它們分別是 GitHub 執行程式、本機工具、文件或交付包。

若雲端專案已有隱藏的 `appsscript.json`，保留原本設定；本機資料夾沒有這個檔案，不表示要刪掉雲端 manifest。

## 2. 開啟正確的既有專案

1. 進入目前網站所使用的 Apps Script 專案。
2. 確认「專案設定 → 指令碼屬性」中的 `SPREADSHEET_ID` 是原本資料庫。
3. 記下現有部署版本號與 `/exec` 網址，方便驗證或切回。
4. 選在沒有工單進行時更新。編輯器執行與排程觸發器使用目前儲存的程式碼，不要在檔案只貼到一半時手動跑工作。

若目前專案是從試算表開啟，也可由那份試算表「擴充功能 → Apps Script」進入；若是獨立專案，直接打開原專案即可。不要為本次更新另建一個空白資料庫。

## 3. 替換檔案內容

1. 先在雲端新增缺少的指令碼：Config、API、DB、Logic、Refreshrunner，以及 9/13 新增的 Transcriptstore。
2. 用本機 UTF-8 編輯器開啟對應檔案，複製全部內容。
3. 在 Apps Script 選到對應檔案，Ctrl+A 全選舊內容，再貼上新內容。
4. 依序更新其餘 `.gs` 與 `.html`，共核對 26 個。
5. 按儲存，等編輯器顯示已儲存，確認沒有語法錯誤。

新的 Code.gs 是完整替換，不是追加在舊 Code.gs 後面。舊函式已移到其他檔案，不應把旧 Code.gs 另存成 `Code_backup.gs` 留在同一個執行專案，否則仍會載入重複函式。備份請留在本機或原部署版本。

這 27 個檔案共同構成一個程式，**最後只部署一次網頁應用程式，不是每個檔案各部署一次**。

## 4. 檢查 GAS 專案設定與指令碼屬性

專案時區使用 `Asia/Taipei`（台北／GMT+08:00），保留 V8 執行環境。到「專案設定 → 指令碼屬性」核對下表，既有正確的值不用重設。

| 屬性 | 這次怎麼處理 |
| --- | --- |
| SPREADSHEET_ID | 保留原本試算表 ID；應與 GitHub Secret 相同 |
| ADMIN_KEY | 保留原管理密鑰；應與 GitHub Secret 相同 |
| GEMINI_API_KEY | 第一把 Gemini 金鑰，保留 |
| GEMINI_API_KEY_2、GEMINI_API_KEY_3 | 有第二、第三把就保留 |
| GEMINI_API_KEY_4、GEMINI_API_KEY_5 | 沒有就不必新增 |
| GEMINI_API_KEYS | 可選的逗號分隔金鑰池；已用分開欄位就不必再填 |
| GEMINI_THINKING_LEVEL | 設為 `medium`；要比較 high 時再改為 `high` |
| FUGLE_API_KEY | 有 Fugle 行情服務就保留，日K與行情功能會用到 |
| OWNER_EMAIL | 原本管理者通知信箱 |
| GITHUB_REPO | 原儲存庫，格式 `擁有者/儲存庫` |
| GITHUB_TOKEN | 原本讓 GAS 派送 GitHub Actions 的權杖 |
| GITHUB_WORKFLOW | 目前工作流程檔案名稱 `daily.yml`，未設定時程式也以此為預設 |
| GITHUB_REF | 實際使用的分支，例如 `main`；需與更新的分支一致 |

其他 Cmoney、會員帳號、配額、快取與工作游標屬性也照原樣保留，不要把整張屬性表清空後只重填上面幾項。

**更正先前文件中容易誤解的地方：**

- GAS 模型由 `Aiservice.gs` 的 `DEFAULT_MODEL = 'gemini-3.5-flash-lite'` 提供，呼叫時也可由 opt.model 指定。目前沒有讀取 `GEMINI_MODEL` 指令碼屬性來選預設模型。
- `GEMINI_MODEL`、`GEMINI_MODEL_2` 等是 GitHub／Python 的環境變數。
- `GEMINI_THINKING_LEVEL` 兩端都有讀取，需各自設定。
- `GEMINI_SEMANTIC_AUDIT` 是 GitHub／Python 的設定，不必新增到 GAS 指令碼屬性。

指令碼屬性是這個 GAS 專案的設定，GitHub Secrets／Variables 是另一套設定，兩者不會自動同步。[Google 屬性服務說明](https://developers.google.com/apps-script/guides/properties)

## 5. 先跑設定檢查

1. 回到程式碼編輯器。
2. 上方函式選單選 `checkSetup`。
3. 按「執行」；若需要授權，核對是自己的專案與帳號，再完成 Google 授權。
4. 查看執行記錄，確認試算表、金鑰、通知信箱、觸發器及快取筆數。

`checkSetup` 是讀取設定與資料筆數，不會呼叫 Gemini，也不寄信。但它不是完整的 Gemini、GitHub 或行情連線測試。

如果只顯示缺少分頁，可以執行 `addMissingSheets`，它只補不存在的分頁，不清空已存在的資料。之後再跑一次 `checkSetup`。

本次是更新既有系統，**不需要執行 `setupSpreadsheet`**。也不要先刪 `SPREADSHEET_ID`；該值不存在時，這個函式會建立新資料庫。

不要以手動執行 `dailyPushJob` 作為部署測試，它可能寄出待寄送郵件。先用網站的郵件查詢檢查內容。

## 6. 核對觸發器，正常就保留

左側點時鐘圖示「觸發條件」。本次拆檔沒有改原觸發函式名稱，已有正常的觸發器不必重装。

目前 `installTriggers` 的固定排程為：

| 函式 | 排程 |
| --- | --- |
| everyFiveMinJob | 每 5 分鐘，內部分派例行工作 |
| cmoneyPollJob | 每 1 分鐘，內部自行檢查執行時段 |
| aggregateHourlyJob | 每日約 14:05 |
| repairCodesJob | 每日約 14:20 |
| rebuildFundamentalsJob | 每日約 14:30 |
| backfillDailyKJob | 每日約 14:35，保留作為日K備援 |
| rebuildHoldingsTrackerJob | 每日約 14:50 |
| snapshotPerformanceJob | 每日約 15:05 |
| rebuildCodeMapJob | 每週日約 04:00 |
| yearlyArchiveJob | 每月 5 日約 03:00；函式內再判斷年度封存需求 |

表內時間以專案時區為準，時間型觸發器並非保證精確到分鐘。執行中的分批工作可能另有一次性觸發器，因此總數不一定恰好 10。

`installTriggers` 一開始會刪除它列出的現有專案觸發器，再建固定排程；不是單純「補缺一個」。因此已有正確排程就保留；只有全新專案或確定要重建排程，且沒有進行中工作時才用它。若只少 14:35 備援，可由介面新增 `backfillDailyKJob` 的時間驅動觸發器，避免整批重裝。

## 7. 發布到既有部署，沿用網址

1. 右上角「部署 → 管理部署作業」。
2. 左側選取目前網站使用的「網頁應用程式」部署。
3. 點鉛筆「編輯」。
4. 「版本」選擇「新版本」。
5. 說明填 `2026-09-13-quality-v6`。
6. 核對執行身分為你自己（部署者）。
7. 本專案需讓 GitHub 在未登入 Google 的情況呼叫端點，因此存取權限應為「所有人」，不是「只有自己」或要求 Google 登入的選項。若帳號／組織政策沒有這個選項，需先處理部署權限，不能單靠重貼程式碼解決。
8. 按「部署」，完成可能出現的授權。
9. 複製或核對 `/exec` 結尾的網頁應用程式網址。

更新同一個部署的版本可沿用原網址；不需要每次「新增部署」取得另一條網址。僅儲存程式碼，既有版本化網站不會因此自動更新。[Google 部署版本說明](https://developers.google.com/apps-script/concepts/deployments)

如果這是第一次建立網頁應用程式，才走「部署 → 新增部署作業 → 網頁應用程式」，套用上述執行身分與存取設定。[Google 網頁應用程式說明](https://developers.google.com/apps-script/guides/web)

## 8. 先確認 ping，再看網站

把 `/exec` 網址後面加上 `?action=ping`。這一步不需要 ADMIN_KEY，也不寫入試算表或寄信。

```text
你的網頁應用程式網址/exec?action=ping
```

回應裡應包含下列欄位（還會有時間、身分等其他欄位）：

```json
{
  "ok": true,
  "pong": true,
  "build": "2026-09-13-quality-v6"
}
```

`features` 新增 `transcript-job-hash`、`auto-dailyk-resume`、`transcript-merge-preview`，並應含 `context-policy-v4`、`refresh-all-resumable`、`perf-history-pending`。

接著開啟正常 `/exec` 網址，確認前台、會員持股／追蹤專區、郵件查詢、深淺色及閱讀設定。後台網址是同一條 `/exec?page=admin`，沿用原 ADMIN_KEY 登入。

若 ping 仍顯示 v3：先核對網址是否為剛更新的部署，再確認「版本」確實選了新版本。若回 Google 登入頁：檢查存取權限。不要把 `/dev` 測試網址填進 GitHub；它只供專案編輯者測試。[Google 測試部署說明](https://developers.google.com/apps-script/guides/web#test_a_web_app_deployment)

## 9. 同步更新 GitHub 端

更新 GAS 不會更新 GitHub，反過來提交 GitHub 裡的 apps-script 資料夾，也不會自動發布到 GAS。此專案目前需要分別更新兩端。

將同一批版本的這些執行檔更新至 `GITHUB_REF` 指定的分支：

```text
pipeline.py
pipeline/pipeline.py
.github/workflows/daily.yml
scripts/sync_quality.py
```

其他原始碼、測試與文件可以跟同一批提交保存。保留原 requirements.txt 及 pipeline/requirements.txt。不要把本機 backups 或測試瀏覽器工具當成執行程式上傳。

兩份 pipeline.py 必須一致；本機需要重新同步時，在專案根目錄執行 `python scripts/sync_quality.py`，再提交兩份檔案。不要只更新其中一份。

GitHub「Settings → Secrets and variables → Actions」：

**Secrets（機密）**

| 名稱 | 核對內容 |
| --- | --- |
| APPS_SCRIPT_URL | 正式 `/exec` 網址，不含 `?action=...` |
| ADMIN_KEY | 與 GAS 相同 |
| SPREADSHEET_ID | 與 GAS 相同 |
| GOOGLE_SHEETS_SERVICE_ACCOUNT | 保留原服務帳號 JSON 與原試算表共用權限 |
| GEMINI_API_KEY、GEMINI_API_KEY_2、GEMINI_API_KEY_3 | 保留原金鑰，不放 Variables |

**Variables（非機密）**

| 名稱 | 建議值 |
| --- | --- |
| GEMINI_MODEL | gemini-3.5-flash-lite |
| GEMINI_THINKING_LEVEL | medium |
| GEMINI_SEMANTIC_AUDIT | true |
| GEMINI_COMBINED_ASSESSMENT | true |
| GEMINI_ARTICLE_ENABLED | false |
| GEMINI_ASSESSMENT_TOKEN_BUDGET | 120000 |
| GEMINI_CONTEXT_TOKENS | 1048576 |

這些 Variables 多數已有 workflow 預設值，明確設定較易核對。`GEMINI_ARTICLE_ENABLED=false` 表示不另叫模型撰稿，不是停止產生文章或寄信。

檢查 `GEMINI_MODEL_2` 至 `_5` 是否有舊模型覆寫。若希望每把都跟主模型一致，移除不需要的覆寫或改成相同模型；其他既有頻道、NotebookLM、Cmoney 設定繼續保留。

若更新同一個 GAS 部署且網址未變，`APPS_SCRIPT_URL` 不必改。如果新建了部署換了網址，才更新這個 Secret。

## 10. 依序驗證工作流

1. GitHub「Actions」選 `daily-transcript-pipeline`。
2. 選「Run workflow」，分支選剛更新且與 `GITHUB_REF` 一致的分支。
3. 僅將 `check_keys` 設 `true`，其他模式維持 `false` 或原預設；送出。
4. 核對每把實際使用的模型與健檢結果。這個檢查會真實生成短回覆，消耗少量 Gemini 配額；逾時表示待重試，不等於已驗證可用。
5. 如果 `checkSetup` 顯示日K空，先在 GAS 編輯器執行 `backfillDailyKJob`，或等既有 13:45 GitHub 日K排程／14:35 GAS 備援補資料。一次未補完可續補，不需要先重算全部歷史。
6. 有日K後，回後台接續未完成工單／指定日期刷新。缺資料時應顯示待補原因，不會直接顯示全成功。
7. 若要驗證新的股票分類，從後台選定 9/10、9/11 的原始逐字稿重新判讀，先各跑一日；**只刷新網站不會重新擷取股票**。
8. 看 Actions 日誌應出現 `context-review`；文章生成應使用最終 JSON，不另叫模型撰稿。
9. 看郵件查詢的會員持股、觀望表、摘要與版面。單純重新部署不會把舊文章改寫成較長的新文章，需重新判讀／產生該日內容。

目前 workflow **沒有手動的 `dailyk_only` 輸入欄**。13:45 排程會自動進入只補日K模式；不要在 Run workflow 尋找不存在的按鈕。手動 `refresh_site=true`、`refresh_from=dailyk` 是「從日K開始刷新，後面還會做追蹤等步驟」，不等於只補日K。

已寄出的信件無法改寫。部署驗證以郵件查詢內容為主，保留防重寄與既有寄送狀態；待寄送日期仍可能由正常排程寄出。

## 11. 常見問題對照

| 現象 | 先檢查 |
| --- | --- |
| APP_TITLE／GAS_BUILD／REFRESH_STEPS_ 未定義 | 是否漏 Config.gs 或 Logic.gs |
| apiRefreshStep_／jsonOut_ 未定義 | 是否漏 API.gs |
| writeSubscriptionFields_ 未定義 | 是否漏 DB.gs |
| runRefreshAllChunk_ 未定義 | 是否漏 Refreshrunner.gs |
| 找不到 HTML 檔案 | 新增類型、名稱大小寫、是否誤加雙重副檔名 |
| 程式有新字樣、網站仍舊版 | 是否建立新版本並更新正確部署 |
| GitHub 收到登入 HTML 而非 JSON | APPS_SCRIPT_URL 是否為正式 /exec，以及部署是否允許未登入存取 |
| 管理密鑰不正確 | GAS 與 GitHub 的 ADMIN_KEY 是否一致 |
| 後台未觸發 Actions | GITHUB_REPO、TOKEN、WORKFLOW、REF，以及權杖原有權限 |
| 績效歷史待補資料 | 日K與交易日曆是否已有資料；先補再續跑 |
| 文章長度沒變 | 是否只部署程式，尚未重跑該日判讀 |
| 網站已新、寄過的信仍舊 | 已寄信不能回頭修改；查看網站的指定日期郵件內容 |

若需回退網站，可在同一部署的版本選單切回先前版本；這只回退網頁部署。觸發器與編輯器仍使用當下儲存原始碼，且已寫入資料不會自動還原。這兩者要分別處理。[Google 部署種類說明](https://developers.google.com/apps-script/concepts/deployments)
