"""OWNER-RUN ONLY: configure Worker secrets without command-line secrets or files.

This never changes GAS, GitHub secrets, deployments or sends mail. The owner must
complete the documented machine-key migration and final activation separately.
"""
import argparse
import getpass
import hashlib
import json
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--wrangler", required=True, type=Path, help="Path to wrangler.js; invokes Node")
    a = p.parse_args()
    if not a.wrangler.is_file():
        p.error("wrangler.js not found")
    check = subprocess.run(["node", str(a.wrangler.resolve()), "secret", "list", "--config", str(ROOT / "site-api/wrangler.jsonc")],
                           text=True, capture_output=True, cwd=ROOT / "site-api")
    try:
        existing = json.loads(check.stdout)
    except (ValueError, TypeError):
        raise SystemExit("Cannot verify existing secret NAMES. No changes made.")
    if check.returncode or any(row.get("name") == "ADMIN_DATA_KEY" for row in existing):
        raise SystemExit("Setup is unavailable or has already been run. Preserve the existing IP encryption key; no changes made.")
    print("Owner setup: keep this local terminal private. No secret file is written.")
    password = getpass.getpass("New manual passphrase (24-256 characters): ")
    if not 24 <= len(password) <= 256 or password != getpass.getpass("Repeat passphrase: "):
        raise SystemExit("Passphrase length or confirmation does not match.")
    # The owner chooses/copies this SAME independent secret into GAS properties.
    gateway = getpass.getpass("Independent ADMIN_GATEWAY_SECRET (64 lowercase hex characters, also set in GAS): ")
    if len(gateway) != 64 or any(c not in "0123456789abcdef" for c in gateway):
        raise SystemExit("Gateway key must be 32 random bytes encoded as 64 lowercase hex characters.")
    if gateway != getpass.getpass("Repeat gateway secret: "):
        raise SystemExit("Gateway confirmation does not match.")
    salt = secrets.token_hex(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600000).hex()
    values = {"ADMIN_PASSWORD_RECORD": json.dumps({"salt": salt, "hash": digest, "iterations": 600000}),
              "ADMIN_DATA_KEY": secrets.token_hex(32), "ADMIN_GATEWAY_SECRET": gateway}
    password = ""
    print("Will configure three Worker secrets. Existing IP encryption key must be preserved on later password changes.")
    if input("First-time setup only; type FIRST SETUP to continue: ") != "FIRST SETUP":
        raise SystemExit("No change made.")
    # CLI output can contain status only; never echo our JSON or send it as argv.
    result = subprocess.run(["node", str(a.wrangler.resolve()), "secret", "bulk", "--config", str(ROOT / "site-api/wrangler.jsonc")],
                            input=json.dumps(values), text=True, capture_output=True, cwd=ROOT / "site-api")
    if result.returncode:
        raise SystemExit("Worker secret update failed. No command output shown, to avoid exposing secrets. Check owner credentials.")
    print("Worker secrets accepted. This does not enable authentication, rotate machine credentials or update GAS deployment.")

if __name__ == "__main__":
    main()
