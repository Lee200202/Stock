"""主資料分卷（2026/11/25 管理者定案）——GitHub 端。

主試算表被 Google 拒絕寫入（This document is too large to continue editing）時，不讓當天資料卡住：
  1. 開表前先向 Apps Script（action=backup-archive, op=main-route）查目前使用中的一卷；查不到就照舊用 SPREADSHEET_ID。
  2. 寫入撞到「檔案太大」→ 請 Apps Script 處理（op=main-relieve：先清空白欄，不夠就整本換到備用卷），
     再把同一個呼叫轉到新卷（同名分頁）重試。
  3. 日K快取這類累積表：舊卷在前、使用中在後合併讀取，欄位依使用中表頭對齊，同一筆以新卷為準。
GitHub secret 的 SPREADSHEET_ID 不用改；備用卷由 Apps Script 以擁有者帳號建立並分享給同一個服務帳號。
試算表 ID 只在記憶體裡使用，不印出、不寫檔。
"""
from __future__ import annotations

import json
import os
import re
import time

TOO_LARGE = re.compile(r'too large to continue editing|above the limit of [\d,]+ cells', re.I)
_STATE = {'route': None, 'client': None, 'email': '', 'switched': False, 'trimmed': False, 'books': {}}


def _secret_id() -> str:
    return os.environ.get('SPREADSHEET_ID', '').strip()


def is_too_large(exc) -> bool:
    text = str(exc)
    resp = getattr(exc, 'response', None)
    try:
        text += ' ' + json.dumps(resp.json(), ensure_ascii=False)
    except Exception:
        pass
    return bool(TOO_LARGE.search(text))


def _call(op, payload=None, source=None, timeout=330):
    url = os.environ.get('APPS_SCRIPT_URL', '').strip()
    key = os.environ.get('ADMIN_KEY', '').strip()
    src = (source or _secret_id()).strip()
    if not (url and key and src):
        return None
    import requests
    r = requests.get(url, params={'action': 'backup-archive', 'key': key, 'op': op, 'source': src,
                                  'names': json.dumps(payload or {}, ensure_ascii=False, separators=(',', ':'))},
                     timeout=timeout)
    try:
        return r.json()
    except ValueError:
        return None


def route(service_email: str = '') -> dict:
    """{'active': 使用中ID, 'archives': [舊卷ID…（舊到新）], 'fallback': 是否沒查到}。"""
    if _STATE['route'] is None:
        data = None
        try:
            data = _call('main-route', {'serviceEmail': service_email or _STATE['email']}, timeout=90)
        except Exception as e:  # 網路或 Apps Script 暫時不通：照舊用 secret
            print(f'  分卷路由查不到（{type(e).__name__}），照舊使用 SPREADSHEET_ID')
        if not data or not data.get('ok') or not data.get('active'):
            data = {'active': _secret_id(), 'archives': [], 'fallback': True}
        elif data['active'] != _secret_id():
            print(f'  主資料已換卷：使用中為第 {len(data.get("archives") or []) + 1} 卷（舊卷只供讀取）')
        _STATE['route'] = data
    return _STATE['route']


def active_id(default_id: str = '', service_email: str = '') -> str:
    if service_email:
        _STATE['email'] = service_email
    return route(service_email).get('active') or default_id or _secret_id()


def remember(client, service_email: str = ''):
    """open_sheets 開表時記下 gspread client 與服務帳號信箱，換卷後重開新卷要用。"""
    _STATE['client'] = client
    if service_email:
        _STATE['email'] = service_email


def _open(book_id):
    if book_id not in _STATE['books']:
        _STATE['books'][book_id] = _STATE['client'].open_by_key(book_id)
    return _STATE['books'][book_id]


def id_of(fn) -> str:
    """呼叫綁在哪一卷：工作表看所屬試算表，試算表看自己；其他（client.open_by_key）回空字串。"""
    obj = getattr(fn, '__self__', None)
    book = getattr(obj, 'spreadsheet', None)
    if book is not None:
        return str(getattr(book, 'id', '') or '')
    return str(getattr(obj, 'id', '') or '') if hasattr(obj, 'worksheet') else ''


def rebind(fn):
    """換卷之後，綁在舊卷工作表或試算表上的呼叫改綁到新卷的同名分頁。沒換卷時原樣回傳。"""
    if not _STATE['switched'] or _STATE['client'] is None:
        return fn
    target = route().get('active')
    obj = getattr(fn, '__self__', None)
    if obj is None or not target:
        return fn
    import gspread
    if isinstance(obj, gspread.Worksheet):
        if obj.spreadsheet.id == target:
            return fn
        return getattr(_open(target).worksheet(obj.title), fn.__name__)
    if isinstance(obj, gspread.Spreadsheet) and obj.id != target:
        return getattr(_open(target), fn.__name__)
    return fn


def relieve(reason: str, failed_id: str = '') -> bool:
    """寫入被拒時請 Apps Script 處理。回 True 代表可以馬上重試（已清空白欄或已換卷）。"""
    payload = {'reason': str(reason)[:150], 'serviceEmail': _STATE['email'], 'trimmed': _STATE['trimmed']}
    source = failed_id or route().get('active') or _secret_id()
    for _ in range(6):
        try:
            data = _call('main-relieve', payload, source=source)
        except Exception as e:
            print(f'  分卷處理請求失敗（{type(e).__name__}）')
            return False
        if not data:
            print('  分卷處理沒有回應（Apps Script 可能還不是含分卷的版本）')
            return False
        if data.get('busy'):
            time.sleep(30)
            continue
        if not data.get('ok'):
            print('  分卷處理被拒：' + str(data.get('error', ''))[:120])
            return False
        if data.get('step') == 'trim':
            _STATE['trimmed'] = True
            print(f'  主表已清掉 {data.get("cells", 0):,} 格空白欄，重試寫入')
            return True
        if data.get('switched') or data.get('step') == 'already':
            _STATE['route'] = None
            _STATE['switched'] = True
            route()
            print('  主表寫不進去，已整本換到新卷，改寫新卷')
            return True
        if data.get('pending'):
            continue   # 分次複製中，下一次請求接著做
        return False
    return False


def merged_values(ss, title: str, key_cols=None, retry=None):
    """get_all_values 的分卷版：舊卷在前、使用中在後；表頭以使用中為準按欄名對齊；有鍵時同一筆以較新的一卷為準。"""
    retry = retry or (lambda f, *a, **k: f(*a, **k))
    active = retry(rebind(ss.worksheet(title).get_all_values))
    archives = route().get('archives') or []
    if not archives or _STATE['client'] is None or not active:
        return active
    sources = []
    for book_id in archives:
        try:
            sources.append(retry(_open(book_id).worksheet(title).get_all_values))
        except Exception as e:
            print(f'  舊卷讀不到「{title}」（{type(e).__name__}），只用使用中一卷')
    return merge_values(sources + [active], key_cols)


def merge_values(sources, key_cols=None):
    """多卷 get_all_values 合併（舊到新，最後一個是使用中）：表頭以最後一個為準按欄名對齊；整列空白略過；有鍵時同一筆以較新的為準。"""
    head = [str(h).strip() for h in (sources[-1][0] if sources and sources[-1] else [])]
    rows, at = [], {}
    for vals in sources:
        if len(vals) < 2:
            continue
        h = [str(x).strip() for x in vals[0]]
        idx = [h.index(c) if c in h else -1 for c in head]
        kidx = [h.index(c) if c in h else -1 for c in (key_cols or [])]
        for src in vals[1:]:
            if not any(str(x).strip() for x in src):
                continue
            row = [src[j] if 0 <= j < len(src) else '' for j in idx]
            if key_cols:
                key = '|'.join(str(src[j]).strip() if 0 <= j < len(src) else '' for j in kidx)
                if key in at:
                    rows[at[key]] = row
                    continue
                at[key] = len(rows)
            rows.append(row)
    return [head] + rows


def _reset_for_tests():
    _STATE.update({'route': None, 'client': None, 'email': '', 'switched': False, 'trimmed': False, 'books': {}})
