"""單日重判的參考檔前置檢查：只執行 main() 讀試算表之前的部分，不呼叫模型、試算表或網路。

- 正式品質驗收：明確指定的參考檔不存在 → 在模型判讀與寫入之前停止。
- 無參考診斷：沒指定、預設答案檔也沒有 → 照跑，但明說未評分、候選不能發布
  （發布端另由 test_model_release_candidate 驗證沒有評分的候選不能套用）。
"""
import argparse
import ast
import contextlib
from datetime import datetime, timedelta
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo('Asia/Taipei')


def run_preflight(argv):
    """執行 main() 到算出 commit 之前為止（之後才開試算表、讀原稿、呼叫模型）。"""
    path = ROOT / 'scripts/regenerate_verified_day.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    stop = next(i for i, n in enumerate(main.body) if isinstance(n, ast.Assign) and
                any(isinstance(t, ast.Name) and t.id == 'commit' for t in n.targets))
    program = ast.Module(body=main.body[:stop], type_ignores=[])
    ns = dict(argparse=argparse, datetime=datetime, timedelta=timedelta, Path=Path, ROOT=ROOT,
              pl=SimpleNamespace(norm_date=lambda x: x, TAIPEI=TAIPEI))
    out = io.StringIO()
    with patch('sys.argv', ['regenerate'] + argv), contextlib.redirect_stdout(out):
        exec(compile(program, str(path), 'exec'), ns)
    return out.getvalue(), ns


class RegeneratorPreflight(unittest.TestCase):
    def setUp(self):
        self.day = datetime.now(TAIPEI).strftime('%Y/%m/%d')

    def test_a_named_reference_that_is_missing_stops_before_the_model(self):
        with self.assertRaises(SystemExit) as raised:
            run_preflight(['--date', self.day, '--reference', 'scripts/golden/does-not-exist.json'])
        reason = str(raised.exception)
        self.assertIn('does-not-exist.json', reason)
        self.assertIn('在模型判讀與寫入之前停止', reason)

    def test_no_reference_runs_as_an_unscored_diagnostic(self):
        with patch.object(Path, 'exists', return_value=False):
            printed, ns = run_preflight(['--date', self.day])
        self.assertIn('未評分的診斷', printed)
        self.assertIn('候選不能發布', printed)
        self.assertTrue(str(ns['reference']).endswith(self.day.replace('/', '-') + '.json'))

    def test_an_existing_default_reference_is_used_quietly(self):
        with patch.object(Path, 'exists', return_value=True):
            printed, ns = run_preflight(['--date', self.day])
        self.assertEqual(printed, '')
        self.assertIn('scripts', str(ns['reference']))

    def test_apply_without_a_candidate_stops_first(self):
        with self.assertRaises(SystemExit) as raised:
            run_preflight(['--date', self.day, '--apply'])
        self.assertIn('候選檔', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
