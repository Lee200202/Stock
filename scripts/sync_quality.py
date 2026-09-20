"""Synchronize deployment copies; pipeline/pipeline.py is the sole runtime source.

Never reinstall stale runtime fragments over a newer pipeline.
"""
import ast
import json
import re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    source=(ROOT/'pipeline/pipeline.py').read_text(encoding='utf-8')
    ast.parse(source)
    # Normalize both copies, including mixed line endings after Windows edits.
    encoded = source.replace('\r\n', '\n').encode('utf-8')
    (ROOT/'pipeline/pipeline.py').write_bytes(encoded)
    # 根目錄 pipeline.py 會遮住 pipeline/ 套件，讓 transcript.py 無法匯入休市表。
    # 只保留正本；不要再次產生已退役的副本。
    # Evaluate only string constants needed for prompt mirrors, no API imports.
    values={}
    for node in ast.parse(source).body:
        if isinstance(node,ast.Assign) and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
            if name in ('POLICY','POLISH_SYSTEM','EXTRACT_SYSTEM','AUDIT_SYSTEM','ARTICLE_SYSTEM','TX_FORMAT_SYSTEM','CM_PARSE_SYSTEM'):
                values[name]=eval(compile(ast.Expression(node.value),'<prompt>','eval'),{'__builtins__':{}},values)
    cm=ROOT/'apps-script/Cmoney.gs'
    cmtext=cm.read_text(encoding='utf-8')
    cmtext=re.sub(r'var CM_PARSE_SYSTEM =.*?(?=\n\n/\*\* 這一條裡)',lambda m:'var CM_PARSE_SYSTEM = '+json.dumps(values['CM_PARSE_SYSTEM'],ensure_ascii=False)+';\n',cmtext,flags=re.S)
    cm.write_text(cmtext,encoding='utf-8')
    path=ROOT/'apps-script/Adminpipeline.gs';text=path.read_text(encoding='utf-8-sig')
    # GAS uses literal source quotes, while Python uses segment IDs. The decision
    # policy is shared; only the evidence transport and review response differ.
    aliases = next(ast.literal_eval(n.value) for n in ast.parse(source).body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='CONFIRMED_NAMES')
    policy = values['POLICY'].replace('這次帶有 S 編號的原始逐字稿', '這次提供的完整原始逐字稿')
    policy = policy.replace('確認名稱見輸入 confirmed_names。', '管理者確認名稱：'+json.dumps(aliases,ensure_ascii=False)+'。')
    first = policy.index('【證據位置，不重寫引句】')
    last = policy.index('不同段落分別證明', first)
    policy = policy[:first] + '【證據位置】\n每筆 evidence 是原文連續片段的陣列，不改字、不加省略號；分段提供名称、主詞、動作與時間的證據。\n' + policy[last:]
    policy = policy.replace('evidence_refs', 'evidence').replace('S 編號', '原文連續片段').replace('段落編號', '原文連續片段')
    review_schema = ('這輪是既有表格複審。依每列來源影片日期解讀時間，不能拿記錄當天另一支影片推翻來源。\n'
        '此介面不能改日期；日期不符或證據不足時 confident=false、why 說明需重跑來源影片。\n'
        '只輸出 {"results":[{"index":1,"verdict":"保留","direction":"會員持股","stock_name":"","why":"","confident":true,"evidence":["原句"]}]}。\n'
        'verdict 可用保留/改分類/改名稱/刪除。direction 可用買入/賣出/觀望不碰/觀望注意/會員持股。')
    review_policy = re.sub(r'【JSON】.*?(?=【收錄與稽核一致性】)', '', policy, flags=re.S)
    mirrors = [('PIPE_POLISH_SYSTEM',values['POLISH_SYSTEM']), ('PIPE_ARTICLE_SYSTEM',values['ARTICLE_SYSTEM']),
        ('PIPE_EXTRACT_SYSTEM',policy), ('PIPE_AUDIT_SYSTEM',policy + '\n獨立重讀全文覆核初稿，輸出完整九類陣列與 changes，不只輸出差異。'),
        ('PIPE_REVIEW_SYSTEM',review_policy + '\n' + review_schema), ('PIPE_CONTEXT_REVIEW_RULES','')]
    for name,value in mirrors:
        start=text.index('var '+name+' =');end=start+re.search(r';\s*\n',text[start:]).end()
        text=text[:start]+'var '+name+' = '+json.dumps(value,ensure_ascii=False)+';\n\n'+text[end:]
    path.write_text(text,encoding='utf-8')
    # 逐字稿排版的提示詞兩端共用（2026/09/17 v44）：工作流寫入時排一次，網站每 15 分鐘補排。
    sheet_path=ROOT/'apps-script/SheetService.gs'
    sheet_text=sheet_path.read_text(encoding='utf-8-sig')
    start=sheet_text.index('var TX_FORMAT_SYSTEM =');end=start+re.search(r';[^\n]*\n',sheet_text[start:]).end()
    sheet_text=sheet_text[:start]+'var TX_FORMAT_SYSTEM = '+json.dumps(values['TX_FORMAT_SYSTEM'],ensure_ascii=False)+';   // 由 scripts/sync_quality.py 從 pipeline.py 同步，不要手改\n'+sheet_text[end:]
    sheet_path.write_text(sheet_text,encoding='utf-8')
    # 公開敘述的別名表兩端共用，不拿模型的代號改寫原始證據。
    aliases = next(ast.literal_eval(n.value) for n in ast.parse(source).body
        if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='CONFIRMED_NAMES')
    helper = (ROOT/'scripts/public_narrative_v10.txt').read_text(encoding='utf-8').replace('__ALIASES__',json.dumps(aliases,ensure_ascii=False))
    for filename in ('Presentationquality.gs','JavaScript.html'):
        dest=ROOT/'apps-script'/filename
        content=dest.read_text(encoding='utf-8-sig')
        if '// BEGIN PUBLIC NARRATIVE V10' in content:
            content=re.sub(r'// BEGIN PUBLIC NARRATIVE V10.*?// END PUBLIC NARRATIVE V10',lambda m:helper.strip(),content,flags=re.S)
        elif filename.endswith('.gs'):
            content += '\n'+helper
        else:
            content=content.replace('  function priceCell(r) {',helper+'\n  function priceCell(r) {')
        dest.write_text(content,encoding='utf-8')
    price_source=(ROOT/'apps-script/Presentationquality.gs').read_text(encoding='utf-8')
    price_fn=re.search(r'function displayPrice_\(.*?\n\}',price_source,re.S).group()
    frontend=ROOT/'apps-script/JavaScript.html'
    content=frontend.read_text(encoding='utf-8')
    content=re.sub(r'function displayPrice_\(.*?\n\}',lambda m:price_fn,content,count=1,flags=re.S)
    frontend.write_text(content,encoding='utf-8')
    doc = ROOT/'docs/0912/PROMPT_CONTEXT_JSON.md'
    if doc.parent.exists():
        doc.write_text('# 原文 JSON 判讀 Prompt\n\n由 pipeline/pipeline.py 同步匯出。規則不寫入公開文章。\n'
            '輸入為 `{ "date": "2026/09/11", "source": { "S0001": "完整原文第一段", "S0002": "完整原文第二段" } }`；'
            '覆核另附 candidates 與疑點，保留全部來源段落。\n\n## 擷取\n\n```text\n' + values['EXTRACT_SYSTEM'] +
            '\n```\n\n## 覆核\n\n沿用上述共同 POLICY，改用以下任務結尾：\n\n```text\n' +
            values['AUDIT_SYSTEM'][len(values['POLICY']):] + '\n```\n',encoding='utf-8')
    print('Synced GAS decision/article prompts and prompt documentation; pipeline/pipeline.py is the only Python entry')
if __name__=='__main__':main()
