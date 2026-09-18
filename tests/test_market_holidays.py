# -*- coding: utf-8 -*-
"""
台股休市日清單。

原本只看星期幾，國定假日落在平日時市場休市、沒有盤中直播，系統仍會照跑、
照判「今日無影片」、照寄狀態信與告警。那些信是假的，看久了就會把真正的
異常一起忽略掉。

這裡要釘住兩件事：
  1. 上游（Python）與下游（Apps Script）的清單必須一模一樣
  2. 沒收錄的年份要「當成交易日」，不能當成休市
     ——寧可多跑一天空跑，也不要因為忘記更新清單而安靜跳過整年的交易日
"""

import datetime
import pathlib
import re
import unittest

import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import market_holidays as H   # noqa: E402


class ListContents(unittest.TestCase):
    def test_has_2026(self):
        self.assertIn(2026, H.COVERED_YEARS)

    def test_known_2026_closures(self):
        """抽查幾個從證交所抓回來的日子。"""
        for d in ("2026-01-01",   # 開國紀念日
                  "2026-02-17",   # 春節
                  "2026-04-03",   # 兒童節補假
                  "2026-05-01",   # 勞動節
                  "2026-09-25",   # 中秋
                  "2026-09-28",   # 教師節
                  "2026-10-09",   # 國慶補假
                  "2026-12-25"):  # 行憲紀念日
            self.assertIn(d, H.MARKET_HOLIDAYS, d)

    def test_trading_day_markers_excluded(self):
        """「春節前最後交易日」「春節後開始交易日」是正常交易日，不可當休市。

        證交所的行事曆把它們跟休市日混在同一份資料裡，沒濾掉就會多關三天市。
        """
        for d in ("2026-01-02", "2026-02-11", "2026-02-23"):
            self.assertNotIn(d, H.MARKET_HOLIDAYS, d)

    def test_dates_are_well_formed_and_sorted(self):
        for d in H.MARKET_HOLIDAYS:
            self.assertRegex(d, r"^\d{4}-\d{2}-\d{2}$", d)
            datetime.date.fromisoformat(d)      # 不合法會丟例外
        self.assertEqual(list(H.MARKET_HOLIDAYS), sorted(H.MARKET_HOLIDAYS))

    def test_no_duplicates(self):
        self.assertEqual(len(set(H.MARKET_HOLIDAYS)), len(H.MARKET_HOLIDAYS))


class Behaviour(unittest.TestCase):
    def d(self, s):
        return datetime.date.fromisoformat(s)

    def test_holiday_is_not_trading_day(self):
        self.assertFalse(H.is_trading_day(self.d("2026-09-25")))
        self.assertEqual(H.why_closed(self.d("2026-09-25")), "台股休市日")

    def test_normal_weekday_is_trading_day(self):
        self.assertTrue(H.is_trading_day(self.d("2026-09-18")))
        self.assertEqual(H.why_closed(self.d("2026-09-18")), "")

    def test_weekend_is_not_trading_day(self):
        self.assertFalse(H.is_trading_day(self.d("2026-09-19")))   # 週六
        self.assertEqual(H.why_closed(self.d("2026-09-19")), "週末不開盤")

    def test_unknown_year_fails_open(self):
        """沒收錄的年份一律當成交易日。

        反過來做（當成休市）會讓整年的排程安靜地停擺，而且不會有任何錯誤訊息。
        多跑一天的代價只是一次沒有影片的空跑，兩者不對等。
        """
        far = datetime.date(2099, 6, 3)          # 週三
        self.assertFalse(H.is_market_holiday(far))
        self.assertTrue(H.is_trading_day(far))


class PythonAndAppsScriptStayInSync(unittest.TestCase):
    """兩邊分開手改遲早會不一致，所以一律由 scripts/update_holidays.py 產生。"""

    def gas_dates(self):
        gs = (ROOT / "apps-script" / "Holidays.gs").read_text(encoding="utf-8")
        body = gs[gs.index("MARKET_HOLIDAYS_ = ["):gs.index("];")]
        return re.findall(r"'(\d{4}-\d{2}-\d{2})'", body)

    def test_same_dates(self):
        self.assertEqual(self.gas_dates(), list(H.MARKET_HOLIDAYS))

    def test_gas_fails_open_too(self):
        gs = (ROOT / "apps-script" / "Holidays.gs").read_text(encoding="utf-8")
        self.assertIn("MARKET_HOLIDAY_YEARS_", gs)
        self.assertIn("當成交易日", gs)


class WiredIntoAllThreeDecisionPoints(unittest.TestCase):
    """三個地方都要判斷，漏一個那一層就會在休市日照跑。"""

    def test_transcript_uses_why_closed(self):
        src = (ROOT / "transcript.py").read_text(encoding="utf-8")
        self.assertIn("from market_holidays import", src)
        self.assertIn("why_closed(target)", src)
        self.assertNotIn("target.weekday() >= 5 and not args.force", src)

    def test_pipeline_uses_why_closed(self):
        src = (ROOT / "pipeline.py").read_text(encoding="utf-8")
        self.assertIn("from market_holidays import why_closed", src)
        self.assertIn("why_closed(today)", src)

    def test_apps_script_uses_why_closed(self):
        src = (ROOT / "apps-script" / "MailService.gs").read_text(encoding="utf-8")
        self.assertIn("return !whyClosed_(new Date());", src)

    def test_holiday_module_shipped_next_to_nested_pipeline(self):
        """pipeline/pipeline.py 會被單獨部署，同目錄要有 market_holidays.py，
        否則那份會在 import 就炸掉。"""
        self.assertTrue((ROOT / "pipeline" / "market_holidays.py").exists())
        self.assertEqual((ROOT / "market_holidays.py").read_bytes(),
                         (ROOT / "pipeline" / "market_holidays.py").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
