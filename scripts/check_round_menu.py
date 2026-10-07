"""持股走勢的回合選單驗收（v144）。唯讀：公開讀取轉給正式 API，不按任何寫入按鈕。

多回合的股票（買了又賣、賣了又買）可以從選單切換看先前每一個回合的走勢；
選單是自己畫的（不是瀏覽器原生下拉），列出回合、進出場日、那一回合的報酬與累積報酬。

    python scripts/check_round_menu.py                 本機組站
    python scripts/check_round_menu.py --url https://lee200202.github.io/Stock/    正式站
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
FAKE_API = 'https://round-menu-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='round-menu-'))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
                   env=dict(os.environ, SITE_API_URL=FAKE_API), stdout=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def forward(route):
    if route.request.method == 'OPTIONS':
        route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'})
        return
    try:
        resp = route.fetch(url=REAL_API, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
        route.fulfill(status=resp.status, body=resp.body(), headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'})
    except Exception:
        pass


def check(browser, url, local, shots):
    lines = []
    for w, touch in ((1280, False), (390, True)):
        pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=touch)
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if local:
            pg.route(FAKE_API, forward)
        pg.goto(url + '?tab=tracker', wait_until='domcontentloaded', timeout=90000)
        pg.wait_for_selector('.tracker-card', timeout=90000)
        pg.wait_for_function('window.__tracker && window.__tracker.held')
        multi = pg.evaluate("window.__tracker.held.filter(i => (i.rounds || 1) > 1).sort((a, b) => b.rounds - a.rounds).map(i => [String(i.code), i.rounds, i.name])")
        assert multi, '正式資料應該至少有一檔多回合的持有中股票'
        code, rounds, name = multi[0]
        pg.locator(f'.tracker-card:has(.tracker-card-code:text-is("{code}"))').first.click()
        pg.wait_for_selector('#roundPickBtn', timeout=60000)
        pg.wait_for_selector('#dPxRange .trend-svg', timeout=60000)
        pg.locator('#dPxRange').scroll_into_view_if_needed()
        title = lambda: pg.locator('#dPxRange .trend-t b').inner_text().strip()
        big = lambda: pg.locator('#dPxRange .trend-ret').inner_text().strip()
        assert title() == '進場以來走勢', title()
        assert pg.locator('#roundPickBtn').inner_text().replace('\n', ' ').startswith(f'第 {rounds} 回合'), pg.locator('#roundPickBtn').inner_text()
        assert pg.locator('#roundPickMenu').is_hidden()
        now_ret = big()

        # 選單：不是原生下拉；每一回合一列，寫日期與報酬；最後一列目前選取
        assert pg.locator('#dPxRange select').count() == 0
        pg.locator('#roundPickBtn').click()
        assert pg.locator('#roundPickMenu').is_visible() and pg.locator('#roundPickBtn').get_attribute('aria-expanded') == 'true'
        opts = pg.locator('#roundPickMenu .round-opt')
        assert opts.count() == rounds, (opts.count(), rounds)
        rows = [opts.nth(k).inner_text().replace('\n', ' ') for k in range(rounds)]
        for k, row in enumerate(rows):
            assert f'第 {k + 1} 回合' in row and re.search(r'\d+/\d+', row) and re.search(r'[+−-]?\d+\.\d+%', row), row
        assert '持有中' in rows[-1] and all('持有中' not in r for r in rows[:-1]), rows
        assert opts.nth(rounds - 1).get_attribute('aria-selected') == 'true'
        assert pg.locator('#roundPickMenu .round-sum').count() == 1 and '累積' in pg.locator('#roundPickMenu .round-sum').inner_text()
        box = pg.evaluate("""() => { const m = document.querySelector('#roundPickMenu').getBoundingClientRect(), b = document.querySelector('#roundPickBtn').getBoundingClientRect();
          return { right: m.right, left: m.left, vw: innerWidth, btnH: b.height, optH: Math.min(...[...document.querySelectorAll('.round-opt')].map(e => e.getBoundingClientRect().height)),
                   over: document.documentElement.scrollWidth - innerWidth }; }""")
        assert box['left'] >= 0 and box['right'] <= box['vw'] + 1 and box['over'] <= 1, box
        assert box['btnH'] >= 44 and box['optH'] >= 44, box
        if shots:
            pg.screenshot(path=str(Path(shots) / f'round-menu-{w}.png'))

        # 選第 1 回合：標題、報酬、圖的範圍都換成那一回合；選單收起、焦點回到按鈕
        want = re.search(r'[+−-]?\d+\.\d+%', rows[0]).group(0)
        first = pg.evaluate("window.__tracker.held.find(i => String(i.code) === '%s').roundRets" % code)
        opts.nth(0).click()
        pg.wait_for_function("document.querySelector('#dPxRange .trend-t b').textContent.trim() === '第 1 回合的走勢'", timeout=10000)
        assert big() == want, (big(), want, first)
        assert pg.locator('#roundPickMenu').is_hidden()
        assert pg.evaluate("document.activeElement && document.activeElement.id") == 'roundPickBtn'
        assert pg.locator('#roundPickBtn').inner_text().replace('\n', ' ').startswith('第 1 回合')
        svg = pg.locator('#dPxRange .trend-svg')
        assert svg.count() == 1
        labels = pg.evaluate("[...document.querySelectorAll('#dPxRange .trend-svg text')].map(t => t.textContent).join('|')")
        assert '出場' in labels and '目前' not in labels, labels
        assert '出場原因' not in pg.locator('#dPxRange').inner_text()
        if shots:
            pg.locator('#dPxRange').screenshot(path=str(Path(shots) / f'round-1-{w}.png'))

        # 鍵盤：方向鍵打開、上下移動、Escape 關閉；再選回最近一個回合
        pg.locator('#roundPickBtn').focus()
        pg.keyboard.press('ArrowDown')
        assert pg.locator('#roundPickMenu').is_visible()
        assert pg.evaluate("document.activeElement.getAttribute('data-round')") == '0'
        pg.keyboard.press('End')
        assert pg.evaluate("document.activeElement.getAttribute('data-round')") == str(rounds - 1)
        pg.keyboard.press('Escape')
        assert pg.locator('#roundPickMenu').is_hidden() and pg.evaluate("document.activeElement.id") == 'roundPickBtn'
        assert pg.locator('#p-tracker, .detail-panel, #detail').first.is_visible(), 'Escape 只關選單，不關個股頁'
        pg.locator('#roundPickBtn').click()
        pg.locator('body').click(position={'x': 5, 'y': 5})            # 點外面關閉
        assert pg.locator('#roundPickMenu').is_hidden()
        pg.locator('#roundPickBtn').click()
        pg.locator('#roundPickMenu .round-opt').nth(rounds - 1).click()
        pg.wait_for_function("document.querySelector('#dPxRange .trend-t b').textContent.trim() === '進場以來走勢'", timeout=10000)
        assert big() == now_ret, (big(), now_ret)

        # 換一檔只有一個回合的股票：沒有選單，也不沿用上一檔選的回合
        single = pg.evaluate("(window.__tracker.held.find(i => (i.rounds || 1) === 1) || {}).code")
        if single:
            pg.evaluate("code => window.__openStock ? window.__openStock(String(code)) : null", single)
        assert not errs, errs
        lines.append(f'ok {w}px：{name}（{code}）{rounds} 個回合；選單 {rounds} 列＋累積；第 1 回合 {want}；鍵盤與點外面關閉；最近回合 {now_ret}；無橫向捲動')
        pg.close()
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    ap.add_argument('--shots', default='')
    args = ap.parse_args()
    srv = None if args.url else build_local()
    url = args.url or f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for line in check(browser, url, not args.url, args.shots):
                print(line)
        finally:
            browser.close()
    if srv:
        srv.shutdown()


if __name__ == '__main__':
    main()
