# 戰情技術設定：完整 AI 實作 Prompt

整理日期：2026-10-06。以下保存上一輪已交付的研究與執行規格；本檔是規格，不代表功能已部署。

本文件保存上一輪研究成果：**62 份不同的官方／研究文件**，來自 **13 個機構或平台**，另外列出張震相關節目轉錄出處。上一輪只交付規格，沒有依此修改產品、推送或部署；本輪則依使用者要求保存為 Markdown。張震的擴充研究另見 [張震選股操作邏輯：來源研究與 AI 實作 Prompt](張震選股操作邏輯-來源研究與AI實作Prompt.md)。

先說研究結論：

1. **目前需要增加的是判定能力，不只是指標數量。** 現有程式已有條件分組，但主要仍是八類條件；需要補上交叉、先後順序、持續時間、多週期確認與完整出場規則。
2. **「常用」不等於「已證明在台股有效」。** 券商與主流平台提供這些工具，可證明它們是成熟、廣泛提供的分析方法；具體組合及門檻仍須另外回測。[Schwab 指標選擇](https://www.schwab.com/learn/story/choosing-technical-indicators-to-analyze-stocks)、[原始研究：技術交易與資料探勘偏誤](https://www.fmg.ac.uk/publications/discussion-papers/data-snooping-technical-trading-rule-performance-and-bootstrap)
3. **爆量跳空不一定適合進場。** 必須加入位階、收盤位置、缺口是否守住及突破是否失敗；高檔爆量跳空也可能成為出場觀察條件。[StockCharts 缺口分析](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis)
4. **開盤成交量不能直接與完整日均量用同一方式判斷。** 盤中應優先比較歷史同時段累計量；沒有這種資料時，必須清楚標示實際日量倍數或估量，不能混稱量比。[TradingView 同時段相對量](https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/)、[XQ 估計量](https://xshelp.xq.com.tw/XSHelp/?HelpName=%E4%BC%B0%E8%A8%88%E9%87%8F&group=QVOLUME)

以下是可直接交給後續實作 AI 的完整 Prompt。文中的 `S01～S62` 對應最後的來源表；張震材料另用 `Z01～Z06`，不混入官方技術文件的證據等級。

---

# AI Prompt：戰情追蹤的進場／出場訊號、進階條件與視覺化設定改版

## 一、任務與目標

你要擴充「盤勢有據」網站的「戰情追蹤」與個股介面中的戰情卡片，建立可以解釋、組合、追溯來源的技術條件系統。

網站：

[https://lee200202.github.io/Stock/](https://lee200202.github.io/Stock/)

實作時先閱讀 Git 追蹤的來源：

- `public-site/gas-source/Battle.html`
- `public-site/gas-source/BattleConfig.html`
- `public-site/gas-source/BattleData.gs`
- `public-site/gas-source/Index.html`
- `public-site/gas-source/JavaScript.html`
- `public-site/gas-source/Stylesheet.html`
- 必要的行情快取、報價及 API 程式。

先對照最新程式與正式站，不得依舊版描述覆寫現有功能。現有「且／或分組」、股票搜尋、指定股票優先載入及使用者設定都要納入相容性設計。

本規格的核心目標是：

> 讓使用者先選擇進場或出場，再選交易方向、分析週期與量價門檻，最後加入技術條件；每一個符合結果都能看見原因、實際數值、判定時間與依據。

條件系統必須有足夠彈性，但基本操作要容易理解。不能把所有指標與參數一次攤滿畫面。

本輪研究本身不執行程式修改或部署。以下內容供後續實作使用。

---

## 二、必須維持的產品規則

### 2.1 分成兩大類

主分類：

- **進場訊號**
- **出場訊號**

預設以台股現股多方情境設計：

- 進場：觀察可能建立多方部位的條件。
- 出場：觀察多方持股可能減碼、停利或停損的條件。

「向下跳空」不能直接等同「放空進場」。

若後續提供空方策略，必須另外建立「空方進場／空方回補」語意，不能將多方規則全部乘以 `-1` 就當成完整空方策略。

### 2.2 技術訊號與網站原始分類分開

戰情計算不得：

- 改寫講者對股票的分類。
- 把「技術條件符合」改成「分析師推薦」。
- 把技術出場訊號寫成實際已賣出。
- 修改持股回合、成本或原始操作紀錄。
- 觸發文章重產、績效重算或 Email／LINE 寄送。

戰情是獨立的行情分析層。

### 2.3 結果清單只顯示符合者

沿用使用者已指定的規則：

- 未符合者不列在主要清單。
- 不恢復「待核對股票」區塊、數量或大量提示。
- 指定股票查詢可以顯示簡短的「目前未符合所選條件」。
- 資料不足、請求失敗與真的不符合，程式內部必須分開記錄。
- 不得將請求失敗顯示成「沒有符合股票」。

### 2.4 數值設定集中在戰情設定

策略數值放在「戰情設定」：

- 指標週期。
- 量能倍數。
- 跳空比例。
- 突破緩衝。
- 回測容許範圍。
- 持續根數。
- 出場與停損參數。

全站版面設定只管排版、色彩、密度與視覺呈現。

不得再次要求一般使用者輸入程式碼或修改 JSON。

---

## 三、依據與命名規則

每個條件及預設策略必須區分三種性質：

| 性質 | 可使用的標示 | 意義 |
|---|---|---|
| 有官方定義或平台參數 | 常見指標／平台常見參數 | 公式或預設值有文件支持 |
| 本站自行組合與量化 | 本站研究設定 | 組合與門檻是工程設計，尚非通用標準 |
| 參考張震節目觀點 | 張震觀點・量化研究版 | 觀點有出處，具體程式門檻另行定義 |

禁止以下命名：

- 主力必買。
- 保證突破。
- 高勝率策略。
- 法人進場確認。
- 張震官方選股公式。

除非另有可靠資料與驗證，價格及成交量推估不能確認交易者身分。

John Bollinger 明確要求避免使用彼此高度相關的指標來假裝多重確認；同時也指出碰到上軌或下軌本身不是買賣訊號。此原則必須反映在規則設計。[S42：Bollinger 原作者規則](https://www.bollingerbands.com/bollinger-band-rules)

---

## 四、使用者設定的操作順序

### 第一步：選目的與方向

主按鈕：

`進場訊號`　`出場訊號`

第二層：

- 進場：突破續強、回測承接、反轉確認、自訂。
- 出場：保護部位、趨勢轉弱、突破失敗、獲利保護、自訂。

價格方向另設：

`向上`　`向下`　`不限制`

不能讓「進場／出場」和「向上／向下」共用同一個變數。

### 第二步：選分析週期

支援資料足夠的：

- 日 K。
- 60 分 K。
- 15 分 K。
- 5 分 K。
- 週 K。

基本模式一次選一個主週期；進階模式可以增加另一個確認週期。

每個條件自行保存週期，不能因使用者切換個股圖表就偷偷改變策略。

### 第三步：選量價門檻

提供：

- 成交量比較方式。
- 比較天數。
- 倍數。
- 流動性門檻。
- 跳空判定方式。
- 價格或成交金額範圍。
- 是否使用收盤確認。

### 第四步：加入技術條件

以分類抽屜或面板選擇：

- 趨勢。
- 突破與支撐。
- 動能。
- 波動與缺口。
- 成交量。
- K 棒結構。
- 部位與出場。
- 張震觀點研究版。

### 第五步：組合並查看結果

可選預設策略，也可用條件積木組合。

必須同時顯示自然語言摘要，例如：

> 日 K 趨勢向上，且「突破前高或回測支撐完成」，再加上同時段量能達門檻。

---

## 五、成交量：必須先處理好的計算口徑

### 5.1 完整日量倍數

```text
日量倍數(t, N)
= 第 t 個交易日實際成交量
  ÷ 前 N 個已完成交易日的平均成交量
```

平均量必須排除當日。

`1 倍`表示達到均量，不應直接標成爆量。

目前設定中既有的 `1 倍`預設及使用者已保存的數字要保留，不得在升級時偷偷換成 `2.5 倍`。

### 5.2 盤中同時段累計量比

```text
同時段累計量比(t, τ, N)
= 今天截至 τ 的累計成交量
  ÷ 前 N 個有效交易日截至相同 τ 的累計成交量平均
```

要求：

- 分子、分母的時段必須一致。
- 不得拿今天尚未完成的 09:10–09:15 K，比較歷史完整的同一根 K。
- 可先採「最近已完成 5 分 K」作共同截止點。
- 排除無法確認資料完整性的歷史日。
- 真正零成交、行情缺漏、停牌不能混為一種情況。
- 若歷史樣本不足，不得回退成完整日均量後仍沿用「同時段量比」名稱。

TradingView 明確區分一般相對量與同時段相對量，也提供單根量和累計量兩種模式。[S37：同時段量](https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/)、[S38：相對量計算](https://www.tradingview.com/support/solutions/43000635874-how-do-we-calculate-relative-volume-and-relative-volume-at-time/)

### 5.3 預估全日量

優先使用有歷史日內分布的估算：

```text
預估全日量
= 今天同時點累計量 ÷ 歷史同時點累計量占全日量比例
```

必須定義比例的估計方式、樣本數及異常日處理。

XQ 官方估計量採歷史分鐘累計量分布來推算全日量，不能將本站簡化算法宣稱為相同方法。[S44：XQ 估計量](https://xshelp.xq.com.tw/XSHelp/?HelpName=%E4%BC%B0%E8%A8%88%E9%87%8F&group=QVOLUME)

目前若仍保留：

```text
累計量＋日均量×剩餘分鐘／全日分鐘
```

只能命名為「簡化估量」，並清楚與：

- 實際成交量。
- 同時段量比。
- 歷史分布估量。

分開呈現。

### 5.4 開盤量能的預設建議

**沒有找到適用所有台股、所有開盤分鐘的固定倍數標準。不得編造。**

可設計以下可調研究預設：

| 用途 | 研究起始值 | 必須標示 |
|---|---:|---|
| 日量達均量 | 1.0 倍 | 達均量，不叫爆量 |
| 放量觀察 | 1.5 倍 | 本站研究設定 |
| 較明顯量能擴張 | 2.0 倍 | 本站研究設定 |
| 嚴格爆量篩選 | 2.5 倍 | 本站研究設定 |
| 極端量能觀察 | 4.0 倍以上 | 同時檢查反轉風險 |

StockCharts 的 RVOL 文件提到不少日內交易者會注意 `2 倍以上`，但這不能直接證明「台股開盤同時段量比 2 倍」具有特定勝率。[S31：RVOL](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol)

新建盤中策略可提供 `2.0 倍`作測試起點，但：

- 必須使用同時段口徑。
- 明示為研究設定。
- 不改寫既有預設。
- 不將門檻越高解釋成越安全。

### 5.5 流動性與爆量分開

應提供兩個獨立條件：

1. 過去 N 日平均成交量／成交金額達到門檻。
2. 今天量能相對歷史有沒有擴張。

不能強迫股票在剛開盤就達到「全日最低 1,000 張」才能通過所有盤中策略。

盤中最低實際量可設為額外選項，但要與平均日流動性分開。

### 5.6 成交量單位必須驗證

富果官方文件目前明列：

- 整股分 K：張。
- 整股日／週／月 K：股。
- 興櫃與指數另有不同口徑。

因此必須在資料正規化層換算，不能在 UI 算式臨時猜測。[S51：盤中 K](https://developer.fugle.tw/docs/data/http-api/intraday/candles/)、[S52：歷史 K 與單位](https://developer.fugle.tw/docs/data/http-api/historical/candles/)

---

## 六、進場訊號條件庫

以下是條件庫，不代表全部同時啟用。每一類必須提供可調參數及資料需求。

| 編號 | 條件 | 判定規格 | 主要依據 |
|---|---|---|---|
| E01 | 趨勢方向 | 價格在指定均線上，且均線斜率符合設定 | S04、S05、S49 |
| E02 | 均線交叉 | 快均線由下往上穿越慢均線；可限制最近幾根內發生 | S04、S49 |
| E03 | 均線排列 | 快、中、慢均線形成指定排列，與交叉事件分開 | S04、S49 |
| E04 | 前高／通道突破 | 收盤突破前 N 根最高價；最高價區間排除當根 | S18 |
| E05 | 突破後回測 | 先突破，再於指定根數內回到突破區附近並重新轉強 | S02、S34 |
| E06 | 均線回測承接 | 先有上升趨勢，再回測指定均線，最後出現反彈確認 | S02、S49 |
| E07 | MACD 轉強 | 支援 DIF／訊號線交叉、零軸交叉、柱體翻正及連續增強 | S07 |
| E08 | RSI 恢復動能 | 支援重新站上 30、50、指定區間，或多頭回測區回升 | S06 |
| E09 | KD／隨機指標交叉 | 支援低檔交叉、離開超賣區、交叉後持續上升 | S17、S55、S62 |
| E10 | ADX 趨勢啟動 | ADX 強度與上升變化，搭配 `+DI > -DI` | S08 |
| E11 | Aroon 趨勢形成 | Aroon-Up／Down 的交叉、位置及新高近期性 | S28 |
| E12 | 布林壓縮後突破 | 先有低 BandWidth，再突破上軌並加上動能／量能確認 | S10、S11、S42 |
| E13 | BB／Keltner 壓縮釋放 | 布林收在 Keltner 內，再離開；須明示公式版本 | S23、S32 |
| E14 | 開盤缺口延續 | 開盤超出昨日區間，幅度達標，且所選確認時間未回補 | S33 |
| E15 | OBV／CMF／ADL 確認 | 量價趨勢改善、零軸轉換或突破自身前高 | S12、S26、S27 |
| E16 | MFI／CCI／ROC 動能 | 提供各自轉強事件，不能一概叫超賣買進 | S13、S14、S15 |
| E17 | 相對強弱改善 | 股價相對基準或產業的比值上升、突破或轉強 | S30 |
| E18 | VWAP 重新站回 | 日內價格重新站回 VWAP，搭配持續根數或回測 | S24 |
| E19 | Anchored VWAP 承接 | 以已知事件為錨點，回測或突破其量加權均價 | S25 |
| E20 | K 棒反轉確認 | 吞噬、錘子、晨星等須有前置趨勢及後續確認 | S35 |
| E21 | 支撐假跌破收回 | 先跌破已知支撐，再收回並有後續確認 | S34；具體算法為本站研究 |
| E22 | 底部背離後確認 | 價格低點與指標低點背離，再突破確認點 | S06、S07、S17 |
| E23 | 開盤區間突破 | 開盤固定區間完成後，突破區間高點並有量價確認 | 本站研究組合，資料要求見 S51、S52 |
| E24 | 位階限制 | 限制距離均線、距離歷史低點或近期累積漲幅 | S34、S49；門檻為本站研究 |

必要說明：

- RSI 小於 30，不等於已經反轉。
- KD 大於 D，不等於今天剛黃金交叉。
- MACD 柱體為正，不等於剛翻紅。
- ADX 高，不代表向上，方向仍須看 DI 或價格。
- 布林壓縮不保證向上突破。
- 量價指標不能證明法人或主力已買進。

以上差異分別有 Fidelity、Schwab、StockCharts 的文件支持。[RSI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI)、[MACD](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd)、[ADX](https://www.fidelity.com/viewpoints/active-investor/average-directional-index-ADX)、[Bollinger 規則](https://www.bollingerbands.com/bollinger-band-rules)

---

## 七、出場訊號條件庫

出場需分成：

- **風險觀察**：沒有持股也可以查看。
- **部位出場**：必須有使用者部位或明確的模擬進場資料。

| 編號 | 條件 | 判定規格 | 主要依據 |
|---|---|---|---|
| X01 | 結構支撐跌破 | 跌破前低、突破區或指定支撐，可設收盤確認與緩衝 | S34 |
| X02 | 均線跌破／死亡交叉 | 區分价格位置、交叉事件及均線轉下 | S04、S49 |
| X03 | 突破失敗 | 突破後於指定期間收回原區間，加入量能／收盤位置 | S18、S34 |
| X04 | 向上缺口失守 | 跌入、部分回補或完全回補，三種狀態分開 | S33 |
| X05 | 向下缺口轉弱 | 向下缺口加上結構跌破；不能單靠低開判斷 | S33、S34 |
| X06 | 高檔量能異常 | 先有位階或漲幅條件，再看極端量、長上影及轉弱 | S31、S36、S46 |
| X07 | MACD 動能轉弱 | 支援死叉、柱體翻負、持續縮短，不能單獨混用 | S07 |
| X08 | RSI／KD 失敗轉折 | 極端區退回、失敗擺盪、交叉及價格確認 | S06、S17 |
| X09 | 頂部背離確認 | 背離成立後，價格跌破確認點才升级为確認訊號 | S06、S07、S17 |
| X10 | 量價轉弱 | OBV、CMF、ADL 與價格背離或跌破自身結構 | S12、S26、S27 |
| X11 | 波動通道失守 | 跌破布林中軌／Keltner 或趨勢支撐；按策略定義 | S10、S23 |
| X12 | VWAP／AVWAP 失守 | 價格跌破指定均價並完成設定確認 | S24、S25 |
| X13 | 固定比例或價格停損 | 需有進場成本，且不能將觸發價視為保證成交價 | S03 |
| X14 | ATR 初始停損 | 進場價格減去指定 ATR 倍數；保存初始風險 | S09 |
| X15 | ATR 移動停損 | 多方停損線只能上移；更新與觸發時間須定義 | S21 |
| X16 | Chandelier Exit | 提供原始指標線，另可選部位單向鎖定版本 | S20 |
| X17 | Parabolic SAR 轉向 | 適合趨勢追蹤，盤整頻繁轉向須顯示限制 | S19 |
| X18 | 相對強弱轉差 | 相對大盤或產業轉弱，再搭配價格條件 | S30 |
| X19 | 分段停利／獲利回撤 | 依 R 倍數、百分比或 ATR 設定；須有部位成本 | S03、S09；組合為本站研究 |
| X20 | 時間出場 | 持有指定交易日仍未達策略目標或趨勢未延伸 | 本站研究規則 |
| X21 | 大盤風險加上個股轉弱 | 多週期及市場條件共同成立才提示 | S02、S30；組合為本站研究 |
| X22 | 反轉 K 棒確認 | 高檔吞噬、暮星、射擊之星等加上後續下跌確認 | S36 |

**禁止把「漲很多」「量很多」「RSI 高於 70」單獨宣稱為應立即賣出。**

保護性出場和較慢的趨勢出場要分開；使用者可以採：

```text
結構停損
或
ATR 保護出場
或
（高檔背離 且 支撐跌破）
```

但不應把必要保護出場強迫放進需要所有條件同時成立的組合。

---

## 八、常見參數與研究參數必須分開

| 工具 | 可提供的常見起始參數 | 注意事項 |
|---|---|---|
| 台股均線 | 5、10、20、60、120、240 根 | 交易根數，不是日曆天；來源 S49 |
| 國際長期均線 | 50、200 根 | 不把 200MA 和台股 240MA 當同一條 |
| MACD | 12／26／9 | 計算版本、初始化與柱體縮放須一致；S07 |
| RSI | 14；30／70 | 強趨勢可長期留在極端區；S06 |
| 台股 KD | 9／3／3 | 保存台股平滑版本；S55、S61、S62 |
| Full Stochastic | 14／3／3 | 與台股遞迴 KD 分開命名；S17 |
| ADX／DMI | 14；20、25 為常見參考帶 | ADX 強度不代表方向；S08 |
| Bollinger | 20／2 | 不聲稱價格必有 95% 機率在帶內；S42 |
| ATR | 14 | 非方向指標；S09 |
| Chandelier | 22／22／3 | 原始線可能下降，與鎖定停損分開；S20 |
| Aroon | 25 | 觀察距離新高／低的時間；S28 |
| MFI | 常見 20／80 參考帶 | 不是單獨反轉確認；S13 |
| CCI | 常見 ±100 | 可調，不同股性可能不同；S14 |
| Williams %R | 14；-20／-80 | 與 Stochastic 高度相關；S16 |
| Ichimoku | 9／26／52，位移 26 | 必須處理時間對齊；S22 |
| Keltner | EMA20、ATR10、倍數2 的平台版本 | 不與其他版本混稱；S23 |

以下數字均屬本站研究設定：

- 跳空 2.5%。
- 爆量 2.5 倍。
- 回測容許 1%。
- 突破緩衝 0.2 ATR。
- 均線糾結 3%。
- BandWidth 歷史百分位 20%。
- 三日不破低。
- 高檔近 60 日漲幅 20%。

可以作預設測試值，但每一個都要可調，且不能寫成市場一致公認的標準。

---

## 九、至少提供的進場組合範本

以下範本是**本站研究組合**。引用來源支持指標及判讀原理，不代表來源已驗證這整套條件。

### I01｜趨勢放量突破

```text
收盤 > SMA60
且 SMA60 五根變化 > 0
且 收盤突破前20根最高價＋0.2×ATR14
且 量能倍數 ≥ 1.5
```

可調：均線、斜率期間、突破區間、ATR 緩衝、量能口徑。

### I02｜突破後縮量回測

```text
最近10根內曾突破前20根高點
然後
回測突破價±0.5×ATR14
且 回測期間量能低於突破期
然後
收盤突破回測確認棒高點
```

需要保存當時的突破價，不能每一根重新用最新前高替換。

### I03｜均線承接再轉強

```text
SMA60向上
且 收盤在SMA60上方
且 最近5根曾接近EMA20
且 回測量能縮小
且 今日收盤突破昨日高點
```

### I04｜布林壓縮後突破

```text
此前BandWidth處於過去120根的低20%區
且 今日收盤突破布林上軌
且 MACD柱體 > 0
且 量能倍數 ≥ 1.5
```

BandWidth 百分位及確認順序可調。

### I05｜波動壓縮釋放

```text
前一根為BB位於Keltner內
且 當根離開壓縮狀態
且 動能方向向上
且 收盤突破區間高點
```

若採現代 EMA／ATR Keltner 版本，名稱用「BB／KC 壓縮」，不要宣稱完全複製另一平台的 TTM Squeeze。

### I06｜爆量開盤跳空續強

```text
開盤 > 昨日最高價
且 開盤較昨收 ≥ 2.5%
且 所選量能倍數 ≥ 2.0
且 所選確認時間缺口仍存在
且 收盤位置 ≥ 當根區間的70%
且 未超過使用者設定的追價距離
```

盤中與盤後分別評估，不將盤中候選寫成已完成日 K 缺口。

### I07｜多頭 RSI 回測恢復

```text
主要趨勢向上
且 RSI14曾回到40～50區間
且 RSI重新站上50
且 價格突破短期確認點
```

40～50 是多頭 RSI 行為的參考區，並非任何個股必守區。

### I08｜KD 低檔交叉確認

```text
K／D曾處於20以下
且 K向上穿越D
且 K重新站上20
且 價格出現確認突破
```

允許「交叉與站回」同根或不同根，時序由使用者選擇。

### I09｜ADX 趨勢啟動

```text
ADX14 ≥ 25
且 ADX連續上升
且 +DI > -DI
且 價格突破前高
```

避免把 ADX 高、但實際下跌的股票列為多方進場。

### I10｜量價與相對強弱確認

```text
價格突破
且（OBV創前高 或 CMF持續為正）
且 股價／基準指數比值向上
```

OBV、CMF 不得命名為「法人已進場」。

### I11｜盤中 VWAP 回測續強

```text
主週期趨勢向上
且 價格先站上VWAP
然後 回測VWAP附近
然後 已完成短週期K重新轉強
且 同時段量比達門檻
```

需要可靠日內行情；沒有資料時不能用日 K 均價代替。

### I12｜支撐假跌破收回

```text
此前有已知支撐
且 價格跌破支撐
且 指定根數內收回支撐
且 突破確認棒高點
且 保護風險距離符合設定
```

屬研究算法，需特別測試未來資料污染。

---

## 十、至少提供的出場組合範本

### O01｜結構失守

```text
收盤 < 已保存的支撐價－0.2×ATR14
且 達到設定的確認根數
```

### O02｜突破失敗

```text
持有期間曾完成突破
且 指定期間內收盤跌回原突破區下方
且（量能放大 或 收盤位於區間下半部）
```

### O03｜缺口失守

```text
已保存向上缺口事件
且 價格達到使用者指定的回補比例
且 所選確認方式成立
```

可選：

- 觸及邊界。
- 收盤進入缺口。
- 完全回補。
- 回補後反彈失敗。

### O04｜高檔爆量開高走低

```text
近期漲幅或均線乖離達設定
且 量能倍數 ≥ 2.5
且 上影線比例 ≥ 40%
且 收盤位置 ≤ 40%
```

這些比例是研究門檻，不得將所有高量紅 K 都判成出貨。

### O05｜MACD 轉弱加價格確認

```text
MACD死叉或柱體翻負
且 價格跌破指定均線或前低
```

### O06｜高檔背離確認

```text
已確認的價格高點更高
且 同期指標高點更低
然後
價格跌破兩個高點間的確認低點
```

可以選 RSI、MACD 或 KD，但相關指標不能變成虛假的獨立三票。

### O07｜量價背離加結構破壞

```text
價格創高但OBV未創高
且 價格跌破支撐
```

可選 CMF／ADL 版本；明示各自公式。

### O08｜ATR 部位保護

```text
價格觸及或收盤跌破部位ATR停損線
```

觸及與收盤確認要分開；不需要再等其他動能條件。

### O09｜Chandelier 獲利保護

```text
收盤跌破 Chandelier 指標線
```

另提供「單向鎖定部位停損」版本，不能把兩種結果混在同一名稱。

### O10｜SAR 轉向

```text
多方SAR轉至價格上方
且 可選價格結構確認
```

### O11｜分段停利與回撤保護

```text
曾達到2R
然後
從持有期間最高價回撤指定比例或ATR距離
```

R 必須由已保存初始風險算出；不能從最新停損重新反推。

### O12｜時間失效出場

```text
持有交易日 ≥ 使用者設定
且 尚未達成策略的最小延伸目標
```

時間門檻由使用者設定，不能宣稱固定天數適用所有策略。

停損觸發與实际成交必須分開。跳空、跌停或流動性不足時，不能把停止價格當保證成交價。[S03：Schwab 停損單說明](https://www.schwab.com/learn/story/help-protect-your-position-using-stop-orders)

---

## 十一、張震觀點：特別標註與條件設計

### 11.1 證據限制

本次找到的具體方法主要來自 168 周報的節目轉錄。亦找到張震原頻道影片資料，但未完整逐句核對上述歷史影片的聲音與圖表。

因此：

- 以下先列為「張震觀點・量化研究版」。
- 節目轉錄可以作找原片的線索。
- 尚未核對原片的內容不能標「原片已驗證」。
- 不得虛構時間碼。
- 不得將本站的百分比、ATR 倍數或期間設定說成張震本人指定。

現有 10/06 整理稿也有「突破前高後回測」「突破季線後量縮回測」「高檔背離」「大量與缺口」等說法，但 AI 整理稿不能代替原片逐句驗證。

### 11.2 可加入的研究條件

| 編號 | 觀點與用途 | 程式化設計 | 出處及限制 |
|---|---|---|---|
| Z-E01 | 突破季線後回測，進場 | 先突破 SMA60，再回測、量縮、重新確認 | Z01；回測距離自行量化 |
| Z-E02 | 均線糾結後突破，進場 | 多條均線分散度低，之後價格突破其上方或區間 | Z02、Z03；糾結比例不是原話固定值 |
| Z-E03 | 季線→半年線→年線的階段，進場／避免追高 | 分開顯示價格與 SMA60／120／240 的相對位置及階段事件 | Z04；不得要求排列固定才成立 |
| Z-E04 | 60分K 35／200均線，回測觀察 | 必須使用60分K的35／200根均線及前置趨勢 | Z02、Z06；不能換成日線35／200 |
| Z-E05 | 日／週／60分動能一起觀察，進場 | 各週期MACD／KD分開判定，再由使用者組合 | Z01、Z02；未完成週K標盤中觀察 |
| Z-E06 | 低基期與整理後轉強，進場 | 以過去漲幅、均線距離、區間寬度表示，避免主觀低檔 | Z02、Z03；不等於基本面便宜 |
| Z-E07 | 大量K棒支撐，進場 | 保存指定區間最大量K棒的高低點，觀察回測與收回 | Z06；不是證實的法人成本 |
| Z-X01 | 高檔爆量避免追價／獲利觀察 | 位階＋量能＋K棒／價格轉弱共同判定 | Z02、Z03 |
| Z-X02 | 60分K動能背離，出場觀察 | 確認高點背離，再配合價格確認 | Z03；背離算法另行定義 |
| Z-X03 | 大量K棒低點失守，出場觀察 | 收盤跌破已保存的量能事件低點 | Z06 |
| Z-X04 | 到上方歷史缺口或壓力，出場觀察 | 保存缺口／壓力事件，觀察觸及、回補及转弱 | Z03；不能每到缺口就自動賣 |
| Z-X05 | 法人庫存、融資與價格一起看 | 只有取得可靠籌碼資料後才開放 | Z01；不能由量價冒充法人資料 |

### 11.3 必須處理的觀念差異

一般「爆量突破策略」可能偏向追隨突破。

張震相關節目轉錄則常提醒低檔布局、高檔爆量不追，兩者可能對同一股票產生不同訊號。

系統必須：

- 保留兩套策略的不同目的。
- 顯示各自符合原因。
- 不強行合併成單一「買／賣」答案。
- 不把任何「爆量且跳空」直接命名成張震進場策略。

### 11.4 張震研究版預設範本

**A｜季線回測研究版**

```text
最近20根曾向上突破SMA60
且 SMA60不再明顯下彎
且 最近5根接近SMA60，距離≤0.5ATR
且 回測量能小於突破事件量
且 已完成K突破回測確認點
```

**B｜均線糾結突破研究版**

```text
前一根的MA集合分散度≤3%
且 壓縮持續至少5根
且 當根突破前一根的MA集合上緣或箱型高點
且 動能轉強
且 沒有超過追價距離
```

MA 集合可選 `5／10／20／60／120／240`，不足期間不能用較短均線代替。

**C｜60分K回測研究版**

```text
日K背景條件符合
且 60分K接近35或200均線
且 支撐没有被所選確認方式破壞
且 60分K動能完成轉折確認
```

**D｜高檔量價風險研究版**

```text
近期漲幅或均線乖離達設定
且 量能擴張達設定
且（長上影、開高走低、背離後確認、35均線失守之一成立）
```

以上數字是研究起始值，必須在介面與設定資料中明確標記。

---

## 十二、條件引擎：不能只支援一層且／或

### 12.1 基本模式

保留現有容易理解的分組：

```text
（A 且 B）或（C 且 D）
```

以及：

```text
（A 或 B）且（C 或 D）
```

升級時不得改變舊設定原意。

### 12.2 進階模式

使用可序列化的條件樹，支援：

- `AND`
- `OR`
- `NOT`
- 至少 N 項符合。
- 最近 N 根曾發生。
- 連續 N 根成立。
- A 發生後，B 在指定期間內成立。
- A、B 可同根或必须不同根。
- 指定主週期與確認週期。

例如：

```text
主要趨勢向上
且
[
  （先突破，再縮量回測，再確認）
  或
  （低波動壓縮後向上突破）
]
且
沒有超過追價距離
```

### 12.3 狀態、事件、時序分開

必须提供：

```text
K > D                    狀態
K[t-1] ≤ D[t-1] 且 K[t] > D[t]   交叉事件
最近3根內發生交叉           事件窗口
交叉後連續2根維持K > D       持續確認
```

不能用第一個替代後面三個。

### 12.4 三值邏輯

內部使用：

- `true`
- `false`
- `unknown`

規則：

- `NOT unknown = unknown`
- `AND`：有 false 為 false；沒有 false 但有 unknown 為 unknown。
- `OR`：有 true 為 true；沒有 true 但有 unknown 為 unknown。
- 空策略或空群組不得產生全市場符合。
- 全局資料有效性條件仍必須通過。

OR 的某一條有效分支已成立時，可以列出結果，但只能展示實際成立的分支，不能把資料不足的另一分支也畫成符合。

### 12.5 相同指標不得假裝多份獨立證據

分類至少包括：

- 價格趨勢。
- 價格動能。
- 波動。
- 成交量。
- 相對表現。
- 部位風險。

RSI、Stochastic、Williams %R 或多條由同樣價格構成的條件，不得直接被解釋為獨立的勝率提升。

「符合 5／6 條件」可以顯示，但不得換算成「83% 上漲機率」。

---

## 十三、公式、時間與邊界規格

### 13.1 基礎約定

- `t`：評估的已完成 K；盤中模式另有標示。
- `O/H/L/C/V`：正規化後的行情。
- 每個條件保存 timeframe、priceBasis、volumeBasis。
- 分 K、日 K、週 K 都使用交易日曆與明確時間邊界。
- 不得把 60 分 K 當成一天固定平均切割且忽略尾段。

### 13.2 前高突破

```text
突破上緣(t, N) = max(H[t-N], …, H[t-1])
```

排除當根，否則容易出現永遠無法真正突破或錯誤自我比較。[S18：Price Channels](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels)

### 13.3 開盤缺口與完整日 K 缺口分開

```text
向上開盤缺口：O[t] > H[t-1]
完整向上日K缺口：L[t] > H[t-1]
```

開盤有缺口，但盤中低點回到昨日高點以下，不能再叫「全天缺口守住」。

「開盤較昨收漲幅」與「超出昨日高點的缺口寬度」也要分成兩個數值。

### 13.4 三日不回補

必須等事件之後三個已完成交易日，再判斷三日結果。

不得：

- 算日曆天。
- 在事件當日直接顯示三日守住。
- 用之後的行情回寫事件當時就已知道結果。
- 宣稱三日是所有缺口的公認有效期。

### 13.5 指標初始化

明確保存：

- EMA 初始種子。
- RSI／ATR Wilder 平滑與初始平均。
- 台股 KD 的遞迴平滑及種子。
- Full Stochastic 的 SMA 平滑版本。
- MACD 柱體是否乘以 2。
- Bollinger 標準差版本。
- 零分母與全程無波動的處理。

不能為了與某張圖接近，就任意改種子而不更新算法版本。

### 13.6 背離不能使用尚未知道的轉折

如果 pivot 必須看右側兩根才能確認：

- pivot 的確認時間是兩根之後。
- 圖表可標轉折位置，但訊號時間不得倒填。
- 回測要使用當時已確認的資料。

### 13.7 Ichimoku 的位移

計算當時價格相對雲層，必須用對應時間的雲值。

不得使用視覺上「畫在未來」的資料來模擬過去可交易訊號。

### 13.8 ATR 與 Chandelier

ATR：

```text
TR = max(H-L, |H-前收|, |L-前收|)
```

Chandelier 多方原始線：

```text
最近22根最高價－3×ATR22
```

另設部位鎖定版本：

```text
本次停損 = max(前次停損, 本次候選停損)
```

兩者命名與算法要分開。

日 K 若同一根先創高又跌破當日上移停損，沒有日內路徑資料時，不能假裝知道先後順序。

### 13.9 VWAP 的資料正確性

- 有累計成交金額與累計成交股數時，可在確認口徑一致後計算。
- 用分 K 典型價×分 K 量計算時，標示為 K 棒近似版本。
- 富果歷史分 K 的 `average` 是自開盤累計均價，不能當成每根獨立均價再乘每根量重複累加。
- 日 K 量加權價格不能冒充日內 VWAP。

### 13.10 公司事件

除權息、分割、減資可能產生機械式價格斷層。

必須：

- 保存原始與調整價格的口徑。
- 核對官方參考價或公司事件。
- 避免混用調整後歷史高低與未調整今日開盤。
- 對無法確認的事件，不產生確定的交易型缺口判定。

官方參考價與供應商調整資料有明確文件，須實際對齊。[S50：TWSE 除權息](https://wwwc.twse.com.tw/zh/announcement/ex-right/twt49u.html)、[S52：Fugle 歷史行情](https://developer.fugle.tw/docs/data/http-api/historical/candles/)

---

## 十四、資料能力與實作順序

### 第一階段：利用既有日 K 擴充

優先完成：

- RSI。
- ATR。
- ADX／DMI。
- OBV／CMF／ADL。
- CCI／MFI／ROC。
- 交叉與時序。
- 突破回測。
- 完整缺口事件。
- 進場／出場分離。
- 部位保護條件。

但要先量測資料長度及正規化是否足夠。

### 第二階段：增加可靠分 K 與基準資料

支援：

- 同時段量比。
- 開盤區間。
- 日內 VWAP。
- 60 分 K 35／200 均線。
- 多週期確認。
- 相對大盤／產業強弱。

富果文件有歷史分 K 及成交量欄位，不表示本站目前已保存完整資料或帳號已具備權限；實作前要確認實際方案、回傳及費用。[S51](https://developer.fugle.tw/docs/data/http-api/intraday/candles/)、[S52](https://developer.fugle.tw/docs/data/http-api/historical/candles/)

### 第三階段：需要新增資料來源的進階條件

- 流通股數與周轉率。
- 法人買賣與持股。
- 融資融券。
- 歷史分價量。
- 產業基準。
- 公司事件完整資料。

沒有資料時：

- 條件選擇處顯示簡短的資料需求。
- 不提供假的計算結果。
- 不用 OBV 替代法人買超。
- 不用總發行股數冒充自由流通股數。
- 不用當日分價量表冒充歷史同時段量。

### 歷史長度

目前部分程式以最近至多 160 根日 K 計算，不能直接支援：

- SMA200。
- SMA240。
- 長期週線／月線。
- 需要更長暖機的平滑指標。

新指標必須宣告 `requiredHistory`、暖機方式與可用性，不能用不足長度的平均冒充完整均線。

---

## 十五、UI／UX 設計

### 15.1 設定頁骨架

建議順序：

```text
戰情設定 ▾

進場訊號 / 出場訊號
策略範本
方向與週期
量價門檻
條件組合
套用與符合結果
```

數值詳細設定放在對應條件展開內容，避免全頁數十個輸入框。

### 15.2 橢圓填色按鈕

沿用網站風格：

- 選中項目完整填色。
- 未選中保留可辨識邊界。
- 圖示加短文字。
- 清楚焦點。
- 不只靠顏色表達狀態。

參考色彩可設計為：

- 進場：品牌主色。
- 出場：橙／琥珀色。
- AND：藍色。
- OR：紫色。
- 排除：灰色與「排除」文字。

台股漲跌仍用既有紅／綠；逻辑色不能與漲跌色混為一談。

### 15.3 條件積木

每塊至少顯示：

- 條件名稱。
- 週期。
- 事件或狀態。
- 主要參數。
- 移除按鈕。
- 展開設定。
- 依據入口。

例如：

```text
MACD 黃金交叉
日K · 12/26/9 · 最近3根
```

組與組之间以括號、連線及「且／或」文字顯示。

加入、移動或刪除時，立即更新自然語言摘要。

### 15.4 拖曳與替代操作

支援：

- 桌機拖曳。
- 點選「加入這一組」。
- 「移到另一組」選單。
- 鍵盤移動。
- 手機點選。
- 移動後復原。

拖曳不能是唯一操作方式，W3C 明確要求非拖曳的單指替代操作。[S58：Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html)

### 15.5 搜尋

「查看股票」：

- 全寬大型橢圓框。
- 輸入框與查看按鈕換行。
- 中文、代號皆可輸入。
- 候選排序優先完整代號、完整名稱、前綴及其他相關結果。
- 支援 IME。
- 支援方向鍵、Enter、Escape。
- 新查詢優先於背景清單。
- 過期回應不得覆蓋新股票。

使用 W3C Combobox 模式處理焦點及候選操作。[S59：Combobox](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/)

### 15.6 結果卡

只列符合股票。

第一層：

- 名稱與代號。
- 進場／出場觀察標籤。
- 符合策略名稱。
- 判定週期與時間。
- 最重要的兩至三項數值。

第二層展開：

- 成立的條件分支。
- 每條實際值與門檻。
- 小型價格／量能圖。
- 突破線、支撐、缺口或停損位置。
- 策略依據。
- 盤中觀察／收盤確認。

視覺方式：

- 突破：價格線與突破水平線。
- 缺口：區間填色與回補比例。
- 量能：今日與比較基準兩條量條。
- MACD：短柱狀圖及實際交叉標記。
- 回測：事件時間軸。
- 出場：價格至保護線的距離。

不得將所有符合者都使用同一個綠色勾勾而沒有差異。

### 15.7 響應式與動態

驗證：

- 1440、1280、768、390、360px。
- 深／淺主題。
- 減少動態模式。
- 鍵盤完整流程。

控制區至少 44×44 CSS px；搜尋框可使用既有較大高度。44×44 屬 WCAG 增強目標，本專案採用此較寬鬆尺寸。[S60：Target Size Enhanced](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html)

動畫只做操作回饋，不做持續閃爍、自动播放或捲動前隱藏內容。

---

## 十六、效率與自動化成本

### 16.1 計算與抓取分開

使用者：

- 切換策略。
- 修改參數。
- 拖曳條件。
- 開關設定。

都只重算已有資料，不新增行情抓取、不啟動 Apps Script 工作。

### 16.2 依賴計算只做一次

同股票、同週期、同算法參數的指標共用結果。

快取鍵至少包含：

```text
股票＋週期＋資料版本＋算法版本＋參數
```

設定改變只重算受到影響的部分。

### 16.3 批次與增量更新

- 先篩選低成本的必要條件。
- 再計算較高成本的背離與時序。
- 行情只追加或修正受影響根數。
- 指定股票查詢優先。
- 背景批次可以暫停、取消及續載。
- 每個回應帶資料版本，避免新舊快取混合。

### 16.4 分 K 的保存規劃

先盤點五分鐘排程拿到的是：

- 累計量快照。
- 真正五分鐘 OHLCV。
- 供應商分 K。

「每五分鐘抓一次報價」不等於「已取得完整五分鐘 K」。

若只拿累計量差：

- 檢查重置、修正、漏抓與時段。
- 不得將缺一筆後的十分鐘量標成五分鐘量。
- 不得靠快照重建不存在的分 K 高低價。

### 16.5 性能驗收目標

以下為工程目標，不是既有實測結果：

- 有快取時，普通參數修改至結果更新，典型裝置 p95 目標 300ms。
- 大批次或背離運算必要時使用 Web Worker。
- 主要執行緒避免長於約 50ms 的同步工作。
- 單一股票查詢不等待全市場批次。
- 連續切換十次，最後畫面只能對應最後一次設定。
- 提供 API 耗時與本機計算耗時的分開紀錄。

---

## 十七、驗證與回測

### 17.1 公式測試

至少涵蓋：

- 固定已知 OHLCV 的指標數值。
- 股／張換算。
- 交叉與既有狀態差異。
- 除權息。
- 停牌。
- 零成交。
- 資料不足。
- 未完成 K。
- 週期聚合。
- 公司事件。
- 相同價格導致零分母。
- 初始種子與暖機。

### 17.2 條件邏輯測試

涵蓋：

- `(A AND B) OR C`
- `(A OR B) AND C`
- 巢狀 NOT。
- 至少 N 項。
- 三值邏輯。
- 空群組。
- 相同指標不同參數。
- 舊設定遷移前後結果一致。
- 序列條件的過期、完成及重置。

### 17.3 避免未來資訊

實際重播每一根，只提供當時能知道的資料。

驗證：

- 背離確認時間。
- 三日缺口結果。
- 尚未結束週 K。
- 當日量。
- 事後才知道的事件。
- 指標資料被供應商修訂後的版本差異。

歷史與即時計算的差異必須揭露。[S39：TradingView Repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/)

### 17.4 策略有效性不能只挑案例

分開：

1. 公式正確。
2. 規則正确。
3. 画面与规则一致。
4. 歷史交易表現。

前面三項通過，不代表第四項成立。

回測要求：

- 不同市場期間。
- 未參與調參的留出資料。
- 可用時採逐期前進驗證。
- 交易成本、滑價與台股成交限制。
- 不只現在仍上市的股票。
- 停損與停利同根觸發時的路徑不確定性。
- 下一可交易時間的實際執行假設。
- 輸出交易數、報酬分布、回撤、換手及參數敏感性。
- 記錄所有試過的組合，避免只回報最佳者。

原始研究指出技術型態可能提供增量資訊，但這與保證獲利不同；大量挑選交易規則还需處理資料探勘偏誤。[S56：Lo 等人研究](https://www.nber.org/papers/w7613)、[S57：Sullivan 等人研究](https://www.fmg.ac.uk/publications/discussion-papers/data-snooping-technical-trading-rule-performance-and-bootstrap)

---

## 十八、實作交付要求

後續實作 AI 必須交付：

1. 現有資料與功能盤點。
2. 新舊設定相容方案。
3. 進場／出場條件登錄表。
4. 每個條件的公式、參數、來源與資料需求。
5. 策略條件樹及自然語言摘要。
6. 張震研究版的出處與原片核對狀態。
7. 公式、時序、公司事件及無未來資訊測試。
8. 多裝置 UI 操作驗收。
9. 真實股票與真實行情日期的比對。
10. API、資料日期、算法版本及部署版本紀錄。
11. 仍無法計算的條件及具體原因。
12. 未經回測的研究預設明確標示。

若進入正式部署階段：

- 比對雲端 Apps Script HEAD。
- 保留其他既有檔案與同期修改。
- 更新原專案與原部署。
- 推送 GitHub 並確認 Pages 成功。
- 實際驗證正式站戰情頁、個股卡及後台相容性。
- 不以本機測試代替正式站驗收。
- 不觸發取稿、重算或重寄。

---

# 來源表：62 份官方／研究文件

**計數方式：59 份技術、行情或研究資料，加上 3 份 UI 無障礙文件。不同語言版本、同篇 PDF、重新導向與追蹤網址不重複計數。來源頁面不同，不代表來自 62 個不同機構。**

## A. 券商與主流指標文件

| 編號 | 來源 | 支持內容 |
|---|---|---|
| S01 | [Schwab：Choosing Technical Indicators](https://www.schwab.com/learn/story/choosing-technical-indicators-to-analyze-stocks) | 常用指標、類別搭配及不同計算方式 |
| S02 | [Schwab：Fundamentals vs. Technicals](https://www.schwab.com/learn/story/how-to-pick-stocks-using-fundamental-and-technical-analysis) | 趨勢、突破、回測與篩選流程 |
| S03 | [Schwab：Stop Orders](https://www.schwab.com/learn/story/help-protect-your-position-using-stop-orders) | 停損、移動停損及成交價限制 |
| S04 | [Fidelity：SMA](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/sma) | 均線、價格與均線交叉 |
| S05 | [Fidelity：EMA](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/ema) | 加權、反應速度與假訊號 |
| S06 | [Fidelity：RSI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/RSI) | 超買超賣、趨勢區間、背離與失敗擺盪 |
| S07 | [Fidelity：MACD](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/macd) | 12／26／9、交叉、零軸及背離 |
| S08 | [Fidelity：ADX](https://www.fidelity.com/viewpoints/active-investor/average-directional-index-ADX) | 趨勢強度、20／25 參考與方向判讀 |
| S09 | [Fidelity：ATR](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr) | 真實區間、平滑及波動停損 |
| S10 | [Fidelity：Bollinger Bands](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-bands) | 通道、壓縮與趨勢延續 |
| S11 | [Fidelity：Bollinger BandWidth](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/bollinger-band-width) | 寬度及歷史相對壓縮 |
| S12 | [Fidelity：OBV](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/OBV) | 累積量、方向與量價背離 |
| S13 | [Fidelity：MFI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/mfi) | 價格與成交量動能 |
| S14 | [Fidelity：CCI](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/cci) | 偏離、±100 與趨勢情境 |
| S15 | [Fidelity：ROC](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/roc) | 報酬變動速度及零軸 |
| S16 | [Fidelity：Williams %R](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/williams-r) | 區間位置與 Stochastic 關聯 |

## B. 進階條件與價格結構

| 編號 | 來源 | 支持內容 |
|---|---|---|
| S17 | [StockCharts：Stochastic](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stochastic-oscillator-fast-slow-and-full) | Fast／Slow／Full 版本與交叉 |
| S18 | [StockCharts：Price Channels](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/price-channels) | Donchian、前高低及排除當根 |
| S19 | [StockCharts：Parabolic SAR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/parabolic-sar) | 趨勢追蹤及反轉 |
| S20 | [StockCharts：Chandelier Exit](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit) | 22 根及 ATR 出場公式 |
| S21 | [StockCharts：ATR Trailing Stops](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/atr-trailing-stops) | 波動移動停損與單向調整 |
| S22 | [StockCharts：Ichimoku Cloud](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/ichimoku-cloud) | 雲層、9／26／52 及時間位移 |
| S23 | [StockCharts：Keltner Channels](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels) | EMA／ATR 版本及通道突破 |
| S24 | [StockCharts：VWAP](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/volume-weighted-average-price-vwap) | 日內量加權均價及 K 棒近似 |
| S25 | [StockCharts：Anchored VWAP](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/anchored-vwap) | 事件錨點及跨日均價 |
| S26 | [StockCharts：CMF](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/chaikin-money-flow-cmf) | 量價流量及零軸 |
| S27 | [StockCharts：Accumulation/Distribution](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/accumulation-distribution-line) | 累積量價流與背離 |
| S28 | [StockCharts：Aroon](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/aroon) | 新高低的近期性與趨勢 |
| S29 | [StockCharts：%B](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/b-indicator) | 布林位置及趨勢背景 |
| S30 | [StockCharts：Relative Strength](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/price-relative-relative-strength) | 相對市場與產業表現 |
| S31 | [StockCharts：Relative Volume](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-volume-rvol) | 量能倍數與極端量限制 |
| S32 | [StockCharts：TTM Squeeze](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/ttm-squeeze) | BB／KC 壓縮與版本差異 |
| S33 | [StockCharts：Gaps](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/gaps-and-gap-analysis) | 開盤／完整缺口、突破及竭盡情境 |
| S34 | [StockCharts：Support & Resistance](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/support-and-resistance) | 支撐區、壓力區及突破 |
| S35 | [StockCharts：Bullish Candlestick Reversals](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns) | 多方型態、前置趨勢及確認 |
| S36 | [StockCharts：Bearish Candlestick Reversals](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bearish-reversal-patterns) | 空方型態、前置趨勢及確認 |

## C. 量能、時間與原作者方法

| 編號 | 來源 | 支持內容 |
|---|---|---|
| S37 | [TradingView：Relative Volume at Time](https://www.tradingview.com/support/solutions/43000705489-relative-volume-at-time/) | 同時段單根／累計量與未完成棒 |
| S38 | [TradingView：Relative Volume Calculation](https://www.tradingview.com/support/solutions/43000635874-how-do-we-calculate-relative-volume-and-relative-volume-at-time/) | 一般量比與同時段量比分開 |
| S39 | [TradingView：Repainting](https://www.tradingview.com/pine-script-docs/concepts/repainting/) | 即時／歷史差異及未來資訊 |
| S40 | [TradingView：Up/Down Volume](https://www.tradingview.com/support/solutions/43000672561-up-down-volume/) | 小週期推估量，不是交易者身分 |
| S41 | [TradingView：Volume Profile](https://www.tradingview.com/support/solutions/43000502040-volume-profile-indicators-basic-concepts/) | POC、VAH／VAL 與分價量資料 |
| S42 | [John Bollinger：Bollinger Band Rules](https://www.bollingerbands.com/bollinger-band-rules) | 指標獨立性、觸軌不等於買賣 |
| S43 | [Chaikin Analytics：CMF](https://help.chaikinanalytics.com/knowledge-center/chaikin-money-flow) | 量價推估與實際機構訂單分開 |
| S44 | [XQ：估計量](https://xshelp.xq.com.tw/XSHelp/?HelpName=%E4%BC%B0%E8%A8%88%E9%87%8F&group=QVOLUME) | 歷史分鐘量分布估算 |
| S45 | [XQ：預估量函數的介紹](https://www.xq.com.tw/xstrader/%E9%82%A3%E4%BA%9B%E8%82%A1%E7%A5%A8%E4%B8%AD%E9%95%B7%E7%B4%85%E4%B9%8B%E5%BE%8C%E9%82%84%E6%9C%83%E7%BA%8C%E6%BC%B2/) | 簡化估量算法；不等同官方估計量 |
| S46 | [XQ：量要放大幾倍股價會漲](https://www.xq.com.tw/xstrader/%E9%87%8F%E8%A6%81%E6%94%BE%E5%A4%A7%E5%B9%BE%E5%80%8D%E8%82%A1%E5%83%B9%E6%9C%83%E6%BC%B2/) | 量倍數不是單調越高越好；歷史研究範圍有限 |
| S47 | [XQ：預估個股的成交量](https://www.xq.com.tw/xstrader/%E9%A0%90%E4%BC%B0%E5%80%8B%E8%82%A1%E7%9A%84%E6%88%90%E4%BA%A4%E9%87%8F/) | 估算方法差異及不確定性 |

## D. 台股行情、參數與研究驗證

| 編號 | 來源 | 支持內容 |
|---|---|---|
| S48 | [TWSE：Trading Mechanism](https://www.twse.com.tw/en/products/system/trading.html) | 交易時段、單位、漲跌限制及延後收盤 |
| S49 | [統一期貨：移動平均線](https://www.pfcf.com.tw/product/detail/2621) | 台股20／60／240、國際200及均線糾結 |
| S50 | [TWSE：除權除息計算結果](https://wwwc.twse.com.tw/zh/announcement/ex-right/twt49u.html) | 官方參考價及價格調整 |
| S51 | [Fugle：Intraday Candles](https://developer.fugle.tw/docs/data/http-api/intraday/candles/) | 分 K OHLCV、週期及成交量單位 |
| S52 | [Fugle：Historical Candles](https://developer.fugle.tw/docs/data/http-api/historical/candles/) | 歷史資料、還原股價、股／張與累計均價 |
| S53 | [Fugle：Intraday Quote](https://developer.fugle.tw/docs/data/http-api/intraday/quote/) | 累計量值、參考價、試撮與交易狀態 |
| S54 | [Fugle：Intraday Volumes](https://developer.fugle.tw/docs/data/http-api/intraday/volumes/) | 分價量；內外盤不含開盤第一筆 |
| S55 | [Fugle：Technical KDJ](https://developer.fugle.tw/docs/data/http-api/technical/kdj/) | KDJ週期、時間框架與 API 限制 |
| S56 | [Lo、Mamaysky、Wang：Foundations of Technical Analysis](https://www.nber.org/papers/w7613) | 系統化型態辨識及增量資訊研究 |
| S57 | [Sullivan、Timmermann、White：Data Snooping](https://www.fmg.ac.uk/publications/discussion-papers/data-snooping-technical-trading-rule-performance-and-bootstrap) | 多重選擇與資料探勘偏誤 |

## E. UI 與台股 KD 補充

| 編號 | 來源 | 支持內容 |
|---|---|---|
| S58 | [W3C：Dragging Movements](https://www.w3.org/WAI/WCAG22/Understanding/dragging-movements.html) | 拖曳的非拖曳替代操作 |
| S59 | [W3C：Combobox Pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) | 搜尋候選、鍵盤及焦點 |
| S60 | [W3C：Target Size Enhanced](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced.html) | 44×44 CSS px 增強目標 |
| S61 | [XQ：KD 填色範例](https://www.xq.com.tw/learn/xspractice/function-coloring/) | 9／3／3、20／80與區間填色 |
| S62 | [XQ：Stochastic 函數](https://xshelp.xq.com.tw/XSHelp/?HelpName=Stochastic&group=TECHINDEXFUNC) | RSV、K、D 參數及台股常见範例 |

## F. 張震節目轉錄出處：另外計數

以下是**媒體節目轉錄**，用於建立研究方向及寻找原片，不能當成已完成的原片驗證。

| 編號 | 出處 | 可研究的觀點 |
|---|---|---|
| Z01 | [這一次的必要之惡，是讓你買中小型股](https://168abc.net/168-tv/19205) | 突破季線回測；日、60分、週線與籌碼一起看 |
| Z02 | [未來三個月會漲的，就是均線糾結突破](https://168abc.net/168-tv/8734) | 均線糾結、60分35均線、高檔爆量與避免追價 |
| Z03 | [別買高檔成就別人，要買低檔成就自己](https://168abc.net/168-tv/8768) | 低基期、均線分散、高檔背離與缺口 |
| Z04 | [壓縮末端，看三條均線數123](https://168abc.net/168-tv/5626) | 季線、半年線、年線的階段觀察 |
| Z05 | [還有很多股票在十年線，把握布局機會](https://168abc.net/168-tv/6042) | 回測季線、MACD轉折及長期位階 |
| Z06 | [盤中跌破來到42376開始買股票](https://168abc.net/168-tv/30577) | 大量K棒低點、假跌破與60分200均線 |

**建議的實作優先順序：先完成進場／出場、量能口徑與時序引擎，再加入多週期與張震研究版。如此才能讓新增的複雜條件具有可驗證的意義。**
