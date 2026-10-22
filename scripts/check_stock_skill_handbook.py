"""檢查離線技能手冊的排版、導覽與安全互動。"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
URL = (ROOT / "docs" / "stock-skills" / "index.html").as_uri()
SHOTS = ROOT / "scratch" / "stock-skill-handbook"


def no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Page overflow"


def main():
    SHOTS.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        calls, errors = [], []
        for width in (1440, 1280, 768, 390, 360):
            page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
            page.on("request", lambda r: calls.append(r.url))
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(URL)
            for theme in ("light", "dark"):
                if page.locator("html").get_attribute("data-theme") != theme:
                    page.locator("#theme").click()
                assert page.evaluate("innerWidth") == width
                assert page.locator("html").get_attribute("data-theme") == theme
                for idx, expected in enumerate(("不新增交易", "當日買入候選", "保留已核對持股", "減碼事件＋剩餘持股")):
                    page.locator(f'[data-case="{idx}"]').click()
                    assert page.locator("#position").inner_text() == expected
                    assert page.locator(f'[data-case="{idx}"]').get_attribute("aria-pressed") == "true"
                page.locator('[data-filter="templates"]').click()
                assert page.locator(".doc:visible").count() == 1
                page.locator("#expand").click()
                assert page.locator(".copy:visible").count() == 19
                no_overflow(page)
                page.locator('[data-filter="all"]').click()
                page.locator("#search").fill("Multiple authentication")
                assert page.locator('.doc[data-group="automation"]:visible').count() == 1
                page.locator("#search").fill("不存在的規格XYZ")
                assert page.locator("#empty").is_visible()
                page.locator("#search").fill("")
                page.locator('a.role[href="#design"]').click()
                assert page.locator("#design details").get_attribute("open") is not None
                assert page.locator("a.role").count() == 10
                # 新增／更新狀態會隨技能沿革改變，不把舊版的新增數量當版面關卡。
                assert page.locator("a.role .badge").count() == page.locator("a.role").count()
                for idx, skill in ((0, "collab"), (4, "writing"), (8, "automation"), (9, "design")):
                    page.locator(f'[data-stage="{idx}"]').click()
                    assert page.locator("#stage-card").get_attribute("class").endswith("k-" + skill)
                    assert page.locator(f'[data-stage="{idx}"]').get_attribute("aria-pressed") == "true"
                    assert page.locator("#stage-title").inner_text().startswith(f"{idx + 1:02d}")
                page.locator('[data-stage="5"]').click()
                page.locator("#stage-link").click()
                assert page.locator("#tuning details").get_attribute("open") is not None
                cols = page.locator(".stages").evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length")
                assert cols == (5 if width > 1000 else 3 if width > 600 else 2), (width, cols)
                no_overflow(page)
                buttons = page.locator("button:visible").evaluate_all("es=>es.map(e=>{const r=e.getBoundingClientRect();return [r.width,r.height]})")
                assert all(w >= 44 and h >= 44 for w, h in buttons)
                page.locator("#top").click()
                page.wait_for_function("scrollY < 2")
                no_overflow(page)
                # 保存收合後的代表性總覽，不輸出整頁展開的巨幅文字截圖。
                page.locator("#docs details").evaluate_all("es=>es.forEach(e=>e.open=false)")
                if width in (1440, 390) and theme == "dark":
                    page.screenshot(path=str(SHOTS / f"handbook-{width}-dark.png"), full_page=True)
                print(width, theme, "layout, classification examples, workflow stages, search, 19 copyable requests, 10 roles, top passed")
            page.close()

        page = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="no-preference")
        page.on("request", lambda r: calls.append(r.url))
        page.goto(URL)
        page.locator("#logic").scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#logic').dataset.playing==='true'")
        first = page.locator(".flow li.active").inner_text()
        page.wait_for_function("document.querySelectorAll('.flow li')[1].classList.contains('active')")
        assert first != page.locator(".flow li.active").inner_text()
        page.locator('[data-case="3"]').focus()
        page.keyboard.press("Enter")
        assert page.locator("#logic").get_attribute("data-playing") == "false"
        page.locator("#replay").click()
        page.wait_for_function("document.querySelector('#logic').dataset.playing==='true'")
        page.wait_for_timeout(4500)
        assert page.locator("#logic").get_attribute("data-playing") == "true", "Replay must keep looping"
        page.locator("footer").scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#logic').dataset.playing==='false'")
        page.locator("#logic").scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#logic').dataset.playing==='true'")
        page.emulate_media(reduced_motion="reduce")
        page.wait_for_function("document.querySelector('#logic').dataset.playing==='false'")
        page.emulate_media(reduced_motion="no-preference")
        page.locator("#pipeline").scroll_into_view_if_needed()
        page.wait_for_function("document.querySelector('#pipeline').dataset.playing==='true'")
        before = page.locator("#stage-title").inner_text()
        page.wait_for_function("t=>document.querySelector('#stage-title').textContent!==t", arg=before, timeout=6000)
        page.locator('[data-stage="2"]').click()
        assert page.locator("#pipeline").get_attribute("data-playing") == "false"
        page.wait_for_timeout(3000)
        assert page.locator("#stage-title").inner_text().startswith("03"), "Click must hold the chosen stage"
        page.locator("#pipe-replay").click()
        page.wait_for_function("document.querySelector('#pipeline').dataset.playing==='true'")
        page.close()

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(URL + "#design")
        assert page.locator("#design details").get_attribute("open") is not None, "A section anchor must open that section"
        page.close()

        page = browser.new_page(viewport={"width": 360, "height": 900}, java_script_enabled=False)
        page.goto(URL)
        assert page.locator("#docs .doc").count() == 22
        page.locator("#pipeline summary").click()
        assert page.locator("#pipeline details li").count() == 10
        assert not page.locator(".stages").is_visible()
        page.locator("#logic summary").click()
        assert page.locator("#logic details li").count() == 4
        assert "部分賣出" in page.locator("#logic details").inner_text()
        page.locator("#common-2 summary").click()
        no_overflow(page)
        page.screenshot(path=str(SHOTS / "handbook-no-js-360.png"), full_page=True)
        page.close()
        assert not errors, errors
        assert not [url for url in calls if url.startswith(("http:", "https:"))], "Handbook must remain offline"
        browser.close()
        print("Loop/replay (examples and workflow), keyboard, offscreen pause, reduced motion, no-JS, zero external network passed")
        print("Screenshots:", SHOTS)


if __name__ == "__main__":
    main()
