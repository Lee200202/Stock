"""產生 LINE 圖文選單的兩張圖（v73）：查資料、通知。

輸出到 line-webhook/static/，由轉送服務 /static/ 提供；後台「建立圖文選單」時網站會來取。
版面與 Line.gs 的 lineRichMenuDefs_ 對齊：2500×1686，上方 220px 是兩個分頁。
通知頁下排的網站入口橫跨兩格；繪圖範圍與點擊熱區必須一致。
圖示用簡單幾何線條畫，不用外部圖檔或生成式圖片。

用法：python scripts/make_line_richmenu.py [--font 字型檔]
"""
import argparse
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'line-webhook', 'static')
W, H, TAB, CW, CH = 2500, 1686, 220, 1250, 733
C = {'bg': '#EEF2EF', 'card': '#FFFFFF', 'ink': '#1B2420', 'muted': '#5F6E67', 'accent': '#04795C', 'hero': '#17322A',
     'heroK': '#9CC8B4', 'tabOff': '#DCE4DF', 'line': '#D5DDD8', 'soft': '#E3F0EA'}
FONTS = ['C:/Windows/Fonts/msjhbd.ttc', 'C:/Windows/Fonts/NotoSansTC-VF.ttf', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
         '/System/Library/Fonts/PingFang.ttc']

PAGES = {
    'query': [('今日整理', '盤勢摘要與個股數', 'doc'), ('查個股', '輸入代號或名稱', 'search'),
              ('持股追蹤', '目前持有的回合', 'trend'), ('逐字稿', '回網站核對原文', 'help')],
    'notify': [('管理訂閱', '每日總覽訂閱設定', 'bell'), ('使用說明', '可以這樣問', 'help'),
               ('開啟網站', '完整整理與圖表', 'link')],
}


def font(path, size):
    return ImageFont.truetype(path, size)


def icon(d, kind, cx, cy, s, col):
    """以 (cx, cy) 為中心、邊長約 s 的線條圖示。"""
    w = max(10, s // 14)
    L, T, R, B = cx - s // 2, cy - s // 2, cx + s // 2, cy + s // 2
    if kind == 'doc':
        d.rounded_rectangle([L + s * .12, T, R - s * .12, B], radius=s // 10, outline=col, width=w)
        for i, k in enumerate((.3, .5, .7)):
            d.line([L + s * .28, T + s * k, R - s * (.28 if i < 2 else .42), T + s * k], fill=col, width=w)
    elif kind == 'search':
        r = s * .32
        d.ellipse([cx - r - s * .08, cy - r - s * .08, cx + r - s * .08, cy + r - s * .08], outline=col, width=w)
        d.line([cx + r * .62, cy + r * .62, R - s * .06, B - s * .06], fill=col, width=int(w * 1.4))
    elif kind == 'trend':
        d.line([L, B, R, B], fill=col, width=w)
        d.line([L + s * .05, B - s * .2, L + s * .35, B - s * .48, L + s * .6, B - s * .34, R - s * .05, T + s * .1],
               fill=col, width=w, joint='curve')
        d.line([R - s * .3, T + s * .1, R - s * .05, T + s * .1, R - s * .05, T + s * .35], fill=col, width=w, joint='curve')
    elif kind == 'bars':
        d.line([L, B, R, B], fill=col, width=w)
        for i, h in enumerate((.45, .75, .55, .95)):
            x = L + s * (.1 + i * .23)
            d.rounded_rectangle([x, B - s * h, x + s * .15, B - w], radius=s // 30, fill=col)
    elif kind == 'bell':
        d.chord([L + s * .12, T + s * .05, R - s * .12, B + s * .35], 180, 360, outline=col, width=w)
        d.line([L + s * .12, cy + s * .22, L + s * .12, cy - s * .02], fill=col, width=w)
        d.line([R - s * .12, cy + s * .22, R - s * .12, cy - s * .02], fill=col, width=w)
        d.line([L, cy + s * .25, R, cy + s * .25], fill=col, width=w)
        d.ellipse([cx - s * .1, B - s * .12, cx + s * .1, B + s * .08], fill=col)
    elif kind == 'chat':
        d.rounded_rectangle([L, T + s * .08, R, B - s * .22], radius=s // 6, outline=col, width=w)
        d.polygon([(L + s * .22, B - s * .24), (L + s * .22, B), (L + s * .45, B - s * .24)], fill=col)
        for i, k in enumerate((.32, .5)):
            d.line([L + s * .2, T + s * k, R - s * (.2 if i == 0 else .38), T + s * k], fill=col, width=w)
    elif kind == 'help':
        d.ellipse([L, T, R, B], outline=col, width=w)
        d.arc([cx - s * .2, cy - s * .32, cx + s * .2, cy + s * .06], 180, 40, fill=col, width=w)
        d.line([cx + s * .1, cy - s * .02, cx, cy + s * .1, cx, cy + s * .16], fill=col, width=w, joint='curve')
        d.ellipse([cx - s * .05, cy + s * .26, cx + s * .05, cy + s * .36], fill=col)
    elif kind == 'link':
        d.rounded_rectangle([L, T + s * .18, R - s * .18, B], radius=s // 9, outline=col, width=w)
        d.line([cx - s * .05, cy + s * .05, R, T], fill=col, width=w)
        d.line([R - s * .32, T, R, T, R, T + s * .32], fill=col, width=w, joint='curve')


def draw_page(page, fpath):
    img = Image.new('RGB', (W, H), C['bg'])
    d = ImageDraw.Draw(img)
    f_tab, f_label, f_sub = font(fpath, 88), font(fpath, 112), font(fpath, 62)
    for i, (key, text) in enumerate((('query', '查資料'), ('notify', '通知'))):
        on = key == page
        x0 = i * CW
        d.rectangle([x0, 0, x0 + CW, TAB], fill=C['hero'] if on else C['tabOff'])
        d.text((x0 + CW / 2, TAB / 2), text, font=f_tab, fill='#FFFFFF' if on else C['muted'], anchor='mm')
        if on:
            d.rectangle([x0 + CW / 2 - 90, TAB - 16, x0 + CW / 2 + 90, TAB - 6], fill=C['heroK'])
    pad = 34
    for n, (label, sub, kind) in enumerate(PAGES[page]):
        wide = page == 'notify' and n == 2
        x, y = (n % 2) * CW if not wide else 0, TAB + (n // 2) * CH
        width = W if wide else CW
        center = x + width / 2
        box = [x + pad, y + pad, x + width - pad, y + CH - pad]
        d.rounded_rectangle(box, radius=48, fill=C['card'], outline=C['line'], width=4)
        cy = y + CH * .36
        d.ellipse([center - 150, cy - 150, center + 150, cy + 150], fill=C['soft'])
        icon(d, kind, int(center), int(cy), 170, C['accent'])
        d.text((center, y + CH * .68), label, font=f_label, fill=C['ink'], anchor='mm')
        d.text((center, y + CH * .82), sub, font=f_sub, fill=C['muted'], anchor='mm')
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--font', default='')
    args = ap.parse_args()
    fpath = args.font or next((f for f in FONTS if os.path.exists(f)), '')
    if not fpath:
        raise SystemExit('找不到中文粗體字型，請用 --font 指定（例如 NotoSansCJK-Bold.ttc）。')
    os.makedirs(OUT, exist_ok=True)
    for page in ('query', 'notify'):
        out = os.path.join(OUT, 'richmenu-%s.png' % page)
        draw_page(page, fpath).save(out, 'PNG', optimize=True)
        size = os.path.getsize(out)
        assert size < 1024 * 1024, out + ' 超過 LINE 圖文選單 1MB 上限'
        print(out, size, 'bytes')


if __name__ == '__main__':
    main()
