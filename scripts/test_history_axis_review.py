"""離線驗證歷史部位與今日立場的覆核路由，不以模擬回覆證明模型準確率。"""
import ast
import hashlib
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'pipeline/pipeline.py'


def harness(answer):
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    selected = [n for n in tree.body if
                isinstance(n, ast.FunctionDef) and n.name == '_audit_direct_verdicts' or
                isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                  {'DIRECT_AUDIT_SKIP', 'DIRECT_AUDIT_SYSTEM', 'VERDICT_VERSION'} for t in n.targets)]
    calls = []
    def model(system, payload, **options):
        calls.append(json.loads(payload))
        return json.dumps(answer)
    ns = dict(json=json, hashlib=hashlib, _QUOTA_STOP={}, budget_left=lambda: 1000,
              GEMINI_KEYS=['offline'], MAX_OUT=10000, call_gemini=model,
              safe_load_json=lambda s, **kw: json.loads(s),
              _own_segments=lambda row, *_: [{'text': row['quote']}],
              _verdict_mark=lambda sent, _: (sent, sent[0]['text']),
              _explicitly_not_held=lambda *_: '', _ev_norm=lambda s: s,
              _verdict_closed=lambda *_: '', note_decision=lambda *_: None,
              _VERDICT_LABEL={'history': '回顧', 'holdings': '會員持股', 'buy': '買入'})
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns, calls


def closed(code='1111'):
    return {'name': '甲公司', 'tier': '直接歸類', 'enforce': True, 'open': False,
            'basis': '紀錄上已經賣出、之後沒有買回：是回顧',
            'cls': 'history', 'current': 'holdings'}


class HistoryAxisReview(unittest.TestCase):
    def run_review(self, verdicts, answer):
        ns, calls = harness(answer)
        contexts = {code: ({'quote': '甲公司以前已賣出，現在回到年線可等待布局。'}, {'甲公司'})
                    for code in verdicts}
        known = {code: ['上週賣出這一檔'] for code in verdicts}
        result = ns['_audit_direct_verdicts'](verdicts, contexts, {}, '原稿', '2026/10/21', known)
        return result, calls, known

    def test_closed_position_is_reviewed_without_changing_trade_record(self):
        entries = {'1111': closed()}
        result, calls, known = self.run_review(entries, {'checks': [
            {'id': '1111', 'about': True, 'supports': False, 'why': '另有當下買點'}]})
        self.assertEqual(len(calls), 1)
        self.assertEqual([c for c, _ in result], ['1111'])
        self.assertEqual(entries['1111']['tier'], '送裁決')
        self.assertEqual(known, {'1111': ['上週賣出這一檔']})
        self.assertNotIn('buy', entries['1111'])

    def test_pure_history_is_not_automatically_promoted(self):
        entries = {'1111': closed()}
        result, calls, _ = self.run_review(entries, {'checks': [
            {'id': '1111', 'about': True, 'supports': True}]})
        self.assertEqual(result, [])
        self.assertEqual(entries['1111']['cls'], 'history')
        self.assertEqual(len(calls), 1)

    def test_notice_and_verified_open_position_are_not_repolled(self):
        entries = {'1111': dict(closed(), basis='當日會員通知已有買賣', cls='buy'),
                   '2222': dict(closed(), basis='紀錄上持有（最近一筆是買入）', open=True)}
        result, calls, _ = self.run_review(entries, {})
        self.assertEqual((result, calls), ([], []))

    def test_multiple_history_candidates_share_one_call(self):
        result, calls, _ = self.run_review({'1111': closed(), '2222': closed()}, {})
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]['items']), 2)
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
