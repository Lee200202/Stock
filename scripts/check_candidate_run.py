"""發布前向GitHub核對候選來源；任意本機JSON或失敗執行不能冒充成功預覽。"""
import os
import requests


def validate_run(run, candidate, repository, commit, day):
    if (not candidate.isdigit() or str(run.get('id')) != candidate
            or run.get('repository', {}).get('full_name', '').lower() != repository.lower()
            or run.get('path') != '.github/workflows/regenerate-verified.yml'
            or run.get('event') != 'workflow_dispatch' or run.get('status') != 'completed'
            or run.get('conclusion') != 'success' or run.get('head_sha') != commit
            or run.get('display_title') not in [f'單日品質核對 {day}（{m}）' for m in ('preview', 'raw')]):
        raise ValueError('只接受同repo、同commit、同日期且成功的模型預覽；不重新生成或修補答案')


if __name__ == '__main__':
    candidate = os.environ.get('CANDIDATE_RUN', '')
    if not candidate.isdigit():
        raise SystemExit('apply必填成功候選run ID')
    repository = os.environ['GITHUB_REPOSITORY']
    response = requests.get(f'https://api.github.com/repos/{repository}/actions/runs/{candidate}',
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json'}, timeout=25)
    response.raise_for_status()
    validate_run(response.json(), candidate, repository, os.environ['GITHUB_SHA'], os.environ['TARGET_DATE'])
    print('GitHub候選來源核對通過；接續下載該次不可變artifact')
