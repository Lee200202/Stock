"""Isolate production generation settings using tiny synthetic input, never publish results."""
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
import requests


def configurations(model):
    tree = ast.parse(Path('pipeline/pipeline.py').read_text(encoding='utf-8'))
    namespace = {'re': re, 'os': os, 'MAX_OUT': int(os.environ.get('GEMINI_MAX_OUT') or 65535),
                 '_SCHEMA_STATE': {'on': True}}
    categories = next(n for n in tree.body if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == 'SIGNAL_CATEGORIES' for t in n.targets))
    namespace['SIGNAL_CATEGORIES'] = ast.literal_eval(categories.value)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ('assessment_response_schema', 'gemini_generation_config')]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<production-config>', 'exec'), namespace)
    full = namespace['gemini_generation_config'](model, min(namespace['MAX_OUT'], 20000), 2048, True, 'assess-json-1')
    json_only = {k: v for k, v in full.items() if k != 'responseJsonSchema'}
    plain = {k: v for k, v in json_only.items() if k != 'thinkingConfig'}
    return [('production-settings', full), ('without-schema', json_only), ('without-schema-or-thinking', plain)]


def main():
    key = os.environ['GEMINI_API_KEY'].strip()
    model = os.environ.get('GEMINI_MODEL', '').strip() or 'gemini-3.5-flash-lite'
    rows = []
    for index, (variant, config) in enumerate(configurations(model)):
        if index:
            time.sleep(45)
        row = {'variant': variant, 'at': datetime.now(timezone.utc).isoformat(), 'model': model,
               'configKeys': sorted(config)}
        started = time.monotonic()
        try:
            r = requests.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                              params={'key': key}, timeout=(10, 45),
                              json={'contents': [{'role': 'user', 'parts': [{'text':
                                  'Return one JSON object with these nine empty arrays: buy, sell, watch_avoid, '
                                  'watch_watch, holdings, history, uncertain, ignored, market. No records exist.'}]}],
                                    'generationConfig': config})
            row['http'] = r.status_code
            body = r.json()
            if r.status_code == 200:
                row['hasText'] = any(p.get('text') for c in body.get('candidates', [])
                                     for p in (c.get('content') or {}).get('parts', []))
            else:
                row['apiStatus'] = (body.get('error') or {}).get('status')
                msg = str((body.get('error') or {}).get('message') or '').replace(key, '[redacted]')
                row['apiMessage'] = re.sub(r'https?://\S+|projects/\d+', '[redacted]', msg)[:300]
        except (requests.RequestException, ValueError) as exc:
            row['errorType'] = type(exc).__name__
        row['seconds'] = round(time.monotonic() - started, 2)
        rows.append(row)
        print(json.dumps(row), flush=True)
    Path('gemini-recovery.json').write_text(json.dumps({'checks': rows,
        'limit': 'Synthetic configuration checks isolate settings, not full transcript quality or availability.'}, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
