"""Local read-only UI checks for the agent principles chapter."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

url, server = serve_local()
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width in (1440, 1024, 768, 390, 360):
            for theme in ('light', 'dark'):
                context = browser.new_context(viewport={'width':width,'height':900}, color_scheme=theme,
                                              reduced_motion='reduce')
                page = context.new_page()
                page.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body=json.dumps({'ok':True,'result':[]})))
                open_tech(page, url)
                page.locator('#tech-agent').scroll_into_view_if_needed()
                assert page.evaluate("[...document.querySelectorAll('.tech-story h2')].every(h => h.dataset.toc && h.dataset.toc.length >= 6 && h.dataset.toc.length <= 10)")
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                requests = []
                page.on('request', lambda r: requests.append(r.url))
                values = []
                for key in ('context','tokens','infer','tools','fault','swap','context'):
                    button = page.locator(f'[data-agent="{key}"]')
                    button.click()
                    assert button.get_attribute('aria-pressed') == 'true'
                    assert page.locator('[data-agent][aria-pressed="true"]').count() == 1
                    values.append(page.locator('#agentOutput').inner_text())
                    assert page.locator('#agentRoute li').count() == 4
                    assert button.bounding_box()['height'] >= 44
                assert len(set(values)) == 6
                for key, token in [('lookup','10/06'),('overload','503'),('live','11:09')]:
                    page.locator(f'[data-agent-real="{key}"]').click()
                    assert token in page.locator('#agentRealRoute').inner_text()
                    assert page.locator('[data-agent-real][aria-pressed="true"]').count() == 1
                page.locator('[data-budget="full"]').click()
                assert '130／100' in page.locator('#agentBudgetNote').inner_text()
                page.locator('[data-budget="fit"]').press('Enter')
                assert '90／100' in page.locator('#agentBudgetNote').inner_text()
                assert not [u for u in requests if '/assets/' not in u], requests
                assert not errors, errors
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (width,theme)
                if width == 390 and theme == 'light':
                    page.screenshot(path=str(Path('scratch/audit-1013/agent-mobile.png')))
                print(f'agent chapter: {width}px {theme}, six scenarios/budget/keyboard/no network passed')
                context.close()
        context = browser.new_context(java_script_enabled=False)
        page=context.new_page(); page.goto(url)
        assert 'Context window' in page.locator('#tech-agent').inner_text()
        assert '停止上限' in page.locator('#tech-agent').inner_text()
        context.close(); browser.close()
finally:
    server.shutdown()
