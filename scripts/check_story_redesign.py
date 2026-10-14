"""v177: digest/full, every main diagram, anchors, reader rail and no backend writes."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, FAKE_API

url,server=serve_local()
out=Path('scratch/story177');out.mkdir(parents=True,exist_ok=True)
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        for width in (1600,1440,768,390,360):
            for theme in ('light','dark'):
                ctx=browser.new_context(viewport={'width':width,'height':900},color_scheme=theme,reduced_motion='reduce')
                page=ctx.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body=json.dumps({'ok':True,'result':[]})))
                page.goto(url,wait_until='domcontentloaded');page.locator('button[data-tab="tech"]').click()
                page.wait_for_selector('.story-main')
                assert page.locator('.story-main').count()==18
                assert page.locator('.story-details[open]').count()==0
                assert page.locator('[data-story-mode="short"]').get_attribute('aria-pressed')=='true'
                for i in range(18):
                    main=page.locator('.story-main').nth(i);main.scroll_into_view_if_needed()
                    main.locator('.story-visual svg').wait_for(state='visible')
                    assert main.locator('.story-visual svg').get_attribute('aria-label')
                    choices=main.locator('[data-story-choice]')
                    if choices.count()>1:
                        choices.nth(1).click()
                        assert choices.nth(1).get_attribute('aria-pressed')=='true'
                    assert main.evaluate("e=>getComputedStyle(e.querySelector('.viz-part')||e).animationName==='none'")
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,i)
                page.locator('[data-story-mode="full"]').click()
                assert page.locator('.story-details[open]').count()==18
                page.locator('[data-story-mode="short"]').click()
                page.locator('.story-rail-handle').click()
                assert page.locator('.story-rail-panel').is_visible()
                page.locator('.story-rail-panel a').first.click()
                assert not page.locator('.story-rail-panel').is_visible()
                assert page.locator('#toc-4').count()==1
                if width==390:
                    page.locator('.story-main').nth(5).scroll_into_view_if_needed()
                    page.screenshot(path=str(out/f'phone-{theme}.png'))
                if width==1600:
                    page.locator('.story-mode-controls').scroll_into_view_if_needed()
                    page.screenshot(path=str(out/f'desktop-{theme}.png'))
                assert not errors,errors
                print('story redesign',width,theme,'18 diagrams, modes, rail, anchors, reduced motion OK')
                ctx.close()
        ctx=browser.new_context(java_script_enabled=False);page=ctx.new_page();page.goto(url)
        assert page.locator('#tech-agent').inner_text()
        assert page.locator('#tech-transcript-progress').inner_text()
        assert page.locator('.story-details').count()==0
        ctx.close();browser.close()
finally:server.shutdown()
