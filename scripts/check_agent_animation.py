"""Verify finite user-triggered motion and compare displayed usage with the log report."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

usage=json.loads(Path('docs/1003-review/2026-10-13-agent-usage.json').read_text(encoding='utf-8'))
assert usage['calls']==len(usage['rows'])==19
assert usage['input']==sum(r['input'] for r in usage['rows'])==179291
assert usage['output']==sum(r['output'] for r in usage['rows'])==26113
assert round(usage['maxInput']/1048576*100,2)==3.08
url,server=serve_local()
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='no-preference')
        page.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body=json.dumps({'ok':True,'result':[]})))
        open_tech(page,url)
        page.locator('#tech-agent').scroll_into_view_if_needed()
        assert '179,291' in page.locator('#tech-agent').inner_text()
        # 2026/10/13 管理者要求這一章全版：三層分工圖用滿這一章的欄寬（先前是不超過 960px）。
        assert page.locator('.agent-map').bounding_box()['width']>=page.locator('#tech-agent').bounding_box()['width']-2
        page.locator('#agentMapPlay').click()
        assert page.locator('#agentMapPlay').is_disabled()
        page.wait_for_function("document.querySelector('#agentNodeDetail').textContent.includes('核對完成')")
        assert page.locator('#agentMapPlay').is_enabled()
        page.locator('#agentMapPlay').click()
        page.locator('[data-agent-node="tool"]').click()
        assert page.locator('#agentMapPlay').is_enabled()
        assert page.locator('[data-agent-node="tool"]').get_attribute('aria-pressed')=='true'
        page.locator('[data-agent="fault"]').click()
        assert page.locator('#agentRoute').evaluate("e=>e.classList.contains('agent-run')")
        assert page.locator('#agentRoute li').first.evaluate("e=>getComputedStyle(e).animationName")=='agentPulse'
        browser.close()
finally:
    server.shutdown()
print('Finite animation/replay/cancel, compact map and source usage totals passed')
