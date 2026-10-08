"""Confirm the read-only replay and one-day apply share the publication floor."""
import os

os.environ.setdefault('SPREADSHEET_ID', 'test-quality-gate')
from replay_extract import invariants, pl
from unittest.mock import patch


def point(kind, duplicate=False):
    return {'kind': kind, 'text': '有原句核對的重點', '_evidence_verified': True,
            '_duplicate_point': duplicate}


long_source = '今日盤勢與操作教學。' * 700
assert len(pl._ev_norm(long_source)) >= pl.LESSON_MIN_SOURCE
complete = {'market': [point('level') for _ in range(3)] + [point('view') for _ in range(3)]}
with patch.object(pl, 'source_segments', return_value=[]), patch.object(pl, 'source_inventory', return_value=[]):
    errors, _ = invariants(complete, long_source)
    assert not errors, errors

    incomplete = {'market': [point('level'), point('level'), point('level', True),
                             point('view'), point('view'), point('view')]}
    errors, warnings = invariants(incomplete, long_source)
    assert not errors and any('只有 2 個不同主題' in w for w in warnings), (errors, warnings)
    errors, _ = invariants(incomplete, '短原稿')
    assert not any('只有 2 點' in e for e in errors), errors
print('one-day apply and replay: minimum verified points gate OK')
_, hard, _ = pl.quality_overview({'market': [point('level'), point('view')]}, long_source)
assert any('盤勢只有 1 點' in e for e in hard), hard
assert any('教學只有 1 點' in e for e in hard), hard
_, hard, _ = pl.quality_overview({'sell': [{'name': '測試公司', 'reason': '每張賺取5、6百萬元（或5、6百元）。'}]}, '')
assert any('互相矛盾的單位' in e for e in hard), hard
print('daily publication: points and contradictory money block release OK')
