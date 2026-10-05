# 聽打第二通道與 Claude 備援（2026/10/05）

GitHub `dc5096e`、`17a2ddb`、`7574534`。只動 `transcript.py`、`pipeline/pipeline.py`、相依套件與 `daily.yml`；網站與 Apps Script 沒有改。

## 起因

10/05 的 `transcript-gemini` 每一把金鑰都回 HTTP 400「Multiple authentication credentials received. Please pass only one.」。10/02 同一份程式、同一版 `google-genai==2.24.0` 都成功。

- 實際送出的請求只有一個 `x-goog-api-key` 標頭（本機攔截確認），不是程式多帶憑證。
- 失敗點是 Interactions API 的「查背景工作結果」（GET `/v1beta/interactions/{id}`）：建立成功、約 11 秒後查詢失敗。
- Google 開發者論壇 10/4 有相同回報（`AQ.` 開頭的金鑰），沒有官方回覆。
- 改用 Interactions 的串流也收不到結果：一次開了 10 分鐘沒有完成，一次 90 秒沒有回應。

## 改動

`transcript.py`

- 第二條通道 `_run_classic`：`generateContent` 串流，同一把金鑰、同一個模型、同一段影片範圍與提示。查背景結果回上面那個 400 時，取消背景工作並改走這條，之後各輪探詢都留在這條（`_CHANNEL`）。
- `TRANSCRIPT_CHANNEL=classic` 可以直接指定第二條通道；空窗上限 `TRANSCRIPT_CLASSIC_READ_TIMEOUT_SEC`（預設 180 秒）。
- 一輪內每一把金鑰都回同一個非額度錯誤時，不再重輪：還沒換過通道就換一次，換過了就結束這次探詢，等下一輪。

`pipeline/pipeline.py`

- `call_gemini` 先跑 Gemini（原本的實作改名 `_call_gemini_only`）。Gemini 整條叫不動（每日額度用完、每一把金鑰都不可用、連續重試失敗）而且有設 `ANTHROPIC_API_KEY` 時，同一份提示交給 Claude。
- 輸出被截斷、失控、空內容不交給 Claude，照舊由呼叫端處理。Claude 也失敗時拋出原本的 Gemini 錯誤。
- Claude 不收影音，聽打用不上。

## 設定

| 名稱 | 種類 | 預設 | 用途 |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Secret | 未設＝不啟用 | Claude 備援的金鑰 |
| `CLAUDE_FALLBACK_MODEL` | Variable | `claude-opus-5-5` | 備援用的模型 |
| `CLAUDE_FALLBACK_EFFORT` | Variable | `medium` | 思考深度 |
| `CLAUDE_FALLBACK_MAX_CALLS` | Variable | `60` | 每一輪最多呼叫幾次（Claude 按量計費） |

## 驗收

- 10/05 11:41 的 `transcript-gemini`（`17a2ddb`）：查背景結果失敗後改走 `generateContent`，金鑰 #1 180 秒沒有回應、換金鑰 #2 後兩段各約 26 秒完成，逐字稿 12,206 字寫入試算表，結束碼 0。前三個交易日是 13,600～14,655 字（片長多 2～6 分鐘）。
- 本機測試 232 項通過，含通道切換 5 項與 Claude 備援 9 項（假回應）。Claude 備援沒有用真金鑰呼叫過。

## 回退

GitHub revert `7574534`（Claude 備援）、`17a2ddb` 與 `dc5096e`（第二通道）。Google 修好之後不必回退：背景查詢正常時仍走原本的通道。

## 10/05 當日稽核（`b28321f`、`a6df5d3`，Apps Script 版本 363＝v121）

管理者問：前台勤誠只寫「資金轉為買進。」、聖暉寫成「賺取6、的持股賣出」，逐字稿真的沒有內容嗎。

查到的原因

- 聖暉：逐字稿有完整說明，說明也用上了；斷句來自價位遮蔽把「賺取6、70塊」的「70塊」當成交易價拿掉。管線、Apps Script、前台三份同一條規則都改：賺賠與漲跌幅度保留，「6、70塊」整組處理。規則的範本在 `scripts/public_narrative_v10.txt`，改 Apps Script 那兩份要改範本再跑 `scripts/sync_quality.py`，否則會被蓋回去。
- 勤誠：講者講了理由，但稿子把股名寫成「行神、情神、程成、情晨」，擷取時列為排除，簡訊那一列拿不到影片內容。同一集「偉穎」（緯穎）、「星KY」（世芯-KY）也被排除，「4916，新科」（事欣科）整段漏掉。
- 根因是聽打詞表只有三檔權值股。`transcript.py` 現在把近兩週「操作紀錄」「會員持股」的股名加進詞表（當天簡訊的買賣在開播前就已寫入）。
- 已確認名稱只加不會出現在一般詞裡的寫法：情晨→勤誠、偉穎→緯穎、星KY→世芯-KY。「程成」（工程成本）、情神、行神不加。

後台更新（逐日編輯，10/05）

- 改說明 8 列：勤誠、聖暉、國巨、友達、台達電、毅嘉、晶心科、達邁。其中三列原本與逐字稿不符：台達電寫成「與 SpaceX 合作蓋廠」（那是台積電）、國巨寫成「空頭形態」（原話是線型像多頭也不代表會漲）、友達把「聽了分析就不會去買」寫反。
- 新增 3 筆（附逐字稿原段）：緯穎 觀望不碰、事欣科 觀望注意、世芯-KY 會員持股。
- 達邁維持觀望注意：講者說「今天一定要買」，但當天會員簡訊只有賣聖暉、買勤誠。
- 沒有收：啟碁、華通（只說沒有推薦，寫進事欣科的對照）、嘉澤（講 1,580 那一段沒有點名）。
