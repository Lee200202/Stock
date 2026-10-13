# 全站圖解與排版改版：給執行 AI 的 Prompt

2026/10/13 建立。依據是當天對正式站（`https://lee200202.github.io/Stock/`，與 Apps Script `/exec` 同一份原始碼）的實際量測：電腦 1600px、手機 390px，逐分頁量字級、末行孤字、溢出、觸控高度、動畫規則與「只有文字的流程圖」。原始結果在同一個資料夾：

- `2026-10-13-tech-layout-audit.txt`（技術說明頁）
- `2026-10-13-site-layout-audit.txt`（其餘分頁與個股頁）

重跑量測：`python scripts/audit_tech_layout.py`、`python scripts/audit_site_layout.py`（唯讀，只開正式站）。重跑的結果會寫到 `docs/1003-review/tech-layout-audit.txt` 與 `site-layout-audit.txt`（沒有日期的檔名），拿來和上面兩份 10/13 的基準比。

---

## 0. 你要做的事（一句話）

把網站上「用文字卡片排成一列、中間放箭頭」來充當流程圖的地方，改成真正看得懂的圖解（圖示、形狀、位置、顏色各自帶資訊），並順手修掉量到的字太小、手機末行孤字、動畫不順或沒有視覺意義的問題。不改任何資料流程、不改文字事實、不新增外部函式庫。

已經做好一個可以照抄做法的範例：技術說明「AI 是怎麼讀原稿、查資料，再交出答案的？」裡的「② 字怎麼變成輸入？」（v175）。原本是四張字卡（01 原文／02 Tokenizer／03 數值表示／04 模型輸入），現在是四個帶圖示的階段：一句話 → 沿虛線剪成小單位 → 查詞彙表換成編號 → 每個編號變成一排數字格並標上位置；點任何一個小單位，四個階段裡同一個單位一起亮。請先打開這一段實際操作一次再動手。

---

## 1. 檔案與部署（務必照做）

| 內容 | 位置 |
|---|---|
| 技術說明頁的 HTML 與互動腳本 | `public-site/gas-source/Tech.html`（腳本在檔尾同一個 `<script>`，工具函式 `one()`、`all()`、`esc()`、`select()` 約在 606 行） |
| 全站樣式 | `public-site/gas-source/Stylesheet.html`（技術頁相關集中在 3780 行之後；代理一章約 4940–5050 行） |
| 首頁各分頁的 HTML | `public-site/gas-source/Index.html` |
| 首頁腳本 | `public-site/gas-source/JavaScript.html` |
| 每日整理郵件的版面（`.mc-*`） | `public-site/gas-source/MailService.gs`（HTML 由後端組出來，行內樣式；改這裡會影響實際寄出的信） |
| 版本標記 | `Config.gs` 的 `GAS_BUILD`、`Setup.gs` 的 `PROJECT_BUILD_`，兩處同一個字串 |
| 版本沿革 | `Changelog.html`，新條目放最上面，文字精簡 |

- GitHub Pages 由 `public-site.yml` 在推上 `main` 後自動組站部署，前台改動推上去就上線。
- Apps Script 要另外同步：新資料夾 `clasp pull` → 只複製有改的檔 → `clasp push -f` → `clasp create-version`。正式 `/exec` 由管理者切換。
- 檔案是 CRLF。用腳本改檔時先把 `\r\n` 換成 `\n` 處理、寫回時還原；Git Bash 的 heredoc 會吃掉反斜線，改檔腳本請用寫檔工具建立。
- `scripts/sync_quality.py` 會重寫 `Adminpipeline.gs` 並在 `Cmoney.gs`、`SheetService.gs` 留下只有換行差異的修改，提交前 `git checkout --` 這兩個檔。
- 另一個工作階段可能同時在改同一個 repo：動手前 `git pull --rebase --autostash`，不要碰不是自己改的未提交檔案。

---

## 2. 設計語彙（全站共用，不要自創顏色與字體）

CSS 變數（淺色、深色主題都已定義，直接用）：

- 底色與面：`--bg`、`--panel`、`--panel-2`；線：`--rule`；字：`--ink`、`--muted`
- 強調色：`--beam`（墨綠）；漲跌：`--rise`、`--fall`；等待：`--wait`
- 圓角：`--r-md`、`--r-pill`；卡片常用 `border-radius: 20px`～`24px`
- 字級倍率：`--rd-fs`（使用者可在設定調整大小），所有字級一律寫成 `calc(Npx * var(--rd-fs))`
- 等寬數字：`font-family: 'IBM Plex Mono', monospace`

淡色底的固定寫法：`background: color-mix(in srgb, var(--beam) 12%, var(--panel))`。被選取的狀態再加 `border-color: var(--beam)`。

圖示一律用行內 SVG：`viewBox="0 0 24 24"`、`fill="none"`、`stroke="currentColor"`、`stroke-width="1.8"`、圓頭圓角，放在 44×44、圓角 14px 的淡色底方塊裡。不用 emoji、不用圖示字型、不載入外部資源。

---

## 3. 參考實作：v175 的「字怎麼變成輸入」圖解

以下是實際上線的程式，照這個結構做其他圖解。

### 3.1 HTML（`Tech.html`，放在原本的字卡清單後面）

```html
<ol class="agent-route" id="agentRoute" aria-label="目前情境的圖解">…原本的四張字卡…</ol>
<div class="tok-viz" id="tokViz" role="group" aria-label="文字變成模型輸入的四個階段（示意）" hidden></div>
<p class="tok-pick" id="tokPick" aria-live="polite" hidden></p>
```

### 3.2 腳本（同檔案的 `<script>`；資料、圖示、組版、連動、切換）

```js
/* 切法、編號與格子深淺都是示意，不是實際 tokenizer 或模型的輸出。 */
var tokData=[['鴻海','18,342',[.9,.25,.6,.35,.8,.15,.5,.7]],['昨天','5,521',[.3,.75,.2,.9,.45,.6,.15,.8]],['賣出','9,107',[.55,.4,.85,.2,.7,.9,.3,.45]]];
var tokIco={ text:'<svg …文件…/>', cut:'<svg …剪刀…/>', book:'<svg …書…/>', grid:'<svg …格線…/>' };
function tokStage(n,ico,kicker,title,body,note,last){
  return '<section class="tok-stage'+(last?' is-result':'')+'"><div class="tok-head"><span class="tok-ico">'+tokIco[ico]+'</span>'+
    '<div><span>'+n+' '+esc(kicker)+'</span><b>'+esc(title)+'</b></div></div><div class="tok-body">'+body+'</div><small>'+esc(note)+'</small></section>';
}
function tokBuild(){            // 第一次切到這一題才組，之後不重組
  var el=one('#tokViz'); if(!el||el.dataset.ready){return;} el.dataset.ready='1';
  /* 四個階段：tok-text（一句話）、tok-cut（虛線分開的小單位）、tok-ids（小單位 → 編號）、tok-vec（位置圓圈＋八格數字） */
  el.innerHTML = tokStage('01','text','原文','人讀的文字', …) + tokStage('02','cut','Tokenizer','剪成小單位', …) + …;
  all('#tokViz [data-tok]').forEach(function(b){b.addEventListener('click',function(){tokPick(+b.dataset.tok);});});
  tokPick(1);
}
function tokPick(i){            // 同一個小單位在四個階段一起亮，說明列跟著換
  all('#tokViz [data-tok]').forEach(function(n){var on=+n.dataset.tok===i;n.classList.toggle('is-on',on);n.setAttribute('aria-pressed',String(on));});
  one('#tokPick').textContent='「'+tokData[i][0]+'」→ 編號 '+tokData[i][1]+' → 第 '+(i+1)+' 個位置的一排數字。…';
}
all('[data-agent]').forEach(function(b){b.addEventListener('click',function(){
  var on=b.dataset.agent==='tokens',viz=one('#tokViz');
  if(on){tokBuild();viz.classList.remove('tok-run');void viz.offsetWidth;viz.classList.add('tok-run');}   // 重新觸發進場
  viz.hidden=!on;one('#tokPick').hidden=!on;one('#agentRoute').hidden=on;
});});
```

### 3.3 樣式（`Stylesheet.html`，重點幾條）

```css
.agent-lab [hidden] { display:none !important; }   /* 這一章的清單有 display:grid，會蓋過 hidden 屬性 */
.agent-lab .tok-viz { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:30px; margin:20px 0 14px; }
.agent-lab .tok-stage { position:relative; display:flex; flex-direction:column; gap:14px; min-width:0; padding:18px 16px;
  border:1px solid var(--rule); border-radius:20px; background:var(--panel); }
.agent-lab .tok-stage:not(:last-child)::after { content:'→'; position:absolute; right:-23px; top:46%; color:var(--beam); font-weight:700; }
.agent-lab .tok-ico { flex:0 0 44px; width:44px; height:44px; display:grid; place-items:center; border-radius:14px;
  background:color-mix(in srgb,var(--beam) 14%,var(--panel)); color:var(--beam); }
.agent-lab .tok-viz button { min-height:44px; }
.agent-lab .tok-cut button + button { border-left:2px dashed var(--beam); }            /* 下刀的位置 */
.agent-lab .tok-cells { display:grid; grid-template-columns:repeat(8,minmax(0,1fr)); gap:3px; }
.agent-lab .tok-cells i { display:block; aspect-ratio:1; border-radius:4px; background:var(--beam); opacity:var(--o,.4); }   /* 深淺＝數字大小 */
.agent-lab .tok-viz.tok-run .tok-stage { animation:tokIn .5s cubic-bezier(.22,1,.36,1) both; }
.agent-lab .tok-viz.tok-run .tok-stage:nth-child(2) { animation-delay:.18s; }          /* 依序出現，交代先後 */
@keyframes tokIn { from { opacity:0; transform:translateX(-10px); } to { opacity:1; transform:none; } }
@media (prefers-reduced-motion: reduce) { .agent-lab .tok-viz.tok-run .tok-stage { animation:none; } }
@media (max-width:1100px) { .agent-lab .tok-viz { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:760px)  { .agent-lab .tok-viz { grid-template-columns:minmax(0,1fr); }
  .agent-lab .tok-stage:not(:last-child)::after { content:'↓'; right:50%; top:auto; bottom:-27px; } }
```

### 3.4 這個範例裡值得照做的六件事

1. 每個階段有圖示＋一個「看得見的東西」（一條字、被剪開的塊、對照表、數字格），不是只有粗體字加小字。
2. 一個物件跨階段保持身分（同一個 `data-tok`），點一下全部一起亮：讀者看得出「它變成了什麼」。
3. 進場動畫只用 `opacity` 與 `transform`，依序延遲 0.18 秒，總長不到 1.1 秒；減少動態時整個關掉。
4. 圖解下面有一行會跟著互動改變的白話說明（`aria-live="polite"`）。
5. 示意資料在畫面上寫明是示意（圖上方的標籤與每一格的小字）。
6. 手機改成直排、箭頭改朝下，不縮小字。

---

## 4. 量到的問題與要改的程式碼

### 4.1 字太小（低於 13px 的一律提高）

規則：內文與說明最小 `calc(14px * var(--rd-fs))`；標籤、圖例、圖上的刻度最小 `calc(13px * var(--rd-fs))`。手機不得比電腦小。

| 目前 | 選擇器（`Stylesheet.html` 約略行號） | 出現在 | 建議 |
|---|---|---|---|
| 10.5px | `.tracker-card-identity .roundtag` | 持股追蹤卡片「第 2 回合」 | 13px，必要時改成外框膠囊並允許換到下一行 |
| 11px | `.tx-mid .tx-cum` | 持股追蹤「累積 -1.75%」 | 13px；這是報酬數字，不該是整張卡最小的字 |
| 11px | `.mc-hero-k`（`MailService.gs` 行內樣式） | 郵件標題卡的「每日整理」 | 13px |
| 11.5px | `.tech-story .pc-ask i`、`.pc-ans i`（3958、3961 行） | 技術頁對話卡的「觀眾」「網站」 | 13px |
| 11.5px | `.tech-story .ur-axis span`（4271 行）、`.ur-dot b`、`.ur-now b` | 沒講名字那一段的價位軸刻度 | 13px，刻度太擠就減少刻度數 |
| 12px | `.tech-story .tech-card small`（3799 行）、資料表地圖裡的 `small > span`（38 處）、`.pn-len`、`.tl-exit small` | 技術頁各處 | 13px |
| 12px | `.mc-tone-k`、`.mail-meta` | 郵件查詢 | 13px |
| 12px | `.subcap .pane-tog` | 個股頁 K 線的「隱藏」按鈕 | 13px，並把觸控高度拉到 40px 以上 |
| 12.5px | `.tech-story .tp-k`（4096 行）、`.tl-state`（3938 行）、`.tech-tag`、`.pn-chip`、`.open-state`、`.pc-where`、`.perf-how` | 技術頁步驟的小標、狀態膠囊；績效走勢說明 | 13px |

大字沒有問題：全頁只有 `.load-stat strong` 是 42px（載入速度的三個數字），保留。

### 4.2 手機上的末行孤字（最後一行只剩一兩個字）

電腦版只有 6 處，手機（390px）技術頁 22 處、郵件查詢 7 處。全站已有通用規則（`Stylesheet.html` 73–78 行）：標題類 `text-wrap: balance; word-break: keep-all;`，內文 `text-wrap: pretty`。但下列元素沒有被涵蓋，請補上 `text-wrap: balance`（它們都是三行以內的短句）：

```css
.tech-story .tech-pains button, .tech-story .tech-pains button b,
.tech-story .pc-head b, .tech-story .tech-card small,
.tech-story .tech-options button, .tech-story .decision-grid button,
.tech-story .agent-models small, .agent-lab summary,
.tech-story .tx-stages li p, .tech-story .th-line li,
.mc-h2, .mc-name { text-wrap: balance; }
```

量到的實例（手機）：「②今天沒買賣，是漏抓還是真的沒講？」末行剩「講？」；「操作散在每一集，沒有一份完整紀錄」；「即時紀錄漏抓核對」末行剩一個字；「立即電寫成華邦電」；「新稿來了，舊工單會不會蓋回去？」（h2）。郵件裡的「① 盤勢總覽重點整理」「② 會員操作紀錄與持股明細」在兩種寬度都折成兩行而且末行很短：`.mc-h2` 的字級或字距要調，或在編號後面允許斷行（把「①」和標題分成兩個行內區塊，窄的時候標題整段換到第二行）。

郵件表格的說明欄（`td.mc-desc`）在手機上五行、末行兩個字：這是 `MailService.gs` 產生的表格，請在手機寬度把每一列改成上下排（名稱與代號一行、分類膠囊一行、說明獨占全寬），不要讓說明擠在窄欄裡。注意這份 HTML 同時用於實際寄出的信，信件用戶端不支援 `grid` 與 `text-wrap`，改法必須在行內樣式下仍可讀（用 `display:block` 的儲存格堆疊，不用 grid）。

### 4.3 動畫：改掉會掉幀的寫法，換掉沒有視覺意義的

**a. 用版面屬性做動畫的三處，改成 `transform`：**

```css
/* 現在（4221–4224 行）：持股時間線的進度條與移動的圓點 */
.tech-story .th-fill   { transition: width .9s cubic-bezier(.4,0,.2,1); }
.tech-story .th-packet { transition: left  .9s cubic-bezier(.4,0,.2,1); }
/* 現在（5037 行）：token 用量條 */
.agent-lab .agent-usage-track span { width:2.594%; transition: width .3s ease; }
```

改法：進度條固定寬度 100%，用 `transform: scaleX(var(--p))` 加 `transform-origin: left`；圓點用 `transform: translateX(...)`（把目標位置算成 px 或用容器寬度的 CSS 變數）。用量條同理。這三處目前每一幀都會重新排版。

**b. 線性的時間曲線：**

```css
/* 4886 行 */ .tech-story .load-charts.is-armed.is-play .load-rail i { animation: loadGrow var(--load-ms,600ms) linear both; }
```

這一條是「等待時間長短」的比較，線性是刻意的（長條的長度就是時間），可以保留；但請在長條跑完時加一個到達的收尾（數字淡入或條尾閃一下），現在跑完就靜止，看不出哪一條先到。

**c. 沒有視覺意義的動畫（重點要改的）：**

| 位置 | 現在做了什麼 | 問題 | 改成 |
|---|---|---|---|
| 代理三層分工圖 `.agent-map`，按鈕「播放一次查詢 →」（`Tech.html` 約 198–203 行；腳本裡的 `showNode`、`mapTimers`） | 三張字卡輪流換底色，下面一行字跟著換 | 沒有任何東西在三層之間「移動」，看不出是誰把什麼交給誰 | 見 5.1 |
| `.agent-lab .agent-run li`（5041 行 `agentPulse`） | 切換題目時四張字卡一起往上彈 5px | 只是提示「內容換了」，四張同時動，沒有先後 | 改成依序出現（照 `tokIn` 的寫法加 `animation-delay`），或每一題換成專屬圖解後拿掉 |
| `.tech-story .tx-stages li`（4118 行） | 六個階段只換底色與框線 | 看不出「舊工單被作廢」「新稿接手」的方向 | 見 5.6 |
| `.tech-story .tech-anim`（`techRise`，4183 行） | 卡片上浮加陰影 | 純裝飾，可留；但同一個畫面同時觸發超過三個時要錯開 |

**d. 共同規範（新寫的動畫都要符合）：**

- 只動 `opacity` 與 `transform`。不要動 `width`、`height`、`left`、`top`、`margin`、`box-shadow`（陰影用疊一層偽元素改透明度）。
- 進場 `cubic-bezier(.22,1,.36,1)`，0.35～0.5 秒；狀態切換 0.18～0.25 秒 `ease-out`；一組依序出現的元素相鄰延遲 0.12～0.18 秒，整組不超過 1.2 秒。
- 一律由使用者動作觸發，播一次就停；不要自動循環（游標閃爍 `techBlink` 除外）。
- `@media (prefers-reduced-motion: reduce)` 下關掉動畫，並直接顯示最終狀態與一句文字說明（技術頁已有 `.tech-story * { animation: none }` 的總開關，新動畫也要在這個開關下有正確的靜止畫面）。
- 會重播的動畫用「移除 class → 讀一次 `offsetWidth` → 加回 class」重新觸發，參考 3.2。

### 4.4 觸控區

- 訂閱通知的勾選框 `label.optcard > input` 只有 22×22：把整張 `label.optcard` 當成可點區（已經是 label），勾選框本身放大到 24×24 並確保 label 高度 ≥ 48px。
- 個股頁 `.subcap .pane-tog`（「隱藏」）高度不足 40px。

### 4.5 版面寬度

- 技術頁在 1880px 螢幕上內容欄是 1320px，左右各留 272px。v175 已把「模型推論」一章裡原本限制在 960px 的三層分工圖與 68em 的段落放開到整欄。其他章節沒有再發現比章節窄很多的區塊。
- 段落（`.tech-story .tech-section > p`，3781 行）仍限制 `max-width: 68em`，這是為了閱讀行長，除非管理者要求不要動。
- 個股頁「估值與公司資料」等 `.kv-grid` 已修成電腦一列三張、手機兩張（欄寬跟著 `--kv-gap` 算）。

---

## 5. 逐一要畫的圖（技術說明頁）

每一項都寫了：現在的樣子（選擇器）、要表達的事、畫法、互動、手機排法。文字內容與數字沿用現有的，不要新增事實。

### 5.1 三層分工與「播放一次查詢」（`.agent-map`、`.agent-map-steps`）

- 現在：三張字卡「01 控制程式 Harness／02 模型 Model／03 工具 Tools」，播放時輪流變色。
- 要表達：一次查詢裡，資料包在三層之間來回的路線——控制程式把選好的原稿交給模型 → 模型提出「要查什麼」→ 控制程式核准後交給工具 → 工具把結果交回控制程式 → 核對。
- 畫法：三個節點橫排，各自一個圖示（控制程式：盾牌加勾；模型：對話框裡三個點；工具：資料庫圓柱）。節點之間畫兩條有方向的連線（去程在上、回程在下，SVG `<path>` 加箭頭 marker）。一個小圓角方塊當「資料包」，播放時沿著連線移動，方塊上的字跟著階段換：「10/13 原稿」→「想查：鴻海今天賣了嗎」→「核准：讀操作紀錄」→「結果：10/06 已出場」→「核對完成」。
- 動畫：資料包用 `offset-path`（瀏覽器不支援時退回逐段 `transform: translate`）；每段 0.6 秒，中間停 0.4 秒讓人讀字；目前所在的節點加外框。播放中按鈕停用、可按任一節點中斷（現有 `stopMap()` 的行為保留，`check_agent_animation.py` 會驗）。
- 互動：點節點顯示它的責任（沿用 `nodeText`）。
- 手機：三個節點直排，連線改成左側去程、右側回程的直線。
- 減少動態：不移動，五個階段的文字依序列成一行行的清單。

### 5.2 其餘五題的圖解（`#agentRoute`，題目鍵值 `context`、`infer`、`tools`、`fault`、`swap`）

目前五題都是「四張字卡＋箭頭」。照 `tokViz` 的做法各做一個專屬區塊，切換題目時顯示對應的那一個、隱藏 `#agentRoute`：

- **① 一次能讀多少（context）**：一條橫向的長條代表輸入上限 1,048,576 tokens，裡面依比例畫出當天最大一次輸入 32,325（約 3.08%，寬度太小時最小畫 6px 並拉一條引線放大顯示）。長條上方用括號標出「指令」「工具規格」「原稿」「查詢結果」各占的示意區段。旁邊另一條短的代表輸出上限 65,536。數字用等寬字。
- **③ 答案怎麼產生（infer）**：一排 token 方塊「鴻海／仍／持有／。」，前面灰色的是已讀入的上下文，後面一格一格依序亮起；每亮一格，上方出現三個候選小條（長短代表機率，只有一個被選中變綠）。按「下一步」逐格前進，最後一格是停止符號。
- **④ 誰真的查資料（tools）**：左右兩個泳道（模型／控制程式與工具）。模型泳道丟出一張「工具請求」卡，卡片經過中間的閘門（名稱、參數、權限三個勾依序打上）才進到右邊；右邊回一張蓋了日期章的「結果」卡。另畫一條被擋下的路線：沒有結果卡就宣稱「已查詢」→ 紅色叉。
- **⑤ 一檔錯了怎麼辦（fault）**：20 個小圓點排成 4×5（代表候選個股），其中一個變成橘色並滑到旁邊標著「留底、不公開」的框，其餘 19 個依序變綠流向「產生文章」。圖下保留現有的那句說明（這是數量示意，不是當日統計）。
- **⑥ 換模型就一樣嗎（swap）**：左邊兩個模型方塊 A、B（形狀不同的插頭），中間一個轉接層（同一種插座），右邊一份相同的驗收清單（三個打勾項目：原稿、分類、錯誤案例）；切換 A／B 時清單逐項重新打勾，其中一項可以示範不通過（紅叉）→「不採用」。

### 5.3 資料從哪裡來（`#techFlow.tech-flow`，6 步）

- 現在：六個編號步驟，文字為主。
- 畫法：一條由左到右的管線。來源用兩個不同的圖示（影片：播放鍵；即時紀錄：閃電或訊號），在第二步匯流；之後每一步一個圖示（聽打：聲波變文字；判讀：放大鏡；核對：盾牌勾；寫入：表格；網站：瀏覽器視窗）。步驟之間的連線粗細一致，匯流處畫成 Y 形。
- 互動：沿用現有點擊步驟顯示說明的行為；被點的步驟之前的管線變成實色（表示已走過）。
- 手機：直排，匯流畫在最上面。

### 5.4 載入速度的三步（`ol.load-steps`）

- 畫法：時間軸。三個時間點（例如 09:05 打開首頁）用圓點釘在一條橫線上，圓點之間的距離按實際毫秒比例；上方畫「手機 → 快取 → 試算表」三個小圖示，請求線打到哪一層就在那一層折返。命中快取時線很短，沒命中時線打到最底。
- 與現有的 `.load-charts`（長條比賽）放在一起，兩者用同一個播放鍵。

### 5.5 沒講是哪一檔（`#unChain.un-chain`，5 步；`.un-range` 價位軸）

- 現有的價位軸（`.ur-axis`、`.ur-dot`、`.ur-now`）是這一頁少數真正的圖，保留並放大：刻度字 13px 以上，軸線加粗到 3px，「昨收 1770」與「現在 1800」兩個點用不同形狀（空心圓／實心圓）。
- 五步推理鏈改成「漏斗」：最上面是追蹤中的全部股票（一排小方塊），第一道篩「昨收對得上」留下少數幾個，第二道篩「今天的高低包住現價」留下一個，其餘淡出。每一道篩旁邊寫條件。最後留下的方塊放大並顯示名稱。

### 5.6 新稿來了，舊工單會不會蓋回去（`#txStages.tx-stages` 6 階段、`ol.tx-steps` 8 種情況）

- 這一章有 3,274px 高，是全頁第二長；圖解可以取代一部分文字。
- 畫法：兩條平行的軌道，上面是「舊稿的工單」、下面是「新稿的工單」，各有六個站（原稿、潤飾、判讀、文章、寄送關卡、背景績效）。每一站一個小圓，狀態用形狀加顏色：完成＝實心勾、作廢＝斜線圓、等待＝空心圓、接手＝箭頭圓。新稿進來的那一刻，舊軌道從當前站之後全部變成作廢樣式，新軌道從第一站開始亮起。
- 互動：沿用現有的 8 個情境按鈕（`data-tx`），每按一個，兩條軌道重畫；現有的 `check_tech_story.py` 會驗每個按鈕都改變結果、不發出網路請求。
- 手機：兩條軌道直排並列（左舊右新），站名寫在中間。

### 5.7 盤中先有操作、影片後到（`#sourcePath.source-path`，3 步）

- 畫法：一條當日時間軸，左邊先落下一張「當日操作」卡（已蓋章），稍後右邊落下一張「影片說明」卡；兩張卡滑向同一檔股票的格子時，先到的那一張決定分類（格子顏色），後到的只把說明補進去（格子下方多一行字）。方向相反時後到的卡被擋在外面。
- 注意前台用字：不出現通知管道的名稱（不寫簡訊、會員通知、盤中通知），沿用頁面上現有的「即時紀錄」「當日操作」。

### 5.8 持股有開始和結束（`#techTimeline.th-line`）

- 已有圖示（`.th-icon`）、進度條（`.th-fill`）與移動的圓點（`.th-packet`），是可用的圖；改兩點：動畫改用 `transform`（見 4.3a）；在時間線下方加一條小的報酬折線（持有期間由進場價到出場價，回合之間斷開），讓「回合」不只是文字。

### 5.9 有一段失敗時（`#failLanes.tech-lanes`，四條泳道各三格）

- 畫法：四條橫向泳道（取稿、判讀、網站文章、背景計算），每條三個站。失敗的站畫成紅色斷點，斷點之後的站變灰；不受影響的泳道照常走完（綠色）。重試畫成從斷點繞回前一站的小弧線，旁邊標次數。
- 狀態膠囊（`.tl-state`）字級提高到 13px。

### 5.10 同一句有「賣」，要先看誰在賣（`#tech-subject`，目前 0 張圖）

- 畫法：把例句放大，句子裡的主詞、動作、對象各用一個底色框起來（主詞：藍、動作：綠／紅、時間：琥珀），下面三個小卡分別接住這三個框（用細線連到句子裡的位置）。切換例句時框的位置跟著換。券商在賣、觀眾在賣、講者在賣三種情況，最後落到不同的分類格。

### 5.11 「168 張」和「900 以下」（`#tech-number`，0 張圖）

- 畫法：一條數線，上面有當天這一檔的最低與最高（一段粗線）。句子裡的每個數字變成一個小標籤掉到數線上：落在粗線附近的是價位（綠框），離很遠的（168、張數）彈到數線外並標上單位圖示（張＝一疊紙）。

### 5.12 語音辨識聽錯股名（`#tech-names`，0 張圖）

- 畫法：左邊是聽寫出來的字（例如「立即電」），右邊是三個候選公司，中間用粗細不同的線連起來（線越粗＝讀音越接近）。人工確認過的那一條線變成實線並加上鎖頭圖示；沒確認、候選差距太小的全部維持虛線並顯示「不猜」。

### 5.13 還沒解決的事（`#openPath.tech-path`，3 步）

- 沿用 5.5 的漏斗樣式，但最後一道篩之後剩下「0 個」或「2 個以上」，框變成虛線並標「不列」。

---

## 6. 其他分頁可以圖像化的地方

依量測結果，下面幾個區塊是大段文字或表格，沒有任何圖形：

| 分頁 | 區塊 | 量測 | 建議 |
|---|---|---|---|
| 每日總覽 | 觀望不碰／觀望注意清單（`section.panel > div.sect`） | 622 字、高 1,492px（手機 1,848px） | 每一列前面加一個小的方向圖示（不碰：盾牌；注意：眼睛），說明超過三行時預設收合成兩行加「展開」；清單上方加一條當日分類的比例條（買入／賣出／持股／注意／不碰各一段，寬度按檔數） |
| 持股追蹤 | 卡片清單 | 2,117 字、高 3,837px（手機 4,664px）；最小字 10.5px、11px | 每張卡的進場價→現價用一條小的區間條表示（左端進場、右端現價、顏色表示正負），取代現在只有數字的「累積 ±x%」；回合標籤放大（見 4.1）；手機預設只顯示前幾張並提供「再看更多」 |
| 郵件查詢 | 每日整理全文 | 2,131 字、高 3,714px（手機 5,919px）；末行孤字 7 處 | 章節 ①②③ 各加一個圖示與可點的章節導覽（固定在內容上方的三顆膠囊）；② 的表格手機改上下排（見 4.2）。這一塊的 HTML 來自 `MailService.gs`，同時是寄出的信，網站上的導覽請在外層另外加，不要寫進信件本體 |
| 績效走勢 | 說明文字 `.perf-how` 12.5px | — | 提高到 13px；在折線圖下方加圖例說明「一個點＝一個交易日」 |
| 個股頁 | 估值卡片 | 標籤 13px | 可在本益比、殖利率卡片右側加一個極小的同產業位置條（需要資料時再做，沒有資料就不要畫） |

---

## 7. 文字與內容上的限制（違反就不能上線）

1. 前台不出現通知管道的字眼：不寫「簡訊」「會員通知」「盤中即時通知」「盤中通知」。分類名稱照舊（買入、賣出、會員持股、觀望注意、觀望不碰）。
2. 不顯示講者口頭講的價位當成進出場價；公開的進出場價是行情資料。不顯示電話號碼。
3. 圖解裡的示意資料必須在畫面上標明「示意」，而且不可與同一頁的實測數字混在同一張圖裡。現有的實測數字（例如 19 次回應、179,291 tokens、3.08%）有 `scripts/check_agent_animation.py` 對 `docs/1003-review/2026-10-13-agent-usage.json` 核對，不要改數字。
4. 所有示範互動都是本機示意：不發出網路請求、不呼叫模型、不寫入。`check_tech_story.py` 會驗切換時沒有 API 請求。
5. 文案精簡：圖解旁的說明一格不超過兩行；不要為了填滿版面加句子。管理者偏好少字、看圖。
6. 不新增投資建議語氣；不新增任何原稿或資料裡沒有的事實。
7. 不寫人名當主詞（不寫「張震表示」「分析師指出」）。

---

## 8. 無障礙與相容

- 互動元件用 `<button type="button">`，選取狀態用 `aria-pressed`；會變動的說明用 `aria-live="polite"`；純裝飾的格子、箭頭、圖示加 `aria-hidden="true"`。
- 每張圖解的容器加 `role="group"` 與一句說明用途的 `aria-label`。
- 顏色不是唯一的訊號：狀態同時用形狀或圖示區分（勾、叉、斜線、空心）。
- 可點目標高度 ≥ 44px（手機至少 40px）。鍵盤可以依序走到每個按鈕，`:focus-visible` 用現有的 `outline: 3px solid var(--beam); outline-offset: 4px`。
- 淺色、深色主題都要看過；顏色一律走 CSS 變數。
- 沒有 JavaScript 時，預設情境的文字版必須仍在 HTML 裡（`check_tech_story.py` 的 no-JS 檢查）。新圖解由腳本產生時，原本的文字清單要留在 HTML 裡當後備，有腳本時再隱藏（v175 就是這樣做：`#agentRoute` 還在，`#tokViz` 由腳本組）。

---

## 9. 驗收（每一項都要跑過）

```bash
python scripts/check_original_parity.py          # 前後台 API 對應
node   scripts/check_quote_flow.js
python scripts/check_tech_story.py               # 技術頁互動：每個按鈕都改變結果、不發請求、五種寬度兩種主題不橫捲、減少動態、no-JS
python scripts/check_agent_chapter.py            # 代理一章 360px 淺深色、六個情境、鍵盤
python scripts/check_agent_animation.py          # 動畫可重播可中斷、三層圖用滿欄寬、用量數字與日誌一致
python scripts/check_token_diagram.py            # v175 圖解：四階段、點選連動、不溢出、動畫尊重設定
python -m unittest discover -s tests             # 611 項
```

新做的每一張圖解都要比照 `scripts/check_token_diagram.py` 寫一支檢查（Playwright，本機組站、假 API）：1440／768／390／360px、淺深色，驗證圖解出現且原本的字卡隱藏、互動確實改變畫面、卡片內的元素沒有超出卡片（量實際元素的右緣，不要用 `scrollWidth`：箭頭偽元素畫在卡片外會被算進去）、整頁沒有橫向捲動、按鈕高度、減少動態時 `animationName` 是 `none`。量位置之前先等動畫結束：

```python
page.wait_for_function("document.getElementById('tokViz').getAnimations({subtree: true}).every(a => a.playState === 'finished')")
```

做完之後重跑兩支量測腳本，回報這三個數字的前後對照：低於 13.5px 的文字種類數、手機末行孤字處數、純文字流程圖的數量。

---

## 10. 建議的施工順序

1. 字級與 `text-wrap`（4.1、4.2）：純 CSS，風險最低，先做先上線。
2. 動畫改 `transform`（4.3a）與觸控區（4.4）。
3. 代理一章：5.1 的資料包動畫，再做 5.2 的五題圖解（結構與 `tokViz` 相同，可以共用 `tokStage` 這類組版函式）。
4. 5.6（兩條軌道）與 5.9（泳道）：這兩章最長，圖解做完後請同時刪減重複的文字說明。
5. 其餘章節（5.3、5.4、5.5、5.7、5.10–5.13）。
6. 其他分頁（第 6 節）。郵件版面的修改要另外寄一封測試信確認之前，先只改網站上顯示的外層。

每完成一個步驟就各自提交、推上 GitHub、更新版本標記與版本沿革，並在正式站用電腦與手機寬度各看一次。
