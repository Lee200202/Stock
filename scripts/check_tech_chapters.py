"""章節拆分後的導覽、原證據與互動保留契約；不呼叫服務。"""
from pathlib import Path
from collections import Counter
import json
import subprocess
from bs4 import BeautifulSoup

root = Path(__file__).resolve().parents[1]
source = (root / 'public-site/gas-source/Tech.html').read_text(encoding='utf-8')
soup = BeautifulSoup(source, 'html.parser')
book = soup.select_one('[data-tech-book]')
chapters = book.select('[data-chapter]')
declared = int(book['data-chapter-count'])
assert len(chapters) == declared
assert [int(c['data-chapter']) for c in chapters] == list(range(1, declared + 1))
ids = Counter(n['id'] for n in soup.select('[id]'))
assert all(n == 1 for n in ids.values()), [k for k, v in ids.items() if v != 1]
menu = book.select('#bookContents .book-contents-grid a')
assert [a['href'] for a in menu] == ['#' + c['id'] for c in chapters]
for i, chapter in enumerate(chapters):
    heading = chapter.select_one('h2[data-toc]')
    assert heading and heading.get('tabindex') == '-1', chapter['id']
    pager = chapter.select_one('nav.book-pager')
    assert pager, chapter['id']
    expected = [('#' + chapters[i - 1]['id']) if i else '#book-index',
                ('#' + chapters[i + 1]['id']) if i + 1 < declared else '#book-index']
    assert [a['href'] for a in pager.select('a')] == expected
    assert f'／{declared}章' in pager.get_text()
    assert chapter.select_one('figure, .review-evidence-flow, .quality-relationship'), chapter['id']
for anchor in book.select('a[href^="#"]'):
    assert anchor['href'][1:] in ids, anchor['href']
inventory = json.loads((root / 'docs/1003-review/tech-redesign-20261016/tech-content-inventory.json').read_text(encoding='utf-8'))
content = Counter(n['data-content-id'] for n in soup.select('[data-content-id]'))
assert all(content[row['id']] == 1 for row in inventory['items'])
for name in ['wf-claim-title', 'wf-trade-title', 'wf-scope-title', 'wf-failure-title', 'wf-verification-title']:
    assert soup.find(id=name).find_parent(attrs={'data-chapter': True})
assert len(soup.select('[data-wf-motion]')) == 5
for script in soup.find_all('script'):
    result = subprocess.run(['node'], input='new Function(' + json.dumps(script.string or script.get_text()) + ');',
                            capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
print(f'Tech chapter contract PASS: {declared} linked chapters; {len(inventory["items"])} original content IDs retained')
