/** 純常數與版本；避免初始化時呼叫其他模組。 */

// 正式紀錄起點；回述的買入成本日期不得讓績效曲線向前延伸。
var PERFORMANCE_START_DATE = '2026/07/08';

var APP_TITLE = '張震股市盤中家教班　逐日追蹤';

var DISCLAIMER = '本站內容僅整理影片中明確講述的內容，不構成任何投資建議，投資決策與盈虧由使用者自行負責。';

// 網站的正式網址（網頁應用程式部署的 /exec），信件裡的退訂連結與網站連結用它。
// 管理者 2026/09/16 提供。排程寄信時 ScriptApp.getService().getUrl() 只拿得到 /dev，
// 別人點了打不開，所以要有一份寫死的正式網址。
// 優先順序見 MailService.gs 的 publicWebAppUrl_：指令碼屬性 WEBAPP_URL → 這裡 → 當下網址。
var WEBAPP_URL_DEFAULT = 'https://script.google.com/macros/s/AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw/exec';

// FinMind token 只存在指令碼屬性；來源檔不得帶金鑰
var FINMIND_API_TOKEN_DEFAULT = '';

// 版本標記。每次改動 doGet 的對外行為就要跟著更新，
// 呼叫端用它確認部署的是不是預期的版本。
var GAS_BUILD = '2026-09-24-quality-v55';

// 簡訊就緒查詢開關；日K與逐字稿刷新已解耦，簡訊優先由來源列保護及同步重建維持。
var SMS_PRIORITY_GUARD = true;

// 這個版本支援的能力。呼叫端據此決定怎麼呼叫，
// 而不是打了才發現對方不認得參數。
var GAS_FEATURES = ['ops-monitor-v55', 'mobile-spacing-v55', 'admin-redesign-v54', 'delivery-ledger-v54', 'unsubscribe-confirm-v54', 'roomy-grid-v54', 'finmind-dailyk-v51', 'dailyk-debug-v51', 'sms-priority-guard-v51', 'dailyk-sms-decoupled-v52', 'tx-real-snapshots-v52', 'pipeline-progress-v52', 'transcript-watchdog-v50', 'persistent-progress-v50', 'market-overlay-v50', 'market-five-minute-v50', 'performance-floor-v48', 'day-edit-actions-v48', 'market-overview-v46','hourly-history-v46','month-k-v46','transcript-handoff-v45','crosshair-sync-v44', 'mail-risk-footer-v44', 'title-min15-v44', 'tx-layout-breaks-v44', 'tx-layout-autofill-v44', 'watch-reversal-tone-v43', 'kline-fill-zoom-v42', 'macd-from-first-bar-v42', 'kline-taller-v42', 'kline-cache-putall-v41', 'kline-bundle-v41', 'kline-short-history-v41', 'macd-sma-seed-v41', 'dailyk-volume-repair-disabled-v41', 'dailyk-paced-v40', 'dailyk-1645-chain-v40', 'dailyk-audit-v39', 'dailyk-repair-v39', 'kline-full-range-v38', 'volume-pane-v38', 'hourly-kline-v38', 'volume-units-verified-v38', 'config-guard-v37', 'project-file-check-v37', 'subscription-mail-design-v36', 'tab-deeplink-v36', 'unsubscribe-exec-url-v35', 'no-watch-alert-v35', 'instant-mail-design-v35', 'tech-toc-flatten-v34', 'tech-subnav-drawer-v33', 'sms-revision-afterhours-v32', 'dividend-not-price-v32', 'holding-subject-v31', 'sound-roster-v31', 'tech-keypoints-v30', 'tx-layout-v29', 'tech-fixed-footer-v29', 'tech-chunks-v28', 'doc-nav-v27', 'mail-darkmode-v26', 'reason-no-rationale-v25', 'test-mail-v25', 'sms-audit-range-v24', 'admin-cal-loading-v23', 'article-sections-v22', 'mail-reading-settings-v22', 'mail-layout-v21', 'title-no-prefix-v21', 'daily-name-wrap-v21', 'cmoney-auto-reconcile-v20', 'title-no-length-limit-v19', 'cmoney-diagnose-v19', 'watch-classify-v18', 'title-168-style-v17', 'narrative-no-asterisk-v17', 'watch-classify-v17', 'dailyk-resume-v15', 'fugle-range-split-v15', 'watch-tone-v15', 'tracker-pricing-v14', 'tracker-rounds-v13', 'refresh-transient-resume', 'transcript-display-punctuation', 'gemini-503-key-switch', 'transcript-job-hash', 'auto-dailyk-resume', 'transcript-merge-preview', 'context-policy-v4', 'refresh-all-resumable', 'perf-history-pending', 'evidence-v2', 'evidence-v1', 'day-edit-sync-v1', 'ping', 'refresh-step', 'admin-page', 'quality-gate', 'full-fix',
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
                    'perf-history-rebuild', 'auto-refresh-after-write', 'day-edit-auto-sync-v47', 'market-technicals-v47'];

var REFRESH_ORDER_ = ['purge', 'gate1', 'gate2', 'gate3',
                      'codes', 'fund', 'dailyk', 'tracker', 'perf'];

var CHAIN_KEY_ = 'refreshChainState';
