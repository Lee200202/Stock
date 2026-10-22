"""以當次文字與來源指紋驗證AI審閱，避免詞表誤報及跨股撰稿。"""
import copy
import hashlib
import os
import unittest
from unittest.mock import patch

os.environ.setdefault('SPREADSHEET_ID', 'offline')
os.environ.setdefault('GEMINI_API_KEY', 'offline-placeholder')
from replay_extract import invariants, pl


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def reviewed(note, source, **changes):
    result = {key: True for key in ('meaning_preserved', 'category_consistent', 'source_supported', 'written_style_ok')}
    result.update(text_sha256=digest(note), source_sha256=digest(source), **changes)
    return result


class PublicSourceReceipts(unittest.TestCase):
    def test_wait_for_signal_then_layout_is_not_reclassified_by_word(self):
        source = '世芯-KY目前沒有量，不急著買，等收斂與量出來。'
        note = '世芯-KY尚未見成交量放大，目前不要急於進場，應等待收斂與出量訊號後再行布局。'
        row = {'name': '世芯-KY', 'code': '3661', 'reason': note, '_public_edit_review': reviewed(note, source)}
        with patch.object(pl, 'source_inventory', return_value=[]):
            errors, _ = invariants({'watch_avoid': [row]}, source)
        self.assertEqual(errors, [])
        self.assertTrue(pl._public_review_covers(row, 'reason', source))

    def test_audit_view_can_bind_to_saved_raw_but_not_another_source(self):
        raw, view, note = '甲 公 司 不 要 追', '甲公司，不要追。', '甲公司不宜追高。'
        row = {'reason': note, '_public_edit_review': reviewed(note, view, raw_source_sha256=digest(raw))}
        self.assertTrue(pl._public_review_covers(row, 'reason', raw))
        self.assertTrue(pl._public_review_covers(row, 'reason', view))
        self.assertFalse(pl._public_review_covers(row, 'reason', '乙公司不要追。'))
        row['reason'] = '甲公司可以買進。'
        self.assertFalse(pl._public_review_covers(row, 'reason', raw))

    def test_explicit_current_source_rejection_quarantines_only_that_stock(self):
        source = '甲公司不宜追高。乙公司在低檔整理。'
        bad, good = '甲公司可以追高買進。', '乙公司目前在低檔整理，仍須觀察量能變化。'
        row = {'name': '甲公司', 'code': '1111', 'reason': bad,
               '_public_edit_review': reviewed(bad, source, source_supported=False)}
        keep = {'name': '乙公司', 'code': '2222', 'reason': good,
                '_public_edit_review': reviewed(good, source)}
        signals = pl.quarantine_public_rows({'watch_watch': [row, keep]}, source)
        self.assertEqual([r['code'] for r in signals['watch_watch']], ['2222'])
        self.assertEqual(signals['_publication_quarantine'][0]['code'], '1111')
        self.assertTrue(pl.public_review_rejection(row, 'reason', source))
        self.assertEqual(pl.public_review_rejection(row, 'reason', '另一份原稿'), '')

    def test_source_packet_uses_verified_quotes_and_not_generated_reason(self):
        source = '甲公司昨天很強，今天跌下來，不宜追逐熱門股。後面談一般低檔布局。'
        proof = {'entity': 'company', 'role': 'current_risk', 'direction': 'negative',
                 'anchor': '甲公司昨天很強', 'quote': '甲公司昨天很強，今天跌下來，不宜追逐熱門股。'}
        answer = {'about_itself': True, 'semantic': proof}
        entry = {'open': False, 'basis': '虛構每股賺九百元', 'attempts': [
            {'class': 'watch_avoid', 'answer': answer},
            {'class': 'watch_watch', 'answer': answer},
            {'class': '', 'answer': answer},
        ]}
        signals = {'_verdicts': {'1111': entry}}
        packet = pl.verified_note_view(signals, {'code': '1111'}, 'watch_avoid', source)
        self.assertEqual(len(packet['quotations']), 1)
        self.assertNotIn('虛構', str(packet))
        wrong = copy.deepcopy(signals)
        wrong['_verdicts']['1111']['attempts'][0]['answer']['semantic']['quote'] = '另一家公司已經買進且持有。'
        self.assertEqual(pl.verified_note_view(wrong, {'code': '1111'}, 'watch_avoid', source)['quotations'], [])


if __name__ == '__main__':
    unittest.main()
