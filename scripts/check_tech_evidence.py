"""章節插圖與精簡來源元件的靜態發布契約。

介面量測由真實瀏覽器另行收集；本腳本不開瀏覽器、不呼叫模型，也不寫正式資料。"""
from pathlib import Path
from collections import Counter
from bs4 import BeautifulSoup
import json, subprocess, re

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'public-site/gas-source'
tech=(SRC/'Tech.html').read_text(encoding='utf-8')
soup=BeautifulSoup(tech,'html.parser')
inventory=json.loads((ROOT/'docs/1003-review/tech-redesign-20261016/tech-content-inventory.json').read_text(encoding='utf-8'))
ids=Counter(n['data-content-id'] for n in soup.select('[data-content-id]'))
assert all(ids[n['id']]==1 for n in inventory['items']), 'retained content missing/duplicated'
assert len(soup.select('[data-chapter]'))==19
assert soup.select_one('[data-tech-book]')['data-chapter-count']=='19'
assert soup.select_one('#tech-day').find_parent(id='tech-overview')
assert len(soup.select('#tech-overview .book-stage'))==2
assert len(soup.select('#bookContents a'))==19
assert not soup.select('#bookPrint,details')
assert len(soup.select('#techFlow [data-flow]'))==6
assert len(soup.select('#techCanvas [data-entity]'))>=34
assert len(soup.select('[data-evidence-kind]'))==3
assert len(soup.select('[data-evidence-photo]'))==2
assert len(soup.select('.src-card'))==13
for anchor in soup.select('a[href^="https:"]'):
    if anchor.find_parent(class_='author-grid'): continue
    assert 'src-card' in anchor.get('class',[]) or anchor.find_parent(class_='author-links'), str(anchor)[:180]
for ch in soup.select('[data-chapter]'):
    svg=ch.select_one('svg.book-art')
    assert svg and svg.get('role')=='img' and svg.get('aria-label')
    assert len(svg.select('[data-book-path]'))>=2
    assert len(svg.select('[data-book-node]'))>=3
    asset=ROOT/'public-site/assets/tech-book'/f"{ch['id']}.svg"
    picture=BeautifulSoup(asset.read_text(encoding='utf-8'),'html.parser')
    assert picture.svg.get_text()==svg.get_text(), ch['id']
    for image in ch.select('img'):
        name=image['src'].split('/')[-1]
        assert (ROOT/'public-site/assets'/name).is_file(), name
evidence=(SRC/'TechEvidence.html').read_text(encoding='utf-8')
assert not re.search(r'\b(fetch|XMLHttpRequest|google\.script\.run|setInterval)\b',evidence)
assert 'IntersectionObserver' in evidence and 'document.hidden' in evidence
assert 'prefers-reduced-motion' in evidence and 'data-ev-replay' in evidence
assert 'getTotalLength' in evidence and 'getPointAtLength' in evidence
assert 'data-mobile-viewbox' in tech
assert 'data-illustration="bespoke"' in tech
assert 'reflow' in evidence and 'mobileViewbox' in evidence
assert "include('TechEvidence')" in (SRC/'Index.html').read_text(encoding='utf-8')
illustrations=(SRC/'TechIllustrations.html').read_text(encoding='utf-8')
assert "include('TechIllustrations')" in (SRC/'Index.html').read_text(encoding='utf-8')
assert not re.search(r'\b(fetch|XMLHttpRequest|google\.script\.run|setInterval)\b',illustrations)
assert '__techBookRefreshArt' in illustrations
assert soup.select_one('#classificationBoundaries')
assert soup.select_one('#voteBoundaryDetails')
assert len(re.findall('三輪仍分歧',illustrations)) >= 1
assert '已有部位例外' in illustrations
assert '選一個情境，看這次會不會命中' not in tech
# 只檢查本次修改的各個內嵌程式語法，不執行程式。
for name in ['Tech.html','StoryDesign.html','TechEvidence.html','TechIllustrations.html']:
    scripts=BeautifulSoup((SRC/name).read_text(encoding='utf-8'),'html.parser').find_all('script')
    for script in scripts:
        check=subprocess.run(['node'],input='new Function('+json.dumps(script.string or script.get_text())+');',capture_output=True,text=True,encoding='utf-8')
        assert check.returncode==0,(name,check.stderr)
print(f'Tech evidence contract PASS: {len(inventory["items"])} retained content IDs, 19 chapters, 13 source cards, 20 SVGs, 3 interactive labs, 2 new screenshots')
