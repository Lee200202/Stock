"""全站按鈕互動性稽核（管理者 2026/10/14 定義）：
一顆按鈕點下去，畫面上看得到的東西必須跟著變——可見文字、圖解、按下／展開狀態、捲動位置、彈出的視窗或新分頁其中之一。
都沒有變的，就是沒有互動性的按鈕，不能放。
本機唯讀：用假的 API（不寄信、不寫資料），逐分頁、逐顆按，點完把分頁切回來。"""
import json
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, FAKE_API

SNAP = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; };
  const svgs = [...document.querySelectorAll('svg')].filter(vis).map(s => s.innerHTML.length + ':' + s.querySelectorAll('*').length).join(',');
  const states = [...document.querySelectorAll('[aria-pressed],[aria-expanded],[aria-selected],[aria-current],details')].filter(vis)
      .map(e => (e.getAttribute('aria-pressed') || '') + (e.getAttribute('aria-expanded') || '') + (e.getAttribute('aria-selected') || '') + (e.getAttribute('aria-current') || '') + (e.open ? 'o' : '')).join('');
  const dialogs = [...document.querySelectorAll('dialog[open],[role=dialog],.modal,.asst-panel')].filter(vis).length;
  return {text: document.body.innerText, svgs, states, dialogs, y: Math.round(scrollY), hash: location.hash, cls: [...document.querySelectorAll('.is-selected,.is-on,.is-active,.is-open')].filter(vis).length};
}"""
BUTTONS = """(tab) => {
  const panel = document.querySelector('#p-' + tab) || document.querySelector('[data-panel="' + tab + '"]');
  if (!panel) return [];
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden' && !e.closest('[hidden]'); };
  // 已經是目前選項的那一顆，再按一次本來就不會變：另外記，不算沒有互動
  const current = e => e.getAttribute('aria-pressed') === 'true' || e.getAttribute('aria-selected') === 'true' || !!e.getAttribute('aria-current') || /(^|\\s)(is-selected|is-active|is-on|active|on)(\\s|$)/.test(e.className);
  // 結果要看後端回覆的（送出訂閱、查詢訂閱）：假 API 量不出來，另外列
  const backend = e => /^(subBtn|unsubBtn)$/.test(e.id) || !!e.closest('form');
  return [...panel.querySelectorAll('button, [role=button]')].filter(e => vis(e) && !e.disabled).map((e, i) => { e.dataset.auditId = tab + '-' + i; return {id: tab + '-' + i, current: current(e), backend: backend(e), label:(e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40)}; });
}"""

u, server = serve_local()
dead, total, skipped = [], 0, []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for width in (1440, 390):
            ctx = b.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
            p = ctx.new_page()
            p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
            p.goto(u, wait_until='networkidle')
            p.wait_for_timeout(800)
            tabs = p.evaluate("()=>[...document.querySelectorAll('.tab[data-tab]')].map(t=>t.dataset.tab)")
            for tab in tabs:
                p.locator(f'.tab[data-tab="{tab}"]').first.click(); p.wait_for_timeout(600)
                if tab == 'tech':
                    p.locator('[data-story-mode="short"]').click(); p.wait_for_timeout(300)
                for item in p.evaluate(BUTTONS, tab):
                    if item['current'] or item['backend']:
                        skipped.append(item['label']); continue
                    loc = p.locator(f'[data-audit-id="{item["id"]}"]')
                    if not loc.count() or not loc.is_visible():
                        continue
                    total += 1
                    try:
                        loc.scroll_into_view_if_needed(timeout=1500); p.wait_for_timeout(120)
                        before = p.evaluate(SNAP)
                        pages = len(ctx.pages)
                        loc.click(timeout=1500); p.wait_for_timeout(450)
                        after = p.evaluate(SNAP)
                    except Exception as e:
                        continue
                    changed = before != after or len(ctx.pages) != pages
                    if not changed:
                        dead.append({'width': width, 'tab': tab, 'label': item['label']})
                    for extra in ctx.pages[1:]:
                        extra.close()
                    if p.evaluate("t=>!(document.querySelector('#p-'+t)||{}).hidden===false", tab):
                        pass
                    if p.evaluate("t=>{const e=document.querySelector('#p-'+t);return !!(e&&e.hidden);}", tab):
                        p.locator(f'.tab[data-tab="{tab}"]').first.click(); p.wait_for_timeout(400)
                    p.keyboard.press('Escape')
            ctx.close()
        b.close()
finally:
    server.shutdown()
lines = [f"按過 {total} 顆（另有 {len(skipped)} 顆是目前選項或要看後端回覆，不在此列）；點了畫面沒有任何變化的 {len(dead)} 顆："] + [f"・{d['width']}px　{d['tab']}　「{d['label']}」" for d in dead]
open('docs/1003-review/site-buttons-audit.txt', 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
