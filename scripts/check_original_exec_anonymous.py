"""在未登入Google的新瀏覽器環境，唯讀驗收原Web App。"""
from pathlib import Path
import json
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/1003-review/2026-10-19-mobile-pan-skills-eval'
URL='https://script.google.com/macros/s/AKfycbyLQjAd-CnQ6D6_JP3OY1WwXDkafCJthzeP3G4FDkT4t26PY9Gx1rhrXJjiHVZExQhoaw/exec?tab=tech#tech-quality-loop'
with sync_playwright() as pw:
    browser=pw.chromium.launch()
    ctx=browser.new_context(viewport={'width':1440,'height':1000},reduced_motion='reduce')
    page=ctx.new_page()
    page.goto(URL,wait_until='domcontentloaded',timeout=60000)
    # HtmlService會在正常的沙箱iframe內呈現應用程式。
    app=None
    for attempt in range(30):
        for frame in page.frames:
            if frame.locator('.tech-book').count():app=frame;break
        if app:break
        page.wait_for_timeout(500)
    if not app:raise AssertionError('Original app missing in anonymous context: '+page.title())
    app.wait_for_function("document.querySelector('.tech-book')?.dataset.bookReady==='true'")
    app.locator('.book-tools [data-open-contents]').click()
    app.locator('#bookContents a[href="#tech-quality-loop"]').click()
    ch=app.locator('#tech-quality-loop')
    assert ch.is_visible()
    assert ch.locator('[data-quality-node]').count()==7
    assert app.locator('[data-chapter]').count()==20
    assert app.locator('.tech-book').get_attribute('data-tech-book')=='v192r1'
    ch.locator('[data-quality-node="2"]').click()
    assert ch.locator('#skillsEntityDetail').is_visible()
    assert '上游依據' in ch.locator('#skillsEntityDetail').inner_text()
    assert ch.locator('[data-quality-copy="2"]').is_visible()
    ch.locator('#skillsGraph').scroll_into_view_if_needed()
    page.screenshot(path=str(OUT/'原WebApp匿名桌面.png'))
    page.set_viewport_size({'width':390,'height':844})
    ch.locator('[data-quality-node="2"]').click()
    assert ch.locator('#skillsScroll').evaluate('(e)=>e.scrollWidth>e.clientWidth')
    assert app.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert 'is-panel' in ch.locator('#skillsEntityDetail').get_attribute('class')
    ch.locator('#skillsScroll').scroll_into_view_if_needed()
    page.screenshot(path=str(OUT/'原WebApp匿名手機.png'))
    result={'passed':True,'context':'fresh non-persistent Chromium context; no imported cookies/login',
            'build':'v192r1','widths':[1440,390],'chapters':20,'nodes':7,
            'originalDeployment':497,'frameHosts':sorted(set(urlsplit(f.url).hostname or '' for f in page.frames))}
    (OUT/'原WebApp匿名驗收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    browser.close()
print('PASS: original /exec renders anonymously; desktop/mobile, same graph, selection, details, horizontal pan')
