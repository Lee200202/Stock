"""讀取Gemini的usageMetadata用量紀錄，不呼叫模型，也不輸出憑證。"""
import argparse
import json
import re
from pathlib import Path

def report(text):
    rows = [dict(stage=m[0], input=int(m[1]), thinking=int(m[2]), output=int(m[3]))
            for m in re.findall(r'\[([^\]]+)\] finish=\w+ 輸入=(\d+) 思考=(\d+) 輸出=(\d+)', text)]
    if not rows:
        raise ValueError('No successful response usage records; do not invent token counts')
    return dict(scope='One workflow, recorded successful text responses only; excludes transcription and unlogged attempts',
                calls=len(rows), input=sum(r['input'] for r in rows),
                output=sum(r['output'] for r in rows), thinking=sum(r['thinking'] for r in rows),
                maxInput=max(r['input'] for r in rows), rows=rows)

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('log')
    p.add_argument('--output', required=True)
    args = p.parse_args()
    data = report(Path(args.log).read_text(encoding='utf-8-sig'))
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Usage report:', data['calls'], 'responses;', data['input'], 'input tokens')
