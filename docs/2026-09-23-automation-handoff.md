# 自動取稿、稽核與前台驗收（2026-09-23）

## 本輪修正與界線

GitHub 的 `transcript-gemini` 只負責將自動聽打的原稿寫入「影片清單」，`daily.yml` 再讀原稿進入 `pipeline/pipeline.py`。手動稿及「手動保留」仍優先。自動流程現在將稽核的實際步驟回寫影片列；後台依選定日期同時查取稿 run、pipeline run 與影片狀態，不把取稿完成畫成整條 pipeline 完成。下游暫時失敗顯示「等待續跑」，保留刷新檢查點。

逐字稿寫入後，郵件查詢同步會排進佇列，即使當日簡訊的說明文字沒有變動也會排。五分鐘排程先處理這份佇列，再做報價快取；「簡訊未解析」不再擋逐字稿刷新或補日 K。簡訊來源 `CMONEY-`、人工補登及已寄送狀態的既有保護維持。

市場總覽的台指期當日折線只累積期交所實際回報的快照，不再從現貨指數與基差合成。開盤初期只有一個快照時會誠實顯示一個點；之後每次前台刷新才增加點，無人開啟時不宣稱有連續五分鐘歷史。加權指數與國際指標仍依畫面標示的來源與時間；產業成交比重是證交所盤後資料，不能標成即時。市場卡片改為較緊湊的全寬版，390px 手機與桌面測試無橫向溢出。

「新增訂閱」表單採加訂：舊戶已有每日總覽、這次只選盤中通知，原項目保留；要關閉其中一項須用管理訂閱頁明確取消勾選。訂閱查詢移除了一次沒有使用到的代號表讀取。個股詳情保留平行分塊請求，因為基本面冷快取慢，一次捆成一個回應會阻塞名稱和報價；K 線已由 `apiGetCandlesBundle` 合併日／週／小時請求。後台看似重疊的「取稿／稽核／郵件同步／日 K」是不同副作用，各自保留進度與重試，不應刪成單一巨型工單。

## GitHub 部署與驗收

本儲存庫只部署 `pipeline/pipeline.py` 和本文件；根目錄 `pipeline.py` 不得重建。推送後確認 `main` 是最新 SHA。`transcript.yml` 預定台北 11:05 起輪詢，`daily.yml` 11:20 起接稿；GitHub schedule 會延遲，預定時間不保證啟動。若取稿成功但判讀沒有開始，先看對應日期的兩個 Actions run，再看「影片清單」該日原文與處理狀態。後台的「即時日誌」分別連到這兩條 run。手動取消只針對選定日期正在執行的取稿 run；取消後既有冷卻期到時可再次派工。

離線回歸是在完整工作目錄執行：Python 561 項通過；Node 52 個既有測試檔通過，另外的加訂測試通過。這不等於已對正式 Google Sheets 或正式 Gemini 完成端到端測試。正式驗收請在下個交易日核對：取稿原文落地、pipeline 狀態由「讀取原文」推進到「寫入／刷新網站」、網站每日總覽及會員持股有當日資料、簡訊先到時稍後的逐字稿仍能補進郵件查詢。

## Apps Script 部署順序

Apps Script 原始碼由管理者手動部署，不在這個 Git 儲存庫。完整工作目錄位於 `C:\Users\user\Downloads\zhangzhen-stock-site-updated\apps-script`。本輪要更新的檔案為：`API.gs`、`Admin.html`、`Adminservice.gs`、`Cachebuilder.gs`、`Cmoney.gs`、`Config.gs`、`Logic.gs`、`MailService.gs`、`Market.html`、`Marketservice.gs`、`Setup.gs`、`SheetService.gs`、`Stylesheet.html`、`Tech.html`。同名檔案整份替換；其他檔案保留目前版本。網站與郵件的會員持股專區不能刪。

1. **先處理 FinMind 憑證，再換 `Config.gs`。** 舊版 `Config.gs` 曾把憑證放在原始碼；新版已移除。確認 Script Properties 內有 `FINMIND_API_TOKEN`，如缺少，在尚未貼新版前用舊版提供的遷移函式寫入，或由管理者在 Apps Script 專案設定中安全設定。不要把憑證貼進 Git、日誌或客服對話。新版 `setFinMindToken(token)` 只接受明確參數，也不回傳憑證。
2. 在同一個 Apps Script 專案更新上述 14 個檔案並儲存。執行 `checkProjectFiles()`；若缺檔或 marker 不符，先補齊再部署。接著執行唯讀的 `checkAutomationReadiness()`，確認 `everyFiveMin=true`、`webapp=true`，並檢查 `transcriptBackup`。
3. 若缺五分鐘觸發器，只執行 `ensureAutomationTick()` 補建；若缺後台工單續跑觸發器，執行 `installAdminJobWatchdog()`。不要為了這次更新重跑 `installTriggers()` 或重設日 K 游標。
4. 到「部署 → 管理部署作業」，編輯既有網頁應用程式，選「新增版本」並部署；開正式 `/exec?action=ping`，應看到 `build=2026-09-23-quality-v52`。只按儲存而沒有發新版本，正式網站仍會是舊版。
5. 在後台選一個有逐字稿的日期，核對取稿與 pipeline 的進度、分別開兩種日誌，刷新頁面確認狀態沒有退回初始值。查詢已寄送日子的郵件應保持「已寄送」，不自動重寄。另用一個測試信箱先訂每日總覽，再從新增表單只勾盤中通知；管理訂閱頁應顯示兩者都開啟。
6. 市場總覽開啟後核對每張卡片的來源時間，台指期一個真實快照時不能顯示虛構折線；手機 390px 左右不應橫向捲動。可用 `backfillDailyKStatus()` 唯讀查看既有日 K 進度。若舊狀態為簡訊造成的中止，新程式會恢復可續跑游標；不要先按 `resetDailyKCursor()`。

若正式 ping 仍是 v51，或 `checkAutomationReadiness()` 顯示觸發器、正式 URL、GitHub 備援憑證缺失，不能宣稱隔日自動化已驗收。優先補這些設定，再用日期對照 Actions 與後台狀態。
