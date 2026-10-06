"""戰情設定 v137 的瀏覽器驗收：進場／出場、策略範本、條件分類、條件參數、依據標示、個股頁、手機寬度與重算時間。

    python scripts/check_battle_signals_ui.py

用本機組出來的站、假的行情（scripts/check_battle_back.py 的那一份）；不打正式行情、不寫入任何資料。
假行情：50 檔，前 120 天平盤 100，最後一天開 105、高 109、低 104、收 108；其中 3 檔量 800 張，其餘 4000 張。
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_battle_back as base  # noqa: E402

P = '#battlePreferences '
STATE = """() => { const p = window.BattleSettings.get(), w = document.getElementById('battlePreferences'), s = document.getElementById('battleTemplate');
  return {purpose: p.purpose, groups: p.groups, exit: p.exit, template: p.template, picked: s.value, note: document.getElementById('battleTemplateNote').textContent,
    options: [...s.querySelectorAll('option')].map(o => o.value), optgroups: [...s.querySelectorAll('optgroup')].map(o => o.label),
    shown: [...w.querySelectorAll('.battle-feature-box:not([hidden])')].map(b => b.dataset.bdrag), formula: document.getElementById('battleFormula').textContent,
    rule: document.getElementById('battleRule').textContent, rulePurpose: document.getElementById('battleRule').dataset.purpose, wrapPurpose: w.dataset.purpose,
    pressed: [...w.querySelectorAll('[data-bpurpose][aria-pressed=true]')].map(b => b.dataset.bpurpose), cat: [...w.querySelectorAll('[data-bcat-filter][aria-pressed=true]')].map(b => b.dataset.bcatFilter),
    message: document.getElementById('battlePrefMessage').textContent, cond: p.cond}; }"""
CARD = """() => { const c = document.querySelector('#battleCards .battle-card'); if (!c) return null;
  return {tag: c.querySelector('.battle-tag').innerText.trim(), strategy: c.querySelector('.battle-strategy').innerText.replace(/\\s+/g, ' ').trim(),
    purposeClass: c.querySelector('.battle-purpose-tag').className, chips: [...c.querySelectorAll('.battle-match-chip')].map(e => e.innerText.trim()),
    rows: [...c.querySelectorAll(':scope > details > .battle-conditions > .battle-condition')].map(r => [r.querySelector('b').textContent, r.querySelector('.battle-tag').textContent.trim(), r.querySelector('strong').textContent, r.querySelector('.battle-condition-origin').textContent]),
    others: [...c.querySelectorAll('.battle-others .battle-condition b')].map(e => e.textContent)}; }"""


def state(pg):
    return pg.evaluate(STATE)


def total(pg):
    return int(pg.evaluate(base.TOTAL) or -1)


def wait_total(pg, n):
    pg.wait_for_function("(n) => (document.getElementById('battlePage').textContent.match(/共 (\\d+) 筆/) || [])[1] === String(n)", arg=n, timeout=8000)


def run(pg, wide):
    pg.wait_for_selector('#battleGroups .battle-group')
    base.wait_list_done(pg, 47)
    s = state(pg)
    # 一、預設：進場訊號、自訂（v136 的預設條件不是任何範本）、清單只列使用中的兩個條件。
    assert s['purpose'] == 'entry' and s['pressed'] == ['entry'] and s['wrapPurpose'] == 'entry', s
    assert s['picked'] == '' and s['cat'] == ['used'] and s['shown'] == ['useGap', 'useVolume'], s
    assert s['optgroups'] == ['本站研究組合', '張震觀點・量化研究版'] and len(s['options']) == 17 and s['options'][0] == '', s['options']
    assert s['rule'] == '符合條件：向上開盤缺口 且 成交量倍數', s['rule']
    card = pg.evaluate(CARD)
    assert card['strategy'] == '進場觀察 自訂條件' and 'is-entry' in card['purposeClass'], card
    assert [r[0] for r in card['rows']] == ['向上開盤缺口', '成交量倍數'] and all(r[1] == '符合' and r[3] == '本站研究設定' for r in card['rows']), card['rows']
    assert len(card['others']) >= 5, card['others']                                  # 其他可判定的原有條件收在下一層

    # 二、切到出場訊號：換一套條件（跌破前低 或 跌破均線），這批假行情是跳空大漲，沒有任何一檔符合；進場那一套原封不動。
    pg.click(P + '[data-bpurpose="exit"]')
    wait_total(pg, 0)
    s = state(pg)
    assert s['purpose'] == 'exit' and s['pressed'] == ['exit'] and s['wrapPurpose'] == 'exit' and s['rulePurpose'] == 'exit', s
    assert s['rule'] == '出場風險條件：跌破前低 或 跌破均線' and s['formula'] == '目前的出場條件：跌破前低 或 跌破均線', (s['rule'], s['formula'])
    assert s['shown'] == ['supportBreak', 'maLost'] and s['groups'] == [['useGap', 'useVolume']], s
    assert len(s['options']) == 14 and 'JX03' in s['options'] and 'I01' not in s['options'], s['options']
    assert '目前沒有符合條件的股票' in pg.inner_text('#battleCards') and '已核對 50／50 檔' in pg.inner_text('#battleSummary')
    # 出場用琥珀色，不和進場共用顏色（按鈕顏色有過場，等它走完）
    pg.wait_for_function("getComputedStyle(document.querySelector('#battlePreferences [data-bpurpose=exit]')).backgroundColor === 'rgb(154, 91, 0)'", timeout=3000)
    # 出場條件的分類：動能類只列出場用的，不混進場條件
    pg.click(P + '[data-bcat-filter="momentum"]')
    shown = state(pg)['shown']
    assert 'macdDead' in shown and 'kdDead' in shown and 'bearDiverge' in shown and 'macdCross' not in shown and 'kdCross' not in shown, shown
    assert 'useMacd' in shown and 'useKD' in shown, shown                            # 原有條件兩邊都能用
    # 套用出場範本：高檔跳空轉弱（方向向上、三組都要成立、最後一組任一成立）
    pg.select_option('#battleTemplate', 'JX04')
    s = state(pg)
    assert s['template']['exit'] == 'JX04' and s['picked'] == 'JX04' and s['exit'] == {'direction': 'up', 'groupLogic': 'allOfAny', 'groups': [['highBase'], ['useGap'], ['volSpike'], ['weakClose', 'upperShadow']]}, s['exit']
    assert s['formula'] == '目前的出場條件：高位階 且 開盤跳空 且 量能擴張 且 （收在當日低檔 或 長上影線）', s['formula']
    assert '節目文字紀錄' in s['note'] and '不是講者的公式' in s['note'] and '未經回測' in s['note'], s['note']
    assert s['cat'] == ['used'] and set(s['shown']) == {'useGap', 'highBase', 'volSpike', 'weakClose', 'upperShadow'}, s['shown']
    assert '已套用範本「高檔跳空轉弱」' in s['message'], s['message']
    wait_total(pg, 0)                                                                # 假行情收在高檔（位置 80%），沒有轉弱跡象

    # 三、回到進場：條件還是原本那一組；套用範本「爆量開盤跳空續強」。
    pg.click(P + '[data-bpurpose="entry"]')
    wait_total(pg, 47)
    s = state(pg)
    assert s['groups'] == [['useGap', 'useVolume']] and s['picked'] == '' and s['exit']['groups'][0] == ['highBase'], s
    pg.select_option('#battleTemplate', 'I06')
    wait_total(pg, 47)                                                               # 量 800 張的 3 檔不到均量 2 倍
    s = state(pg)
    assert s['template']['entry'] == 'I06' and s['groups'] == [['useGap', 'gapHold', 'volSpike', 'useBody', 'notExtended']], s['groups']
    assert s['cond']['volSpike']['mult'] == 2 and s['cond']['notExtended']['pct'] == 10, s['cond']
    card = pg.evaluate(CARD)
    assert card['strategy'] == '進場觀察 範本：爆量開盤跳空續強（本站研究組合）', card['strategy']
    assert [r[0] for r in card['rows']] == ['向上開盤缺口', '向上缺口沒有回補', '量能擴張', '紅 K 與收盤位置', '沒有離均線太遠'], card['rows']
    rows = {r[0]: r for r in card['rows']}
    assert rows['向上缺口沒有回補'][2] == '開盤較昨收 5%；今低 104／昨高 101，整天沒有回補', rows['向上缺口沒有回補']
    assert rows['量能擴張'][2] == '當日量是前 20 根均量的 4 倍' and rows['沒有離均線太遠'][2] == '收盤高於 20 日均線 7.57%', (rows['量能擴張'], rows['沒有離均線太遠'])
    # 改一個參數：不再是範本（選單回到自訂、卡片寫自訂條件），結果立刻重算——離均線 7.57% 超過 5%，一檔都不剩
    field = pg.locator(P + '[data-cnum="notExtended:pct"]')
    field.fill('5')
    field.dispatch_event('change')
    wait_total(pg, 0)
    s = state(pg)
    assert s['picked'] == '' and s['template']['entry'] == '' and s['cond']['notExtended']['pct'] == 5 and '已套用' in s['message'], s
    field.fill('999')                                                                # 超出範圍：不採用，提示錯誤
    field.dispatch_event('change')
    assert state(pg)['cond']['notExtended']['pct'] == 5 and '超出範圍' in pg.inner_text('#battlePrefMessage')
    field.fill('10')
    field.dispatch_event('change')
    wait_total(pg, 47)
    assert state(pg)['picked'] == 'I06'                                              # 改回範本的值，又認得出來
    # 選項型的參數用橢圓按鈕：量能擴張不在動能類，換到成交量類找「平日流動性」
    pg.click(P + '[data-bcat-filter="volume"]')
    shown = state(pg)['shown']
    assert shown == ['useVolume', 'volSpike', 'volDryUp', 'liquidity', 'obvHigh', 'cmfPositive', 'mfiUp'], shown

    # 四、張震觀點研究版：範本與條件都標明性質，並連到節目文字紀錄。
    pg.select_option('#battleTemplate', 'JE01')
    s = state(pg)
    assert s['groups'] == [['maPullback']] and s['shown'] == ['maPullback'] and '季線向上時回測季線' in s['note'], s
    tag = pg.evaluate("""() => { const b = document.querySelector('#battlePreferences .battle-feature-box[data-bdrag=maPullback]');
      return {tag: b.querySelector('.battle-origin-tag').innerText, cls: b.querySelector('.battle-origin-tag').className, links: [...b.querySelectorAll('.battle-origin a')].map(a => [a.innerText, a.href, a.target, a.rel])}; }""")
    assert tag['links'][-1] == ['168 周報 2026-07-14 節目紀錄', 'https://168abc.net/168-tv/30909', '_blank', 'noopener'], tag
    wait_total(pg, 50)                                                               # 前兩根低點貼著 60 日均線、最後一根收在均線上且把均線帶高：50 檔都算回測承接
    # 參數的橢圓按鈕：改成 20 日均線
    pg.click(P + '[data-cparam="maPullback:maDays"][data-value="20"]')
    s = state(pg)
    assert s['cond']['maPullback']['maDays'] == 20 and s['picked'] == '', s['cond']['maPullback']
    assert pg.get_attribute(P + '[data-cparam="maPullback:maDays"][data-value="20"]', 'aria-pressed') == 'true'
    kinds = pg.evaluate("[...document.querySelectorAll('#battlePreferences .battle-origin-tag')].reduce((m, e) => (m[e.innerText] = (m[e.innerText] || 0) + 1, m), {})")
    assert kinds == {'常見指標': 28, '本站研究設定': 22, '張震觀點・量化研究版': 7}, kinds

    # 五、還不能提供的條件：列出原因，不用別的數字代替。
    lines = pg.evaluate("[...document.querySelectorAll('#battlePreferences .battle-unsupported li')].map(e => e.textContent)")
    assert len(lines) == 7 and any('同時段量比' in x and '歷史分鐘成交資料' in x for x in lines) and any('60 分 K' in x for x in lines) and any('法人' in x for x in lines), lines

    # 六、鍵盤：Tab 到「出場訊號」按 Enter 可以切換；範本選單可以用鍵盤選。
    pg.focus(P + '[data-bpurpose="exit"]')
    pg.keyboard.press('Enter')
    assert state(pg)['purpose'] == 'exit'
    pg.focus('#battleTemplate')
    pg.keyboard.press('ArrowUp')                                                     # JX04 的上一個
    s = state(pg)
    assert s['picked'] == 'JX03' and s['exit']['groups'] == [['seasonBounceFail']], s

    # 七、重新整理：目的、兩套條件與參數都還在。
    pg.reload(wait_until='domcontentloaded')
    base.open_battle(pg, pg.url)
    pg.wait_for_selector('#battleGroups .battle-group')
    s = state(pg)
    assert s['purpose'] == 'exit' and s['picked'] == 'JX03' and s['groups'] == [['maPullback']] and s['cond']['maPullback']['maDays'] == 20, s

    # 八、個股頁：全部條件都算出來，納入篩選的在上、其餘收在下一層；資料不足的不出現在「其餘」。
    pg.click(P + '[data-bpurpose="entry"]')
    pg.select_option('#battleTemplate', 'I06')
    pg.evaluate("window.__battleDetail('1005')")
    pg.wait_for_function("document.querySelector('#dBattle .battle-card')", timeout=8000)
    d = pg.evaluate("""() => { const c = document.querySelector('#dBattle .battle-card');
      return {on: [...c.querySelectorAll(':scope > details > .battle-conditions > .battle-condition b')].map(e => e.textContent), others: [...c.querySelectorAll('.battle-others .battle-condition')].map(r => [r.querySelector('b').textContent, r.querySelector('.battle-tag').textContent.trim()]),
        strategy: c.querySelector('.battle-strategy').textContent.replace(/\\s+/g, ' ').trim()}; }""")
    assert d['on'] == ['向上開盤缺口', '向上缺口沒有回補', '量能擴張', '紅 K 與收盤位置', '沒有離均線太遠'] and '爆量開盤跳空續強' in d['strategy'], d
    names = [o[0] for o in d['others']]
    assert len(names) >= 35 and '跌破前低' in names and 'MACD 黃金交叉' in names and all('未啟用篩選' in o[1] for o in d['others']), (len(names), names[:8])
    # 假行情只有 121 根：需要更長日 K 的條件是「資料不足」，不會被列成符合或未符合
    assert not {'低檔大量後量縮', '均線糾結後突破', '布林壓縮後突破'} & set(names), names
    assert '低位階' in names and '高位階' in names, names                              # 121 根剛好夠算前 120 根的位階

    # 九、還原預設：兩套條件與參數都回到預設。
    pg.click('#battleReset')
    s = state(pg)
    assert s['purpose'] == 'entry' and s['groups'] == [['useGap', 'useVolume']] and s['exit']['groups'] == [['supportBreak'], ['maLost']] and s['cond']['maPullback']['maDays'] == 60 and s['cat'] == ['used'], s
    base.wait_list_done(pg, 47)

    # 十、版面：沒有水平捲軸；可點的控制項至少 44px 高；手機單欄。
    m = pg.evaluate("""() => { const w = document.getElementById('battlePreferences'), r = e => e.getBoundingClientRect();
      const small = [...w.querySelectorAll('button, select, input')].filter(e => e.offsetParent && r(e).height < 44).map(e => (e.dataset.bflag || e.dataset.bchoice || e.id || e.className) + ':' + Math.round(r(e).height));
      const pb = [...w.querySelectorAll('[data-bpurpose]')].map(r);
      return {overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, small, sameRow: Math.abs(pb[0].top - pb[1].top) < 2,
        clipped: [...w.querySelectorAll('.battle-purpose-button, .battle-template select, .battle-cats .battle-pill')].filter(e => r(e).right > r(w).right + 1 || r(e).left < r(w).left - 1).length}; }""")
    assert m['overflow'] <= 0 and not m['small'] and not m['clipped'], m
    assert m['sameRow'] == wide, m
    pg.click(P + '[data-bcat-filter="all"]')
    m2 = pg.evaluate("""() => { const w = document.getElementById('battlePreferences'), r = e => e.getBoundingClientRect();
      return {overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth, shown: w.querySelectorAll('.battle-feature-box:not([hidden])').length,
        wide: [...w.querySelectorAll('.battle-feature-box:not([hidden])')].filter(e => r(e).right > r(w).right + 1).length}; }""")
    assert m2['overflow'] <= 0 and m2['shown'] == 40 and not m2['wide'], m2           # 進場：兩邊都能用的 12 個＋進場專用 28 個

    # 十一、重算時間：240 檔、每檔 160 根日 K，在這個瀏覽器裡評估一輪。
    t = pg.evaluate("""() => {
      const cal = []; let d = new Date('2025-10-02T12:00:00Z'); while (cal.length < 245) { if (d.getUTCDay() > 0 && d.getUTCDay() < 6) cal.push(d.toISOString().slice(0, 10).replaceAll('-', '/')); d.setUTCDate(d.getUTCDate() + 1); }
      const items = Array.from({length: 240}, (_, n) => ({code: String(1000 + n), name: 'x', bars: cal.slice(-160).map((date, i) => { const c = 100 + Math.sin(i / 7 + n) * 5 + i * 0.05; return {date, open: c, high: c + 1, low: c - 1, close: c, volume: 1000 + (i * 37 + n) % 900}; })}));
      const ctx = {date: cal[cal.length - 1], calendar: cal, volumeUnit: 'lots', afterClose: true}, cfg = window.BATTLE_CONFIG, E = window.MarketBattle.evaluate;
      const time = prefs => { const best = []; for (let k = 0; k < 5; k++) { const t0 = performance.now(); items.forEach(it => E(it, prefs, ctx)); best.push(performance.now() - t0); } return Math.round(best.sort((a, b) => a - b)[2]); };
      const t = cfg.templates.find(x => x.id === 'JE02');
      return {plain: time({}), template: time({groups: t.groups, groupLogic: t.groupLogic, cond: t.params}), everything: time({groups: [cfg.flags.slice(8, 12), cfg.flags.slice(12, 16)], cond: {}})}; }""")
    assert t['plain'] < 300 and t['template'] < 300, t
    return t


def main():
    srv = base.build_local()
    url = f'http://127.0.0.1:{srv.server_address[1]}/'
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for w, scheme in ((1440, 'light'), (1280, 'dark'), (768, 'light'), (390, 'dark'), (360, 'light')):
            pg = browser.new_page(viewport={'width': w, 'height': 1000}, has_touch=w < 700, color_scheme=scheme, reduced_motion='reduce' if w == 768 else 'no-preference')
            errs = []
            pg.on('pageerror', lambda e, errs=errs: errs.append(str(e)))
            # 假的後端對報價跑馬燈（apiGetQuotesFor）回空值，首頁那一段會記一筆錯誤；和戰情無關，不算。
            pg.on('console', lambda m, errs=errs: errs.append(m.text) if m.type == 'error' and 'Failed to load resource' not in m.text and 'apiGetQuotesFor' not in m.text else None)
            pg.route(base.FAKE_API, base.make_router([]))
            base.open_battle(pg, url)
            t = run(pg, w > 720)
            assert not errs, f'{w}px 頁面錯誤：{errs[:2]}'
            print(f'ok {w}px（{"深色" if scheme == "dark" else "淺色"}）：進場／出場切換、範本套用與辨認、分類、參數、依據標示、個股頁、鍵盤、保存與還原、版面；'
                  f'240 檔重算 {t["plain"]} ms（預設）／{t["template"]} ms（範本 JE02）')
            try:
                pg.unroute_all(behavior='ignoreErrors')
            except Exception:
                pass
            pg.close()
        browser.close()
    srv.shutdown()


if __name__ == '__main__':
    main()
