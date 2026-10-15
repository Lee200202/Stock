"""Local acceptance: six source definitions, voting diagrams, motion and no-JS fallback."""
import json
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local,open_tech,FAKE_API
url,server=serve_local()
try:
 with sync_playwright() as pw:
  browser=pw.chromium.launch()
  for width in (1440,1280,768,390,360):
   for theme in ('light','dark'):
    p=browser.new_page(viewport={'width':width,'height':900},reduced_motion='reduce',color_scheme=theme)
    p.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"result":[]}'))
    p.add_init_script("localStorage.setItem('theme', '"+theme+"')")
    open_tech(p,url);lab=p.locator('#classDefinitions');lab.scroll_into_view_if_needed()
    assert lab.is_visible()
    assert p.locator('html').get_attribute('data-theme')==theme
    for i in range(6):
     button=lab.locator('.cd-select button').nth(i);button.click()
     assert button.get_attribute('aria-pressed')=='true'
     assert lab.locator('.cd-panel:visible').count()==1
     assert lab.locator('.cd-panel:visible .cd-flow li').count()==4
     assert lab.locator('.cd-panel:visible').inner_text().strip()
    for i,count in enumerate((1,2,3,3,1)):
     lab.locator('.cd-vote-select button').nth(i).click()
     assert lab.locator('.cd-ballot').count()==count
    assert not p.evaluate('document.documentElement.scrollWidth > innerWidth')
    assert all(b['width']>=44 and b['height']>=44 for b in lab.locator('button:visible').evaluate_all('(es)=>es.map(e=>{const r=e.getBoundingClientRect();return {width:r.width,height:r.height}})'))
    lab.locator('.cd-select button').first.focus();p.keyboard.press('Enter');assert lab.locator('.cd-select button').first.get_attribute('aria-pressed')=='true'
    print(width,theme,'six definitions, five voting cases, keyboard and layout passed');p.close()
  p=browser.new_page(viewport={'width':768,'height':900},reduced_motion='no-preference');calls=[]
  p.route(FAKE_API,lambda r:(calls.append(r.request.post_data),r.fulfill(content_type='application/json',body='{"ok":true,"result":[]}'))[-1])
  open_tech(p,url);lab=p.locator('#classDefinitions');lab.scroll_into_view_if_needed();p.wait_for_function("document.querySelector('#classDefinitions').dataset.playing==='true'")
  step=lab.get_attribute('data-step');p.wait_for_timeout(1400);assert step!=lab.get_attribute('data-step')
  lab.locator('.cd-select button').nth(4).click();step=lab.get_attribute('data-step');p.wait_for_timeout(1400);assert step==lab.get_attribute('data-step')
  lab.locator('.cd-replay').click();p.wait_for_function("document.querySelector('#classDefinitions').dataset.playing==='true'")
  before=len(calls);p.wait_for_timeout(5200);assert lab.get_attribute('data-playing')=='true';assert len(calls)==before
  p.emulate_media(reduced_motion='reduce');p.wait_for_timeout(150);assert lab.get_attribute('data-playing')=='false';p.close()
  p=browser.new_page(java_script_enabled=False);p.goto(url);assert p.locator('[data-class-definition]').count()==6;assert '3 票一致' in p.locator('.cd-votes').inner_text();p.close()
  print('Autoplay loops; selection stops; replay resumes; reduced motion stops; zero extra API; no-JS rules readable.');browser.close()
finally:server.shutdown()
