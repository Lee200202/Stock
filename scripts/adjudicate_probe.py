"""分類裁決的唯讀檢驗：拿有人工答案的日子，把每一檔都送問卷，重複幾輪，量三件事。

  一、問卷本身：多數票與人工答案相不相符、每一輪答得一不一樣。
  二、流程判對時：照定案規則走，會不會被問卷改錯。
  三、流程判錯時（公開的被漏掉、不該列的被列出、觀望方向相反）：能不能救回，還是標成未定交人工。

不寫任何資料、不改分類、不寄送。輸入的分類取自人工答案，與當輪擷取結果無關。
"""
import argparse
import json
from pathlib import Path

from replay_extract import DryBook, pl

LABEL = pl._VERDICT_LABEL
HIDDEN = {'history', 'ignored', 'uncertain', ''}
WATCH = ('watch_watch', 'watch_avoid')
# 只供這支檢驗找段落：正式流程裡，聽寫的名稱由當輪擷取帶在每一列上。
HEARD = {'8210': ['琴城', '情晨'], '2439': ['美綠'], '3533': ['加折', '家澤'], '3661': ['四星KY', '星KY'], '6770': ['立積電', '立即電'],
         '4966': ['普瑞'], '5536': ['聖輝'], '6533': ['金星科'], '6669': ['偉穎'], '3008': ['大力光']}


def build(golden):
    aliases = {}
    for heard, (code, _official) in pl.CONFIRMED_NAMES.items():
        aliases.setdefault(code, []).append(heard)
    signals = {c: [] for c in pl._VERDICT_ORDER}
    want = {}
    for code, name in (golden.get('names') or {}).items():
        accepted = list((golden.get('must') or {}).get(code) or [])
        cat = accepted[0] if accepted else 'ignored'
        want[code] = set(accepted) if accepted else set(HIDDEN)
        signals[cat].append({'name': name, 'code': code, 'evidence': [],
                             'aliases': list(dict.fromkeys(aliases.get(code, []) + HEARD.get(code, []) + [code]))})
    return signals, want


def accepted(cls, want):
    return cls in want or (cls in HIDDEN and bool(want & HIDDEN))


def settle(current, votes):
    """照定案規則一票一票看，回傳最後的 (狀態, 分類)。"""
    for n in range(1, len(votes) + 1):
        state, cls, _why = pl.verdict_settle(current, votes[:n], last=n == len(votes))
        if state != '再問':
            return state, cls
    return '未定', current


def wrong_starts(want):
    """這一檔如果被流程判錯，最常見會錯在哪：公開的被漏掉、不該列的被列出、觀望方向相反。"""
    public = {c for c in want if c not in HIDDEN}
    if not public:
        return ['watch_avoid']
    out = ['ignored']
    if len(public) == 1 and next(iter(public)) in WATCH:
        out.append(WATCH[1 - WATCH.index(next(iter(public)))])
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dates', required=True, help='逗號分隔，YYYY/MM/DD')
    parser.add_argument('--repeat', type=int, default=3)
    args = parser.parse_args()
    dry = DryBook(pl.open_sheets())
    try:
        pl.get_code_map()
    except Exception as e:
        print(f'官方代號表暫不可用（{type(e).__name__}），仍用確認名稱')
    tally = {'total': 0, 'right': 0, 'same': 0, 'unusable': 0, 'asked': 0,
             'a_keep': 0, 'a_open': 0, 'a_harm': 0, 'b_total': 0, 'b_fixed': 0, 'b_open': 0, 'b_harm': 0}
    days = []
    for raw_day in [d for d in args.dates.split(',') if d.strip()]:
        day = pl.norm_date(raw_day.strip())
        golden_file = Path('scripts/golden') / (day.replace('/', '-') + '.json')
        if not golden_file.exists():
            print(f'{day} 沒有人工答案，略過')
            continue
        golden = json.loads(golden_file.read_text(encoding='utf-8'))
        transcript, _polished = pl.existing_transcript(dry, '', day, snapshot={})
        if not transcript:
            print(f'{day} 讀不到原始逐字稿，略過')
            continue
        signals, want = build(golden)
        known, _notice = pl._verdict_known(dry, day)
        items, start = [], {}
        for cat in pl._VERDICT_ORDER:
            for row in signals[cat]:
                code = row['code']
                names = pl._signal_names(row) | {row['name']}
                sent, text = pl._verdict_mark(pl._own_segments(row, signals, transcript, 300, 500), names | {code})
                if not text:
                    print(f"  {row['name']}（{code}）原文找不到本股段落，不問")
                    continue
                start[code] = cat
                items.append((day + '#' + code, {'date': day, 'code': code, 'name': row['name'],
                                                 'heard': sorted(n for n in names if n not in (row['name'], code))[:6],
                                                 'known': known.get(code, []), 'passages': sent[:10], '_norm': pl._ev_norm(text),
                                                 '_names': sorted(names | {code})}))
        days.append((day, items, start, want))
    everything = [pair for _day, items, _start, _want in days for pair in items]
    votes, kept = {key: [] for key, _ in everything}, {key: [] for key, _ in everything}
    for round_no in range(args.repeat):
        answers = pl._ask_verdicts(everything, '', round_no)        # 所有日期同一個請求（依字數裝箱）
        for key, item in everything:
            cls, basis = pl.verdict_class(answers.get(key), item['_norm'], item['_names'])
            votes[key].append(cls)
            kept[key].append((cls, basis, answers.get(key) or {}))
    for day, items, start, want in days:
        print(f'\n{day}　{len(items)} 檔 × {args.repeat} 輪')
        for key, item in items:
            code = item['code']
            got = votes[key]
            usable = [v for v in got if v]
            top = max(dict.fromkeys(usable), key=usable.count) if usable else ''
            ok = accepted(top, want[code])
            agree = len(set(got)) == 1
            tally['total'] += 1
            tally['right'] += ok
            tally['same'] += agree
            tally['asked'] += len(got)
            tally['unusable'] += got.count('')
            state, cls = settle(start[code], got)
            kind_a = '維持' if cls == start[code] and state == '定案' else ('未定' if state == '未定' else ('改對' if accepted(cls, want[code]) else '改錯'))
            tally['a_keep' if kind_a in ('維持', '改對') else ('a_open' if kind_a == '未定' else 'a_harm')] += 1
            rescue = []
            for bad in wrong_starts(want[code]):
                state_b, cls_b = settle(bad, got)
                kind_b = '救回' if state_b == '定案' and accepted(cls_b, want[code]) else ('未定' if state_b == '未定' or cls_b == bad else '改錯')
                if state_b == '定案' and cls_b == bad:
                    kind_b = '改錯'                                    # 問卷替錯的分類背書
                tally['b_total'] += 1
                tally['b_fixed' if kind_b == '救回' else ('b_open' if kind_b == '未定' else 'b_harm')] += 1
                rescue.append(f"若流程判成{LABEL.get(bad, bad)}→{kind_b}")
            accept = '／'.join(LABEL.get(c, c) for c in sorted(want[code], key=lambda c: (c in HIDDEN, c)) if c) or '不公開'
            print(f"  {'✓' if ok else '✗'} {item['name']}（{code}）答案 {accept}｜問卷 {'、'.join(LABEL.get(v, v) or '不能用' for v in got)}"
                  f"｜流程判對時：{kind_a}；{'；'.join(rescue)}" + ('' if agree else '　←各輪不同'))
            if not ok or kind_a == '改錯' or '改錯' in ''.join(rescue):
                for cls, basis, a in kept[key]:
                    facts = [k for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'about_itself') if a.get(k) is True]
                    quotes = [q for qs in (a.get('quotes') or {}).values() for q in (qs or []) if isinstance(q, str)]
                    print(f"      {LABEL.get(cls, cls) or '不能用'}：{basis[:60]}｜{'、'.join(facts) or '全部為否'}；now={a.get('now')}、tone={a.get('tone')}"
                          f"｜引句 {' / '.join(q[:30] for q in quotes[:3])}" + (f"｜{str(a.get('unsure'))[:60]}" if a.get('unsure') else ''))
    t = tally
    if t['total']:
        print(f"\n合計 {t['total']} 檔 × {args.repeat} 輪")
        print(f"  問卷本身：多數票與人工答案相符 {t['right']}（{t['right'] / t['total']:.0%}）；各輪完全相同 {t['same']}（{t['same'] / t['total']:.0%}）；"
              f"答案不能用 {t['unusable']}／{t['asked']} 份")
        print(f"  流程判對時：維持正確 {t['a_keep']}、標成未定（仍維持正確）{t['a_open']}、被改錯 {t['a_harm']}")
        print(f"  流程判錯時（{t['b_total']} 種情況）：救回 {t['b_fixed']}、標成未定交人工 {t['b_open']}、仍錯而且定案 {t['b_harm']}")
    print('唯讀檢驗，未寫入任何資料')


if __name__ == '__main__':
    main()
