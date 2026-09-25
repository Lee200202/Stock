from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from refresh_next_holidays import install  # noqa: E402
from pipeline.market_holidays import is_trading_day, why_closed  # noqa: E402


class CalendarYearlyTests(unittest.TestCase):
    def test_unpublished_year_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'market_holidays.py'
            source = 'MARKET_HOLIDAYS = (\n    "2026-09-25",\n)\n'
            target.write_text(source, encoding='utf-8')
            with self.assertRaises(ValueError):
                install(2027, [], target)
            self.assertEqual(target.read_text(encoding='utf-8'), source)

    def test_one_successful_update_and_idempotent_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'market_holidays.py'
            target.write_text('MARKET_HOLIDAYS = (\n    "2026-09-25",\n)\n', encoding='utf-8')
            rows = [(f'2027-01-{day:02}', '已驗證休市') for day in range(1, 6)]
            self.assertTrue(install(2027, rows, target))
            first = target.read_text(encoding='utf-8')
            self.assertFalse(install(2027, rows, target))
            self.assertEqual(target.read_text(encoding='utf-8'), first)
            self.assertIn('2026-09-25', first)

    def test_unknown_year_stops_scheduled_market_work_loudly(self):
        unknown = date(2099, 6, 3)
        self.assertFalse(is_trading_day(unknown))
        self.assertIn('尚未驗證', why_closed(unknown))

    def test_known_holiday_and_open_day(self):
        self.assertFalse(is_trading_day(date(2026, 9, 25)))
        self.assertTrue(is_trading_day(date(2026, 9, 18)))

    def test_workflow_guards_scheduled_runs(self):
        for name in ('daily.yml', 'transcript.yml', 'market-data.yml'):
            text = (ROOT / '.github' / 'workflows' / name).read_text(encoding='utf-8')
            self.assertIn('交易日預檢' if name != 'market-data.yml' else '休市日預檢', text)
            self.assertIn('is_trading_day' if name == 'market-data.yml' else 'why_closed', text)


if __name__ == '__main__':
    unittest.main()
