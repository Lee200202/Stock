"""檢查來源稽核章節；使用本機假資料，不呼叫模型或API寫入。"""
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
    assert ch.locator('svg.book-art').count()==0
    assert ch.locator('.tech-domain').count()==6
    assert ch.locator('.tg-edge.is-quiet:visible').count()==0
    titles=set()
    for i in range(7):
        ch.locator(f'[data-quality-node="{i}"]').click()
        assert ch.locator(f'[data-quality-copy="{i}"]').is_visible()
        assert ch.locator('#skillsEntityDetail').is_visible()
        assert ch.locator('#skillsEntityDetail .tg-rel').count()>=2
        titles.add(ch.locator(f'[data-quality-copy="{i}"] h3').first.inner_text())
    assert len(titles)==7
    ch.locator('[data-quality-node="2"]').press('Enter')
    assert ch.locator('[data-quality-node="1"]').get_attribute('class').find('is-related')>=0
    assert ch.locator('.tg-edge.is-quiet.is-on:visible').count()==1
    ch.locator('#skillsEntityDetail [data-jump="4"]').click()
    assert ch.locator('[data-quality-copy="4"]').is_visible()
    ch.locator('[data-quality-node="5"]').click()
    ch.locator('[data-relationship-more]').click()
    results=set()
    for i in range(3):
        ch.locator(f'[data-quality-experiment="{i}"]').click()
        results.add(ch.locator('[data-quality-metric]').inner_text())
    assert len(results)==3
    ch.locator('[data-quality-node="1"]').click()
    assert '不會自動' in ch.inner_text()
    assert ch.locator('a[href="https://www.youtube.com/watch?v=rZl_3izD8y8"]').count()==1
    assert ch.locator('a[href="https://www.alphalab.site/agent-skill-routing-ab-test"]').count()==1
    ch.locator('#skillsGraphReset').click()
    assert ch.locator('.tg-edge.is-quiet:visible').count()==0
    layouts=[]
    for width in (1916,1440,1280,768,390,360):
        page.set_viewport_size({'width':width,'height':1000})
        for theme in ('light','dark'):
            page.evaluate('(v)=>document.documentElement.dataset.theme=v',theme)
            for font in ('m','x'):
                page.evaluate('(v)=>document.documentElement.dataset.fs=v',font)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,theme,font)
                ch.locator('[data-quality-node="2"]').click()
                if width<700:assert ch.locator('#skillsEntityDetail').get_attribute('class').find('is-panel')>=0
                else:
                    # 共用配置不能遮住已選取的卡片。
                    assert ch.locator('#skillsEntityDetail').evaluate("""p=>{const a=p.getBoundingClientRect(),b=document.querySelector('#skillsGraph [aria-pressed=true]').getBoundingClientRect();return a.right<=b.left||a.left>=b.right||a.bottom<=b.top||a.top>=b.bottom;}""")
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,theme,font,'after selection')
                layouts.append([width,theme,font])
        if width in (1440,390):
            ch.locator('#skillsScroll').scroll_into_view_if_needed();page.screenshot(path=str(out/f'quality-{width}.png'))
    # 兩章共用同一元件，原關聯圖仍須能操作34張卡片。
    ch.locator('.book-pager a').last.click();assert page.locator('#tech-names').is_visible()
    page.locator('#tech-names .book-pager a').first.click();assert ch.is_visible()
    page.reload();page.wait_for_function("document.querySelector('.tech-book')?.dataset.activeChapter==='tech-quality-loop'")
    ch.locator('[data-quality-node="1"]').click();page.keyboard.press('Escape');assert ch.locator('#skillsEntityDetail').is_hidden()
    page.emulate_media(reduced_motion='no-preference');ch.locator('[data-quality-replay]').click();ch.locator('#skillsGraph').scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('#skillsGraph').dataset.playing==='true'")
    page.wait_for_function("Number(document.querySelector('#skillsGraph').dataset.cycles)>=2",timeout=23000)
    ch.locator('[data-quality-node="1"]').click();assert ch.locator('#skillsGraph').get_attribute('data-playing')=='false'
    ch.locator('[data-quality-replay]').click();ch.locator('#skillsGraph').scroll_into_view_if_needed()
    page.wait_for_function("document.querySelector('#skillsGraph').dataset.playing==='true'")
    page.emulate_media(reduced_motion='reduce');page.wait_for_function("document.querySelector('#skillsGraph').dataset.playing==='false'")
    page.locator('.book-tools [data-open-contents]').click();page.locator('#bookContents a[href="#tech-overview"]').click()
    assert page.locator('#techGraph [data-entity]').count()==34
    page.locator('#techGraph [data-entity="video"]').click();assert page.locator('#techEntityDetail').is_visible()
    assert '影片' in page.locator('#techEntityDetail').inner_text()
    assert page.locator('#techGraph .is-related').count()>1
    assert not errors,errors
    browser.close()
    if not args.url:
        browser=pw.chromium.launch();ctx=browser.new_context(java_script_enabled=False);p=ctx.new_page();p.goto(url)
        assert p.locator('#tech-quality-loop').is_visible()
        assert '程式：先核對分類依據' in p.locator('#tech-quality-loop').inner_text()
        browser.close()
if server:server.shutdown()
(out/'results.json').write_text(json.dumps({'layouts':layouts,'errors':errors,'url':url,'passed':True},ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS:',len(layouts),'layouts; shared six-column graph, 7 connected roles, conditional edges, A/B configurations, navigation, loop/replay, reduced motion, no-JS')
