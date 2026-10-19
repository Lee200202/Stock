"""Check source-review chapter: no model/API writes; fake data for local UI."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--url');args=parser.parse_args()
out=ROOT/'scratch/quality-chapter-qa';out.mkdir(parents=True,exist_ok=True)
server=None
if args.url:url=args.url
else:
    build=out/'site'
    subprocess.run([sys.executable,str(ROOT/'scripts/build_original_site.py'),'--output',str(build)],
                   env=dict(os.environ,SITE_API_URL='https://quality-test.example.workers.dev/api'),check=True,stdout=subprocess.DEVNULL)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),partial(Quiet,directory=str(build)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f'http://127.0.0.1:{server.server_port}/?tab=tech#tech-quality-loop'
with sync_playwright() as pw:
    browser=pw.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='reduce')
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    if not args.url:
        page.route('https://quality-test.example.workers.dev/**',lambda r:r.fulfill(content_type='application/json',body='{"ok":true,"items":[],"held":[]}'))
    page.goto(url,wait_until='domcontentloaded')
    page.wait_for_function("document.querySelector('.tech-book')?.dataset.activeChapter==='tech-quality-loop'")
    ch=page.locator('#tech-quality-loop');assert ch.is_visible()
    assert page.locator('[data-chapter]').count()==20
    assert page.locator('#bookContents a').count()==20
    assert '技能評估與分類依據' in ch.inner_text()
    assert '08 / 20' in ch.locator('.book-eyebrow').inner_text()
    assert ch.locator('[data-quality-node]').count()==7
    assert ch.locator('[data-quality-role]').count()==0
    assert ch.locator('[data-quality-edge][data-quality-cross="true"]:visible').count()==0
    titles=set()
    for i in range(7):
        ch.locator(f'[data-quality-node="{i}"]').click()
        assert ch.locator(f'[data-quality-copy="{i}"]').is_visible()
        titles.add(ch.locator(f'[data-quality-copy="{i}"] h3').first.inner_text())
        assert ch.locator('[data-quality-neighbors]').inner_text()
    assert len(titles)==7
    # Select the program: raw-data cross-column edge appears; unrelated feedback stays hidden.
    ch.locator('[data-quality-node="2"]').press('Enter')
    assert ch.locator('[data-quality-edge="3-2"]').is_visible()
    assert ch.locator('[data-quality-edge="6-1"]').is_hidden()
    assert ch.locator('[data-quality-node="1"]').get_attribute('class').find('quality-upstream')>=0
    ch.locator('[data-quality-jump="4"]').click()
    assert ch.locator('[data-quality-copy="4"]').is_visible()
    ch.locator('[data-quality-node="5"]').click()
    results=set()
    for i in range(3):
        ch.locator(f'[data-quality-experiment="{i}"]').click()
        results.add(ch.locator('[data-quality-metric]').inner_text())
    assert len(results)==3
    ch.locator('[data-quality-node="1"]').click()
    assert '不會自動' in ch.inner_text()
    assert ch.locator('a[href="https://www.youtube.com/watch?v=rZl_3izD8y8"]').count()==1
    assert ch.locator('a[href="https://www.alphalab.site/agent-skill-routing-ab-test"]').count()==1
    ch.locator('[data-quality-reset]').click()
    assert ch.locator('[data-quality-edge][data-quality-cross="true"]:visible').count()==0
    layouts=[]
    for width in (1916,1440,1280,768,390,360):
        page.set_viewport_size({'width':width,'height':1000})
        for theme in ('light','dark'):
            page.evaluate('(v)=>document.documentElement.dataset.theme=v',theme)
            for font in ('m','x'):
                page.evaluate('(v)=>document.documentElement.dataset.fs=v',font)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,theme,font)
                clipped=ch.locator('svg.book-art').evaluate('''svg=>{
                    const s=svg.getBoundingClientRect();return [...svg.querySelectorAll('text')].filter(n=>{
                      const b=n.getBoundingClientRect();return b.left<s.left-1||b.right>s.right+1||b.top<s.top-1||b.bottom>s.bottom+1;
                    }).map(n=>n.textContent);
                }''')
                assert not clipped,(width,theme,font,clipped)
                assert ch.locator('.quality-controls button').evaluate_all('(els)=>els.every(e=>e.getBoundingClientRect().height>=44)')
                layouts.append([width,theme,font])
        if width in (1440,390):
            ch.locator('.tech-art-pan').first.scroll_into_view_if_needed();page.screenshot(path=str(out/f'quality-{width}.png'))
    # Original chapter links, new previous/next, hash reload and zoom dialog.
    ch.locator('.book-pager a').last.click();assert page.locator('#tech-names').is_visible()
    page.locator('#tech-names .book-pager a').first.click();assert ch.is_visible()
    page.reload();page.wait_for_function("document.querySelector('.tech-book')?.dataset.activeChapter==='tech-quality-loop'")
    ch.locator('.book-visual-open').click();page.wait_for_function('document.querySelector("#bookArtImage").naturalWidth>0')
    page.keyboard.press('Escape');assert page.locator('#bookArtDialog').is_hidden()
    # Visible loop, user stop, replay loop, hidden-page pause, reduced motion.
    page.emulate_media(reduced_motion='no-preference');ch.locator('[data-book-replay]').click();ch.locator('.book-stage').scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('.quality-stage').dataset.playing==='true'")
    page.wait_for_function("Number(document.querySelector('.quality-stage').dataset.cycles)>=2",timeout=22000)
    ch.locator('[data-quality-node="1"]').click();assert ch.locator('.book-stage').get_attribute('data-playing')=='false'
    ch.locator('[data-book-replay]').click();ch.locator('.book-stage').scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('.quality-stage').dataset.playing==='true'")
    page.emulate_media(reduced_motion='reduce');page.wait_for_function("document.querySelector('.quality-stage').dataset.playing==='false'")
    assert not errors,errors
    browser.close()
    if not args.url:
        browser=pw.chromium.launch();ctx=browser.new_context(java_script_enabled=False);p=ctx.new_page();p.goto(url)
        assert p.locator('#tech-quality-loop').is_visible()
        assert '程式：先核對分類依據' in p.locator('#tech-quality-loop').inner_text()
        browser.close()
if server:server.shutdown()
(out/'results.json').write_text(json.dumps({'layouts':layouts,'errors':errors,'url':url,'passed':True},ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS:',len(layouts),'layouts; 7 connected roles, conditional edges, A/B configurations, SVG text, navigation, zoom, loop/replay, reduced motion, no-JS')
