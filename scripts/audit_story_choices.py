"""圖解按鈕的互動性稽核（管理者 2026/10/14）：圖解上的每一個選項按鈕，點下去之後「圖解本身」看得到的內容（圖形、圖上文字、狀態列）
必須跟著變；只有收合在「展開這一章」裡的內容變、圖解不變的，就是沒有互動性。
這支只量、不改：逐章逐顆按，比較點之前與點之後圖解的圖形與文字。"""
import json
import sys
from playwright.sync_api import sync_playwright
from check_tech_story import serve_local, open_tech, FAKE_API

SNAP = """(i) => {
  const m = document.querySelectorAll('.story-main')[i];
  const svg = m.querySelector('.story-visual svg');
  const shapes = svg ? [...svg.querySelectorAll('*')].filter(e => !e.classList.contains('story-playhead'))
      .map(e => e.tagName + [...e.attributes].filter(a => a.name !== 'style').map(a => a.name + '=' + a.value).join(',') + (e.children.length ? '' : e.textContent)).join('|') : '';
  const status = (m.querySelector('.story-status') || {}).textContent || '';
  return {shapes, status};
}"""

u, server = serve_local()
report = []
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        p = b.new_page(viewport={'width': 1440, 'height': 900}, reduced_motion='reduce')
        p.route(FAKE_API, lambda r: r.fulfill(content_type='application/json', body='{"ok":true,"result":[]}'))
        open_tech(p, u)
        p.locator('[data-story-mode="short"]').click()
        mains = p.locator('.story-main')
        for i in range(mains.count()):
            main = mains.nth(i)
            main.scroll_into_view_if_needed(); p.wait_for_timeout(250)
            title = p.evaluate("i=>document.querySelectorAll('.story-main')[i].closest('.story-section').querySelector('h2').textContent.trim()", i)
            more = main.locator('[data-story-more]')
            if more.count():
                more.first.click(); p.wait_for_timeout(150)
            choices = main.locator('[data-story-choice]')
            n = choices.count()
            if not n:
                report.append({'chapter': title, 'choices': [], 'note': '沒有選項按鈕'}); continue
            rows = []
            before = p.evaluate(SNAP, i)
            for j in range(n):
                c = choices.nth(j)
                label = c.inner_text().strip()
                if c.get_attribute('aria-pressed') == 'true' and j == 0:
                    # 預設那一顆：先點第二顆再點回來，看得出它自己的畫面
                    pass
                c.click(); p.wait_for_timeout(250)
                after = p.evaluate(SNAP, i)
                rows.append({'label': label, 'shapes_changed': after['shapes'] != before['shapes'],
                             'status_changed': after['status'] != before['status']})
                before = after
            # 第一顆是預設：用最後一顆→第一顆再量一次
            choices.nth(0).click(); p.wait_for_timeout(250)
            back = p.evaluate(SNAP, i)
            rows[0] = dict(rows[0], shapes_changed=back['shapes'] != before['shapes'], status_changed=back['status'] != before['status'])
            report.append({'chapter': title, 'choices': rows})
        b.close()
finally:
    server.shutdown()

dead_total = 0
lines = []
for ch in report:
    rows = ch['choices']
    if not rows:
        lines.append(f"・{ch['chapter']}：{ch['note']}")
        continue
    dead = [r['label'] for r in rows if not r['shapes_changed']]
    status_only = [r['label'] for r in rows if not r['shapes_changed'] and r['status_changed']]
    dead_total += len(dead)
    verdict = '圖解會跟著變' if not dead else ('圖形不變' + ('（只有狀態列文字變）' if len(status_only) == len(dead) else ''))
    lines.append(f"・{ch['chapter']}：{len(rows)} 顆，{verdict}" + (f"：{'、'.join(dead)}" if dead else ''))
out = '\n'.join(lines)
open('docs/1003-review/story-choices-audit.txt', 'w', encoding='utf-8').write(out + '\n')
json.dump(report, open('docs/1003-review/story-choices-audit.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(out)
print('圖形不變的按鈕共', dead_total, '顆')
