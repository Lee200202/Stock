# 全站圖解與排版改版：給執行 AI 的 Prompt

2026/10/13 建立。依據是當天對正式站（`https://lee200202.github.io/Stock/`，與 Apps Script `/exec` 同一份原始碼）的實際量測：電腦 1600px、手機 390px，逐分頁量字級、末行孤字、溢出、觸控高度、動畫規則與「只有文字的流程圖」。原始結果在同一個資料夾：

- `2026-10-13-tech-layout-audit.txt`（技術說明頁）
- `2026-10-13-site-layout-audit.txt`（其餘分頁與個股頁）

重跑量測：`python scripts/audit_tech_layout.py`、`python scripts/audit_site_layout.py`（唯讀，只開正式站）。重跑的結果會寫到 `docs/1003-review/tech-layout-audit.txt` 與 `site-layout-audit.txt`（沒有日期的檔名），拿來和上面兩份 10/13 的基準比。

---

## 0. 你要做的事（一句話）

把網站上「用文字卡片排成一列、中間放箭頭」來充當流程圖的地方，改成真正看得懂的圖解（圖示、形狀、位置、顏色各自帶資訊），並順手修掉量到的字太小、手機末行孤字、動畫不順或沒有視覺意義的問題。不改任何資料流程、不改文字事實、不新增外部函式庫。

已經做好兩個可以照抄做法的範例（第 3 節的圖解、3B 節的連結卡片）。第 11 節是不受現有版面限制的整頁重新設計提案，可以整批採用，也可以挑著做。先說第一個範例：技術說明「AI 是怎麼讀原稿、查資料，再交出答案的？」裡的「② 字怎麼變成輸入？」（v175）。原本是四張字卡（01 原文／02 Tokenizer／03 數值表示／04 模型輸入），現在是四個帶圖示的階段：一句話 → 沿虛線剪成小單位 → 查詞彙表換成編號 → 每個編號變成一排數字格並標上位置；點任何一個小單位，四個階段裡同一個單位一起亮。請先打開這一段實際操作一次再動手。

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

## 3B. 第二個參考實作：v176 的「依據連結卡片」（自動播放、點了就停）

管理者的要求：像「實際依據：10/13取稿日誌 · 10/13判讀日誌 · 取稿程式 · 助手控制程式」這種一行連結，改成一張張可以點出去的卡片；善用顏色與不同方向的滑動；滑到那一段才自動播放；點卡片時動畫停止。已上線，位置在技術頁「模型推論」一章的兩處（實際依據 4 張、原理依據 5 張）。

### 做法

- HTML 不動：原本的 `<p class="agent-sources">標題：<a>…</a> · <a>…</a></p>` 留著當沒有腳本時的後備。
- 腳本把「有兩個以上連結」的那種段落改排成卡片並把原段落 `hidden`；只有一個連結或沒有連結的 `.agent-sources`（純說明文字）不動。
- 顏色依連結種類（用既有的 `tone-*` 類別帶出 `--tc`）：

| 種類 | 判斷 | 類別 | 顏色 | 圖示 |
|---|---|---|---|---|
| 執行紀錄 | `github.com/…/actions/runs/` | `tone-wait` | 琥珀 `--wait` | 終端機 |
| 原始碼 | `github.com/…/blob/` | `tone-ok` | 墨綠 `--beam` | 角括號 |
| 論文 | `arxiv.org` | `tone-skip` | 灰 `--muted` | 文件 |
| 文件 | 其他 | `tone-resume` | 藍 `--note` | 書 |

### 腳本（`Tech.html`）

```js
all('p.agent-sources').forEach(function(p){
  var links=Array.prototype.slice.call(p.querySelectorAll('a[href]'));
  if(links.length<2){return;}
  var title=(p.firstChild&&p.firstChild.nodeType===3?p.firstChild.textContent:'').replace(/[：:\s]+$/,'');
  var box=document.createElement('div');
  box.className='src-cards';box.setAttribute('role','group');box.setAttribute('aria-label',title||'依據連結');
  box.innerHTML=(title?'<p class="src-cards-h">'+esc(title)+'</p>':'')+'<div class="src-cards-row">'+links.map(function(a,i){
    var k=srcKind(a.href),host=String(a.hostname||'').replace(/^www\./,'');
    return '<a class="src-card '+k[2]+'" href="'+esc(a.getAttribute('href'))+'" target="_blank" rel="noopener" style="--i:'+i+'">'+
      '<span class="src-ico">'+srcIco[k[0]]+'</span><span class="src-txt"><small>'+k[1]+'</small><b>'+esc(a.textContent)+'</b><i>'+esc(host)+'</i></span>'+
      '<span class="src-go" aria-hidden="true">↗</span></a>';
  }).join('')+'</div>';
  p.hidden=true;p.parentNode.insertBefore(box,p.nextSibling);
  /* 點任何一張卡片：這一組全部停下 */
  box.addEventListener('click',function(e){var t=e.target;while(t&&t!==box){if(t.classList&&t.classList.contains('src-card')){box.classList.add('is-stopped');return;}t=t.parentNode;}});
  /* 滑進畫面才播放；離開畫面暫停待機動畫；減少動態或不支援時直接顯示 */
  if('IntersectionObserver' in window && !reduce){
    new IntersectionObserver(function(entries){entries.forEach(function(en){
      box.classList.toggle('is-live',en.isIntersecting);if(en.isIntersecting){box.classList.add('is-in');}
    });},{threshold:0.3}).observe(box);
  }else{box.classList.add('is-in','is-stopped');}
});
```

### 樣式（`Stylesheet.html`）

```css
.agent-lab .src-cards-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr)); gap:14px; }
.agent-lab .src-card { position:relative; overflow:hidden; display:flex; align-items:center; gap:12px; min-height:76px; padding:14px 16px;
  border:1px solid var(--rule); border-left:4px solid var(--tc,var(--muted)); border-radius:16px; background:var(--panel); opacity:0; }
/* 光帶：蓋在卡片上的一層，用 transform 由左掃到右 */
.agent-lab .src-card::after { content:''; position:absolute; inset:0; pointer-events:none; transform:translateX(-110%);
  background:linear-gradient(100deg,transparent 0 35%,color-mix(in srgb,var(--tc,var(--beam)) 24%,transparent) 50%,transparent 65% 100%); }
/* 進場：依序從左、從右、從下滑入（--i 是第幾張，用來錯開） */
.agent-lab .src-cards.is-in .src-card { animation:srcInL .55s cubic-bezier(.22,1,.36,1) both; animation-delay:calc(var(--i) * 110ms); }
.agent-lab .src-cards.is-in .src-card:nth-child(3n+2) { animation-name:srcInR; }
.agent-lab .src-cards.is-in .src-card:nth-child(3n)   { animation-name:srcInUp; }
/* 待機：只在畫面內，每張卡片輪流被掃一次、箭頭輕推 */
.agent-lab .src-cards.is-in.is-live .src-card::after { animation:srcSweep 4.4s cubic-bezier(.4,0,.2,1) infinite; animation-delay:calc(1.1s + var(--i) * 550ms); }
.agent-lab .src-cards.is-in.is-live .src-go { animation:srcNudge 4.4s ease-in-out infinite; animation-delay:calc(1.1s + var(--i) * 550ms); }
@keyframes srcInL { from { opacity:0; transform:translateX(-28px); } to { opacity:1; transform:none; } }
@keyframes srcInR { from { opacity:0; transform:translateX(28px); } to { opacity:1; transform:none; } }
@keyframes srcInUp { from { opacity:0; transform:translateY(22px); } to { opacity:1; transform:none; } }
@keyframes srcSweep { 0% { transform:translateX(-110%); } 26%,100% { transform:translateX(110%); } }
@keyframes srcNudge { 0%,8%,30%,100% { transform:none; } 18% { transform:translate(3px,-3px); } }
/* 點了就停：動畫關掉、卡片維持可見 */
.agent-lab .src-cards.is-stopped .src-card, .agent-lab .src-cards.is-stopped .src-card::after, .agent-lab .src-cards.is-stopped .src-go { animation:none !important; }
.agent-lab .src-cards.is-stopped .src-card { opacity:1; }
@media (prefers-reduced-motion: reduce) { .agent-lab .src-card { opacity:1; } }
```

### 三個狀態類別就是整套機制，其他元件照用

| 類別 | 誰加上 | 意思 |
|---|---|---|
| `is-in` | 觀察器第一次看到 | 進場動畫開始（只會加一次） |
| `is-live` | 觀察器，進出畫面時切換 | 待機循環可以播放 |
| `is-stopped` | 使用者點了卡片 | 這一組所有動畫關掉 |

注意：卡片預設 `opacity:0`，靠進場動畫的 `both` 留在看得見的狀態；所以「停下」與「減少動態」兩種情況都必須另外寫 `opacity:1`，否則卡片會消失。驗收腳本 `scripts/check_source_cards.py` 會檢查這一點，也檢查：連結網址與另開分頁的屬性沒有被改、還沒滑到不播放、三種方向都有用到、keyframes 只動 `opacity` 與 `transform`、點一組不影響另一組、離開畫面暫停。

### 還可以套用這個元件的地方

- `Tech.html` 約 235 行：「查看計算方式與來源」摺疊區裡的三個連結（Gemini 3.5 Flash Lite 規格、當次執行日誌、19 筆用量與加總）。
- 「9 月 23 日」時間線右側的八顆「↑ 查看每日整理／影片與原稿／每輪判讀留底…」小按鈕：它們是跳到資料表關聯圖的站內連結，可以改成同一套卡片的小尺寸版本（只有圖示與名稱），顏色依它指向的資料表所屬的欄（來源、判讀、紀錄、行情、衍生結果、網站與寄信）。
- 「關於作者」的聯絡連結（`.author-links`）。
- 常見問題裡提到其他章節的地方：改成站內卡片連結（圖示用章節的圖示，點了捲到那一章）。
- 其他分頁：郵件查詢每一期下方可以放「這一期的原稿」「這一天的總覽」兩張站內卡片；個股頁「每天講了什麼」每一筆可以有一張「看當天整理」的小卡。

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
- 觸發方式（管理者 2026/10/13）：**滑到那一段才自動播放；使用者點了那一組裡的任何一張卡片，動畫就停**。待機用的循環動畫只在畫面內播放（離開畫面暫停），幅度要小、一輪至少 4 秒；需要使用者按「播放」才有意義的示範（例如三層分工的資料包）仍由按鈕觸發。實作照 3B 節。
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
python scripts/check_source_cards.py             # v176 連結卡片：網址不變、滑到才播放、點了就停、離開畫面暫停、不溢出
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

---

## 11. 跳脫既有框架的提案（技術說明頁整頁重新設計）

管理者 2026/10/13：呈現方式不一定要照原版，可以跳脫既有框架，有優化空間盡量提，不限數量。以下是把整頁逐章看過（電腦 1440px 與手機 390px 截圖）之後的提案；每一項都可以單獨做。先說整體的觀察：

- 整頁高 26,400px（電腦），18 章、約 16,000 字、160 顆按鈕。內容紮實、每章都有可操作的情境，但讀者要捲 29 個螢幕才看得完。
- 版面元件其實只有四種反覆出現：膠囊按鈕列（選情境）、白底圓角卡片、帶左側色條的結論條、編號步驟卡。章與章之間看起來很像，沒有哪一章有自己的「主視覺」。
- 真正的圖只有三處：開場的五站路線（有圖示、可播放）、資料表關聯圖（節點與連線）、沒講名字那一章的價位軸。其餘「圖」都是文字卡片。
- 開場四張痛點卡是全版直排（管理者 10/09 的要求），每張左邊一組對話、右邊三條說明，四張共約 1,400px 高。

### 11.1 閱讀深度：同一頁提供「三分鐘版」與「完整版」

- 頁首加一個兩段式切換：「先看重點」／「完整說明」，預設「先看重點」。選擇記在 `localStorage`（讀寫包 try/catch，取不到就用預設）。
- 「先看重點」：每一章只留四樣——章名、一句話結論（現有的螢光筆標示句就是）、一張主圖解、一個最有代表性的情境按鈕列。其餘段落、補充清單、來源收進「展開細節」。目標是整頁 9,000px 以內。
- 「完整說明」就是現在的內容全部展開。
- 沒有腳本時全部展開（維持現有的 no-JS 檢查）。
- 每章右上角放一顆「展開／收合這一章」，鍵盤可操作，`aria-expanded` 正確。

### 11.2 把 18 章編成四幕，換掉右側的「子目錄」

- 四幕：**為什麼做**（開場）→ **資料怎麼流**（資料從哪裡來、載入速度、9/23 那一天）→ **怎麼判讀**（模型推論、聽錯股名、誰在賣、哪個是股價、沒講是哪一檔、來源先後、哪句能公開）→ **怎麼確保可靠**（新舊稿、持股回合、失敗時、限制、還沒解決的事、常見問題、作者）。
- 電腦：左側固定一條細的進度軌（寬 56px，可展開到 220px），四幕各一個圖示，下面是該幕的章節小圓點；目前讀到的章節圓點變實心、已讀過的變色。點圓點捲到那一章。
- 手機：底部固定一條四段的進度條（高 44px），點任何一段彈出那一幕的章節清單。現有浮在右下的「回到頂端」「問問題」兩顆按鈕併進這條進度條的兩端，不再蓋住內文（現在「回到頂端」的膠囊固定在畫面下方正中央，截圖裡每一章都被它壓住一行字）。
- 每一幕的開頭加一張「幕首卡」：一個大圖示、幕名、這一幕回答的三個問題。幕與幕之間換底色（`--bg` 與 `--panel-2` 交替），讓長頁面有段落感。

### 11.3 一筆紀錄貫穿全頁（同一個例子走到底）

- 現在每一章各用各的例子（9/23 力積電、10/08 金山電、10/13 鴻海、鴻準、勤誠 900…），讀者每章都要重新進入狀況。
- 選一筆真實紀錄當主線（建議 10/13 鴻海「先前 260 元出場、今天不是賣出、列觀望注意」，這一筆在模型推論一章已經用了），做成一張固定樣式的「紀錄票卡」：左邊日期與股名、中間一句原話、右邊目前狀態章。
- 這張票卡出現在每一章的主圖解裡，樣式不變、只有狀態章在變：`原稿` → `聽打完成` → `候選：賣出？` → `核對日期：10/06` → `改列觀望注意` → `寫入` → `上站`。讀者一眼看得出「這一章在處理票卡的哪一步」。
- 各章原有的其他例子保留在情境按鈕裡當「再看別的情況」。
- 頁面最上方的五站路線（開場那一張）改成全頁的迷你地圖：捲動時固定在標題列下方縮成一條（高 40px，五個站點），目前這一章對應的站點亮起。

### 11.4 兩個最長的章節改成「左圖右文、捲動換圖」

- 「新稿來了，舊工單會不會蓋回去？」已經是這種結構（`.tx-sticky` 加 `.tx-scrolly`），但左邊固定的是六格文字狀態。照 5.6 換成兩條軌道圖之後，右邊每捲到一個情境，左邊的軌道重畫。
- 「AI 是怎麼讀原稿…」（3,787px）照同樣的做法：左邊固定一張大的三層圖（控制程式／模型／工具），右邊依序是六個問題；捲到哪一題，左邊的圖就高亮那一題相關的部分並播放對應的小動畫（例如捲到「字怎麼變成輸入」時，控制程式到模型之間的那條線上出現被切開的小單位）。六題的膠囊按鈕列保留，當成跳題用。
- 捲動觸發一律用 `IntersectionObserver`（`threshold` 0.5、`rootMargin` 上下各留 20%），不要綁 `scroll` 事件逐幀計算。手機不固定圖，改成圖在上、文在下，逐題各自一張。

### 11.5 每章一張主視覺（不是卡片排成的流程，而是一個「東西」）

比 5.1～5.13 再進一步，不限於把步驟畫出來，而是替每一章找一個實物比喻當主視覺，全頁共用同一種線條風格（1.8px 線、圓角、單色加一個強調色）：

| 章 | 主視覺 | 會動的部分 |
|---|---|---|
| 資料從哪裡來 | 一條有分岔與匯流的輸送帶，票卡在上面移動 | 票卡依工作順序前進，經過哪一站那一站亮 |
| 載入速度 | 三層書架（手機、快取、試算表），越下層越遠 | 一隻手伸到第幾層取資料，伸得越深越慢 |
| 模型推論 | 一張工作桌：桌上是這次給模型的文件（上下文），桌外的檔案櫃是整個資料庫 | 控制程式把文件放上桌、模型在桌上寫、工具去檔案櫃拿 |
| 聽錯股名 | 一隻耳朵接到三個相近的名字，連到公司名牌 | 連線粗細依讀音接近程度，確認過的上鎖 |
| 誰在賣 | 一句話被拆成四塊拼圖：哪一檔、誰、做了什麼、什麼時候 | 四塊各自飛到對應的欄位，缺一塊就不能歸到「當日買賣」 |
| 哪個是股價 | 一把尺（當天的高低區間），數字一個個掉上去 | 落在尺上的留下，掉到尺外的彈開並顯示單位 |
| 沒講是哪一檔 | 一個漏斗，或一排蓋著的名牌逐一翻開 | 符合昨收的留下，再符合今天高低的留下 |
| 新舊稿 | 兩列火車在平行軌道上，新車進站時舊車被引到側線 | 見 5.6 |
| 來源先後 | 兩封信先後投進同一個信箱，先到的蓋了章 | 後到的只把內容附在後面 |
| 哪句能公開 | 一張稿紙經過三道閘門（有原句、數字對得上、主詞對） | 被擋下的句子留在閘門外並標原因 |
| 持股回合 | 一條時間線上的幾段色帶（每段一個回合），下面是價格線 | 回合起訖點落下圖釘 |
| 失敗時 | 四條平行的管線，其中一條出現斷點與繞回的重試弧線 | 見 5.9 |
| 限制 | 左右對開的兩欄：「能幫你做的」「做不到的」，各四個圖示 | 滑入時左右兩欄從兩側合攏 |
| 還沒解決的事 | 三張便利貼（發生了什麼、試過什麼、想試什麼），依狀態分色 | 依序貼上 |

### 11.6 開場重新安排（維持全版直排，但壓到一半高度）

- 四張痛點卡仍然全版直排（管理者要求），但每張改成三欄：左邊對話（保留現有的進場動畫）、中間一張「答案長什麼樣子」的小圖（該功能的縮圖：每日總覽的五個分類計數、持股追蹤的一張卡、觀望清單的三列、績效走勢的一條線——用 SVG 畫的示意，不是截圖）、右邊一張可以點的站內卡片「去看每日總覽 →」。現在右邊的三條文字說明收進「為什麼需要這個」摺疊。
- 三個數字卡（70 天、1,460 筆、243 檔）加上「怎麼來的」：每張下面一條細的累積曲線（資料可從 `apiGetTechStats` 取得的才畫；取不到就不畫，不要用假資料）。
- 「我是李世勛…」那一句自我介紹移到頁尾作者區，開場只留網站在做什麼。

### 11.7 資料表關聯圖

- 現況：六欄節點加灰色連線，點節點看說明。連線多的地方（判讀到紀錄之間）糊成一片。
- 改法：平常只顯示節點，連線預設隱藏；滑過或點一個節點時，只畫出它的上游與下游，並依方向用兩種顏色（進來的藍、出去的綠），線條用 `stroke-dashoffset` 畫出來（已有 `@keyframes draw`）。
- 加一顆「跟著一筆紀錄走」：票卡從「影片與原稿」出發，依序走過它會經過的節點，走過的連線留下顏色。
- 手機：改成六個可展開的區塊（每欄一個），展開後列出節點；點節點時在下方顯示「它從哪裡來、往哪裡去」兩行，不畫連線。
- 加圖例：框線顏色代表哪一欄、虛線代表備援。

### 11.8 「9 月 23 日」時間線

- 現況：左邊時間、右邊文字的清單，間距相同。
- 改法：做成真正按比例的時間軸（10:10 到收盤後），上下兩條泳道（即時紀錄、影片），事件用圖釘釘在對應的時間；人工改回那一步用不同的圖釘形狀（手）。時間集中的區段（11:05～12:21）可以點一下放大。
- 下方「換成其他日子，會怎麼走」四個情境切換時，圖釘重新排列（影片晚到：影片泳道的圖釘整排往右移）。

### 11.9 情境按鈕列

- 現況：全頁十多處用膠囊按鈕選情境，有的一列四顆、有的兩列八顆；手機上每顆占一整列，八顆就是八列。
- 改法：手機改成可左右滑的一列（`overflow-x:auto; scroll-snap-type:x mandatory`），右側淡出提示還有更多，並在列尾顯示「3／8」。電腦維持格狀，但超過六顆時分成「常見」與「更多」兩組。
- 每顆按鈕前面加一個 12px 的狀態點，顏色就是點了之後結果條的顏色（綠＝可公開、琥珀＝待複核、紅＝不列），讓讀者在點之前就知道八個情境大概分成幾種結果。

### 11.10 結論條與「得到什麼」

- 現況：每個互動下面一條淡綠底的結論（`.agent-output`、`.tech-verdict`），全頁長得一樣。
- 改法：結論條左邊放一個會跟著結果變的大圖示（勾、驚嘆號、叉、沙漏），底色跟著結果的語意色變；切換情境時圖示用 0.2 秒的翻面換過去。這樣讀者不必讀字就知道結果變了。

### 11.11 截圖

- 「資料量與載入速度」最下面那張正式站截圖是深色主題的畫面放在淺色頁面裡，而且是 2480×1400 的 JPG 縮到 1082px。
- 改法一：換成用 HTML 與 CSS 畫的迷你首頁（跟著目前主題），上面標三個編號熱點（①先顯示的日期與分類計數、②之後才載入的分頁、③背景更新時間），點熱點顯示說明。
- 改法二（保留截圖）：淺色、深色各一張，用 `<picture>` 依主題切換，並加上同樣的編號熱點。

### 11.12 常見問題與作者

- 常見問題（1,100 字，現在全部展開）：改成可搜尋的摺疊清單，上方一列分類膠囊（資料來源、準確度、訂閱、隱私）；每題的答案如果在前面某一章有圖，放一張站內卡片連過去。
- 作者區：聯絡方式改成 3B 的連結卡片；信箱旁邊加一顆「複製」。

### 11.13 效能（頁面變長、圖變多之後要顧）

- 每一章加 `content-visibility: auto; contain-intrinsic-size: auto 900px;`，離畫面遠的章節不排版、不繪製。
- 圖解一律「第一次看到才組」（照 `tokBuild` 的 `dataset.ready`）；觀察器在不需要時 `unobserve`。
- 同一時間最多一組待機動畫在跑：進入新的一組時，把前一組的 `is-live` 拿掉。
- 動畫元素加 `will-change: transform` 只在播放期間，播完移除。
- `Tech.html` 現在 150KB、樣式表五千多行都是行內載入；新增的 SVG 圖示集中成一個 `<svg><symbol>` 圖庫，用 `<use href="#i-…">` 引用，不要每次貼一份路徑。

### 11.14 一致性清單（做任何一項都要對過）

- 圖示：全頁一套，24px 格線、1.8px 線寬、圓頭。需要的概念至少有：影片、即時紀錄、聲波、原稿、指紋、放大鏡、盾牌勾、表格、瀏覽器、信封、書架、時鐘、資料包、剪刀、詞彙表、數字格、鎖、警示、重試、暫停、勾、叉、手（人工）、漏斗。
- 語意色：來源＝藍（`--note`）、判讀與通過＝墨綠（`--beam`）、等待與待複核＝琥珀（`--wait`）、停止與不列＝紅（`--rise`，注意全站同時用紅色表示上漲，圖解裡的紅色一律搭配叉或斜線圖示）、略過＝灰（`--muted`）。
- 每個示範右上角固定一個小標「示意・不連線」，取代現在各章各自寫的一句說明。
- 章名的寫法統一成問句或一句結論（現在兩種混用）。
- 每章的第一個螢幕（電腦 900px 高）內一定要看得到主圖解，不能先讀三段文字才看到圖。

### 11.15 排版是否方便：目前的結論

好的部分：沒有任何寬度出現整頁橫向捲動；按鈕觸控高度普遍足夠；淺深色都完整；減少動態有總開關；沒有腳本時文字都在。

不方便的地方（依影響排序）：

1. 太長：18 章 26,400px，沒有閱讀深度的選擇（11.1）。
2. 找不到自己在哪：右側「子目錄」是收合的小籤，沒有進度感（11.2）。
3. 浮動的「回到頂端」固定在畫面下方中央，蓋住內文一行（11.2）。
4. 手機上情境按鈕一顆一列，八個情境就要捲兩個螢幕才看到結果（11.9）。
5. 章與章長得一樣，沒有視覺上的記憶點（11.5）。
6. 例子每章都換，讀者要重新理解背景（11.3）。
7. 小字與末行孤字（第 4 節）。
