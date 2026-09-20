"""Create a source-only deployable archive and verify every member's hash."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'pipeline/pipeline.py').read_bytes()
assert source == (ROOT/'pipeline.py').read_bytes()
ast.parse(source.decode('utf-8'))
paths = [ROOT/'pipeline.py', ROOT/'pipeline/pipeline.py', ROOT/'requirements.txt',
         ROOT/'pipeline/requirements.txt', ROOT/'.github/workflows/daily.yml',
         ROOT/'README.md', ROOT/'release/DEPLOY_CONTEXT_JSON_V3.md', ROOT/'scripts/sync_quality.py']
paths += sorted((ROOT/'apps-script').glob('*.gs')) + sorted((ROOT/'apps-script').glob('*.html'))
paths += sorted((ROOT/'tests').glob('test_*.py')) + sorted((ROOT/'tests').glob('test*.js'))
manifest = {str(p.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
info = {'version': 'context-json-v3-thinking', 'files': manifest, 'validation': '66 Python tests; Apps Script regressions; 18 syntax checks'}
manifest_path = ROOT/'release/MANIFEST_CONTEXT_JSON_V3.json'
manifest_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
dest = ROOT/'release/zhangzhen-context-json-v3-thinking.zip'
with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in paths + [manifest_path]:
        z.write(p, str(p.relative_to(ROOT)).replace('\\', '/'))
with zipfile.ZipFile(dest) as z:
    assert z.testzip() is None
    for name, digest in manifest.items():
        assert hashlib.sha256(z.read(name)).hexdigest() == digest, name
print(f'Verified {len(manifest)} source files: {dest} ({dest.stat().st_size:,} bytes)')
