"""List every visible control that looks like a text link (underline, or no border and no background) on each tab.
Read-only; uses the working-tree build on the real Pages origin (LOCAL=1 for the working-tree build)."""
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / 'scratch' / 'ss' / '_site'
URL = 'https://lee200202.github.io/Stock/'
LIVE = not os.environ.get('LOCAL')  # 預設量正式站；LOCAL=1 改量工作區建置
if not LIVE and not os.environ.get('NOBUILD'):
    env = dict(os.environ, SITE_API_URL='https://zhangzhen-site-api.rainforecast2026-6fb.workers.dev/api', PYTHONUTF8='1')
    subprocess.run([sys.executable, 'scripts/build_original_site.py', '--output', str(SITE)], cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
html = '' if LIVE else (SITE / 'index.html').read_text(encoding='utf-8')

JS = r"""() => {
  const out = [];
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const clear = c => !c || c === 'transparent' || /rgba\(\d+, \d+, \d+, 0\)/.test(c);
  document.querySelectorAll('a[href], button, [role="button"], summary').forEach(e => {
    if (!vis(e) || e.closest('svg, .mail-content, .tx-body, [data-tech-book] .book-art')) return;
    const s = getComputedStyle(e), txt = (e.textContent || '').trim().replace(/\s+/g, ' ');
    if (!txt || txt.length > 40) return;
    const underline = s.textDecorationLine.includes('underline');
    const bare = clear(s.backgroundColor) && parseFloat(s.borderTopWidth) === 0 && parseFloat(s.borderBottomWidth) === 0 && !e.querySelector('svg') ;
    const pill = parseFloat(s.borderTopLeftRadius) >= Math.min(e.offsetHeight / 2 - 1, 18);
    if (underline || bare) out.push({sel: e.tagName.toLowerCase() + (e.className && typeof e.className === 'string' ? '.' + e.className.trim().split(/\s+/).slice(0, 2).join('.') : ''),
      text: txt.slice(0, 24), underline, bare, pill, parent: (e.parentElement.className || '').toString().split(' ')[0]});
  });
  return out;
}"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={'width': 1440, 'height': 900})
    if not LIVE:
        p.route(lambda u: u.split('?')[0].rstrip('/') == URL.rstrip('/'), lambda r: r.fulfill(status=200, content_type='text/html; charset=utf-8', body=html))
    p.goto(URL, wait_until='domcontentloaded', timeout=90000)
    p.wait_for_function("document.querySelector('#ledger .led-pill')", timeout=60000)
    p.wait_for_timeout(2500)
    found = Counter(); samples = {}
    for tab in ['overview', 'tracker', 'perf', 'subscribe', 'mail', 'tx', 'tech']:
        p.evaluate("t => document.querySelector('button.tab[data-tab=\"' + t + '\"]').click()", tab)
        p.wait_for_timeout(4000)
        if tab in ('mail', 'tx'):
            p.evaluate("t => { const c = document.querySelector(t === 'mail' ? '#calGrid .cal-c.has' : '#txCalGrid .cal-c.has'); if (c) c.click(); }", tab)
            p.wait_for_timeout(5000)
        if tab == 'overview':
            p.evaluate("() => { const c = document.querySelector('#dayCalGrid .cal-c.has'); if (c) c.click(); }"); p.wait_for_timeout(5000)
        for r in p.evaluate(JS):
            k = (tab, r['sel'], r['underline'], r['pill'])
            found[k] += 1; samples.setdefault(k, r['text'] + ' ｜in .' + r['parent'])
    p.goto(URL + '?stock=2330', wait_until='domcontentloaded'); p.wait_for_timeout(12000)
    for r in p.evaluate(JS):
        k = ('stock', r['sel'], r['underline'], r['pill']); found[k] += 1; samples.setdefault(k, r['text'] + ' ｜in .' + r['parent'])
    for k, n in sorted(found.items()):
        print(f"{k[0]:10} {'底線' if k[2] else '無框'} {'膠囊' if k[3] else '非膠囊'} ×{n}  {k[1]}  「{samples[k]}」")
    b.close()
