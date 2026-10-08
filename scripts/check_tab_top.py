"""換分頁回到最上面的驗收（v151）。唯讀：公開讀取轉給正式 API，不按任何寫入按鈕。

捲到頁面下面再點別的分頁，新分頁要從最上面開始看，不能停在上一頁捲到的位置。
先前用平滑捲動，面板換內容時高度一變就被中斷，停在半路。

    python scripts/check_tab_top.py                 本機組站
    python scripts/check_tab_top.py --url https://lee200202.github.io/Stock/    正式站
"""
import argparse
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
FAKE_API = 'https://tab-top-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'
ORDER = ['tracker', 'perf', 'tech', 'mail', 'tx', 'subscribe', 'overview', 'tech', 'tracker']


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='tab-top-'))
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


def check(browser, url, local):
    lines = []
    for width, touch in ((1280, False), (390, True)):
        ctx = browser.new_context(viewport={'width': width, 'height': 800}, has_touch=touch)
        pg = ctx.new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if local:
            pg.route(FAKE_API, forward)
        pg.goto(url + '?_=' + str(time.time()), wait_until='domcontentloaded', timeout=90000)
        pg.wait_for_function("document.body.innerText.includes('目前持有')", timeout=90000)
        pg.wait_for_timeout(2500)
        deepest = 0
        for name in ORDER:
            pg.evaluate('window.scrollTo(0, document.documentElement.scrollHeight)')
            pg.wait_for_timeout(500)
            deepest = max(deepest, pg.evaluate('Math.round(scrollY)'))
            pg.evaluate("n => document.querySelector('button.tab[data-tab=\"' + n + '\"]').click()", name)
            for wait in (300, 1200, 1200):          # 立刻、內容載入後都要在最上面
                pg.wait_for_timeout(wait)
                y = pg.evaluate('Math.round(scrollY)')
                assert y == 0, f'{width}px 切到 {name}：應該回到最上面，實際停在 {y} 像素'
            assert pg.locator(f'#p-{name}').is_visible(), f'{width}px 切到 {name}：面板沒有顯示'
        assert deepest > 300, f'{width}px 測試沒有真的捲到下面（最深 {deepest}）'
        # 分頁內自己的跳轉不受影響：技術說明的子目錄仍然捲得到章節
        if width > 800:
            pg.evaluate("document.querySelector('button.tab[data-tab=\"tech\"]').click()")
            pg.wait_for_selector('.subnav-tab', timeout=30000)
            pg.locator('.subnav-tab').hover()
            pg.wait_for_timeout(900)
            pg.locator('.subnav-list a').nth(4).click()
            pg.wait_for_timeout(1500)
            assert pg.evaluate('scrollY') > 300, '子目錄跳到章節後應該捲到那一節'
        assert not errs, errs
        lines.append(f'ok {width}px：{len(ORDER)} 次切換都回到最上面（切換前最深捲到 {deepest} 像素）')
        ctx.close()
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    srv = None if args.url else build_local()
    url = args.url or f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for line in check(browser, url, srv is not None):
                print(line)
        finally:
            browser.close()
    if srv:
        srv.shutdown()
    print('結果：全部符合')


if __name__ == '__main__':
    main()
