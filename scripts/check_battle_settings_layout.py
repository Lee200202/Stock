"""戰情設定版面驗收（v135）。行情用假資料。

    python scripts/check_battle_settings_layout.py [--shots 目錄]

寬螢幕（981px 以上）：跳空方向、均量基準、量能比較橫向一列，各自對齊下方的最低開盤幅度、最低日量倍數、最低當日量。
平板（721–980px）：每欄放不下三顆選項（均量基準有 5 日、20 日、自訂），三組各佔一列；數值欄位仍是三欄。
手機：單欄依序排列。任何寬度都不出現水平捲軸，選項不被裁切、同一組的選項在同一行。
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_battle_back as base  # noqa: E402  共用本機組站與假行情

MEASURE = """() => {
  const box = e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width}; };
  const choices = [...document.querySelectorAll('#battlePreferences .battle-choice-row > .battle-choice')];
  const fields = [...document.querySelectorAll('#battlePreferences .battle-number-grid > .battle-field')];
  const pills = choices.map(c => [...c.querySelectorAll('.battle-pill')].map(box));
  return {choices: choices.map(box), fields: fields.map(box), pills,
          legends: choices.map(c => c.querySelector('legend').innerText.trim()),
          labels: fields.map(f => f.childNodes[0].textContent.trim()),
          overflow: document.documentElement.scrollWidth - window.innerWidth,
          panel: box(document.getElementById('battlePreferences'))};
}"""
near = lambda a, b, tol=2: abs(a - b) <= tol


def check(m, w):
    assert m['legends'] == ['跳空方向', '均量基準', '量能比較'], m['legends']
    assert m['labels'] == ['最低開盤幅度', '最低日量倍數', '最低當日量'], m['labels']
    assert m['overflow'] <= 0, f'{w}px 出現水平捲軸（多 {m["overflow"]}px）'
    c, f = m['choices'], m['fields']
    for i, group in enumerate(m['pills']):
        assert group and all(p['l'] >= c[i]['l'] - 1 and p['r'] <= c[i]['r'] + 1 for p in group), f'{w}px 第 {i + 1} 組選項超出自己的欄位'
        assert all(near(p['t'], group[0]['t']) for p in group), f'{w}px 第 {i + 1} 組的選項沒有排在同一列'
    assert [len(g) for g in m['pills']] == [2, 3, 2], '均量基準應有 5 日、20 日、自訂三顆'
    if 720 < w <= 980:
        assert c[0]['t'] < c[1]['t'] < c[2]['t'] and all(near(x['l'], c[0]['l']) for x in c), f'{w}px 三組選項應各佔一列'
        assert all(near(x['t'], f[0]['t']) for x in f) and f[0]['t'] >= c[2]['b'], f'{w}px 數值欄位應在選項下方排成一列'
        return '三組選項各佔一列，數值欄位三欄'
    if w > 980:
        assert all(near(x['t'], c[0]['t']) for x in c), f'{w}px 三組選項沒有排在同一列：{[round(x["t"]) for x in c]}'
        assert all(near(x['t'], f[0]['t']) for x in f), f'{w}px 三個數值欄位沒有排在同一列'
        assert f[0]['t'] >= max(x['b'] for x in c), f'{w}px 數值欄位應在選項下方'
        for i in range(3):
            assert near(c[i]['l'], f[i]['l']) and near(c[i]['w'], f[i]['w'], 3), f'{w}px 第 {i + 1} 欄上下沒有對齊：{c[i]["l"]:.0f}/{f[i]["l"]:.0f}'
        assert c[0]['l'] < c[1]['l'] < c[2]['l']
        return f'三組選項一列、對齊下方三個欄位（每欄 {c[0]["w"]:.0f}px）'
    order = [x['t'] for x in c] + [x['t'] for x in f]
    assert order == sorted(order) and len(set(round(t) for t in order)) == 6, f'{w}px 應該單欄依序排列'
    assert all(near(x['l'], c[0]['l']) for x in c + f)
    return '單欄依序排列'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shots', default='')
    args = ap.parse_args()
    srv = base.build_local()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in (1440, 1280, 1100, 1000, 900, 768, 390, 360):
            pg = browser.new_page(viewport={'width': w, 'height': 900}, has_touch=w < 700)
            errs = []
            pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
            pg.route(base.FAKE_API, base.make_router([]))
            base.open_battle(pg, url)
            pg.wait_for_selector('#battlePreferences .battle-choice-row .battle-pill')
            for theme in ('light', 'dark'):
                pg.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
                note = check(pg.evaluate(MEASURE), w)
                if args.shots:
                    Path(args.shots).mkdir(parents=True, exist_ok=True)
                    pg.evaluate("document.getElementById('battlePreferences').scrollIntoView({block: 'start'})")
                    pg.wait_for_timeout(250)
                    pg.screenshot(path=str(Path(args.shots) / f'battle-settings-{w}-{theme}.png'))
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px：{note}；無水平捲軸、選項未被裁切（淺色與深色）')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
