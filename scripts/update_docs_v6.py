from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'README.md';s=p.read_text(encoding='utf-8')
intro='''> **目前版本：2026-09-13-quality-v6**。修正行情例子被排除、ETF00981A、文章縮水覆核、價位格式及技術線200、背景日K／績效與非同步介面。請先閱讀 [v6修改與詳細部署流程](docs/0913-v6/修改與部署流程.md) 與 [驗證紀錄](docs/0913-v6/驗證紀錄.md)。apps-script **27檔全部部署（19個.gs、8個.html）**，新增 `Presentationquality.gs`；`Transcriptstore.gs` 仍需保留。發布包：`release/zhangzhen-quality-v6.zip`。長期品質要求：`AGENTS.md`。

網站及郵件保留會員持股；原文點名台股／已確認ETF的當下行情與資金風險亦納入觀察。純歷史獲利故事不推定持股。公開價位取最高可證實值，交易原始多次價保留；不得將技術天數或不明黏連当股價。後台工單先發布內容、印Gemini用量，後段才補績效所缺日K；頁面分開顯示內容完成與背景待續跑。

以下v5與更早版本為修改沿革；部署清單及操作以v6手冊為準。

'''
p.write_text(intro+s,encoding='utf-8')
p=ROOT/'docs/0912/Apps-Script-詳細部署手冊.md';s=p.read_text(encoding='utf-8')
s=s.replace('26 檔','27 檔').replace('26 個檔案','27 個檔案').replace('18 個指令碼','19 個指令碼').replace('2026-09-13-overwrite-v5','2026-09-13-quality-v6')
s=s.replace('| Setup.gs |', '| Presentationquality.gs | 指令碼，名稱 Presentationquality | v6新增：公開價位與盤中日K預覽 |\n| Setup.gs |',1)
s='> 最新操作請先看 [v6修改與詳細部署流程](../0913-v6/修改與部署流程.md)，包括新增Presentationquality、文章先發布及盤中快取欄位。\n\n'+s
p.write_text(s,encoding='utf-8')
