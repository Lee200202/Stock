"""複核確認的聽寫別名（pipeline/reviewed_aliases.json）：檢查與合併。

管理者 2026/11/03 授權：原稿唸的股名與實際公司不符、而且身分確定時，雲端複核可以自行修正當天資料並記下別名，
不必先問。這裡把「確定」的硬條件寫死，排程與套用工作流程共用：
  - 別名 2～8 字、代號與官方名稱一致。
  - 別名本身不能是另一家上市櫃公司的名稱（例：「力致」是 3483 的正式名稱，即使當天指的是力智 6719，
    也只修當天，不加全域別名，否則以後真的講力致會被換掉）。
  - 不能和程式內建別名衝突；同一個別名只能對到一檔。
用法：
  python scripts/daily_review/aliases.py check            檢查 main 上的別名檔（需要代號表）
  python scripts/daily_review/aliases.py merge BASE NEW   把 NEW 新增的別名併進 BASE（衝突就失敗）
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
FILE = ROOT / 'pipeline' / 'reviewed_aliases.json'
FIELDS = ('heard', 'code', 'name', 'date', 'basis')


def problems(entries, official, builtin):
    """official：{代號: 官方名稱}；builtin：程式內建 {別名: (代號, 名稱)}。回傳問題清單，空的才可用。"""
    out, seen = [], {}
    by_name = {}
    for code, name in official.items():
        by_name.setdefault(name, set()).add(code)
    for e in entries:
        miss = [f for f in FIELDS if not str(e.get(f) or '').strip()]
        if miss:
            out.append(f'{e.get("heard")}：缺少欄位 {"、".join(miss)}')
            continue
        heard, code, name = e['heard'], str(e['code']), e['name']
        if not 2 <= len(heard) <= 8:
            out.append(f'{heard}：長度要 2～8 字')
        if official.get(code) != name:
            out.append(f'{heard}：代號 {code} 的官方名稱是 {official.get(code) or "（查無）"}，不是 {name}')
        if by_name.get(heard, set()) - {code}:
            out.append(f'{heard}：本身是另一家公司（{"、".join(sorted(by_name[heard]))}）的名稱，只能修當天，不能加全域別名')
        if heard in builtin and str(builtin[heard][0]) != code:
            out.append(f'{heard}：程式內建已對到 {builtin[heard][0]}')
        if heard in seen and seen[heard] != code:
            out.append(f'{heard}：同一個別名對到兩檔')
        seen[heard] = code
    return out


def merge(base, new):
    """以 base 為準，加入 new 裡 base 沒有的別名；同別名不同代號就失敗。"""
    have = {e['heard']: e for e in base}
    out = list(base)
    for e in new:
        old = have.get(e['heard'])
        if old is None:
            out.append(e)
            have[e['heard']] = e
        elif str(old['code']) != str(e['code']):
            raise ValueError(f'別名衝突：{e["heard"]} 在 main 是 {old["code"]}，新提案是 {e["code"]}')
    return out


def load(path):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []


def dump(path, entries):
    text = '[\n' + ',\n'.join('  ' + json.dumps(e, ensure_ascii=False) for e in entries) + '\n]\n'
    Path(path).write_text(text, encoding='utf-8')


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'merge':
        merged = merge(load(sys.argv[2]), load(sys.argv[3]))
        dump(sys.argv[2], merged)
        print('別名', len(merged), '筆')
        return
    sys.path.insert(0, str(ROOT / 'pipeline'))
    import pipeline as pl
    official = {str(k): pl._display_name(v) for k, v in pl.get_code_map().items()}
    entries = load(FILE)
    # 載入時內建別名優先，所以 CONFIRMED_NAMES 裡同名但代號不同＝與內建衝突
    bad = problems(entries, official, pl.CONFIRMED_NAMES)
    if bad:
        raise SystemExit('別名檔不通過：\n' + '\n'.join(bad))
    print('別名檔通過', len(entries), '筆')


if __name__ == '__main__':
    main()
