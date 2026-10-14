"""Autoplay stays local, stops on interaction/offscreen, repeats a holding round, respects motion preference."""
import json
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local,open_tech,FAKE_API
u,server=serve_local()
try:
 with sync_playwright() as pw:
  b=pw.chromium.launch()
  for width in [1440,390]:
   p=b.new_page(viewport={'width':width,'height':900},reduced_motion='no-preference');p.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"result":[]}'));open_tech(p,u)
   p.locator('[data-story-mode="short"]').click();p.locator('#tech-subject .story-main').scroll_into_view_if_needed();p.wait_for_function("document.querySelector('#tech-subject .story-main').dataset.playing==='true'")
   phase=p.locator('#tech-subject .story-main').get_attribute('data-phase');p.wait_for_timeout(1900);assert p.locator('#tech-subject .story-main').get_attribute('data-phase')!=phase
   p.locator('#tech-subject [data-story-choice="1"]').click();phase=p.locator('#tech-subject .story-main').get_attribute('data-phase');p.wait_for_timeout(1800);assert p.locator('#tech-subject .story-main').get_attribute('data-phase')==phase
   p.locator('#tech-hold .story-main').scroll_into_view_if_needed();p.wait_for_function("document.querySelector('#tlPlay').getAttribute('aria-pressed')==='true'")
   values=set()
   for _ in range(7):values.add(p.locator('#tlSlider').input_value());p.wait_for_timeout(1500)
   assert len(values)==6,values
   p.locator('#tech-hold .story-chapter-toggle').click();p.locator('#tlPlay').click();v=p.locator('#tlSlider').input_value();p.wait_for_timeout(1800);assert p.locator('#tlSlider').input_value()==v
   p.locator('#tech-limit .story-main').scroll_into_view_if_needed();p.locator('#tech-hold .story-main').scroll_into_view_if_needed();p.wait_for_timeout(1800);assert p.locator('#tlSlider').input_value()==v
   p.locator('#tech-hold .story-replay').click();p.wait_for_function("document.querySelector('#tlPlay').getAttribute('aria-pressed')==='true'");p.wait_for_timeout(10500);assert p.locator('#tlPlay').get_attribute('aria-pressed')=='true';assert p.locator('#tech-hold .story-main').get_attribute('data-playing')=='true'
   p.locator('button[data-tab="overview"]').click();v=p.locator('#tlSlider').input_value();p.wait_for_timeout(1800);assert p.locator('#tlSlider').input_value()==v
   print('autoplay',width,'six steps loop, click freezes, return stays stopped, explicit replay, tab pause passed');p.close()
  p=b.new_page(viewport={'width':1280,'height':900},reduced_motion='no-preference');calls=[];p.on('request',lambda r:calls.append(r.url) if '/api' in r.url else None);p.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"result":[]}'));open_tech(p,u);p.locator('[data-story-mode="short"]').click();p.wait_for_load_state('networkidle');base=len(calls)
  for i in range(18):
   print('checking autoplay',i,flush=True);main=p.locator('.story-main').nth(i);main.scroll_into_view_if_needed();p.wait_for_function("i=>document.querySelectorAll('.story-main')[i].dataset.playing==='true'",arg=i)
   old=main.get_attribute('data-phase') if i!=12 else p.locator('#tlSlider').input_value();p.wait_for_timeout(1900)
   new=main.get_attribute('data-phase') if i!=12 else p.locator('#tlSlider').input_value();assert new!=old,(i,old,new)
  assert len(calls)==base,(base,calls);p.close();print('all 18 main diagrams autoplay locally, no new API requests')
  p=b.new_page(reduced_motion='reduce');p.route(FAKE_API,lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"result":[]}'));open_tech(p,u);p.locator('#tlTrack').scroll_into_view_if_needed();p.wait_for_timeout(1800);assert p.locator('#tlPlay').get_attribute('aria-pressed')=='false';assert p.locator('#tlSlider').input_value()=='0';b.close()
finally:server.shutdown()
