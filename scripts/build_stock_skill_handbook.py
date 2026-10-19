"""Build an offline handbook from versioned Stock skills; optionally sync installed copies.

Requires mistune for Markdown rendering. No network, model or production calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import html
import re
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "docs" / "stock-skills"
SKILLS = (
    ("stock-site-design", "介面與圖解設計", "design-and-qa.md", "design", "設計"),
    ("stock-content-audit", "來源與分類稽核", "evidence.md", "content", "內容"),
    ("stock-public-writing", "公開文字與書面說明", "writing.md", "writing", "文字"),
    ("stock-rule-tuning", "規則調整與回測", "tuning.md", "tuning", "規則"),
    ("stock-market-data", "行情與回合計算", "market-and-tracker.md", "market", "行情"),
    ("stock-automation-triage", "自動化與續跑", "triage.md", "automation", "流程"),
    ("stock-release-check", "同步發布與驗收", "release.md", "release", "發布"),
    ("stock-assistant", "網站 AI 助理", "assistant.md", "assistant", "助理"),
    ("stock-collaboration", "協作、授權與交接", "collaboration.md", "collab", "協作"),
)
# 2026/10/15 整併：哪些技能是新增、哪些是把新規則併入既有技能。
STATUS = {
    "stock-site-design": ("併入", "補共用寬版、自製可及控制項、數據閱讀層次與動畫分工"),
    "stock-content-audit": ("併入", "區分 accepted 驗收、部位事實與獨立評估，支援歷史樣本擴充"),
    "stock-public-writing": ("併入", "改為 AI 批次語意審閱，錯例作 rubric，保留證據與通道核對"),
    "stock-rule-tuning": ("併入", "補技能路由、離線 A/B、歷史樣本擴充、AI 裁判與答案污染檢查"),
    "stock-market-data": ("併入", "補逐次行情與持股裁示、證交所／櫃買來源實務"),
    "stock-automation-triage": ("併入", "補 10/05–10/15 事故表與沒有 gh CLI 時的 Actions 操作"),
    "stock-release-check": ("併入", "補品質提升的證據門檻，區分已安裝、已評估與已發布"),
    "stock-assistant": ("新增", "只用 Flash Lite、只訂閱每日總覽、唯讀查詢與三條硬限制"),
    "stock-collaboration": ("新增", "常用語句辭典、授權帳本、交接 prompt 與結案格式"),
}
# 一天的資料從訊息到正式站經過的站點：(站名, 技能 key, 做什麼, 關卡, 常犯錯誤, 完成證據)
STAGES = (
    ("拆句與授權", "collab", "把管理者的一則訊息拆成逐項待辦，對照哪些動作已授權、哪些是擁有者步驟。",
     "不可逆的動作（apply、刪列、寄送、加別名、移 /exec）要被點名", "把貼上紀錄寫的「已完成」當成事實", "拆句清單、結案報告"),
    ("取稿與原稿", "automation", "偵測影片、轉錄、保存原稿與指紋，失敗時保留已完成片段續跑。",
     "原稿 SHA-256 不變；直播中延後，不當失敗", "400 重複認證時換三把金鑰空等", "原稿字數、指紋、片段與 run"),
    ("校字與股名", "content", "核對名稱、代號與聽錯的字；不確定的字合併一批請模型修。",
     "價位對得上本股；日常用語先排除", "合法代號就當對（宏達電其實是鴻海）", "名稱判定紀錄、逐處採用憑證"),
    ("分類與裁決", "content", "明確事實直接歸類，有疑點的送問卷，引句逐字核對後由程式決定分類。",
     "引句在可引用範圍；跨公開或持股要三票", "「今天賣很蠢」當成今日賣出", "獨立分類表、答案檔比對"),
    ("撰寫說明", "writing", "把本股的技術位置、量能、籌碼、基本面整理成書面說明，清掉口語與大盤子句。",
     "不借鄰股數字；前台不出現會員通知", "「作為觀望不碰的標的」這種自稱分類", "逐則讀 print_public_texts 與正式站"),
    ("規則調整", "tuning", "判錯時找成因，固定來源做成對比較；回歸、未見與人工輔助分開報告。",
     "核對答案注入與快取，保留來源、預算及失敗紀錄", "人工輔助預覽當獨立品質證明", "run、commit、來源指紋、用量及逐日差異"),
    ("行情與回合", "market", "報價、日 K、持股回合與報酬；進出場取當日高低中點與明講價。",
     "同股同價；收盤後標「收盤」", "「略過 240」改寫成「成功 240」", "check_tracker_midprice 逐回合比對"),
    ("發布與驗收", "release", "GitHub 推送、Apps Script 推送與建版、擁有者移 /exec、正式站讀回。",
     "/exec?action=ping 的 build 與 GitHub 一致", "建了版本就說已上線", "commit、版本號、正式讀回"),
    ("通知與帳本", "automation", "每日總覽 Email／LINE 與盤中通知，依帳本防重寄。",
     "已寄送不重寄；未知結果先核對", "平台接受當成收件者已讀", "Email 與 LINE 各自的帳本"),
    ("介面與助理", "design", "前台呈現、技術頁圖解與網站 AI 助理的回答。",
     "按鈕要讓圖解有可見變化；助理三條硬限制", "順手放大卡片與圖形", "1916 寬版大字與 1440、390 截圖"),
)


def validate_references() -> list[Path]:
    files = [BASE / "常用工作規格與指令.md"]
    for name, _, ref, _, _ in SKILLS:
        folder = BASE / "skills" / name
        files.extend([folder / "SKILL.md", folder / "references" / ref])
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        for link in re.findall(r"\]\((references/[^)#]+)(?:#[^)]*)?\)", text):
            if not (folder / link).is_file():
                raise ValueError(f"Broken skill reference: {name}/{link}")
        files.extend(p for p in sorted((folder / "references").glob("*.md")) if p not in files)
    for path in files:
        if not path.is_file():
            raise FileNotFoundError(path)
    return files


def sync_installed(install: bool, check: bool) -> None:
    target_root = Path.home() / ".codex" / "skills"
    mismatches = []
    count = 0
    for name, *_ in SKILLS:
        source = BASE / "skills" / name
        for src in sorted(source.rglob("*.md")):
            dest = target_root / name / src.relative_to(source)
            if install:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dest)
            if check or install:
                expected = hashlib.sha256(src.read_bytes()).digest()
                if not dest.is_file() or hashlib.sha256(dest.read_bytes()).digest() != expected:
                    mismatches.append(str(dest))
                count += 1
    if mismatches:
        raise ValueError("Installed skills differ:\n" + "\n".join(mismatches))
    if check or install:
        print(f"Installed skill files match: {count}")


def build() -> str:
    import mistune

    render_md = mistune.create_markdown(escape=True, plugins=["table"])

    def md(source: str, link_base: str = "") -> str:
        rendered = render_md(source)
        if link_base:
            def relocate(match):
                target = match.group(1)
                if target.startswith(("#", "/")) or urlsplit(target).scheme:
                    return match.group(0)
                return 'href="' + urljoin(link_base, target) + '"'
            rendered = re.sub(r'href="([^"]+)"', relocate, rendered)
        return rendered.replace('<table>', '<div class="table-wrap" tabindex="0" role="region" aria-label="可左右捲動的規格表"><table>').replace('</table>', '</table></div>')
    intro = (BASE / "常用工作規格與指令.md").read_text(encoding="utf-8")
    # Put common work and the copyable request templates in separate expandable cards.
    chunks = re.split(r"(?m)^(?=## \d+\.)", intro)
    sections = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        lines = chunk.strip().splitlines()
        title = lines[0].lstrip("# ")
        content = "\n".join(lines[1:])
        group = "templates" if title.startswith("6.") else "common"
        opened = i in (0, 2) or title.startswith("10.")
        sections.append(f'<section class="doc" data-group="{group}" id="common-{i}">'
                        f'<details{" open" if opened else ""}><summary>{html.escape(title)}</summary>'
                        f'<div class="prose">{md(content)}</div></details></section>')
    role_cards = []
    for name, title, ref, key, label in SKILLS:
        path = BASE / "skills" / name / "references" / ref
        state, change = STATUS[name]
        role_cards.append(f'<a class="role k-{key}" href="#{key}"><span>{label}<em class="badge{" new" if state == "新增" else ""}">{state}</em></span>'
                          f'<strong>{title}</strong><small>{name}</small><small class="change">{html.escape(change)}</small></a>')
        link_base = f"skills/{name}/references/"
        extra = ''.join(md(p.read_text(encoding="utf-8"), link_base) for p in sorted(path.parent.glob("*.md")) if p != path)
        sections.append(f'<section class="doc" data-group="{key}" id="{key}"><details>'
                        f'<summary><span class="tag k-{key}">{label}</span>{title}</summary>'
                        f'<div class="prose"><p><code>{name}</code> · {state}（{html.escape(change)}） · '
                        f'<a href="skills/{name}/SKILL.md">技能入口</a></p>'
                        f'{md(path.read_text(encoding="utf-8"), link_base)}{extra}</div></details></section>')
    filters = '<button type="button" data-filter="all" aria-pressed="true">全部</button>'
    filters += '<button type="button" data-filter="common" aria-pressed="false">共用規格</button>'
    filters += '<button type="button" data-filter="templates" aria-pressed="false">請求範本</button>'
    filters += ''.join(f'<button type="button" data-filter="{key}" aria-pressed="false">{label}</button>'
                       for _, _, _, key, label in SKILLS)
    return TEMPLATE.replace("@@PIPELINE@@", pipeline()).replace("@@ROLES@@", ''.join(role_cards)).replace("@@FILTERS@@", filters).replace("@@DOCS@@", ''.join(sections))


def pipeline() -> str:
    """Workflow panel: stage buttons, one detail card, and a no-JS list of every stage."""
    label = {key: (name, title, short) for name, title, _, key, short in SKILLS}
    buttons, items, data = [], [], []
    for i, (stage, key, what, gate, mistake, proof) in enumerate(STAGES):
        name, title, short = label[key]
        buttons.append(f'<button type="button" class="stage k-{key}" data-stage="{i}" aria-pressed="{str(i == 0).lower()}">'
                       f'<b>{i + 1:02d}</b><span>{stage}</span><small>{short}</small></button>')
        items.append(f'<li><strong>{stage}</strong>（{title}，<code>{name}</code>）：{what}關卡：{gate}。常犯：{mistake}。證據：{proof}。</li>')
        data.append([stage, key, title, what, gate, mistake, proof])
    first = data[0]
    payload = json.dumps(data, ensure_ascii=False)
    assert "</" not in payload, "stage text must not close the JSON script"
    return ('<section class="panel" id="pipeline" aria-labelledby="pipe-title"><div class="logic-head">'
            '<h2 id="pipe-title">一天的資料，經過哪些技能把關？</h2>'
            '<button type="button" id="pipe-replay" class="js-only">再播流程</button></div>'
            '<p class="muted">從一則訊息到正式站共十站，每站只由一個技能負責。點選一站看關卡、常犯錯誤與完成證據。</p>'
            f'<div class="stages js-only" role="group" aria-label="流程站點">{"".join(buttons)}</div>'
            f'<div class="stage-card js-only k-{first[1]}" id="stage-card"><div class="stage-top">'
            f'<span class="tag" id="stage-skill">{first[2]}</span><strong id="stage-title">01 {first[0]}</strong></div>'
            f'<p id="stage-what">{first[3]}</p><div class="result three">'
            f'<div><span>關卡</span><strong id="stage-gate">{first[4]}</strong></div>'
            f'<div><span>常犯錯誤</span><strong id="stage-mistake">{first[5]}</strong></div>'
            f'<div><span>完成證據</span><strong id="stage-proof">{first[6]}</strong></div></div>'
            f'<a id="stage-link" href="#{first[1]}">讀這個技能的完整規格</a></div>'
            '<p id="pipe-state" class="note muted js-only" aria-live="polite">捲到這裡會依序播放；點選任一站即停止。</p>'
            f'<details><summary>沒有 JavaScript 也能讀的十站流程</summary><ol>{"".join(items)}</ol></details>'
            f'<script type="application/json" id="stage-data">{payload}</script>'
            '</section>')


TEMPLATE = '''<!doctype html>
<html lang="zh-Hant" data-theme="light"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>盤勢有據｜工作規格與技能索引</title>
<style>
:root{--bg:#f3f2ec;--panel:#fff;--text:#18352e;--muted:#536b62;--line:#cbd8cf;--accent:#126449;--soft:#e2efe7;--focus:#985400;color-scheme:light}
html[data-theme=dark]{--bg:#101915;--panel:#19251f;--text:#ebf3ee;--muted:#afc1b6;--line:#3e5749;--accent:#83e2ba;--soft:#243b30;--focus:#ffd086;color-scheme:dark}
*{box-sizing:border-box}html{scroll-padding-top:22px}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.8 system-ui,"Microsoft JhengHei",sans-serif;letter-spacing:.02em}button,input{font:inherit;color:inherit}button,a,input{touch-action:manipulation}a{color:var(--accent);text-underline-offset:4px}button{cursor:pointer;background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:9px 19px;min-height:46px}button[aria-pressed=true]{background:var(--accent);color:var(--bg);border-color:var(--accent)}:focus-visible{outline:3px solid var(--focus);outline-offset:4px}main{max-width:1420px;padding:28px 30px 120px;margin:auto}.top{display:flex;gap:18px;align-items:center;justify-content:space-between}.brand{font-weight:800;letter-spacing:.07em}.muted,small{color:var(--muted)}h1{font-size:clamp(30px,4vw,54px);line-height:1.35;margin:18px 0}h2{font-size:24px;line-height:1.5}p{margin:12px 0}.hero{background:var(--accent);color:var(--bg);padding:clamp(24px,4vw,44px);border-radius:32px;margin:24px 0}.hero p{max-width:920px}.hero .eyebrow{font-size:14px;letter-spacing:.12em}.roles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.role{padding:20px;border:1px solid var(--line);border-radius:24px;background:var(--panel);text-decoration:none;display:flex;flex-direction:column;gap:10px;min-width:0}.role strong{color:var(--text);font-size:19px}.role small{font-size:13px;overflow-wrap:anywhere}.role>span,.tag{font-weight:750;color:var(--k,var(--accent))}.role{border-top:5px solid var(--k,var(--accent))}.role>span{display:flex;justify-content:space-between;gap:10px;align-items:center}.badge{font-style:normal;font-size:13px;font-weight:700;padding:2px 12px;border-radius:999px;border:1px solid var(--line);color:var(--muted)}.badge.new{background:var(--k,var(--accent));border-color:var(--k,var(--accent));color:var(--panel)}.role .change{font-size:14px;line-height:1.7}.k-design{--k:#1f5fbf}.k-content{--k:#8a4b00}.k-writing{--k:#7a3e9d}.k-tuning{--k:#a2361f}.k-market{--k:#0b6e6e}.k-automation{--k:#4d6a00}.k-release{--k:#126449}.k-assistant{--k:#8a2f62}.k-collab{--k:#6b5600}html[data-theme=dark] .k-design{--k:#8fb8ff}html[data-theme=dark] .k-content{--k:#f0b46a}html[data-theme=dark] .k-writing{--k:#d3a6f0}html[data-theme=dark] .k-tuning{--k:#ff9f8a}html[data-theme=dark] .k-market{--k:#6fd6d6}html[data-theme=dark] .k-automation{--k:#b8d86a}html[data-theme=dark] .k-release{--k:#83e2ba}html[data-theme=dark] .k-assistant{--k:#f29cc8}html[data-theme=dark] .k-collab{--k:#e3cf6a}html.js .stages.js-only{display:grid}.stages{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:18px 0}.stage{display:flex;flex-direction:column;align-items:flex-start;gap:2px;text-align:left;border-radius:22px;padding:14px 18px;border:2px solid var(--line);border-left:6px solid var(--k);min-width:0}.stage b{color:var(--muted);font-size:14px}.stage span{font-weight:750;font-size:17px}.stage small{color:var(--k)}.stage[aria-pressed=true]{background:var(--soft);color:var(--text);border-color:var(--k);transform:translateY(-2px)}.stage-card{border:2px solid var(--k);border-radius:26px;padding:24px;background:var(--panel)}.stage-top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline}.stage-top strong{font-size:24px}#stage-what{font-size:17px}.result.three{grid-template-columns:repeat(3,minmax(0,1fr))}.result.three strong{font-size:17px;line-height:1.7;margin-top:6px}#pipeline details{margin-top:16px}#pipeline li{margin:10px 0}.panel{padding:26px;border:1px solid var(--line);border-radius:28px;background:var(--panel);margin:24px 0}.logic-head{display:flex;gap:16px;justify-content:space-between;align-items:center}.logic-head h2{margin:0}.cases,.filters{display:flex;flex-wrap:wrap;gap:10px;margin:18px 0}.flow{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;list-style:none;padding:0;counter-reset:flow}.flow li{padding:20px;border-radius:22px;background:var(--soft);border:2px solid transparent;min-width:0;position:relative}.flow li b{display:block;font-size:22px}.flow li.active{border-color:var(--accent);transform:translateY(-3px)}.flow li:before{counter-increment:flow;content:"0" counter(flow);display:block;font-weight:800;color:var(--muted)}.result{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.result>div{padding:20px;border:1px solid var(--line);border-radius:22px}.result strong{font-size:22px;display:block}.result span{color:var(--muted)}#example{padding:14px 18px;border-radius:18px;background:var(--soft)}.tools{margin-top:32px}.search{display:block;width:100%;min-height:56px;padding:13px 24px;border:1px solid var(--line);border-radius:999px;background:var(--panel)}.doc{margin:18px 0;scroll-margin-top:20px}.doc details{border:1px solid var(--line);border-radius:24px;background:var(--panel);overflow:hidden}.doc summary{cursor:pointer;min-height:58px;padding:18px 24px;font-size:20px;font-weight:750;overflow-wrap:anywhere}.tag{font-size:14px;margin-right:14px}.prose{padding:0 26px 26px;overflow-wrap:anywhere}.prose h1{font-size:28px}.prose h2{margin-top:24px}.prose h3{font-size:20px}.prose li{margin:8px 0}.prose pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--soft);padding:20px;border-radius:18px;position:relative}.prose code{font-size:14px;font-family:ui-monospace,Consolas,monospace}.prose pre code{font-size:15px;line-height:1.9}.prose table{width:100%;border-collapse:collapse;min-width:560px}.table-wrap{overflow:auto;margin:18px 0;border:1px solid var(--line);border-radius:16px}.prose th,.prose td{padding:13px 16px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}.prose th{background:var(--soft)}.copy{display:block;margin:12px 0 0 auto;font-size:14px}.top-button{position:fixed;bottom:max(12px,env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);box-shadow:0 4px 20px #0002;z-index:5}.no-results{padding:24px}.note{font-size:14px}.js-only{display:none}html.js .js-only{display:block}html.js .cases.js-only{display:flex}.prose>h1:first-child{display:none}
@media(max-width:1000px){.roles{grid-template-columns:repeat(2,minmax(0,1fr))}.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.stages{grid-template-columns:repeat(3,minmax(0,1fr))}.result.three{grid-template-columns:1fr}}
@media(max-width:600px){main{padding:16px 16px 110px}.roles{grid-template-columns:1fr}.role{padding:20px}.panel{padding:20px}.logic-head{align-items:flex-start;flex-direction:column}.flow,.result{grid-template-columns:1fr}.stages{grid-template-columns:repeat(2,minmax(0,1fr))}.stage{padding:12px 14px}.stage-card{padding:20px}.hero{border-radius:26px}.prose{padding:0 20px 20px}.doc summary{padding:18px 20px;font-size:18px}.prose table{min-width:520px}.top{align-items:flex-start}.top .brand{max-width:190px}}
@media(prefers-reduced-motion:no-preference){.flow li,.stage{transition:transform .2s,border-color .2s,background .2s}.stage-card{transition:border-color .3s}}
@media print{button,.tools,.top-button{display:none!important}main{padding:0}.roles{display:none}.doc details{border:0}.prose{padding:0}body{background:#fff;color:#000}}
</style></head><body><main>
<div class="top"><div><div class="brand">盤勢有據 · 工作手冊</div><small>整理日期 2026/10/19 · 可離線閱讀</small></div><button type="button" id="theme" aria-label="切換深淺主題" class="js-only">切換深色</button></div>
<header class="hero"><div class="eyebrow">規格 × 技能 × 可複製請求</div><h1>把每次確認的規則，<br>變成下次工作的起點。</h1><p>先選任務角色，再查完整規格。九個技能各管一個問題：協作與授權、介面、分類證據、公開文字、規則調整、行情、自動化、發布驗收、網站助理；同一條規則只寫在一個地方。</p><p>本檔是工作規格索引，不會讀取正式資料、呼叫 AI 或發送通知。</p></header>
<nav class="roles" aria-label="九種工作角色">@@ROLES@@</nav>
@@PIPELINE@@
<section class="panel" id="logic" aria-labelledby="logic-title"><div class="logic-head"><h2 id="logic-title">一句「買賣」，為何可能得到不同分類？</h2><button type="button" id="replay" class="js-only">再播判定</button></div><p class="muted">以下全部是虛構甲股句型；先分動作、日期與部位，再決定進場立場。</p>
<div class="cases js-only" role="group" aria-label="比較句型"><button type="button" data-case="0" aria-pressed="true">可以買</button><button type="button" data-case="1" aria-pressed="false">今天已買</button><button type="button" data-case="2" aria-pressed="false">不要殺低</button><button type="button" data-case="3" aria-pressed="false">部分賣出</button></div>
<p id="example">虛構示例：「如果想買股票，甲股可以買。」</p><ol class="flow"><li><b>核對主體</b><span id="actor">對尚未進場者的建議</span></li><li><b>核對動作</b><span id="action">可以買，沒有完成成交</span></li><li><b>分開判斷</b><span id="axis">部位事實與新進場立場</span></li><li><b>保留證據</b><span id="proof">原句核對，不靠「買」單字</span></li></ol>
<div class="result"><div><span>部位／交易</span><strong id="position">不新增交易</strong></div><div><span>新進場立場</span><strong id="stance">觀望注意</strong></div></div><p id="animation-state" class="note muted" aria-live="polite">點選例子可比較；減少動態模式不自動播放。</p>
<details><summary>沒有 JavaScript 也能讀的四種結果</summary><ul><li>可以買：進場建議，觀望注意，不新增成交。</li><li>今天已買：本人今天完成且證據核實，才是當日買入候選。</li><li>不要殺低：不是今日賣出；有已核對部位時保持持有。</li><li>部分賣出：保留減碼事件及剩餘部位；暫不買回可另列不碰，不關整檔。</li></ul></details></section>
<section class="tools js-only" aria-label="規格篩選"><label for="search">找規則、工具或範本</label><input class="search" id="search" type="search" placeholder="例如：可以買、自稱分類、回測、clasp、Flash Lite、務必" autocomplete="off"><div class="filters" role="group" aria-label="領域篩選">@@FILTERS@@</div><p id="count" class="note muted" role="status"></p><button type="button" id="expand">展開目前結果</button></section>
<div id="docs">@@DOCS@@</div><p class="no-results" id="empty" hidden>沒有符合的規格。可清除搜尋或改選「全部」。</p>
<footer><p>Markdown 是維護正本。修改後以 <code>python scripts/build_stock_skill_handbook.py</code> 重建本檔。</p><p class="muted note">原安裝技能可同步更新；本檔不是正式網站技術頁，沒有重新部署或執行每日流程。</p><a href="常用工作規格與指令.md">閱讀 Markdown 正本</a></footer>
</main><button type="button" class="top-button js-only" id="top">↑ 回到頂端</button>
<script>
document.documentElement.classList.add('js');
const $=s=>document.querySelector(s),all=s=>Array.from(document.querySelectorAll(s));
let dark=matchMedia('(prefers-color-scheme:dark)').matches;
function theme(){document.documentElement.dataset.theme=dark?'dark':'light';$('#theme').textContent=dark?'切換淺色':'切換深色'}theme();$('#theme').addEventListener('click',()=>{dark=!dark;theme()});
all('pre').forEach(pre=>{const b=document.createElement('button');b.type='button';b.className='copy';b.textContent='複製這段請求';const value=pre.textContent;b.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(value);b.textContent='已複製'}catch{const range=document.createRange();range.selectNodeContents(pre.querySelector('code')||pre);getSelection().removeAllRanges();getSelection().addRange(range);b.textContent='已選取，請複製'}});pre.after(b)});
const docs=all('.doc');let filter='all';function apply(){const query=$('#search').value.trim().toLocaleLowerCase();let n=0;docs.forEach(d=>{const show=(filter==='all'||d.dataset.group===filter)&&d.textContent.toLocaleLowerCase().includes(query);d.hidden=!show;if(show)n++});$('#count').textContent=`顯示 ${n} 個規格區塊` ;$('#empty').hidden=!!n}all('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;all('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));apply()}));$('#search').addEventListener('input',apply);apply();$('#expand').addEventListener('click',()=>{docs.filter(d=>!d.hidden).forEach(d=>d.querySelector('details').open=true)});
all('.role').forEach(a=>a.addEventListener('click',()=>{filter='all';$('#search').value='';all('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.filter==='all')));apply();document.querySelector(a.getAttribute('href')).querySelector('details').open=true}));
$('#top').addEventListener('click',()=>scrollTo({top:0,behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth'}));
const cases=[
['虛構示例：「如果想買股票，甲股可以買。」','對尚未進場者的建議','可以買，沒有完成成交','部位事實與新進場立場','原句核對，不靠「買」單字','不新增交易','觀望注意'],
['虛構示例：「我今天已買進甲股。」','本人，非觀眾提問','今天，已完成買進','成交候選送既有批次核對','原句、日期與公司都需核實','當日買入候選','不是僅憑正面語氣'],
['虛構示例：「甲股今天不准殺低，繼續抱。」','原有部位者，需有部位證據','勸續抱，並未宣告賣出','不要賣，不等於不要買','既有部位及否定作用核對','保留已核對持股','不據此新增買賣'],
['虛構示例：「甲股今天只賣部分，剩餘續抱，暫不買回。」','已核對原有部位','部分賣出，沒有全數出場','剩餘部位與暫不買回分開','不猜剩餘張數或報酬權重','減碼事件＋剩餘持股','暫不買回：觀望不碰']
];let manual=false,visible=false,timer=null,step=-1;const reduced=matchMedia('(prefers-reduced-motion:reduce)'),flow=all('.flow li');
function stop(){clearInterval(timer);timer=null;flow.forEach(x=>x.classList.remove('active'));$('#logic').dataset.playing='false'}
function play(){stop();if(!visible||manual||document.hidden||reduced.matches)return;$('#logic').dataset.playing='true';$('#animation-state').textContent='圖解循環中；點選句型後停止，再播可恢復。';step=0;flow[0].classList.add('active');timer=setInterval(()=>{step=(step+1)%flow.length;flow.forEach((x,i)=>x.classList.toggle('active',i===step))},950)}
all('[data-case]').forEach(b=>b.addEventListener('click',()=>{manual=true;stop();const c=cases[Number(b.dataset.case)];['example','actor','action','axis','proof','position','stance'].forEach((id,i)=>$('#'+id).textContent=c[i]);all('[data-case]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));$('#animation-state').textContent='已依選擇停止。可按「再播判定」繼續。'}));$('#replay').addEventListener('click',()=>{manual=false;play();if(reduced.matches)$('#animation-state').textContent='減少動態模式：保留靜態圖解。'});new IntersectionObserver(es=>{visible=es[0].isIntersecting;if(visible)play();else stop()},{threshold:.15}).observe($('#logic'));document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();else play()});reduced.addEventListener('change',play);
const stages=JSON.parse($('#stage-data').textContent),stageButtons=all('[data-stage]'),card=$('#stage-card');let pManual=false,pVisible=false,pTimer=null,pIdx=0;
function showStage(i){pIdx=i;const d=stages[i];card.className='stage-card js-only k-'+d[1];$('#stage-skill').textContent=d[2];$('#stage-title').textContent=String(i+1).padStart(2,'0')+' '+d[0];$('#stage-what').textContent=d[3];$('#stage-gate').textContent=d[4];$('#stage-mistake').textContent=d[5];$('#stage-proof').textContent=d[6];$('#stage-link').setAttribute('href','#'+d[1]);stageButtons.forEach((b,j)=>b.setAttribute('aria-pressed',String(j===i)))}
function pStop(){clearInterval(pTimer);pTimer=null;$('#pipeline').dataset.playing='false'}
function pPlay(){pStop();if(!pVisible||pManual||document.hidden||reduced.matches)return;$('#pipeline').dataset.playing='true';$('#pipe-state').textContent='流程依序播放中；點選任一站即停止，再播可恢復。';pTimer=setInterval(()=>showStage((pIdx+1)%stages.length),2400)}
stageButtons.forEach(b=>b.addEventListener('click',()=>{pManual=true;pStop();showStage(Number(b.dataset.stage));$('#pipe-state').textContent='已停在這一站。按「再播流程」繼續。'}));
$('#pipe-replay').addEventListener('click',()=>{pManual=false;pPlay();if(reduced.matches)$('#pipe-state').textContent='減少動態模式：不自動播放，可逐站點選。'});
$('#stage-link').addEventListener('click',()=>{const t=document.querySelector($('#stage-link').getAttribute('href'));if(t){filter='all';$('#search').value='';all('[data-filter]').forEach(x=>x.setAttribute('aria-pressed',String(x.dataset.filter==='all')));apply();t.querySelector('details').open=true}});
function openHash(){const t=location.hash&&document.getElementById(decodeURIComponent(location.hash.slice(1)));if(t&&t.classList.contains('doc')){t.hidden=false;t.querySelector('details').open=true;t.scrollIntoView()}}addEventListener('hashchange',openHash);openHash();
new IntersectionObserver(es=>{pVisible=es[0].isIntersecting;if(pVisible)pPlay();else pStop()},{threshold:.15}).observe($('#pipeline'));document.addEventListener('visibilitychange',()=>{if(document.hidden)pStop();else pPlay()});reduced.addEventListener('change',pPlay);
</script></body></html>'''


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true", help="Sync only the nine versioned Stock skills into ~/.codex/skills")
    parser.add_argument("--check-installed", action="store_true", help="Check SHA-256 parity with installed skill files")
    args = parser.parse_args()
    files = validate_references()
    output = build()
    (BASE / "index.html").write_text(output, encoding="utf-8", newline="\n")
    sync_installed(args.install, args.check_installed)
    print(f"Built offline handbook from {len(files)} Markdown sources: {BASE / 'index.html'}")


if __name__ == "__main__":
    main()
