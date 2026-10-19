"""Real-source failure regressions. No network, spreadsheet writes or model requests."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault('SPREADSHEET_ID', 'local-test')
os.environ.setdefault('GEMINI_API_KEY', 'local-test-not-a-key')
spec = importlib.util.spec_from_file_location('stability_pipeline', ROOT / 'pipeline/pipeline.py')
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)


class EvidenceBoundaries(unittest.TestCase):
    def verdict(self, text, **fields):
        answer = dict(about_itself=True, now='conditional', tone='bearish',
                      quotes={'stance': [text], 'entry_condition_actionable': [text]})
        answer.update(fields)
        return pl.verdict_class(answer, pl._ev_norm(text))[0]

    def test_wait_for_new_judgment_is_not_an_actionable_buy_condition(self):
        self.assertEqual(self.verdict('我會建議最近想買聯發科的人等我等幾天判斷完我就會跟你們講',
                                      entry_condition_actionable=False), 'watch_avoid')

    def test_executable_pullback_condition_is_still_watch(self):
        self.assertEqual(self.verdict('群聯等回測季線就可以買，不要追高',
                                      entry_condition_actionable=True), 'watch_watch')

    def test_false_without_quoted_evidence_is_not_a_vote(self):
        self.assertEqual(self.verdict('群聯等回測季線就可以買', entry_condition_actionable=False,
                                      quotes={'stance': ['群聯等回測季線就可以買']}), '')

    def test_quote_from_another_topic_is_rejected(self):
        self.assertEqual(self.verdict('群聯等回測季線就可以買', entry_condition_actionable=False,
            quotes={'stance': ['群聯等回測季線就可以買'],
                    'entry_condition_actionable': ['聯發科等我判斷完再說']}), '')

    def test_advice_is_not_a_completed_sale(self):
        self.assertNotEqual(self.verdict('張總早，請問我有台郡6269，要賣嗎？張震回答賣。',
            sell_today=True, now='none', quotes={'sell_today': ['台郡6269，要賣嗎？張震回答賣。'],
                                               'stance': ['台郡6269，要賣嗎？張震回答賣。']}), 'sell')

    def inventory(self, text):
        with patch.object(pl, '_CODE_MAP', {'3152': '璟德', '2026': '假年度公司', '3266': '昇陽'}), \
                patch.object(pl, '_sound_hits', return_value={}), patch.object(pl, '_manual_memo_entries', return_value=[]):
            return pl.source_inventory({'S1': {'text': text}})

    def test_code_before_industry_and_partial_name_remains_a_review_candidate(self):
        rows = self.inventory('今天早上9點多買什麼3152網通股的警，磨到今天我進場。')
        row = next(x for x in rows if x['code'] == '3152')
        self.assertTrue(row['identity_unconfirmed'])
        self.assertTrue(row['weak'])  # candidate does not prove a trade or stock identity

    def test_prices_years_and_digits_inside_long_numbers_do_not_become_code_probes(self):
        for text in ['2026年成交價3152元', '2026/10/19，13152元', '3152.5元', '3152張']:
            with self.subTest(text=text):
                self.assertFalse(any(x.get('identity_unconfirmed') for x in self.inventory(text)))

    def test_existing_named_candidate_is_not_duplicated_by_number(self):
        rows = self.inventory('3152璟德今天進場')
        self.assertEqual(sum(x['code'] == '3152' for x in rows), 1)

    def test_open_ledger_cannot_publish_an_ignored_supplier_news_mention(self):
        text = 'Google手機晶片去年採用台積電，今年進行更換。'
        row = {'name': '台積電', 'code': '2330', 'reason': text, 'evidence': [text]}
        signals = {c: [] for c in pl._VERDICT_ORDER}
        signals['ignored'] = [row]
        reply = dict(id='2330', about_itself=True, now='none', tone='neutral',
                     quotes={'stance': [text]})
        with patch.object(pl, '_verdict_known', return_value=({'2330': ['系統紀錄：之前買入']}, set(), {'2330': '台積電'})), \
                patch.object(pl, '_verdict_cache', return_value={}), \
                patch.object(pl, '_own_segments', return_value=[text]), \
                patch.object(pl, '_ask_verdicts', return_value={'2330': reply}), \
                patch.object(pl, 'GEMINI_KEYS', ['local']), patch.object(pl, 'budget_left', return_value=9999), \
                patch.dict(os.environ, {'CLASS_ADJUDICATION': 'on'}):
            out = pl.adjudicate_classes(object(), signals, text, '2026/08/21')
        self.assertFalse(out['holdings'])
        self.assertEqual(out['_verdicts']['2330']['cls'], 'ignored')


if __name__ == '__main__':
    unittest.main()
