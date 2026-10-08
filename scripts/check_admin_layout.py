"""後台版面檢查（v115）：本機組站＋假 API，不碰正式資料。

每個分頁檢查：卡片與面板同寬；表單欄位填滿卡片（不留空欄）；同一表單的輸入框與下拉選單同高同圓角；沒有整頁橫向溢出。
用法：python scripts/check_admin_layout.py [寬度,寬度…]（預設 1440,1280,768,390）
"""
import json, os, subprocess, sys, tempfile, threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]; OUT = os.getenv('ADMIN_AUDIT_OUT', ''); WIDTHS = [int(x) for x in (sys.argv[1] if len(sys.argv) > 1 else '1440,1280,768,390').split(',')]
API = 'https://admin-check.example.workers.dev/api'
site = Path(tempfile.mkdtemp(prefix='admin-audit-'))
subprocess.run([sys.executable, str(ROOT / 'scripts' / 'build_original_site.py'), '--output', str(site)], check=True,
               env=dict(os.environ, SITE_API_URL=API), stdout=subprocess.DEVNULL)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


srv = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
threading.Thread(target=srv.serve_forever, daemon=True).start()


def mock(route):
    m = route.request.post_data_json.get('method', '')
    res = {'apiAdminLogin': {'ok': True, 'data': {'today': '2026/10/03', 'closedToday': True, 'hasData': False, 'todayTrades': 0, 'postedDates': [], 'holidays': []}},
           'apiAdminTodayStatus': {'ok': True, 'today': '2026/10/05', 'now': '10:00', 'items': [], 'automation': [],
                                   'smsOriginals': [{'id': '185117227', 'time': '2026/10/05 09:41:55',
                                                     'text': '張震-1:請於955元以上獲利賣出5536聖暉，\n資金轉為880元以下市價買進8210勤誠！'}],
                                   'smsOperations': [{'time': '2026/10/05 09:41:55', 'name': '聖暉', 'code': '5536',
                                                      'direction': '賣出', 'price': '955 元以上', 'detail': '請獲利賣出。'}]},
           'apiAdminHeldList': {'ok': True, 'items': [], 'exited': []}}.get(m, {'ok': False, 'reason': '測試環境'})
    route.fulfill(status=200, content_type='application/json', body=json.dumps({'ok': True, 'result': res}))


AUDIT = """() => {
  const p = [...document.querySelectorAll('.panel')].find(x => !x.hidden);
  const pw = p.getBoundingClientRect().width;
  const vis = e => e.offsetParent && e.getBoundingClientRect().width > 0;
  const label = e => (e.querySelector('h2,h3,b,summary,label')?.innerText || e.className || e.tagName).trim().slice(0, 22);
  const out = [];
  [...p.querySelectorAll('.card, details.card')].filter(vis).forEach(c => {
    const cw = c.getBoundingClientRect().width;
    if (cw < pw - 4) out.push(['card', label(c), Math.round(cw), Math.round(pw)]);
    const inner = cw - parseFloat(getComputedStyle(c).paddingLeft) - parseFloat(getComputedStyle(c).paddingRight);
    [...c.querySelectorAll(':scope > .line-form, :scope > .row, :scope > .field, :scope > .mtool, :scope > textarea, :scope > .field > textarea, :scope > .line-search')].filter(vis).forEach(b => {
      const bw = b.getBoundingClientRect().width;
      if (bw < inner * 0.9) out.push(['block', label(c) + ' › ' + (b.className || b.tagName), Math.round(bw), Math.round(inner)]);
    });
    [...c.querySelectorAll('.line-form, .row')].filter(vis).forEach(f => {
      const fw = f.getBoundingClientRect().width;
      const used = [...f.children].filter(vis).reduce((s, k) => s + k.getBoundingClientRect().width, 0);
      const rows = new Set([...f.children].filter(vis).map(k => Math.round(k.getBoundingClientRect().top))).size;
      if (rows === 1 && used < fw * 0.8) out.push(['fields', label(c) + ' › ' + f.className, Math.round(used), Math.round(fw)]);
    });
  });
  // 同一個表單裡的輸入框與下拉選單：高度差 ≤2px、圓角相同
  [...p.querySelectorAll('.line-form, .row, .card')].filter(vis).forEach(f => {
    const ctl = [...f.querySelectorAll(':scope > .field > input:not([type=checkbox]):not([type=radio]):not([type=hidden]), :scope > .field > select')].filter(vis);
    if (ctl.length < 2) return;
    const hs = ctl.map(c => Math.round(c.getBoundingClientRect().height)), rs = ctl.map(c => getComputedStyle(c).borderTopLeftRadius);
    if (Math.max(...hs) - Math.min(...hs) > 2 || new Set(rs).size > 1) out.push(['controls', label(f.closest('.card') || f), hs.join('/'), [...new Set(rs)].join('/')]);
  });
  // 後台專用郵件通知：收件者與操作上下排、各佔整列
  const st = p.querySelector('.line-form.is-stack');
  if (st && vis(st)) {
    const k = [...st.children].filter(vis).map(x => x.getBoundingClientRect()), fw = st.getBoundingClientRect().width;
    if (!(k.length === 2 && k[1].top >= k[0].bottom - 1 && k.every(r => r.width >= fw - 2))) out.push(['stack', '後台專用郵件通知', k.map(r => Math.round(r.width)).join('/'), Math.round(fw)]);
  }
  const over = document.documentElement.scrollWidth - innerWidth;
  if (over > 1) out.push(['overflow', 'page', over, innerWidth]);
  return out;
}"""
found = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for w in WIDTHS:
        pg = b.new_page(viewport={'width': w, 'height': 1000})
        pg.route(API, mock)
        pg.goto(f'http://127.0.0.1:{srv.server_port}/admin.html', wait_until='domcontentloaded')
        pg.locator('#key').fill('local-mock-key'); pg.locator('#loginBtn').click()
        pg.wait_for_selector('#app:not([hidden])', timeout=15000)
        links = pg.evaluate("""() => [...document.querySelectorAll('a.loglink, a.admin-link')].map(a => ({
          id: a.id || a.textContent.trim(), href: a.getAttribute('href'),
          height: a.getBoundingClientRect().height,
          radius: getComputedStyle(a).borderTopLeftRadius,
          visible: !!a.offsetParent
        }))""")
        for link in links:
            assert link['href'] and link['href'] != '#', (w, 'dead link', link)
            assert link['href'].startswith(('https://github.com/Lee200202/Stock/actions/',
                                            'https://script.google.com/home/projects/')), (w, 'unexpected destination', link)
            if link['visible']:
                assert link['height'] >= 44 and link['radius'] != '0px', (w, 'link target', link)
        for tab in ['post', 'manual', 'held', 'maint', 'smsadmin', 'ops', 'line', 'ver']:
            pg.locator(f'button.tab[data-tab="{tab}"]').click(); pg.wait_for_timeout(500)
            if tab == 'post':
                pg.locator('.sms-original-text').wait_for(timeout=10000)
                raw = pg.locator('.sms-original-text').inner_text()
                assert '955元以上' in raw and '880元以下' in raw and '\n' in raw
                assert pg.locator('#todaySmsCount').inner_text() == '1 則原文 · 1 筆操作'
                assert pg.locator('#todaySmsOps .sms-stage h3').all_inner_texts() == ['逐字原文', '已寫入操作']
                assert pg.locator('#todaySmsOps .sms-original-head').inner_text().startswith('文章 185117227')
                assert pg.locator('#todaySmsOps .sms-stage').count() == 2
                if w in (1440, 390):
                    pg.evaluate("document.documentElement.dataset.theme='dark'; document.documentElement.style.setProperty('--rd-fs','1.5')")
                    for row in pg.evaluate(AUDIT):
                        found.append((w, 'post-dark-150', *row)); print('FOUND', w, 'post-dark-150', *row)
                    if OUT: pg.screenshot(path=f'{OUT}/audit-post-dark-150-{w}.png', full_page=True)
                    pg.evaluate("document.documentElement.dataset.theme='light'; document.documentElement.style.removeProperty('--rd-fs')")
            for row in pg.evaluate(AUDIT):
                found.append((w, tab, *row)); print('FOUND', w, tab, *row)
            if tab in ('smsadmin', 'line', 'post', 'manual') and OUT:
                pg.screenshot(path=f'{OUT}/audit-{tab}-{w}.png', full_page=True)
        print(f'checked {w}px: 8 tabs')
        pg.close()
    b.close()
srv.shutdown()
assert not found, f'{len(found)} layout findings'
print('admin layout OK: cards full width, forms fill cards, controls match, no sideways scroll')
