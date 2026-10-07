"""持股追蹤「平均已實現報酬以回合計」與移除戰情追蹤的驗收（v139）。唯讀，不按任何寫入按鈕。

已實現：同一檔買了又賣、賣了又買，每一個已經結束的回合各算一次；目前又持有中的股票，先前結束的回合也算。
這裡用正式 API 的持股資料獨立重算一次（不沿用後端的程式），和後端回傳、畫面顯示逐一比對。
同時確認：沒有戰情追蹤分頁與面板、個股頁沒有技術條件區塊、個股頁打得開、畫面上的時間沒有 UTC 的 ISO 字串。

    python scripts/check_tracker_rounds.py                 本機組站（後端若還是舊版，由這裡的獨立重算補上回合欄位，只驗畫面）
    python scripts/check_tracker_rounds.py --url https://lee200202.github.io/Stock/    正式站（後端回傳也要對得上）
"""
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
FAKE_API = 'https://tracker-rounds-check.example.workers.dev/api'
REAL_API = 'https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api'
ORIGIN = 'https://lee200202.github.io'
ISO_UTC = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z')


def round_returns(item):
    return [float(n.replace('−', '-').replace('－', '-')) for n in re.findall(r'([+\-−－]?\d+(?:\.\d+)?)\s*%', str(item.get('roundRets') or ''))]


def closed_rounds(item):
    """這一檔已經結束的回合報酬。持有中：最後一個回合還沒結束；已出場：最後一個回合用賣出當日的價格（ret）。"""
    rets, n = round_returns(item), int(item.get('rounds') or 1)
    if item.get('stillHeld'):
        return rets[:n - 1] if n > 1 and len(rets) == n else []
    if n > 1 and len(rets) == n:
        if item.get('ret') is not None:
            rets[-1] = item['ret']
        return rets
    return [] if item.get('ret') is None else [item['ret']]


def js_round(x, digits):
    """和 JavaScript 的 Math.round(x * 10^n) / 10^n 同一種進位（.5 往正無限大）。"""
    import math
    k = 10 ** digits
    return math.floor(x * k + 0.5) / k


def expected(tracker):
    realized, from_held = [], []
    for i in tracker.get('exited') or []:
        realized += closed_rounds(i)
    for i in tracker.get('held') or []:
        prior = closed_rounds(i)
        if prior:
            realized += prior
            from_held.append({'code': str(i.get('code')), 'name': i.get('name'), 'rets': prior})
    if not realized:
        return None
    return {'rounds': len(realized), 'fromHeld': from_held,
            'avgReturn': js_round(sum(realized) / len(realized), 2),
            'positiveRatio': js_round(len([v for v in realized if v > 0]) / len(realized) * 100, 1)}


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def build_local():
    site = Path(tempfile.mkdtemp(prefix='tracker-rounds-'))
    subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
                   env=dict(os.environ, SITE_API_URL=FAKE_API), stdout=subprocess.DEVNULL)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def find_tracker(node):
    """回應裡帶有持股追蹤資料（held／exited／exitedSummary）的那一層。"""
    if isinstance(node, dict):
        if isinstance(node.get('exitedSummary'), dict) and isinstance(node.get('held'), list):
            return node
        for v in node.values():
            hit = find_tracker(v)
            if hit:
                return hit
    return None


def forward(state, route):
    """本機頁面的 API 請求轉給正式的唯讀 API。後端還是舊版時，補上獨立重算的回合欄位，讓畫面可以先驗。"""
    if route.request.method == 'OPTIONS':
        route.fulfill(status=204, headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'})
        return
    try:
        resp = route.fetch(url=REAL_API, headers={'Content-Type': 'application/json', 'Origin': ORIGIN})
        body = resp.body()
        try:
            data = json.loads(body)
            t = find_tracker(data)
            if t and 'rounds' not in t['exitedSummary']:
                exp = expected(t)
                if exp:
                    t['exitedSummary'].update(exp)
                    state['patched'] = True
                    body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        except ValueError:
            pass
        route.fulfill(status=resp.status, body=body, headers={'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'})
    except Exception:
        pass                      # 頁面關閉時還在路上的背景請求


def check(browser, url, local):
    lines = []
    for w, touch in ((1280, False), (390, True)):
        state = {}
        pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=touch)
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        if local:
            pg.route(FAKE_API, partial(forward, state))
        pg.goto(url + '?tab=tracker', wait_until='domcontentloaded', timeout=90000)
        pg.wait_for_selector('.tracker-card', timeout=90000)
        pg.wait_for_function('window.__tracker && window.__tracker.held')

        # 一、沒有戰情追蹤：分頁、面板、個股頁的技術條件區塊、相關的全域函式
        gone = pg.evaluate("""() => ({
          tab: !!document.querySelector('[data-tab="battle"]'), panel: !!document.querySelector('#p-battle'),
          section: !!document.querySelector('#dSecBattle'), nav: !!document.querySelector('[data-go="dSecBattle"]'),
          hooks: ['__battleLoad', '__openBattleStock', '__battleQuote', '__battleDetail', 'MarketBattle', 'BATTLE_CONFIG'].filter(k => k in window),
          words: [...document.querySelectorAll('.tabs .tab, nav button, nav a')].map(e => e.textContent.trim()).filter(t => /戰情/.test(t)),
          classes: [...document.querySelectorAll('[class*="battle"]')].length })""")
        assert not gone['tab'] and not gone['panel'] and not gone['section'] and not gone['nav'], gone
        assert not gone['hooks'] and not gone['words'] and not gone['classes'], gone
        # ?tab=battle 的舊連結：回到每日總覽，不是空白頁
        old = browser.new_page(viewport={'width': w, 'height': 900})
        if local:
            old.route(FAKE_API, partial(forward, {}))
        old.goto(url + '?tab=battle', wait_until='domcontentloaded', timeout=90000)
        old.wait_for_function("document.querySelector('.tab[aria-selected=\"true\"]')", timeout=30000)
        assert old.evaluate("document.querySelector('.tab[aria-selected=\"true\"]').dataset.tab") == 'overview'
        old.close()

        # 二、已實現報酬以回合計
        t = pg.evaluate("({held: window.__tracker.held, exited: window.__tracker.exited, sum: window.__tracker.exitedSummary})")
        exp = expected(t)
        assert exp, '正式資料應該至少有一個已結束的回合'
        got = t['sum']
        assert got.get('rounds') == exp['rounds'], (got.get('rounds'), exp['rounds'])
        assert abs(got['avgReturn'] - exp['avgReturn']) < 0.006 and abs(got['positiveRatio'] - exp['positiveRatio']) < 0.06, (got, exp)
        assert [(h['code'], h['rets']) for h in got.get('fromHeld') or []] == [(h['code'], h['rets']) for h in exp['fromHeld']], (got.get('fromHeld'), exp['fromHeld'])
        assert exp['rounds'] >= len([i for i in t['exited'] if i.get('ret') is not None]), '回合數不會比有報酬的出場檔數少'
        multi_held = [i for i in t['held'] if int(i.get('rounds') or 1) > 1 and len(round_returns(i)) == int(i.get('rounds') or 1)]
        for i in multi_held:
            assert any(h['code'] == str(i['code']) for h in exp['fromHeld']), ('持有中的多回合股票，先前的回合沒有被計入', i['code'])

        pg.locator('[data-tk="exited"]').click()
        pg.wait_for_selector('.tsum-note', timeout=10000)
        shown = pg.evaluate("""() => ({ note: document.querySelector('.tsum-note').textContent,
          cells: [...document.querySelectorAll('.tsum .tsum-i')].map(e => e.querySelector('.k').textContent.trim() + '=' + e.querySelector('.v').textContent.trim()),
          over: document.documentElement.scrollWidth - innerWidth,
          noteRight: document.querySelector('.tsum-note').getBoundingClientRect().right, vw: innerWidth })""")
        assert f"共 {exp['rounds']} 個已結束的回合" in shown['note'], shown['note']
        for h in exp['fromHeld']:
            assert h['name'] in shown['note'], (h['name'], shown['note'])
        avg_cell = next(c for c in shown['cells'] if c.startswith('平均已實現報酬'))
        assert f"{abs(exp['avgReturn']):.2f}" in avg_cell, (avg_cell, exp['avgReturn'])
        assert shown['over'] <= 1 and shown['noteRight'] <= shown['vw'] + 1, shown
        # 持有中那一頁不顯示這段說明
        pg.locator('[data-tk="held"]').click()
        pg.wait_for_function("!document.querySelector('.tsum-note')", timeout=10000)

        # 三、個股頁打得開、沒有技術條件；畫面上沒有 UTC 的 ISO 時間
        code = str((exp['fromHeld'] or [{'code': t['held'][0]['code']}])[0]['code'])
        pg.locator(f'.tracker-card:has(.tracker-card-code:text-is("{code}"))').first.click()
        pg.wait_for_function("document.querySelector('#dSecPrice, .detail, #detail') && !document.querySelector('#dSecBattle')", timeout=30000)
        pg.wait_for_timeout(4000)
        text = pg.evaluate("document.body.innerText")
        if not state.get('patched'):                 # 後端還是舊版時，時間文字也還是舊的；正式站一律要過
            assert not ISO_UTC.search(text), ISO_UTC.search(text).group(0)
        assert '技術條件' not in pg.evaluate("[...document.querySelectorAll('[data-go]')].map(b => b.textContent).join('|')")
        assert not errs, errs
        lines.append(f"ok {w}px：沒有戰情分頁與技術條件區塊；已實現以回合計 {exp['rounds']} 回合、平均 {exp['avgReturn']:+.2f}%、正報酬 {exp['positiveRatio']}%"
                     f"（含持有中先前回合：{'、'.join(h['name'] for h in exp['fromHeld']) or '無'}）"
                     f"{'；後端為舊版，回合欄位由獨立重算補上' if state.get('patched') else '；後端回傳與獨立重算一致'}；個股 {code} 可開、無 UTC 時間")
        pg.close()
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='')
    args = ap.parse_args()
    srv = None if args.url else build_local()
    url = args.url or f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for line in check(browser, url, not args.url):
                print(line)
        finally:
            browser.close()
    if srv:
        srv.shutdown()


if __name__ == '__main__':
    main()
