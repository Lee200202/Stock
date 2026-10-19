---
name: stock-release-check
description: 驗收盤勢有據 GitHub、Pages、Apps Script 原部署、網站 API Worker、LINE 與郵件的跨系統修改；核對權威來源、版本一致、正式操作與交付證據。
---

# 跨系統發布與正式驗收

擔任發布驗證工程師。先確認實際 cwd、remote、branch、dirty files、適用 `AGENTS.md`、權威來源與部署面。GAS Git 正本目前為 `public-site/gas-source/`，檢查 `apps-script/` 的連結／鏡像及 `scripts/build_original_site.py`；Python 正本為 `pipeline/pipeline.py`。不覆寫其他工作階段未提交內容。

建立本次變更矩陣：Python／Actions、Pages、GAS HEAD／版本／原 `/exec`、Worker、LINE relay。只更新變更涉及的部署面。詳細程序與工具陷阱讀 [發布手冊](references/release.md)；Claude Code 端的 clasp 步驟、編輯器檔名對應、擁有者才能移 `/exec`、沒有 gh CLI、heredoc 與 CRLF 陷阱、後台在巢狀 iframe 只能截圖操作，讀第 8 節。誰能授權哪個動作見 `stock-collaboration` 第 2 節。正式資料寫入時 GitHub 單日更新與 Apps Script 定時工作（寄信前品質關卡、補代號、複審）互相蓋寫的風險、套用後怎麼讀回（代號型別、持股回合）讀第 9 節；Node 測試的既有失敗基準、Windows 編碼、等待與用量讀第 10 節。

先做相關離線關卡及來源一致性，再比對雲端 HEAD；保留額外雲端檔與 Script Properties。核對 `Config.gs` build、`Setup.gs` manifest／markers、更新紀錄。建立新版本不代表更新原部署，原部署更新不代表 Pages／Worker 已發布。

依本次／既有授權和工作範圍推送及部署；技能載入或例行檢查不新增正式寄送權。正式資料更新先備份及保存原文指紋、人工列、寄送旗標、收件者與帳本，指定範圍後讀回。一般部署不執行 `installTriggers()`。

正式驗收需實際操作受影響的前後台、讀回資料日期及 build，核對 Pages commit 與原 Apps Script 版本／來源。若 Web App 瀏覽器因 Google 錯誤無法渲染，獨立列限制，不以 Pages 測試冒充。平台接受、真機收到及已讀分開。

推送 Apps Script 時用已提交版本的 worktree 當來源，只複製本輪檔案，推送後重新 pull 確認 `differs: []`；`/exec` 的 ping 只回 `GAS_BUILD` 字串，沒改這個字串時不能據此確認版本號。

交付「元件、commit／版本、離線結果、正式證據、回退點、未驗項」；測試數、速度與今日狀態用本輪證據，不沿用舊紀錄。技能／規格文件更新只驗證文件與技能安裝，不因此部署網站或發動每日流程。

宣稱 Prompt／技能更好前，查 `stock-rule-tuning/references/evaluation.md`：A/B 是否相同來源與部位快照、是否注入人工答案、快取是否隔離、失敗及重試是否計入、是否有未見樣本。品質、速度與成本分列，不能用加權總分抵銷虛構交易、跨股證據或越權。離線合成資料只能驗證評估工具；不得寫成真實模型提升或統計顯著。新規範已安裝與新流程已實測／發布要分開報告。
