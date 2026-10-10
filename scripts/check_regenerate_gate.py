"""Confirm the read-only replay and one-day apply share the publication floor."""
import os
import json

os.environ.setdefault('SPREADSHEET_ID', 'test-quality-gate')
from replay_extract import compare, invariants, pl
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

# A spoken 500–600 per share must not become 500–600 per lot when the same
# stock's transcript explicitly gives a six-figure per-lot amount.
unit_source = '世芯-KY今天全出。每股賺600元，一張賺60萬。'
bad_lot = {'name': '世芯-KY', 'code': '3661', 'reason': '世芯-KY全出，每張至少賺5、6百元。'}
assert pl._lot_profit_unit_conflict(bad_lot['reason'], unit_source, bad_lot)
assert not pl._lot_profit_unit_conflict(bad_lot['reason'], '另一檔一張賺60萬。', bad_lot)
assert not pl._lot_profit_unit_conflict('世芯-KY每張賺60萬元。', unit_source, bad_lot)
print('daily publication: per-share/per-lot mismatch blocked for the named stock')

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

# The context-writing pass checks only sentences it adds. A model's original
# published note can still invent a period (10/08 台積電: 下半年 vs 下週).
tsmc = {'name': '台積電', 'code': '2330', 'evidence': ['下個禮拜台積電要公布營收、法說會']}
raw = '下個禮拜台積電要公布營收、法說會。' + '其他行情說明。' * 30
wrong = {**tsmc, 'note': '台積電穩穩抱著，下半年法說會前表現強勢。'}
_, hard, _ = pl.quality_overview({'holdings': [wrong]}, raw)
assert any('台積電' in e and '時間詞無本股原句：下半年' in e for e in hard), hard
right = {**tsmc, 'note': '台積電穩穩抱著，下週法說會前留意表現。'}
_, hard, _ = pl.quality_overview({'holdings': [right]}, raw)
assert not any('時間詞無本股原句' in e for e in hard), hard
print('daily publication: unsupported stock time blocked, 下個禮拜／下週 accepted')
_, hard, _ = pl.quality_overview({'holdings': [{'name': '甲公司', 'note': '原稿說甲公司仍在持股名單，節目後段再次明講尚持有。'}]}, '')
assert any('來源或編輯過程' in e for e in hard), hard
_, hard, _ = pl.quality_overview({'watch_watch': [{'name': '乙公司', 'reason': '乙公司出現第一根長紅棒，續抱看好。'}]}, '')
assert any('觀望卻寫成當前持股' in e for e in hard), hard
_, hard, _ = pl.quality_overview({'watch_watch': [{'name': '乙公司', 'reason': '乙公司出現第一根長紅棒，為會員持有的標的之一。'}]}, '')
assert any('觀望卻寫成當前持股' in e for e in hard), hard
_, hard, _ = pl.quality_overview({'watch_watch': [{'name': '乙公司', 'reason': '乙公司出現第一根長紅棒，須等回測季線再確認。'}]}, '')
assert not any('觀望卻寫成當前持股' in e or '來源或編輯過程' in e for e in hard), hard
print('daily publication: editorial process text and watch/holding contradiction blocked')
assert pl.public_narrative('台積電缺口守三天。原稿說下週公布營收。') == '台積電缺口守三天。下週公布營收。'
assert pl.public_narrative('力積電目前仍在持股名單，節目後段再次明講尚持有。') == '力積電目前仍持有。'
print('public narrative: source wrappers removed without changing claims')
segment = '你有沒有看到創意？創意是不是回來碰季線，就黏在季線。所以記憶體後面有一波。這一個發動的時間要抓住。'
passages, marked = pl._verdict_mark([segment], {'創意'})
assert '創意是不是回來碰季線' in marked and '記憶體後面' not in marked and '發動' not in marked, passages
print('class adjudication: new sector topic cannot become the preceding stock stance')
bad_level = {'name': '力積電', 'code': '6770', 'price': '74', 'note': '力積電目前上漲，正壓在1545元關卡等待突破。',
             'evidence': ['力積電目前上漲', '正壓在1545元關卡等待突破']}
_, hard, _ = pl.quality_overview({'holdings': [bad_level]}, '力積電目前上漲。正壓在1545元關卡等待突破。')
assert any('力積電' in e and '價位與本股已核對價位相差過大：1545' in e for e in hard), hard
same_scale = {**bad_level, 'note': '力積電目前上漲，正壓在75元關卡等待突破。'}
_, hard, _ = pl.quality_overview({'holdings': [same_scale]}, '力積電目前上漲，正壓在75元關卡等待突破。')
assert not any('價位與本股已核對價位相差過大' in e for e in hard), hard
print('daily publication: grossly misattributed price level blocked when a verified row price exists')

# A real transcript can contain the number but leave the stock unnamed when
# the speaker switches charts. The per-day human audit must reject a known
# wrong attribution even if a model cites the same raw segment again.
golden = {'names': {'2330': '台積電', '6770': '力積電', '3443': '創意'},
          'must': {'2330': ['holdings'], '6770': ['holdings']},
          'must_not': ['3443'],
          'note_must_not': {'2330': ['下半年'], '6770': ['1545']}}
creative_bad = {'name': '創意', 'code': '3443', 'reason': '創意碰季線，記憶體族群將有一波行情。'}
rows = {'holdings': [wrong, bad_level], 'watch_watch': [creative_bad]}
ok, report = compare(rows, golden)
assert not ok and '下半年' in report and '1545' in report and '創意' in report, report
rows = {'holdings': [right, same_scale]}
ok, report = compare(rows, golden)
assert ok, report
print('daily golden audit: known wrong stock phrases blocked independently of raw transcript')


class TradeSheet:
    def get_all_values(self):
        return [['日期', '代號', '名稱', '方向', '來源影片ID'],
                ['2026/10/08', '3661', '世芯-KY', '賣出', 'CMONEY-audited']]


class TradeBook:
    def worksheet(self, name):
        if name == '操作紀錄':
            return TradeSheet()
        raise KeyError(name)


today = '2026/10/08'
signals = {'sell': [{'code': '3661', 'name': '世芯-KY', 'reason': '今日賣出。'}],
           'holdings': [{'code': '3661', 'name': '世芯-KY', 'note': '其餘續抱。'},
                        {'code': '2330', 'name': '台積電', 'note': '仍持有。'}]}
pl.drop_sms_sold_holdings(TradeBook(), signals, today)
assert [row['code'] for row in signals['holdings']] == ['2330'], signals['holdings']
print('daily publication: SMS-sold stock cannot re-enter holdings before article generation')

def neutral_case(quote, name):
    answer = {'buy_today': False, 'sell_today': False, 'holding_now': False,
              'past_trade': False, 'about_itself': True, 'now': 'none', 'tone': 'neutral',
              'quotes': {'stance': [quote]}}
    return pl.verdict_class(answer, pl._ev_norm(quote), {name})[0]


assert neutral_case('那今天剛開始漲的被動元件，金山電，你有沒有看到被動元件？', '金山電') == 'watch_watch'
assert neutral_case('普瑞KY也會跟著衝出去', '普瑞KY') == 'watch_watch'
assert neutral_case('創意是不是回來碰季線，就黏在季線。', '創意') == 'ignored'
assert neutral_case('我有在觀察創見。', '創見') == 'ignored'
assert neutral_case('南亞科不是記憶體龍頭。', '南亞科') == 'ignored'
print('watch boundary: forward stock-specific signal accepted; mention/position/comparison excluded')

mixed = {'sell': [{'name': '甲公司', 'reason': '技術指標未同步創高，每張獲利60萬元以上及5、6百元。'}],
         'holdings': [{'name': '乙公司', 'note': '成本74元，短線雖小幅套牢但買進在成本之上且早盤未賠。後續仍持有。'}]}
_, hard, _ = pl.quality_overview(mixed)
assert any('每張獲利混入' in e for e in hard), hard
assert any('同時寫套牢與未賠' in e for e in hard), hard
pl.repair_public_note_contradictions(mixed)
assert mixed['sell'][0]['reason'] == '技術指標未同步創高，每張獲利60萬元以上。'
assert mixed['holdings'][0]['note'] == '成本74元，短線小幅套牢。後續仍持有。'
_, hard, _ = pl.quality_overview(mixed)
assert not any('每張獲利混入' in e or '同時寫套牢與未賠' in e for e in hard), hard
print('daily publication: mixed profit units and contradictory position claims repaired before write')
