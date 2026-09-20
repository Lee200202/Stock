# 階段一：獨立測試環境 Step-by-Step 部署手冊

日期：2026/09/12。依本機原始碼製作；尚未連線驗證你的 Google、GitHub 帳號、遠端分支、正式試算表或部署內容。

本輪先交付部署概念、可執行的本機產生器與防護範本。沒有執行遠端建立、push、部署、資料匯入或寄信。0910.txt、0911.txt 已收到，階段二可直接使用，不必重傳。

## 1. 先看懂要建立什麼

```text
現有本機資料夾                         正式環境（維持原設定）
zhangzhen-stock-site-updated            Lee200202/Stock 的正式分支
                                       正式 GAS → 正式 Sheets → 正式 Web App

新本機資料夾                           測試環境
zhangzhen-stock-site-test               Lee200202/Stock / codex/test
  apps-script/  ──clasp push──────────→ 新 GAS → 新 Sheets → 新 /exec 網址
  pipeline.py   ──GitHub Actions──────→ 僅有測試表權限的新服務帳號
```

採用分支而非在正式版底下新增 test/：現有 Python 工作流程假設 pipeline.py 在根目錄，GAS 的 HTML 也有固定檔名。分支能保留這些路徑。單純建立分支不會自動隔離憑證、資料庫、排程或派工目標，因此以下設定必須一起完成。

| 項目 | 正式 | 測試 |
|---|---|---|
| 本機資料夾 | zhangzhen-stock-site-updated | zhangzhen-stock-site-test，同層獨立 clone |
| Git 分支 | 沿用現有正式分支 | codex/test |
| Google Sheets | 現有 ID | 全新實體檔、新 ID |
| GAS | 現有 Script ID | 全新專案、新 Script ID |
| Web App | 現有 /exec | 新專案首次部署得到的新 /exec |
| Actions 設定 | 既有名稱 | 僅引用 TEST_ 前綴 Secrets / Variables |
| GAS 派工 | 既有設定 | GITHUB_REF=codex/test，GITHUB_WORKFLOW=daily.yml |
| 服務帳號 | 既有帳號 | 新帳號，只共用測試表給它 |
| 排程 | 既有排程 | 初期全部手動，移除測試 workflow 的 schedule |
| 郵件 | 原收件者 | 預設阻擋；選擇啟用後，全部導向 TEST_EMAIL |

獨立 GAS／Sheet 不等於獨立配額：同一 Google 使用者或同一 API 專案仍可能共用配額。大量回放請用專屬測試 Google 帳號與獨立 API 專案，並限制測試頻率。[Google 配額文件](https://developers.google.com/apps-script/guides/services/quotas)

## 2. 先記錄正式環境識別資料

建立一份只留在本機的記錄，抄下正式 Sheet ID、Script ID、Web App URL、GitHub 正式分支與目前部署版本。ID 是識別碼，API key、服務帳號 private_key、ADMIN_KEY 是秘密，兩者不要混放到版本庫。

本機檢查發現：原資料夾沒有自己的 .git，git rev-parse --show-toplevel 回到 C:/Users/user；origin 是 Lee200202/leehsun.github.io，並非 Stock。**不要在原資料夾直接 git add . 或 git push。**產生器會另行 clone Stock，得到自己的 .git。

執行產生器前，先有 Git、Python 3.11 以上、Node.js 與 npm；確認 PowerShell 能執行 git --version、python --version、node --version。私有儲存庫須先用 Git Credential Manager 或 GitHub CLI 完成自己的登入。

## 3. 產生完全獨立的本機測試資料夾

開 PowerShell 執行：

```powershell
& 'C:\Users\user\Downloads\zhangzhen-stock-site-updated\docs\phase1\New-TestWorkspace.ps1'
```

若 Windows 的腳本政策阻擋，檢閱檔案後可只對本次程序執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Users\user\Downloads\zhangzhen-stock-site-updated\docs\phase1\New-TestWorkspace.ps1'
```

產生器會：clone Lee200202/Stock、建立本機 codex/test、覆蓋本機這批程式碼、停用 clone 中繼承的工作流程，再建立測試版 daily.yml。它拒絕覆蓋已存在的目標，也不複製本機備份、登入狀態或服務帳號金鑰。

Python 入口優先使用來源根目錄 pipeline.py，若不存在則取 pipeline/pipeline.py，並在新副本產生兩份相同內容。來源若同時有兩份而內容不同，產生器會拒絕執行，避免默默選錯版本。

它只在新副本加入：Sheet／Script ID 檢查、GitHub 派工檢查、[TEST] 網站標題、郵件攔截，以及阻擋 installTriggers()。目前找到的 11 處 MailApp.sendEmail 均改接 testSendEmail_；原資料夾內的應用程式保持不變。

如果 clone 失敗，原檔不變，可能留下部分新目錄；先檢查錯誤並選新 Destination，不要對不明路徑做清除。遠端若已有 codex/test，也先確認是否已有測試工作，初次流程不要強制推送覆蓋它。

```text
zhangzhen-stock-site-test/
  .git/                         Stock 的獨立 Git 歷史
  .github/workflows/daily.yml    只有 workflow_dispatch；使用 TEST_ 設定
  .github/workflows/*.disabled   clone 原 workflow 留存，不執行
  apps-script/                  全部既有 .gs 與 .html
    TestEnvironment.gs          實際接入的測試防護
    appsscript.json             Asia/Taipei、V8
  pipeline.py                   主要 Python 入口
  pipeline/pipeline.py           既有同步副本，兩者需相同
  scripts/、tests/
  .clasp.json.example           新 Script ID 範本
  .claspignore
  credentials/                  不提交 Git
  fixtures/private/             私有逐字稿與驗證資料，不提交 Git
  artifacts/                    執行結果，不提交 Git
  docs/phase1/DEPLOY_TEST.md
```

驗證本機來源：

```powershell
Set-Location 'C:\Users\user\Downloads\zhangzhen-stock-site-test'
git rev-parse --show-toplevel
git remote -v
git branch --show-current
git diff --stat
git diff -- .github/workflows/daily.yml apps-script/Setup.gs apps-script/Code.gs
```

應分別看到新資料夾、Lee200202/Stock、codex/test。clone 可能比你 Downloads 的快照新；請檢查差異，避免把遠端較新的功能意外回退。來源檔標記改變時，產生器會停止，留下可檢查的副本。

## 4. 新建 GAS 專案與全新資料庫

1. 打開 Apps Script 首頁，按「新專案」，命名「張震股市 TEST」。推薦用專屬測試帳號。
2. 專案設定中找到「指令碼 ID」，記成 TEST_SCRIPT_ID。這不是 Sheets ID，也不是部署 ID。
3. 不複製正式 .clasp.json，不整包匯入正式 Script Properties。
4. 本機安裝 clasp 並登入測試帳號，啟用 Apps Script API。clasp 同步的是程式，指令碼屬性仍需在雲端另設。

```powershell
npm install -g @google/clasp
clasp --version
clasp login
Copy-Item -LiteralPath '.clasp.json.example' -Destination '.clasp.json'
notepad.exe .clasp.json
```

把占位 Script ID 換成剛建立的新 ID，rootDir 維持 apps-script。然後執行：

```powershell
clasp show-file-status
clasp push
```

show-file-status 應只包含 apps-script 的 .gs、.html 與 appsscript.json；若清單空白就先修正設定。clasp push 會替換目標專案內容，所以每次先核對 .clasp.json。[clasp 官方儲存庫說明](https://github.com/google/clasp/blob/master/README.md)

5. 回雲端編輯器重新整理。這批原始碼目前未使用 manifest 的進階服務依賴；產生器提供基本 manifest。若之後新增進階服務或明列 scopes，須同步維護。
6. 在新 GAS 手動執行 setupSpreadsheet()，完成自己的 Google 授權。它會新建資料庫、建立 SHEET_SCHEMA 定義的分頁，並自動設定 SPREADSHEET_ID。把新表改名「張震股市 TEST 資料庫」。
7. 記下新 Sheet ID。這個函式遇到已有 SPREADSHEET_ID 會直接返回，不會替既有表補 schema，因此初次請保持該屬性空白，讓它建立新表。
8. 先不執行 installTriggers()。測試副本已阻擋它，避免一口氣安裝推播、會員簡訊與封存排程。

## 5. 設定新 GAS 的指令碼屬性

到新專案「專案設定 → 指令碼屬性」，逐一新增：

| 屬性 | 值 |
|---|---|
| APP_ENV | test |
| SPREADSHEET_ID | setupSpreadsheet 已自動寫入的新 Sheet ID |
| TEST_SPREADSHEET_ID | 同一個新 Sheet ID，用於獨立核對 |
| PRODUCTION_SPREADSHEET_ID | 正式 Sheet ID，只供比對拒絕用途 |
| TEST_SCRIPT_ID | 新 GAS 的 Script ID |
| GITHUB_REPO | Lee200202/Stock |
| GITHUB_REF | codex/test |
| GITHUB_WORKFLOW | daily.yml |
| GITHUB_TOKEN | 可對 Stock 操作 Actions 的測試用細粒度 token；後台派工才需要 |
| ADMIN_KEY | 新產生的測試密鑰，不能沿用正式密鑰 |
| GEMINI_API_KEY | 測試 API key；其他 key 視需求另設 |
| TEST_MAIL_ENABLED | false |
| TEST_EMAIL | 你自己的測試信箱，單一地址 |
| OWNER_EMAIL | 你自己的測試信箱 |
| STATUS_EMAILS | 你自己的測試信箱 |
| YOUTUBE_CHANNEL_ID | 既有頻道 ID，可共用公開來源 |
| FUGLE_API_KEY | 需要上櫃行情測試時才設測試 key |

checkTestEnvironment() 會驗證環境及派工設定、讀取測試表，輸出非秘密的識別資料。初期 installedTriggers 應為 0；若你已經送出後台工單，續跑用的一次性觸發器可能存在，需逐筆核對用途。

防護不是任意腳本的沙箱：後續新增直接 SpreadsheetApp.openById、GmailApp 或外部郵件 API，仍須接入相同防護。專屬測試帳號沒有正式表編輯權限，是比 ID 字串核對更強的隔離。

## 6. 安全匯入正式歷史資料

先取固定快照，避免在正式上游正在寫入時跨分頁複製出不一致的資料。可選正式流水線閒置的時段，記錄匯入時間、各表資料列數、最後來源影片 ID；不必停掉既有排程。若來源在複製期間變動，捨棄該次快照並重取，不宣稱 Google Sheets 跨表複製具有交易一致性。

**推薦只匯入白名單資料表，不複製整份正式試算表的腳本、訂阅人或工單。**

| 分類 | 工作表與處理 |
|---|---|
| 必要歷史來源 | 影片清單、操作紀錄、會員持股、每日推播內容 |
| 保留判讀證據 | 逐字稿判讀稽核、判定歷程（若存在）、人工補登；依實際歷史功能需要帶入 |
| 歷史行情與查碼 | 日K快取、小時K、股票對照表、代號對照快取（若存在）、基本面快取 |
| 績效與衍生結果 | 每日績效可保留作對照；持股追蹤匯入後重算；即時快取另抓新資料 |
| 有會員簡訊需求才匯入 | 會員簡訊、會員簡訊稽核；內容僅留在受限測試表 |
| 保持空白 | 使用者訂閱清單、使用者上下文記憶、使用紀錄、後台工單、今日影片候選、系統狀態、修正建議、盤中快照 |
| 年度封存 | 若歷史曾封存，連同資料索引及其列出的封存分頁一起匯入，不能只複製索引 |

每張表依序操作：

1. 在正式表的分頁選單選「複製到 → 現有試算表」，目標挑剛建立的新測試表。這是讀來源、寫目標。
2. 進入**測試表**，核對瀏覽器 URL 的 Sheet ID。
3. 把 setupSpreadsheet 建立的同名空白分頁改名為「schema備查_原名」，將複製來的分頁改成程式預期的完全相同名稱。
4. 對照備查表頭，保留來源原欄位並補齊缺少的新欄位；不要任意改動中文表頭。
5. 若有公式連到正式表，在測試副本轉為純值；測試歷史應是固定快照，避免 IMPORTRANGE 持續讀正式表。保留代號的字串格式、日期及原始逐字稿。
6. 核對來源與目標的資料列數、表頭、最早／最晚日期、首尾影片 ID；抽查原始逐字稿長度與文字。資料量大時分表操作，每完成一表就記錄，失敗只重做該表。
7. 「每日推播內容」保留原文與原寄送狀態，另記這是歷史快照。不要把全部「已寄送」改為「待寄送」。測試新文章的寄送狀態只在測試表使用。

已有 0910.txt 與 0911.txt，可放到新本機 fixtures/private，供階段二回放；不要提交登入 cookie、服務帳號 JSON、會員訂閱資料或非公開逐字稿到 Git。

## 7. 建立測試專用服務帳號

1. 在 Google Cloud 建立測試專案或使用專用測試 Cloud 專案，啟用 Google Sheets API 與 Google Drive API。
2. 建立服務帳號，例如 stock-test-writer；不需要給它整個 Cloud 專案的 Owner 角色。
3. 為目前程式需要的 JSON 認證產生金鑰，保存在 credentials/，不要提交 Git。
4. 打開**測試試算表**「共用」，把 JSON 的 client_email 加為編輯者。
5. 不把該帳號加到正式試算表，也不要加入具有正式檔案繼承存取權的共享雲端硬碟。

Python pipeline 使用此帳號寫表；GAS Web App 則以部署執行者身分存取資料。這是兩套身分，不能只隔離其中一套。

## 8. 發佈新測試 Web App

1. 確認 checkTestEnvironment() 成功。
2. 新 GAS 右上角「部署 → 新增部署作業 → 網頁應用程式」。
3. 描述填「TEST 初次部署」，執行身分選你自己（最好是專用測試帳號）。
4. 首先可限定自己存取來驗證頁面。現有 GitHub Python 回呼沒有 Google 登入流程；要沿用它做端到端測試，需將存取設定為「所有人」，並使用測試 ADMIN_KEY。此設定會讓網站讀取頁面可被公開存取，ADMIN_KEY 只保護相應管理操作；公開前只保留適合公開的資料。
5. Workspace 政策若不允許匿名存取，先使用私有頁面＋GAS 編輯器手動刷新；完整自動回呼需另做已驗證的認證方案，不能把登入頁當成成功回應。
6. 記下新的 /exec URL。瀏覽器打開 /exec?action=ping，檢查 scriptId 是 TEST_SCRIPT_ID，再開網站確認標題 [TEST]。

首次部署與更新版本的差別、執行者和存取權限請以 [Google Web Apps 文件](https://developers.google.com/apps-script/guides/web) 為準。/dev 用於編輯者開發驗證，Actions 設定填 /exec。

## 9. GitHub 設定：只使用 TEST_ 名稱

在 Lee200202/Stock → Settings → Environments 建立 test，允許部署分支限定 codex/test。依帳號方案，私有庫的 Environment 功能可能受限；無法使用時可把 TEST_ 設定放 Repository Secrets／Variables，移除生成 workflow 的 environment: test，保留分支與 ID 防護。[GitHub Environment 說明](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)

在 test Environment 新增 Secrets：

| 名稱 | 填入 |
|---|---|
| TEST_GOOGLE_SHEETS_SERVICE_ACCOUNT | 新服務帳號 JSON 全文 |
| TEST_SPREADSHEET_ID | 新 Sheet ID |
| TEST_APPS_SCRIPT_URL | 新 /exec URL |
| TEST_ADMIN_KEY | 與新 GAS 的 ADMIN_KEY 相同 |
| TEST_GEMINI_API_KEY | 測試 Gemini key |
| TEST_YOUTUBE_API_KEY | 測試 YouTube key |
| TEST_GEMINI_API_KEY_2 等 | 多 key 才加；名稱依生成 workflow |

Variables：

| 名稱 | 填入 |
|---|---|
| TEST_EXPECTED_SPREADSHEET_ID | 新 Sheet ID，獨立核對欄位 |
| TEST_PRODUCTION_SPREADSHEET_ID | 正式 Sheet ID，只用來拒絕寫入 |
| TEST_SERVICE_ACCOUNT_EMAIL | 新服務帳號 client_email |
| TEST_EXPECTED_APPS_SCRIPT_URL | 新 /exec URL，人工核對其 ping 回傳的新 Script ID |
| TEST_YOUTUBE_CHANNEL_ID | UCPqyYS3n6yyXL2jygauXpzg |
| TEST_GEMINI_MODEL | 與待比較正式版一致的可用模型名稱 |
| TEST_GEMINI_THINKING_LEVEL | 與正式版一致，避免把推理設定差异誤當 Prompt 成效 |
| TEST_AUTO_REFRESH | true，端到端測試時啟用 |

其他 TEST_ Variables 對照生成 workflow，未設者只採用其程式預設值，不讀無前綴正式設定。初期不啟用會員簡訊抓取。

不要只依賴 Environment 裡「相同名稱」的 Secret 覆蓋正式 Secret：同名設定存在優先次序，漏設時可能仍取得其他層的值。使用 TEST_ 名稱並做必填驗證，更容易辨認錯綁。[GitHub Secrets 規則](https://docs.github.com/en/actions/reference/security/secrets)

## 10. 同步測試分支並啟動一次流程

先檢視要提交的檔案，尤其 clone 原本已追蹤的檔案不會因新增 .gitignore 自動停止追蹤。確認沒有憑證後，在**新資料夾**執行：

```powershell
git status --short
git add -- apps-script pipeline.py pipeline requirements.txt scripts tests .github .gitignore .claspignore .clasp.json.example docs/phase1
git diff --cached --stat
git diff --cached --check
git commit -m "Add isolated stock test environment"
git push -u origin codex/test
```

這些是你準備好後執行的指令；本次交付尚未執行 push。不要使用 --force，也不合併 main。

GitHub Actions → 選 daily.yml 對應的 workflow → Run workflow → 選 codex/test。workflow_dispatch 的定義需要存在預設分支才能被手動觸發；本機現有 daily.yml 已包含此事件，但遠端尚未驗證。若遠端缺少，需單獨補上預設分支的手動入口，不能宣稱只推 test 分支一定有按鈕。測試分支仍沿用 daily.yml 的檔案路徑，所以 GAS 的 GITHUB_WORKFLOW 也是 daily.yml。[GitHub 手動觸發規則](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)

第一次建議用 repair_codes=true，其他模式關閉，確認測試設定檢查通過、只讀寫新表。之後从測試後台貼入一天逐字稿，讓新 GAS 建立測試工單並派送 codex/test；沿用原日期與 MANUAL 來源規則，不在正式後台操作。

本產生器保留既有處理模式與工作流程檢查。根目錄 pipeline.py 與 pipeline/pipeline.py 必須一致；遇到原有回歸測試失敗，記錄基線問題，先不要把失敗當成新 Prompt 的效果。

測試 workflow 的 concurrency 改成 stock-test-${{ github.ref }}，避免與正式 daily-pipeline 共用佇列。只接受 codex/test 的手動觸發；schedule 已移除，並不會每天自動抓資料。

## 11. 歷史驗證與未來新資料如何進入

1. **固定回放**：0910、0911 原始逐字稿分別走測試工單。保存原文 SHA256、程式 commit、Prompt 版本、模型與推理設定、判讀 JSON、逐筆證據、最終文章、欄位數及來源 ID。
2. **避免重新抓歷史**：匯入影片清單時保留既有已完成狀態；只針對指定日期回放。不要把所有歷史列改成待處理。
3. **新資料**：同一公開 YouTube 頻道可供兩套流程各自讀取；TEST Actions 使用測試表與測試憑證，是否已處理由測試影片清單判斷。表格欄位相同，不代表資料或狀態共用。
4. **最初手動**：每日收盤後手動執行 codex/test；首次使用只選需要的模式。貼逐字稿回放不需建立另一條正式信件輸入鏈。
5. **下游分段**：先修代號、再補日K、重算持股、再記績效。若日K快取空白，重算績效歷史的「略過」不算驗收完成。待日K就緒後補跑並檢查結果。
6. **時間限制**：不要讓一個 GAS 函式一次跑完全部歷史。每表／每日期記錄進度，接近時間上限時續跑；初期用手動分批與既有續跑機制。Script 單次執行目前一般上限 6 分鐘。[Google 配額](https://developers.google.com/apps-script/guides/services/quotas)
7. **之後才加排程**：GitHub schedule 從預設分支執行，不能只在 codex/test 填 cron 就當作每日自動跑。若需要正式測試排程，另在預設分支加獨立 test-scheduled.yml，明確 checkout codex/test、test Environment、TEST_ 憑證及獨立 concurrency。這是另一次可檢閱的流程變更，本輪沒有加入。[GitHub 事件規則](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)

不要直接恢復 installTriggers()：它會安裝多項正式用途排程。需要測試定時報價時，在新 GAS 的觸發器 UI 只新增指定函式，先限定一項並確認其資料與寄信途徑。

## 12. 郵件、部署與回復驗收

預設 TEST_MAIL_ENABLED=false 時，寄信會明確失敗並留下測試錯誤，不能將其記為郵件測試成功。需要實際寄一封給自己時，在測試 GAS 設 true，TEST_EMAIL 填單一自己的信箱，測試訂閱清單也只加入自己；所有被攔截的信件都改成 [TEST] 主旨且移除 cc/bcc/replyTo。

這一步只有你開始郵件驗收時才開。本次工作沒有寄送任何信件。

| 驗收項 | 通過條件 |
|---|---|
| 本機 | Git root 在新目錄；origin 是 Stock；分支 codex/test |
| 雲端 | 新 Script ID、新 Sheet ID、新部署 URL 都與正式不同 |
| 權限 | 測試服務帳號沒有正式表編輯權限 |
| 防護 | 故意把測試副本 SPREADSHEET_ID 改成正式 ID，checkTestEnvironment 立即拒絕；測完還原 |
| 派工 | 新後台的 Actions run 確實使用 codex/test；缺 GITHUB_REF 時拒絕 |
| 歷史 | 來源快照的表頭、列數、日期、影片 ID 與測試匯入一致 |
| 新資料 | 單日工單新增至測試表，正式表未新增對應測試紀錄 |
| 下游 | 同日文章、持股追蹤、日K與績效均有實際完成證據，略過需另記 |
| 郵件 | 關閉時明確阻擋；另行啟用的單封測試僅到自己的信箱 |
| 排程 | 無測試 cron；沒有意外安裝正式推播觸發器 |

日後改 GAS：在 codex/test 修改 → 本機檢查 → clasp push → GAS「管理部署作業」編輯既有測試部署並選「新版本」。這樣通常可保留測試 /exec URL；若建立另一個部署，必須更新 TEST_APPS_SCRIPT_URL 和 TEST_EXPECTED_APPS_SCRIPT_URL。

日後改 Python：提交並 push codex/test，下一次 Actions checkout 才使用新碼；clasp push 不會同步 Python。

回復：取消測試 Actions run、移除新 GAS 中你建立的測試觸發器、把測試部署改回上一個已驗證版本；資料由匯入前快照還原至測試表。不要為了回復測試去修改正式部署、正式 Secrets 或正式分支。

階段一完成的定義是「獨立環境已按上表驗證」。這份交付是可檢阅與執行的部署套件，不能代替尚未完成的雲端實測。概念確認後，階段二直接使用已提供的兩天逐字稿做獨立四分類，再比較現有結果；持有狀態不會直接當成當日買入。

## 本次交付的離線檢查

- PowerShell 語法解析通過；以模擬 git 的方式，在暫存目錄完整執行產生器，不連 GitHub。
- 生成的 13 個 GAS 檔案均通過 JavaScript 語法解析。
- 生成 YAML 解析、內嵌 Python 語法、只有手動事件、test Environment 與独立 concurrency 檢查通過。
- 生成 workflow 沒有無 TEST_ 前綴的 Secrets／Variables 引用；兩份 Python 入口位元組一致。
- 模擬 PropertiesService／MailApp 驗證：正式 Sheet ID 被拒絕、main 派工被拒絕、預設不寄信、啟用後只向測試收件者送出且不含 cc/bcc/replyTo。
- 來源 Setup.gs 的執行前後 SHA256 相同。

以上驗證不包含真實 clone／push、Google 授權、Apps Script 執行、郵件寄送及瀏覽器端到端驗收。
