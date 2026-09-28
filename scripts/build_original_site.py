"""Render the unmodified Apps Script public HTML includes for GitHub Pages.

The source fragments in public-site/gas-source are byte-for-byte copies of the
current GAS HTML files. Only the four values normally supplied by doGet and the
include directives are expanded. Backend calls use a separately deployed relay.
"""

from __future__ import annotations

import argparse
import html
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "public-site" / "gas-source"
INCLUDE = re.compile(r"<\?!=\s*include\('([A-Za-z]+)'\);?\s*\?>")
VALUE = re.compile(r"<\?=\s*([A-Za-z]+)\s*\?>")
PUBLIC_URL = "https://script.google.com/macros/s/AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw/exec"


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


def build(output: Path, api_url: str) -> None:
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+/(?:api/?|api\?[A-Za-z0-9=&_-]+)", api_url):
        raise ValueError("SITE_API_URL must be an HTTPS /api endpoint")
    output.mkdir(parents=True, exist_ok=True)
    values = {
        "initialTab": "",
        "initialStock": "",
        "webAppUrl": PUBLIC_URL,
        "disclaimer": "本網站整理公開節目內容，不構成投資建議或獲利保證。",
    }
    index = (SOURCE / "Index.html").read_text(encoding="utf-8")
    marker = "<?!= include('JavaScript'); ?>"
    if index.count(marker) != 1:
        raise ValueError("Original JavaScript include location changed")
    index = index.replace(marker, '<script src="original-bridge.js"></script>\n' + marker, 1)
    staged = output / "_index_template.html"
    staged.write_text(index, encoding="utf-8")
    try:
        page = render(staged, values)
    finally:
        staged.unlink(missing_ok=True)
    if "<?" in page:
        raise ValueError("Unexpanded Apps Script template tag")
    bridge = (ROOT / "public-site" / "original-bridge.js").read_text(encoding="utf-8")
    bridge = bridge.replace("__SITE_API_URL__", api_url)
    # 原檔的 CSS、HTML、JS 全部不改；橋接器在原站 JavaScript 前執行。
    (output / "index.html").write_text(page, encoding="utf-8")
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
