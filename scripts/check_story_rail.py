"""四幕導覽（子目錄）在右邊：任何版面寬度與字級都不蓋住內容、標籤不換行、不超出畫面；滑過去跳出完整章節視窗（四幕十八章），移開收起；窄螢幕維持底部列。"""
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

MEASURE = """() => {
  const rail = document.querySelector('.story-rail'), r = rail.getBoundingClientRect();
  const root = document.querySelector('.tech-story'), c = root.getBoundingClientRect();
  const wraps = [...rail.querySelectorAll('button')].filter(b => {
    const range = document.createRange(); range.selectNodeContents(b);
    const tops = new Set([...range.getClientRects()].filter(x => x.width > 2).map(x => Math.round(x.top)));
    return tops.size > 2;                       // 圖示一行、文字一行；多於兩行就是文字換行
  }).map(b => b.textContent);
  return {rail: [r.left, r.right, r.top, r.bottom], content: [c.left, c.right], vw: document.documentElement.clientWidth, vh: innerHeight, wraps,
          bottomBar: r.width > innerWidth * .8};
}"""

u, server = serve_local()
fails = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for w, h in ((1916, 857), (1440, 900), (1280, 800), (1100, 760)):
            for fs in ('m', 'x'):
                for dw in ('n', 'm', 'w', 'x'):
                    p = b.new_page(viewport={'width': w, 'height': h}, reduced_motion='reduce')
                    p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
                    open_tech(p, u)
                    p.evaluate("([fs,dw])=>{document.documentElement.dataset.fs=fs;document.documentElement.dataset.w=dw;}", [fs, dw])
                    p.wait_for_timeout(400)
                    m = p.evaluate(MEASURE)
                    tag = f'{w}x{h} fs={fs} w={dw}'
                    if m['bottomBar']:
                        fails.append(f'{tag}: rail is a bottom bar'); p.close(); continue
                    if m['rail'][0] < m['content'][1] + 4:
                        fails.append(f"{tag}: rail overlaps content (rail left {m['rail'][0]:.0f} < content right {m['content'][1]:.0f})")
                    if m['wraps']:
                        fails.append(f"{tag}: labels wrap {m['wraps']}")
                    if m['rail'][1] > m['vw'] or m['rail'][3] > m['vh'] or m['rail'][2] < 100:
                        fails.append(f"{tag}: rail outside the screen {m['rail']}")
                    # 滑過去：完整章節視窗
                    p.locator('.story-rail [data-story-act="2"]').hover(); p.wait_for_timeout(250)
                    panel = p.evaluate("()=>{const e=document.querySelector('.story-rail-panel'),r=e.getBoundingClientRect();return {hidden:e.hidden,links:e.querySelectorAll('a').length,acts:e.querySelectorAll('.story-rail-act').length,hot:(e.querySelector('.story-rail-act.is-hot')||{}).textContent||'',left:r.left}}")
                    if panel['hidden'] or panel['links'] != 18 or panel['acts'] != 4 or not panel['hot'].startswith('3幕') or panel['left'] < 0:
                        fails.append(f'{tag}: hover window {panel}')
                    p.mouse.move(40, h - 40); p.wait_for_timeout(500)
                    if not p.evaluate("()=>document.querySelector('.story-rail-panel').hidden"):
                        fails.append(f'{tag}: window stays open after the mouse leaves')
                    p.close()
            print('checked', w, h, flush=True)
        # 窄螢幕：底部列，點一幕列出那一幕
        p = b.new_page(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
        open_tech(p, u)
        m = p.evaluate(MEASURE)
        if not m['bottomBar']:
            fails.append('390: rail is not a bottom bar')
        p.locator('.story-rail [data-story-act="1"]').click(); p.wait_for_timeout(200)
        n = p.evaluate("()=>document.querySelector('.story-rail-panel').querySelectorAll('a').length")
        if not 1 <= n < 18:
            fails.append(f'390: act panel lists {n} links')
        b.close()
finally:
    server.shutdown()
if fails:
    raise SystemExit('\n'.join(fails))
print('story rail: right side, no overlap, no wrapping, on screen, full chapter window on hover, bottom bar on phones')
