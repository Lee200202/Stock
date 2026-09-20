import ast
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'pipeline/pipeline.py').read_text(encoding='utf-8')
policy = next(ast.literal_eval(n.value) for n in ast.parse(source).body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'POLICY')
extra = policy[policy.index('【收錄與稽核一致性】'):]
path = ROOT/'apps-script/Adminpipeline.gs'
text = path.read_text(encoding='utf-8')
if '// context-thinking-policy-v1' not in text:
    text += '\n// context-thinking-policy-v1\nvar PIPE_CONTEXT_REVIEW_RULES = ' + json.dumps(extra.replace('evidence_refs', 'evidence（原文引句）'), ensure_ascii=False) + ';\n'
    text += '\n'.join(n + ' += "\\n" + PIPE_CONTEXT_REVIEW_RULES;' for n in ('PIPE_EXTRACT_SYSTEM', 'PIPE_AUDIT_SYSTEM', 'PIPE_REVIEW_SYSTEM')) + '\n'
path.write_text(text, encoding='utf-8')
path = ROOT/'tests/test_model_dailyk.py'
text = path.read_text(encoding='utf-8').replace("{'thinkingLevel': 'low'}", "{'thinkingLevel': 'medium'}")
if 'def test_configurable_thinking' not in text:
    text = text.replace('class ModelAndDailyKTests(unittest.TestCase):', '''class ModelAndDailyKTests(unittest.TestCase):
    def test_configurable_thinking(self):
        for level in ('low', 'medium', 'high'):
            with patch.dict(p.os.environ, {'GEMINI_THINKING_LEVEL': level}):
                self.assertEqual(p.gemini_generation_config('gemini-3.5-flash-lite')['thinkingConfig']['thinkingLevel'], level)
        with patch.dict(p.os.environ, {'GEMINI_THINKING_LEVEL': 'invalid'}):
            with self.assertRaises(ValueError): p.gemini_generation_config('gemini-3.5-flash-lite')
''')
path.write_text(text, encoding='utf-8')
doc = ROOT/'release/DEPLOY_CONTEXT_JSON_V3.md'
text = doc.read_text(encoding='utf-8').replace('zhangzhen-context-json-v3-integrated.zip', 'zhangzhen-context-json-v3-thinking.zip').replace('65 項 Python', '66 項 Python').replace('thinkingLevel: low', 'thinkingLevel: medium（可設定）')
text += '''

## Thinking 可設定更新

預設 medium。GitHub Repository Variables 設 GEMINI_THINKING_LEVEL=medium 或 high；Apps Script 在「專案設定 → 指令碼屬性」新增同名屬性，值同樣是 medium 或 high。兩邊分開設定；未設皆用 medium，可改 low 降低延遲。Apps Script 個別呼叫可用 opt.thinkingLevel 覆寫。只影響 3.x，2.5 原參數保留。設定錯字明確報錯。

3.5 Flash-Lite 支援 low/medium/high，免費層含 thinking tokens，官方未將 high 列為付費限定；實際額度依專案。較高思考可能增加 token、延遲及輸出截斷機率，不保證分類較準。建議日常 medium，難例重跑才改 high；尚未用正式原文比較兩者品質。

來源：[思考參數](https://ai.google.dev/gemini-api/docs/generate-content/thinking)、[免費方案價格](https://ai.google.dev/gemini-api/docs/pricing)。

稽核 prompt 在 Python 與 Apps Script 同步補上：分散的身份／動作／時間證據可補回、禁止跨股借用動作、上下文日期說明、明確候選分類、範例股票不得無原文新增、覆核候選不可無聲消失。放寬的是證據格式與上下文收錄，不是新增沒有來源的交易。部署後仍需以 9/10 原文確認。
'''
doc.write_text(text, encoding='utf-8')
path = ROOT/'scripts/package_context_json.py'
text = path.read_text(encoding='utf-8').replace('65 Python', '66 Python').replace('context-json-v3-integrated', 'context-json-v3-thinking')
path.write_text(text, encoding='utf-8')
