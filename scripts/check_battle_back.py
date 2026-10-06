"""戰情追蹤「查看單一股票之後回到符合條件的清單」驗收（v133）。行情全部用假資料，不打正式行情。

    python scripts/check_battle_back.py

查過個股之後，三條路都要回得去：把搜尋清空、空白卡片上的按鈕、分頁列的按鈕。
清單已經讀完時回來不重讀；還在讀的時候離開，回來要把剩下的讀完。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FAKE_API = 'https://battle-back-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'

CALENDAR = []
_d = date(2026, 2, 2)
while _d <= date(2026, 10, 6):
    if _d.weekday() < 5:
        CALENDAR.append(_d.strftime('%Y/%m/%d'))
    _d += timedelta(days=1)
UNIVERSE = [{'code': str(1000 + n), 'name': '示意股票' + str(n)} for n in range(50)]
FAIL = {'1001', '1007', '1013'}                      # 量 800 張：低於均量也低於最低張數，不符合


def item(code):
    bars = [dict(date=d, open=100, high=101, low=99, close=100, volume=1000) for d in CALENDAR[-121:-1]]
    bars.append(dict(date=CALENDAR[-1], open=105, high=109, low=104, close=108, volume=800 if code in FAIL else 4000))
    return dict(code=code, name='示意股票' + str(int(code) - 1000), bars=bars, quote=None)


def battle_reply(codes, options=None, legacy=False):
    """和 BattleData.gs 同樣的兩種回應：原格式（舊版後端，或前台沒帶第二個參數），以及精簡格式（v:2）。"""
    v2 = (not legacy) and isinstance(options, dict) and options.get('v', 0) >= 2
    codes = codes or [u['code'] for u in UNIVERSE[:(20 if v2 else 12)]]
    items = [item(c) for c in codes]
    out = dict(ok=True, date=CALENDAR[-1], today=CALENDAR[-1], afterClose=True, at='本機示意', quoteCadence='模擬資料',
               calendar=CALENDAR, volumeUnit='lots', universe=UNIVERSE, items=items)
    if not v2:
        return out
    out['items'] = [dict(code=i['code'], name=i['name'], error='', q=i['quote'],
                         b=[[int(b['date'].replace('/', '')), b['open'], b['high'], b['low'], b['close'], b['volume']] for b in i['bars']]) for i in items]
    out.update(format=2, batchMax=20, calendarKey=f'{len(CALENDAR)}:{CALENDAR[0]}:{CALENDAR[-1]}',
               universeKey=f"{len(UNIVERSE)}:{UNIVERSE[0]['code']}:{UNIVERSE[-1]['code']}")
    if options.get('lite') is True:
        del out['calendar'], out['universe']
        out['lite'] = True
    return out


# 追蹤清單的分批讀取可以放慢（window.__slowBatches 毫秒），用來測「還在讀就離開」。延遲做在頁面裡，不卡住操作。
SLOW = """(() => { const real = window.fetch;
  window.fetch = async (url, opt) => { const res = await real(url, opt); let wait = 0;
    try { const b = JSON.parse(opt.body); if (b.method === 'apiGetBattleData' && b.args[0] && b.args[0].length > 1) { wait = window.__slowBatches || 0; } } catch (e) {}
    if (wait) { await new Promise(r => setTimeout(r, wait)); }
    return res; }; })()"""
CARDS = "() => [...document.querySelectorAll('#battleCards [data-bstock]')].map(e => e.dataset.bstock).filter((c, i, a) => a.indexOf(c) === i)"
TOTAL = "() => (document.getElementById('battlePage').textContent.match(/共 (\\d+) 筆/) || [])[1]"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='battle-back-'))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
                   env=dict(os.environ, SITE_API_URL=FAKE_API), stdout=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_router(calls, legacy=False):
    cors = {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}

    def handle(route):
        req = route.request
        if req.method == 'OPTIONS':
            route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'})
            return
        try:
            body = json.loads(req.post_data or '{}')
            method, args = body.get('method'), body.get('args') or []
            if method == 'apiGetBattleData':
                calls.append(args[0] if args else None)
                out = {'ok': True, 'result': battle_reply(args[0] if args else None, args[1] if len(args) > 1 else None, legacy)}
            elif method == 'apiSuggestCodes':
                q = str(args[0]).strip()
                out = {'ok': True, 'result': [u for u in UNIVERSE if u['code'].startswith(q) or q in u['name']][:12]}
            elif method in ('apiGetDashboard', 'apiListRecordDates', 'apiGetLineEntry'):
                resp = route.fetch(url=REAL_API, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
                route.fulfill(status=resp.status, body=resp.body(), headers=cors)
                return
            else:
                out = {'ok': True, 'result': None}
            route.fulfill(status=200, headers=cors, body=json.dumps(out, ensure_ascii=False))
        except Exception:
            pass                      # 頁面關閉時還在路上的背景請求
    return handle


def open_battle(pg, url):
    pg.goto(url, wait_until='domcontentloaded')
    pg.wait_for_selector('button.tab[data-tab="battle"]', state='attached', timeout=30000)
    pg.evaluate("document.querySelector('button.tab[data-tab=\"battle\"]').click()")
    pg.wait_for_selector('#p-battle:not([hidden]) #battleCode', timeout=15000)


def wait_list_done(pg, want):
    pg.wait_for_function("(n) => !document.getElementById('battleRefresh').disabled && (document.getElementById('battlePage').textContent.match(/共 (\\d+) 筆/) || [])[1] === String(n)"
                         " && document.getElementById('battleUniverse').hidden", arg=want, timeout=15000)


def pick(pg, code):
    pg.fill('#battleCode', '')
    pg.type('#battleCode', code, delay=20)
    pg.keyboard.press('Enter')


def wait_single(pg, code, passes):
    if passes:
        pg.wait_for_function("(c) => { const a = [...document.querySelectorAll('#battleCards [data-bstock]')].map(e => e.dataset.bstock); return a.length && a.every(x => x === c)"
                             " && !document.querySelector('#battleCards .battle-empty') && !document.getElementById('battleUniverse').hidden; }", arg=code, timeout=8000)
    else:
        pg.wait_for_function("(c) => { const e = document.querySelector('#battleCards .battle-empty'); return e && e.innerText.indexOf(c) >= 0 && e.innerText.indexOf('不符合條件') >= 0; }",
                             arg=code, timeout=8000)


def run(pg, calls, w):
    want = len(UNIVERSE) - len(FAIL)
    wait_list_done(pg, want)
    first_page = pg.evaluate(CARDS)
    assert first_page and not set(first_page) & FAIL, first_page

    # 一、查一檔不符合的：卡片寫明是哪一檔，並有兩顆按鈕。
    pick(pg, '1001')
    wait_single(pg, '1001', False)
    empty = pg.inner_text('#battleCards .battle-empty')
    assert '示意股票1' in empty and '已核對' not in empty, empty
    assert pg.locator('#battleCards [data-bback]').count() == 1 and pg.locator('#battleCards .battle-empty [data-bstock="1001"]').count() == 1
    assert '清空搜尋可回到符合條件的清單' in pg.inner_text('#battleSearchHint')
    assert pg.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0, f'{w}px 出現水平捲軸'

    # 二、把搜尋清空 → 回到符合條件的清單；清單剛讀完，不再打行情。
    seen = len(calls)
    pg.fill('#battleCode', '')
    wait_list_done(pg, want)
    assert pg.evaluate(CARDS) == first_page, '回來的清單和離開前不一樣'
    assert len(calls) == seen, f'回到清單又讀了 {len(calls) - seen} 次行情'
    assert '已核對 50／50 檔' in pg.inner_text('#battleSummary'), pg.inner_text('#battleSummary')

    # 三、翻到第二頁再查個股，回來仍在第二頁。
    pg.click('#battleNext')
    second_page = pg.evaluate(CARDS)
    assert second_page != first_page
    pick(pg, '1007')
    wait_single(pg, '1007', False)
    pg.click('#battleCards [data-bback]')                     # 空白卡片上的按鈕
    wait_list_done(pg, want)
    assert pg.evaluate(CARDS) == second_page, '沒有回到離開前的那一頁'
    assert pg.input_value('#battleCode') == '', '按鈕回清單之後搜尋框應該清空'

    # 四、查一檔符合的 → 只看那一檔；分頁列的按鈕回清單。
    pick(pg, '1003')
    wait_single(pg, '1003', True)
    assert pg.inner_text('#battleUniverse').strip() == '回到符合條件的清單'
    pg.click('#battleUniverse')
    wait_list_done(pg, want)

    # 五、連查兩檔（第二檔時已經不在清單畫面）再清空，一樣回得去。
    pick(pg, '1001')
    wait_single(pg, '1001', False)
    pick(pg, '1013')
    wait_single(pg, '1013', False)
    pg.fill('#battleCode', '')
    wait_list_done(pg, want)

    # 六、候選開著時按 Escape 只收候選，不清字、也不離開這一檔；再按一次清空才回清單。
    pg.fill('#battleCode', '')
    pg.type('#battleCode', '100', delay=20)
    pg.wait_for_function("() => !document.getElementById('battleSuggestions').hidden")
    pg.keyboard.press('Escape')
    assert pg.input_value('#battleCode') == '100'


def run_while_loading(pg, calls):
    """清單還在分批讀的時候查個股、再清空：回來要把剩下的讀完。"""
    want = len(UNIVERSE) - len(FAIL)
    pg.wait_for_function("() => document.querySelectorAll('#battleCards [data-bstock]').length > 0", timeout=15000)
    assert pg.evaluate("document.getElementById('battleRefresh').disabled"), '這一段要在清單還沒讀完時進行'
    pick(pg, '1001')
    wait_single(pg, '1001', False)
    pg.evaluate("window.__slowBatches = 0")
    pg.fill('#battleCode', '')
    wait_list_done(pg, want)
    assert '已核對 50／50 檔' in pg.inner_text('#battleSummary'), pg.inner_text('#battleSummary')


def main():
    srv = build_local()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in (1280, 390):
            for slow, legacy in ((0, False), (1500, False), (0, True)):
                pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=w < 700)
                errs, calls = [], []
                pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
                pg.add_init_script(SLOW)
                pg.add_init_script(f'window.__slowBatches = {slow};')
                pg.route(FAKE_API, make_router(calls, legacy))
                open_battle(pg, url)
                if slow:
                    run_while_loading(pg, calls)
                    print(f'ok {w}px：清單讀到一半查個股，清空搜尋後回到清單並讀完 50 檔')
                else:
                    run(pg, calls, w)
                    kind = '舊格式後端（一次 24 檔）' if legacy else '精簡格式（後端告知一次 20 檔、後續批次省略清單與行事曆）'
                    print(f'ok {w}px［{kind}］：清空搜尋、空白卡片按鈕、分頁列按鈕都回到符合條件的清單；回來不重讀、頁碼保留')
                assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
                try:
                    pg.unroute_all(behavior='ignoreErrors')
                except Exception:
                    pass
                pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
