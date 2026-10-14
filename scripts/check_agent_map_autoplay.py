"""三層分工圖（播放一次查詢 →）：章節展開、捲進畫面就自動播放；點同一章別組的控制（情境）不停；點這一組的卡片才停；再播圖解恢復。"""
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

NODES = "[...document.querySelectorAll('.agent-map [data-agent-node]')].map(b=>b.getAttribute('aria-pressed')==='true'?b.dataset.agentNode:'').join('')"


def seen_nodes(p, ms=6500):
    got = set()
    for _ in range(ms // 250):
        got.add(p.evaluate(NODES))
        p.wait_for_timeout(250)
    return got


u, server = serve_local()
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for width in (1440, 390):
            p = b.new_page(viewport={'width': width, 'height': 900}, reduced_motion='no-preference')
            p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
            open_tech(p, u)
            p.locator('[data-story-mode="short"]').click()
            p.locator('#tech-agent .story-chapter-toggle').click()
            p.locator('#tech-agent .agent-map').scroll_into_view_if_needed()
            assert {'model', 'tool'} <= seen_nodes(p), 'map did not autoplay when it came into view'
            # 同一章別組的控制：情境按鈕。三層分工圖照樣播。
            p.locator('#tech-agent [data-agent="infer"]').click()
            p.locator('#tech-agent .agent-map').scroll_into_view_if_needed()
            assert {'model', 'tool'} <= seen_nodes(p), 'a click on another group stopped the map'
            # 點這一組的卡片：停在那一張。
            p.locator('#tech-agent .agent-map [data-agent-node="tool"]').click()
            p.wait_for_timeout(300)
            assert seen_nodes(p) == {'tool'}, 'clicking a map card did not stop the map'
            # 再播圖解：恢復
            p.locator('#tech-agent .story-replay').click()
            p.locator('#tech-agent .agent-map').scroll_into_view_if_needed()
            assert {'model', 'control'} <= seen_nodes(p), 'replay did not resume the map'
            print('agent map', width, 'autoplays in view, survives other-group clicks, stops on its own card, resumes on replay')
            p.close()
        b.close()
finally:
    server.shutdown()
