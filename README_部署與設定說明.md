# 張震股市盤中家教班　自動追蹤網站

把 YouTube 頻道「張震_股市盤中家教班」每天的盤中直播，自動整理成可查詢、可比對、可追蹤的網站與每日推播信。核心原則只有一條：**只呈現影片中明確講過的內容，沒講到就標「未說明」，不推測、不補完。**

本站與該頻道無隸屬關係，內容整理自公開直播。

---

## 一、系統架構

兩層式，中間用一份 Google 試算表當唯一交換介面，彼此不直接呼叫。

上游 GitHub Actions（平日，內部輪詢循環）：抓影片清單 → 取逐字稿（拿到即先落地）→ Gemini 潤飾/擷取/完整性稽核 → 分類、代號比對、產業剔除、寫入試算表。

下游 Apps Script（觸發器）：補快取（日K/基本面/報價）、重算持股追蹤（含逐日說明、轉譯錯誤剔除）、渲染網站（圖表、逐日、郵件查詢、AI 助手）、寄信（推播含防重寄、狀態、告警）。

---

## 二、檔案清單

上游（GitHub，Python）：
- pipeline.py：主程式。抓影片、取逐字稿、Gemini 潤飾與擷取、分類、寫試算表。含內部輪詢循環與多種手動模式。
- daily.yml：GitHub Actions 工作流程。放在 .github/workflows/daily.yml。
- requirements.txt：Python 相依套件。

下游（Google Apps Script，.gs）：
- Code.gs：對外 API 端點（api* 系列），前端經 google.script.run 呼叫。
- Setup.gs：初始化試算表分頁、安裝所有觸發器（installTriggers）。
- SheetService.gs：讀試算表、每日總覽、搜尋、持股追蹤重算（rebuildHoldingsTrackerJob）。
- CacheBuilder.gs：日K/基本面/代號對照快取；產業列清除（purgeIndustryRows）；一鍵刷新（refreshSiteNow）。
- QuoteService.gs：即時報價、K 線聚合、共用快取與速率保護。
- AiService.gs：問答小幫手後端，代呼叫使用者自帶的 Gemini 金鑰；含郵件唯讀查詢。
- MailService.gs：訂閱、每日推播（含防重寄鎖）、狀態報告、失敗告警、郵件查詢函式。含假日判斷。

下游（前端 HTML，貼進 Apps Script）：
- Index.html：頁面骨架與各分頁結構（含郵件查詢分頁）。
- JavaScript.html：前端邏輯（分頁、圖表、詳情抽屜、聊天、訂閱、郵件查詢、管理刷新）。
- Stylesheet.html：全站樣式與動畫。整份必須只有一組 <style>...</style>，</style> 為最後一行。
- Tech.html：技術說明頁（架構、金鑰、排程、版本沿革、常見問題、管理刷新入口）。
- Unsubscribed.html：退訂完成頁。

---

## 三、需要哪些金鑰與設定

- NOTEBOOKLM_AUTH_JSON（GitHub Secrets）：NotebookLM 登入狀態（storage_state.json 內容），取逐字稿。必要。
- GEMINI_API_KEY（GitHub Secrets + 指令碼屬性）：潤飾、擷取、稽核。必要。
- GOOGLE_SHEETS_SERVICE_ACCOUNT（GitHub Secrets）：服務帳號金鑰，寫試算表。必要。
- SPREADSHEET_ID（GitHub Secrets + 指令碼屬性）：試算表位置。必要。
- YOUTUBE_API_KEY（GitHub Secrets）：取影片清單（避開 RSS 對機房 IP 的 404）。建議。
- YOUTUBE_CHANNEL_ID（GitHub Variables）：目標頻道，未設有內建預設。選用。
- OWNER_EMAIL（指令碼屬性）：失敗告警與稽核通知收件者。必要。
- STATUS_EMAILS（指令碼屬性）：爬取狀態報告收件者，逗號分隔。建議。
- FUGLE_API_KEY（指令碼屬性）：上櫃股票日K；個股現爬。選用。
- ADMIN_KEY（指令碼屬性）：網站「立即刷新」管理密鑰，只有站方知道。建議。

使用者在問答小幫手貼的自己那把 Gemini 金鑰不屬於上表，只存在使用者瀏覽器的 localStorage，隨每次提問傳進後端、用完即丟，不寫入任何地方。

NotebookLM 認證重點：notebooklm-py 0.7.3 用 Google web session cookie（storage_state.json）認證，cookie 會被 Google 輪換、數天內過期。過期時在本機重新登入，把新的 storage_state.json 內容更新到 NOTEBOOKLM_AUTH_JSON。若 Playwright login 產生的 session 過期太快，改用瀏覽器 cookie 來源（需要能安裝 [cookies] extra 的 Python 環境，例如 3.11/3.12，Firefox 最不會被 App-Bound Encryption 擋）。

---

## 四、部署步驟

上游（GitHub）：
1. 把 pipeline.py、requirements.txt 放進儲存庫根目錄。
2. daily.yml 放進 .github/workflows/。
3. 到 Settings → Secrets and variables → Actions 設定上表的 Secrets 與 Variables。

下游（Apps Script）：
1. 建立一個 Apps Script 專案，綁定目標 Google 試算表。
2. 把 7 個 .gs 與 5 個 .html 逐一貼進去（HTML 檔用「HTML」類型建立，檔名不含副檔名，例如 Index）。
3. 到專案設定的「指令碼屬性」設定上表對應的屬性。
4. 執行一次 Setup.gs 的 installTriggers() 安裝所有觸發器。
5. 部署為網頁應用程式（每次改前端後要「部署新版本」才會生效）。

日後只改了前端（HTML）時，覆蓋對應檔案並「部署新版本」即可，不需要重跑資料。

---

## 五、系統排程（台灣時間，僅平日）

上游 GitHub：cron 只是「觸發」，實際爬取由 pipeline 進來後的內部輪詢循環控制（每次 job 進來自己每 3 分鐘敲一次門，約 25 分鐘收尾，交給下一次接力）。觸發點：11:20/35/50、12:05~12:50、13:05~13:50、14:05/25/45、15:03（最後一次才判定今日無影片）。

下游 Apps Script：
- 每 5 分鐘 refreshQuoteCacheJob（報價快取）
- 每 30 分鐘 authWatchJob（檢查登入是否失效）
- 12:30、13:10、15:50 statusReportJob（狀態報告）
- 13:00、14:00、15:30 dailyPushJob（每日整理，有防重寄鎖）
- 14:05 aggregateHourlyJob、14:20 repairCodesJob、14:30 rebuildFundamentalsJob
- 14:35 backfillDailyKJob、14:50 rebuildHoldingsTrackerJob、15:05 snapshotPerformanceJob
- 15:45 failureAlertJob（異常才寄）
- 每週日 04:00 rebuildCodeMapJob、每月 5 日 03:00 yearlyArchiveJob

推播分三時段用鎖搶占：搶到的先標「寄送中」，其餘退出，確保同一天只寄一封。

---

## 六、可手動調動的設定（GitHub Actions → Run workflow，互斥）

- backfill：一次處理 2026 起未完成的全部影片（會呼叫 NotebookLM/Gemini）。
- final：30 分鐘長逾時，手動補跑單一影片。
- fill_blanks：補齊缺逐字稿的列並重跑擷取。
- repair_codes：只重跑代號比對，非個股剔除（不呼叫 AI，幾十秒）。
- reclassify：依理由摘錄情緒把觀望改成觀望不碰/注意（不呼叫 AI，秒級）。
- reconcile：AI 判定產業並刪除、抽取明講買入價並核對後寫回（只呼叫 Gemini，用已存逐字稿）。

Apps Script 端常用函式：
- refreshSiteNow()：一鍵刷新網站既有內容（清產業、修代號、補日K、基本面、重算追蹤、記績效）。不抓新影片。想立刻更新網站而不等下個交易日時用這個。
- purgeIndustryRows()：立刻移除記憶體、台塑集團、AB載板等產業列。
- rebuildHoldingsTrackerJob()：重算持股追蹤與逐日說明。
- backfillDailyKJob()：補日K快取。
- installTriggers()：重裝所有排程。
- statusReportJob()：立即寄一封爬取狀態信。
- auditDigest("YYYY/MM/DD")：稽核某天推播與網站是否一致。

立即刷新網站的三種方式：
1. 網站管理入口：技術說明頁最底部，輸入 ADMIN_KEY 後按「立即刷新網站內容」。
2. Apps Script 編輯器：直接執行 refreshSiteNow()。
3. GitHub：先勾 reconcile=true 整頓資料，再用方式 1 或 2 刷新網站。

---

## 七、試算表分頁

影片清單、操作紀錄、會員持股、每日推播內容（日期／文字稿／寄送狀態）、使用者訂閱清單、日K快取、小時K、即時快取、每日績效、盤中快照、基本面快取、持股追蹤、系統狀態、修正建議。各層只透過這些分頁溝通，程式用表頭名稱動態尋找欄位，不寫死欄號。

---

## 八、網站分頁

每日總覽、持股追蹤、績效走勢、個股查詢、訂閱通知、郵件查詢、AI 助手、技術說明。

- 郵件查詢：查任一天寄出的每日整理內容（與網站同源）。AI 助手也能唯讀查詢郵件內容（無權修改）。
- 持股追蹤：進場價標明是否為張震明講的買入/賣出價；說明用最新補充而非首次理由；連續無實質操作字眼且首次理由未說明者，視為轉譯錯誤剔除。

---

## 九、已知的脆弱環節

notebooklm-py 使用 Google 未公開文件化的內部 API，且 web session cookie 會被 Google 輪換、數天內過期。一旦取逐字稿失敗，當天就沒有自動產出——這也是失敗告警與認證守望存在的理由。憑證失效時的恢復動作是在本機重新登入，把新的 storage_state.json 更新到 GitHub Secret。這是整套系統裡唯一無法完全自動化的環節。

---

## 十、維護提醒

- Stylesheet.html 的 </style> 必須是最後一行。用附加方式加 CSS 時務必插在 </style> 之前，否則多出來的 CSS 會被當成網頁內文顯示成亂碼。
- 不要在 <tr> 上加 ::before / ::after 偽元素。在 table 版面它會被當成一個匿名儲存格，把第一個 <td> 擠到第二欄，造成整列右移。要加就加在某個 <td> 上。
- 數字一律不憑印象填，改動前先對照試算表或原始檔驗證。
- 每日推播的「每日推播內容」分頁欄名須為 日期／文字稿／寄送狀態，郵件查詢才讀得到。
