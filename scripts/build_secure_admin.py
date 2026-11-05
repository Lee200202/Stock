"""Build the existing full admin UI as same-origin Worker assets; no API calls or secrets."""
from pathlib import Path
import argparse
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("original_build", ROOT / "scripts/build_original_site.py")
original = importlib.util.module_from_spec(spec)
spec.loader.exec_module(original)

def build(output: Path):
    target = output / "admin"
    target.mkdir(parents=True, exist_ok=True)
    page = original.render(original.SOURCE / "Admin.html", {"webAppUrl": original.PUBLIC_URL})
    page = original.with_viewport(page, "Admin")
    page = page.replace('<body>', '<body><script src="/admin/original-bridge.js"></script>', 1)
    if 'src="/admin/original-bridge.js"' not in page:
        raise ValueError("Secure admin bridge insertion failed")
    (target / "index.html").write_text(page, encoding="utf-8")
    bridge = (ROOT / "public-site/original-bridge.js").read_text(encoding="utf-8")
    bridge = bridge.replace("__SITE_API_URL__", "https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api")
    (target / "original-bridge.js").write_text(bridge, encoding="utf-8")
    print("Secure admin assets built; legacy login activation is unchanged.")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, default=ROOT / "site-api/admin-assets")
    build(p.parse_args().output)
