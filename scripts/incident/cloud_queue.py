"""雲端「事故處理」排程用的佇列（2026/11/26）。只讀寫複核紀錄「事故紀錄」，不碰主資料、不寄信、不推播。

  python scripts/incident/cloud_queue.py list                       # 待雲端處理的事故（含錯誤摘要）；沒有就印「沒有待處理事故」並退出碼 3
  python scripts/incident/cloud_queue.py claim --id INC...          # 開始處理（狀態改「雲端處理中」）
  python scripts/incident/cloud_queue.py release --id INC... --state 已提交修正 --branch claude/incident-...
  python scripts/incident/cloud_queue.py release --id INC... --state 待管理者 --note '不是程式問題：…'
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import triage  # noqa: E402

STATES = ('已提交修正', '待管理者', '不需修正')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('list')
    c = sub.add_parser('claim')
    c.add_argument('--id', required=True)
    r = sub.add_parser('release')
    r.add_argument('--id', required=True)
    r.add_argument('--state', required=True, choices=STATES)
    r.add_argument('--note', default='')
    r.add_argument('--branch', default='')
    a = ap.parse_args()
    book, rows_of = triage.open_tabs()
    ws = book.worksheet(triage.TAB)
    head, rows = rows_of(ws)
    col = {h: i + 1 for i, h in enumerate(head)}
    if a.cmd == 'list':
        todo = [r for r in rows if r.get('處理狀態') == '待雲端處理']
        if not todo:
            print('沒有待處理事故')
            return 3
        for r in todo:
            print(f"== {r['事故ID']}　{r['日期']}　{r['工作流程']}　執行 {r['執行']}（第 {r['嘗試']} 次）")
            print(f"失敗步驟：{r['失敗步驟']}；分類：{r['分類']}；指紋：{r['指紋']}；說明：{r['說明']}")
            print('錯誤摘要：\n' + r.get('錯誤摘要', ''))
        return 0
    for i, r in enumerate(rows, start=2):
        if r.get('事故ID') != a.id:
            continue
        if a.cmd == 'claim':
            if r.get('處理狀態') != '待雲端處理':
                print(f"{a.id} 目前是「{r.get('處理狀態')}」，不能接手")
                return 1
            ws.update_cell(i, col['處理狀態'], '雲端處理中')
            ws.update_cell(i, col['更新時間'], triage.stamp())
            print(f'{a.id} → 雲端處理中')
            return 0
        if a.state == '已提交修正':
            ws.update_cell(i, col['處理狀態'], '已提交修正')
            ws.update_cell(i, col['分支'], a.branch)
            ws.update_cell(i, col['結果'], a.note[:500] or 'incident-fix 工作流程把關中')
            ws.update_cell(i, col['更新時間'], triage.stamp())
            print(f'{a.id} → 已提交修正（{a.branch}）')
            return 0
        return triage.cmd_resolve(a.id, a.state, a.note, a.branch)
    print(f'找不到 {a.id}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
