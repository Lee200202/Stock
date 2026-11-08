"""回到本機時，一次看清雲端那邊動過什麼（唯讀）。

    python scripts/cloud_sync_status.py [--days 14] [--no-fetch] [--json]

列出：
1. 本機 checkout：分支、未提交檔數、和 origin/main 差幾個提交。
2. 雲端（排程或雲端工作階段）推過的分支：每日修正、每週檢討、其他 claude/ 分支，
   最後提交時間、是否已併入 main。
3. 關鍵 GitHub Actions 最近的執行與結論（公開 API；有 GITHUB_TOKEN 環境變數就帶上）。
4. main 上最近的每週檢討報告（第 5 節是待管理者問卷）。

不讀試算表、不讀金鑰、不改任何檔案；只會 `git fetch --prune origin`（可用 --no-fetch 關掉）。
雲端排程本身的狀態（enabled、next_run_at、執行紀錄）要用 Claude Code 的 RemoteTrigger 或
claude.ai/code/routines 看，這支腳本拿不到。見 docs/stock-skills/skills/stock-cloud-sync。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "Lee200202/Stock"
TPE = timezone(timedelta(hours=8))

# 分支名稱 → 誰推的、推了會觸發什麼（與 .github/workflows 的 push 觸發條件一致）
BRANCH_KINDS = (
    (re.compile(r"^(daily-review/apply-|claude/daily-review-apply-)(\d{8})$"), "每日修正", "daily-review-apply"),
    (re.compile(r"^(claude/pipeline-weekly-|pipeline-weekly/)(\d{8})$"), "每週檢討", "pipeline-weekly"),
    (re.compile(r"^claude/"), "其他雲端／協作分支", ""),
)
WORKFLOWS = (
    ("daily-review-apply.yml", "每日修正套用"),
    ("pipeline-weekly.yml", "每週檢討守門與合併"),
    ("daily.yml", "每日主流程"),
    ("regenerate-verified.yml", "單日品質核對與更新"),
    ("day-sync.yml", "逐日編輯同步"),
    ("daily-review-check.yml", "每日複核腳本實測"),
)


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def local_state() -> dict:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()
    dirty = [l for l in git("status", "--porcelain").splitlines() if l.strip()]
    behind, ahead = git("rev-list", "--left-right", "--count", "origin/main...HEAD").split()
    return {"branch": branch, "dirty": len(dirty), "untracked": sum(l.startswith("??") for l in dirty),
            "behind_main": int(behind), "ahead_of_main": int(ahead),
            "main": git("log", "-1", "--format=%h %s", "origin/main").strip()}


def cloud_branches(days: int) -> list[dict]:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    merged = {l.strip() for l in git("branch", "-r", "--merged", "origin/main").splitlines()}
    rows = []
    fmt = "%(refname:short)\t%(committerdate:iso-strict)\t%(objectname:short)\t%(subject)"
    for line in git("for-each-ref", "--sort=-committerdate", f"--format={fmt}", "refs/remotes/origin").splitlines():
        ref, when, sha, subject = (line.split("\t", 3) + [""] * 4)[:4]
        name = ref.removeprefix("origin/")
        if name in ("HEAD", "main", "origin"):
            continue
        stamp = datetime.fromisoformat(when)
        if stamp < since:
            continue
        for pattern, kind, workflow in BRANCH_KINDS:
            if pattern.match(name):
                rows.append({"branch": name, "kind": kind, "workflow": workflow, "sha": sha,
                             "when": stamp.astimezone(TPE).strftime("%m/%d %H:%M"),
                             "merged": ref in merged, "subject": subject})
                break
    return rows


def actions_runs(days: int, per: int = 5) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "stock-cloud-sync-status"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    rows = []
    for wf, label in WORKFLOWS:
        url = (f"https://api.github.com/repos/{REPO}/actions/workflows/{wf}/runs"
               f"?per_page={per}&created=%3E%3D{since}")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                data = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            rows.append({"workflow": wf, "label": label, "error": f"讀不到（{type(exc).__name__}）"})
            continue
        for run in data.get("workflow_runs", []):
            created = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(TPE)
            rows.append({"workflow": wf, "label": label, "id": run["id"], "event": run["event"],
                         "branch": run["head_branch"], "when": created.strftime("%m/%d %H:%M"),
                         "status": run["status"], "conclusion": run["conclusion"] or "",
                         "url": run["html_url"]})
    return rows


def weekly_reports(days: int) -> list[str]:
    since = (datetime.now(TPE) - timedelta(days=days)).strftime("%Y-%m-%d")
    names = git("ls-tree", "-d", "--name-only", "origin/main", "docs/weekly-review/").splitlines()
    out = []
    for path in names:
        day = path.rsplit("/", 1)[-1]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day >= since:
            out.append(f"{path}/報告.md")
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=14, help="往回看幾天（預設 14）")
    ap.add_argument("--no-fetch", action="store_true", help="不先 git fetch")
    ap.add_argument("--json", action="store_true", help="輸出 JSON")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not a.no_fetch:
        git("fetch", "--prune", "--quiet", "origin")
    report = {"local": local_state(), "branches": cloud_branches(a.days),
              "actions": actions_runs(a.days), "weekly_reports": weekly_reports(a.days)}
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    loc = report["local"]
    print(f"== 本機：{loc['branch']}｜未提交 {loc['dirty']} 檔（其中未追蹤 {loc['untracked']}）"
          f"｜落後 main {loc['behind_main']}、領先 {loc['ahead_of_main']}")
    print(f"   origin/main：{loc['main']}")
    print(f"\n== 近 {a.days} 天雲端推過的分支")
    if not report["branches"]:
        print("   （沒有）")
    for b in report["branches"]:
        if b["kind"] == "每日修正":
            state = "只放計畫檔、不合併；看套用 run"  # 修正分支本來就不併入 main
        else:
            state = "已併入 main" if b["merged"] else "未併入"
        trig = f"→ {b['workflow']}" if b["workflow"] else ""
        print(f"   {b['when']}  {b['branch']}  [{b['kind']}｜{state}] {trig}  {b['sha']} {b['subject'][:60]}")
    print(f"\n== 近 {a.days} 天 GitHub Actions（每個工作流程最多 5 筆）")
    for r in report["actions"]:
        if "error" in r:
            print(f"   {r['label']}：{r['error']}")
            continue
        result = r["conclusion"] or r["status"]
        print(f"   {r['when']}  {r['label']}  {r['event']}/{r['branch']}  {result}  #{r['id']}")
    print("\n== main 上的每週檢討報告（第 5 節是待管理者問卷）")
    for p in report["weekly_reports"] or ["（沒有）"]:
        print(f"   {p}")
    print("\n下一步：未併入的每週分支看 pipeline-weekly 結論；failure 的 run 讀 log；"
          "雲端排程狀態用 RemoteTrigger list／list_runs；記憶跑 stock-private-sync/sync.ps1。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
