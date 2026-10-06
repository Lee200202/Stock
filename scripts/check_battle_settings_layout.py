"""戰情設定版面與操作驗收（v137）。行情用假資料。

    python scripts/check_battle_settings_layout.py [--shots 目錄]

門檻在各自的條件卡片裡：
  「開盤跳空」卡片：跳空方向、最低開盤幅度。
  「爆量與最低量」卡片：均量基準、量能比較、最低日量倍數、最低當日量。
  設定頁上方不再有另外一排方向／均量／量能的選項。
使用中而且有設定的卡片佔整列；寬螢幕時卡片裡的設定橫向排開，平板兩欄，手機單欄。
留白：卡片內距至少 20px、設定之間的間隔至少 24px、卡片與卡片之間至少 24px；任何寬度都沒有水平捲軸，沒有被裁切的控制項。
操作：整張條件卡片都可以點（加入／移除），不是只有橢圓按鈕；卡片裡的設定與來源連結照常操作，不會誤觸加入或移除。
"""
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_battle_back as base  # noqa: E402  共用本機組站與假行情

P = '#battlePreferences '
MEASURE = """() => {
  const w = document.getElementById('battlePreferences'), box = e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height}; };
  const card = id => {
    const c = w.querySelector('.battle-feature-box[data-bdrag="' + id + '"]'), x = c.querySelector('[data-bextra]'), cs = getComputedStyle(c);
    const parts = [...x.children].filter(e => e.offsetParent);
    return {box: box(c), pad: [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft].map(parseFloat), cursor: cs.cursor, extraHidden: x.hidden,
      names: parts.map(e => (e.querySelector('legend') || e.childNodes[0]).textContent.trim()), parts: parts.map(box),
      pills: parts.map(e => [...e.querySelectorAll('.battle-pill')].filter(b => b.offsetParent).map(box)),
      controls: [...x.querySelectorAll('button, input')].filter(e => e.offsetParent).map(box)};
  };
  const grid = box(w.querySelector('.battle-feature-grid')), shown = [...w.querySelectorAll('.battle-feature-box:not([hidden])')];
  return {gap: card('useGap'), vol: card('useVolume'), grid, panel: box(w), shown: shown.map(e => e.dataset.bdrag), boxes: shown.map(box),
    oldRow: !!w.querySelector('.battle-basic, .battle-choice-row, .battle-number-grid'),
    topChoices: [...w.querySelectorAll('[data-bchoice="direction"], [data-bdays], [data-bchoice="volumeMode"], [data-bpref]')].filter(e => !e.closest('.battle-feature-box')).length,
    overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    clipped: [...w.querySelectorAll('button, select, input, a')].filter(e => e.offsetParent && (e.getBoundingClientRect().right > w.getBoundingClientRect().right + 1 || e.getBoundingClientRect().left < w.getBoundingClientRect().left - 1)).length,
    small: [...w.querySelectorAll('button, select, input')].filter(e => e.offsetParent && e.getBoundingClientRect().height < 43.5).map(e => (e.className || e.id) + ':' + Math.round(e.getBoundingClientRect().height))}; }"""
near = lambda a, b, tol=2: abs(a - b) <= tol


def rows(parts):
    """同一列的元件歸在一起（依上緣）。"""
    out = []
    for p in sorted(parts, key=lambda x: (round(x['t']), x['l'])):
        if out and near(out[-1][0]['t'], p['t'], 6):
            out[-1].append(p)
        else:
            out.append([p])
    return out


def check(m, w):
    assert not m['oldRow'] and m['topChoices'] == 0, f'{w}px 設定頁上方不應該還有獨立的方向／均量／量能選項'
    assert m['shown'] == ['useGap', 'useVolume'], m['shown']
    assert m['gap']['names'] == ['跳空方向', '最低開盤幅度'], m['gap']['names']
    assert m['vol']['names'] == ['均量基準', '量能比較', '最低日量倍數', '最低當日量'], m['vol']['names']
    assert [len(x) for x in m['gap']['pills']] == [2, 0] and [len(x) for x in m['vol']['pills']] == [3, 2, 0, 0], (m['gap']['pills'], m['vol']['pills'])
    assert m['overflow'] <= 0 and not m['clipped'] and not m['small'], f'{w}px 水平捲軸 {m["overflow"]}、被裁切 {m["clipped"]}、太小的控制項 {m["small"]}'
    for name, c in (('開盤跳空', m['gap']), ('爆量與最低量', m['vol'])):
        assert c['cursor'] == 'pointer', f'{w}px {name} 卡片的游標是 {c["cursor"]}'
        # 使用中而且有設定的卡片佔整列
        assert near(c['box']['l'], m['grid']['l']) and near(c['box']['r'], m['grid']['r']), f'{w}px {name} 卡片沒有佔滿整列'
        assert min(c['pad']) >= 20, f'{w}px {name} 卡片內距 {c["pad"]}'
        for part, pills in zip(c['parts'], c['pills']):
            assert part['l'] >= c['box']['l'] + c['pad'][3] - 1 and part['r'] <= c['box']['r'] - c['pad'][1] + 1, f'{w}px {name} 的設定超出卡片內距'
            assert not pills or all(near(p['t'], pills[0]['t']) for p in pills), f'{w}px {name} 同一組的選項沒有排在同一列'
        lines = rows(c['parts'])
        for line in lines:                                                           # 同一列相鄰的設定之間
            for a, b in zip(line, line[1:]):
                assert b['l'] - a['r'] >= 24, f'{w}px {name} 相鄰設定的間隔只有 {b["l"] - a["r"]:.0f}px'
        for a, b in zip(lines, lines[1:]):                                           # 上下兩列之間
            assert min(x['t'] for x in b) - max(x['b'] for x in a) >= 24, f'{w}px {name} 上下兩列設定的間隔不足'
        c['lines'] = [len(x) for x in lines]
    assert m['vol']['box']['t'] - m['gap']['box']['b'] >= 24, f'{w}px 兩張卡片之間只隔 {m["vol"]["box"]["t"] - m["gap"]["box"]["b"]:.0f}px'
    if w > 1180:
        assert m['gap']['lines'] == [2] and m['vol']['lines'] == [4], (w, m['gap']['lines'], m['vol']['lines'])
        return '卡片裡的設定橫向一列（開盤跳空 2 項、爆量與最低量 4 項）'
    if w > 720:
        assert m['gap']['lines'] == [2] and m['vol']['lines'] in ([3, 1], [2, 2]), (w, m['gap']['lines'], m['vol']['lines'])
        return f'卡片裡的設定分兩列（爆量與最低量 {"＋".join(map(str, m["vol"]["lines"]))}）'
    assert m['gap']['lines'] == [1, 1] and m['vol']['lines'] == [1, 1, 1, 1], (w, m['gap']['lines'], m['vol']['lines'])
    return '卡片裡的設定單欄依序排列'


def clicks(pg, w):
    """整張卡片可點：點卡片的說明文字、右下角空白處都會移除／加入；點卡片裡的設定、來源連結不會。"""
    groups = lambda: pg.evaluate('window.BattleSettings.get().groups')
    hit = lambda sel, fx, fy: pg.evaluate("""([sel, fx, fy]) => { const e = document.querySelector(sel); e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect();
        const x = r.left + r.width * fx, y = r.top + r.height * fy, at = document.elementFromPoint(x, y); return [x, y, at ? at.className || at.tagName : '']; }""", [sel, fx, fy])
    gap, vol = P + '.battle-feature-box[data-bdrag="useGap"]', P + '.battle-feature-box[data-bdrag="useVolume"]'
    # 卡片內距裡的一點（右下角往內 10px）：不是橢圓按鈕、不是說明文字，也不是來源連結
    corner = lambda sel: pg.evaluate("""(sel) => { const e = document.querySelector(sel); e.scrollIntoView({block: 'center'}); const r = e.getBoundingClientRect();
        const x = r.right - 10, y = r.bottom - 10, at = document.elementFromPoint(x, y); return [x, y, at ? String(at.className || at.tagName) : '']; }""", sel)
    assert groups() == [['useGap', 'useVolume']]
    # 卡片裡的設定：點方向、改數字、在輸入框裡點一下，條件都還在
    pg.click(gap + ' [data-bchoice="direction"][data-value="down"]')
    assert groups() == [['useGap', 'useVolume']] and pg.evaluate('window.BattleSettings.get().direction') == 'down'
    pg.click(gap + ' [data-bchoice="direction"][data-value="up"]')
    pg.click(gap + ' [data-bpref="gapPct"]')
    x, y, at = hit(gap + ' [data-bextra]', 0.5, 0.02)                                # 設定區的空白處（分隔線下方）
    pg.mouse.click(x, y)
    assert groups() == [['useGap', 'useVolume']], '點卡片裡的設定區不應該移除條件'
    # 從輸入框按下、拖到卡片上方放開（選字）：不算點卡片
    b = pg.locator(gap + ' [data-bpref="gapPct"]').bounding_box()
    pg.mouse.move(b['x'] + 30, b['y'] + b['height'] / 2)
    pg.mouse.down()
    pg.mouse.move(b['x'] + 10, b['y'] - 150, steps=6)
    pg.mouse.up()
    assert groups() == [['useGap', 'useVolume']], '在輸入框裡拖著選字不應該移除條件'
    # 來源連結：不加入也不移除（連結另開視窗，這裡攔下不真的開）
    pg.evaluate("document.querySelectorAll('#battlePreferences .battle-origin a').forEach(a => a.addEventListener('click', e => e.preventDefault()))")
    pg.click(gap + ' .battle-origin a')
    assert groups() == [['useGap', 'useVolume']], '點來源連結不應該移除條件'
    # 點卡片的說明文字：移除；清單回到只剩一張
    pg.click(gap + ' .battle-feature-hint')
    assert groups() == [['useVolume']], groups()
    assert pg.evaluate("[...document.querySelectorAll('#battlePreferences .battle-feature-box:not([hidden])')].map(b => b.dataset.bdrag)") == ['useVolume']
    # 到分類裡點卡片右下角的空白處：加入（不是點到橢圓按鈕）
    pg.click(P + '[data-bcat-filter="volatility"]')
    x, y, at = corner(gap)
    assert 'battle-feature-box' in at, f'{w}px 沒有點在卡片空白處：{at}'
    pg.mouse.click(x, y)
    assert groups() == [['useVolume', 'useGap']], groups()
    assert pg.get_attribute(gap + ' [data-bflag]', 'aria-pressed') == 'true' and not pg.evaluate("document.querySelector('#battlePreferences .battle-feature-box[data-bdrag=useGap] [data-bextra]').hidden")
    # 沒有設定的卡片（向上缺口沒有回補有一個數值；選一張完全沒有設定的：K 棒結構的多方反轉）也是整張可點
    pg.click(P + '[data-bcat-filter="candle"]')
    x, y, at = corner(P + '.battle-feature-box[data-bdrag="bullReversal"]')
    assert 'battle-feature-box' in at, f'{w}px 沒有點在卡片空白處：{at}'
    pg.mouse.click(x, y)
    assert groups() == [['useVolume', 'useGap', 'bullReversal']], groups()
    pg.mouse.click(x, y)
    assert groups() == [['useVolume', 'useGap']], groups()
    # 鍵盤：橢圓按鈕仍是鍵盤操作的入口
    pg.focus(P + '.battle-feature-box[data-bdrag="bullReversal"] [data-bflag]')
    pg.keyboard.press('Enter')
    assert groups() == [['useVolume', 'useGap', 'bullReversal']]
    pg.keyboard.press('Enter')
    assert groups() == [['useVolume', 'useGap']]
    # 結果卡片也整張可點：點指標數字那一區會開個股；點「條件、數值與歷史缺口」是展開，不開個股
    pg.click('#battleReset')
    base.wait_list_done(pg, 47)
    pg.evaluate("() => { window.__opened = []; window.__openBattleStock = c => { window.__opened.push(c); }; }")   # 換成記錄用的假函式，不真的開個股
    first = '#battleCards .battle-card'
    assert pg.evaluate("getComputedStyle(document.querySelector('#battleCards .battle-card')).cursor") == 'pointer'
    pg.click(first + ' .battle-metrics')
    pg.click(first + ' details summary')
    assert pg.evaluate("document.querySelector('#battleCards .battle-card details').open") is True
    pg.click(first + ' .battle-name')
    assert pg.evaluate('window.__opened') == ['1000', '1000'], pg.evaluate('window.__opened')


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
            pg.wait_for_selector(P + '.battle-feature-box[data-bdrag="useGap"] [data-bextra] .battle-pill')
            base.wait_list_done(pg, 47)
            for theme in ('light', 'dark'):
                pg.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
                note = check(pg.evaluate(MEASURE), w)
                if args.shots:
                    Path(args.shots).mkdir(parents=True, exist_ok=True)
                    pg.locator('#battlePreferences').screenshot(path=str(Path(args.shots) / f'battle-settings-{w}-{theme}.png'))
            clicks(pg, w)
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px：{note}；整張卡片可點、設定與連結不誤觸；留白足夠、無水平捲軸（淺色與深色）')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
