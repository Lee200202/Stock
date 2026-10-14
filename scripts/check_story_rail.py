"""技術說明目錄：每一種寬度都在右邊、平常收起只露出把手、不蓋住內容、不出現「幾幕」按鈕；
滑鼠移過去展開完整章節（回到頂端＋十八章），移開收起；觸控點一下展開，點章節跳過去並收起。"""
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

MEASURE = """() => {
  const rail = document.querySelector('.story-rail'), r = rail.getBoundingClientRect();
  const c = document.querySelector('.tech-story').getBoundingClientRect(), panel = document.querySelector('.story-rail-panel');
  return {rail: [r.left, r.right, r.top, r.bottom, r.width], content: [c.left, c.right], vw: document.documentElement.clientWidth, vh: innerHeight,
          collapsed: panel.hidden, acts: (rail.textContent.match(/\\d幕/g) || []).length, actButtons: rail.querySelectorAll('[data-story-act]').length};
}"""
PANEL = """() => { const e = document.querySelector('.story-rail-panel'), r = e.getBoundingClientRect();
  return {hidden: e.hidden, chapters: e.querySelectorAll('a[data-story-go]').length, top: e.querySelectorAll('[data-story-top]').length,
          heads: e.querySelectorAll('.story-rail-act').length, acts: (e.textContent.match(/\\d幕/g) || []).length, box: [r.left, r.top, r.right, r.bottom]}; }"""

u, server = serve_local()
fails = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for w, h in ((1916, 857), (1440, 900), (1100, 760), (830, 800), (390, 844)):
            for fs in ('m', 'x'):
                for dw in ('n', 'w'):
                    p = b.new_page(viewport={'width': w, 'height': h}, reduced_motion='reduce')
                    p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
                    open_tech(p, u)
                    p.evaluate("([fs,dw])=>{document.documentElement.dataset.fs=fs;document.documentElement.dataset.w=dw;}", [fs, dw])
                    p.wait_for_timeout(400)
                    m = p.evaluate(MEASURE)
                    tag = f'{w}x{h} fs={fs} w={dw}'
                    if not m['collapsed']:
                        fails.append(f'{tag}: not collapsed by default')
                    if m['acts'] or m['actButtons']:
                        fails.append(f'{tag}: still shows act buttons')
                    if m['rail'][0] < m['vw'] * .6 or m['rail'][4] > 90:
                        fails.append(f"{tag}: not a slim handle on the right {m['rail']}")
                    if m['rail'][0] < m['content'][1] + 2:
                        fails.append(f"{tag}: covers content (rail left {m['rail'][0]:.0f} < content right {m['content'][1]:.0f})")
                    if m['rail'][1] > m['vw'] or m['rail'][3] > m['vh'] or m['rail'][2] < 60:
                        fails.append(f"{tag}: handle outside the screen {m['rail']}")
                    p.locator('.story-rail-handle').hover(); p.wait_for_timeout(250)
                    o = p.evaluate(PANEL)
                    if o['hidden'] or o['chapters'] != 18 or o['top'] != 1 or o['heads'] != 4 or o['acts']:
                        fails.append(f'{tag}: hover window {o}')
                    if o['box'][0] < 0 or o['box'][1] < 0 or o['box'][3] > h + 1:
                        fails.append(f"{tag}: window off screen {o['box']}")
                    p.mouse.move(60, 30); p.wait_for_timeout(500)          # 頂端列：一定在視窗外
                    if not p.evaluate("()=>document.querySelector('.story-rail-panel').hidden"):
                        fails.append(f'{tag}: window stays open after the mouse leaves')
                    p.close()
            print('checked', w, h, flush=True)
        # 觸控：點一下展開，點章節跳過去並收起；點外面收起
        ctx = b.new_context(viewport={'width': 390, 'height': 844}, has_touch=True, is_mobile=True, reduced_motion='reduce')
        p = ctx.new_page()
        p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
        open_tech(p, u)
        p.locator('.story-rail-handle').tap(); p.wait_for_timeout(200)
        if p.evaluate("()=>document.querySelector('.story-rail-panel').hidden"):
            fails.append('touch: tap did not open the window')
        y0 = p.evaluate('scrollY')
        p.locator('.story-rail-panel a[data-story-go]').nth(10).tap(); p.wait_for_timeout(900)
        if not p.evaluate("()=>document.querySelector('.story-rail-panel').hidden") or p.evaluate('scrollY') <= y0:
            fails.append('touch: chapter tap did not jump and close')
        p.locator('.story-rail-handle').tap(); p.wait_for_timeout(200)
        p.touchscreen.tap(60, 30); p.wait_for_timeout(200)       # 頂端列的站名：在視窗外
        if not p.evaluate("()=>document.querySelector('.story-rail-panel').hidden"):
            fails.append('touch: tapping outside did not close')
        ctx.close()
        b.close()
finally:
    server.shutdown()
if fails:
    raise SystemExit('\n'.join(fails))
print('table of contents: right side at every width, collapsed handle, no act buttons, no overlap, full chapters on hover/tap, closes on leave/outside')
