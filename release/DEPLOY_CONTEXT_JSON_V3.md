# 2026/09/11 更新：上下文收錄與合併 JSON 判讀

本說明已更新為整合版：`zhangzhen-context-json-v3-thinking.zip`，包含 Gemini 3.5 Flash-Lite 與 13:45 日K路徑。先前的 ZIP 保留供回復，請使用整合版。

## 模型與日K整合補充

- 已確認 `gemini-3.5-flash-lite` 是正式穩定名稱，支援結構化輸出，輸入／輸出上限為 1,048,576／65,536 tokens。[Google 官方模型文件](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
- Python 及 Apps Script 預設均為此模型。`GEMINI_MODEL` 有值時仍優先；欲全部改用 3.5，請設 `GEMINI_MODEL=gemini-3.5-flash-lite` 並清除 `GEMINI_MODEL_2` 到 `_5` 的舊覆寫，尤其 `_3`。
- 正式呼叫與健檢共用模型參數規則，重試／換金鑰時重新組裝。3.x 使用 `thinkingLevel: medium（可設定）` 並省略 temperature；這不是關閉思考。2.5 保留 thinkingBudget。Apps Script 同步套用。[Google 3.x 參數指引](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.5)
- `check_keys` 不只查模型清單，還會實際生成短回覆，會使用少量配額；工作流程文字已更正。模型名稱正式存在不代表每把金鑰當下都有權限或額度，部署後仍須實測。
- 配額按專案套用、依模型與方案而異；同一專案多把金鑰不能視為額度加倍。是否可用及可用量以 AI Studio 實際顯示為準，不保證換模型就有獨立免費額度。[官方限制說明](https://ai.google.dev/gemini-api/docs/rate-limits)
- 保留 `45 5 * * 1-5`（台北 13:45）、`daily-dailyk` 獨立佇列、探測放行與 `DAILYK_ONLY` 路徑，日K不要求 Gemini 金鑰，也不執行生成健檢。Apps Script 14:35 備援排程保持原設定。
- 在每批／每次重試送出前檢查剩餘時間，HTTP timeout 也受剩餘預算限制；不足時保留日K游標並正常結束。下游真正回報錯誤時則標記失敗，避免把失敗說成補完。
- JSON 判讀的四個設定現已接入 workflow，可直接用 Repository Variables 調整。套件仍須先更新 Apps Script、再更新 GitHub，最後 `check_keys` 與重新判讀 9/10 原稿。

## 已完成

- 以您更新的 `pipeline/pipeline.py` 為主，根目錄副本同步為完全相同內容；同步工具也修正 Windows 混合換行造成的差異。
- 普威固定對應譜瑞-KY（4966）；戲制台改為矽製材，只接受官方清單完全同名的代號，找不到就保留代號待確認。後續 AI 不再把這些人工確認名稱改掉。
- 日幣及常见貨幣先於代號／拼音比對排除，日幣即使錯帶 1526 也不會變成日馳。真正的日馳仍正常保留。
- 引用缺名稱：從完整原稿補回該名稱的來源段落。有可判斷分類的 uncertain 候選納入原分類，保留待確認註記到理由／持股說明及稽核資料。
- 缺時間短句：先從已確認原句補足；模型已由上下文判出日期者保留並註明依上下文判讀。明確歷史且日期不明仍列歷史；不把回顧硬算成今天。
- 原文沒有這家公司、分類也無法判斷的候選仍保留待確認，避免拿另一檔的動作補進來。沒有原句支持的價格仍留「未說明」。

## 減少模型呼叫

正常一批逐字稿由一次 JSON 請求完成擷取、分類、日期、同次補漏自查及大盤摘要。來源只送一次，以 S 編號取代重抄引句。程式在本機還原引用並補回上下文；有未解問題才追加一輪修復，每批最多一次。修復 JSON 只帶候選欄位，不重送展開引句或內部欄位。

每日整理用最後的結構化資料產生六章文章，不再額外請模型撰稿，因此文風比較固定，第五章使用已驗證的觀點摘要。代號、日期、持股與操作表仍取自最終校對結果。完整性檢查改為同一次模型回答內自查，並非另一個模型的獨立覆核。

以這次約 24,396 字的長度，離線長度測試能放進一批。這代表判讀主流程通常一次，需修復則兩次；不包含金鑰健康檢查、首次潤飾、仍有未解名稱的釐清或 API 配額重試。JSON 本身不保證省 token，主要節省來自不重送整份原稿與重複引句。

Google 官方文件列出的 Gemini 2.5 Flash 與 3.1 Flash-Lite 輸入上限均為 1,048,576 tokens、輸出上限均為 65,536 tokens，皆支援結構化輸出：

- https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash
- https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite

程式刻意使用更低的預算：以 UTF-8 位元組數作保守 token 上界估計，總預算預設 120,000，扣除 prompt、輸出與餘裕後才裝入原文；不是精確 tokenizer，也不額外呼叫 countTokens。長稿自動分批、保留全域 S 編號與前兩段重疊；不假設不同 API 請求會記得上次內容。修復送出前再量一次整包大小，超限則保留稽核註記、不截掉原文。

可選環境變數（不新增也能執行）：

| 變數 | 預設 | 用途 |
| --- | --- | --- |
| GEMINI_COMBINED_ASSESSMENT | true | false 可切回分開擷取／覆核 |
| GEMINI_ASSESSMENT_TOKEN_BUDGET | 120000 | 合併判讀總預算，含輸出預留 |
| GEMINI_CONTEXT_TOKENS | 1048576 | 換成較小上下文模型時，請設為所有可能切換模型中的最小值 |
| GEMINI_ARTICLE_ENABLED | false | true 恢復額外 AI 撰稿 |

這些環境變數已在整合版 workflow 的主程式 `env` 區塊接入，可直接透過 GitHub Repository Variables 調整。

## 部署與重跑

1. 解壓縮 `zhangzhen-context-json-v3-thinking.zip`。先把包內 Apps Script 檔案更新至原專案，儲存並部署新版本，保留原網址、指令碼屬性與試算表。
2. Apps Script build 應顯示 `2026-09-11-context-json-v3`。此次實際改動的 Apps Script 檔為 `Adminpipeline.gs`、`Aiservice.gs` 與 `Code.gs`；包內其他檔為目前工作區版本，方便完整部署。
3. GitHub 必須一起更新根目錄 `pipeline.py` 與 `pipeline/pipeline.py`，保留 `.github/workflows/daily.yml` 及相依套件。不要只更新其中一份。
4. 在後台重新判讀 2026/09/10 的原始逐字稿。已存在的潤飾稿可沿用；只按網站刷新不會重新擷取，也不會補回被隔離的股票。原工單若仍是處理中，可依既有續跑流程處理；新規則版本不會沿用舊 evidence-v2 的刷新完成檢查點。
5. 日誌應出現 `JSON合併判讀`、`JSON本機校對完成` 及 `每日整理：使用最終JSON資料產生六章文章`。檢查普威／譜瑞-KY、祥碩、國巨是否依原文分類列入；四星KY的買賣是否保留並有正確日期註記；日幣是否退出個股表。
6. 等候郵件查詢、代號、持股追蹤與績效等必要刷新步驟全部結束，才視為完成。您提供的日誌停在持股追蹤開始，尚無足夠資訊判定該步是卡住或只是尚未結束；本版保留逐步檢查點與失敗續跑機制。

先前誤寫的日馳需透過上述來源影片重新判讀覆蓋，程式不會將全站所有真正的日馳資料刪掉。已寄出的郵件不能改動；更新的是表格、網站及郵件查詢內容，不自動重寄。

## 驗證與範圍

66 項 Python 離線測試通過，涵蓋收錄、時間、假引句拒絕、代號、批次完整性、一次判讀呼叫、修復上限、文章、檢查點、模型參數／重試及日K時間到續補。Apps Script 名稱／貨幣回歸與原有流程測試通過，18 組脚本語法檢查通過。

此更新尚未部署至正式 GitHub／Apps Script，未使用正式 Gemini 金鑰或完整 9/10 原稿進行線上重判；實際分類與呼叫次數須以重跑結果為準。修改前備份保存在工作區 `backups/before-context-json-v3.zip`。Codex 的週用量與 Gemini API 配額是兩套限制，本版在本次工作完成前交付，不替您建立背景排程或消耗重設額度。


## Thinking 可設定更新

預設 medium。GitHub Repository Variables 設 GEMINI_THINKING_LEVEL=medium 或 high；Apps Script 在「專案設定 → 指令碼屬性」新增同名屬性，值同樣是 medium 或 high。兩邊分開設定；未設皆用 medium，可改 low 降低延遲。Apps Script 個別呼叫可用 opt.thinkingLevel 覆寫。只影響 3.x，2.5 原參數保留。設定錯字明確報錯。

3.5 Flash-Lite 支援 low/medium/high，免費層含 thinking tokens，官方未將 high 列為付費限定；實際額度依專案。較高思考可能增加 token、延遲及輸出截斷機率，不保證分類較準。建議日常 medium，難例重跑才改 high；尚未用正式原文比較兩者品質。

來源：[思考參數](https://ai.google.dev/gemini-api/docs/generate-content/thinking)、[免費方案價格](https://ai.google.dev/gemini-api/docs/pricing)。

稽核 prompt 在 Python 與 Apps Script 同步補上：分散的身份／動作／時間證據可補回、禁止跨股借用動作、上下文日期說明、明確候選分類、範例股票不得無原文新增、覆核候選不可無聲消失。放寬的是證據格式與上下文收錄，不是新增沒有來源的交易。部署後仍需以 9/10 原文確認。
