"""Missing reference fails before model or sheet access, with an actionable reason."""
import argparse
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo


class RegeneratorPreflight(unittest.TestCase):
    def test_missing_reference_is_not_a_permission_or_model_failure(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/regenerate_verified_day.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
        # Execute exactly the production preflight, excluding all later IO/model code.
        stop = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and
                    any(isinstance(t, ast.Name) and t.id == 'golden' for t in n.targets))
        program = ast.Module(body=main.body[:stop], type_ignores=[])
        day = datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y/%m/%d')
        ns = dict(argparse=argparse, datetime=datetime, timedelta=timedelta,
                  Path=Path, ZoneInfo=ZoneInfo, pl=SimpleNamespace(norm_date=lambda x: x))
        with patch('sys.argv', ['regenerate', '--date', day, '--apply']), \
             patch.object(Path, 'exists', return_value=False), self.assertRaises(SystemExit) as raised:
            exec(compile(program, str(path), 'exec'), ns)
        reason = str(raised.exception)
        self.assertIn('scripts', reason)
        self.assertIn('golden', reason)
        self.assertIn('在模型判讀與寫入之前停止', reason)
        self.assertIn('source-review-preview', reason)


if __name__ == '__main__':
    unittest.main()
