"""Build only reviewed deployment files and verify every member checksum."""
from pathlib import Path
import ast, hashlib, json, zipfile
ROOT=Path(__file__).resolve().parents[1]
paths=[ROOT/n for n in ('AGENTS.md','README.md','pipeline.py','pipeline/pipeline.py',
 'requirements.txt','pipeline/requirements.txt','.github/workflows/daily.yml',
 'scripts/sync_quality.py','scripts/evaluate_transcript_assessment.py',
 'scripts/preview_0912.js','scripts/package_quality_v6.py')]
assert (ROOT/'pipeline.py').read_bytes()==(ROOT/'pipeline/pipeline.py').read_bytes()
ast.parse((ROOT/'pipeline.py').read_text(encoding='utf-8'))
paths += sorted((ROOT/'apps-script').glob('*.gs'))+sorted((ROOT/'apps-script').glob('*.html'))
assert len(list((ROOT/'apps-script').glob('*.gs')))==19
paths += sorted((ROOT/'tests').glob('test_*.py'))+sorted((ROOT/'tests').glob('test*.js'))
paths += [p for folder in ('docs/0912','docs/0913','docs/0913-v6') for p in sorted((ROOT/folder).rglob('*')) if p.is_file()]
manifest={'build':'2026-09-13-quality-v6','verification':'docs/0913-v6/驗證紀錄.md',
 'files':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}}
dest=ROOT/'release/zhangzhen-quality-v6.zip'
with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as z:
    for p in paths:z.write(p,p.relative_to(ROOT).as_posix())
    z.writestr('MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
with zipfile.ZipFile(dest) as z:
    assert z.testzip() is None
    for name,digest in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==digest,name
(ROOT/'release/MANIFEST_QUALITY_V6.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Verified {len(paths)} files, {dest.stat().st_size:,} bytes: {dest}')
