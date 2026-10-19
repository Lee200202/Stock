"""Score frozen source-review constraints against read-only replay logs.

These are agent-reviewed references, not human gold or an estimate of total
classification accuracy. Website comparison sections are deliberately ignored.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

LABELS = {'買入':'buy','賣出':'sell','會員持股':'holdings','觀望注意':'watch_watch','觀望不碰':'watch_avoid'}


def transcript_rows(log):
    text = log.split('===== 重播結果', 1)[1].split('【①', 1)[0]
    result, category = {}, ''
    for line in text.splitlines():
        heading = re.search(r'【([^】]+)】\d+ 檔', line)
        if heading:
            category = LABELS.get(heading[1], '')
        row = re.search(r'- (.+?)（([A-Z0-9]+)）\d+ 字：(.*)$', line)
        if row and category:
            result.setdefault(row[2], []).append({'class':category,'name':row[1],'note':row[3]})
    return result


def score(reference, rows):
    checks = []
    for code, allowed in reference.get('must', {}).items():
        found = [r['class'] for r in rows.get(code, [])]
        checks.append({'code':code,'expected':allowed,'actual':found,'ok':bool(set(found)&set(allowed))})
    excluded = [{'code':c,'actual':[r['class'] for r in rows.get(c, [])], 'ok':c not in rows}
                for c in reference.get('must_not', [])]
    return {'matched_required':sum(c['ok'] for c in checks),'required':len(checks),
            'missing':[c['code'] for c in checks if not c['actual']],
            'wrong_category':[c for c in checks if c['actual'] and not c['ok']],
            'forbidden_present':[c for c in excluded if not c['ok']],
            'checks':checks, 'exclusion_checks':excluded}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    results=[]
    for run in manifest['runs']:
        ref=json.loads(Path(run['reference']).read_text(encoding='utf-8'))
        path=Path(run['log'])
        if not path.is_file():
            results.append({**run,'status':'not_downloaded'});continue
        log=path.read_text(encoding='utf-8-sig')
        if '===== 重播結果' not in log:
            results.append({**run,'status':'no_final_rows','not_scored_as_success':True});continue
        rows=transcript_rows(log)
        usage=re.findall(r'本輪呼叫 Gemini (\d+) 次',log)
        results.append({**run,'status':'scored','label_origin':ref['label_origin'],
                        'review_status':ref['review_status'], 'raw_sha256':ref['raw_sha256'],
                        'reference_sha256':hashlib.sha256(Path(run['reference']).read_bytes()).hexdigest(),
                        'scope':'transcript rows before notice-priority article; website ignored',
                        'model_calls':int(usage[-1]) if usage else None,
                        'constraint_score':score(ref,rows),'rows':rows})
    report={'interpretation':'source-reviewed constraint agreement, not overall accuracy or human gold',
            'website_is_not_ground_truth':True, 'runs':results}
    Path(args.out).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for r in results:
        s=r.get('constraint_score',{})
        print(r['run_id'],r.get('variant'),r.get('date'),r['status'],
              f"{s.get('matched_required','-')}/{s.get('required','-')}",r.get('model_calls'))


if __name__=='__main__':main()
