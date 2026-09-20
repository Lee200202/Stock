"""Package current deployment sources and reviewed artifacts; verify ZIP hashes."""
import ast
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
paths = [ROOT/name for name in ('pipeline.py','pipeline/pipeline.py','requirements.txt',
    'pipeline/requirements.txt','.github/workflows/daily.yml','README.md',
    'scripts/sync_quality.py','scripts/evaluate_transcript_assessment.py',
    'scripts/package_review_0912.py','scripts/preview_0912.js')]
assert paths[0].read_bytes() == paths[1].read_bytes()
ast.parse(paths[0].read_text(encoding='utf-8'))
paths += sorted((ROOT/'apps-script').glob('*.gs')) + sorted((ROOT/'apps-script').glob('*.html'))
paths += sorted((ROOT/'tests').glob('test_*.py')) + sorted((ROOT/'tests').glob('test*.js'))
paths += [p for p in sorted((ROOT/'docs/0912').rglob('*')) if p.is_file()]
manifest = {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
record = {'build':'2026-09-12-context-json-v4','files':manifest,
          'verification':'See docs/0912/驗證紀錄.md; local tests and browser fixtures, no live deployment.'}
dest = ROOT/'release/zhangzhen-context-json-v4.zip'
with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as archive:
    for p in paths: archive.write(p,p.relative_to(ROOT).as_posix())
    archive.writestr('MANIFEST.json',json.dumps(record,ensure_ascii=False,indent=2))
with zipfile.ZipFile(dest) as archive:
    assert archive.testzip() is None
    for name,digest in manifest.items():
        assert hashlib.sha256(archive.read(name)).hexdigest()==digest,name
(ROOT/'release/MANIFEST_CONTEXT_JSON_V4.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Verified {len(paths)} files; {dest.stat().st_size:,} bytes: {dest}')
