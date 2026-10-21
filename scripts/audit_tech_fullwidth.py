"""Tech page: list cards/boxes that sit alone on their row but do not span the column (all 20 chapters)."""
import os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright
html = Path('scratch/ss/_site/index.html').read_text(encoding='utf-8'); URL='https://lee200202.github.io/Stock/'
JS = r"""()=>{const out=[];const ch=document.querySelector('[data-chapter]:not([hidden])');
 const vis=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
 ch.querySelectorAll('*').forEach(e=>{if(e.closest('svg,dialog,.tech-art-pan,.book-strip,.book-scene-key,.book-motion,button,a'))return;if(!vis(e))return;const s=getComputedStyle(e);
  const boxy=(s.backgroundColor!=='rgba(0, 0, 0, 0)'&&s.backgroundColor!=='transparent')||parseFloat(s.borderTopLeftRadius)>=14||parseFloat(s.borderTopWidth)>0;
  const r=e.getBoundingClientRect();if(!boxy||r.width<240||r.height<60)return;const p=e.parentElement;const ps=getComputedStyle(p);const pr=p.getBoundingClientRect();
  const inner=pr.width-parseFloat(ps.paddingLeft)-parseFloat(ps.paddingRight);
  const row=[...p.children].filter(c=>c!==e&&vis(c)).some(c=>{const q=c.getBoundingClientRect();return q.top<r.bottom-4&&q.bottom>r.top+4});
  if(!row&&r.width<inner*0.94){const cls=(e.className&&e.className.baseVal===undefined?e.className:'').trim().split(/\s+/).slice(0,2).join('.');
   out.push({cls:e.tagName.toLowerCase()+(cls?'.'+cls:''),w:Math.round(r.width),inner:Math.round(inner),parent:(p.className||'').toString().split(' ')[0],text:(e.textContent||'').trim().slice(0,16)})}});return out}"""
ids = ['tech-start','tech-overview','tech-volume','tech-loading','tech-agent','tech-context','tech-harness','tech-quality-loop','tech-names','tech-number','tech-unnamed','tech-subject','tech-verdict','tech-source-priority','tech-public-notes','tech-transcript-progress','tech-hold','tech-failure','tech-faq','tech-author']
w = int(sys.argv[1]) if len(sys.argv) > 1 else 1440
with sync_playwright() as pw:
    b=pw.chromium.launch(); p=b.new_page(viewport={'width':w,'height':900})
    p.route(lambda u: u.split('?')[0].split('#')[0].rstrip('/')==URL.rstrip('/'), lambda r: r.fulfill(status=200,content_type='text/html; charset=utf-8',body=html))
    p.goto(URL+'?tab=tech'); p.wait_for_function("document.querySelector('.tech-book')?.dataset.bookReady==='true'",timeout=60000)
    if len(sys.argv) > 2: p.evaluate("([a,b])=>{document.documentElement.setAttribute('data-w',a);document.documentElement.setAttribute('data-fs',b)}", sys.argv[2:4])
    total=0
    for i in ids:
        p.evaluate("i=>{history.pushState(null,'','#'+i);dispatchEvent(new PopStateEvent('popstate'))}", i); p.wait_for_timeout(600)
        for o in p.evaluate(JS):
            total+=1; print(i, o)
    print('narrow boxes:', total)
