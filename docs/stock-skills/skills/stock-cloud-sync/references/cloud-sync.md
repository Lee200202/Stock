# 雲端與本機銜接手冊（2026/11/08）

本檔在公開 repo：不寫金鑰、密碼、試算表 ID、信箱。設定值的位置見 `docs/雲端排程/雲端排程總覽.md` 第二節；
新電腦從零安裝見 `docs/交接/新電腦接手指南.md`。

## 1. 自動化怎麼接起來（一天的資料流）

```text
主流程 daily.yml（GitHub，11:09 起）── 寫試算表 ── Apps Script 刷新網站
        │
雲端排程「每日寄信前複核」（claude.ai，11:40）
  ├─ 讀：fetch_day.py --hold（服務帳戶金鑰 → 試算表；把當天設成「複核中」，郵件最晚等到 13:00）
  ├─ 寫：write_log.py → 複核紀錄「每日摘要」「差異明細」
  └─ 有明確錯誤：推 daily-review/apply-<今天>（被拒就推 claude/daily-review-apply-<今天>）
        └─ GitHub daily-review-apply.yml：預覽 → 套用 → 讀回 → Apps Script 刷新 → mark_fixed（GitHub Secret 的金鑰）
12:30 Apps Script 把複核結果併進「爬取狀態」信
雲端排程「每週流程檢討」（週五 17:00）→ 推 claude/pipeline-weekly-<週五> → pipeline-weekly.yml 守門、重播、全過才合併 main
```

- 同一把服務帳戶金鑰存在兩個地方：claude.ai 雲端環境（排程讀寫試算表）與 GitHub Secret（11 個工作流程）。換金鑰要兩邊都換，再刪舊的。
- 雲端排程推分支用的是 claude.ai 帳號上的 GitHub 連線（授權 GitHub 使用者 `Lee200202`），不是本機的 git 憑證。
- 網站資料不在 git：本機沒有「網站內容」可以 pull。要對齊的是 git（程式、裁示、計畫檔）、複核紀錄、雲端排程狀態。

## 2. 只有帳號、沒有本機檔案的電腦能做什麼

前提：瀏覽器登入 claude.ai（訂閱帳號）與 GitHub（`Lee200202`）。不用裝任何東西。

| 要做的事 | 去哪裡 | 怎麼做 | 誰按 |
|---|---|---|---|
| 看今天複核結果、待管理者項目 | 複核紀錄試算表「每日摘要」「差異明細」；12:30 狀態信 | 直接讀 | 管理者 |
| 手機上看稽核結果、下裁示 | LINE 官方帳號：綁定一次後輸入「管理者登入」 | 每天複核完成會收到「今日稽核結果」「待你裁示」；輸入「〔日期〕〔代號〕分類—說明」並按確認送出，雲端改網站後回報（`docs/雲端排程/雲端排程總覽.md` 第六之一節） | 管理者 |
| 看排程有沒有跑、跑到哪 | claude.ai/code/routines → 排程 → 執行紀錄 | 點進那次執行看對話 | 任何人 |
| 看每週問卷（待管理者裁示） | GitHub 網頁 `docs/weekly-review/<週五>/報告.md` 第 5 節；複核紀錄「每週檢討」 | 直接讀 | 管理者 |
| 下裁示、改規則文件、改技能 | claude.ai/code 開**雲端工作階段**：repo `Lee200202/Stock`、環境 Default | 用中文說要寫進哪份裁示（`docs/daily-review/裁示紀錄.md`、`docs/分類裁決基準.md` 或負責的技能）；它推 `claude/` 分支 | 雲端工作階段推分支；**合併 main 由管理者在 GitHub 網頁開 PR → Merge** |
| 修正**今天**網站上的明確錯誤 | 同上，雲端工作階段 | 照 `docs/daily-review/每日複核作業.md` 第 6 步寫計畫檔，推 `claude/daily-review-apply-<今天>`；工作流程會套用、讀回、刷新 | 只能修當天（工作流程擋其他日期） |
| 重做**某一天**（歷史日） | GitHub 網頁 → Actions →「單日品質核對與更新」→ Run workflow | 先 `mode=preview`，看結果再 `mode=apply`；要有該日答案檔 | 管理者自己按 apply |
| 手改一列或一句說明 | 網站後台 `?page=admin`（管理者登入） | 改完會變成人工列，之後的單日重建會保留它 | 管理者 |
| 手動觸發任一工作流程 | GitHub 網頁 → Actions → 選工作流程 → Run workflow | 正式寫入類（apply、維護）先確認是要的那一天 | 管理者 |
| 改排程時間、提示詞、啟用／停用 | claude.ai/code/routines → 排程 → 編輯 | 改完回 `docs/雲端排程/雲端排程總覽.md` 同步（開雲端工作階段改文件） | 管理者 |
| 改雲端環境（變數、網路） | claude.ai/code → 左下「☁」→ Default 旁齒輪 | 金鑰、ID 由管理者自己貼；Claude 被擋改設定是正常的 | 管理者 |

雲端工作階段的限制（和本機不同）：

- 沒有本機記憶（`MEMORY.md`）與 `~/.claude/skills`；它讀得到的是 repo：`AGENTS.md` → `docs/stock-skills/skills/stock-collaboration/SKILL.md` → 本技能。要它記住的事一律寫進 repo。
- 推送：2026/11/08 實測雲端排程推 `claude/…` 和非 `claude/` 開頭的分支都可以；仍一律不推 main、不合併（合併在 GitHub 網頁做）。
- 沒有 GitHub API 憑證：不能觸發或取消 Actions、讀不到需要登入的 log；這些在 GitHub 網頁做。
- 有雲端環境的服務帳戶金鑰：可以跑 `fetch_day.py --date <過去交易日>`（不加 `--hold`）讀資料；不可印出金鑰或原稿以外的會員資料，不把 `.daily-review/` 提交進 git。
- 雲端排程只有一份。不要在雲端工作階段裡再建排程；建立、更新排程用 claude.ai 的排程頁或本機 Claude Code 的 RemoteTrigger，建立前先列出現有的。

## 3. 回到本機：開工清單

1. 記憶：`powershell -ExecutionPolicy Bypass -File "$HOME\stock-private-sync\sync.ps1"`（先拉另一台的，再推這台的）。
2. 雲端變動總覽：`python scripts/cloud_sync_status.py --days 14`（唯讀，會先 `git fetch --prune`）。看：
   - **本機**：目前分支、未提交檔、落後 main 幾個。有別人（Codex、其他工作階段）的未提交改動就不要切分支、stash 或 reset；另開 `git worktree add ../Stock-<用途> origin/main`。
   - **每週分支**：「未併入」→ 看同一列的 `pipeline-weekly` 結論；`failure` 多半是守門擋下（重播或線上驗收沒過，不是接線壞了），讀 log 的「結論」段與複核紀錄「每週檢討」。
   - **每日修正分支**：本來就不併入 main，只看 `daily-review-apply` 那次是不是 success。
   - **其他 `claude/` 分支**：雲端工作階段推的裁示或文件修改；確認管理者是否已在 GitHub 合併，沒合併就問。
3. 更新本機：在乾淨的 worktree 或自己的分支 `git pull --ff-only origin main`；不要在別人的工作目錄 pull。
4. 雲端排程狀態：Claude Code 裡 `RemoteTrigger list`，再對兩個排程 `get`（`enabled`、`next_run_at`、`last_run`），有問題才 `list_runs` → `get_run_log`。
5. 待管理者：每週報告第 5 節的問卷、複核紀錄「待管理者」列。管理者在雲端或本機回覆的裁示，要寫進 repo 對應文件才算數。
6. 收工：推自己的分支；再跑一次 `sync.ps1`。

## 4. 銜接驗收清單（換帳號、換環境、換金鑰之後）

每一項都要有證據（run 編號、執行紀錄、讀回結果）。2026/11/08 新帳號重建時全部做過一次，結果列在右欄。

| 項目 | 怎麼驗（唯讀或可撤回） | 2026/11/08 結果 |
|---|---|---|
| 帳號 | 瀏覽器 `claude.ai/api/account` 的信箱；排程列表看得到兩個排程 | 新訂閱帳號，兩個排程在 |
| 排程設定 | `RemoteTrigger get`：`enabled` true、cron、模型、`mcp_connections` 空、`environment_id` | 兩個都啟用；每週的連接器已清空 |
| 下次執行 | `next_run_at`：下一交易日 03:40 UTC、下一個週五 09:00 UTC（伺服器會錯開幾分鐘） | 11/09 03:40、11/13 09:06 |
| 環境變數 | 齒輪 → 環境變數，只看名稱：`GOOGLE_SHEETS_SERVICE_ACCOUNT`、`SPREADSHEET_ID`、`REVIEW_LOG_ID`、`GEMINI_API_KEY` | 4 個都在，金鑰為一行 Base64 |
| 雲端讀試算表 | 一次性排程（`run_once_at`、只給 Bash）跑 `fetch_day.py --date <上一個交易日>`，**不加 `--hold`**，只回報 READY 那行 | READY，和 GitHub 端一致 |
| 雲端推分支 | 一次性排程推空提交到不觸發工作流程的分支（`claude/push-probe-<日期>`、`daily-probe/<日期>`），確認沒有觸發 Actions，驗完刪分支 | 兩個名稱都推成功、沒有觸發工作流程，已刪 |
| GitHub 讀試算表 | Actions「daily-review-check」：`date`=上一交易日、`hold`=no，看 READY | READY（run 42475536493） |
| GitHub 呼叫 Apps Script | Actions「day-edit-sync」`mode=queue`：只問後端工單狀態，印「完成」 | 完成（run 42457042739） |
| 套用並刷新網站 | 最近一次 `daily-review-apply` log：「正式列逐欄讀回一致」「重算結束：成功 N 步，失敗 0 步」「網站已是最新內容」 | 11/06 兩輪都成功 |
| 每日排程本身 | 13:00 後手動 `run`；平日走完取輸入包 → 讀裁示 → 凍結判讀 → 比對 → 寫紀錄；週末只會停在 NO_TRADING_DAY | 週日只驗到第 0 步（三個變數都在） |

不要做的驗收：13:00 前手動跑每日排程（郵件會等）；推 `daily-review/apply-*` 測試（工作流程會把當天標成「修正失敗」）；為了測試改網站正式內容再改回來（會員可能在中間看到，且單日 apply 只收當天）。

## 5. 已知陷阱

- **週末跑每日排程只驗第 0 步**：`fetch_day.py` 先查日曆，非交易日在連試算表前就結束，金鑰沒被用到。
- **身分**：`~/.claude.json` 的 `oauthAccount` 可能是舊快取；看 claude.ai 的帳號頁或 `$CLAUDE_CODE_USER_EMAIL`。工作階段的登入在它啟動時就固定，換帳號後要開新工作階段。
- **重複排程**：兩個工作階段同時重建會各建一份；`create` 前立刻再 `list` 一次。多的那份先停用、改名「（重複，請刪除）」，由管理者在網頁刪（API 不能刪）。
- **一次性排程**：`run_once_at` 跑完自動停用（`ended_reason: run_once_fired`），仍留在列表，管理者可刪。
- **網路**：Default 環境顯示「受限 · 無網域」時仍連得到 Google 試算表需要的主機（實測），不一定要加允許清單；連不到才加 `sheets.googleapis.com`、`oauth2.googleapis.com`、`www.googleapis.com`。
- **換金鑰順序**：GCP 產生新金鑰 → 更新 GitHub Secret → 更新雲端環境 → 兩邊都驗 READY → 才刪舊金鑰。只換一邊、先刪舊的，隔天主流程或排程就讀不到試算表。
- **每週 `failure` 不等於壞掉**：守門沒過時工作流程以失敗結束、不合併，但紀錄照寫；看 log 的「結論」段。
- **Claude 改不了的設定**：雲端環境、排程刪除、Actions 的正式 apply，被權限擋下是預期行為；說明要管理者按哪裡，不繞過。

| 版本 | 日期 | 內容 |
|---|---|---|
| 2 | 2026/11/08 | 加 LINE 管理者模式與第三個排程「管理者裁示」（API 觸發）、`line-ruling-apply.yml` |
| 1 | 2026/11/08 | 初版：新帳號重建排程後的銜接驗收、只有帳號的電腦操作表、回本機開工清單、`scripts/cloud_sync_status.py` |
