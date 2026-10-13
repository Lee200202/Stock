"""技術說明「AI 是怎麼讀原稿…」一章：全版排版與「字怎麼變成輸入」圖解（v175）。本機組站、假 API，不連外。

檢查：
  1440px：三層分工圖、導言與圖解都用滿這一章的欄寬（管理者 2026/10/13 要求全版）。
  「② 字怎麼變成輸入？」→ 四個階段的圖解出現、原本的字卡隱藏；每個階段都有圖示；向量是一排數字格、有位置圓圈。
  點任何一個小單位：四個階段裡同一個單位一起亮（各一個），說明列跟著換。
  換到別的題目：圖解收起、字卡回來。
  1440／768／390／360px、淺深色：圖解不超出容器、整頁沒有橫向捲動；可點的小單位觸控高度 ≥ 40px。
  減少動態：進場動畫關閉。
"""
import json

from playwright.sync_api import sync_playwright

from check_tech_story import FAKE_API, open_tech, serve_local


def run(page, width, scheme):
    page.set_viewport_size({'width': width, 'height': 900})
    page.locator('#tech-agent').scroll_into_view_if_needed()
    section = page.locator('#tech-agent').bounding_box()
    if width >= 1100:
        for sel in ('#tech-agent > p >> nth=0', '.agent-map', '.agent-answer'):
            box = page.locator(sel).bounding_box()
            assert box['width'] >= section['width'] - 2, (width, sel, box['width'], section['width'])
    page.locator('[data-agent="tokens"]').click()
    assert page.locator('#tokViz').is_visible() and page.locator('#agentRoute').is_hidden(), width
    assert page.locator('#tokViz .tok-stage').count() == 4
    assert page.locator('#tokViz .tok-ico svg').count() == 4
    assert page.locator('#tokViz .tok-cells i').count() == 24                      # 三個小單位 × 八格
    assert page.locator('#tokViz .tok-pos').all_inner_texts() == ['1', '2', '3']
    for i in range(3):
        page.locator(f'#tokViz .tok-cut [data-tok="{i}"]').click()
        assert page.locator('#tokViz .is-on').count() == 4, (width, i)             # 四個階段各一個
        assert page.locator(f'#tokViz [data-tok="{i}"].is-on').count() == 4
        text = page.locator('#tokPick').inner_text()
        assert f'第 {i + 1} 個位置' in text, text
    page.wait_for_function("document.getElementById('tokViz').getAnimations({subtree: true}).every(a => a.playState === 'finished')")   # 進場位移結束後才量
    viz = page.locator('#tokViz').bounding_box()
    for k in range(4):
        box = page.locator('#tokViz .tok-stage').nth(k).bounding_box()
        assert box['x'] >= viz['x'] - 1 and box['x'] + box['width'] <= viz['x'] + viz['width'] + 1, (width, k, box, viz)
    heights = page.locator('#tokViz button').evaluate_all('els => els.map(e => e.getBoundingClientRect().height)')
    assert min(heights) >= 40, (width, min(heights))
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1'), (width, scheme, '整頁橫向捲動')
    # 階段之間的箭頭是畫在卡片外的偽元素，scrollWidth 會把它算進去；這裡量的是卡片裡每個實際元素的右緣。
    overflow = page.locator('#tokViz .tok-stage').evaluate_all('''els => els.map((e, i) => {
        const box = e.getBoundingClientRect();
        const right = Math.max(...[...e.querySelectorAll('*')].map(n => n.getBoundingClientRect().right));
        const left = Math.min(...[...e.querySelectorAll('*')].map(n => n.getBoundingClientRect().left));
        return [i + 1, Math.round(right - box.right), Math.round(box.left - left)];
    }).filter(v => v[1] > 1 || v[2] > 1)''')
    assert not overflow, (width, '階段內容超出卡片（階段、內容寬、卡片寬）', overflow)
    page.locator('[data-agent="infer"]').click()
    assert page.locator('#tokViz').is_hidden() and page.locator('#tokPick').is_hidden() and page.locator('#agentRoute').is_visible()
    assert page.locator('#agentRoute li').count() == 4
    print(f'ok {width}px {scheme}: 全版、四階段圖解、點選連動、沒有溢出')


def main():
    url, server = serve_local()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for scheme in ('light', 'dark'):
                page = browser.new_page(color_scheme=scheme, reduced_motion='no-preference')
                page.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body=json.dumps({'ok': True, 'result': []})))
                open_tech(page, url)
                for width in (1440, 768, 390, 360):
                    run(page, width, scheme)
                page.close()
            page = browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
            page.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body=json.dumps({'ok': True, 'result': []})))
            open_tech(page, url)
            page.locator('[data-agent="tokens"]').click()
            assert page.locator('#tokViz .tok-stage').first.evaluate('e => getComputedStyle(e).animationName') == 'none'
            page.close()
            page = browser.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='no-preference')
            page.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body=json.dumps({'ok': True, 'result': []})))
            open_tech(page, url)
            page.locator('[data-agent="tokens"]').click()
            assert page.locator('#tokViz .tok-stage').first.evaluate('e => getComputedStyle(e).animationName') == 'tokIn'
            browser.close()
    finally:
        server.shutdown()
    print('Token diagram: full width, four illustrated stages, linked selection, no overflow, motion respects the setting')


if __name__ == '__main__':
    main()
