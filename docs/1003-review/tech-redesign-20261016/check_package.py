"""驗證設計交接套件，不驗證正式實作。"""
import json
import re
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.local_files = []
        self.anchors = []
        self.dialogs = []
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if 'data-dialog' in attrs:
            self.dialogs.append(attrs['data-dialog'])
        for attr in ('src', 'href'):
            value = attrs.get(attr, '')
            if value.startswith('#'):
                self.anchors.append(value[1:])
            elif value and not value.startswith(('http:', 'https:', 'data:')):
                self.local_files.append(value)

doc = Document()
doc.feed((ROOT / 'prototype.html').read_text(encoding='utf-8'))
assert not [key for key, n in Counter(doc.ids).items() if n > 1], 'Duplicate HTML ids'
assert all(key in doc.ids for key in doc.anchors + doc.dialogs), 'Unresolved local target'
assert all((ROOT / file).is_file() for file in doc.local_files), 'Missing local asset'
mapping = json.loads((ROOT / 'chapter-map.json').read_text(encoding='utf-8'))
chapters = mapping['chapters']
assert len(chapters) == 20
assert len({item['id'] for item in chapters}) == 20
assert all(6 <= len(item['title']) <= 10 for item in chapters)
aliases = [alias for item in chapters for alias in item['aliases']]
assert set(f'toc-{n}' for n in range(1, 19)).issubset(aliases)
assert len(aliases) == len(set(aliases))
review = mapping['reviewIntegration']
assert review['sourceChapterCount'] == 18 and review['targetChapterCount'] == 20
assert (ROOT / review['decisions']).is_file()
assert all(item['chapter'] in {chapter['id'] for chapter in chapters}
           for item in review['requiredModules'])
assert len(review['requiredModules']) == 8
prompt = (ROOT / '執行Prompt.md').read_text(encoding='utf-8')
assert len(re.findall(r'^### \d\d ', prompt, flags=re.M)) == 20
assert '整合取捨與查證.md' in prompt
research = (ROOT / '研究與現況診斷.md').read_text(encoding='utf-8')
apple = set(re.findall(r'https://www\.apple\.com/[^)\s>]*', research))
assert len(apple) >= 20
js = (ROOT / 'prototype.js').read_text(encoding='utf-8')
assert not re.search(r'\b(fetch|XMLHttpRequest|setInterval)\s*\(', js)
assert '.innerHTML' not in js
assert 'getPointAtLength' in js and 'requestAnimationFrame' in js
assert 'prefers-reduced-motion' in js and 'visibilitychange' in js
print(json.dumps({'chapters': len(chapters), 'legacy_toc_aliases': 18,
                  'integrated_modules': len(review['requiredModules']),
                  'apple_official_pages': len(apple), 'html_ids': len(doc.ids),
                  'local_assets': len(set(doc.local_files)), 'result': 'PASS'}, ensure_ascii=False))
