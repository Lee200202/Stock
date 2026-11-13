"""套用修正時的「沒有重寄」核對（純函式，不連試算表）。

2026/11/13 管理者：LINE 回覆裁示後雲端套用，GitHub 一直寄「同期寄送帳本變動，請核對」的失敗通知，但資料其實已經寫好、
網站也刷新了。原因是舊核對把整張「寄送帳本」「LINE 寄送帳本」做指紋，前後不同就判失敗；可是套用期間別的程序本來就會寫帳本
（管理者在 LINE 回覆、待辦卡、簡訊通知、12:10 整理信正常寄出），所以每次有人同時在用 LINE 就誤報，還讓後面的
股名學習與①③章節步驟被跳過。

套用本身不寄信；要防的只有「套用讓當天整理信再寄一次」。所以只看當天每日整理信的列：
- 帳本裡訊息 ID 是 `daily|<日期>` 的列：套用前已經被服務接受的收件者，接受時間不能變，也不能多出第二列；
- 每日推播內容的寄送狀態：套用前已寄（已寄送／部分寄送／寄送中）不能退回未寄。
套用期間第一次寄出（待寄送 → 已寄送）、其他種類的信、LINE 對話與卡片都不算問題。
"""
import re

SENT_RE = re.compile(r'已寄|寄送中|部分寄送')


def _rows(values, day):
    if not values:
        return []
    head = [str(h).strip() for h in values[0]]

    def col(*names):
        return next((head.index(n) for n in names if n in head), None)
    mid, who = col('訊息ID'), col('收件者')
    acc = col('服務接受時間', '接受時間')
    if mid is None or who is None:
        return []
    key = 'daily|' + day
    out = []
    for r in values[1:]:
        cell = lambda i: str(r[i]).strip() if i is not None and i < len(r) else ''
        if cell(mid) == key:
            out.append((cell(who), cell(acc)))
    return out


def resend_problems(before, after, day, name='寄送帳本'):
    """before/after＝帳本 get_all_values()。回傳問題清單（空＝沒有重寄跡象）。"""
    problems = []
    was = {}
    for who, acc in _rows(before, day):
        if acc:
            was[who] = acc
    seen = {}
    for who, acc in _rows(after, day):
        seen[who] = seen.get(who, 0) + 1
        if who in was and acc != was[who]:
            problems.append(f'{name}：{who[:3]}… 當天整理信的接受時間改變（{was[who]} → {acc or "空"}）')
    for who, n in seen.items():
        if n > 1 and who in was:
            problems.append(f'{name}：{who[:3]}… 當天整理信出現 {n} 列')
    return problems


def status_regressed(before, after):
    """寄送狀態從已寄退回未寄（套用把已寄的信改回待寄送，下一棒就會再寄）。"""
    return bool(SENT_RE.search(str(before or ''))) and not SENT_RE.search(str(after or ''))


LEDGERS = ('寄送帳本', 'LINE 寄送帳本')


def delivery_snapshot(book, day, status_fn=None):
    """寫入前記下兩張帳本與當天整理信寄送狀態（讀不到的帳本略過）。所有套用工具共用。"""
    ledgers = {}
    for name in LEDGERS:
        try:
            ledgers[name] = book.worksheet(name).get_all_values()
        except Exception:                                   # noqa: BLE001  沒有這張表就不核對
            pass
    return {'day': day, 'ledgers': ledgers, 'sent': status_fn(book, day) if status_fn else None}


def delivery_problems(book, snap, status_fn=None):
    """刷新後核對：只回報「當天整理信重寄」或「寄送狀態退回未寄」；別的程序同時寫帳本不算。"""
    out = []
    for name, before in snap['ledgers'].items():
        try:
            now = book.worksheet(name).get_all_values()
        except Exception:                                   # noqa: BLE001
            continue
        out += resend_problems(before, now, snap['day'], name)
    if status_fn and snap.get('sent') is not None:
        now_sent = status_fn(book, snap['day'])
        if status_regressed(snap['sent'], now_sent):
            out.append(f"當天整理信寄送狀態從「{snap['sent']}」退回「{now_sent or '空'}」，下一棒會再寄")
    return out
