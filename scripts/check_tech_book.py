"""v184 chapter reader acceptance: real controls, content identity, branches, motion, layout.
Usage: python scripts/check_tech_book.py [--url URL] [--quick]
Local build uses a fake API. Live mode performs read-only UI interactions.
"""
import argparse, json, os, sys, threading, tempfile, subprocess
from pathlib import Path
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.stdout.reconfigure(encoding='utf-8')
args=argparse.ArgumentParser();args.add_argument('--url');args.add_argument('--quick',action='store_true');options=args.parse_args()
out=ROOT/'scratch/tech-book-acceptance';out.mkdir(parents=True,exist_ok=True)
source=(ROOT/'public-site/gas-source/Tech.html').read_text(encoding='utf-8');soup=BeautifulSoup(source,'html.parser')
inventory=json.loads((ROOT/'docs/1003-review/tech-redesign-20261016/tech-content-inventory.json').read_text(encoding='utf-8'))
counts=Counter(n['data-content-id'] for n in soup.select('[data-content-id]'))
assert all(counts[item['id']]==1 for item in inventory['items'])
assert len(soup.select('[data-chapter]'))==20 and not soup.select('details')
ids=[n['id'] for n in soup.select('[id]')];assert len(ids)==len(set(ids)),[i for i,n in Counter(ids).items() if n>1]
server=None
if options.url:url=options.url
else:
 build=out/'site';env=dict(os.environ,SITE_API_URL='https://book-test.example.workers.dev/api')
 subprocess.run([sys.executable,str(ROOT/'scripts/build_original_site.py'),'--output',str(build)],env=env,check=True,stdout=subprocess.DEVNULL)
 class Quiet(SimpleHTTPRequestHandler):
  def log_message(self,*args):pass
 server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(build)));threading.Thread(target=server.serve_forever,daemon=True).start();url=f'http://127.0.0.1:{server.server_port}/?tab=tech'
results=[]
with sync_playwright() as p:
 browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 if not options.url:
  page.route('https://book-test.example.workers.dev/**',lambda r:r.fulfill(content_type='application/json',body=json.dumps({'ok':True,'items':[],'held':[],'latestDate':'2026/10/16','asOf':'2026/10/16 12:00'})))
 page.goto(url,wait_until='domcontentloaded');page.wait_for_function("document.querySelector('.tech-book')?.dataset.bookReady==='true'",timeout=30000)
 assert not errors,errors
 chapter_ids=page.locator('[data-chapter]').evaluate_all('(els)=>els.map(e=>e.id)')
 def go(id):
  page.locator('.book-tools [data-open-contents]').click();page.locator('#bookContents a[href="#'+id+'"]').click();assert page.locator('[data-chapter]:visible').count()==1
 # The original six-stage graph remains interactive, not a flattened screenshot.
 go('tech-overview');assert page.locator('#techFlow [data-flow]').count()==6
 assert page.locator('#techCanvas [data-entity]').count()>=30
 flow_results=set()
 for button in page.locator('#techFlow [data-flow]').all():
  button.click();flow_results.add(page.locator('#techFlowDetail').inner_text())
 assert len(flow_results)==6
 page.locator('#techCanvas [data-entity="video"]').click();assert page.locator('#techEntityDetail').is_visible()
 page.locator('#techEntityDetail .tg-x').click()
 go('tech-day');page.locator('[data-go="video"]').first.click()
 assert page.locator('.tech-book').get_attribute('data-active-chapter')=='tech-overview'
 assert page.locator('#techEntityDetail').is_visible()
 page.locator('#techEntityDetail .tg-x').click()
 for size in ['l','x']:
  page.set_viewport_size({'width':1916,'height':857});page.evaluate('(s)=>{document.documentElement.dataset.w="w";document.documentElement.dataset.fs=s}',size)
  for id in chapter_ids:
   go(id);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(size,id)
 page.evaluate('()=>{delete document.documentElement.dataset.w;delete document.documentElement.dataset.fs}')
 for width in ([1440,390] if options.quick else [1916,1440,768,390,360]):
  page.set_viewport_size({'width':width,'height':1000})
  for theme in ['light','dark']:
   page.evaluate('(t)=>{document.documentElement.dataset.theme=t}',theme)
   for id in chapter_ids:
    go(id)
    metric=page.evaluate("""()=>({width:innerWidth,page:document.querySelector('.tech-book').dataset.activeChapter,overflow:document.documentElement.scrollWidth>innerWidth,heading:document.querySelector('[data-chapter]:not([hidden]) h2').getBoundingClientRect().width})""")
    metric['theme']=theme;results.append(metric)
    assert not metric['overflow'],metric
    assert metric['heading']>200,metric
   print('layout',width,theme,'20 chapters PASS',flush=True)
 # All retained case controls must produce a distinct visible result.
 go('tech-transcript-progress');seen=set()
 for key in ['newer','same','cancel','pause','m503','trunc','gap','lock']:
  page.locator('[data-tx="'+key+'"]').click();seen.add(page.locator('#txStages').inner_text()+page.locator('#txOut').inner_text())
 assert len(seen)==8
 go('tech-public-notes');seen=set()
 for n in range(7):page.locator('[data-pn="'+str(n)+'"]').click();seen.add(page.locator('#pnDesk').inner_text())
 assert len(seen)==7
 go('tech-unnamed');seen=set()
 for key in ['one','two','none']:page.locator('[data-un="'+key+'"]').click();seen.add(page.locator('#unRange').inner_text())
 assert len(seen)==3
 go('tech-loading')
 for key,n in [('recent',2),('quiet',2),('rare',3),('stale',3)]:
  page.locator('[data-load-case="'+key+'"]').click();assert page.locator('#tech-loading .book-stage').get_attribute('data-playing')=='false';assert page.locator('#tech-loading .book-art [data-book-path]').evaluate_all('(els)=>els.filter(e=>e.style.opacity==="1").length')==n
 # Modal, native Escape and opener focus.
 page.locator('#tech-loading .book-visual-open').click();page.wait_for_function("document.querySelector('#bookArtImage').naturalWidth>0");assert page.locator('#bookArtDialog').is_visible();page.screenshot(path=str(out/'dialog.png'));page.keyboard.press('Escape');assert page.locator('#bookArtDialog').is_hidden();assert page.evaluate("document.activeElement.classList.contains('book-visual-open')")
 # Replay means a loop, not a one-shot. Scroll to the stage before checking eligibility.
 page.locator('#tech-loading [data-book-replay]').click();page.locator('#tech-loading .book-stage').scroll_into_view_if_needed();page.wait_for_function("document.querySelector('#tech-loading .book-stage').dataset.playing==='true'")
 page.wait_for_function("Number(document.querySelector('#tech-loading .book-stage').dataset.cycles)>=2",timeout=22000)
 page.emulate_media(reduced_motion='reduce');page.wait_for_function("document.querySelector('#tech-loading .book-stage').dataset.playing==='false'");page.emulate_media(reduced_motion='no-preference')
 # Deep links and refresh preserve the active chapter.
 page.goto(url.split('#')[0]+'#toc-10',wait_until='domcontentloaded');page.wait_for_function("document.querySelector('.tech-book')?.dataset.activeChapter==='tech-transcript-progress'");page.reload();page.wait_for_function("document.querySelector('.tech-book')?.dataset.activeChapter==='tech-transcript-progress'")
 # Print reveals all chapters without changing screen routing.
 page.emulate_media(media='print');assert page.locator('[data-chapter]:visible').count()==20;page.emulate_media(media='screen')
 # Search includes inactive chapters.
 page.locator('#bookSearch').fill('快取');assert page.locator('#bookSearchResults a').count()>0;page.locator('#bookSearch').fill('')
 for width in [1916,390]:
  page.set_viewport_size({'width':width,'height':857});go('tech-loading');page.locator('#tech-loading .book-stage').scroll_into_view_if_needed();page.screenshot(path=str(out/f'loading-{width}.png'))
 assert not errors,errors
 browser.close()
 # Actual no-JavaScript rendering, not a script flag.
 browser=p.chromium.launch();ctx=browser.new_context(java_script_enabled=False);nojs=ctx.new_page();nojs.goto(url,wait_until='domcontentloaded')
 assert nojs.locator('[data-chapter]').count()==20
 assert nojs.locator('.tech-book').inner_text().count('SHA-256')>0
 browser.close()
(out/'results.json').write_text(json.dumps({'url':url,'layouts':results,'contentItems':len(inventory['items']),'errors':errors,'result':'PASS'},ensure_ascii=False,indent=2),encoding='utf-8')
if server:server.shutdown()
print('PASS:',len(results),'layouts;',len(inventory['items']),'content items; cases, branches, replay, modal, refresh, print, reduced motion, no-JS')
