"""Keep the Pages transport aligned with the original Apps Script UI."""

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
    return set(re.findall(r"\.(api[A-Z][A-Za-z0-9_]*)\s*\(", content))


def main() -> None:
    public = invoked("Index.html", "JavaScript.html", "Market.html",
                     "MarketDetail.html", "MarketCharts.html", "Tech.html",
                     "Settings.html", "Unsubscribed.html")
    admin = invoked("Admin.html", "AdminLegacy.html")
    for name in public:
        if f"{name}: {name}" not in GAS or f"'{name}'" not in WORKER:
            raise SystemExit(f"Missing public bridge method: {name}")
    for name in admin:
        if name not in GAS or name not in WORKER:
            raise SystemExit(f"Missing admin bridge method: {name}")
    if "getProperty('ADMIN_KEY')" not in GAS:
        raise SystemExit("Admin bridge lost its backend key check")

    # The local GAS editor export is optional on CI. When present, require every
    # source file to be identical; Pages must not acquire a second, shorter UI.
    local = os.getenv("GAS_SOURCE_DIR")
    if local:
        for source in Path(local).iterdir():
            if source.suffix not in (".gs", ".html"):
                continue
            target = COPY / source.name
            if not target.is_file() or hashlib.sha256(source.read_bytes()).digest() != hashlib.sha256(target.read_bytes()).digest():
                raise SystemExit(f"Original GAS source diverged: {source.name}")
    print(f"Original UI bridge parity OK: {len(public)} public, {len(admin)} admin methods")


if __name__ == "__main__":
    main()
