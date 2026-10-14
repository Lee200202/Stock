"""技術說明頁版面量測（唯讀、正式站）：字級、換行、動畫、區塊寬度、純文字流程圖、圖片。結果寫到 docs/1003-review/tech-layout-audit.txt"""
import json
from playwright.sync_api import sync_playwright

JS = r"""
() => {
  const root = document.querySelector('.tech-story');
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const sel = el => { let s = el.tagName.toLowerCase(); if (el.id) s += '#' + el.id; const c = [...el.classList].slice(0, 2).join('.'); if (c) s += '.' + c; const p = el.parentElement; if (p && p !== root) { const pc = [...p.classList].slice(0, 1).join('.'); s = p.tagName.toLowerCase() + (pc ? '.' + pc : '') + ' > ' + s; } return s; };
  const out = {viewport: innerWidth, sections: [], small: {}, large: {}, orphans: [], images: [], textDiagrams: [], narrow: []};
  // 區段
  [...root.querySelectorAll('section.tech-section, .tech-intro')].forEach(sec => {
    const h = sec.querySelector('h2, h1');
    const sw = sec.getBoundingClientRect().width;
    const blocks = [...sec.children].filter(vis).map(c => ({s: c.tagName.toLowerCase() + (c.className ? '.' + String(c.className).split(' ')[0] : ''), w: Math.round(c.getBoundingClientRect().width), h: Math.round(c.getBoundingClientRect().height)}));
    blocks.forEach(b => { if (sw > 900 && b.w < sw * 0.8 && b.h > 60 && !/^(p|h2|h3|ul|ol)$/.test(b.s)) out.narrow.push({section: h ? h.textContent.trim().slice(0, 24) : sec.id, block: b.s, width: b.w, sectionWidth: Math.round(sw)}); });
    out.sections.push({id: sec.id || sec.className.split(' ')[0], title: h ? h.textContent.trim().slice(0, 40) : '', height: Math.round(sec.getBoundingClientRect().height),
      imgs: sec.querySelectorAll('img').length, svgs: sec.querySelectorAll('svg').length, buttons: sec.querySelectorAll('button').length, figures: sec.querySelectorAll('figure').length,
      words: sec.textContent.replace(/\s+/g, '').length});
  });
  // 字級
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  while (walker.nextNode()) {
    const t = walker.currentNode; const txt = t.textContent.trim(); if (txt.length < 2) continue;
    const el = t.parentElement; if (!el || seen.has(el) || !vis(el)) continue; seen.add(el);
    // SVG 裡的字以 viewBox 單位設定：乘上畫面縮放比，量的是實際顯示的字級（圖解放大後文字反向縮放）
    const svg = el.closest && el.closest('svg'), vb = svg && svg.viewBox && svg.viewBox.baseVal;
    const scale = vb && vb.width ? svg.getBoundingClientRect().width / vb.width : 1;
    const fs = Math.round(parseFloat(getComputedStyle(el).fontSize) * scale * 100) / 100;
    const key = sel(el).replace(/#[\w-]+/g, '');
    if (fs < 13.5) { (out.small[key] = out.small[key] || {px: fs, n: 0, sample: txt.slice(0, 26)}).n++; }
    if (fs > 36) { (out.large[key] = out.large[key] || {px: fs, n: 0, sample: txt.slice(0, 26)}).n++; }
  }
  // 換行：最後一行只剩一兩個字
  [...root.querySelectorAll('h2, h3, h4, b, strong, button, figcaption, small, summary, p, li')].forEach(el => {
    if (!vis(el) || el.children.length > 3) return;
    const txt = el.textContent.trim(); if (txt.length < 8 || txt.length > 160) return;
    const range = document.createRange(); range.selectNodeContents(el);
    const rects = [...range.getClientRects()].filter(r => r.width > 1);
    if (rects.length < 2) return;
    const lines = []; rects.forEach(r => { const l = lines.find(x => Math.abs(x.top - r.top) < 4); if (l) { l.left = Math.min(l.left, r.left); l.right = Math.max(l.right, r.right); } else lines.push({top: r.top, left: r.left, right: r.right}); });
    if (lines.length < 2) return;
    lines.sort((a, b) => a.top - b.top);
    const fs = parseFloat(getComputedStyle(el).fontSize);
    const last = lines[lines.length - 1].right - lines[lines.length - 1].left;
    if (last <= fs * 2.2) out.orphans.push({s: sel(el), lines: lines.length, lastChars: Math.round(last / fs * 10) / 10, text: txt.slice(0, 38)});
  });
  // 圖片
  [...root.querySelectorAll('img')].forEach(i => out.images.push({src: (i.getAttribute('src') || '').split('/').pop(), natural: i.naturalWidth + 'x' + i.naturalHeight, shown: Math.round(i.getBoundingClientRect().width) + 'x' + Math.round(i.getBoundingClientRect().height), alt: (i.alt || '').slice(0, 30)}));
  // 只有文字的「流程圖」：有箭頭偽元素或 ol 排成格、但裡面沒有 svg／img
  [...root.querySelectorAll('ol, .agent-models, .agent-map-steps, [class*="flow"], [class*="route"], [class*="steps"], [class*="timeline"], [class*="lane"]')].forEach(el => {
    if (!vis(el)) return; const d = getComputedStyle(el).display;
    if ((d === 'grid' || d === 'flex') && !el.querySelector('svg, img') && el.children.length >= 3) {
      const sec = el.closest('section'); const h = sec && sec.querySelector('h2');
      out.textDiagrams.push({section: h ? h.textContent.trim().slice(0, 22) : '', s: sel(el), items: el.children.length, sample: el.children[0].textContent.trim().replace(/\s+/g, ' ').slice(0, 30)});
    }
  });
  // 動畫
  const anim = [], keyframes = {};
  [...document.styleSheets].forEach(ss => { let rules = []; try { rules = [...ss.cssRules]; } catch (e) { return; }
    const walk = (list, media) => list.forEach(r => {
      if (r.type === CSSRule.KEYFRAMES_RULE) { keyframes[r.name] = [...new Set([...r.cssRules].flatMap(k => [...k.style]))]; return; }
      if (r.cssRules && r.type !== CSSRule.STYLE_RULE) { walk([...r.cssRules], r.conditionText || media); return; }
      if (r.type !== CSSRule.STYLE_RULE) return;
      const st = r.style; const a = st.animation || st.animationName, tr = st.transition;
      if (!/tech|agent|pc-|tx-|pn-|tok-|tsum|lane|story|load-|rel-|src-/.test(r.selectorText)) return;
      if (a && a !== 'none') anim.push({sel: r.selectorText.slice(0, 80), animation: (st.animation || st.animationName).slice(0, 90), media: media || ''});
      else if (tr && tr !== 'none' && !/^all 0s/.test(tr)) anim.push({sel: r.selectorText.slice(0, 80), transition: tr.slice(0, 90), media: media || ''});
    });
    walk(rules, '');
  });
  out.animations = anim; out.keyframes = keyframes;
  return out;
}
"""

with sync_playwright() as pw:
    b = pw.chromium.launch()
    report = {}
    for w in (1600, 390):
        p = b.new_page(viewport={'width': w, 'height': 900})
        p.goto('https://lee200202.github.io/Stock/?tab=tech', wait_until='domcontentloaded', timeout=90000)
        p.wait_for_selector('#tech-agent', state='attached', timeout=30000)
        p.wait_for_timeout(3000)
        # 把整頁捲過一次，讓進場動畫與延遲載入都完成
        p.evaluate("async () => { for (let y = 0; y < document.documentElement.scrollHeight; y += 700) { window.scrollTo(0, y); await new Promise(r => setTimeout(r, 60)); } window.scrollTo(0, 0); }")
        p.wait_for_timeout(800)
        report[w] = p.evaluate(JS)
        p.close()
    b.close()
open('docs/1003-review/tech-layout-audit.json', 'w', encoding='utf-8').write(json.dumps(report, ensure_ascii=False, indent=1))
d, m = report[1600], report[390]
lines = []
lines.append('== 區段（1600px）')
for s in d['sections']:
    lines.append(f"{s['id']}｜{s['title']}｜高 {s['height']}｜字 {s['words']}｜圖片 {s['imgs']}、SVG {s['svgs']}、figure {s['figures']}、按鈕 {s['buttons']}")
lines.append('\n== 比章節窄很多的區塊（1600px）')
for n in d['narrow']:
    lines.append(f"{n['section']}｜{n['block']}｜{n['width']}／{n['sectionWidth']}")
for w, r in ((1600, d), (390, m)):
    lines.append(f'\n== 小於 13.5px 的字（{w}px）')
    for k, v in sorted(r['small'].items(), key=lambda kv: kv[1]['px'])[:28]:
        lines.append(f"{v['px']}px ×{v['n']}｜{k}｜{v['sample']}")
    lines.append(f'\n== 大於 36px 的字（{w}px）')
    for k, v in sorted(r['large'].items(), key=lambda kv: -kv[1]['px'])[:10]:
        lines.append(f"{v['px']}px ×{v['n']}｜{k}｜{v['sample']}")
    lines.append(f'\n== 最後一行只剩一兩個字（{w}px）共 {len(r["orphans"])} 處')
    for o in r['orphans'][:30]:
        lines.append(f"{o['s']}｜{o['lines']} 行、末行約 {o['lastChars']} 字｜{o['text']}")
lines.append('\n== 只有文字的流程／步驟圖（1600px）')
for t in d['textDiagrams']:
    lines.append(f"{t['section']}｜{t['s']}｜{t['items']} 項｜{t['sample']}")
lines.append('\n== 圖片')
for i in d['images']:
    lines.append(f"{i['src']}｜原圖 {i['natural']}｜顯示 {i['shown']}｜{i['alt']}")
lines.append(f"\n== 動畫與轉場規則 {len(d['animations'])} 條；@keyframes {len(d['keyframes'])} 個")
for name, props in d['keyframes'].items():
    lines.append(f"@keyframes {name}：{', '.join(props)}")
for a in d['animations']:
    lines.append(f"{a.get('media', '')[:26]}｜{a['sel']}｜{a.get('animation') or a.get('transition')}")
open('docs/1003-review/tech-layout-audit.txt', 'w', encoding='utf-8').write('\n'.join(lines))
print(len(lines), 'lines')
