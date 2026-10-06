"""個股查詢搜尋框驗收（v132）。唯讀：候選與紀錄用假回應，其餘公開讀取轉給正式 API。

版面：全寬橢圓輸入框（64px 以上）、候選在輸入框正下方、查看按鈕獨立一行、不出現水平捲軸。
行為：中文股名與代號候選、完整名稱排最前、上下鍵與 Enter、點選、Escape、中文組字不誤送、
      較慢的舊回應不蓋掉新候選、選代號後行情先開（不等紀錄）、改打字後不留「查詢中」。

    python scripts/check_stock_search.py                     本機組站
    python scripts/check_stock_search.py --url https://lee200202.github.io/Stock/   正式站（只看版面與真實候選）
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FAKE_API = 'https://stock-search-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'
WIDTHS = (1440, 1024, 768, 390, 360)
CATALOG = [('2303', '聯電'), ('2454', '聯發科'), ('2317', '鴻海'), ('2354', '鴻準'), ('6770', '力積電'),
           ('2330', '台積電'), ('3443', '創意'), ('2882', '國泰金'), ('1101', '台泥'), ('2337', '旺宏')]
SEARCH_DELAY = 1.4                  # 紀錄查詢故意慢，行情要先開
# 延遲做在頁面裡（包住 fetch）：在 Playwright 的攔截函式裡 sleep 會把整個瀏覽器操作一起卡住，量不出先後。
#   「聯」的候選 0.9 秒才回（測舊回應）；紀錄查詢 SEARCH_DELAY 秒才回（測行情先開）。
DELAYS = """(() => { const real = window.fetch; window.__done = [];
  window.fetch = async (url, opt) => { const res = await real(url, opt); let wait = 0, tag = '';
    try { const b = JSON.parse(opt.body); if (b.method === 'apiSearchStock') { wait = %d; tag = 'search:' + b.args[0]; }
          if (b.method === 'apiSuggestCodes' && String(b.args[0]).trim() === '聯') { wait = 900; } } catch (e) {}
    if (wait) { await new Promise(r => setTimeout(r, wait)); }
    if (tag) { window.__done.push(tag); }
    return res; }; })()""" % int(SEARCH_DELAY * 1000)

OPTIONS = "() => [...document.querySelectorAll('#suggestList [role=option]')].map(e => e.innerText.replace(/\\s+/g, ' ').trim())"
LIST_OPEN = "() => !document.getElementById('suggestList').hidden"
LAYOUT = """() => {
  const input = document.getElementById('stockInput'), btn = document.getElementById('stockBtn'), hint = document.getElementById('stockSearchHint');
  const box = input.closest('.stock-search').getBoundingClientRect(), i = input.getBoundingClientRect(), b = btn.getBoundingClientRect(), h = hint.getBoundingClientRect();
  const cs = getComputedStyle(input);
  return {h: i.height, w: i.width, boxW: box.width, radius: parseFloat(cs.borderTopLeftRadius), btnTop: b.top, inputBottom: i.bottom,
          hintTop: h.top, overflow: document.documentElement.scrollWidth - window.innerWidth, vw: window.innerWidth, font: parseFloat(cs.fontSize)};
}"""


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='stock-search-'))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
                   env=dict(os.environ, SITE_API_URL=FAKE_API), stdout=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def make_router(calls):
    cors = {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'}

    def handle(route):
        req = route.request
        if req.method == 'OPTIONS':
            route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'})
            return
        try:
            body = json.loads(req.post_data or '{}')
            method, args = body.get('method'), body.get('args') or []
            if method == 'apiSuggestCodes':
                q = str(args[0]).strip()
                calls.append(('suggest', q, time.monotonic()))
                # 故意倒著回，排序要由前台自己做。
                hits = [{'code': c, 'name': n} for c, n in reversed(CATALOG) if q in n or c.startswith(q)]
                route.fulfill(status=200, headers=cors, body=json.dumps({'ok': True, 'result': hits}, ensure_ascii=False))
            elif method == 'apiSearchStock':
                calls.append(('search', str(args[0]), time.monotonic()))
                route.fulfill(status=200, headers=cors, body=json.dumps({'ok': True, 'result': {'found': False, 'resolvedCode': ''}}))
            elif method in ('apiLogUsage', 'apiSubscribe', 'apiUpdateSubscription', 'apiStopAllMail', 'apiAsk'):
                route.fulfill(status=200, headers=cors, body=json.dumps({'ok': True, 'result': None}))
            else:
                resp = route.fetch(url=REAL_API, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
                route.fulfill(status=resp.status, body=resp.body(), headers=cors)
        except Exception:
            pass                      # 頁面關閉時還在路上的背景請求
    return handle


def open_stock_tab(pg, url):
    pg.goto(url, wait_until='domcontentloaded')
    pg.wait_for_selector('#stockTabBtn', state='attached', timeout=30000)
    pg.evaluate("document.getElementById('stockTabBtn').click()")
    pg.wait_for_selector('#p-stock:not([hidden]) #stockInput', timeout=15000)


def check_layout(pg, w):
    m = pg.evaluate(LAYOUT)
    assert m['h'] >= 64, f'{w}px：輸入框高度 {m["h"]}，應至少 64'
    assert m['radius'] >= m['h'] / 2, f'{w}px：圓角 {m["radius"]} 不是橢圓'
    assert abs(m['w'] - m['boxW']) <= 1, f'{w}px：輸入框寬 {m["w"]} 沒有佔滿 {m["boxW"]}'
    assert m['hintTop'] >= m['inputBottom'] - 1 and m['btnTop'] >= m['hintTop'], f'{w}px：提示與按鈕應在輸入框下方各自一行'
    assert m['overflow'] <= 0, f'{w}px：出現水平捲軸（多 {m["overflow"]}px）'
    assert m['font'] >= 16, f'{w}px：輸入字級 {m["font"]}，手機會被自動放大'
    return m


def type_fresh(pg, text):
    pg.fill('#stockInput', '')
    pg.type('#stockInput', text, delay=30)


def close_detail(pg):
    if pg.evaluate("document.getElementById('detail').classList.contains('open')"):
        pg.evaluate("document.getElementById('detailClose').click()")
    pg.wait_for_function("!document.getElementById('detail').classList.contains('open')")


def wait_detail(pg, code, timeout=1200):
    pg.wait_for_function("(c) => document.getElementById('detail').classList.contains('open') && document.getElementById('dCode').textContent.trim() === c",
                         arg=code, timeout=timeout)


def check_behaviour(pg, calls):
    # 一、中文候選；完整名稱在最前，其餘依代號。
    type_fresh(pg, '聯發')
    pg.wait_for_function(LIST_OPEN)
    assert pg.evaluate(OPTIONS) == ['聯發科 2454'], pg.evaluate(OPTIONS)
    type_fresh(pg, '鴻')
    pg.wait_for_function("() => document.querySelectorAll('#suggestList [role=option]').length === 2")
    assert pg.evaluate(OPTIONS) == ['鴻海 2317', '鴻準 2354'], pg.evaluate(OPTIONS)
    assert pg.get_attribute('#stockInput', 'aria-expanded') == 'true'
    assert '找到 2 個' in pg.inner_text('#stockSearchHint')
    # 代號開頭也有候選。
    type_fresh(pg, '23')
    pg.wait_for_function("() => document.querySelectorAll('#suggestList [role=option]').length >= 3")
    assert [o.split()[-1] for o in pg.evaluate(OPTIONS)] == ['2303', '2317', '2330', '2337', '2354'], pg.evaluate(OPTIONS)

    # 二、較慢的舊回應不蓋掉新候選：「聯」0.9 秒才回，期間已改成「聯發」。
    pg.fill('#stockInput', '')
    pg.type('#stockInput', '聯')
    deadline = time.monotonic() + 2
    while not any(c[0] == 'suggest' and c[1] == '聯' for c in calls) and time.monotonic() < deadline:
        pg.wait_for_timeout(40)
    assert any(c[0] == 'suggest' and c[1] == '聯' for c in calls), '「聯」的查詢沒有送出'
    pg.type('#stockInput', '發')
    pg.wait_for_function(LIST_OPEN)
    assert pg.evaluate(OPTIONS) == ['聯發科 2454']
    pg.wait_for_timeout(1200)
    assert pg.evaluate(OPTIONS) == ['聯發科 2454'], '舊的「聯」回應蓋掉了新候選：' + str(pg.evaluate(OPTIONS))

    # 三、上下鍵與 Enter；選代號後行情先開，不等紀錄。
    pg.keyboard.press('ArrowDown')
    assert pg.get_attribute('#suggestList [role=option]', 'aria-selected') == 'true'
    assert pg.get_attribute('#stockInput', 'aria-activedescendant') == 'stockSuggestion0'
    before = time.monotonic()
    pg.keyboard.press('Enter')
    wait_detail(pg, '2454', timeout=900)
    opened = time.monotonic()
    assert 'search:2454' not in pg.evaluate('window.__done'), '行情應該在紀錄回來之前就打開'
    assert opened - before < SEARCH_DELAY, f'行情等了 {opened - before:.2f} 秒才開'
    assert not pg.evaluate(LIST_OPEN)
    close_detail(pg)

    # 四、紀錄還在路上時改打字：「查詢中」收掉，舊回應回來也不寫進畫面。
    assert '查詢中' in pg.inner_text('#stockRecord')
    pg.focus('#stockInput')
    pg.type('#stockInput', '科')
    assert '查詢中' not in pg.inner_text('#stockRecord'), '改打字後還留著「查詢中」'
    pg.wait_for_timeout(int(SEARCH_DELAY * 1000) + 300)
    assert pg.inner_text('#stockRecord').strip() == '', '舊的紀錄回應寫進了畫面：' + pg.inner_text('#stockRecord')[:40]

    # 五、中文組字期間不送查詢，組字結束才送。
    pg.fill('#stockInput', '')
    seen = len(calls)
    pg.evaluate("""() => { const i = document.getElementById('stockInput'); i.focus();
      i.dispatchEvent(new CompositionEvent('compositionstart', {bubbles: true}));
      i.value = '力積'; i.dispatchEvent(new InputEvent('input', {bubbles: true, isComposing: true})); }""")
    pg.wait_for_timeout(450)
    assert not [c for c in calls[seen:] if c[0] == 'suggest'], '組字期間送出了查詢'
    pg.keyboard.press('Enter')                                    # 組字中的 Enter 是選字，不能送出
    pg.evaluate("() => document.getElementById('stockInput').dispatchEvent(new CompositionEvent('compositionend', {bubbles: true, data: '力積'}))")
    pg.wait_for_function(LIST_OPEN)
    assert pg.evaluate(OPTIONS) == ['力積電 6770']
    assert not pg.evaluate("document.getElementById('detail').classList.contains('open')")

    # 六、Escape 收起候選；名稱打完整後按「查看」直接開那一檔。
    pg.keyboard.press('Escape')
    assert not pg.evaluate(LIST_OPEN)
    type_fresh(pg, '力積電')
    pg.wait_for_function(LIST_OPEN)
    pg.keyboard.press('Escape')
    # 搜尋框按 Escape 瀏覽器預設會清空；候選開著時只收候選，打的字要留著。
    assert pg.input_value('#stockInput') == '力積電', 'Escape 把打的字清掉了'
    pg.click('#stockBtn')
    wait_detail(pg, '6770')
    close_detail(pg)

    # 七、點選候選。
    type_fresh(pg, '鴻')
    pg.wait_for_function("() => document.querySelectorAll('#suggestList [role=option]').length === 2")
    pg.click('#suggestList [role=option] >> nth=1')
    wait_detail(pg, '2354')
    assert '鴻準' in pg.inner_text('#stockSearchHint')
    close_detail(pg)

    # 八、沒有候選時講清楚；直接打代號按 Enter 仍可開行情。
    type_fresh(pg, '不存在的公司')
    pg.wait_for_function("() => document.getElementById('stockSearchHint').textContent.indexOf('沒有相關候選') >= 0")
    assert not pg.evaluate(LIST_OPEN)
    type_fresh(pg, '1101')
    pg.keyboard.press('Enter')
    wait_detail(pg, '1101')
    close_detail(pg)

    # 九、同一個關鍵字第二次不再送查詢（候選快取）。
    seen = len([c for c in calls if c[0] == 'suggest' and c[1] == '鴻'])
    type_fresh(pg, '鴻')
    pg.wait_for_function(LIST_OPEN)
    pg.wait_for_timeout(350)
    assert len([c for c in calls if c[0] == 'suggest' and c[1] == '鴻']) == seen, '同一個關鍵字又送了一次查詢'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    ap.add_argument('--shots', default='')
    args = ap.parse_args()
    srv = None if args.url else build_local()
    url = args.url or f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in WIDTHS:
            pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=w < 700)
            errs, calls = [], []
            pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
            if not args.url:
                pg.add_init_script(DELAYS)
                pg.route(FAKE_API, make_router(calls))
            open_stock_tab(pg, url)
            m = check_layout(pg, w)
            if args.url:
                # 正式站：只打真實候選，不點進任何會寫入的地方。
                type_fresh(pg, '台積')
                pg.wait_for_function(LIST_OPEN, timeout=20000)
                first = pg.evaluate(OPTIONS)[0]
                assert '台積電' in first and '2330' in first, first
                done = '真實候選第一筆 ' + first
            else:
                check_behaviour(pg, calls)
                done = '候選、鍵盤、點選、組字、舊回應、行情先開、快取'
            if args.shots:
                Path(args.shots).mkdir(parents=True, exist_ok=True)
                type_fresh(pg, '鴻' if not args.url else '台積')
                pg.wait_for_function(LIST_OPEN, timeout=20000)
                pg.screenshot(path=str(Path(args.shots) / f'stock-search-{w}.png'))
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px：輸入框 {m["w"]:.0f}×{m["h"]:.0f}、橢圓、按鈕獨立一行、無水平捲軸；{done}')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    if srv:
        srv.shutdown()


if __name__ == '__main__':
    main()
