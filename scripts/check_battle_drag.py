"""戰情條件分組與拖曳驗收（v136）。行情用假資料，拖曳用真的滑鼠事件。

    python scripts/check_battle_drag.py

條件分成幾組：同一組一種邏輯、組與組之間另一種（例：（開盤跳空 且 爆量）或 均線位置）。
- 整張卡片可以拖進某一組或「新增一組」；組裡的標籤可以拖到別組（移動）或拖回清單（只從那一組拿掉）。
- 不拖也行：先點一個組，再點條件。同一個條件可以放進不同的組；最多四組。
- 卡片裡的週期按鈕與數值輸入框照常點選、選字，不會把卡片拖走。
- 均量基準可以自訂天數；結果清單與每張卡片照新的組合重算。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_battle_back as base  # noqa: E402  共用本機組站與假行情

STATE = """() => {
  const p = window.BattleSettings.get(), wrap = document.getElementById('battlePreferences');
  const shown = [...wrap.querySelectorAll('.battle-group[data-bgroup]')].map(g => [...g.querySelectorAll('.battle-expression-chip')].map(c => c.dataset.bdrag));
  const target = wrap.querySelector('.battle-group.is-target');
  return {groups: p.groups, mode: p.groupLogic, shown, target: target ? Number(target.dataset.bgroup) : -1,
          hasNew: !!wrap.querySelector('.battle-group-new'), formula: document.getElementById('battleFormula').textContent,
          pressed: [...wrap.querySelectorAll('.battle-feature[aria-pressed=true]')].map(e => e.dataset.bflag)};
}"""
G, V, T, M, K, B, O, R = 'useGap', 'useVolume', 'useTrend', 'useMacd', 'useKD', 'useBoll', 'useBody', 'useBreakout'


def card(pg, key):
    return pg.locator(f'#battlePreferences .battle-feature-box[data-bdrag="{key}"]')


def chip(pg, group, key):
    return pg.locator(f'#battleGroups .battle-group[data-bgroup="{group}"] .battle-expression-chip[data-bdrag="{key}"]')


def point(pg, locator, fx=0.5, fy=0.5):
    locator.scroll_into_view_if_needed()
    b = locator.bounding_box()
    return b['x'] + b['width'] * fx, b['y'] + b['height'] * fy


def drag(pg, start_locator, end_locator, sfx=0.5, sfy=0.5, efx=0.5, efy=0.8):
    # 兩端都要在畫面裡：先算起點，按住之後再算終點（頁面不會在拖曳中捲動）。
    pg.evaluate("document.querySelector('#battlePreferences .battle-optional-head').scrollIntoView({block: 'start'})")
    pg.wait_for_timeout(120)
    sb, eb = start_locator.bounding_box(), end_locator.bounding_box()
    start = (sb['x'] + sb['width'] * sfx, sb['y'] + sb['height'] * sfy)
    end = (eb['x'] + eb['width'] * efx, eb['y'] + eb['height'] * efy)
    pg.mouse.move(*start)
    pg.mouse.down()
    pg.mouse.move(start[0] + 6, start[1] + 6, steps=3)
    pg.mouse.move(*end, steps=14)
    pg.mouse.up()
    pg.wait_for_timeout(180)


def group(pg, n):
    return pg.locator(f'#battleGroups .battle-group[data-bgroup="{n}"]')


def new_zone(pg):
    return pg.locator('#battleGroups .battle-group-new')


def click_box(pg, locator, fx=0.5):
    """點在框的內距上（離下緣 7px）：不是橢圓按鈕，也不是標籤。"""
    locator.scroll_into_view_if_needed()
    b = locator.bounding_box()
    x, y = b['x'] + b['width'] * fx, b['y'] + b['height'] - 7
    hit = pg.evaluate("([x, y]) => { const e = document.elementFromPoint(x, y); return e ? e.className : ''; }", [x, y])
    assert 'battle-group' in hit and 'pick' not in hit and 'chip' not in hit, f'沒有點在框的空白處：{hit}'
    pg.mouse.click(x, y)
    pg.wait_for_timeout(80)


def pool(pg):
    # 丟在別張卡片上就是丟回條件清單。用排在前面的「前高低突破」當落點：v137 每張卡片多了依據那一行，排在後面的卡片在平板寬度會掉到畫面外。
    return card(pg, R)


def state(pg):
    s = pg.evaluate(STATE)
    assert s['shown'][:len(s['groups'])] == s['groups'], f'畫面上的組和存的設定不一致：{s}'
    return s


def run(pg):
    pg.wait_for_selector('#battleGroups .battle-group')
    base.wait_list_done(pg, 47)
    s = state(pg)
    assert s['groups'] == [[G, V]] and s['mode'] == 'anyOfAll' and s['hasNew'], s
    info = pg.evaluate("""() => [...document.querySelectorAll('#battlePreferences .battle-feature-box')].map(b => ({
        drag: b.getAttribute('draggable'), key: b.dataset.bdrag, pill: b.querySelector('.battle-feature').getAttribute('draggable'), cursor: getComputedStyle(b).cursor}))""")
    assert len(info) == 57 and all(i['drag'] == 'true' and i['pill'] is None and i['cursor'] == 'pointer' for i in info), len(info)   # 整張卡片可點也可拖
    # v137：條件清單預設只列「使用中」的；這份驗收要拖沒用到的條件，先切到「全部」。
    shown = pg.evaluate("[...document.querySelectorAll('#battlePreferences .battle-feature-box:not([hidden])')].map(b => b.dataset.bdrag)")
    assert shown == [G, V], shown
    pg.click('#battlePreferences [data-bcat-filter="all"]')

    # 一、卡片（從說明文字抓）拖到「新增一組」→（開盤跳空 且 爆量）或 均線位置。
    drag(pg, card(pg, T).locator('.battle-feature-hint'), new_zone(pg), efy=0.5)
    s = state(pg)
    assert s['groups'] == [[G, V], [T]], s
    assert s['formula'] == '目前的條件：（開盤跳空 且 爆量與最低量） 或 均線位置', s['formula']
    assert pg.inner_text('#battleRule') == '符合條件：（向上開盤缺口 且 成交量倍數） 或 20 日均線', pg.inner_text('#battleRule')
    # 結果跟著重算：量不夠的三檔（缺口成立、收在均線上）現在由第 2 組符合。
    base.wait_list_done(pg, 50)

    # 二、卡片（從右下角空白處抓）拖進第 2 組。
    drag(pg, card(pg, M), group(pg, 1), sfx=0.93, sfy=0.9)
    assert state(pg)['groups'] == [[G, V], [T, M]]

    # 三、組裡的標籤拖到另一組：是移動，不是複製。
    drag(pg, chip(pg, 0, V), group(pg, 1))
    assert state(pg)['groups'] == [[G], [T, M, V]]

    # 四、同一個條件放進另一組（從橢圓按鈕抓整張卡片）；卡片上寫出它在哪幾組。
    drag(pg, card(pg, G).locator('.battle-feature'), new_zone(pg), efy=0.5)
    s = state(pg)
    assert s['groups'] == [[G], [T, M, V], [G]], s
    assert pg.inner_text(f'#battlePreferences [data-bwhere="{G}"]') == '在第 1、3 組'
    assert s['formula'] == '目前的條件：開盤跳空 或 （均線位置 且 MACD 柱體 且 爆量與最低量） 或 開盤跳空', s['formula']

    # 五、點標籤的 × 只從那一組拿掉；空掉的組會消失，後面的組往前遞補。
    chip(pg, 0, G).click()
    assert state(pg)['groups'] == [[T, M, V], [G]]

    # 六、標籤拖回條件清單：只從那一組拿掉。卡片本身拖回清單：從每一組拿掉。
    drag(pg, chip(pg, 0, M), pool(pg))
    assert state(pg)['groups'] == [[T, V], [G]]
    drag(pg, card(pg, G).locator('.battle-feature-hint'), group(pg, 0))
    assert state(pg)['groups'] == [[T, V, G], [G]]
    drag(pg, card(pg, G).locator('.battle-feature-hint'), pool(pg))
    s = state(pg)
    assert s['groups'] == [[T, V]] and G not in s['pressed'], s
    assert R not in sum(s['groups'], []), '丟在別張卡片上不應該把那一張加進去'

    # 七、「且／或」直接點（v138，原本的「且 → 或／或 → 且」兩顆大按鈕已拿掉）：
    #     點條件之間的那一顆換這一組的；點組與組之間的那一顆換組間的。可以各組不同。
    drag(pg, card(pg, G).locator('.battle-feature-hint'), new_zone(pg), efy=0.5)
    assert pg.locator('#battlePreferences .battle-mode-button').count() == 0 and pg.locator('#battlePreferences .battle-mode').count() == 0
    logic = lambda: pg.evaluate("(() => { const p = window.BattleSettings.get(); return [p.ops, p.join]; })()")
    assert state(pg)['groups'] == [[T, V], [G]] and logic() == [['and', 'and'], 'or']
    pg.click('#battleGroups [data-bop="0"]')
    s = state(pg)
    assert logic() == [['or', 'and'], 'or'] and s['groups'] == [[T, V], [G]], logic()
    assert s['formula'] == '目前的條件：（均線位置 或 爆量與最低量） 或 開盤跳空', s['formula']
    assert pg.inner_text('#battleGroups [data-bop="0"]').strip() == '或' and '點一下改成「且」' in pg.get_attribute('#battleGroups [data-bop="0"]', 'aria-label')
    pg.click('#battleGroups [data-bjoin]')
    s = state(pg)
    assert logic() == [['or', 'and'], 'and'] and s['formula'] == '目前的條件：（均線位置 或 爆量與最低量） 且 開盤跳空', (logic(), s['formula'])
    assert pg.inner_text('#battleGroups [data-bjoin]').strip() == '且'
    assert pg.inner_text('#battleRule') == '符合條件：（20 日均線 或 成交量倍數） 且 向上開盤缺口', pg.inner_text('#battleRule')
    # 鍵盤：焦點留在剛按的那一顆上，按 Enter 再換回來
    assert pg.evaluate("document.activeElement && document.activeElement.hasAttribute('data-bjoin')")
    pg.keyboard.press('Enter')
    assert logic() == [['or', 'and'], 'or']
    pg.focus('#battleGroups [data-bop="0"]')
    pg.keyboard.press('Enter')
    assert logic() == [['and', 'and'], 'or'] and state(pg)['formula'] == '目前的條件：（均線位置 且 爆量與最低量） 或 開盤跳空'
    # 且／或按鈕夠大、點它不會選組也不會移除條件
    sizes = pg.evaluate("[...document.querySelectorAll('#battleGroups [data-bop], #battleGroups [data-bjoin]')].map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })")
    assert sizes and all(w2 >= 44 and h2 >= 44 for w2, h2 in sizes), sizes

    # 八、不拖也行：點「新增一組」出現空的一組，再點條件加進去；再點一次拿掉。
    # 整個框都可以點（v137）：點虛線框的空白處，不必點到裡面的橢圓按鈕。
    assert pg.evaluate("[...document.querySelectorAll('#battleGroups .battle-group')].every(g => getComputedStyle(g).cursor === 'pointer')")
    click_box(pg, new_zone(pg), fx=0.9)
    s = state(pg)
    assert s['target'] == 2 and len(s['shown']) == 3 and s['shown'][2] == [], s
    card(pg, K).locator('.battle-feature').click()
    s = state(pg)
    assert s['groups'] == [[T, V], [G], [K]] and s['target'] == 2, s
    card(pg, K).locator('.battle-feature').click()
    assert state(pg)['groups'] == [[T, V], [G]]
    # 先點第 1 組（點框的空白處），再點條件：加進第 1 組。
    click_box(pg, group(pg, 1), fx=0.9)
    assert state(pg)['target'] == 1
    click_box(pg, group(pg, 0), fx=0.9)
    s = state(pg)
    assert s['target'] == 0 and s['groups'] == [[T, V], [G]], s
    assert pg.get_attribute('#battleGroups [data-bpick="0"]', 'aria-pressed') == 'true'
    card(pg, B).locator('.battle-feature').click()
    s = state(pg)
    assert s['groups'] == [[T, V, B], [G]] and s['target'] == 0, s

    # 九、最多四組：滿了之後不再出現「新增一組」。
    drag(pg, card(pg, K).locator('.battle-feature-hint'), new_zone(pg), efy=0.5)
    drag(pg, card(pg, M).locator('.battle-feature-hint'), new_zone(pg), efy=0.5)
    s = state(pg)
    assert s['groups'] == [[T, V, B], [G], [K], [M]] and not s['hasNew'], s
    assert 'is-dragging' not in (pg.locator('#battlePreferences').get_attribute('class') or '')
    assert pg.evaluate("document.querySelectorAll('#battlePreferences .is-dragged, #battlePreferences .drag-over').length") == 0

    # 十、卡片裡的設定區：在週期按鈕上按住拖動、在輸入框裡拖著選字，都不會把卡片拖走；之後卡片仍可拖。
    before = state(pg)['groups']
    drag(pg, card(pg, T).locator('[data-bextra] .battle-pill').first, group(pg, 1))
    assert state(pg)['groups'] == before, '從週期按鈕按住拖動，不應該拖走卡片'
    pg.click('#battleGroups [data-bpick="1"]')
    card(pg, O).locator('.battle-feature').click()                      # 加入後設定區才會出現
    field = card(pg, O).locator('[data-bextra] input')
    before = state(pg)['groups']
    box = field.bounding_box()
    pg.mouse.move(box['x'] + 12, box['y'] + box['height'] / 2)
    pg.mouse.down()
    pg.mouse.move(box['x'] + box['width'] - 30, box['y'] + box['height'] / 2 - 260, steps=12)
    pg.mouse.up()
    assert state(pg)['groups'] == before, '在輸入框裡拖動不應該拖走卡片'
    field.fill('0.6')
    field.dispatch_event('change')
    assert field.input_value() == '0.6' and '已套用' in pg.inner_text('#battlePrefMessage')
    card(pg, T).locator('[data-bextra] .battle-pill').first.click()
    assert card(pg, T).locator('[data-bextra] .battle-pill').first.get_attribute('aria-pressed') == 'true'
    drag(pg, card(pg, O).locator('.battle-feature-hint'), pool(pg))
    assert O not in sum(state(pg)['groups'], []), '用過設定區之後卡片就拖不動了'

    # 十一、均量基準自訂天數。
    pg.click('#battlePreferences [data-bdays="custom"]')
    days = pg.locator('#battleDaysCustom input')
    assert days.is_visible() and pg.evaluate("document.activeElement === document.querySelector('#battleDaysCustom input')")
    days.fill('10')
    days.dispatch_event('change')
    p = pg.evaluate('window.BattleSettings.get()')
    assert p['volumeDays'] == 10 and p['volumeCustom'] is True, p
    assert pg.get_attribute('#battlePreferences [data-bdays="custom"]', 'aria-pressed') == 'true'
    assert pg.get_attribute('#battlePreferences [data-bdays="20"]', 'aria-pressed') == 'false'
    pg.wait_for_function("() => /前 10 日均量/.test(document.getElementById('battleCards').innerText)")
    for bad in ('1', '121', '7.5', ''):
        days.fill(bad)
        days.dispatch_event('change')
        assert pg.evaluate('window.BattleSettings.get().volumeDays') == 10, f'輸入 {bad!r} 不應該被採用'
        assert '超出範圍' in pg.inner_text('#battlePrefMessage'), bad
    pg.click('#battlePreferences [data-bdays="5"]')
    p = pg.evaluate('window.BattleSettings.get()')
    assert p['volumeDays'] == 5 and p['volumeCustom'] is False and not days.is_visible(), p
    assert '已套用' in pg.inner_text('#battlePrefMessage')

    # 十二、重新整理後設定還在；還原預設回到一組。
    want = state(pg)['groups']
    pg.reload(wait_until='domcontentloaded')
    base.open_battle(pg, pg.url)
    pg.wait_for_selector('#battleGroups .battle-group')
    pg.click('#battlePreferences [data-bcat-filter="all"]')
    assert state(pg)['groups'] == want and pg.evaluate('window.BattleSettings.get().volumeDays') == 5
    pg.click('#battleReset')
    s = state(pg)
    assert s['groups'] == [[G, V]] and s['mode'] == 'anyOfAll' and pg.evaluate('window.BattleSettings.get().volumeDays') == 20, s
    base.wait_list_done(pg, 47)


def results(pg):
    """結果卡片逐組標示：量不夠的那一檔，第 1 組不成立、第 2 組成立。"""
    pg.click('#battlePreferences [data-bcat-filter="all"]')        # 還原預設後清單回到「使用中」
    drag(pg, card(pg, T).locator('.battle-feature-hint'), new_zone(pg), efy=0.5)
    base.wait_list_done(pg, 50)
    base.pick(pg, '1001')
    base.wait_single(pg, '1001', True)
    strip = pg.evaluate("""() => { const s = document.querySelector('#battleCards .battle-match-strip');
      return {mode: s.dataset.join, groups: [...s.querySelectorAll('.battle-match-group')].map(g => [g.className.replace('battle-match-group', '').trim(), g.innerText.replace(/\\s+/g, ' ').trim()]),
              join: [...s.querySelectorAll('.battle-match-join')].map(e => e.innerText.trim())}; }""")
    assert strip['mode'] == 'or' and strip['join'] == ['或'], strip
    assert strip['groups'][0][0] == 'is-and off' and '第 1 組' in strip['groups'][0][1] and '✓ 向上開盤缺口' in strip['groups'][0][1] and '− 成交量倍數' in strip['groups'][0][1], strip
    assert strip['groups'][1][0] == 'is-and pass' and '✓ 20 日均線' in strip['groups'][1][1], strip
    assert pg.inner_text('#battleCards .battle-tag').strip() == '盤後條件符合'


def main():
    srv = base.build_local()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in (1440, 1024):
            # 拖曳的起點和終點都要在畫面裡。v137 使用中的卡片帶著自己的設定、佔整列，「全部」清單裡後面的卡片排得更下面，畫面拉高到 4200px。
            pg = browser.new_page(viewport={'width': w, 'height': 4200})
            errs = []
            pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
            pg.route(base.FAKE_API, base.make_router([]))
            base.open_battle(pg, url)
            run(pg)
            results(pg)
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px：拖進組／新增一組／組間移動／拖回清單、同條件多組、且或互換、點選加入、最多四組、設定區不拖走卡片、自訂均量天數、設定保存、結果逐組標示')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
