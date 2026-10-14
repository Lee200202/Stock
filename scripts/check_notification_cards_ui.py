"""Offline render acceptance for unsent Email and site mail cards."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1];out=ROOT/'scratch/ui180';css=(ROOT/'public-site/gas-source/Stylesheet.html').read_text(encoding='utf8')
with sync_playwright() as pw:
 browser=pw.chromium.launch()
 for width in [1440,768,390,360]:
  for theme in ['light','dark']:
   p=browser.new_page(viewport={'width':width,'height':1000})
   for fixture in ['mail','private']:
    body=(out/(fixture+'-fixture.html')).read_text(encoding='utf8')
    if theme=='dark':body='<main class="mail-content">'+body+'</main>'
    p.set_content('<!doctype html><html data-theme="'+theme+'"><head><meta charset="utf-8">'+(css if theme=='dark' else '')+'</head><body>'+body+'</body></html>')
    p.wait_for_timeout(100)
    assert p.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),(width,theme,fixture)
    if fixture=='mail':
     cards=p.locator('.mc-stock-card');assert cards.count()==2
     a=cards.nth(0).bounding_box();b=cards.nth(1).bounding_box();assert b['y']-a['y']-a['height']>=15,(width,theme,a,b)
     assert p.locator('.mc-desc').first.evaluate('(e)=>parseFloat(getComputedStyle(e).fontSize)')>=15.5
    else:
     assert p.locator('a').first.bounding_box()['height']>=50
    if width in [1440,390]:p.screenshot(path=str(out/f'{fixture}-{width}-{theme}.png'))
   p.close()
  print('notification cards',width,'two themes: full content, no overflow, separated cards, readable body and large controls')
 browser.close()
