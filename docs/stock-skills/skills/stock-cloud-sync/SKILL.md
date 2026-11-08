---
name: stock-cloud-sync
description: 從只有 Claude／GitHub 帳號、沒有本機檔案的電腦操作盤勢有據的雲端部分（claude.ai 雲端工作階段、兩個雲端排程、雲端環境、GitHub 網頁與 Actions、複核紀錄、網站後台），以及回到本機時把雲端產出（排程推的分支、Actions 結果、複核紀錄、待管理者問卷、後台手改）對齊本機；含雲端排程 ↔ GitHub ↔ 網站銜接的驗收清單。排程內部判讀用 stock-content-audit，發布用 stock-release-check。
---

# 雲端與本機銜接

擔任雲端銜接管理員。盤勢有據的資料有四個家，各自只有一份正本：

| 正本 | 在哪裡 | 誰會改 |
|---|---|---|
| 程式、裁示、答案檔、技能 | GitHub `Lee200202/Stock` main | 本機或雲端工作階段推分支 → 合併 |
| 網站資料（每日列、說明、章節、績效） | Google 試算表，經 Apps Script 給網站 | 主流程、`daily-review-apply`、單日更新、後台手改 |
| 複核紀錄（每日摘要、差異明細、每週檢討） | 複核紀錄試算表 | 雲端排程的 `write_log.py`、工作流程的 `mark_fixed.py` |
| 雲端排程、雲端環境 | claude.ai 帳號（不跟電腦走） | 管理者在 claude.ai，或 Claude Code 的 RemoteTrigger |

記憶與本機技能在私人庫 `stock-private-sync`，只是輔助；雲端排程只讀得到公開 repo，裁示一定要寫進 repo。

## 開始時判斷是哪一種工作

1. **只有帳號的電腦要操作或下裁示**：讀 [銜接手冊](references/cloud-sync.md) 第 1–2 節。能做什麼、要去哪個網頁、哪些是管理者自己按的步驟都在表裡。
2. **回到本機要把雲端的變動接上**：照第 3 節開工清單；先跑 `python scripts/cloud_sync_status.py` 看雲端推過哪些分支、Actions 結果，再決定要不要 pull、合併或補裁示。
3. **驗收雲端排程 ↔ GitHub ↔ 網站是否接得上**（換帳號、換環境、換金鑰之後）：照第 4 節逐項做，每一項都要有證據；週末跑每日排程只驗得到第 0 步。

## 硬規則

- 雲端排程只有一份，在目前的訂閱帳號上；建立前一定先 `list`，兩個工作階段可能同時在做。
- 金鑰、試算表 ID、管理密鑰：不讀、不印、不貼進對話或 repo；只核對變數名稱與格式。環境設定由管理者自己存。
- 台北 13:00 前不手動跑每日排程（`--hold` 會讓郵件等）；要驗金鑰用過去的交易日、不加 `--hold`。
- 正式寫入（單日 apply、後台維護按鈕、刪金鑰）是管理者的步驟或需要對那一項的明確同意；技能被選用不代表多了任何寫入權。授權細節見 `stock-collaboration`。
- 推送測試用不觸發工作流程的分支名（例如 `claude/push-probe-<日期>`），不要用 `daily-review/apply-*`、`claude/daily-review-apply-*`、`claude/pipeline-weekly-*`。

結案分列：雲端排程狀態（`enabled`、`next_run_at`、最近執行）、環境（變數名稱、網路）、GitHub（分支、Actions run 編號與結論）、網站（讀回或刷新證據）、本機（pull 到哪個 commit、記憶是否已同步）、未驗項。
