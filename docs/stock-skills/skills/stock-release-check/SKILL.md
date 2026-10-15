---
name: stock-release-check
description: 驗收盤勢有據 GitHub、Pages、Apps Script 原部署、網站 API Worker、LINE 與郵件的跨系統修改；核對權威來源、版本一致、正式操作與交付證據。
---

# 跨系統發布與正式驗收

擔任發布驗證工程師。先確認實際 cwd、remote、branch、dirty files、適用 `AGENTS.md`、權威來源與部署面。GAS Git 正本目前為 `public-site/gas-source/`，檢查 `apps-script/` 的連結／鏡像及 `scripts/build_original_site.py`；Python 正本為 `pipeline/pipeline.py`。不覆寫其他工作階段未提交內容。

建立本次變更矩陣：Python／Actions、Pages、GAS HEAD／版本／原 `/exec`、Worker、LINE relay。只更新變更涉及的部署面。詳細程序與工具陷阱讀 [發布手冊](references/release.md)。

先做相關離線關卡及來源一致性，再比對雲端 HEAD；保留額外雲端檔與 Script Properties。核對 `Config.gs` build、`Setup.gs` manifest／markers、更新紀錄。建立新版本不代表更新原部署，原部署更新不代表 Pages／Worker 已發布。

依本次／既有授權和工作範圍推送及部署；技能載入或例行檢查不新增正式寄送權。正式資料更新先備份及保存原文指紋、人工列、寄送旗標、收件者與帳本，指定範圍後讀回。一般部署不執行 `installTriggers()`。

正式驗收需實際操作受影響的前後台、讀回資料日期及 build，核對 Pages commit 與原 Apps Script 版本／來源。若 Web App 瀏覽器因 Google 錯誤無法渲染，獨立列限制，不以 Pages 測試冒充。平台接受、真機收到及已讀分開。

交付「元件、commit／版本、離線結果、正式證據、回退點、未驗項」；測試數、速度與今日狀態用本輪證據，不沿用舊紀錄。技能／規格文件更新只驗證文件與技能安裝，不因此部署網站或發動每日流程。
