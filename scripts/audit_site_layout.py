"""全站各分頁的版面量測（唯讀、正式站）：字級、末行孤字、橫向溢出、觸控高度、純文字區塊。結果寫到 docs/1003-review/site-layout-audit.txt"""
import json
from playwright.sync_api import sync_playwright

JS = r"""
(scopeSel) => {
  const root = document.querySelector(scopeSel) || document.body;
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const sel = el => { let s = el.tagName.toLowerCase(); const c = [...el.classList].slice(0, 2).join('.'); if (c) s += '.' + c; const p = el.parentElement; if (p) { const pc = [...p.classList].slice(0, 1).join('.'); s = p.tagName.toLowerCase() + (pc ? '.' + pc : '') + ' > ' + s; } return s; };
  const out = {small: {}, orphans: [], overflow: [], smallTargets: {}, blocks: [], pageOverflow: document.documentElement.scrollWidth > innerWidth + 1, height: Math.round(root.getBoundingClientRect().height)};
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); const seen = new Set();
  while (walker.nextNode()) { const t = walker.currentNode; const txt = t.textContent.trim(); if (txt.length < 2) continue; const el = t.parentElement; if (!el || seen.has(el) || !vis(el)) continue; seen.add(el);
    const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < 13.5) { const k = sel(el); (out.small[k] = out.small[k] || {px: fs, n: 0, sample: txt.slice(0, 22)}).n++; } }
  [...root.querySelectorAll('h1, h2, h3, h4, b, strong, button, figcaption, small, summary, p, li, td, th, .k, .v')].forEach(el => {
    if (!vis(el) || el.children.length > 3) return; const txt = el.textContent.trim(); if (txt.length < 8 || txt.length > 160) return;
    const range = document.createRange(); range.selectNodeContents(el); const rects = [...range.getClientRects()].filter(r => r.width > 1); if (rects.length < 2) return;
    const lines = []; rects.forEach(r => { const l = lines.find(x => Math.abs(x.top - r.top) < 4); if (l) { l.left = Math.min(l.left, r.left); l.right = Math.max(l.right, r.right); } else lines.push({top: r.top, left: r.left, right: r.right}); });
    if (lines.length < 2) return; lines.sort((a, b) => a.top - b.top); const fs = parseFloat(getComputedStyle(el).fontSize); const last = lines[lines.length - 1].right - lines[lines.length - 1].left;
    if (last <= fs * 2.2) out.orphans.push({s: sel(el), lines: lines.length, text: txt.slice(0, 34)}); });
  [...root.querySelectorAll('*')].forEach(el => { if (!vis(el)) return; const s = getComputedStyle(el);
    if (el.scrollWidth > el.clientWidth + 2 && s.overflowX === 'visible' && el.clientWidth > 40 && el.children.length) { const r = el.getBoundingClientRect(); if (r.right > innerWidth + 1) out.overflow.push({s: sel(el), by: Math.round(r.right - innerWidth)}); } });
  [...root.querySelectorAll('button, a[href], select, input, summary, [role="button"]')].forEach(el => { if (!vis(el)) return; const r = el.getBoundingClientRect(); if (r.height < 40 || r.width < 40) { const k = sel(el); (out.smallTargets[k] = out.smallTargets[k] || {w: Math.round(r.width), h: Math.round(r.height), n: 0, sample: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 14)}).n++; } });
  [...root.querySelectorAll('.sect, .card, section, article')].forEach(el => { if (!vis(el)) return; const r = el.getBoundingClientRect(); if (r.height < 160) return; const h = el.querySelector('h2, h3'); const words = el.textContent.replace(/\s+/g, '').length; const media = el.querySelectorAll('svg, img, canvas').length;
    if (words > 260 && media === 0) out.blocks.push({s: sel(el), title: h ? h.textContent.trim().slice(0, 22) : '', words, height: Math.round(r.height)}); });
  return out;
}
"""

TABS = [('overview', '每日總覽'), ('tracker', '持股追蹤'), ('perf', '績效走勢'), ('subscribe', '訂閱通知'), ('mail', '郵件查詢'), ('transcript', '逐字稿')]
lines = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    for w in (1600, 390):
        p = b.new_page(viewport={'width': w, 'height': 900})
        p.goto('https://lee200202.github.io/Stock/', wait_until='domcontentloaded', timeout=90000)
        p.wait_for_timeout(6000)
        tabs = p.evaluate("[...document.querySelectorAll('button.tab[data-tab]')].map(b => [b.dataset.tab, b.textContent.trim()])")
        if w == 1600:
            lines.append('分頁：' + json.dumps(tabs, ensure_ascii=False))
        targets = [(t[0], t[1]) for t in tabs if t[0] != 'tech']
        targets.append(('__stock', '個股頁 2330'))
        for key, label in targets:
            try:
                if key == '__stock':
                    p.goto('https://lee200202.github.io/Stock/?stock=2330', wait_until='domcontentloaded', timeout=90000)
                    p.wait_for_timeout(9000)
                    scope = '.detail-body'
                else:
                    p.locator(f'button.tab[data-tab="{key}"]').first.click()
                    p.wait_for_timeout(5000)
                    scope = 'main, .wrap, body'
                    scope = p.evaluate("(k) => { const el = document.querySelector('.panel.active, .tabpanel.active, section.panel:not([hidden]), [data-panel=\"' + k + '\"]:not([hidden])'); if (!el) return 'body'; el.setAttribute('data-audit', '1'); return '[data-audit=\"1\"]'; }", key)
                p.evaluate("async () => { const sc = document.querySelector('.detail-body') || document.scrollingElement; for (let y = 0; y < sc.scrollHeight; y += 700) { sc.scrollTo ? sc.scrollTo(0, y) : window.scrollTo(0, y); await new Promise(r => setTimeout(r, 50)); } }")
                r = p.evaluate(JS, scope)
                p.evaluate("document.querySelectorAll('[data-audit]').forEach(e => e.removeAttribute('data-audit'))")
            except Exception as e:
                lines.append(f'\n== {label}（{w}px）量測失敗：{str(e)[:120]}')
                continue
            lines.append(f"\n== {label}（{w}px）範圍 {scope}｜高 {r['height']}｜整頁橫向捲動 {'有' if r['pageOverflow'] else '無'}")
            for k, v in sorted(r['small'].items(), key=lambda kv: (kv[1]['px'], -kv[1]['n']))[:14]:
                lines.append(f"  小字 {v['px']}px ×{v['n']}｜{k}｜{v['sample']}")
            lines.append(f"  末行孤字 {len(r['orphans'])} 處")
            for o in r['orphans'][:10]:
                lines.append(f"    {o['s']}｜{o['lines']} 行｜{o['text']}")
            for o in r['overflow'][:6]:
                lines.append(f"  超出畫面 {o['by']}px｜{o['s']}")
            for k, v in sorted(r['smallTargets'].items(), key=lambda kv: kv[1]['h'])[:10]:
                lines.append(f"  觸控區偏小 {v['w']}x{v['h']} ×{v['n']}｜{k}｜{v['sample']}")
            for bl in r['blocks'][:8]:
                lines.append(f"  純文字大區塊 {bl['words']} 字、高 {bl['height']}｜{bl['s']}｜{bl['title']}")
        p.close()
    b.close()
open('docs/1003-review/site-layout-audit.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print(len(lines), 'lines')
