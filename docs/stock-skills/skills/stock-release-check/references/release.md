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

按 diff 選相關測試、數值與畫面，再擴測解決具體風險；不把固定歷史測試總數當門檻。新增回歸失敗需處理，舊失敗也要指出影響，不能只說「本來就有」全部略過。

常用 `scripts/check_original_parity.py`、`scripts/build_original_site.py`；內容、行情、UI 各腳本讀目前參數後執行。`sync_quality.py` 只同步指定 Prompt／共用片段，不能代表全部 Python 與 GAS 等效。

建議來源核對：Git 追蹤清單 → GAS 對應檔（`.gs`／`.js`、大小寫、CRLF 正規化）→ cloud HEAD 差異 → 本次選定新增／修改檔。雲端多出的 Editor／Review 等檔不能自動刪；意外漂移先解讀合併，不能整包舊來源覆蓋。

## 3. GAS 原部署

1. 新版內容、manifest／markers、`GAS_BUILD`／`PROJECT_BUILD_` 與更新紀錄相符；保持 Properties、試算表、密鑰、寄送旗標。
2. CLI 確認 staging 工作目錄及 `.clasp.json`。先建立目錄再以它為 workdir，不能在同一命令才建立尚不存在的 workdir。可用目前本機 clasp 安裝（曾在 `scratch/clasp/node_modules/.bin/clasp.cmd`），先查存在與 CLI 語法。
3. 對照雲端 HEAD 後推送；儲存／push 是 HEAD，create-version 是不可變版本，更新原 deployment 才是 `/exec` 上線。
4. 在編輯器明確選取 `checkProjectFiles()` 並核對目前程式確有此函式。重新載入後下拉曾回到 `setupSpreadsheet`，不可沿用舊選取直接按 Run。不要為檢查執行初始化／重裝觸發器。
5. 編輯原「管理部署作業」，保持執行身分、存取權、部署 ID 與 URL。協作者可 push／建版但可能無權 deploy；重複相同 CLI 不會解掉 domain 限制。用已授權且登入的 owner 流程；需要使用者處理時列具體版本及阻擋原因。
6. 讀正式 `/exec?action=ping`，核對預期 build；傳播可能延遲，先有限重讀並確認部署版本，不每讀到舊值就再建一版。必要時拉指定不可變版本與 Git 來源比對，HEAD 相同不等於 production 相同。

普通更新不執行 `installTriggers()`。`whoAmI`、`showDeployInfo`、`ensureAutomationTick` 等只有讀到現行實作及副作用、確有需要才執行。寄件有效身分與部署身分分清。

## 4. GitHub、Worker 與正式資料

精確 stage 本輪檔案，檢查 diff，再 commit／push。記提交及 Pages run 實際對應 commit；主分支有其他人提交時追實際部署，不拿舊 run 當本輪成功。Python main 更新不保證 GAS Prompt 同步；Pages 成功不保證 Worker 已更新。

Worker health、cron、KV 快照產生時間和回源結果分開。快照 stale 超限需按來源時間驗，不是 HTTP 200 即正常；不要用全清 cache 製造過載。LINE relay 有獨立佇列、期限、平台接受結果；已整合就修既有通道，不新增第二套。

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
| GHA | `gh run list/view`；dispatch 先讀 workflow inputs／concurrency | 假設 `apply` 不寫資料、排多個 pending 互相取消 |
| 正式 UI | 可用瀏覽器工具，依新快照定位；腳本讀 DOM 作驗證 | 盲按過期選取；讀 cookie／隱藏憑證代登入 |
| 動態頁測試 | theme 鍵與 `html[data-theme]` 實查；等可觀察條件 | 錯 localStorage key 兩次都測同主題；smooth scroll 尚未結束就判失敗 |
| 工具方法 | 先查可用工具／函式及參數 | 用不存在的方法或舊欄位硬跑；宣稱操作成功無回覆 |

需新工具時優先現有 CLI／API／連接器；瀏覽器負責真實 UI。查詢使用者資料保持任務範圍，文件／技能維護不需要登入正式帳號、使用 Gemini 或重播資料。

## 7. 交付與回退

提供元件表：檔案／commit、HEAD／不可變版／原部署版、Pages run、Worker 版本、離線結果、正式 readback、帳本是否變化、速度樣本、未驗項。只有 specs、已存編輯器、建版、已部署、正式驗過是五種狀態。

回退列上一 commit／原部署版本、資料備份位置與重新驗證範圍。回退程式不會自動還原正式資料或瀏覽器新設定。不要將下一交易日尚未發生的端到端驗收寫成已完成。
