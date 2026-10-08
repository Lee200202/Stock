"""Confirm the read-only replay and one-day apply share the publication floor."""
import os
import json

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

# Same chart numbers do not make an account of the day's market the same
# point as an entry/position-sizing lesson. The 10/08 apply lost two lessons
# when the semantic judge treated shared 60/35 as sufficient evidence.
market = '大盤今天拉回，指數觸及60分K的35均後收斂，外資短線賣壓減輕，市場仍在等待美元指數回落。'
lesson = '操作股票應在60分K的35均附近分批布局，先確認持股成本與資金比例，避免急著追高買進。'
rows = {'market': [{'kind': 'level', 'text': market, '_evidence_verified': True},
                   {'kind': 'event', 'text': '台積電缺口守住三天，法人說明會即將公布，權值股牽動指數短線走勢。', '_evidence_verified': True},
                   {'kind': 'view', 'text': lesson, '_evidence_verified': True}]}
assert pl._theme_overlap(market, lesson) < pl.ARTICLE_SAME_THEME_ACROSS_NUMBERS
with patch.object(pl, 'call_gemini', return_value=json.dumps({'groups': [['p0', 'p2']]})), \
        patch.object(pl, 'budget_left', return_value=2000), patch.object(pl, 'GEMINI_KEYS', ['test']), \
        patch.object(pl, 'note_decision', lambda *a, **k: None), patch.dict(pl._QUOTA_STOP, {'daily': False}):
    assert pl.judge_same_theme(rows) == 0
assert not rows['market'][2].get('_duplicate_point')
print('cross-chapter semantic audit: shared 60/35 alone does not drop a lesson')
