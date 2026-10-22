# 發布、版本同步與實測交付

## 1. 權威與範圍

工作前讀 cwd／`git status --short`／remote／branch／目前 build；找適用的 `AGENTS.md`，不存在就不虛構。源碼以目前 Git 追蹤、symlink 及構建腳本確認。`public-site/gas-source/` 是現行正本；`apps-script/` 可能連到它，不可只改未追蹤鏡像。其他工作階段的 dirty files 不加入本輪 commit。

| 修改 | 需核對的部署面 |
|---|---|
| Python 規則／workflow | main commit、Actions 執行提交、preview／apply 模式與資料 |
| HTML／CSS／JS | Pages 構建、原 GAS HEAD／不可變版本／原部署、真實頁面 |
| GAS API／欄位 | 原部署、Worker 白名單／參數與回應相容、前後台 |
| Worker／relay | 對應部署、health、queue／cron、回源及資料日期 |
| 通知模板 | 網頁查詢、Email／LINE 各格式、同意及帳本；不自動發測試給全體 |
| 本機 skills／規格 | 本機安裝、版本化副本、引用與 HTML 索引；不需要 GAS 部署 |

目前常用入口（重新讀設定核實，非永久保證）：倉庫 `Lee200202/Stock`；Pages `https://lee200202.github.io/Stock/`；GAS project `1eTCOyRy7G9YoG_UaO_LTPgesYmhPjpjzT4ItmXjyE71Q4JrARliIjYIE`；原 deployment `AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw`。從 `.clasp.json`、設定與原部署交叉核對；不得因 deploy 權限問題另建網址。

## 2. 本機關卡與來源同步

修改Config的GAS_BUILD或畫面版本標記時，同步Setup的PROJECT_BUILD及PROJECT_HTML；新增共用include也納入檢查。發布前執行 `node scripts/check_project_manifest.cjs`，核對真實HTML標記及版本一致，避免新版被舊檢查表誤報。標記優先採穩定元件識別，不依賴可翻譯的英文註解。

按 diff 選相關測試、數值與畫面，再擴測解決具體風險；不把固定歷史測試總數當門檻。新增回歸失敗需處理，舊失敗也要指出影響，不能只說「本來就有」全部略過。

常用 `scripts/check_original_parity.py`、`scripts/build_original_site.py`；內容、行情、UI 各腳本讀目前參數後執行。`sync_quality.py` 只同步指定 Prompt／共用片段，不能代表全部 Python 與 GAS 等效。

建議來源核對：Git 追蹤清單 → GAS 對應檔（`.gs`／`.js`、大小寫、CRLF 正規化）→ cloud HEAD 差異 → 本次選定新增／修改檔。雲端多出的 Editor／Review 等檔不能自動刪；意外漂移先解讀合併，不能整包舊來源覆蓋。

## 3. GAS 原部署

1. 新版內容、manifest／markers、`GAS_BUILD`／`PROJECT_BUILD_` 與更新紀錄相符；保持 Properties、試算表、密鑰、寄送旗標。
2. CLI 確認 staging 工作目錄及 `.clasp.json`。先建立目錄再以它為 workdir，不能在同一命令才建立尚不存在的 workdir。clasp 裝在 `scratch/clasp/node_modules`：Codex 端曾直接呼叫 `scratch/clasp/node_modules/.bin/clasp.cmd`，Claude Code 端在 `scratch/clasp/headNNN` 裡用 `npx --prefix .. clasp`（第 8.1 節）；兩者是同一份安裝，先查存在與 CLI 語法。
3. 對照雲端 HEAD 後推送；儲存／push 是 HEAD，create-version 是不可變版本，更新原 deployment 才是 `/exec` 上線。
4. `checkProjectFiles()` 要在編輯器裡選取後執行：Codex 端可在已登入的瀏覽器操作，Claude Code 端沒有登入編輯器、改用重新 `clasp pull` 後 `compare.py` 比對（第 8.1 節）。編輯器重新載入後下拉曾回到 `setupSpreadsheet`，不可沿用舊選取直接按 Run；它的「舊版」判斷靠檔案裡的特徵字串，字串過期會誤報（第 8.1 節第 7 點）。不要為檢查執行初始化／重裝觸發器。
5. 編輯原「管理部署作業」，保持執行身分、存取權、部署 ID 與 URL。協作者可 push／建版但可能無權 deploy；重複相同 CLI 不會解掉 domain 限制。用已授權且登入的 owner 流程；需要使用者處理時列具體版本及阻擋原因。
6. 讀正式 `/exec?action=ping`，核對預期 build；傳播可能延遲，先有限重讀並確認部署版本，不每讀到舊值就再建一版。必要時拉指定不可變版本與 Git 來源比對，HEAD 相同不等於 production 相同。

普通更新不執行 `installTriggers()`。`whoAmI`、`showDeployInfo`、`ensureAutomationTick` 等只有讀到現行實作及副作用、確有需要才執行。寄件有效身分與部署身分分清。

## 4. GitHub、Worker 與正式資料

精確 stage 本輪檔案，檢查 diff，再 commit／push。記提交及 Pages run 實際對應 commit；主分支有其他人提交時追實際部署，不拿舊 run 當本輪成功。Python main 更新不保證 GAS Prompt 同步；Pages 成功不保證 Worker 已更新。

Worker health、cron、KV 快照產生時間和回源結果分開。快照 stale 超限需按來源時間驗，不是 HTTP 200 即正常；不要用全清 cache 製造過載。LINE relay 有獨立佇列、期限、平台接受結果；已整合就修既有通道，不新增第二套。

來源審核修正使用 `source-review-preview/apply` 時，先確認計畫的raw SHA、舊列SHA與通知來源，三表備份確認後，以同一Sheets原子批次寫入修改及修正收據；不對結果未知的寫入盲目重送。重跑先核對收據及最終列，只接續郵件查詢、持股、績效刷新。全部讀回後核對寄送旗標與帳本。備份前後比較與GitHub concurrency不能消除獨立GAS工作的所有競爭，偵測同期變更時須停止並對照備份。

`apiSearchByDate`讀到新資料不表示首頁與郵件快照已更新。分別核對`apiGetDashboard.today`、`apiGetMailContent`及實際瀏覽器畫面，保存快取核對時間；正常排程範圍內等待該鍵更新，不為驗收全清快取。首頁「目前持有」為全部未出場回合，不等於當天逐字稿提及的持股檔數；移除當日錯位說明後，歷史持股仍可能合理存在。

正式資料 apply 前備份指定日期／表到獨立私人試算表，檢查原稿指紋及並行寫入。讀回同日分類、文章、來源、持股／績效及寄送帳本；已寄送旗標不可重置。局部說明更新優先走來源綁定工具，不為幾句重跑整天模型。歷史單日 preview 通過和本輪 apply 的模型結果不是同一份，apply 還要過關。

## 5. 真實介面及通知驗收

瀏覽器走受影響的前台與後台流程，至少桌面／手機，按需求補五寬度、兩主題、鍵盤／減少動態。讀實際 `innerWidth`，因視窗工具要求尺寸可能未生效。截圖只證明靜態呈現，還要點選、切換、等待結果與返回。

原 `/exec` 若只出 Google banner／無法開啟檔案：報原站視覺未驗，另提供 ping、不可變版本比對及 Pages 視覺，不混成全部通過。頁面已開不代表後台已登入／資料已載完。API 測試也不能取代真實畫面。

Email 查詢／HTML 預覽不等於 Gmail 收件匣。LINE JSON 合法不等於真機收到。真寄送須明確在授權範圍及指定測試對象；只看正式帳本就標「服務接受」，不得寫已讀。已寄出的 Email 是快照，更新網站不會替收件匣改稿。

## 6. 常用工具及已發生的操作失誤

| 情境 | 選工具／做法 | 避免 |
|---|---|---|
| 查來源／函式 | `rg`／`rg --files`，限定追蹤目錄及 `-g` | 將 scratch/node_modules 全部列出；PowerShell 路徑 glob 當 rg 檔名 |
| PowerShell 上下文 | `Select-String -Context 0,5` 或 rg | `Select-String -A` 是無效參數 |
| 測試後提交 | 查每個 exit code；失敗停止後續提交 | 分號連測試與 git，最後 git 成功掩蓋測試失敗 |
| 大量輸出 | 指定欄位、有限日誌／關鍵結果，JSON 明確欄 | 金鑰、私人通知、整份 HTTP body 進公開 artifacts |
| GHA | Codex 端：`gh run list/view`；Claude Code 端沒有 `gh`，用 REST（`scratch/v139/gh.py`，第 8.2 節）。dispatch 前先讀 workflow inputs／concurrency | 假設 `apply` 不寫資料；同一 concurrency group 連續排多個 run（只留一個 pending，其餘被取消，10/16 的 10/15 重播就這樣被取消） |
| 正式 UI | 可用瀏覽器工具，依新快照定位；腳本讀 DOM 作驗證 | 盲按過期選取；讀 cookie／隱藏憑證代登入 |
| 動態頁測試 | theme 鍵與 `html[data-theme]` 實查；等可觀察條件 | 錯 localStorage key 兩次都測同主題；smooth scroll 尚未結束就判失敗 |
| 工具方法 | 先查可用工具／函式及參數 | 用不存在的方法或舊欄位硬跑；宣稱操作成功無回覆 |

需新工具時優先現有 CLI／API／連接器；瀏覽器負責真實 UI。查詢使用者資料保持任務範圍，文件／技能維護不需要登入正式帳號、使用 Gemini 或重播資料。

## 7. 交付與回退

提供元件表：檔案／commit、HEAD／不可變版／原部署版、Pages run、Worker 版本、離線結果、正式 readback、帳本是否變化、速度樣本、未驗項。只有 specs、已存編輯器、建版、已部署、正式驗過是五種狀態。

回退列上一 commit／原部署版本、資料備份位置與重新驗證範圍。回退程式不會自動還原正式資料或瀏覽器新設定。不要將下一交易日尚未發生的端到端驗收寫成已完成。

## 8. Claude Code 端的發布步驟與陷阱

### 8.1 Apps Script（clasp）

1. 建一個新的暫存目錄 `scratch/clasp/headNNN`，複製 `.clasp.json` 進去，在該目錄 `npx --prefix .. clasp pull` 取得編輯器 HEAD。
2. `python scratch/clasp/compare.py scratch/clasp/headNNN` 列出 `differs`、`only in editor`、`only local`；再加檔名參數（如 `Aiservice.js Index.html`）只複製本輪改到的檔案。不要整包覆蓋：編輯器另有 `Editor.html`、`Editorpanel.html`、`Editorservice.js`、`Review.js`、`Reviewpanel.html`、`Versionlog.html` 等 repo 沒有的檔案，必須保留。
3. 檔名對應：repo 的 `.gs` 在編輯器是 `.js`；`MailService.gs`、`SheetService.gs` 在編輯器是小寫的 `Mailservice`、`Sheetservice`（其他檔名大小寫相同）。複製時用編輯器的檔名，否則會多出一個同名不同大小寫的檔案。
4. `clasp push -f` → `clasp create-version "說明"`，記下版本號。
5. 原 `/exec` 更新依本次授權與當前帳號能力執行。歷史曾出現「Only users in the same domain as the script owner may deploy this script」及瀏覽器權限錯誤；當前若重現，停止該部署並請擁有者協助，不繞過。未遇到時不能把舊拒絕當永久擁有者限定。更新後核對原deployment指向、版本來源及實際畫面，ping只作一項證據。
6. 定時觸發器跑的是 HEAD，網站刷新與後台按鈕跑 `/exec`：推送後到原部署更新前，兩條路徑可能使用不同版本。改到資料計算方式時要明講。
7. `checkProjectFiles()` 的「舊版」標記以檔案裡某個特徵字串判斷（10/14：`MailService.gs 還是舊版（mailHero_ 裡找不到「border-radius:22px」）`）。標記若沒隨程式更新，會誤報舊版；先查標記本身是否過期。
8. 對齊的四處：GitHub main、編輯器 HEAD、不可變版本、`/exec`。10/15 驗收時版本 479 與 GitHub `3d1f39c` 一致，`/exec` 仍在 478，等擁有者切換。
9. 推送來源要用**已提交**的版本，不用工作區：`git worktree add "$TEMP/wtgas" HEAD`，在那個目錄執行 `compare.py`，避免把另一個工作階段尚未提交的 `Tech.html`、`Stylesheet.html` 推進編輯器；用完 `git worktree remove --force`。
10. `compare.py` 用 repo 檔名比對，`MailService.gs`、`SheetService.gs` 會出現在 `only local`、編輯器的 `Mailservice.js`、`Sheetservice.js` 出現在 `only in editor`：這兩個要手動複製成編輯器的小寫檔名。推送後重新 `clasp pull` 到新目錄，再跑一次 `compare.py` 確認 `differs: []`。
11. `/exec?action=ping` 回的是 `Config.gs` 的 `GAS_BUILD` 字串；這一版沒改 `GAS_BUILD` 時，ping 分不出 `/exec` 指向哪一個版本號（10/16：484、485、486 的 build 都是 `2026-10-16-tech-book-v184r1`）。要嘛在改動時一起更新 `GAS_BUILD`（先確認沒有別的工作階段正在改 `Config.gs`），要嘛請擁有者在「管理部署作業」看版本號回報；不要把「build 字串相同」寫成「已確認是新版」。

### 8.2 GitHub 與 Pages

- 沒有 `gh` CLI，用 REST（`stock-automation-triage` 第 9 節）。Pages 建置結果：`GET /repos/Lee200202/Stock/pages/builds/latest`。
- `tests/` 在 .gitignore：本機測試不會跟著推送，CI 只跑 `.github/workflows/public-site.yml` 列出的 `scripts/check_*`。推送前本機跑 `python -m unittest discover -s tests -p "test_*.py"`。
- 另一個工作階段可能同時推送：先 `git fetch`。工作區有別人未提交的改動時 `git pull --rebase` 會拒絕執行，不要為了 pull 而 stash 或還原別人的檔案；改用 `git merge --ff-only origin/main`（別人改的檔案上游沒動時可以快轉），或確認 `git log HEAD..origin/main` 是空的再直接 push。
- 只 stage 本輪檔案。同一個檔案裡混有別人的修改時，只暫存自己的區塊：`git diff -U0 檔案 > d.diff`，留下自己的 hunk，`git apply --cached --unidiff-zero d.diff`，再用 `git diff --cached` 確認（10/16 `JavaScript.html` 同時有另一個工作階段的 `data-tech-book` 改動）。

### 8.3 修改檔案時的陷阱

- Git Bash 的 heredoc 會吃掉反斜線（即使用 `<<'EOF'`）：含正規式或 `\n` 的修補腳本用 Write 工具寫成檔案再執行。
- 工作目錄的檔案是 CRLF：修補腳本讀位元組、轉 LF、修改、寫回原本的換行。
- `scripts/sync_quality.py` 會從 Python 與範本重新產生 GAS 的區塊：直接改那些 GAS 區塊，下次同步就被蓋回。
- 權限分類器會擋下它判定為正式部署的動作（單日 `apply`、Worker 部署、`/exec` 更新）：被擋就照實回報，請管理者決定，不要換一種方式繞過，也不要去改權限設定。

### 8.4 瀏覽器窗格

- 後台（`/exec?page=admin`）在巢狀跨網域 iframe 裡：`get_page_text`、`find`、`read_page` 只看得到 Google 橫幅；只能截圖、縮放、滾輪、依座標點選。分頁列要拖曳（`left_click_drag`）後再截圖定位。
- 管理者自己登入（不輸入管理金鑰或 Google 密碼）；勾選「記住這台裝置」後重新載入會自動登入。
- 「維護工具」分頁的按鈕會啟動正式工作（全面重整、從此步驟重跑）：只看不點，除非管理者要求那一項。
- 後台顯示的是 `/exec` 指向的版本，不是 HEAD。

## 9. 正式資料寫入的並行安全（10/16 事故）

GitHub 的單日更新（`apply`／`apply-rules`）會刪掉並重寫那一天的列；Apps Script 也有好幾個定時工作會讀寫同一天的列。兩邊同時跑就會互相蓋掉，或寫到錯的列。

| Apps Script 寫入者 | 什麼時候跑 | 寫什麼 |
|---|---|---|
| 寄信前品質關卡（`runQualityGate_` → 稽核／複審／重寫） | 12:00–22:00 每五分鐘一棒的 `dailyPushJob`，當天第一次寄送前 | 稽核：`autoFixAuditMissing` 補登漏抓的列；複審：`reviewDayRecords_` 改分類、改名稱、刪列；重寫：重寫每日整理 |
| `resolveBlankCodes_` | 撰稿、刷新時 | 依名稱補空白代號（寫成數字） |
| `cleanNamesPhase_`、全面重整 | 後台按鈕、全面重整工作 | 名稱清理 |
| 逐日同步 | 後台存檔後、假日與夜間 | 同步到網站快取 |

10/16 發生的事：流程把聯電、新興、天域判為不公開，寄信前的關卡把它們當成漏抓，另外問模型後直接補登。模型回的說明是別檔的（聯電掛著台積電的說明、長興掛著瑞鼎的說明），代號留空後又被名稱補成數字，四筆錯位列就上了網站。已修正（`7b2cee3`）：
- 補登不收流程「分類裁決」當天已裁決過的股票；引句與說明要講到這一檔；原文念出的代號優先。
- 複審寫回前確認那一列的名稱、代號、說明沒變；被別的工作改寫過就整天不寫，下一棒重新收集。

發布時的檢查：
1. 套用前看當天的寄送狀態與關卡狀態。12:00–22:00 還是「待寄送」時，關卡可能正在跑：先告訴管理者「今天的信還沒寄出、關卡可能同時寫入」，讓管理者決定要不要先寄、先等或先套用。
2. 套用後用公開 API（`apiSearchByDate`）讀回，檢查每一列代號的型別：流程寫的是字串，Apps Script 寫的（手動補登、通知列、關卡補登、代號補填）是數字。數字代號的列要逐筆看是誰寫的、說明是不是在講這一檔。
3. 讀回持股追蹤（`apiGetHoldingsTracker`）：今天有沒有新開或被結束的回合（10/16 聯詠被錯開一個回合、嘉澤的回合被錯誤結束）。
4. 要刪錯列時用有指紋的修復流程（`regenerate-verified.yml` 的 `row-repair-preview`／`row-repair-apply`：鎖定日期、股名、代號與說明指紋，先備份），不在後台手動刪；刪除前要管理者同意。
5. 已寄出的信不會跟著網站改：寄出前發現錯誤，要先問管理者要不要讓它照常寄出。

## 10. 測試基準與工具雜項

- **先量基準再說回歸**：`tests/*.js` 有一批測試依賴未進版控的本機檔案，在乾淨的 HEAD 上本來就會失敗（10/16 有 13 個）。宣稱「我的改動沒有造成失敗」前，用 `git worktree add` 在乾淨的 HEAD 與「HEAD＋本輪 diff」各跑一次，比較兩份失敗清單。
- **Windows 編碼**：`quick_validate.py` 等讀中文檔的腳本要加 `PYTHONUTF8=1`，否則 cp950 讀檔失敗；Python 輸出中文時加 `PYTHONIOENCODING=utf-8`。
- **等待**：不用 `sleep` 等工作完成；GitHub run 用 `gh.py wait`，長時間工作用背景執行。同一個 concurrency group 要一個跑完再送下一個。
- **用量**：送出會用到模型的 run 前，估算請求數（單日重播或套用約 14–18 個模型回覆，可在日誌數 `finish=` 的次數），並記錄當天累計；額度台灣 15:00 歸零。
- **管理者中止工具呼叫時**：停下來等管理者說明，不要換一種方式重試同一件事。

### 圖解章節發布的額外核對

- 技術頁新增共用 HTML 片段時，由 `Index.html` 直接 include；同步 `Setup.gs` 的檔案特徵檢查。雲端檔案數會增加，不能沿用上一輪固定檔案數宣稱全部一致。
- 除 HTML／CSS／JS 外，比對放大視窗的 SVG 與公開截圖資產。Pages 資產發布成功後，再驗圖解視窗與圖片的實際載入；`HTTP 200` 或部署版本正確不能代替圖片完整呈現。
- 章節驗收以實際 `innerWidth`、字級與深淺色紀錄，不能只記工具要求的尺寸。瀏覽器尺寸覆寫可能只作用在當前分頁；量到另一個寬度時先修正測試分頁，不把它算進目標矩陣。
