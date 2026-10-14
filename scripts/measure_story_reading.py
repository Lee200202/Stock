"""Offline digest/full visible heights; real chart data is not fabricated."""
import json
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local,FAKE_API

url,server=serve_local()
try:
    with sync_playwright() as pw:
        b=pw.chromium.launch();p=b.new_page(viewport={'width':1600,'height':900},reduced_motion='reduce')
        p.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body=json.dumps({'ok':True,'result':[]})))
        p.goto(url);p.locator('button[data-tab="tech"]').click();p.wait_for_selector('.story-main')
        sizes={}
        for mode in ('short','full'):
            p.locator(f'button[data-story-mode="{mode}"]').click()
            for main in p.locator('.story-main').all():
                main.scroll_into_view_if_needed();main.locator('svg').wait_for(state='visible')
            for section in p.locator('.story-section').all():
                section.scroll_into_view_if_needed()
            p.locator('.story-section').evaluate_all("es=>es.forEach(e=>e.style.contentVisibility='visible')")
            p.wait_for_timeout(100)
            sizes[mode]=p.locator('.tech-story').bounding_box()['height']
            if mode=='short':print(p.locator('.story-main').nth(1).evaluate("e=>[...e.children].map(c=>({tag:c.className,h:c.getBoundingClientRect().height,font:getComputedStyle(c).fontSize,margin:getComputedStyle(c).margin, pad:getComputedStyle(c).padding}))"))
            print(mode,p.locator('.story-section').evaluate_all("es=>es.map(e=>({id:e.id,h:Math.round(e.getBoundingClientRect().height), main:Math.round(e.querySelector('.story-main').getBoundingClientRect().height),svg:e.querySelector('.story-visual svg')?.getAttribute('viewBox')}))"))
        print(json.dumps(sizes));b.close()
finally:server.shutdown()
