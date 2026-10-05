"""持股追蹤排序驗收（v124）。不按任何寫入按鈕。

前台：預設依獲利高到低；按另一顆換依據、同一顆再按反向；沒有值的排最後；已出場多一顆「出場日」。
後台：同一組排序；有未儲存的修改時不重排。

    python scripts/check_tracker_sort.py                 本機組站；前台資料經由正式 API 唯讀取得，後台用假資料
    python scripts/check_tracker_sort.py --url https://lee200202.github.io/Stock/    正式站前台
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FAKE_API = 'https://tracker-sort-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'

EXPECT = """([key, dir, view]) => {
  const t = window.__tracker, items = (view === 'held' ? t.held : t.exited) || [];
  const val = i => key === 'ret' ? (i.ret == null ? null : Number(i.ret))
    : key === 'in' ? (String(i.entryDate || i.roundStart || i.firstBuy || '') || null)
    : key === 'out' ? (String(i.lastSell || '') || null)
    : key === 'seen' ? (String(i.latestReasonDate || '') || null)
    : (i.valid === false ? null : (String(i.code || '') || null));
  return items.map((i, n) => ({ c: String(i.code), n, v: val(i) })).sort((a, b) => {
    if (a.v === null || b.v === null) return a.v === b.v ? a.n - b.n : (a.v === null ? 1 : -1);
    const c = typeof a.v === 'number' ? a.v - b.v : String(a.v).localeCompare(String(b.v), 'en', { numeric: true });
    return c ? c * dir : a.n - b.n;
  }).map(x => x.c);
}"""
SHOWN = "() => [...document.querySelectorAll('#trackerTable .tracker-card .tracker-card-code')].map(e => e.innerText.trim())"
PILLS = "(id) => [...document.querySelectorAll('#' + id + ' .pill')].map(b => b.innerText.replace(/\\s+/g, '') + (b.getAttribute('aria-pressed') === 'true' ? '*' : ''))"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='tracker-sort-'))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
                   env=dict(os.environ, SITE_API_URL=FAKE_API), stdout=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def forward(route):
    """本機頁面的 API 請求轉給正式的唯讀 API（公開方法），回應補上本機來源的 CORS。"""
    if route.request.method == 'OPTIONS':
        route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'})
        return
    try:
        resp = route.fetch(url=REAL_API, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
        route.fulfill(status=resp.status, body=resp.body(), headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'})
    except Exception:
        pass                      # 頁面關閉時還在路上的背景請求


def check_front(browser, url, local, shots):
    for w, touch in ((1280, False), (390, True)):
        pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=touch)
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if local:
            pg.route(FAKE_API, forward)
        pg.goto(url + '?tab=tracker', wait_until='domcontentloaded', timeout=90000)
        pg.wait_for_selector('.tracker-card', timeout=90000)
        pg.wait_for_function('window.__tracker && window.__tracker.held')
        expect = lambda key, d, view: pg.evaluate(EXPECT, [key, d, view])
        shown = lambda: pg.evaluate(SHOWN)

        # 預設：獲利高到低，四顆（持有）
        assert pg.evaluate(PILLS, 'trackerSort') == ['獲利↓*', '進場日', '最近提到', '代號'], pg.evaluate(PILLS, 'trackerSort')
        assert shown() == expect('ret', -1, 'held'), '預設應依獲利高到低'
        rets = [x for x in pg.evaluate("window.__tracker.held.map(i => i.ret)") if x is not None]
        assert len(rets) >= 2, '持有至少兩檔才驗得出排序'
        # 再按一次反向；沒有值的仍在最後
        pg.locator('#trackerSort [data-ts="ret"]').click()
        assert pg.evaluate(PILLS, 'trackerSort')[0] == '獲利↑*' and shown() == expect('ret', 1, 'held')
        # 代號：預設小到大，再按大到小
        pg.locator('#trackerSort [data-ts="code"]').click()
        assert pg.evaluate(PILLS, 'trackerSort') == ['獲利', '進場日', '最近提到', '代號↑*'] and shown() == expect('code', 1, 'held')
        codes = shown()
        assert codes == sorted(codes, key=lambda c: (not c.isdigit(), int(c) if c.isdigit() else 0, c)), codes
        pg.locator('#trackerSort [data-ts="code"]').click()
        assert shown() == expect('code', -1, 'held')
        # 進場日、最近提到：新到舊
        pg.locator('#trackerSort [data-ts="in"]').click()
        assert shown() == expect('in', -1, 'held')
        pg.locator('#trackerSort [data-ts="seen"]').click()
        assert shown() == expect('seen', -1, 'held')
        # 外觀：橢圓、一行、不溢出；觸控高度 44 以上
        box = pg.evaluate("""() => { const b = document.querySelector('#trackerSort'), p = b.querySelector('.pill'), r = b.getBoundingClientRect(), pr = p.getBoundingClientRect();
          return { radius: parseFloat(getComputedStyle(b).borderTopLeftRadius), pillRadius: parseFloat(getComputedStyle(p).borderTopLeftRadius), h: r.height, pillH: pr.height,
                   right: r.right, vw: innerWidth, over: document.documentElement.scrollWidth - innerWidth, hidden: b.scrollWidth - b.clientWidth,
                   oneRow: new Set([...b.querySelectorAll('.pill')].map(x => Math.round(x.getBoundingClientRect().top))).size === 1 }; }""")
        assert box['radius'] >= box['h'] / 2 - 1 and box['pillRadius'] >= box['pillH'] / 2 - 1, box
        assert box['oneRow'] and box['over'] <= 1 and box['right'] <= box['vw'] + 1, box
        assert box['hidden'] <= 1, ('排序膠囊要全部看得到，不必左右滑', box)
        if touch:
            assert box['pillH'] >= 44, box
        if shots:
            pg.locator('#trackerToggle').scroll_into_view_if_needed()
            pg.screenshot(path=f'{shots}/tracker-sort-{w}.png')
        # 已出場：多「出場日」、沒有「最近提到」；持有時選的「最近提到」在這裡不存在，回到獲利
        pg.locator('[data-tk="exited"]').click()
        pg.wait_for_timeout(400)
        if pg.locator('.tracker-card').count():
            assert pg.evaluate(PILLS, 'trackerSort') == ['獲利↓*', '進場日', '出場日', '代號'], pg.evaluate(PILLS, 'trackerSort')
            assert shown() == expect('ret', -1, 'exited')
            pg.locator('#trackerSort [data-ts="out"]').click()
            assert shown() == expect('out', -1, 'exited')
            pg.locator('[data-tk="held"]').click()
            pg.wait_for_timeout(400)
            assert pg.evaluate(PILLS, 'trackerSort')[0] == '獲利↓*' and shown() == expect('ret', -1, 'held')
        # 排序後點卡片仍然開得了個股
        pg.locator('#trackerSort [data-ts="code"]').click()
        first = pg.locator('.tracker-card[data-code]').first
        code = first.get_attribute('data-code')
        first.locator('.tracker-card-note, .tracker-card-axis').first.click()
        pg.wait_for_selector('#detail.open', timeout=15000)
        assert pg.locator('#dCode').inner_text().strip() == code
        assert not errs, errs
        print(f'ok front {w}px: default by return, reverse, code, dates, exited view, oval pills, no overflow')
        pg.unroute_all(behavior='ignoreErrors')
        pg.close()


HELD = [{'code': '2330', 'name': '台積電', 'valid': True, 'roundStart': '2026/06/29', 'entry': 2330, 'entrySrc': '取當日最低', 'current': 2500, 'ret': 7.3, 'override': None},
        {'code': '8210', 'name': '勤誠', 'valid': True, 'roundStart': '2026/10/05', 'entry': None, 'entrySrc': '待補', 'current': 885, 'ret': None, 'override': None},
        {'code': '5536', 'name': '聖暉', 'valid': True, 'roundStart': '2026/09/22', 'entry': 869, 'entrySrc': '取當日最低', 'current': 956, 'ret': 10.01, 'override': None},
        {'code': '2402', 'name': '毅嘉', 'valid': True, 'roundStart': '2026/08/12', 'entry': 55.9, 'entrySrc': '取當日最低', 'current': 54, 'ret': -3.4, 'override': None}]
EXITED = [{'code': '3037', 'name': '欣興', 'valid': True, 'roundStart': '2026/08/06', 'entry': 180, 'entrySrc': '取當日最低', 'current': 205, 'ret': 13.89, 'override': None, 'closed': True, 'lastSell': '2026/08/25', 'exitReason': '明講賣出'},
          {'code': '2409', 'name': '友達', 'valid': True, 'roundStart': '2026/07/15', 'entry': 42.5, 'entrySrc': '取當日最低', 'current': 39.8, 'ret': -6.35, 'override': None, 'closed': True, 'lastSell': '2026/09/10', 'exitReason': '明講賣出'}]


def check_admin(browser, base):
    calls = []

    def mock(route):
        m = route.request.post_data_json.get('method', '')
        calls.append(m)
        res = {'apiAdminLogin': {'ok': True, 'data': {'today': '2026/10/05', 'closedToday': False, 'hasData': True, 'todayTrades': 0, 'postedDates': [], 'holidays': []}},
               'apiAdminHeldList': {'ok': True, 'items': HELD, 'exited': EXITED}}.get(m, {'ok': False, 'reason': '測試環境'})
        route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True, 'result': res}))

    names = "() => [...document.querySelectorAll('#heldList .hrow-wrap .hrow-view .hn small')].map(e => e.textContent.trim())"
    for w in (1280, 390):
        pg = browser.new_page(viewport={'width': w, 'height': 900})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.route(FAKE_API, mock)
        pg.goto(base + 'admin.html', wait_until='domcontentloaded')
        pg.locator('#key').fill('local-mock-key')
        pg.locator('#loginBtn').click()
        pg.wait_for_selector('#app:not([hidden])', timeout=15000)
        pg.locator('button.tab[data-tab="held"]').click()
        pg.wait_for_selector('#heldList .hrow-wrap')
        order = lambda: pg.evaluate(names)
        assert pg.evaluate(PILLS, 'heldSort') == ['獲利↓*', '進場日', '代號'], pg.evaluate(PILLS, 'heldSort')
        assert order() == ['5536', '2330', '2402', '8210'], order()          # 高到低，沒有報酬的排最後
        pg.locator('#heldSort [data-hs="ret"]').click()
        assert order() == ['2402', '2330', '5536', '8210'], order()
        pg.locator('#heldSort [data-hs="code"]').click()
        assert order() == ['2330', '2402', '5536', '8210'], order()
        pg.locator('#heldSort [data-hs="in"]').click()
        assert order() == ['8210', '5536', '2402', '2330'], order()
        # 改到一半不重排，也不會把別張卡的內容換到這一張
        first = pg.locator('#heldList .hrow-wrap').first
        first.locator('.h-edit-btn').click()
        first.locator('.h-cost').fill('860')
        pg.locator('#heldSort [data-hs="code"]').click()
        assert order() == ['8210', '5536', '2402', '2330'] and '未儲存' in pg.locator('#heldMsg').inner_text()
        first.locator('.h-cost').fill('')                     # 改回原值就不算未儲存
        first.locator('.h-cancel-btn').click()
        pg.locator('#heldSort [data-hs="code"]').click()
        assert order() == ['2330', '2402', '5536', '8210'], order()
        # 已出場：多「出場日」
        pg.locator('[data-hk="exited"]').click()
        assert pg.evaluate(PILLS, 'heldSort') == ['獲利', '進場日', '出場日', '代號↑*'], pg.evaluate(PILLS, 'heldSort')
        pg.locator('#heldSort [data-hs="out"]').click()
        assert order() == ['2409', '3037'], order()
        assert pg.evaluate('document.documentElement.scrollWidth - innerWidth') <= 1
        assert 'apiAdminSetHoldingCost' not in calls and not errs, (calls, errs)
        print(f'ok admin {w}px: same sort pills, blocked while a card is dirty, exited adds exit date')
        pg.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    ap.add_argument('--shots', default='')
    args = ap.parse_args()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        if args.url:
            check_front(browser, args.url, False, args.shots)
        else:
            srv = build_local()
            base = f'http://127.0.0.1:{srv.server_port}/'
            check_front(browser, base, True, args.shots)
            check_admin(browser, base)
            srv.shutdown()
        try:
            browser.close()
        except Exception:
            pass                  # 關閉時還在路上的背景請求不算失敗


if __name__ == '__main__':
    main()
