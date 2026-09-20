"""One-time local migration; future edits belong in pipeline/pipeline.py."""
import ast
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / 'pipeline/pipeline.py'
source = path.read_text(encoding='utf-8-sig')
if "ASSESSMENT_VERSION = 'context-json-v3'" in source:
    raise SystemExit('Already installed; edit the canonical runtime directly.')
with zipfile.ZipFile(ROOT / 'backups/before-context-json-v3.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in [ROOT/'pipeline.py', path, *list((ROOT/'apps-script').glob('*'))]:
        if p.is_file(): z.write(p, p.relative_to(ROOT))
helpers = (ROOT/'scripts/context_batch_runtime.py').read_text(encoding='utf-8')
source = source.replace('def extract_signals(v2, date_str):', helpers + '\n\ndef extract_signals(v2, date_str):\n    if os.environ.get("GEMINI_COMBINED_ASSESSMENT", "true").lower() != "false":\n        return extract_context_json(v2, date_str)')
source = source.replace('def audit_signals(v2, signals, date_str):', 'def audit_signals(v2, signals, date_str):\n    if signals.pop("_combined_pass", False):\n        return audit_context_json(v2, signals, date_str)')
source = source.replace('    hay = _ev_norm(transcript)\n    for cat in SIGNAL_CATEGORIES + ("history", "uncertain"):', '    recover_context(signals, transcript)\n    hay = _ev_norm(transcript)\n    for cat in SIGNAL_CATEGORIES + ("history", "uncertain"):')
source = source.replace('if not te or not any(te in _ev_norm(q) for q in quotes):', 'if (not te or not any(te in _ev_norm(q) for q in quotes)) and not row.get("_time_from_context"):')
source = source.replace('elif when in _WHEN_MARKERS and not re.search(_WHEN_MARKERS[when], te):', 'elif when in _WHEN_MARKERS and not re.search(_WHEN_MARKERS[when], te) and not row.get("_time_from_context"):')
# With no separate time quote, check actual evidence for a conflicting historical marker.
source = source.replace('r"昨天|昨日|前天|前幾天|當天|那一天|先前|以前", te):', 'r"昨天|昨日|前天|前幾天|當天|那一天|先前|以前", te or evidence):')
source = source.replace("if not te or te not in ev or when not in ('today','yesterday','date','prev_trading_day'):", "if ((not te or te not in ev) and not row.get('_time_from_context')) or when not in ('today','yesterday','date','prev_trading_day'):")
source = source.replace('NON_STOCK_EXACT = {', 'NON_STOCK_EXACT = {\n    "日幣", "日圓", "日元", "美元", "美金", "台幣", "臺幣", "新台幣", "人民幣", "歐元",')
at = source.index('    m = get_code_map()', source.index('def resolve_code('))
source = source[:at] + '''    if name in NON_EQUITY_NAMES:
        return REJECT, name, '貨幣，不是股票'
    if name in CONFIRMED_NAMES:
        code, fixed = CONFIRMED_NAMES[name]
        if code:
            return code, fixed, '管理者確認名稱'
        official = get_code_map()
        exact = next((c for c, n in official.items() if n == fixed), None)
        return exact or UNRESOLVED, fixed, '管理者確認名稱；僅接受正式名稱完全相同的代號'

''' + source[at:]
# Preserve corrected-but-unresolved names and prevent a second AI guessing them away.
source = source.replace('            if code == UNRESOLVED:\n                stat["待確認"]', '            if raw in CONFIRMED_NAMES:\n                r["name"] = fixed\n                r["aliases"] = list(dict.fromkeys((r.get("aliases") or []) + [raw]))\n            if code == UNRESOLVED:\n                stat["待確認"]')
source = source.replace('if _in_transcript(nm, hay) or _in_transcript(cd, hay):', 'if _in_transcript(nm, hay) or _in_transcript(cd, hay) or any(_in_transcript(a, hay) for a in r.get("aliases", []) if a):')
source = source.replace('    def _public(r):', '    if os.environ.get("GEMINI_ARTICLE_ENABLED", "false").lower() != "true":\n        print("每日整理：使用最終JSON資料產生六章文章，不另呼叫模型")\n        return canonical_article(signals, date_str)\n\n    def _public(r):', 1)
# New policy removes contradictory requirements rather than appending a competing prompt.
source = source.replace('同音候選可以送 uncertain 並寫出上下文，不能憑同音直接選定另一家公司。', '管理者確認：普威＝譜瑞-KY（4966）；戲制台＝矽製材，代號未確認不得猜。日幣是貨幣，不是日馳或其他股票。其餘同音候選依上下文判讀，無法確認身分才送 uncertain。')
source = source.replace('buy/sell 必填 when、time_evidence（短句原字，含動作與時間）、seq。', 'buy/sell 填 when、seq，time_evidence 能找到就填。時間可以分布在前後段；漏附獨立時間短句不影響收錄。當下已執行的操作可依上下文判 today；明確歷史回顧仍不得猜成今天。')
source = source.replace('history 另填 when=unknown、action=buy/sell。uncertain 明列疑點與可能分類。', 'history 另填 when=unknown、action=buy/sell。uncertain 明列疑點與 suggested_category（九類英文鍵之一）；可判斷分類而只有引用定位或缺少時間短句的問題，直接收進該類並寫 review_note，不要隔離。')
source = source.replace('EXTRACT_SYSTEM = POLICY + "\\n請獨立建立完整初稿。"', 'EXTRACT_SYSTEM = POLICY + "\\n這次合併擷取、分類、日期判斷、補漏及大盤摘要。輸入為JSON，source每個鍵是來源編號。完整讀完各段後在同一次回答自行覆核，特別檢查最後20%，只輸出完成的九類陣列，不輸出初稿或重複引句。長稿各批保留原始S編號，不假設記得其他請求。"')
source = source.replace("'evidence-v2',payload", "ASSESSMENT_VERSION,payload")
source = source.replace("'evidence-v2',json.dumps", "ASSESSMENT_VERSION,json.dumps")
source = source.replace("fingerprint,'evidence-v2'", "fingerprint,ASSESSMENT_VERSION")
ast.parse(source)
path.write_text(source, encoding='utf-8')

gas = ROOT/'apps-script/Adminpipeline.gs'
text = gas.read_text(encoding='utf-8-sig')
text = text.replace('var PIPE_NON_STOCK_EXACT = [', "var PIPE_NON_STOCK_EXACT = [\n  '日幣', '日圓', '日元', '美元', '美金', '台幣', '臺幣', '新台幣', '人民幣', '歐元',")
needle = "  if (!n) { return { reject: true, how: '空白名稱' }; }"
text = text.replace(needle, needle + "\n  if (['日幣','日圓','日元','美元','美金','台幣','臺幣','新台幣','人民幣','歐元'].indexOf(n) >= 0) { return {reject:true, how:'貨幣，不是股票'}; }\n  if (n === '普威') { return {code:'4966', name:'譜瑞-KY', how:'管理者確認名稱'}; }\n  if (n === '戲制台' || n === '矽製材') {\n    var code = map.byName['矽製材'];\n    return code ? {code:code,name:'矽製材',how:'管理者確認名稱'} : {pending:true,name:'矽製材',candidates:[],how:'管理者確認名稱；代號待確認'};\n  }")
text = text.replace('每筆 buy/sell 必填 when 及 time_evidence；time_evidence 是 evidence 中完整原句的逐字片段。', '每筆 buy/sell 填 when；time_evidence 可由前後段補足，缺少獨立時間短句不直接降級。')
text = text.replace('不要隱藏未能確認的項目或把 uncertain 偷改成 watch_watch。', '有原文上下文且分類可判斷的項目照常納入該分類並附待確認註記；只有身分或分類真正不明才留 uncertain。普威＝譜瑞-KY（4966），戲制台＝矽製材（代號勿猜）；日幣是貨幣，不是股票。')
gas.write_text(text, encoding='utf-8')
code = ROOT/'apps-script/Code.gs'
code.write_text(code.read_text(encoding='utf-8-sig').replace("2026-09-10-evidence-v2", "2026-09-11-context-json-v3"), encoding='utf-8')
print('Installed context JSON assessment; original files saved in backups/before-context-json-v3.zip')
