"""分類裁決的唯讀檢驗：拿有人工答案的日子，把每一檔都送問卷，重複幾輪，看答得對不對、每一輪一不一樣。

不寫任何資料、不改分類、不寄送。輸入的分類取自人工答案（每一檔取第一個可接受的類別），
所以量到的是「問卷本身」的準確與一致，與當輪擷取結果無關。
"""
import argparse
import json
from pathlib import Path

from replay_extract import DryBook, pl

LABEL = pl._VERDICT_LABEL
HIDDEN = {'history', 'ignored', 'uncertain', ''}


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
        signals[cat].append({'name': name, 'code': code, 'aliases': aliases.get(code, []), 'evidence': []})
    return signals, want


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
    total = right = same = unusable = asked = 0
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
        items = []
        for cat in pl._VERDICT_ORDER:
            for row in signals[cat]:
                code = row['code']
                names = pl._signal_names(row) | {row['name']}
                sent, text = pl._verdict_mark(pl._own_segments(row, signals, transcript, 300, 500), names | {code})
                if not text:
                    print(f"  {row['name']}（{code}）原文找不到本股段落，不問")
                    continue
                items.append((code, {'name': row['name'], 'heard': sorted(n for n in names if n != row['name'])[:6],
                                     'known': known.get(code, []), 'passages': sent[:10], '_norm': pl._ev_norm(text)}))
        votes, kept = {code: [] for code, _ in items}, {code: [] for code, _ in items}
        for round_no in range(args.repeat):
            answers = pl._ask_verdicts(items, day, round_no)
            for code, item in items:
                cls, basis = pl.verdict_class(answers.get(code), item['_norm'])
                votes[code].append(cls)
                kept[code].append((cls, basis, answers.get(code) or {}))
        print(f'\n{day}　{len(items)} 檔 × {args.repeat} 輪')
        for code, item in items:
            got = votes[code]
            usable = [v for v in got if v]
            top = max(set(usable), key=usable.count) if usable else ''
            ok = top in want[code] or (top in HIDDEN and want[code] & HIDDEN)
            agree = len(set(got)) == 1
            total += 1
            right += ok
            same += agree
            asked += len(got)
            unusable += got.count('')
            accept = '／'.join(LABEL.get(c, c) for c in sorted(want[code], key=lambda c: (c in HIDDEN, c)) if c) or '不公開'
            print(f"  {'✓' if ok else '✗'} {item['name']}（{code}）答案 {accept}｜問卷 {'、'.join(LABEL.get(v, v) or '不能用' for v in got)}"
                  + ('' if agree else '　←各輪不同'))
            if not ok or not agree:
                for cls, basis, a in kept[code]:
                    facts = [k for k in ('buy_today', 'sell_today', 'holding_now', 'past_trade', 'about_itself') if a.get(k) is True]
                    quotes = [q for qs in (a.get('quotes') or {}).values() for q in (qs or []) if isinstance(q, str)]
                    print(f"      {LABEL.get(cls, cls) or '不能用'}：{basis[:60]}｜{'、'.join(facts) or '全部為否'}；now={a.get('now')}、tone={a.get('tone')}"
                          f"｜引句 {' / '.join(q[:30] for q in quotes[:3])}" + (f"｜{str(a.get('unsure'))[:60]}" if a.get('unsure') else ''))
    if total:
        print(f'\n合計 {total} 檔：多數票與人工答案相符 {right}（{right / total:.0%}）；各輪完全相同 {same}（{same / total:.0%}）；'
              f'答案不能用 {unusable}／{asked} 份')
    print('唯讀檢驗，未寫入任何資料')


if __name__ == '__main__':
    main()
