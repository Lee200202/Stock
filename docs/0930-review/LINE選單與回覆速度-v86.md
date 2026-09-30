# LINE 選單與回覆速度：部署及驗收（v86）

## 改了什麼

- 圖文選單「查資料」保留唯一的「今日整理」。通知頁改為「管理訂閱」「使用說明」及橫跨下排的「開啟網站」，圖片和 LINE 點擊熱區一致，沒有空白熱區或隱藏的重複功能。
- Worker 收到訊息後，以背景請求啟動 LINE 載入提示。GAS 不再同步重複呼叫該 API，先處理正式答覆。
- 查個股時，已發布紀錄先回覆；只有既有持股快取可用才附加持股走勢字卡，不為附圖臨時重算全體持股及行情。直接點「持股追蹤」仍照原功能取得清單。
- LINE 事件帳本的「處理結果」會記錄去重、查詢、LINE 回覆 API 各花多久。新版 Worker 另記入列到 GAS 的轉送時間，後台 LINE 分頁顯示最近一則互動的分段耗時。這個數字尚未包含手機網路與 LINE 客戶端顯示時間。

## 更新檔案

| 位置 | 用途 |
| --- | --- |
| `line-webhook/worker.mjs` | 新增入列時間；`/healthz` build 改為 `2026-09-30-line-response-v4`。 |
| `line-webhook/static/richmenu-notify.png` | 新版三入口圖片，SHA-256：`8fec54c4e64d7968f87f9e8d03402fba54e787dda5e2e658049edc1ed7bbc802`。 |
| `scripts/make_line_richmenu.py` | 圖片產生原始程式；日後要換字或位置，需和 `Line.gs` 熱區一起改。 |
| `public-site/gas-source/Line.gs` | 選單熱區、答覆路徑、耗時紀錄。 |
| `public-site/gas-source/Admin.html` | 後台耗時卡與三入口檢查文案。 |
| `public-site/gas-source/Config.gs`、`Setup.gs` | GAS 版本一致性，build 為 `2026-09-30-line-response-v86`。 |

`C:\Users\user\Downloads\zhangzhen-stock-site-updated\apps-script` 中的上述四個 GAS 檔已同步；GitHub 內的正本在 `public-site/gas-source/`。不要把 Worker 的 `.mjs` 貼進 Apps Script。

## 部署順序

1. **Worker**：在本機 `line-webhook` 目錄保留原來的 `wrangler.jsonc` 與 Cloudflare Secrets，同步 GitHub 的 `worker.mjs` 和 `static/richmenu-notify.png`，執行 `npx wrangler deploy`。不用重新建立 Queue、LINE Channel 或重貼密鑰。開 `https://zhangzhen-line-relay.rainforecast2026-6fb.workers.dev/healthz`，確認 build 是 `2026-09-30-line-response-v4`、`configured` 與 `loadingConfigured` 都是 `true`。再開 `/static/richmenu-notify.png`，確認只有三個入口；若環境是本機終端，可用 `Get-FileHash .\static\richmenu-notify.png -Algorithm SHA256` 核對上表。
2. **Apps Script 原始碼**：開現有專案，更新 `Line.gs`、`Admin.html`、`Config.gs`、`Setup.gs` 四個檔，儲存。先在編輯器執行 `checkProjectFiles()`，確認 0 個問題，再執行 `lineValidateTemplates()`。不要新建專案或替換其他較新的檔案。
3. **GAS 正式 Web App**：右上「部署」→「管理部署作業」→ 找原來 `/exec` 的網頁應用程式 → 鉛筆「編輯」→「版本」選「新版本」→「部署」。沿用原部署 ID、執行身分及原存取設定。開正式 `/exec?action=ping`，確認 build 為 `2026-09-30-line-response-v86`，否則 LINE Worker 仍會轉給舊版。
4. **LINE 圖文選單**：在 GAS 編輯器執行 `lineSetupRichMenus()`；或在正式後台 LINE 分頁按「建立圖文選單」。此步會更新 LINE 選單別名，不會向好友推送訊息。後台重新整理後，通知選單應為 `zz-notify-v3`。如果顯示「richmenu-notify.png 不是目前版本」，先回第 1 步確認 Worker 的圖片，勿修改 GAS 內的 SHA-256 來跳過檢查。
5. **手機驗收**：重開 LINE 官方帳號聊天室，必要時將選單收合再展開。切換兩頁：查資料只有一個「今日整理」，通知頁三個入口各點一次，確認管理訂閱、使用說明與網站連結正確。
6. **速度驗收**：使用已加好友的測試帳號，在手機分別傳「今日整理」「2330」「持股追蹤」，每則從按送出到卡片出現手動計時。後台 LINE 分頁按「重新整理」，看「互動回覆耗時」：綠色低於 8 秒、黃色 8–15 秒、紅色至少 15 秒。若卡片顯示「Worker 轉送」偏高，查 Cloudflare Queue 消費與重試；「GAS 查詢」偏高，查試算表或持股快取；「LINE API」偏高，查 LINE API 回應。事件帳本保留每則的詳細毫秒數。此指標是最近一則，不可當作全部使用者的平均值。

## 驗證與限制

離線執行通過 `node tests/test_line_v73_gas.js`、`node tests/test_line_free_relay.mjs`、`node tests/test_line_intents_v78.js`、後台及原有持股相關回歸。離線測試不會產生真實 LINE 回覆時間；若上線後仍超過 15 秒，應以新增的分段耗時定位，再決定是否需要另做已發布個股紀錄索引。切勿為縮短時間而把未完成或舊快取內容稱為當日資料。
