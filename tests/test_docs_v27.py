"""技術說明與版本沿革（2026/09/16）：頁面上的門檻數字要跟程式裡的常數對得上。"""
import re
import unittest
from pathlib import Path

from test_quality import p

ROOT = Path(__file__).resolve().parents[1]
TECH = (ROOT / 'apps-script/Tech.html').read_text(encoding='utf-8')
LOG = (ROOT / 'apps-script/Changelog.html').read_text(encoding='utf-8')
CSS = (ROOT / 'apps-script/Stylesheet.html').read_text(encoding='utf-8')
JS = (ROOT / 'apps-script/JavaScript.html').read_text(encoding='utf-8')


class TechDocTests(unittest.TestCase):
    def test_thresholds_match_the_code(self):
        self.assertIn('超過 10 個交易日未提及', TECH)
        self.assertIn('最後提及日收盤', TECH)
        self.assertIn('不能向後取價', TECH)

    def test_model_settings_match_the_code(self):
        # v45 不在公開頁重複可變的模型設定；實際設定由部署文件與程式維護。
        self.assertEqual(p.assessment_token_budget(), 150000)
        self.assertIn('不宣稱每次生成逐字一致', TECH)
        self.assertNotIn('每個價位都拿行情驗過', TECH)

    def test_new_sections_and_the_case_are_documented(self):
        self.assertIn('ETF 賣出勤誠', TECH)
        self.assertIn('SHA256', TECH)
        self.assertIn('缺日 K 先補資料', TECH)

    def test_changelog_covers_the_recent_work(self):
        titles = re.findall(r'<div class="ver-t">(.*?)</div>', LOG)
        self.assertGreaterEqual(len([t for t in titles if '2026/09/1' in t]), 10)
        dates = [re.search(r'（(\d{4}/\d{2}/\d{2})）$', t) for t in titles]
        self.assertIsNotNone(dates[0], '最新一筆標題要以（YYYY/MM/DD）結尾')
        self.assertEqual(dates[0].group(1), max(d.group(1) for d in dates if d), '最新的排在最前面')
        for keyword in ('成本不再接到隔壁那一檔', '逐日稽核不再只剩今天一格', '疊列表格'):
            self.assertTrue(any(keyword in t for t in titles), keyword)

    def test_six_chunks_and_stepper(self):
        # 六段式：一次看一段，步進器與每段底下的上一段／下一段都要在。
        for i in range(6):
            self.assertIn(f'id="tech-chunk-{i}"', TECH)
        self.assertEqual(TECH.count('class="step-b"'), 6)
        self.assertEqual(TECH.count('class="chunk-foot"'), 6)
        self.assertIn('data-step="0"', TECH)
        # 只有第一段預設打開，其餘 hidden
        self.assertEqual(TECH.count('class="tech-chunk" id="tech-chunk-'), 5)
        self.assertIn('class="tech-chunk is-active" id="tech-chunk-0"', TECH)
        self.assertIn("location.hash", TECH)          # 網址記得停在第幾段

    def test_motivation_moved_to_the_front(self):
        self.assertLess(TECH.index('不必只相信一段摘要'), TECH.index('id="tech-chunk-1"'))
        fixed=TECH.index('class="tech-fixed"')
        self.assertGreater(TECH.index('<h2>關於作者</h2>'), fixed)
        self.assertGreater(TECH.index('card author'), fixed)

    def test_new_diagrams_replace_walls_of_text(self):
        self.assertEqual(TECH.count('class="flow-map"'),6)
        self.assertEqual(TECH.count('class="flow-index"'),24)
        self.assertNotIn('閉環圖：憑證從試算表取出',TECH)

    def test_zoom_button_does_not_cover_the_diagram(self):
        # 版面稽核：先前「放大」絕對定位在圖的右上角，每一張圖的角落都被蓋住。
        self.assertIn("cap.appendChild(hint)", JS)
        self.assertNotIn('position: absolute; right: 10px; top: 8px;', CSS)

    def test_scenes_replace_dialogue_only_intro(self):
        first=TECH[:TECH.index('id="tech-chunk-1"')]
        for word in ['取得原稿','核對內容','發布整理','更新追蹤']:self.assertIn(word,first)
        self.assertIn('.flow-map { display:grid;',CSS)

    def test_three_key_points_per_section(self):
        # 舊版三點文字改成四格圖解，避免兩套內容互相重複。
        for part in TECH.split('class="flow-map"')[1:]:
            self.assertEqual(part[:part.index('</ol>')].count('<li>'),4)
        self.assertEqual(TECH.count('class="flow-note"'),6)

    def test_pinned_blocks_are_not_trapped_in_a_details(self):
        for heading in ['關於作者','常見問題']:
            head=TECH[:TECH.index('<h2>'+heading+'</h2>')]
            self.assertEqual(head.count('<details'),head.count('</details>'))

    def test_operations_manual_is_trimmed(self):
        body=TECH[TECH.index('id="tech-chunk-5"'):TECH.index('class="tech-fixed"')]
        text=re.sub(r'<[^>]+>','',body)
        self.assertLess(len(text),1000)
        self.assertIn('11:05 起',body)
        self.assertIn('11:20 起',body)
        self.assertNotIn('登入憑證：為什麼會時好時壞',TECH)

    def test_subnav_is_a_right_edge_drawer(self):
        # 子目錄收在右緣，滑鼠靠近或停在標籤上展開；觸控點一下切換。
        self.assertIn('position: fixed; right: 0; top: 150px;', CSS)
        self.assertIn('.subnav.open .subnav-panel', CSS)
        self.assertIn("aside.addEventListener('mouseenter'", JS)
        self.assertIn('window.innerWidth - e.clientX <= 40', JS)          # 靠近右緣就展開
        self.assertIn("tab.addEventListener('click'", JS)                   # 觸控裝置
        self.assertIn('document.body.appendChild(aside)', JS)              # 不放在會掛 transform 的面板裡
        self.assertNotIn('.docnav {', CSS)

    def test_subnav_links_really_jump(self):
        # 先前點了沒反應：Apps Script 帶 <base target="_top">、各段標題撞號、標題在收起來的想深究裡、平滑捲動不動。
        self.assertIn('e.preventDefault();', JS)
        self.assertIn("h.id = 'toc-' + (++uid)", JS)                        # 全頁唯一
        self.assertIn('d.open = true;', JS)                                 # 先打開想深究
        self.assertIn("document.querySelectorAll('.topbar, .tabbar')", JS)   # 落在固定頁首下面
        self.assertIn('if (Math.abs(window.scrollY - start) < 2) { window.scrollTo(0, top); }', JS)
        self.assertIn('window.__scrollToY(top)', TECH)                       # 步進器用同一支

    def test_toc_flattens_single_child_headings(self):
        # 大標底下只有一個小標時拉平成同一層，兩個以上才縮排（2026/09/16）。
        self.assertIn('children[parent[i]] > 1', JS)
        self.assertIn("li.className = nested ? 'lv3' : 'lv2';", JS)
        # 相鄰兩個標題同名只列一次；第一段那個與問句重複的大標也拿掉了
        self.assertIn('if (text === prevText) { return false; }', JS)
        self.assertNotIn('<h2>今天買入賣出全是空的，網站壞了嗎？</h2>', TECH)

    def test_collapsibles_and_footer_are_chunky(self):
        self.assertEqual(TECH.count('class="chunk-foot"'),6)
        self.assertEqual(TECH.count('<details>'),5)
        self.assertIn('aria-current',TECH)
        self.assertIn('aria-controls',TECH)

    def test_reader_aids_are_wired(self):
        self.assertIn("aside.className = 'subnav'", JS)        # 子目錄從 h2／h3 自動長出來
        self.assertIn("hint.textContent = '放大'", JS)          # 流程圖可全螢幕
        self.assertIn('.subnav-bar', CSS)
        self.assertIn('.tech-steps', CSS)
        self.assertIn('.cmatrix', CSS)
        self.assertIn('.tech-fixed', CSS)
        self.assertIn('window.__docnavRebuild', JS)
        self.assertIn('.spec dd em', CSS)
        self.assertIn('transition-delay: calc(min(var(--i, 0), 6) * .12s);', CSS)


if __name__ == '__main__':
    unittest.main()
