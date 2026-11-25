"""Three low-volume, manual recovery checks. No Sheets, website writes or notifications."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import requests


def main():
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    model = os.environ.get('GEMINI_MODEL', '').strip() or 'gemini-3.5-flash-lite'
    if not key:
        raise SystemExit('Primary Gemini key is missing; no request sent')
    rows = []
    for attempt in range(1, 4):
        if attempt > 1:
            time.sleep(45)
        row = {'attempt': attempt, 'at': datetime.now(timezone.utc).isoformat(), 'model': model}
        started = time.monotonic()
        try:
            response = requests.post(
                f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
                params={'key': key}, json={'contents': [{'parts': [{'text': 'Reply with OK only.'}]}],
                                           'generationConfig': {'maxOutputTokens': 64}}, timeout=(10, 45))
            row['http'] = response.status_code
            if response.status_code == 200:
                body = response.json()
                row['hasText'] = any(part.get('text') for candidate in body.get('candidates', [])
                                     for part in (candidate.get('content') or {}).get('parts', []))
        except (requests.RequestException, ValueError) as exc:
            row['errorType'] = type(exc).__name__  # Never print URLs or credential-bearing exception text.
        row['seconds'] = round(time.monotonic() - started, 2)
        rows.append(row)
        print(json.dumps(row), flush=True)
    report = {'checks': rows, 'successfulResponses': sum(r.get('http') == 200 and r.get('hasText', False) for r in rows),
              'limit': 'A short response does not establish full transcript processing recovery.'}
    Path('gemini-recovery.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as out:
            out.write('Gemini recovery checks: '+str(report['successfulResponses'])+'/3 returned text.\n')
    # Diagnostic collection completed; HTTP results remain explicit in the report.


if __name__ == '__main__':
    main()
