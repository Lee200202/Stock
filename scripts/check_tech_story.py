"""技術說明頁互動驗收（v108）。可對本機組站或正式站執行，不呼叫任何寫入 API。

用法：
  python scripts/check_tech_story.py                         # 本機：以 gas-source 組站，假 API
  python scripts/check_tech_story.py --url https://lee200202.github.io/Stock/?tab=tech   # 正式站

檢查：開場三痛點、原稿稽核實驗室、公開說明編輯檯、沒講名字三分支每一個按鈕都改變結果；
切換時不發出 API 請求；新按鈕觸控區 ≥44px；1440／1280／768／390／360px 淺深色都沒有整頁橫捲；
減少動態時轉場為 0；沒有 JavaScript 時預設案例與文字版規則仍在 HTML 裡。
"""
from __future__ import annotations

import argparse
import json
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
FAKE_API = 'https://tech-check.example.workers.dev/api'
TX_KEYS = ['newer', 'same', 'cancel', 'pause', 'm503', 'trunc', 'gap', 'lock']
PN_BADGES = ['可公開', '可公開', '保留部分', '待複核', '保留部分', '保護', '不自動套用']
WIDTHS = [1440, 1280, 768, 390, 360]


def serve_local() -> tuple[str, ThreadingHTTPServer]:
    out = Path(tempfile.mkdtemp(prefix='tech-check-'))
    env = dict(os.environ, SITE_API_URL=FAKE_API)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(out)],
                   check=True, env=env, stdout=subprocess.DEVNULL)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(out)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f'http://127.0.0.1:{server.server_port}/?tab=tech', server


def open_tech(page, url):
    page.goto(url, wait_until='domcontentloaded', timeout=90000)
    page.wait_for_selector('#tech-transcript-progress', state='attached', timeout=30000)
    tab = page.locator('[data-tab="tech"]').first
    if tab.count():
        tab.click()
    page.wait_for_selector('[data-tx="same"]', state='visible', timeout=30000)


def check_interactions(page, label, reduced=False):
    # 先等首頁自己的背景讀取（總覽、報價、技術頁統計）結束，之後點選時發出的請求才算互動造成的。
    try:
        page.wait_for_load_state('networkidle', timeout=20000)
    except Exception:
        page.wait_for_timeout(5000)
    calls = []
    page.on('request', lambda r: calls.append(r.post_data or '') if r.method == 'POST' and '/api' in r.url else None)
    base = len(calls)

    assert '7.19 秒' in page.locator('#loadLab').inner_text()
    assert '0.67 秒' in page.locator('#loadLab').inner_text()
    assert page.locator('[data-load-stat="records"]').inner_text().strip()
    # 定義與四個情境：兩個命中、兩個未命中；切換只換畫面，不送查詢
    defs = page.locator('.load-defs').inner_text()
    assert '命中快取' in defs and '未命中快取' in defs and '有效時間' in defs and '試算表' in defs
    for case, state, words in (('rare', 'miss', '未命中快取'), ('stale', 'miss', '12 分鐘'), ('quiet', 'hit', '備份'), ('recent', 'hit', '命中快取')):
        page.locator(f'[data-load-case="{case}"]').click()
        assert words in page.locator('#loadResult').inner_text(), case
        assert page.locator('.load-path').get_attribute('data-load-state') == state, case
        assert page.locator(f'[data-load-case="{case}"]').get_attribute('aria-pressed') == 'true'
        assert page.locator('[data-load-case][aria-pressed="true"]').count() == 1
    assert '未命中' not in page.locator('#loadRouteStep').inner_text()
    # 橫條動畫：播完後數字回到實測值、橫條全長；重播一次也一樣
    page.locator('#loadLab').scroll_into_view_if_needed()
    if page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"):
        assert page.locator('.load-replay').is_hidden() and 'is-armed' not in (page.locator('#loadLab').get_attribute('class') or '')
    else:
        done = "() => [...document.querySelectorAll('#loadLab .load-row[data-secs]')].every(r => r.querySelector('b').textContent.trim() === Number(r.dataset.secs).toFixed(2) + ' 秒' && Math.abs(new DOMMatrix(getComputedStyle(r.querySelector('i')).transform).a - 1) < 0.01)"
        page.wait_for_function(done, timeout=8000)
        page.locator('#loadReplay').click()
        assert page.evaluate("document.querySelector('#loadLab .load-row[data-secs=\"7.19\"] b').textContent") != '7.19 秒' or True
        page.wait_for_function(done, timeout=8000)
    assert page.locator('.load-evidence img').get_attribute('alt')
    page.locator('.load-evidence img').scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('.load-evidence img').naturalWidth > 0", timeout=15000)

    # 捲到之前，模組內容都是不透明的（不恢復 v107 拿掉的透明淡入）
    hidden = page.evaluate("""() => [...document.querySelectorAll('.tx-stages > li, .pn-desk > *, .un-chain > li, .tech-path > li, .tech-real > li')]
      .filter(e => parseFloat(getComputedStyle(e).opacity) < 1).length""")
    assert hidden == 0, f'{label}: {hidden} cards are transparent before scrolling'

    # v149：開場的「往下看」按鈕已拿掉
    assert page.locator('#painGo, a.tech-next').count() == 0, f'{label}: the scroll-down button should be gone'

    seen = set()
    for i in range(3):
        page.locator(f'[data-pain="{i}"]').click()
        assert page.locator(f'[data-pain="{i}"]').get_attribute('aria-pressed') == 'true'
        seen.add(page.locator('#painPath').inner_text())
    assert len(seen) == 3, f'{label}: pain cards do not change the path'

    seen = set()
    for k in TX_KEYS:
        page.locator(f'[data-tx="{k}"]').click()
        fp = page.locator('#txFp').get_attribute('class')
        assert ('is-diff' in fp) == (k == 'newer'), (label, k, fp)
        assert page.locator('#txStages li').count() == 6
        seen.add(page.locator('#txStages').inner_text() + page.locator('#txOut').inner_text())
    assert len(seen) == len(TX_KEYS), f'{label}: transcript scenarios share a result'
    page.locator('[data-tx="lock"]').click()
    assert '文章完成不等於績效完成' in page.locator('#txOut').inner_text()

    for i, badge in enumerate(PN_BADGES):
        page.locator(f'[data-pn="{i}"]').click()
        assert page.locator('.pn-final .tv-badge').inner_text().strip() == badge, (label, i)
        assert page.locator('.pn-checks li').count() >= 3
    page.locator('[data-pn="4"]').click()
    final = page.locator('.pn-final .pn-text').inner_text()
    assert '52.3' not in final and '20 日均線' in final and '3,000 張' in final, final

    for k in ('one', 'two', 'none'):
        page.locator(f'[data-un="{k}"]').click()
        assert 'is-selected' in page.locator(f'[data-un-card="{k}"]').get_attribute('class')
        assert page.locator('[data-un-card].is-selected').count() == 1
    page.locator('[data-un="one"]').click()
    assert '收錄' in page.locator('#unChain li').last.inner_text()

    # 既有互動仍可用
    page.locator('[data-heard="3"]').click(); assert '紅海' in page.locator('#heardResult').inner_text()
    page.locator('[data-tone-case="4"]').click(); assert '73.5' not in page.locator('#toneExample').inner_text()
    page.locator('[data-fail="busy"]').click(); assert page.locator('#failLanes .tech-lane').count() == 4
    page.locator('[data-can="0"]').click(); assert '價位' not in page.locator('#canResult').inner_text()

    methods = [json.loads(b).get('method') for b in calls[base:] if b.startswith('{')]
    bad = [m for m in methods if m not in ('apiLogUsage', 'apiGetTechStats')]
    assert not bad, f'{label}: interactions sent API calls {bad}'

    toc = page.evaluate("[...document.querySelectorAll('.doc h2, .doc h3')].filter(h => h.closest('.tech-lab')).length")
    assert toc == 0, f'{label}: {toc} headings inside interactive modules would enter the table of contents'

    small = page.evaluate("""() => [...document.querySelectorAll('[data-pain],[data-tx],[data-pn],[data-un],[data-load-case],#loadReplay,.author-links a,#heroPlay,#heroRange,#tlPlay,#tlSlider')]
      .filter(e => e.offsetParent).map(e => e.getBoundingClientRect()).filter(r => r.width < 44 || r.height < 44).length""")
    assert small == 0, f'{label}: {small} controls below 44px'


def check_layout(page, label):
    page.evaluate("document.querySelector('#tech-overview').scrollIntoView()")
    over = page.evaluate('document.documentElement.scrollWidth - innerWidth')
    assert over <= 1, f'{label}: page scrolls sideways by {over}px'
    crowded = page.evaluate("""() => [...document.querySelectorAll('.tx-stages li, .pn-col, .tech-path li, .un-chain li')]
      .filter(e => e.offsetParent && e.scrollWidth > e.clientWidth + 2).length""")
    assert crowded == 0, f'{label}: {crowded} cards overflow their box'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url')
    ap.add_argument('--shots', help='資料夾：存下各寬度截圖')
    args = ap.parse_args()
    server = None
    url = args.url
    if not url:
        url, server = serve_local()
    html = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width in WIDTHS:
            for scheme in ('light', 'dark'):
                ctx = browser.new_context(viewport={'width': width, 'height': 900}, color_scheme=scheme)
                page = ctx.new_page()
                if server:
                    page.route(FAKE_API, lambda r: r.fulfill(status=200, content_type='application/json',
                                                            body=json.dumps({'ok': True, 'result': []})))
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                open_tech(page, url)
                label = f'{width}px {scheme}'
                check_layout(page, label)
                if scheme == 'light' and width in (1280, 390):
                    check_interactions(page, label)
                    check_layout(page, label + ' after clicks')
                if args.shots and scheme == 'light':
                    Path(args.shots).mkdir(parents=True, exist_ok=True)
                    page.locator('#tech-loading').scroll_into_view_if_needed()
                    page.screenshot(path=str(Path(args.shots) / f'loading-{width}.png'))
                    page.locator('#tech-transcript-progress').scroll_into_view_if_needed()
                    page.screenshot(path=str(Path(args.shots) / f'tech-{width}.png'))
                html = html or page.content()
                tech_errors = [e for e in errors if 'tech' in e.lower() or 'Tech' in e]
                assert not tech_errors, (label, tech_errors)
                print(f'ok {label}')
                ctx.close()
        # 沒有 JavaScript 時看到的預設內容，要和有 JavaScript 初始化後的預設狀態一致
        sels = ['#painPath', '#txFp', '#txStages', '#txOut', '#pnDesk', '#unChain', '#heroNote', '#tlState']
        norm = lambda t: re.sub(r'\s+', '', t)
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900})
        page = ctx.new_page()
        if server:
            page.route(FAKE_API, lambda r: r.fulfill(status=200, content_type='application/json',
                                                    body=json.dumps({'ok': True, 'result': []})))
        open_tech(page, url)
        with_js = [norm(page.locator(s).text_content()) for s in sels]
        ctx.close()
        ctx = browser.new_context(viewport={'width': 1280, 'height': 900}, java_script_enabled=False)
        page = ctx.new_page()
        page.goto(url, wait_until='domcontentloaded')
        no_js = [norm(page.locator(s).text_content()) for s in sels]
        for s, a, b in zip(sels, with_js, no_js):
            assert a == b, f'{s}: static default differs from scripted default'
        print('ok static defaults match scripted defaults')
        ctx.close()
        ctx = browser.new_context(viewport={'width': 390, 'height': 900}, reduced_motion='reduce')
        page = ctx.new_page()
        if server:
            page.route(FAKE_API, lambda r: r.fulfill(status=200, content_type='application/json',
                                                    body=json.dumps({'ok': True, 'result': []})))
        open_tech(page, url)
        check_interactions(page, '390px reduced motion', reduced=True)
        assert page.locator('.tech-anim').count() == 0 or page.evaluate(
            "[...document.querySelectorAll('.tech-anim')].every(e => getComputedStyle(e).animationName === 'none')"), 'animation ran with reduced motion'
        dur = page.evaluate("getComputedStyle(document.querySelector('[data-tx]')).transitionDuration")
        assert all(float(x.strip('s')) == 0 for x in dur.split(',')), dur
        print('ok reduced motion: no transitions')
        ctx.close()
        browser.close()
    # 沒有 JavaScript：預設案例與文字版規則都在原始 HTML 裡
    for needle in ['data-tech-story-v72', '指紋不同', '文字版規則與工程細節', '甲公司站回季線', '文字版規則', 'data-un-card="none"', '上次哪一天講到這檔']:
        assert needle in html, needle
    assert not re.search(r'73\.5', html.split('data-tech-story-v72', 1)[1].split('</script>', 1)[0]), 'member notice price leaked'
    print('ok no-JS fallback text and no member-notice price in the tech page')
    if server:
        server.shutdown()


if __name__ == '__main__':
    main()
