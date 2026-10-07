/** 純常數與版本；避免初始化時呼叫其他模組。 */

// 正式紀錄起點；回述的買入成本日期不得讓績效曲線向前延伸。
var PERFORMANCE_START_DATE = '2026/07/08';

/* 站名（v103，2026/10/02 管理者）：「張震 股市盤中家教班」是節目方的名稱，本站是第三方整理，不拿它當站名。
   「盤勢有據」取自網站的做法——每一筆紀錄都對得回節目原話，沒講到的標示未說明；LINE 官方帳號也用這個名字。
   初衷是「投資，是為了讓家人過更好的生活」：要對家人負責，就不能靠印象與轉述，所以逐日留下有據可查的紀錄。
   網站標題、頂列、信件品牌列與主旨、LINE 歡迎詞都讀 SITE_NAME；節目名稱只出現在「資料來源」的說明（SOURCE_PROGRAM、DISCLAIMER）
   與判讀提示詞裡。要改站名改這一行，並把 APP_TITLE 與 Index.html 頂列一起改（測試會核對三處相同）。 */
var SITE_NAME = '盤勢有據';
// 主標語（首屏大字）與短標語（分頁標題、頂列、信件品牌列）。站名保持四個字，主旨與分頁標題才不會太長。
var SITE_SLOGAN = '投資，是為了讓家人過更好的生活';
var SITE_TAGLINE = '為家人投資，逐日有據';
var SOURCE_PROGRAM = '張震 股市盤中家教班';
var APP_TITLE = '盤勢有據　為家人投資，逐日有據';

var DISCLAIMER = '本站為第三方整理，依據 YouTube 節目「張震 股市盤中家教班」的公開影片，與節目及製作單位沒有隸屬或合作關係。內容不構成任何投資建議；投資決策與盈虧請自行負責。';

// 網站的正式網址（網頁應用程式部署的 /exec），信件裡的退訂連結與網站連結用它。
// 管理者 2026/09/16 提供。排程寄信時 ScriptApp.getService().getUrl() 只拿得到 /dev，
// 別人點了打不開，所以要有一份寫死的正式網址。
// 優先順序見 MailService.gs 的 publicWebAppUrl_：指令碼屬性 WEBAPP_URL → 這裡 → 當下網址。
var WEBAPP_URL_DEFAULT = 'https://script.google.com/macros/s/AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw/exec';

// 對外瀏覽入口放在原版面 GitHub Pages；退訂的 token 驗證仍由 GAS 後端執行。
// 先前把所有信件連到 /exec，部分多帳號瀏覽器會先被 Google Drive 擋掉。
var PUBLIC_SITE_URL_DEFAULT = 'https://lee200202.github.io/Stock/';

// FinMind token 只存在指令碼屬性；來源檔不得帶金鑰
var FINMIND_API_TOKEN_DEFAULT = '';

// 版本標記。每次改動 doGet 的對外行為就要跟著更新，
// 呼叫端用它確認部署的是不是預期的版本。
var GAS_BUILD = '2026-10-07-round-menu-v144';

// 簡訊就緒查詢開關；日K與逐字稿刷新已解耦，簡訊優先由來源列保護及同步重建維持。
var SMS_PRIORITY_GUARD = true;

// 這個版本支援的能力。呼叫端據此決定怎麼呼叫，
// 而不是打了才發現對方不認得參數。
var GAS_FEATURES = ['battle-v128', 'public-wording-v127', 'transcript-readiness-v126', 'trigger-budget-v125', 'admin-sms-layout-v120', 'admin-sms-raw-v119', 'sms-sender-v118', 'private-sms-v117', 'admin-stack-v116', 'admin-layout-v115', 'admin-exited-v114', 'tracker-clean-v113', 'tracker-cards-v112', 'visual-motion-v111', 'line-sms-consent-v110', 'tech-story-v108', 'site-review-v107', 'no-stated-price-v104', 'site-name-v103', 'dailyk-gap-v102', 'roomy-pills-v101', 'transcript-flow-v100', 'manual-note-guard-v99', 'public-notes-v98', 'unnamed-price-v97', 'stock-context-v96', 'transcript-headers-v95', 'read-registry-v94', 'live-quotes-v93', 'mail-spacing-v92', 'workflow-evidence-v91', 'official-dailyk-v90', 'name-price-ui-v89', 'read-batch-layout-v88', 'delivery-fix-v87', 'line-visual-v85', 'line-menu-audit-v84', 'layout-runtime-v83', 'performance-count-basis-v82', 'manual-hold-confirm-v81', 'line-gate-handoff-v81', 'held-source-link-v81', 'admin-automation-health-v79', 'line-loading-v79', 'pages-shared-read-v79', 'line-explicit-sms-v78', 'line-sector-history-v78', 'daily-only-v77', 'line-intent-v77', 'sms-two-source-v77', 'pages-links-v76', 'pages-unsubscribe-v76', 'line-v73', 'tech-lab-v72', 'tech-graph-v71', 'tech-story-v70', 'mobile-tables-v69', 'admin-account-topbar-v69', 'tracker-low-high-v68', 'trend-span-v68', 'k-range-pill-v68', 'ops-runtime-v67', 'holiday-trigger-skip-v67', 'ops-day-switch-v67', 'k-panel-seg-v67', 'trend-since-entry-v67', 'wheel-page-scroll-v67', 'calendar-model-monthly-v66', 'assistant-confirm-no-video-v65', 'tracker-cache-v62', 'recent-k-gap-price-v61', 'day-sync-cancel-v60', 'tracker-cache-limit-v59', 'tracker-range-price-v58', 'live-layout-v58', 'ops-exit-v57', 'no-show-v56', 'ops-monitor-v55', 'mobile-spacing-v55', 'admin-redesign-v54', 'delivery-ledger-v54', 'unsubscribe-confirm-v54', 'roomy-grid-v54', 'finmind-dailyk-v51', 'dailyk-debug-v51', 'sms-priority-guard-v51', 'dailyk-sms-decoupled-v52', 'tx-real-snapshots-v52', 'pipeline-progress-v52', 'transcript-watchdog-v50', 'persistent-progress-v50', 'performance-floor-v48', 'day-edit-actions-v48', 'hourly-history-v46','month-k-v46','transcript-handoff-v45','crosshair-sync-v44', 'mail-risk-footer-v44', 'title-min15-v44', 'tx-layout-breaks-v44', 'tx-layout-autofill-v44', 'watch-reversal-tone-v43', 'kline-fill-zoom-v42', 'macd-from-first-bar-v42', 'kline-taller-v42', 'kline-cache-putall-v41', 'kline-bundle-v41', 'kline-short-history-v41', 'macd-sma-seed-v41', 'dailyk-volume-repair-disabled-v41', 'dailyk-paced-v40', 'dailyk-1645-chain-v40', 'dailyk-audit-v39', 'dailyk-repair-v39', 'kline-full-range-v38', 'volume-pane-v38', 'hourly-kline-v38', 'volume-units-verified-v38', 'config-guard-v37', 'project-file-check-v37', 'subscription-mail-design-v36', 'tab-deeplink-v36', 'unsubscribe-exec-url-v35', 'no-watch-alert-v35', 'instant-mail-design-v35', 'tech-toc-flatten-v34', 'tech-subnav-drawer-v33', 'sms-revision-afterhours-v32', 'dividend-not-price-v32', 'holding-subject-v31', 'sound-roster-v31', 'tech-keypoints-v30', 'tx-layout-v29', 'tech-fixed-footer-v29', 'tech-chunks-v28', 'doc-nav-v27', 'mail-darkmode-v26', 'reason-no-rationale-v25', 'test-mail-v25', 'sms-audit-range-v24', 'admin-cal-loading-v23', 'article-sections-v22', 'mail-reading-settings-v22', 'mail-layout-v21', 'title-no-prefix-v21', 'daily-name-wrap-v21', 'cmoney-auto-reconcile-v20', 'title-no-length-limit-v19', 'cmoney-diagnose-v19', 'watch-classify-v18', 'title-168-style-v17', 'narrative-no-asterisk-v17', 'watch-classify-v17', 'dailyk-resume-v15', 'fugle-range-split-v15', 'watch-tone-v15', 'tracker-pricing-v14', 'tracker-rounds-v13', 'refresh-transient-resume', 'transcript-display-punctuation', 'gemini-503-key-switch', 'transcript-job-hash', 'auto-dailyk-resume', 'transcript-merge-preview', 'context-policy-v4', 'refresh-all-resumable', 'perf-history-pending', 'evidence-v2', 'evidence-v1', 'day-edit-sync-v1', 'ping', 'refresh-step', 'admin-page', 'quality-gate', 'full-fix',
                    'chain-state', 'chain-cancel', 'refresh-from', 'fullfix-state',
                    'manual-entry', 'review-rename', 'avoid-provisional', 'autofill-dailyk',
                    'day-edit', 'fullfix-scope',
                    'when-prev', 'trade-seq', 'reason-rewrite', 'shared-settings',
                    'ghost-guard', 'push-diagnostics', 'fine-progress', 'sms-parse', 'sms-detail', 'sms-manage',
                    'sms-checkpoint', 'sms-date-repair', 'natural-reasons', 'sms-sync-progress',
                    'sms-item-verifier', 'sms-direction-column', 'dailyk-safe-chunks',
                    /* 新版會員簡訊：範圍分流（當天／過去空白）、解析版本戳記、
                       配額熔斷與冷卻、逐步落地寫入、逐日稽核後的十三格收錄流程。 */
                    'sms-scope-v6', 'sms-version-stamp', 'sms-quota-cooldown',
                    'sms-incremental-write', 'sms-merge-chunks',
                    /* 資料修正的小工單：同步執行也回報段落進度，可看進度、可取消。 */
                    'fix-job-progress', 'fix-job-cancel',
                    /* 判定歷程與名稱判定紀錄：稽核每一輪做了哪些決定，後台查得到。 */
                    'decision-log', 'name-memo',
                    /* 續跑改成重新派工給 GitHub（先前排的是已退役的觸發器那條路）。 */
                    'resume-via-github', 'job-watchdog', 'step-index-fallback', 'legacy-runner-retired',
                    /* 訂閱管理直接做在頁面上：輸入信箱就列出目前訂了什麼，
                       逐項勾選後儲存，不寄信也不另開頁面。 */
                    'subscription-inline-manage',
                    /* 會員簡訊優先：簡訊的列不交給逐字稿複審，
                       否則複審會因為「逐字稿裡找不到」而把正確的紀錄刪掉。 */
                    'sms-priority-exempt-review',
                    /* 績效歷史可重算，且資料寫入後會自動觸發輕量刷新。 */
                    'perf-history-rebuild', 'auto-refresh-after-write', 'day-edit-auto-sync-v47', 'pages-original-ui-v75'];

var REFRESH_ORDER_ = ['purge', 'gate1', 'gate2', 'gate3',
                      'codes', 'fund', 'dailyk', 'tracker', 'perf'];

var CHAIN_KEY_ = 'refreshChainState';
