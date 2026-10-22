"""維持Pages轉接層與原Apps Script介面一致。"""

import hashlib
import os
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COPY = ROOT / "public-site" / "gas-source"
WORKER = (ROOT / "site-api" / "worker.mjs").read_text(encoding="utf-8")
GAS = (COPY / "SiteBridge.gs").read_text(encoding="utf-8")


def invoked(*filenames: str) -> set[str]:
    content = "\n".join((COPY / name).read_text(encoding="utf-8") for name in filenames)
    # 後台工具有 run[fn](KEY) 動態呼叫；fn 是字串，舊檢查漏掉它，部署後才出現 method-not-allowed。
    direct = re.findall(r"\.(api[A-Z][A-Za-z0-9_]*)\s*\(", content)
    quoted = re.findall(r"['\"](apiAdmin[A-Z][A-Za-z0-9_]*)['\"]", content)
    return set(direct + quoted)


def admin_allowlist(source: str, start: str) -> set[str]:
    block = source.split(start, 1)[1].split(".split(' ')", 1)[0]
    return set(re.findall(r"apiAdmin[A-Z][A-Za-z0-9_]*", block))


def main() -> None:
    public = invoked("Index.html", "JavaScript.html", "Tech.html",
                     "Settings.html", "Unsubscribed.html", "UsageTracker.html")
    admin = invoked("Admin.html", "UsagePanel.html")
    for name in public:
        if f"{name}: {name}" not in GAS or f"'{name}'" not in WORKER:
            raise SystemExit(f"Missing public bridge method: {name}")
    gas_admin = admin_allowlist(GAS, "var adminNames =")
    worker_admin = admin_allowlist(WORKER, "const ADMIN_METHODS =")
    for name in admin:
        if name.startswith("apiAdmin") and (name not in gas_admin or name not in worker_admin):
            raise SystemExit(f"Missing admin bridge method: {name}")
        if not name.startswith("apiAdmin") and (f"{name}: {name}" not in GAS or f"'{name}'" not in WORKER):
            raise SystemExit(f"Missing public method used by admin: {name}")
    if "getProperty('ADMIN_KEY')" not in GAS:
        raise SystemExit("Admin bridge lost its backend key check")

    # CI可不提供本機GAS編輯器匯出檔；若有提供，
    # 每個來源檔都必須相同，Pages不可變成另一套縮減版介面。
    local = os.getenv("GAS_SOURCE_DIR")
    if local:
        for source in Path(local).iterdir():
            if source.suffix not in (".gs", ".html"):
                continue
            target = COPY / source.name
            if not target.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                raise SystemExit(f"Original GAS source diverged: {source.name}")
    # Apps Script的include()直接回傳原始檔內容，不求值其中模板標籤；
    # 因此巢狀include在Pages遞迴建置可用，但在原/exec會印成文字；2026/10/14的StoryDesign曾發生。
    top_level = {"Index.html", "Admin.html", "Unsubscribed.html", "Editor.html", "Reviewpanel.html", "Editorpanel.html"}
    for html in COPY.glob("*.html"):
        if html.name not in top_level and re.search(r"<\?", html.read_text(encoding="utf-8")):
            raise SystemExit(f"Included file has a template tag that /exec will not evaluate: {html.name}")
    print(f"Original UI bridge parity OK: {len(public)} public, {len(admin)} admin methods")


if __name__ == "__main__":
    main()
