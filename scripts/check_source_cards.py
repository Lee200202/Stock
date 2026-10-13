"""技術說明頁的「依據連結卡片」（v176）。本機組站、假 API；點卡片時攔下跳轉，不連外。

檢查：
  兩段各有兩個以上連結的依據（實際依據 4 個、原理依據 5 個）變成卡片，原本的段落隱藏但仍在 HTML 裡；
  每張卡片保留原本的網址、另開分頁（target=_blank、rel=noopener）、有種類標籤與圖示、顏色依種類不同。
  還沒滑到：不播放。滑進畫面：自動進場，三種方向（左、右、下）都有用到，並開始輪流掃過光帶。
  點任何一張卡片：這一組的動畫全部停下、卡片都看得見；另一組不受影響。離開畫面：待機動畫暫停。
  1440／390／360px、淺深色：卡片不超出容器、整頁沒有橫向捲動、卡片高度 ≥ 44px。
  減少動態：不播放，卡片直接顯示。
"""
import json

from playwright.sync_api import sync_playwright

from check_tech_story import FAKE_API, open_tech, serve_local

EXPECT = [('實際依據', 4, {'tone-wait': 2, 'tone-ok': 2}), ('原理依據', 5, {'tone-resume': 4, 'tone-skip': 1})]


def fresh(browser, url, **kw):
    page = browser.new_page(**kw)
    page.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body=json.dumps({'ok': True, 'result': []})))
    open_tech(page, url)
    page.evaluate("document.addEventListener('click', e => { if (e.target.closest && e.target.closest('.src-card')) e.preventDefault(); }, true)")   # 測試時不真的連出去
    return page


def structure(page):
    groups = page.locator('#tech-agent .src-cards')
    assert groups.count() == 2, groups.count()
    for i, (title, n, tones) in enumerate(EXPECT):
        box = groups.nth(i)
        assert box.locator('.src-cards-h').inner_text() == title
        cards = box.locator('.src-card')
        assert cards.count() == n, (title, cards.count())
        original = page.locator('#tech-agent p.agent-sources', has_text=title)
        assert original.count() == 1 and original.is_hidden()
        hrefs = original.locator('a').evaluate_all('els => els.map(a => a.getAttribute("href"))')
        assert cards.evaluate_all('els => els.map(a => a.getAttribute("href"))') == hrefs
        assert set(cards.evaluate_all('els => els.map(a => a.target + "|" + a.rel)')) == {'_blank|noopener'}
        assert box.locator('.src-ico svg').count() == n and box.locator('.src-txt small').count() == n
        for tone, count in tones.items():
            assert box.locator(f'.src-card.{tone}').count() == count, (title, tone)
    colours = groups.nth(0).locator('.src-card').evaluate_all('els => [...new Set(els.map(e => getComputedStyle(e).borderLeftColor))]')
    assert len(colours) == 2, colours                                                # 執行紀錄與原始碼兩種顏色


def motion(page):
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.evaluate('window.scrollTo(0, 0)')
    page.wait_for_timeout(300)
    first, second = page.locator('#tech-agent .src-cards').nth(0), page.locator('#tech-agent .src-cards').nth(1)
    assert 'is-in' not in (first.get_attribute('class') or ''), '還沒滑到就不該播放'
    first.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelectorAll('#tech-agent .src-cards')[0].classList.contains('is-live')")
    names = first.locator('.src-card').evaluate_all('els => els.map(e => getComputedStyle(e).animationName)')
    assert names == ['srcInL', 'srcInR', 'srcInUp', 'srcInL'], names                  # 左、右、下輪流
    assert first.locator('.src-card').first.evaluate("e => getComputedStyle(e, '::after').animationName") == 'srcSweep'
    assert first.locator('.src-go').first.evaluate('e => getComputedStyle(e).animationName') == 'srcNudge'
    props = page.evaluate("""() => [...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } })
        .filter(r => r.type === CSSRule.KEYFRAMES_RULE && /^src/.test(r.name)).flatMap(r => [...r.cssRules].flatMap(k => [...k.style]))""")
    assert set(props) <= {'opacity', 'transform'}, props                              # 只動透明度與位移
    first.locator('.src-card').nth(1).click()
    assert 'is-stopped' in first.get_attribute('class')
    stopped = first.locator('.src-card').evaluate_all("els => els.map(e => [getComputedStyle(e).animationName, getComputedStyle(e, '::after').animationName, getComputedStyle(e).opacity])")
    assert all(x == ['none', 'none', '1'] for x in stopped), stopped
    assert first.locator('.src-go').first.evaluate('e => getComputedStyle(e).animationName') == 'none'
    second.scroll_into_view_if_needed()
    page.wait_for_function("document.querySelectorAll('#tech-agent .src-cards')[1].classList.contains('is-live')")
    assert 'is-stopped' not in (second.get_attribute('class') or '')                  # 另一組照常
    page.evaluate('window.scrollTo(0, 0)')
    page.wait_for_function("!document.querySelectorAll('#tech-agent .src-cards')[1].classList.contains('is-live')")
    assert second.locator('.src-card').first.evaluate("e => getComputedStyle(e, '::after').animationName") == 'none'   # 離開畫面就暫停


def layout(page, width, scheme):
    page.set_viewport_size({'width': width, 'height': 900})
    for i in range(2):
        box = page.locator('#tech-agent .src-cards').nth(i)
        box.scroll_into_view_if_needed()
        page.wait_for_function("i => document.querySelectorAll('#tech-agent .src-cards')[i].classList.contains('is-in')", arg=i)   # 觀察器是非同步的，先等它進場
        page.wait_for_function("i => document.querySelectorAll('#tech-agent .src-cards')[i].getAnimations({subtree: true}).filter(a => a.animationName && a.animationName.startsWith('srcIn')).every(a => a.playState === 'finished')", arg=i)
        outer = box.bounding_box()
        for card in box.locator('.src-card').all():
            b = card.bounding_box()
            assert b['x'] >= outer['x'] - 1 and b['x'] + b['width'] <= outer['x'] + outer['width'] + 1, (width, b, outer)
            assert b['height'] >= 44, (width, b['height'])
            inner = card.evaluate("e => Math.max(...[...e.querySelectorAll('*')].map(n => n.getBoundingClientRect().right)) - e.getBoundingClientRect().right")
            assert inner <= 1, (width, '卡片內文字超出', inner)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (width, scheme)
    print(f'ok {width}px {scheme}: 卡片不溢出、整頁不橫捲')


def main():
    url, server = serve_local()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = fresh(browser, url, viewport={'width': 1440, 'height': 900}, reduced_motion='no-preference')
            structure(page)
            motion(page)
            page.close()
            for scheme in ('light', 'dark'):
                page = fresh(browser, url, color_scheme=scheme, reduced_motion='no-preference')
                for width in (1440, 390, 360):
                    layout(page, width, scheme)
                page.close()
            page = fresh(browser, url, viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
            box = page.locator('#tech-agent .src-cards').first
            assert 'is-stopped' in box.get_attribute('class')
            box.scroll_into_view_if_needed()
            assert box.locator('.src-card').evaluate_all('els => els.every(e => getComputedStyle(e).animationName === "none" && getComputedStyle(e).opacity === "1")')
            browser.close()
    finally:
        server.shutdown()
    print('Source cards: links kept, colour by kind, plays on scroll, stops on click, pauses off screen, no overflow, motion respects the setting')


if __name__ == '__main__':
    main()
