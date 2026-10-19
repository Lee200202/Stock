"""Capture every technical illustration on mobile, plus native touch/keyboard pan."""
import argparse,json,os,subprocess,sys,threading,math
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
a=argparse.ArgumentParser();a.add_argument('--url');opt=a.parse_args()
out=ROOT/'scratch/mobile-pan-20261019/qa';out.mkdir(parents=True,exist_ok=True)
server=None
if opt.url:url=opt.url
else:
 build=out/'site';subprocess.run([sys.executable,str(ROOT/'scripts/build_original_site.py'),'--output',str(build)],env=dict(os.environ,SITE_API_URL='https://pan-test.example.workers.dev/api'),check=True,stdout=subprocess.DEVNULL)
 class Quiet(SimpleHTTPRequestHandler):
  def log_message(self,*a):pass
 server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(build)));threading.Thread(target=server.serve_forever,daemon=True).start()
 url=f'http://127.0.0.1:{server.server_port}/?tab=tech'
proof=[];errors=[]
with sync_playwright() as pw:
 b=pw.chromium.launch();ctx=b.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,reduced_motion='reduce');page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
 if not opt.url:page.route('https://pan-test.example.workers.dev/**',lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"items":[],"held":[]}'))
 page.goto(url,wait_until='domcontentloaded');page.wait_for_function("document.querySelector('.tech-book')?.dataset.bookReady==='true'")
 ids=page.locator('[data-chapter]').evaluate_all('(a)=>a.map(x=>x.id)')
 def go(id):
  page.locator('.book-tools [data-open-contents]').click();page.locator('#bookContents a[href="#'+id+'"]').click()
 def capture(pan,label,id,j):
  metric=pan.evaluate('(e)=>({client:e.clientWidth,scroll:e.scrollWidth})');count=max(3,math.ceil(metric['scroll']/metric['client'])+1);files=[];offsets=[]
  for i in range(count):
   left=round((metric['scroll']-metric['client'])*i/(count-1));pan.evaluate('(e,x)=>e.scrollLeft=x',left)
   file=f'{label}-part{i+1:02}.png';pan.screenshot(path=str(out/file),style='#toTop,#asstFab,.book-side-nav{visibility:hidden!important}');files.append(file);offsets.append(left)
  proof.append(dict(chapter=id,diagram=j,**metric,files=files,offsets=offsets))
 for n,id in enumerate(ids):
  go(id);ch=page.locator('#'+id)
  for width in (360,390,768,1440):
   page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(id,width)
  page.set_viewport_size({'width':390,'height':844})
  pans=ch.locator('.tech-art-pan')
  for j,pan in enumerate(pans.all()):
   if not pan.is_visible():continue
   pan.scroll_into_view_if_needed()
   # Text stays within the complete drawing; pan clips the viewport intentionally.
   for svg in pan.locator('svg.book-art,svg.evidence-svg:visible').all():
    bad=svg.evaluate("""s=>{const r=s.getBoundingClientRect();return [...s.querySelectorAll('text')].filter(t=>{const b=t.getBoundingClientRect();return b.width&& (b.left<r.left-2||b.right>r.right+2||b.top<r.top-2||b.bottom>r.bottom+2)}).map(t=>t.textContent)}""")
    assert not bad,(id,j,bad)
   metric=pan.evaluate('(e)=>({client:e.clientWidth,scroll:e.scrollWidth})');assert metric['scroll']>metric['client'],(id,j,metric)
   label=f'{n+1:02}-{id}-{j+1}'
   capture(pan,label,id,j+1)
 # Additional interactive HTML diagrams retain their full readable geometry.
 go('tech-agent');page.locator('[data-agent="tokens"]').click();tok=page.locator('#tokViz');tok.scroll_into_view_if_needed()
 assert tok.is_visible();assert tok.evaluate('(e)=>e.scrollWidth>e.clientWidth');capture(tok,'tokenization','tech-agent-tokenization',1)
 go('tech-overview');graph=page.locator('#techScroll');graph.scroll_into_view_if_needed();capture(graph,'data-graph','tech-overview-data-graph',1)
 go('tech-quality-loop');graph=page.locator('#skillsScroll');graph.scroll_into_view_if_needed();capture(graph,'skills-graph','tech-quality-skills-graph',1)
 # All three original screenshots have working independent enlarge controls.
 for id in ('tech-loading','tech-agent','tech-hold'):
  go(id);photo=page.locator('#'+id+' [data-evidence-photo]');photo.click();page.wait_for_function('document.querySelector("#bookArtImage").naturalWidth>0');assert page.locator('#bookArtDialog').is_visible();page.keyboard.press('Escape')
 # Actual touch gesture, then keyboard arrows, must not open zoom or move page horizontally.
 go('tech-loading');pan=page.locator('#tech-loading .tech-art-pan').first;pan.scroll_into_view_if_needed();pan.evaluate('(e)=>e.scrollLeft=0')
 box=pan.bounding_box();session=ctx.new_cdp_session(page);x=box['x']+box['width']*.8;y=max(160,box['y']+min(150,box['height']/2))
 session.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':[{'x':x,'y':y}]})
 for dx in (25,60,110,160):session.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':[{'x':x-dx,'y':y}]})
 session.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
 page.wait_for_function("document.querySelector('#tech-loading .tech-art-pan').scrollLeft>20")
 assert page.locator('#bookArtDialog').is_hidden();pan.focus();before=pan.evaluate('(e)=>e.scrollLeft');pan.press('ArrowRight');assert pan.evaluate('(e)=>e.scrollLeft')>before
 assert page.evaluate('scrollX')==0
 page.locator('#tech-loading .book-visual-open').first.click();assert page.locator('#bookArtDialog').is_visible();page.keyboard.press('Escape')
 assert not errors,errors
 b.close()
if server:server.shutdown()
(out/'inventory.json').write_text(json.dumps({'url':url,'diagrams':proof,'count':len(proof),'errors':errors,'passed':True},ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS:',len(proof),'diagrams captured with complete overlapping slices; 20 chapters x 4 widths; touch, keyboard, zoom')
