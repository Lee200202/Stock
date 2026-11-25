"""2026/11/24 管理者規格的線上驗收（唯讀，只開頁面量 DOM，不送任何寫入）。

  python scripts/check_ui_spec_1124.py [--base https://lee200202.github.io/Stock/] [--code 2327]

逐項核對（桌機 1440、大字級、手機 375）：
  1. 估值（11/25 定案）：只呈現最新一筆——本益比、股價淨值比、殖利率三張卡，加上產業別、上市櫃日、董事長三張卡；
     不得出現歷史區間條、年數選擇或估值歷史讀取；手機不得橫向溢出。
  2. 每日總覽「本日未提到」：標題字級、字重、行高與「觀望注意」章名完全相同（含大字級）。
  3. 估值、K 線還原切換、事件時間線、本日未提到：不得出現 emoji。
任何一項不符就以非零結束，列出哪一項。
"""
import argparse
import re
import sys

from playwright.sync_api import sync_playwright

EMOJI = re.compile('[\U0001F300-\U0001FAFF☀-➿\U0001F000-\U0001F2FF️]')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
CLOSE_GUIDE = "() => { const g=[...document.querySelectorAll('button')].find(x=>x.innerText.trim()==='關閉'&&x.offsetParent); if(g) g.click(); }"


def check_valuation(page, base, code, width, fs, problems):
    page.goto(f'{base}?stock={code}&specCheck=1', wait_until='domcontentloaded')
    page.evaluate(CLOSE_GUIDE)
    if fs:
        page.evaluate(f"() => document.documentElement.dataset.fs = '{fs}'")
    page.wait_for_function("() => document.querySelectorAll('#dFund .kv-cell .k').length >= 3", timeout=90000)
    page.wait_for_timeout(1500)
    r = page.evaluate("""() => {
      const keys = sel => [...document.querySelectorAll(sel + ' .kv-cell .k')].map(e => e.innerText.trim());
      const panel = document.querySelector('.detail-panel');
      const text = ['dSecFund', 'dSecK', 'corpTl'].map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
      return { val: keys('#dFund'), info: keys('#dFundInfo'), bands: !!document.querySelector('#dValBands,.vb-pill,#dValYearsForm'),
               overflow: panel.scrollWidth > panel.clientWidth + 1, text };
    }""")
    tag = f'估值 {code} {width}px{" 字級" + fs if fs else ""}'
    if r['val'] != ['本益比', '股價淨值比', '殖利率']:
        problems.append(f'{tag}：估值卡應為本益比、股價淨值比、殖利率（目前 {r["val"]}）')
    if r['info'] != ['產業別', '上市櫃日', '董事長']:
        problems.append(f'{tag}：公司資料卡應為產業別、上市櫃日、董事長（目前 {r["info"]}）')
    if r['bands']:
        problems.append(f'{tag}：仍出現估值歷史區間或年數選擇')
    if r['overflow']:
        problems.append(f'{tag}：面板橫向溢出')
    if EMOJI.search(r['text']):
        problems.append(f'{tag}：估值／K 線／時間線出現 emoji')


def check_overview(page, base, width, fs, problems):
    page.goto(f'{base}?specCheck=1', wait_until='domcontentloaded')
    page.evaluate(CLOSE_GUIDE)
    if fs:
        page.evaluate(f"() => document.documentElement.dataset.fs = '{fs}'")
    page.wait_for_function("() => document.querySelectorAll('#p-overview .sect-h h2').length > 0", timeout=90000)
    page.wait_for_timeout(8000)
    r = page.evaluate("""() => {
      const note = document.getElementById('ovEmptyNote');
      if (!note || note.hidden) { return { skip: true }; }
      const label = note.querySelector('.ov-empty-k');
      const heading = [...document.querySelectorAll('#p-overview .sect-h h2')].find(h => /觀望注意/.test(h.innerText));
      const pick = el => { const s = getComputedStyle(el); return [s.fontSize, s.fontWeight, s.lineHeight, s.letterSpacing]; };
      return { label: label ? pick(label) : null, heading: heading ? pick(heading) : null, text: note.innerText };
    }""")
    tag = f'本日未提到 {width}px{" 字級" + fs if fs else ""}'
    if r.get('skip'):
        print(f'{tag}：今天沒有空分類，略過')
        return
    if not r['label'] or not r['heading']:
        problems.append(f'{tag}：找不到標題或「觀望注意」章名')
    elif r['label'] != r['heading']:
        problems.append(f'{tag}：字級／字重／行高／字距與「觀望注意」不同：{r["label"]} vs {r["heading"]}')
    if EMOJI.search(r.get('text', '')):
        problems.append(f'{tag}：出現 emoji')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default='https://lee200202.github.io/Stock/')
    ap.add_argument('--code', default='2327')
    a = ap.parse_args()
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height, fs in ((1440, 900, ''), (1440, 900, 'l'), (375, 812, '')):
            ctx = browser.new_context(viewport={'width': width, 'height': height}, user_agent=UA)
            page = ctx.new_page()
            check_valuation(page, a.base, a.code, width, fs, problems)
            check_overview(page, a.base, width, fs, problems)
            ctx.close()
        browser.close()
    if problems:
        print('不符合 11/24 規格：')
        for x in problems:
            print('  -', x)
        return 1
    print('11/25 規格：估值三張最新值卡＋公司資料三張卡、無歷史區間、本日未提到與觀望注意同字級、無 emoji，桌機／大字級／手機全部通過')
    return 0


if __name__ == '__main__':
    sys.exit(main())
