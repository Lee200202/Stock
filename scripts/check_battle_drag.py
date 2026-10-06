"""戰情條件卡片拖曳驗收（v135）。行情用假資料，拖曳用真的滑鼠事件。

    python scripts/check_battle_drag.py

整張卡片都可以拖進「且／或」區：從說明文字、卡片空白處、橢圓按鈕開始拖都算。
卡片裡的週期按鈕與數值輸入框照常點選、選字，不會把卡片拖走。點橢圓按鈕仍然是加入／移除。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_battle_back as base  # noqa: E402  共用本機組站與假行情

STATE = """() => {
  const wrap = document.getElementById('battlePreferences');
  const zone = wrap.querySelector('.battle-dropzone.is-active');
  return {logic: zone ? zone.dataset.bdrop : '',
          chips: [...wrap.querySelectorAll('.battle-dropzone.is-active .battle-expression-chip')].map(e => e.dataset.bflag),
          pressed: [...wrap.querySelectorAll('.battle-feature[aria-pressed=true]')].map(e => e.dataset.bflag)};
}"""


def card(pg, key):
    return pg.locator(f'#battlePreferences .battle-feature-box[data-bdrag="{key}"]')


def point(pg, locator, fx=0.5, fy=0.5):
    locator.scroll_into_view_if_needed()
    b = locator.bounding_box()
    return b['x'] + b['width'] * fx, b['y'] + b['height'] * fy


def drag(pg, start, end):
    pg.mouse.move(*start)
    pg.mouse.down()
    pg.mouse.move(start[0] + 6, start[1] + 6, steps=3)
    pg.mouse.move(*end, steps=14)
    pg.mouse.up()
    pg.wait_for_timeout(150)


def zone(pg, name):
    return point(pg, pg.locator(f'#battlePreferences .battle-dropzone[data-bdrop="{name}"]'), 0.5, 0.75)


def run(pg):
    wrap = pg.locator('#battlePreferences')
    pg.wait_for_selector('#battlePreferences .battle-feature-box[data-bdrag]')
    pg.evaluate("document.querySelector('#battlePreferences .battle-logic-board').scrollIntoView({block: 'start'})")
    assert pg.evaluate(STATE) == {'logic': 'all', 'chips': ['useGap', 'useVolume'], 'pressed': ['useGap', 'useVolume']}, pg.evaluate(STATE)

    # 一、八張卡片本身可拖；裡面的橢圓按鈕不是另一個可拖的東西；游標提示可以抓。
    info = pg.evaluate("""() => [...document.querySelectorAll('#battlePreferences .battle-feature-box')].map(b => ({
        drag: b.getAttribute('draggable'), key: b.dataset.bdrag, pill: b.querySelector('.battle-feature').getAttribute('draggable'),
        pillKey: b.querySelector('.battle-feature').dataset.bflag, cursor: getComputedStyle(b).cursor}))""")
    assert len(info) == 8 and all(i['drag'] == 'true' and i['key'] == i['pillKey'] and i['pill'] is None and i['cursor'] == 'grab' for i in info), info

    # 二、從說明文字（不是橢圓按鈕）開始拖 → 加入「且」。
    drag(pg, point(pg, card(pg, 'useTrend').locator('.battle-feature-hint')), zone(pg, 'all'))
    s = pg.evaluate(STATE)
    assert s['logic'] == 'all' and 'useTrend' in s['chips'] and 'useTrend' in s['pressed'], f'從說明文字拖不進去：{s}'

    # 三、從卡片右下角的空白處開始拖 → 拖進「或」，邏輯跟著切換。
    drag(pg, point(pg, card(pg, 'useMacd'), 0.93, 0.9), zone(pg, 'any'))
    s = pg.evaluate(STATE)
    assert s['logic'] == 'any' and 'useMacd' in s['chips'], f'從卡片空白處拖不進去：{s}'

    # 四、從橢圓按鈕開始拖也一樣（拖的是整張卡片）。
    drag(pg, point(pg, card(pg, 'useKD').locator('.battle-feature')), zone(pg, 'all'))
    s = pg.evaluate(STATE)
    assert s['logic'] == 'all' and 'useKD' in s['chips'], f'從橢圓按鈕拖不進去：{s}'

    # 五、把已加入的卡片拖回條件清單（丟在別張卡片上）→ 移除。
    drag(pg, point(pg, card(pg, 'useGap').locator('.battle-feature-hint')), point(pg, card(pg, 'useBoll'), 0.5, 0.8))
    s = pg.evaluate(STATE)
    assert 'useGap' not in s['chips'] and 'useGap' not in s['pressed'], f'拖回條件清單沒有移除：{s}'
    assert 'useBoll' not in s['chips'], '丟在別張卡片上不應該把那一張加進去'

    # 六、卡片裡的設定區：在週期按鈕上按住拖動、在輸入框裡拖著選字，都不會把卡片拖走。
    before = pg.evaluate(STATE)
    drag(pg, point(pg, card(pg, 'useTrend').locator('[data-bextra] .battle-pill').first), zone(pg, 'any'))
    assert pg.evaluate(STATE) == before, '從週期按鈕按住拖動，不應該拖走卡片或切換邏輯'
    drag(pg, point(pg, card(pg, 'useBody').locator('.battle-feature')), zone(pg, 'all'))          # 先加入，設定區才會出現
    field = card(pg, 'useBody').locator('[data-bextra] input')
    before = pg.evaluate(STATE)
    box = field.bounding_box()
    drag(pg, (box['x'] + 12, box['y'] + box['height'] / 2), (box['x'] + box['width'] - 30, box['y'] + box['height'] / 2 - 220))
    assert pg.evaluate(STATE) == before, '在輸入框裡拖動不應該拖走卡片'
    field.fill('0.6')
    field.dispatch_event('change')
    assert field.input_value() == '0.6' and '已套用' in pg.inner_text('#battlePrefMessage')
    card(pg, 'useTrend').locator('[data-bextra] .battle-pill').first.click()
    assert card(pg, 'useTrend').locator('[data-bextra] .battle-pill').first.get_attribute('aria-pressed') == 'true'

    # 七、設定區用過之後，卡片其他地方仍然可以拖。
    drag(pg, point(pg, card(pg, 'useTrend').locator('.battle-feature-hint')), point(pg, card(pg, 'useBoll'), 0.5, 0.8))
    assert 'useTrend' not in pg.evaluate(STATE)['chips'], '用過設定區之後卡片就拖不動了'

    # 八、點橢圓按鈕仍然是加入／移除；拖完之後不留著「拖曳中」的樣子。
    card(pg, 'useBoll').locator('.battle-feature').click()
    assert 'useBoll' in pg.evaluate(STATE)['chips']
    card(pg, 'useBoll').locator('.battle-feature').click()
    assert 'useBoll' not in pg.evaluate(STATE)['chips']
    assert pg.evaluate("document.querySelectorAll('#battlePreferences .is-dragged, #battlePreferences .drag-over').length") == 0
    assert 'is-dragging' not in (wrap.get_attribute('class') or '')


def main():
    srv = base.build_local()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w in (1440, 1024):
            pg = browser.new_page(viewport={'width': w, 'height': 1500})
            errs = []
            pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
            pg.route(base.FAKE_API, base.make_router([]))
            base.open_battle(pg, url)
            run(pg)
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px：從說明文字、卡片空白處、橢圓按鈕拖進「且／或」；拖回清單移除；設定區可點選輸入、不拖走卡片；點按鈕仍可加入移除')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
