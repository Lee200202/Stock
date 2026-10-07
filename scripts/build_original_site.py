"""Render the unmodified Apps Script public HTML includes for GitHub Pages.

The source fragments in public-site/gas-source are byte-for-byte copies of the
current GAS HTML files. Only the four values normally supplied by doGet and the
include directives are expanded. Backend calls use a separately deployed relay.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "public-site" / "gas-source"
INCLUDE = re.compile(r"<\?!=\s*include\('([A-Za-z]+)'\);?\s*\?>")
VALUE = re.compile(r"<\?=\s*([A-Za-z]+)\s*\?>")
PUBLIC_URL = "https://lee200202.github.io/Stock/"


def render(source: Path, values: dict[str, str]) -> str:
    text = source.read_text(encoding="utf-8")

    def include(match: re.Match[str]) -> str:
        name = match.group(1)
        path = SOURCE / f"{name}.html"
        if not path.is_file():
            raise ValueError(f"Apps Script include missing: {name}")
        return render(path, values)

    text = INCLUDE.sub(include, text)

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ValueError(f"Apps Script template value missing: {key}")
        return html.escape(values[key], quote=True)

    return VALUE.sub(value, text)


# Apps Script 的 viewport 由 doGet 的 HtmlService.addMetaTag 加上，不在 HTML 檔裡。
# Pages 組裝少了這一行時，手機瀏覽器會用 980px 的桌面寬度排版再整頁縮小：
# 字變得很小、手機卡片版（max-width 760px 媒體查詢）永遠不會啟用（2026/09/30 實測）。
VIEWPORT = {
    "Index": "width=device-width, initial-scale=1, viewport-fit=cover",
    "Admin": "width=device-width, initial-scale=1, viewport-fit=cover",
    "Unsubscribed": "width=device-width, initial-scale=1",
}


def with_viewport(page: str, template: str) -> str:
    if re.search(r'<meta[^>]+name="viewport"', page):
        return page
    tag = '<meta name="viewport" content="' + VIEWPORT[template] + '">'
    page, count = re.subn(r'<meta charset="utf-8">', '<meta charset="utf-8">\n  ' + tag, page, count=1)
    if count != 1:
        page, count = re.subn(r"<head([^>]*)>", r"<head\1>\n  " + tag, page, count=1)
    if count != 1:
        raise ValueError(f"Head tag missing in {template}")
    return page


def build(output: Path, api_url: str) -> None:
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+/(?:api/?|api\?[A-Za-z0-9=&_-]+)", api_url):
        raise ValueError("SITE_API_URL must be an HTTPS /api endpoint")
    output.mkdir(parents=True, exist_ok=True)
    config = (SOURCE / "Config.gs").read_text(encoding="utf-8")
    title_match = re.search(r"var APP_TITLE = '([^']+)';", config)
    app_title = title_match.group(1) if title_match else "盤勢有據　為家人投資，逐日有據"
    # 頁尾的來源與免責聲明與 Apps Script 版同一句（Config.gs 的 DISCLAIMER）
    disclaimer_match = re.search(r"var DISCLAIMER = '([^']+)';", config)
    disclaimer = disclaimer_match.group(1) if disclaimer_match else "本網站整理公開節目內容，不構成投資建議或獲利保證。"
    values = {
        "initialTab": "",
        "initialStock": "",
        "webAppUrl": PUBLIC_URL,
        "appTitle": app_title,
        "disclaimer": disclaimer,
    }
    index = (SOURCE / "Index.html").read_text(encoding="utf-8")
    if index.count("<?!= include('JavaScript'); ?>") != 1:
        raise ValueError("Original JavaScript include location changed")
    page = render(SOURCE / "Index.html", values)
    # GAS sets this through HtmlService.setTitle(APP_TITLE), outside Index.html.
    # Keep the browser tab identical when the same source is served by Pages.
    if "<title>" not in page:
        page = page.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <title>' + html.escape(app_title) + '</title>', 1)
    if "<?" in page:
        raise ValueError("Unexpanded Apps Script template tag")
    page = with_viewport(page, "Index")
    bridge = (ROOT / "public-site" / "original-bridge.js").read_text(encoding="utf-8")
    bridge = bridge.replace("__SITE_API_URL__", api_url)
    # 市場模組在 JavaScript.html 之前就有 script；橋接器必須在 body 開頭。
    # 首頁把橋接器直接內嵌（約 8KB）：少一個會擋住解析的請求。後台頁仍用外部檔。
    if "</script" in bridge.lower():
        raise ValueError("Bridge cannot be inlined: it contains a closing script tag")
    page, count = re.subn(r"<body([^>]*)>", lambda m: "<body" + m.group(1) + ">\n<script>\n" + bridge + "</script>", page, count=1)
    if count != 1:
        raise ValueError("Body tag missing in Index")
    # 首頁總覽提早出發：頁面開頭就送出 apiGetDashboard（和橋接器送的是同一個請求），不等整頁解析完。
    # 只在首頁、而且不是轉往後台或退訂時送。這是當下的即時請求，不存、不重用；橋接器接走它或 15 秒後作廢。
    origin = re.match(r"https://[^/]+", api_url).group(0)
    early = ('<link rel="preconnect" href="' + origin + '" crossorigin>\n  <script>(function(){try{'
             'if(/[?&](?:page|action)=/.test(location.search))return;'
             'var p=fetch(' + json.dumps(api_url) + ',{method:"POST",mode:"cors",cache:"no-store",headers:{"Content-Type":"application/json"},'
             'body:\'{"method":"apiGetDashboard","args":[]}\'}).then(function(r){return r.json().then(function(b){'
             'if(!r.ok||!b||!b.ok){var e=new Error((b&&b.error)||("HTTP "+r.status));e.retry=!r.ok&&r.status>=500;throw e;}return b.result;});});'
             'p.catch(function(){});window.__earlyDashboard={at:Date.now(),promise:p};'
             '}catch(e){}})();</script>')
    page, count = re.subn(r'<meta charset="utf-8">', lambda m: m.group(0) + "\n  " + early, page, count=1)
    if count != 1:
        raise ValueError("Charset tag missing in Index")
    (output / "index.html").write_text(page, encoding="utf-8")
    for template, filename in (("Admin", "admin.html"), ("Admin", "admin-legacy.html"), ("Unsubscribed", "unsubscribe.html")):
        content = render(SOURCE / f"{template}.html", values)
        if "<?" in content:
            raise ValueError(f"Unexpanded Apps Script template tag in {template}")
        content = with_viewport(content, template)
        content, count = re.subn(r"<body([^>]*)>",
            r'<body\1>\n<script src="original-bridge.js"></script>', content, count=1)
        if count != 1:
            raise ValueError(f"Body tag missing in {template}")
        (output / filename).write_text(content, encoding="utf-8")
    (output / "original-bridge.js").write_text(bridge, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")
    print("原站 HTML/CSS/JavaScript 已原樣組裝；後端請求經 SITE_API_URL 橋接")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    build(args.output, os.environ.get("SITE_API_URL", ""))


if __name__ == "__main__":
    main()
