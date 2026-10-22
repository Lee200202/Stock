"""檢查與修正模式不得誤入模型重新生成流程。"""
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]


class ReviewWorkflowContract(unittest.TestCase):
    def test_read_and_write_modes_are_in_the_step_with_credentials_and_exit(self):
        data=yaml.safe_load((ROOT/'.github/workflows/regenerate-verified.yml').read_text(encoding='utf-8'))
        steps=data['jobs']['regenerate']['steps']
        active=next(s for s in steps if 'MODE' in s.get('env',{}))
        for mode in ['source-review-inspect','source-review-preview','source-review-apply']:
            text=active['run'].split('if [ "$MODE" = '+mode+' ]; then',1)[1].split('\nfi',1)[0]
            self.assertIn('exit 0',text.split('fi',1)[0])
        offline=next(s for s in steps if s.get('name','').startswith('離線發布'))
        self.assertNotIn('$MODE',offline['run'])

    def test_only_explicit_model_modes_can_reach_regenerator(self):
        data=yaml.safe_load((ROOT/'.github/workflows/regenerate-verified.yml').read_text(encoding='utf-8'))
        active=next(s for s in data['jobs']['regenerate']['steps'] if 'MODE' in s.get('env',{}))
        body=active['run']; guard=body.index('未處理的模式')
        self.assertLess(guard,body.index('python scripts/regenerate_verified_day.py'))
        self.assertIn('exit 1',body[guard:body.index('ARGS=(--date')])


if __name__=='__main__':unittest.main()
