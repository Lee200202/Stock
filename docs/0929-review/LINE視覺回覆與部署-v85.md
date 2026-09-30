# LINE 視覺回覆 v85：資料、圖卡與部署

這版讓 LINE 查詢直接讀網站已發布的資料，並把短文字改成方便在手機上掃讀的圖形。折線圖只有在同一時段有至少三個真實價格點時才出現；**不拿兩點硬連成「走勢」，也不把昨日日線當今日即時行情**。LINE 不生成投資建議。

## 已接入的 16 種視覺元素

| # | 使用情境 | 呈現方式 | 來源與不成立時的處理 |
|---|---|---|---|
| 1 | 每日整理 | 日期與文章標題的深色主視覺 | 已發布且品質關卡完成的文章；無影片不寄。 |
| 2 | 每日整理 | 盤勢兩點摘要 | 已驗證文章原句；不足不補造。 |
| 3 | 每日整理 | 買入／賣出／持有／觀望四個計數格 | `searchByDate`；沒有的類別顯示 0。 |
| 4 | 每日整理 | 四類占比色帶 | 同一組計數；總數 0 不顯示。 |
| 5 | 個股查詢 | 最近一次分類色標 | `searchStock` 已發布紀錄；顏色只表示原分類，不表示現在建議。 |
| 6 | 個股查詢 | 可證實價位格 | 經 `displayPrice_` 檢查；均線天數、張數等不當價格。 |
| 7 | 個股查詢 | 至多四次提及的時間軸 | 去除同日同方向重複紀錄，並連到網站完整歷史。 |
| 8 | 持股回合 | 進場基準、目前價或出場價，以及示意報酬字卡 | `getHoldingsTracker` 的同一回合；已出場明確標示，不把出場價冒充目前價。不是實際成交報酬。 |
| 9 | 持股回合 | 進場後日 K 收盤折線，虛線為進場基準 | 只讀已預熱的 `dk2_代號` 快取；已出場的走勢截在出場日。少於三根、起點或中間缺口過大、資料太舊，就不畫。最後一點可接該回合目前價或出場價。 |
| 10 | 持股追蹤 | 有報價正報酬／其餘／無報價色帶 | 已發布持股回合；無報價不算虧損。 |
| 11 | 持股追蹤 | 前八檔小卡 | 進場與最近提及日；其餘檔數與完整清單連到網站。 |
| 12 | 市場總覽 | 加權指數當日折線及前收虛線 | 現有 `market_live_index` 或市場快取的同日價點；至少三點才畫。 |
| 13 | 市場總覽 | 台指期當時段折線及漲跌比較基準虛線 | 期交所快照的日盤或夜盤 `line`；兩個時段不接線。 |
| 14 | 市場總覽 | 成交金額／預估金額、台指期成交口數 | 快取有數值才列；預估明確標為預估。 |
| 15 | 市場總覽 | 美元指數、美元／台幣與原油指標卡 | 讀同一份「市場總覽快取」，每項附來源資料時間；不宣稱與台股同時更新。 |
| 16 | 市場總覽 | 前五大產業成交比重橫向色帶 | 證交所產業比重快取及日期；完整清單在網站展開。 |

原有歡迎卡、訂閱狀態卡、會員通知原文卡保留。盤中通知仍是明確啟用才送的後台／隱藏指令功能，公開圖文選單沒有新增此入口。上述 16 項是**視覺元素與圖卡樣式**，不是 16 張同時推給每位使用者的訊息。市場查詢最多三則 Flex：台股及台指期、國際指標、產業；不會為查詢再打新的行情 API。

## 圖片服務與資料邊界

`line-webhook/chart.mjs` 在既有 Cloudflare Worker 的 `GET /chart.png` 產生 480×180 PNG。LINE Flex 圖片使用既有 `relayUrl` 的 HTTPS 網址。網址只有已發布價格序列與參考價，**不含 LINE userId、頻道金鑰、Email 或訂閱資訊**。Worker 僅接受 3–32 個有限正數，拒絕其他內容；無資料不輸出假圖。每張圖最長快取 5 分鐘。台股漲用紅、跌用綠，線段越過參考價時換色；虛線是進場價、指數前收，或期貨行情的漲跌比較基準。

K 線讀取只命中 GAS `CacheService` 的逐檔快取，沒有快取不為一次聊天讀整張「日K快取」表，更不額外呼叫富果。市場與產業資料透過 `marketPayload_` 共用單次表格索引，沒有新建爬取排程。這樣 LINE 查詢不會提高行情來源呼叫頻率。圖像是價格趨勢，不含網站完整的交易回合註記、MACD 或 KD；完整互動圖仍連回網站。

## 部署順序（GitHub Pages 更新不會替你部署這兩處）

1. **先部署 Cloudflare Worker**：從 GitHub 取得最新版 `line-webhook/chart.mjs`、`line-webhook/worker.mjs`、**整個 `line-webhook/static/` 圖片目錄**，並保留既有 `wrangler.jsonc`。在已有的 `line-webhook/` 目錄執行 `npx wrangler deploy`（首次會提示安裝 Wrangler）。沿用原 Worker 名稱、Queue、Assets、Secrets；不用重建 LINE Channel。從瀏覽器開 `https://你的-worker網址/healthz`，確認 `build` 為 `2026-09-29-line-chart-v3`。只更新兩個 `.mjs` 卻留下舊 `static/richmenu-notify.png`，健康檢查仍會通過，但 `lineSetupRichMenus()` 會因圖片版本不符而停止。
2. **驗證圖片路由**：開 `https://你的-worker網址/chart.png?ref=100&v=99,100,102`，應顯示帶虛線的小折線 PNG。少於三點的 `v=99,100` 應回 HTTP 400。再檢查 `https://你的-worker網址/static/richmenu-notify.png`：目前通知選單圖片的 SHA-256 應為 `fe8cf11f982bfc1911a9c4a6bc5550d5f8e19863732d80c45abd23599ca8d8dd`。若使用 Cloudflare 管理頁直接上傳單檔程式，這版多了 `chart.mjs` 模組和圖片 Assets，需改由專案目錄執行 Wrangler 部署。
3. **再更新 Apps Script 編輯器**：將 GitHub `public-site/gas-source/` 裡的 `Line.gs`、`Config.gs`、`Setup.gs` 三檔覆蓋到**同一個正式 GAS 專案**。先儲存，執行 `checkProjectFiles()`，應無缺檔；再執行 `lineSetupCheck()`，確認「轉送服務網址」仍是剛才的 Worker 根網址。不要把 `/chart.png` 當 Webhook URL；LINE Developers Webhook 維持 `/callback`。
4. **更新網頁應用程式部署**：Apps Script 右上「部署」→「管理部署作業」→ 找現有正式 Web App → 鉛筆「編輯」→「版本」選「新版本」→「部署」。既有 `/exec` 部署 ID、執行身分與存取權限維持原設定。網站後台版本應讀到 `2026-09-29-line-visual-v85`；若仍是 v84，表示只儲存原始碼、未更新正式部署。
5. **驗卡與實測**：在 GAS 編輯器執行 `lineValidateTemplates()`；它呼叫 LINE 的格式驗證 API，不推送訊息。接著用已加好友的測試帳號查「市場總覽」「持股追蹤」「查個股 代號」「今日整理」。市場有三個同盤價點才會畫；查到持有回合且 `dk2_代號` 有三根以上才會畫進場後線。圖片打不開時先檢查 Worker `/chart.png` 與後台 `relayUrl`；文字卡片仍可查。

若 `lineSetupRichMenus()` 回報 `richmenu-notify.png 不是目前版本`，先比對 Worker 的 `/static/richmenu-notify.png` SHA-256，再同步 `static/`、重新部署 Worker，然後**直接在 GAS 編輯器重跑 `lineSetupRichMenus()`**。這個函式從編輯器執行目前儲存的程式，不必為了重建選單先換 Web App 部署；但 LINE 視覺回覆要生效，正式 `/exec?action=ping` 仍必須在更新 Web App 後顯示 v85。

程式的離線驗收是 `node tests/test_line_v73_gas.js`、`node tests/test_line_free_relay.mjs`、`node tests/test_line_intents_v78.js`。正式 LINE 的圖片顯示、真實資料時間與格式驗證須依上述部署後的測試帳號再確認；GitHub 推送不代表 GAS 與 Worker 已上線。
