"""十字線水平線貼齊當天數值的驗收（v148）。唯讀：公開讀取轉給正式 API，不按任何寫入按鈕。

游標移到哪一天，縱軸標籤就該寫那一天的數值，而不是滑鼠高度指到的數字。
驗法：同一個橫向位置、滑鼠放在圖的上緣與下緣，畫出來的圖必須一模一樣（水平線不跟著滑鼠高度走），
而且貼上去的數值等於表格（績效走勢）或上方讀數列（個股 K 線）那一天的數字。

    python scripts/check_crosshair_snap.py                 本機組站
    python scripts/check_crosshair_snap.py --url https://lee200202.github.io/Stock/    正式站
"""
import argparse
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
FAKE_API = 'https://crosshair-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='crosshair-'))
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


def hover_pair(pg, sel, fx):
    """同一個橫向位置，滑鼠放在圖的上緣與下緣各截一張；回傳（上緣圖、下緣圖、貼上的讀數）。"""
    box = pg.locator(sel).bounding_box()
    x = box['x'] + box['width'] * fx
    shots, cross = [], []
    for fy in (0.12, 0.80):
        got = None
        for nudge in range(6):          # 圖剛換資料時十字線會被清掉一次：挪一個像素再讀
            pg.mouse.move(x + (nudge % 2) * 0.5, box['y'] + box['height'] * fy)
            pg.wait_for_timeout(350)
            got = pg.locator(sel).get_attribute('data-cross')
            if got:
                break
        assert got, f'{sel} 沒有貼上當天的讀數'
        cross.append(got)
        shots.append(pg.locator(sel).screenshot())
    assert cross[0] == cross[1], (sel, cross)
    return shots[0], shots[1], cross[0]


def check(browser, url, local, shots_dir):
    lines = []
    pg = browser.new_page(viewport={'width': 1280, 'height': 900})
    errs = []
    pg.on('pageerror', lambda e: errs.append(str(e)))
    if local:
        pg.route(FAKE_API, forward)

    # 一、績效走勢：三個指標都要貼到當天的值
    pg.goto(url + '?tab=perf', wait_until='domcontentloaded', timeout=90000)
    pg.wait_for_selector('#perfTable table tbody tr', timeout=90000)
    pg.wait_for_selector('#perfChart canvas', timeout=30000)
    pg.locator('#perfChart').scroll_into_view_if_needed()
    table = pg.evaluate("""[...document.querySelectorAll('#perfTable tbody tr')].map(r => [...r.children].map(c => c.textContent.trim()))""")
    by_date = {r[0]: r for r in table}
    for label, col in (('平均報酬', 2), ('正報酬比例', 3), ('持有檔數', 1)):
        pg.get_by_role('button', name=label, exact=True).first.click()
        pg.wait_for_timeout(400)
        seen = set()
        for fx in (0.15, 0.45, 0.80):
            top, bottom, cross = hover_pair(pg, '#perfChart', fx)
            assert top == bottom, f'績效走勢（{label}）：同一天、滑鼠高度不同，水平線位置不該改變'
            day, value = cross.split('|')
            assert day in by_date, (day, list(by_date)[:3])
            shown = float(by_date[day][col].replace('%', '').replace('−', '-').replace('+', ''))
            assert abs(float(value) - shown) < 0.051, (label, day, value, by_date[day])
            seen.add(day)
        assert len(seen) == 3, seen
        lines.append(f'ok 績效走勢・{label}：{len(seen)} 個日期，水平線貼在當天數值、不隨滑鼠高度移動（例如 {day} → {value}）')
        if shots_dir and col == 2:
            pg.locator('#perfChart').screenshot(path=str(Path(shots_dir) / 'perf-snap.png'))
    pg.mouse.move(5, 5)
    pg.wait_for_function("!document.querySelector('#perfChart').getAttribute('data-cross')", timeout=10000)

    # 二、個股 K 線：貼收盤價（不是最近的那一條均線），成交量等副圖同一根
    pg.goto(url + '?tab=tracker', wait_until='domcontentloaded', timeout=90000)
    pg.wait_for_selector('.tracker-card', timeout=90000)
    pg.locator('.tracker-card').first.click()
    pg.wait_for_selector('#chart canvas', timeout=60000)
    pg.locator('#chart').scroll_into_view_if_needed()
    pg.wait_for_timeout(2500)
    days = set()
    for fx in (0.25, 0.55, 0.85):
        top, bottom, cross = hover_pair(pg, '#chart', fx)
        assert top == bottom, '個股 K 線：同一根、滑鼠高度不同，水平線位置不該改變'
        day, value = cross.split('|')
        assert abs(float(value) - float(pg.locator('#rClose').inner_text())) < 0.006, (cross, pg.locator('#rClose').inner_text())
        assert pg.locator('#rDate').inner_text().strip() == day, (pg.locator('#rDate').inner_text(), day)
        days.add(day)
    assert len(days) == 3, days
    lines.append(f'ok 個股 K 線：{len(days)} 根，水平線貼在當天收盤、不隨滑鼠高度移動（例如 {day} 收 {value}）')
    if shots_dir:
        pg.locator('#chart').screenshot(path=str(Path(shots_dir) / 'kline-snap.png'))
    # 滑在成交量上：成交量自己也貼齊（上下兩個高度畫面相同）
    box = pg.locator('#volChart').bounding_box()
    vol = []
    for fy in (0.15, 0.85):
        pg.mouse.move(box['x'] + box['width'] * 0.5, box['y'] + box['height'] * fy)
        pg.wait_for_timeout(300)
        vol.append(pg.locator('#volChart').screenshot())
    assert vol[0] == vol[1], '成交量：同一根、滑鼠高度不同，水平線位置不該改變'
    lines.append('ok 成交量：水平線貼在當天成交量')
    assert not errs, errs
    pg.close()
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url')
    ap.add_argument('--shots')
    args = ap.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    srv = None if args.url else build_local()
    url = args.url or f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for line in check(browser, url, srv is not None, args.shots):
                print(line)
        finally:
            browser.close()
    if srv:
        srv.shutdown()
    print('結果：全部符合')


if __name__ == '__main__':
    main()
